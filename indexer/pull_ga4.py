"""
Pulls per-SKU sales data from Google Analytics 4 via Data API.

Output: JSON dict {item_id: {"s30": int, "s365": int}} on the path given
        by GA4_OUTPUT env var (default: ./sales_data.json).

Env vars:
    GA4_PROPERTY_ID                — GA4 property ID, numeric (e.g. 123456789)
    GOOGLE_APPLICATION_CREDENTIALS — path to service-account JSON key
    GA4_OUTPUT                     — output JSON path (default: sales_data.json)
    GA4_METRIC                     — metric to use; default 'itemsPurchased'.
                                     Set to 'itemPurchaseQuantity' on properties
                                     where that variant is configured.
    GA4_DRY_RUN                    — if "true", print first 10 rows and exit
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)

PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID", "")
OUTPUT_PATH = os.environ.get("GA4_OUTPUT", "sales_data.json")
METRIC_NAME = os.environ.get("GA4_METRIC", "itemsPurchased")
DRY_RUN = os.environ.get("GA4_DRY_RUN", "false").lower() == "true"

DATE_RANGES = {
    "s30": ("30daysAgo", "yesterday"),
    "s365": ("365daysAgo", "yesterday"),
}


def run_report(client: BetaAnalyticsDataClient, property_id: str, start: str, end: str) -> dict[str, int]:
    """Returns {item_id: quantity_sold} for the given date range."""
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="itemId")],
        metrics=[Metric(name=METRIC_NAME)],
        date_ranges=[DateRange(start_date=start, end_date=end)],
        limit=250_000,
    )
    response = client.run_report(request=request)
    result: dict[str, int] = {}
    for row in response.rows:
        sku = row.dimension_values[0].value
        if not sku:
            continue
        try:
            qty = int(float(row.metric_values[0].value))
        except (TypeError, ValueError):
            qty = 0
        if qty > 0:
            result[sku] = qty
    return result


def main() -> int:
    if not PROPERTY_ID:
        print("ERROR: GA4_PROPERTY_ID not set", file=sys.stderr)
        return 2
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        print("ERROR: GOOGLE_APPLICATION_CREDENTIALS not set", file=sys.stderr)
        return 2

    client = BetaAnalyticsDataClient()

    if DRY_RUN:
        sample = run_report(client, PROPERTY_ID, "7daysAgo", "yesterday")
        items = list(sample.items())[:10]
        print(f"[DRY RUN] Got {len(sample)} rows for last 7d. First 10:")
        for sku, qty in items:
            print(f"    {sku!r}: {qty}")
        return 0

    merged: dict[str, dict[str, int]] = defaultdict(lambda: {"s30": 0, "s365": 0})
    for window, (start, end) in DATE_RANGES.items():
        print(f"[*] Querying GA4 {window}: {start} → {end} (metric={METRIC_NAME})")
        data = run_report(client, PROPERTY_ID, start, end)
        print(f"[OK] {window}: {len(data)} items with sales")
        for sku, qty in data.items():
            merged[sku][window] = qty

    out_path = Path(OUTPUT_PATH)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(dict(merged), f, ensure_ascii=False)
    print(f"[OK] Wrote {len(merged)} SKUs to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
