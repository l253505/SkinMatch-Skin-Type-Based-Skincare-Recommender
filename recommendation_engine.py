"""GlowGuide's reproducible Phase 4 recommendation engine.

Logistic Regression labels and K-means groups are read from the trained notebook exports.
Reference-product similarity and ranking are computed at request time, exactly as in
recommend_products() in 04_recommendation_system.ipynb:

    Score = cosine similarity + 0.05 x shared key ingredients + 0.03 x same K-means group
    Products with cosine similarity below MIN_SIMILARITY are not recommended.
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
    'has_retinoid':'Retinoids',
    'has_vitamin_c':'Vitamin C',
    'has_aha':'AHAs',
    'has_uv_filter':'UV filters',
}
ACTIVE_BONUS=0.05      # per shared key ingredient
CLUSTER_BONUS=0.03     # same K-means ingredient group
MIN_SIMILARITY=0.08    # below this, products share little beyond base ingredients
CONCERNS = {'Dehydration':'helps_dryness','Breakouts':'helps_acne','Excess oil':'helps_oil_control','Redness':'helps_redness'}
BASIC_FEATURES = ['has_niacinamide','has_zinc','has_salicylic_acid','has_ceramide','has_hyaluronic','has_squalane',
                  'has_clay','has_heavy_oil_butter','has_fragrance','has_alcohol_denat','has_essential_oil']

def flag(row,column):
    try:return float(row.get(column,0) or 0)>=.5
    except (ValueError,TypeError):return False

def ingredients(row):
    return {x.strip().casefold() for x in str(row['ingredients_clean']).split('|') if x.strip()}

def label_source(row):
    return 'Product label' if flag(row,'has_skin_label') else 'Logistic Regression prediction'

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
        is_sunscreen=matches['name'].str.contains('SPF|sunscreen|sunblock|broad spectrum|UV',case=False,na=False)
        if 'has_uv_filter' in matches:
            is_sunscreen=is_sunscreen|(pd.to_numeric(matches['has_uv_filter'],errors='coerce').fillna(0)==1)
        is_tanner=matches['name'].str.contains('tan|bronz',case=False,na=False)
        matches=matches[is_sunscreen&~is_tanner]
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
        missing=[c for c in KEY_INGREDIENTS if c not in self.catalog]
        if missing:
            raise ValueError('skinmatch_recommendations.csv is missing '+', '.join(missing)+'. Re-run 04_recommendation_system.ipynb.')
        self.actives=self.catalog[list(KEY_INGREDIENTS)].apply(pd.to_numeric,errors='coerce').fillna(0).astype(int)
        self.cluster_count=int(self.catalog.Cluster.nunique())

    def rank(self,candidates,reference_index,min_similarity=MIN_SIMILARITY):
        reference_index=int(reference_index)
        if reference_index not in self.catalog.index:raise ValueError('Unknown reference product')
        result=candidates.loc[candidates.index!=reference_index].copy()
        if result.empty:
            for column,dtype in [('Similarity',float),('Shared_Actives',int),('Same_Cluster',int),('Score',float)]:
                result[column]=pd.Series(dtype=dtype)
            return result
        reference_flags=self.actives.loc[reference_index]
        selected=reference_flags.index[reference_flags==1].tolist()
        result['Similarity']=np.clip(cosine_similarity(self.matrix[reference_index],self.matrix[result.index.to_numpy(dtype=int)])[0],0,1)
        result['Shared_Actives']=self.actives.loc[result.index,selected].sum(axis=1).astype(int) if selected else 0
        reference_cluster=int(self.catalog.loc[reference_index,'Cluster'])
        result['Same_Cluster']=(pd.to_numeric(result['Cluster'],errors='coerce')==reference_cluster).astype(int)
        result['Score']=result.Similarity+ACTIVE_BONUS*result.Shared_Actives+CLUSTER_BONUS*result.Same_Cluster
        result=result[result.Similarity>=min_similarity]
        return result.sort_values('Score',ascending=False)

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
                'predicted_label_products':int((~labeled).sum()),'alignment_verified':True}
