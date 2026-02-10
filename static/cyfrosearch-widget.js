/**
 * CyfroSearch Autocomplete Widget
 * Embed with: <script async src="https://YOUR_API/static/cyfrosearch-widget.js" data-api="https://YOUR_API" data-input="#search-input"></script>
 *
 * Configuration via data-* attributes on the script tag:
 *   data-api       — API base URL (required), e.g. "https://api.example.com"
 *   data-input     — CSS selector for the search input (default: "[name=q], [name=search], #search-input, .search-input, input[type=search]")
 *   data-limit     — max results (default: 7)
 *   data-debounce  — debounce ms (default: 120)
 *   data-min-chars — minimum characters to trigger (default: 2)
 *   data-lang      — language (default: "pl")
 */
(function () {
  "use strict";

  // ── Read config from script tag ──
  const SCRIPT = document.currentScript || (function () {
    const scripts = document.getElementsByTagName("script");
    return scripts[scripts.length - 1];
  })();

  const CFG = {
    api: SCRIPT.getAttribute("data-api") || "",
    inputSelector: SCRIPT.getAttribute("data-input") || '[name="q"], [name="search"], #search-input, .search-input, input[type="search"]',
    limit: parseInt(SCRIPT.getAttribute("data-limit") || "7", 10),
    debounce: parseInt(SCRIPT.getAttribute("data-debounce") || "80", 10),
    minChars: parseInt(SCRIPT.getAttribute("data-min-chars") || "2", 10),
    lang: SCRIPT.getAttribute("data-lang") || "pl",
  };

  if (!CFG.api) {
    console.warn("[CyfroSearch] Missing data-api attribute on script tag.");
    return;
  }

  // ── Inject CSS ──
  const WIDGET_CSS = `
/* CyfroSearch Widget — cyfrowe.pl production design */
.cfs-overlay {
  display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.15); z-index: 99998;
}
.cfs-overlay.cfs-active { display: block; }
.cfs-dropdown {
  display: none; position: absolute; z-index: 99999;
  background: #fff; border: 1px solid #e0e0e0; box-shadow: 0 6px 24px rgba(0,0,0,0.15);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  font-size: 13px; color: #333; width: 900px; max-width: 95vw;
  overflow: hidden;
}
.cfs-dropdown.cfs-active { display: block; }
.cfs-close {
  position: absolute; top: 8px; right: 10px; background: none; border: none;
  font-size: 22px; cursor: pointer; color: #999; line-height: 1; z-index: 10; padding: 2px 6px;
}
.cfs-close:hover { color: #333; }
.cfs-body { display: flex; min-height: 260px; }

/* Column 1: Queries + Categories */
.cfs-col-left { width: 190px; min-width: 190px; max-width: 190px; border-right: 1px solid #eee; padding: 16px 0; overflow: hidden; }
.cfs-heading { font-size: 13px; font-weight: 700; color: #333; padding: 0 16px 8px; }
.cfs-tags { padding: 0 12px 10px; display: flex; flex-wrap: wrap; gap: 5px; }
.cfs-tag {
  display: inline-block; padding: 4px 10px; background: #333; border: none;
  border-radius: 4px; font-size: 11px; color: #fff; cursor: pointer; white-space: normal;
  transition: background 0.15s; line-height: 1.3; max-width: 100%;
}
.cfs-tag:hover, .cfs-tag.cfs-tag-active { background: #555; }
.cfs-cats { margin-top: 6px; }
.cfs-cat {
  display: block; padding: 4px 16px; font-size: 13px; color: #333;
  text-decoration: none; cursor: pointer; line-height: 1.5;
}
.cfs-cat:hover { background: #f5f5f5; }
.cfs-cat mark { background: none; font-weight: 700; color: #333; text-decoration: underline; }

/* Column 2: Featured product */
.cfs-col-feat {
  width: 240px; min-width: 240px; border-right: 1px solid #eee;
  padding: 16px 18px; display: flex; flex-direction: column; align-items: center;
}
.cfs-feat-img { width: 180px; height: 180px; object-fit: contain; margin-bottom: 10px; }
.cfs-feat-name {
  font-size: 13px; color: #333; text-align: center; line-height: 1.4;
  margin-bottom: 8px; text-decoration: none; display: -webkit-box;
  -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
}
.cfs-feat-name:hover { text-decoration: underline; }
.cfs-feat-name mark { background: none; font-weight: 700; }
.cfs-feat-brand { color: #333; font-weight: 700; text-decoration: underline; }
.cfs-feat-price { font-size: 20px; font-weight: 700; color: #333; margin-top: 4px; }
.cfs-feat-old { font-size: 13px; color: #999; text-decoration: line-through; margin-top: 2px; }

/* Column 3: Product grid */
.cfs-col-prods { flex: 1; padding: 16px 14px; min-width: 0; overflow: hidden; }
.cfs-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.cfs-prod {
  display: flex; align-items: center; gap: 8px; padding: 6px;
  border-radius: 4px; text-decoration: none; color: inherit; cursor: pointer;
}
.cfs-prod:hover { background: #f5f5f5; }
.cfs-prod-img {
  width: 60px; height: 60px; object-fit: contain; flex-shrink: 0;
  background: #fff;
}
.cfs-prod-info { min-width: 0; overflow: hidden; }
.cfs-prod-name {
  font-size: 12px; color: #333; line-height: 1.35;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
  overflow: hidden; word-break: break-word;
}
.cfs-prod-name mark { background: none; font-weight: 700; }
.cfs-prod-brand { color: #333; font-weight: 700; text-decoration: underline; }
.cfs-prod-price { font-size: 13px; font-weight: 700; color: #333; margin-top: 3px; }
.cfs-prod-old { font-size: 11px; color: #999; text-decoration: line-through; }

/* Footer */
.cfs-footer {
  border-top: 1px solid #eee; padding: 12px 14px; text-align: center;
}
.cfs-show-all {
  display: inline-block; background: #444; color: #fff; padding: 10px 60px;
  font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
  border: none; cursor: pointer; text-decoration: none;
}
.cfs-show-all:hover { background: #333; }

/* Spinner */
.cfs-spinner { display: none; padding: 60px; text-align: center; width: 100%; }
.cfs-spinner::after {
  content: ''; display: inline-block; width: 22px; height: 22px;
  border: 3px solid #ddd; border-top-color: #333; border-radius: 50%;
  animation: cfs-spin 0.6s linear infinite;
}
@keyframes cfs-spin { to { transform: rotate(360deg); } }
.cfs-empty { padding: 60px 20px; text-align: center; color: #999; font-size: 13px; width: 100%; }

/* Mobile header (hidden on desktop) */
.cfs-mobile-header { display: none; }

/* Mobile */
@media (max-width: 800px) {
  .cfs-dropdown {
    position: fixed !important; top: 0 !important; left: 0 !important; right: 0 !important; bottom: 0 !important;
    width: 100% !important; max-width: 100% !important; transform: none !important;
    border: none; box-shadow: none; display: flex; flex-direction: column; overflow-y: auto;
  }
  .cfs-close { display: none; }
  .cfs-mobile-header {
    display: flex; align-items: center; gap: 8px;
    position: sticky; top: 0; z-index: 10;
    background: #fff; padding: 8px 12px;
    border-bottom: 1px solid #eee; flex-shrink: 0;
  }
  .cfs-mobile-back {
    background: none; border: none; font-size: 22px;
    cursor: pointer; padding: 4px 8px; color: #333; line-height: 1;
  }
  .cfs-mobile-input {
    flex: 1; border: 1px solid #ddd; border-radius: 4px;
    padding: 8px 12px; font-size: 14px; outline: none; font-family: inherit;
  }
  .cfs-mobile-input:focus { border-color: #999; }
  .cfs-body { flex-direction: column; min-height: unset; }
  .cfs-col-left {
    width: 100%; min-width: unset; max-width: unset;
    border-right: none; border-bottom: none;
    padding: 12px 14px 0; overflow: visible;
  }
  .cfs-heading { font-size: 12px; padding: 0 0 6px; }
  .cfs-tags { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 0 10px; }
  .cfs-cats { display: none; }
  .cfs-col-feat {
    width: 100%; min-width: unset;
    border-right: none; border-bottom: 1px solid #f0f0f0;
    flex-direction: row; align-items: center;
    gap: 12px; padding: 12px 14px;
  }
  .cfs-col-feat .cfs-heading { display: none; }
  .cfs-feat-img { width: 56px; height: 56px; flex-shrink: 0; margin-bottom: 0; }
  .cfs-feat-name { font-size: 13px; text-align: left; -webkit-line-clamp: 2; margin-bottom: 2px; }
  .cfs-feat-price { font-size: 15px; }
  .cfs-feat-old { font-size: 11px; }
  .cfs-col-prods { padding: 0; width: 100%; }
  .cfs-col-prods .cfs-heading { display: none; }
  .cfs-grid { display: flex; flex-direction: column; gap: 0; }
  .cfs-grid a {
    display: flex; align-items: center; gap: 12px;
    padding: 10px 14px; border-bottom: 1px solid #f0f0f0; border-radius: 0;
  }
  .cfs-grid a:last-child { border-bottom: none; }
  .cfs-prod-img { width: 56px; height: 56px; }
  .cfs-prod-name { font-size: 13px; -webkit-line-clamp: 2; }
  .cfs-prod-price { font-size: 14px; color: #e53935; }
  .cfs-prod-old { font-size: 11px; }
  .cfs-footer { padding: 14px; }
  .cfs-footer span { display: block; width: 100%; padding: 12px; font-size: 13px; border-radius: 6px; }
}
`;

  const styleEl = document.createElement("style");
  styleEl.textContent = WIDGET_CSS;
  document.head.appendChild(styleEl);

  // ── DNS Prefetch ──
  try {
    const apiHost = new URL(CFG.api).hostname;
    const link = document.createElement("link");
    link.rel = "dns-prefetch";
    link.href = "//" + apiHost;
    document.head.appendChild(link);
    const preconnect = document.createElement("link");
    preconnect.rel = "preconnect";
    preconnect.href = CFG.api;
    preconnect.crossOrigin = "anonymous";
    document.head.appendChild(preconnect);
  } catch (_) {}

  // ── Utility functions ──
  function esc(s) { return s ? s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;") : ""; }
  function ea(s) { return s ? s.replace(/&/g, "&amp;").replace(/'/g, "&#39;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;") : ""; }
  function er(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
  function fp(p) { return (p || p === 0) ? p.toLocaleString("pl-PL", { minimumFractionDigits: 0, maximumFractionDigits: 0 }) : "-"; }
  function _hlOutsideTags(html, word) {
    // Replace 'word' only in text nodes (outside < > tags) to avoid corrupting HTML tags
    return html.replace(/([^<]*)(<[^>]*>)?/gi, function(m, text, tag) {
      var replaced = text ? text.replace(new RegExp("(" + er(word) + ")", "gi"), "<mark>$1</mark>") : "";
      return replaced + (tag || "");
    });
  }
  function hl(t, q) {
    let r = esc(t);
    q.trim().split(/\s+/).filter(w => w.length > 1).forEach(w => {
      r = _hlOutsideTags(r, w);
    });
    return r;
  }
  function hlBrand(name, brand, q) {
    if (!brand) return hl(name, q);
    let r = esc(name);
    // Highlight brand with colored span
    const brandEsc = esc(brand);
    r = r.replace(new RegExp("(" + er(brandEsc) + ")", "gi"), '<span class="cfs-prod-brand">$1</span>');
    // Then highlight query terms (skip brand if already colored)
    q.trim().split(/\s+/).filter(w => w.length > 1).forEach(w => {
      if (w.toLowerCase() !== brand.toLowerCase()) r = _hlOutsideTags(r, w);
    });
    return r;
  }
  function hlFeatBrand(name, brand, q) {
    if (!brand) return hl(name, q);
    let r = esc(name);
    const brandEsc = esc(brand);
    r = r.replace(new RegExp("(" + er(brandEsc) + ")", "gi"), '<span class="cfs-feat-brand">$1</span>');
    q.trim().split(/\s+/).filter(w => w.length > 1).forEach(w => {
      if (w.toLowerCase() !== brand.toLowerCase()) r = _hlOutsideTags(r, w);
    });
    return r;
  }
  function hlInverted(text, query) {
    // Inverted highlighting for query suggestions: bold the UN-typed part
    const qLower = query.toLowerCase().trim();
    const tLower = text.toLowerCase();
    const idx = tLower.indexOf(qLower);
    if (idx >= 0) {
      const before = esc(text.substring(0, idx));
      const match = esc(text.substring(idx, idx + qLower.length));
      const after = esc(text.substring(idx + qLower.length));
      return (before ? '<strong>' + before + '</strong>' : '') + match + (after ? '<strong>' + after + '</strong>' : '');
    }
    // Fallback: bold entire suggestion
    return '<strong>' + esc(text) + '</strong>';
  }
  const PH_IMG = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect fill='%23f0f0f0' width='100' height='100'/%3E%3C/svg%3E";

  // ── Client-side cache ──
  const _cache = new Map();
  const CACHE_TTL = 60000;
  function cacheGet(k) { const e = _cache.get(k); if (e && Date.now() - e.ts < CACHE_TTL) return e.data; _cache.delete(k); return null; }
  function cachePut(k, d) { _cache.set(k, { ts: Date.now(), data: d }); if (_cache.size > 200) _cache.delete(_cache.keys().next().value); }

  // ── Zero-state (trending) cache ──
  let _trendingData = null;
  let _trendingFetching = false;

  // ── Create DOM elements ──
  const overlay = document.createElement("div");
  overlay.className = "cfs-overlay";
  document.body.appendChild(overlay);

  const dropdown = document.createElement("div");
  dropdown.className = "cfs-dropdown";
  dropdown.setAttribute("role", "listbox");
  dropdown.setAttribute("id", "cfs-listbox");
  dropdown.innerHTML = '<button class="cfs-close" aria-label="Zamknij">&times;</button><div class="cfs-spinner"></div><div class="cfs-content"></div>';
  document.body.appendChild(dropdown);

  const closeBtn = dropdown.querySelector(".cfs-close");
  const spinner = dropdown.querySelector(".cfs-spinner");
  const content = dropdown.querySelector(".cfs-content");

  // ── State ──
  let debounceTimer = null;
  let currentCtrl = null;
  let lastQuery = "";
  let inputEl = null;

  function showDropdown() {
    dropdown.classList.add("cfs-active");
    overlay.classList.add("cfs-active");
    if (inputEl) inputEl.setAttribute("aria-expanded", "true");
  }
  function hideDropdown() {
    dropdown.classList.remove("cfs-active");
    overlay.classList.remove("cfs-active");
    if (inputEl) inputEl.setAttribute("aria-expanded", "false");
  }

  function positionDropdown() {
    if (!inputEl) return;
    const rect = inputEl.getBoundingClientRect();
    const ddWidth = Math.min(900, window.innerWidth - 10);
    // Center under the search bar
    let left = rect.left + (rect.width / 2) - (ddWidth / 2);
    left = Math.max(5, Math.min(left, window.innerWidth - ddWidth - 5));
    dropdown.style.position = "fixed";
    dropdown.style.top = (rect.bottom + 2) + "px";
    dropdown.style.left = left + "px";
    dropdown.style.width = ddWidth + "px";
  }

  // ── Search function ──
  function searchFor(text) {
    if (!inputEl) return;
    inputEl.value = text;
    inputEl.dispatchEvent(new Event("input", { bubbles: true }));
    doSearch(text);
    inputEl.focus();
  }

  async function doSearch(q) {
    if (currentCtrl) currentCtrl.abort();
    const controller = new AbortController();
    currentCtrl = controller;
    lastQuery = q;

    const cached = cacheGet(q.toLowerCase());
    if (cached) {
      renderDropdown(cached, q);
      showDropdown();
      return;
    }

    // Delayed spinner: show only if fetch takes >150ms (avoids flash on fast responses)
    var spinnerTimeout = setTimeout(function() { spinner.style.display = "block"; }, 150);
    positionDropdown();
    showDropdown();

    try {
      const resp = await fetch(CFG.api + "/api/suggest?q=" + encodeURIComponent(q) + "&limit=" + CFG.limit, {
        signal: controller.signal,
      });
      const data = await resp.json();
      clearTimeout(spinnerTimeout);
      spinner.style.display = "none";
      cachePut(q.toLowerCase(), data);
      renderDropdown(data, q);
    } catch (e) {
      clearTimeout(spinnerTimeout);
      if (e.name !== "AbortError") {
        spinner.style.display = "none";
        content.innerHTML = '<div class="cfs-empty">Brak połączenia z API</div>';
      }
    }
  }

  function renderDropdown(data, query) {
    if (!data.products?.length && !data.popular_queries?.length && !data.categories?.length) {
      content.innerHTML = '<div class="cfs-empty">Brak wyników dla "' + esc(query) + '"</div>';
      return;
    }

    const featured = data.products?.[0] || null;
    const grid = (data.products || []).slice(1, 7);

    // Column 1
    let c1 = '<div class="cfs-col-left">';
    if (data.popular_queries?.length) {
      c1 += '<div class="cfs-heading">Zapytania</div><div class="cfs-tags">';
      data.popular_queries.forEach((pq, idx) => {
        c1 += '<span class="cfs-tag" role="option" id="cfs-opt-' + idx + '" data-cfs-search="' + ea(pq.text) + '">' + hlInverted(pq.text, query) + '</span>';
      });
      c1 += '</div>';
    }
    if (data.categories?.length) {
      c1 += '<div class="cfs-cats"><div class="cfs-heading">Kategorie</div>';
      data.categories.forEach(cat => {
        const nm = cat.short_name || cat.name;
        c1 += '<a class="cfs-cat" data-cfs-search="' + ea(nm) + '">' + hl(nm, query) + '</a>';
      });
      c1 += '</div>';
    }
    c1 += '</div>';

    // Column 2
    let c2 = '<div class="cfs-col-feat"><div class="cfs-heading">Popularny produkt</div>';
    if (featured) {
      const img = featured.image_url || PH_IMG;
      c2 += '<a href="' + ea(featured.product_url || '#') + '" target="_blank"><img class="cfs-feat-img" src="' + ea(img) + '" alt="" loading="lazy" onerror="this.src=\'' + PH_IMG + '\'"></a>';
      c2 += '<a class="cfs-feat-name" href="' + ea(featured.product_url || '#') + '" target="_blank">' + hlFeatBrand(featured.name, featured.brand, query) + '</a>';
      if (featured.original_price && featured.original_price > featured.price)
        c2 += '<div class="cfs-feat-old">' + fp(featured.original_price) + ' zł</div>';
      c2 += '<div class="cfs-feat-price">' + fp(featured.price) + ' zł</div>';
    }
    c2 += '</div>';

    // Column 3
    let c3 = '<div class="cfs-col-prods"><div class="cfs-heading">Produkty</div>';
    if (grid.length) {
      c3 += '<div class="cfs-grid">';
      grid.forEach(p => {
        const img = p.image_url || PH_IMG;
        let ph = '<div class="cfs-prod-price">' + fp(p.price) + ' zł</div>';
        if (p.original_price && p.original_price > p.price)
          ph = '<div class="cfs-prod-old">' + fp(p.original_price) + ' zł</div>' + ph;
        c3 += '<a class="cfs-prod" href="' + ea(p.product_url || '#') + '" target="_blank">' +
          '<img class="cfs-prod-img" src="' + ea(img) + '" alt="" loading="lazy" onerror="this.src=\'' + PH_IMG + '\'">' +
          '<div class="cfs-prod-info"><div class="cfs-prod-name">' + hlBrand(p.name, p.brand, query) + '</div>' + ph + '</div></a>';
      });
      c3 += '</div>';
    }
    c3 += '</div>';

    // Mobile header with search input (visible only on <=800px via CSS)
    var isMobile = window.innerWidth <= 800;
    var mobileHeader = '<div class="cfs-mobile-header">' +
      '<button class="cfs-mobile-back" data-cfs-hide>&#8592;</button>' +
      '<input class="cfs-mobile-input" type="text" value="' + ea(query) + '" placeholder="Szukaj produktów...">' +
      '<button class="cfs-mobile-back" data-cfs-hide>&times;</button>' +
      '</div>';

    let html = mobileHeader + '<div class="cfs-body">' + c1 + c2 + c3 + '</div>';
    html += '<div class="cfs-footer"><a class="cfs-show-all" data-cfs-search="' + ea(query) + '">POKAŻ WSZYSTKIE PRODUKTY</a></div>';
    content.innerHTML = html;

    // Mobile header event binding
    content.querySelectorAll("[data-cfs-hide]").forEach(function(btn) {
      btn.addEventListener("click", hideDropdown);
    });
    if (isMobile) {
      var mobileInput = content.querySelector(".cfs-mobile-input");
      if (mobileInput) {
        mobileInput.focus();
        mobileInput.setSelectionRange(mobileInput.value.length, mobileInput.value.length);
        mobileInput.addEventListener("input", function(e) {
          var mq = e.target.value.trim();
          if (inputEl) inputEl.value = mq;
          clearTimeout(debounceTimer);
          if (mq.length >= CFG.minChars) {
            debounceTimer = setTimeout(function() { doSearch(mq); }, CFG.debounce);
          } else if (mq.length === 0) {
            showZeroState();
          }
        });
      }
    }
  }

  // ── Zero-state (trending) rendering ──
  function renderZeroState(data) {
    if (!data.products?.length && !data.categories?.length) return;

    let c1 = '<div class="cfs-col-left" style="width:220px;min-width:220px;">';
    c1 += '<div class="cfs-heading">Popularne kategorie</div>';
    if (data.categories?.length) {
      c1 += '<div class="cfs-cats">';
      data.categories.forEach(cat => {
        c1 += '<a class="cfs-cat" data-cfs-search="' + ea(cat.name) + '">' + esc(cat.name) + ' <span style="color:#999;font-size:11px;">(' + cat.count + ')</span></a>';
      });
      c1 += '</div>';
    }
    c1 += '</div>';

    let c2 = '<div class="cfs-col-prods" style="flex:1;padding:16px 14px;">';
    c2 += '<div class="cfs-heading">Popularne produkty</div>';
    if (data.products?.length) {
      c2 += '<div class="cfs-grid">';
      data.products.forEach(p => {
        const img = p.image_url || PH_IMG;
        let ph = '<div class="cfs-prod-price">' + fp(p.price) + ' zł</div>';
        if (p.original_price && p.original_price > p.price)
          ph = '<div class="cfs-prod-old">' + fp(p.original_price) + ' zł</div>' + ph;
        const badgeHtml = p.badge ? '<div style="font-size:10px;color:#e53935;font-weight:700;">' + esc(p.badge) + '</div>' : '';
        c2 += '<a class="cfs-prod" href="' + ea(p.product_url || '#') + '" target="_blank">' +
          '<img class="cfs-prod-img" src="' + ea(img) + '" alt="" loading="lazy" onerror="this.src=\'' + PH_IMG + '\'">' +
          '<div class="cfs-prod-info">' + badgeHtml + '<div class="cfs-prod-name">' + esc(p.name) + '</div>' + ph + '</div></a>';
      });
      c2 += '</div>';
    }
    c2 += '</div>';

    // Mobile header for zero-state
    var isMobileZero = window.innerWidth <= 800;
    var mobileHeaderZero = '<div class="cfs-mobile-header">' +
      '<button class="cfs-mobile-back" data-cfs-hide>&#8592;</button>' +
      '<input class="cfs-mobile-input" type="text" value="" placeholder="Szukaj produktów...">' +
      '<button class="cfs-mobile-back" data-cfs-hide>&times;</button>' +
      '</div>';

    content.innerHTML = mobileHeaderZero + '<div class="cfs-body">' + c1 + c2 + '</div>';

    content.querySelectorAll("[data-cfs-hide]").forEach(function(btn) {
      btn.addEventListener("click", hideDropdown);
    });
    if (isMobileZero) {
      var mobileInputZero = content.querySelector(".cfs-mobile-input");
      if (mobileInputZero) {
        mobileInputZero.focus();
        mobileInputZero.addEventListener("input", function(e) {
          var mq = e.target.value.trim();
          if (inputEl) inputEl.value = mq;
          clearTimeout(debounceTimer);
          if (mq.length >= CFG.minChars) {
            debounceTimer = setTimeout(function() { doSearch(mq); }, CFG.debounce);
          } else if (mq.length === 0) {
            showZeroState();
          }
        });
      }
    }
  }

  async function showZeroState() {
    if (_trendingData) {
      renderZeroState(_trendingData);
      positionDropdown();
      showDropdown();
      return;
    }
    if (_trendingFetching) return;
    _trendingFetching = true;
    try {
      const resp = await fetch(CFG.api + "/api/trending");
      _trendingData = await resp.json();
      _trendingFetching = false;
      // Only show if input is still empty and focused
      if (inputEl && inputEl === document.activeElement && inputEl.value.trim().length === 0) {
        renderZeroState(_trendingData);
        positionDropdown();
        showDropdown();
      }
    } catch (e) {
      _trendingFetching = false;
    }
  }

  // ── Attach to input ──
  function init() {
    inputEl = document.querySelector(CFG.inputSelector);
    if (!inputEl) {
      // Retry in case page is still loading
      setTimeout(init, 500);
      return;
    }

    // Disable browser autocomplete on the input
    inputEl.setAttribute("autocomplete", "off");
    inputEl.setAttribute("autocorrect", "off");
    inputEl.setAttribute("spellcheck", "false");

    // WAI-ARIA Combobox Pattern
    inputEl.setAttribute("role", "combobox");
    inputEl.setAttribute("aria-autocomplete", "list");
    inputEl.setAttribute("aria-expanded", "false");
    inputEl.setAttribute("aria-haspopup", "listbox");
    inputEl.setAttribute("aria-owns", "cfs-listbox");

    // Warm up connection
    fetch(CFG.api + "/api/health").catch(() => {});

    inputEl.addEventListener("input", () => {
      const q = inputEl.value.trim();
      if (q.length < CFG.minChars) { hideDropdown(); return; }
      if (q === lastQuery) return;
      clearTimeout(debounceTimer);
      const cached = cacheGet(q.toLowerCase());
      if (cached) {
        lastQuery = q;
        positionDropdown();
        renderDropdown(cached, q);
        showDropdown();
        return;
      }
      debounceTimer = setTimeout(() => doSearch(q), CFG.debounce);
    });

    inputEl.addEventListener("focus", () => {
      const q = inputEl.value.trim();
      if (q.length >= CFG.minChars && content.innerHTML) {
        positionDropdown();
        showDropdown();
      } else if (q.length === 0) {
        showZeroState();
      }
    });

    inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { hideDropdown(); inputEl.blur(); return; }
      if (!dropdown.classList.contains("cfs-active")) return;

      const tags = content.querySelectorAll(".cfs-tag");
      if (!tags.length) return;

      let activeIdx = -1;
      tags.forEach((t, i) => { if (t.classList.contains("cfs-tag-active")) activeIdx = i; });

      if (e.key === "ArrowDown") {
        e.preventDefault();
        activeIdx = (activeIdx + 1) % tags.length;
        tags.forEach(t => t.classList.remove("cfs-tag-active"));
        tags[activeIdx].classList.add("cfs-tag-active");
        tags[activeIdx].scrollIntoView({ block: "nearest" });
        inputEl.setAttribute("aria-activedescendant", "cfs-opt-" + activeIdx);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activeIdx = activeIdx <= 0 ? tags.length - 1 : activeIdx - 1;
        tags.forEach(t => t.classList.remove("cfs-tag-active"));
        tags[activeIdx].classList.add("cfs-tag-active");
        tags[activeIdx].scrollIntoView({ block: "nearest" });
        inputEl.setAttribute("aria-activedescendant", "cfs-opt-" + activeIdx);
      } else if (e.key === "Enter" && activeIdx >= 0) {
        e.preventDefault();
        searchFor(tags[activeIdx].getAttribute("data-cfs-search"));
      }
    });

    overlay.addEventListener("click", hideDropdown);
    closeBtn.addEventListener("click", hideDropdown);

    // Event delegation: single handler for all [data-cfs-search] clicks (added once, not per-render)
    content.addEventListener("click", function(e) {
      var el = e.target.closest("[data-cfs-search]");
      if (el) { e.preventDefault(); searchFor(el.getAttribute("data-cfs-search")); }
    });

    // Reposition on scroll (throttled with rAF) and resize
    var _rafPending = false;
    window.addEventListener("scroll", function() {
      if (!_rafPending && dropdown.classList.contains("cfs-active")) {
        _rafPending = true;
        requestAnimationFrame(function() { positionDropdown(); _rafPending = false; });
      }
    }, { passive: true });
    window.addEventListener("resize", function() { if (dropdown.classList.contains("cfs-active")) positionDropdown(); }, { passive: true });

    console.log("[CyfroSearch] Widget initialized, attached to:", inputEl);
  }

  // Wait for DOM
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
