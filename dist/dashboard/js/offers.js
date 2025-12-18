// ======== ENHANCED OFFERS MANAGEMENT WITH VISUAL PRODUCT SELECTOR ========

let currentOfferData = {};
let isLoadingOffers = false;
let offersAvailableProducts = []; // Namespaced to avoid conflicts
const offerProductOrder = new Map(); // NEW: Track product order for each offer (offerKey -> [productId1, productId2, ...])

function resolveOfferMsgTarget() {
    if (document.getElementById('offersMsg')) return 'offersMsg';
    if (document.getElementById('offersListMessage')) return 'offersListMessage';
    return null;
}

function relayOfferMessage(text, type = 'info') {
    const target = resolveOfferMsgTarget();
    if (target) {
        setMsg(target, text, type);
    }
}

// ======== DASHBOARD OFFER LIST (CARD VIEW) ========

const dashboardOffersState = {
    offers: [],
    isLoading: false,
    productsMap: new Map(),
    clientID: '',
    loadBtn: null
};

(function(window, document) {
    'use strict';

    function init() {
        bindLoadButton();
        bindAddButton();
        bindCardActions();
        if (dashboardOffersState.offers.length) {
            renderOfferCards();
        }
    }

    function bindLoadButton() {
        const btn = document.getElementById('btnLoadOffers');
        if (!btn || btn.dataset.offersBound === 'true') return;
        btn.dataset.offersBound = 'true';
        btn.addEventListener('click', () => loadOffersList());
        dashboardOffersState.loadBtn = btn;
    }

    function bindCardActions() {
        const container = document.getElementById('offersListContainer');
        if (!container || container.dataset.offersBound === 'true') return;
        container.dataset.offersBound = 'true';
        container.addEventListener('click', handleCardAction);
    }

    function handleCardAction(event) {
        const actionBtn = event.target.closest('[data-offer-action]');
        if (!actionBtn) return;
        const key = actionBtn.dataset.offerKey;
        const action = actionBtn.dataset.offerAction;
        if (action === 'view') {
            viewOffer(key);
        } else if (action === 'edit') {
            editOffer(key);
        } else if (action === 'delete') {
            deleteOffer(key);
        }
    }

    function bindAddButton() {
        const btn = document.getElementById('btnAddOffer');
        if (!btn || btn.dataset.addOfferBound === 'true') return;
        btn.dataset.addOfferBound = 'true';
        btn.addEventListener('click', openAddOfferModal);

        // Bind save button
        const saveBtn = document.getElementById('btnSaveNewOffer');
        if (saveBtn) {
            saveBtn.addEventListener('click', saveNewOffer);
        }

        // Bind product selector button
        const selectProductsBtn = document.getElementById('btnSelectProducts');
        if (selectProductsBtn) {
            selectProductsBtn.addEventListener('click', openAddOfferProductSelector);
        }

        // Bind product search
        const productSearchInput = document.getElementById('addOfferProductSearchInput');
        if (productSearchInput) {
            productSearchInput.addEventListener('input', (e) => {
                filterAddOfferProducts(e.target.value);
            });
        }

        // Auto-generate slug from name
        const nameInput = document.getElementById('offerName');
        const slugInput = document.getElementById('offerSlug');
        if (nameInput && slugInput) {
            nameInput.addEventListener('input', () => {
                if (!slugInput.dataset.manuallyEdited) {
                    slugInput.value = generateSlug(nameInput.value);
                }
            });
            slugInput.addEventListener('input', () => {
                slugInput.dataset.manuallyEdited = 'true';
            });
        }

        // Close modal on overlay click
        const modal = document.getElementById('addOfferModal');
        if (modal) {
            const overlay = modal.querySelector('.modal-overlay');
            if (overlay) {
                overlay.addEventListener('click', closeAddOfferModal);
            }
        }

        // Close product selector on overlay click
        const productModal = document.getElementById('addOfferProductSelectorModal');
        if (productModal) {
            const overlay = productModal.querySelector('.modal-overlay');
            if (overlay) {
                overlay.addEventListener('click', closeAddOfferProductSelector);
            }
        }

        // Close modal on Escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                const modal = document.getElementById('addOfferModal');
                const productModal = document.getElementById('addOfferProductSelectorModal');
                if (productModal && productModal.classList.contains('active')) {
                    closeAddOfferProductSelector();
                } else if (modal && modal.classList.contains('active')) {
                    closeAddOfferModal();
                }
            }
        });
    }

    function openAddOfferModal() {
        const modal = document.getElementById('addOfferModal');
        if (!modal) return;

        // Reset form
        const form = document.getElementById('addOfferForm');
        if (form) form.reset();

        // Clear manually edited flag
        const slugInput = document.getElementById('offerSlug');
        if (slugInput) delete slugInput.dataset.manuallyEdited;

        // Clear messages
        const msgEl = document.getElementById('addOfferMessage');
        if (msgEl) msgEl.innerHTML = '';

        // Reset selected products
        selectedProductIds.clear();
        tempSelectedProductIds.clear();
        updateSelectedProductsDisplay();

        modal.classList.add('active');
    }

    function closeAddOfferModal() {
        const modal = document.getElementById('addOfferModal');
        if (modal) {
            modal.classList.remove('active');
        }
    }

    function generateSlug(text) {
        return text
            .toLowerCase()
            .trim()
            .replace(/[^\w\s-]/g, '')
            .replace(/[\s_-]+/g, '-')
            .replace(/^-+|-+$/g, '');
    }

    // ========== PRODUCT SELECTOR ==========

    // State for product selection
    let allProducts = [];
    let selectedProductIds = new Set();
    let tempSelectedProductIds = new Set();

    async function openAddOfferProductSelector() {
        const modal = document.getElementById('addOfferProductSelectorModal');
        if (!modal) return;

        const msgEl = document.getElementById('addOfferProductSelectorMessage');
        const gridEl = document.getElementById('addOfferProductSelectorGrid');

        // Copy current selections to temp
        tempSelectedProductIds = new Set(selectedProductIds);

        // Reset button text
        updateApplyButtonText();

        // Show modal
        modal.classList.add('active');

        // Load products if not already loaded
        if (allProducts.length === 0) {
            try {
                if (msgEl) {
                    msgEl.innerHTML = '<div class="alert alert-info"><div class="alert-content">Loading products...</div></div>';
                }
                if (gridEl) {
                    gridEl.innerHTML = '';
                }

                const clientID = resolveClientId();
                if (!clientID) {
                    if (msgEl) {
                        msgEl.innerHTML = '<div class="alert alert-danger"><div class="alert-content">No client ID found</div></div>';
                    }
                    return;
                }

                const response = await authedFetch(`/admin/products?clientID=${encodeURIComponent(clientID)}`, {
                    method: 'GET',
                    headers: {
                        'X-Client-Id': clientID
                    }
                });

                if (!response.ok) {
                    throw new Error(response.body?.error || `Failed to load products: HTTP ${response.status}`);
                }

                allProducts = response.body?.products || [];

                if (msgEl) {
                    msgEl.innerHTML = '';
                }

                renderAddOfferProductGrid(allProducts);

            } catch (error) {
                console.error('Load products error:', error);
                if (msgEl) {
                    msgEl.innerHTML = `<div class="alert alert-danger"><div class="alert-content">Failed to load products: ${error.message}</div></div>`;
                }
            }
        } else {
            // Products already loaded, just render
            if (msgEl) {
                msgEl.innerHTML = '';
            }
            renderAddOfferProductGrid(allProducts);
        }
    }

    function closeAddOfferProductSelector() {
        const modal = document.getElementById('addOfferProductSelectorModal');
        if (modal) {
            modal.classList.remove('active');
        }
        // Clear search
        const searchInput = document.getElementById('addOfferProductSearchInput');
        if (searchInput) {
            searchInput.value = '';
        }
        // Reset button text
        const applyBtn = document.querySelector('#addOfferProductSelectorModal .btn-primary');
        if (applyBtn) {
            applyBtn.textContent = 'Apply Selection';
        }
    }

    function renderAddOfferProductGrid(products) {
        const gridEl = document.getElementById('addOfferProductSelectorGrid');
        if (!gridEl) return;

        if (products.length === 0) {
            gridEl.innerHTML = '<div class="selected-products-empty">No products found</div>';
            return;
        }

        gridEl.innerHTML = products.map(product => {
            const isSelected = tempSelectedProductIds.has(product.id);
            const price = product.lowest_price ? formatCurrency(product.lowest_price) : 'N/A';
            const image = product.images && product.images.length > 0 ? product.images[0] : null;

            return `
                <div class="product-card ${isSelected ? 'selected' : ''}" data-product-id="${product.id}">
                    <div class="product-card-image-container">
                        ${image
                            ? `<img src="${image}" alt="${product.name}" class="product-card-image">`
                            : '<div class="product-card-image no-image">No Image</div>'
                        }
                        <div class="product-card-checkmark"></div>
                    </div>
                    <div class="product-card-content">
                        <div class="product-card-name">${product.name || 'Unnamed Product'}</div>
                        <div class="product-card-price">${price}</div>
                    </div>
                </div>
            `;
        }).join('');

        // Add click handlers to cards
        gridEl.querySelectorAll('.product-card').forEach(card => {
            card.addEventListener('click', () => {
                const productId = card.dataset.productId;
                toggleAddOfferProductSelection(productId);
            });
        });
    }

    function toggleAddOfferProductSelection(productId) {
        if (tempSelectedProductIds.has(productId)) {
            tempSelectedProductIds.delete(productId);
        } else {
            tempSelectedProductIds.add(productId);
        }

        // Update card visual state
        const card = document.querySelector(`#addOfferProductSelectorGrid [data-product-id="${productId}"]`);
        if (card) {
            const isSelected = tempSelectedProductIds.has(productId);
            if (isSelected) {
                card.classList.add('selected');
            } else {
                card.classList.remove('selected');
            }
            console.log(`Product ${productId} ${isSelected ? 'selected' : 'deselected'}`);
        } else {
            console.warn(`Card not found for product ${productId}`);
        }

        // Update apply button text with count
        updateApplyButtonText();
    }

    function updateApplyButtonText() {
        const applyBtn = document.querySelector('#addOfferProductSelectorModal .btn-primary');
        const count = tempSelectedProductIds.size;

        if (applyBtn) {
            if (count === 0) {
                applyBtn.textContent = 'Apply Selection';
            } else if (count === 1) {
                applyBtn.textContent = '1 selected';
            } else {
                applyBtn.textContent = `${count} selected`;
            }
        }
    }

    function filterAddOfferProducts(searchTerm) {
        const term = searchTerm.toLowerCase().trim();
        if (!term) {
            renderAddOfferProductGrid(allProducts);
            return;
        }

        const filtered = allProducts.filter(product => {
            const name = (product.name || '').toLowerCase();
            return name.includes(term);
        });

        renderAddOfferProductGrid(filtered);
    }

    function applyAddOfferProductSelection() {
        // Copy temp selections to actual selections
        selectedProductIds = new Set(tempSelectedProductIds);

        // Update the selected products display
        updateSelectedProductsDisplay();

        // Close the selector modal
        closeAddOfferProductSelector();
    }

    function updateSelectedProductsDisplay() {
        const listEl = document.getElementById('selectedProductsList');
        if (!listEl) return;

        if (selectedProductIds.size === 0) {
            listEl.innerHTML = '<div class="selected-products-empty">No products selected</div>';
            return;
        }

        const selectedProducts = allProducts.filter(p => selectedProductIds.has(p.id));

        listEl.innerHTML = selectedProducts.map(product => {
            return `
                <div class="selected-product-badge">
                    <span>${product.name}</span>
                    <button class="selected-product-badge-remove" onclick="removeSelectedProduct('${product.id}')" type="button">×</button>
                </div>
            `;
        }).join('');
    }

    function removeSelectedProduct(productId) {
        selectedProductIds.delete(productId);
        tempSelectedProductIds.delete(productId);
        updateSelectedProductsDisplay();
    }

    // ========== END PRODUCT SELECTOR ==========

    async function saveNewOffer() {
        const nameInput = document.getElementById('offerName');
        const slugInput = document.getElementById('offerSlug');
        const activeInput = document.getElementById('offerActive');
        const msgEl = document.getElementById('addOfferMessage');

        // Validation
        if (!nameInput || !nameInput.value.trim()) {
            if (msgEl) {
                msgEl.innerHTML = '<div class="alert alert-danger"><div class="alert-content">Please enter an offer name</div></div>';
            }
            return;
        }

        if (!slugInput || !slugInput.value.trim()) {
            if (msgEl) {
                msgEl.innerHTML = '<div class="alert alert-danger"><div class="alert-content">Please enter a slug</div></div>';
            }
            return;
        }

        const clientID = resolveClientId();
        if (!clientID) {
            if (msgEl) {
                msgEl.innerHTML = '<div class="alert alert-danger"><div class="alert-content">No client ID found. Please select a client first.</div></div>';
            }
            return;
        }

        const slug = slugInput.value.trim();
        const name = nameInput.value.trim();
        const active = activeInput ? activeInput.checked : true;

        try {
            if (msgEl) {
                msgEl.innerHTML = '<div class="alert alert-info"><div class="alert-content">Creating offer...</div></div>';
            }

            // Step 1: Load current offers
            const getResponse = await authedFetch(`/admin/offers?clientID=${encodeURIComponent(clientID)}`, {
                method: 'GET',
                headers: {
                    'X-Client-Id': clientID
                }
            });

            if (!getResponse.ok) {
                throw new Error(getResponse.body?.error || `Failed to load current offers: HTTP ${getResponse.status}`);
            }

            // Step 2: Add new offer to the offers object
            const currentOffers = getResponse.body?.offers || {};

            // Check if slug already exists
            if (currentOffers[slug]) {
                if (msgEl) {
                    msgEl.innerHTML = '<div class="alert alert-danger"><div class="alert-content">An offer with this slug already exists</div></div>';
                }
                return;
            }

            // Add the new offer using slug as key
            currentOffers[slug] = {
                name: name,
                active: active,
                product_ids: Array.from(selectedProductIds)
            };

            // Step 3: Save the updated offers object
            const putResponse = await authedFetch(`/admin/offers`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Client-Id': clientID
                },
                body: { offers: currentOffers }
            });

            if (!putResponse.ok) {
                throw new Error(putResponse.body?.error || `HTTP ${putResponse.status}`);
            }

            // Success!
            closeAddOfferModal();
            setMsg('offersListMessage', `Offer "${name}" created successfully!`, 'success');

            // Reload offers to show the new one
            await loadOffersList();

        } catch (error) {
            console.error('Save new offer error:', error);
            if (msgEl) {
                msgEl.innerHTML = `<div class="alert alert-danger"><div class="alert-content">Failed to create offer: ${error.message}</div></div>`;
            }
        }
    }

    function resolveClientId() {
        const field = document.getElementById('clientID');
        if (field && field.value.trim()) return field.value.trim();
        if (typeof window.currentClientID === 'function') {
            const id = window.currentClientID();
            if (id) return id;
        }
        return localStorage.getItem('clientID') || sessionStorage.getItem('clientID') || '';
    }

    async function loadOffersList() {
        if (dashboardOffersState.isLoading) return;
        const clientID = resolveClientId();
        if (!clientID) {
            setMsg('offersListMessage', 'Please select a client ID first.', 'error');
            return;
        }

        dashboardOffersState.isLoading = true;
        dashboardOffersState.clientID = clientID;
        toggleLoadButton(true, 'Loading...');
        setMsg('offersListMessage', 'Loading offers...', 'info');

        try {
            await ensureProductsCache(clientID);
            const response = await authedFetch(`/admin/offers?clientID=${encodeURIComponent(clientID)}`, {
                method: 'GET',
                headers: { 'X-Client-Id': clientID }
            });

            if (!response.ok) {
                throw new Error(response.body?.error || `HTTP ${response.status}`);
            }

            const offersObject = response.body?.offers || {};
            dashboardOffersState.offers = normalizeOffers(offersObject);
            renderOfferCards();

            const count = dashboardOffersState.offers.length;
            setMsg('offersListMessage', `Loaded ${count} offer${count === 1 ? '' : 's'}.`, 'success');
        } catch (error) {
            console.error('Load offers error:', error);
            dashboardOffersState.offers = [];
            renderOfferCards();
            setMsg('offersListMessage', `Failed to load offers: ${error.message}`, 'error');
        } finally {
            dashboardOffersState.isLoading = false;
            toggleLoadButton(false);
        }
    }

    async function ensureProductsCache(clientID) {
        if (!offersAvailableProducts.length) {
            await loadAvailableProducts(clientID);
        }
        const map = new Map();
        offersAvailableProducts.forEach(product => map.set(product.id, product));
        dashboardOffersState.productsMap = map;
        return map;
    }

    function normalizeOffers(offersObj) {
        const entries = Object.entries(offersObj || {});
        if (!entries.length) return [];

        const map = dashboardOffersState.productsMap;

        return entries.map(([key, details]) => {
            const productIds = Array.isArray(details?.product_ids)
                ? details.product_ids.filter(Boolean)
                : [];
            const firstProduct = productIds
                .map(id => map.get(id))
                .find(Boolean);

            const heroTitle = details?.hero_title || details?.title || details?.name || key;
            const slug = (details?.path && details.path.trim()) || key;

            return {
                key,
                slug,
                name: heroTitle,
                active: details?.active !== false,
                productIds,
                firstProduct,
                image: details?.hero_image || firstProduct?.images?.[0] || '',
                productsSummary: productIds.length ? productIds.join(', ') : 'No products linked'
            };
        }).sort((a, b) => a.name.localeCompare(b.name));
    }

    function renderOfferCards() {
        const container = document.getElementById('offersListContainer');
        if (!container) return;

        const offers = dashboardOffersState.offers;

        if (!offers.length) {
            container.classList.add('empty');
            container.innerHTML = '<div class="text-muted text-center" style="padding: 32px 0;">No offers found. Click "Load Offers" to fetch the latest data.</div>';
            return;
        }

        container.classList.remove('empty');
        container.innerHTML = offers.map(renderOfferCard).join('');
    }

    function renderOfferCard(offer) {
        const statusClass = offer.active ? 'offer-card-status active' : 'offer-card-status inactive';
        const statusLabel = offer.active ? 'Active' : 'Inactive';

        // Generate responsive image markup using <picture> element
        let imageMarkup;
        if (offer.image) {
            // Check if image is from juniorbay image service (supports responsive sizes)
            const isJuniorBayImage = offer.image.includes('images.juniorbay.net') ||
                                     offer.image.includes('images.juniorbay.com');

            let thumbUrl, mediumUrl;
            if (isJuniorBayImage) {
                // Generate thumb and medium URLs for optimized images
                const baseUrl = offer.image.replace(/\/(medium|thumb|large)\.webp$/, '');
                thumbUrl = `${baseUrl}/thumb.webp`;
                mediumUrl = `${baseUrl}/medium.webp`;
            } else {
                // Use same URL for all sources (e.g., Stripe images)
                thumbUrl = offer.image;
                mediumUrl = offer.image;
            }

            imageMarkup = `
                <picture>
                    <source media="(max-width: 768px)" srcset="${safeHtml(thumbUrl)}">
                    <source media="(min-width: 769px)" srcset="${safeHtml(mediumUrl)}">
                    <img src="${safeHtml(mediumUrl)}" alt="${safeHtml(offer.name)}" loading="lazy">
                </picture>
            `;
        } else {
            imageMarkup = `<div class="offer-card-placeholder">${giftIconSVG()}</div>`;
        }

        return `
            <article class="offer-card ${offer.active ? '' : 'offer-card--inactive'}" data-offer-key="${safeHtml(offer.key)}">
                <div class="offer-card-media">
                    ${imageMarkup}
                </div>
                <div class="offer-card-body">
                    <div class="offer-card-header">
                        <div>
                            <div class="offer-card-name">${safeHtml(offer.name)}</div>
                            <div class="offer-card-slug">/${safeHtml(offer.slug)}</div>
                        </div>
                        <span class="${statusClass}">${statusLabel}</span>
                    </div>
                    <div class="offer-card-products">
                        <span class="label">Products:</span>
                        <span class="value">${safeHtml(offer.productsSummary)}</span>
                    </div>
                    <div class="offer-card-actions">
                        <button class="btn-icon btn-icon-primary" data-offer-action="view" data-offer-key="${safeHtml(offer.key)}" title="View">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                                <circle cx="12" cy="12" r="3"/>
                            </svg>
                        </button>
                        <button class="btn-icon btn-icon-success" data-offer-action="edit" data-offer-key="${safeHtml(offer.key)}" title="Edit">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                            </svg>
                        </button>
                        <button class="btn-icon btn-icon-danger" data-offer-action="delete" data-offer-key="${safeHtml(offer.key)}" title="Delete">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <polyline points="3 6 5 6 21 6"/>
                                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                                <line x1="10" y1="11" x2="10" y2="17"/>
                                <line x1="14" y1="11" x2="14" y2="17"/>
                            </svg>
                        </button>
                    </div>
                </div>
            </article>
        `;
    }

    function viewOffer(key) {
        const offer = dashboardOffersState.offers.find(o => o.key === key);
        if (!offer) return;

        const clientID = dashboardOffersState.clientID || resolveClientId();
        const baseParam = (window.CONFIG && window.CONFIG.apiBase) ? `&baseUrl=${encodeURIComponent(window.CONFIG.apiBase)}` : '';
        const previewUrl = `${window.location.origin}/offer/${encodeURIComponent(offer.slug)}?clientID=${encodeURIComponent(clientID)}${baseParam}`;
        window.open(previewUrl, '_blank');
    }

    function editOffer(key) {
        const offer = dashboardOffersState.offers.find(o => o.key === key);
        if (!offer) return;
        setMsg('offersListMessage', `Edit workflow for "${offer.name}" is coming soon.`, 'info');
    }

    function deleteOffer(key) {
        const offer = dashboardOffersState.offers.find(o => o.key === key);
        if (!offer) return;
        if (!confirm(`Remove offer "${offer.name}" from this list?`)) return;

        dashboardOffersState.offers = dashboardOffersState.offers.filter(o => o.key !== key);
        renderOfferCards();
        setMsg('offersListMessage', `Offer "${offer.name}" removed from the current view.`, 'warning');
    }

    function toggleLoadButton(isLoading, label = 'Loading...') {
        const btn = dashboardOffersState.loadBtn || document.getElementById('btnLoadOffers');
        if (!btn) return;
        if (isLoading) {
            btn.dataset.originalText = btn.textContent;
            btn.disabled = true;
            btn.innerHTML = `<span class="button-spinner"></span> ${label}`;
        } else {
            const original = btn.dataset.originalText || 'Load Offers';
            btn.disabled = false;
            btn.textContent = original;
            delete btn.dataset.originalText;
        }
    }

    function giftIconSVG() {
        return `
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-7"></path>
                <path d="M3 12h18"></path>
                <path d="M12 7v14"></path>
                <path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C10 2 12 7 12 7z"></path>
                <path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C14 2 12 7 12 7z"></path>
            </svg>
        `;
    }

    function safeHtml(value) {
        try {
            if (typeof escapeHtml === 'function') {
                return escapeHtml(value ?? '');
            }
        } catch {
            // fall through
        }
        const div = document.createElement('div');
        div.textContent = value ?? '';
        return div.innerHTML;
    }

    function clearOffers() {
        dashboardOffersState.offers = [];
        dashboardOffersState.clientID = '';
        renderOfferCards();
        setMsg('offersListMessage', '', 'info');
    }

    window.OffersDashboard = {
        init,
        loadOffers: loadOffersList,
        clearOffers,
        viewOffer,
        editOffer,
        deleteOffer
    };

    // Export add offer functions to window
    window.openAddOfferProductSelector = openAddOfferProductSelector;
    window.closeAddOfferProductSelector = closeAddOfferProductSelector;
    window.toggleAddOfferProductSelection = toggleAddOfferProductSelection;
    window.applyAddOfferProductSelection = applyAddOfferProductSelection;
    window.removeSelectedProduct = removeSelectedProduct;
    window.openAddOfferModal = openAddOfferModal;
    window.closeAddOfferModal = closeAddOfferModal;

})(window, document);

// ======== AUTO-LOAD ON TAB SWITCH ========

function initOffersTab() {
    const clientID = window.currentClientID ? window.currentClientID() : null;
    if (clientID && !isLoadingOffers && Object.keys(currentOfferData).length === 0) {
        loadOffers(clientID);
    }
}

// ======== LOAD OFFERS ========

async function loadOffers(clientID) {
    if (isLoadingOffers) return;
    
    try {
        isLoadingOffers = true;
        
        // Use currentClientID if no clientID provided
        if (!clientID) {
            clientID = window.currentClientID ? window.currentClientID() : null;
        }
        
        if (!clientID) {
            setMsg('offersMsg', 'No client ID available. Please sign in.', 'error');
            return;
        }
        
        setMsg('offersMsg', 'Loading offers...', 'info');
        
        // const res = await authedFetch(`/`, { 
        //     method: 'POST', 
        //     body: { 
        //         action: "get_client_offers",
        //         clientID: clientID
        //     } 
        // });

        const res = await authedFetch(`/admin/offers?clientID=${encodeURIComponent(clientID)}`, {
            method: 'GET',
            headers: { 'X-Client-Id': clientID }
        });

        
        if (res.ok && res.body) {
            currentOfferData = res.body;

            // Display current mode and preview URL
            const modeSpan = document.getElementById('currentMode');
            if (modeSpan) {
                modeSpan.textContent = currentOfferData.mode || 'test';
            }
            
            const previewUrl = document.getElementById('previewUrl');
            if (previewUrl && currentOfferData.base_url) {
                previewUrl.textContent = `${currentOfferData.base_url}{offer-path}/`;
            }
            
            // Set base URL (read-only display)
            const baseUrlField = document.getElementById('baseFrontendUrl');
            if (baseUrlField) {
                baseUrlField.value = currentOfferData.base_url || '';
                baseUrlField.readOnly = false; // Allow editing if needed
            }
            
            // Clear and populate offers
            const container = document.getElementById('offersContainer');
            if (container) {
                container.innerHTML = '';
                
                const offers = currentOfferData.offers || {};
                const offerKeys = Object.keys(offers);
                
                if (offerKeys.length === 0) {
                    container.innerHTML = '<div class="no-orders">No offers configured yet. Click "+ Add Offer" to create one.</div>';
                } else {
                    offerKeys.forEach(key => {
                        addOfferToDOM(key, offers[key]);
                    });
                }
                
                setMsg('offersMsg', `Loaded ${offerKeys.length} offer(s) for client ${clientID}`, 'success');
            }
        } else {
            const errorMsg = res.body?.error || res.body?.raw || 'Unknown error';
            setMsg('offersMsg', `Failed to load offers: ${errorMsg}`, 'error');
            console.error('Load offers error:', res);
        }
    } catch (error) {
        console.error('Error loading offers:', error);
        setMsg('offersMsg', `Error loading offers: ${error.message}`, 'error');
    } finally {
        isLoadingOffers = false;
    }
}

// ======== LOAD AVAILABLE PRODUCTS ========

async function loadAvailableProducts(clientIdOverride = null) {
  try {
    let clientID = clientIdOverride || (window.currentClientID ? window.currentClientID() : null);
    if (!clientID) {
        const input = document.getElementById('clientID');
        clientID = input?.value?.trim() || localStorage.getItem('clientID') || sessionStorage.getItem('clientID') || null;
    }
    if (!clientID) return [];

    // Updated to use RESTful endpoint
    const res = await authedFetch(`/admin/products?clientID=${encodeURIComponent(clientID)}`, {
      method: 'GET',
      headers: { 'X-Client-Id': clientID }
    });

    if (res.ok && !res.body?.error) {
      offersAvailableProducts = res.body.products || [];
      return offersAvailableProducts;
    } else {
      console.error('Failed to load products:', res);
      relayOfferMessage(res.body?.error || `HTTP ${res.status}`, 'error');
    }
  } catch (err) {
    console.error('Failed to load products:', err);
    relayOfferMessage(`Failed to load products: ${err.message}`, 'error');
  }
  return [];
}

// ======== VISUAL PRODUCT SELECTOR MODAL ========

function openProductSelector(offerElement) {
    // Create modal if it doesn't exist
    let modal = document.getElementById('productSelectorModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'productSelectorModal';
        modal.className = 'modal-overlay';
        modal.innerHTML = `
            <div class="modal-content" style="max-width: 800px; max-height: 85vh;">
                <div class="modal-header">
                    Select Products for Offer
                    <button class="modal-close-btn" onclick="closeProductSelector()">&times;</button>
                </div>
                <div class="modal-body">
                    <div class="product-selector-search">
                        <input type="text" id="productSearchInput" placeholder="Search products..." 
                               onkeyup="filterProductSelector()">
                    </div>
                    <div id="productSelectorGrid" class="product-selector-grid">
                        <div class="loading-spinner"></div>
                    </div>
                    <div class="selected-products-summary">
                        <strong>Selected:</strong> <span id="selectedProductsCount">0</span> products
                    </div>
                </div>
                <div class="modal-actions">
                    <button onclick="closeProductSelector()" class="secondary">Cancel</button>
                    <button onclick="applyProductSelection()">Apply Selection</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }
    
    // Store reference to the offer element
    modal.dataset.targetOffer = offerElement.dataset.offerKey;
    
    // Get current product IDs (preserve order)
    const offerKey = offerElement.dataset.offerKey;
    const currentProductIds = offerProductOrder.get(offerKey) || 
        offerElement.querySelector('.offer-products').value
            .split(',')
            .map(id => id.trim())
            .filter(id => id);
    
    // Show modal
    modal.classList.add('active');
    document.body.classList.add('modal-open');
    
    // Load and display products
    renderProductSelectorGrid(currentProductIds);
}

// ======== RENDER PRODUCT SELECTOR GRID ========

async function renderProductSelectorGrid(selectedProductIds = []) {
    const grid = document.getElementById('productSelectorGrid');
    
    // Load products if not cached
    if (offersAvailableProducts.length === 0) {
        grid.innerHTML = '<div class="loading-spinner"></div>';
        await loadAvailableProducts();
    }
    
    if (offersAvailableProducts.length === 0) {
        grid.innerHTML = '<div class="no-products">No products available. Please create products first.</div>';
        return;
    }
    
    // Render product grid with tooltips for full product names
    grid.innerHTML = offersAvailableProducts.map(product => {
        const isSelected = selectedProductIds.includes(product.id);
        const price = product.lowest_price ? `$${(product.lowest_price / 100).toFixed(2)}` : 'No price';
        const imageUrl = product.images && product.images.length > 0 ? product.images[0] : '';
        const isActive = product.active !== false;
        
        return `
            <div class="product-selector-item ${isSelected ? 'selected' : ''} ${!isActive ? 'inactive' : ''}" 
                 data-product-id="${product.id}"
                 data-product-name="${escapeHtml(product.name.toLowerCase())}"
                 title="${escapeHtml(product.name)} - ${price}"
                 onclick="toggleProductSelection('${product.id}')">
                ${imageUrl ? 
                    `<img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(product.name)}">` : 
                    '<div class="no-image"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg></div>'
                }
                <div class="product-info">
                    <div class="product-name">${escapeHtml(product.name)}</div>
                    <div class="product-price">${price}</div>
                    ${!isActive ? '<div class="product-status">Inactive</div>' : ''}
                </div>
                ${isSelected ? '<div class="selected-badge">✓ Selected</div>' : ''}
            </div>
        `;
    }).join('');
    
    updateSelectedCount();
}

// ======== TOGGLE PRODUCT SELECTION ========

function toggleProductSelection(productId) {
    const item = document.querySelector(`[data-product-id="${productId}"]`);
    if (!item) return;
    
    item.classList.toggle('selected');
    
    // Update badge
    const badge = item.querySelector('.selected-badge');
    if (item.classList.contains('selected')) {
        if (!badge) {
            const newBadge = document.createElement('div');
            newBadge.className = 'selected-badge';
            newBadge.textContent = '✓ Selected';
            item.appendChild(newBadge);
        }
    } else {
        if (badge) badge.remove();
    }
    
    updateSelectedCount();
}

// ======== UPDATE SELECTED COUNT ========

function updateSelectedCount() {
    const selectedItems = document.querySelectorAll('.product-selector-item.selected');
    const countSpan = document.getElementById('selectedProductsCount');
    if (countSpan) {
        countSpan.textContent = selectedItems.length;
    }
}

// ======== FILTER PRODUCTS IN SELECTOR ========

function filterProductSelector() {
    const searchInput = document.getElementById('productSearchInput');
    const searchTerm = searchInput.value.toLowerCase();
    const items = document.querySelectorAll('.product-selector-item');
    
    items.forEach(item => {
        const productName = item.dataset.productName || '';
        const productId = item.dataset.productId || '';
        const matches = productName.includes(searchTerm) || productId.toLowerCase().includes(searchTerm);
        item.style.display = matches ? '' : 'none';
    });
}

// ======== APPLY PRODUCT SELECTION WITH ORDERING ========

function applyProductSelection() {
    const modal = document.getElementById('productSelectorModal');
    const offerKey = modal.dataset.targetOffer;
    const offerElement = document.querySelector(`[data-offer-key="${offerKey}"]`);
    
    if (!offerElement) {
        closeProductSelector();
        return;
    }
    
    // Get selected product IDs
    const selectedItems = document.querySelectorAll('.product-selector-item.selected');
    const selectedProductIds = Array.from(selectedItems).map(item => item.dataset.productId);
    
    // Initialize or get existing order
    if (!offerProductOrder.has(offerKey)) {
        offerProductOrder.set(offerKey, []);
    }
    
    const existingOrder = offerProductOrder.get(offerKey);
    const newOrder = [];
    
    // Preserve existing order for products that are still selected
    existingOrder.forEach(id => {
        if (selectedProductIds.includes(id)) {
            newOrder.push(id);
        }
    });
    
    // Add newly selected products at the end
    selectedProductIds.forEach(id => {
        if (!newOrder.includes(id)) {
            newOrder.push(id);
        }
    });
    
    // Update order
    offerProductOrder.set(offerKey, newOrder);
    
    // Update the products input field
    const productsInput = offerElement.querySelector('.offer-products');
    productsInput.value = newOrder.join(', ');
    
    // Update product display
    updateOfferProductDisplay(offerElement, newOrder);
    
    closeProductSelector();
    setMsg('offersMsg', `Selected ${newOrder.length} product(s) for offer. Use ↑↓ to reorder.`, 'success');
}

// ======== UPDATE OFFER PRODUCT DISPLAY WITH ORDERING ========

function updateOfferProductDisplay(offerElement, productIds) {
    const offerKey = offerElement.dataset.offerKey;
    
    // Initialize order if not exists
    if (!offerProductOrder.has(offerKey)) {
        offerProductOrder.set(offerKey, [...productIds]);
    }
    
    // Get ordered product IDs
    const orderedIds = offerProductOrder.get(offerKey);
    
    // Find or create product display area
    let displayArea = offerElement.querySelector('.offer-products-display');
    if (!displayArea) {
        displayArea = document.createElement('div');
        displayArea.className = 'offer-products-display';
        const productsInput = offerElement.querySelector('.offer-products');
        productsInput.parentNode.insertBefore(displayArea, productsInput.nextSibling);
    }
    
    if (orderedIds.length === 0) {
        displayArea.innerHTML = '<div class="no-products-selected">No products selected</div>';
        return;
    }
    
    // Display selected products with order badges and controls
    const selectedProducts = orderedIds
        .map(id => offersAvailableProducts.find(p => p.id === id))
        .filter(p => p); // Remove any null/undefined
    
    displayArea.innerHTML = `
        <div class="selected-products-ordered">
            ${selectedProducts.map((p, index) => {
                const imageUrl = p.images && p.images[0] ? p.images[0] : '';
                const price = p.lowest_price ? `$${(p.lowest_price / 100).toFixed(2)}` : 'No price';
                return `
                    <div class="product-ordered-item" data-product-id="${p.id}" data-order="${index + 1}">
                        <div class="product-order-badge">${index + 1}</div>
                        <div class="product-ordered-content">
                            ${imageUrl ? 
                                `<img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(p.name)}">` :
                                '<div class="no-image-mini">No Image</div>'
                            }
                            <div class="product-ordered-info">
                                <span class="product-ordered-name" title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</span>
                                <span class="product-ordered-price">${price}</span>
                            </div>
                        </div>
                        <div class="product-order-controls">
                            <button type="button" class="order-btn order-up" 
                                    onclick="moveProductUp('${offerKey}', '${p.id}')"
                                    ${index === 0 ? 'disabled' : ''}
                                    title="Move up">
                                ↑
                            </button>
                            <button type="button" class="order-btn order-down" 
                                    onclick="moveProductDown('${offerKey}', '${p.id}')"
                                    ${index === selectedProducts.length - 1 ? 'disabled' : ''}
                                    title="Move down">
                                ↓
                            </button>
                            <button type="button" class="order-btn order-remove" 
                                    onclick="removeProductFromOffer('${offerKey}', '${p.id}')"
                                    title="Remove">
                                ✕
                            </button>
                        </div>
                    </div>
                `;
            }).join('')}
        </div>
        <div class="product-order-hint">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"></circle>
                <path d="M12 16v-4"></path>
                <path d="M12 8h.01"></path>
            </svg>
            Products are shown in order on landing pages. Use ↑↓ to reorder.
        </div>
    `;
    
    // Update the hidden input with ordered IDs
    const productsInput = offerElement.querySelector('.offer-products');
    productsInput.value = orderedIds.join(', ');
}

// ======== MOVE PRODUCT UP ========

function moveProductUp(offerKey, productId) {
    const order = offerProductOrder.get(offerKey);
    if (!order) return;
    
    const index = order.indexOf(productId);
    if (index <= 0) return; // Already at top or not found
    
    // Swap with previous
    [order[index - 1], order[index]] = [order[index], order[index - 1]];
    
    // Update display
    const offerElement = document.querySelector(`[data-offer-key="${offerKey}"]`);
    if (offerElement) {
        updateOfferProductDisplay(offerElement, order);
        setMsg('offersMsg', 'Product order updated. Remember to click "Save Offers" to persist changes.', 'info');
    }
}

// ======== MOVE PRODUCT DOWN ========

function moveProductDown(offerKey, productId) {
    const order = offerProductOrder.get(offerKey);
    if (!order) return;
    
    const index = order.indexOf(productId);
    if (index === -1 || index >= order.length - 1) return; // Already at bottom or not found
    
    // Swap with next
    [order[index], order[index + 1]] = [order[index + 1], order[index]];
    
    // Update display
    const offerElement = document.querySelector(`[data-offer-key="${offerKey}"]`);
    if (offerElement) {
        updateOfferProductDisplay(offerElement, order);
        setMsg('offersMsg', 'Product order updated. Remember to click "Save Offers" to persist changes.', 'info');
    }
}

// ======== REMOVE PRODUCT FROM OFFER ========

function removeProductFromOffer(offerKey, productId) {
    const order = offerProductOrder.get(offerKey);
    if (!order) return;
    
    const index = order.indexOf(productId);
    if (index === -1) return;
    
    // Remove from order
    order.splice(index, 1);
    
    // Update display
    const offerElement = document.querySelector(`[data-offer-key="${offerKey}"]`);
    if (offerElement) {
        updateOfferProductDisplay(offerElement, order);
        
        // Also deselect in modal if it's open
        const modal = document.getElementById('productSelectorModal');
        if (modal && modal.classList.contains('active')) {
            const item = modal.querySelector(`[data-product-id="${productId}"]`);
            if (item) {
                item.classList.remove('selected');
                const badge = item.querySelector('.selected-badge');
                if (badge) badge.remove();
                updateSelectedCount();
            }
        }
        
        setMsg('offersMsg', 'Product removed. Remember to click "Save Offers" to persist changes.', 'info');
    }
}

// ======== CLOSE PRODUCT SELECTOR ========

function closeProductSelector() {
    const modal = document.getElementById('productSelectorModal');
    if (modal) {
        modal.classList.remove('active');
        document.body.classList.remove('modal-open');
    }
}

// ======== CREATE OFFER ELEMENT (ENHANCED) ========

function createOfferElement(key, offer) {
    const div = document.createElement('div');
    div.className = 'offer-item';
    div.dataset.offerKey = key;
    
    // Initialize product order
    if (offer.product_ids && offer.product_ids.length > 0) {
        offerProductOrder.set(key, [...offer.product_ids]);
    }
    
    // Format full URL
    const fullUrl = offer.full_url || 
                    (currentOfferData.base_url ? `${currentOfferData.base_url}${offer.path || key}/` : '');
    
    div.innerHTML = `
        <div class="offer-header">
            <div class="offer-key">${escapeHtml(key)}</div>
            <button class="small danger" onclick="removeOffer('${escapeHtml(key)}')">Remove</button>
        </div>
        <label>Path</label>
        <input class="offer-path mono" value="${escapeHtml(offer.path || key)}" placeholder="e.g., summer-sale" />
        
        <label>Products</label>
        <button class="small secondary select-products-btn" onclick="openProductSelector(this.closest('.offer-item'))">
            Select Products Visually
        </button>
        <div class="offer-products-display"></div>
        <input class="offer-products mono" value="${escapeHtml((offer.product_ids || []).join(', '))}" 
               placeholder="Product IDs will appear here" style="margin-top: 8px;" />
        
        <div class="row mt8">
            <label style="margin:0; display: flex; align-items: center; gap: 8px;">
                <input type="checkbox" class="offer-active" ${offer.active ? 'checked' : ''} style="width:auto" />
                Active
            </label>
        </div>
        ${fullUrl ? `
            <div class="muted mt8" style="font-size:11px; word-break: break-all;">
                <strong>URL:</strong> <a href="${escapeHtml(fullUrl)}" target="_blank" rel="noopener">${escapeHtml(fullUrl)}</a>
            </div>
        ` : ''}
    `;
    
    // Load product display if products exist
    if (offer.product_ids && offer.product_ids.length > 0) {
        setTimeout(() => updateOfferProductDisplay(div, offer.product_ids), 100);
    }
    
    return div;
}

// ======== ADD OFFER TO DOM ========

function addOfferToDOM(key = '', offer = {}) {
    const container = document.getElementById('offersContainer');
    
    // Remove "no offers" message if it exists
    const noOffersMsg = container.querySelector('.no-orders');
    if (noOffersMsg) {
        noOffersMsg.remove();
    }
    
    const offerElement = createOfferElement(key, offer);
    container.appendChild(offerElement);
    
    // If this is a new offer (no key), make the key editable
    if (!key) {
        const keySpan = offerElement.querySelector('.offer-key');
        keySpan.contentEditable = true;
        keySpan.classList.add('editable');
        keySpan.focus();
        
        // Add placeholder text
        if (!keySpan.textContent.trim()) {
            keySpan.textContent = 'new-offer';
            keySpan.style.color = '#999';
        }
        
        // Clear placeholder on focus
        keySpan.addEventListener('focus', function() {
            if (this.textContent === 'new-offer') {
                this.textContent = '';
                this.style.color = '';
            }
        });
        
        // Restore placeholder if empty on blur
        keySpan.addEventListener('blur', function() {
            if (!this.textContent.trim()) {
                this.textContent = 'new-offer';
                this.style.color = '#999';
            }
        });
    }
}

// ======== REMOVE OFFER ========

function removeOffer(key) {
    if (!confirm(`Remove offer "${key}"? This will not delete it from the server until you click Save.`)) {
        return;
    }
    
    const element = document.querySelector(`[data-offer-key="${key}"]`);
    if (element) {
        element.remove();
        // Clear from order tracking
        offerProductOrder.delete(key);
        setMsg('offersMsg', `Offer "${key}" removed. Click "Save Offers" to persist changes.`, 'info');
        
        // Show "no offers" message if container is empty
        const container = document.getElementById('offersContainer');
        if (container && container.children.length === 0) {
            container.innerHTML = '<div class="no-orders">No offers configured. Click "+ Add Offer" to create one.</div>';
        }
    }
}

// ======== ADD NEW OFFER ========

function handleAddOffer() {
    const container = document.getElementById('offersContainer');
    
    // Check if there's already an unsaved new offer
    const existingNew = container.querySelector('.offer-key.editable');
    if (existingNew) {
        existingNew.focus();
        setMsg('offersMsg', 'Please complete the existing offer first', 'error');
        return;
    }
    
    // Add new blank offer
    addOfferToDOM('', { 
        path: '', 
        product_ids: [], 
        active: true 
    });
    
    setMsg('offersMsg', 'New offer added. Enter a name and click "Save Offers" to persist.', 'info');
}

// ======== REFRESH/RELOAD OFFERS ========

async function handleRefreshOffers() {
    const clientID = window.currentClientID ? window.currentClientID() : null;
    if (!clientID) {
        setMsg('offersMsg', 'No client ID available', 'error');
        return;
    }
    
    currentOfferData = {}; // Clear cache to force reload
    offersAvailableProducts = []; // Clear products cache
    offerProductOrder.clear(); // Clear order tracking
    await loadOffers(clientID);
}

// ======== SAVE OFFERS ========

async function handleSaveOffers() {
    try {
        const clientID = window.currentClientID ? window.currentClientID() : null;
        if (!clientID) {
            return setMsg('offersMsg', 'Client ID is required. Please sign in.', 'error');
        }
        
        // Check if baseFrontendUrl field exists (it might not be in the DOM)
        const baseFrontendUrlField = document.getElementById('baseFrontendUrl');
        const baseFrontendUrl = baseFrontendUrlField ? baseFrontendUrlField.value.trim() : '';
        
        const offerElements = document.querySelectorAll('.offer-item');
        if (offerElements.length === 0) {
            return setMsg('offersMsg', 'No offers to save', 'error');
        }
        
        setMsg('offersMsg', 'Saving offers...', 'info');
        
        // Collect all offers into a single object
        const offersObject = {};
        
        for (const element of offerElements) {
            const keyElement = element.querySelector('.offer-key');
            let offerName = keyElement.textContent.trim();
            
            // Skip if offer name is placeholder or empty
            if (!offerName || offerName === 'new-offer') {
                setMsg('offersMsg', 'Please provide a name for all offers', 'error');
                keyElement.focus();
                return;
            }
            
            // Store original name for user feedback
            const originalName = offerName;
            
            // Sanitize offer name (replace spaces and special chars with hyphens)
            offerName = offerName.toLowerCase()
                .trim()
                .replace(/\s+/g, '-')  // Replace spaces with hyphens
                .replace(/[^a-z0-9-_]/g, '-')  // Replace other special chars
                .replace(/-+/g, '-')  // Replace multiple hyphens with single
                .replace(/^-|-$/g, '');  // Remove leading/trailing hyphens
            
            // Update the display if name was changed
            if (originalName !== offerName && !keyElement.contentEditable) {
                keyElement.textContent = offerName;
                keyElement.setAttribute('data-original-name', originalName);
                keyElement.title = `Original: "${originalName}"`;
                
                // Show feedback about the name change
                setMsg('offersMsg', `Note: Offer name "${originalName}" was sanitized to "${offerName}"`, 'info');
            }
            
            const path = element.querySelector('.offer-path').value.trim() || offerName;
            
            // Get ordered product IDs
            const offerKey = element.dataset.offerKey;
            const orderedProductIds = offerProductOrder.get(offerKey) || 
                element.querySelector('.offer-products').value.trim().split(',').map(id => id.trim()).filter(id => id);
            
            const active = element.querySelector('.offer-active').checked;
            
            // Add offer to the collection
            offersObject[offerName] = {
                path: path,
                product_ids: orderedProductIds,
                active: active
            };
            
            // Include base_frontend_url in the first offer if it exists
            if (Object.keys(offersObject).length === 1 && baseFrontendUrl) {
                offersObject[offerName].base_frontend_url = baseFrontendUrl;
            }
        }
        
        try {
            // Updated to use RESTful endpoint - send all offers in one request
            const res = await authedFetch(`/admin/offers`, { 
                method: 'PUT',
                headers: { 'X-Client-Id': clientID },
                body: {
                    clientID: clientID,
                    offers: offersObject
                }
            });
            
            if (!res.ok) {
                const errorMsg = res.body?.error || `HTTP ${res.status}`;
                setMsg('offersMsg', `Failed to save offers: ${errorMsg}`, 'error');
                console.error('Save offers error:', res);
            } else {
                setMsg('offersMsg', `✓ Successfully saved ${Object.keys(offersObject).length} offer(s)!`, 'success');
                
                // Reload offers to show updated data
                setTimeout(() => {
                    currentOfferData = {}; // Clear cache
                    offersAvailableProducts = []; // Clear products cache
                    loadOffers(clientID);
                }, 1000);
            }
            
        } catch (fetchError) {
            setMsg('offersMsg', `Network error saving offers: ${fetchError.message}`, 'error');
            console.error('Network error:', fetchError);
        }
        
    } catch (error) {
        console.error('Save offers error:', error);
        setMsg('offersMsg', `Error: ${error.message}`, 'error');
    }
}

// ======== EVENT LISTENERS ========

function setupOffersListeners() {
    const btnAddOffer = document.getElementById('btnAddOffer');
    const btnRefreshOffers = document.getElementById('btnRefreshOffers');
    const btnSaveOffers = document.getElementById('btnSaveOffers');
    
    if (btnAddOffer) {
        btnAddOffer.addEventListener('click', handleAddOffer);
    }
    
    if (btnRefreshOffers) {
        btnRefreshOffers.addEventListener('click', handleRefreshOffers);
    }
    
    if (btnSaveOffers) {
        btnSaveOffers.addEventListener('click', handleSaveOffers);
    }
    
    console.log('Offers listeners setup complete');
}

// ======== CSS STYLES ========

const offerSelectorStyles = `
/* ===== Product Ordering in Offers ===== */

.selected-products-ordered {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 12px;
    background: #0b1426;
    border: 1px solid #2a3f5f;
    border-radius: 6px;
    margin-bottom: 8px;
}

.product-ordered-item {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px;
    background: #0f1a2e;
    border: 1px solid #2a3f5f;
    border-radius: 6px;
    transition: all 0.2s;
}

.product-ordered-item:hover {
    border-color: #4a7ba7;
    background: rgba(74, 123, 167, 0.05);
}

.product-order-badge {
    flex-shrink: 0;
    width: 32px;
    height: 32px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--brand);
    color: #0b1220;
    font-weight: 700;
    font-size: 0.875rem;
    border-radius: 50%;
}

.product-ordered-content {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0; /* Enable text truncation */
}

.product-ordered-content img {
    width: 48px;
    height: 48px;
    object-fit: cover;
    border-radius: 4px;
    flex-shrink: 0;
}

.product-ordered-content .no-image-mini {
    width: 48px;
    height: 48px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #1a2742;
    border-radius: 4px;
    color: #6b7280;
    font-size: 0.625rem;
    flex-shrink: 0;
}

.product-ordered-info {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0; /* Enable text truncation */
}

.product-ordered-name {
    font-weight: 600;
    color: #e8eefc;
    font-size: 0.875rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.product-ordered-price {
    font-size: 0.75rem;
    color: var(--accent);
    font-weight: 600;
}

.product-order-controls {
    display: flex;
    gap: 4px;
    flex-shrink: 0;
}

.order-btn {
    width: 28px;
    height: 28px;
    padding: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #1a2742;
    border: 1px solid #2a3f5f;
    border-radius: 4px;
    color: #e8eefc;
    cursor: pointer;
    font-size: 0.875rem;
    transition: all 0.2s;
}

.order-btn:hover:not(:disabled) {
    background: #2a3f5f;
    border-color: var(--brand);
    color: var(--brand);
}

.order-btn:disabled {
    opacity: 0.3;
    cursor: not-allowed;
}

.order-btn.order-remove {
    color: var(--danger);
}

.order-btn.order-remove:hover:not(:disabled) {
    background: rgba(239, 68, 68, 0.1);
    border-color: var(--danger);
}

.product-order-hint {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    background: rgba(106, 168, 255, 0.1);
    border: 1px solid rgba(106, 168, 255, 0.3);
    border-radius: 4px;
    color: #9ca3af;
    font-size: 0.75rem;
    line-height: 1.4;
}

.product-order-hint svg {
    flex-shrink: 0;
    color: var(--brand);
}

/* Responsive adjustments */
@media (max-width: 640px) {
    .product-ordered-item {
        flex-wrap: wrap;
    }
    
    .product-order-controls {
        width: 100%;
        justify-content: flex-end;
    }
    
    .product-ordered-name {
        white-space: normal;
        overflow: visible;
    }
}

/* ===== Landing Page Editor Enhancements ===== */

/* Collapsible form panel for better preview */
.landing-page-editor {
    transition: grid-template-columns 0.3s ease;
}

.landing-page-editor.preview-only {
    grid-template-columns: 0 1fr;
}

.landing-page-editor.preview-only .editor-form {
    opacity: 0;
    pointer-events: none;
    overflow: hidden;
    width: 0;
    min-width: 0;
}

.preview-toggle-btn {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    background: var(--brand);
    border: none;
    border-radius: 6px;
    color: #0b1220;
    font-weight: 600;
    font-size: 0.875rem;
    cursor: pointer;
    transition: all 0.2s;
}

.preview-toggle-btn:hover {
    background: #5a98f0;
    transform: translateY(-1px);
}

.preview-toggle-btn svg {
    flex-shrink: 0;
}

/* Update preview header to include toggle button */
.preview-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 16px;
    background: #0f1a2e;
    border: 1px solid #2a3f5f;
    border-radius: 8px 8px 0 0;
    gap: 12px;
}

.preview-header-left {
    display: flex;
    align-items: center;
    gap: 12px;
}

.preview-header h3 {
    margin: 0;
    font-size: 1rem;
    color: #e8eefc;
}

/* Responsive adjustments for preview toggle */
@media (max-width: 1200px) {
    .landing-page-editor.preview-only {
        grid-template-columns: 1fr;
    }
    
    .preview-toggle-btn {
        font-size: 0.75rem;
        padding: 4px 8px;
    }
    
    .preview-toggle-btn span {
        display: none;
    }
}
    
/* Product Selector Modal Styles */
.product-selector-search {
    margin-bottom: 16px;
}

.product-selector-search input {
    width: 100%;
    padding: 10px 12px;
    border: 1px solid #2a3f5f;
    border-radius: 8px;
    background: #0b1426;
    color: #e8eefc;
    font-size: 14px;
}

.product-selector-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 12px;
    max-height: 450px;
    overflow-y: auto;
    padding: 8px;
    border: 1px solid #2a3f5f;
    border-radius: 8px;
    background: #0b1426;
}

.product-selector-item {
    position: relative;
    border: 2px solid #2a3f5f;
    border-radius: 8px;
    padding: 8px;
    cursor: pointer;
    transition: all 0.2s;
    background: #0f1a2e;
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
}

/* Enhanced tooltip for product selector items */
.product-selector-item[title]:hover::after {
    content: attr(title);
    position: absolute;
    bottom: 100%;
    left: 50%;
    transform: translateX(-50%);
    padding: 8px 12px;
    background: rgba(0, 0, 0, 0.9);
    color: #fff;
    font-size: 0.875rem;
    border-radius: 6px;
    white-space: nowrap;
    z-index: 1000;
    margin-bottom: 8px;
    pointer-events: none;
    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
}

.product-selector-item[title]:hover::before {
    content: '';
    position: absolute;
    bottom: 100%;
    left: 50%;
    transform: translateX(-50%);
    border-left: 6px solid transparent;
    border-right: 6px solid transparent;
    border-top: 6px solid rgba(0, 0, 0, 0.9);
    margin-bottom: 2px;
    z-index: 1001;
}

.product-selector-item:hover {
    border-color: #4a7ba7;
    transform: translateY(-2px);
    box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
}

.product-selector-item.selected {
    border-color: var(--accent);
    background: rgba(34, 197, 94, 0.1);
}

.product-selector-item.inactive {
    opacity: 0.6;
}

.product-selector-item img {
    width: 100%;
    height: 100px;
    object-fit: cover;
    border-radius: 6px;
    margin-bottom: 8px;
}

.product-selector-item .no-image {
    width: 100%;
    height: 100px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #1a2742;
    border-radius: 6px;
    color: #6b7280;
    margin-bottom: 8px;
}

.product-selector-item .product-info {
    width: 100%;
}

.product-selector-item .product-name {
    font-weight: 600;
    font-size: 0.875rem;
    color: #e8eefc;
    margin-bottom: 4px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.product-selector-item .product-price {
    font-size: 1rem;
    font-weight: 700;
    color: var(--accent);
}

.product-selector-item .product-status {
    font-size: 0.75rem;
    color: #ff6b6b;
    margin-top: 4px;
}

.selected-badge {
    position: absolute;
    top: 4px;
    right: 4px;
    background: var(--accent);
    color: white;
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 600;
}

.selected-products-summary {
    margin-top: 16px;
    padding: 12px;
    background: #0b1426;
    border: 1px solid #2a3f5f;
    border-radius: 8px;
}

/* Mini Product Display in Offers */
.offer-products-display {
    margin-top: 8px;
    margin-bottom: 8px;
}

.selected-products-mini {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 8px;
    background: #0b1426;
    border: 1px solid #2a3f5f;
    border-radius: 6px;
}

.product-mini {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 8px;
    background: #0f1a2e;
    border: 1px solid #2a3f5f;
    border-radius: 4px;
    font-size: 0.875rem;
    cursor: help;
    transition: all 0.2s;
}

.product-mini:hover {
    border-color: #4a7ba7;
    background: rgba(74, 123, 167, 0.1);
}

.product-mini img {
    width: 24px;
    height: 24px;
    object-fit: cover;
    border-radius: 3px;
}

.product-mini .no-image-mini {
    width: 24px;
    height: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #1a2742;
    border-radius: 3px;
    color: #6b7280;
    font-size: 0.6rem;
}

.no-products-selected {
    padding: 12px;
    text-align: center;
    color: #6b7280;
    font-style: italic;
}

.select-products-btn {
    margin-top: 4px;
}

/* Offer item enhancements */
.offer-item {
    background: var(--card-bg);
    border: 1px solid #2a3f5f;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
}

.offer-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
    padding-bottom: 12px;
    border-bottom: 1px solid #2a3f5f;
}

.offer-key {
    font-size: 1.1rem;
    font-weight: 600;
    color: #e8eefc;
    position: relative;
}

.offer-key[data-original-name]::after {
    content: ' (sanitized)';
    font-size: 0.75rem;
    color: #9ca3af;
    font-weight: normal;
}

.offer-key.editable {
    padding: 4px 8px;
    border: 1px solid var(--brand);
    border-radius: 4px;
    background: rgba(255, 131, 9, 0.1);
}
`;

// ======== INITIALIZATION ========

// Inject styles
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        const style = document.createElement('style');
        style.textContent = offerSelectorStyles;
        document.head.appendChild(style);
    });
} else {
    const style = document.createElement('style');
    style.textContent = offerSelectorStyles;
    document.head.appendChild(style);
}

// Auto-load when switching to offers tab
document.addEventListener('DOMContentLoaded', () => {
    const offersTab = document.querySelector('[data-tab="offers"]');
    if (offersTab) {
        offersTab.addEventListener('click', () => {
            // Small delay to ensure tab is active
            setTimeout(() => {
                initOffersTab();
                // Pre-load products when tab is opened
                if (offersAvailableProducts.length === 0) {
                    loadAvailableProducts();
                }
            }, 100);
        });
    }
});

// Export functions (for functions outside the main IIFE)
window.setupOffersListeners = setupOffersListeners;
window.initOffersTab = initOffersTab;

// Setup listeners if DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupOffersListeners);
} else {
    setupOffersListeners();
}
