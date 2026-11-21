"""GlowGuide's reproducible Phase 4 recommendation engine.

KNN labels and K-means groups are read from the trained notebook exports.
Reference-product similarity and ranking are computed at request time.
"""
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.sparse import load_npz
from sklearn.metrics.pairwise import cosine_similarity

SKIN_TYPES = {'Dry':'dry','Oily':'oily','Combination':'combination','Normal':'normal','Sensitive':'sensitive','Acne-prone':'acne_prone'}
KEY_INGREDIENTS = {
    'has_niacinamide':'Niacinamide',
    'has_zinc':'Zinc',
    'has_salicylic_acid':'Salicylic acid',
    'has_ceramide':'Ceramides',
    'has_hyaluronic':'Hyaluronic acid',
    'has_squalane':'Squalane',
}
CONCERNS = {'Dehydration':'helps_dryness','Breakouts':'helps_acne','Excess oil':'helps_oil_control','Redness':'helps_redness'}
BASIC_FEATURES = list(KEY_INGREDIENTS) + ['has_clay','has_heavy_oil_butter','has_fragrance','has_alcohol_denat','has_essential_oil']

def flag(row,column):
    try:return float(row.get(column,0) or 0)>=.5
    except (ValueError,TypeError):return False

def ingredients(row):
    return {x.strip().casefold() for x in str(row['ingredients_clean']).split('|') if x.strip()}

def label_source(row):
    return 'Product label' if flag(row,'has_skin_label') else 'KNN prediction'

def skin_matches(row,skin):
    suffix=SKIN_TYPES.get(skin,skin)
    return flag(row,'match_tag_'+suffix)

def filter_catalog(df,skin,budget,category='All products',query='',brands=(),exclusions=(),photo_only=False):
    suffix=SKIN_TYPES.get(skin,skin)
    column='match_tag_'+suffix
    if column not in df:raise ValueError('Unknown skin profile')
    matches=df[(pd.to_numeric(df[column],errors='coerce')==1)&(df.price_pkr>0)&(df.price_pkr<=budget)].copy()
    if category not in ('All products','All'):
        matches=matches[matches.category.str.casefold()==category.casefold()]
    if category.casefold()=='sun protection':
        matches=matches[matches['name'].str.contains('SPF|sunscreen|sunblock|broad spectrum',case=False,na=False)]
    if query.strip():
        text=matches.brand+' '+matches['name']+' '+matches.ingredients_clean
        matches=matches[text.str.contains(query.strip(),case=False,regex=False)]
    if brands:matches=matches[matches.brand.isin(brands)]
    for exclusion in exclusions:
        matches=matches[pd.to_numeric(matches[exclusion],errors='coerce').fillna(0)<.5]
    if photo_only and 'has_photo' in matches:matches=matches[matches.has_photo]
    return matches

class RecommendationEngine:
    def __init__(self,root):
        root=Path(root)
        self.catalog=pd.read_csv(root/'skinmatch_recommendations.csv').fillna('')
        latest_path=root/'skincare_with_predictions.csv'
        if latest_path.exists():
            latest=pd.read_csv(latest_path).fillna('')
            identity=['brand','name','ingredients_clean']
            if not self.catalog[identity].equals(latest[identity]):
                raise ValueError('The latest prediction export is not aligned with the catalog.')
            labeled=pd.to_numeric(latest.has_skin_label,errors='raise').eq(1)
            for suffix in SKIN_TYPES.values():
                prediction='pred_tag_'+suffix
                self.catalog[prediction]=latest[prediction]
                self.catalog['match_tag_'+suffix]=pd.to_numeric(latest['tag_'+suffix].where(labeled,latest[prediction]),errors='raise').astype(int)
        original=pd.read_csv(root/'skincare_clean.csv').fillna('')
        self.matrix=load_npz(root/'ingredients_tfidf.npz').tocsr()
        identity=['brand','name','ingredients_clean']
        if not self.catalog[identity].equals(original[identity]):
            raise ValueError('Ingredient feature rows do not match the catalog order. Re-export aligned project files.')
        if self.matrix.shape[0]!=len(self.catalog):
            raise ValueError('The ingredient matrix and catalog have different row counts.')
        if not np.isfinite(self.matrix.data).all():
            raise ValueError('The ingredient matrix contains invalid numerical values.')
        self.actives=self.catalog[list(KEY_INGREDIENTS)].apply(pd.to_numeric,errors='coerce').fillna(0).astype(int)
        self.cluster_count=int(self.catalog.Cluster.nunique())

    def rank(self,candidates,reference_index):
        reference_index=int(reference_index)
        if reference_index not in self.catalog.index:raise ValueError('Unknown reference product')
        result=candidates.loc[candidates.index!=reference_index].copy()
        if result.empty:
            result['Similarity']=pd.Series(dtype=float)
            result['Shared_Actives']=pd.Series(dtype=int)
            return result
        reference_flags=self.actives.loc[reference_index]
        selected=reference_flags.index[reference_flags==1].tolist()
        result['Similarity']=np.clip(cosine_similarity(self.matrix[reference_index],self.matrix[result.index.to_numpy(dtype=int)])[0],0,1)
        result['Shared_Actives']=self.actives.loc[result.index,selected].sum(axis=1).astype(int) if selected else 0
        result=result[(result.Shared_Actives>0)|(result.Similarity>0)]
        return result.sort_values(['Shared_Actives','Similarity'],ascending=[False,False],kind='stable')

    def explain(self,row,reference_index,skin):
        reference=self.catalog.loc[int(reference_index)]
        shared=[name for col,name in KEY_INGREDIENTS.items() if flag(reference,col) and flag(row,col)]
        common=sorted(ingredients(reference)&ingredients(row))
        return {'shared_actives':shared,'common_ingredients':common,'label_source':label_source(row),
                'skin_match':skin_matches(row,skin),'same_cluster':int(row.Cluster)==int(reference.Cluster),
                'cluster':int(row.Cluster),'reference_cluster':int(reference.Cluster)}

    def cluster_profile(self,cluster):
        group=self.catalog[self.catalog.Cluster==int(cluster)]
        means=group[BASIC_FEATURES].apply(pd.to_numeric,errors='coerce').fillna(0).mean().sort_values(ascending=False)
        return len(group),means

    @property
    def diagnostics(self):
        labeled=pd.to_numeric(self.catalog.has_skin_label,errors='coerce').fillna(0).eq(1)
        return {'products':len(self.catalog),'tfidf_features':self.matrix.shape[1],
                'clusters':self.cluster_count,'labeled_products':int(labeled.sum()),
                'knn_inferred_products':int((~labeled).sum()),'alignment_verified':True}
