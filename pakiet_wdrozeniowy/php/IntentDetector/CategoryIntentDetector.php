<?php

declare(strict_types=1);

namespace App\Component\Elasticsearch\IntentDetector;

use FOS\ElasticaBundle\Finder\PaginatedFinderInterface;

/**
 * Detects category-intent in search queries.
 *
 * When user searches "lampa", "karta sd", "obiektyw" etc., this detects that
 * the user wants to browse a product category, not search for a specific product.
 *
 * Supports:
 * - CATEGORY_ALIASES: "lampa" → multiple lamp subcategories
 * - First-word alias: "klatka sony a7 iv" → cat=klatki, remainder="sony a7 iv"
 * - Exact subcategory match: "bezlusterkowce" → that subcategory
 * - Folded match: "staloogniskowe" → "stałoogniskowe" (Polish diacritics)
 * - Prefix match: "obiektywy stalo" → "obiektywy stałoogniskowe"
 */
final class CategoryIntentDetector
{
    /**
     * Maps common search terms to lists of ES subcategory values.
     * Keys must be lowercase. Values must match exact ES subcategory field values.
     *
     * Source: CyfroSearch prototype CATEGORY_ALIASES (validated against ES index).
     */
    private const CATEGORY_ALIASES = [
        // lampa (singular) → lamp subcategories
        'lampa' => ['lampy LED', 'lampy błyskowe', 'lampy studyjne',
            'lampy plenerowe (akumulatorowe)', 'lampy studyjne LED',
            'lampy panelowe LED', 'lampy pierścieniowe LED'],
        'lampy' => ['lampy LED', 'lampy błyskowe', 'lampy studyjne',
            'lampy plenerowe (akumulatorowe)', 'lampy studyjne LED',
            'lampy panelowe LED', 'lampy pierścieniowe LED'],
        // torba
        'torba' => ['torby fotograficzne', 'torby kufry i walizki'],
        // karta → memory cards
        'karta' => ['SD / SDHC / SDXC', 'CFexpress', 'microSD', 'SD / SDHC', 'CompactFlash'],
        'karty' => ['SD / SDHC / SDXC', 'CFexpress', 'microSD', 'SD / SDHC', 'CompactFlash'],
        // obiektyw → ALL lens subcategories
        'obiektyw' => ['obiektywy stałoogniskowe', 'obiektywy zmiennoogniskowe (zoom)',
            'obiektywy do lustrzanek', 'obiektywy do bezlusterkowców'],
        'obiektywy' => ['obiektywy stałoogniskowe', 'obiektywy zmiennoogniskowe (zoom)',
            'obiektywy do lustrzanek', 'obiektywy do bezlusterkowców'],
        // tło / tlo → backdrop subcategories
        'tlo' => ['tła kartonowe', 'tła składane', 'tła plastikowe',
            'tła materiałowe', 'tła winylowe', 'tła podświetlane',
            'systemy zawieszania teł'],
        'tło' => ['tła kartonowe', 'tła składane', 'tła plastikowe',
            'tła materiałowe', 'tła winylowe', 'tła podświetlane',
            'systemy zawieszania teł'],
        'tła' => ['tła kartonowe', 'tła składane', 'tła plastikowe',
            'tła materiałowe', 'tła winylowe', 'tła podświetlane',
            'systemy zawieszania teł'],
        'tla' => ['tła kartonowe', 'tła składane', 'tła plastikowe',
            'tła materiałowe', 'tła winylowe', 'tła podświetlane',
            'systemy zawieszania teł'],
        // Additional aliases
        'klatka' => ['klatki'],
        'klatki' => ['klatki', 'zestawy do foto-video'],
        'plecak' => ['plecaki fotograficzne'],
        'plecaki' => ['plecaki fotograficzne'],
        'plecak fotograficzny' => ['plecaki fotograficzne'],
        'statyw' => ['statywy (trójnogi)', 'statywy do filmowania'],
        'filtr' => ['filtry', 'połówkowe i szare'],
        'akumulator' => ['akumulatory i baterie'],
        'pasek' => ['paski', 'pasy biodrowe, szelki i kamizelki'],
        'paski' => ['paski', 'pasy biodrowe, szelki i kamizelki'],
        'karta sd' => ['SD / SDHC / SDXC', 'SD / SDHC'],
        'karty sd' => ['SD / SDHC / SDXC', 'SD / SDHC'],
        'karta pamięci' => ['SD / SDHC / SDXC', 'CFexpress', 'microSD', 'SD / SDHC', 'CompactFlash'],
        'karta cfexpress' => ['CFexpress', 'CFexpress Typ A', 'CFexpress Type B'],
        'karta microsd' => ['microSD'],
        'softbox' => ['softboxy', 'softboxy oktagonalne', 'softboxy prostokątne',
            'softboxy heksagonalne', 'softboxy paraboliczne', 'softboxy wideo', 'stripboxy'],
        'softboxy' => ['softboxy', 'softboxy oktagonalne', 'softboxy prostokątne',
            'softboxy heksagonalne', 'softboxy paraboliczne', 'softboxy wideo', 'stripboxy'],
        'gimbal' => ['gimbale', 'stabilizatory', 'systemy stabilizacji'],
        'gimbale' => ['gimbale', 'stabilizatory', 'systemy stabilizacji'],
        'statyw oswietleniowy' => ['statywy studyjne', 'statywy wolnostojące', 'statywy podłogowe (piesek)'],
        'statyw oświetleniowy' => ['statywy studyjne', 'statywy wolnostojące', 'statywy podłogowe (piesek)'],
        'mikrofon' => ['mikrofony', 'mikrofony bezprzewodowe', 'systemy bezprzewodowe'],
        'mikrofony' => ['mikrofony', 'mikrofony bezprzewodowe', 'systemy bezprzewodowe'],
        'mic' => ['mikrofony', 'mikrofony bezprzewodowe', 'systemy bezprzewodowe'],
        'lampa led' => ['lampy LED', 'lampy studyjne LED', 'lampy panelowe LED',
            'miecze świetlne LED', 'lampy pierścieniowe LED'],
        'lampy led' => ['lampy LED', 'lampy studyjne LED', 'lampy panelowe LED',
            'miecze świetlne LED', 'lampy pierścieniowe LED'],
        'instax' => ['kompakty z natychmiastowym wydrukiem', 'mobilne do fotografii natychmiastowej',
            'Instax / Polaroid'],
        'blenda' => ['blendy', 'mocowania do blend i paneli'],
        'blendy' => ['blendy', 'mocowania do blend i paneli'],
        'monopod' => ['statywy monopody'],
        'monopody' => ['statywy monopody'],
        'boom' => ['statywy typu boom'],
        'beauty dish' => ['beauty dish'],
        'strumienica' => ['strumienice'],
        'strumienice' => ['strumienice'],
        'monitor podgladowy' => ['Monitory podglądowe'],
        'monitor podglądowy' => ['Monitory podglądowe'],
        'monitory podgladowe' => ['Monitory podglądowe'],
        'monitory podglądowe' => ['Monitory podglądowe'],
        'karta cf express' => ['CFexpress', 'CFexpress Typ A', 'CFexpress Type B'],
        'hdmi' => ['HDMI'],
        'torba fotograficzna' => ['torby fotograficzne', 'torby kufry i walizki'],
        'torby fotograficzne' => ['torby fotograficzne', 'torby kufry i walizki'],
    ];

    /** Polish diacritics folding map */
    private const POLISH_FOLD_MAP = [
        'ą' => 'a', 'ć' => 'c', 'ę' => 'e', 'ł' => 'l', 'ń' => 'n',
        'ó' => 'o', 'ś' => 's', 'ź' => 'z', 'ż' => 'z',
        'Ą' => 'A', 'Ć' => 'C', 'Ę' => 'E', 'Ł' => 'L', 'Ń' => 'N',
        'Ó' => 'O', 'Ś' => 'S', 'Ź' => 'Z', 'Ż' => 'Z',
    ];

    /** @var array<string, string> lowered subcategory → original ES key */
    private array $subcategoryMap = [];

    /** @var array<string, string> folded_lower → lowered subcategory */
    private array $subcategoryFoldedMap = [];

    private bool $loaded = false;

    public function __construct(
        private PaginatedFinderInterface $productsFinder,
    ) {
    }

    /**
     * Detect category intent in query text.
     *
     * @param string $queryText Text to check for category intent.
     *                          When brand-intent is active, pass the remainder (after removing brand).
     */
    public function detect(string $queryText): ?CategoryIntent
    {
        $this->ensureLoaded();

        $qLower = mb_strtolower(trim($queryText));
        if ('' === $qLower) {
            return null;
        }

        $qFolded = $this->foldPolish($qLower);

        // Step 1: Full-query alias match
        if (isset(self::CATEGORY_ALIASES[$qLower])) {
            return new CategoryIntent(
                subcategories: self::CATEGORY_ALIASES[$qLower],
                remainder: '',
            );
        }
        if (isset(self::CATEGORY_ALIASES[$qFolded])) {
            return new CategoryIntent(
                subcategories: self::CATEGORY_ALIASES[$qFolded],
                remainder: '',
            );
        }

        // Step 2: First-word(s) alias match — "klatka sony a7 iv" → cat=klatki, remainder="sony a7 iv"
        // Also try first two words: "lampa led godox" → cat="lampa led", remainder="godox"
        $tokens = explode(' ', $qLower);
        foreach ([2, 1] as $nWords) {
            if ($nWords >= count($tokens)) {
                continue; // Only match if there are MORE words after the prefix
            }

            $prefix = implode(' ', array_slice($tokens, 0, $nWords));
            $prefixFolded = $this->foldPolish($prefix);
            $remainder = trim(implode(' ', array_slice($tokens, $nWords)));

            if (isset(self::CATEGORY_ALIASES[$prefix])) {
                return new CategoryIntent(
                    subcategories: self::CATEGORY_ALIASES[$prefix],
                    remainder: $remainder,
                );
            }
            if (isset(self::CATEGORY_ALIASES[$prefixFolded])) {
                return new CategoryIntent(
                    subcategories: self::CATEGORY_ALIASES[$prefixFolded],
                    remainder: $remainder,
                );
            }
        }

        // Step 3: Exact subcategory match from ES
        if (isset($this->subcategoryMap[$qLower])) {
            return new CategoryIntent(
                subcategories: [$this->subcategoryMap[$qLower]],
                remainder: '',
            );
        }

        // Step 4: Folded match (user types without Polish diacritics)
        if (isset($this->subcategoryFoldedMap[$qFolded])) {
            $originalLower = $this->subcategoryFoldedMap[$qFolded];
            return new CategoryIntent(
                subcategories: [$this->subcategoryMap[$originalLower]],
                remainder: '',
            );
        }

        // Step 5: Prefix match — both folded and unfolded
        if (mb_strlen($qFolded) >= 6) {
            $bestMatch = null;
            $bestLen = 0;

            foreach ($this->subcategoryFoldedMap as $foldedCat => $originalCat) {
                // Query is prefix of category: "obiektywy stalo" → "obiektywy stałoogniskowe"
                if (str_starts_with($foldedCat, $qFolded)) {
                    if (mb_strlen($originalCat) > $bestLen) {
                        $bestMatch = $originalCat;
                        $bestLen = mb_strlen($originalCat);
                    }
                }
                // Category is prefix of query: "bezlusterkowce" → "bezlusterkowce sony"
                if (mb_strlen($foldedCat) >= 6 && str_starts_with($qFolded, $foldedCat)) {
                    if (mb_strlen($originalCat) > $bestLen) {
                        $bestMatch = $originalCat;
                        $bestLen = mb_strlen($originalCat);
                    }
                }
            }

            if (null !== $bestMatch) {
                return new CategoryIntent(
                    subcategories: [$this->subcategoryMap[$bestMatch]],
                    remainder: '',
                );
            }
        }

        return null;
    }

    private function foldPolish(string $text): string
    {
        return strtr($text, self::POLISH_FOLD_MAP);
    }

    /**
     * Load subcategory names from ES index via aggregation.
     */
    private function ensureLoaded(): void
    {
        if ($this->loaded) {
            return;
        }

        $this->loaded = true;

        try {
            $this->loadSubcategoriesFromIndex();
        } catch (\Throwable) {
            // If loading fails, CATEGORY_ALIASES will still work
        }
    }

    private function loadSubcategoriesFromIndex(): void
    {
        try {
            $finder = $this->productsFinder;
            $searchable = $this->getSearchableFromFinder($finder);
            if (null === $searchable) {
                return;
            }

            $query = new \Elastica\Query();
            $query->setSize(0);

            // Aggregate on product_main_taxon_name fields (level 1 = child = most specific)
            // In Cyfrowe's schema: product_main_taxon_name_1_pl_pl is the lowest level
            $agg = new \Elastica\Aggregation\Terms('subcats');
            $agg->setField('product_main_taxon_name_1_pl_pl');
            $agg->setSize(500);
            $query->addAggregation($agg);

            $results = $searchable->search($query);
            $buckets = $results->getAggregation('subcats')['buckets'] ?? [];

            foreach ($buckets as $bucket) {
                if ($bucket['doc_count'] < 3) {
                    continue;
                }
                $originalKey = $bucket['key'];
                $keyLower = mb_strtolower($originalKey);

                $this->subcategoryMap[$keyLower] = $originalKey;

                $folded = $this->foldPolish($keyLower);
                $this->subcategoryFoldedMap[$folded] = $keyLower;
            }
        } catch (\Throwable) {
            // Silently fail
        }
    }

    private function getSearchableFromFinder(object $finder): ?\Elastica\SearchableInterface
    {
        try {
            $ref = new \ReflectionObject($finder);

            foreach (['finder', 'searchable', 'index'] as $prop) {
                if ($ref->hasProperty($prop)) {
                    $property = $ref->getProperty($prop);
                    $property->setAccessible(true);
                    $value = $property->getValue($finder);

                    if ($value instanceof \Elastica\SearchableInterface) {
                        return $value;
                    }

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
     * Manually register a subcategory (useful for testing).
     */
    public function registerSubcategory(string $subcategory): void
    {
        $lower = mb_strtolower($subcategory);
        $this->subcategoryMap[$lower] = $subcategory;
        $this->subcategoryFoldedMap[$this->foldPolish($lower)] = $lower;
        $this->loaded = true;
    }
}
