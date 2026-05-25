import os
from dotenv import load_dotenv

load_dotenv()

# Search backend URL — supports both ELASTIC_URL (spec convention) and
# ES_HOST (existing Bonsai/OpenSearch deployment), so neither GitHub Secrets
# nor Render env vars need renaming during the migration.
ELASTIC_URL = os.getenv("ELASTIC_URL") or os.getenv("ES_HOST")
ELASTIC_API_KEY = os.getenv("ELASTIC_API_KEY") or os.getenv("ES_API_KEY", "")

GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "")
# Accept GA4_SA_JSON too (legacy secret name from earlier workflow).
GA4_SERVICE_ACCOUNT_JSON = (
    os.getenv("GA4_SERVICE_ACCOUNT_JSON") or os.getenv("GA4_SA_JSON", "")
)

FEED_URL = os.getenv(
    "FEED_URL",
    "https://feeds.datafeedwatch.com/45030/8155cd63e2e29744fd3fb6fd20b04d7dab3275a5.json",
)

# Reuse the index name from the legacy deployment so Bonsai Sandbox
# (125 MB / 35k docs limit) doesn't have to host two parallel indexes.
INDEX_NAME = "products"
