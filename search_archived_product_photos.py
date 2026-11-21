"""Search public product records for names omitted from current brand listings."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from urllib.parse import urlencode,urljoin
import json
import pandas as pd
from find_archived_product_photos import fetch,BASE
from resolve_product_photo_search import score,ALIASES
from sync_product_photos import key

ROOT=Path(__file__).resolve().parent

def search(product):
    name=ALIASES.get(key(product['brand'],product['name']),product['name'])
    q=product['brand']+' '+name
    soup=fetch(BASE+'/products?'+urlencode({'q':q}))
    found=[]
    if soup:
        for a in soup.select('a[href^="/product/"]'):
            image=a.find('img',src=True)
            if not image:continue
            caption=image.get('alt','')
            candidate={'title':caption,'caption':caption,'image_url':image['src'],'source_url':urljoin(BASE,a['href'])}
            if candidate['image_url'].startswith('https://images.skinwis.com/') and score(product,candidate)>0:found.append(candidate)
    return dict(product,candidates=found)

def main():
    df=pd.read_csv(ROOT/'skinmatch_recommendations.csv').fillna('');photos=json.loads((ROOT/'product_photos.json').read_text(encoding='utf-8'))
    missing={key(r.brand,r['name']):{'brand':r.brand,'name':r['name'],'category':r.category} for _,r in df.iterrows() if key(r.brand,r['name']) not in photos}
    print(f'Searching exact archive records for {len(missing)} products',flush=True)
    rows=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for future in as_completed([pool.submit(search,r) for r in missing.values()]):
            r=future.result();rows.append(r)
            if r['candidates']:print(f"Found: {r['brand']} / {r['name']}",flush=True)
    (ROOT/'photo_search_parts'/'archive-exact-search.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Finished: {sum(bool(r["candidates"]) for r in rows)} more exact archive matches',flush=True)

if __name__=='__main__':main()
