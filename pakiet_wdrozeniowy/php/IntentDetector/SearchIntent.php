<?php

declare(strict_types=1);

namespace App\Component\Elasticsearch\IntentDetector;

/**
 * Composite value object holding ALL detected intents for a search query.
 *
 * Built by SearchIntentResolver by combining results from all detectors.
 * Passed to QueryBuilders to construct intent-aware ES queries.
 */
final readonly class SearchIntent
{
    public function __construct(
        /** Original user query */
        public string $originalQuery,
        /** Normalized query for ES text matching (model numbers split, marks merged, aliases rewritten) */
        public string $normalizedQuery,
        /** Detected brand intent, or null */
        public ?BrandIntent $brand = null,
        /** Detected category intent, or null */
        public ?CategoryIntent $category = null,
        /** Detected focal length range (e.g. "24-70"), or null */
        public ?string $focalLength = null,
        /** Whether "używany"/"used" intent was detected */
        public bool $usedCondition = false,
    ) {
    }

    public function hasBrandIntent(): bool
    {
        return null !== $this->brand;
    }

    public function hasCategoryIntent(): bool
    {
        return null !== $this->category;
    }

    public function hasFocalLengthIntent(): bool
    {
        return null !== $this->focalLength;
    }

    /**
     * Get the query text to use for ES text matching.
     * - For pure category browse: empty string (use constant_score)
     * - For category + remainder: the remainder text
     * - For brand + no remainder: empty string (brand filter only)
     * - Otherwise: the normalized query
     */
    public function getTextQuery(): string
    {
        if ($this->hasCategoryIntent()) {
            return $this->category->remainder;
        }

        return $this->normalizedQuery;
    }
}
