"""Refresh verified photo URLs from the project's original product pages.

Run manually: python sync_product_photos.py
Only product-level JSON-LD photos are accepted; generic social images are ignored.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
import json
import re
import unicodedata

import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent

def key(brand, name):
    text = re.sub(r"[™®℠]", "", f"{brand} {name}")
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", text).casefold())

def product_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from product_nodes(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        if "Product" in (types if isinstance(types, list) else [types]):
            yield value
        if "@graph" in value:
            yield from product_nodes(value["@graph"])

def fetch_photo(record):
    brand, name, url = record
    try:
        response = requests.get(url, timeout=18, headers={"User-Agent": "GlowGuide/1.0 product-photo metadata"})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                nodes = list(product_nodes(json.loads(script.get_text())))
            except (ValueError, TypeError):
                continue
            for node in nodes:
                # Reject recommendations embedded in the same page.
                if key("", node.get("name", "")) not in {key("", name), key(brand, name)}:
                    continue
                images = node.get("image", [])
                if not isinstance(images, list):
                    images = [images]
                for image in images:
                    if isinstance(image, dict):
                        image = image.get("url", image.get("contentUrl", ""))
                    if isinstance(image, str) and image:
                        image = urljoin(response.url, image)
                        if image.startswith("https://"):
                            return key(brand, name), {"image_url": image, "source_url": url, "brand": brand, "name": name, "checked_at": datetime.now(timezone.utc).isoformat()}
    except requests.RequestException:
        pass
    return None

def main():
    products = pd.read_csv(ROOT / "skinmatch_recommendations.csv").fillna("")
    source = pd.read_csv(ROOT / "combined_filled.csv").fillna("")
    wanted = {key(r.brand, r['name']) for _, r in products.iterrows()}
    records = list(dict.fromkeys((str(r.brand), str(r['name']), str(r.url)) for _, r in source.iterrows() if str(r.url).startswith("https://") and key(r.brand, r['name']) in wanted))
    path = ROOT / "product_photos.json"
    photos = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    pending = [r for r in records if key(r[0], r[1]) not in photos]
    print(f"Checking {len(pending)} original product pages", flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, future in enumerate(as_completed([pool.submit(fetch_photo, r) for r in pending]), 1):
            result = future.result()
            if result:
                photos[result[0]] = result[1]
            if i % 20 == 0:
                print(f"Checked {i}/{len(pending)}; {len(photos)} verified photo links", flush=True)
                path.write_text(json.dumps(photos, ensure_ascii=False, indent=2), encoding="utf-8")
    path.write_text(json.dumps(photos, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(photos)} exact product photo links", flush=True)

if __name__ == "__main__":
    main()
