"""
Generate search_quality_judgments.json from the product feed.
Simulates expected search results based on the actual search algorithm logic.

Key algorithm rules (from search_engine.py):
1. Brand detection → exclusive brand filter (unless compatibility query)
2. Exact phrase match in name → highest text boost
3. All-terms-AND match → strong boost
4. Popularity (GA views/sales) → multiplicative factor
5. New products rank above used/outlet (unless "używany" filter)
6. In-stock above out-of-stock
"""

import json
import re
from urllib.request import urlopen
from datetime import datetime

FEED_URL = "https://feeds.datafeedwatch.com/45030/8155cd63e2e29744fd3fb6fd20b04d7dab3275a5.json"
REGRESSION_FILE = "regression_tests.json"
ALIASES_FILE = "brand_aliases.json"
OUTPUT_FILE = "search_quality_judgments.json"


def load_feed():
    print("Downloading product feed...", end=" ", flush=True)
    with urlopen(FEED_URL, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    products = data["products"]
    print(f"{len(products)} products")
    return products


def load_aliases():
    with open(ALIASES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    alias_map = {}
    for entry in data:
        canonical = entry["brand"]
        for alias in entry["aliases"]:
            alias_map[alias.strip().lower()] = canonical
    return alias_map


def normalize(text):
    return re.sub(r'\s+', ' ', (text or "").lower().strip())


def is_outlet_or_used(p):
    """Check if product is used/outlet based on name and condition."""
    name = (p.get("name") or "").lower()
    condition = (p.get("condition") or "").lower()
    return condition == "used" or "outlet" in name or "używany" in name or "używana" in name


def estimate_popularity(p):
    """
    Estimate product popularity based on available signals.
    The real algorithm uses GA views/sales. Without GA data, we approximate:
    - Camera bodies are most popular (flagship products)
    - Higher-priced products tend to be flagships with more traffic
    - New condition > used
    - Products with shorter, cleaner names are typically main products
    """
    score = 0.0
    name = (p.get("name") or "").lower()
    cat = (p.get("category") or "").lower()

    # Category-based popularity estimation
    if "aparat" in cat or "body" in name:
        score += 15  # cameras get most views
    elif "obiektyw" in cat and "pokrow" not in name and "osłon" not in name:
        score += 10  # lenses are second
    elif "dron" in cat:
        score += 10
    elif "lamp" in cat and ("studyjn" in cat or "reportersk" in cat):
        score += 8
    elif "gimbal" in cat or "stabiliz" in cat:
        score += 7
    elif "mikrofon" in cat:
        score += 6

    # Price as popularity proxy (flagships are expensive and popular)
    try:
        price = float(p.get("price", 0) or 0)
        if price > 10000:
            score += 8
        elif price > 5000:
            score += 5
        elif price > 2000:
            score += 3
        elif price > 500:
            score += 1
    except (ValueError, TypeError):
        pass

    # Penalty for outlet/used — these have much lower GA scores
    if is_outlet_or_used(p):
        score *= 0.3

    # Shorter names often = main products (not kits, not accessories for the product)
    name_len = len(p.get("name", ""))
    if name_len < 40:
        score += 2
    elif name_len > 80:
        score -= 1

    return score


def _kw_in_text(kw, text):
    """Check if keyword matches text (exact or prefix for Polish inflection)."""
    if kw in text:
        return True
    # Prefix match for Polish inflection: "lampa" matches "lampy", "obiektyw" matches "obiektywy"
    if len(kw) >= 4:
        prefix = kw[:max(4, len(kw) - 1)]
        if prefix in text:
            return True
    return False


def score_text_relevance(p, query_keywords, original_query):
    """
    Simulate Elasticsearch text scoring.

    Matches algorithm boosts:
    - Exact phrase in name (boost 25)
    - All terms AND match in name (boost 20)
    - Individual term matches in name/category
    - Prefix matching for Polish inflection (obiektyw→obiektywy, lampa→lampy)
    """
    name = normalize(p.get("name", ""))
    cat = normalize(p.get("category", ""))
    brand = normalize(p.get("brand", ""))
    combined = f"{name} {cat} {brand}"

    score = 0.0
    query_norm = normalize(original_query)

    # === Exact phrase match in name (highest boost) ===
    if query_norm and query_norm in name:
        score += 250  # simulates boost 25 on phrase match

    # === All keywords AND match ===
    if query_keywords:
        name_matches = sum(1 for kw in query_keywords if _kw_in_text(kw, name))
        combined_matches = sum(1 for kw in query_keywords if _kw_in_text(kw, combined))

        # All keywords found in name → strong boost
        if name_matches == len(query_keywords):
            score += 200  # simulates boost 20 AND match

        # All keywords found in combined text → decent boost
        elif combined_matches == len(query_keywords):
            score += 100

        # Partial matches — each keyword in name
        score += name_matches * 30
        # Keywords in category/brand (weaker)
        score += (combined_matches - name_matches) * 10

        # Bonus for keyword ratio (more keywords matched = better)
        if len(query_keywords) > 0:
            ratio = combined_matches / len(query_keywords)
            score += ratio * 50

    return score


def find_products_for_query(products, query, test, alias_map, known_brands):
    """Find best 5 products for a test query, simulating the search algorithm."""
    test_id = test["id"]
    expect = test.get("expect", {})
    q_lower = query.lower().strip()

    # --- EAN search (exact match, boost 50) ---
    if "ean" in test_id:
        ean = query.strip()
        return [p for p in products if p.get("ean") == ean][:5]

    # --- Extract condition filter ---
    condition_filter = None
    clean_q = q_lower
    for word in ["używany", "używane", "używanych", "używanego", "używana"]:
        if word in clean_q:
            condition_filter = "used"
            clean_q = clean_q.replace(word, "").strip()

    # --- Detect compatibility (do/dla/na/pod) ---
    is_compat = "compatibility" in test_id or bool(re.search(r'\b(do|dla|na|pod)\b', clean_q))

    # --- Detect and resolve brands ---
    words = clean_q.split()
    detected_brands = []
    search_words = []

    for w in words:
        if w in ("do", "dla", "na", "pod"):
            continue
        bl = w.lower()
        if bl in known_brands:
            detected_brands.append(known_brands[bl])
        elif bl in alias_map:
            detected_brands.append(alias_map[bl])
        else:
            search_words.append(w)

    # --- Brand filter logic (matches search_engine.py) ---
    brand_filter = None
    if len(detected_brands) == 1 and not is_compat and "two_brands" not in test_id:
        brand_filter = detected_brands[0]

    # --- Build search keywords for text matching ---
    # After brand filter, brand is removed from query (like in the real algorithm)
    query_keywords = [w.lower() for w in search_words if len(w) > 1]

    # For text scoring, use the cleaned query (without brand if filtered)
    text_query = " ".join(search_words).strip()
    if not text_query and brand_filter:
        # Brand-only query → match_all with brand filter
        text_query = ""

    # --- Filter candidates ---
    candidates = list(products)

    if condition_filter:
        candidates = [p for p in candidates if p.get("condition") == condition_filter]

    if brand_filter:
        candidates = [p for p in candidates
                      if (p.get("brand") or "").lower() == brand_filter.lower()]

    # --- Score and rank (simulating function_score) ---
    scored = []
    for p in candidates:
        # Text relevance score
        if query_keywords:
            text_score = score_text_relevance(p, query_keywords, text_query)
            if text_score == 0:
                continue  # No match at all — skip
        else:
            # Brand-only or condition-only query → match_all
            text_score = 1.0

        # Popularity multiplier: 1 + popularity * 0.15
        pop = estimate_popularity(p)
        popularity_multiplier = 1.0 + pop * 0.15

        # New > used penalty (for non-used queries)
        if not condition_filter and is_outlet_or_used(p):
            text_score *= 0.3  # outlet/used products get much lower text relevance

        # In-stock bonus
        if p.get("availability") == "in stock":
            text_score *= 1.1

        # Final score = text_relevance * popularity_multiplier
        final_score = text_score * popularity_multiplier

        scored.append((final_score, p))

    scored.sort(key=lambda x: -x[0])

    # For multi-brand results, ensure diversity
    if is_compat or "two_brands" in test_id or "top_10_brands_min_unique" in expect:
        result = []
        brands_seen = set()
        for score, p in scored:
            b = (p.get("brand") or "").lower()
            if len(brands_seen) < 2 and b in brands_seen and len(scored) > len(result) + 1:
                continue
            result.append(p)
            brands_seen.add(b)
            if len(result) >= 5:
                break
        if len(result) < 5:
            for score, p in scored:
                if p not in result:
                    result.append(p)
                if len(result) >= 5:
                    break
        return result[:5]

    return [p for _, p in scored[:5]]


def main():
    products = load_feed()

    known_brands = {}
    for p in products:
        b = p.get("brand", "")
        if b:
            known_brands[b.lower()] = b

    alias_map = load_aliases()

    with open(REGRESSION_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    tests = data["tests"]

    judgments = []
    print(f"\nGenerating judgments for {len(tests)} queries...")
    print("-" * 60)

    for test in tests:
        query = test["query"]
        test_id = test["id"]
        print(f"  [{test_id}] {query}...", end=" ", flush=True)

        best = find_products_for_query(products, query, test, alias_map, known_brands)

        expected = []
        for i, p in enumerate(best):
            expected.append({
                "id": p.get("id_verto", ""),
                "name": (p.get("name") or "")[:100],
                "ideal_position": i + 1,
                "weight": 3 if i == 0 else (2 if i < 3 else 1),
            })

        judgments.append({
            "id": test_id,
            "query": query,
            "description": test.get("description", ""),
            "expected_products": expected,
        })

        names = [f"{p.get('name', '?')[:45]} ({'used' if is_outlet_or_used(p) else 'new'})" for p in best[:2]]
        print(f"OK → {names}")

    output = {
        "_comment": "Search quality judgments — edit expected_products to define ideal rankings. "
                    "weight: 3=critical (top result), 2=important (top 3), 1=nice-to-have (top 5). "
                    "ideal_position: where this product should appear (1=first).",
        "generated": datetime.now().isoformat(),
        "judgments": judgments,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    total_products = sum(len(j["expected_products"]) for j in judgments)
    empty = [j["id"] for j in judgments if not j["expected_products"]]
    print(f"\n{'=' * 60}")
    print(f"Saved {len(judgments)} judgments ({total_products} products) to {OUTPUT_FILE}")
    if empty:
        print(f"WARNING: Empty judgments: {empty}")


if __name__ == "__main__":
    main()
