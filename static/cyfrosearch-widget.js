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
    debounce: parseInt(SCRIPT.getAttribute("data-debounce") || "120", 10),
    minChars: parseInt(SCRIPT.getAttribute("data-min-chars") || "2", 10),
    lang: SCRIPT.getAttribute("data-lang") || "pl",
  };

  if (!CFG.api) {
    console.warn("[CyfroSearch] Missing data-api attribute on script tag.");
    return;
  }

  // ── Inject CSS ──
  const WIDGET_CSS = `
/* CyfroSearch Widget — Scoped styles */
.cfs-overlay {
  display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.2); z-index: 99998;
}
.cfs-overlay.cfs-active { display: block; }
.cfs-dropdown {
  display: none; position: absolute; z-index: 99999;
  background: #fff; border: 1px solid #ddd; box-shadow: 0 8px 30px rgba(0,0,0,0.18);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  font-size: 13px; color: #333; width: 720px; max-width: 95vw;
  overflow: hidden; border-radius: 0 0 6px 6px;
}
.cfs-dropdown.cfs-active { display: block; }
.cfs-close {
  position: absolute; top: 4px; right: 8px; background: none; border: none;
  font-size: 20px; cursor: pointer; color: #999; line-height: 1; z-index: 10; padding: 2px 6px;
}
.cfs-close:hover { color: #333; }
.cfs-body { display: flex; min-height: 200px; }

/* Column 1: Queries + Categories */
.cfs-col-left { width: 180px; min-width: 180px; border-right: 1px solid #eee; padding: 12px 0; }
.cfs-heading { font-size: 11px; font-weight: 700; color: #333; padding: 0 12px 5px; text-transform: uppercase; letter-spacing: 0.3px; }
.cfs-tags { padding: 0 8px 6px; display: flex; flex-wrap: wrap; gap: 4px; }
.cfs-tag {
  display: inline-block; padding: 3px 8px; background: #f0f0f0; border: 1px solid #ddd;
  border-radius: 3px; font-size: 11px; color: #333; cursor: pointer; white-space: nowrap;
  transition: background 0.15s;
}
.cfs-tag:hover { background: #e0e0e0; }
.cfs-cats { margin-top: 8px; }
.cfs-cat {
  display: block; padding: 3px 12px; font-size: 12px; color: #333;
  text-decoration: none; cursor: pointer;
}
.cfs-cat:hover { background: #f5f5f5; text-decoration: underline; }
.cfs-cat mark { background: none; font-weight: 700; }

/* Column 2: Featured product */
.cfs-col-feat {
  width: 190px; min-width: 190px; border-right: 1px solid #eee;
  padding: 12px 14px; display: flex; flex-direction: column; align-items: center;
}
.cfs-feat-img { width: 130px; height: 130px; object-fit: contain; margin-bottom: 6px; }
.cfs-feat-name {
  font-size: 12px; color: #333; text-align: center; line-height: 1.3;
  margin-bottom: 4px; text-decoration: none; display: -webkit-box;
  -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
}
.cfs-feat-name:hover { text-decoration: underline; }
.cfs-feat-name mark { background: none; font-weight: 700; }
.cfs-feat-price { font-size: 17px; font-weight: 700; color: #333; }
.cfs-feat-old { font-size: 12px; color: #999; text-decoration: line-through; }

/* Column 3: Product grid */
.cfs-col-prods { flex: 1; padding: 12px 10px 12px 14px; min-width: 0; overflow: hidden; }
.cfs-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 5px; }
.cfs-prod {
  display: flex; align-items: center; gap: 6px; padding: 4px;
  border-radius: 3px; text-decoration: none; color: inherit; cursor: pointer;
}
.cfs-prod:hover { background: #f5f5f5; }
.cfs-prod-img {
  width: 48px; height: 48px; object-fit: contain; flex-shrink: 0;
  border: 1px solid #eee; border-radius: 2px; background: #fafafa;
}
.cfs-prod-info { min-width: 0; overflow: hidden; }
.cfs-prod-name {
  font-size: 11px; color: #333; line-height: 1.25;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; word-break: break-word;
}
.cfs-prod-name mark { background: none; font-weight: 700; }
.cfs-prod-price { font-size: 12px; font-weight: 700; color: #333; margin-top: 1px; }
.cfs-prod-old { font-size: 10px; color: #999; text-decoration: line-through; }

/* Footer */
.cfs-footer {
  border-top: 1px solid #eee; padding: 8px 14px; text-align: center;
}
.cfs-show-all {
  display: inline-block; background: #333; color: #fff; padding: 8px 36px;
  font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
  border: none; border-radius: 3px; cursor: pointer; text-decoration: none;
}
.cfs-show-all:hover { background: #555; }

/* Powered by */
.cfs-powered {
  text-align: center; padding: 4px 0 6px; font-size: 9px; color: #bbb;
}
.cfs-powered a { color: #999; text-decoration: none; }
.cfs-powered a:hover { text-decoration: underline; }

/* Spinner */
.cfs-spinner { display: none; padding: 50px; text-align: center; width: 100%; }
.cfs-spinner::after {
  content: ''; display: inline-block; width: 22px; height: 22px;
  border: 3px solid #ddd; border-top-color: #333; border-radius: 50%;
  animation: cfs-spin 0.6s linear infinite;
}
@keyframes cfs-spin { to { transform: rotate(360deg); } }
.cfs-empty { padding: 50px 20px; text-align: center; color: #999; font-size: 13px; width: 100%; }

/* Mobile */
@media (max-width: 800px) {
  .cfs-dropdown { width: 100%; max-width: 100%; left: 0 !important; right: 0; border-radius: 0; }
  .cfs-body { flex-direction: column; }
  .cfs-col-left { width: 100%; min-width: unset; border-right: none; border-bottom: 1px solid #eee; }
  .cfs-col-feat { width: 100%; min-width: unset; border-right: none; border-bottom: 1px solid #eee; flex-direction: row; gap: 12px; }
  .cfs-feat-img { width: 70px; height: 70px; }
  .cfs-col-prods { padding: 8px; }
  .cfs-grid { grid-template-columns: 1fr; }
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
  function hl(t, q) {
    let r = esc(t);
    q.trim().split(/\s+/).filter(w => w.length > 1).forEach(w => {
      r = r.replace(new RegExp("(" + er(w) + ")", "gi"), "<mark>$1</mark>");
    });
    return r;
  }
  const PH_IMG = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect fill='%23f0f0f0' width='100' height='100'/%3E%3C/svg%3E";

  // ── Client-side cache ──
  const _cache = new Map();
  const CACHE_TTL = 60000;
  function cacheGet(k) { const e = _cache.get(k); if (e && Date.now() - e.ts < CACHE_TTL) return e.data; _cache.delete(k); return null; }
  function cachePut(k, d) { _cache.set(k, { ts: Date.now(), data: d }); if (_cache.size > 200) _cache.delete(_cache.keys().next().value); }

  // ── Create DOM elements ──
  const overlay = document.createElement("div");
  overlay.className = "cfs-overlay";
  document.body.appendChild(overlay);

  const dropdown = document.createElement("div");
  dropdown.className = "cfs-dropdown";
  dropdown.innerHTML = '<button class="cfs-close">&times;</button><div class="cfs-spinner"></div><div class="cfs-content"></div>';
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
  }
  function hideDropdown() {
    dropdown.classList.remove("cfs-active");
    overlay.classList.remove("cfs-active");
  }

  function positionDropdown() {
    if (!inputEl) return;
    const rect = inputEl.getBoundingClientRect();
    const ddWidth = Math.min(720, window.innerWidth - 10);
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

    spinner.style.display = "block";
    content.innerHTML = "";
    positionDropdown();
    showDropdown();

    try {
      const resp = await fetch(CFG.api + "/api/suggest?q=" + encodeURIComponent(q) + "&limit=" + CFG.limit, {
        signal: controller.signal,
      });
      const data = await resp.json();
      spinner.style.display = "none";
      cachePut(q.toLowerCase(), data);
      renderDropdown(data, q);
    } catch (e) {
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
      data.popular_queries.forEach(pq => {
        c1 += '<span class="cfs-tag" data-cfs-search="' + ea(pq.text) + '">' + esc(pq.text) + '</span>';
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
      c2 += '<img class="cfs-feat-img" src="' + ea(img) + '" alt="" loading="lazy" onerror="this.src=\'' + PH_IMG + '\'">';
      c2 += '<a class="cfs-feat-name" href="' + ea(featured.product_url || '#') + '" target="_blank">' + (featured.highlight || esc(featured.name)) + '</a>';
      c2 += '<div class="cfs-feat-price">' + fp(featured.price) + ' zł</div>';
      if (featured.original_price && featured.original_price > featured.price)
        c2 += '<div class="cfs-feat-old">' + fp(featured.original_price) + ' zł</div>';
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
          '<div class="cfs-prod-info"><div class="cfs-prod-name">' + (p.highlight || esc(p.name)) + '</div>' + ph + '</div></a>';
      });
      c3 += '</div>';
    }
    c3 += '</div>';

    let html = '<div class="cfs-body">' + c1 + c2 + c3 + '</div>';
    html += '<div class="cfs-footer"><a class="cfs-show-all" data-cfs-search="' + ea(query) + '">Pokaż wszystkie produkty</a></div>';
    html += '<div class="cfs-powered">Powered by <a href="https://github.com/cyfrosearch" target="_blank">CyfroSearch</a></div>';
    content.innerHTML = html;

    // Bind click events for search tags
    content.querySelectorAll("[data-cfs-search]").forEach(el => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        searchFor(el.getAttribute("data-cfs-search"));
      });
    });
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
      if (inputEl.value.trim().length >= CFG.minChars && content.innerHTML) {
        positionDropdown();
        showDropdown();
      }
    });

    inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { hideDropdown(); inputEl.blur(); }
    });

    overlay.addEventListener("click", hideDropdown);
    closeBtn.addEventListener("click", hideDropdown);

    // Reposition on scroll/resize
    window.addEventListener("scroll", () => { if (dropdown.classList.contains("cfs-active")) positionDropdown(); }, { passive: true });
    window.addEventListener("resize", () => { if (dropdown.classList.contains("cfs-active")) positionDropdown(); }, { passive: true });

    console.log("[CyfroSearch] Widget initialized, attached to:", inputEl);
  }

  // Wait for DOM
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
