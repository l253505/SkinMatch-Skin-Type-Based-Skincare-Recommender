"""Expand exact-name image coverage from public INCI Decoder brand catalogs.

Resumable; keeps source URLs and an audit of unresolved products.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
import json
import re
import time
import unicodedata
import threading
import sys

import pandas as pd
import requests
from bs4 import BeautifulSoup
from sync_product_photos import key

ROOT = Path(__file__).resolve().parent
BASE = "https://incidecoder.com"
LOCAL = threading.local()
BRAND_ALIASES = {"Dr. Brandt Skincare":"dr-brandt", "Dr. Lara Devgan Scientific Beauty":"dr-lara-devgan", "Olehenriksen":"ole-henriksen", "REN Clean Skincare":"ren", "SOBEL SKIN Rx":"sobel-skin", "TULA Skincare":"tula", "Neogen Dermalogy":"neogen", "St. Tropez Tanning Essentials":"st-tropez"}

def slug(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text.replace("'", "").replace("’", "")).strip("-")

def read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

def save_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)

def fetch(url):
    try:
        if not hasattr(LOCAL, "session"):
            LOCAL.session = requests.Session()
            LOCAL.session.headers["User-Agent"] = "GlowGuide research project photo matching/1.0"
        response = LOCAL.session.get(url, timeout=15)
        if response.status_code in (403, 429):
            return None  # Respect access/rate limiting; no proxy or bypass.
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")
    except requests.RequestException:
        return None

def brand_catalog(brand):
    catalog = {}
    url = f"{BASE}/brands/{BRAND_ALIASES.get(brand, slug(brand))}"
    for _ in range(30):
        soup = fetch(url)
        if soup is None:
            break
        links = soup.select('a[href^="/products/"]')
        for link in links:
            title = link.get_text(" ", strip=True)
            catalog[key("", title)] = {"title": title, "url": urljoin(BASE, link["href"])}
        next_link = next((a for a in soup.find_all("a", href=True) if "Next page" in a.get_text()), None)
        if not next_link:
            break
        url = urljoin(BASE, next_link["href"])
        time.sleep(.2)
    return brand, catalog

def exact_photo(record):
    brand, name, url = record[:3]
    method = record[3] if len(record) > 3 else "exact normalized brand and product name"
    soup = fetch(url)
    if soup is None:
        return None
    for image in soup.find_all("img", src=True):
        src = image["src"]
        if "incidecoder-content.storage.googleapis.com/" in src and "/products/" in src and "front_photo" in src:
            return key(brand, name), {"image_url":src, "source_url":url, "brand":brand, "name":name, "match_method":method, "checked_at":datetime.now(timezone.utc).isoformat()}
    return None

def main():
    supplement = "--supplement" in sys.argv
    df = pd.read_csv(ROOT / "skinmatch_recommendations.csv").fillna("")
    photos_path = ROOT / ("product_photos_supplement.json" if supplement else "product_photos.json")
    photos = read_json(photos_path)
    photos = {key(v['brand'], v['name']):v for v in photos.values()}
    baseline = read_json(ROOT / "product_photos.json") if supplement else {}
    baseline_keys = {key(v['brand'], v['name']) for v in baseline.values()}
    catalog_path = ROOT / "photo_catalog_cache.json"
    catalogs = read_json(catalog_path)
    catalogs = {b:{key('',v['title']):v for v in values.values()} for b,values in catalogs.items()}
    brands = [b for b in df.brand.unique() if b not in catalogs or (not catalogs[b] and b in BRAND_ALIASES)]
    print(f"Reading {len(brands)} brand catalogs; {len(photos)} product photos already saved", flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        for i, future in enumerate(as_completed([pool.submit(brand_catalog, b) for b in brands]), 1):
            brand, catalog = future.result()
            catalogs[brand] = catalog
            if i % 10 == 0:
                save_json(catalog_path, catalogs)
                print(f"Catalogs {i}/{len(brands)}; {sum(len(v) for v in catalogs.values())} product names indexed", flush=True)
    save_json(catalog_path, catalogs)
    pending = []
    for _, row in df.iterrows():
        if key(row.brand, row['name']) in photos or key(row.brand, row['name']) in baseline_keys:
            continue
        catalog = catalogs.get(row.brand, {})
        if supplement and row.brand not in BRAND_ALIASES:
            def legacy_key(text):
                return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', text).casefold())
            if legacy_key(row.brand + ' ' + row['name']) in {legacy_key(v['title']) for v in catalog.values()}:
                continue  # The main collector is handling this exact-name record.
        match = catalog.get(key(row.brand, row['name']))
        method = "exact normalized brand and product name"
        if not match:
            # Accept unique equivalent names differing only in generic marketing words.
            # Numeric strengths and meaningful variant words remain mandatory.
            stop = {'the','a','an','and','with','for','face','facial','skin','skincare','moisturizer','moisturiser','makeup','remover','removing','rinse','off','formula'}
            def tokens(text):
                text = unicodedata.normalize('NFKD', re.sub(r'[™®℠]', '', text)).casefold()
                return set(re.findall(r'[a-z]+|\d+(?:\.\d+)?', text)) - stop
            wanted = tokens(row.brand + ' ' + row['name'])
            candidates = [v for v in catalog.values() if len(wanted)>=3 and tokens(v['title'])==wanted]
            if len(candidates) == 1:
                match = candidates[0]
                method = "unique equivalent product name; generic descriptors omitted"
        if match:
            pending.append((row.brand, row['name'], match['url'], method))
    pending = list(dict.fromkeys(pending))
    print(f"Found {len(pending)} exact additional catalog matches; fetching their photos", flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        for i, future in enumerate(as_completed([pool.submit(exact_photo, r) for r in pending]), 1):
            result = future.result()
            if result:
                photos[result[0]] = result[1]
            if i % 30 == 0:
                save_json(photos_path, photos)
                print(f"Photos checked {i}/{len(pending)}; {len(photos)} photos saved", flush=True)
    save_json(photos_path, photos)
    if supplement:
        print(f"Finished: {len(photos)} supplemental product photo matches", flush=True)
        return
    missing = df[~df.apply(lambda r:key(r.brand,r['name']) in photos, axis=1)][['brand','name','category']]
    missing.to_csv(ROOT / "products_missing_photos.csv", index=False)
    print(f"Finished: {len(photos)} photo matches; {len(missing)} product records unresolved", flush=True)

if __name__ == "__main__":
    main()
