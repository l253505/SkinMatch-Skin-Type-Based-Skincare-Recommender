"""Validate product identity and download packaging photos from saved search results.

Search evidence is retained in photo_search_parts/. Existing photos are preserved.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import html
import json
import re
import threading
import sys
import time
import unicodedata

from PIL import Image
import pandas as pd
import requests
from sync_product_photos import key

ROOT = Path(__file__).resolve().parent
LOCAL = threading.local()
STOP = {'a','an','the','and','with','for','of','to','by','in','on','your','product','products','photo','packaging','face','facial','skin','skincare','moisturizer','moisturiser','anti','aging','ageing','ml','oz','size','firming','lifting','hydrating','hydration','deep','brightening','smoothing','correcting','glowing','nourishing','reviving','retexturizing','defying','age','minimizing','balancing','natural','rapid','gentle','puffing','depuffing'}
SYNONYMS = {'moisturising':'moisturizing','ageing':'aging','cleansing':'cleanser','replenishment':'replenishing','defence':'defense','minimiser':'minimizer','antiaging':'aging','creme':'cream','crème':'cream','travel':'mini'}
ALIAS_PATH=ROOT/'product_photo_name_aliases.json'
ALIASES={key(r['brand'],r['name']):r['photo_name'] for r in json.loads(ALIAS_PATH.read_text(encoding='utf-8'))} if ALIAS_PATH.exists() else {}
DISPLAY_NAMES={key(r['brand'],r['name']):r['display_name'] for r in json.loads(ALIAS_PATH.read_text(encoding='utf-8')) if r.get('display_name')} if ALIAS_PATH.exists() else {}
REJECTED_PATH=ROOT/'photo_rejected_image_hashes.json'
REJECTED_HASHES=set(json.loads(REJECTED_PATH.read_text(encoding='utf-8'))) if REJECTED_PATH.exists() else set()
BRAND_NAMES={'Dr. Lara Devgan Scientific Beauty':'Dr. Lara Devgan','Shani Darden Skin Care':'Shani Darden','Saint Jane Beauty':'Saint Jane','REN Clean Skincare':'REN','Sephora Collection':'Sephora','St. Tropez Tanning Essentials':'St. Tropez','Skin Inc Supplement Bar':'Skin Inc','Dr. Brandt Skincare':'Dr. Brandt'}

def form_conflict(product,candidate):
    original=DISPLAY_NAMES.get(key(product['brand'],product['name']),product['name'])
    target=words(original)
    source=words(candidate.get('caption',''))
    all_source=words(candidate.get('title','')+' '+candidate.get('caption',''))
    mixed=bool(target & {'kit','set','duo','bundle','collection','medley','haves','frenzy'})
    target_mask='mask' in original.casefold() or 'masque' in original.casefold()
    if not mixed:
        if source & {'mask','masque'} and not target_mask and product.get('category')!='Mask':return True
        if source & {'cleanser','wash','soap'} and not target & {'cleanser','wash','soap'} and product.get('category')!='Cleanser':return True
        if source & {'toner','tonic'} and not target & {'toner','tonic','micellar','water'}:return True
        if product.get('category')=='Moisturizer' and source & {'cleanser','toner'} and not target & {'cleanser','toner'}:return True
        if source & {'bb','cc'} and not target & {'bb','cc','balm'}:return True
        for area in ['eye','lip','body','hand','foot','scalp']:
            if area in source and area not in target and not (area=='eye' and product.get('category')=='Eye care'):return True
    if 'oily' in target and 'dry' not in target and 'dry' in all_source:return True
    if 'dry' in target and 'oily' not in target and 'oily' in all_source:return True
    if 'unscented' in target and all_source & {'cucumber','coconut','mango','citrus'}:return True
    return False

def words(text):
    text = unicodedata.normalize('NFKD', re.sub(r"[™®℠'’]", '', html.unescape(str(text)))).encode('ascii','ignore').decode().casefold()
    text = re.sub(r'(?<=[a-z])\.(?=[a-z]|\s|$)', '', text)
    text = re.sub(r'\b(?:retinol|vitamin\s+c)\s+alternative\b', '', text)
    return {SYNONYMS.get(t,t) for t in re.findall(r'[a-z]+|\d+(?:\.\d+)?',text)} - STOP

def score(product, candidate):
    if form_conflict(product,candidate):return 0
    brand_name=BRAND_NAMES.get(product['brand'],product['brand'])
    brand = words(brand_name)
    heading = candidate.get('title','') + ' ' + candidate.get('caption','')
    identity=candidate.get('caption','') if candidate.get('origin') in {'product schema','product image alt text'} else heading
    evidence = words(identity)
    brand_evidence=words(heading)
    parent_brand_match=product['brand']=='INC.redible' and key('','Nails Inc') in key('',heading)
    if not parent_brand_match and not brand.issubset(brand_evidence) and key('',brand_name) not in key('',heading):
        return 0
    match_name=ALIASES.get(key(product['brand'],product['name']),product['name'])
    wanted = words(match_name) - brand
    if not wanted:
        return 0
    target_spfs = set(re.findall(r'spf\s*(\d+)', product['name'], re.I))
    source_spfs = set(re.findall(r'spf\s*(\d+)', heading, re.I))
    if target_spfs and source_spfs and not source_spfs.issubset(target_spfs):
        return 0
    # Preserve body-area and named variant identity.
    manual_alias=key(product['brand'],product['name']) in ALIASES
    for term in ['eye','lip','body','hand','foot','scalp','rich','tinted','untinted','kids','baby','mini','travel','refill']:
        if not manual_alias and (term in wanted) != (term in evidence):
            return 0
    # Explicitly conflicting active strengths are not interchangeable.
    target_strengths = set(re.findall(r'(\d+(?:\.\d+)?)\s*%', product['name']))
    source_strengths = set(re.findall(r'(\d+(?:\.\d+)?)\s*%', heading))
    if target_strengths and source_strengths and source_strengths != target_strengths:
        return 0
    coverage = len(wanted & evidence) / len(wanted)
    compact_match=key('',match_name) in key('',identity)
    if compact_match:coverage=1.0
    if manual_alias and not compact_match and not wanted.issubset(evidence):
        return 0
    core=re.split(r'\s+with\s+',match_name,flags=re.I)[0]
    core_words=words(core)-brand
    if core!=match_name and len(core_words)>=2 and core_words.issubset(evidence):
        coverage=max(coverage,.95)
    if coverage < .8 or len(wanted & evidence) < min(2,len(wanted)):
        return 0
    # Avoid selecting category collages, comparisons or unrelated source pages.
    if not candidate.get('source_url') or any(t in candidate['title'].lower() for t in [' vs ', 'best products', 'top 10','dupes','alternatives','review of 10']):
        return 0
    return coverage + (.05 if key(product['brand'],product['name']) in key('',heading) else 0)

def fetch_image(product):
    ranked = sorted([(score(product,c),c) for c in product.get('candidates',[])],key=lambda x:x[0],reverse=True)
    seen=set();attempts=0
    for confidence, candidate in ranked:
        if not confidence:
            continue
        candidate_url=html.unescape(candidate['image_url'])
        if candidate_url in seen or 'jomashop.com/' in candidate_url:continue
        seen.add(candidate_url)
        if attempts>=12:break
        attempts+=1
        try:
            if not hasattr(LOCAL,'session'):
                LOCAL.session=requests.Session()
                LOCAL.session.headers['User-Agent']='GlowGuide product-photo collector/1.0'
            url=html.unescape(candidate['image_url'])
            if not url.startswith('https://'):
                continue
            response=LOCAL.session.get(url,timeout=15)
            response.raise_for_status()
            if len(response.content)>12_000_000:
                continue
            if sha256(response.content).hexdigest() in REJECTED_HASHES:continue
            photo=Image.open(BytesIO(response.content))
            if min(photo.size)<100 or photo.format not in {'JPEG','PNG','WEBP','AVIF'}:
                continue
            photo.verify()
            ext={'JPEG':'.jpg','PNG':'.png','WEBP':'.webp','AVIF':'.avif'}[photo.format]
            product_key=key(product['brand'],product['name'])
            filename=sha256(product_key.encode()).hexdigest()[:24]+ext
            path=ROOT/'assets'/'products'/filename
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(response.content)
            return product_key, {'brand':product['brand'],'name':product['name'],'display_name':DISPLAY_NAMES.get(product_key,product['name']),'image_url':url,'local_image':'products/'+filename,'source_url':candidate['source_url'],'source_title':candidate['title'],'image_caption':candidate.get('caption',''),'match_method':'reviewed canonical product name and brand; decoded image checked' if product_key in ALIASES else 'brand and product name corroborated in source; decoded image checked','checked_at':datetime.now(timezone.utc).isoformat()}
        except (requests.RequestException,OSError,ValueError):
            continue
    return None

def save_photos(path,photos):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(photos,ensure_ascii=False,indent=2),encoding='utf-8')
    # OneDrive can briefly hold a Windows file handle during synchronization.
    for attempt in range(30):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt==29:raise
            time.sleep(.3)

def main():
    path=ROOT/'product_photos.json'
    photos=json.loads(path.read_text(encoding='utf-8'))
    photos={key(v['brand'],v['name']):v for v in photos.values()}
    watch='--watch' in sys.argv
    attempted=set()
    added=0
    while True:
        rows={}
        for part in sorted((ROOT/'photo_search_parts').glob('*.json')):
            try:
                for product in json.loads(part.read_text(encoding='utf-8')):
                    product_key=key(product['brand'],product['name'])
                    if product_key in rows:
                        seen={c['image_url'] for c in rows[product_key].get('candidates',[])}
                        rows[product_key]['candidates'].extend(c for c in product.get('candidates',[]) if c['image_url'] not in seen)
                    else:
                        rows[product_key]=product
            except (ValueError,OSError):
                continue
        pending=[r for k,r in rows.items() if k not in photos and k not in attempted]
        if pending:
            print(f'Validating {len(pending)} new product searches',flush=True)
            with ThreadPoolExecutor(max_workers=6) as pool:
                for i,future in enumerate(as_completed([pool.submit(fetch_image,r) for r in pending]),1):
                    result=future.result()
                    if result:
                        photos[result[0]]=result[1]
                        added+=1
                    if i%20==0:
                        save_photos(path,photos)
            attempted.update(key(r['brand'],r['name']) for r in pending)
            save_photos(path,photos)
            print(f'Added {added} photos this run; total {len(photos)} sourced photo matches',flush=True)
        if not watch or (ROOT/'photo_search_complete.json').exists() and not pending:
            break
        time.sleep(3)
    save_photos(path,photos)
    df=pd.read_csv(ROOT/'skinmatch_recommendations.csv').fillna('')
    covered=df.apply(lambda r:key(r.brand,r['name']) in photos,axis=1)
    df.loc[~covered,['brand','name','category']].to_csv(ROOT/'products_missing_photos.csv',index=False)
    report_path=ROOT/'photo_coverage.json'
    report=json.loads(report_path.read_text())
    report.update(sourced_product_records=int(covered.sum()),unverified_photo_records=int((~covered).sum()),unique_photo_matches=len(photos))
    report_path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(f'Finished: added {added}; {int(covered.sum())}/{len(df)} records now have product photos',flush=True)

if __name__=='__main__':
    main()
