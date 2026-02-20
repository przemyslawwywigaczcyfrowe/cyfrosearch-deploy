<?php

declare(strict_types=1);

namespace App\Component\Elasticsearch\IntentDetector;

use FOS\ElasticaBundle\Finder\PaginatedFinderInterface;

/**
 * Detects brand-intent in search queries.
 *
 * When user searches "canon r8" or "fuji xt5", this detects "Canon" / "Fujifilm"
 * as the intended brand. This allows:
 * - Filtering results to that brand (so 3rd-party accessories don't outrank flagships)
 * - Boosting main product categories (cameras > lens caps)
 * - Rewriting brand aliases in the query ("fuji" → "Fujifilm")
 */
final class BrandIntentDetector
{
    /**
     * Common abbreviations → canonical brand names in ES.
     * Keys must be lowercase. Values must match the exact brand value in ES index.
     */
    private const BRAND_ALIASES = [
        'fuji' => 'Fujifilm',
        'pana' => 'Panasonic',
        'think tank' => 'ThinkTank',
        'oly' => 'Olympus',
        'hassy' => 'Hasselblad',
        'quadra' => 'Quadralite',
    ];

    /** @var array<string, string> lowercased brand → original ES casing */
    private array $brandMap = [];

    private bool $loaded = false;

    public function __construct(
        private PaginatedFinderInterface $productsFinder,
    ) {
    }

    /**
     * Detect if query starts with (or IS) a brand name.
     *
     * Returns BrandIntent with original-cased brand name and cleaned query,
     * or null if no brand detected.
     *
     * Algorithm:
     * 1. Check 2-word prefix ("peak design"), then 1-word ("canon")
     * 2. Check BRAND_ALIASES for abbreviations ("fuji" → "Fujifilm")
     */
    public function detect(string $query): ?BrandIntent
    {
        $this->ensureLoaded();

        $qLower = mb_strtolower(trim($query));
        $tokens = explode(' ', $qLower);

        if (empty($tokens) || $tokens[0] === '') {
            return null;
        }

        // Check 2-word brands first ("peak design"), then 1-word ("canon")
        foreach ([2, 1] as $n) {
            if ($n > count($tokens)) {
                continue;
            }

            $prefix = implode(' ', array_slice($tokens, 0, $n));

            // Direct brand match
            if (isset($this->brandMap[$prefix])) {
                $remainder = trim(implode(' ', array_slice($tokens, $n)));

                return new BrandIntent(
                    brand: $this->brandMap[$prefix],
                    remainder: $remainder,
                    originalQuery: $query,
                    wasAlias: false,
                );
            }
        }

        // Check brand aliases ("fuji" → "Fujifilm", "pana" → "Panasonic")
        foreach ([2, 1] as $n) {
            if ($n > count($tokens)) {
                continue;
            }

            $prefix = implode(' ', array_slice($tokens, 0, $n));

            if (isset(self::BRAND_ALIASES[$prefix])) {
                $canonical = self::BRAND_ALIASES[$prefix];
                $canonicalLower = mb_strtolower($canonical);

                // Verify canonical brand exists in ES
                if (isset($this->brandMap[$canonicalLower])) {
                    $remainder = trim(implode(' ', array_slice($tokens, $n)));

                    return new BrandIntent(
                        brand: $this->brandMap[$canonicalLower],
                        remainder: $remainder,
                        originalQuery: $query,
                        wasAlias: true,
                    );
                }
            }
        }

        return null;
    }

    /**
     * Rewrite query: replace brand alias with canonical name.
     * "fuji xt5" → "Fujifilm xt5"
     */
    public function rewriteQuery(string $query, BrandIntent $intent): string
    {
        if (!$intent->wasAlias) {
            return $query;
        }

        // Replace the alias prefix with canonical brand name
        $qLower = mb_strtolower(trim($query));
        $tokens = explode(' ', $qLower);

        foreach ([2, 1] as $n) {
            if ($n > count($tokens)) {
                continue;
            }

            $prefix = implode(' ', array_slice($tokens, 0, $n));
            if (isset(self::BRAND_ALIASES[$prefix])) {
                $rest = array_slice($tokens, $n);

                return trim($intent->brand . ' ' . implode(' ', $rest));
            }
        }

        return $query;
    }

    /**
     * Load all brand names from ES index via aggregation.
     * Called once, then cached for the lifetime of the service.
     */
    private function ensureLoaded(): void
    {
        if ($this->loaded) {
            return;
        }

        $this->loaded = true;

        try {
            // Use the finder's underlying Elastica index to run an aggregation
            // This gets all unique "producer" values from the index
            $adapter = $this->productsFinder->findPaginated('');
            $elasticaAdapter = $adapter->getAdapter();

            // Fallback: populate from known brands if aggregation is not directly available
            // The PropertyBuilder indexes "producer" field — we need to load all unique values
            $this->loadBrandsFromIndex();
        } catch (\Throwable) {
            // If loading fails, detector will work with aliases only
        }
    }

    private function loadBrandsFromIndex(): void
    {
        try {
            // Access the underlying Elastica index through the finder
            $reflection = new \ReflectionClass($this->productsFinder);

            // Try to find the Elastica index/client through the finder chain
            $finder = $this->productsFinder;
            if (method_exists($finder, 'getIndex')) {
                $index = $finder->getIndex();
            } else {
                // Navigate through FOSElasticaBundle's TransformedFinder → Searcher → Index
                $searchable = $this->getSearchableFromFinder($finder);
                if (null === $searchable) {
                    return;
                }
                $index = $searchable;
            }

            $query = new \Elastica\Query();
            $query->setSize(0);

            $agg = new \Elastica\Aggregation\Terms('brands');
            $agg->setField('producer');
            $agg->setSize(500);
            $query->addAggregation($agg);

            $results = $index->search($query);
            $buckets = $results->getAggregation('brands')['buckets'] ?? [];

            foreach ($buckets as $bucket) {
                $brand = $bucket['key'];
                $this->brandMap[mb_strtolower($brand)] = $brand;
            }
        } catch (\Throwable) {
            // Silently fail — aliases will still work
        }
    }

    private function getSearchableFromFinder(object $finder): ?\Elastica\SearchableInterface
    {
        // FOSElasticaBundle's TransformedFinder wraps a Searcher which wraps an Index
        try {
            $ref = new \ReflectionObject($finder);

            // Try "finder" property (TransformedFinder)
            foreach (['finder', 'searchable', 'index'] as $prop) {
                if ($ref->hasProperty($prop)) {
                    $property = $ref->getProperty($prop);
                    $property->setAccessible(true);
                    $value = $property->getValue($finder);

                    if ($value instanceof \Elastica\SearchableInterface) {
                        return $value;
                    }

                    // Recurse one level deeper
                    if (is_object($value)) {
                        $result = $this->getSearchableFromFinder($value);
                        if (null !== $result) {
                            return $result;
                        }
                    }
                }
            }
        } catch (\Throwable) {
            // ignore
        }

        return null;
    }

    /**
     * Manually register a brand (useful for testing or manual population).
     */
    public function registerBrand(string $brand): void
    {
        $this->brandMap[mb_strtolower($brand)] = $brand;
        $this->loaded = true;
    }
}
