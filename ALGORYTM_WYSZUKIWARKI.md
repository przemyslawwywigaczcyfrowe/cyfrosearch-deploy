# Algorytm Wyszukiwarki Cyfrowe.pl — Dokumentacja Techniczna

## 1. Zasada nadrzędna

**Wszystkie reguły rankingu są UNIWERSALNE.** Nie istnieją żadne wyjątki per kategoria,
marka ani typ produktu. Każdy produkt przechodzi przez identyczny pipeline scoringu.
Każda zmiana musi być walidowana testami regresyjnymi na pełnym zbiorze zapytań.

---

## 2. Model scoringu (Multiplicative Function Score)

```
final_score = text_relevance * popularity_factor
```

**WAŻNE:** Dostępność (availability) NIE jest sygnałem rankingowym.
Produkty niedostępne na magazynie mogą być zamawiane na życzenie klienta,
więc nie są karane w wynikach. Decyduje WYŁĄCZNIE popularność (GA4).

### Dlaczego mnożenie (nie dodawanie)?

W modelu addytywnym (STARY, odrzucony):
- text_relevance = ~160 punktów
- GA boost = ~8 punktów (log1p(11590)*0.5 + log1p(53)*1.0)
- Popularity stanowiło ~5% wyniku — NIEWIDOCZNE

W modelu multiplikatywnym (NOWY, aktualny):
- text_relevance = ~160 punktów
- popularity_factor = 4.19x (dla Canon EOS R6 II)
- final_score = 160 * 4.19 = ~670

Kluczowa właściwość mnożenia: **nie może promować nieistotnych wyników**.
Produkt z text_relevance = 0 (nie pasuje do zapytania) dostaje 0 * 4.19 = nadal 0.
Popularność działa WYŁĄCZNIE jako mnożnik na istniejącą trafność tekstową.

---

## 3. Składowe scoringu

### 3.1 Text Relevance (zapytanie Elasticsearch bool)

```json
{
  "bool": {
    "must": [{
      "bool": {
        "should": [
          // Główne wyszukiwanie na searchable_text (nazwa + marka + kategoria)
          {"match": {"searchable_text": {"query": "...", "operator": "or", "fuzziness": "AUTO"}}},
          // Prefix matching dla polskiej odmiany (obiektyw → obiektywy)
          {"match_phrase_prefix": {"searchable_text": {"query": "..."}}},
          // Split kodów modeli: "rs 5" matchuje "RS 5" w nazwie
          {"match": {"searchable_text": {"query": "SPLIT_QUERY", "operator": "or", "boost": 0.5}}},
          // Compact mm: "100mm" matchuje produkty bez spacji
          {"match": {"searchable_text": {"query": "COMPACT_MM", "operator": "or", "boost": 0.5}}},
          // Join kodów modeli: "z5II" matchuje połączone kody
          {"match": {"searchable_text": {"query": "JOINED_QUERY", "operator": "or", "boost": 0.5}}},
          // EAN / ID produktu exact match + prefix match
          {"term": {"ean": {"value": "RAW_QUERY", "boost": 50}}},
          {"prefix": {"ean": {"value": "RAW_QUERY", "boost": 40}}},
          {"term": {"id_verto": {"value": "RAW_QUERY", "boost": 50}}},
          {"prefix": {"id_verto": {"value": "RAW_QUERY", "boost": 40}}}
        ],
        "minimum_should_match": 1
      }
    }],
    "should": [
      // searchable_text AND (wszystkie termy) — najsilniejszy sygnał
      {"match": {"searchable_text": {"query": "...", "operator": "and", "boost": 20}}},
      // Split AND: "rs 5" matchuje gimbal "RS 5"
      {"match": {"searchable_text": {"query": "SPLIT", "operator": "and", "boost": 15}}},
      // Compact mm AND: "35-100mm" matchuje obiektyw
      {"match": {"searchable_text": {"query": "COMPACT", "operator": "and", "boost": 15}}},
      // Joined AND: "z5II" matchuje model
      {"match": {"searchable_text": {"query": "JOINED", "operator": "and", "boost": 15}}},
      // Multi-token: dopasowanie 2+ terminów (ważona precyzja)
      {"match": {"searchable_text": {"query": "...", "minimum_should_match": 2, "boost": 40}}},
      // Compact mm phrase: dokładna sekwencja ogniskowej
      {"match_phrase": {"searchable_text": {"query": "COMPACT", "boost": 30}}},
      // Dokładna fraza w nazwie
      {"match_phrase": {"name.exact": {"query": "...", "boost": 15}}},
      // Nazwa AND + split/joined warianty
      {"match": {"name": {"query": "...", "operator": "and", "boost": 10}}},
      {"match": {"name": {"query": "SPLIT", "operator": "and", "boost": 10}}},
      {"match": {"name": {"query": "JOINED", "operator": "and", "boost": 10}}},
      // Nazwa OR
      {"match": {"name": {"query": "...", "operator": "or", "boost": 3}}},
      // Marka keyword exact match
      {"term": {"brand.keyword": {"value": "RAW_QUERY", "boost": 20}}},
      // Marka text match
      {"match": {"brand": {"query": "...", "boost": 8}}}
    ]
  }
}
```

**Warianty zapytania (generowane automatycznie):**

| Wariant         | Przykład                 | Cel                                      |
|-----------------|--------------------------|------------------------------------------|
| `query`         | "85 mm f/1.4"            | Znormalizowany tekst                     |
| `raw_query`     | "85mm f1.4"              | Oryginał dla exact match (EAN, ID)       |
| `split_query`   | "rs 5" (z "rs5")         | Kody modeli ze spacją                    |
| `joined_query`  | "z5II" (z "z5 II")       | Kody modeli bez spacji                   |
| `compact_mm`    | "85mm" (z "85 mm")       | Ogniskowe bez spacji                     |
| `nospace`       | "statyw" (z "sta tyw")   | Zapytanie bez spacji (przypadkowe spacje)|

**Pole `searchable_text`** to konkatenacja: nazwa + marka + kategoria.
Umożliwia cross-term matching (np. "lampa Godox" matchuje "lampa" z nazwy + "Godox" z marki).

### 3.2 Popularity Factor (mnożnik popularności z GA4)

```
popularity_factor = 1 + popularity_score * 0.15
```

Gdzie `popularity_score` jest pre-kalkulowany w ga_updater.py:
```
popularity_score = log1p(ga_views) * 1.0 + log1p(ga_sales) * 3.0
```

**Przykłady:**

| Produkt                          | Views  | Sales | pop_score | Mnożnik |
|----------------------------------|--------|-------|-----------|---------|
| Canon EOS R6 mark II             | 11 590 | 53    | 21.27     | 4.19x   |
| FujiFilm X100VI czarny           | 3 713  | 19    | 17.11     | 3.57x   |
| Canon RC-6 pilot                 | 122    | 4     | 8.95      | 2.34x   |
| Smallrig klatka (0 GA data)      | 0      | 0     | 0.00      | 1.00x   |

**Parametr tuningowy:** Współczynnik `0.15` jest JEDYNYM pokrętłem do regulacji wpływu popularności.
- 0.05 = łagodny wpływ (popularność ledwo widoczna)
- 0.10 = umiarkowany
- 0.15 = silny wpływ (AKTUALNY — użytkownik zażądał: "niech decyduje popularność")
- 0.20 = bardzo silny wpływ

### 3.3 Konfiguracja function_score w Elasticsearch

```json
{
  "function_score": {
    "query": { "bool": { ... } },
    "functions": [
      {
        "script_score": {
          "script": {"source": "1 + doc['popularity_score'].value * 0.15"}
        }
      }
    ],
    "score_mode": "multiply",
    "boost_mode": "multiply"
  }
}
```

- `score_mode: multiply` — funkcje mnożone między sobą
- `boost_mode: multiply` — wynik function_score mnoży text_relevance
- **Brak availability boost** — dostępność nie wpływa na ranking

---

## 4. Indeksowanie i mapping

### 4.1 Analyzery

| Analyzer              | Użycie                    | Filtry                          |
|-----------------------|---------------------------|---------------------------------|
| polish_with_synonyms  | name, searchable_text     | lowercase, synonymy, polish_stop |
| polish_analyzer       | description, category     | lowercase, polish_stop          |
| autocomplete_analyzer | name.autocomplete         | lowercase, edge_ngram (2-15)    |
| search_analyzer       | search na autocomplete    | lowercase (bez ngram)           |

### 4.2 Synonimy (uniwersalne)

```
fuji, fujifilm
pana, panasonic
obiektyw, lens
aparat, kamera, camera
statyw, tripod
lampa, flash, blyskowa
filtr, filter
plecak, backpack
torba, bag
mikrofon, mic
bateria, akumulator, battery
```

### 4.3 Pola w indeksie

| Pole             | Typ       | Analyzer              | Cel                              |
|------------------|-----------|-----------------------|----------------------------------|
| id_verto         | keyword   | —                     | Identyfikator produktu (główny) |
| id_internal      | keyword   | —                     | ID wewnętrzny (matching z GA4)  |
| ean              | keyword   | —                     | Kod EAN                         |
| name             | text      | polish_with_synonyms  | Nazwa produktu (główne pole)    |
| name.exact       | text      | standard              | Phrase matching                  |
| name.autocomplete| text      | autocomplete_analyzer | Edge ngram autocomplete         |
| name.keyword     | keyword   | —                     | Sortowanie A-Z                  |
| description      | text      | polish_analyzer       | Opis produktu                    |
| brand            | text      | standard              | Marka (text + keyword subfield) |
| brand.keyword    | keyword   | —                     | Filtrowanie, agregacje          |
| category         | text      | polish_analyzer       | Pełna ścieżka kategorii         |
| category_lvl0    | keyword   | —                     | Kategoria poziom 0 (facet)      |
| searchable_text  | text      | polish_with_synonyms  | Połączenie: name+brand+category |
| price            | float     | —                     | Cena                            |
| availability     | keyword   | —                     | in stock / out of stock / preorder |
| image            | keyword   | —                     | URL obrazka (nie indeksowany)   |
| ga_views         | integer   | —                     | Wyświetlenia z GA4 (90 dni)    |
| ga_sales         | integer   | —                     | Sprzedaż z GA4 (90 dni)        |
| popularity_score | float     | —                     | Pre-kalkulowany scoring GA      |

---

## 5. Integracja z Google Analytics 4

### 5.1 Pobieranie danych

- **Views**: wymiar `pagePath`, metryka `screenPageViews`, filtr `-p.html`
  - Matching: URL path produktu → `link` w indeksie
- **Sales**: wymiar `itemId`, metryka `itemsPurchased`
  - Matching: GA `itemId` → `id_internal` w indeksie
- **Okres**: ostatnie 90 dni
- **Limit**: 10 000 rekordów per raport

### 5.2 Obliczanie popularity_score

```python
popularity_score = log1p(ga_views) * 1.0 + log1p(ga_sales) * 3.0
```

Wagi: sprzedaż ma 3x większe znaczenie niż wyświetlenia, ponieważ:
- Views mogą być przypadkowe (SEO, reklamy)
- Sales to potwierdzony sygnał jakości/popularności produktu
- Użycie log1p zapobiega dominacji ekstremalnych wartości

### 5.3 Harmonogram aktualizacji

Rekomendacja: codziennie o 4:00 rano (niższy ruch).
Kolejność: 1) Pobierz feed → 2) Reindeksuj → 3) Pobierz GA → 4) Zaktualizuj popularity_score

---

## 6. Preprocessing zapytania (ekstrakcja filtrów semantycznych)

Przed wykonaniem zapytania ES, fraza wyszukiwania jest analizowana w celu
ekstrakcji filtrów semantycznych. Reguły są **UNIWERSALNE** — działają
identycznie dla każdego zapytania.

### 6.1 Słowo "używany/używane" → filtr condition=used (WYKLUCZAJĄCY)

Jeśli fraza zawiera słowo "używany", "używane", "używanych" itp.:
- Słowo jest usuwane z frazy tekstowej
- Dodawany jest TWARDY filtr: `condition = "used"`
- Wyświetlane są WYŁĄCZNIE produkty używane

**Uzasadnienie:** Nikt wpisujący "używany" nie szuka nowego produktu.
To jedyne bezpieczne założenie — filtr jest wykluczający.

**Przykłady:**
| Zapytanie           | Cleaned query | Filtry                         |
|---------------------|---------------|--------------------------------|
| "Canon używany"     | "Canon"       | brand=Canon, condition=used    |
| "używane"           | (match_all)   | condition=used                 |
| "Sony A7 używany"   | "Sony A7"     | brand=Sony, condition=used     |

### 6.1b Łączenie rozbitych marek (extra-space brand joining)

**Problem:** Użytkownik przypadkowo wstawia spację w środek nazwy marki:
"smal lrig", "pana sonic", "elin chrom". Tokenizer tworzy dwa osobne tokeny,
które nie matchują żadnej marki — w efekcie filtr marki nie jest stosowany,
a wyniki są nietrafne.

**Rozwiązanie (UNIWERSALNE):** Przed detekcją marki, dla każdej pary sąsiednich
tokenów sprawdzamy czy ich złączenie tworzy znaną markę (z cache ES + aliasy).
Jeśli tak — tokeny są łączone w jeden.

**Kolejność:** Krok wykonywany PRZED detekcją marki (6.2) i splitowaniem (6.1c),
aby zapobiec fałszywym rozpoznaniom na fragmentach nazwy.

**Przykłady:**

| Zapytanie użytkownika | Po złączeniu   | Rozpoznana marka |
|-----------------------|----------------|------------------|
| "smal lrig"           | "Smallrig"     | Smallrig         |
| "pana sonic"          | "Panasonic"    | Panasonic        |
| "elin chrom"          | "Elinchrom"    | Elinchrom        |

### 6.1c Rozdzielanie sklejonych marek (no-space brand splitting)

**Problem:** Użytkownik wpisuje zapytanie bez spacji między marką a resztą:
"sonya6700", "canonrf", "peakdesignpaski". Tokenizer traktuje to jako jeden token,
który nie matchuje ani marki, ani nazwy produktu.

**Rozwiązanie (UNIWERSALNE):** Dla każdego tokena sprawdzamy czy zaczyna się
od znanej nazwy marki (z cache ES ~300+ marek oraz aliasów). Jeśli tak,
a reszta tokena ma ≥2 znaki, token jest dzielony na granicy marki.

**Warunek minimalnej długości reszty:** Reszta po odcięciu marki musi mieć ≥2 znaki.
Zapobiega to fałszywym splitom dla krótkich sufiksów (np. "canoni" → NIE dzieli
na "canon" + "i").

**Kolejność:** Krok wykonywany PO łączeniu rozbitych marek (6.1b), ale PRZED
właściwą detekcją marki (6.2).

**Przykłady:**

| Zapytanie użytkownika | Po rozdzieleniu        | Rozpoznana marka |
|-----------------------|------------------------|------------------|
| "sonya6700"           | "Sony a6700"           | Sony             |
| "canonrf"             | "Canon rf"             | Canon            |
| "peakdesignpaski"     | "PEAKDESIGN paski"     | Peak Design      |

### 6.2 Nazwa marki → filtr brand (WYKLUCZAJĄCY)

Jeśli fraza zawiera rozpoznaną nazwę marki:
- Dodawany jest TWARDY filtr: `brand.keyword = "NazwaMarki"`
- Wyświetlane są WYŁĄCZNIE produkty tej marki
- Nazwa marki pozostaje w tekście zapytania (dla scoringu)

**Wyjątki od filtra marki:**

1. **Przyimek kompatybilności** ("do", "dla", "na", "pod") przed marką:
   - "obiektyw do Canon" → BEZ filtra marki (szukamy obiektywów PASUJĄCYCH do Canon)
   - "lampa dla Nikon" → BEZ filtra marki
   - Użytkownik szuka produktów kompatybilnych, nie tylko jednej marki

2. **Wiele marek w zapytaniu:**
   - "Sigma 24-70 Canon" → BEZ filtra marki (intencja niejednoznaczna)
   - Może oznaczać "obiektyw Sigma na Canon mount"

**Przykłady:**
| Zapytanie             | Brand filter | Uzasadnienie                     |
|-----------------------|-------------|----------------------------------|
| "Canon"               | Canon       | Jednoznaczna marka               |
| "Canon R6"            | Canon       | Marka + model                    |
| "obiektyw do Canon"   | (brak)      | Przyimek "do" = kompatybilność   |
| "lampa dla Nikon"     | (brak)      | Przyimek "dla" = kompatybilność  |
| "Sigma 24-70 Canon"   | (brak)      | Dwie marki = niejednoznaczne     |
| "Sony używane"        | Sony        | Marka + condition                |

### 6.3 Cache marek

Przy pierwszym zapytaniu pobierany jest pełny słownik marek z indeksu ES
(agregacja `brand.keyword`, ~300 marek). Cache jest trzymany w pamięci
procesu i nie wymaga odświeżania przy każdym zapytaniu.

Porównanie jest case-insensitive: "canon" matchuje "Canon", "DJI" matchuje "DJI".
Minimalna długość marki: 2 znaki (unika false positives dla inicjałów).

### 6.4 Normalizacja notacji obiektywowej

Po ekstrakcji filtrów semantycznych, fraza jest normalizowana do dominującej
konwencji nazewnictwa w feedzie produktowym. Jest to konieczne, ponieważ:

- **94% obiektywów** używa "XX mm" ze spacją (np. "85 mm", "24-70 mm")
- **93% obiektywów** używa "f/X.X" ze slashem (np. "f/2.8", "f/1.4")
- Tokenizer standard traktuje "85mm" jako JEDEN token, a "85 mm" jako DWA
- Dlatego query "85mm" NIE matchuje produktów z "85 mm" w nazwie

**Reguły normalizacji:**

| Wzorzec użytkownika | Po normalizacji | Regex                                          |
|---------------------|-----------------|-------------------------------------------------|
| `85mm`              | `85 mm`         | `(\d)\s*mm\b` → `\1 mm`                        |
| `f2.8` / `F2.8`    | `f/2.8`         | `\b[fF](\d)` → `f/\1`                          |
| `f/2,8`             | `f/2.8`         | `(f/\d+),(\d)` → `\1.\2`                       |
| `f/3,5-5,6`         | `f/3.5-5.6`     | Comma-to-dot w kontekście zakresu przysłony     |

**Ważne:** Normalizacja jest stosowana WYŁĄCZNIE do pól tekstowych
(searchable_text, name, brand text). Pola exact-match (EAN, id_verto,
brand.keyword) używają oryginalnej frazy (raw_query), aby nie łamać
wyszukiwania po kodach produktu (np. "SEL85F14GM" → "F14" w kodzie
NIE jest normalizowane do "f/14").

### 6.5 Normalizacja kodów modeli (split/join)

**Problem:** Nazwy produktów są niespójne: "RS 5" vs "RS5", "Z fc" vs "Zfc", "Z5II" vs "Z5 II".
Tokenizer traktuje je różnie (1 token vs 2 tokeny). Użytkownik może wpisać dowolną formę.

**Rozwiązanie (query-time, UNIWERSALNE):** Generujemy 3 warianty zapytania:

1. **Split** — rozdzielamy na granicach litera↔cyfra: "rs5" → "rs 5", "a7iv" → "a 7 iv"
   - Dodatkowo: 3-znakowe słowa alfa dzielimy po 1. literze: "zfc" → "z fc"
2. **Join** — łączymy sąsiedzkie tokeny litera+cyfra: "z5 II" → "z5II", "z fc" → "zfc"
3. **Compact mm** — usuwamy spacje przed mm: "100 mm" → "100mm"

Wszystkie warianty są wyszukiwane RÓWNOCZEŚNIE jako should clauses (boost 0.5 w must,
boost 15 w should AND). Oryginalny query pozostaje głównym matchem.

**Swap joined↔original dla krótkich tokenów:** Gdy WSZYSTKIE tokeny zapytania mają ≤3 znaki
(np. "ml 087", "220 C", "rs 5"), joined form staje się PRIMARY query, a oryginał trafia
do joined_query. Dzięki temu "manfrotto ml 087" i "manfrotto ml087" dają identyczne wyniki.
Po swapie warianty (split, soft_split, compact_mm) są rekomputowane z nowego primary query.

**Wyjątek od swapu:** Jeśli któryś token to jednostka miary (mm, cm, gb, tb, mb, kg),
swap NIE jest wykonywany. "50 mm" to notacja obiektywowa, nie kod modelu — musi pozostać
jako dwa tokeny żeby matchować "50 mm" w nazwach produktów.

**Przykłady:**

| Zapytanie użytkownika | Split           | Join    | Dopasowany produkt    |
|-----------------------|-----------------|---------|-----------------------|
| "nikon zfc"           | "z fc"          | "zfc"   | Nikon Z fc            |
| "nikon z5 II"         | "z 5 II"        | "z5II"  | Nikon Z5II body       |
| "dji rs5"             | "rs 5"          | "rs5"   | DJI Gimbal RS 5 Combo |
| "fuji x100vi"         | "x 100 vi"      | "x100vi"| FujiFilm X100VI       |
| "manfrotto ml 087"    | "ml 087"        | "ml087" | Manfrotto ML087NWB    |

### 6.5b Wariant nospace (zapytanie bez spacji)

**Problem:** Użytkownik przypadkowo wstawia spację w środek słowa:
"sta tyw", "ple cak", "obi ektyw". Żaden z tokenów nie matchuje poprawnie,
bo "sta" i "tyw" to niekompletne fragmenty słowa "statyw".

**Rozwiązanie (UNIWERSALNE):** Dla zapytań zawierających spacje generowany jest
dodatkowy wariant `nospace` — zapytanie z usuniętymi wszystkimi spacjami.
Wariant jest dodawany jako dodatkowa klauzula wyszukiwania (should clause),
analogicznie do wariantów split/join/compact_mm.

**Przykłady:**

| Zapytanie użytkownika | Wariant nospace | Dopasowany produkt        |
|-----------------------|-----------------|---------------------------|
| "sta tyw"             | "statyw"        | Statyw Manfrotto...       |
| "ple cak"             | "plecak"        | Plecak fotograficzny...   |
| "obi ektyw"           | "obiektyw"      | Obiektyw Canon...         |

**Uwaga:** Wariant nospace jest generowany TYLKO gdy zapytanie zawiera spacje.
Dla zapytań bez spacji (np. "statyw") krok jest pomijany.

### 6.6 Marki wielowyrazowe

**Problem:** Marki takie jak "Peak Design", "Carl Zeiss", "OM System" składają się z 2-3 słów.
Preprocessing sprawdzający każde słowo osobno nie rozpoznaje ich jako jednej marki.

**Rozwiązanie (UNIWERSALNE):** Detekcja marek przebiega w 2 przebiegach:
1. **Ngramy 3- i 2-wyrazowe** — sprawdzamy frazy "peak design", "carl zeiss" w cache marek
2. **Słowa pojedyncze** — sprawdzamy każde niezamatchowane słowo

Kolejność gwarantuje, że "Peak Design" jest rozpoznane jako jedna marka (nie "peak" + "design").

**Brand groups:** Jedna marka może mieć wiele wariantów w feedzie:
"Peak Design" i "PEAKDESIGN" — filtr używa `terms` z listą WSZYSTKICH wariantów brand.keyword.

### 6.7 Aliasy marek (rozwiązywanie literówek i synonimów)

Plik `brand_aliases.json` zawiera 202 reguły mapujące typowe błędy ortograficzne
i alternatywne nazwy marek na nazwy kanoniczne z indeksu ES.

**Przykłady:**
| Wpisane przez użytkownika | Rozwiązane do | Typ         |
|---------------------------|---------------|-------------|
| "cannon"                  | Canon         | Literówka   |
| "nikkon"                  | Nikon         | Literówka   |
| "soni"                    | Sony          | Literówka   |
| "lumix"                   | Panasonic     | Alias marki |
| "fuji"                    | FujiFilm      | Skrót       |
| "simga"                   | Sigma         | Literówka   |
| "godoks"                  | Godox         | Literówka   |
| "carl zeiss", "zeiss"     | Carl Zeiss    | Alias       |

**Mechanizm:** Rozwiązywanie działa na poziomie `preprocess_query()`:
1. Słowo jest porównywane z cache marek z indeksu ES (brand.keyword)
2. Jeśli nie znaleziono, słowo jest porównywane z mapą aliasów
3. Jeśli alias pasuje, słowo jest podmieniane na nazwę kanoniczną
4. Filtr marki jest stosowany na nazwie kanonicznej

### 6.7b Automatyczna tolerancja na ucięcia nazw marek

**Problem:** Klienci często nie dopisują ostatnich 1-2 znaków nazwy marki:
"manfrott" (zamiast Manfrotto), "panasoni" (zamiast Panasonic), "hasselbla" (zamiast Hasselblad).

**Rozwiązanie (UNIWERSALNE, algorytmiczne):** W `preprocess_query()`, gdy słowo ma ≥5 znaków
i nie pasuje do żadnej marki ani aliasu, sprawdzane jest czy jest **prefiksem** znanej marki
z różnicą max 2 znaków. Działa automatycznie dla wszystkich 300+ marek bez ręcznych reguł.

```python
# W pętli single-word brand matching:
elif len(wl) >= 5:
    for brand_lower, brand_original in brands.items():
        if brand_lower.startswith(wl) and len(brand_lower) - len(wl) <= 2:
            canonical = brand_original
            break
```

**Przykłady:**
| Wpisane       | Rozpoznane  | Ucięcie  |
|---------------|-------------|----------|
| "manfrott"    | Manfrotto   | -1 znak  |
| "panasoni"    | Panasonic   | -1 znak  |
| "hasselbla"   | Hasselblad  | -1 znak  |
| "fujifil"     | FujiFilm    | -1 znak  |
| "blackmagi"   | Blackmagic  | -1 znak  |
| "sandis"      | Sandisk     | -1 znak  |
| "viltro"      | Viltrox     | -1 znak  |
| "viltr"       | Viltrox     | -2 znaki |

### 6.8 Kody produktów i EAN-y (exact + prefix matching)

Wyszukiwarka rozpoznaje kody produktowe (id_verto) i kody kreskowe (EAN).
Obsługiwane są zarówno **dokładne** jak i **częściowe** kody:

- `term` query (boost 50) — dokładne dopasowanie kodu
- `prefix` query (boost 40) — dopasowanie początku kodu

Dzięki temu użytkownik nie musi wpisywać pełnego kodu — wystarczy początek:
- "ACFCANEOSR6" → Canon EOS R6 mark II
- "489711693022" → Kodak EKTAR H35 (brak ostatniej cyfry EAN)

### 6.9 Prefix matching (obsługa polskiej odmiany)

**Problem:** Analizator ES nie używa polskiego stemmera (plugin `analysis-stempel`
nie jest zainstalowany). Dlatego "obiektyw" i "obiektywy" to różne tokeny.

**Rozwiązanie (query-time, UNIWERSALNE):**
Do sekcji `must` zapytania dodany jest `match_phrase_prefix`, który matchuje
prefiksowo: "obiektyw" → matchuje "obiektyw*" → "obiektywy", "obiektywu" itp.

**Rekomendacja produkcyjna:** Zainstalować plugin `analysis-stempel` na Elastic Cloud
i dodać filtr `polish_stem` do analizera `polish_with_synonyms`. To wyeliminuje
potrzebę prefix matching i poprawi recall dla WSZYSTKICH polskich odmian wyrazów.

---

## 7. Sugester (Autocomplete)

Sugester używa pełnego pipeline'u preprocessingu (detekcja "używany", filtr marki,
normalizacja obiektywowa, split/join kodów modeli) z function_score opartym na popularności.

**Zapytania brand-only** (np. "Peak Design", "Canon"): produkty sortowane WYŁĄCZNIE
po `popularity_score` (desc), potem `price` (desc) jako tiebreaker. Gwarantuje to,
że flagowce i najpopularniejsze produkty marki są widoczne na górze, a nie
nowe/losowe produkty z małych kategorii.

**Zapytania tekstowe**: używają function_score (text_relevance * popularity_factor)
identycznego z pełnym wyszukiwaniem.

### 7.1 Layout sugestera

Prosty układ — lista 8 produktów z miniaturkami i cenami:

```
+----------------------------------------------------------+
| [mini] Produkt 1 - nazwa                        Cena zł |
| [mini] Produkt 2 - nazwa                        Cena zł |
| [mini] Produkt 3 - nazwa                        Cena zł |
| ...                                                      |
| [mini] Produkt 8 - nazwa                        Cena zł |
+----------------------------------------------------------+
|              [ Pokaż wszystkie produkty ]                |
+----------------------------------------------------------+
```

### 7.2 Optymalizacja wydajności sugestera

| Optymalizacja                      | Efekt                                |
|------------------------------------|--------------------------------------|
| Singleton ES client (es_client.py) | Reużywa połączenie HTTP/TLS          |
| Warm cache na starcie (startup)    | Cache marek ładowany przy starcie    |
| Lekkie zapytanie suggest           | Bez script_score, mniej should clauses |
| Debounce 300ms (frontend)          | Mniej zapytań przy szybkim wpisywaniu |

**Wyniki pomiaru:**
- Przed: ~1.5-2.7s na zapytanie
- Po: ~0.27-0.31s na zapytanie
- **Przyspieszenie: ~8x**

---

## 8. Testy regresyjne

Plik `regression_tests.json` zawiera **51 testów** pokrywających całą logikę wyszukiwarki.
Każda zmiana w `search_engine.py` lub `es_mapping.py` MUSI przejść wszystkie testy.

### Uruchamianie:
```bash
python run_regression.py --verbose       # Wszystkie testy
python run_regression.py -t brand_canon  # Pojedynczy test
```

### Pliki danych:
- `brand_aliases.json` — 202 aliasy marek (literówki, synonimy)
- `taxon_aliases.json` — 230 aliasów kategorii (synonimy taksonów)

---

## 9. Rekomendacje produkcyjne

### 9.1 Zainstalować plugin analysis-stempel

Plugin `analysis-stempel` dodaje polski stemmer do Elasticsearch.
Aktualnie "obiektyw" i "obiektywy" to różne tokeny, co wymaga prefix matching.
Ze stemmerem, oba tokeny byłyby zredukowane do jednego rdzenia.

### 9.2 Reindeksować z nowymi synonimami

Aktualne synonimy w indeksie ES zawierają tylko ~10 reguł.
Nowy mapping w `es_mapping.py` ładuje 330+ reguł z plików JSON.
Wymaga ponownego utworzenia indeksu.

### 9.3 Serwer bez flagi --reload

W produkcji NIE używać `--reload`. Flaga powoduje restart workera
przy każdej zmianie pliku, co resetuje singleton ES client i brand cache.
