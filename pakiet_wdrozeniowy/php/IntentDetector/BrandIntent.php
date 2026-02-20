<?php

declare(strict_types=1);

namespace App\Component\Elasticsearch\IntentDetector;

/**
 * Value object representing a detected brand intent.
 *
 * Example: query "fuji xt5" → brand="Fujifilm", remainder="xt5", wasAlias=true
 */
final readonly class BrandIntent
{
    public function __construct(
        /** Canonical brand name as stored in ES (e.g. "Canon", "Fujifilm", "Peak Design") */
        public string $brand,
        /** Query text after removing the brand prefix (e.g. "r8", "xt5", "paski") */
        public string $remainder,
        /** Original user query before any rewriting */
        public string $originalQuery,
        /** Whether the brand was detected via an alias (e.g. "fuji" → "Fujifilm") */
        public bool $wasAlias,
    ) {
    }

    public function hasRemainder(): bool
    {
        return '' !== $this->remainder;
    }
}
