<?php

declare(strict_types=1);

namespace App\Component\Elasticsearch\IntentDetector;

/**
 * Orchestrates all intent detectors and query normalizations.
 *
 * Flow:
 * 1. Normalize model numbers ("a7iv" → "a7 iv")
 * 2. Merge "mark III" → "markIII"
 * 3. Detect brand intent ("fuji xt5" → brand=Fujifilm)
 * 4. Rewrite brand alias in query ("fuji" → "Fujifilm")
 * 5. Detect condition intent ("używany canon r5" → used=true, strip keyword)
 * 6. Detect category intent ("lampa godox" → cat=lampy, remainder="godox")
 * 7. Detect focal length intent ("24-70" → focal="24-70")
 * 8. Return composite SearchIntent
 */
final class SearchIntentResolver
{
    /** Regex: split digit→letter boundary: "a7iv" → "a7 iv" */
    private const RE_DIGIT_TO_ALPHA = '/(\d)([a-zA-Z])/';

    /** Regex: merge "mark III" → "markIII" to match ES analyzer */
    private const RE_MARK_ROMAN = '/\bmark\s+(i{1,4}v?|iv|v)\b/i';

    /** Regex: detect focal length ranges like "24-70", "70-200" */
    private const RE_FOCAL_LENGTH = '/\b(\d{2,3})\s*[-–]\s*(\d{2,3})\b/';

    /** Condition-intent keyword prefixes */
    private const USED_INTENT_PREFIXES = ['używan', 'uzywany', 'używany', 'uży', 'uzy'];

    /** Lens subcategories for focal-length intent */
    public const LENS_SUBCATS = [
        'obiektywy stałoogniskowe',
        'obiektywy zmiennoogniskowe (zoom)',
        'obiektywy do lustrzanek',
        'obiektywy do bezlusterkowców',
    ];

    /** Main product categories — boosted when brand-intent detected */
    public const MAIN_SUBCATS = [
        'aparaty cyfrowe',
        'bezlusterkowce',
        'kompakty',
        'lustrzanki',
        'obiektywy stałoogniskowe',
        'obiektywy zmiennoogniskowe (zoom)',
        'obiektywy do lustrzanek',
        'obiektywy do bezlusterkowców',
        'kamery cyfrowe',
        'kamery sportowe',
        'drony',
    ];

    public function __construct(
        private BrandIntentDetector $brandDetector,
        private CategoryIntentDetector $categoryDetector,
    ) {
    }

    public function resolve(string $query): SearchIntent
    {
        $originalQuery = $query;

        // Step 1: Normalize model numbers: "a7iv" → "a7 iv"
        $query = $this->normalizeModelQuery($query);

        // Step 2: Merge mark + roman: "mark III" → "markIII"
        $query = $this->mergeMarkRoman($query);

        $qLower = mb_strtolower(trim($query));

        // Step 3: Detect brand intent
        $brandIntent = $this->brandDetector->detect($query);

        // Step 4: Rewrite brand alias in query
        if (null !== $brandIntent && $brandIntent->wasAlias) {
            $query = $this->brandDetector->rewriteQuery($query, $brandIntent);
        }

        // Step 5: Detect condition intent ("używany" / "uzywany")
        $usedCondition = false;
        $queryForEs = $query;
        $words = explode(' ', $qLower);

        foreach ($words as $word) {
            foreach (self::USED_INTENT_PREFIXES as $prefix) {
                if (str_starts_with($word, $prefix)) {
                    $usedCondition = true;
                    break 2;
                }
            }
        }

        // Strip condition keyword from ES query (it would penalize products without "używany" in name)
        if ($usedCondition) {
            $cleanWords = [];
            foreach (explode(' ', $query) as $w) {
                $keep = true;
                foreach (self::USED_INTENT_PREFIXES as $prefix) {
                    if (str_starts_with(mb_strtolower($w), $prefix)) {
                        $keep = false;
                        break;
                    }
                }
                if ($keep) {
                    $cleanWords[] = $w;
                }
            }
            $queryForEs = trim(implode(' ', $cleanWords));
            if ('' === $queryForEs) {
                $queryForEs = $query; // fallback if entire query was the condition keyword
            }
        }

        // Step 6: Detect category intent
        // When brand-intent is active, check the REMAINDER for category aliases
        // e.g. "peak design paski" → brand=Peak Design, check "paski" for category
        $categoryCheckText = null !== $brandIntent ? $brandIntent->remainder : $qLower;
        $categoryIntent = !empty($categoryCheckText) ? $this->categoryDetector->detect($categoryCheckText) : null;

        // Step 7: Detect focal length intent (only when no category intent)
        $focalLength = null;
        if (null === $categoryIntent) {
            if (preg_match(self::RE_FOCAL_LENGTH, $qLower, $focalMatch)) {
                $focalLength = $focalMatch[1] . '-' . $focalMatch[2];
            }
        }

        return new SearchIntent(
            originalQuery: $originalQuery,
            normalizedQuery: $queryForEs,
            brand: $brandIntent,
            category: $categoryIntent,
            focalLength: $focalLength,
            usedCondition: $usedCondition,
        );
    }

    /**
     * Insert space at digit→letter boundaries: "a7iv" → "a7 iv"
     */
    private function normalizeModelQuery(string $query): string
    {
        return preg_replace(self::RE_DIGIT_TO_ALPHA, '$1 $2', $query) ?? $query;
    }

    /**
     * Merge "mark III" → "markIII" to match ES analyzer tokenization.
     */
    private function mergeMarkRoman(string $query): string
    {
        return preg_replace_callback(
            self::RE_MARK_ROMAN,
            static fn (array $m) => 'mark' . $m[1],
            $query,
        ) ?? $query;
    }
}
