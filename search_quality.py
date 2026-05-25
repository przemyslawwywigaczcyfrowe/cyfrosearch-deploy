"""
Search Quality Score (SQS) — objective measure of search ranking quality.

Produces a single percentage (0-100%) indicating how well search results
match expected product rankings. Higher = better.

Usage:
    python search_quality.py --baseline                          # baseline via local ES
    python search_quality.py --baseline --api-url http://...     # baseline via remote API
    python search_quality.py                                     # evaluate and show SQS
    python search_quality.py --verbose                           # per-query breakdown
    python search_quality.py --query brand_canon                 # single query

Workflow:
    1. Run --baseline to capture current state as the starting point
    2. Review search_quality_judgments.json — adjust expected products if needed
    3. After any algorithm change, run again to see if SQS went up or down
"""

import json
import sys
import os
import argparse
from datetime import datetime
from urllib.request import urlopen
from urllib.parse import urlencode, quote

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

JUDGMENTS_FILE = "search_quality_judgments.json"
HISTORY_FILE = "search_quality_history.json"
REGRESSION_FILE = "regression_tests.json"

# Global search function — set in main() based on --api-url flag
_search_fn = None


def _local_search(query, page=1, size=10):
    """Search via local Elasticsearch (requires ES credentials)."""
    from search_engine import search
    return search(query, page=page, size=size)


def _make_api_search(api_url):
    """Create a search function that queries a remote API endpoint."""
    def api_search(query, page=1, size=10):
        params = urlencode({"q": query, "page": page, "size": size})
        url = f"{api_url.rstrip('/')}/api/search?{params}"
        with urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    return api_search


def do_search(query, page=1, size=10):
    """Execute search using the configured search function."""
    return _search_fn(query, page=page, size=size)


# =============================================================================
# Scoring
# =============================================================================

def score_position(ideal_pos, actual_pos):
    """
    Score how close actual position is to ideal. Returns 0.0 - 1.0.

    Scoring table:
        Distance 0 (exact match):  100%
        Distance 1:                 85%
        Distance 2:                 70%
        Distance 3:                 55%
        Distance 4-5:               30%
        Distance 6-9:               10%
        Not found in top 10:         0%
    """
    if actual_pos is None:
        return 0.0

    distance = abs(actual_pos - ideal_pos)

    if distance == 0:
        return 1.0
    elif distance == 1:
        return 0.85
    elif distance == 2:
        return 0.70
    elif distance == 3:
        return 0.55
    elif distance <= 5:
        return 0.30
    else:
        return 0.10


def evaluate_query(judgment):
    """Run a query and score its results against expected products."""
    query = judgment["query"]
    expected = judgment["expected_products"]

    results = do_search(query, page=1, size=10)
    products = results.get("products", [])

    # Map product IDs to their positions (1-based)
    position_map = {}
    for i, p in enumerate(products):
        pid = p.get("id_verto", "")
        if pid and pid not in position_map:
            position_map[pid] = i + 1

    total_earned = 0.0
    total_max = 0.0
    product_details = []

    for exp in expected:
        weight = exp.get("weight", 2)
        ideal_pos = exp["ideal_position"]
        pid = exp["id"]

        actual_pos = position_map.get(pid)
        pos_score = score_position(ideal_pos, actual_pos)
        earned = weight * pos_score

        total_earned += earned
        total_max += weight

        product_details.append({
            "id": pid,
            "name": exp.get("name", ""),
            "ideal_position": ideal_pos,
            "actual_position": actual_pos,
            "weight": weight,
            "score_pct": round(pos_score * 100),
            "earned": round(earned, 2),
        })

    query_pct = (total_earned / total_max * 100) if total_max > 0 else 0

    return {
        "id": judgment.get("id", ""),
        "query": query,
        "score_pct": round(query_pct, 1),
        "earned": round(total_earned, 2),
        "max": round(total_max, 2),
        "products": product_details,
        "actual_top3": [
            {"name": p.get("name", "")[:80], "id": p.get("id_verto", "")}
            for p in products[:3]
        ],
    }


# =============================================================================
# Baseline generation
# =============================================================================

def generate_baseline():
    """Generate initial judgments from current search results."""
    with open(REGRESSION_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    tests = data["tests"]
    judgments = []

    print(f"Generating baseline from {len(tests)} regression test queries...")
    print("-" * 60)

    for test in tests:
        query = test["query"]
        print(f"  {query}...", end=" ", flush=True)

        try:
            results = do_search(query, page=1, size=10)
            products = results.get("products", [])

            expected = []
            for i, p in enumerate(products[:5]):
                expected.append({
                    "id": p.get("id_verto", ""),
                    "name": p.get("name", "")[:100],
                    "ideal_position": i + 1,
                    "weight": 3 if i == 0 else (2 if i < 3 else 1),
                })

            judgments.append({
                "id": test["id"],
                "query": query,
                "description": test.get("description", ""),
                "expected_products": expected,
            })
            print(f"OK ({results.get('total', 0)} results, {len(expected)} judged)")

        except Exception as e:
            print(f"ERROR: {e}")
            judgments.append({
                "id": test["id"],
                "query": query,
                "description": test.get("description", ""),
                "expected_products": [],
            })

    output = {
        "_comment": "Search quality judgments — edit expected_products to define ideal rankings. "
                    "weight: 3=critical (top result), 2=important (top 3), 1=nice-to-have (top 5). "
                    "ideal_position: where this product should appear (1=first).",
        "generated": datetime.now().isoformat(),
        "judgments": judgments,
    }

    with open(JUDGMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    print(f"Saved {len(judgments)} judgments to {JUDGMENTS_FILE}")
    print(f"\nNext steps:")
    print(f"  1. Review {JUDGMENTS_FILE} — adjust products/positions if needed")
    print(f"  2. Run: python search_quality.py")
    print(f"  3. After algorithm changes, run again to compare SQS")


# =============================================================================
# History
# =============================================================================

def load_judgments(path=JUDGMENTS_FILE):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_history(entry):
    history = load_history()
    history.append(entry)
    history = history[-100:]  # keep last 100
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


# =============================================================================
# Main evaluation
# =============================================================================

def run_evaluation(judgments, verbose=False):
    """Evaluate all queries and return (sqs, results_list)."""
    results = []

    for j in judgments:
        if not j.get("expected_products"):
            continue
        try:
            result = evaluate_query(j)
            results.append(result)
        except Exception as e:
            print(f"  ERROR on '{j['query']}': {e}")
            max_pts = sum(p.get("weight", 2) for p in j["expected_products"])
            results.append({
                "id": j.get("id", ""),
                "query": j["query"],
                "score_pct": 0,
                "earned": 0,
                "max": max_pts,
                "products": [],
                "error": str(e),
            })

    total_earned = sum(r["earned"] for r in results)
    total_max = sum(r["max"] for r in results)
    sqs = (total_earned / total_max * 100) if total_max > 0 else 0

    return round(sqs, 1), results


def print_report(sqs, results, verbose=False):
    """Print the SQS report."""
    total_earned = sum(r["earned"] for r in results)
    total_max = sum(r["max"] for r in results)

    perfect = [r for r in results if r["score_pct"] >= 95]
    good = [r for r in results if 70 <= r["score_pct"] < 95]
    degraded = [r for r in results if 40 <= r["score_pct"] < 70]
    poor = [r for r in results if r["score_pct"] < 40]

    print(f"\n{'=' * 60}")
    print(f"  SQS: {sqs:.1f}%  ({total_earned:.1f} / {total_max:.1f} points)")
    print(f"{'=' * 60}")
    print(f"  Perfect (95-100%):  {len(perfect):3d} queries")
    print(f"  Good    (70-94%):   {len(good):3d} queries")
    print(f"  Degraded (40-69%):  {len(degraded):3d} queries")
    print(f"  Poor    (<40%):     {len(poor):3d} queries")

    # Show degraded and poor queries
    trouble = sorted(degraded + poor, key=lambda x: x["score_pct"])
    if trouble:
        print(f"\n  Queries needing attention:")
        for r in trouble:
            print(f"    {r['score_pct']:5.1f}%  [{r['id']}] \"{r['query']}\"")
            if verbose:
                for p in r.get("products", []):
                    pos_str = f"pos {p['actual_position']}" if p["actual_position"] else "NOT FOUND"
                    indicator = "✓" if p["score_pct"] >= 85 else ("~" if p["score_pct"] >= 55 else "✗")
                    print(f"           {indicator} {p['name'][:50]:50s}  expected={p['ideal_position']} actual={pos_str}")

    # Show all queries in verbose mode
    if verbose:
        print(f"\n  All queries (sorted by score):")
        for r in sorted(results, key=lambda x: x["score_pct"]):
            print(f"    {r['score_pct']:5.1f}%  [{r['id']}] \"{r['query']}\"")
            for p in r.get("products", []):
                pos_str = f"pos {p['actual_position']}" if p["actual_position"] else "NOT FOUND"
                indicator = "✓" if p["score_pct"] >= 85 else ("~" if p["score_pct"] >= 55 else "✗")
                print(f"           {indicator} {p['name'][:50]:50s}  expected={p['ideal_position']} actual={pos_str}")

    # History
    history = load_history()
    if len(history) > 1:
        print(f"\n  History (last 5 runs):")
        for h in history[-5:]:
            ts = h["timestamp"][:16].replace("T", " ")
            print(f"    {ts}  SQS: {h['sqs']:5.1f}%  ({h['perfect']}P {h['good']}G {h['degraded']}D {h['poor']}F)")

        prev_sqs = history[-2]["sqs"]
        diff = sqs - prev_sqs
        if diff > 0:
            print(f"\n  Trend: ↑ +{diff:.1f}pp vs previous run")
        elif diff < 0:
            print(f"\n  Trend: ↓ {diff:.1f}pp vs previous run")
        else:
            print(f"\n  Trend: = no change vs previous run")

    print()

    return len(perfect), len(good), len(degraded), len(poor)


def main():
    global _search_fn

    parser = argparse.ArgumentParser(
        description="Search Quality Score (SQS) — measures search ranking quality as a single percentage."
    )
    parser.add_argument("--baseline", action="store_true",
                        help="Generate initial judgments from current search results")
    parser.add_argument("--api-url",
                        help="Use remote API instead of local ES (e.g. https://myapp.onrender.com)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show per-query and per-product details")
    parser.add_argument("--query", "-q",
                        help="Evaluate a single query by test ID")
    parser.add_argument("--no-history", action="store_true",
                        help="Don't save this run to history")
    args = parser.parse_args()

    # Configure search function
    if args.api_url:
        print(f"Using remote API: {args.api_url}")
        _search_fn = _make_api_search(args.api_url)
    else:
        _search_fn = _local_search

    if args.baseline:
        generate_baseline()
        return

    if not os.path.exists(JUDGMENTS_FILE):
        print(f"No judgments file found.")
        print(f"Generate one from current results first:")
        print(f"  python search_quality.py --baseline")
        print(f"  python search_quality.py --baseline --api-url https://your-app.onrender.com")
        sys.exit(1)

    data = load_judgments()
    judgments = data["judgments"]

    if args.query:
        judgments = [j for j in judgments if j["id"] == args.query]
        if not judgments:
            print(f"Query '{args.query}' not found in judgments.")
            sys.exit(1)

    print(f"Search Quality Score (SQS)")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Evaluating {len(judgments)} queries...")

    sqs, results = run_evaluation(judgments, verbose=args.verbose)
    perfect, good, degraded, poor = print_report(sqs, results, verbose=args.verbose)

    # Save to history
    if not args.no_history and not args.query:
        save_history({
            "timestamp": datetime.now().isoformat(),
            "sqs": sqs,
            "total_queries": len(results),
            "perfect": perfect,
            "good": good,
            "degraded": degraded,
            "poor": poor,
        })

    # Exit code for CI integration
    if sqs < 50:
        print("⚠ CRITICAL: SQS below 50% — do not deploy!")
        sys.exit(2)
    elif poor:
        print(f"⚠ WARNING: {poor} queries scoring below 40%")
        sys.exit(1)
    else:
        print("✓ Search quality acceptable.")


if __name__ == "__main__":
    main()
