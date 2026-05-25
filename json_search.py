"""
Fallback search engine using product feed JSON (no Elasticsearch required).

Used when ELASTIC_URL is not configured. Downloads the product feed and
provides search/suggest using in-memory text matching.
"""

import json
import re
import os
import math
from urllib.request import urlopen

from config import FEED_URL

_products: list[dict] | None = None
_brand_cache: dict[str, str] | None = None
_brand_alias_map: dict[str, str] | None = None


def _load_products():
    global _products, _brand_cache
    if _products is not None:
        return

    cache_path = os.path.join(os.path.dirname(__file__), "feed_cache.json")

    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _products = data["products"]
    else:
        with urlopen(FEED_URL, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        _products = data["products"]
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    _brand_cache = {}
    for p in _products:
        b = p.get("brand", "")
        if b and len(b) >= 2:
            _brand_cache[b.lower()] = b


def _load_aliases():
    global _brand_alias_map
    if _brand_alias_map is not None:
        return _brand_alias_map
    _brand_alias_map = {}
    path = os.path.join(os.path.dirname(__file__), "brand_aliases.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data:
            canonical = entry["brand"]
            for alias in entry["aliases"]:
                _brand_alias_map[alias.strip().lower()] = canonical
    return _brand_alias_map


def _normalize(text):
    return re.sub(r'\s+', ' ', (text or "").lower().strip())


def _is_outlet_or_used(p):
    name = (p.get("name") or "").lower()
    condition = (p.get("condition") or "").lower()
    return condition == "used" or "outlet" in name or "używany" in name or "używana" in name


_COMPAT_PREPOSITIONS = {"do", "dla", "na", "pod"}
_USED_KEYWORDS = {"używany", "używane", "używanych", "używanego", "używana", "używanym"}

_PRODUCT_TYPE_MAP = {
    'obiektyw': ('obiektywy', 'lens'), 'obiektywy': ('obiektywy', 'lens'), 'obiektywu': ('obiektywy', 'lens'),
    'aparat': ('aparaty', 'camera'), 'aparaty': ('aparaty', 'camera'), 'aparatu': ('aparaty', 'camera'),
    'lampa': ('lamp', 'flash'), 'lampe': ('lamp', 'flash'), 'błyskowa': ('lamp', 'flash'), 'blyskowa': ('lamp', 'flash'),
    'statyw': ('statyw', 'tripod'), 'statywy': ('statyw', 'tripod'),
    'torba': ('torb', 'bag'), 'plecak': ('plecak', 'bag'),
    'filtr': ('filtr', 'filter'), 'filtry': ('filtr', 'filter'),
    'karta': ('kart', 'card'), 'karty': ('kart', 'card'),
    'akumulator': ('akumulator', 'battery'), 'bateria': ('bateria', 'battery'),
    'ładowarka': ('ładowark', 'charger'), 'ladowarka': ('ładowark', 'charger'),
    'adapter': ('adapter', 'adapter'), 'pierścień': ('adapter', 'adapter'), 'pierscien': ('adapter', 'adapter'),
    'mikrofon': ('mikrofon', 'microphone'), 'mikrofony': ('mikrofon', 'microphone'),
}

_MOUNT_SIGNATURES = {
    'canon-efm': [re.compile(r'eos[\s-]?m\b'), re.compile(r'ef[\s-]?m\b')],
    'canon-rf-ef': [re.compile(r'canon\s?rf\b'), re.compile(r'\brf\b'), re.compile(r'canon\s?ef\b'), re.compile(r'\bef\b(?![\s-]?m)')],
    'sony-e': [re.compile(r'sony\s?e\b'), re.compile(r'/\s*sony'), re.compile(r'\bfe\b'), re.compile(r'\bnex\b')],
    'nikon-z': [re.compile(r'nikon\s?z\b'), re.compile(r'/\s*nikon\s?z')],
    'nikon-f': [re.compile(r'nikon\s?f\b'), re.compile(r'/\s*nikon\b(?!\s?z)')],
    'fuji-x': [re.compile(r'fuji(film)?\s?x\b'), re.compile(r'/\s*fuji')],
    'mft': [re.compile(r'micro\s?4/3'), re.compile(r'\bmft\b'), re.compile(r'm4/3'), re.compile(r'/\s*olympus'), re.compile(r'/\s*panasonic'), re.compile(r'\bm\.zuiko\b')],
}


def _detect_camera_family(q):
    if re.search(r'eos[\s-]?m\d{0,3}\b', q) or re.search(r'\bm50\b', q) or re.search(r'\bm6\b', q) or re.search(r'\bm5\b', q) or re.search(r'\bm200\b', q) or re.search(r'\bm100\b', q):
        return 'canon-efm'
    if re.search(r'eos[\s-]?r\d{0,2}\b', q) or re.search(r'\br5\b', q) or re.search(r'\br6\b', q) or re.search(r'\br7\b', q) or re.search(r'\br8\b', q) or re.search(r'\br10\b', q) or re.search(r'\br50\b', q) or re.search(r'\brp\b', q):
        return 'canon-rf-ef'
    if re.search(r'\ba7\b', q) or re.search(r'\ba9\b', q) or re.search(r'\ba1\b', q) or re.search(r'\ba6\d{3}\b', q) or re.search(r'\bzv[\s-]?\w', q):
        return 'sony-e'
    if re.search(r'nikon[\s-]?z\d{0,2}\b', q) or re.search(r'\bz5\b', q) or re.search(r'\bz6\b', q) or re.search(r'\bz7\b', q) or re.search(r'\bz8\b', q) or re.search(r'\bz9\b', q) or re.search(r'\bz50\b', q) or re.search(r'\bzf\b', q) or re.search(r'\bzfc\b', q):
        return 'nikon-z'
    if re.search(r'nikon[\s-]?d\d', q) or re.search(r'\bd\d{3,4}\b', q):
        return 'nikon-f'
    if re.search(r'x-t\d', q) or re.search(r'x-h\d', q) or re.search(r'x-e\d', q) or re.search(r'x-s\d{1,2}', q) or re.search(r'x-pro', q) or re.search(r'x100', q):
        return 'fuji-x'
    if re.search(r'\bom-\d', q) or re.search(r'\bgh\d', q) or re.search(r'\bg9\b', q) or re.search(r'm4/3', q) or re.search(r'micro\s?4/3', q):
        return 'mft'
    return None


def _lens_mount_mismatch(name_lower, expected_family):
    for family, patterns in _MOUNT_SIGNATURES.items():
        if family == expected_family:
            continue
        for pat in patterns:
            if pat.search(name_lower):
                expected_pats = _MOUNT_SIGNATURES.get(expected_family, [])
                if any(ep.search(name_lower) for ep in expected_pats):
                    return False
                return True
    return False


def _preprocess_query(query):
    q = query.lower().strip()

    condition_filter = None
    for word in _USED_KEYWORDS:
        if word in q:
            condition_filter = "used"
            q = q.replace(word, "").strip()
            break

    q = re.sub(r'(\d)([a-zżźćńóąłęś]{2,})', r'\1 \2', q)
    is_compat = bool(re.search(r'\b(do|dla|na|pod)\b', q))

    _load_aliases()
    _load_products()
    words = q.split()
    detected_brands = []
    search_words = []
    product_type = None

    for w in words:
        if w in _COMPAT_PREPOSITIONS:
            continue
        if product_type is None and w in _PRODUCT_TYPE_MAP:
            product_type = _PRODUCT_TYPE_MAP[w]
            continue
        if _brand_cache and w in _brand_cache:
            detected_brands.append(_brand_cache[w])
        elif _brand_alias_map and w in _brand_alias_map:
            detected_brands.append(_brand_alias_map[w])
        else:
            search_words.append(w)

    brand_filter = None
    if len(detected_brands) == 1 and not is_compat:
        brand_filter = detected_brands[0]

    camera_family = _detect_camera_family(q)
    return " ".join(search_words), brand_filter, condition_filter, product_type, camera_family


def _kw_in_text(kw, text):
    if kw in text:
        return True
    if len(kw) >= 4:
        prefix = kw[:max(4, len(kw) - 1)]
        if prefix in text:
            return True
    return False


def _score_product(p, keywords, text_query, brand_filter, condition_filter, product_type=None, camera_family=None):
    name = _normalize(p.get("name", ""))
    cat = _normalize(p.get("category", ""))
    brand = _normalize(p.get("brand", ""))
    combined = f"{name} {cat} {brand}"
    query_norm = _normalize(text_query)

    score = 0.0

    if query_norm and query_norm in name:
        score += 250
    if keywords:
        name_matches = sum(1 for kw in keywords if _kw_in_text(kw, name))
        combined_matches = sum(1 for kw in keywords if _kw_in_text(kw, combined))
        if name_matches == len(keywords):
            score += 200
        elif combined_matches == len(keywords):
            score += 100
        score += name_matches * 30
        score += (combined_matches - name_matches) * 10
        if len(keywords) > 0:
            score += (combined_matches / len(keywords)) * 50
    else:
        score = 1.0

    try:
        price = float(p.get("price", 0) or 0)
        pop = math.log1p(price / 100) * 2
    except (ValueError, TypeError):
        pop = 0
    popularity_multiplier = 1.0 + pop * 0.05

    if product_type:
        cat_substr, type_key = product_type
        if cat_substr in cat:
            score += 200
        else:
            score *= 0.2
    else:
        type_key = None

    if type_key == 'lens' and camera_family:
        if _lens_mount_mismatch(name, camera_family):
            score *= 0.1

    if condition_filter == 'used' and 'outlet' in name:
        score *= 0.3

    if ' + ' in name and '+' not in text_query and not re.search(r'\d\s*mm\b', text_query):
        score *= 0.85

    if not condition_filter and _is_outlet_or_used(p):
        score *= 0.3

    if p.get("availability") == "in stock":
        score *= 1.1

    return score * popularity_multiplier


def _format_product(p):
    return {
        "id_verto": p.get("id_verto", ""),
        "name": p.get("name", ""),
        "brand": p.get("brand", ""),
        "category": p.get("category", ""),
        "price": p.get("price"),
        "sales_price": p.get("sales_price"),
        "image": p.get("image", ""),
        "link": p.get("link", ""),
        "availability": p.get("availability", ""),
        "condition": p.get("condition", ""),
        "ean": p.get("ean", ""),
    }


def warm_caches():
    _load_products()
    _load_aliases()
    print(f"[JSON fallback] Loaded {len(_products)} products from feed")


def search(query: str, page: int = 1, size: int = 24, filters: dict | None = None, sort_by: str | None = None) -> dict:
    _load_products()
    text_query, brand_filter, condition_filter, product_type, camera_family = _preprocess_query(query)

    if filters:
        if filters.get("brand"):
            brand_filter = filters["brand"]
        if filters.get("condition"):
            condition_filter = filters["condition"]

    candidates = list(_products)
    if brand_filter:
        candidates = [p for p in candidates if (p.get("brand") or "").lower() == brand_filter.lower()]
    if condition_filter:
        candidates = [p for p in candidates if p.get("condition") == condition_filter]

    keywords = [w for w in text_query.split() if len(w) > 1]

    scored = []
    for p in candidates:
        s = _score_product(p, keywords, text_query, brand_filter, condition_filter, product_type, camera_family)
        if s > 0:
            scored.append((s, p))

    scored.sort(key=lambda x: -x[0])

    if sort_by == "price_asc":
        scored.sort(key=lambda x: float(x[1].get("sales_price") or x[1].get("price") or 999999))
    elif sort_by == "price_desc":
        scored.sort(key=lambda x: -float(x[1].get("sales_price") or x[1].get("price") or 0))

    start = (page - 1) * size
    page_results = scored[start:start + size]

    brands = {}
    for _, p in scored[:200]:
        b = p.get("brand", "")
        if b:
            brands[b] = brands.get(b, 0) + 1

    return {
        "total": len(scored),
        "products": [_format_product(p) for _, p in page_results],
        "aggregations": {
            "brands": {"buckets": [{"key": k, "doc_count": v} for k, v in sorted(brands.items(), key=lambda x: -x[1])[:20]]}
        },
    }


def suggest(query: str, size: int = 7) -> dict:
    _load_products()
    text_query, brand_filter, condition_filter, product_type, camera_family = _preprocess_query(query)
    keywords = [w for w in text_query.split() if len(w) > 1]

    candidates = list(_products)
    if brand_filter:
        candidates = [p for p in candidates if (p.get("brand") or "").lower() == brand_filter.lower()]
    if condition_filter:
        candidates = [p for p in candidates if p.get("condition") == condition_filter]

    scored = []
    for p in candidates:
        s = _score_product(p, keywords, text_query, brand_filter, condition_filter, product_type, camera_family)
        if s > 0:
            scored.append((s, p))
    scored.sort(key=lambda x: -x[0])

    products = []
    for _, p in scored[:size]:
        products.append(_format_product(p))

    return {
        "products": products,
    }
