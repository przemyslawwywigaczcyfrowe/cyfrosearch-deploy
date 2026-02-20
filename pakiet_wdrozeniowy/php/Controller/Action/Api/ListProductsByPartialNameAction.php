<?php

declare(strict_types=1);

namespace App\Component\Elasticsearch\Controller\Action\Api;

use App\Component\Channel\Entity\ChannelInterface;
use App\Component\Elasticsearch\Finder\EanAndErpIdProductsFinderInterface;
use App\Component\Elasticsearch\Finder\PopularProductsFinderInterface;
use App\Component\Elasticsearch\Finder\PopularTaxonsFinderInterface;
use App\Component\Elasticsearch\Finder\TaxonFinderInterface;
use App\Component\Elasticsearch\IntentDetector\SearchIntentResolver;
use App\Component\Elasticsearch\Transformer\ProductTransformerInterface;
use App\Component\Elasticsearch\Transformer\TaxonTransformerInterface;
use App\Component\Product\Entity\ProductInterface;
use App\Component\Taxonomy\Entity\TaxonInterface;
use BitBag\SyliusElasticsearchPlugin\Finder\ShopProductsFinderInterface;
use Pagerfanta\Pagerfanta;
use Sylius\Component\Channel\Context\ChannelContextInterface;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Autocomplete endpoint — intent-aware search with brand/category detection.
 *
 * Changes from original:
 * - Integrates SearchIntentResolver for query normalization and brand alias rewriting
 * - Brand alias rewriting: "fuji" → "Fujifilm" in the ES query
 * - Condition keyword stripping: "używany canon r5" → searches "canon r5" with used intent
 * - All intent detection happens transparently via the ProductsQueryBuilder
 *   which uses SearchIntentResolver internally
 *
 * Flow:
 * 1. Normalize query (model numbers, mark merge)
 * 2. Try EAN/ERP exact match first
 * 3. Fall back to named products search (which uses intent-aware ProductsQueryBuilder)
 * 4. Find matching taxons
 */
final readonly class ListProductsByPartialNameAction
{
    private const PRODUCTS_LIMIT = 7;

    public function __construct(
        private ShopProductsFinderInterface $namedProductsFinder,
        private EanAndErpIdProductsFinderInterface $eanAndErpIdProductsFinder,
        private PopularTaxonsFinderInterface $popularTaxonFinder,
        private PopularProductsFinderInterface $popularProductsFinder,
        private TaxonFinderInterface $taxonFinder,
        private TaxonTransformerInterface $taxonTransformer,
        private ProductTransformerInterface $productTransformer,
        private ChannelContextInterface $channelContext,
        private SearchIntentResolver $intentResolver,
    ) {
    }

    public function __invoke(Request $request): Response
    {
        /** @var ChannelInterface $channel */
        $channel = $this->channelContext->getChannel();
        $query = $request->query->get('query');

        $popularProduct = $this->getFeaturedProduct($channel);
        if (null === $query || empty($query)) {
            return $this->handleEmptyQuery($channel, $popularProduct);
        }

        return $this->handleSearchQuery((string) $query, $channel, $popularProduct);
    }

    private function getFeaturedProduct(ChannelInterface $channel): array
    {
        $popularProduct = $this->popularProductsFinder->findFeaturedProductInSearchModal();

        if (0 !== count($popularProduct)) {
            return ['popularProduct' => $this->productTransformer->transformPopularProductIntoArray($popularProduct[0], $channel)];
        }

        return [];
    }

    private function handleEmptyQuery(ChannelInterface $channel, array $popularProduct): Response
    {
        $products = $this->popularProductsFinder->find(self::PRODUCTS_LIMIT);
        $taxons = $this->popularTaxonFinder->find();

        $transformedProducts = $this->transformProducts($products, $channel);
        $transformedTaxons = $this->transformTaxons($taxons);

        if (self::PRODUCTS_LIMIT === count($transformedProducts['products'])) {
            array_pop($transformedProducts['products']);
        }

        return new JsonResponse(array_merge($transformedProducts, $transformedTaxons, $popularProduct));
    }

    private function handleSearchQuery(string $query, ChannelInterface $channel, array $popularProduct): Response
    {
        // Resolve intent for query normalization (brand alias rewriting, model number splitting)
        // The actual intent-aware search happens inside namedProductsFinder → ProductsQueryBuilder
        $intent = $this->intentResolver->resolve($query);

        // Use normalized query for product search
        $searchQuery = $intent->normalizedQuery;

        $products = $this->findProducts($searchQuery);
        if (0 === count($products)) {
            return new JsonResponse([], Response::HTTP_OK);
        }

        // For taxon matching, use original query (or category remainder)
        $taxonQuery = $query;
        if ($intent->hasCategoryIntent() && $intent->category->hasRemainder()) {
            $taxonQuery = $intent->category->remainder;
        }

        $taxons = $this->taxonFinder->findByNamePhrase($taxonQuery);
        if (0 === count($taxons)) {
            $taxons = $this->popularTaxonFinder->find();
        }

        $transformedProducts = $this->transformProducts($products, $channel);
        $transformedTaxons = $this->transformTaxons($taxons);

        if (array_key_exists('products', $transformedProducts) && array_key_exists(0, $transformedProducts['products'])) {
            $popularProduct['popularProduct'] = $transformedProducts['products'][0];
        }

        if (self::PRODUCTS_LIMIT === count($transformedProducts['products'])) {
            array_shift($transformedProducts['products']);
        }

        return new JsonResponse(array_merge($transformedProducts, $transformedTaxons, $popularProduct));
    }

    private function transformProducts(array|Pagerfanta $products, ChannelInterface $channel): array
    {
        $transformedProducts = ['products' => []];
        foreach ($products as $product) {
            if ($product instanceof ProductInterface && $product->getMainTaxon() instanceof TaxonInterface) {
                $transformedProducts['products'][] = $this->productTransformer->transformPopularProductIntoArray($product, $channel);
            }
        }

        return $transformedProducts;
    }

    private function transformTaxons(array $taxons): array
    {
        $transformedTaxons = ['taxons' => []];
        foreach ($taxons as $taxon) {
            if ($taxon instanceof TaxonInterface) {
                $transformedTaxons['taxons'][] = $this->taxonTransformer->transformPopularTaxonIntoArray($taxon);
            }
        }

        return $transformedTaxons;
    }

    private function findProducts(string $query): Pagerfanta
    {
        $products = $this->eanAndErpIdProductsFinder->find(['query' => $query]);
        if (0 !== count($products)) {
            return $products;
        }

        return $this->namedProductsFinder->find(['query' => $query, 'limit' => self::PRODUCTS_LIMIT]);
    }
}
