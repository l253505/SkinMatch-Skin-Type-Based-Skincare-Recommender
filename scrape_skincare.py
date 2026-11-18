"""
Skincare product scraper (course project: SkinMatch).

What it does, per brand:
  1. Reads robots.txt and only fetches allowed URLs (skips /es/ etc.).
  2. Finds candidate URLs from the sitemap (or your manual_urls).
  3. Fetches each page politely and keeps ONLY real product pages
     (pages with schema.org "Product" data), so category pages are dropped.
  4. Extracts: name, category, ingredient list (from the ingredients section),
     and skin types (from the product DESCRIPTION only, not menus/filters).
  5. Appends rows to skincare_products.csv (safe to stop and re-run: it resumes).

Install:  pip install requests beautifulsoup4 lxml
Run:      python scrape_skincare.py
          (in Jupyter:  %run scrape_skincare.py)

FIRST RUN: keep MAX_PRODUCTS_PER_BRAND small (15) and check the CSV by eye.
If ingredients or skin_types look wrong, open the saved pages in debug_html/
and adjust the extraction functions. Then raise the limit and run again.
"""

import csv
import html as htmllib
import json
import os
import random
import re
import time
import urllib.robotparser
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# ---------------- configuration ----------------
# Per-brand options:
#   base         site root
#   manual_urls  product URLs you paste yourself (used first if not empty)
#   seeds        listing pages to crawl for product links when the sitemap gives nothing
#   product_re   regex a product URL must match (keeps categories/articles out)
#   shopify      True = the shop runs on Shopify; read its public /products.json
#   collection   (Shopify) collection handle, e.g. only the Jenpharm products of a retailer
#   currency     currency of the prices on that site
# BEFORE scraping any brand: read its Terms of Use (footer link). robots.txt alone is not permission.
SITES = {
    "CeraVe":   {"base": "https://www.cerave.com",   "manual_urls": [], "currency": "USD",
                 "seeds": ["https://www.cerave.com/skincare", "https://www.cerave.com/skincare/cleansers",
                           "https://www.cerave.com/skincare/moisturizers", "https://www.cerave.com/skincare/sunscreen",
                           "https://www.cerave.com/skincare/treatments"],
                 "product_re": r"^/skincare/[^/]+/[^/]+"},
    "Cetaphil": {"base": "https://www.cetaphil.com", "manual_urls": [], "currency": "USD",
                 "seeds": ["https://www.cetaphil.com/us/"], "product_re": r"/us/.+"},
    "Neutrogena": {"base": "https://www.neutrogena.com", "manual_urls": [], "currency": "USD",
                   "seeds": ["https://www.neutrogena.com/skin-care.html", "https://www.neutrogena.com/"],
                   "product_re": r"/products?/|\.html$"},
    "Aveeno": {"base": "https://www.aveeno.com", "manual_urls": [], "currency": "USD",
               "seeds": ["https://www.aveeno.com/products", "https://www.aveeno.com/"],
               "product_re": r"/products?/"},
    "The Ordinary": {"base": "https://theordinary.com", "manual_urls": [], "currency": "USD",
                     # listing page shows 94 products; sz=100 asks for them all at once.
                     # If only 12 come back, add start=12, start=24 ... seeds (sz=12).
                     "seeds": ["https://theordinary.com/en-pk/category/skincare?start=0&sz=100"],
                     "product_re": r"/en-[a-z]{2}/[^/]+-\d+\.html"},
    # Pakistani brands are mostly sold through Shopify shops; these two are retailer/brand stores.
    # Check each shop's Terms and confirm the shop is the one you mean.
    # Jenpharm's own shop. Ingredients are added to the page by JavaScript (not in the downloaded HTML),
    # so render_ingredients=True opens each page in a headless browser (needs Playwright, see render_pages).
    # Skin types come from the shop's tags (oily / combination / normal / All Skin Types).
    "Jenpharm": {"base": "https://jenpharm.com", "shopify": True, "collection": "shop-all",
                 "render_ingredients": True,        # ingredient text is added by JavaScript -> use Playwright
                 "manual_urls": [], "currency": "PKR"},
    "Blissful": {"base": "https://shopblissfulbeauty.com", "shopify": True, "collection": "",
                 "manual_urls": [], "currency": "PKR"},
}
# INKEEDecoder (formerly INCIDecoder, same site): ingredient lists for many brands.
# It has NO price, rating or skin-type field, so skin_types stays mostly empty for these rows.
# ITS TERMS (inkeedecoder.com/termsofservice) say copying portions of the site needs written
# permission and allow keeping information for your own NON-COMMERCIAL purposes. robots.txt allows
# crawling. Set INKEE_PERMISSION_OK = True only after you have decided you are OK with that
# (e.g. you emailed the address on the Terms page, or your instructor approved). Do not publish
# the raw scraped file in a public repo.
INKEE_PERMISSION_OK = False
INKEE = {"base": "https://inkeedecoder.com",
         "brands": {"The Ordinary": "the-ordinary", "CeraVe": "cerave", "Cetaphil": "cetaphil",
                    "Neutrogena": "neutrogena", "Aveeno": "aveeno", "Jenpharm": "jenpharm"}}
USE_INKEE = False       # True = also scrape the brands above from INKEEDecoder

# To run only some brands, list them here (empty list = all brands above):
RUN_ONLY = []

MAX_PRODUCTS_PER_BRAND = 15     # raise to 300 after your first check
MAX_PAGES_FETCHED_PER_BRAND = 120   # safety cap (category pages also cost a fetch)
DELAY_SECONDS = 3
TIMEOUT = 20
OUTPUT_CSV = "skincare_products.csv"
DEBUG_SAVE_HTML = 3             # save the first N product pages per brand to debug_html/
USER_AGENT = "Mozilla/5.0 (student project; DS2001 coursework; contact: your_email@example.com)"

HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
URL_HINTS = ("product", "/p/", "/shop/", "/skincare/", "/products/")
SKIP_URL_PARTS = ("/es/", "/sitecore", "/search", "/cart", "/login", "/account")

FIELDS = ["brand", "name", "category", "ingredients", "ingredient_count", "skin_types",
          "helps_with", "price_usd", "rating", "review_count", "url", "currency", "source"]
# NOTE: column price_usd holds the price in the site's own currency; check the `currency` column
# (PKR for the Pakistani shops) and convert or split by currency before analysis.


# ---------------- networking helpers ----------------
def get(url, tries=3):
    """GET with retries; prints the FULL error so we can see what went wrong."""
    last = None
    for i in range(tries):
        try:
            return requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as e:
            last = e
            time.sleep(2 * (i + 1))
    print(f"    request error after {tries} tries: {url}\n      {type(last).__name__}: {str(last)[-300:]}")
    return None


def load_robots(base):
    rp = urllib.robotparser.RobotFileParser()
    r = get(base + "/robots.txt")
    if r is None or r.status_code != 200:
        print("  robots.txt not readable; check it manually before continuing")
        return None
    rp.parse(r.text.splitlines())
    return rp


def find_candidate_urls(base, rp, brand="site"):
    """Sitemap discovery. Successful results are cached in url_cache_<brand>.txt so a
    broken or unreachable sitemap host later does not stop the scraper."""
    cache = f"url_cache_{brand.replace(' ', '_')}.txt"
    found = _discover(base, rp)
    if found:
        with open(cache, "w", encoding="utf-8") as f:
            f.write("\n".join(found))
    elif os.path.exists(cache):
        print(f"  sitemap unreachable; using cached URL list {cache}")
        found = open(cache, encoding="utf-8").read().split()
    return found


def _discover(base, rp):
    sitemaps = list(rp.site_maps() or []) if rp else []
    if not sitemaps:
        sitemaps = [base + "/sitemap.xml"]
    urls, queue, seen = [], list(sitemaps), set()
    while queue and len(seen) < 25:
        sm = queue.pop(0)
        if sm in seen or "image" in sm.lower():
            continue
        seen.add(sm)
        r = get(sm)
        if not r or r.status_code != 200:
            continue
        for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", r.text):
            if loc.endswith(".xml") or ".xml?" in loc:
                queue.append(loc)
            elif any(h in loc.lower() for h in URL_HINTS):
                urls.append(loc)
        time.sleep(1)
    return list(dict.fromkeys(urls))


def crawl_seed_links(cfg, rp):
    """Fallback when the sitemap has no product URLs: read links from listing pages."""
    from urllib.parse import urljoin
    pat = re.compile(cfg.get("product_re", "."))
    host = urlparse(cfg["base"]).netloc.replace("www.", "")
    found = []
    for seed in cfg.get("seeds", []):
        if rp and not rp.can_fetch(USER_AGENT, seed):
            continue
        r = get(seed)
        time.sleep(DELAY_SECONDS)
        if r is None or r.status_code != 200:
            continue
        for a in BeautifulSoup(r.text, "lxml").find_all("a", href=True):
            link = urljoin(seed, a["href"]).split("#")[0].split("?")[0]
            if host in urlparse(link).netloc and pat.search(urlparse(link).path):
                found.append(link)
    return list(dict.fromkeys(found))


def shopify_rows(brand, cfg, rp, done):
    """Read a Shopify shop's public /products.json (name, type, description, price, tags)."""
    base = cfg["base"].rstrip("/")
    prefix = f"/collections/{cfg['collection']}" if cfg.get("collection") else ""
    rows = []
    for page in range(1, 21):
        url = f"{base}{prefix}/products.json?limit=250&page={page}"
        if rp and not rp.can_fetch(USER_AGENT, url):
            print("  robots.txt disallows products.json for this shop; skipping")
            break
        r = get(url)
        time.sleep(DELAY_SECONDS)
        if r is None or r.status_code != 200:
            print(f"  products.json not available (status {r.status_code if r else 'error'})")
            break
        try:
            prods = r.json().get("products", [])
        except Exception:
            break
        if not prods:
            break
        for pr in prods:
            link = f"{base}/products/{pr.get('handle')}"
            if link in done:
                continue
            if re.search(r"\b(bundle|kit|set|combo|duo|trio|pack|gift)\b", pr.get("title", ""), re.I):
                continue                     # bundles are not single products
            body = BeautifulSoup(pr.get("body_html") or "", "lxml")
            text = body.get_text("\n", strip=True)
            ings = extract_ingredients_from_text(text)
            tags = pr.get("tags") or []
            tag_text = " ".join(tags if isinstance(tags, list) else str(tags).split(","))
            skin = [k for k, pat in SKIN_PATTERNS.items() if re.search(pat, (text + " " + tag_text).lower())]
            variants = pr.get("variants") or [{}]
            rows.append({
                "brand": brand, "name": pr.get("title", "").strip(),
                "category": pr.get("product_type", ""),
                "ingredients": ", ".join(ings) if ings else "",
                "ingredient_count": len(ings) if ings else 0,
                "skin_types": "|".join(skin), "helps_with": "",
                "price_usd": variants[0].get("price", ""), "rating": "", "review_count": "",
                "url": link, "currency": cfg.get("currency", ""), "source": "shopify shop"})
        if len(prods) < 250:
            break
    return rows


def ingredients_after_heading(text):
    """Rendered-page text: the list is the line(s) right after a 'Full ingredient list' heading."""
    lines = [l.strip() for l in text.split("\n")]
    for i, l in enumerate(lines):
        if re.search(r"full ingredient list", l, re.I):
            for nxt in lines[i + 1:i + 6]:
                if nxt.count(",") >= 3:
                    parts = clean_ingredients(nxt)
                    if parts:
                        return parts
    return None


def render_pages(rows, rp, check_robots=True):
    """Runs the browser work in its own thread: Jupyter already has an asyncio loop running,
    and Playwright's sync API refuses to start inside it."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as ex:
        ex.submit(_render_pages, rows, rp, check_robots).result()


def _render_pages(rows, rp, check_robots=True):
    """For shops whose ingredient text is added by JavaScript: open each product page in a real
    (headless) browser, expand the panels, read the list and the rating from the rendered page.
    One-time setup:  pip install playwright   then   playwright install chromium"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  Playwright is not installed: run  pip install playwright  and  playwright install chromium")
        return
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_context(user_agent=USER_AGENT).new_page()
        for row in rows:
            if row.get("ingredients"):
                continue
            if check_robots and rp and not rp.can_fetch(USER_AGENT, row["url"]):
                continue
            try:
                page.goto(row["url"], wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
                try:
                    page.get_by_text("Full ingredient list").first.click(timeout=3000)
                except Exception:
                    pass
                page.wait_for_timeout(800)
                text = page.evaluate("() => document.body.innerText")
                html = page.content()
            except Exception as e:
                print(f"    render failed: {row['url']} ({str(e)[:80]})")
                continue
            ings = ingredients_after_heading(text) or extract_ingredients_from_text(text)
            if ings:
                row["ingredients"] = ", ".join(ings)
                row["ingredient_count"] = len(ings)
            nums = numbers_from_ld(find_product_ld(BeautifulSoup(html, "lxml")))
            if nums["rating"] and not row.get("rating"):
                row["rating"], row["review_count"] = nums["rating"], nums["review_count"]
            time.sleep(DELAY_SECONDS)
        browser.close()


# ---------------- extraction helpers ----------------
def find_product_ld(soup):
    """Return the schema.org Product dict if the page has one (real product pages do)."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "", strict=False)   # strict=False: CeraVe's JSON has raw line breaks inside strings
        except Exception:
            continue
        nodes = data if isinstance(data, list) else [data]
        flat = []
        for n in nodes:
            if isinstance(n, dict) and "@graph" in n:
                flat.extend(n["@graph"])
            else:
                flat.append(n)
        for n in flat:
            if not isinstance(n, dict):
                continue
            t = n.get("@type")
            types = t if isinstance(t, list) else [t]
            if "Product" in types or "ProductGroup" in types:
                return n
    return None


LABELS = {"ingredients", "full ingredients", "full ingredient list",
          "ingredient list", "all ingredients", "ingredients list"}
STOP_WORDS = re.compile(
    r"(directions|how to use|warnings?|caution|questions\?|see more|view all|"
    r"shop now|add to cart|reviews?|where to buy|you may also like)", re.I)


def _norm_ingredient(p):
    """'aqua / water / eau' -> 'water'; 'ci 77891 / titanium dioxide' -> 'titanium dioxide'."""
    if " / " in p:
        segs = [s.strip() for s in p.split(" / ")]
        segs = [s for s in segs if not s.startswith("ci ")] or segs
        p = segs[0]
    if p in ("water/aqua", "aqua/water") or p == "aqua" or p.startswith("aqua ("):
        return "water"
    return p


def clean_ingredients(raw):
    raw = STOP_WORDS.split(raw)[0]
    raw = re.sub(r"^\s*(active\s+)?ingredients?\s*[:\-]?\s*", "", raw, flags=re.I)
    parts = [re.sub(r"\s+", " ", p).strip(" .;:*").lower() for p in raw.split(",")]
    parts = [_norm_ingredient(p) for p in parts if p]
    if not (5 <= len(parts) <= 90):
        return None
    if sum(len(p) for p in parts) / len(parts) > 45:     # looks like prose, not a list
        return None
    words = [len(p.split()) for p in parts]
    if sum(words) / len(words) > 4 or max(words) > 12:    # sentences, not ingredient names
        return None
    if re.search(r"\b(it's|its|hydration|helps|for long|you|your)\b", " ".join(parts[:3])):
        return None
    return parts


def extract_ingredients(soup):
    # CeraVe: the full list sits inside a <details> block ("See Full Ingredient List");
    # the label text is drawn by CSS, so there is no 'Ingredients' text node to find.
    # (second selector: The Ordinary's ingredients flyout)
    for p in soup.select('[class*="keyIngredients-details__content"] p, p.ingredients-flyout-content'):
        txt = p.get_text(" ", strip=True)
        if txt.count(",") >= 5:
            parts = clean_ingredients(txt)
            if parts:
                return parts
    tags = ["h1", "h2", "h3", "h4", "h5", "h6", "button", "span", "div",
            "p", "strong", "b", "summary", "dt", "label", "a", "li"]
    for el in soup.find_all(tags):
        label = el.get_text(" ", strip=True).lower().rstrip(":").strip()
        if label not in LABELS:
            continue
        candidates = []
        sib = el.find_next_sibling()
        if sib:
            candidates.append(sib.get_text(" ", strip=True))
        if el.parent:
            candidates.append(el.parent.get_text(" ", strip=True))
        nxt = el.find_next(lambda t: t.name in ("p", "div", "span", "li")
                           and t.get_text().count(",") >= 5)
        if nxt:
            candidates.append(nxt.get_text(" ", strip=True))
        for c in candidates:
            if c.count(",") >= 5 and len(c) < 4000:
                parts = clean_ingredients(c)
                if parts:
                    return parts
    return None


def plausible_ingredients(parts):
    """True only if the list looks like INCI names, not a marketing paragraph."""
    if not parts or len(parts) < 3:
        return False
    too_long = sum(1 for x in parts if len(x.split()) > 6 or len(x) > 70)
    prose = sum(1 for x in parts if re.search(r"\b(your|you|helps?|it's|its|our|for|that|this|is|are|will|can)\b|[.!?]\s", x))
    return too_long <= 0.15 * len(parts) and prose <= 0.15 * len(parts)


def extract_ingredients_from_text(text):
    """Last resort: the word 'Ingredients' followed by a comma-separated chunk of plain text."""
    # first choice: an explicit "(Full) Ingredient List" heading (beats "Key Ingredients" bullets)
    m = re.search(r"ingredient\s*list\s*[:\-]?\s*\n?\s*(.{20,2500})", text, flags=re.I | re.S)
    if m:
        chunk = m.group(1).split("\n\n")[0].replace("\n", " ")
        if chunk.count(",") >= 3:
            parts = clean_ingredients(chunk)
            if parts and plausible_ingredients(parts):
                return parts
    # second choice: a heading that sits at the START of a line ("Ingredients", "Ingredients:"),
    # then only that line (or the next one). Sentences that merely contain the word are ignored.
    lines = [l.strip() for l in text.split("\n")]
    for i, l in enumerate(lines):
        m = re.match(r"^(?:full |all )?ingredients?(?: list)?\s*[:\-]?\s*(.*)$", l, flags=re.I)
        if not m:
            continue
        for cand in (m.group(1), *(lines[i + 1:i + 3] if not m.group(1) else [])):
            if cand and cand.count(",") >= 5:
                parts = clean_ingredients(cand)
                if parts and plausible_ingredients(parts):
                    return parts
    return None


SKIN_PATTERNS = {
    "dry": r"\bdry\b",
    "oily": r"\boily\b",
    "combination": r"\bcombination\b",
    "sensitive": r"\bsensitive\b",
    "normal": r"\bnormal\b",
    "acne-prone": r"acne[- ]prone|blemish[- ]prone",
    "all": r"all skin types|every skin type",
}


def extract_tagline(soup, label):
    """CeraVe shows 'Made For: Normal to Dry Skin' and 'Helps With: Cleansing; Hydrating'."""
    for li in soup.select('ul[class*="product-details__tagline"] li'):
        txt = li.get_text(" ", strip=True)
        if txt.lower().startswith(label.lower()):
            return txt[len(label):].strip(" :")
    return ""


def numbers_from_ld(ld):
    out = {"price_usd": "", "rating": "", "review_count": ""}
    if not ld:
        return out
    offers = ld.get("offers")
    if isinstance(offers, list) and offers:
        offers = offers[0]
    if isinstance(offers, dict):
        out["price_usd"] = offers.get("price") or offers.get("lowPrice") or ""
    ar = ld.get("aggregateRating")
    if isinstance(ar, dict):
        out["rating"] = ar.get("ratingValue", "")
        out["review_count"] = ar.get("reviewCount") or ar.get("ratingCount") or ""
    return out


def _targets(soup):
    el = soup.select_one("p.skin-concern")
    if not el:
        return ""
    txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
    return re.sub(r"(?i)^targets", "", txt).strip(" :").replace(" ,", ",").replace(", ", "; ")


def _clean_price(x):
    return str(x).replace(",", "") if x not in (None, "") else ""


def extract_numbers(soup, ld=None):
    out = _extract_numbers(soup, ld)
    out["price_usd"] = _clean_price(out["price_usd"])
    return out


def _extract_numbers(soup, ld=None):
    """Price, rating and review count from the product block's data attributes
    (CeraVe), otherwise from the JSON-LD offers / aggregateRating."""
    el = soup.select_one(".product-details__info[data-tag-product-price]") \
        or soup.select_one("[data-tag-product-price]")
    if not el:
        out = numbers_from_ld(ld)
        if not out["price_usd"]:
            pe = soup.select_one(".product-price .price, .product-price")
            m = re.search(r"[\d,]+\.?\d*", pe.get_text(" ", strip=True)) if pe else None
            if m:
                out["price_usd"] = m.group(0).replace(",", "")
        return out
    return {
        "price_usd": el.get("data-tag-product-price", ""),
        "rating": el.get("data-tag-product-dimension38", ""),
        "review_count": el.get("data-tag-product-dimension59", ""),
    }


def extract_skin_types(soup, ld):
    """Use ONLY description text (JSON-LD, meta tags, description blocks) so nav menus
    and 'shop by skin type' filters do not pollute the labels."""
    # best source: the labelled 'Made For' field on the page (CeraVe) / 'Suited for' (The Ordinary)
    made_for = extract_tagline(soup, "Made For")
    if not made_for:
        el = soup.select_one("p.suitedTo")
        if el:
            made_for = re.sub(r"(?i)^suited for", "", el.get_text(" ", strip=True)).strip(" :")
    if made_for:
        t = made_for.lower()
        hit = [k for k, pat in SKIN_PATTERNS.items() if re.search(pat, t)]
        if hit:
            return hit
    chunks = []
    if ld and ld.get("description"):
        chunks.append(str(ld["description"]))
    for sel in ('meta[name="description"]', 'meta[property="og:description"]'):
        m = soup.select_one(sel)
        if m and m.get("content"):
            chunks.append(m["content"])
    for el in soup.select('[class*="description"]')[:3]:
        chunks.append(el.get_text(" ", strip=True)[:700])
    text = " ".join(chunks).lower()
    found = [k for k, pat in SKIN_PATTERNS.items() if re.search(pat, text)]
    return found


def extract_category(url, ld, html=""):
    m = re.search(r'"category"\s*:\s*"([^"]+)"', html)      # The Ordinary: dataLayer category
    if m and not (ld and ld.get("category")):
        return m.group(1)
    if ld and ld.get("category"):
        c = ld["category"]
        return c if isinstance(c, str) else str(c)
    parts = [p for p in urlparse(url).path.split("/") if p]
    return parts[-2] if len(parts) >= 2 else ""


def extract_ingredients_from_json(raw_html):
    """Fallback: many sites ship ingredients inside a JSON blob in the page source
    instead of visible HTML next to an 'Ingredients' label."""
    for m in re.finditer(r'"(?:full)?ingredients?(?:List)?"\s*:\s*"((?:[^"\\]|\\.)*)"',
                         raw_html, flags=re.I):
        try:
            text = json.loads('"' + m.group(1) + '"')
        except Exception:
            continue
        text = BeautifulSoup(text, "lxml").get_text(" ", strip=True)
        parts = clean_ingredients(text)
        if parts:
            return parts
    return None


def parse_product(html, url, brand, currency="USD"):
    soup = BeautifulSoup(html, "lxml")
    ld = find_product_ld(soup)
    ingredients = (extract_ingredients(soup) or extract_ingredients_from_json(html)
                   or extract_ingredients_from_text(soup.get_text("\n", strip=True)))
    og = soup.select_one('meta[property="og:type"]')
    is_product_meta = bool(og and "product" in (og.get("content") or "").lower())
    is_pdp = '"pageType":"product detail"' in html     # CeraVe's own page-type tag
    if not ld and not is_product_meta and not is_pdp and not ingredients:
        return None                      # not a product page (category, article, etc.)
    ld = ld or {}
    name = ld.get("name") or (soup.h1.get_text(strip=True) if soup.h1 else "")
    skin = extract_skin_types(soup, ld)
    return {
        "brand": brand,
        "name": htmllib.unescape(str(name)).strip(),
        "category": extract_category(url, ld, html),
        "ingredients": ", ".join(ingredients) if ingredients else "",
        "ingredient_count": len(ingredients) if ingredients else 0,
        "skin_types": "|".join(skin),
        "helps_with": extract_tagline(soup, "Helps With") or _targets(soup),
        **extract_numbers(soup, ld),
        "url": url,
        "currency": currency,
        "source": "brand site",
    }


# ---------------- INKEEDecoder ----------------
CATEGORY_WORDS = [("cleanser", "cleanser"), ("face wash", "cleanser"), ("wash", "cleanser"), ("cleansing", "cleanser"),
                  ("sunscreen", "sunscreen"), ("spf", "sunscreen"), ("sun", "sunscreen"),
                  ("serum", "serum"), ("moistur", "moisturizer"), ("cream", "moisturizer"), ("lotion", "moisturizer"),
                  ("gel", "moisturizer"), ("toner", "toner"), ("mask", "mask"), ("exfoli", "exfoliant"),
                  ("peel", "exfoliant"), ("retinol", "treatment"), ("oil", "oil"), ("balm", "balm")]


def guess_category(name):
    n = name.lower()
    for word, cat in CATEGORY_WORDS:
        if word in n:
            return cat
    return ""


def parse_inkee(html, url, brand_label):
    soup = BeautifulSoup(html, "lxml")
    name_el = soup.select_one("#product-title")
    box = soup.select_one("#ingredlist-short")
    if not name_el or not box:
        return None
    ings = []
    for a in box.select("a.ingred-link"):
        x = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).lower()
        if x.startswith("aqua"):
            x = "water"
        x = x.replace(",", "-")       # '1,2-hexanediol' must not be split by the comma separator
        if x and x not in ings:
            ings.append(x)
    if len(ings) < 3:
        return None
    desc_el = soup.select_one("#product-details")
    desc = desc_el.get_text(" ", strip=True) if desc_el else ""
    tags = [h.get_text(strip=True).lstrip("#") for h in soup.select(".hashtags .hashtag")]
    skin = [k for k, pat in SKIN_PATTERNS.items() if re.search(pat, (desc + " " + " ".join(tags)).lower())]
    name = name_el.get_text(strip=True)
    return {"brand": brand_label, "name": name, "category": guess_category(name),
            "ingredients": ", ".join(ings), "ingredient_count": len(ings),
            "skin_types": "|".join(skin), "helps_with": "; ".join(tags),
            "price_usd": "", "rating": "", "review_count": "", "url": url,
            "currency": "", "source": "inkeedecoder"}


def scrape_inkee(done):
    out = []
    if not INKEE_PERMISSION_OK:
        print("\n=== INKEEDecoder === skipped: set INKEE_PERMISSION_OK = True after reading its Terms (see top of file)")
        return out
    base = INKEE["base"]
    print("\n=== INKEEDecoder ===")
    rp = load_robots(base)
    all_urls = find_candidate_urls(base, rp, "INKEEDecoder")
    all_urls = [u for u in all_urls if "/products/" in u and "/products/recommend" not in u
                and (not rp or rp.can_fetch(USER_AGENT, u))]
    print(f"  product URLs in sitemap: {len(all_urls)}")
    for label, slug in INKEE["brands"].items():
        if RUN_ONLY and label not in RUN_ONLY:
            continue
        urls = [u for u in all_urls if urlparse(u).path.split("/products/")[-1].startswith(slug + "-")
                and u not in done]
        random.shuffle(urls)
        print(f"  {label}: {len(urls)} product pages")
        saved = fetched = 0
        for u in urls:
            if saved >= MAX_PRODUCTS_PER_BRAND or fetched >= MAX_PAGES_FETCHED_PER_BRAND:
                break
            r = get(u)
            fetched += 1
            time.sleep(DELAY_SECONDS)
            if r is None or r.status_code != 200:
                continue
            row = parse_inkee(r.text, u, label)
            if row is None:
                print(f"    not parsed: {u}")
                continue
            append_row(row)
            saved += 1
            print(f"    saved: {row['name'][:45]} | ingredients: {row['ingredient_count']}")
        out.append({"brand": label + " (INKEEDecoder)", "saved": saved, "fetched": fetched})
    return out


# ---------------- storage ----------------
def load_done_urls():
    if not os.path.exists(OUTPUT_CSV):
        return set()
    with open(OUTPUT_CSV, newline="", encoding="utf-8") as f:
        return {row["url"] for row in csv.DictReader(f)}


def migrate_csv():
    """If an older CSV has fewer columns (e.g. no 'currency'), rewrite it with the new header."""
    if not os.path.exists(OUTPUT_CSV):
        return
    with open(OUTPUT_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        header = rows and list(rows[0].keys())
    if not rows or header == FIELDS:
        return
    for r in rows:
        r.setdefault("currency", "USD")
        r.setdefault("source", "brand site")
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"Updated {OUTPUT_CSV} to the new column layout ({len(rows)} rows kept).")


def append_row(row):
    new_file = not os.path.exists(OUTPUT_CSV)
    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)


# ---------------- main ----------------
def scrape_brand(brand, cfg, done):
    print(f"\n=== {brand} ===")
    base = cfg["base"]
    print("  reminder: read this site's Terms of Use before scraping (robots.txt is not the whole story)")
    rp = load_robots(base)

    if cfg.get("shopify"):
        rows = shopify_rows(brand, cfg, rp, done)
        saved = 0
        rows = rows[:MAX_PRODUCTS_PER_BRAND]
        if cfg.get("render_ingredients"):
            render_pages(rows, rp)
        for row in rows:
            append_row(row)
            saved += 1
            print(f"    saved: {row['name'][:45]} | ingredients: {row['ingredient_count']} | skin: {row['skin_types'] or '-'}")
        return {"brand": brand, "saved": saved, "fetched": len(rows),
                "ingredients": sum(1 for r in rows[:saved] if r["ingredients"]),
                "skin": sum(1 for r in rows[:saved] if r["skin_types"])}

    urls = list(cfg["manual_urls"]) or find_candidate_urls(base, rp, brand)
    if cfg.get("product_re") and not cfg["manual_urls"]:
        urls = [u for u in urls if re.search(cfg["product_re"], urlparse(u).path)]
    if not urls and cfg.get("seeds"):
        print("  sitemap gave no product URLs; crawling listing pages instead")
        urls = crawl_seed_links(cfg, rp)
    urls = [u for u in urls if not any(s in u.lower() for s in SKIP_URL_PARTS)]
    if rp:
        urls = [u for u in urls if rp.can_fetch(USER_AGENT, u)]
    urls = [u for u in urls if u not in done]
    print(f"  candidate URLs (allowed, not yet scraped): {len(urls)}")
    if not urls:
        print("  nothing to do (paste manual_urls if the sitemap gave nothing)")
        return {"brand": brand, "saved": 0, "fetched": 0, "ingredients": 0, "skin": 0}

    random.shuffle(urls)
    saved = fetched = with_ing = with_skin = debug_saved = rejected_saved = 0

    for u in urls:
        if saved >= MAX_PRODUCTS_PER_BRAND or fetched >= MAX_PAGES_FETCHED_PER_BRAND:
            break
        r = get(u)
        fetched += 1
        time.sleep(DELAY_SECONDS)
        if r is None or r.status_code != 200:
            print(f"    skip ({r.status_code if r else 'error'}): {u}")
            continue

        row = parse_product(r.text, u, brand, cfg.get("currency", "USD"))
        if row is None:
            print(f"    not a product page: {u}")
            # keep a few rejected pages so we can check whether real products are being missed
            if rejected_saved < 6:
                os.makedirs("debug_html", exist_ok=True)
                with open(f"debug_html/{brand.replace(' ', '_')}_rejected_{rejected_saved}.html", "w", encoding="utf-8") as f:
                    f.write(f"<!-- URL: {u} -->\n" + r.text)
                rejected_saved += 1
            continue

        if debug_saved < DEBUG_SAVE_HTML:
            os.makedirs("debug_html", exist_ok=True)
            with open(f"debug_html/{brand.replace(' ', '_')}_{debug_saved}.html", "w", encoding="utf-8") as f:
                f.write(r.text)
            debug_saved += 1

        append_row(row)
        saved += 1
        with_ing += bool(row["ingredients"])
        with_skin += bool(row["skin_types"])
        print(f"    saved: {row['name'][:45]} | ingredients: {row['ingredient_count']} | skin: {row['skin_types'] or '-'}")

    return {"brand": brand, "saved": saved, "fetched": fetched,
            "ingredients": with_ing, "skin": with_skin}


if __name__ == "__main__":
    migrate_csv()
    done = load_done_urls()
    chosen = {b: c for b, c in SITES.items() if not RUN_ONLY or b in RUN_ONLY}
    summary = [scrape_brand(b, c, done) for b, c in chosen.items()]
    if USE_INKEE:
        summary += scrape_inkee(done)
    print("\n========== SUMMARY ==========")
    for s in summary:
        print(s)
    print(f"\nData saved to {OUTPUT_CSV}")
    print("Check by eye: do ingredient lists look right? Are skin_types specific (1-3 values), not all 5?")