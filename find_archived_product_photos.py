"""Find exact older packaging photos in SkinWis public brand catalogs."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from urllib.parse import urljoin
import json,re,threading,unicodedata
import pandas as pd
import requests
from bs4 import BeautifulSoup
from sync_product_photos import key
from resolve_product_photo_search import score,BRAND_NAMES

ROOT=Path(__file__).resolve().parent
BASE='https://www.skinwis.com'
LOCAL=threading.local()

def fetch(url):
    if not hasattr(LOCAL,'session'):
        LOCAL.session=requests.Session()
    try:
        r=LOCAL.session.get(url,timeout=20)
        if r.status_code!=200:return None
        return BeautifulSoup(r.text,'html.parser')
    except requests.RequestException:return None

def slug(s):
    s=unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','-',s.replace("'",'').replace('’','')).strip('-')

def collect_brand(brand,products,brand_url):
    candidates=[];url=brand_url;matched=set()
    for page in range(80):
        soup=fetch(url)
        if soup is None:break
        for a in soup.select('a[href^="/product/"]'):
            heading=a.find('h3');image=a.find('img',src=True)
            if not heading or not image:continue
            name=heading.get_text(' ',strip=True)
            caption=image.get('alt','')
            candidate={'title':caption,'caption':caption,'image_url':image['src'],'source_url':urljoin(BASE,a['href'])}
            if not candidate['image_url'].startswith('https://images.skinwis.com/'):continue
            for product in products:
                if score(product,candidate)>0:
                    product_key=key(product['brand'],product['name'])
                    matched.add(product_key)
                    candidates.append((product_key,candidate))
        if len(matched)==len(products):break
        nxt=next((a for a in soup.find_all('a',href=True) if a.get_text(' ',strip=True)=='Next'),None)
        if not nxt:break
        url=urljoin(BASE,nxt['href'])
    return brand,candidates

def main():
    df=pd.read_csv(ROOT/'skinmatch_recommendations.csv').fillna('')
    photos=json.loads((ROOT/'product_photos.json').read_text(encoding='utf-8'))
    missing=[{'brand':r.brand,'name':r['name'],'category':r.category} for _,r in df.iterrows() if key(r.brand,r['name']) not in photos]
    groups={}
    for r in missing:groups.setdefault(r['brand'],{})[key(r['brand'],r['name'])]=r
    brand_index={}
    soup=fetch(BASE+'/brands')
    if soup:
        for a in soup.select('a[href^="/brand/"]'):
            brand_index[key('',a.get_text(' ',strip=True))]=urljoin(BASE,a['href'])
    print(f'Checking archived photos for {len(missing)} records across {len(groups)} brands',flush=True)
    all_candidates={key(r['brand'],r['name']):dict(r,candidates=[]) for r in missing}
    with ThreadPoolExecutor(max_workers=6) as pool:
        tasks=[]
        for brand,products in groups.items():
            url=brand_index.get(key('',brand)) or brand_index.get(key('',BRAND_NAMES.get(brand,brand))) or BASE+'/brand/'+slug(brand)
            tasks.append(pool.submit(collect_brand,brand,list(products.values()),url))
        for future in as_completed(tasks):
            brand,candidates=future.result()
            for product_key,candidate in candidates:all_candidates[product_key]['candidates'].append(candidate)
            print(f'{brand}: {len(set(k for k,_ in candidates))} exact/equivalent product names found',flush=True)
    out=ROOT/'photo_search_parts'/'archived-skinwis.json'
    out.write_text(json.dumps(list(all_candidates.values()),ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Archive candidate file saved: {sum(bool(r["candidates"]) for r in all_candidates.values())} product matches',flush=True)

if __name__=='__main__':main()
