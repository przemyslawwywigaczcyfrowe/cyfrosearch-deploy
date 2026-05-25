"""
Product indexer — loads feed JSON and bulk-indexes into Elasticsearch.

Universal approach: no category-specific transformations during indexing.
Category hierarchy is split mechanically (by " > " separator) for all products.
"""

import json
import sys
import httpx
from opensearchpy.helpers import bulk

from config import FEED_URL, INDEX_NAME
from es_client import get_es_client
from es_mapping import INDEX_SETTINGS


def fetch_feed(url: str | None = None, local_path: str | None = None) -> list[dict]:
    """Fetch product feed from URL or local file."""
    if local_path:
        print(f"Loading feed from local file: {local_path}")
        with open(local_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        print(f"Downloading feed from: {url}")
        resp = httpx.get(url, timeout=120)
        resp.raise_for_status()
        data = resp.json()

    if isinstance(data, dict) and "products" in data:
        return data["products"]
    if isinstance(data, list):
        return data
    raise ValueError("Unexpected feed format")


def transform_product(product: dict) -> dict:
    """
    Transform a raw feed product into an ES document.

    UNIVERSAL: All transformations apply equally to every product.
    No category-specific logic here.
    """
    # Split category hierarchy mechanically for all products
    category = product.get("category", "")
    parts = [p.strip() for p in category.split(" > ")] if category else []

    doc = {
        "id_verto": product.get("id_verto", ""),
        "id_internal": str(product.get("id_internal", "")),
        "ean": product.get("ean", ""),
        "name": product.get("name", ""),
        "description": product.get("description", ""),
        "link": product.get("link", ""),
        "image": product.get("image", ""),
        "brand": product.get("brand", ""),
        "category": category,
        "category_lvl0": parts[0] if len(parts) > 0 else "",
        "category_lvl1": " > ".join(parts[:2]) if len(parts) > 1 else "",
        "category_lvl2": " > ".join(parts[:3]) if len(parts) > 2 else "",
        "condition": product.get("condition", ""),
        "availability": product.get("availability", ""),
        "price": _safe_float(product.get("price")),
        "sales_price": _safe_float(product.get("sales_price")),
        # Combined search field: name + brand + category for cross-term matching
        "searchable_text": " ".join(filter(None, [
            product.get("name", ""),
            product.get("brand", ""),
            category,
        ])),
        # GA fields default to 0, updated separately by ga_updater.py
        "ga_views": 0,
        "ga_sales": 0,
        "popularity_score": 0.0,
    }
    return doc


def _safe_float(val) -> float:
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def create_index(es, index_name: str = INDEX_NAME):
    """Create or recreate the index with our mapping."""
    if es.indices.exists(index=index_name):
        print(f"Deleting existing index: {index_name}")
        es.indices.delete(index=index_name)
    print(f"Creating index: {index_name}")
    es.indices.create(index=index_name, body=INDEX_SETTINGS)


def index_products(es, products: list[dict], index_name: str = INDEX_NAME):
    """Bulk index products into Elasticsearch."""
    actions = []
    for p in products:
        doc = transform_product(p)
        actions.append({
            "_index": index_name,
            "_id": doc["id_verto"],
            "_source": doc,
        })

    print(f"Indexing {len(actions)} products...")
    success, errors = bulk(es, actions, chunk_size=500, raise_on_error=False)
    print(f"Indexed: {success}, Errors: {len(errors) if isinstance(errors, list) else errors}")
    return success


def main():
    """Main indexing flow."""
    es = get_es_client()

    # Check connection
    info = es.info()
    print(f"Connected to Elasticsearch: {info['version']['number']}")

    # Use local file if available, otherwise download
    import os
    local_path = os.path.join(os.path.dirname(__file__), "feed.json")
    if os.path.exists(local_path):
        products = fetch_feed(local_path=local_path)
    else:
        products = fetch_feed(url=FEED_URL)

    print(f"Loaded {len(products)} products from feed")

    create_index(es)
    index_products(es, products)

    # Refresh index
    es.indices.refresh(index=INDEX_NAME)
    count = es.count(index=INDEX_NAME)
    print(f"Total documents in index: {count['count']}")


if __name__ == "__main__":
    main()
