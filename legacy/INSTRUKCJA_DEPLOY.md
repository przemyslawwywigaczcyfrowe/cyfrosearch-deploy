# Instrukcja wdrożenia prototypu CyfroSearch

Postawienie demo od zera: **Bonsai (darmowy ES z polskim stempelem) + GA4 service account + GitHub Actions (indeksowanie) + Render (hosting)**.

Wszystko darmowe, bez karty. Łączny czas: ~40 minut (z GA4: +20 min na service account).

> **Aiven OpenSearch nie nadaje się** dla polskich sklepów — brak plugina `analysis-stempel`.
> Jeśli zacząłeś setup w Aiven, możesz zostawić lub usunąć (Aiven Console → Services → Delete).

---

## 1. Załóż darmowy Elasticsearch na Bonsai (~5 min)

1. Otwórz <https://bonsai.io/signup>
2. Załóż konto (email + hasło, bez karty)
3. **Create Cluster** → **Sandbox** (free, 125 MB), region **EU** (Frankfurt/Ireland)
4. **Access** → **Credentials** → skopiuj **Full Access URL**, np.:
   ```
   https://abc123:xyz789@cyfrosearch-1234567890.eu-central-1.bonsaisearch.net:443
   ```
5. **Plugins** w panelu klastra → upewnij się, że `analysis-stempel` i `analysis-icu` są **enabled**.

---

## 2. Skonfiguruj GA4 service account (~20 min)

### 2a. GCP project + Analytics Data API

1. <https://console.cloud.google.com> → wybierz swój projekt (lub utwórz nowy)
2. **APIs & Services** → **Library** → wyszukaj `Google Analytics Data API` → **Enable**

### 2b. Service account + klucz

1. **IAM & Admin** → **Service Accounts** → **Create Service Account**
2. Nazwa: `cyfrosearch-ga4-reader`, opis dowolny → **Create**
3. Role: pomiń (przyznamy w GA4) → **Done**
4. Wejdź w utworzony SA → zakładka **Keys** → **Add Key** → **Create new key** → **JSON** → ściągnie się plik `*.json`
5. **Zachowaj ten plik** — wkleisz całą zawartość jako GitHub secret w kroku 3

### 2c. Daj service accountowi dostęp do GA4 property

1. <https://analytics.google.com> → **Admin** (koło zębate)
2. W kolumnie **Property** → **Property Access Management**
3. **+** w prawym górnym → wklej email service accounta (postać `cyfrosearch-ga4-reader@<projekt>.iam.gserviceaccount.com`)
4. Role: **Viewer** → **Add**
5. Notatka: GA4 **Property ID** (numer, np. `123456789`) — znajdziesz w **Admin** → **Property Settings** → "Property ID" przy nagłówku. Zapisz, wpiszesz w secrets.

---

## 3. Ustaw GitHub Secrets (~3 min)

W repo: <https://github.com/przemyslawwywigaczcyfrowe/cyfrosearch-deploy/settings/secrets/actions>

| Name | Value |
|---|---|
| `ES_HOST` | Full Access URL z Bonsai (z embedded creds) |
| `GA4_PROPERTY_ID` | numeryczne ID property z GA4 (np. `123456789`) |
| `GA4_SA_JSON` | **cała** zawartość pliku JSON service accounta (wklej w pole "Value") |

Zostaw `ES_USER` / `ES_PASSWORD` / `ES_API_KEY` puste — nie trzeba ich tworzyć skoro URL z Bonsai zawiera creds.

---

## 4. Odpal pipeline (GitHub Actions) (~3-5 min)

1. <https://github.com/przemyslawwywigaczcyfrowe/cyfrosearch-deploy/actions/workflows/index.yml>
2. **Run workflow** → zostaw `recreate=false`, `es_index=products`, `skip_ga4=false` → **Run workflow**
3. Po ~3-5 min sprawdź logi:
   ```
   [*] Querying GA4 s30: 30daysAgo → yesterday
   [OK] s30: ~5-15k items with sales
   [*] Querying GA4 s365: 365daysAgo → yesterday
   [OK] s365: ~15-25k items with sales
   [OK] Wrote N SKUs to /tmp/.../sales_data.json
   ...
   [OK] Connected to ES 8.x — cluster '...'
   [*] Plugins: [..., 'analysis-icu', 'analysis-stempel', ...]
   [*] Feed contains 17556 products
   [*] Loaded N sales records
   [OK] Indexed ~17500 docs in ~60s
   [OK] Index 'products' now has ~17500 documents
   ```

> **Jeśli GA4 zawiedzie** (np. zły property ID, brak permission): odpal workflow ponownie z `skip_ga4=true` — użyje committed `sales_data.json` jako fallback (~16k SKU, stare ale działa).

---

## 5. Postaw aplikację na Render (~10 min)

1. <https://dashboard.render.com> → **New** → **Blueprint**
2. **Connect a repository** → wybierz `cyfrosearch-deploy`
3. Render wykryje `render.yaml` i pokaże usługę `cyfrosearch-demo` → **Apply**
4. Przy pierwszym deployu poprosi o env vars:
   - **ES_HOST** → wklej ten sam URL co do GitHub Secret
   - **ES_USER**, **ES_PASSWORD**, **ES_API_KEY** → puste
   - **ES_INDEX** → już `products`
5. Build + start ~3-5 min

Render poda URL `https://cyfrosearch-demo-XXXX.onrender.com`.

---

## 6. Weryfikacja (~1 min)

- `https://<url>/api/health` → `{"ok": true, "es_version": "8.x", "doc_count": ~17500}`
- `https://<url>/` → demo, wpisz `canon`, `obiektyw`, `obiektywy do nikona`, `drony`
- Pierwsze otwarcie po przerwie zajmie ~30s (cold start Render free tier)

---

## Co dalej

- **Reindex po nowych danych GA4:** odpal workflow z `recreate=true` (wyczyści indeks i zbuduje od zera ze świeżymi danymi sprzedażowymi)
- **Render free usypia po 15 min** — wpierw zapytanie wybudza, kolejne są szybkie. Płatny `$7/mc` eliminuje cold start.
- **Bonsai Sandbox 125 MB / 35k docs** — dataset 17.5k zajmuje ~50 MB, masz zapas

## Troubleshooting

| Problem | Co sprawdzić |
|---|---|
| GA4 step: `403 PERMISSION_DENIED` | Service account nie ma dostępu do property — wróć do **2c** |
| GA4 step: `INVALID_ARGUMENT` | Zły `GA4_PROPERTY_ID` (musi być sam numer, nie `properties/123...`) |
| GA4 step: 0 wierszy | Property nie ma e-commerce event tracking, lub złe daty — spróbuj `GA4_METRIC=itemPurchaseQuantity` |
| `/api/health` 500 | Logi Render → najczęściej zły `ES_HOST` lub Bonsai cluster śpi |
| Wyszukiwanie pusto | Indeks pusty — workflow z `recreate=true` |
| `analysis-stempel not detected` | Bonsai → Plugins → enable, potem reindex |
