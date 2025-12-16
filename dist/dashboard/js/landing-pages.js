// landing-pages.js - Enhanced with Offer Integration

let currentLandingPages = [];
let currentLandingPageId = null;
let editingLandingPage = false;
let previewDebounceTimer = null;
let availableProducts = [];
let availableOffers = []; // NEW: Store available offers
let _landingPagesInitDone = false;

// Initialize landing pages tab
function initLandingPages() {
    if (_landingPagesInitDone) return;
    
    const btnLoadLandingPages = document.getElementById('btnLoadLandingPages');
    if (!btnLoadLandingPages) {
        console.warn('Landing pages elements not ready yet, skipping initialization');
        return;
    }
    
    btnLoadLandingPages.addEventListener('click', loadLandingPages);
    
    const btnNew = document.getElementById('btnNewLandingPage');
    if (btnNew) btnNew.addEventListener('click', () => showLandingPageForm());
    
    const btnSave = document.getElementById('btnSaveLandingPage');
    if (btnSave) btnSave.addEventListener('click', saveLandingPage);
    
    const btnPublish = document.getElementById('btnPublishLandingPage');
    if (btnPublish) btnPublish.addEventListener('click', publishLandingPage);
    
    const btnCancel = document.getElementById('btnCancelLandingPage');
    if (btnCancel) btnCancel.addEventListener('click', hideLandingPageForm);
    
    const templateType = document.getElementById('templateType');
    if (templateType) templateType.addEventListener('change', onTemplateTypeChange);
    
    const btnAddProduct = document.getElementById('btnAddLandingProduct');
    if (btnAddProduct) btnAddProduct.addEventListener('click', addProductRow);
    
    // NEW: Add offer selector listener
    const offerSelector = document.getElementById('landingPageOfferSelector');
    if (offerSelector) offerSelector.addEventListener('change', onOfferSelected);
    
    // NEW: Add source mode toggle listener
    const sourceMode = document.getElementById('productSourceMode');
    if (sourceMode) sourceMode.addEventListener('change', onSourceModeChange);
    
    setupPreviewListeners();
    
    const btnCreateABTest = document.getElementById('btnCreateABTest');
    if (btnCreateABTest) btnCreateABTest.addEventListener('click', showABTestDialog);
    
    // Setup toggle button for form panel
    setupToggleButton();
    
    _landingPagesInitDone = true;
    console.log('Landing pages module initialized');
}

// NEW: Load available offers
async function loadAvailableOffers() {
    try {
        const clientID = getClientID();
        if (!clientID) return [];
        
        const session = await getSession();
        const token = session.getIdToken().getJwtToken();
        
        // Updated to use RESTful endpoint
        const response = await fetch(`${window.CONFIG.apiBase}/admin/offers?clientID=${encodeURIComponent(clientID)}`, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            }
        });
        
        const data = await response.json();
        
        if (!data.error && data.offers) {
            availableOffers = Object.entries(data.offers).map(([key, offer]) => ({
                id: key,
                name: key,
                path: offer.path,
                product_ids: offer.product_ids || [],
                active: offer.active
            }));
            return availableOffers;
        }
    } catch (err) {
        console.error('Failed to load offers:', err);
    }
    return [];
}

// NEW: Handle source mode change (offer vs manual)
function onSourceModeChange() {
    const sourceMode = document.getElementById('productSourceMode').value;
    const offerSection = document.getElementById('offerSelectionSection');
    const manualSection = document.getElementById('manualProductSection');
    
    if (sourceMode === 'offer') {
        offerSection.classList.remove('hidden');
        manualSection.classList.add('hidden');
        
        // Load offers if not already loaded
        if (availableOffers.length === 0) {
            loadAvailableOffers().then(populateOfferSelector);
        } else {
            populateOfferSelector();
        }
    } else {
        offerSection.classList.add('hidden');
        manualSection.classList.remove('hidden');
    }
}

// NEW: Populate offer selector dropdown
function populateOfferSelector() {
    const selector = document.getElementById('landingPageOfferSelector');
    if (!selector) return;
    
    selector.innerHTML = '<option value="">-- Select an Offer --</option>';
    
    availableOffers
        .filter(offer => offer.active !== false)
        .forEach(offer => {
            const option = document.createElement('option');
            option.value = offer.id;
            option.textContent = `${offer.name} (${offer.product_ids.length} products)`;
            selector.appendChild(option);
        });
}

// NEW: Handle offer selection
async function onOfferSelected() {
    const offerSelector = document.getElementById('landingPageOfferSelector');
    const offerId = offerSelector.value;
    
    if (!offerId) {
        // Clear products list
        document.getElementById('landingProductsList').innerHTML = '';
        updatePreview();
        return;
    }
    
    const selectedOffer = availableOffers.find(o => o.id === offerId);
    if (!selectedOffer) return;
    
    showLandingPageMsg(`Loading products from offer "${selectedOffer.name}"...`, 'muted');
    
    // Load products if not already loaded
    if (availableProducts.length === 0) {
        await loadAvailableProducts();
    }
    
    // Clear existing products list
    const container = document.getElementById('landingProductsList');
    container.innerHTML = '';
    
    // Add each product from the offer
    selectedOffer.product_ids.forEach((productId, index) => {
        const product = availableProducts.find(p => p.id === productId);
        if (product) {
            const productData = {
                product_id: productId,
                price_id: product.prices && product.prices.length > 0 ? product.prices[0].id : null,
                tier_label: '', // User can customize
                display_order: index + 1,
                is_featured: index === Math.floor(selectedOffer.product_ids.length / 2), // Feature middle product
                custom_description_override: null
            };
            addProductRow(productData);
        }
    });
    
    showLandingPageMsg(`Loaded ${selectedOffer.product_ids.length} products from offer`, 'ok');
    updatePreview();
}

// Load all landing pages for current tenant
async function loadLandingPages() {
    const clientID = getClientID();
    if (!clientID) {
        showLandingPageMsg('Please set your clientID in the Stripe Keys tab first', 'err');
        return;
    }
    
    const btn = document.getElementById('btnLoadLandingPages');
    const res = startSpin(btn, 'Loading...');
    showLandingPageMsg('Loading landing pages...');
    
    try {
        const session = await getSession();
        const token = session.getIdToken().getJwtToken();
        
        const response = await fetch(`${window.CONFIG.apiBase}/admin/landing-pages`, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`,
                'X-Client-Id': clientID
            }
        });
        
        const data = await response.json();
        
        if (data.error) {
            stopSpin(res);
            showLandingPageMsg(`Error: ${data.error}`, 'err');
            return;
        }
        
        currentLandingPages = data.landing_pages || [];
        renderLandingPagesList();
        showLandingPageMsg(`Loaded ${currentLandingPages.length} landing pages`, 'ok');
    } catch (err) {
        stopSpin(res);
        console.error('Load landing pages error:', err);
        showLandingPageMsg(`Failed to load landing pages: ${err.message}`, 'err');
    } finally {
        stopSpin(res);
    }
}

// Render landing pages list
function renderLandingPagesList() {
    const container = document.getElementById('landingPagesListContainer');
    
    if (currentLandingPages.length === 0) {
        container.innerHTML = '<div class="no-orders">No landing pages found. Create your first landing page!</div>';
        return;
    }
    
    container.innerHTML = currentLandingPages.map(page => {
        const statusBadge = page.status === 'published' 
            ? '<span class="order-badge badge-success">Published</span>'
            : '<span class="order-badge badge-warning">Draft</span>';
        
        const abTestBadge = page.ab_test_id 
            ? '<span class="order-badge badge-info">A/B Testing</span>'
            : '';
        
        // NEW: Show offer info if page was created from offer
        const offerBadge = page.source_offer_id
            ? `<span class="order-badge badge-info">From Offer: ${page.source_offer_id}</span>`
            : '';
        
        return `
        <div class="landing-page-card">
            <div class="landing-page-header">
                <div>
                    <div class="landing-page-name">${escapeHtml(page.page_name)}</div>
                    <div class="landing-page-id">${page.landing_page_id}</div>
                    ${page.seo_friendly_prefix ? `<div class="muted">/${page.seo_friendly_prefix}</div>` : ''}
                </div>
                <div class="landing-page-actions">
                    ${statusBadge}
                    ${abTestBadge}
                    ${offerBadge}
                    <button class="small" onclick="editLandingPage('${page.landing_page_id}')">Edit</button>
                    ${page.status === 'published' ? 
                        `<button class="small secondary" onclick="viewLandingPagePreview('${page.landing_page_id}')">View</button>` :
                        ''
                    }
                    <button class="small danger" onclick="archiveLandingPage('${page.landing_page_id}')">Archive</button>
                </div>
            </div>
            <div class="landing-page-details">
                <div class="detail-group">
                    <span class="detail-label">Template</span>
                    <span class="detail-value">${getTemplateDisplayName(page.template_type)}</span>
                </div>
                <div class="detail-group">
                    <span class="detail-label">Products</span>
                    <span class="detail-value">${page.products.length} product(s)</span>
                </div>
                <div class="detail-group">
                    <span class="detail-label">Views</span>
                    <span class="detail-value">${page.analytics?.views || 0}</span>
                </div>
                <div class="detail-group">
                    <span class="detail-label">Conversions</span>
                    <span class="detail-value">${page.analytics?.conversions || 0}</span>
                </div>
                <div class="detail-group">
                    <span class="detail-label">Revenue</span>
                    <span class="amount">$${((page.analytics?.revenue || 0) / 100).toFixed(2)}</span>
                </div>
            </div>
            ${page.s3_url ? `
                <div class="landing-page-url">
                    <a href="${page.s3_url}" target="_blank">${page.s3_url}</a>
                </div>
            ` : ''}
        </div>
        `;
    }).join('');
}

// Show landing page form
function showLandingPageForm(pageData = null) {
    editingLandingPage = !!pageData;
    currentLandingPageId = pageData?.landing_page_id || null;
    
    document.getElementById('landingPagesListSection').classList.add('hidden');
    document.getElementById('landingPageFormSection').classList.remove('hidden');
    
    if (pageData) {
        // Populate form with existing data
        document.getElementById('landingPageFormTitle').textContent = 'Edit Landing Page';
        document.getElementById('pageName').value = pageData.page_name || '';
        document.getElementById('seoPrefix').value = pageData.seo_friendly_prefix || '';
        document.getElementById('templateType').value = pageData.template_type || 'single-product-hero';
        document.getElementById('lpHeroTitle').value = pageData.hero_title || '';
        document.getElementById('lpHeroSubtitle').value = pageData.hero_subtitle || '';
        document.getElementById('lpGuarantee').value = pageData.guarantee || '';
        document.getElementById('checkoutInSubfolder').checked = pageData.checkout_in_subfolder || false;
        
        // NEW: Set source mode if page was created from offer
        if (pageData.source_offer_id) {
            document.getElementById('productSourceMode').value = 'offer';
            onSourceModeChange();
            document.getElementById('landingPageOfferSelector').value = pageData.source_offer_id;
        } else {
            document.getElementById('productSourceMode').value = 'manual';
            onSourceModeChange();
        }
        
        document.getElementById('googlePixel').value = pageData.analytics_pixels?.google || '';
        document.getElementById('metaPixel').value = pageData.analytics_pixels?.meta || '';
        
        document.getElementById('customCss').value = pageData.custom_css || '';
        document.getElementById('customJs').value = pageData.custom_js || '';
        
        const layout = pageData.layout_options || {};
        document.getElementById('showTestimonials').checked = layout.show_testimonials || false;
        document.getElementById('showFaq').checked = layout.show_faq || false;
        document.getElementById('colorPrimary').value = layout.color_scheme?.primary || '#007bff';
        document.getElementById('colorAccent').value = layout.color_scheme?.accent || '#28a745';
        
        setTimeout(() => {
            loadProductsForLandingPage(pageData.products || []);
        }, 100);
    } else {
        // Clear form for new landing page
        document.getElementById('landingPageFormTitle').textContent = 'Create New Landing Page';
        document.getElementById('landingPageForm').reset();
        document.getElementById('templateType').value = 'single-product-hero';
        document.getElementById('colorPrimary').value = '#007bff';
        document.getElementById('colorAccent').value = '#28a745';
        
        // NEW: Default to offer mode and load offers
        document.getElementById('productSourceMode').value = 'offer';
        onSourceModeChange();
        
        setTimeout(() => {
            loadAvailableProducts();
            loadAvailableOffers().then(populateOfferSelector);
        }, 100);
    }
    
    onTemplateTypeChange();
    updatePreview();
}

// Hide landing page form
function hideLandingPageForm() {
    document.getElementById('landingPagesListSection').classList.remove('hidden');
    document.getElementById('landingPageFormSection').classList.add('hidden');
    currentLandingPageId = null;
    editingLandingPage = false;
}

// Template type changed
function onTemplateTypeChange() {
    const templateType = document.getElementById('templateType').value;
    const customFieldsSection = document.getElementById('customFieldsSection');
    const emailCaptureSection = document.getElementById('emailCaptureSection');
    
    if (templateType === 'configurable-product' || templateType === 'custom') {
        customFieldsSection.classList.remove('hidden');
    } else {
        customFieldsSection.classList.add('hidden');
    }
    
    if (templateType === 'email-capture-lead') {
        emailCaptureSection.classList.remove('hidden');
    } else {
        emailCaptureSection.classList.add('hidden');
    }
    
    const maxProducts = getMaxProductsForTemplate(templateType);
    const addBtn = document.getElementById('btnAddLandingProduct');
    
    if (maxProducts === 1) {
        addBtn.disabled = true;
        addBtn.textContent = 'Single Product Only';
    } else {
        addBtn.disabled = false;
        addBtn.textContent = `+ Add Product (max ${maxProducts})`;
    }
    
    updatePreview();
}

// Get max products for template type
function getMaxProductsForTemplate(templateType) {
    const limits = {
        'single-product-hero': 1,
        'three-tier-pricing': 3,
        'four-tier-pricing': 4,
        'five-tier-pricing': 5,
        'sales-letter': 1,
        'video-sales': 1,
        'email-capture-lead': 1,
        'subscription-focus': 1,
        'configurable-product': 1,
        'custom': 5
    };
    return limits[templateType] || 1;
}

// Get template display name
function getTemplateDisplayName(templateType) {
    const names = {
        'single-product-hero': 'Single Product Hero',
        'three-tier-pricing': 'Three-Tier Pricing',
        'four-tier-pricing': 'Four-Tier Pricing',
        'five-tier-pricing': 'Five-Tier Pricing',
        'sales-letter': 'Sales Letter',
        'video-sales': 'Video Sales',
        'email-capture-lead': 'Email Capture',
        'subscription-focus': 'Subscription Focus',
        'configurable-product': 'Configurable Product',
        'custom': 'Custom'
    };
    return names[templateType] || templateType;
}

// Load available products for selection
async function loadAvailableProducts() {
    try {
        const clientID = getClientID();
        const token = await getIdToken();
        
        // Updated to use RESTful endpoint
        const response = await fetch(`${window.CONFIG.apiBase}/admin/products?clientID=${encodeURIComponent(clientID)}`, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token
            }
        });
        
        const data = await response.json();
        
        if (data.error) {
            showLandingPageMsg(`Error loading products: ${data.error}`, 'err');
            return;
        }
        
        availableProducts = data.products || [];
        
        const container = document.getElementById('landingProductsList');
        if (container && container.children.length === 0) {
            container.innerHTML = '';
            addProductRow();
        }
        
    } catch (err) {
        console.error('Error loading products:', err);
        showLandingPageMsg(`Failed to load products: ${err.message}`, 'err');
    }
}

// Load products for editing
function loadProductsForLandingPage(products) {
    const container = document.getElementById('landingProductsList');
    container.innerHTML = '';
    
    if (products.length === 0) {
        addProductRow();
    } else {
        products.forEach(product => {
            addProductRow(product);
        });
    }
}

// Add product row
function addProductRow(productData = null) {
    const container = document.getElementById('landingProductsList');
    const templateType = document.getElementById('templateType').value;
    const maxProducts = getMaxProductsForTemplate(templateType);
    
    const currentCount = container.querySelectorAll('.landing-product-row').length;
    if (currentCount >= maxProducts) {
        showLandingPageMsg(`Maximum ${maxProducts} product(s) for this template`, 'err');
        return;
    }
    
    const rowId = Date.now();
    const row = document.createElement('div');
    row.className = 'landing-product-row';
    row.id = `product-row-${rowId}`;
    
    const productOptions = availableProducts.map(p => 
        `<option value="${p.id}" ${productData?.product_id === p.id ? 'selected' : ''}>
            ${escapeHtml(p.name)} - $${(p.lowest_price / 100).toFixed(2)}
        </option>`
    ).join('');
    
    row.innerHTML = `
        <div class="grid grid-two">
            <div>
                <label>Product</label>
                <select class="product-selector" data-row-id="${rowId}">
                    <option value="">-- Select Product --</option>
                    ${productOptions}
                </select>
            </div>
            <div>
                <label>Price</label>
                <select class="price-selector" data-row-id="${rowId}">
                    <option value="">-- Select Price --</option>
                </select>
            </div>
        </div>
        <div class="grid grid-two mt8">
            <div>
                <label>Tier Label (e.g., "Basic", "Premium")</label>
                <input type="text" class="tier-label" value="${escapeHtml(productData?.tier_label || '')}" placeholder="Optional">
            </div>
            <div>
                <label>Display Order</label>
                <input type="number" class="display-order" value="${productData?.display_order || currentCount + 1}" min="1">
            </div>
        </div>
        <div class="form-group mt8">
            <label style="display:flex;align-items:center;gap:8px;">
                <input type="checkbox" class="is-featured" ${productData?.is_featured ? 'checked' : ''} style="width:auto;">
                Featured Product
            </label>
        </div>
        <div class="form-group mt8">
            <label>Custom Description Override (optional)</label>
            <textarea class="custom-description" rows="2" placeholder="Leave empty to use product description">${escapeHtml(productData?.custom_description_override || '')}</textarea>
        </div>
        <div class="mt8">
            <button type="button" class="small danger" onclick="removeProductRow('${rowId}')">Remove Product</button>
        </div>
    `;
    
    container.appendChild(row);
    
    const productSelector = row.querySelector('.product-selector');
    const priceSelector = row.querySelector('.price-selector');
    
    productSelector.addEventListener('change', (e) => {
        const selectedProductId = e.target.value;
        updatePriceOptions(priceSelector, selectedProductId, productData?.price_id);
        updatePreview();
    });
    
    if (productData?.product_id) {
        updatePriceOptions(priceSelector, productData.product_id, productData.price_id);
    }
    
    priceSelector.addEventListener('change', () => updatePreview());
    row.querySelector('.tier-label').addEventListener('input', () => updatePreview());
    row.querySelector('.custom-description').addEventListener('input', () => updatePreview());
}

// Helper function to update price options
function updatePriceOptions(priceSelector, productId, selectedPriceId = null) {
    priceSelector.innerHTML = '<option value="">-- Select Price --</option>';
    
    if (!productId) return;
    
    const product = availableProducts.find(p => p.id === productId);
    
    if (!product || !product.prices || product.prices.length === 0) {
        priceSelector.innerHTML = '<option value="">No prices available</option>';
        return;
    }
    
    product.prices.forEach(price => {
        const option = document.createElement('option');
        option.value = price.id;
        
        const amount = price.unit_amount || price.unitAmount || 0;
        const currency = (price.currency || 'usd').toUpperCase();
        const recurring = price.recurring ? ` / ${price.recurring.interval}` : '';
        
        option.textContent = `$${(amount / 100).toFixed(2)} ${currency}${recurring}`;
        
        if (selectedPriceId === price.id) {
            option.selected = true;
        }
        priceSelector.appendChild(option);
    });
}

// Remove product row
function removeProductRow(rowId) {
    const row = document.getElementById(`product-row-${rowId}`);
    if (row) {
        row.remove();
        updatePreview();
    }
}

// Setup preview listeners
function setupPreviewListeners() {
    const fields = [
        'pageName', 'lpHeroTitle', 'lpHeroSubtitle', 'lpGuarantee',
        'colorPrimary', 'colorAccent', 'customCss', 'customJs'
    ];
    
    fields.forEach(fieldId => {
        const field = document.getElementById(fieldId);
        if (field) {
            field.addEventListener('input', () => {
                clearTimeout(previewDebounceTimer);
                previewDebounceTimer = setTimeout(updatePreview, 300);
            });
        }
    });
    
    const templateTypeField = document.getElementById('templateType');
    const showTestimonials = document.getElementById('showTestimonials');
    const showFaq = document.getElementById('showFaq');
    
    if (templateTypeField) templateTypeField.addEventListener('change', updatePreview);
    if (showTestimonials) showTestimonials.addEventListener('change', updatePreview);
    if (showFaq) showFaq.addEventListener('change', updatePreview);
}

// Update live preview
function updatePreview() {
    const previewFrame = document.getElementById('landingPagePreview');
    if (!previewFrame) return;
    
    try {
        const formData = gatherFormData();
        
        // Check if renderTemplate function exists
        if (typeof renderTemplate !== 'function') {
            console.error('renderTemplate function not found');
            return;
        }
        
        const html = renderTemplate(formData);
        
        // Check if HTML is valid before writing
        if (!html || html.trim().length === 0) {
            console.warn('Generated HTML is empty');
            return;
        }
        
        const doc = previewFrame.contentDocument || previewFrame.contentWindow.document;
        
        // Clear the document first to prevent script conflicts
        doc.open();
        doc.write('');
        doc.close();
        
        // Small delay before writing new content
        setTimeout(() => {
            try {
                doc.open();
                doc.write(html);
                doc.close();
            } catch (writeError) {
                console.error('Error writing to preview iframe:', writeError);
                // Don't show error to user - preview will just not update
            }
        }, 50);
        
    } catch (error) {
        console.error('Preview error:', error);
        // Don't show error to user - preview will just not update
    }
}

// Gather form data for preview/save
function gatherFormData() {
    const products = [];
    document.querySelectorAll('.landing-product-row').forEach(row => {
        const productId = row.querySelector('.product-selector').value;
        const priceId = row.querySelector('.price-selector').value;
        
        if (productId && priceId) {
            const product = availableProducts.find(p => p.id === productId);
            products.push({
                product_id: productId,
                price_id: priceId,
                tier_label: row.querySelector('.tier-label').value.trim(),
                display_order: parseInt(row.querySelector('.display-order').value) || 1,
                is_featured: row.querySelector('.is-featured').checked,
                custom_description_override: row.querySelector('.custom-description').value.trim() || null,
                _product: product
            });
        }
    });
    
    // NEW: Include source offer if one was selected
    const sourceMode = document.getElementById('productSourceMode').value;
    const sourceOfferId = sourceMode === 'offer' 
        ? document.getElementById('landingPageOfferSelector').value 
        : null;
    
    return {
        page_name: document.getElementById('pageName').value.trim(),
        seo_friendly_prefix: document.getElementById('seoPrefix').value.trim(),
        template_type: document.getElementById('templateType').value,
        hero_title: document.getElementById('lpHeroTitle').value.trim(),
        hero_subtitle: document.getElementById('lpHeroSubtitle').value.trim(),
        guarantee: document.getElementById('lpGuarantee').value.trim(),
        products: products,
        source_offer_id: sourceOfferId, // NEW
        checkout_in_subfolder: document.getElementById('checkoutInSubfolder').checked,
        analytics_pixels: {
            google: document.getElementById('googlePixel').value.trim(),
            meta: document.getElementById('metaPixel').value.trim()
        },
        custom_css: document.getElementById('customCss').value.trim(),
        custom_js: document.getElementById('customJs').value.trim(),
        layout_options: {
            show_testimonials: document.getElementById('showTestimonials').checked,
            show_faq: document.getElementById('showFaq').checked,
            color_scheme: {
                primary: document.getElementById('colorPrimary').value,
                accent: document.getElementById('colorAccent').value
            }
        }
    };
}

// Auto-suggest SEO prefix from page name
const pageNameField = document.getElementById('pageName');
const seoPrefixField = document.getElementById('seoPrefix');

if (pageNameField && seoPrefixField) {
    pageNameField.addEventListener('input', (e) => {
        if (!seoPrefixField.value || seoPrefixField.dataset.autoGenerated === 'true') {
            const slug = slugify(e.target.value);
            seoPrefixField.value = slug;
            seoPrefixField.dataset.autoGenerated = 'true';
        }
    });

    seoPrefixField.addEventListener('input', (e) => {
        if (e.target.value) {
            delete e.target.dataset.autoGenerated;
        }
    });
}

function slugify(text) {
    return text
        .toLowerCase()
        .replace(/[^\w\s-]/g, '')
        .replace(/\s+/g, '-')
        .replace(/-+/g, '-')
        .trim();
}

// Save landing page
async function saveLandingPage(evt) {
    evt.preventDefault();
    
    const btn = document.getElementById('btnSaveLandingPage');
    const res = startSpin(btn, 'Saving...');
    showLandingPageMsg('Saving landing page...', 'muted');
    
    try {
        const clientID = getClientID();
        if (!clientID) {
            showLandingPageMsg('Please set your clientID first', 'err');
            stopSpin(res);
            return;
        }
        
        const formData = gatherFormData();
        
        // Validation...
        if (!formData.page_name) {
            showLandingPageMsg('Page name is required', 'err');
            stopSpin(res);
            return;
        }
        
        if (!formData.seo_friendly_prefix) {
            showLandingPageMsg('SEO friendly prefix is required', 'err');
            stopSpin(res);
            return;
        }
        
        if (formData.products.length === 0) {
            showLandingPageMsg('At least one product is required', 'err');
            stopSpin(res);
            return;
        }
        
        const token = await getIdToken();
        
        // Clean up products - use snake_case for backend
        const cleanProducts = formData.products.map(p => ({
            product_id: p.product_id,
            price_id: p.price_id,
            tier_label: p.tier_label || '',
            display_order: p.display_order || 1,
            is_featured: p.is_featured || false,
            custom_description_override: p.custom_description_override || null
        }));
        
        // Build payload using snake_case to match Python backend
        const payload = {
            page_name: formData.page_name,
            seo_friendly_prefix: formData.seo_friendly_prefix,
            template_type: formData.template_type,
            hero_title: formData.hero_title,
            hero_subtitle: formData.hero_subtitle,
            guarantee: formData.guarantee,
            products: cleanProducts,
            source_offer_id: formData.source_offer_id || null,
            checkout_in_subfolder: formData.checkout_in_subfolder || false,
            analytics_pixels: formData.analytics_pixels,
            custom_css: formData.custom_css || '',
            custom_js: formData.custom_js || '',
            layout_options: formData.layout_options,
            status: 'draft'
        };
        
        // FIXED: Use correct endpoint and HTTP method
        let url, method;
        if (editingLandingPage && currentLandingPageId) {
            // Update existing - PUT to /admin/landing-pages/{id}
            url = `${window.CONFIG.apiBase}/admin/landing-pages/${currentLandingPageId}`;
            method = 'PUT';
        } else {
            // Create new - POST to /admin/landing-pages
            url = `${window.CONFIG.apiBase}/admin/landing-pages`;
            method = 'POST';
        }
        
        console.log(`${method} ${url}`, JSON.stringify(payload, null, 2));
        
        const response = await fetch(url, {
            method: method,
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token,
                'X-Client-Id': clientID
            },
            body: JSON.stringify(payload)
        });
        
        const data = await response.json();
        
        console.log('Save response:', data);
        
        if (!response.ok || data.error) {
            stopSpin(res);
            const errorMsg = data.error || data.message || `HTTP ${response.status}`;
            console.error('Save error details:', data);
            showLandingPageMsg(`Error: ${errorMsg}`, 'err');
            return;
        }
        
        showLandingPageMsg('Landing page saved successfully!', 'ok');
        currentLandingPageId = data.landing_page_id;
        editingLandingPage = true;
        
    } catch (err) {
        stopSpin(res);
        console.error('Save landing page error:', err);
        showLandingPageMsg(`Failed to save: ${err.message}`, 'err');
    } finally {
        stopSpin(res);
    }
}

// Publish landing page
async function publishLandingPage(evt) {
    evt.preventDefault();
    
    if (!currentLandingPageId) {
        showLandingPageMsg('Please save the landing page first', 'err');
        return;
    }
    
    const btn = document.getElementById('btnPublishLandingPage');
    const res = startSpin(btn, 'Publishing...');
    showLandingPageMsg('Generating static page and uploading to S3...', 'muted');
    
    try {
        const clientID = getClientID();
        const token = await getIdToken();
        
        console.log('Publishing landing page:', currentLandingPageId);
        
        // Updated to use RESTful endpoint
        const response = await fetch(`${window.CONFIG.apiBase}/admin/landing-pages/publish/${currentLandingPageId}?clientID=${encodeURIComponent(clientID)}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token
            }
        });
        
        const data = await response.json();
        
        console.log('Publish response:', data);
        
        if (!response.ok || data.error) {
            stopSpin(res);
            const errorMsg = data.error || data.message || `HTTP ${response.status}`;
            console.error('Publish error details:', data);
            showLandingPageMsg(`Publish error: ${errorMsg}`, 'err');
            return;
        }
        
        showLandingPageMsg(`Published successfully! URL: ${data.s3_url}`, 'ok');
        
        setTimeout(() => {
            hideLandingPageForm();
            loadLandingPages();
        }, 2000);
        
    } catch (err) {
        stopSpin(res);
        console.error('Publish error:', err);
        showLandingPageMsg(`Failed to publish: ${err.message}`, 'err');
    } finally {
        stopSpin(res);
    }
}

// Edit existing landing page
async function editLandingPage(landingPageId) {
    showLandingPageMsg('');
    
    try {
        const clientID = getClientID();
        const session = await getSession();
        const token = session.getIdToken().getJwtToken();
        
        const response = await fetch(`${window.CONFIG.apiBase}/admin/landing-pages/${landingPageId}`, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`,
                'X-Client-Id': clientID
            }
        });
        
        const data = await response.json();
        
        if (data.error) {
            showLandingPageMsg(`Error: ${data.error}`, 'err');
            return;
        }
        
        await loadAvailableProducts();
        await loadAvailableOffers();
        
        showLandingPageForm(data.landing_page);
        
    } catch (err) {
        console.error('Edit landing page error:', err);
        showLandingPageMsg(`Failed to load: ${err.message}`, 'err');
    }
}

// View published landing page
function viewLandingPagePreview(landingPageId) {
    const page = currentLandingPages.find(p => p.landing_page_id === landingPageId);
    if (page && page.s3_url) {
        window.open(page.s3_url, '_blank');
    }
}

// Archive landing page
async function archiveLandingPage(landingPageId) {
    if (!confirm('Archive this landing page? It will no longer be accessible.')) {
        return;
    }
    
    showLandingPageMsg('Archiving...', 'muted');
    
    try {
        const clientID = getClientID();
        const token = await getIdToken();
        
        // Updated to use RESTful endpoint
        const response = await fetch(`${window.CONFIG.apiBase}/admin/landing-pages/${landingPageId}?clientID=${encodeURIComponent(clientID)}`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token
            }
        });
        
        const data = await response.json();
        
        if (data.error) {
            showLandingPageMsg(`Error: ${data.error}`, 'err');
            return;
        }
        
        showLandingPageMsg('Landing page archived', 'ok');
        loadLandingPages();
        
    } catch (err) {
        console.error('Archive error:', err);
        showLandingPageMsg(`Failed to archive: ${err.message}`, 'err');
    }
}

// Show A/B test dialog
function showABTestDialog() {
    alert('A/B Testing feature coming soon!');
}

// Helper to show messages
function showLandingPageMsg(msg, cls = 'muted') {
    const msgEl = document.getElementById('landingPageMsg');
    if (msgEl) {
        msgEl.textContent = msg;
        msgEl.className = cls;
    }
}

// Toggle form panel visibility for better preview
function toggleFormPanel() {
    const editor = document.querySelector('.landing-page-editor');
    const toggleBtn = document.getElementById('btnToggleForm');
    
    if (!editor || !toggleBtn) return;
    
    if (editor.classList.contains('preview-only')) {
        // Show form
        editor.classList.remove('preview-only');
        toggleBtn.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="11 17 6 12 11 7"></polyline>
                <polyline points="18 17 13 12 18 7"></polyline>
            </svg>
            <span>Hide Form</span>
        `;
        toggleBtn.title = 'Hide form panel';
    } else {
        // Hide form
        editor.classList.add('preview-only');
        toggleBtn.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="13 17 18 12 13 7"></polyline>
                <polyline points="6 17 11 12 6 7"></polyline>
            </svg>
            <span>Show Form</span>
        `;
        toggleBtn.title = 'Show form panel';
    }
}

// Setup toggle button
function setupToggleButton() {
    const toggleBtn = document.getElementById('btnToggleForm');
    if (toggleBtn) {
        toggleBtn.addEventListener('click', toggleFormPanel);
    }
}

// Export functions to global scope for inline onclick handlers
window.editLandingPage = editLandingPage;
window.viewLandingPagePreview = viewLandingPagePreview;
window.archiveLandingPage = archiveLandingPage;
window.removeProductRow = removeProductRow;
window.initLandingPages = initLandingPages;
window.toggleFormPanel = toggleFormPanel;