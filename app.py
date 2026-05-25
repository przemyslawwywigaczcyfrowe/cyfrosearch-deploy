"""
FastAPI application — simple search API + HTML mockup UI.

Supports two backends:
- Elasticsearch (when ELASTIC_URL is configured)
- JSON feed fallback (when ES is unavailable — downloads product feed and searches in-memory)
"""

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Template
import os
import logging

from config import ELASTIC_URL

_use_json_fallback = not ELASTIC_URL

if _use_json_fallback:
    from json_search import search, suggest, warm_caches
    logging.warning("ELASTIC_URL not set — using JSON feed fallback search")
else:
    from search_engine import search, suggest, warm_caches

app = FastAPI(title="Cyfrowe.pl Search Engine")


@app.on_event("startup")
async def startup_event():
    """Warm caches at startup so first user query is fast."""
    try:
        warm_caches()
    except Exception as e:
        logging.warning(f"Failed to warm caches at startup (will retry on first request): {e}")


# === API Endpoints ===

@app.get("/api/search")
async def api_search(
    q: str = Query(..., min_length=1, description="Search query"),
    page: int = Query(1, ge=1),
    size: int = Query(24, ge=1, le=100),
    availability: str | None = None,
    brand: str | None = None,
    condition: str | None = None,
    category: str | None = None,
    category_lvl0: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    sort: str | None = None,
):
    """Search products. Returns JSON with results and aggregations."""
    filters = {}
    if availability:
        filters["availability"] = availability
    if brand:
        filters["brand"] = brand
    if condition:
        filters["condition"] = condition
    if category:
        filters["category"] = category
    if category_lvl0:
        filters["category_lvl0"] = category_lvl0
    if price_min:
        filters["price_min"] = price_min
    if price_max:
        filters["price_max"] = price_max

    results = search(q, page=page, size=size, filters=filters, sort_by=sort)
    return JSONResponse(content=results)


@app.get("/api/suggest")
async def api_suggest(
    q: str = Query(..., min_length=2, description="Autocomplete query"),
):
    """Autocomplete suggestions — returns top 8 products."""
    results = suggest(q, size=8)
    return JSONResponse(content=results)


# === HTML UI ===

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cyfrowe.pl — Sugester (Dev)</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #fff; color: #333; }

        .search-wrapper {
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 60vh;
            padding: 20px;
        }
        .search-container {
            position: relative;
            width: 100%;
            max-width: 680px;
        }
        .search-bar {
            display: flex;
            align-items: center;
            border: 1px solid #ccc;
            border-radius: 4px;
            background: #fff;
            overflow: hidden;
        }
        .search-bar input {
            flex: 1;
            padding: 14px 16px;
            font-size: 15px;
            border: none;
            outline: none;
            background: transparent;
        }
        .search-bar button {
            padding: 14px 18px;
            background: none;
            border: none;
            cursor: pointer;
            color: #666;
            font-size: 18px;
            display: flex;
            align-items: center;
        }
        .search-bar button:hover { color: #333; }

        /* === Suggest dropdown === */
        .suggest-panel {
            display: none;
            position: absolute;
            top: 100%;
            left: 0;
            right: 0;
            background: #fff;
            border: 1px solid #ddd;
            border-top: none;
            box-shadow: 0 8px 24px rgba(0,0,0,0.12);
            z-index: 100;
        }
        .suggest-panel.active { display: block; }

        .suggest-close {
            position: absolute;
            top: 8px;
            right: 12px;
            background: none;
            border: none;
            font-size: 18px;
            cursor: pointer;
            color: #999;
            z-index: 10;
        }
        .suggest-close:hover { color: #333; }

        /* Product list */
        .suggest-list {
            padding: 8px 0;
        }
        .sg-product {
            display: flex;
            gap: 12px;
            padding: 8px 16px;
            cursor: pointer;
            align-items: center;
        }
        .sg-product:hover { background: #f5f5f5; }
        .sg-thumb {
            width: 48px;
            height: 48px;
            background: #f8f8f8;
            border: 1px solid #eee;
            border-radius: 3px;
            flex-shrink: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
        }
        .sg-thumb img {
            max-width: 100%;
            max-height: 100%;
            object-fit: contain;
        }
        .sg-thumb .placeholder-sm {
            font-size: 16px;
            color: #ddd;
        }
        .sg-info {
            flex: 1;
            min-width: 0;
        }
        .sg-name {
            font-size: 13px;
            color: #333;
            line-height: 1.3;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .sg-price-row {
            margin-top: 2px;
        }
        .sg-price {
            font-size: 14px;
            font-weight: 700;
            color: #000;
        }
        .sg-old-price {
            font-size: 12px;
            color: #999;
            text-decoration: line-through;
            margin-left: 6px;
        }
        .sg-discount {
            font-size: 10px;
            color: #fff;
            background: #e53935;
            padding: 1px 5px;
            border-radius: 2px;
            margin-left: 6px;
            font-weight: 600;
        }

        /* Footer */
        .suggest-footer {
            border-top: 1px solid #eee;
            padding: 12px;
            text-align: center;
        }
        .suggest-footer button {
            background: #fff;
            border: 1px solid #333;
            padding: 10px 40px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            border-radius: 3px;
            color: #333;
        }
        .suggest-footer button:hover {
            background: #333;
            color: #fff;
        }
    </style>
</head>
<body>
    <div class="search-wrapper">
        <div class="search-container">
            <div class="search-bar">
                <input type="text" id="searchInput" placeholder="Czego szukasz?" autofocus>
                <button type="button" onclick="doSearch()">&#128269;</button>
            </div>
            <div class="suggest-panel" id="suggestPanel">
                <button class="suggest-close" onclick="closePanel()">&times;</button>
                <div class="suggest-list" id="suggestList"></div>
                <div class="suggest-footer">
                    <button onclick="doSearch()">Pokaz wszystkie produkty</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        let suggestTimeout = null;
        const searchInput = document.getElementById('searchInput');
        const panel = document.getElementById('suggestPanel');

        searchInput.addEventListener('input', () => {
            clearTimeout(suggestTimeout);
            const q = searchInput.value.trim();
            if (q.length < 2) { closePanel(); return; }
            suggestTimeout = setTimeout(() => fetchSuggest(q), 300);
        });

        document.addEventListener('click', (e) => {
            if (!e.target.closest('.search-container')) closePanel();
        });

        searchInput.addEventListener('focus', () => {
            const q = searchInput.value.trim();
            if (q.length >= 2) fetchSuggest(q);
        });

        function closePanel() {
            panel.classList.remove('active');
        }

        function formatPrice(p) {
            if (!p) return '';
            return Number(p).toLocaleString('pl-PL', {minimumFractionDigits: 0, maximumFractionDigits: 0}) + ' zl';
        }

        function goToProduct(link) {
            if (link) window.open(link, '_blank');
        }

        async function fetchSuggest(q) {
            try {
                const resp = await fetch('/api/suggest?q=' + encodeURIComponent(q));
                const data = await resp.json();
                renderSuggest(data);
            } catch (e) { console.error(e); }
        }

        function renderSuggest(data) {
            const prods = data.products || [];

            if (prods.length === 0) {
                closePanel();
                return;
            }

            const listEl = document.getElementById('suggestList');
            listEl.innerHTML = prods.map(p => {
                const hasDiscount = p.sales_price && p.price && p.sales_price < p.price;
                const discountPct = hasDiscount ? Math.round((1 - p.sales_price / p.price) * 100) : 0;
                const priceHtml = hasDiscount
                    ? `<span class="sg-price">${formatPrice(p.sales_price)}</span><span class="sg-old-price">${formatPrice(p.price)}</span><span class="sg-discount">-${discountPct}%</span>`
                    : `<span class="sg-price">${formatPrice(p.price)}</span>`;

                const thumbHtml = p.image
                    ? `<img src="${p.image}" alt="${p.name}" loading="lazy" decoding="async" width="48" height="48">`
                    : `<span class="placeholder-sm">&#128247;</span>`;

                return `<div class="sg-product" onclick="goToProduct('${(p.link||'').replace(/'/g,"\\\\'")}')">
                    <div class="sg-thumb">${thumbHtml}</div>
                    <div class="sg-info">
                        <div class="sg-name">${p.name}</div>
                        <div class="sg-price-row">${priceHtml}</div>
                    </div>
                </div>`;
            }).join('');

            panel.classList.add('active');
        }

        function doSearch() {
            const q = searchInput.value.trim();
            if (!q) return;
            closePanel();
            alert('Szukaj: ' + q + ' (full search TBD)');
        }
    </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def home():
    return HTML_TEMPLATE
