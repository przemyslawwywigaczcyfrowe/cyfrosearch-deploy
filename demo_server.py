"""
CyfroSearch Demo Server — standalone FastAPI app querying existing 'products' index.
Serves both the API and the HTML demo page.
Optimized for low-latency autosuggest (<100ms target).
"""

from __future__ import annotations

import os
import re
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles

# ── Config (env vars for cloud deploy, defaults for local dev) ──
ES_HOST = os.environ.get("ES_HOST", "http://localhost:9200")
ES_USER = os.environ.get("ES_USER", "elastic")
ES_PASSWORD = os.environ.get("ES_PASSWORD", "changeme")
ES_INDEX = os.environ.get("ES_INDEX", "products")
ES_API_KEY = os.environ.get("ES_API_KEY", "")  # Elastic Cloud uses API key auth

_es: AsyncElasticsearch | None = None


async def get_es() -> AsyncElasticsearch:
    global _es
    if _es is None:
        kwargs: dict[str, Any] = {
            "hosts": [ES_HOST],
            "request_timeout": 10,
        }
        # Elastic Cloud: use API key auth (preferred)
        if ES_API_KEY:
            kwargs["api_key"] = ES_API_KEY
        else:
            kwargs["basic_auth"] = (ES_USER, ES_PASSWORD)

        # Elastic Cloud uses HTTPS with valid certs — no verify_certs=False needed
        # For self-signed local dev, set ES_VERIFY_CERTS=false
        if os.environ.get("ES_VERIFY_CERTS", "true").lower() == "false":
            kwargs["verify_certs"] = False
            kwargs["ssl_show_warn"] = False

        _es = AsyncElasticsearch(**kwargs)
    return _es


# ── LRU Cache with TTL ──
class TTLCache:
    """Simple in-memory LRU cache with per-entry TTL."""
    def __init__(self, maxsize: int = 256, ttl: float = 60.0):
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl

    def get(self, key: str) -> Any | None:
        if key in self._cache:
            ts, val = self._cache[key]
            if time.monotonic() - ts < self._ttl:
                self._cache.move_to_end(key)
                return val
            del self._cache[key]
        return None

    def put(self, key: str, val: Any) -> None:
        self._cache[key] = (time.monotonic(), val)
        self._cache.move_to_end(key)
        while len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)


_suggest_cache = TTLCache(maxsize=512, ttl=60.0)

# ── Subcategory cache for category-intent detection ──
_subcategory_set: set[str] = set()  # lowered versions for matching
_subcategory_folded: dict[str, str] = {}  # folded_lower→original_ES_key (preserves ES casing)
_subcategory_lower_to_original: dict[str, str] = {}  # lower→original_ES_key


def _fold_polish(text: str) -> str:
    """Fold Polish diacritics: ą→a, ć→c, ę→e, ł→l, ń→n, ó→o, ś→s, ź→z, ż→z."""
    _map = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszz" + "ACELNOSZZ")
    return text.translate(_map)


# ── Model number normalization ──
# Splits tokens on digit→letter boundaries so "a7iv" → "a7 iv", "r6iii" → "r6 iii"
# This is critical for camera model searches like "sony a7iv", "canon r6ii", "fuji xt5ii"
_RE_DIGIT_TO_ALPHA = re.compile(r'(\d)([a-zA-Z])')

def _normalize_model_query(q: str) -> str:
    """Insert space at digit→letter boundaries within words.
    'sony a7iv' → 'sony a7 iv', 'canon r6ii' → 'canon r6 ii'
    Preserves original spacing and case.
    """
    return _RE_DIGIT_TO_ALPHA.sub(r'\1 \2', q)


# ── Roman numeral merging (matches ES analyzer behavior) ──
# The polish_folded analyzer concatenates "mark" + Roman numerals into one token:
# "mark III" → "markiii", "mark II" → "markii", "5D Mark IV" → "5d markiv"
# We must do the same at query time so match_phrase/multi_match align with the index.
_RE_MARK_ROMAN = re.compile(r'\bmark\s+(i{1,4}v?|iv|v)\b', re.IGNORECASE)

def _merge_mark_roman(q: str) -> str:
    """Merge 'mark III' → 'markIII' etc. to match ES analyzer tokenization."""
    return _RE_MARK_ROMAN.sub(lambda m: 'mark' + m.group(1), q)


# ── Precompiled constants (module-level, not per-request) ──
STOP = frozenset({
    "do", "na", "w", "z", "i", "s", "n", "ze", "od", "po", "dla", "za",
    "nie", "sie", "jest", "to", "ale", "jak", "sn", "body", "p",
    "mm", "wt", "ob", "szt",
    "aparat", "obiektyw", "kabel", "tusz", "pokrywka",
    "adapter", "pilot", "konwerter", "pasek", "torba", "futeralgx",
    "plecak", "lampa", "ladowarka", "bateria", "akumulator",
    "osona", "oslona", "filtr", "statyw", "grip", "zasilacz",
    "outlet", "uzywany", "u\u017cywany", "demo", "nowy", "nowa",
    "promocja", "promo", "zestaw", "set", "kit",
    "za", "0", "1", "z\u0142", "pln", "cena", "tylko",
    "zbroje", "zbroja", "klatka", "operatorska",
})

MODEL_PATTERN = re.compile(
    r'^(?:'
    r'[a-z]+\d+[a-z0-9]*'
    r'|\d+[a-z]+[a-z0-9]*'
    r'|eos|alpha|coolpix|powershot|lumix|cybershot'
    r'|rf|ef|fe|gm|sel|nikkor|fujinon|zuiko|sport'
    r'|mark|mkii|mkiii|mk'
    r')$', re.IGNORECASE
)

ACCESSORY_CODES = frozenset({
    "fz100", "fw50", "fz", "fw", "bx1", "lcs", "lcj",
    "bp", "lp", "nb", "blc", "bls", "blh", "bln", "btc", "bch",
    "en", "el", "enel", "lch", "mh", "cg", "cb", "ifc",
    "u11", "u21", "u5", "rxk", "sc",
    "wft", "gps", "ptz", "usb", "hdmi",
    "cage", "protect", "zbroje", "zbroja",
})

ACCESSORY_BRANDS = frozenset({
    "smallrig", "easycover", "jjc", "hoya", "marumi", "kenko",
    "ggs", "larmor", "patona", "newell", "godox", "yongnuo",
    "quadralite", "phottix", "pixel", "aputure", "nanlite",
    "zhiyun", "feiyu", "moza",
    "peak", "cotton", "carrier", "caruba",
})

MAIN_SUBCATS = frozenset({
    "aparaty cyfrowe", "bezlusterkowce", "kompakty", "lustrzanki",
    "obiektywy staloogniskowe", "obiektywy zmiennoogniskowe (zoom)",
    "obiektywy do lustrzanek", "obiektywy do bezlusterkowcow",
    "kamery cyfrowe", "kamery sportowe",
})

SKIP_CATS = frozenset({
    "do ", "zbroje", "klatki", "pokrywki", "kable", "akcesoria", "torby", "filtry",
})

# Condition-intent keywords (module-level, not per-request)
USED_INTENT_PREFIXES = ("używan", "uzywany", "używany", "uży", "uzy")

# ── Brand-intent detection ──
# Brand alias mapping — common abbreviations → canonical brand names
BRAND_ALIASES: dict[str, str] = {
    "fuji": "fujifilm",
    "pana": "panasonic",
}

# Brand cache — populated once from ES on first request
_brand_set: set[str] = set()            # {"nikon", "canon", "sigma", ...}
_brand_original: dict[str, str] = {}    # {"nikon": "Nikon", "sigma": "Sigma", ...}


async def _ensure_brands(es: AsyncElasticsearch) -> None:
    """One-time load of all brand names from ES for brand-intent detection."""
    global _brand_set, _brand_original
    if _brand_set:
        return
    try:
        resp = await es.search(
            index=ES_INDEX,
            body={"size": 0, "aggs": {"brands": {"terms": {"field": "brand", "size": 500}}}},
        )
        for b in resp["aggregations"]["brands"]["buckets"]:
            key = b["key"]
            _brand_set.add(key.lower())
            _brand_original[key.lower()] = key
        print(f"[OK] Loaded {len(_brand_set)} brands for brand-intent detection")
    except Exception as e:
        print(f"Warning: Could not load brands: {e}")


def _detect_brand_intent(q_lower: str) -> str | None:
    """Detect if query starts with (or IS) a brand name. Returns original-cased brand or None."""
    tokens = q_lower.split()
    if not tokens:
        return None
    # Check 2-word brands first ("peak design"), then 1-word
    for n in (2, 1):
        if n > len(tokens):
            continue
        prefix = " ".join(tokens[:n])
        if prefix in _brand_set:
            return _brand_original[prefix]
    # Check brand aliases ("fuji" → "fujifilm", "pana" → "panasonic")
    for n in (2, 1):
        if n > len(tokens):
            continue
        prefix = " ".join(tokens[:n])
        if prefix in BRAND_ALIASES:
            alias_target = BRAND_ALIASES[prefix]
            if alias_target in _brand_set:
                return _brand_original[alias_target]
    return None

TOKEN_RE = re.compile(r'[A-Za-z0-9\u0080-\u024F]+')


@asynccontextmanager
async def lifespan(app: FastAPI):
    es = await get_es()
    info = await es.info()
    print(f"[OK] Connected to ES {info['version']['number']}, cluster: {info['cluster_name']}")
    count = await es.count(index=ES_INDEX)
    print(f"[OK] Index '{ES_INDEX}' has {count['count']} products")

    # ── Warmup: pre-populate suggest cache for popular queries ──
    # ── Load subcategory names for category-intent detection ──
    global _subcategory_set, _subcategory_folded, _subcategory_lower_to_original
    try:
        agg_resp = await es.search(
            index=ES_INDEX,
            body={"size": 0, "aggs": {"subcats": {"terms": {"field": "subcategory", "size": 500}}}},
        )
        _subcategory_set = set()
        _subcategory_folded = {}
        _subcategory_lower_to_original = {}
        for b in agg_resp["aggregations"]["subcats"]["buckets"]:
            if b["doc_count"] >= 3:
                original_key = b["key"]  # Original ES casing, e.g. "lampy LED"
                key_lower = original_key.lower()
                _subcategory_set.add(key_lower)
                _subcategory_lower_to_original[key_lower] = original_key
                # Build folded→original_key mapping for accent-insensitive matching
                folded = _fold_polish(key_lower)
                _subcategory_folded[folded] = key_lower
        print(f"[OK] Loaded {len(_subcategory_set)} subcategories for category-intent detection")
    except Exception as e:
        print(f"Warning: Could not load subcategories: {e}")

    warmup_queries = ["canon", "sony", "nikon", "sigma", "fujifilm", "panasonic", "tamron",
                      "samyang", "leica", "olympus", "godox", "profoto", "hasselblad", "zeiss"]
    warmup_ok = 0
    for wq in warmup_queries:
        try:
            from starlette.datastructures import QueryParams
            # Directly invoke the suggest endpoint logic
            class _FakeResp:
                headers: dict = {}
            result = await _suggest_internal(es, wq, 7)
            _suggest_cache.put(f"{wq}:7", result)
            warmup_ok += 1
        except Exception as e:
            print(f"Warmup skip '{wq}': {e}")
    print(f"[OK] Warmed up {warmup_ok}/{len(warmup_queries)} queries into cache")

    yield
    if _es:
        await _es.close()


try:
    import orjson  # noqa: F401
    app = FastAPI(title="CyfroSearch Demo", lifespan=lifespan, default_response_class=ORJSONResponse)
except ImportError:
    app = FastAPI(title="CyfroSearch Demo", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────────
# SUGGEST ENDPOINT — optimized with msearch + cache
# ──────────────────────────────────────────────────

async def _suggest_internal(es: AsyncElasticsearch, q: str, limit: int) -> dict:
    """Core suggest logic — executes ES queries, builds response dict. No caching here."""
    product_results = []
    category_results = []
    brand_results = []
    popular_queries: list[dict] = []

    # Normalize model numbers: "a7iv" → "a7 iv", "r6ii" → "r6 ii"
    q = _merge_mark_roman(_normalize_model_query(q))

    q_lower = q.lower().strip()
    q_words = set(q_lower.split())

    # ── Brand-intent detection ──
    await _ensure_brands(es)
    _brand_intent = _detect_brand_intent(q_lower)

    # If brand alias matched (e.g. "fuji"→"Fujifilm"), also rewrite the ES query
    # so text matching finds products with the canonical brand name
    if _brand_intent:
        canonical_lower = _brand_intent.lower()
        # Check if the query used an alias (not the canonical name itself)
        tokens = q_lower.split()
        for n in (2, 1):
            if n > len(tokens):
                continue
            prefix = " ".join(tokens[:n])
            if prefix in BRAND_ALIASES and BRAND_ALIASES[prefix] == canonical_lower:
                # Replace the alias with canonical brand name in query
                q = _brand_intent + q[len(prefix):]
                q_lower = q.lower().strip()
                q_words = set(q_lower.split())
                break

    # ── Condition-intent detection ──
    # If query contains "używany"/"uzywany" (or prefix like "uży"/"uzy"),
    # heavily boost condition:"used" and suppress condition:"new" boost.
    # Also strip the condition keyword from the ES query so it doesn't penalize
    # products that don't have "używany" in their name field.
    _used_intent = any(
        any(w.startswith(kw) for kw in USED_INTENT_PREFIXES)
        for w in q_lower.split()
    )
    # Build a "clean" query without the condition keyword for ES text matching
    if _used_intent:
        q_clean_words = [
            w for w in q.split()
            if not any(w.lower().startswith(kw) for kw in USED_INTENT_PREFIXES)
        ]
        q_for_es = " ".join(q_clean_words).strip() or q
    else:
        q_for_es = q

    # ── Category-intent detection ──
    # If the query exactly matches (or closely matches) a subcategory name,
    # treat it as a category search — filter to that subcategory so accessories
    # like "adapter do obiektywów" don't pollute results for "obiektywy do lustrzanek"
    matched_subcategory: str | None = None
    if _subcategory_set:
        # 1) Exact match (with original Polish characters)
        if q_lower in _subcategory_set:
            matched_subcategory = q_lower
        else:
            # 2) Folded match (user types without Polish diacritics)
            q_folded = _fold_polish(q_lower)
            if q_folded in _subcategory_folded:
                matched_subcategory = _subcategory_folded[q_folded]
            else:
                # 3) Prefix match — both folded and unfolded
                best_match: str | None = None
                best_len = 0
                for folded_cat, original_cat in _subcategory_folded.items():
                    # Query is prefix of category: "obiektywy stalo" → "obiektywy stałoogniskowe"
                    if len(q_folded) >= 6 and folded_cat.startswith(q_folded):
                        if len(original_cat) > best_len:
                            best_match = original_cat
                            best_len = len(original_cat)
                    # Category is prefix of query: "bezlusterkowce" → "bezlusterkowce sony"
                    elif len(q_folded) >= 6 and q_folded.startswith(folded_cat) and len(folded_cat) >= 6:
                        if len(original_cat) > best_len:
                            best_match = original_cat
                            best_len = len(original_cat)
                if best_match:
                    matched_subcategory = best_match

    # ── Build all queries, execute as single msearch ──
    # Query 1: Products — hybrid scoring: relevance × (popularity + business signals)
    # Key insight: use boost_mode="sum" so popularity is ADDED to BM25 (not multiplied)
    # This prevents small accessories with repeated brand name from dominating flagships

    # Build the core bool query — if category intent detected, add a strong category filter
    if matched_subcategory:
        # Resolve to original ES key (preserves casing like "lampy LED")
        es_subcategory = _subcategory_lower_to_original.get(matched_subcategory, matched_subcategory)

        # Category-intent query: use constant_score filter so BM25 text relevance
        # does NOT affect ranking — all products in the category are equally relevant.
        # Ranking comes entirely from function_score (popularity, pageviews, etc.)
        product_bool_query = {
            "constant_score": {
                "filter": {"term": {"subcategory": es_subcategory}},
                "boost": 1,
            }
        }
    else:
        # Standard text-matching query
        q_trimmed = q.strip()
        q_upper = q_trimmed.upper()
        product_bool_query = {
            "bool": {
                "should": [
                    {
                        "multi_match": {
                            "query": q_for_es,
                            "type": "best_fields",
                            "fields": [
                                "name^3", "name.prefix^2",
                                "name.morfologik^2", "name.folded^2",
                                "brand^3", "sku^6", "ean^6",
                                "manufacturer_code^8", "id_erp^8",
                            ],
                            "fuzziness": "AUTO",
                            "prefix_length": 2,
                            "minimum_should_match": "70%",
                        }
                    },
                    {
                        "match_phrase": {
                            "name": {"query": q_for_es, "boost": 50}
                        }
                    },
                    {
                        "match_phrase_prefix": {
                            "name": {"query": q_for_es, "boost": 5}
                        }
                    },
                    # Exact-match on keyword fields (case-sensitive) for product codes
                    {"term": {"manufacturer_code": {"value": q_trimmed, "boost": 100}}},
                    {"term": {"id_erp": {"value": q_trimmed, "boost": 100}}},
                    {"term": {"sku": {"value": q_trimmed, "boost": 100}}},
                    {"term": {"ean": {"value": q_trimmed, "boost": 100}}},
                    # Case-insensitive fallback (uppercase variant)
                    {"term": {"manufacturer_code": {"value": q_upper, "boost": 90}}},
                    {"term": {"id_erp": {"value": q_upper, "boost": 90}}},
                ],
                "minimum_should_match": 1,
            }
        }

    # ── Build function_score functions based on query type ──
    if matched_subcategory:
        # Category-intent: user browses a category → rank by POPULARITY, not price
        # Use sqrt modifier (not log1p) so differences in pageviews actually matter:
        # sqrt(400)=20 vs sqrt(20)=4.5 → 4.4x difference (log1p would only give 2x)
        scoring_functions = [
            # Pageviews — strongest signal for category browsing
            {
                "field_value_factor": {
                    "field": "ga4.pageviews_30d",
                    "factor": 1.0, "modifier": "sqrt", "missing": 0,
                },
                "weight": 50,
            },
            # Sessions — complements pageviews
            {
                "field_value_factor": {
                    "field": "ga4.sessions_30d",
                    "factor": 1.0, "modifier": "sqrt", "missing": 0,
                },
                "weight": 30,
            },
            # Popularity score (composite GA4 metric)
            {
                "field_value_factor": {
                    "field": "ga4.popularity_score",
                    "factor": 1.0, "modifier": "sqrt", "missing": 0,
                },
                "weight": 20,
            },
            # Add-to-carts — high purchase intent signal
            {
                "field_value_factor": {
                    "field": "ga4.add_to_carts_30d",
                    "factor": 1.0, "modifier": "log1p", "missing": 0,
                },
                "weight": 40,
            },
            # Availability — crucial for category browsing
            {"filter": {"term": {"availability": "in_stock"}}, "weight": 30},
            # Bestseller
            {"filter": {"term": {"is_bestseller": True}}, "weight": 60},
            # Image available
            {"filter": {"term": {"has_image": True}}, "weight": 10},
            # Promo
            {"filter": {"term": {"is_promo": True}}, "weight": 10},
            # Minimal price boost — just enough to prefer real products over free samples
            {
                "field_value_factor": {
                    "field": "price",
                    "factor": 0.001, "modifier": "log1p", "missing": 0,
                },
                "weight": 2,
            },
            # Condition-based boost — dynamically switch based on user intent
            *(
                [{"filter": {"term": {"condition": "used"}}, "weight": 200}]
                if _used_intent else
                [{"filter": {"term": {"condition": "new"}}, "weight": 25}]
            ),
        ]
    else:
        # Standard text-matching: brand queries like "canon", "sony a7" etc.
        # Price matters more here (Canon → EOS R5, not RC-6 remote)
        #
        # Brand-intent: when detected, boost products of that brand (+300)
        # and strongly boost main categories (+300) so cameras/lenses beat
        # accessories, scopes, and other non-core products of the same brand.
        _main_cat_weight = 300 if _brand_intent else 80
        _price_weight = 30 if _brand_intent else 15
        scoring_functions = [
            # Brand-intent boost — if user searches a brand, prefer that brand's products
            *(
                [{"filter": {"term": {"brand": _brand_intent}}, "weight": 300}]
                if _brand_intent else []
            ),
            # Popularity — strongest signal
            {
                "field_value_factor": {
                    "field": "ga4.popularity_score",
                    "factor": 1.5, "modifier": "log1p", "missing": 0,
                },
                "weight": 80,
            },
            # Sales volume boost
            {
                "field_value_factor": {
                    "field": "sales_30d",
                    "factor": 1.0, "modifier": "log1p", "missing": 0,
                },
                "weight": 30,
            },
            # Price tier boost — flagship products more relevant for brand queries
            # Higher weight when brand-intent (prefer cameras over lens caps)
            {
                "field_value_factor": {
                    "field": "price",
                    "factor": 0.001, "modifier": "log1p", "missing": 0,
                },
                "weight": _price_weight,
            },
            # Availability
            {"filter": {"term": {"availability": "in_stock"}}, "weight": 50},
            # Bestseller — strong signal
            {"filter": {"term": {"is_bestseller": True}}, "weight": 60},
            # Main product categories get boosted over accessories
            # Much higher weight when brand-intent detected (cameras > scopes/lens caps)
            {"filter": {"terms": {"subcategory": [
                "bezlusterkowce", "aparaty cyfrowe", "lustrzanki", "kompakty",
                "obiektywy stałoogniskowe", "obiektywy zmiennoogniskowe (zoom)",
                "obiektywy do lustrzanek", "obiektywy do bezlusterkowców",
                "kamery cyfrowe", "kamery sportowe", "drony",
            ]}}, "weight": _main_cat_weight},
            # Image available
            {"filter": {"term": {"has_image": True}}, "weight": 10},
            # Promo
            {"filter": {"term": {"is_promo": True}}, "weight": 20},
            # New products (cold start problem solver)
            {"filter": {"term": {"is_new": True}}, "weight": 30},
            # Condition-based boost — dynamically switch based on user intent
            # Higher "new" weight when brand-intent (prefer new over used products)
            *(
                [{"filter": {"term": {"condition": "used"}}, "weight": 200}]
                if _used_intent else
                [{"filter": {"term": {"condition": "new"}}, "weight": 50 if _brand_intent else 25}]
            ),
        ]

    # Temporary debug: log the brand intent
    if _brand_intent:
        print(f"[BRAND-INTENT] q='{q}', brand='{_brand_intent}', main_cat_weight={_main_cat_weight}, funcs={len(scoring_functions)}")

    product_body = {
        "size": limit,
        "query": {
            "function_score": {
                "query": product_bool_query,
                "functions": scoring_functions,
                "score_mode": "sum",
                "boost_mode": "sum",
                "max_boost": 2000,
            }
        },
        "highlight": {
            "pre_tags": ["<mark>"], "post_tags": ["</mark>"],
            "fields": {
                "name": {"number_of_fragments": 0},
                "name.prefix": {"number_of_fragments": 0},
            },
        },
        "_source": [
            "name", "brand", "price", "sale_price",
            "availability", "condition", "image_url", "product_url",
            "is_promo", "is_bestseller", "is_new",
            "sku", "ean", "manufacturer_code",
        ],
    }

    # Query 2: Categories + Brands (combined into ONE query with multiple aggs)
    agg_body = {
        "size": 0,
        "query": {
            "multi_match": {
                "query": q,
                "fields": ["name^3", "name.prefix^2", "brand^2", "subcategory"],
                "fuzziness": "AUTO",
            }
        },
        "aggs": {
            "subcategories": {"terms": {"field": "subcategory", "size": 6}},
            "categories": {"terms": {"field": "category_path", "size": 5}},
            "brands": {"terms": {"field": "brand", "size": 4}},
        },
    }

    # Query 3: Suggestion source (top 20 popular products for name extraction)
    suggest_source_body = {
        "size": 20,
        "query": {
            "function_score": {
                "query": {
                    "multi_match": {
                        "query": q,
                        "fields": ["name^3", "name.prefix^2", "brand^3"],
                        "fuzziness": "AUTO",
                    }
                },
                "functions": [
                    {
                        "field_value_factor": {
                            "field": "ga4.popularity_score",
                            "factor": 1.5, "modifier": "log1p", "missing": 0,
                        },
                        "weight": 80,
                    },
                    {"filter": {"term": {"availability": "in_stock"}}, "weight": 30},
                    {"filter": {"term": {"is_bestseller": True}}, "weight": 50},
                    {
                        "field_value_factor": {
                            "field": "sales_30d",
                            "factor": 1.0, "modifier": "log1p", "missing": 0,
                        },
                        "weight": 30,
                    },
                    # Prefer main product categories for suggestion extraction
                    {"filter": {"terms": {"subcategory": [
                        "bezlusterkowce", "aparaty cyfrowe", "lustrzanki", "kompakty",
                        "obiektywy stałoogniskowe", "obiektywy zmiennoogniskowe (zoom)",
                        "obiektywy do lustrzanek", "obiektywy do bezlusterkowców",
                        "kamery cyfrowe", "kamery sportowe", "drony",
                    ]}}, "weight": 60},
                ],
                "score_mode": "sum",
                "boost_mode": "sum",
            }
        },
        "_source": ["name", "brand", "subcategory", "ga4.popularity_score", "sales_30d"],
    }

    # ── Execute all 3 queries as a single msearch call ──
    try:
        msearch_body = []
        for body in (product_body, agg_body, suggest_source_body):
            msearch_body.append({"index": ES_INDEX})
            msearch_body.append(body)

        ms_resp = await es.msearch(body=msearch_body)
        responses = ms_resp["responses"]

        product_resp = responses[0]
        agg_resp = responses[1]
        suggest_resp = responses[2]
    except Exception as e:
        print(f"msearch error: {e}")
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)
        return {"meta": {"query": q, "time_ms": elapsed_ms, "total_products": 0},
                "popular_queries": [], "categories": [], "brands": [], "products": []}

    # ── Parse products ──
    try:
        for hit in product_resp.get("hits", {}).get("hits", []):
            src = hit["_source"]
            hl = hit.get("highlight", {})
            highlighted_name = hl.get("name", hl.get("name.prefix", [src["name"]]))[0]

            price = src.get("sale_price") or src.get("price", 0)
            original_price = src.get("price") if src.get("sale_price") else None
            discount_pct = 0
            if original_price and src.get("sale_price") and original_price > src["sale_price"]:
                discount_pct = round((1 - src["sale_price"] / original_price) * 100)

            badge = None
            if src.get("is_bestseller"):
                badge = "Bestseller"
            elif src.get("is_promo"):
                badge = "Promocja"
            elif src.get("is_new"):
                badge = "Nowość"

            product_results.append({
                "name": src["name"],
                "highlight": highlighted_name,
                "brand": src.get("brand", ""),
                "price": price,
                "original_price": original_price,
                "discount_pct": discount_pct,
                "currency": "PLN",
                "availability": src.get("availability", "out_of_stock"),
                "image_url": src.get("image_url"),
                "product_url": src.get("product_url", "#"),
                "badge": badge,
            })
    except Exception as e:
        print(f"Product parse error: {e}")

    # ── Parse categories — prefer subcategories ──
    try:
        aggs = agg_resp.get("aggregations", {})
        for bucket in aggs.get("subcategories", {}).get("buckets", []):
            name = bucket["key"]
            if name and len(name) > 2:
                category_results.append({
                    "name": name, "short_name": name, "count": bucket["doc_count"],
                })
        if not category_results:
            for bucket in aggs.get("categories", {}).get("buckets", []):
                path = bucket["key"]
                parts = path.split(" > ")
                category_results.append({
                    "name": path,
                    "short_name": parts[-1] if parts else path,
                    "count": bucket["doc_count"],
                })
    except Exception as e:
        print(f"Category parse error: {e}")

    # ── Parse brands ──
    try:
        for bucket in agg_resp.get("aggregations", {}).get("brands", {}).get("buckets", []):
            brand_results.append({"name": bucket["key"], "count": bucket["doc_count"]})
    except Exception as e:
        print(f"Brand parse error: {e}")

    # ── Build smart query suggestions from suggest_resp ──
    try:
        _build_suggestions(suggest_resp, q_lower, q_words,
                           brand_results, category_results, popular_queries)
    except Exception as e:
        print(f"Query suggestion error: {e}")

    return {
        "meta": {
            "query": q,
            "time_ms": 0,
            "total_products": len(product_results),
            "cached": False,
        },
        "popular_queries": popular_queries[:5],
        "categories": category_results[:5],
        "brands": brand_results[:4],
        "products": product_results,
    }


@app.get("/api/suggest")
async def suggest(
    response: Response,
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(7, ge=1, le=20),
):
    """Main autosuggest endpoint — returns products, categories, brands, query suggestions."""
    start = time.monotonic()

    # ── Check cache ──
    cache_key = f"{q.lower().strip()}:{limit}"
    cached = _suggest_cache.get(cache_key)
    if cached is not None:
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)
        cached["meta"]["time_ms"] = elapsed_ms
        cached["meta"]["cached"] = True
        response.headers["Cache-Control"] = "public, max-age=30"
        return cached

    es = await get_es()
    result = await _suggest_internal(es, q, limit)

    elapsed_ms = round((time.monotonic() - start) * 1000, 1)
    result["meta"]["time_ms"] = elapsed_ms

    # ── Store in cache ──
    _suggest_cache.put(cache_key, result)

    response.headers["Cache-Control"] = "public, max-age=30"
    return result


def _build_suggestions(
    suggest_resp: dict,
    q_lower: str,
    q_words: set[str],
    brand_results: list[dict],
    category_results: list[dict],
    popular_queries: list[dict],
) -> None:
    """Extract model/series suggestions from product names. Runs synchronously, CPU-only."""
    existing: set[str] = set()
    candidates: dict[str, dict] = {}

    hits = suggest_resp.get("hits", {}).get("hits", [])

    # --- Pass 1: Extract model/series phrases + cache tokenized data for Pass 3 ---
    tokenized_hits: list[tuple[str, str, float, bool, list[str]]] = []  # (name_lower, brand_l, pop, is_main, after_tokens not needed here)

    for hit in hits:
        src = hit["_source"]
        name = src.get("name", "")
        brand = (src.get("brand", "") or "").lower()
        subcat = (src.get("subcategory", "") or "").lower()
        pop = src.get("ga4", {}).get("popularity_score", 0) or 0
        sales = src.get("sales_30d", 0) or 0
        is_main = subcat in MAIN_SUBCATS

        # Tokenize — merge number + "mm"
        raw_tokens = TOKEN_RE.findall(name)
        tokens: list[str] = []
        skip_next = False
        for j, t in enumerate(raw_tokens):
            if skip_next:
                skip_next = False
                continue
            if j + 1 < len(raw_tokens) and raw_tokens[j + 1].lower() == "mm" and t.isdigit():
                tokens.append(t + "mm")
                skip_next = True
            elif t.lower() == "mm" and tokens and tokens[-1][-1].isdigit():
                tokens[-1] = tokens[-1] + "mm"
            else:
                tokens.append(t)
        tokens_lower = [t.lower() for t in tokens]

        # Cache for Pass 3 (avoids re-tokenizing)
        tokenized_hits.append((name.lower(), brand, pop, is_main, tokens_lower))

        # Build 2-3 token windows
        for wlen in (2, 3):
            for i in range(len(tokens_lower) - wlen + 1):
                window = tokens_lower[i:i + wlen]
                first = window[0]
                last = window[-1]

                if first not in q_words and first != brand:
                    continue
                if first in ACCESSORY_BRANDS and first not in q_words:
                    continue
                if any(w in ACCESSORY_BRANDS and w not in q_words for w in window):
                    continue
                if all(w in q_words for w in window):
                    continue
                if last in STOP or len(last) <= 1:
                    continue
                if any(w in STOP for w in window[1:-1]):
                    continue
                if any(w in ACCESSORY_CODES for w in window):
                    continue
                if all(w.isdigit() for w in window):
                    continue
                meaningful = [w for w in window if w not in STOP and len(w) > 1]
                if len(meaningful) < 2:
                    continue
                has_model = any(MODEL_PATTERN.match(w) for w in window)
                if len(set(window)) < len(window):
                    continue
                model_tokens_all = [w for w in window if MODEL_PATTERN.match(w)]
                model_not_query = [w for w in model_tokens_all if w not in q_words]
                if len(model_tokens_all) >= 2 and len(model_not_query) >= 1:
                    continue

                phrase = " ".join(tokens_lower[i:i + wlen])
                if phrase == q_lower:
                    continue

                if phrase not in candidates:
                    candidates[phrase] = {
                        "count": 0, "pop_sum": 0.0,
                        "has_model": False, "main_product": False,
                        "display": " ".join(tokens[i:i + wlen]),
                    }
                c = candidates[phrase]
                c["count"] += 1
                c["pop_sum"] += pop + sales * 0.5
                if has_model:
                    c["has_model"] = True
                if is_main:
                    c["main_product"] = True

    # --- Pass 2: "category + brand" suggestions for single-word brand queries ---
    if brand_results and len(q_words) == 1:
        top_brand = brand_results[0]["name"].lower()
        if top_brand in q_lower or q_lower in top_brand:
            for cat in category_results[:4]:
                cat_name = cat.get("short_name", cat["name"]).lower()
                if any(cat_name.startswith(skip) or cat_name == skip for skip in SKIP_CATS):
                    continue
                phrase = f"{cat_name} {top_brand}"
                if phrase not in candidates and len(phrase.split()) <= 3:
                    candidates[phrase] = {
                        "count": 3, "pop_sum": 50.0,
                        "has_model": False, "main_product": True,
                        "display": phrase,
                    }

    # --- Scoring & ranking ---
    scored: list[tuple[str, float]] = []
    for phrase, info in candidates.items():
        if info["count"] < 2:
            continue
        score = info["count"] * 2.0 + info["pop_sum"] * 0.1
        if info["has_model"]:
            score += 15.0
        if info["main_product"]:
            score += 8.0
        words = set(phrase.split())
        if words & ACCESSORY_CODES:
            score *= 0.15
        elif words & STOP:
            score *= 0.5
        scored.append((phrase, score))

    scored.sort(key=lambda x: -x[1])

    # Deduplicate overlapping phrases
    selected: list[tuple[str, float]] = []
    selected_set: set[str] = set()
    for phrase, score in scored:
        if any(phrase in sel for sel in selected_set):
            continue
        superset_of = [sel for sel in selected_set if sel in phrase]
        if superset_of:
            selected = [(p, s) for p, s in selected if p not in superset_of]
            selected_set -= set(superset_of)
        selected.append((phrase, score))
        selected_set.add(phrase)
        if len(selected) >= 5:
            break

    for phrase, score in selected:
        popular_queries.append({"text": candidates[phrase]["display"], "score": round(score, 1)})
        existing.add(phrase)

    # --- Pass 3: Query variant suggestions if fewer than 5 ---
    # Uses tokenized_hits cached from Pass 1 (no re-tokenization needed)
    if len(popular_queries) < 5:
        variant_phrases: dict[str, float] = {}
        for name_lower, brand_l, pop, is_main, _tokens_lower in tokenized_hits:
            if brand_l in ACCESSORY_BRANDS:
                continue
            pos = name_lower.find(q_lower)
            if pos < 0:
                continue
            after = name_lower[pos + len(q_lower):].strip()
            if not after:
                continue

            after_tokens = TOKEN_RE.findall(after)
            while after_tokens and (after_tokens[0].lower() in STOP or len(after_tokens[0]) <= 1):
                after_tokens.pop(0)
            if not after_tokens:
                continue

            for take in (2, 1):
                if take > len(after_tokens):
                    continue
                suffix_tokens = after_tokens[:take]
                if all(t.lower() in STOP or len(t) <= 1 for t in suffix_tokens):
                    continue
                if any(t.lower() in ACCESSORY_CODES for t in suffix_tokens):
                    continue
                if any(t.lower() in ACCESSORY_BRANDS for t in suffix_tokens):
                    continue
                if any(t.isdigit() and len(t) > 4 for t in suffix_tokens):
                    continue
                if any(t.lower() in STOP for t in suffix_tokens):
                    continue
                variant = q_lower + " " + " ".join(t.lower() for t in suffix_tokens)
                if variant not in existing and variant != q_lower:
                    if variant not in variant_phrases:
                        variant_phrases[variant] = 0.0
                    boost = 10.0 if is_main else 3.0
                    if take >= 2:
                        boost *= 1.5
                    variant_phrases[variant] += pop * 0.1 + boost

        sorted_variants = sorted(variant_phrases.items(), key=lambda x: -x[1])
        for variant, vscore in sorted_variants:
            if len(popular_queries) >= 5:
                break
            if any(variant in pq["text"].lower() or pq["text"].lower() in variant
                   for pq in popular_queries):
                continue
            popular_queries.append({"text": variant, "score": round(vscore, 1)})
            existing.add(variant)


# ──────────────────────────────────────────────────
# FULL SEARCH ENDPOINT
# ──────────────────────────────────────────────────

@app.get("/api/search")
async def search(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    per_page: int = Query(24, ge=1, le=100),
    brand: str | None = None,
    category: str | None = None,
    availability: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    sort: str = "relevance",
):
    es = await get_es()
    # Normalize model numbers: "a7iv" → "a7 iv", "r6ii" → "r6 ii"
    q = _merge_mark_roman(_normalize_model_query(q))
    offset = (page - 1) * per_page

    filters = []
    if brand:
        filters.append({"terms": {"brand": brand.split(",")}})
    if category:
        filters.append({"term": {"category_path": category}})
    if availability:
        filters.append({"term": {"availability": availability}})
    if price_min is not None:
        filters.append({"range": {"price": {"gte": price_min}}})
    if price_max is not None:
        filters.append({"range": {"price": {"lte": price_max}}})

    # ── Brand-intent detection (same logic as suggest) ──
    await _ensure_brands(es)
    q_lower_s = q.lower().strip()
    _brand_intent_s = _detect_brand_intent(q_lower_s)

    # Rewrite alias in query text (e.g. "fuji xt5" → "Fujifilm xt5")
    if _brand_intent_s:
        canonical_lower_s = _brand_intent_s.lower()
        tokens_s = q_lower_s.split()
        for n in (2, 1):
            if n > len(tokens_s):
                continue
            prefix_s = " ".join(tokens_s[:n])
            if prefix_s in BRAND_ALIASES and BRAND_ALIASES[prefix_s] == canonical_lower_s:
                q = _brand_intent_s + q[len(prefix_s):]
                q_lower_s = q.lower().strip()
                break

    # ── Condition-intent detection (same logic as suggest) ──
    _used_intent_s = any(
        any(w.startswith(kw) for kw in USED_INTENT_PREFIXES)
        for w in q_lower_s.split()
    )
    if _used_intent_s:
        q_for_es_s = " ".join(
            w for w in q.split()
            if not any(w.lower().startswith(kw) for kw in USED_INTENT_PREFIXES)
        ).strip() or q
        # Force filter to used products
        filters.append({"term": {"condition": "used"}})
    else:
        q_for_es_s = q

    q_trimmed = q.strip()
    q_upper = q_trimmed.upper()
    must = {
        "bool": {
            "should": [
                {
                    "multi_match": {
                        "query": q_for_es_s,
                        "fields": [
                            "name^5", "name.prefix^3", "name.morfologik^2",
                            "name.folded^2", "brand^3", "description", "tags^2",
                            "sku^6", "ean^6", "manufacturer_code^8", "id_erp^8",
                        ],
                        "fuzziness": "AUTO",
                        "minimum_should_match": "70%",
                    }
                },
                # Exact-match on keyword fields for product codes
                {"term": {"manufacturer_code": {"value": q_trimmed, "boost": 100}}},
                {"term": {"id_erp": {"value": q_trimmed, "boost": 100}}},
                {"term": {"sku": {"value": q_trimmed, "boost": 100}}},
                {"term": {"ean": {"value": q_trimmed, "boost": 100}}},
                {"term": {"manufacturer_code": {"value": q_upper, "boost": 90}}},
                {"term": {"id_erp": {"value": q_upper, "boost": 90}}},
            ],
            "minimum_should_match": 1,
        }
    }

    base_query = {"bool": {"must": [must], "filter": filters}} if filters else must

    # Wrap in function_score when brand-intent detected (same pattern as suggest)
    if _brand_intent_s:
        query = {
            "function_score": {
                "query": base_query,
                "functions": [
                    {"filter": {"term": {"brand": _brand_intent_s}}, "weight": 300},
                    {"filter": {"terms": {"subcategory": [
                        "bezlusterkowce", "aparaty cyfrowe", "lustrzanki", "kompakty",
                        "obiektywy stałoogniskowe", "obiektywy zmiennoogniskowe (zoom)",
                        "obiektywy do lustrzanek", "obiektywy do bezlusterkowców",
                        "kamery cyfrowe", "kamery sportowe", "drony",
                    ]}}, "weight": 300},
                    {"filter": {"term": {"availability": "in_stock"}}, "weight": 50},
                    {"filter": {"term": {"condition": "new"}}, "weight": 50},
                ],
                "score_mode": "sum",
                "boost_mode": "sum",
            }
        }
    else:
        query = base_query

    body: dict[str, Any] = {
        "query": query,
        "from": offset,
        "size": per_page,
        "aggs": {
            "brands": {"terms": {"field": "brand", "size": 20}},
            "categories": {"terms": {"field": "category_path", "size": 15}},
            "availability": {"terms": {"field": "availability", "size": 3}},
        },
        "_source": [
            "name", "brand", "price", "sale_price", "currency",
            "availability", "condition", "image_url", "product_url",
            "is_promo", "is_bestseller", "is_new", "category_path",
        ],
    }

    if sort == "price_asc":
        body["sort"] = [{"price": "asc"}]
    elif sort == "price_desc":
        body["sort"] = [{"price": "desc"}]
    elif sort == "popularity":
        body["sort"] = [{"ga4.popularity_score": {"order": "desc", "missing": "_last"}}]

    resp = await es.search(index=ES_INDEX, body=body)
    total = resp["hits"]["total"]["value"]

    products = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        products.append({
            "name": src["name"],
            "brand": src.get("brand", ""),
            "price": src.get("sale_price") or src.get("price", 0),
            "original_price": src.get("price") if src.get("sale_price") else None,
            "currency": src.get("currency", "PLN"),
            "availability": src.get("availability"),
            "image_url": src.get("image_url"),
            "product_url": src.get("product_url"),
        })

    facets = {}
    for agg_name, agg_data in resp.get("aggregations", {}).items():
        facets[agg_name] = [
            {"key": b["key"], "count": b["doc_count"]}
            for b in agg_data.get("buckets", [])
        ]

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "products": products,
        "facets": facets,
    }


# ──────────────────────────────────────────────────
# HEALTH
# ──────────────────────────────────────────────────

@app.get("/api/debug-brand")
async def debug_brand(q: str = Query("nikon"), limit: int = 5):
    """Debug endpoint to verify brand-intent detection and raw ES scores."""
    es = await get_es()
    await _ensure_brands(es)
    q_lower = q.lower().strip()
    brand = _detect_brand_intent(q_lower)

    # Build the EXACT same query as _suggest_internal
    q_norm = _merge_mark_roman(_normalize_model_query(q))
    q_for_es = q_norm
    scoring_functions = []
    _main_cat_weight = 300 if brand else 80
    _price_weight = 30 if brand else 15
    if brand:
        scoring_functions.append({"filter": {"term": {"brand": brand}}, "weight": 300})
    scoring_functions.extend([
        {"field_value_factor": {"field": "ga4.popularity_score", "factor": 1.5, "modifier": "log1p", "missing": 0}, "weight": 80},
        {"field_value_factor": {"field": "sales_30d", "factor": 1.0, "modifier": "log1p", "missing": 0}, "weight": 30},
        {"field_value_factor": {"field": "price", "factor": 0.001, "modifier": "log1p", "missing": 0}, "weight": _price_weight},
        {"filter": {"term": {"availability": "in_stock"}}, "weight": 50},
        {"filter": {"term": {"is_bestseller": True}}, "weight": 60},
        {"filter": {"terms": {"subcategory": [
            "bezlusterkowce", "aparaty cyfrowe", "lustrzanki", "kompakty",
            "obiektywy sta\u0142oogniskowe", "obiektywy zmiennoogniskowe (zoom)",
            "obiektywy do lustrzanek", "obiektywy do bezlusterkowc\u00f3w",
            "kamery cyfrowe", "kamery sportowe", "drony",
        ]}}, "weight": _main_cat_weight},
        {"filter": {"term": {"has_image": True}}, "weight": 10},
        {"filter": {"term": {"is_promo": True}}, "weight": 20},
        {"filter": {"term": {"is_new": True}}, "weight": 30},
        {"filter": {"term": {"condition": "new"}}, "weight": 50 if brand else 25},
    ])
    body = {
        "size": limit,
        "query": {
            "function_score": {
                "query": {
                    "bool": {
                        "should": [
                            {"multi_match": {"query": q_for_es, "type": "best_fields", "fields": ["name^3", "name.prefix^2", "name.morfologik^2", "name.folded^2", "brand^3", "sku^6", "ean^6", "manufacturer_code^8", "id_erp^8"], "fuzziness": "AUTO", "prefix_length": 2, "minimum_should_match": "70%"}},
                            {"match_phrase": {"name": {"query": q_for_es, "boost": 50}}},
                            {"match_phrase_prefix": {"name": {"query": q_for_es, "boost": 5}}},
                        ],
                        "minimum_should_match": 1,
                    }
                },
                "functions": scoring_functions,
                "score_mode": "sum",
                "boost_mode": "sum",
                "max_boost": 2000,
            }
        },
        "_source": ["name", "brand", "subcategory", "availability", "condition", "ga4.popularity_score", "price"],
    }
    resp = await es.search(index=ES_INDEX, body=body)
    hits = []
    for h in resp["hits"]["hits"]:
        hits.append({"id": h["_id"], "score": round(h["_score"], 2), "src": h["_source"]})
    return {
        "brand_intent": brand,
        "main_cat_weight": _main_cat_weight,
        "num_functions": len(scoring_functions),
        "total_hits": resp["hits"]["total"]["value"],
        "hits": hits,
    }


@app.get("/api/health")
async def health():
    es = await get_es()
    try:
        cluster = await es.cluster.health()
        count = await es.count(index=ES_INDEX)
        return {
            "status": "ok",
            "es_status": cluster["status"],
            "product_count": count["count"],
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ──────────────────────────────────────────────────
# TRENDING / ZERO-STATE ENDPOINT
# ──────────────────────────────────────────────────

_trending_cache = TTLCache(maxsize=1, ttl=300.0)  # 5-min cache for trending


@app.get("/api/trending")
async def trending():
    """Return trending products and top categories for zero-state display (empty search box focus)."""
    cached = _trending_cache.get("__trending__")
    if cached is not None:
        return cached

    es = await get_es()
    try:
        # Two queries in one msearch: top products + top categories
        msearch_body = [
            {"index": ES_INDEX},
            {
                "size": 6,
                "query": {
                    "function_score": {
                        "query": {
                            "bool": {
                                "filter": [
                                    {"term": {"availability": "in_stock"}},
                                    {"term": {"has_image": True}},
                                    {"terms": {"subcategory": [
                                        "bezlusterkowce", "aparaty cyfrowe", "lustrzanki", "kompakty",
                                        "obiektywy stałoogniskowe", "obiektywy zmiennoogniskowe (zoom)",
                                        "obiektywy do lustrzanek", "obiektywy do bezlusterkowców",
                                        "kamery cyfrowe", "kamery sportowe", "drony",
                                    ]}},
                                ]
                            }
                        },
                        "functions": [
                            {
                                "field_value_factor": {
                                    "field": "ga4.popularity_score",
                                    "factor": 1.5, "modifier": "sqrt", "missing": 0,
                                },
                                "weight": 80,
                            },
                            {"filter": {"term": {"is_bestseller": True}}, "weight": 60},
                            {
                                "field_value_factor": {
                                    "field": "ga4.add_to_carts_30d",
                                    "factor": 1.0, "modifier": "log1p", "missing": 0,
                                },
                                "weight": 40,
                            },
                            {"filter": {"term": {"condition": "new"}}, "weight": 25},
                        ],
                        "score_mode": "sum",
                        "boost_mode": "sum",
                    }
                },
                "_source": [
                    "name", "brand", "price", "sale_price",
                    "image_url", "product_url", "is_bestseller", "is_promo", "is_new",
                ],
            },
            {"index": ES_INDEX},
            {
                "size": 0,
                "query": {"term": {"availability": "in_stock"}},
                "aggs": {
                    "subcategories": {"terms": {"field": "subcategory", "size": 8}},
                },
            },
        ]

        ms_resp = await es.msearch(body=msearch_body)
        responses = ms_resp["responses"]

        products = []
        for hit in responses[0].get("hits", {}).get("hits", []):
            src = hit["_source"]
            price = src.get("sale_price") or src.get("price", 0)
            original_price = src.get("price") if src.get("sale_price") else None
            badge = None
            if src.get("is_bestseller"):
                badge = "Bestseller"
            elif src.get("is_promo"):
                badge = "Promocja"
            elif src.get("is_new"):
                badge = "Nowość"
            products.append({
                "name": src["name"],
                "brand": src.get("brand", ""),
                "price": price,
                "original_price": original_price,
                "image_url": src.get("image_url"),
                "product_url": src.get("product_url", "#"),
                "badge": badge,
            })

        categories = []
        for bucket in responses[1].get("aggregations", {}).get("subcategories", {}).get("buckets", []):
            name = bucket["key"]
            if name and len(name) > 2:
                categories.append({"name": name, "count": bucket["doc_count"]})

        result = {"products": products, "categories": categories[:6]}
        _trending_cache.put("__trending__", result)
        return result

    except Exception as e:
        print(f"Trending endpoint error: {e}")
        return {"products": [], "categories": []}


# ──────────────────────────────────────────────────
# SERVE STATIC FILES (widget JS, etc.)
# ──────────────────────────────────────────────────

_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ──────────────────────────────────────────────────
# SERVE HTML DEMO PAGE
# ──────────────────────────────────────────────────

_html_cache: str | None = None


@app.get("/", response_class=HTMLResponse)
async def demo_page(request: Request):
    """Serve the demo HTML page. Dynamically inject the correct API base URL."""
    global _html_cache
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
    scheme = request.headers.get("x-forwarded-proto") or "http"
    api_base = f"{scheme}://{host}"

    if _html_cache is None:
        html_path = Path(__file__).parent / "demo.html"
        _html_cache = html_path.read_text(encoding="utf-8")

    html_content = _html_cache.replace("__API_BASE__", api_base)
    return HTMLResponse(content=html_content)


@app.get("/widget-test", response_class=HTMLResponse)
async def widget_test_page(request: Request):
    """Minimal test page demonstrating the embeddable widget on a foreign site."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
    scheme = request.headers.get("x-forwarded-proto") or "http"
    api_base = f"{scheme}://{host}"
    return HTMLResponse(content=f"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>cyfrowe.pl — Widget Test</title>
<style>
  body {{ font-family: Arial, sans-serif; margin: 0; background: #f9f9f9; color: #333; }}
  header {{ background: #1a1a2e; color: #fff; padding: 12px 24px; display: flex; align-items: center; gap: 20px; }}
  header .logo {{ font-size: 20px; font-weight: bold; text-decoration: none; color: #fff; }}
  header .logo span {{ color: #e53935; }}
  .search-box {{ flex: 1; max-width: 500px; }}
  .search-box input {{
    width: 100%; padding: 10px 14px; font-size: 14px;
    border: 2px solid #555; border-radius: 4px; background: #fff; color: #333;
    outline: none;
  }}
  .search-box input:focus {{ border-color: #e53935; }}
  nav {{ background: #16213e; padding: 8px 24px; display: flex; gap: 20px; }}
  nav a {{ color: #ccc; text-decoration: none; font-size: 13px; }}
  nav a:hover {{ color: #fff; }}
  main {{ max-width: 1000px; margin: 30px auto; padding: 0 20px; }}
  .banner {{ background: #fff; border-radius: 8px; padding: 40px; text-align: center; border: 1px solid #ddd; margin-bottom: 24px; }}
  .banner h1 {{ font-size: 22px; margin-bottom: 8px; }}
  .banner p {{ color: #777; }}
  .code-box {{ background: #1a1a2e; color: #e53935; padding: 16px 20px; border-radius: 6px; font-family: monospace; font-size: 13px; margin: 20px 0; overflow-x: auto; }}
  .products {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; }}
  .product {{ background: #fff; border: 1px solid #eee; border-radius: 6px; padding: 16px; text-align: center; }}
  .product .placeholder {{ width: 100px; height: 100px; background: #f0f0f0; margin: 0 auto 10px; border-radius: 4px; }}
  .product .name {{ font-size: 13px; margin-bottom: 6px; }}
  .product .price {{ font-weight: bold; color: #e53935; }}
  footer {{ text-align: center; padding: 30px; color: #aaa; font-size: 12px; }}
</style>
</head>
<body>

<header>
  <div class="logo">cyfr<span>o</span>we.pl</div>
  <div class="search-box">
    <input type="search" id="search" placeholder="Szukaj aparatów, obiektywów, akcesoriów...">
  </div>
</header>

<nav>
  <a href="#">Aparaty</a>
  <a href="#">Obiektywy</a>
  <a href="#">Lampy</a>
  <a href="#">Statywy</a>
  <a href="#">Drony</a>
  <a href="#">Akcesoria</a>
</nav>

<main>
  <div class="banner">
    <h1>🔌 CyfroSearch Widget Test</h1>
    <p>Ta strona symuluje dowolny zewnętrzny sklep. Widget jest załadowany jednym tagiem <code>&lt;script&gt;</code>.</p>
    <div class="code-box">&lt;script async src="{api_base}/static/cyfrosearch-widget.js" data-api="{api_base}" data-input="#search"&gt;&lt;/script&gt;</div>
    <p style="font-size:12px;color:#999;">Wpisz cokolwiek w pole wyszukiwania powyżej — np. "canon", "sony a7", "obiektyw 50mm"</p>
  </div>

  <h3 style="margin-bottom:12px;">Przykładowe produkty</h3>
  <div class="products">
    <div class="product"><div class="placeholder"></div><div class="name">Canon EOS R6 Mark III</div><div class="price">12 868 zł</div></div>
    <div class="product"><div class="placeholder"></div><div class="name">Sony A7R V</div><div class="price">16 499 zł</div></div>
    <div class="product"><div class="placeholder"></div><div class="name">Nikon Z8</div><div class="price">17 999 zł</div></div>
    <div class="product"><div class="placeholder"></div><div class="name">Fujifilm X100VI</div><div class="price">8 299 zł</div></div>
  </div>
</main>

<footer>
  cyfrowe.pl — strona testowa demonstrująca embedowalny widget CyfroSearch.
</footer>

<!-- ✨ This is the only line needed to add CyfroSearch to any website ✨ -->
<script async src="{api_base}/static/cyfrosearch-widget.js" data-api="{api_base}" data-input="#search"></script>

</body>
</html>""")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("demo_server:app", host="0.0.0.0", port=8000, reload=True)
