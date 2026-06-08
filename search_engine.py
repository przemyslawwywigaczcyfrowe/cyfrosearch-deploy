"""
Search engine — universal search query builder for Cyfrowe.pl.

DESIGN PRINCIPLES:
1. All ranking rules are universal — they apply to every product equally
2. No category-specific boosting or filtering in the core query
3. Popularity (GA data) acts as MULTIPLICATIVE signal on text relevance
4. Availability is NOT a ranking signal (out-of-stock products can be ordered on demand)
5. Every change to this file must be validated with regression tests

SCORING MODEL:
  final_score = text_relevance * popularity_factor

  - text_relevance: multi_match across name, brand, category (bool query with
    must/should clauses, fuzziness, phrase match, brand keyword match)
  - popularity_factor: 1 + popularity_score * 0.15
    where popularity_score = log1p(ga_views) + log1p(ga_sales) * 3

  The 0.1 coefficient is the PRIMARY TUNING KNOB for popularity influence.

WHY MULTIPLICATIVE (not additive):
  With additive scoring, GA boost was ~8 points on text scores of 150-500,
  making popularity invisible. Brand-only queries ("canon") returned cheap
  accessories (RC-6 pilot 95 PLN) above flagships (EOS R6 9000 PLN) because
  text scores were nearly identical and popularity barely nudged the total.

  Multiplicative scoring means: identical text relevance + 3x popularity
  multiplier = flagship wins decisively. But text relevance remains the
  gatekeeper: irrelevant products score 0 * any_multiplier = still 0.
"""

import os
import re

from config import INDEX_NAME
from es_client import get_es_client


# =============================================================================
# Query Preprocessing — semantic filter extraction
# =============================================================================
#
# UNIVERSAL rules applied to every query equally:
# 1. "używany/używane" → exclusive condition=used filter
# 2. Brand name → exclusive brand filter
#    EXCEPTION: "do/dla [brand]" = compatibility query, NO brand filter
#
# These rules ensure that:
# - "Canon używany" → only used Canon products
# - "obiektyw do Canon" → all lenses compatible with Canon (any brand)
# - "Canon" → only Canon brand products
# - "używane" → all used products regardless of brand

_brand_cache: dict[str, str] | None = None  # lowercase → original case
_brand_alias_map: dict[str, str] | None = None  # alias_lowercase → canonical_brand
_brand_groups: dict[str, list[str]] | None = None  # canonical → [all brand.keyword values]


def _load_brand_aliases() -> dict[str, str]:
    """Load brand alias map from brand_aliases.json (typo → canonical brand)."""
    global _brand_alias_map
    if _brand_alias_map is not None:
        return _brand_alias_map

    _brand_alias_map = {}
    aliases_path = os.path.join(os.path.dirname(__file__), "brand_aliases.json")
    if os.path.exists(aliases_path):
        import json
        with open(aliases_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data:
            canonical = entry["brand"]
            for alias in entry["aliases"]:
                _brand_alias_map[alias.strip().lower()] = canonical
    return _brand_alias_map


def _get_brand_cache() -> dict[str, str]:
    """Load and cache all unique brand names from Elasticsearch index."""
    global _brand_cache
    if _brand_cache is not None:
        return _brand_cache

    es = get_es_client()
    _brand_cache = {}

    resp = es.search(
        index=INDEX_NAME,
        body={
            "size": 0,
            "aggs": {
                "all_brands": {
                    "terms": {"field": "brand.keyword", "size": 2000}
                }
            },
        },
    )

    for bucket in resp["aggregations"]["all_brands"]["buckets"]:
        brand = bucket["key"]
        if brand and len(brand) >= 2:  # Skip single-char brands
            _brand_cache[brand.lower()] = brand

    # Also load brand aliases
    _load_brand_aliases()

    # Build brand groups: canonical → [all brand.keyword values in ES]
    # E.g., "Peak Design" → ["Peak Design", "PEAKDESIGN"]
    # This handles cases where the same brand appears with different spellings in the feed
    global _brand_groups
    _brand_groups = {}
    aliases = _load_brand_aliases()
    canonical_map: dict[str, str] = {}  # es_brand_keyword → canonical
    for es_brand_lower, es_brand_original in _brand_cache.items():
        if es_brand_lower in aliases:
            canonical = aliases[es_brand_lower]
        else:
            canonical = es_brand_original
        canonical_map[es_brand_original] = canonical

    for es_brand, canonical in canonical_map.items():
        _brand_groups.setdefault(canonical, [])
        if es_brand not in _brand_groups[canonical]:
            _brand_groups[canonical].append(es_brand)

    return _brand_cache


def _get_brand_keywords(canonical_brand: str) -> list[str]:
    """Return all brand.keyword values for a canonical brand name.
    E.g., _get_brand_keywords("Peak Design") → ["Peak Design", "PEAKDESIGN"]
    Falls back to [canonical_brand] if no group found."""
    if _brand_groups and canonical_brand in _brand_groups:
        return _brand_groups[canonical_brand]
    return [canonical_brand]


# =============================================================================
# Category-term detection ("category browse")
# =============================================================================
#
# PROBLEM: A bare category word like "obiektyw" (lens) does NOT appear in the
# NAMES of actual category members — lenses are named by brand + focal length
# + aperture ("Canon RF 50 mm f/1.4"), with "Obiektywy" only in the CATEGORY
# path. Accessories, however, DO contain the word in their name ("pokrywka na
# obiektyw" = lens cap). Because literal name matches outrank category matches,
# a search for "obiektyw" surfaced accessories instead of lenses.
#
# SOLUTION (universal, data-driven): build a cache of category-segment head
# words from the index taxonomy. When a query is a single token that matches a
# category head (Polish-inflection-aware), treat it as a CATEGORY BROWSE:
# filter to that category's products and rank by popularity — exactly like a
# brand-only query. No category names are hard-coded; everything comes from the
# index. Lens caps live under "Pokrywki ..." (head "pokrywki"), so they fall
# out of the "obiektyw" → "Obiektywy ..." browse automatically.

_category_cache: dict[str, list[str]] | None = None  # head word → [full category paths]


def _get_category_cache() -> dict[str, list[str]]:
    """Build {category-head-word -> [full category.keyword paths]} from the index.

    The head word is the first word of each non-root category segment
    (e.g. "Fotografia > Obiektywy do bezlusterkowcow" -> head "obiektywy").
    The root segment (level 0, e.g. "Fotografia"/"Filmowanie") is skipped so a
    top-level umbrella word can never browse the whole catalog.
    """
    global _category_cache
    if _category_cache is not None:
        return _category_cache

    es = get_es_client()
    resp = es.search(
        index=INDEX_NAME,
        body={
            "size": 0,
            "aggs": {"all_cats": {"terms": {"field": "category.keyword", "size": 2000}}},
        },
    )

    head_to_paths: dict[str, set] = {}
    for bucket in resp["aggregations"]["all_cats"]["buckets"]:
        path = bucket["key"]
        if not path:
            continue
        segments = [s.strip() for s in path.split(">")]
        for seg in segments[1:]:  # skip the root umbrella segment (level 0)
            words = re.findall(r"\w+", seg, flags=re.UNICODE)
            if not words:
                continue
            head = words[0].lower()
            if len(head) < 4:  # skip short/stop-like heads ("do", "na", "i")
                continue
            head_to_paths.setdefault(head, set()).add(path)

    _category_cache = {h: sorted(p) for h, p in head_to_paths.items()}
    return _category_cache


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _detect_category_paths(cleaned_query: str) -> list[str] | None:
    """If the (single-token) query is a category term, return its category paths.

    Matching is Polish-inflection tolerant: the query and the category head word
    may differ only in their trailing inflection (e.g. "obiektyw" <-> "obiektywy",
    "lampa" <-> "lampy", "aparat" <-> "aparaty"). Returns None when the query is
    multi-token, too short, or does not look like a category name.
    """
    q = cleaned_query.strip().lower()
    if not q or " " in q or len(q) < 4:
        return None
    cats = _get_category_cache()
    best_c = 0
    best_paths: list[str] = []
    for head, paths in cats.items():
        c = _common_prefix_len(q, head)
        # Require a long shared prefix: full overlap or differing only in the
        # trailing inflection char, and at least 4 chars in common.
        if c >= 4 and c >= min(len(q), len(head)) - 1:
            if c > best_c:
                best_c = c
                best_paths = list(paths)
            elif c == best_c:
                best_paths = sorted(set(best_paths) | set(paths))
    return best_paths or None


# Polish prepositions indicating "compatible with" (not "made by")
_COMPAT_PREPOSITIONS = {"do", "dla", "na", "pod"}

# Keywords indicating used/second-hand condition
_USED_KEYWORDS = {
    "używany", "używane", "używanych", "używanego", "używana", "używanym",
    "uzywany", "uzywane", "uzywanych", "uzywanego", "uzywana", "uzywanym",
}


def preprocess_query(query: str) -> tuple[str, dict]:
    """
    Extract semantic filters from search query text.

    UNIVERSAL rules (applied to every query equally):

    1. "używany/używane/..." → EXCLUSIVE filter: condition=used
       The keyword is removed from the query text and replaced with a
       hard filter. Nobody searching "używany" wants new products.

    2. Brand name detected → EXCLUSIVE filter: brand=X
       When query contains a recognized brand name, only that brand's
       products are returned.
       EXCEPTION: if the brand is preceded by "do/dla/na/pod" (Polish
       prepositions meaning "for/to"), it's a compatibility query —
       e.g., "obiektyw do Canon" means "lens for Canon", not "Canon lens".
       In this case NO brand filter is applied.
       When multiple brands are detected → no filter (ambiguous intent).

    Returns: (cleaned_query, extra_filters)
    """
    extra_filters = {}
    words = query.strip().split()
    words_lower = [w.lower() for w in words]

    # --- 1. Detect "used" keywords → condition filter ---
    keep = []
    for i, wl in enumerate(words_lower):
        if wl in _USED_KEYWORDS:
            extra_filters["condition"] = "used"
        else:
            keep.append(i)

    words = [words[i] for i in keep]
    words_lower = [w.lower() for w in words]

    # --- 1b. Extra-space brand joining (run BEFORE splitting) ---
    # "smal lrig" → "smallrig", "pana sonic" → "panasonic", "elin chrom" → "elinchrom"
    # When adjacent tokens form a known brand when joined, merge them.
    # Must run before splitting to prevent false positives (e.g., "sonic" → "soni" + "c").
    brands = _get_brand_cache()
    aliases = _load_brand_aliases()

    # Build brand lookup with spaces removed (for multi-word brands like "peak design" → "peakdesign")
    _brand_nospace = {}
    for bl, bo in brands.items():
        ns = bl.replace(" ", "")
        if ns not in _brand_nospace or len(bl) > len(_brand_nospace[ns][0]):
            _brand_nospace[ns] = (bl, bo)
    for al, ao in aliases.items():
        ns = al.replace(" ", "")
        if ns not in _brand_nospace or len(al) > len(_brand_nospace[ns][0]):
            _brand_nospace[ns] = (al, ao)

    if len(words) >= 2:
        new_words = []
        i = 0
        joined_any = False
        while i < len(words):
            if i + 1 < len(words):
                joined_pair = (words[i] + words[i + 1]).lower()
                canonical = brands.get(joined_pair) or aliases.get(joined_pair)
                # Also check nospace variants of multi-word brands
                if not canonical and joined_pair in _brand_nospace:
                    canonical = _brand_nospace[joined_pair][1]
                if canonical:
                    new_words.append(canonical)
                    i += 2
                    joined_any = True
                    continue
            new_words.append(words[i])
            i += 1
        if joined_any:
            words = new_words
            words_lower = [w.lower() for w in words]

    # --- 1c. No-space brand splitting ---
    # "sonya6700" → "sony a6700", "canonrf" → "canon rf", "peakdesignpaski" → "PEAKDESIGN paski"
    # When a single token starts with a known brand (spaces removed), split it there.
    # Remainder must be ≥2 chars to avoid false positives like "sonic" → "soni" + "c".
    new_words = []
    nospace_changed = False
    for word in words:
        wl = word.lower()
        if len(wl) >= 5:  # word must be ≥5 chars (brand ≥3 + remainder ≥2)
            # Try longest brand prefix first
            best_brand = None
            best_len = 0
            for ns, (_, brand_original) in _brand_nospace.items():
                if (wl.startswith(ns) and len(ns) > best_len
                        and len(wl) - len(ns) >= 2  # remainder must be ≥2 chars
                        and len(ns) >= 3):
                    best_brand = brand_original
                    best_len = len(ns)
            if best_brand:
                remainder = word[best_len:]
                new_words.append(best_brand)
                new_words.append(remainder)
                nospace_changed = True
                continue
        new_words.append(word)
    if nospace_changed:
        words = new_words
        words_lower = [w.lower() for w in words]

    # --- 2. Detect brand names → brand filter ---
    # Also resolves typos/aliases: "cannon" → "Canon", "lumix" → "Panasonic"
    # Supports MULTI-WORD brands: "Peak Design", "Carl Zeiss", "OM System"
    found_brands = []  # list of (indices_tuple, original_case_brand)
    matched_indices = set()  # track which word indices are part of a brand match

    # First pass: try multi-word brand matches (2-word and 3-word)
    # This handles brands like "Peak Design", "Carl Zeiss", "Venus Optics"
    for ngram_size in (3, 2):  # longest first to avoid partial matches
        for i in range(len(words_lower) - ngram_size + 1):
            if any(idx in matched_indices for idx in range(i, i + ngram_size)):
                continue  # skip if any word already matched
            phrase = " ".join(words_lower[i:i + ngram_size])
            canonical = None
            if phrase in brands:
                canonical = brands[phrase]
            elif phrase in aliases:
                canonical = aliases[phrase]
            if canonical:
                # Check compatibility preposition before the phrase
                preceded_by_compat = i > 0 and words_lower[i - 1] in _COMPAT_PREPOSITIONS
                if not preceded_by_compat:
                    found_brands.append((tuple(range(i, i + ngram_size)), canonical))
                    for idx in range(i, i + ngram_size):
                        matched_indices.add(idx)

    # Second pass: single-word brand matches (skip already matched words)
    for i, wl in enumerate(words_lower):
        if i in matched_indices:
            continue
        canonical = None
        if wl in brands:
            canonical = brands[wl]
        elif wl in aliases:
            canonical = aliases[wl]
        # Truncation tolerance: if word ≥5 chars and not found,
        # check if it's a prefix of a known brand (handles "manfrott" → "manfrotto")
        elif len(wl) >= 5:
            for brand_lower, brand_original in brands.items():
                if brand_lower.startswith(wl) and len(brand_lower) - len(wl) <= 2:
                    canonical = brand_original
                    break
            if canonical is None:
                for alias_lower, alias_target in aliases.items():
                    if alias_lower.startswith(wl) and len(alias_lower) - len(wl) <= 2:
                        canonical = alias_target
                        break
        if canonical:
            preceded_by_compat = i > 0 and words_lower[i - 1] in _COMPAT_PREPOSITIONS
            if not preceded_by_compat:
                found_brands.append(((i,), canonical))
                matched_indices.add(i)
                # Replace the typo in query with canonical brand name
                words[i] = canonical
                words_lower[i] = canonical.lower()

    # Apply brand filter ONLY if exactly one non-compatibility brand found
    # Multiple brands = ambiguous intent (e.g., "Sigma Canon" = Sigma for Canon mount)
    if len(found_brands) == 1:
        extra_filters["brand"] = found_brands[0][1]
        # Remove brand words from query text — the brand filter handles them.
        # This prevents text-match conflicts (e.g., "peak design" not matching "PEAKDESIGN")
        # and lets brand-only queries fall through to match_all + brand filter.
        brand_indices = set(found_brands[0][0])
        words = [w for i, w in enumerate(words) if i not in brand_indices]

    cleaned = " ".join(words).strip()
    # If remaining text is too short (≤2 chars), it may be a stop word
    # (e.g., "Nikon z" → "z" is a Polish stop word removed by analyzer).
    # In this case, keep the brand name in query text for better matching.
    if len(cleaned) <= 2 and cleaned and extra_filters.get("brand"):
        brand_name = extra_filters["brand"]
        cleaned = brand_name + " " + cleaned
    # If cleaned is empty BUT we have filters, return "" to trigger match_all
    # (e.g., "peak design" → brand filter, no text query needed)
    # Only fall back to original query if no filters were extracted at all
    if not cleaned and not extra_filters:
        cleaned = query.strip()

    # --- Category-term detection → category browse ---
    # When the remaining query is a single category word ("obiektyw", "aparat",
    # "statyw"...), browse that category ranked by popularity instead of running
    # a name-text match (which surfaces accessories that merely contain the word
    # in their name). UNIVERSAL: the taxonomy comes from the index, not hard-coded.
    if "category_paths" not in extra_filters:
        cat_paths = _detect_category_paths(cleaned)
        if cat_paths:
            extra_filters["category_paths"] = cat_paths
            cleaned = ""  # empty text → match_all + category filter + popularity sort

    return cleaned, extra_filters


# =============================================================================
# Lens Notation Normalization
# =============================================================================
#
# PROBLEM: Users type "85mm f2.8" but products are named "85 mm f/2.8".
# The standard tokenizer treats "85mm" as ONE token and "85 mm" as TWO.
# This means "85mm" in query doesn't match products with "85 mm" in name.
#
# SOLUTION: Normalize query text to match the DOMINANT naming convention
# in the product feed (94% of lenses use "XX mm" with space, 93% use "f/X.X").
#
# IMPORTANT: This normalization is applied ONLY to text matching fields.
# Exact-match fields (EAN, product ID, brand.keyword) use the raw query
# to preserve exact lookup capability.
#
# Normalizations (UNIVERSAL — same for every query):
#   1. "85mm"    → "85 mm"    (space before mm)
#   2. "f2.8"    → "f/2.8"    (add slash after f/F before digit)
#   3. "F2.8"    → "f/2.8"    (lowercase + slash)
#   4. "f/2,8"   → "f/2.8"    (comma → dot in aperture context)
#   5. "85MM"    → "85 mm"    (uppercase MM → lowercase mm)

def normalize_lens_notation(query: str) -> str:
    """
    Normalize lens focal length and aperture notation to match product names.

    Applied at query time to bridge the gap between how users type queries
    and how products are named in the feed.

    Examples:
        "85mm f/1.4"     → "85 mm f/1.4"     (space before mm)
        "85mm f1.4"      → "85 mm f/1.4"     (space + slash)
        "24-70mm f2.8"   → "24-70 mm f/2.8"  (both fixes)
        "50mm"           → "50 mm"            (just space)
        "F2.8"           → "f/2.8"            (lowercase + slash)
        "f/2,8"          → "f/2.8"            (comma to dot)
        "Canon 70-200"   → "Canon 70-200"     (no change — no lens notation)
        "SEL85F14GM"     → not normalized (used via raw_query for exact match)
    """
    # 1. Space before mm/MM: "85mm" → "85 mm", "24-70mm" → "24-70 mm"
    #    Idempotent: "85 mm" stays "85 mm"
    query = re.sub(r'(\d)\s*mm\b', r'\1 mm', query, flags=re.IGNORECASE)

    # 3. F-stop slash: "f2.8" → "f/2.8", "F1.4" → "f/1.4", "f 1.2" → "f/1.2"
    #    Handles both "f2.8" (no space) and "f 1,2" (with space, Polish decimal)
    #    Safe: won't match "FE", "fx3", "F-Stop" (requires digit after f/space)
    #    First normalize "f/..." that already has slash (just lowercase F→f)
    query = re.sub(r'\b[fF]/(\d)', r'f/\1', query)
    #    Then handle missing slash: "f2.8", "f 1,2", "F1.4"
    query = re.sub(r'\b[fF]\s+(\d)', r'f/\1', query)
    query = re.sub(r'\b[fF](\d)', r'f/\1', query)

    # 3. Comma → dot in aperture: "f/2,8" → "f/2.8", "f/3,5-5,6" → "f/3.5-5.6"
    #    Only in f/NUMBER context (safe — won't affect non-aperture numbers)
    query = re.sub(r'(f/\d+),(\d)', r'\1.\2', query)
    # Also handle second comma in ranges: "f/3.5-5,6" → "f/3.5-5.6"
    query = re.sub(r'(\d)-(\d+),(\d)', r'\1-\2.\3', query)

    # 4. Standalone aperture after focal length: "35 1.4" → "35 f/1.4"
    #    Matches pattern: (focal_length_or_range) (small_decimal_number)
    #    where small decimal = 0.x to 9.x (typical f-stops: 1.2, 1.4, 1.8, 2.8, 4, 5.6)
    #    Safe: only applies after mm-range patterns or single focal lengths
    query = re.sub(
        r'(\b\d{2,3}(?:\s*mm)?)\s+(\d\.\d)\b',
        lambda m: m.group(1) + ' f/' + m.group(2)
        if float(m.group(2)) <= 9.9
        else m.group(0),
        query
    )

    return query


def _compact_mm_variant(query: str) -> str:
    """Generate compact mm variant: "100 mm" → "100mm", "35-100 mm" → "35-100mm".

    Products inconsistently use "100mm" (no space) vs "100 mm" (with space).
    The index stores "100mm" as one token. This generates the compact form
    to match products where mm is glued to the number.
    """
    return re.sub(r'(\d)\s+mm\b', r'\1mm', query, flags=re.IGNORECASE)


def _soft_split_model_codes(query: str) -> str:
    """Split ONLY at digit→letter boundaries. Keeps letter+digit together.

    "a7IV" → "a7 IV" (useful for name.exact phrase matching where "A7 IV"
    is tokenized as "a7" + "iv"). Contrast with _split_model_codes which
    gives "a 7 IV" (full split at both boundaries).
    """
    return re.sub(r'(\d)([a-zA-Z])', r'\1 \2', query)


def _split_model_codes(query: str) -> str:
    """Generate letter-digit split variant of query for model code matching.

    Product names are inconsistent: "RS 5" vs "Z8" vs "X100VI".
    The index stores them as-is, so "rs5" → one token, "RS 5" → two tokens.
    This function splits letters from digits: "rs5" → "rs 5", "a7iv" → "a 7 iv".
    Also splits single-letter prefix from remaining alpha chars for camera series
    like "Zfc" → "Z fc", "Zf" → stays "Zf" (too short to split).
    Used as an ADDITIONAL should clause (not a replacement) so both the
    original combined form AND the split form are searched.
    """
    # Split at letter→digit and digit→letter boundaries
    result = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', query)
    result = re.sub(r'(\d)([a-zA-Z])', r'\1 \2', result)

    # Split single-letter prefix from remaining alpha for short model codes:
    # "zfc" → "z fc", "zfii" → "z fii" (Nikon Z-series naming convention)
    # Only applies to words that are 3-5 chars, all alpha, single leading letter
    words = result.split()
    new_words = []
    for w in words:
        if len(w) == 3 and w.isalpha():
            new_words.append(w[0] + " " + w[1:])
        else:
            new_words.append(w)
    result = " ".join(new_words)

    return result


def _join_model_codes(query: str) -> str:
    """Generate compact variant by joining adjacent model code tokens.

    The REVERSE of _split_model_codes: "z5 II" → "z5II", "rs 5" → "rs5".
    This handles products like "Z5II" stored as a single token in the index
    when the user types "z5 II" with spaces.
    Also joins single letter + short word: "z fc" → "zfc".

    Used as an ADDITIONAL should clause alongside the split variant.
    """
    words = query.split()
    if len(words) <= 1:
        return query

    joined = []
    i = 0
    while i < len(words):
        current = words[i]
        if i + 1 < len(words):
            nxt = words[i + 1]
            # Join if: letter-ending + digit-starting, digit-ending + letter-starting,
            # or single letter + short word (z + fc → zfc)
            should_join = False
            if current and nxt:
                if current[-1].isalpha() and nxt[0].isdigit():
                    should_join = True
                elif current[-1].isdigit() and nxt[0].isalpha():
                    should_join = True
                elif len(current) == 1 and current.isalpha() and len(nxt) <= 4 and nxt.isalpha():
                    should_join = True
            if should_join:
                joined.append(current + nxt)
                i += 2
                continue
        joined.append(current)
        i += 1
    return " ".join(joined)


def warm_caches():
    """Pre-load brand + category caches at startup (called from FastAPI startup event)."""
    _get_brand_cache()
    _get_category_cache()


def build_search_query(
    query: str,
    page: int = 1,
    size: int = 24,
    filters: dict | None = None,
    sort_by: str | None = None,
) -> dict:
    """
    Build an Elasticsearch query for product search.

    Uses function_score to combine:
    - Text relevance (multi_match across name, brand, description, category)
    - Availability boost (universal: in-stock products score higher)
    - Popularity boost (universal: based on GA views/sales, gentle log scale)

    All scoring functions are UNIVERSAL — they treat every product category equally.
    """
    filters = filters or {}

    # === Preprocess query (semantic filter extraction) ===
    # Detects "używany" → condition filter, brand names → brand filter
    cleaned_query, extra_filters = preprocess_query(query)
    for key, value in extra_filters.items():
        if key not in filters:  # Explicit API filters take precedence
            filters[key] = value
    query = cleaned_query

    # === Normalize lens notation for text matching ===
    # "85mm f2.8" → "85 mm f/2.8" (matches product naming convention)
    # raw_query preserved for exact-match fields (EAN, product ID, brand keyword)
    raw_query = query
    query = normalize_lens_notation(query)
    # Generate model code split variant: "rs5" → "rs 5", "a7iv" → "a 7 iv", "zfc" → "z fc"
    # Used as additional should clause so both combined AND split forms match
    split_query = _split_model_codes(query)
    # Generate joined variant (reverse): "z5 II" → "z5II", "z fc" → "zfc"
    joined_query = _join_model_codes(query)
    # Generate compact mm variant: "100 mm" → "100mm" (matches unsplit index tokens)
    compact_mm = _compact_mm_variant(query)

    # When query tokens are all short (≤3 chars each), they're likely fragments of a
    # model code (e.g., "ml 087", "220 C", "rs 5"). Short tokens cause noise by matching
    # broadly. Swap primary query with joined variant which is more precise.
    # Exception: don't swap when tokens include measurement units (mm, cm) — that's
    # lens notation ("50 mm") which should stay split for proper matching.
    _UNITS = {"mm", "cm", "kg", "gb", "tb", "mb"}
    tokens = query.split()
    all_short_tokens = len(tokens) >= 2 and all(len(w) <= 3 for w in tokens)
    has_unit = any(w.lower() in _UNITS for w in tokens)
    if all_short_tokens and not has_unit and joined_query != query:
        query, joined_query = joined_query, query  # swap: "ml087" is now primary
        # Recompute variants for the new primary query
        split_query = _split_model_codes(query)
        compact_mm = _compact_mm_variant(query)

    # Generate no-space variant: "sta tyw" → "statyw", "sof tbox" → "softbox"
    # Handles accidental spaces in the middle of words
    nospace_query = query.replace(" ", "") if " " in query else None

    # === Model-code disambiguation (DIGIT-GATED) ===
    # Only queries that contain a digit ("a7v", "z5", "r6", "220c") are model
    # codes that need extra boosts to distinguish a specific model ("Sony A7 V")
    # from look-alikes ("A7RV"/"A7IV"). Plain category words ("obiektyw",
    # "lampa", "filtr") contain no digit, so they NEVER receive these boosts —
    # keeping category browse (§6.10) and generic ranking untouched.
    soft_split = _soft_split_model_codes(query)
    has_digit = any(ch.isdigit() for ch in query)
    model_code_must: list = []
    model_code_should: list = []
    if has_digit:
        model_code_must = [
            {"match": {"searchable_text": {"query": soft_split, "operator": "or",
                                           "fuzziness": "AUTO", "prefix_length": 1}}},
            {"term": {"name.autocomplete": {"value": query.lower(), "boost": 60}}},
        ]
        model_code_should = [
            # Soft-split phrase (slop 1): "a7v"→"a7 v" pins token adjacency, so the
            # short canonical name "Sony A7 V body" outranks long accessory names.
            {"match_phrase": {"name.exact": {"query": soft_split, "slop": 1, "boost": 35}}},
            {"match_phrase_prefix": {"name.exact": {"query": soft_split, "boost": 25}}},
            {"term": {"name.autocomplete": {"value": query.lower(), "boost": 80}}},
            {"match": {"searchable_text": {"query": soft_split, "operator": "and", "boost": 20}}},
        ]

    # === Core text query ===
    # Strategy (UNIVERSAL for all categories):
    #
    # The `searchable_text` field combines name + brand + category into one
    # field, enabling cross-term matching with fuzziness in a single match
    # query (e.g., "lampa Godox" matches "lampa" from name + "Godox" from
    # brand, and fuzziness bridges Polish inflection: lampa→lampy).
    #
    # Query structure:
    #   "must": OR of [text search, EAN match, product ID match]
    #     → ensures we find SOMETHING (broad recall + exact ID lookups)
    #   "should": precision boosters that improve ranking
    #     → rewards products matching ALL terms, exact phrases, etc.

    must_query = {
        "bool": {
            # MUST: at least one of these must match
            "must": [
                {
                    "bool": {
                        "should": [
                            # Text search on combined field (primary)
                            {
                                "match": {
                                    "searchable_text": {
                                        "query": query,
                                        "operator": "or",
                                        "fuzziness": "AUTO",
                                        "prefix_length": 1,
                                    }
                                }
                            },
                            # Prefix matching for Polish inflection (UNIVERSAL)
                            # Polish preserves word stems, only suffixes change:
                            # "obiektyw" → "obiektywy/obiektywu/obiektywów"
                            # "aparat" → "aparaty/aparatów"
                            # Without a Polish stemmer plugin, prefix matching
                            # bridges this gap universally for ALL words.
                            {
                                "match_phrase_prefix": {
                                    "searchable_text": {
                                        "query": query,
                                    }
                                }
                            },
                            # Model code split variant: "rs5" → "rs 5"
                            # Uses AND to require ALL split tokens (avoids single-char noise)
                            {
                                "match": {
                                    "searchable_text": {
                                        "query": split_query,
                                        "operator": "and",
                                        "boost": 0.5,
                                    }
                                }
                            },
                            # Compact mm variant: "100 mm" → "100mm"
                            {
                                "match": {
                                    "searchable_text": {
                                        "query": compact_mm,
                                        "operator": "and",
                                        "boost": 0.5,
                                    }
                                }
                            },
                            # Joined model code variant: "z5 II" → "z5II"
                            {
                                "match": {
                                    "searchable_text": {
                                        "query": joined_query,
                                        "operator": "and",
                                        "boost": 0.5,
                                    }
                                }
                            },
                            # Joined prefix: "ml 087" → joined "ml087" prefix-matches "ml087nwb"
                            {
                                "match_phrase_prefix": {
                                    "searchable_text": {
                                        "query": joined_query,
                                        "boost": 0.5,
                                    }
                                }
                            },
                            # No-space variant: "sta tyw" → "statyw" matches products
                            # Handles accidental spaces in the middle of words
                            *(
                                [
                                    {
                                        "match": {
                                            "searchable_text": {
                                                "query": nospace_query,
                                                "operator": "and",
                                                "boost": 0.5,
                                            }
                                        }
                                    },
                                    {
                                        "match_phrase_prefix": {
                                            "searchable_text": {
                                                "query": nospace_query,
                                                "boost": 0.5,
                                            }
                                        }
                                    },
                                ]
                                if nospace_query
                                else []
                            ),
                            # Model-code recall (digit-gated): soft-split + autocomplete term
                            *model_code_must,
                            # EAN exact match (raw query — no lens normalization)
                            {"term": {"ean": {"value": raw_query.strip(), "boost": 50}}},
                            # EAN prefix match (partial barcodes)
                            {"prefix": {"ean": {"value": raw_query.strip(), "boost": 40}}},
                            # Product ID exact match (raw query — no lens normalization)
                            {"term": {"id_verto": {"value": raw_query.strip().upper(), "boost": 50}}},
                            # Product ID prefix match (partial codes like "ACFKODEKTRAH35")
                            {"prefix": {"id_verto": {"value": raw_query.strip().upper(), "boost": 40}}},
                        ],
                        "minimum_should_match": 1,
                    }
                },
            ],
            # SHOULD: precision boosters — reward better matches
            "should": [
                # ALL query terms found in combined field (AND) — strongest signal
                # This is what makes "lampa Godox" find Godox lamps, not random lamps
                {
                    "match": {
                        "searchable_text": {
                            "query": query,
                            "operator": "and",
                            "fuzziness": "AUTO",
                            "prefix_length": 1,
                            "boost": 20,
                        }
                    }
                },
                # Model code split AND match: "rs 5" (split) with AND
                # boosts products where the split model code matches
                {
                    "match": {
                        "searchable_text": {
                            "query": split_query,
                            "operator": "and",
                            "boost": 15,
                        }
                    }
                },
                # Compact mm AND match: "35-100mm" boosts exact focal-length match
                {
                    "match": {
                        "searchable_text": {
                            "query": compact_mm,
                            "operator": "and",
                            "boost": 15,
                        }
                    }
                },
                # Joined model code AND match: "220C" boosts exact model code
                # High boost (40) because joined variant is very precise —
                # when user types "220 C", the joined form "220C" is a strong
                # signal that they want products with "220C" in the name,
                # not random "USB-C" matches from the lone "C" token.
                {
                    "match": {
                        "searchable_text": {
                            "query": joined_query,
                            "operator": "and",
                            "boost": 40,
                        }
                    }
                },
                # No-space AND match: "statyw" from "sta tyw" boosts correct products
                *(
                    [
                        {
                            "match": {
                                "searchable_text": {
                                    "query": nospace_query,
                                    "operator": "and",
                                    "boost": 15,
                                }
                            }
                        },
                    ]
                    if nospace_query
                    else []
                ),
                # MULTI-TOKEN MATCH: boost products matching 2+ query terms.
                # Uses joined_query when available to avoid noise from single-char
                # tokens (e.g., "220 C" → joined "220C" prevents "C" matching "USB-C").
                # When joined == original, this works normally on the original query.
                {
                    "match": {
                        "searchable_text": {
                            "query": joined_query if joined_query != query else query,
                            "minimum_should_match": 2,
                            "boost": 40,
                        }
                    }
                },
                # Compact mm phrase match: "35-100mm" as exact token sequence
                # Strongly boosts products with exact focal length notation
                {
                    "match_phrase": {
                        "searchable_text": {
                            "query": compact_mm,
                            "boost": 30,
                        }
                    }
                },
                # Joined phrase match in name — "220C" as exact sequence
                {
                    "match_phrase": {
                        "name.exact": {
                            "query": joined_query,
                            "boost": 25,
                        }
                    }
                },
                # Exact phrase in name — very high precision
                {
                    "match_phrase": {
                        "name.exact": {
                            "query": query,
                            "boost": 25,
                        }
                    }
                },
                # Model-code disambiguation boosts (digit-gated, see above)
                *model_code_should,
                # Name.exact AND match — NO synonyms, rewards products where
                # query words appear LITERALLY in name (not via category path)
                # "akumulator" in name beats "akumulator" only in category
                {
                    "match": {
                        "name.exact": {
                            "query": query,
                            "operator": "and",
                            "boost": 20,
                        }
                    }
                },
                # Name match with AND — rewards products with all terms in name
                {
                    "match": {
                        "name": {
                            "query": query,
                            "operator": "and",
                            "fuzziness": "AUTO",
                            "prefix_length": 1,
                            "boost": 10,
                        }
                    }
                },
                # Name match with split variant AND — e.g. "rs 5" matches "RS 5" in name
                {
                    "match": {
                        "name": {
                            "query": split_query,
                            "operator": "and",
                            "boost": 10,
                        }
                    }
                },
                # Name match with joined variant AND — e.g. "z5II" matches "Z5II" in name
                {
                    "match": {
                        "name": {
                            "query": joined_query,
                            "operator": "and",
                            "boost": 10,
                        }
                    }
                },
                # Name match with OR — rewards name relevance
                {
                    "match": {
                        "name": {
                            "query": query,
                            "operator": "or",
                            "boost": 3,
                        }
                    }
                },
                # Brand exact keyword match — very strong signal when
                # query exactly matches a brand name (raw — no lens normalization)
                {
                    "term": {
                        "brand.keyword": {
                            "value": raw_query.strip(),
                            "boost": 20,
                        }
                    }
                },
                # Brand text match — for partial/fuzzy brand matching
                {
                    "match": {
                        "brand": {
                            "query": query,
                            "boost": 8,
                        }
                    }
                },
            ],
        }
    }

    # Handle empty query after preprocessing (e.g., query was just "używane")
    # All keywords were extracted as filters, nothing left for text matching
    if not query:
        must_query = {"match_all": {}}

    # === Filters ===
    filter_clauses = []

    if filters.get("availability"):
        filter_clauses.append({"term": {"availability": filters["availability"]}})

    if filters.get("brand"):
        filter_clauses.append({"terms": {"brand.keyword": _get_brand_keywords(filters["brand"])}})

    if filters.get("condition"):
        filter_clauses.append({"term": {"condition": filters["condition"]}})

    if filters.get("category"):
        filter_clauses.append({"term": {"category.keyword": filters["category"]}})

    if filters.get("category_lvl0"):
        filter_clauses.append({"term": {"category_lvl0": filters["category_lvl0"]}})

    # Category browse: restrict to the detected category's full paths.
    if filters.get("category_paths"):
        filter_clauses.append({"terms": {"category.keyword": filters["category_paths"]}})

    if filters.get("price_min") or filters.get("price_max"):
        price_range = {}
        if filters.get("price_min"):
            price_range["gte"] = float(filters["price_min"])
        if filters.get("price_max"):
            price_range["lte"] = float(filters["price_max"])
        filter_clauses.append({"range": {"price": price_range}})

    # === Function score (UNIVERSAL MULTIPLICATIVE boosting) ===
    #
    # DESIGN: Multiplicative scoring means:
    #   final_score = text_relevance * availability_factor * popularity_factor
    #
    # WHY MULTIPLICATIVE:
    # - With additive (old approach), GA boost was ~8 points on text scores of
    #   ~150-500, making popularity invisible (~2-5% of total score).
    # - With multiplicative, a product with 11,000 views gets 3x multiplier
    #   while a product with 0 views gets 1.0x. This creates meaningful
    #   separation when text scores are similar (brand-only queries).
    # - Multiplicative CANNOT promote irrelevant products: 0 text relevance
    #   * 3.0 popularity = still 0. Text relevance remains the gatekeeper.
    #
    # TUNING: The single tuning knob is the 0.1 factor in the script_score.
    #   - 0.05 = gentle popularity influence
    #   - 0.10 = moderate
    #   - 0.15 = strong (current — user demanded "let popularity decide")
    #   - 0.20 = very strong popularity influence
    #
    # popularity_score formula (from ga_updater.py):
    #   pop = log1p(views) * 1.0 + log1p(sales) * 3.0
    # Examples:
    #   Canon EOS R6 II (views=11590, sales=53): pop=21.27 → multiplier=3.13
    #   Canon RC-6 pilot (views=122, sales=4):   pop=8.95  → multiplier=1.90
    #   Zero GA product:                          pop=0     → multiplier=1.00
    #
    functions = [
        # Popularity multiplier from pre-computed GA score — universal
        # Converts popularity_score to a multiplier centered at 1.0
        # Products with no GA data get neutral 1.0 (no penalty, no boost)
        {
            "script_score": {
                "script": {
                    "source": "1 + doc['popularity_score'].value * 0.15"
                }
            }
        },
    ]

    # === Build full query ===
    body = {
        "query": {
            "function_score": {
                "query": {
                    "bool": {
                        "must": [must_query],
                        "filter": filter_clauses,
                    }
                },
                "functions": functions,
                "score_mode": "multiply",   # availability * popularity compound
                "boost_mode": "multiply",   # function_score multiplies text relevance
            }
        },
        "from": (page - 1) * size,
        "size": size,
        "highlight": {
            "fields": {
                "name": {"number_of_fragments": 1},
                "description": {"number_of_fragments": 1, "fragment_size": 150},
            }
        },
        "aggs": {
            "brands": {
                "terms": {"field": "brand.keyword", "size": 20}
            },
            "categories_lvl0": {
                "terms": {"field": "category_lvl0", "size": 20}
            },
            "availability": {
                "terms": {"field": "availability", "size": 5}
            },
            "price_stats": {
                "stats": {"field": "price"}
            },
        },
    }

    # === Sorting ===
    if sort_by == "price_asc":
        body["sort"] = [{"price": "asc"}, "_score"]
    elif sort_by == "price_desc":
        body["sort"] = [{"price": "desc"}, "_score"]
    elif sort_by == "name":
        body["sort"] = [{"name.keyword": "asc"}]
    # Default: sort by _score (relevance + function_score)

    return body


def search(query: str, **kwargs) -> dict:
    """Execute search and return results."""
    es = get_es_client()
    body = build_search_query(query, **kwargs)
    response = es.search(index=INDEX_NAME, body=body)

    hits = response["hits"]
    results = {
        "total": hits["total"]["value"],
        "products": [],
        "aggregations": response.get("aggregations", {}),
    }

    for hit in hits["hits"]:
        product = hit["_source"]
        product["_score"] = hit["_score"]
        product["_highlight"] = hit.get("highlight", {})
        results["products"].append(product)

    return results


def suggest(query: str, size: int = 7) -> dict:
    """
    Sugester-style autocomplete: returns categories + products.

    Returns dict with:
    - categories: list of deepest category names (lvl2 preferred, lvl1 fallback)
    - products: list of product dicts (first one = "popularny produkt")

    PERFORMANCE: Uses a lightweight query optimized for speed:
    - Fewer should clauses (no fuzziness for short queries)
    - Minimal _source fields
    - Only category aggregations (no brands/price stats)
    - No highlights
    """
    es = get_es_client()

    # === Preprocess query (same as full search for consistency) ===
    cleaned_query, extra_filters = preprocess_query(query)
    filters = dict(extra_filters)
    query_text = cleaned_query

    # Normalize lens notation
    raw_query = query_text
    query_text = normalize_lens_notation(query_text)
    split_query = _split_model_codes(query_text)
    joined_query = _join_model_codes(query_text)
    soft_split = _soft_split_model_codes(query_text)
    compact_mm = _compact_mm_variant(query_text)

    # Use joined as primary when all tokens are short (≤3 chars) — likely model code fragments
    # Exception: don't swap when tokens include measurement units (mm, cm, gb etc.)
    _UNITS = {"mm", "cm", "kg", "gb", "tb", "mb"}
    tokens = query_text.split()
    all_short_tokens = len(tokens) >= 2 and all(len(w) <= 3 for w in tokens)
    has_unit = any(w.lower() in _UNITS for w in tokens)
    if all_short_tokens and not has_unit and joined_query != query_text:
        query_text, joined_query = joined_query, query_text
        # Recompute variants for the new primary query
        split_query = _split_model_codes(query_text)
        soft_split = _soft_split_model_codes(query_text)
        compact_mm = _compact_mm_variant(query_text)

    # No-space variant for accidental spaces: "sta tyw" → "statyw"
    nospace_query = query_text.replace(" ", "") if " " in query_text else None

    # === Build lightweight query ===
    filter_clauses = []
    if filters.get("brand"):
        filter_clauses.append({"terms": {"brand.keyword": _get_brand_keywords(filters["brand"])}})
    if filters.get("condition"):
        filter_clauses.append({"term": {"condition": filters["condition"]}})
    if filters.get("category_paths"):
        filter_clauses.append({"terms": {"category.keyword": filters["category_paths"]}})

    if query_text:
        must_query = {
            "bool": {
                "should": [
                    # Primary: combined text search
                    {
                        "match": {
                            "searchable_text": {
                                "query": query_text,
                                "operator": "or",
                                "fuzziness": "AUTO",
                                "prefix_length": 1,
                            }
                        }
                    },
                    # Prefix matching for Polish inflection (same as full search)
                    {
                        "match_phrase_prefix": {
                            "searchable_text": {
                                "query": query_text,
                            }
                        }
                    },
                    # Soft-split in must: "a7iii"→"a7 iii" as proper 2-token
                    # query. Critical because "a7iii" as 1 token exceeds fuzz
                    # limit (5 chars, max 2 edits, but "a7"→"a7iii" = 3 inserts)
                    {
                        "match": {
                            "searchable_text": {
                                "query": soft_split,
                                "operator": "or",
                                "fuzziness": "AUTO",
                                "prefix_length": 1,
                            }
                        }
                    },
                    # Model code split variant (OR for recall)
                    {
                        "match": {
                            "searchable_text": {
                                "query": split_query,
                                "operator": "or",
                                "fuzziness": "AUTO",
                                "prefix_length": 1,
                                "boost": 0.5,
                            }
                        }
                    },
                    # Compact mm variant
                    {
                        "match": {
                            "searchable_text": {
                                "query": compact_mm,
                                "operator": "and",
                                "boost": 0.5,
                            }
                        }
                    },
                    # Joined model code variant
                    {
                        "match": {
                            "searchable_text": {
                                "query": joined_query,
                                "operator": "and",
                                "boost": 0.5,
                            }
                        }
                    },
                    # Joined prefix: "ml 087" → "ml087" prefix-matches "ml087nwb"
                    {
                        "match_phrase_prefix": {
                            "searchable_text": {
                                "query": joined_query,
                                "boost": 0.5,
                            }
                        }
                    },
                    # No-space variant: "sta tyw" → "statyw"
                    *(
                        [
                            {
                                "match": {
                                    "searchable_text": {
                                        "query": nospace_query,
                                        "operator": "and",
                                        "boost": 0.5,
                                    }
                                }
                            },
                            {
                                "match_phrase_prefix": {
                                    "searchable_text": {
                                        "query": nospace_query,
                                        "boost": 0.5,
                                    }
                                }
                            },
                        ]
                        if nospace_query
                        else []
                    ),
                    # Relaxed fuzz (prefix_length=0) on name.exact for model codes
                    # where first char differs: "6700" → "A6700", "100" → "X100"
                    # Uses name.exact (standard analyzer, no stop word removal)
                    {
                        "match": {
                            "name.exact": {
                                "query": query_text,
                                "fuzziness": "AUTO",
                                "prefix_length": 0,
                                "boost": 5,
                            }
                        }
                    },
                    # EAN exact match
                    {"term": {"ean": {"value": raw_query.strip(), "boost": 50}}},
                    # EAN prefix match (partial barcodes)
                    {"prefix": {"ean": {"value": raw_query.strip(), "boost": 40}}},
                    # Product ID exact match
                    {"term": {"id_verto": {"value": raw_query.strip().upper(), "boost": 50}}},
                    # Product ID prefix match (partial codes like "ACFKODEKTRAH35")
                    {"prefix": {"id_verto": {"value": raw_query.strip().upper(), "boost": 40}}},
                ],
                "minimum_should_match": 1,
            }
        }
        should_clauses = [
            # ALL terms match — strongest signal
            {
                "match": {
                    "searchable_text": {
                        "query": query_text,
                        "operator": "and",
                        "boost": 20,
                    }
                }
            },
            # Soft-split AND: "a7iii"→"a7 iii" requires BOTH "a7" AND "iii"
            # Critical for model codes where letters+numbers are glued:
            # "a7iii" → must contain both "a7" and "iii" → Sony A7 III wins
            {
                "match": {
                    "searchable_text": {
                        "query": soft_split,
                        "operator": "and",
                        "boost": 20,
                    }
                }
            },
            # Split model code AND match
            {
                "match": {
                    "searchable_text": {
                        "query": split_query,
                        "operator": "and",
                        "boost": 15,
                    }
                }
            },
            # Compact mm AND match
            {
                "match": {
                    "searchable_text": {
                        "query": compact_mm,
                        "operator": "and",
                        "boost": 15,
                    }
                }
            },
            # Joined model code AND match: "220C" boosts exact model code (high boost)
            {
                "match": {
                    "searchable_text": {
                        "query": joined_query,
                        "operator": "and",
                        "boost": 40,
                    }
                }
            },
            # No-space AND match: "statyw" from "sta tyw"
            *(
                [
                    {
                        "match": {
                            "searchable_text": {
                                "query": nospace_query,
                                "operator": "and",
                                "boost": 15,
                            }
                        }
                    },
                ]
                if nospace_query
                else []
            ),
            # Joined phrase in name: "220C" as exact sequence
            {
                "match_phrase": {
                    "name.exact": {
                        "query": joined_query,
                        "boost": 25,
                    }
                }
            },
            # Exact phrase in name
            {
                "match_phrase": {
                    "name.exact": {
                        "query": query_text,
                        "boost": 25,
                    }
                }
            },
            # Name.exact AND — literal words in name beat category-only matches
            {
                "match": {
                    "name.exact": {
                        "query": query_text,
                        "operator": "and",
                        "boost": 20,
                    }
                }
            },
            # Brand keyword match
            {
                "term": {
                    "brand.keyword": {
                        "value": raw_query.strip(),
                        "boost": 20,
                    }
                }
            },
            # NAME PHRASE BOOST with slop: "R6 mark II" matches "r6 II"
            # because slop=2 allows up to 2 words between matched terms.
            # Uses name.exact (standard analyzer, no stop words).
            {
                "match_phrase": {
                    "name.exact": {
                        "query": soft_split,
                        "slop": 2,
                        "boost": 30,
                    }
                }
            },
            # Strict phrase prefix for autocomplete-style matching
            {
                "match_phrase_prefix": {
                    "name.exact": {
                        "query": soft_split,
                        "boost": 25,
                    }
                }
            },
            # BRAND + QUERY at name start: "FujiFilm X100VI" wins over
            # "FujiFilm AR-X100" because the brand+model phrase appears
            # at the very start of the name (the product IS the X100).
            *(
                [
                    {
                        "match_phrase_prefix": {
                            "name.exact": {
                                "query": filters["brand"] + " " + soft_split,
                                "boost": 35,
                            }
                        }
                    },
                ]
                if filters.get("brand") and query_text
                else []
            ),
        ]
    else:
        must_query = {"match_all": {}}
        should_clauses = []

    source_fields = [
        "name", "brand", "price", "sales_price",
        "category_lvl1", "category_lvl2",
        "id_verto", "link", "image", "availability",
    ]

    # --- BRAND-ONLY QUERY: popularity decides ---
    # When query is just a brand name (no text), show the MOST POPULAR
    # products regardless of category. Popularity (GA data) is the sole
    # ranking signal — availability is NOT considered.
    if not query_text and filters.get("brand"):
        body = {
            "query": {
                "bool": {
                    "must": [must_query],
                    "filter": filter_clauses,
                }
            },
            "size": size,
            "_source": source_fields,
            "sort": [
                {"popularity_score": {"order": "desc"}},
                {"price": {"order": "desc"}},  # tiebreaker: flagships first
            ],
            "aggs": {
                "categories_lvl2": {
                    "terms": {"field": "category_lvl2", "size": 10}
                },
                "categories_lvl1": {
                    "terms": {"field": "category_lvl1", "size": 10}
                },
            },
        }

        response = es.search(index=INDEX_NAME, body=body)

        categories = []
        seen_parents = set()

        if "aggregations" in response and "categories_lvl2" in response["aggregations"]:
            for b in response["aggregations"]["categories_lvl2"]["buckets"]:
                if b["key"]:
                    categories.append(b["key"])
                    parts = b["key"].split(" > ")
                    if len(parts) >= 2:
                        seen_parents.add(" > ".join(parts[:2]))

        if "aggregations" in response and "categories_lvl1" in response["aggregations"]:
            for b in response["aggregations"]["categories_lvl1"]["buckets"]:
                if b["key"] and b["key"] not in seen_parents:
                    categories.append(b["key"])

        products = []
        for hit in response["hits"]["hits"]:
            p = hit["_source"]
            p["_score"] = hit.get("_score", 0) or hit.get("sort", [0])[0]
            products.append(p)

        return {
            "categories": categories[:8],
            "products": products,
        }

    # --- STANDARD QUERY: text search + aggregations ---
    body = {
        "query": {
            "function_score": {
                "query": {
                    "bool": {
                        "must": [must_query],
                        "should": should_clauses,
                        "filter": filter_clauses,
                    }
                },
                "functions": [
                    {
                        "script_score": {
                            "script": {
                                "source": "1 + doc['popularity_score'].value * 0.15"
                            }
                        }
                    },
                ],
                "score_mode": "multiply",
                "boost_mode": "multiply",
            }
        },
        "size": size,
        "_source": source_fields,
        "aggs": {
            "categories_lvl2": {
                "terms": {"field": "category_lvl2", "size": 10}
            },
            "categories_lvl1": {
                "terms": {"field": "category_lvl1", "size": 10}
            },
        },
    }

    response = es.search(index=INDEX_NAME, body=body)

    # Extract categories — prefer deeper levels, deduplicate
    categories = []
    seen_parents = set()

    if "aggregations" in response and "categories_lvl2" in response["aggregations"]:
        for b in response["aggregations"]["categories_lvl2"]["buckets"]:
            if b["key"]:
                categories.append(b["key"])
                parts = b["key"].split(" > ")
                if len(parts) >= 2:
                    seen_parents.add(" > ".join(parts[:2]))

    if "aggregations" in response and "categories_lvl1" in response["aggregations"]:
        for b in response["aggregations"]["categories_lvl1"]["buckets"]:
            if b["key"] and b["key"] not in seen_parents:
                categories.append(b["key"])

    categories = categories[:8]

    # Extract products
    products = []
    for hit in response["hits"]["hits"]:
        p = hit["_source"]
        p["_score"] = hit["_score"]
        products.append(p)

    return {
        "categories": categories,
        "products": products,
    }
