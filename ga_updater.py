"""
Google Analytics 4 data fetcher and Elasticsearch updater.

Fetches product page views and purchase counts from GA4,
then updates Elasticsearch documents with these popularity signals.

Requires:
- GA4_PROPERTY_ID in .env (e.g., "properties/123456789")
- GA4_SERVICE_ACCOUNT_JSON in .env (path to service account key file)
- Service account must have "Viewer" role in GA4 property
"""

import re
import json
import sys
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest,
    DateRange,
    Dimension,
    Metric,
    FilterExpression,
    Filter,
)
from google.oauth2 import service_account

from config import GA4_PROPERTY_ID, GA4_SERVICE_ACCOUNT_JSON, INDEX_NAME
from es_client import get_es_client


def get_ga_client() -> BetaAnalyticsDataClient:
    """Create GA4 API client with service account credentials.

    Accepts GA4_SERVICE_ACCOUNT_JSON in either form:
    - inline JSON string (typical for GitHub Actions / Render env vars), or
    - filesystem path to a JSON keyfile (typical for local development).
    """
    scopes = ["https://www.googleapis.com/auth/analytics.readonly"]
    val = (GA4_SERVICE_ACCOUNT_JSON or "").strip()
    if val.startswith("{"):
        info = json.loads(val)
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=scopes
        )
    else:
        credentials = service_account.Credentials.from_service_account_file(
            val, scopes=scopes
        )
    return BetaAnalyticsDataClient(credentials=credentials)


def extract_product_id_from_path(page_path: str) -> str | None:
    """
    Extract product identifier from URL path.

    Universal approach: extract the slug before '-p.html' which is the
    standard Cyfrowe.pl product URL pattern.
    """
    # Pattern: /some-product-name-p.html → extract the slug
    # We'll map by URL path to product link field in ES
    return page_path.strip()


def fetch_product_views(client: BetaAnalyticsDataClient, days: int = 90) -> dict[str, int]:
    """
    Fetch page views per product page from GA4.

    Returns: dict mapping page_path → view count
    """
    request = RunReportRequest(
        property=GA4_PROPERTY_ID,
        date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
        dimensions=[Dimension(name="pagePath")],
        metrics=[Metric(name="screenPageViews")],
        dimension_filter=FilterExpression(
            filter=Filter(
                field_name="pagePath",
                string_filter=Filter.StringFilter(
                    match_type=Filter.StringFilter.MatchType.CONTAINS,
                    value="-p.html",
                ),
            )
        ),
        limit=10000,
    )

    response = client.run_report(request)
    views = {}
    for row in response.rows:
        path = row.dimension_values[0].value
        count = int(row.metric_values[0].value)
        views[path] = count

    print(f"Fetched views for {len(views)} product pages")
    return views


def fetch_product_sales(client: BetaAnalyticsDataClient, days: int = 90) -> dict[str, int]:
    """
    Fetch purchase/transaction counts per product from GA4.

    Returns: dict mapping itemId → purchase count
    (itemId in GA corresponds to id_internal in our feed)
    """
    request = RunReportRequest(
        property=GA4_PROPERTY_ID,
        date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
        dimensions=[Dimension(name="itemId")],
        metrics=[Metric(name="itemsPurchased")],
        limit=10000,
    )

    response = client.run_report(request)
    sales = {}
    for row in response.rows:
        item_id = row.dimension_values[0].value
        count = int(row.metric_values[0].value)
        if item_id not in ("(not set)", "(other)"):
            sales[item_id] = count

    print(f"Fetched sales for {len(sales)} products")
    return sales


def build_product_maps(es) -> tuple[dict[str, str], dict[str, str]]:
    """
    Build mappings from URL paths and id_internal to id_verto.

    Scans all products in the index and creates:
    - url_map: URL path → id_verto (for matching GA views by pagePath)
    - internal_map: id_internal → id_verto (for matching GA sales by itemId)
    """
    from urllib.parse import urlparse
    url_map = {}
    internal_map = {}
    resp = es.search(
        index=INDEX_NAME,
        body={"query": {"match_all": {}}, "_source": ["id_verto", "link", "id_internal"]},
        scroll="2m",
        size=1000,
    )

    while resp["hits"]["hits"]:
        for hit in resp["hits"]["hits"]:
            src = hit["_source"]
            id_verto = src["id_verto"]
            link = src.get("link", "")
            id_internal = src.get("id_internal", "")
            if link:
                path = urlparse(link).path
                url_map[path] = id_verto
            if id_internal:
                internal_map[str(id_internal)] = id_verto
        resp = es.scroll(scroll_id=resp["_scroll_id"], scroll="2m")

    return url_map, internal_map


def update_ga_data(views: dict, sales: dict):
    """
    Update Elasticsearch documents with GA data.

    Universal approach: every product gets the same treatment.
    Products without GA data keep their default 0 values.

    Matching strategy:
    - Views: matched by URL path (pagePath → product link)
    - Sales: matched by product name (itemName → product name)
    """
    import math
    es = get_es_client()

    # Build mappings
    url_map, internal_map = build_product_maps(es)
    print(f"Built maps: {len(url_map)} URLs, {len(internal_map)} internal IDs")

    # Map views (by URL path)
    product_views = {}
    for path, count in views.items():
        if path in url_map:
            product_views[url_map[path]] = count

    # Map sales (by id_internal matching GA itemId)
    product_sales = {}
    for item_id, count in sales.items():
        if item_id in internal_map:
            product_sales[internal_map[item_id]] = count

    print(f"Matched: {len(product_views)} view records, {len(product_sales)} sales records")

    # Combine and compute popularity_score
    all_ids = set(product_views.keys()) | set(product_sales.keys())

    bulk_body = []
    for product_id in all_ids:
        v = product_views.get(product_id, 0)
        s = product_sales.get(product_id, 0)

        # Universal popularity score: weighted combination
        # Views are common, sales are strong signal
        pop_score = math.log1p(v) * 1.0 + math.log1p(s) * 3.0

        bulk_body.append({"update": {"_index": INDEX_NAME, "_id": product_id}})
        bulk_body.append({
            "doc": {
                "ga_views": v,
                "ga_sales": s,
                "popularity_score": round(pop_score, 2),
            }
        })

    if bulk_body:
        resp = es.bulk(body=bulk_body)
        errors = [item for item in resp["items"] if "error" in item.get("update", {})]
        print(f"Updated {len(all_ids)} products with GA data, {len(errors)} errors")
    else:
        print("No GA data to update")


def main():
    """Fetch GA4 data and update Elasticsearch."""
    if not GA4_PROPERTY_ID or not GA4_SERVICE_ACCOUNT_JSON:
        print("ERROR: GA4_PROPERTY_ID and GA4_SERVICE_ACCOUNT_JSON must be set in .env")
        print("Skipping GA data update. Products will have ga_views=0, ga_sales=0.")
        return

    print("Connecting to Google Analytics 4...")
    client = get_ga_client()

    print("Fetching product views (last 90 days)...")
    views = fetch_product_views(client)

    print("Fetching product sales (last 90 days)...")
    sales = fetch_product_sales(client)

    print("Updating Elasticsearch with GA data...")
    update_ga_data(views, sales)

    print("Done!")


if __name__ == "__main__":
    main()
