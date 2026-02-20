<?php

declare(strict_types=1);

namespace App\Component\Elasticsearch\IntentDetector;

/**
 * Value object representing a detected category intent.
 *
 * Example: query "lampa godox" → subcategories=["lampy LED","lampy blyskowe",...], remainder="godox"
 * Example: query "karta sd" → subcategories=["SD / SDHC / SDXC","SD / SDHC"], remainder=""
 */
final readonly class CategoryIntent
{
    /**
     * @param list<string> $subcategories ES subcategory values to filter on
     */
    public function __construct(
        /** List of ES subcategory values (product_main_taxon_name) to filter on */
        public array $subcategories,
        /** Remaining query text after stripping the category prefix (e.g. "godox" from "lampa godox") */
        public string $remainder,
    ) {
    }

    public function hasRemainder(): bool
    {
        return '' !== $this->remainder;
    }

    public function isSingleCategory(): bool
    {
        return 1 === count($this->subcategories);
    }
}
