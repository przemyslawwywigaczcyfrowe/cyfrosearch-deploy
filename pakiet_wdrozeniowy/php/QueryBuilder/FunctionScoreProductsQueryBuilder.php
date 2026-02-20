<?php

declare(strict_types=1);

namespace App\Component\Elasticsearch\QueryBuilder;

use App\Component\Elasticsearch\IntentDetector\SearchIntent;
use App\Component\Elasticsearch\IntentDetector\SearchIntentResolver;
use BitBag\SyliusElasticsearchPlugin\QueryBuilder\QueryBuilderInterface;
use Elastica\Query\AbstractQuery;
use Elastica\Query\FunctionScore;
use Elastica\Query\Term;
use Elastica\Query\Terms;

/**
 * Intent-aware function_score query builder.
 *
 * Builds different scoring functions depending on the detected SearchIntent:
 *
 * **Category-intent** (e.g. "lampa", "karta sd"):
 * - sqrt modifier for popularity (bigger differences between popular/unpopular)
 * - Availability boost (+50 in_stock, +30 na_zamowienie)
 * - Bestseller (+60), image (+10), promo (+10)
 * - Condition switch (used +200 if condition-intent, new +25 otherwise)
 *
 * **Text-matching** (e.g. "canon", "sony a7iv"):
 * - Brand match boost (+300 when brand-intent detected)
 * - Main subcategory boost (+1000 with brand-intent, +120 without)
 * - Popularity with log1p (weight=80)
 * - Availability (+50 in_stock, +30 na_zamowienie)
 * - Bestseller (+60), promo (+20), new (+30)
 * - Focal-length lens boost (+150 when detected)
 * - Condition switch
 *
 * **Always preserved**: realization_time_booster (unique to Cyfrowe, not in CyfroSearch)
 */
final class FunctionScoreProductsQueryBuilder implements QueryBuilderInterface
{
    public function __construct(
        private string $ratingProperty,
        private string $featuredProperty,
        private string $newOrPromotionProperty,
        private string $popularityProperty,
        private int $popularityWeight,
        private int $ratingWeight,
        private int $newOrPromotionWeight,
        private int $featuredWeight,
        private string $productRealizationTimeBoosterProperty,
        private string $bestsellerProperty,
    ) {
    }

    /**
     * Build function_score without intent awareness (backward-compatible).
     * Used by TaxonProductsQueryBuilder and other non-search contexts.
     */
    public function buildQuery(array $data): ?AbstractQuery
    {
        $functionScore = new FunctionScore();
        $functionScore->setBoostMode(FunctionScore::BOOST_MODE_SUM);
        $functionScore->setScoreMode(FunctionScore::SCORE_MODE_SUM);

        $this->addFieldValueFactorFunction($functionScore, $this->popularityProperty, 'log1p', $this->popularityWeight);
        $this->addFieldValueFactorFunction($functionScore, $this->ratingProperty, 'none', $this->ratingWeight);
        $this->addFieldValueFactorFunction($functionScore, $this->newOrPromotionProperty, 'none', $this->newOrPromotionWeight);
        $this->addFieldValueFactorFunction($functionScore, $this->featuredProperty, 'none', $this->featuredWeight);

        $functionScore->addFieldValueFactorFunction(
            $this->productRealizationTimeBoosterProperty,
            TaxonProductsQueryBuilder::FACTOR,
            TaxonProductsQueryBuilder::MODIFIER,
        );

        return $functionScore;
    }

    /**
     * Build intent-aware function_score with dynamic scoring functions.
     *
     * This is called by SiteWideProductsQueryBuilder instead of buildQuery()
     * when a SearchIntent is available.
     */
    public function buildIntentAwareQuery(SearchIntent $intent): FunctionScore
    {
        $functionScore = new FunctionScore();
        $functionScore->setBoostMode(FunctionScore::BOOST_MODE_SUM);
        $functionScore->setScoreMode(FunctionScore::SCORE_MODE_SUM);
        $functionScore->setParam('max_boost', 2000);

        if ($intent->hasCategoryIntent()) {
            $this->addCategoryIntentFunctions($functionScore, $intent);
        } else {
            $this->addTextMatchingFunctions($functionScore, $intent);
        }

        // ALWAYS preserve realization_time_booster (unique Cyfrowe feature)
        $functionScore->addFieldValueFactorFunction(
            $this->productRealizationTimeBoosterProperty,
            TaxonProductsQueryBuilder::FACTOR,
            TaxonProductsQueryBuilder::MODIFIER,
        );

        return $functionScore;
    }

    /**
     * Category-intent scoring: rank by POPULARITY, not text relevance.
     *
     * When user searches "lampa" or "karta sd", they browse a category.
     * Use sqrt modifier so differences in popularity actually matter:
     * sqrt(400)=20 vs sqrt(20)=4.5 → 4.4x difference (log1p would only give 2x)
     */
    private function addCategoryIntentFunctions(FunctionScore $functionScore, SearchIntent $intent): void
    {
        // Popularity — strongest signal for category browsing (sqrt modifier)
        $this->addFieldValueFactorFunction($functionScore, $this->popularityProperty, 'sqrt', 50);

        // Rating — good products float up
        $this->addFieldValueFactorFunction($functionScore, $this->ratingProperty, 'none', $this->ratingWeight);

        // Availability — crucial for category browsing
        $functionScore->addFunction(
            'filter',
            null,
            new Term(['state' => 'in_stock']),
            30,
        );

        // Bestseller
        $this->addFilterFunction($functionScore, $this->bestsellerProperty, true, 60);

        // Featured
        $this->addFieldValueFactorFunction($functionScore, $this->featuredProperty, 'none', $this->featuredWeight);

        // New or promotion
        $this->addFieldValueFactorFunction($functionScore, $this->newOrPromotionProperty, 'none', 10);

        // Condition switch: used intent → boost used +200, otherwise new +25
        if ($intent->usedCondition) {
            $functionScore->addFunction(
                'filter',
                null,
                new Term(['state' => 'used']),
                200,
            );
        } else {
            $functionScore->addFunction(
                'filter',
                null,
                new Term(['state' => 'new']),
                25,
            );
        }
    }

    /**
     * Text-matching scoring: brand queries, model searches, etc.
     *
     * When brand-intent is detected ("canon", "sony a7"):
     * - Brand match boost +300 (products of that brand)
     * - Main subcategory boost +1000 (cameras > lens caps)
     * - Higher price weight (flagships > accessories)
     */
    private function addTextMatchingFunctions(FunctionScore $functionScore, SearchIntent $intent): void
    {
        $mainCatWeight = $intent->hasBrandIntent() ? 1000 : 120;

        // Brand-intent boost — if user searches a brand, prefer that brand's products
        if ($intent->hasBrandIntent()) {
            $functionScore->addFunction(
                'filter',
                null,
                new Term(['producer' => $intent->brand->brand]),
                300,
            );
        }

        // Popularity — strongest general signal (log1p modifier)
        $this->addFieldValueFactorFunction($functionScore, $this->popularityProperty, 'log1p', $this->popularityWeight);

        // Rating
        $this->addFieldValueFactorFunction($functionScore, $this->ratingProperty, 'none', $this->ratingWeight);

        // Availability — in_stock (+50), implicit out_of_stock (no boost)
        $functionScore->addFunction(
            'filter',
            null,
            new Term(['state' => 'in_stock']),
            50,
        );

        // Bestseller — strong signal
        $this->addFilterFunction($functionScore, $this->bestsellerProperty, true, 60);

        // Main product categories get boosted over accessories
        // Much higher weight when brand-intent detected (cameras > scopes/lens caps)
        $functionScore->addFunction(
            'filter',
            null,
            new Terms('product_main_taxon_name_1_pl_pl', SearchIntentResolver::MAIN_SUBCATS),
            $mainCatWeight,
        );

        // Featured
        $this->addFieldValueFactorFunction($functionScore, $this->featuredProperty, 'none', $this->featuredWeight);

        // New or promotion
        $this->addFieldValueFactorFunction($functionScore, $this->newOrPromotionProperty, 'none', $this->newOrPromotionWeight);

        // Promo boost
        $this->addFilterFunction($functionScore, $this->newOrPromotionProperty, 1, 20);

        // New products (cold start problem solver)
        $this->addFieldValueFactorFunction($functionScore, $this->newOrPromotionProperty, 'none', 30);

        // Condition switch
        if ($intent->usedCondition) {
            $functionScore->addFunction(
                'filter',
                null,
                new Term(['state' => 'used']),
                200,
            );
        } else {
            $newWeight = $intent->hasBrandIntent() ? 50 : 25;
            $functionScore->addFunction(
                'filter',
                null,
                new Term(['state' => 'new']),
                $newWeight,
            );
        }

        // Focal-length intent: boost lens subcategories
        if ($intent->hasFocalLengthIntent()) {
            $functionScore->addFunction(
                'filter',
                null,
                new Terms('product_main_taxon_name_1_pl_pl', SearchIntentResolver::LENS_SUBCATS),
                150,
            );
        }
    }

    private function addFieldValueFactorFunction(FunctionScore $functionScore, string $field, string $modifier, int $weight): void
    {
        $functionScore->addFunction(
            'field_value_factor',
            [
                'field' => $field,
                'factor' => 1,
                'modifier' => $modifier,
                'missing' => 1,
            ],
            null,
            $weight,
        );
    }

    private function addFilterFunction(FunctionScore $functionScore, string $field, mixed $value, int $weight): void
    {
        $functionScore->addFunction(
            'filter',
            null,
            new Term([$field => $value]),
            $weight,
        );
    }
}
