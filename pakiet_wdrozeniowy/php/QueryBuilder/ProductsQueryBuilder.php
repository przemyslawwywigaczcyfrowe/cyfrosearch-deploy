<?php

declare(strict_types=1);

namespace App\Component\Elasticsearch\QueryBuilder;

use App\Component\Elasticsearch\IntentDetector\SearchIntent;
use App\Component\Elasticsearch\IntentDetector\SearchIntentResolver;
use App\Component\Product\Entity\ProductStateInterface;
use BitBag\SyliusElasticsearchPlugin\QueryBuilder\QueryBuilderInterface;
use Elastica\Query\AbstractQuery;
use Elastica\Query\BoolQuery;
use Elastica\Query\ConstantScore;
use Elastica\Query\MatchPhrase;
use Elastica\Query\MatchPhrasePrefix;
use Elastica\Query\MultiMatch;
use Elastica\Query\Term;
use Elastica\Query\Terms;
use Webmozart\Assert\Assert;

/**
 * Main search query builder — intent-aware, with match_phrase and exact code matching.
 *
 * Replaces simple MultiMatch with 3 query variants:
 * 1. Category-intent: constant_score filter (or filter + multi_match for remainder text)
 * 2. Standard text search: bool.should with multi_match + match_phrase + match_phrase_prefix + exact codes
 * 3. Brand-intent modifies both: adds brand filter, adjusts phrase boosts
 *
 * The SearchIntent is resolved here and stored for access by FunctionScoreProductsQueryBuilder.
 */
final class ProductsQueryBuilder implements QueryBuilderInterface
{
    private ?SearchIntent $lastIntent = null;

    public function __construct(
        private QueryBuilderInterface $isEnabledQueryBuilder,
        private QueryBuilderInterface $hasChannelQueryBuilder,
        private SearchIntentResolver $intentResolver,
        private string $fuziness,
        private string $propertyState,
        private int $nameBooster,
        private int $descriptionBooster,
        private int $producerBooster,
        private int $mainTaxonBooster,
        private int $subTaxonBooster,
        private int $childTaxonBooster,
    ) {
    }

    public function buildQuery(array $data): ?AbstractQuery
    {
        $query = $data['name'] ?? $data['query'] ?? '';

        // Resolve all intents for this query
        $intent = $this->intentResolver->resolve($query);
        $this->lastIntent = $intent;

        $boolQuery = new BoolQuery();

        // Standard filters: enabled, channel, visible state
        $isEnabledQuery = $this->isEnabledQueryBuilder->buildQuery([]);
        $hasChannelQuery = $this->hasChannelQueryBuilder->buildQuery([]);
        Assert::notNull($isEnabledQuery);
        Assert::notNull($hasChannelQuery);

        $boolQuery->addMust($isEnabledQuery);
        $boolQuery->addMust($hasChannelQuery);

        $stateQuery = new Terms($this->propertyState, ProductStateInterface::STATES_VISIBLE_ON_LISTINGS_AND_SEARCHING);
        $boolQuery->addMust($stateQuery);

        // Build the search query based on detected intents
        if ($intent->hasCategoryIntent()) {
            $this->buildCategoryIntentQuery($boolQuery, $intent);
        } else {
            $this->buildTextMatchingQuery($boolQuery, $intent);
        }

        return $boolQuery;
    }

    /**
     * Get the last resolved SearchIntent (for use by FunctionScoreProductsQueryBuilder).
     */
    public function getLastIntent(): ?SearchIntent
    {
        return $this->lastIntent;
    }

    /**
     * Category-intent query: filter to subcategories.
     *
     * - Pure category browse ("lampa") → constant_score (BM25 doesn't affect ranking)
     * - Category + remainder ("klatka sony a7 iv") → filter + multi_match on remainder
     * - Brand + category ("peak design paski") → brand filter + category filter
     */
    private function buildCategoryIntentQuery(BoolQuery $boolQuery, SearchIntent $intent): void
    {
        $catIntent = $intent->category;
        Assert::notNull($catIntent);

        // Build subcategory filter
        if ($catIntent->isSingleCategory()) {
            $subcatFilter = new Term(['product_main_taxon_name_1_pl_pl' => $catIntent->subcategories[0]]);
        } else {
            $subcatFilter = new Terms('product_main_taxon_name_1_pl_pl', $catIntent->subcategories);
        }

        $boolQuery->addFilter($subcatFilter);

        // Add brand filter if brand-intent is active
        if ($intent->hasBrandIntent()) {
            $boolQuery->addFilter(new Term(['producer' => $intent->brand->brand]));
        }

        if ($catIntent->hasRemainder()) {
            // Category + additional text: use multi_match within the category filter
            $multiMatch = new MultiMatch();
            $multiMatch->setQuery($catIntent->remainder);
            $multiMatch->setFields([
                sprintf('name_pl_pl^%d', $this->nameBooster),
                sprintf('producer^%d', $this->producerBooster),
            ]);
            $multiMatch->setFuzziness('AUTO');
            $multiMatch->setParam('prefix_length', 2);
            $multiMatch->setParam('minimum_should_match', '70%');

            $boolQuery->addMust($multiMatch);
        } else {
            // Pure category browse — add constant_score so BM25 doesn't affect ranking
            // FunctionScore will handle ranking by popularity
            $boolQuery->addMust(new \Elastica\Query\MatchAll());
        }
    }

    /**
     * Standard text-matching query: bool.should with multiple strategies.
     *
     * Combines:
     * 1. multi_match on name, producer, taxon fields (fuzziness for typos)
     * 2. match_phrase with slop=2 (exact phrase proximity)
     * 3. match_phrase_prefix (autocomplete-like matching)
     * 4. Exact term matches on product codes (EAN, SKU, ERP ID, manufacturer_code)
     * 5. Brand filter (when brand-intent detected)
     * 6. Focal-length filter (when focal range detected, e.g. "24-70")
     */
    private function buildTextMatchingQuery(BoolQuery $boolQuery, SearchIntent $intent): void
    {
        $queryText = $intent->normalizedQuery;
        $queryTrimmed = trim($queryText);
        $queryUpper = mb_strtoupper($queryTrimmed);

        // When brand-intent detected, reduce phrase boosts so function_score signals dominate
        $phraseBoost = $intent->hasBrandIntent() ? 10 : 50;
        $phrasePrefixBoost = $intent->hasBrandIntent() ? 2 : 5;

        $shouldQuery = new BoolQuery();

        // 1. MultiMatch on text fields
        $multiMatch = new MultiMatch();
        $multiMatch->setQuery($queryText);
        $multiMatch->setType('best_fields');
        $multiMatch->setFields([
            sprintf('name_pl_pl^%d', $this->nameBooster),
            sprintf('description_pl_pl^%d', $this->descriptionBooster),
            sprintf('producer^%d', $this->producerBooster),
            sprintf('product_main_taxon_name_3_pl_pl^%d', $this->mainTaxonBooster),
            sprintf('product_main_taxon_name_2_pl_pl^%d', $this->subTaxonBooster),
            sprintf('product_main_taxon_name_1_pl_pl^%d', $this->childTaxonBooster),
            'ean^6',
        ]);
        $multiMatch->setFuzziness($this->fuziness);
        $multiMatch->setParam('prefix_length', 2);
        $multiMatch->setParam('minimum_should_match', '70%');
        $shouldQuery->addShould($multiMatch);

        // 2. match_phrase with slop=2 — rewards exact phrase order
        // "canon r8" scores higher when "Canon" and "R8" are adjacent
        $matchPhrase = new MatchPhrase('name_pl_pl', [
            'query' => $queryText,
            'boost' => $phraseBoost,
            'slop' => 2,
        ]);
        $shouldQuery->addShould($matchPhrase);

        // 3. match_phrase_prefix — for partial model names
        $matchPhrasePrefix = new MatchPhrasePrefix('name_pl_pl', [
            'query' => $queryText,
            'boost' => $phrasePrefixBoost,
        ]);
        $shouldQuery->addShould($matchPhrasePrefix);

        // 4. Exact term matches on product code fields (boost=100)
        // These catch searches by EAN, SKU, manufacturer code, ERP ID
        $codeFields = [
            'ean.keyword' => $queryTrimmed,
            'erp_id' => is_numeric($queryTrimmed) ? (int) $queryTrimmed : null,
        ];

        foreach ($codeFields as $field => $value) {
            if (null !== $value) {
                $shouldQuery->addShould(new Term([$field => ['value' => $value, 'boost' => 100]]));
            }
        }

        // String code fields — both original case and uppercase
        $stringCodeFields = ['ean.keyword', 'product_code.keyword'];
        foreach ($stringCodeFields as $field) {
            $shouldQuery->addShould(new Term([$field => ['value' => $queryTrimmed, 'boost' => 100]]));
            if ($queryTrimmed !== $queryUpper) {
                $shouldQuery->addShould(new Term([$field => ['value' => $queryUpper, 'boost' => 90]]));
            }
        }

        $shouldQuery->setMinimumShouldMatch(1);

        // Add brand filter when brand-intent detected
        if ($intent->hasBrandIntent()) {
            $shouldQuery->addFilter(new Term(['producer' => $intent->brand->brand]));
        }

        // Add focal-length filter when detected (restrict to lens subcategories)
        if ($intent->hasFocalLengthIntent()) {
            $shouldQuery->addFilter(new Terms('product_main_taxon_name_1_pl_pl', SearchIntentResolver::LENS_SUBCATS));
        }

        $boolQuery->addMust($shouldQuery);
    }
}
