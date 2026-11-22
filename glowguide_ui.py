"""GlowGuide's Streamlit experience."""
from pathlib import Path
from html import escape
from io import BytesIO
import base64
import json
import mimetypes

import numpy as np
import pandas as pd
from PIL import Image
import streamlit as st

from recommendation_engine import RecommendationEngine,SKIN_TYPES,KEY_INGREDIENTS,CONCERNS,flag,label_source,skin_matches,filter_catalog,ingredients
from sync_product_photos import key as photo_key

ROOT=Path(__file__).resolve().parent

@st.cache_resource(show_spinner=False)
def get_engine(data_version):
    return RecommendationEngine(ROOT)

@st.cache_data(show_spinner=False)
def get_catalog(photo_version,data_version):
    df=get_engine(data_version).catalog.copy()
    df['product_id']=df.index.astype(str)
    df['price_pkr']=pd.to_numeric(df.price_pkr,errors='coerce')
    path=ROOT/'product_photos.json'
    photos=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    photos={photo_key(p['brand'],p['name']):p for p in photos.values()}
    records=[photos.get(photo_key(r.brand,r['name']),{}) for _,r in df.iterrows()]
    for column,field in [('image_url','image_url'),('local_image','local_image'),('photo_source','source_url'),('photo_note','photo_note')]:
        df[column]=[r.get(field,'') for r in records]
    df['name']=[r.get('display_name',name) for r,name in zip(records,df['name'])]
    df['has_photo']=[bool(r.get('local_image') and (ROOT/'assets'/r['local_image']).is_file()) for r in records]
    df['photo_verified']=[bool(r.get('photo_verified',True) and present) for r,present in zip(records,df['has_photo'])]
    df['photo_gallery']=[r.get('photo_gallery',[]) for r in records]
    df['photo_documentation']=[r.get('verification_note','') for r in records]
    return df

@st.cache_data(show_spinner=False,max_entries=200)
def image_uri(filename,thumbnail=False):
    path=ROOT/'assets'/filename
    if not path.is_file():return ''
    data=path.read_bytes();mime=mimetypes.guess_type(path.name)[0] or 'image/png'
    if thumbnail:
        try:
            with Image.open(BytesIO(data)) as im:
                im.thumbnail((720,720))
                buf=BytesIO();im.save(buf,format='WEBP',quality=90)
                data=buf.getvalue();mime='image/webp'
        except (OSError,ValueError):pass
    return f'data:{mime};base64,'+base64.b64encode(data).decode('ascii')

def chips(values,selected=False):
    cls='ingredient-chip selected' if selected else 'ingredient-chip'
    return ''.join(f'<span class="{cls}">{escape(str(x))}</span>' for x in values)

def money(price):return f'PKR {float(price):,.0f}'

def ingredient_callback(value):
    st.session_state.ingredient_query=value
    st.session_state.ingredient_page=0

def render_app():
    st.set_page_config(page_title='GlowGuide · Your personal skin edit',page_icon='🌷',layout='wide')
    css=(ROOT/'assets/glowguide.css').read_text(encoding='utf-8')
    st.markdown('<style>'+css+'</style>',unsafe_allow_html=True)
    try:
        data_version=tuple((ROOT/name).stat().st_mtime_ns for name in ['skinmatch_recommendations.csv','skincare_with_predictions.csv','skincare_clean.csv','ingredients_tfidf.npz'])
        engine=get_engine(data_version)
        path=ROOT/'product_photos.json'
        products=get_catalog(path.stat().st_mtime_ns if path.exists() else 0,data_version)
    except (OSError,ValueError) as exc:
        st.error(f'GlowGuide could not load its aligned project data: {exc}')
        st.stop()
    st.session_state.setdefault('routine',[])
    st.session_state.setdefault('page',0)
    st.session_state.setdefault('ingredient_page',0)
    st.session_state.setdefault('ingredient_query','')

    with st.sidebar:
        st.markdown('<div class="brand">glow<span>guide</span> ✿</div><p class="small-note">A little care. A little confidence.</p>',unsafe_allow_html=True)
        st.divider()
        st.markdown('### Your skin, your rules')
        skin=st.selectbox('My skin type',list(SKIN_TYPES),index=2)
        concerns=st.multiselect('My skin goals',list(CONCERNS),default=['Dehydration'])
        budget=st.slider('Budget per product (PKR)',1000,150000,25000,1000,format='PKR %d')
        st.caption('Prices are estimates from your project dataset.')
        st.markdown('#### Ingredient preferences')
        exclusions=[]
        for label,col in [('No listed fragrance','has_fragrance'),('No denatured alcohol','has_alcohol_denat'),('No listed essential oils','has_essential_oil')]:
            if st.checkbox(label):exclusions.append(col)
        st.caption('Preferences use the ingredient flags recorded in the collection.')
        st.divider()
        st.markdown('<div class="eyebrow">MADE FOR YOUR KIND OF GLOW</div><p class="small-note">Explore thoughtfully. Save your favorites. Get to know what works for you.</p>',unsafe_allow_html=True)

    st.markdown('<div class="glow-header"><div class="brand">glow<span>guide</span> ✿</div><p class="header-tagline">YOUR PERSONAL SKIN EDIT</p></div>',unsafe_allow_html=True)
    hero=image_uri('glowguide-rose-editorial.jpg')
    style=f'background-image:linear-gradient(90deg,rgba(255,244,248,.97),rgba(255,244,248,.88) 35%,rgba(255,244,248,.08) 76%),url({hero})'
    st.markdown(f'<div class="hero photo-hero" style="{style}"><div class="eyebrow">A softer approach to skincare</div><h1>Meet your skin’s<br>next <em>favorite thing.</em></h1><p>Your skin, your ingredients, your personal edit. Discover the science behind your next skincare favorite.</p></div>',unsafe_allow_html=True)
    discover,wishlist,notebook,insights,about=st.tabs(['Discover your matches',f'My Wishlist ({len(st.session_state.routine)})','Ingredient Notebook','ML Insights','Behind the glow'])

    def product_image(row,small=False):
        gallery=row.get('photo_gallery',[])
        if gallery:
            pictures=''.join(f'<figure><img src="{image_uri(part["local_image"],thumbnail=True)}" alt="{escape(part["name"],quote=True)}" loading="lazy"></figure>' for part in gallery)
            cls='notebook-product-photo' if small else 'product-photo'
            return f'<div class="{cls} product-gallery">{pictures}<span class="photo-badge">{escape(row.photo_note)}</span></div>'
        src=image_uri(row.local_image,thumbnail=True) if row.local_image else ''
        if src:
            cls='notebook-product-photo' if small else 'product-photo'
            note=f'<span class="photo-badge">{escape(str(row.get("photo_note","")))}</span>' if row.get('photo_note','') else ''
            return f'<div class="{cls}"><img src="{src}" alt="{escape(str(row.brand)+" "+str(row["name"]),quote=True)}" loading="lazy">{note}</div>'
        return '<div class="photo-missing"><span>✿</span><strong>Photo not verified</strong><small>Original product photography is being checked.</small></div>'

    def wishlist_button(row,prefix):
        pid=str(row.product_id);saved=pid in st.session_state.routine
        if st.button('✓ Wishlisted · remove' if saved else '♡ Add to wishlist',key=f'{prefix}_{pid}',width='stretch',type='secondary' if saved else 'primary'):
            if saved:st.session_state.routine.remove(pid)
            else:st.session_state.routine.append(pid)
            st.rerun()

    def full_ingredients(row):
        st.markdown('<div class="inci-list">'+chips([x.strip() for x in row.ingredients_clean.split('|') if x.strip()])+'</div>',unsafe_allow_html=True)

    with discover:
        stats=engine.diagnostics
        cols=st.columns(3)
        for col,value,label in zip(cols,[f'{len(products):,}',f'{stats["tfidf_features"]:,}',str(stats['clusters'])],['products to explore','ingredient features in the model','K-means ingredient groups']):
            col.markdown(f'<div class="collection-stat"><strong>{value}</strong><span>{label}</span></div>',unsafe_allow_html=True)
        st.markdown('<div class="section-title">Your glow, with a little science.</div>',unsafe_allow_html=True)
        mode=st.radio('How would you like to explore?',['Find similar products','Explore collection'],horizontal=True,key='discovery_mode')
        reference_id=None
        if mode=='Find similar products':
            reference_id=st.selectbox('Start with a product you know',products.index.tolist(),index=6,format_func=lambda i:f'{products.loc[i,"brand"]} · {products.loc[i,"name"]}',key='reference_product')
            reference=products.loc[int(reference_id)]
            c1,c2=st.columns([1,5])
            c1.markdown(product_image(reference,small=True),unsafe_allow_html=True)
            c2.markdown(f'<div class="reference-note"><span class="eyebrow">YOUR INGREDIENT REFERENCE</span><h3>{escape(reference["name"])}</h3><p>{escape(reference.brand)} · {escape(reference.category)} · {money(reference.price_pkr)}</p><span class="product-tag">K-means group C{int(reference.Cluster)}</span></div>',unsafe_allow_html=True)
            st.caption('Matches follow Phase 4: ranked by ingredient cosine similarity, with a small bonus for each shared key ingredient (+0.05) and for the same K-means ingredient group (+0.03). Products below 8% similarity are not shown. Your skin profile and budget filter the candidate products.')
        else:
            st.caption('Browse the collection using your skin profile and preferences. Choose “Find similar products” for a calculated ingredient comparison.')
        st.caption('A match is a starting point: recorded labels and Logistic Regression predictions can be incomplete or incorrect. Ingredient similarity does not guarantee suitability. Open ML Insights to see model evaluation and limitations.')
        c1,c2,c3=st.columns([2,1.3,1.3])
        query=c1.text_input('Search products or ingredients',placeholder='Brand, product, or ingredient…',key='catalog_search')
        category=c2.selectbox('Product category',['All products']+sorted(products.category.unique()))
        order=c3.selectbox('Sort by',['Recommended','Ingredient similarity','Price: low to high','Price: high to low'] if reference_id is not None else ['Recommended','Price: low to high','Price: high to low'])
        b1,b2=st.columns([2,1])
        brands=b1.multiselect('Favorite brands',sorted(products.brand.unique()),placeholder='Explore all brands')
        photo_only=b2.checkbox('Verified photos only',value=False)
        signature=(skin,tuple(concerns),budget,tuple(exclusions),mode,reference_id,query,category,order,tuple(brands),photo_only)
        if st.session_state.get('filter_signature')!=signature:
            st.session_state.page=0;st.session_state.filter_signature=signature
        candidates=filter_catalog(products,skin,budget,category,query,brands,exclusions,photo_only)
        results=engine.rank(candidates,reference_id) if reference_id is not None else candidates.copy()
        if order=='Ingredient similarity':results=results.sort_values('Similarity',ascending=False,kind='stable')
        elif order.startswith('Price:'):results=results.sort_values('price_pkr',ascending=order=='Price: low to high',kind='stable')
        elif reference_id is None:
            results['Goal_Matches']=results.apply(lambda r:sum(flag(r,CONCERNS[c]) for c in concerns),axis=1)
            results=results.sort_values('Goal_Matches',ascending=False,kind='stable')
        st.caption(f'{len(results):,} matching products · {skin} skin · budget up to {money(budget)}')
        if results.empty:st.info('No matches in this edit. Try a higher budget, fewer filters, or another reference product. When a reference product is selected, only products with at least 8% ingredient similarity are shown.')
        else:
            pages=(len(results)+8)//9;st.session_state.page=min(st.session_state.page,pages-1)
            visible=results.iloc[st.session_state.page*9:(st.session_state.page+1)*9]
            for start in range(0,len(visible),3):
                for col,(_,row) in zip(st.columns(3),visible.iloc[start:start+3].iterrows()):
                    with col,st.container(border=True):
                        st.markdown(product_image(row),unsafe_allow_html=True)
                        st.markdown(f'<p class="product-brand">{escape(row.brand)}</p><div class="product-name">{escape(row["name"])}</div><span class="product-tag">{escape(row.category)}</span><span class="product-tag">{escape(skin)} skin</span><div class="product-price">{money(row.price_pkr)}</div>',unsafe_allow_html=True)
                        if reference_id is not None:
                            explanation=engine.explain(row,reference_id,skin);similarity=float(row.Similarity)*100
                            st.markdown(f'<div class="match-panel"><div class="match-heading">Why this product matches</div><div class="similarity-row"><span>Ingredient similarity</span><strong>{similarity:.1f}%</strong></div><div class="similarity-track" role="progressbar" aria-label="Ingredient cosine similarity" aria-valuenow="{similarity:.1f}" aria-valuemin="0" aria-valuemax="100"><div style="width:{similarity:.2f}%"></div></div><div class="chip-list">{chips(explanation["shared_actives"])}</div><p>{int(row.Shared_Actives)} shared key-ingredient flags · {len(explanation["common_ingredients"])} common ingredients</p><p>{escape(label_source(row))} matches {escape(skin.lower())} skin.</p></div>',unsafe_allow_html=True)
                            with st.expander('See the match evidence'):
                                st.write(f'Compared with {reference.brand} · {reference["name"]}.')
                                st.write(f'TF-IDF cosine similarity: {float(row.Similarity):.6f} on a 0–1 scale. The percentage is this value × 100, not a suitability probability.')
                                st.write(f'Shared key ingredients: {int(row.Shared_Actives)} of the reference product’s ten tracked key-ingredient flags are shared (+0.05 each).')
                                st.write(f'Ranking score: {float(row.Score):.3f} = similarity {float(row.Similarity):.3f} + 0.05 × {int(row.Shared_Actives)} shared key ingredients + 0.03 × same K-means group ({int(row.Same_Cluster)}).')
                                st.write(('Same K-means ingredient group' if explanation['same_cluster'] else 'Different K-means ingredient group')+f': candidate C{explanation["cluster"]}, reference C{explanation["reference_cluster"]}. The same group adds a small +0.03 to the ranking score.')
                                st.write('Shared ingredient entries')
                                st.markdown(chips(explanation['common_ingredients']),unsafe_allow_html=True)
                                if not explanation['shared_actives']:st.caption('This is a general ingredient-similarity match; no tracked key-ingredient flags are shared.')
                        else:
                            st.markdown('**Why this product?**')
                            st.caption(f'{label_source(row)} includes {skin.lower()} skin. Fits your PKR budget and selected filters. K-means ingredient group C{int(row.Cluster)}.')
                        goals=[c for c in concerns if flag(row,CONCERNS[c])]
                        if goals:st.caption('Recorded focus: '+', '.join(goals))
                        wishlist_button(row,'discover')
                        with st.expander('Ingredients & photo source'):
                            full_ingredients(row)
                            if row.photo_source:st.link_button('View photo source',row.photo_source,width='stretch')
                            if row.photo_documentation:st.caption(row.photo_documentation)
                            for part in row.photo_gallery:
                                st.link_button(part['name']+' photo source',part['source_url'])
                            st.caption('Product data and PKR prices are from the project export. Packaging editions and sizes may differ.')
            prev,number,nxt=st.columns([1,3,1])
            if prev.button('Previous',disabled=st.session_state.page==0,width='stretch'):
                st.session_state.page-=1;st.rerun()
            number.markdown(f'<p class="small-note" style="text-align:center">Page {st.session_state.page+1} of {pages}</p>',unsafe_allow_html=True)
            if nxt.button('Next',disabled=st.session_state.page==pages-1,width='stretch'):
                st.session_state.page+=1;st.rerun()

    with wishlist:
        st.markdown('### Your favorites, all in one place')
        st.write('Save your skincare favorites and explore them whenever you’re ready.')
        saved=products[products.product_id.isin(st.session_state.routine)]
        if saved.empty:st.info('Your wishlist is empty. Add a product from Discover to save your first favorite.')
        else:
            st.metric('Your wishlist · estimated total',money(saved.price_pkr.sum()))
            st.download_button('Download my wishlist (CSV)',saved[['brand','name','category','price_pkr','ingredients_clean']].to_csv(index=False).encode('utf-8'),'glowguide-wishlist.csv','text/csv')
            for _,row in saved.iterrows():
                with st.container(border=True):
                    c1,c2,c3=st.columns([1,4,1])
                    c1.markdown(product_image(row,small=True),unsafe_allow_html=True)
                    c2.markdown(f'**{row.brand} · {row["name"]}**')
                    c2.write(money(row.price_pkr));c2.caption(row.category+' · '+('Matches your current profile' if skin_matches(row,skin) else 'Saved for a different skin profile'))
                    if c3.button('Remove',key='wishlist_'+row.product_id):
                        st.session_state.routine.remove(row.product_id);st.rerun()

    with notebook:
        journal=image_uri('glowguide-textures-editorial.jpg')
        st.markdown(f'<section class="ingredient-intro"><div><div class="eyebrow">The Ingredient Notebook</div><h2>A closer look at<br>your skincare.</h2><p>Explore the names behind the formulas, discover shared ingredients, and build a more thoughtful skincare wishlist.</p></div><img src="{journal}" alt="Rose petals and skincare textures on blush glass"></section>',unsafe_allow_html=True)
        st.text_input('Find an ingredient',placeholder='Try niacinamide, ceramide, or glycerin…',key='ingredient_query')
        quick=st.columns(4)
        for col,name in zip(quick,['Niacinamide','Ceramide','Glycerin','Squalane']):
            col.button(name,key='quick_'+name,on_click=ingredient_callback,args=(name.casefold(),),width='stretch')
        term=st.session_state.ingredient_query.strip()
        if st.session_state.get('last_ingredient')!=term:
            st.session_state.ingredient_page=0;st.session_state.last_ingredient=term
        if not term:
            st.markdown('#### Start with a little curiosity')
            st.write('Search a listed ingredient to see the products that contain it. Each result highlights the matching ingredient names; full formulas stay neatly tucked into the details.')
        else:
            found=products[products.ingredients_clean.str.contains(term,case=False,regex=False)]
            st.caption(f'{len(found):,} products · {found.brand.nunique()} brands · ingredient-name search for “{term}”')
            if found.empty:st.info('No ingredient entries match that spelling. Try part of the ingredient name.')
            else:
                pages=(len(found)+5)//6;st.session_state.ingredient_page=min(st.session_state.ingredient_page,pages-1)
                visible=found.iloc[st.session_state.ingredient_page*6:(st.session_state.ingredient_page+1)*6]
                for _,row in visible.iterrows():
                    with st.container(border=True):
                        left,right=st.columns([1,5])
                        left.markdown(product_image(row,small=True),unsafe_allow_html=True)
                        right.markdown(f'<p class="product-brand">{escape(row.brand)}</p><h3 class="notebook-product-name">{escape(row["name"])}</h3><p class="small-note">{escape(row.category)} · {money(row.price_pkr)}</p>',unsafe_allow_html=True)
                        matched=[i for i in row.ingredients_clean.split('|') if term.casefold() in i.casefold()]
                        right.markdown(chips(matched,selected=True),unsafe_allow_html=True)
                        with st.expander('Read the full formula'):
                            full_ingredients(row)
                        wishlist_button(row,'notebook')
                p1,p2,p3=st.columns([1,3,1])
                if p1.button('Previous ingredients page',disabled=st.session_state.ingredient_page==0,width='stretch'):
                    st.session_state.ingredient_page-=1;st.rerun()
                p2.caption(f'Page {st.session_state.ingredient_page+1} of {pages}')
                if p3.button('Next ingredients page',disabled=st.session_state.ingredient_page==pages-1,width='stretch'):
                    st.session_state.ingredient_page+=1;st.rerun()

    with insights:
        st.markdown('### The science behind your glow')
        st.write('Explore the evaluation results saved by your updated classification notebook.')
        comparison_path=ROOT/'model_comparison.csv'
        if comparison_path.exists():
            comparison=pd.read_csv(comparison_path)
            st.subheader('KNN vs Logistic Regression')
            st.dataframe(comparison.round(3),hide_index=True,width='stretch')
            graph=comparison.set_index('Model')[['Accuracy','Micro F1','Macro F1']].T
            st.bar_chart(graph,color=['#C96C95','#86435F'],stack=False)
            conclusions=[]
            for metric in ['Accuracy','Micro F1','Macro F1']:
                scores=pd.to_numeric(comparison[metric],errors='coerce')
                if scores.notna().any():
                    best=scores.max()
                    leaders=comparison.loc[np.isclose(scores,best,rtol=0,atol=1e-12),'Model'].astype(str).tolist()
                    conclusions.append(f'{metric}: {" and ".join(leaders)} {"tie" if len(leaders)>1 else "leads"} ({best:.3f}).')
            st.success(' '.join(conclusions))
            lr=comparison.loc[comparison.Model.eq('Logistic Regression')]
            if not lr.empty:
                st.info(f'Logistic Regression was selected for predicting skin types of unlabelled products: it has the highest Macro F1 ({float(lr.iloc[0]["Macro F1"]):.3f}), which gives every skin type equal weight, and finds far more acne-prone products than KNN. KNN has a slightly higher Micro F1 because it predicts the common skin types more often.')
                st.warning('Performance varies across skin labels; sensitive and acne-prone are the hardest to predict. Predictions may be wrong and are not a guarantee that a product will suit your skin.')
            st.caption('Test-set scores from model_comparison.csv. Accuracy requires all six labels to match for a product. Micro F1 aggregates label decisions; Macro F1 gives each skin-type label equal weight. These scores are not individual product suitability probabilities.')
        else:
            st.info('Add model_comparison.csv to see the model evaluation results.')
        efficiency_path=ROOT/'model_efficiency.csv'
        if efficiency_path.exists():
            st.subheader('Speed, overfitting and model size')
            st.dataframe(pd.read_csv(efficiency_path),hide_index=True,width='stretch')
            st.caption('Gap = training Macro F1 minus test Macro F1; a larger gap means more overfitting. KNN trains almost instantly because it only stores the training data, but it is larger and slower at prediction time. Logistic Regression overfits more, yet still performs better on unseen products.')
        clustering_path=ROOT/'clustering_comparison.csv'
        if clustering_path.exists():
            st.subheader('K-Means vs DBSCAN')
            st.dataframe(pd.read_csv(clustering_path),hide_index=True,width='stretch')
            st.caption('ARI and NMI compare clusters with real labels (higher = closer match). K-Means relates more to product category; DBSCAN labels about half of the products as noise and its clusters mostly group products of the same brand. K-Means was selected.')
        st.markdown('#### Products per ingredient group')
        counts=products.Cluster.value_counts().sort_index()
        counts.index=[f'C{int(i)}' for i in counts.index]
        st.bar_chart(counts,color='#C96C95')
        st.caption('Logistic Regression supplies saved predictions for unlabelled products. K-means groups ingredient patterns; the group number is not a quality score. See Behind the glow for the complete recommendation pipeline.')

    with about:
        st.markdown('### The intelligence behind GlowGuide')
        st.write('A personal skincare edit grounded in your project’s actual ingredient features, skin labels, and trained model exports.')
        d=engine.diagnostics
        c1,c2,c3=st.columns(3)
        c1.metric('Original skin-label records',f'{d["labeled_products"]:,}')
        c2.metric('Predicted label records (Logistic Regression)',f'{d["predicted_label_products"]:,}')
        c3.metric('Ingredient-profile groups',d['clusters'])
        st.markdown('#### How a recommendation is made')
        for title,text in [('Your skin profile','Products must match the selected skin type and budget. Original skin labels are used where available; saved Logistic Regression predictions supply labels for unlabelled products.'),('Your ingredient reference',f'Choose a product to compare. Its ingredient list is represented in the project’s {d["tfidf_features"]:,}-feature TF-IDF vocabulary, and cosine similarity is calculated live against eligible products.'),('Your Phase 4 ranking','Score = cosine similarity + 0.05 for each shared key ingredient (of ten tracked flags) + 0.03 for the same K-means group. Similarity carries most of the weight. The reference itself and products below 8% similarity are excluded.'),('Your K-means context',f'The saved K-means export contains {d["clusters"]} ingredient-profile groups. Group IDs describe ingredient patterns; they are not skin diagnoses or skin-type names.')]:
            with st.container(border=True):st.markdown('**'+title+'**');st.write(text)
        st.markdown('#### Inside the ingredient groups')
        counts=products.Cluster.value_counts().sort_index()
        bars=''.join(f'<div class="cluster-row"><strong>C{int(k)}</strong><div><span style="width:{v/counts.max()*100:.1f}%"></span></div><span>{v} products</span></div>' for k,v in counts.items())
        st.markdown('<div class="cluster-chart">'+bars+'</div>',unsafe_allow_html=True)
        group=st.selectbox('Explore an ingredient group',counts.index.tolist(),format_func=lambda i:f'C{int(i)} · {int(counts.loc[i])} products',key='cluster_inspector')
        group_count,profile=engine.cluster_profile(group)
        feature_labels={**KEY_INGREDIENTS,'has_clay':'Clay','has_heavy_oil_butter':'Heavy oils / butters','has_fragrance':'Fragrance flag','has_alcohol_denat':'Denatured alcohol','has_essential_oil':'Essential oils'}
        positive=profile[profile>0].head(4)
        if positive.empty:st.caption('This group has no positive flags among the 11 tracked ingredient features.')
        else:
            st.markdown(chips([f'{feature_labels[col]} · {value:.0%} of group' for col,value in positive.items()]),unsafe_allow_html=True)
            st.caption('These percentages describe recorded feature frequency within this group; they are not ingredient concentrations or feature-importance scores.')
        st.markdown('#### Photo provenance')
        st.write(f'{int(products.has_photo.sum()):,} of {len(products):,} product records have locally saved, sourced product images. Sources include brand sites, retailers, and ingredient catalogs. Each photographed card links to its source; generic skincare photography is reserved for the editorial sections.')
        st.caption(f'{int(products.photo_verified.sum()):,} records have verified product or documented set-content images. One Algenist reference gallery has an unconfirmed dataset edition and is excluded from Verified photos only.')
        with st.expander('For your project presentation: reproducibility'):
            st.write('Phase 1: ingredient preparation and TF-IDF. Phase 2: K-means groups on TF-IDF reduced with TruncatedSVD (compared with DBSCAN). Phase 3: Logistic Regression skin-label predictions (compared with KNN). Phase 4: live reference-product ranking in this app.')
            st.write(f'Verified matrix-to-catalog row alignment: {d["products"]:,} rows × {d["tfidf_features"]:,} features. The app stops with an error if the feature rows and catalog no longer align.')
            st.latex(r'\operatorname{cosine}(a,b)=\frac{a\cdot b}{\lVert a\rVert_2\lVert b\rVert_2}')
            st.write('Model training remains in the project notebooks. The app uses their exported results; it does not retrain models for each visit. The same K-means group adds only a small +0.03 to the ranking score.')
            st.write('Cosine percentages measure ingredient-vector similarity. They are not probabilities of safety, effectiveness, or skin suitability. Ingredient presence does not reveal concentration.')
        st.caption('PKR prices are dataset estimates, not live retail quotes. Check current product labels and patch test new products. Editorial images were created for GlowGuide and are separate from sourced product photos.')
    st.markdown('<div class="footer"><span>glowguide ✿ &nbsp; A little care goes a long way.</span><span>Made for your own kind of glow.</span></div>',unsafe_allow_html=True)
