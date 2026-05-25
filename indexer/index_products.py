"""
CyfroSearch indexer — downloads product feed from datafeedwatch and indexes
into Elasticsearch (Bonsai-compatible, requires analysis-stempel + analysis-icu).

Run manually (or via GitHub Actions workflow):
    ES_HOST=https://user:pass@xxx.bonsaisearch.net python index_products.py

Env vars:
    ES_HOST       — full ES URL (with credentials embedded for Bonsai, or use ES_USER/ES_PASSWORD)
    ES_USER       — optional, if not embedded in ES_HOST
    ES_PASSWORD   — optional
    ES_API_KEY    — optional, Elastic Cloud style
    ES_INDEX      — target index name (default: products)
    FEED_URL      — product feed URL (default: cyfrowe.pl datafeedwatch)
    SALES_DATA    — optional path to sales_data.json (default: ../sales_data.json)
    RECREATE      — "true" to delete index first (default: false)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from opensearchpy import OpenSearch, helpers

ES_HOST = os.environ.get("ES_HOST", "http://localhost:9200")
ES_USER = os.environ.get("ES_USER", "")
ES_PASSWORD = os.environ.get("ES_PASSWORD", "")
ES_API_KEY = os.environ.get("ES_API_KEY", "")
ES_INDEX = os.environ.get("ES_INDEX", "products")
FEED_URL = os.environ.get(
    "FEED_URL",
    "https://feeds.datafeedwatch.com/45030/8155cd63e2e29744fd3fb6fd20b04d7dab3275a5.json",
)
SALES_DATA_PATH = os.environ.get(
    "SALES_DATA", str(Path(__file__).parent.parent / "sales_data.json")
)
RECREATE = os.environ.get("RECREATE", "false").lower() == "true"


# ── Index mapping ──
# Uses analysis-stempel (polish_stem) + analysis-icu (icu_folding/icu_tokenizer).
# `name.morfologik` is included as alias to polish_stem because morfologik
# plugin is not available on shared/free ES hosting.
INDEX_SETTINGS = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "filter": {
                "polish_stop_filter": {
                    "type": "stop",
                    "stopwords": "_polish_",
                },
                "polish_stem_filter": {
                    "type": "stempel_stem",
                },
                "edge_ngram_filter": {
                    "type": "edge_ngram",
                    "min_gram": 2,
                    "max_gram": 20,
                },
            },
            "analyzer": {
                "polish_stem": {
                    "type": "custom",
                    "tokenizer": "icu_tokenizer",
                    "filter": [
                        "lowercase",
                        "icu_folding",
                        "polish_stop_filter",
                        "polish_stem_filter",
                    ],
                },
                "polish_folded": {
                    "type": "custom",
                    "tokenizer": "icu_tokenizer",
                    "filter": ["lowercase", "icu_folding"],
                },
                "polish_prefix": {
                    "type": "custom",
                    "tokenizer": "icu_tokenizer",
                    "filter": ["lowercase", "icu_folding", "edge_ngram_filter"],
                },
                "polish_prefix_search": {
                    "type": "custom",
                    "tokenizer": "icu_tokenizer",
                    "filter": ["lowercase", "icu_folding"],
                },
            },
        },
    },
    "mappings": {
        "properties": {
            "name": {
                "type": "text",
                "analyzer": "polish_stem",
                "fields": {
                    "folded": {"type": "text", "analyzer": "polish_folded"},
                    "prefix": {
                        "type": "text",
                        "analyzer": "polish_prefix",
                        "search_analyzer": "polish_prefix_search",
                    },
                    "morfologik": {"type": "text", "analyzer": "polish_stem"},
                    "keyword": {"type": "keyword", "ignore_above": 256},
                },
            },
            "description": {"type": "text", "analyzer": "polish_stem"},
            "brand": {"type": "keyword"},
            "subcategory": {"type": "keyword"},
            "category_path": {"type": "keyword"},
            "category_l1": {"type": "keyword"},
            "category_l2": {"type": "keyword"},
            "condition": {"type": "keyword"},
            "availability": {"type": "keyword"},
            "price": {"type": "float"},
            "sale_price": {"type": "float"},
            "ean": {"type": "keyword"},
            "product_code": {"type": "keyword"},
            "erp_id": {"type": "keyword"},
            "image_url": {"type": "keyword", "index": False},
            "product_url": {"type": "keyword", "index": False},
            "sales_30d": {"type": "integer"},
            "sales_365d": {"type": "integer"},
            "ga4": {
                "properties": {
                    "popularity_score": {"type": "float"},
                }
            },
            "is_bestseller": {"type": "boolean"},
            "is_promo": {"type": "boolean"},
            "is_new": {"type": "boolean"},
        }
    },
}


AVAILABILITY_MAP = {
    "in stock": "in_stock",
    "in_stock": "in_stock",
    "out of stock": "out_of_stock",
    "out_of_stock": "out_of_stock",
    "to order": "na_zamowienie",
    "na zamowienie": "na_zamowienie",
    "na_zamowienie": "na_zamowienie",
    "preorder": "na_zamowienie",
    "available for order": "na_zamowienie",
}


def get_es() -> OpenSearch:
    kwargs: dict = {"timeout": 60, "hosts": [ES_HOST]}
    if ES_API_KEY:
        kwargs["headers"] = {"Authorization": f"ApiKey {ES_API_KEY}"}
    elif ES_USER:
        kwargs["http_auth"] = (ES_USER, ES_PASSWORD)
    # else: credentials may be embedded in ES_HOST (Bonsai style)
    return OpenSearch(**kwargs)


def download_feed(url: str) -> dict:
    print(f"[*] Downloading feed: {url}")
    t0 = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": "cyfrosearch-indexer/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    print(f"[OK] Downloaded {len(raw)/1024/1024:.1f} MB in {time.monotonic()-t0:.1f}s")
    return json.loads(raw)


def load_sales_data(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        print(f"[WARN] sales_data.json not found at {path} — popularity will be zeroed")
        return {}
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def split_category(cat: str) -> tuple[str, str, str, str]:
    """Maps cyfrowe.pl category path to demo's expected schema.

    Feed example: 'Fotografia > Obiektywy do lustrzanek > Standardowe'
    Demo expects subcategory like 'obiektywy do lustrzanek' (the L2 middle level
    matches CATEGORY_ALIASES targets — see demo_server.py).
    """
    parts = [p.strip() for p in (cat or "").split(">") if p.strip()]
    if not parts:
        return ("", "", "", "")
    path = " > ".join(parts)
    l1 = parts[0]
    # subcategory = L2 if hierarchy has ≥2 levels (typical for cyfrowe.pl feed),
    # else fall back to deepest available level
    subcategory = (parts[1] if len(parts) >= 2 else parts[-1]).lower()
    # category_l2 stores the deepest level (e.g. "standardowe" for lenses)
    category_l2 = parts[2].lower() if len(parts) >= 3 else ""
    return (path, l1, category_l2, subcategory)


def parse_price(raw) -> float:
    if raw is None or raw == "":
        return 0.0
    try:
        return float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def transform(product: dict, sales: dict) -> dict:
    sku = product.get("id_verto") or product.get("id_internal") or ""
    name = (product.get("name") or "").strip()
    price = parse_price(product.get("price"))
    sale_price_raw = product.get("sales_price")
    sale_price = parse_price(sale_price_raw) if sale_price_raw else None
    if sale_price and sale_price >= price:
        sale_price = None  # not a real discount

    avail_raw = (product.get("availability") or "").lower().strip()
    availability = AVAILABILITY_MAP.get(avail_raw, "out_of_stock")

    cat_path, cat_l1, cat_detail, subcategory = split_category(product.get("category", ""))

    sku_sales = sales.get(sku, {})
    sales_30d = int(sku_sales.get("s30", 0))
    sales_365d = int(sku_sales.get("s365", 0))
    popularity_score = float(sales_365d)  # simple proxy

    is_promo = sale_price is not None
    is_bestseller = sales_365d >= 50

    return {
        "_index": ES_INDEX,
        "_id": sku or None,
        "_source": {
            "name": name,
            "description": product.get("description") or "",
            "brand": (product.get("brand") or "").strip(),
            "subcategory": subcategory,
            "category_path": cat_path,
            "category_l1": cat_l1,
            "category_l2": cat_detail,
            "condition": (product.get("condition") or "new").lower(),
            "availability": availability,
            "price": price,
            "sale_price": sale_price,
            "ean": (product.get("ean") or "").strip(),
            "product_code": sku,
            "erp_id": product.get("id_internal") or "",
            "image_url": product.get("image") or "",
            "product_url": product.get("link") or "",
            "sales_30d": sales_30d,
            "sales_365d": sales_365d,
            "ga4": {"popularity_score": popularity_score},
            "is_bestseller": is_bestseller,
            "is_promo": is_promo,
            "is_new": False,
        },
    }


def main() -> int:
    es = get_es()
    info = es.info()
    print(f"[OK] Connected to ES {info['version']['number']} — cluster '{info['cluster_name']}'")

    plugins = es.cat.plugins(format="json")
    plugin_names = {p.get("component", "") for p in plugins}
    print(f"[*] Plugins: {sorted(plugin_names)}")
    for required in ("analysis-stempel", "analysis-icu"):
        if not any(required in n for n in plugin_names):
            print(f"[WARN] Plugin {required} not detected — index creation may fail")

    if RECREATE and es.indices.exists(index=ES_INDEX):
        print(f"[*] Deleting existing index '{ES_INDEX}'")
        es.indices.delete(index=ES_INDEX)

    if not es.indices.exists(index=ES_INDEX):
        print(f"[*] Creating index '{ES_INDEX}'")
        es.indices.create(index=ES_INDEX, body=INDEX_SETTINGS)
        print("[OK] Index created")
    else:
        print(f"[*] Index '{ES_INDEX}' already exists — bulk-indexing into it (set RECREATE=true to wipe)")

    feed = download_feed(FEED_URL)
    products = feed.get("products", [])
    print(f"[*] Feed contains {len(products)} products")

    sales = load_sales_data(SALES_DATA_PATH)
    print(f"[*] Loaded {len(sales)} sales records")

    actions = (transform(p, sales) for p in products if (p.get("name") or "").strip())

    print(f"[*] Bulk indexing into '{ES_INDEX}'...")
    t0 = time.monotonic()
    ok, errors = helpers.bulk(
        es,
        actions,
        chunk_size=500,
        request_timeout=120,
        raise_on_error=False,
        max_retries=2,
    )
    elapsed = time.monotonic() - t0
    print(f"[OK] Indexed {ok} docs in {elapsed:.1f}s")
    if errors:
        print(f"[WARN] {len(errors)} errors — first 3:")
        for e in errors[:3]:
            print(f"    {e}")

    es.indices.refresh(index=ES_INDEX)
    count = es.count(index=ES_INDEX)["count"]
    print(f"[OK] Index '{ES_INDEX}' now has {count} documents")
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
