<?php

declare(strict_types=1);

namespace App\Tests\Component\Elasticsearch\IntentDetector;

use App\Component\Elasticsearch\IntentDetector\BrandIntentDetector;
use App\Component\Elasticsearch\IntentDetector\CategoryIntentDetector;
use App\Component\Elasticsearch\IntentDetector\SearchIntentResolver;
use PHPUnit\Framework\TestCase;

/**
 * Testy jednostkowe dla SearchIntentResolver.
 *
 * Jak uruchomić:
 *   bin/phpunit tests/Component/Elasticsearch/IntentDetector/SearchIntentResolverTest.php
 *
 * Uwaga: BrandIntentDetector i CategoryIntentDetector ładują dane z ES.
 * W testach jednostkowych rejestrujemy marki i podkategorie ręcznie
 * za pomocą registerBrand() i registerSubcategory().
 */
final class SearchIntentResolverTest extends TestCase
{
    private SearchIntentResolver $resolver;
    private BrandIntentDetector $brandDetector;
    private CategoryIntentDetector $categoryDetector;

    protected function setUp(): void
    {
        // Tworzymy mock findera — nie potrzebujemy prawdziwego ES w unit testach
        $finder = $this->createMock(\FOS\ElasticaBundle\Finder\PaginatedFinderInterface::class);

        $this->brandDetector = new BrandIntentDetector($finder);
        $this->categoryDetector = new CategoryIntentDetector($finder);

        // Rejestrujemy znane marki (normalnie ładowane z ES)
        foreach (['Canon', 'Nikon', 'Sony', 'Fujifilm', 'Panasonic', 'Olympus',
                   'Peak Design', 'Godox', 'SmallRig', 'Sigma', 'Tamron',
                   'GoPro', 'DJI', 'Insta360', 'Hasselblad', 'Leica'] as $brand) {
            $this->brandDetector->registerBrand($brand);
        }

        // Rejestrujemy znane podkategorie (normalnie ładowane z ES)
        foreach (['bezlusterkowce', 'aparaty cyfrowe', 'lustrzanki', 'kompakty',
                   'obiektywy stałoogniskowe', 'obiektywy zmiennoogniskowe (zoom)',
                   'obiektywy do lustrzanek', 'obiektywy do bezlusterkowców',
                   'lampy LED', 'lampy błyskowe', 'lampy studyjne LED',
                   'lampy panelowe LED', 'lampy pierścieniowe LED',
                   'SD / SDHC / SDXC', 'CFexpress', 'microSD',
                   'statywy (trójnogi)', 'statywy do filmowania',
                   'klatki', 'plecaki fotograficzne', 'torby fotograficzne',
                   'kamery cyfrowe', 'kamery sportowe', 'drony',
                   'akumulatory i baterie', 'filtry',
                   'paski', 'gimbale', 'mikrofony'] as $subcat) {
            $this->categoryDetector->registerSubcategory($subcat);
        }

        $this->resolver = new SearchIntentResolver($this->brandDetector, $this->categoryDetector);
    }

    // ═══════════════════════════════════════════════════════
    //  Brand Intent
    // ═══════════════════════════════════════════════════════

    public function testBrandIntentCanon(): void
    {
        $intent = $this->resolver->resolve('canon');
        $this->assertNotNull($intent->brand, 'Brand should be detected for "canon"');
        $this->assertSame('Canon', $intent->brand->brand);
        $this->assertSame('', $intent->brand->remainder);
    }

    public function testBrandIntentCanonR8(): void
    {
        $intent = $this->resolver->resolve('canon r8');
        $this->assertNotNull($intent->brand);
        $this->assertSame('Canon', $intent->brand->brand);
        $this->assertSame('r8', $intent->brand->remainder);
    }

    public function testBrandAliasFuji(): void
    {
        $intent = $this->resolver->resolve('fuji xt5');
        $this->assertNotNull($intent->brand, 'Brand alias "fuji" should resolve to Fujifilm');
        $this->assertSame('Fujifilm', $intent->brand->brand);
        $this->assertTrue($intent->brand->wasAlias);
        $this->assertSame('xt5', $intent->brand->remainder);
    }

    public function testBrandAliasPana(): void
    {
        $intent = $this->resolver->resolve('pana gh6');
        $this->assertNotNull($intent->brand);
        $this->assertSame('Panasonic', $intent->brand->brand);
        $this->assertTrue($intent->brand->wasAlias);
    }

    public function testTwoWordBrand(): void
    {
        $intent = $this->resolver->resolve('peak design paski');
        $this->assertNotNull($intent->brand);
        $this->assertSame('Peak Design', $intent->brand->brand);
        $this->assertSame('paski', $intent->brand->remainder);
    }

    public function testNoBrandForGenericQuery(): void
    {
        $intent = $this->resolver->resolve('statyw');
        $this->assertNull($intent->brand, '"statyw" should NOT have brand intent');
    }

    // ═══════════════════════════════════════════════════════
    //  Category Intent
    // ═══════════════════════════════════════════════════════

    public function testCategoryIntentLampa(): void
    {
        $intent = $this->resolver->resolve('lampa');
        $this->assertNotNull($intent->category, '"lampa" should have category intent');
        $this->assertGreaterThanOrEqual(3, count($intent->category->subcategories));
        $this->assertSame('', $intent->category->remainder);
    }

    public function testCategoryIntentKartaSd(): void
    {
        $intent = $this->resolver->resolve('karta sd');
        $this->assertNotNull($intent->category, '"karta sd" should have category intent');
        $this->assertContains('SD / SDHC / SDXC', $intent->category->subcategories);
        $this->assertSame('', $intent->category->remainder);
    }

    public function testCategoryIntentObiektyw(): void
    {
        $intent = $this->resolver->resolve('obiektyw');
        $this->assertNotNull($intent->category);
        $this->assertGreaterThanOrEqual(2, count($intent->category->subcategories));
    }

    public function testCategoryWithRemainder(): void
    {
        $intent = $this->resolver->resolve('klatka sony a7 iv');
        $this->assertNotNull($intent->category, '"klatka sony a7 iv" should have category intent');
        $this->assertSame('sony a7 iv', $intent->category->remainder);
    }

    public function testBrandPlusCategoryRemainder(): void
    {
        // "peak design paski" → brand=Peak Design, remainder="paski" → category=paski
        $intent = $this->resolver->resolve('peak design paski');
        $this->assertNotNull($intent->brand);
        $this->assertSame('Peak Design', $intent->brand->brand);
        $this->assertNotNull($intent->category, 'Category should be detected from brand remainder "paski"');
    }

    // ═══════════════════════════════════════════════════════
    //  Model Normalization
    // ═══════════════════════════════════════════════════════

    public function testModelSplitA7iv(): void
    {
        $intent = $this->resolver->resolve('sony a7iv');
        // "a7iv" → "a7 iv" po normalizacji
        $this->assertStringContainsString('a7 iv', mb_strtolower($intent->normalizedQuery));
    }

    public function testMarkMerge(): void
    {
        $intent = $this->resolver->resolve('canon r5 mark II');
        // "mark II" → "markII" po normalizacji
        $this->assertStringContainsString('markII', $intent->normalizedQuery);
    }

    // ═══════════════════════════════════════════════════════
    //  Condition Intent (używany / used)
    // ═══════════════════════════════════════════════════════

    public function testConditionIntentUsed(): void
    {
        $intent = $this->resolver->resolve('używany canon r5');
        $this->assertTrue($intent->usedCondition, '"używany" should set usedCondition=true');
        $this->assertStringNotContainsString('używany', $intent->normalizedQuery,
            '"używany" should be stripped from normalizedQuery');
    }

    public function testConditionIntentUzywany(): void
    {
        $intent = $this->resolver->resolve('uzywany nikon z8');
        $this->assertTrue($intent->usedCondition, '"uzywany" (without diacritics) should work too');
    }

    public function testNoConditionForRegularQuery(): void
    {
        $intent = $this->resolver->resolve('canon eos r8');
        $this->assertFalse($intent->usedCondition);
    }

    // ═══════════════════════════════════════════════════════
    //  Focal Length Intent
    // ═══════════════════════════════════════════════════════

    public function testFocalLengthIntent(): void
    {
        $intent = $this->resolver->resolve('24-70');
        $this->assertNotNull($intent->focalLength, '"24-70" should detect focal length intent');
        $this->assertSame('24-70', $intent->focalLength);
    }

    public function testFocalLengthWithBrand(): void
    {
        $intent = $this->resolver->resolve('sigma 24-70');
        $this->assertNotNull($intent->focalLength);
        $this->assertSame('24-70', $intent->focalLength);
        $this->assertNotNull($intent->brand);
        $this->assertSame('Sigma', $intent->brand->brand);
    }

    public function testNoFocalLengthForCategoryQuery(): void
    {
        // "lampa" triggers category intent → focal length NOT checked
        $intent = $this->resolver->resolve('lampa');
        $this->assertNull($intent->focalLength);
    }

    // ═══════════════════════════════════════════════════════
    //  Composite Intents
    // ═══════════════════════════════════════════════════════

    public function testMethodsHasBrandHasCategory(): void
    {
        $intent = $this->resolver->resolve('canon');
        $this->assertTrue($intent->hasBrandIntent());
        $this->assertFalse($intent->hasCategoryIntent());
        $this->assertFalse($intent->hasFocalLengthIntent());

        $intent2 = $this->resolver->resolve('lampa');
        $this->assertFalse($intent2->hasBrandIntent());
        $this->assertTrue($intent2->hasCategoryIntent());
    }

    // ═══════════════════════════════════════════════════════
    //  Regression Tests — Top 15 GA4 queries
    // ═══════════════════════════════════════════════════════

    /**
     * @dataProvider topQueriesProvider
     */
    public function testTopGA4Query(string $query, ?string $expectedBrand, bool $expectCategory): void
    {
        $intent = $this->resolver->resolve($query);

        if (null !== $expectedBrand) {
            $this->assertNotNull($intent->brand, "Brand should be detected for '$query'");
            $this->assertSame($expectedBrand, $intent->brand->brand);
        }

        if ($expectCategory) {
            $this->assertNotNull($intent->category, "Category should be detected for '$query'");
        }
    }

    public static function topQueriesProvider(): iterable
    {
        yield 'canon'              => ['canon', 'Canon', false];
        yield 'sony'               => ['sony', 'Sony', false];
        yield 'nikon'              => ['nikon', 'Nikon', false];
        yield 'fujifilm'           => ['fujifilm', 'Fujifilm', false];
        yield 'fuji xt5'           => ['fuji xt5', 'Fujifilm', false];
        yield 'canon eos r8'       => ['canon eos r8', 'Canon', false];
        yield 'lampa'              => ['lampa', null, true];
        yield 'karta sd'           => ['karta sd', null, true];
        yield 'obiektyw'           => ['obiektyw', null, true];
        yield 'statyw'             => ['statyw', null, true];
        yield 'peak design paski'  => ['peak design paski', 'Peak Design', true];
        yield 'godox'              => ['godox', 'Godox', false];
        yield 'smallrig'           => ['smallrig', 'SmallRig', false];
        yield 'gopro'              => ['gopro', 'GoPro', false];
        yield 'sigma 24-70'        => ['sigma 24-70', 'Sigma', false];
    }
}
