// Client-side search engine — ports json_search.py to JavaScript
// Data: products.json (minimal fields), brand_aliases.json

const state = {
  products: [],
  brandCache: new Map(), // lower → original
  aliasMap: new Map(),   // alias_lower → canonical
  loaded: false,
};

const COMPAT_PREPOSITIONS = new Set(['do', 'dla', 'na', 'pod']);
const USED_KEYWORDS = new Set([
  'używany','używane','używanych','używanego','używana','używanym',
  'uzywany','uzywane','uzywanych','uzywanego','uzywana','uzywanym',
]);

const PRODUCT_TYPE_MAP = new Map([
  ['obiektyw', { catSubstr: 'obiektywy', typeKey: 'lens' }],
  ['obiektywy', { catSubstr: 'obiektywy', typeKey: 'lens' }],
  ['obiektywu', { catSubstr: 'obiektywy', typeKey: 'lens' }],
  ['aparat', { catSubstr: 'aparaty', typeKey: 'camera' }],
  ['aparaty', { catSubstr: 'aparaty', typeKey: 'camera' }],
  ['aparatu', { catSubstr: 'aparaty', typeKey: 'camera' }],
  ['lampa', { catSubstr: 'lamp', typeKey: 'flash' }],
  ['lampe', { catSubstr: 'lamp', typeKey: 'flash' }],
  ['błyskowa', { catSubstr: 'lamp', typeKey: 'flash' }],
  ['blyskowa', { catSubstr: 'lamp', typeKey: 'flash' }],
  ['statyw', { catSubstr: 'statyw', typeKey: 'tripod' }],
  ['statywy', { catSubstr: 'statyw', typeKey: 'tripod' }],
  ['torba', { catSubstr: 'torb', typeKey: 'bag' }],
  ['plecak', { catSubstr: 'plecak', typeKey: 'bag' }],
  ['filtr', { catSubstr: 'filtr', typeKey: 'filter' }],
  ['filtry', { catSubstr: 'filtr', typeKey: 'filter' }],
  ['karta', { catSubstr: 'kart', typeKey: 'card' }],
  ['karty', { catSubstr: 'kart', typeKey: 'card' }],
  ['akumulator', { catSubstr: 'akumulator', typeKey: 'battery' }],
  ['bateria', { catSubstr: 'bateria', typeKey: 'battery' }],
  ['ładowarka', { catSubstr: 'ładowark', typeKey: 'charger' }],
  ['ladowarka', { catSubstr: 'ładowark', typeKey: 'charger' }],
  ['adapter', { catSubstr: 'adapter', typeKey: 'adapter' }],
  ['pierścień', { catSubstr: 'adapter', typeKey: 'adapter' }],
  ['pierscien', { catSubstr: 'adapter', typeKey: 'adapter' }],
  ['mikrofon', { catSubstr: 'mikrofon', typeKey: 'microphone' }],
  ['mikrofony', { catSubstr: 'mikrofon', typeKey: 'microphone' }],
]);

const MOUNT_SIGNATURES = {
  'canon-efm': [/eos[\s-]?m\b/, /ef[\s-]?m\b/],
  'canon-rf-ef': [/canon\s?rf\b/, /\brf\b/, /canon\s?ef\b/, /\bef\b(?![\s-]?m)/],
  'sony-e': [/sony\s?e\b/, /\/\s*sony/, /\bfe\b/, /\bnex\b/],
  'nikon-z': [/nikon\s?z\b/, /\/\s*nikon\s?z/],
  'nikon-f': [/nikon\s?f\b/, /\/\s*nikon\b(?!\s?z)/],
  'fuji-x': [/fuji(film)?\s?x\b/, /\/\s*fuji/],
  'mft': [/micro\s?4\/3/, /\bmft\b/, /m4\/3/, /\/\s*olympus/, /\/\s*panasonic/, /\bm\.zuiko\b/],
};

function detectCameraFamily(q) {
  if (/eos[\s-]?m\d{0,3}\b/.test(q) || /\bm50\b/.test(q) || /\bm6\b/.test(q) || /\bm5\b/.test(q) || /\bm200\b/.test(q) || /\bm100\b/.test(q)) return 'canon-efm';
  if (/eos[\s-]?r\d{0,2}\b/.test(q) || /\br5\b/.test(q) || /\br6\b/.test(q) || /\br7\b/.test(q) || /\br8\b/.test(q) || /\br10\b/.test(q) || /\br50\b/.test(q) || /\brp\b/.test(q)) return 'canon-rf-ef';
  if (/\ba7\b/.test(q) || /\ba9\b/.test(q) || /\ba1\b/.test(q) || /\ba6\d{3}\b/.test(q) || /\bzv[\s-]?\w/.test(q)) return 'sony-e';
  if (/nikon[\s-]?z\d{0,2}\b/.test(q) || /\bz5\b/.test(q) || /\bz6\b/.test(q) || /\bz7\b/.test(q) || /\bz8\b/.test(q) || /\bz9\b/.test(q) || /\bz50\b/.test(q) || /\bzf\b/.test(q) || /\bzfc\b/.test(q)) return 'nikon-z';
  if (/nikon[\s-]?d\d/.test(q) || /\bd\d{3,4}\b/.test(q)) return 'nikon-f';
  if (/x-t\d/.test(q) || /x-h\d/.test(q) || /x-e\d/.test(q) || /x-s\d{1,2}/.test(q) || /x-pro/.test(q) || /x100/.test(q)) return 'fuji-x';
  if (/\bom-\d/.test(q) || /\bgh\d/.test(q) || /\bg9\b/.test(q) || /m4\/3/.test(q) || /micro\s?4\/3/.test(q)) return 'mft';
  return null;
}

function lensMountMismatch(nameLower, expectedFamily) {
  for (const [family, patterns] of Object.entries(MOUNT_SIGNATURES)) {
    if (family === expectedFamily) continue;
    for (const re of patterns) {
      if (re.test(nameLower)) {
        const expected = MOUNT_SIGNATURES[expectedFamily] || [];
        for (const ere of expected) { if (ere.test(nameLower)) return false; }
        return true;
      }
    }
  }
  return false;
}

function normalize(t) {
  return (t || '').toLowerCase().replace(/\s+/g, ' ').trim();
}

function isOutletOrUsed(p) {
  const name = (p.n || '').toLowerCase();
  return p.cn === 'used' || name.includes('outlet') || name.includes('używany') || name.includes('używana');
}

async function loadData() {
  const status = document.getElementById('statusBar');
  const input = document.getElementById('searchInput');
  try {
    status.textContent = 'Ładowanie katalogu produktów...';
    const [prodResp, aliasResp] = await Promise.all([
      fetch('./products.json'),
      fetch('./brand_aliases.json'),
    ]);
    state.products = await prodResp.json();
    const aliases = await aliasResp.json();

    for (const entry of aliases) {
      for (const alias of entry.aliases) {
        state.aliasMap.set(alias.trim().toLowerCase(), entry.brand);
      }
    }
    for (const p of state.products) {
      const b = p.b;
      if (b && b.length >= 2) {
        state.brandCache.set(b.toLowerCase(), b);
      }
    }
    state.loaded = true;
    status.className = 'status-bar ready';
    status.textContent = `Gotowe · ${state.products.length.toLocaleString('pl-PL')} produktów w indeksie`;
    input.disabled = false;
    input.focus();
    setTimeout(() => status.style.display = 'none', 3000);
  } catch (e) {
    status.className = 'status-bar error';
    status.textContent = 'Błąd ładowania danych: ' + e.message;
  }
}

function preprocessQuery(query) {
  let q = query.toLowerCase().trim();
  let conditionFilter = null;
  for (const w of USED_KEYWORDS) {
    if (q.includes(w)) {
      conditionFilter = 'used';
      q = q.replace(w, '').trim();
      break;
    }
  }
  q = q.replace(/(\d)([a-zżźćńóąłęś]{2,})/g, '$1 $2');
  const isCompat = /\b(do|dla|na|pod)\b/.test(q);
  const words = q.split(/\s+/).filter(Boolean);
  const detectedBrands = [];
  const searchWords = [];
  let productType = null;
  for (const w of words) {
    if (COMPAT_PREPOSITIONS.has(w)) continue;
    if (!productType && PRODUCT_TYPE_MAP.has(w)) { productType = PRODUCT_TYPE_MAP.get(w); continue; }
    if (state.brandCache.has(w)) detectedBrands.push(state.brandCache.get(w));
    else if (state.aliasMap.has(w)) detectedBrands.push(state.aliasMap.get(w));
    else searchWords.push(w);
  }
  const brandFilter = (detectedBrands.length === 1 && !isCompat) ? detectedBrands[0] : null;
  const cameraFamily = detectCameraFamily(q);
  return { text: searchWords.join(' '), brandFilter, conditionFilter, productType, cameraFamily };
}

function kwInText(kw, text) {
  if (text.includes(kw)) return true;
  if (kw.length >= 4) {
    const prefix = kw.substring(0, Math.max(4, kw.length - 1));
    if (text.includes(prefix)) return true;
  }
  return false;
}

function scoreProduct(p, keywords, textQuery, conditionFilter, productType, cameraFamily) {
  const name = normalize(p.n);
  const cat = normalize(p.c);
  const brand = normalize(p.b);
  const combined = `${name} ${cat} ${brand}`;
  const queryNorm = normalize(textQuery);

  let score = 0;
  if (queryNorm && name.includes(queryNorm)) score += 250;

  if (keywords.length > 0) {
    let nameMatches = 0, combinedMatches = 0;
    for (const kw of keywords) {
      if (kwInText(kw, name)) nameMatches++;
      if (kwInText(kw, combined)) combinedMatches++;
    }
    if (nameMatches === keywords.length) score += 200;
    else if (combinedMatches === keywords.length) score += 100;
    score += nameMatches * 30;
    score += (combinedMatches - nameMatches) * 10;
    score += (combinedMatches / keywords.length) * 50;
  } else {
    score = 1;
  }

  const price = parseFloat(p.p) || 0;
  const pop = Math.log1p(price / 100) * 2;
  const popMult = 1 + pop * 0.05;

  if (productType) {
    if (cat.includes(productType.catSubstr)) score += 200;
    else score *= 0.2;
  }
  if (productType && productType.typeKey === 'lens' && cameraFamily) {
    if (lensMountMismatch(name, cameraFamily)) score *= 0.1;
  }
  if (conditionFilter === 'used' && name.includes('outlet')) score *= 0.3;

  if (name.includes(' + ') && !textQuery.includes('+') && !/\d\s*mm\b/.test(textQuery)) score *= 0.85;

  if (!conditionFilter && isOutletOrUsed(p)) score *= 0.3;
  if (p.a === 'in stock') score *= 1.1;

  return score * popMult;
}

function runSearch(query, limit = 24) {
  const { text, brandFilter, conditionFilter, productType, cameraFamily } = preprocessQuery(query);
  const keywords = text.split(/\s+/).filter(w => w.length > 1);

  let candidates = state.products;
  if (brandFilter) {
    candidates = candidates.filter(p => (p.b || '').toLowerCase() === brandFilter.toLowerCase());
  }
  if (conditionFilter) {
    candidates = candidates.filter(p => p.cn === conditionFilter);
  }

  const scored = [];
  for (const p of candidates) {
    const s = scoreProduct(p, keywords, text, conditionFilter, productType, cameraFamily);
    if (s > 0) scored.push([s, p]);
  }
  scored.sort((a, b) => b[0] - a[0]);

  return {
    total: scored.length,
    products: scored.slice(0, limit).map(([, p]) => p),
  };
}

// === UI ===
const input = document.getElementById('searchInput');
const panel = document.getElementById('suggestPanel');
const listEl = document.getElementById('suggestList');
const resultsWrapper = document.getElementById('resultsWrapper');
const resultsGrid = document.getElementById('resultsGrid');
const resultsTitle = document.getElementById('resultsTitle');
const resultsCount = document.getElementById('resultsCount');

let suggestTimeout = null;

input.addEventListener('input', () => {
  clearTimeout(suggestTimeout);
  const q = input.value.trim();
  if (q.length < 2) { closePanel(); return; }
  suggestTimeout = setTimeout(() => fetchSuggest(q), 150);
});

document.addEventListener('click', (e) => {
  if (!e.target.closest('.search-container')) closePanel();
});

input.addEventListener('focus', () => {
  const q = input.value.trim();
  if (q.length >= 2) fetchSuggest(q);
});

input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') doSearch();
  if (e.key === 'Escape') closePanel();
});

function closePanel() { panel.classList.remove('active'); }

function formatPrice(p) {
  if (!p) return '';
  return Number(p).toLocaleString('pl-PL', { minimumFractionDigits: 0, maximumFractionDigits: 0 }) + ' zł';
}

function goToProduct(link) {
  if (link) window.open(link, '_blank');
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function fetchSuggest(q) {
  if (!state.loaded) return;
  const result = runSearch(q, 7);
  renderSuggest(result);
}

function renderSuggest(data) {
  const prods = data.products || [];

  if (prods.length === 0) {
    closePanel();
    return;
  }

  listEl.innerHTML = prods.map(p => {
    const hasDiscount = p.sp && p.p && p.sp < p.p;
    const discountPct = hasDiscount ? Math.round((1 - p.sp / p.p) * 100) : 0;
    const priceHtml = hasDiscount
      ? `<span class="sg-price">${formatPrice(p.sp)}</span><span class="sg-old-price">${formatPrice(p.p)}</span><span class="sg-discount">-${discountPct}%</span>`
      : `<span class="sg-price">${formatPrice(p.p)}</span>`;
    const thumbHtml = p.im
      ? `<img src="${escapeHtml(p.im)}" alt="" loading="lazy" decoding="async">`
      : `<span class="placeholder-sm">&#128247;</span>`;
    return `<div class="sg-product" onclick="goToProduct('${escapeHtml(p.l).replace(/'/g,"\\\\'")}')">
      <div class="sg-thumb">${thumbHtml}</div>
      <div class="sg-info">
        <div class="sg-name">${escapeHtml(p.n)}</div>
        <div class="sg-price-row">${priceHtml}</div>
      </div>
    </div>`;
  }).join('');

  panel.classList.add('active');
}

function doSearch() {
  const q = input.value.trim();
  if (!q || !state.loaded) return;
  closePanel();
  const result = runSearch(q, 48);
  renderResults(q, result);
}

function renderResults(query, data) {
  resultsWrapper.classList.add('active');
  resultsTitle.textContent = `Wyniki dla: "${query}"`;
  resultsCount.textContent = `${data.total.toLocaleString('pl-PL')} produktów`;
  resultsGrid.innerHTML = data.products.map(p => {
    const hasDiscount = p.sp && p.p && p.sp < p.p;
    const discountPct = hasDiscount ? Math.round((1 - p.sp / p.p) * 100) : 0;
    const priceHtml = hasDiscount
      ? `<div class="result-price">${formatPrice(p.sp)}<span class="result-old-price">${formatPrice(p.p)}</span></div>`
      : `<div class="result-price">${formatPrice(p.p)}</div>`;
    const thumbHtml = p.im
      ? `<img src="${escapeHtml(p.im)}" alt="" loading="lazy" decoding="async">`
      : `<span style="font-size:32px;color:#ddd">&#128247;</span>`;
    return `<div class="result-card" onclick="goToProduct('${escapeHtml(p.l).replace(/'/g,"\\\\'")}')">
      <div class="result-thumb">${thumbHtml}</div>
      <div class="result-brand">${escapeHtml(p.b || '')}</div>
      <div class="result-name">${escapeHtml(p.n)}</div>
      ${priceHtml}
    </div>`;
  }).join('');
  window.scrollTo({ top: resultsWrapper.offsetTop - 20, behavior: 'smooth' });
}

window.goToProduct = goToProduct;
window.doSearch = doSearch;
window.closePanel = closePanel;

loadData();
