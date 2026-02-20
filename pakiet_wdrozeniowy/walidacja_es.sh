#!/bin/bash
# =============================================================================
# Skrypt walidacyjny — sprawdza stan indeksu ES i testuje kluczowe zapytania
# Uruchom po wdrożeniu, aby sprawdzić czy indeks jest poprawnie skonfigurowany.
#
# Użycie:
#   chmod +x walidacja_es.sh
#   ./walidacja_es.sh [ES_URL] [INDEX_NAME]
#
# Domyślne:
#   ES_URL=http://localhost:9200
#   INDEX_NAME=bitbag_shop_products_prod
# =============================================================================

ES_URL="${1:-http://localhost:9200}"
INDEX_NAME="${2:-bitbag_shop_products_prod}"

echo "========================================"
echo "  Walidacja ES: $ES_URL/$INDEX_NAME"
echo "========================================"
echo ""

# --- 1. Sprawdź czy indeks istnieje ---
echo "1. Sprawdzam indeks..."
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$ES_URL/$INDEX_NAME")
if [ "$STATUS" = "200" ]; then
    echo "   OK: Indeks istnieje"
else
    echo "   BŁĄD: Indeks nie istnieje (HTTP $STATUS)"
    exit 1
fi

# --- 2. Sprawdź liczbę dokumentów ---
echo ""
echo "2. Liczba dokumentów..."
DOC_COUNT=$(curl -s "$ES_URL/$INDEX_NAME/_count" | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])" 2>/dev/null || echo "???")
echo "   Dokumentów: $DOC_COUNT"
if [ "$DOC_COUNT" -lt 1000 ] 2>/dev/null; then
    echo "   UWAGA: Mniej niż 1000 dokumentów — czy reindex się zakończył?"
fi

# --- 3. Sprawdź czy pluginy analysis są zainstalowane ---
echo ""
echo "3. Pluginy ES..."
PLUGINS=$(curl -s "$ES_URL/_cat/plugins" 2>/dev/null)
if echo "$PLUGINS" | grep -q "analysis-stempel"; then
    echo "   OK: analysis-stempel zainstalowany"
else
    echo "   BRAK: analysis-stempel — wymagany dla polskiego stemmera!"
fi
if echo "$PLUGINS" | grep -q "analysis-icu"; then
    echo "   OK: analysis-icu zainstalowany"
else
    echo "   BRAK: analysis-icu — wymagany dla ICU folding!"
fi

# --- 4. Sprawdź analyzery ---
echo ""
echo "4. Analyzery w indeksie..."
SETTINGS=$(curl -s "$ES_URL/$INDEX_NAME/_settings")
if echo "$SETTINGS" | grep -q "polish_stem\|synonym_graph_filter"; then
    echo "   OK: Niestandardowe analyzery skonfigurowane"
else
    echo "   UWAGA: Brak polish_stem / synonym_graph_filter w settings"
fi

# --- 5. Sprawdź kluczowe pola ---
echo ""
echo "5. Pola w indeksie..."
MAPPING=$(curl -s "$ES_URL/$INDEX_NAME/_mapping")
for FIELD in name_pl_pl producer product_main_taxon_name_1_pl_pl ean; do
    if echo "$MAPPING" | grep -q "\"$FIELD\""; then
        echo "   OK: $FIELD"
    else
        echo "   BRAK: $FIELD"
    fi
done

# --- 6. Unikalne marki (top 10) ---
echo ""
echo "6. Top 10 marek..."
curl -s "$ES_URL/$INDEX_NAME/_search" -H "Content-Type: application/json" -d '{
  "size": 0,
  "aggs": {"brands": {"terms": {"field": "producer", "size": 10}}}
}' | python3 -c "
import sys, json
data = json.load(sys.stdin)
for b in data.get('aggregations',{}).get('brands',{}).get('buckets',[]):
    print(f\"   {b['key']}: {b['doc_count']} produktów\")
" 2>/dev/null || echo "   (nie udało się pobrać)"

# --- 7. Unikalne podkategorie (top 15) ---
echo ""
echo "7. Top 15 podkategorii..."
curl -s "$ES_URL/$INDEX_NAME/_search" -H "Content-Type: application/json" -d '{
  "size": 0,
  "aggs": {"subcats": {"terms": {"field": "product_main_taxon_name_1_pl_pl", "size": 15}}}
}' | python3 -c "
import sys, json
data = json.load(sys.stdin)
for b in data.get('aggregations',{}).get('subcats',{}).get('buckets',[]):
    print(f\"   {b['key']}: {b['doc_count']} produktów\")
" 2>/dev/null || echo "   (nie udało się pobrać)"

# --- 8. Test zapytań ---
echo ""
echo "8. Test zapytań (top 3 wyniki)..."
echo "   ─────────────────────────────"

for QUERY in "canon" "karta sd" "sony a7iv" "lampa" "fuji xt5"; do
    echo ""
    echo "   Zapytanie: \"$QUERY\""
    curl -s "$ES_URL/$INDEX_NAME/_search" -H "Content-Type: application/json" -d "{
      \"size\": 3,
      \"_source\": [\"name_pl_pl\", \"producer\"],
      \"query\": {
        \"multi_match\": {
          \"query\": \"$QUERY\",
          \"fields\": [\"name_pl_pl^3\", \"producer^3\"],
          \"fuzziness\": \"AUTO\"
        }
      }
    }" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for i, hit in enumerate(data.get('hits',{}).get('hits',[]), 1):
    name = hit['_source'].get('name_pl_pl','?')
    brand = hit['_source'].get('producer','?')
    score = hit.get('_score', 0)
    print(f'   {i}. [{brand}] {name}  (score={score:.1f})')
" 2>/dev/null || echo "   (nie udało się wykonać)"
done

echo ""
echo "========================================"
echo "  Walidacja zakończona"
echo "========================================"
