"""
Regression test runner for the search engine.

CRITICAL: Run this BEFORE and AFTER every change to search_engine.py or es_mapping.py.
All tests must pass. A failing test after a change means the change breaks
the universal behavior guarantee and must be fixed or reverted.

Usage:
    python run_regression.py              # Run all tests
    python run_regression.py --verbose    # Show detailed results
    python run_regression.py --test brand_canon  # Run single test
"""

import json
import sys
import os
import argparse
from datetime import datetime

# Fix Windows console encoding
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from search_engine import search


def load_tests(path: str = "regression_tests.json") -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["tests"]


def check_expectation(test: dict, results: dict) -> list[str]:
    """
    Check if search results meet the test expectations.
    Returns list of failure messages (empty = pass).
    """
    failures = []
    expect = test["expect"]
    products = results["products"]
    total = results["total"]

    # min_results
    if "min_results" in expect:
        if total < expect["min_results"]:
            failures.append(f"Expected at least {expect['min_results']} results, got {total}")

    # first_result_id
    if "first_result_id" in expect:
        if not products or products[0].get("id_verto") != expect["first_result_id"]:
            actual = products[0].get("id_verto", "N/A") if products else "NO RESULTS"
            failures.append(f"Expected first result ID '{expect['first_result_id']}', got '{actual}'")

    # top_N_must_contain_brand
    if "top_5_must_contain_brand" in expect:
        brand = expect["top_5_must_contain_brand"].lower()
        top5 = products[:5]
        matches = [p for p in top5 if brand in (p.get("brand", "") or "").lower()]
        if len(matches) < 3:  # At least 3 of top 5 should match
            actual_brands = [p.get("brand", "?") for p in top5]
            failures.append(f"Expected brand '{brand}' in at least 3 of top 5, found {len(matches)}. Brands: {actual_brands}")

    if "top_3_must_contain_in_name" in expect:
        text = expect["top_3_must_contain_in_name"].lower()
        top3 = products[:3]
        matches = [p for p in top3 if text in (p.get("name", "") or "").lower()]
        if not matches:
            names = [p.get("name", "?") for p in top3]
            failures.append(f"Expected '{text}' in name of at least 1 of top 3. Names: {names}")

    if "top_5_must_contain_in_category" in expect:
        text = expect["top_5_must_contain_in_category"].lower()
        top5 = products[:5]
        matches = [p for p in top5 if text in (p.get("category", "") or "").lower()]
        if len(matches) < 2:
            cats = [p.get("category", "?") for p in top5]
            failures.append(f"Expected '{text}' in category of at least 2 of top 5. Categories: {cats}")

    if "top_10_must_contain_in_name_or_category" in expect:
        text = expect["top_10_must_contain_in_name_or_category"].lower()
        top10 = products[:10]
        matches = [p for p in top10 if text in (p.get("name", "") or "").lower() or text in (p.get("category", "") or "").lower()]
        if len(matches) < 3:
            failures.append(f"Expected '{text}' in name/category of at least 3 of top 10, found {len(matches)}")

    if "top_10_brands_min_unique" in expect:
        top10 = products[:10]
        brands = set(p.get("brand", "") for p in top10 if p.get("brand"))
        if len(brands) < expect["top_10_brands_min_unique"]:
            failures.append(f"Expected at least {expect['top_10_brands_min_unique']} unique brands in top 10, got {len(brands)}: {brands}")

    if "top_10_must_contain_brand_ci" in expect:
        text = expect["top_10_must_contain_brand_ci"].lower()
        top10 = products[:10]
        matches = [p for p in top10 if text in (p.get("brand", "") or "").lower()]
        if not matches:
            brands = [p.get("brand", "?") for p in top10]
            failures.append(f"Expected brand containing '{text}' in top 10. Brands: {brands}")

    if "top_5_majority_availability" in expect:
        avail = expect["top_5_majority_availability"]
        top5 = products[:5]
        matches = [p for p in top5 if p.get("availability") == avail]
        if len(matches) < 3:
            avails = [p.get("availability", "?") for p in top5]
            failures.append(f"Expected majority (3+) of top 5 to be '{avail}', got {len(matches)}. Availabilities: {avails}")

    # all_must_have_condition: every result must have this condition value
    if "all_must_have_condition" in expect:
        cond = expect["all_must_have_condition"]
        bad = [p for p in products if p.get("condition") != cond]
        if bad:
            failures.append(f"Expected all results condition='{cond}', but {len(bad)} have different: {[p.get('condition','?') for p in bad[:3]]}")

    # all_must_have_brand: every result must have this brand (exclusive filter)
    if "all_must_have_brand" in expect:
        brand = expect["all_must_have_brand"].lower()
        bad = [p for p in products if brand not in (p.get("brand", "") or "").lower()]
        if bad:
            failures.append(f"Expected all results brand='{brand}', but {len(bad)} have different: {[p.get('brand','?') for p in bad[:3]]}")

    # top_10_brands_min_unique: at least N unique brands in top 10 (for non-exclusive queries)
    # (already exists above, but adding alias for clarity)

    return failures


def run_tests(tests: list[dict], verbose: bool = False) -> tuple[int, int, list]:
    """Run all tests and return (passed, failed, details)."""
    passed = 0
    failed = 0
    details = []

    for test in tests:
        test_id = test["id"]
        query = test["query"]

        if verbose:
            print(f"\n{'='*60}")
            print(f"TEST: {test_id}")
            print(f"Query: '{query}'")
            print(f"Description: {test['description']}")

        try:
            results = search(query, page=1, size=24)
            failures = check_expectation(test, results)

            if failures:
                failed += 1
                status = "FAIL"
                if verbose:
                    for f in failures:
                        print(f"  ✗ {f}")
            else:
                passed += 1
                status = "PASS"
                if verbose:
                    print(f"  ✓ All expectations met ({results['total']} results)")

            details.append({
                "id": test_id,
                "status": status,
                "query": query,
                "total_results": results["total"],
                "failures": failures,
                "top_3": [
                    {"name": p.get("name", ""), "brand": p.get("brand", ""), "score": p.get("_score", 0)}
                    for p in results["products"][:3]
                ],
            })

        except Exception as e:
            failed += 1
            details.append({
                "id": test_id,
                "status": "ERROR",
                "query": query,
                "failures": [str(e)],
            })
            if verbose:
                print(f"  ✗ ERROR: {e}")

    return passed, failed, details


def main():
    parser = argparse.ArgumentParser(description="Run search regression tests")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--test", "-t", help="Run a specific test by ID")
    parser.add_argument("--output", "-o", help="Save results to JSON file")
    args = parser.parse_args()

    tests = load_tests()
    if args.test:
        tests = [t for t in tests if t["id"] == args.test]
        if not tests:
            print(f"Test '{args.test}' not found")
            sys.exit(1)

    print(f"Running {len(tests)} regression tests...")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("-" * 60)

    passed, failed, details = run_tests(tests, verbose=args.verbose)

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed out of {len(tests)} tests")

    if failed > 0:
        print("\nFAILED TESTS:")
        for d in details:
            if d["status"] != "PASS":
                print(f"  ✗ {d['id']}: {', '.join(d['failures'][:2])}")
        print("\n⚠ REGRESSION DETECTED — do not deploy this change!")
        sys.exit(1)
    else:
        print("\n✓ All tests passed — safe to proceed.")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "passed": passed,
                "failed": failed,
                "details": details,
            }, f, indent=2, ensure_ascii=False)
        print(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
