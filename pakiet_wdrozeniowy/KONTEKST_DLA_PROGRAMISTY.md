# Kontekst wdrożenia CyfroSearch w BitBag ES Plugin

## Skąd to się wzięło

Zbudowaliśmy prototyp wyszukiwarki (CyfroSearch) na Pythonie + Elasticsearch 8.17
z danymi z cyfrowe.pl (~17 500 produktów). Prototyp osiągnął **98.9% success rate**
na top 100 zapytań z Google Analytics (GA4).

Teraz przenosimy tę logikę do produkcyjnego kodu PHP w BitBagSyliusElasticsearchPlugin.

**Demo prototypu:** https://cyfrosearch-demo.onrender.com
**Repozytorium prototypu:** https://github.com/przemyslawwywigaczcyfrowe/cyfrosearch-deploy

---

## Kluczowe założenia

### 1. Nazwy pól w ES
Pliki PHP odwołują się do konkretnych nazw pól w indeksie Elasticsearch.
**Musisz zweryfikować** czy nazwy pasują do Twojego indeksu:

```bash
# Sprawdź aktualne pola:
curl -s localhost:9200/bitbag_shop_products_prod/_mapping | python -m json.tool | head -100

# Sprawdź unikalne wartości podkategorii:
curl -s localhost:9200/bitbag_shop_products_prod/_search -d '{
  "size": 0,
  "aggs": {"subcats": {"terms": {"field": "product_main_taxon_name_1_pl_pl", "size": 200}}}
}' | python -m json.tool
```

**Pola używane w kodzie:**
| Pole w kodzie | Typ | Opis |
|---|---|---|
| `name_pl_pl` | text | Nazwa produktu (analizowany polskim stemmerem) |
| `description_pl_pl` | text | Opis produktu |
| `producer` | keyword | Marka/producent (exact match) |
| `product_main_taxon_name_1_pl_pl` | keyword | Nazwa podkategorii (najniższy poziom) |
| `product_main_taxon_name_2_pl_pl` | keyword | Nazwa kategorii (środkowy poziom) |
| `product_main_taxon_name_3_pl_pl` | keyword | Nazwa kategorii (najwyższy poziom) |
| `ean` / `ean.keyword` | keyword | Kod EAN |
| `erp_id` | keyword | ID z systemu ERP |
| `product_code` / `product_code.keyword` | keyword | Kod producenta / SKU |
| `state` | keyword | Stan produktu (widoczność) |
| `popularity` | integer | Popularność (z GA4 lub inna metryka) |
| `bestseller` | boolean | Czy bestseller |
| `featured` | integer | Wyróżniony |
| `new_or_promotion` | integer | Nowy/w promocji |
| `rating` | float | Ocena |

### 2. Nazwy podkategorii (CATEGORY_ALIASES)
Plik `CategoryIntentDetector.php` zawiera mapę `CATEGORY_ALIASES` — słowa kluczowe → nazwy podkategorii ES.

**To jest najważniejsza rzecz do dostosowania.** Nazwy podkategorii MUSZĄ dokładnie pasować
do wartości w polu `product_main_taxon_name_1_pl_pl` w Twoim indeksie.

Aby pobrać listę istniejących podkategorii:
```bash
curl -s localhost:9200/bitbag_shop_products_prod/_search -d '{
  "size": 0,
  "aggs": {"subcats": {"terms": {"field": "product_main_taxon_name_1_pl_pl", "size": 500}}}
}'
```

### 3. Nazwy marek (BRAND_ALIASES)
Plik `BrandIntentDetector.php` ładuje marki automatycznie z ES (pole `producer`).
Stałe `BRAND_ALIASES` zawierają tylko aliasy/skróty — nie trzeba ich zmieniać
chyba że chcesz dodać nowe skróty.

### 4. Pole `state` vs `condition`
W prototypie CyfroSearch:
- `condition` = "new" / "used" (stan produktu: nowy/używany)
- `availability` = "in_stock" / "na_zamowienie" / "out_of_stock"

W obecnym BitBag:
- `state` = stany widoczności produktu

**Dostosuj filtry** w `FunctionScoreProductsQueryBuilder.php` do swoich pól.

### 5. Boostery z .env
Wartości boosterów są czytane z `.env` przez `parameters.yaml`. Zalecane wartości:

```env
ELASTICSEARCH_NAME_BOOSTER=3
ELASTICSEARCH_DESCRIPTION_BOOSTER=1
ELASTICSEARCH_PRODUCER_BOOSTER=3
ELASTICSEARCH_MAIN_CATEGORY_BOOSTER=300
ELASTICSEARCH_SUB_CATEGORY_BOOSTER=200
ELASTICSEARCH_CHILD_CATEGORY_BOOSTER=100
ELASTICSEARCH_POPULARITY_WEIGHT=80
ELASTICSEARCH_NEW_OR_PROMOTION_WEIGHT=20
ELASTICSEARCH_RATING_WEIGHT=10
ELASTICSEARCH_FEATURED_WEIGHT=100
```

### 6. realization_time_booster
To jest pole unikalne dla Cyfrowe.pl — czas realizacji zamówienia.
Jest **zachowane** we wszystkich ścieżkach FunctionScore. Jeśli go nie masz, usuń.

---

## Architektura zmian

```
Zapytanie użytkownika
       │
       ▼
SearchIntentResolver  ← orkiestrator
  ├── normalizeModelQuery()    "a7iv" → "a7 iv"
  ├── mergeMarkRoman()         "mark III" → "markIII"
  ├── BrandIntentDetector      "fuji xt5" → brand=Fujifilm, remainder="xt5"
  ├── rewriteQuery()           "fuji xt5" → "Fujifilm xt5"
  ├── detectCondition()        "używany canon r5" → used=true, clean="canon r5"
  ├── CategoryIntentDetector   "lampa" → 7 podkategorii lamp
  └── detectFocalLength()      "24-70" → focal="24-70"
       │
       ▼
  SearchIntent (composite VO)
       │
       ▼
ProductsQueryBuilder
  ├── Category-intent → constant_score + Terms filter na podkategoriach
  └── Text-matching → bool.should: MultiMatch + match_phrase + match_phrase_prefix + exact codes
       │
       ▼
FunctionScoreProductsQueryBuilder
  ├── Category-intent → sqrt(popularity), availability, bestseller
  └── Text-matching → brand boost, main_cat boost, log1p(popularity)
       │
       ▼
SiteWideProductsQueryBuilder
  └── Pre-qualify top 200 products → filter by IDs
       │
       ▼
  Wyniki
```

---

## Co NIE jest w tym pakiecie

1. **Sugestie fraz** — w Syliusie prezentujemy wyłącznie produkty i kategorie
2. **A/B testing** — prototyp ma framework A/B, ale nie jest wdrażany na produkcji
3. **Reranking sprzedażowy** — opisany w INSTRUKCJA_WDROZENIA_BITBAG.pdf (Faza 7),
   wymaga integracji z danymi ERP, nie jest w tym pakiecie
4. **Testy wydajnościowe** — tylko testy funkcjonalne

---

## Jak testować

1. Zmień `.env` (boostery) → natychmiastowa poprawa
2. Wrzuć pliki PHP → uruchom `bin/console fos:elastica:populate`
3. Sprawdź zapytania testowe (patrz `SearchIntentResolverTest.php`)
4. Porównaj wyniki z demo: https://cyfrosearch-demo.onrender.com/api/suggest?q=TWOJE_ZAPYTANIE

---

## Kontakt

Pytania dotyczące prototypu → repozytorium GitHub Issues:
https://github.com/przemyslawwywigaczcyfrowe/cyfrosearch-deploy/issues
