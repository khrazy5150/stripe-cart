// products.js - Stripe Product Management
const _productCache = new Map();
const UPLOAD_API_BASE = 'https://dph4d1c6p8.execute-api.us-west-2.amazonaws.com/v3';

let _apiBaseCache = resolveApiBase();

function resolveApiBase() {
    const cfg = (window.CONFIG && typeof window.CONFIG === 'object') ? window.CONFIG : {};
    const base =
        cfg.apiBase ||
        cfg.api_base ||
        cfg.apiBaseUrl ||
        cfg.api_url ||
        cfg.baseUrl ||
        '';
    return typeof base === 'string' ? base.replace(/\/+$/, '') : '';
}

function ensureApiBase() {
    if (!_apiBaseCache) {
        _apiBaseCache = resolveApiBase();
    }
    return _apiBaseCache;
}

function requireApiBase() {
    const base = ensureApiBase();
    if (!base) {
        console.warn('Stripe dashboard API base URL is not configured yet (CONFIG not loaded).');
    }
    return base;
}

window.addEventListener('configLoaded', () => {
    _apiBaseCache = resolveApiBase();
});

window.addEventListener('environmentChanged', () => {
    _apiBaseCache = resolveApiBase();
});

let currentProducts = [];
let currentProductId = null;
let editingProduct = false;
let uploadedImages = [];
let pendingUploads = []; // array of Promises
let _productsInitDone = false; // one-time init guard to prevent duplicate listeners

/**
 * Dashboard product browsing + filtering module
 * Handles the simplified Products tab experience inside the main dashboard.
 */
(function(window, document) {
    'use strict';

    const DASHBOARD_DEFAULT_FILTERS = {
        query: '',
        metadataKey: '',
        metadataValue: '',
        status: 'active'
    };
    const DASHBOARD_PAGE_SIZE = 10;

    const dashboardState = {
        products: [],
        filters: { ...DASHBOARD_DEFAULT_FILTERS },
        cursor: null,
        hasMore: false,
        isLoading: false,
        loadingMore: false
    };

    let dashboardObserver = null;
    let dashboardInitialized = false;

    function initDashboardModule() {
        if (dashboardInitialized) return;
        dashboardInitialized = true;
        bindLoadProductsButton();
    }

    function initOnReady() {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => {
                initDashboardModule();
                renderDashboardProducts();
            });
        } else {
            initDashboardModule();
            renderDashboardProducts();
        }
    }

    let currentDashboardEnv = (window.CONFIG && window.CONFIG.environment)
        ? window.CONFIG.environment
        : (localStorage.getItem('env') || 'dev');

    function resolveClientId() {
        const input = document.getElementById('clientID');
        if (input && input.value.trim()) return input.value.trim();
        return localStorage.getItem('clientID') ||
            sessionStorage.getItem('clientID') || '';
    }

    function getStoredEnv() {
        return currentDashboardEnv || localStorage.getItem('env') || 'dev';
    }

    function getStripeMode() {
        const env = getStoredEnv();
        return env === 'prod' ? 'live' : 'test';
    }

    function safeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text ?? '';
        return div.innerHTML;
    }

    function formatCurrency(cents, currency) {
        const amount = (cents || 0) / 100;
        const iso = (currency || 'USD').toUpperCase();
        try {
            return new Intl.NumberFormat(undefined, {
                style: 'currency',
                currency: iso
            }).format(amount);
        } catch {
            return `${iso} ${amount.toFixed(2)}`;
        }
    }

    function normalizeDashboardProducts(payload, offerKey) {
        const mapProduct = (p = {}, index = 0) => {
            const prices = Array.isArray(p.prices) ? p.prices : [];
            const activePrices = prices.filter(pr => typeof pr.unit_amount === 'number');
            const lowestCents = (typeof p.lowest_price === 'number')
                ? p.lowest_price
                : (activePrices.length ? Math.min(...activePrices.map(pr => pr.unit_amount)) : null);
            const highestCents = activePrices.length ? Math.max(...activePrices.map(pr => pr.unit_amount)) : null;

            const priceValue = typeof lowestCents === 'number' ? lowestCents / 100 : 0;
            const compareAt = (typeof highestCents === 'number' && typeof lowestCents === 'number' && highestCents > lowestCents)
                ? highestCents / 100
                : 0;

            const lowestPrice = activePrices.find(pr => pr.unit_amount === lowestCents) || activePrices[0] || null;
            const currency = (lowestPrice?.currency || activePrices[0]?.currency || p.currency || 'USD').toUpperCase();
            const images = Array.isArray(p.images) ? p.images : [];

            return {
                id: p.id || `prod-${index + 1}`,
                price_id: lowestPrice ? lowestPrice.id : null,
                name: p.name || `Product ${index + 1}`,
                description: p.description || '',
                price: priceValue,
                compare_at: compareAt,
                currency,
                prices,
                metadata: p.metadata || {},
                active: p.active !== false,
                images
            };
        };

        if (payload && Array.isArray(payload.products)) {
            return payload.products.map((p, idx) => mapProduct(p, idx));
        }

        let node = payload;
        if (offerKey && node && node.offers && node.offers[offerKey]) {
            node = node.offers[offerKey];
        }

        const legacyArray = Array.isArray(node?.product_ids)
            ? node.product_ids
            : (Array.isArray(node) ? node : []);

        return legacyArray.map((p, idx) => mapProduct(p, idx));
    }

    function bindLoadProductsButton() {
        const btn = document.getElementById('btnLoadProducts');
        if (btn && !btn.dataset.dashboardBound) {
            btn.addEventListener('click', () => loadDashboardProducts());
            btn.dataset.dashboardBound = 'true';
        }
    }

    function bindProductFilterEvents() {
        const form = document.getElementById('productFiltersForm');
        if (form && !form.dataset.dashboardBound) {
            form.addEventListener('submit', handleProductFiltersSubmit);
            form.dataset.dashboardBound = 'true';
        }

        const resetBtn = document.getElementById('btnResetProductFilters');
        if (resetBtn && !resetBtn.dataset.dashboardBound) {
            resetBtn.addEventListener('click', resetProductFilters);
            resetBtn.dataset.dashboardBound = 'true';
        }
    }

    function handleProductFiltersSubmit(event) {
        event.preventDefault();
        const form = event.target.closest('form') || document.getElementById('productFiltersForm');
        if (!form) return;
        dashboardState.filters = {
            query: form.querySelector('#productSearchInput')?.value.trim() || '',
            metadataKey: form.querySelector('#productMetaKeyInput')?.value.trim() || '',
            metadataValue: form.querySelector('#productMetaValueInput')?.value.trim() || '',
            status: form.querySelector('#productStatusSelect')?.value || 'active'
        };
        resetDashboardProducts({ keepFilters: true, silent: true });
        renderDashboardProducts();
        loadDashboardProducts();
    }

    function resetProductFilters() {
        dashboardState.filters = { ...DASHBOARD_DEFAULT_FILTERS };
        resetDashboardProducts({ keepFilters: true, silent: true });
        renderDashboardProducts();
        loadDashboardProducts();
    }

    function resetDashboardProducts({ keepFilters = false, silent = false } = {}) {
        dashboardState.products = [];
        dashboardState.cursor = null;
        dashboardState.hasMore = false;
        dashboardState.isLoading = false;
        dashboardState.loadingMore = false;
        if (!keepFilters) {
            dashboardState.filters = { ...DASHBOARD_DEFAULT_FILTERS };
        }
        if (!silent) {
            renderDashboardProducts();
        }
    }

    function renderDashboardProducts() {
        const tab = document.getElementById('products-tab');
        if (!tab) return;
        initDashboardModule();

        const cardBody = tab.querySelector('.card-body');
        if (!cardBody) return;

        const filters = dashboardState.filters || { ...DASHBOARD_DEFAULT_FILTERS };
        const filtersHtml = `
            <form id="productFiltersForm" class="product-filters">
                <div class="form-group compact">
                    <label class="form-label" for="productSearchInput">Search</label>
                    <input id="productSearchInput" class="form-input" placeholder="Name, ID..." value="${safeHtml(filters.query)}">
                </div>
                <div class="form-group compact">
                    <label class="form-label" for="productMetaKeyInput">Metadata Key</label>
                    <input id="productMetaKeyInput" class="form-input" placeholder="e.g., category" value="${safeHtml(filters.metadataKey)}">
                </div>
                <div class="form-group compact">
                    <label class="form-label" for="productMetaValueInput">Metadata Value</label>
                    <input id="productMetaValueInput" class="form-input" placeholder="Value" value="${safeHtml(filters.metadataValue)}">
                </div>
                <div class="form-group compact">
                    <label class="form-label" for="productStatusSelect">Status</label>
                    <select id="productStatusSelect" class="form-select">
                        <option value="active" ${filters.status === 'active' ? 'selected' : ''}>Active</option>
                        <option value="all" ${filters.status === 'all' ? 'selected' : ''}>All</option>
                        <option value="archived" ${filters.status === 'archived' ? 'selected' : ''}>Archived</option>
                    </select>
                </div>
                <div class="product-filter-actions">
                    <button type="submit" class="btn btn-secondary btn-sm">Apply</button>
                    <button type="button" id="btnResetProductFilters" class="btn btn-secondary btn-sm">Reset</button>
                </div>
            </form>
        `;

        let content = filtersHtml;

        if (!dashboardState.products.length) {
            const message = dashboardState.isLoading
                ? 'Loading products...'
                : 'Click "Load Products" or adjust your filters to see products.';
            content += `
                <p class="text-muted text-center" style="padding: 40px 0;">
                    ${message}
                </p>
            `;
            cardBody.innerHTML = content;
            bindProductFilterEvents();
            return;
        }

        const listHtml = `
            <div class="products-list">
                ${dashboardState.products.map(product => {
                    const imageUrl = Array.isArray(product.images) && product.images.length ? product.images[0] : '';
                    const initial = (product.name || '?').trim().charAt(0).toUpperCase() || '?';
                    const priceCents = Math.round((product.price || 0) * 100);
                    const regularCents = Math.round((product.compare_at || 0) * 100);
                    const priceText = formatCurrency(priceCents, product.currency || 'USD');
                    const regularText = regularCents > priceCents ? formatCurrency(regularCents, product.currency || 'USD') : null;
                    const desc = product.description ? safeHtml(product.description) : '<span class="text-muted">No description</span>';
                    const rowClasses = ['product-row'];
                    if (!product.active) rowClasses.push('archived');
                    return `
                        <div class="${rowClasses.join(' ')}">
                            <div class="product-thumb">
                                ${imageUrl
                                    ? `<img src="${safeHtml(imageUrl)}" alt="${safeHtml(product.name || 'Product image')}" loading="lazy">`
                                    : `<div class="product-thumb-placeholder">${safeHtml(initial)}</div>`}
                            </div>
                            <div class="product-info">
                                <div class="product-title">${safeHtml(product.name || 'Untitled product')}</div>
                                <div class="product-description">${desc}</div>
                                <div class="product-pricing">
                                    <span class="product-price">${priceText}</span>
                                    <span class="product-regular">${regularText ? `Regular ${regularText}` : 'Regular —'}</span>
                                </div>
                                <div class="product-actions">
                                    <button class="btn btn-secondary btn-sm" onclick="editProduct('${product.id}')">Edit</button>
                                    <button class="btn btn-secondary btn-sm" onclick="viewProductDetails('${product.id}')">Details</button>
                                    ${product.active
                                        ? `<button class="btn btn-secondary btn-sm" onclick="archiveProduct('${product.id}')">Archive</button>`
                                        : `<button class="btn btn-secondary btn-sm" onclick="restoreProduct('${product.id}')">Restore</button>`}
                                </div>
                                ${product.active ? '' : '<div class="product-status archived">Archived</div>'}
                            </div>
                        </div>
                    `;
                }).join('')}
            </div>
        `;

        content += listHtml;
        if (dashboardState.loadingMore) {
            content += `<div class="products-loading-more">Loading more products…</div>`;
        }
        if (dashboardState.hasMore) {
            content += `<div id="productsInfiniteSentinel" class="infinite-sentinel"></div>`;
        }

        cardBody.innerHTML = content;
        bindProductFilterEvents();
        setupProductsInfiniteScroll();
    }

    function setupProductsInfiniteScroll() {
        if (dashboardObserver) {
            dashboardObserver.disconnect();
            dashboardObserver = null;
        }

        if (!dashboardState.hasMore || dashboardState.isLoading) {
            return;
        }

        const sentinel = document.getElementById('productsInfiniteSentinel');
        if (!sentinel) {
            return;
        }

        dashboardObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (!entry.isIntersecting) {
                    return;
                }
                dashboardObserver.disconnect();
                dashboardObserver = null;

                if (!dashboardState.loadingMore && dashboardState.hasMore) {
                    loadDashboardProducts({ append: true });
                }
            });
        }, { rootMargin: '200px 0px', threshold: 0 });

        dashboardObserver.observe(sentinel);
    }

    function notify(message, type = 'info') {
        const selector = '#products-tab .card-body';
        if (window.AdminDashboard && typeof window.AdminDashboard.showMessage === 'function') {
            window.AdminDashboard.showMessage(selector, message, type);
            return;
        }

        const container = document.querySelector(selector);
        if (!container) return;
        const msgEl = document.createElement('div');
        msgEl.className = `badge ${type}`;
        msgEl.textContent = message;
        msgEl.style.marginTop = '12px';
        container.appendChild(msgEl);
        setTimeout(() => msgEl.remove(), 4000);
    }

    async function loadDashboardProducts(options = {}) {
        const { append = false } = options;
        initDashboardModule();

        const clientID = resolveClientId();
        if (!clientID) {
            alert('Please enter a Client ID first');
            if (window.AdminDashboard && typeof window.AdminDashboard.switchTab === 'function') {
                window.AdminDashboard.switchTab('keys');
            }
            return;
        }

        const fetcher = window.authedFetch;
        if (typeof fetcher !== 'function') {
            alert('Authentication is not ready yet. Please try again after signing in.');
            return;
        }

        if (dashboardState.isLoading) return;
        dashboardState.isLoading = true;
        dashboardState.loadingMore = append;

        if (!append) {
            dashboardState.products = [];
            dashboardState.cursor = null;
            dashboardState.hasMore = false;
            renderDashboardProducts();
        }

        const btn = !append ? document.getElementById('btnLoadProducts') : null;
        let previousBtnHtml = null;
        if (btn && !append) {
            previousBtnHtml = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<span class="button-spinner"></span> Loading…';
        }

        const params = new URLSearchParams({
            clientID,
            limit: String(DASHBOARD_PAGE_SIZE)
        });
        const stripeMode = getStripeMode();
        params.set('stripeMode', stripeMode);

        if (append && dashboardState.cursor) {
            params.set('cursor', dashboardState.cursor);
        }

        const filters = dashboardState.filters || { ...DASHBOARD_DEFAULT_FILTERS };
        if (filters.query) {
            params.set('search', filters.query);
        }
        if (filters.metadataKey && filters.metadataValue) {
            params.set('metadataKey', filters.metadataKey);
            params.set('metadataValue', filters.metadataValue);
        }
        if (filters.status) {
            params.set('status', filters.status);
        }

        try {
            const res = await fetcher(`/admin/products?${params.toString()}`);
            if (!res.ok) {
                throw new Error(res.body?.error || `HTTP ${res.status}`);
            }

            const products = Array.isArray(res.body?.products) ? res.body.products : [];
            const normalized = normalizeDashboardProducts({ products });
            const merged = append ? dashboardState.products.concat(normalized) : normalized;
            merged.sort((a, b) => (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base' }));
            dashboardState.products = merged;
            dashboardState.cursor = res.body?.nextCursor || null;
            dashboardState.hasMore = !!res.body?.hasMore;
            dashboardState.loadingMore = false;
            dashboardState.isLoading = false;
            renderDashboardProducts();
            if (!append) {
                notify(`Loaded ${dashboardState.products.length} products`, 'success');
            }
        } catch (err) {
            console.error('Failed to load products', err);
            dashboardState.loadingMore = false;
            dashboardState.isLoading = false;
            renderDashboardProducts();
            notify(`Failed to load products: ${err.message}`, 'danger');
        } finally {
            if (!append && btn) {
                btn.innerHTML = previousBtnHtml || 'Load Products';
                btn.disabled = false;
            }
        }
    }

    window.ProductsDashboard = {
        init: initDashboardModule,
        render: renderDashboardProducts,
        loadProducts: loadDashboardProducts,
        resetFilters: resetProductFilters,
        resetState: (options = {}) => resetDashboardProducts(options),
        setEnv: (env) => {
            if (env) currentDashboardEnv = env;
        },
        updateProductState: (productId, updates) => {
            // Helper to update a product's state in dashboardState
            const product = dashboardState.products.find(p => p.id === productId);
            if (product) {
                Object.assign(product, updates);
                renderDashboardProducts();
            }
        }
    };
    window.getStripeMode = getStripeMode;

    window.addEventListener('environmentChanged', (event) => {
        const nextEnv = event?.detail?.env;
        if (nextEnv) {
            currentDashboardEnv = nextEnv;
        } else {
            currentDashboardEnv = localStorage.getItem('env') || currentDashboardEnv || 'dev';
        }
        resetDashboardProducts({ keepFilters: true, silent: true });
        renderDashboardProducts();
    });

    initOnReady();
})(window, document);

function enqueueUpload(promise) {
  pendingUploads.push(promise);
  promise.finally(() => {
    pendingUploads = pendingUploads.filter(p => p !== promise);
  });
}

// PATCH: tiny helpers for *background* upgrade from original -> medium (no logic changes elsewhere)
function replaceImageInUI(oldUrl, newUrl) {
  // update state used by save()
  uploadedImages = uploadedImages.map(u => (u === oldUrl ? newUrl : u));
  // update previews
  const container = document.getElementById('imagePreviewContainer');
  if (!container) return;
  container.querySelectorAll('.image-preview-item img').forEach(img => {
    if (img.src === oldUrl) img.src = newUrl;
  });
}

function startUpgradeWatcher(uploadId, mediumCandidates, oldUrl, {
  maxSeconds = 300,
  intervalMs = 3000
} = {}) {
  if (!uploadId || !Array.isArray(mediumCandidates) || mediumCandidates.length === 0) return;

  const deadline = Date.now() + maxSeconds * 1000;
  (async function loop() {
    while (Date.now() < deadline) {
      // light probe to keep status warm (ignore errors)
      try { await fetch(`${UPLOAD_API_BASE}/upload/status/${uploadId}`, { cache: 'no-store' }); } catch {}

      for (const url of mediumCandidates) {
        try {
          const u = new URL(url);
          u.searchParams.set('_upgrade', Date.now().toString());
          const head = await fetch(u.toString(), { method: 'HEAD', cache: 'no-store' });
          if (head.ok) {
            replaceImageInUI(oldUrl, url);
            return; // upgrade done
          }
        } catch {/* keep trying */}
      }
      await new Promise(r => setTimeout(r, intervalMs));
    }
  })();
}

// Initialize products tab
function initProducts() {
    if (_productsInitDone) return;

    // Load products button
    const loadProductsBtn = document.getElementById('btnLoadProducts');
    if (loadProductsBtn && !loadProductsBtn.dataset.dashboardBound) {
        loadProductsBtn.addEventListener('click', loadProducts);
        loadProductsBtn.dataset.dashboardBound = 'true';
    }
    
    const createProductBtn = document.getElementById('btnCreateProduct');
    if (createProductBtn && !createProductBtn.dataset.dashboardBound) {
        createProductBtn.addEventListener('click', () => showProductForm());
        createProductBtn.dataset.dashboardBound = 'true';
    }
    
    const saveProductBtn = document.getElementById('btnSaveProduct');
    if (saveProductBtn && !saveProductBtn.dataset.dashboardBound) {
        saveProductBtn.addEventListener('click', saveProduct);
        saveProductBtn.dataset.dashboardBound = 'true';
    }
    
    const cancelProductBtn = document.getElementById('btnCancelProduct');
    if (cancelProductBtn && !cancelProductBtn.dataset.dashboardBound) {
        cancelProductBtn.addEventListener('click', hideProductForm);
        cancelProductBtn.dataset.dashboardBound = 'true';
    }
    
    const closeModalBtn = document.getElementById('btnCloseProductModal');
    if (closeModalBtn && !closeModalBtn.dataset.dashboardBound) {
        closeModalBtn.addEventListener('click', hideProductForm);
        closeModalBtn.dataset.dashboardBound = 'true';
    }
    
    document.querySelectorAll('[data-close-product-modal]').forEach(el => {
        if (!el.dataset.dashboardBound) {
            el.addEventListener('click', hideProductForm);
            el.dataset.dashboardBound = 'true';
        }
    });

    // Enable "Add new price" button
    const addNewBtn = document.getElementById('btnAddNewPrice');
    if (addNewBtn) {
        addNewBtn.addEventListener('click', () => {
        document.getElementById('pricesSection').classList.remove('hidden'); // reveal the add-price UI
        });
    }

    _productsInitDone = true;
}

// Load all products for the current client
// Load all products for the current client
async function loadProducts() {
    const clientID = getClientID();
    if (!clientID) {
        showProductMsg('Please set your clientID in the Stripe Keys tab first', 'err');
        return;
    }

    const btn = document.getElementById('btnLoadProducts');
    const res = startSpin(btn, 'Loading ...');

    const apiBase = requireApiBase();
    if (!apiBase) {
        stopSpin(res);
        showProductMsg('Configuration is still loading. Please try again in a moment.', 'err');
        return;
    }

    console.log(apiBase);
    
    try {
        const token = await getIdToken();
        const stripeMode = getStripeMode();
        const url = new URL(`${apiBase}/admin/products`);
        url.searchParams.set('clientID', clientID);
        url.searchParams.set('stripeMode', stripeMode);
        // Updated to use RESTful endpoint: GET /admin/products?clientID=xxx
        const response = await fetch(url.toString(), {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token
            }
        });

        const data = await response.json();
        
        if (data.error) {
            // ONLY stopSpin and show error message here.
            throw new Error(data.error); 
        }

        currentProducts = data.products || [];
        renderProductsList();
        showProductMsg(`Loaded ${currentProducts.length} products`, 'ok');
    } catch (err) {
        console.error('Load products error:', err);
        showProductMsg(`Failed to load products: ${err.message}`, 'err');
    } finally {
        // ALWAYS stopSpin in the finally block, regardless of success or error.
        stopSpin(res);
        // showProductMsg(''); // Removed to keep the success/fail message visible
    }
}

// async function loadProducts() {
//     const clientID = getClientID();
//     if (!clientID) {
//         showProductMsg('Please set your clientID in the Stripe Keys tab first', 'err');
//         return;
//     }

//     const btn = document.getElementById('btnLoadProducts');
//     const res = startSpin(btn, 'Loading ...');
//     showProductMsg('Loading products...');
    
//     try {
//         const token = await getIdToken();
//         // Updated to use RESTful endpoint: GET /admin/products?clientID=xxx
//         const response = await fetch(`${API_BASE}/admin/products?clientID=${encodeURIComponent(clientID)}`, {
//             method: 'GET',
//             headers: {
//                 'Content-Type': 'application/json',
//                 'Authorization': token
//             }
//         });

//         const data = await response.json();
        
//         if (data.error) {
//             stopSpin(res); 
//             showProductMsg(`Error: ${data.error}`, 'err');
//             return;
//         }

//         currentProducts = data.products || [];
//         renderProductsList();
//         showProductMsg(`Loaded ${currentProducts.length} products`, 'ok');
//     } catch (err) {
//         stopSpin(res); 
//         console.error('Load products error:', err);
//         showProductMsg(`Failed to load products: ${err.message}`, 'err');
//     } finally {
//         stopSpin(res);
//         showProductMsg('');
//     }
// }

// Render products list
function renderProductsList() {
    const container = document.getElementById('productsListContainer');
    if (!container) {
        console.warn('renderProductsList called but #productsListContainer was not found in the DOM.');
        return;
    }
    
    if (currentProducts.length === 0) {
        container.innerHTML = '<div class="no-orders">No products found. Create your first product!</div>';
        return;
    }

    container.innerHTML = currentProducts.map(product => {
        const meta = product.metadata || {};
        const productType = meta.product_type || 'physical';
        const productCategory = meta.product_category || 'standard';
        
        return `
        <div class="product-card">
            <div class="product-header">
                <div>
                    <div class="product-name">${escapeHtml(product.name)}</div>
                    <div class="product-id">${product.id}</div>
                </div>
                <div class="product-actions">
                    ${product.active ? 
                        '<span class="order-badge badge-success">Active</span>' : 
                        '<span class="order-badge badge-danger">Inactive</span>'
                    }
                    <button class="small" onclick="editProduct('${product.id}')">Edit</button>
                    <button class="small secondary" onclick="viewProductDetails('${product.id}')">Details</button>
                    ${product.active ? 
                        `<button class="small danger" onclick="archiveProduct('${product.id}')">Archive</button>` :
                        `<button class="small" onclick="restoreProduct('${product.id}')">Restore</button>`
                    }
                </div>
            </div>
            <div class="product-details">
                <div class="detail-group">
                    <span class="detail-label">Description</span>
                    <span class="detail-value">${escapeHtml(product.description || 'No description')}</span>
                </div>
                <div class="detail-group">
                    <span class="detail-label">Type</span>
                    <span class="detail-value">${productType === 'physical' ? '📦 Physical' : '💻 Digital'}</span>
                </div>
                <div class="detail-group">
                    <span class="detail-label">Category</span>
                    <span class="detail-value">${productCategory}</span>
                </div>
                <div class="detail-group">
                    <span class="detail-label">Prices</span>
                    <span class="detail-value">${product.price_count} price(s)</span>
                </div>
                <div class="detail-group">
                    <span class="detail-label">Lowest Price</span>
                    <span class="amount">$${(product.lowest_price / 100).toFixed(2)}</span>
                </div>
            </div>
            ${product.has_upsell ? 
                '<div class="product-badge">Has Upsell Configured</div>' : 
                ''
            }
        </div>
    `;
    }).join('');
}

function openProductModal() {
    const modal = document.getElementById('productModal');
    if (modal) {
        modal.classList.remove('hidden');
        document.body.classList.add('modal-open');
    }
}

function closeProductModal() {
    const modal = document.getElementById('productModal');
    if (modal) {
        modal.classList.add('hidden');
        document.body.classList.remove('modal-open');
    }
}

function showProductForm(productData = null, isCopying = false) {
    editingProduct = !!productData && !isCopying;
    currentProductId = (productData?.id && !isCopying) ? productData.id : null;
    openProductModal();
    
    // Reset uploaded images
    clearImagePreviews();
    
    if (productData) {
        // Set title based on whether we're copying or editing
        const titleText = isCopying ? 'Copy Product (Create New)' : 'Edit Product';
        const titleElement = document.getElementById('productFormTitle');
        titleElement.textContent = titleText;
        
        // Add copy button if we're editing (not copying)
        if (!isCopying) {
            titleElement.innerHTML = `
                ${titleText}
                <button type="button" id="btnCopyProduct" class="small secondary" style="float: right; margin-top: -4px;">Copy Product</button>
            `;
            
            // Set up copy button handler
            setTimeout(() => {
                const copyBtn = document.getElementById('btnCopyProduct');
                if (copyBtn) {
                    copyBtn.addEventListener('click', () => copyProduct(productData));
                }
            }, 0);
        }
        
        // Populate form with existing data
        document.getElementById('productName').value = productData.name || '';
        document.getElementById('productDescription').value = productData.description || '';
        document.getElementById('productActive').checked = productData.active !== false;
        
        // Metadata
        const meta = productData.metadata || {};
        document.getElementById('productType').value = meta.product_type || 'physical';
        document.getElementById('productCategory').value = meta.product_category || 'standard';
        document.getElementById('packageLength').value = meta.package_length || '10';
        document.getElementById('packageWidth').value = meta.package_width || '8';
        document.getElementById('packageHeight').value = meta.package_height || '4';
        document.getElementById('packageWeight').value = meta.package_weight || '1';
        
        // Upsell config - clear when copying (will be hidden anyway)
        document.getElementById('upsellProductId').value = '';
        document.getElementById('upsellPriceId').value = '';
        document.getElementById('upsellOfferText').value = '';
        
        // Page config - only copy if not copying
        if (!isCopying) {
            document.getElementById('heroTitle').value = meta.hero_title || '';
            document.getElementById('heroSubtitle').value = meta.hero_subtitle || '';
        } else {
            document.getElementById('heroTitle').value = '';
            document.getElementById('heroSubtitle').value = '';
        }
        document.getElementById('benefits').value = meta.benefits || '';
        document.getElementById('guarantee').value = meta.guarantee || '';
        
        // Load products for upsell selection (only when editing)
        if (!isCopying) {
            loadProductsForUpsellSelection(meta.upsell_product_id, meta.upsell_price_id);
        }
        
        // Initialize image upload UI first
        setTimeout(() => {
            initImageUpload();

            const pricesContainer = document.getElementById('pricesContainer');
            if (pricesContainer) {
                pricesContainer.innerHTML = '';
                addPriceRow(true); // First price is default
            }
            
            // THEN show existing images as previews (only if not copying)
            if (!isCopying && productData.images && productData.images.length > 0) {
                productData.images.forEach(url => {
                    uploadedImages.push(url);
                    addImagePreview(url);
                });
            }
        }, 100);

        const pricesSection = document.getElementById('pricesSection');
        const upsellSection = document.getElementById('upsellConfigSection');
        
        if (isCopying) {
            // When copying, show the prices section as if creating new
            pricesSection.classList.remove('hidden');
            setSectionDisabled(pricesSection, false);
            
            // Hide price management section
            document.getElementById('priceManagementSection').classList.add('hidden');
            
            // Hide upsell configuration section when copying
            if (upsellSection) {
                upsellSection.classList.add('hidden');
            }
        } else {
            // When editing, hide prices section and show price management
            pricesSection.classList.add('hidden');
            setSectionDisabled(pricesSection, true);
            
            // Show upsell configuration section when editing
            if (upsellSection) {
                upsellSection.classList.remove('hidden');
            }
        }
    } else {
        // Clear form for new product
        document.getElementById('productFormTitle').textContent = 'Create New Product';
        document.getElementById('productForm').reset();
        document.getElementById('productType').value = 'physical';
        document.getElementById('productCategory').value = 'standard';
        document.getElementById('packageLength').value = '10';
        document.getElementById('packageWidth').value = '8';
        document.getElementById('packageHeight').value = '4';
        document.getElementById('packageWeight').value = '1';
        document.getElementById('productActive').checked = true;

        const pricesSection = document.getElementById('pricesSection');
        pricesSection.classList.remove('hidden');
        setSectionDisabled(pricesSection, false);
        
        // Hide upsell configuration for new products
        const upsellSection = document.getElementById('upsellConfigSection');
        if (upsellSection) {
            upsellSection.classList.add('hidden');
        }
        
        // Initialize image upload UI and add price row
        setTimeout(() => {
            initImageUpload();
            
            const pricesContainer = document.getElementById('pricesContainer');
            if (pricesContainer) {
                pricesContainer.innerHTML = '';
                addPriceRow();
            }
        }, 100);
    }
}

// Hide product form
function hideProductForm() {
    closeProductModal();
    currentProductId = null;
    editingProduct = false;
}

// Copy product - creates a new product form with selected attributes copied
function copyProduct(productData) {
    if (!productData) return;
    
    // Show confirmation message
    showProductMsg('Creating copy of product. Modify as needed and save to create new product.', 'ok');
    
    // Call showProductForm with isCopying=true to populate form but treat as new product
    showProductForm(productData, true);
}

// Load products for upsell selection
async function loadProductsForUpsellSelection(selectedProductId = null, selectedPriceId = null) {
    const container = document.getElementById('upsellProductsContainer');
    if (!container) return;
    
    container.innerHTML = '<div class="muted">Loading products...</div>';
    
    try {
        const clientID = getClientID();
        const token = await getIdToken();
        const apiBase = requireApiBase();
        if (!apiBase) {
            container.innerHTML = '<div class="muted">API configuration not ready. Please try again.</div>';
            return;
        }
        
        const stripeMode = getStripeMode();
        const url = new URL(`${apiBase}/admin/products`);
        url.searchParams.set('clientID', clientID);
        url.searchParams.set('stripeMode', stripeMode);

        // Updated to use RESTful endpoint
        const response = await fetch(url.toString(), {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token
            }
        });

        const data = await response.json();
        
        if (data.error) {
            container.innerHTML = '<div class="muted">Failed to load products</div>';
            return;
        }

        const products = data.products || [];
        
        if (products.length === 0) {
            container.innerHTML = '<div class="muted">No products available for upsell</div>';
            return;
        }

        // Render products
        container.innerHTML = products.map(product => {
            const isSelected = product.id === selectedProductId;
            const defaultPrice = product.lowest_price / 100; // Convert cents to dollars
            const imageUrl = product.images && product.images.length > 0 ? product.images[0] : '';
            
            return `
                <div class="upsell-product-item ${isSelected ? 'selected' : ''}" 
                     data-product-id="${product.id}" 
                     data-price-id="${product.default_price_id || ''}"
                     data-price="${defaultPrice}"
                     onclick="selectUpsellProduct('${product.id}', '${product.default_price_id || ''}', ${defaultPrice})">
                    ${imageUrl ? `<img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(product.name)}">` : '<div class="no-image">No Image</div>'}
                    <div class="upsell-product-info">
                        <div class="upsell-product-name">${escapeHtml(product.name)}</div>
                        <div class="upsell-product-price">${defaultPrice.toFixed(2)}</div>
                    </div>
                    ${isSelected ? '<div class="selected-badge">✓ Selected</div>' : ''}
                </div>
            `;
        }).join('');

        // If there was a selected product, update the offer text
        if (selectedProductId && selectedPriceId) {
            const selectedProduct = products.find(p => p.id === selectedProductId);
            if (selectedProduct) {
                const price = selectedProduct.lowest_price / 100;
                // Only set default text if the field is empty or has the generic template
                const currentText = document.getElementById('upsellOfferText').value;
                if (!currentText || currentText.includes('{{upsell_price}}')) {
                    document.getElementById('upsellOfferText').value = `Yes, I accept the offer for only ${price.toFixed(2)}!`;
                }
            }
        }
        
    } catch (err) {
        console.error('Load upsell products error:', err);
        container.innerHTML = '<div class="muted">Failed to load products</div>';
    }
}

// Select an upsell product
function selectUpsellProduct(productId, priceId, price) {
    // Update hidden fields
    document.getElementById('upsellProductId').value = productId;
    document.getElementById('upsellPriceId').value = priceId;
    
    // Update offer text
    document.getElementById('upsellOfferText').value = `Yes, I accept the offer for only ${price.toFixed(2)}!`;
    
    // Update UI selection
    document.querySelectorAll('.upsell-product-item').forEach(item => {
        item.classList.remove('selected');
        const badge = item.querySelector('.selected-badge');
        if (badge) badge.remove();
    });
    
    const selectedItem = document.querySelector(`[data-product-id="${productId}"]`);
    if (selectedItem) {
        selectedItem.classList.add('selected');
        const badge = document.createElement('div');
        badge.className = 'selected-badge';
        badge.textContent = '✓ Selected';
        selectedItem.appendChild(badge);
    }
    
    showProductMsg('Upsell product selected', 'ok');
    setTimeout(() => showProductMsg(''), 2000);
}

// Modified addPriceRow to support default selection
function addPriceRow(isFirst = false) {
    const container = document.getElementById('pricesContainer');
    const priceId = Date.now();
    
    const priceRow = document.createElement('div');
    priceRow.className = 'price-row';
    priceRow.id = `price-row-${priceId}`;
    priceRow.innerHTML = `
        <div class="grid grid-two">
            <div>
                <label>Nickname</label>
                <input type="text" class="price-nickname" placeholder="e.g. Sales Price or Regular Price">
            </div>
            <div>
                <label>Amount (USD)</label>
                <input type="number" class="price-amount" placeholder="29.99" min="0.50" step="0.01" required>
            </div>
        </div>
        <div class="grid grid-two mt8">
            <div>
                <label>Currency</label>
                <select class="price-currency">
                    <option value="usd" selected>USD</option>
                    <option value="eur">EUR</option>
                    <option value="gbp">GBP</option>
                    <option value="cad">CAD</option>
                </select>
            </div>
            <div>
                <label style="display:flex;align-items:center;gap:8px;">
                    <input type="checkbox" class="price-default" ${isFirst ? 'checked' : ''} style="width:auto;" onchange="setDefaultPrice(this)"> 
                    Default Price
                </label>
            </div>
        </div>
        <div style="margin-top: 8px;">
            <button type="button" class="small danger" onclick="removePriceRow('${priceId}')" style="width: auto;">Remove Price</button>
        </div>
    `;
    
    container.appendChild(priceRow);
}

// Ensure only one default price is selected
function setDefaultPrice(checkbox) {
    if (checkbox.checked) {
        document.querySelectorAll('.price-default').forEach(cb => {
            if (cb !== checkbox) cb.checked = false;
        });
    } else {
        // If unchecking, ensure at least one is checked
        const anyChecked = Array.from(document.querySelectorAll('.price-default')).some(cb => cb.checked);
        if (!anyChecked) {
            checkbox.checked = true; // Keep this one checked
            showProductMsg('At least one price must be marked as default', 'err');
        }
    }
}

// Remove a price row
function removePriceRow(priceId) {
    const row = document.getElementById(`price-row-${priceId}`);
    if (row) row.remove();
}

// Modified saveProduct to use getAllProductImages()
async function saveProduct(evt) {
    if (evt && typeof evt.preventDefault === 'function') {
        evt.preventDefault();
    }

    const btn = document.getElementById('btnSaveProduct');
    const res = startSpin(btn, 'Saving... (waiting for images)');
    showProductMsg('Saving product...', 'muted');

    try {
        if (pendingUploads.length) {
            await Promise.race([
                Promise.allSettled(pendingUploads),
                new Promise((_, rej) => setTimeout(() => rej(new Error('Image upload timeout')), 180000))
            ]);
        }
    
        const clientID = getClientID();
        if (!clientID) {
            showProductMsg('Please set your clientID in the Stripe Keys tab first', 'err');
            return;
        }

        if (!currentProductId) {
            editingProduct = false;
        }

        // Gather form data
        const productData = {
            name: document.getElementById('productName').value.trim(),
            description: document.getElementById('productDescription').value.trim(),
            active: document.getElementById('productActive').checked,
            product_type: document.getElementById('productType').value,
            product_category: document.getElementById('productCategory').value,
            package_length: parseFloat(document.getElementById('packageLength').value),
            package_width: parseFloat(document.getElementById('packageWidth').value),
            package_height: parseFloat(document.getElementById('packageHeight').value),
            package_weight: parseFloat(document.getElementById('packageWeight').value),
            upsell_product_id: document.getElementById('upsellProductId').value.trim(),
            upsell_price_id: document.getElementById('upsellPriceId').value.trim(),
            upsell_offer_text: document.getElementById('upsellOfferText').value.trim(),
            hero_title: document.getElementById('heroTitle').value.trim(),
            hero_subtitle: document.getElementById('heroSubtitle').value.trim(),
            benefits: document.getElementById('benefits').value.trim(),
            guarantee: document.getElementById('guarantee').value.trim()
        };

        console.log(productData);

        if (!productData.name) {
            showProductMsg('Product name is required', 'err');
            return;
        }

        const allImages = getAllProductImages();
        productData.images = allImages; // may be []

        if (productData.images.length === 0) {
            showProductMsg('No images selected. Saving will clear images on Stripe.', 'muted');
        }

        // If creating new product, gather prices
        if (!editingProduct) {
            const priceRows = document.querySelectorAll('.price-row');
            productData.prices = [];

            for (const row of priceRows) {
                const nickname = row.querySelector('.price-nickname').value.trim();
                const amountDollars = parseFloat(row.querySelector('.price-amount').value);
                const currency = row.querySelector('.price-currency').value;
                const isDefault = row.querySelector('.price-default').checked;
                
                const amountCents = Math.round(amountDollars * 100);
                
                if (amountCents && amountCents >= 50) {
                    productData.prices.push({
                        nickname,
                        unit_amount: amountCents,
                        currency,
                        metadata: {
                            is_default: isDefault ? 'true' : 'false'
                        }
                    });
                }
            }

            if (productData.prices.length === 0) {
                showProductMsg('Please add at least one price ($0.50 minimum)', 'err');
                return;
            }
        }
    
        const token = await getIdToken();
        
        console.log('Sending payload:', JSON.stringify(productData, null, 2));
        
        // Updated to use RESTful endpoints
        const apiBase = requireApiBase();
        if (!apiBase) {
            stopSpin(res);
            showProductMsg('Configuration not ready. Please try again.', 'err');
            return;
        }

        const stripeMode = getStripeMode();
        const query = `clientID=${encodeURIComponent(clientID)}&stripeMode=${encodeURIComponent(stripeMode)}`;
        let url, method;
        if (editingProduct && currentProductId) {
            // Update existing product
            url = `${apiBase}/admin/products/${currentProductId}?${query}`;
            method = 'PUT';
        } else {
            // Create new product
            url = `${apiBase}/admin/products?${query}`;
            method = 'POST';
        }
        
        const response = await fetch(url, {
            method: method,
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token
            },
            body: JSON.stringify(productData)
        });

        const data = await response.json();
        
        if (data.error) {
            stopSpin(res); 
            showProductMsg(`Error: ${data.error}`, 'err');
            return;
        }

        showProductMsg(`Product ${editingProduct ? 'updated' : 'created'} successfully!`, 'ok');
        setTimeout(() => {
            hideProductForm();
            loadProducts();
        }, 1500);
    } catch (err) {
        stopSpin(res); 
        console.error('Save product error:', err);
        showProductMsg(`Failed to save product: ${err.message}`, 'err');
    } finally {
        stopSpin(res); 
    }
}

// Edit existing product
async function editProduct(productId) {
    showProductMsg('');
    
    // Check if we have the product form in the page
    const formSection = document.getElementById('productFormSection');
    const priceManagementSection = document.getElementById('priceManagementSection');
    
    if (!formSection || !priceManagementSection) {
        // Form doesn't exist - open in Stripe dashboard or show simple edit modal
        showSimpleEditModal(productId);
        return;
    }
    
    try {
        const clientID = getClientID();
        const token = await getIdToken();
        const apiBase = requireApiBase();
        if (!apiBase) {
            showProductMsg('Configuration not ready. Please try again.', 'err');
            return;
        }
        const stripeMode = getStripeMode();
        const url = `${apiBase}/admin/products/${productId}?clientID=${encodeURIComponent(clientID)}&stripeMode=${encodeURIComponent(stripeMode)}`;
        
        // Updated to use RESTful endpoint
        const response = await fetch(url, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token
            }
        });

        const data = await response.json();
        console.log(data);
        
        if (data.error) {
            showProductMsg(`Error: ${data.error}`, 'err');
            return;
        }

        // Store prices globally for showProductForm to access
        window.currentProductPrices = data.prices || [];

        // Show Price Management section to allow price default change
        priceManagementSection.classList.remove('hidden');
        
        showProductForm(data.product);

        // Render existing prices so default can be changed
        displayExistingPrices(window.currentProductPrices);

    } catch (err) {
        console.error('Edit product error:', err);
        showProductMsg(`Failed to load product: ${err.message}`, 'err');
    } finally {
        showProductMsg('');
    }
}

// Simple edit modal for when full form isn't available
function showSimpleEditModal(productId) {
    const stripeMode = getStripeMode();
    const isDev = stripeMode === 'test';
    
    let modal = document.getElementById('simpleEditModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'simpleEditModal';
        modal.className = 'modal-overlay';
        modal.innerHTML = `
            <div class="modal-content">
                <div class="modal-header">
                    <h2>Edit Product</h2>
                    <button class="modal-close" onclick="closeSimpleEditModal()">×</button>
                </div>
                <div class="modal-body" id="simpleEditModalBody">
                    <p>To edit this product, you can:</p>
                    <div style="margin-top: 20px;">
                        <button class="btn btn-primary" onclick="openInStripeDashboard('${productId}', ${isDev})">
                            Open in Stripe Dashboard
                        </button>
                    </div>
                    <div style="margin-top: 10px;">
                        <button class="btn btn-secondary" onclick="closeSimpleEditModal()">
                            Cancel
                        </button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }
    
    modal.classList.add('active');
    document.body.classList.add('modal-open');
}

// Close simple edit modal
function closeSimpleEditModal() {
    const modal = document.getElementById('simpleEditModal');
    if (!modal) return;
    
    modal.classList.remove('active');
    document.body.classList.remove('modal-open');
}

// Open product in Stripe dashboard
function openInStripeDashboard(productId, isTest = false) {
    const baseUrl = isTest 
        ? 'https://dashboard.stripe.com/test/products'
        : 'https://dashboard.stripe.com/products';
    window.open(`${baseUrl}/${productId}`, '_blank');
    closeSimpleEditModal();
}

// View product details (modal or expanded view)
async function viewProductDetails(productId) {
    showProductMsg('Loading product details...');
    
    try {
        const clientID = getClientID();
        const token = await getIdToken();
        const apiBase = requireApiBase();
        if (!apiBase) {
            showProductMsg('Configuration not ready. Please try again.', 'err');
            return;
        }
        const stripeMode = getStripeMode();
        const url = `${apiBase}/admin/products/${productId}?clientID=${encodeURIComponent(clientID)}&stripeMode=${encodeURIComponent(stripeMode)}`;
        
        // Updated to use RESTful endpoint
        const response = await fetch(url, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token
            }
        });

        const data = await response.json();
        
        if (data.error) {
            showProductMsg(`Error: ${data.error}`, 'err');
            return;
        }

        displayProductDetailsModal(data.product, data.prices);
    } catch (err) {
        console.error('View product error:', err);
        showProductMsg(`Failed to load product: ${err.message}`, 'err');
    } finally {
        showProductMsg(''); 
    }
}

// Display product details in modal
function displayProductDetailsModal(product, prices) {
    let modal = document.getElementById('productDetailsModal');
    let modalBody = document.getElementById('productDetailsBody');
    
    // Create modal if it doesn't exist
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'productDetailsModal';
        modal.className = 'modal-overlay';
        modal.innerHTML = `
            <div class="modal-content">
                <div class="modal-header">
                    <h2>Product Details</h2>
                    <button class="modal-close" onclick="closeProductDetailsModal()">×</button>
                </div>
                <div class="modal-body" id="productDetailsBody">
                    <!-- Content will be inserted here -->
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        modalBody = document.getElementById('productDetailsBody');
    }
    
    const meta = product.metadata || {};
    const productType = meta.product_type || 'physical';
    const productCategory = meta.product_category || 'standard';
    
    modalBody.innerHTML = `
        <div class="product-details-view">
            <h3>${escapeHtml(product.name)}</h3>
            <p class="muted mono">${product.id}</p>
            
            ${product.description ? `
                <div class="mt16">
                    <strong>Description:</strong>
                    <p>${escapeHtml(product.description)}</p>
                </div>
            ` : ''}
            
            <div class="mt16">
                <strong>Status:</strong> 
                ${product.active ? 
                    '<span class="order-badge badge-success">Active</span>' : 
                    '<span class="order-badge badge-danger">Inactive</span>'
                }
            </div>
            
            <div class="mt16">
                <strong>Product Type:</strong> ${productType === 'physical' ? '📦 Physical (requires shipping)' : '💻 Digital (service)'}
            </div>
            
            <div class="mt16">
                <strong>Product Category:</strong> ${productCategory}
            </div>
            
            ${productType === 'physical' ? `
                <div class="mt16">
                    <strong>Package Dimensions:</strong>
                    <div class="grid grid-two mt8">
                        <div>Length: ${meta.package_length || 10}"</div>
                        <div>Width: ${meta.package_width || 8}"</div>
                        <div>Height: ${meta.package_height || 4}"</div>
                        <div>Weight: ${meta.package_weight || 1} lbs</div>
                    </div>
                </div>
            ` : ''}
            
            ${prices && prices.length > 0 ? `
                <div class="mt16">
                    <strong>Prices:</strong>
                    <div class="mt8">
                        ${prices.map(p => `
                            <div class="price-item">
                                <span>${p.nickname || 'Unnamed'}</span>
                                <span class="amount">$${(p.unit_amount / 100).toFixed(2)} ${p.currency.toUpperCase()}</span>
                                ${p.active ? 
                                    '<span class="order-badge badge-success">Active</span>' : 
                                    '<span class="order-badge badge-danger">Inactive</span>'
                                }
                            </div>
                        `).join('')}
                    </div>
                </div>
            ` : ''}
            
            ${meta.upsell_product_id ? `
                <div class="mt16">
                    <strong>Upsell Configuration:</strong>
                    <div class="muted mt8">
                        <div>
                            Product ID:
                            <a href="#"
                                class="upsell-product-link"
                                onclick="event.preventDefault(); viewProductDetails('${meta.upsell_product_id}')">
                                ${meta.upsell_product_id}
                            </a>
                        </div>
                        ${meta.upsell_offer_text ? `<div>Offer Text: ${escapeHtml(meta.upsell_offer_text)}</div>` : ''}
                    </div>
                </div>
            ` : ''}
            
            ${product.images && product.images.length > 0 ? `
                <div class="mt16">
                    <strong>Images:</strong>
                    <div class="product-images mt8">
                        ${product.images.map(url => `
                            <img src="${escapeHtml(url)}" alt="Product image" style="max-width: 150px; border-radius: 8px; margin-right: 8px;">
                        `).join('')}
                    </div>
                </div>
            ` : ''}
        </div>
    `;
    
    modal.classList.add('active');
    document.body.classList.add('modal-open');
    enableModalInteractions();
    
    // Add escape key listener
    document.addEventListener('keydown', handleModalEscape);
}

// Close product details modal
function closeProductDetailsModal() {
    const modal = document.getElementById('productDetailsModal');
    if (!modal) return;
    
    modal.classList.remove('active');
    document.body.classList.remove('modal-open');
    document.removeEventListener('keydown', handleModalEscape);

    const overlay = document.getElementById('productDetailsModal');
    if (overlay && overlay._onClickToClose) {
        overlay.removeEventListener('click', overlay._onClickToClose);
        delete overlay._onClickToClose;
    }
}

// Archive a product
async function archiveProduct(productId) {
    showProductMsg('Archiving product...', 'muted');
    
    try {
        const clientID = getClientID();
        const token = await getIdToken();
        const apiBase = requireApiBase();
        if (!apiBase) {
            showProductMsg('Configuration not ready. Please try again.', 'err');
            return;
        }
        const stripeMode = getStripeMode();
        const url = `${apiBase}/admin/products/${productId}?clientID=${encodeURIComponent(clientID)}&stripeMode=${encodeURIComponent(stripeMode)}`;
        
        // Updated to use RESTful endpoint
        const response = await fetch(url, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token
            }
        });

        const data = await response.json();
        
        if (data.error) {
            showProductMsg(`Error: ${data.error}`, 'err');
            return;
        }

        showProductMsg('Product archived successfully!', 'ok');
        
        // Update currentProducts array (used by renderProductsList)
        const currentProduct = currentProducts.find(p => p.id === productId);
        if (currentProduct) {
            currentProduct.active = false;
        }
        
        // Update dashboard state if available
        if (window.ProductsDashboard && window.ProductsDashboard.updateProductState) {
            window.ProductsDashboard.updateProductState(productId, { active: false });
        }
        
        // Re-render legacy view if visible
        if (document.getElementById('productsListContainer')) {
            renderProductsList();
        }
        
    } catch (err) {
        console.error('Archive product error:', err);
        showProductMsg(`Failed to archive product: ${err.message}`, 'err');
    }
}

// Restore an archived product
async function restoreProduct(productId) {
    showProductMsg('Restoring product...', 'muted');
    
    try {
        const clientID = getClientID();
        const token = await getIdToken();
        const apiBase = requireApiBase();
        if (!apiBase) {
            showProductMsg('Configuration not ready. Please try again.', 'err');
            return;
        }
        const stripeMode = getStripeMode();
        const url = `${apiBase}/admin/products/${productId}?clientID=${encodeURIComponent(clientID)}&stripeMode=${encodeURIComponent(stripeMode)}`;
        
        // Use PUT to update the product to active
        const response = await fetch(url, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token
            },
            body: JSON.stringify({
                active: true
            })
        });

        const data = await response.json();
        
        if (data.error) {
            showProductMsg(`Error: ${data.error}`, 'err');
            return;
        }

        showProductMsg('Product restored successfully!', 'ok');
        
        // Update currentProducts array (used by renderProductsList)
        const currentProduct = currentProducts.find(p => p.id === productId);
        if (currentProduct) {
            currentProduct.active = true;
        }
        
        // Update dashboard state if available
        if (window.ProductsDashboard && window.ProductsDashboard.updateProductState) {
            window.ProductsDashboard.updateProductState(productId, { active: true });
        }
        
        // Re-render legacy view if visible
        if (document.getElementById('productsListContainer')) {
            renderProductsList();
        }
        
    } catch (err) {
        console.error('Restore product error:', err);
        showProductMsg(`Failed to restore product: ${err.message}`, 'err');
    }
}

// Display existing prices for a product
function displayExistingPrices(prices) {
    const container = document.getElementById('existingPricesContainer');
    if (!container || !prices || prices.length === 0) {
        if (container) container.innerHTML = '<div class="muted">No prices found</div>';
        return;
    }
    
    container.innerHTML = prices.map(price => {
        const isDefault = price.metadata?.is_default === 'true';
        return `
            <div class="price-card ${isDefault ? 'default-price' : ''}">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <strong>${price.nickname || 'Unnamed Price'}</strong>
                        <div class="muted">$${(price.unit_amount / 100).toFixed(2)} ${price.currency.toUpperCase()}</div>
                        <div class="mono" style="font-size: 0.75rem; color: var(--muted);">${price.id}</div>
                    </div>
                    <div style="display: flex; gap: 8px; align-items: center;">
                        ${price.active ? 
                            '<span class="order-badge badge-success">Active</span>' : 
                            '<span class="order-badge badge-danger">Archived</span>'
                        }
                        ${isDefault ? '<span class="order-badge badge-success">Default</span>' : ''}
                        ${price.active && !isDefault ? 
                            `<button type="button" id="btnSetAsDefault" class="small" onclick="setExistingPriceAsDefault('${price.id}')">Set Default</button>` : 
                            ''
                        }
                        ${price.active ? 
                            `<button type="button" id="btnArchivePrice" class="small danger" onclick="archivePrice('${price.id}')">Archive</button>` : 
                            ''
                        }
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

async function setExistingPriceAsDefault(priceId) {
    const btn = document.getElementById('btnSetAsDefault');
    const res = startSpin(btn);
    showProductMsg('Setting default price...', 'muted');
    
    try {
        // 1) Clear default on all prices (delete the key by setting null)
        await unsetDefault();

        // 2) Mark the chosen price as default
        await setDefault(priceId);
        showProductMsg('Default price updated successfully', 'ok');

        // Reload the product to refresh the list / badges
        editProduct(currentProductId);

    } catch (err) {
        stopSpin(res);
        console.error('Set default price error:', err);
        showProductMsg(`Failed to set default price: ${err.message}`, 'err');
    } finally {
        stopSpin(res);
    }
}


// Archive a price
async function archivePrice(priceId) {
    const btn = document.getElementById('btnArchivePrice');
    const res = startSpin(btn);
    showProductMsg('Archiving...', 'muted');
    
    try {
        const clientID = getClientID();
        const token = await getIdToken();
        const apiBase = requireApiBase();
        if (!apiBase) {
            stopSpin(res);
            showProductMsg('Configuration not ready. Please try again.', 'err');
            return;
        }
        const stripeMode = getStripeMode();
        
        // Updated to use RESTful endpoint
        const response = await fetch(`${apiBase}/admin/prices/${priceId}?clientID=${encodeURIComponent(clientID)}&stripeMode=${encodeURIComponent(stripeMode)}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token
            },
            body: JSON.stringify({
                active: false
            })
        });

        const data = await response.json();
        
        if (data.error) {
            stopSpin(res);
            showProductMsg(`Error: ${data.error}`, 'err');
            return;
        }

        showProductMsg('Price archived successfully', 'ok');
        editProduct(currentProductId);
        
    } catch (err) {
        stopSpin(res);
        console.error('Archive price error:', err);
        showProductMsg(`Failed to archive price: ${err.message}`, 'err');
    } finally {
        stopSpin(res);
    }
}

// Initialize image upload UI
function initImageUpload() {
    const container = document.getElementById('productImages');
    if (!container) return;
    
    // Replace textarea with upload UI
    const parent = container.parentElement;
    
    const uploadUI = document.createElement('div');
    uploadUI.className = 'image-upload-container';
    uploadUI.innerHTML = `
    <div class="upload-area" id="uploadArea">
        <input type="file" id="imageFileInput" accept="image/*" multiple style="display: none;">
        <div class="upload-prompt">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="17 8 12 3 7 8"></polyline>
                <line x1="12" y1="3" x2="12" y2="15"></line>
            </svg>
            <p>Click to upload or drag and drop</p>
            <p class="muted" style="font-size: 0.875rem;">PNG, JPG, WEBP up to 10MB</p>
        </div>
    </div>
    <div id="imagePreviewContainer" class="image-preview-container"></div>

    <!-- NEW: selected URL panel -->
    <div id="imageUrlPanel" class="image-url-panel hidden">
        <label class="muted">Selected image URL</label>
        <input id="imageUrlPanelInput" type="text" readonly onclick="this.select()">
        <button type="button" class="small" id="copyImageUrlBtn">Copy</button>
    </div>

    <div id="uploadStatus" class="upload-status"></div>
    <div class="form-group mt12">
        <label>Or paste image URLs (one per line)</label>
        <textarea id="manualImageUrls" rows="3" placeholder="https://example.com/image1.jpg"></textarea>
    </div>
    `;
    
    parent.replaceChild(uploadUI, container);
    // copy button for URL panel
    const copyBtn = document.getElementById('copyImageUrlBtn');
    if (copyBtn) {
        copyBtn.addEventListener('click', () => {
            const inp = document.getElementById('imageUrlPanelInput');
            if (inp) {
            inp.select();
            document.execCommand('copy');
            }
        });
    }
    
    // Set up event listeners
    const uploadArea = document.getElementById('uploadArea');
    const fileInput = document.getElementById('imageFileInput');
    
    uploadArea.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', handleFileSelect);
    
    // Drag and drop
    uploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadArea.classList.add('drag-over');
    });
    
    uploadArea.addEventListener('dragleave', () => {
        uploadArea.classList.remove('drag-over');
    });
    
    uploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadArea.classList.remove('drag-over');
        const files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/'));
        if (files.length > 0) {
            handleFiles(files);
        }
    });
}

async function handleFileSelect(e) {
    const files = Array.from(e.target.files);
    await handleFiles(files);
    e.target.value = ''; // Reset input
}

async function handleFiles(files) {
  const statusEl = document.getElementById('uploadStatus');
  statusEl.textContent = `Uploading ${files.length} image(s)...`;
  statusEl.className = 'upload-status uploading';

  let ok = 0, done = 0, total = files.length;

  for (const file of files) {
    // kick off the upload but do NOT await here (so users can keep working)
    const p = (async () => {
      try {
        const url = await uploadImageSync(file, {
          // prefer medium, fall back to original if medium isn't ready
          prefer: "medium",
          requirePreferred: true,
          maxSeconds: 360,    // bump a bit if your queue is slow
          firstDelayMs: 1000,
          backoffFactor: 1.4
        });
        uploadedImages.push(url);
        addImagePreview(url);
        ok++;
      } catch (err) {
        console.error('Upload failed:', err);
        // Keep the status visible but don't throw; other files can still finish
        statusEl.textContent = `Failed to upload ${file.name}: ${err.message}`;
        statusEl.className = 'upload-status error';
      } finally {
        done++;
        if (done < total) {
          statusEl.textContent = `Uploaded ${ok}/${total}…`;
        } else {
          statusEl.textContent = `Successfully uploaded ${ok}/${total} image(s)`;
          statusEl.className = (ok === total) ? 'upload-status success' : 'upload-status error';
          setTimeout(() => {
            statusEl.textContent = '';
            statusEl.className = 'upload-status';
          }, 3000);
        }
      }
    })();

    enqueueUpload(p);
  }
}

// === small helpers ===
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
async function headOK(url) {
  try {
    // bust CDN cache on the probe to avoid stale 404/403
    const u = new URL(url);
    u.searchParams.set("_probe", Date.now().toString());
    const res = await fetch(u.toString(), { method: "HEAD", cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  }
}

// === synchronous uploader ===
// Resolves with a URL guaranteed to return 200 to a HEAD request.
// Preference: medium (.webp → .jpg) then original (unless requirePreferred=true).
async function uploadImageSync(
  file,
  {
    prefer = "medium",        // "medium" or "original"
    requirePreferred = true, // if true: do NOT fall back to original
    maxSeconds = 360,
    firstDelayMs = 1000,
    backoffFactor = 1.35
  } = {}
) {
  // 1) Get presigned POST
  const presignRes = await fetch(`${UPLOAD_API_BASE}/upload/multiple`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      fileName: file.name,
      contentType: file.type,
      basePrefix: 'products',
      targetBucket: 'images.juniorbay.net'
    })
  });
  if (!presignRes.ok) throw new Error('Failed to get upload URL');
  const presigned = await presignRes.json(); // { id, upload: { url, fields } }

  // 2) Upload to S3
  const form = new FormData();
  Object.entries(presigned.upload.fields).forEach(([k, v]) => form.append(k, v));
  form.append('file', file);
  const putRes = await fetch(presigned.upload.url, { method: 'POST', body: form });
  if (!putRes.ok) throw new Error('Failed to upload file');

  // 3) Poll status → probe candidate URLs
  const deadline = Date.now() + maxSeconds * 1000;
  let delay = firstDelayMs;
  let lastStatus = null;
  let sawMedium = false; // PATCH: give brief extra patience once medium appears

  while (Date.now() < deadline) {
    await sleep(delay);
    delay = Math.min(10_000, Math.ceil(delay * backoffFactor));

    try {
      const s = await fetch(`${UPLOAD_API_BASE}/upload/status/${presigned.id}`, { cache: "no-store" });
      lastStatus = await s.json(); // { status, urls: { original, medium:{webp,jpg} } }
    } catch {
      continue; // transient
    }

    const urls = lastStatus?.urls || {};
    const mediumCandidates = [];
    if (urls.medium?.webp) mediumCandidates.push(urls.medium.webp);
    if (urls.medium?.jpg)  mediumCandidates.push(urls.medium.jpg);

    // PATCH: if medium just appeared, extend deadline a bit to ride out CF error cache
    if (mediumCandidates.length && !sawMedium) {
      sawMedium = true;
      const graceMs = 30_000;
      // small grace to let CF stop 403-caching the new object
      if (Date.now() + graceMs > deadline) {
        // extend only forward, don't shorten
        // (keeps your original maxSeconds if already bigger)
        // eslint-disable-next-line no-self-assign
        delay = Math.min(delay, 1500);
      }
    }

    // Build preference list
    const candidates = [];
    if (prefer === "medium") {
      candidates.push(...mediumCandidates);
      if (!requirePreferred && urls.original) candidates.push(urls.original);
    } else {
      if (urls.original) candidates.push(urls.original);
    }

    for (const url of candidates) {
      if (await headOK(url)) {
        // PATCH: if we are returning original while medium exists, start background upgrade watcher
        if (prefer === 'medium' && !requirePreferred && url === urls.original && mediumCandidates.length) {
          startUpgradeWatcher(presigned.id, mediumCandidates, url);
        }
        return url; // success
      }
    }

    if (lastStatus?.status === 'failed') {
      throw new Error('Image processing failed');
    }
    // If complete but HEAD still 403, keep probing briefly (CF propagation race)
  }

  // Final grace fallback if allowed
  if (!requirePreferred && lastStatus?.urls?.original && await headOK(lastStatus.urls.original)) {
    // PATCH: also kick off background upgrade if mediums exist
    const mc = [];
    if (lastStatus?.urls?.medium?.webp) mc.push(lastStatus.urls.medium.webp);
    if (lastStatus?.urls?.medium?.jpg)  mc.push(lastStatus.urls.medium.jpg);
    if (prefer === 'medium' && mc.length) {
      startUpgradeWatcher(presigned.id, mc, lastStatus.urls.original);
    }
    return lastStatus.urls.original;
  }
  throw new Error(`Timed out waiting for ${prefer} image`);
}


async function uploadImage(file) {
    // Step 1: Request presigned URL
    const response = await fetch(`${UPLOAD_API_BASE}/upload/multiple`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            fileName: file.name,
            contentType: file.type,
            basePrefix: 'products',
            targetBucket: 'images.juniorbay.net'
        })
    });
    
    if (!response.ok) {
        throw new Error('Failed to get upload URL');
    }
    
    const data = await response.json();
    
    // Step 2: Upload file to S3
    const formData = new FormData();
    Object.entries(data.upload.fields).forEach(([key, value]) => {
        formData.append(key, value);
    });
    formData.append('file', file);
    
    const uploadResponse = await fetch(data.upload.url, {
        method: 'POST',
        body: formData
    });
    
    if (!uploadResponse.ok) {
        throw new Error('Failed to upload file');
    }
    
    // Step 3: Poll for processing completion
    const imageUrl = await pollForCompletion(data.id);
    return imageUrl;
}

async function pollForCompletion(imageId, maxAttempts = 30) {
    for (let i = 0; i < maxAttempts; i++) {
        await new Promise(resolve => setTimeout(resolve, 1000)); // Wait 1 second
        
        try {
            const response = await fetch(`${UPLOAD_API_BASE}/upload/status/${imageId}`);
            const status = await response.json();
            
            if (status.status === 'complete') {
                // Return medium JPG URL
                return status.urls?.medium?.jpg || status.urls?.original;
            } else if (status.status === 'failed') {
                throw new Error('Image processing failed');
            }
            // else status is 'pending' or 'processing', keep polling
        } catch (err) {
            console.error('Status check error:', err);
        }
    }
    
    throw new Error('Image processing timeout');
}

function addImagePreview(url) {
  const container = document.getElementById('imagePreviewContainer');

  const preview = document.createElement('div');
  preview.className = 'image-preview-item';
  preview.setAttribute('data-url', url);

  // simple tooltip via title
  preview.title = url;

  preview.innerHTML = `
      <img src="${url}" alt="Product image">
      <div class="image-url-overlay">${url}</div>
      <button type="button" class="remove-image-btn" onclick="removeImage('${url}')">×</button>
  `;

  // click/tap shows URL in the panel
  const showInPanel = () => {
    const panel = document.getElementById('imageUrlPanel');
    const input = document.getElementById('imageUrlPanelInput');
    if (panel && input) {
      panel.classList.remove('hidden');
      input.value = url;
      // auto-select so mobile users can long-press to copy
      input.focus();
      input.select();
    }
  };
  preview.addEventListener('click', showInPanel);
  preview.addEventListener('touchstart', showInPanel, { passive: true });

  container.appendChild(preview);
}


function removeImage(url) {
    uploadedImages = uploadedImages.filter(u => u !== url);
    
    // Remove preview
    const container = document.getElementById('imagePreviewContainer');
    const previews = container.querySelectorAll('.image-preview-item');
    previews.forEach(preview => {
        const img = preview.querySelector('img');
        if (img && img.src === url) {
            preview.remove();
        }
    });

    const ta = document.getElementById('manualImageUrls');
    if (ta) {
        ta.value = ta.value
        .split('\n')
        .map(s => s.trim())
        .filter(s => s && s !== url)
        .join('\n');
    }

    const disp = document.getElementById('imageUrlPanelInput');
    if (disp && disp.value === url) disp.value = '';
}

function getAllProductImages() {
    // Combine uploaded images with manually entered URLs
    const manualUrls = document.getElementById('manualImageUrls')?.value || '';
    const manualImageArray = manualUrls
        .split('\n')
        .map(url => url.trim())
        .filter(url => url);
    
    const all = [...uploadedImages, ...manualImageArray];
    return Array.from(new Set(all)); // remove duplicates
}

function clearImagePreviews() {
    // --- cleanup any previous upload UI state if it already exists ---
    const previewEl = document.getElementById('imagePreviewContainer');
    if (previewEl) previewEl.innerHTML = '';

    const statusEl = document.getElementById('uploadStatus');
    if (statusEl) {
    statusEl.textContent = '';
    statusEl.className = 'upload-status';
    }

    const manualEl = document.getElementById('manualImageUrls');
    if (manualEl) manualEl.value = '';

    const urlPanel = document.getElementById('imageUrlPanel');
    const urlPanelInput = document.getElementById('imageUrlPanelInput');
    if (urlPanel && urlPanelInput) {
    urlPanel.classList.add('hidden');
    urlPanelInput.value = '';
    }

    // also reset our in-memory list
    uploadedImages = [];
}


// Helper to show messages
function showProductMsg(msg, cls = 'muted') {
    const formMsgTop = document.getElementById('productFormMsg');
    const formMsgBottom = document.getElementById('productFormMsgBottom');
    const listMsg = document.getElementById('productsMsg');
    const formSection = document.getElementById('productFormSection');
    const formVisible = formSection && !formSection.classList.contains('hidden');

    if (formVisible && (formMsgTop || formMsgBottom)) {
        if (formMsgTop) {
            formMsgTop.textContent = msg;
            formMsgTop.className = cls;
        }
        if (formMsgBottom) {
            formMsgBottom.textContent = msg;
            formMsgBottom.className = cls;
        }
        return;
    }

    if (listMsg) {
        listMsg.textContent = msg;
        listMsg.className = cls;
        return;
    }

    const dashboardCard = document.querySelector('#products-tab .card-body');
    if (dashboardCard) {
        let banner = dashboardCard.querySelector('.products-dashboard-msg');
        if (!banner) {
            banner = document.createElement('div');
            banner.className = `products-dashboard-msg ${cls}`;
            banner.style.marginTop = '12px';
            dashboardCard.prepend(banner);
        }
        banner.textContent = msg;
        banner.className = `products-dashboard-msg ${cls}`;
        if (!msg) {
            banner.remove();
        }
        return;
    }

    if (msg) {
        console.log(`[Products] ${cls}: ${msg}`);
    }
}

// HTML escape helper
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function startSpin(el, loadMsg='') {
    const originalText = el.innerHTML;
    el.disabled = true;
    el.innerHTML = '<span class="button-spinner"></span>'; 
    loadMsg.length > 0 ? el.innerHTML += ` ${loadMsg}` : '';
    return spinObj = {
        element: el,
        innerHtml: originalText
    }
}

function stopSpin(spinObj) {
    spinObj.element.disabled = false;
    spinObj.element.innerHTML = spinObj.innerHtml;
}

// Helper to get current clientID
function getClientID() {
    return document.getElementById('clientID')?.value?.trim() || 
           sessionStorage.getItem('clientID') || 
           null;
}

// helper to remove required attribute from hidden sections.
function setSectionDisabled(sectionEl, disabled = true) {
  if (!sectionEl) return;
  sectionEl.querySelectorAll('input, select, textarea, button').forEach(el => {
    if (disabled) {
      // remember original required so we can restore
      if (el.required) { el.dataset.wasRequired = '1'; el.required = false; }
      el.disabled = true;
    } else {
      el.disabled = false;
      if (el.dataset.wasRequired === '1') { el.required = true; delete el.dataset.wasRequired; }
    }
  });
}

// Helper to set all existing prices as default = false
async function unsetDefault() {
    const prices = Array.isArray(window.currentProductPrices) ? window.currentProductPrices : [];
    if (!prices || prices.length === 0) return;

    const token = await getIdToken();
    const clientID = getClientID();
    const apiBase = requireApiBase();
    if (!apiBase) {
        showProductMsg('Configuration not ready. Please try again.', 'err');
        return;
    }
    const stripeMode = getStripeMode();

    for (const p of prices) {
      // Updated to use RESTful endpoint
      await fetch(`${apiBase}/admin/prices/${p.id}?clientID=${encodeURIComponent(clientID)}&stripeMode=${encodeURIComponent(stripeMode)}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': token
        },
        body: JSON.stringify({
          metadata: { is_default: "" }
        })
      });
    }
}

// Helper to set a single price as default
async function setDefault(priceId) {
    const clientID = getClientID();
    const token = await getIdToken();
    const apiBase = requireApiBase();
    if (!apiBase) {
        showProductMsg('Configuration not ready. Please try again.', 'err');
        return;
    }
    const stripeMode = getStripeMode();

    // Updated to use RESTful endpoint
    await fetch(`${apiBase}/admin/prices/${priceId}?clientID=${encodeURIComponent(clientID)}&stripeMode=${encodeURIComponent(stripeMode)}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': token
      },
      body: JSON.stringify({
        metadata: { is_default: 'true' }
      })
    });
}

// --- modal helpers ---
function handleModalEscape(e) {
  if (e.key === 'Escape') {
    closeProductDetailsModal();
  }
}

function enableModalInteractions() {
  const overlay = document.getElementById('productDetailsModal');
  const content = overlay?.querySelector('.modal-content');

  // Close when clicking outside the dialog
  if (overlay) {
    overlay.addEventListener('click', overlay._onClickToClose = (evt) => {
      if (!content || !content.contains(evt.target)) {
        closeProductDetailsModal();
      }
    });
  }

  // Prevent wheel/touch from bubbling to the page
  if (content) {
    content.addEventListener('wheel', (e) => e.stopPropagation(), { passive: true });
    content.addEventListener('touchmove', (e) => e.stopPropagation(), { passive: true });
  }
}

// CSS for image upload UI - add to admin-styles.css
const imageUploadStyles = `
.image-upload-container {
    margin-top: 8px;
}

.upload-area {
    border: 2px dashed #ccc;
    border-radius: 8px;
    padding: 32px;
    text-align: center;
    cursor: pointer;
    transition: all 0.3s;
    background: #fafafa;
}

.upload-area:hover {
    border-color: #007bff;
    background: #f0f8ff;
}

.upload-area.drag-over {
    border-color: #007bff;
    background: #e3f2fd;
}

.upload-prompt svg {
    margin: 0 auto 16px;
    color: #666;
}

.upload-prompt p {
    margin: 8px 0;
    color: #333;
}

.price-card {
    border: 1px solid #ddd;
    padding: 12px;
    margin-bottom: 8px;
    border-radius: 4px;
    background: #0f1a2e;
    transition: all 0.2s;
}

.price-card.default-price {
    border: 1px solid var(--accent);
    background: rgba(34, 197, 94, 0.1);
}

.price-card:hover {
    border-color: var(--brand);
}

.image-preview-container {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
    gap: 12px;
    margin-top: 16px;
}

.image-preview-item {
    position: relative;
    border: 1px solid #ddd;
    border-radius: 8px;
    overflow: hidden;
    aspect-ratio: 1;
}

.image-preview-item img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

.remove-image-btn {
    position: absolute;
    top: 4px;
    right: 4px;
    background: rgba(255, 0, 0, 0.8);
    color: white;
    border: none;
    border-radius: 50%;
    width: 24px;
    height: 24px;
    cursor: pointer;
    font-size: 18px;
    line-height: 1;
    display: flex;
    align-items: center;
    justify-content: center;
}

.remove-image-btn:hover {
    background: rgba(255, 0, 0, 1);
}

.upload-status {
    margin-top: 12px;
    padding: 8px;
    border-radius: 4px;
    font-size: 0.875rem;
}

.upload-status.uploading {
    background: #e3f2fd;
    color: #1976d2;
}

.upload-status.success {
    background: #e8f5e9;
    color: #2e7d32;
}

.upload-status.error {
    background: #ffebee;
    color: #c62828;
}

/* Shows the URL on hover/tap */
.image-preview-item {
  position: relative;
  cursor: pointer;
}

.image-url-overlay {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  max-height: 70%;
  overflow: hidden;
  padding: 6px 8px;
  font-size: 12px;
  line-height: 1.2;
  background: rgba(0,0,0,0.65);
  color: #fff;
  opacity: 0;
  transition: opacity .2s ease;
  word-break: break-all;
}

.image-preview-item:hover .image-url-overlay {
  opacity: 1;
}

/* Panel under grid showing the selected URL */
.image-url-panel {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  align-items: end;
  margin-top: 8px;
}

.image-url-panel label {
  grid-column: 1 / -1;
}

.image-url-panel input[type="text"] {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid #ccc;
  background: #0b1426; /* matches your dark tone */
  color: #e8eefc;
  border-radius: 6px;
}

/* Upsell Product Selection Styles */
.upsell-product-link {
    color: #ff8309;
    text-decoration: underline;
    cursor: pointer;
}

.upsell-product-link:hover {
    color: #ff4181;
}

.upsell-products-container {
    max-height: 400px;
    overflow-y: auto;
    border: 1px solid #ddd;
    border-radius: 8px;
    padding: 12px;
    background: #0b1426;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 12px;
}

.upsell-product-item {
    position: relative;
    border: 2px solid #2a3f5f;
    border-radius: 8px;
    padding: 12px;
    cursor: pointer;
    transition: all 0.2s;
    background: #0f1a2e;
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.upsell-product-item:hover {
    border-color: #4a7ba7;
    transform: translateY(-2px);
    box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
}

.upsell-product-item.selected {
    border-color: var(--accent);
    background: rgba(34, 197, 94, 0.1);
}

.upsell-product-item img {
    width: 100%;
    height: 120px;
    object-fit: cover;
    border-radius: 6px;
}

.upsell-product-item .no-image {
    width: 100%;
    height: 120px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #1a2742;
    border-radius: 6px;
    color: #6b7280;
    font-size: 0.875rem;
}

.upsell-product-info {
    display: flex;
    flex-direction: column;
    gap: 4px;
}

.upsell-product-name {
    font-weight: 600;
    font-size: 0.95rem;
    color: #e8eefc;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.upsell-product-price {
    font-size: 1.1rem;
    font-weight: 700;
    color: var(--accent);
}

.selected-badge {
    position: absolute;
    top: 8px;
    right: 8px;
    background: var(--accent);
    color: white;
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 600;
}

/* Modal styles */
.modal-overlay {
    display: none;
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.7);
    z-index: 1000;
    align-items: center;
    justify-content: center;
    padding: 20px;
}

.modal-overlay.active {
    display: flex;
    position: fixed;
    overflow: hidden;
}

.modal-content {
    background: var(--card-bg, #0f1a2e);
    border-radius: 12px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
    position: relative;
    display: flex;
    flex-direction: column;
}

.modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 20px;
    border-bottom: 1px solid #2a3f5f;
    font-size: 1.25rem;
    font-weight: 600;
    color: #e8eefc;
}

.modal-close-btn {
    background: none;
    border: none;
    font-size: 2rem;
    line-height: 1;
    color: #9ca3af;
    cursor: pointer;
    padding: 0;
    width: 32px;
    height: 32px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 4px;
    transition: all 0.2s;
}

.modal-close-btn:hover {
    background: rgba(255, 255, 255, 0.1);
    color: #e8eefc;
}

.modal-body {
    padding: 20px;
    overflow-y: auto;
    flex: 1;
}

.modal-actions {
    padding: 16px 20px;
    border-top: 1px solid #2a3f5f;
    display: flex;
    justify-content: flex-end;
    gap: 8px;
}

/* Lock page scroll whenever a modal is open */
body.modal-open {
  overflow: hidden;
}

/* Ensure the overlay itself doesn't pass scroll to the page (esp. iOS) */
.modal-overlay {
  overscroll-behavior: contain;
}

/* Constrain modal to viewport and allow inner scrolling */
.modal-content {
  max-height: 90vh;              /* fits on screen */
  display: flex;
  flex-direction: column;
}

/* Already present but keep it here for clarity */
.modal-body {
  overflow-y: auto;              /* scroll long content */
  -webkit-overflow-scrolling: touch;
}
`;

function bootstrapProductsModule() {
    const injectStyles = () => {
        const existing = document.getElementById('product-upload-style');
        if (existing) return;
        const style = document.createElement('style');
        style.id = 'product-upload-style';
        style.textContent = imageUploadStyles;
        document.head.appendChild(style);
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            injectStyles();
            initProducts();
        }, { once: true });
    } else {
        injectStyles();
        initProducts();
    }
}

bootstrapProductsModule();
