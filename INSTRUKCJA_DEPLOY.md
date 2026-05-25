# Instrukcja wdrożenia prototypu CyfroSearch

Postawienie demo od zera: **Bonsai (darmowy ES) + GitHub Actions (indeksowanie) + Render (hosting)**.

Wszystko darmowe, bez karty kredytowej. Łączny czas: ~25 minut.

---

## 1. Załóż darmowy Elasticsearch na Bonsai (~5 min)

1. Otwórz <https://bonsai.io/signup>
2. Załóż konto (email + hasło, bez karty)
3. Po zalogowaniu: **Create Cluster** → **Sandbox** (free, 125MB)
4. Nazwa: dowolna (np. `cyfrosearch`), region: **EU** (Frankfurt lub Ireland)
5. Po utworzeniu klastra wejdź w **Access** → **Credentials**
6. Skopiuj **Full Access URL** — to jest pełny URL z wbudowanym loginem/hasłem, np.:
   ```
   https://abc123:xyz789@cyfrosearch-1234567890.eu-central-1.bonsaisearch.net:443
   ```

> **Sprawdź pluginy:** wejdź w `Plugins` w panelu Bonsai i upewnij się, że są
> aktywne `analysis-stempel` i `analysis-icu`. Powinny być włączone domyślnie
> na każdym Sandboxie — jeśli nie są, kliknij **Enable** przy każdym.

---

## 2. Ustaw GitHub Secrets (~2 min)

W repozytorium <https://github.com/przemyslawwywigaczcyfrowe/cyfrosearch-deploy>:

1. **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
2. Dodaj sekret:
   - **Name:** `ES_HOST`
   - **Value:** pełny URL z Bonsai (z `https://user:pass@...`)
3. Zostaw `ES_USER` / `ES_PASSWORD` / `ES_API_KEY` puste — nie trzeba ich tworzyć
   skoro URL zawiera już credentials.

---

## 3. Odpal indeksowanie (GitHub Actions) (~5 min)

1. W repo: **Actions** → **Index products to Elasticsearch** (lewa kolumna)
2. **Run workflow** (prawy górny róg) → zostaw `recreate=false`, `es_index=products` → **Run workflow**
3. Po ~2-4 min workflow skończy. Sprawdź logi — w `Run indexer` powinieneś zobaczyć:
   ```
   [OK] Connected to ES 8.x — cluster 'cyfrosearch-...'
   [*] Plugins: [..., 'analysis-icu', 'analysis-stempel', ...]
   [*] Feed contains 17556 products
   [*] Loaded 16262 sales records
   [OK] Indexed ~17500 docs in ~60s
   [OK] Index 'products' now has ~17500 documents
   ```

Jeśli widzisz `Plugin analysis-stempel not detected` — wróć do Bonsai i włącz plugin,
potem odpal workflow jeszcze raz z `recreate=true`.

---

## 4. Postaw aplikację na Render (~10 min)

1. Wejdź na <https://dashboard.render.com>
2. **New** → **Blueprint**
3. **Connect a repository** → wybierz `cyfrosearch-deploy`
4. Render wykryje `render.yaml` i pokaże usługę `cyfrosearch-demo`
5. Kliknij **Apply** — Render zacznie budować obraz Docker
6. Podczas pierwszego deployu Render zapyta o brakujące env vars:
   - **ES_HOST** → wklej ten sam URL co do GitHub Secrets
   - **ES_USER**, **ES_PASSWORD**, **ES_API_KEY** → zostaw puste
   - **ES_INDEX** → już ustawione na `products`
7. Poczekaj ~3-5 minut na deploy (Docker build + start)

Render poda URL w stylu `https://cyfrosearch-demo-XXXX.onrender.com`. Otwórz go.

---

## 5. Weryfikacja (~1 min)

W przeglądarce:

- `https://<twój-url>/api/health` → powinno zwrócić `{"ok": true, "es_version": "...", "doc_count": 17500}`
- `https://<twój-url>/` → demo z polem wyszukiwania
- Wpisz `canon`, `obiektyw`, `karta sd`, `drony` — powinno działać

---

## Co dalej?

- **Reindex po update feed-a:** odpal workflow z `recreate=true` żeby usunąć stary indeks i zbudować od zera.
- **Free tier Render usypia po 15 min nieaktywności** — pierwsze otwarcie po dłuższej przerwie zajmie ~30s (cold start). Płatny plan ($7/mc) eliminuje cold start.
- **Bonsai Sandbox limit:** 125MB / 35k dokumentów. Aktualny dataset (~17.5k) zajmie ~40-60MB, masz zapas.

## Troubleshooting

| Problem | Co sprawdzić |
|---|---|
| `/api/health` zwraca 500 | Logi Render → najczęściej zły `ES_HOST` lub Bonsai cluster wstał |
| Wyszukiwanie nic nie znajduje | Indeks jest pusty — odpal workflow GitHub Actions z `recreate=true` |
| `connection error` w workflow | Sprawdź czy secret `ES_HOST` jest ustawiony i URL jest poprawny |
| `analysis-stempel not detected` | Bonsai → Plugins → włącz `analysis-stempel` i `analysis-icu`, potem reindex |
