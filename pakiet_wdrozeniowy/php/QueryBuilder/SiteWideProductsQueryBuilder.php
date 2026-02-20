<?php

declare(strict_types=1);

namespace App\Component\Elasticsearch\QueryBuilder;

use App\Component\Elasticsearch\Model\Search;
use App\Component\Product\Entity\ProductInterface;
use BitBag\SyliusElasticsearchPlugin\QueryBuilder\QueryBuilderInterface;
use Elastica\Query;
use Elastica\Query\AbstractQuery;
use Elastica\Query\BoolQuery;
use Elastica\Query\Ids;
use FOS\ElasticaBundle\Finder\PaginatedFinderInterface;

/**
 * Pre-qualifies top products via function_score, then filters by IDs.
 *
 * Changes from original:
 * - Integrates SearchIntent from ProductsQueryBuilder to use intent-aware function_score
 * - Increased pre-qualification from 100 to 200 for better recall
 * - Uses buildIntentAwareQuery() when intent is available
 */
final class SiteWideProductsQueryBuilder implements QueryBuilderInterface
{
    private ?BoolQuery $cachedQuery = null;

    /**
     * Max products to pre-qualify via function_score.
     * Increased from 100 to 200 for better recall on broad queries.
     */
    private const PRE_QUALIFY_SIZE = 200;

    public function __construct(
        private ProductsQueryBuilder $queryBuilder,
        private PaginatedFinderInterface $finder,
        private FunctionScoreProductsQueryBuilder $fuctionScoreProductsQueryBuilder,
        private QueryBuilderInterface $hasPriceBetweenQueryBuilder,
    ) {
    }

    public function buildQuery(array $data): ?AbstractQuery
    {
        if (null !== $this->cachedQuery) {
            return $this->cachedQuery;
        }

        /** @var BoolQuery $boolQuery */
        $boolQuery = $this->queryBuilder->buildQuery($data);

        if (array_key_exists('search_in_results_name', $data) && '' !== $data['search_in_results_name'] && null !== $data['search_in_results_name']) {
            $nameQuery = new Query\MatchQuery();
            $nameQuery->setField('name_pl_pl', $data['search_in_results_name']);

            $boolQuery->addMust($nameQuery);
        }

        // Get the SearchIntent resolved by ProductsQueryBuilder
        $intent = $this->queryBuilder->getLastIntent();

        // Build function_score: intent-aware if intent is available, fallback otherwise
        if (null !== $intent) {
            $functionScore = $this->fuctionScoreProductsQueryBuilder->buildIntentAwareQuery($intent);
        } else {
            /** @var Query\FunctionScore $functionScore */
            $functionScore = $this->fuctionScoreProductsQueryBuilder->buildQuery([]);
        }

        $functionScore->setQuery($boolQuery);

        $query = new Query($functionScore);
        $query->setSize(self::PRE_QUALIFY_SIZE);
        $query->setSource(false);
        $query->setSort(['_score' => ['order' => 'desc']]);

        $ids = $this->getPreQualifiedProducts($query);

        if (empty($ids)) {
            $this->cachedQuery = $boolQuery;

            return $boolQuery;
        }

        $boolQuery->addFilter(new Ids($ids));

        if (true === Search::isPriceFilterSet($data)) {
            $priceQuery = $this->hasPriceBetweenQueryBuilder->buildQuery($data['price']);

            if (null !== $priceQuery) {
                $boolQuery->addFilter($priceQuery);
            }
        }

        $this->cachedQuery = $boolQuery;

        return $boolQuery;
    }

    private function getPreQualifiedProducts(Query $query): array
    {
        $topHits = $this->finder->find($query, self::PRE_QUALIFY_SIZE, ['size' => self::PRE_QUALIFY_SIZE]);

        $ids = [];
        foreach ($topHits as $hit) {
            if ($hit instanceof ProductInterface && null !== $hit->getId()) {
                $ids[] = $hit->getId();
            }
        }

        return $ids;
    }
}
