"""Extract matching product photos from the remaining verified product-page results."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from urllib.parse import urljoin
import json,re,threading,time
import requests
from bs4 import BeautifulSoup
from resolve_product_photo_search import score

ROOT=Path(__file__).resolve().parent
LOCAL=threading.local()

def nodes(data):
    if isinstance(data,list):
        for x in data:yield from nodes(x)
    elif isinstance(data,dict):
        kind=data.get('@type',[])
        if isinstance(kind,str):kind=[kind]
        if set(kind)&{'Product','ProductGroup'}:yield data
        for name in ['@graph','hasVariant','mainEntity']:
            if name in data:yield from nodes(data[name])

def product_page(row):
    candidates=[]
    started=time.monotonic()
    if not hasattr(LOCAL,'session'):LOCAL.session=requests.Session()
    for page in row.get('pages',[])[:8]:
        if time.monotonic()-started>35:break
        url=page['url']
        if 'jomashop.com/' in url or '.pdf' in url.lower():continue
        try:
            with LOCAL.session.get(url,timeout=(4,4),stream=True) as response:
                if response.status_code!=200:continue
                content_type=response.headers.get('Content-Type','')
                if not any(t in content_type for t in ['text/html','application/xhtml']):continue
                chunks=[];size=0
                for chunk in response.iter_content(16384):
                    chunks.append(chunk);size+=len(chunk)
                    if size>=2_000_000 or time.monotonic()-started>35:break
                soup=BeautifulSoup(b''.join(chunks),'html.parser')
        except requests.RequestException:continue
        title=soup.title.get_text(' ',strip=True) if soup.title else page['title']
        for script in soup.find_all('script',type='application/ld+json'):
            try:products=list(nodes(json.loads(script.get_text())))
            except (ValueError,TypeError):continue
            for p in products:
                brand=p.get('brand',{})
                if isinstance(brand,dict):brand=brand.get('name','')
                name=str(p.get('name',''))
                images=p.get('image',[])
                if not isinstance(images,list):images=[images]
                for image in images[:3]:
                    if isinstance(image,dict):image=image.get('url',image.get('contentUrl',''))
                    if not isinstance(image,str) or not image:continue
                    c={'title':f'{brand} {name} | {title}','caption':f'{brand} {name}','image_url':urljoin(response.url,image),'source_url':url,'origin':'product schema'}
                    if score(row,c)>0:candidates.append(c)
        for img in soup.find_all('img'):
            alt=img.get('alt','')
            image=img.get('src') or img.get('data-src')
            if not image or image.startswith('data:'):continue
            if re.search(r'logo|placeholder|favicon|avatar|no[_-]?image',image,re.I):continue
            c={'title':title,'caption':alt,'image_url':urljoin(response.url,image),'source_url':url,'origin':'product image alt text'}
            if score(row,c)>0:candidates.append(c)
        meta=soup.find('meta',property='og:image')
        if meta and meta.get('content'):
            image=urljoin(response.url,meta['content'])
            commerce=any(p in url for p in ['/product/','/products/','/listing/','/shop/','/item/','/itm/'])
            if commerce and not re.search(r'logo|placeholder|favicon|avatar|homepage|social-share',image,re.I):
                c={'title':title,'caption':page['title'],'image_url':image,'source_url':url,'origin':'matching product-page social image'}
                if score(row,c)>0:candidates.append(c)
        if candidates:break
    return {**row,'candidates':candidates}

def main():
    rows=json.loads((ROOT/'product_page_searches.json').read_text(encoding='utf-8'))
    output_path=ROOT/'photo_search_parts'/'direct-product-page-images.json'
    previous=json.loads(output_path.read_text(encoding='utf-8')) if output_path.exists() else []
    done={(r['brand'],r['name']) for r in previous}
    rows=[r for r in rows if (r['brand'],r['name']) not in done]
    print(f'Extracting exact photos from product pages for {len(rows)} remaining products',flush=True)
    output=previous
    with ThreadPoolExecutor(max_workers=6) as pool:
        for future in as_completed([pool.submit(product_page,row) for row in rows]):
            r=future.result();output.append(r)
            output_path.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
            if r['candidates']:print(f"Found {len(r['candidates'])} images: {r['brand']} / {r['name']}",flush=True)
    (ROOT/'photo_search_parts'/'direct-product-page-images.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Product-page extraction complete: {sum(bool(r["candidates"]) for r in output)} products',flush=True)

if __name__=='__main__':main()
