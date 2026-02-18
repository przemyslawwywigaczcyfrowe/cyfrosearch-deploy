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


# ── Focal-length pattern ──
# Detects zoom ranges like "24-70", "70-200", "100-400" in queries
_RE_FOCAL_LENGTH = re.compile(r'\b(\d{2,3})\s*[-–]\s*(\d{2,3})\b')

# ── Model number normalization ──
# Splits tokens on digit→letter boundaries so "a7iv" → "a7 iv", "r6iii" → "r6 iii"
# This is critical for camera model searches like "sony a7iv", "canon r6ii", "fuji xt5ii"
_RE_DIGIT_TO_ALPHA = re.compile(r'(\d)([a-zA-Z])')

# Model rewrites: users often concatenate model names that have spaces in the product name.
# These are applied as whole-word replacements (case-insensitive).
# Key pattern: Nikon Z series uses "Z fc", "Z f", "Z 30", "Z 50" with a space after Z.
MODEL_REWRITES: dict[str, str] = {
    "zfc": "z fc",
    "z fc": "z fc",     # already correct, no-op
}

def _normalize_model_query(q: str) -> str:
    """Insert space at digit→letter boundaries within words.
    'sony a7iv' → 'sony a7 iv', 'canon r6ii' → 'canon r6 ii'
    Also applies MODEL_REWRITES for known concatenated camera models.
    Preserves original spacing and case.
    """
    # Apply model rewrites first (case-insensitive, whole-word)
    q_lower_words = q.lower().split()
    for i, w in enumerate(q_lower_words):
        if w in MODEL_REWRITES:
            rewrite = MODEL_REWRITES[w]
            # Preserve original case if single char difference
            q_words = q.split()
            q_words[i] = rewrite
            q = " ".join(q_words)
            break

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

LENS_SUBCATS = [
    "obiektywy stałoogniskowe", "obiektywy zmiennoogniskowe (zoom)",
    "obiektywy do lustrzanek", "obiektywy do bezlusterkowców",
]

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
    "think tank": "thinktank",
}

# ── Category-intent aliases ──
# Maps common search terms (singular forms, abbreviations) to lists of ES subcategory values.
# Checked BEFORE the standard exact/folded/prefix category-intent matching.
CATEGORY_ALIASES: dict[str, list[str]] = {
    # lampa (singular) → lamp subcategories
    "lampa": ["lampy LED", "lampy błyskowe", "lampy studyjne",
              "lampy plenerowe (akumulatorowe)", "lampy studyjne LED",
              "lampy panelowe LED", "lampy pierścieniowe LED"],
    "lampy": ["lampy LED", "lampy błyskowe", "lampy studyjne",
              "lampy plenerowe (akumulatorowe)", "lampy studyjne LED",
              "lampy panelowe LED", "lampy pierścieniowe LED"],
    # torba (singular) → bag subcategories
    "torba": ["torby fotograficzne", "torby kufry i walizki"],
    # karta → memory cards (NOT gift cards!)
    "karta": ["SD / SDHC / SDXC", "CFexpress", "microSD",
              "SD / SDHC", "CompactFlash"],
    "karty": ["SD / SDHC / SDXC", "CFexpress", "microSD",
              "SD / SDHC", "CompactFlash"],
    # obiektyw → ALL lens subcategories (not just "obiektywy" with 21 products)
    "obiektyw": ["obiektywy stałoogniskowe",
                 "obiektywy zmiennoogniskowe (zoom)",
                 "obiektywy do lustrzanek",
                 "obiektywy do bezlusterkowców"],
    "obiektywy": ["obiektywy stałoogniskowe",
                  "obiektywy zmiennoogniskowe (zoom)",
                  "obiektywy do lustrzanek",
                  "obiektywy do bezlusterkowców"],
    # tło/tlo → backdrop subcategories
    "tlo": ["tła kartonowe", "tła składane", "tła plastikowe",
            "tła materiałowe", "tła winylowe", "tła podświetlane",
            "systemy zawieszania teł"],
    "tło": ["tła kartonowe", "tła składane", "tła plastikowe",
            "tła materiałowe", "tła winylowe", "tła podświetlane",
            "systemy zawieszania teł"],
    "tła": ["tła kartonowe", "tła składane", "tła plastikowe",
            "tła materiałowe", "tła winylowe", "tła podświetlane",
            "systemy zawieszania teł"],
    "tla": ["tła kartonowe", "tła składane", "tła plastikowe",
            "tła materiałowe", "tła winylowe", "tła podświetlane",
            "systemy zawieszania teł"],
    # Additional useful aliases
    "klatka": ["klatki"],
    "plecak": ["plecaki fotograficzne"],
    "plecaki": ["plecaki fotograficzne"],
    "plecak fotograficzny": ["plecaki fotograficzne"],
    "statyw": ["statywy (trójnogi)", "statywy do filmowania"],
    "filtr": ["filtry", "połówkowe i szare"],
    "akumulator": ["akumulatory i baterie"],
    # pasek / paski → strap subcategory
    "pasek": ["paski", "pasy biodrowe, szelki i kamizelki"],
    "paski": ["paski", "pasy biodrowe, szelki i kamizelki"],
    # karta sd / karty sd → memory card subcategories
    "karta sd": ["SD / SDHC / SDXC", "SD / SDHC"],
    "karty sd": ["SD / SDHC / SDXC", "SD / SDHC"],
    "karta pamięci": ["SD / SDHC / SDXC", "CFexpress", "microSD",
                       "SD / SDHC", "CompactFlash"],
    "karta cfexpress": ["CFexpress", "CFexpress Typ A", "CFexpress Type B"],
    "karta microsd": ["microSD"],
    # softbox → all softbox subcategories
    "softbox": ["softboxy", "softboxy oktagonalne", "softboxy prostokątne",
                "softboxy heksagonalne", "softboxy paraboliczne", "softboxy wideo",
                "stripboxy"],
    "softboxy": ["softboxy", "softboxy oktagonalne", "softboxy prostokątne",
                 "softboxy heksagonalne", "softboxy paraboliczne", "softboxy wideo",
                 "stripboxy"],
    # gimbal → gimbal subcategories
    "gimbal": ["gimbale", "stabilizatory", "systemy stabilizacji"],
    "gimbale": ["gimbale", "stabilizatory", "systemy stabilizacji"],
    # statyw oświetleniowy
    "statyw oswietleniowy": ["statywy studyjne", "statywy wolnostojące",
                              "statywy podłogowe (piesek)"],
    "statyw oświetleniowy": ["statywy studyjne", "statywy wolnostojące",
                              "statywy podłogowe (piesek)"],
    # mikrofon → microphone subcategories
    "mikrofon": ["mikrofony", "mikrofony bezprzewodowe", "systemy bezprzewodowe"],
    "mikrofony": ["mikrofony", "mikrofony bezprzewodowe", "systemy bezprzewodowe"],
    "mic": ["mikrofony", "mikrofony bezprzewodowe", "systemy bezprzewodowe"],
    # lampa led → LED light subcategories
    "lampa led": ["lampy LED", "lampy studyjne LED", "lampy panelowe LED",
                  "miecze świetlne LED", "lampy pierścieniowe LED"],
    "lampy led": ["lampy LED", "lampy studyjne LED", "lampy panelowe LED",
                  "miecze świetlne LED", "lampy pierścieniowe LED"],
    # instax → instant cameras (NOT albums!)
    "instax": ["kompakty z natychmiastowym wydrukiem", "mobilne do fotografii natychmiastowej",
               "Instax / Polaroid"],
    # klatka → cage subcategories (broader)
    "klatki": ["klatki", "zestawy do foto-video"],
    # blenda → reflector subcategories
    "blenda": ["blendy", "mocowania do blend i paneli"],
    "blendy": ["blendy", "mocowania do blend i paneli"],
    # monopod
    "monopod": ["statywy monopody"],
    "monopody": ["statywy monopody"],
    # boom → boom stands
    "boom": ["statywy typu boom"],
    # beauty dish
    "beauty dish": ["beauty dish"],
    # strumienica → snoot subcategories
    "strumienica": ["strumienice"],
    "strumienice": ["strumienice"],
    # monitor podglądowy
    "monitor podgladowy": ["Monitory podglądowe"],
    "monitor podglądowy": ["Monitory podglądowe"],
    "monitory podgladowe": ["Monitory podglądowe"],
    "monitory podglądowe": ["Monitory podglądowe"],
    # karta cf express
    "karta cf express": ["CFexpress", "CFexpress Typ A", "CFexpress Type B"],
    "karta cfexpress": ["CFexpress", "CFexpress Typ A", "CFexpress Type B"],
    # hdmi
    "hdmi": ["HDMI"],
    # torba fotograficzna
    "torba fotograficzna": ["torby fotograficzne", "torby kufry i walizki"],
    "torby fotograficzne": ["torby fotograficzne", "torby kufry i walizki"],
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
        # Validate CATEGORY_ALIASES targets against actual ES subcategories
        _all_es_subcats = {k for k in _subcategory_lower_to_original.values()}
        for alias_key, alias_targets in CATEGORY_ALIASES.items():
            for target in alias_targets:
                if target not in _all_es_subcats:
                    print(f"Warning: CATEGORY_ALIASES['{alias_key}'] target '{target}' not found in ES subcategories")
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
    # If the query matches a subcategory name (or a CATEGORY_ALIAS), treat it as a
    # category search — filter to those subcategories so accessories and bundles
    # don't pollute results. Supports MULTIPLE subcategories via CATEGORY_ALIASES.
    # When brand-intent is active, check the REMAINDER of the query (after removing
    # brand name) for category aliases (e.g. "peak design paski" → brand=Peak Design + cat=paski).
    matched_subcategories: list[str] | None = None
    _cat_remainder_text: str = ""  # remaining query text after stripping category prefix

    # Determine what text to check for category aliases
    _cat_check_text = q_lower
    if _brand_intent:
        # Remove brand name from query to check remainder for category intent
        brand_lower = _brand_intent.lower()
        remainder = q_lower.replace(brand_lower, "").strip()
        # Also try removing alias form
        for alias, canonical in BRAND_ALIASES.items():
            if canonical == brand_lower and alias in q_lower:
                remainder2 = q_lower.replace(alias, "").strip()
                if len(remainder2) > len(remainder):
                    pass  # keep shorter remainder
                else:
                    remainder = remainder2
        _cat_check_text = remainder if remainder else ""

    if _cat_check_text:
        q_folded = _fold_polish(_cat_check_text)
        # 0) Alias match — CATEGORY_ALIASES handles singular forms, short words
        if _cat_check_text in CATEGORY_ALIASES:
            matched_subcategories = CATEGORY_ALIASES[_cat_check_text]
        elif q_folded in CATEGORY_ALIASES:
            matched_subcategories = CATEGORY_ALIASES[q_folded]
        # 0b) First-word alias match — "klatka sony a7 iv" → check "klatka"
        #     Also try first two words: "lampa led godox" → check "lampa led"
        #     Saves the REMAINDER text for use in text-matching within the category.
        if not matched_subcategories:
            cat_tokens = _cat_check_text.split()
            for nw in (2, 1):
                if nw > len(cat_tokens) or nw >= len(cat_tokens):
                    continue  # only if there are MORE words after the prefix
                prefix_cat = " ".join(cat_tokens[:nw])
                prefix_folded = _fold_polish(prefix_cat)
                if prefix_cat in CATEGORY_ALIASES:
                    matched_subcategories = CATEGORY_ALIASES[prefix_cat]
                    _cat_remainder_text = " ".join(cat_tokens[nw:]).strip()
                    break
                elif prefix_folded in CATEGORY_ALIASES:
                    matched_subcategories = CATEGORY_ALIASES[prefix_folded]
                    _cat_remainder_text = " ".join(cat_tokens[nw:]).strip()
                    break
    if not matched_subcategories and not _brand_intent and _cat_check_text:
        q_folded = _fold_polish(_cat_check_text)
        if _subcategory_set:
            # 1) Exact match (with original Polish characters)
            if q_lower in _subcategory_set:
                es_key = _subcategory_lower_to_original.get(q_lower, q_lower)
                matched_subcategories = [es_key]
            else:
                # 2) Folded match (user types without Polish diacritics)
                if q_folded in _subcategory_folded:
                    original = _subcategory_folded[q_folded]
                    es_key = _subcategory_lower_to_original.get(original, original)
                    matched_subcategories = [es_key]
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
                        es_key = _subcategory_lower_to_original.get(best_match, best_match)
                        matched_subcategories = [es_key]

    # ── Focal-length intent detection ──
    # Queries like "24-70", "sigma 24-70", "70-200 f2.8" → filter to lens subcategories
    # and require the focal length to appear as a phrase (not tokenized "24" OR "70")
    _focal_intent: str | None = None
    if not matched_subcategories:
        focal_match = _RE_FOCAL_LENGTH.search(q_lower)
        if focal_match:
            _focal_intent = f"{focal_match.group(1)}-{focal_match.group(2)}"

    # ── Build all queries, execute as single msearch ──
    # Query 1: Products — hybrid scoring: relevance × (popularity + business signals)
    # Key insight: use boost_mode="sum" so popularity is ADDED to BM25 (not multiplied)
    # This prevents small accessories with repeated brand name from dominating flagships

    # Build the core bool query — if category intent detected, add a strong category filter
    if matched_subcategories:
        # Category-intent query: filter to subcategories.
        # Supports multiple subcategories via CATEGORY_ALIASES (e.g. "lampa" → lampy LED + błyskowe + ...)
        subcat_filter = (
            {"term": {"subcategory": matched_subcategories[0]}}
            if len(matched_subcategories) == 1
            else {"terms": {"subcategory": matched_subcategories}}
        )
        # When both brand-intent and category-intent are active (e.g. "peak design paski"),
        # combine both filters: brand + subcategory
        _combined_filters = [subcat_filter]
        if _brand_intent:
            _combined_filters.append({"term": {"brand": _brand_intent}})
        _all_cat_filters = {"bool": {"must": _combined_filters}} if len(_combined_filters) > 1 else subcat_filter

        if _cat_remainder_text:
            # Category + additional text (e.g. "klatka sony a7 iv" → cat=klatki, text="sony a7 iv")
            # Use text matching within the category filter so results are relevant to remainder
            product_bool_query = {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": _cat_remainder_text,
                                "fields": ["name^3", "name.prefix^2", "name.morfologik^2",
                                           "name.folded^2", "brand^3"],
                                "fuzziness": "AUTO",
                                "prefix_length": 2,
                                "minimum_should_match": "70%",
                            }
                        }
                    ],
                    "filter": [_all_cat_filters],
                }
            }
        else:
            # Pure category browse (e.g. "lampa", "karta sd") — constant_score
            # so BM25 text relevance does NOT affect ranking
            product_bool_query = {
                "constant_score": {
                    "filter": _all_cat_filters,
                    "boost": 1,
                }
            }
    else:
        # Standard text-matching query
        q_trimmed = q.strip()
        q_upper = q_trimmed.upper()
        # When brand-intent detected, reduce phrase match boosts so that
        # function_score signals (brand +300, main_cat +1000, price) can outweigh
        # lucky text matches (e.g. "Canon R-F-3 zaślepka" matching "canon r")
        # BUT keep boost high enough so "sigma 15 1.4" beats "sigma 85 1.4" via phrase proximity
        #
        # Model-number intent: when brand-intent is active and the remainder
        # looks like a specific model code (short, contains digits), the user is
        # searching for an EXACT product model. In that case, phrase matching
        # should strongly dominate over popularity-based ranking.
        # Examples: "smallrig 220B", "canon r8", "sony a7iv", "godox v1"
        _model_number_intent = False
        if _brand_intent:
            _brand_lower = _brand_intent.lower()
            _remainder = q_lower.replace(_brand_lower, "").strip()
            # Also check alias form
            for _al, _cn in BRAND_ALIASES.items():
                if _cn == _brand_lower and _al in q_lower:
                    _r2 = q_lower.replace(_al, "").strip()
                    if len(_r2) < len(_remainder):
                        _remainder = _r2
            # Model-number intent: remainder must be a specific model code.
            # Requires at least 2 "specificity points" to avoid false positives
            # on short brand+model queries like "canon r8" (1 point) where popularity
            # should still influence ranking.
            # Scoring: each digit = 1 point, roman numeral token = 1 point.
            # "smallrig 220B" (3 digits) → 3 pts → model intent.
            # "sony a7 iv" (1 digit + roman) → 2 pts → model intent.
            # "nikon z5 ii" (1 digit + roman) → 2 pts → model intent.
            # "canon r8" (1 digit, no roman) → 1 pt → NOT model intent.
            _ROMAN_SET = {"ii", "iii", "iv", "v"}
            _rem_tokens = _remainder.split()
            _total_digits = sum(c.isdigit() for c in _remainder)
            _roman_bonus = sum(1 for t in _rem_tokens if t in _ROMAN_SET)
            _specificity = _total_digits + _roman_bonus
            if (1 <= len(_rem_tokens) <= 3
                and _specificity >= 2
                and any(any(c.isdigit() for c in t) for t in _rem_tokens)):
                _model_number_intent = True
            # Also trigger for very specific long queries (e.g. "35 mm f/1.4 sony fe")
            # These are full product name searches where phrase match should dominate.
            # Heuristic: >3 tokens with ≥2 digits = user knows exactly what they want.
            elif (len(_rem_tokens) > 3
                  and _total_digits >= 2
                  and any(any(c.isdigit() for c in t) for t in _rem_tokens)):
                _model_number_intent = True

        if _model_number_intent:
            # Specific model search: phrase must dominate
            _phrase_boost = 80
            _phrase_prefix_boost = 15
        elif _brand_intent:
            _phrase_boost = 30
            _phrase_prefix_boost = 5
        else:
            _phrase_boost = 50
            _phrase_prefix_boost = 5

        # Focal-length intent: filter to lens subcategories and require phrase match
        _focal_filter = (
            [{"terms": {"subcategory": LENS_SUBCATS}}]
            if _focal_intent else []
        )

        # Brand-intent filter: restrict to products of the detected brand.
        # This prevents 3rd-party products with brand name in title
        # (e.g. "7Artisans 50mm Canon R") from outscoring actual brand products
        # due to high BM25 text match on the brand name in the product name.
        _brand_filter = (
            [{"term": {"brand": _brand_intent}}]
            if _brand_intent else []
        )

        # ── Model code variant generation ──
        # Many photo/video products use letter+number codes: B300, G200, X100, R5, A7
        # Users often drop the letter prefix: "molus 300" instead of "molus b300"
        # Generate match_phrase variants with common letter prefixes prepended to numbers.
        #
        # Also handles alphanumeric codes where user INCLUDES the letter:
        # "220B" → also try without the letter ("220") with all prefixes → "a220", "b220" etc.
        # This helps when ES tokenizes "RC 220B" differently from user's "220B".
        _ROMAN_NUMS = {"ii", "iii", "iv", "v"}
        _model_variant_phrases = []
        _q_tokens = q_for_es.lower().split()
        for _i, _tok in enumerate(_q_tokens):
            # Case 1: Pure digits (e.g. "300") → generate "a300", "b300", ...
            if _tok.isdigit() and len(_tok) >= 2:
                for _pre in "abcdefghijklmnoprstuvwxyz":
                    _vtokens = _q_tokens[:_i] + [_pre + _tok] + _q_tokens[_i+1:]
                    _model_variant_phrases.append(" ".join(_vtokens))
            # Case 2: Digits + trailing letter(s) (e.g. "220b", "100d", "r5c")
            # Strip trailing letters and generate variants with different prefixes
            elif len(_tok) >= 2 and not _tok.isalpha():
                _m = re.match(r'^(\d{2,})([a-z]{1,2})$', _tok)
                if _m:
                    # "220b" → digits="220", suffix="b"
                    _digits = _m.group(1)
                    _suffix = _m.group(2)
                    # Generate variants swapping the suffix letter
                    for _pre in "abcdefghijklmnoprstuvwxyz":
                        if _pre != _suffix:
                            _vtokens = _q_tokens[:_i] + [_digits + _pre] + _q_tokens[_i+1:]
                            _model_variant_phrases.append(" ".join(_vtokens))
                # Also check leading letter + digits: "r5" → "a5", "b5", ...
                _m2 = re.match(r'^([a-z])(\d{1,})$', _tok)
                if _m2 and len(_tok) >= 2:
                    _letter = _m2.group(1)
                    _digits2 = _m2.group(2)
                    for _pre in "abcdefghijklmnoprstuvwxyz":
                        if _pre != _letter:
                            _vtokens = _q_tokens[:_i] + [_pre + _digits2] + _q_tokens[_i+1:]
                            _model_variant_phrases.append(" ".join(_vtokens))

            # Case 3: Model + Roman numeral concatenation
            # Products like "Nikon Z5II" are indexed as single token "z5ii" but users
            # type "z5 II" (two tokens). Generate concatenated variant: "z5" + "ii" → "z5ii"
            # Pattern: alphanumeric token (e.g. "z5", "z6", "z50") followed by roman numeral
            if (_i + 1 < len(_q_tokens)
                and _q_tokens[_i + 1] in _ROMAN_NUMS
                and re.match(r'^[a-z]\d+$', _tok)):
                _concat = _tok + _q_tokens[_i + 1]  # "z5" + "ii" → "z5ii"
                _vtokens = _q_tokens[:_i] + [_concat] + _q_tokens[_i+2:]
                _model_variant_phrases.append(" ".join(_vtokens))

            # Case 4: Standalone Roman numeral → prepend "mark" to match ES tokenization
            # ES mark_normalizer char filter concatenates "mark III" → "markiii" in the index.
            # Users type "canon r6III" → normalized to "canon r6 iii" → tokens ["canon","r6","iii"]
            # but the index has "markiii" as a single token. Generate variant with "mark" prepended:
            # "canon r6 iii" → "canon r6 markiii"
            if (_tok in _ROMAN_NUMS
                and _i > 0):
                _mark_concat = "mark" + _tok  # "iii" → "markiii"
                _vtokens = _q_tokens[:_i] + [_mark_concat] + _q_tokens[_i+1:]
                _model_variant_phrases.append(" ".join(_vtokens))

        # When model-number intent detected, add a focused match on the remainder
        # (model code only, without brand). This avoids IDF dilution from the brand
        # token that appears in ALL products when brand filter is active.
        _model_remainder_clauses = []
        _model_remainder2 = None
        if _model_number_intent and _brand_intent:
            _brand_lower2 = _brand_intent.lower()
            _model_remainder2 = q_for_es.lower().replace(_brand_lower2, "").strip()
            if _model_remainder2:
                # _normalize_model_query inserts spaces at digit→letter boundaries:
                # "220B" → "220 B". But ES indexes "220B" as single token "220b"
                # (split_on_numerics=false). We need BOTH forms for matching:
                # - "220 b" for search-time analyzer (standard tokenizer splits it)
                # - "220b" (concatenated) for direct token match in index
                _rem_concat = re.sub(r'\s+', '', _model_remainder2)  # "220 b" → "220b"
                _model_remainder_clauses = [
                    # Concatenated form — matches ES index token directly
                    {"match_phrase": {"name": {"query": _rem_concat, "boost": 300, "slop": 0}}},
                    # Spaced form — matches via search analyzer tokenization
                    {"match_phrase": {"name": {"query": _model_remainder2, "boost": 200, "slop": 2}}},
                    # Sub-field with different analyzer
                    {"match_phrase": {"name.folded": {"query": _rem_concat, "boost": 200, "slop": 0}}},
                ]
                # If remainder ends with a roman numeral, also try "mark" + roman variant.
                # E.g. remainder "r6 iii" → also try "r6 markiii" (ES indexes "mark III" as "markiii")
                _rem_tokens2 = _model_remainder2.split()
                if _rem_tokens2 and _rem_tokens2[-1] in {"ii", "iii", "iv", "v"}:
                    _mark_rem = " ".join(_rem_tokens2[:-1]) + " mark" + _rem_tokens2[-1]
                    _mark_rem_concat = re.sub(r'\s+', '', _mark_rem)
                    _model_remainder_clauses.extend([
                        {"match_phrase": {"name": {"query": _mark_rem, "boost": 250, "slop": 2}}},
                        {"match_phrase": {"name": {"query": _mark_rem_concat, "boost": 250, "slop": 0}}},
                    ])

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
                            "name": {"query": q_for_es, "boost": _phrase_boost, "slop": 3}
                        }
                    },
                    # Model-number remainder match — high boost, no IDF dilution from brand
                    *_model_remainder_clauses,
                    {
                        "match_phrase_prefix": {
                            "name": {"query": q_for_es, "boost": _phrase_prefix_boost}
                        }
                    },
                    # Model code variants: "molus 300" → try "molus b300", "molus g300" etc.
                    *[
                        {"match_phrase": {"name": {"query": v, "boost": _phrase_boost * 2, "slop": 2}}}
                        for v in _model_variant_phrases[:26]
                    ],
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
                # Filters: brand-intent + focal-length intent (both optional)
                "filter": _brand_filter + _focal_filter,
            }
        }

    # ── Build function_score functions based on query type ──
    if matched_subcategories:
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
            # na_zamowienie — available for order, less than in_stock but above out_of_stock
            {"filter": {"term": {"availability": "na_zamowienie"}}, "weight": 15},
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
        # and strongly boost main categories (+1000) so cameras/lenses beat
        # accessories, scopes, and other non-core products of the same brand.
        #
        # Model-number intent: when user searches for a specific model (brand + digits),
        # REDUCE popularity/price influence so that text relevance (match_phrase)
        # can dominate. This ensures "smallrig 220B" → RC 220B, not random popular items.
        if _model_number_intent:
            _main_cat_weight = 100  # low — model specificity matters more
            _price_weight = 5       # minimal — price doesn't determine model match
            _pop_weight = 15        # reduced — popularity shouldn't override exact model match
            _sales_weight = 5       # reduced
            _brand_weight = 200     # still useful but lower than standard brand-intent
        elif _brand_intent:
            _main_cat_weight = 1000
            _price_weight = 30
            _pop_weight = 80
            _sales_weight = 30
            _brand_weight = 300
        else:
            _main_cat_weight = 120
            _price_weight = 15
            _pop_weight = 80
            _sales_weight = 30
            _brand_weight = 0  # no brand detected

        scoring_functions = [
            # Brand-intent boost — if user searches a brand, prefer that brand's products
            *(
                [{"filter": {"term": {"brand": _brand_intent}}, "weight": _brand_weight}]
                if _brand_intent else []
            ),
            # Popularity — strongest signal for generic queries, reduced for model-number queries
            {
                "field_value_factor": {
                    "field": "ga4.popularity_score",
                    "factor": 1.5, "modifier": "log1p", "missing": 0,
                },
                "weight": _pop_weight,
            },
            # Sales volume boost
            {
                "field_value_factor": {
                    "field": "sales_30d",
                    "factor": 1.0, "modifier": "log1p", "missing": 0,
                },
                "weight": _sales_weight,
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
            # na_zamowienie — available for order, less than in_stock but above out_of_stock
            {"filter": {"term": {"availability": "na_zamowienie"}}, "weight": 30},
            # Bestseller — strong signal (reduced for model-number intent)
            {"filter": {"term": {"is_bestseller": True}}, "weight": 20 if _model_number_intent else 60},
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
            # Focal-length intent: boost lens subcategories when query contains focal range
            *(
                [{"filter": {"terms": {"subcategory": LENS_SUBCATS}}, "weight": 150}]
                if _focal_intent else []
            ),
        ]

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
    # Use the SAME query as product search (with brand/category filters) so that
    # category suggestions reflect the actual matched products, not the entire index.
    # Previously used a simple multi_match on the whole index which caused
    # "obiektywy stałoogniskowe" to dominate for every query (largest category).
    agg_body = {
        "size": 0,
        "query": product_bool_query,
        "aggs": {
            "subcategories": {"terms": {"field": "subcategory", "size": 6}},
            "categories": {"terms": {"field": "category_path", "size": 5}},
            "brands": {"terms": {"field": "brand", "size": 4}},
        },
    }

    # Query 3: Suggestion source (top 20 popular products for name extraction)
    # Use the same product_bool_query (with brand/category filters) so that
    # "popular product" suggestions are filtered to the correct brand/category.
    # Previously used a simple multi_match without brand filter, causing e.g.
    # "Canon r6III" to suggest R6 II (more popular) as the popular product.
    suggest_source_body = {
        "size": 20,
        "query": {
            "function_score": {
                "query": product_bool_query,
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
    # Use a fixed "preference" so ES always routes to the SAME shard copy.
    # Without this, ES round-robins between primary and replica, which can
    # return different BM25 scores (term statistics diverge after bulk writes).
    _msearch_header = {"index": ES_INDEX, "preference": "cyfrosearch"}
    try:
        msearch_body = []
        for body in (product_body, agg_body, suggest_source_body):
            msearch_body.append(_msearch_header)
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

    resp = await es.search(index=ES_INDEX, body=body, preference="cyfrosearch")
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
        _hdr = {"index": ES_INDEX, "preference": "cyfrosearch"}
        msearch_body = [
            _hdr,
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
            _hdr,
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
