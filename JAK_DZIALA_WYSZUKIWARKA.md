# Jak działa wyszukiwarka Cyfrowe.pl

Dokument wyjaśniający krok po kroku, co się dzieje od momentu wpisania frazy do momentu wyświetlenia wyników. Napisany prostym językiem dla osób nietechnicznych.

---

## Krok 1: Użytkownik wpisuje frazę

Gdy klient zaczyna pisać w polu wyszukiwania, wyszukiwarka czeka 300 milisekund po ostatnim wciśniętym klawiszu (żeby nie wysyłać zapytania po każdej literze). Po tych 300ms fraza trafia do serwera.

**Przykład:** Klient wpisuje `Canon używany obiektyw 85mm f2.8`

---

## Krok 2: Analiza frazy — co klient miał na myśli?

Zanim wyszukiwarka cokolwiek wyszuka, najpierw **rozbiera frazę na części** i stara się zrozumieć intencję klienta. Robi to w kilku etapach:

### Etap A: Czy klient szuka produktów używanych?

Wyszukiwarka sprawdza, czy we frazie jest słowo "używany", "używane", "używanych" itp. Jeśli tak:
- **Usuwa to słowo z frazy** (bo to nie jest nazwa produktu, tylko filtr)
- **Włącza twardy filtr**: od teraz wyniki będą zawierać WYŁĄCZNIE produkty oznaczone jako używane

W naszym przykładzie: słowo "używany" zostaje usunięte. Fraza staje się `Canon obiektyw 85mm f2.8`, a filtr `condition=used` jest aktywny.

### Etap B: Czy klient szuka konkretnej marki?

Wyszukiwarka ma w pamięci listę **ponad 300 marek** ze sklepu (Canon, Sony, Nikon, Sigma, Godox, Peak Design itd.). Sprawdza, czy któreś słowo z frazy to nazwa marki.

W naszym przykładzie: "Canon" to znana marka → włączany jest filtr `brand=Canon`. Od teraz wyniki będą zawierać WYŁĄCZNIE produkty Canon.

**Ale uwaga — są dwa wyjątki:**

1. **"obiektyw DO Canon"** — słówko "do" przed marką oznacza, że klient szuka produktów *kompatybilnych* z Canon, niekoniecznie marki Canon. Wtedy filtr marki NIE jest włączany. To samo dotyczy słów "dla", "na", "pod".

2. **"Sigma 24-70 Canon"** — dwie marki w jednej frazie. Klient prawdopodobnie szuka obiektywu Sigma pasującego do aparatu Canon. Intencja jest niejednoznaczna, więc filtr marki NIE jest włączany.

### Etap C: Czy klient zrobił literówkę w nazwie marki?

Wyszukiwarka radzi sobie z błędami w nazwach marek na dwa sposoby:

**Baza typowych literówek (202 reguły ręczne):**
- "cannon" → rozpoznaje jako Canon
- "sygma" → rozpoznaje jako Sigma
- "fuji" → rozpoznaje jako FujiFilm
- "lumix" → rozpoznaje jako Panasonic
- "manfroto" → rozpoznaje jako Manfrotto

**Automatyczna tolerancja na ucięte nazwy:**

Klienci często nie dopisują ostatniej litery lub dwóch liter nazwy marki — np. piszą "manfrott" zamiast "Manfrotto" albo "panasoni" zamiast "Panasonic". Wyszukiwarka automatycznie rozpoznaje takie ucięcia dla **wszystkich 300+ marek** — nie trzeba dodawać osobnych reguł. Działa to tak: jeśli wpisane słowo (min. 5 znaków) nie pasuje do żadnej marki, wyszukiwarka sprawdza czy jest początkiem nazwy znanej marki (z różnicą max 2 znaków).

Przykłady:
- "manfrott" → Manfrotto (brak ostatniego "o")
- "panasoni" → Panasonic (brak "c")
- "hasselbla" → Hasselblad (brak "d")
- "fujifil" → FujiFilm (brak "m")
- "blackmagi" → Blackmagic (brak "c")
- "sandis" → Sandisk (brak "k")

Dzięki temu klient nie musi znać dokładnej pisowni marki — wystarczy wpisać jej przybliżony początek.

**Przypadkowa spacja w środku nazwy marki:**

Zdarza się, że klient przypadkowo wstawi spację w środku nazwy marki — np. wpisze "smal lrig" zamiast "smallrig" albo "pana sonic" zamiast "panasonic". Wyszukiwarka rozpoznaje, że te dwa słowa obok siebie tworzą razem nazwę znanej marki, i automatycznie je łączy. Klient nawet nie zauważy pomyłki — wyniki będą poprawne.

**Marka wklejona do kodu produktu (bez spacji):**

Klienci czasem wpisują markę i kod produktu jednym ciągiem, bez spacji — np. "sonya6700", "canonr8" czy "peakdesignpaski". Wyszukiwarka rozpoznaje znaną markę na początku takiego ciągu i automatycznie ją oddziela. Dzięki temu "sonya6700" jest traktowane tak samo, jakby klient wpisał "sony a6700".

### Etap D: Normalizacja zapisu technicznego

Klienci piszą parametry obiektywów na wiele sposobów, ale w katalogu sklepu jest jeden format. Wyszukiwarka automatycznie przetwarza frazę do formatu katalogu:

- `85mm` → `85 mm` (dodanie spacji, bo w katalogu jest "85 mm")
- `f2.8` → `f/2.8` (dodanie ukośnika, bo w katalogu jest "f/2.8")
- `f/2,8` → `f/2.8` (zamiana polskiego przecinka na kropkę)

W naszym przykładzie: `85mm f2.8` staje się `85 mm f/2.8`.

### Etap E: Generowanie wariantów zapisu modeli

Nazwy produktów w sklepie bywają niespójne — ten sam model może być zapisany jako "RS 5" lub "RS5", "Z fc" lub "Zfc". Wyszukiwarka generuje **kilka wariantów** frazy i szuka ich wszystkich jednocześnie:

| Klient wpisał | Warianty, które wyszukiwarka sprawdza |
|---------------|---------------------------------------|
| dji rs5       | "rs5" oraz "rs 5"                     |
| dji rs 5      | "rs 5" oraz "rs5"                     |
| nikon zfc     | "zfc" oraz "z fc"                     |
| sony a7iv     | "a7iv" oraz "a7 iv"                   |
| r6mark        | "r6mark" oraz "r6 mark"               |
| manfrotto ml 087 | "ml087" oraz "ml 087"              |

**Ważne:** Wyszukiwarka daje **identyczne wyniki** niezależnie od tego, czy klient wpisze kod ze spacją czy bez. "manfrotto ml087" i "manfrotto ml 087" pokażą te same produkty w tej samej kolejności. Gdy wszystkie fragmenty frazy są krótkie (1-3 znaki), wyszukiwarka automatycznie łączy je w jeden ciąg i traktuje jako kod produktu.

Jedyny wyjątek: frazy z jednostkami miar ("50 mm", "128 gb") NIE są łączone — tam spacja jest istotna dla prawidłowego wyszukiwania parametrów technicznych.

### Etap F: Przypadkowa spacja w środku słowa

Klienci czasem przypadkowo wstawią spację w środku zwykłego słowa — np. wpiszą "sta tyw" zamiast "statyw" albo "obiek tyw" zamiast "obiektyw". Wyszukiwarka automatycznie próbuje też wariantu bez spacji — skleja słowa razem i sprawdza, czy taki zapis daje lepsze wyniki. Dzięki temu literówka typu przypadkowa spacja nie powoduje pustych wyników.

**Po zakończeniu analizy** nasza przykładowa fraza wygląda tak:
- Oryginał: `Canon używany obiektyw 85mm f2.8`
- Po przetworzeniu: tekst `obiektyw 85 mm f/2.8` + filtr marki Canon + filtr condition=used

---

## Krok 2b: Bazy wiedzy, z których korzysta wyszukiwarka

Opisane powyżej etapy analizy frazy nie działają "z powietrza" — opierają się na trzech bazach wiedzy, które wyszukiwarka ładuje do pamięci przy starcie:

### Baza marek (~300 marek z katalogu)

Wyszukiwarka przy starcie pobiera z bazy produktów listę wszystkich marek, które faktycznie występują w katalogu sklepu. Dzięki temu wie, że "Canon", "Sony", "Peak Design" to marki, ale "obiektyw" czy "statyw" — nie.

Lista marek odświeża się automatycznie — jeśli w feedzie pojawi się nowa marka, wyszukiwarka ją rozpozna po restarcie serwera.

**Ważne:** Wyszukiwarka rozpoznaje też marki wielowyrazowe. "Peak Design" to jedna marka (nie "peak" + "design" osobno). Sprawdzanie odbywa się od dłuższych fraz do krótszych: najpierw 3-wyrazowe ("3 Legged Thing"), potem 2-wyrazowe ("Peak Design", "Carl Zeiss", "OM System"), a na końcu jednowyrazowe ("Canon", "Sony").

### Baza aliasów marek (202 reguły) — plik `brand_aliases.json`

To ręcznie przygotowana baza **alternatywnych nazw i literówek marek**. Zawiera 202 reguły, podzielone na kilka typów:

**Literówki** — klienci często robią te same błędy:
| Klient wpisuje | Wyszukiwarka rozumie jako |
|---|---|
| cannon, canoon | Canon |
| nikkon, nickon | Nikon |
| soni, sonny | Sony |
| sygma, simga | Sigma |
| manfroto, manfoto | Manfrotto |
| godoks, godrox | Godox |
| hasselblat | Hasselblad |

**Skróty i potoczne nazwy** — klienci używają uproszczonych nazw:
| Klient wpisuje | Wyszukiwarka rozumie jako |
|---|---|
| fuji | FujiFilm |
| pana | Panasonic |
| lumix | Panasonic |
| oly, olympus | OM System |
| bmpcc | Blackmagic |
| zeiss | Carl Zeiss |
| dji action | DJI |

**Warianty zapisu** — ta sama marka, inny sposób pisania:
| Klient wpisuje | Wyszukiwarka rozumie jako |
|---|---|
| peakdesign, peak-design | Peak Design |
| smallrig, small rig | Smallrig |
| 3leggedthing | 3 Legged Thing |
| gopro, go pro | GoPro |

Ta baza została wygenerowana na podstawie arkusza dostarczonego przez zespół Cyfrowe.pl i jest rozszerzana gdy testy na rzeczywistych frazach z Google Analytics ujawniają nowe warianty.

### Baza aliasów kategorii/taksonów (230 reguł) — plik `taxon_aliases.json`

Klienci szukając produktów używają różnych słów na tę samą kategorię. Na przykład:
- "lustrzanka" = "dslr" = "lustrzanka cyfrowa" — to ten sam typ aparatu
- "bezlusterkowiec" = "mirrorless" — to ten sam typ
- "obiektyw" = "lens" — polskie i angielskie słowo na to samo
- "statyw" = "tripod" — to samo
- "lampa" = "flash" = "błyskowa" — różne nazwy lampy błyskowej
- "filtr" = "filter" — polskie i angielskie
- "plecak" = "backpack" — polskie i angielskie
- "mikrofon" = "mic" — pełna nazwa i skrót

Te synonimy są wbudowane w silnik wyszukiwania (Elasticsearch). Gdy klient wpisze "lens", system automatycznie szuka też "obiektyw" i odwrotnie. Działa to w obie strony.

**Jak to wygląda w praktyce:**

Klient wpisuje "tripod karbon" → wyszukiwarka traktuje to jak "statyw karbon" → znajduje produkty z kategorii "Statywy" zawierające "karbon" w nazwie.

---

## Krok 3: Wyszukiwanie w Elasticsearch — co pasuje?

Przetworzona fraza trafia do silnika Elasticsearch, który przeszukuje ~17 000 produktów. Każdy produkt dostaje **punkty za trafność** — im lepiej pasuje do frazy, tym więcej punktów.

### Skąd wyszukiwarka wie, co pasuje?

Każdy produkt w bazie ma pole `searchable_text`, które łączy w sobie:
- **Nazwę produktu** (np. "Canon RF 85 mm f/1.4L VCM")
- **Markę** (np. "Canon")
- **Kategorię** (np. "Fotografia > Obiektywy > Stałoogniskowe")

Dzięki temu fraza "obiektyw 85 mm" matchuje produkt, nawet jeśli słowo "obiektyw" jest w kategorii, a "85 mm" w nazwie.

### Jak przydzielane są punkty za trafność?

Wyszukiwarka przyznaje punkty na kilku poziomach — od najsłabszego do najsilniejszego:

| Poziom dopasowania | Punkty | Przykład |
|---|---|---|
| Jedno słowo z frazy pasuje | niskie | "obiektyw" pasuje, ale "85 mm f/2.8" nie |
| Większość słów pasuje | średnie | "obiektyw 85 mm" pasuje (3 z 4 słów) |
| **Wszystkie słowa pasują** | **wysokie** | "obiektyw 85 mm f/2.8" — wszystko się zgadza |
| **Dokładna fraza w nazwie** | **bardzo wysokie** | "85 mm f/2.8" pojawia się w nazwie dokładnie w tej kolejności |
| **Kod produktu lub EAN** | **najwyższe** | Klient wpisał kod "ACFCANRF85F14" — to jest ten konkretny produkt |

Produkty, które **w ogóle nie pasują** do frazy, dostają 0 punktów i nie pojawiają się w wynikach.

### Tolerancja literówek

Wyszukiwarka toleruje drobne literówki również w nazwach produktów (nie tylko marek). Jeśli klient wpisze "schinobi" zamiast "shinobi", wyszukiwarka i tak znajdzie monitor Atomos SHINOBI II. Dopuszczalna jest różnica 1-2 znaków w zależności od długości słowa.

### Kody produktowe i EAN-y

Jeśli klient wpisuje kod produktu (np. ACFSONILCEA7V) lub kod kreskowy EAN, wyszukiwarka rozpoznaje to automatycznie i szuka dokładnego dopasowania. Co ważne — **wystarczy wpisać początek kodu**, nie trzeba go wpisywać w całości:

- `ACFCANEOSR6` (początek kodu) → znajdzie Canon EOS R6 mark II
- `489711693022` (EAN bez ostatniej cyfry) → znajdzie prawidłowy produkt

---

## Krok 4: Ranking — kto jest pierwszy?

Po tym jak Elasticsearch znalazł pasujące produkty i przyznał im punkty za trafność, następuje **mnożenie przez popularność**:

```
Pozycja w wynikach = Trafność tekstu  ×  Popularność produktu
```

### Skąd bierze się popularność?

Dane o popularności są pobierane z **Google Analytics 4** raz na dobę. Bierzemy:

1. **Wyświetlenia strony produktu** — ile razy klienci odwiedzili stronę tego produktu w ciągu ostatnich 90 dni
2. **Sprzedaż** — ile sztuk tego produktu zostało sprzedanych w ciągu ostatnich 90 dni

**Sprzedaż ma 3 razy większą wagę** niż wyświetlenia. Dlaczego? Bo wyświetlenie może być przypadkowe (ktoś kliknął z reklamy i od razu wyszedł), ale sprzedaż to potwierdzony sygnał, że produkt jest ważny dla klientów.

### Jak to działa w praktyce?

Wyobraźmy sobie, że dwa produkty pasują do frazy "Canon obiektyw 85mm":

| Produkt | Trafność tekstu | Popularność (mnożnik) | Wynik końcowy | Pozycja |
|---|---|---|---|---|
| Canon RF 85 mm f/1.4L VCM | 100 pkt | ×2.5 (popularny) | **250** | **#1** |
| Canon RF 85 mm f/2 Macro IS STM | 100 pkt | ×3.8 (bardzo popularny) | **380** | **#1** ← wygrywa! |
| Canon pokrywka na obiektyw 85mm | 40 pkt | ×1.0 (brak danych GA) | **40** | nisko |

Zauważ:
- Oba obiektywy pasują równie dobrze do frazy (100 pkt), ale f/2 Macro jest popularniejszy, więc wygrywa
- Pokrywka na obiektyw pasuje gorzej (40 pkt, bo "pokrywka" to nie to samo co "obiektyw"), a w dodatku nie ma danych GA, więc jest daleko w wynikach

### Dlaczego mnożenie, a nie dodawanie?

To jest kluczowa decyzja projektowa. Gdybyśmy **dodawali** popularność do trafności (np. 100 + 8 = 108), to popularność stanowiłaby zaledwie ~5% wyniku i byłaby praktycznie niewidoczna.

Przez **mnożenie** (100 × 2.5 = 250) popularność realnie wpływa na kolejność — najpopularniejsze produkty w danej kategorii są wyraźnie wyżej.

Jednocześnie mnożenie jest bezpieczne: produkt, który **nie pasuje** do frazy (0 punktów trafności), nigdy nie pojawi się w wynikach, bez względu na to jak jest popularny (0 × 2.5 = nadal 0).

---

## Co NIE wpływa na wyniki

### Dostępność na magazynie

Produkty, których nie ma aktualnie na stanie, **nie są karane** w wynikach. Wiele drogich i profesjonalnych produktów (kamery filmowe za 50 000 zł, obiektywy specjalistyczne) jest sprowadzanych na zamówienie od dostawców lub bezpośrednio od producenta. To, że produktu nie ma fizycznie na magazynie, nie oznacza, że nie powinien być wysoko w wynikach.

**O kolejności decyduje WYŁĄCZNIE popularność produktu**, a nie jego stan magazynowy.

### Cena produktu

Cena nie ma żadnego wpływu na pozycję w wynikach. Drogi aparat za 12 000 zł nie jest faworyzowany nad tanim akcesorium za 50 zł (ani odwrotnie). Jedyną rzeczą, która decyduje o kolejności poza trafnością tekstu, jest popularność z Google Analytics.

---

## Jak działa sugester (podpowiedzi podczas wpisywania)

Sugester to lista **8 produktów**, która pojawia się pod polem wyszukiwania w trakcie pisania. Używa dokładnie tego samego algorytmu co opisany powyżej (trafność × popularność), ale jest zoptymalizowany pod kątem szybkości — wyniki pojawiają się w około 0.3 sekundy.

Podpowiedzi aktualizują się po każdym wpisanym znaku (z 300ms opóźnieniem, żeby nie przeciążać serwera zbyt wieloma zapytaniami przy szybkim pisaniu).

---

## Najczęściej zadawane pytania

### "Dlaczego ten produkt jest pierwszy?"

Bo najlepiej pasuje do wpisanej frazy **i** jednocześnie jest najpopularniejszy wśród pasujących produktów (według danych z Google Analytics — wyświetlenia stron + sprzedaż).

### "Wpisałem nazwę produktu, ale go nie widzę w wynikach"

Najczęstsza przyczyna: nazwa produktu w feedzie jest inna niż to, co wpisał klient. Na przykład klient pisze "Sony A7 III", ale w feedzie produkt nazywa się "Sony A7III body (ILCE7M3B.CEC)". Wyszukiwarka stara się obsłużyć różne warianty zapisu (ze spacją i bez), ale jeśli nazwa w feedzie jest bardzo nietypowa, może nie zostać znaleziona.

### "Dlaczego używane produkty się nie pokazują?"

Produkty używane są ukryte domyślnie i pokażą się **dopiero gdy klient wpisze słowo "używany" lub "używane"**. Wtedy widoczne są WYŁĄCZNIE produkty używane — to twardy filtr, który wyklucza nowe produkty z wyników.

### "Dlaczego tanie akcesorium jest wyżej niż drogi aparat?"

Prawdopodobnie to akcesorium ma więcej wyświetleń i/lub sprzedaży w Google Analytics niż ten aparat. Ranking opiera się na popularności (dane GA), a nie na cenie produktu. Popularna pokrywka na obiektyw za 50 zł, którą kupuje 200 osób miesięcznie, będzie wyżej niż profesjonalny aparat za 30 000 zł, który kupuje 1 osoba na kwartał.

### "Dlaczego produkt niedostępny jest wysoko?"

Dostępność celowo nie wpływa na ranking. Wiele produktów profesjonalnych jest zamawianych na życzenie klienta bezpośrednio od producenta. Gdyby wyszukiwarka kaziła produkty niedostępne, klienci szukający drogiego sprzętu nie znajdowaliby go w wynikach.

### "Wpisałem 'obiektyw do Canon' i widzę obiektywy Sigma i Tamron — dlaczego?"

Bo słowo "do" przed marką oznacza, że szukasz produktów **pasujących do** Canon, a nie produktów **marki** Canon. Wyszukiwarka celowo nie filtruje po marce w takim przypadku — pokazuje obiektywy wszystkich marek kompatybilne z systemem Canon. Gdybyś chciał tylko Canon, wpisz po prostu "Canon obiektyw" (bez "do").

### "Jak mogę znaleźć produkt po kodzie?"

Wystarczy wpisać kod produktu (np. ACFCANEOSR6MKII) lub kod kreskowy EAN (np. 4549292197518) w pole wyszukiwania. Wyszukiwarka rozpozna, że to kod (a nie nazwa), i znajdzie dokładnie ten produkt. Nie trzeba wpisywać pełnego kodu — wystarczy jego początek.

---

*Dokument aktualizowany razem z kodem wyszukiwarki. Wersja techniczna dla programistów: [ALGORYTM_WYSZUKIWARKI.md](ALGORYTM_WYSZUKIWARKI.md)*
