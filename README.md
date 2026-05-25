# Cyfrowe.pl Search Engine — Prototyp i specyfikacja

Prototyp wyszukiwarki produktowej dla sklepu [Cyfrowe.pl](https://www.cyfrowe.pl), oparty o **Elasticsearch** z integracją **Google Analytics 4**. Projekt służy jako działające demo oraz **specyfikacja techniczna dla programistów** wdrażających wyszukiwarkę w produkcji.

## Kluczowe cechy

- **Multiplikatywny model scoringu** — popularność (GA4) mnoży trafność tekstową zamiast dodawać punkty, co daje realne rozróżnienie produktów
- **Uniwersalne reguły rankingu** — brak wyjątków per kategoria, marka czy typ produktu
- **Integracja z Google Analytics 4** — wyświetlenia stron i sprzedaż z ostatnich 90 dni wpływają na ranking
- **Sugester/autocomplete** — lista 8 najlepszych produktów na żywo
- **Testy regresyjne** — każda zmiana algorytmu musi przejść zestaw testów

## Architektura

```
final_score = text_relevance * popularity_factor
```

| Składowa             | Opis                                           | Zakres        |
|----------------------|------------------------------------------------|---------------|
| `text_relevance`     | Bool query ES: must (recall) + should (precyzja) | 0 - ~500    |
| `popularity_factor`  | `1 + popularity_score * 0.15`                  | 1.0 - ~4.5x  |

**UWAGA:** Dostępność produktu (availability) NIE jest sygnałem rankingowym.
Produkty niedostępne na magazynie mogą być zamawiane na życzenie klienta,
więc nie powinny być karane w wynikach. Decyduje WYŁĄCZNIE popularność (GA4).

Szczegółowa dokumentacja algorytmu: **[ALGORYTM_WYSZUKIWARKI.md](ALGORYTM_WYSZUKIWARKI.md)**

Dokument dla osób nietechnicznych: **[JAK_DZIALA_WYSZUKIWARKA.md](JAK_DZIALA_WYSZUKIWARKA.md)**

## Struktura projektu

```
.
├── app.py                  # FastAPI — API endpoints + HTML UI sugestera
├── search_engine.py        # Algorytm wyszukiwania (function_score query)
├── es_mapping.py           # Mapping indeksu ES (analyzery, pola, synonimy)
├── indexer.py              # Pobieranie feeda i indeksowanie produktów
├── ga_updater.py           # Pobieranie danych GA4 i aktualizacja popularity_score
├── config.py               # Konfiguracja (zmienne środowiskowe)
├── es_client.py            # Klient Elasticsearch
├── run_regression.py       # Runner testów regresyjnych
├── regression_tests.json   # Definicje testów regresyjnych
├── brand_aliases.json      # 202 aliasy marek (literówki, synonimy)
├── taxon_aliases.json      # 230 aliasów kategorii
├── ALGORYTM_WYSZUKIWARKI.md # Pełna dokumentacja techniczna algorytmu
├── JAK_DZIALA_WYSZUKIWARKA.md # Prosty opis dla osób nietechnicznych
├── .env.example            # Wzór konfiguracji
├── requirements.txt        # Zależności Python
└── README.md               # Ten plik
```

## Wymagania

- **Python 3.11+**
- **Elasticsearch 8.x** (Elastic Cloud lub lokalna instancja)
- **Google Analytics 4** z Data API (opcjonalne — bez GA produkty mają popularity_score = 0)
- **Feed produktowy** w formacie JSON (DataFeedWatch lub kompatybilny)

## Instalacja

### 1. Klonowanie repozytorium

```bash
git clone https://github.com/przemyslawwywigaczcyfrowe/cyfrowe-search-engine.git
cd cyfrowe-search-engine
```

### 2. Środowisko wirtualne i zależności

```bash
python -m venv venv
venv\Scripts\activate     # Windows
# source venv/bin/activate  # Linux/Mac

pip install -r requirements.txt
```

### 3. Konfiguracja

```bash
copy .env.example .env    # Windows
# cp .env.example .env    # Linux/Mac
```

Uzupełnij `.env`:

```ini
ELASTIC_URL=https://your-deployment.region.gcp.cloud.es.io:443
ELASTIC_API_KEY=your-api-key
GA4_PROPERTY_ID=properties/123456789
GA4_SERVICE_ACCOUNT_JSON=ga4-credentials.json
FEED_URL=https://your-feed-url.json
```

Dla GA4 potrzebujesz pliku `ga4-credentials.json` z kluczem Service Account, który ma rolę "Viewer" w GA4.

### 4. Indeksowanie produktów

```bash
python indexer.py
```

Pobiera feed (z URL lub lokalnego `feed.json`), tworzy indeks ES i indeksuje ~17 000 produktów.

### 5. Aktualizacja danych GA4 (opcjonalnie)

```bash
python ga_updater.py
```

Pobiera wyświetlenia i sprzedaż z ostatnich 90 dni, oblicza `popularity_score` i aktualizuje dokumenty w ES.

### 6. Uruchomienie serwera

```bash
uvicorn app:app --reload --port 8001
```

**Demo online:** https://web-production-94a4c.up.railway.app

Lokalnie: `http://localhost:8001`

## API

### `GET /api/search`

Wyszukiwanie produktów z pełnym scoringiem.

| Parametr       | Typ    | Opis                                    |
|----------------|--------|-----------------------------------------|
| `q`            | string | Fraza wyszukiwania (wymagane)           |
| `page`         | int    | Numer strony (domyślnie 1)              |
| `size`         | int    | Liczba wyników na stronę (domyślnie 24) |
| `availability` | string | Filtr: "in stock", "out of stock", "preorder" |
| `brand`        | string | Filtr po marce                          |
| `category_lvl0`| string | Filtr po kategorii głównej              |
| `price_min`    | float  | Minimalna cena                          |
| `price_max`    | float  | Maksymalna cena                         |
| `sort`         | string | Sortowanie: "price_asc", "price_desc", "name" |

### `GET /api/suggest`

Autocomplete — zwraca 8 najlepszych produktów (ten sam ranking co search).

| Parametr | Typ    | Opis                          |
|----------|--------|-------------------------------|
| `q`      | string | Fraza (min. 2 znaki, wymagane)|

## Testy regresyjne

```bash
python run_regression.py           # Wszystkie testy
python run_regression.py -v        # Szczegółowe wyniki
python run_regression.py -t brand_canon  # Pojedynczy test
```

**Każda zmiana w `search_engine.py` lub `es_mapping.py` musi przejść wszystkie testy.**

## Parametry do strojenia

| Parametr | Plik | Wartość | Wpływ |
|----------|------|---------|-------|
| Popularity factor | search_engine.py | 0.15 | Siła wpływu GA na ranking (jedyne pokrętło) |
| Sales weight | ga_updater.py | 3.0 | Waga sprzedaży vs wyświetleń |
| Views weight | ga_updater.py | 1.0 | Waga wyświetleń |
| AND match boost | search_engine.py | 20 | Nagroda za dopasowanie wszystkich terminów |
| Multi-token boost | search_engine.py | 40 | Nagroda za dopasowanie 2+ terminów |
| Split model AND boost | search_engine.py | 15 | Nagroda za dopasowanie kodu modelu (split) |
| Joined model AND boost | search_engine.py | 15 | Nagroda za dopasowanie kodu modelu (joined) |
| Phrase match boost | search_engine.py | 15 | Nagroda za dokładną frazę |
| Brand keyword boost | search_engine.py | 20 | Nagroda za dopasowanie marki |

## Harmonogram aktualizacji (produkcja)

Rekomendacja: **codziennie o 4:00 rano**

1. Pobierz feed produktowy
2. Reindeksuj produkty (`indexer.py`)
3. Pobierz dane GA4 (`ga_updater.py`)
4. Uruchom testy regresyjne (`run_regression.py`)

## Preprocessing zapytania (pipeline)

Każde zapytanie przechodzi przez uniwersalny pipeline:

1. **Detekcja "używany/używane"** → filtr `condition=used` (słowo usuwane z frazy)
2. **Łączenie rozdzielonych marek** → "smal lrig" → "Smallrig", "pana sonic" → "Panasonic" (łączy sąsiednie tokeny tworzące znaną markę)
3. **Rozdzielanie sklejonych marek** → "sonya6700" → "Sony a6700", "canonr8" → "Canon r8", "peakdesignpaski" → "PEAKDESIGN paski"
4. **Detekcja marki** → filtr `brand.keyword` (obsługuje marki wielowyrazowe: "Peak Design", "Carl Zeiss")
   - Wyjątek: "do/dla/na/pod" + marka = kompatybilność, BEZ filtra
   - Wiele marek = niejednoznaczne, BEZ filtra
5. **Aliasy marek** → "cannon" → Canon, "lumix" → Panasonic, "sygma" → Sigma (202 reguły)
6. **Normalizacja obiektywowa** → "85mm f2.8" → "85 mm f/2.8", "f 1,2" → "f/1.2"
7. **Split kodów modeli** → "rs5" → "rs 5", "zfc" → "z fc", "a7iv" → "a 7 iv"
8. **Join kodów modeli** → "z5 II" → "z5II", "z fc" → "zfc" (odwrotność split)
9. **Compact mm** → "100 mm" → "100mm" (dopasowanie obu wariantów zapisu)
10. **Swap dla krótkich tokenów** → "ml 087" i "ml087" dają identyczne wyniki (auto-join fragmentów kodu modelu). Wyjątek: jednostki miar (mm, gb) nie są joinowane.
11. **Tolerancja ucięć marek** → "manfrott" → Manfrotto, "panasoni" → Panasonic (automatycznie dla wszystkich 300+ marek)
12. **Wariant bez spacji** → "sta tyw" dodatkowo szuka "statyw" jako jednego tokenu (obsługuje przypadkowe spacje w słowach)

Wszystkie warianty (split, joined, compact, nospace) są wyszukiwane RÓWNOCZEŚNIE jako should clauses.

## Znane ograniczenia

1. **Brak polskiego stemmera** — plugin `analysis-stempel` nie jest zainstalowany, dlatego "obiektyw" i "obiektywy" to różne tokeny (obejście: prefix matching)
2. **Produkty spoza katalogu** — jeśli produkt nie istnieje w feedzie, wyniki będą nieadekwatne
3. **Akcesoria vs główne produkty** — akcesorium z kodem modelu (np. "klatka do A7IV") może wgrać z produktem głównym gdy ma exact token match i wyższą popularność GA

## Licencja

Wewnętrzny projekt Cyfrowe.pl. Wszelkie prawa zastrzeżone.
