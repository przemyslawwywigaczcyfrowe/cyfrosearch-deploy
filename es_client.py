from opensearchpy import OpenSearch as Elasticsearch
from config import ELASTIC_URL, ELASTIC_API_KEY

_es_client: Elasticsearch | None = None


def get_es_client() -> Elasticsearch:
    """Return a singleton search client (reuses HTTP connection pool).

    Backed by opensearch-py because the production cluster is Bonsai's
    managed OpenSearch 2.x. Aliased as `Elasticsearch` so the rest of
    the codebase reads naturally and stays compatible with the original
    spec that targets ES 8.x.
    """
    global _es_client
    if _es_client is None:
        kwargs: dict = {"hosts": [ELASTIC_URL], "timeout": 30}
        if ELASTIC_API_KEY:
            kwargs["headers"] = {"Authorization": f"ApiKey {ELASTIC_API_KEY}"}
        _es_client = Elasticsearch(**kwargs)
    return _es_client
