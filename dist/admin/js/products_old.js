// products.js - Stripe Product Management
const _productCache = new Map();
const API_BASE = window.CONFIG.apiBase;
const UPLOAD_API_BASE = 'https://dph4d1c6p8.execute-api.us-west-2.amazonaws.com/v3';

let currentProducts = [];
let currentProductId = null;
let editingProduct = false;
let uploadedImages = [];
let pendingUploads = []; // array of Promises
let _productsInitDone = false; // one-time init guard to prevent duplicate listeners

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
    document.getElementById('btnLoadProducts').addEventListener('click', loadProducts);
    
    // Create new product button
    document.getElementById('btnNewProduct').addEventListener('click', () => showProductForm());
    
    // Save product button
    document.getElementById('btnSaveProduct').addEventListener('click', saveProduct);
    
    // Cancel button
    document.getElementById('btnCancelProduct').addEventListener('click', hideProductForm);
    
    // Add price button
    document.getElementById('btnAddPrice').addEventListener('click', addPriceRow);

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
async function loadProducts() {
    const clientID = getClientID();
    if (!clientID) {
        showProductMsg('Please set your clientID in the Stripe Keys tab first', 'err');
        return;
    }

    const btn = document.getElementById('btnLoadProducts');
    const res = startSpin(btn, 'Loading ...');
    showProductMsg('Loading products...');
    
    try {
        const token = await getIdToken();
        // Updated to use RESTful endpoint: GET /admin/products?clientID=xxx
        const response = await fetch(`${API_BASE}/admin/products?clientID=${encodeURIComponent(clientID)}`, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token
            }
        });

        const data = await response.json();
        
        if (data.error) {
            stopSpin(res); 
            showProductMsg(`Error: ${data.error}`, 'err');
            return;
        }

        currentProducts = data.products || [];
        renderProductsList();
        showProductMsg(`Loaded ${currentProducts.length} products`, 'ok');
    } catch (err) {
        stopSpin(res); 
        console.error('Load products error:', err);
        showProductMsg(`Failed to load products: ${err.message}`, 'err');
    } finally {
        stopSpin(res);
        showProductMsg('');
    }
}

// Render products list
function renderProductsList() {
    const container = document.getElementById('productsListContainer');
    
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
                        ''
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

function showProductForm(productData = null, isCopying = false) {
    editingProduct = !!productData && !isCopying;
    currentProductId = (productData?.id && !isCopying) ? productData.id : null;
    
    document.getElementById('productsListSection').classList.add('hidden');
    document.getElementById('productFormSection').classList.remove('hidden');
    
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
    document.getElementById('productsListSection').classList.remove('hidden');
    document.getElementById('productFormSection').classList.add('hidden');
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
        
        const response = await fetch(API_BASE, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token,
                'X-Client-Id': clientID
            },
            body: JSON.stringify({
                action: 'get_available_products'
            })
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
    evt.preventDefault();

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
        const action = (editingProduct && currentProductId) ? 'update_product' : 'create_product';
        
        const payload = {
            action,
            ...productData
        };
        
        if (editingProduct && currentProductId) {
            payload.product_id = currentProductId;
        }

        console.log('Sending payload:', JSON.stringify(payload, null, 2));
        
        const response = await fetch(API_BASE, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token,
                'X-Client-Id': clientID
            },
            body: JSON.stringify(payload)
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
    
    try {
        const clientID = getClientID();
        const token = await getIdToken();
        
        const response = await fetch(API_BASE, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token,
                'X-Client-Id': clientID
            },
            body: JSON.stringify({
                action: 'get_product_details',
                product_id: productId
            })
        });

        const data = await response.json();
        console.log(data);
        
        if (data.error) {
            showProductMsg(`Error: ${data.error}`, 'err');
            return;
        }

        // Store prices globally for showProductForm to access
        window.currentProductPrices = data.prices || [];

        //Show Price Management section to allow price default change (should be a function but for now, doing DOM manipulation)
        document.getElementById('priceManagementSection').classList.remove('hidden');
        
        showProductForm(data.product);

        // ⬅️ Render existing prices so default can be changed
        displayExistingPrices(window.currentProductPrices);

    } catch (err) {
        console.error('Edit product error:', err);
        showProductMsg(`Failed to load product: ${err.message}`, 'err');
    } finally {
        showProductMsg('');
    }
}

// View product details (modal or expanded view)
async function viewProductDetails(productId) {
    showProductMsg('Loading product details...');
    
    try {
        const clientID = getClientID();
        const token = await getIdToken();
        
        const response = await fetch(API_BASE, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token,
                'X-Client-Id': clientID
            },
            body: JSON.stringify({
                action: 'get_product_details',
                product_id: productId
            })
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
    const modal = document.getElementById('productDetailsModal');
    const modalBody = document.getElementById('productDetailsBody');
    
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
    if (!confirm('Are you sure you want to archive this product? It will be set to inactive in Stripe.')) {
        return;
    }
    
    showProductMsg('Archiving product...', 'muted');
    
    try {
        const clientID = getClientID();
        const token = await getIdToken();
        
        const response = await fetch(API_BASE, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token,
                'X-Client-Id': clientID
            },
            body: JSON.stringify({
                action: 'delete_product',
                product_id: productId
            })
        });

        const data = await response.json();
        
        if (data.error) {
            showProductMsg(`Error: ${data.error}`, 'err');
            return;
        }

        showProductMsg('Product archived successfully!', 'ok');
        loadProducts();
    } catch (err) {
        console.error('Archive product error:', err);
        showProductMsg(`Failed to archive product: ${err.message}`, 'err');
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
    if (!confirm('Archive this price? It will no longer be available for new purchases.')) {
        return;
    }

    const btn = document.getElementById('btnArchivePrice');
    const res = startSpin(btn);
    showProductMsg('Archiving...', 'muted');
    
    try {
        const clientID = getClientID();
        const token = await getIdToken();
        
        const response = await fetch(API_BASE, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token,
                'X-Client-Id': clientID
            },
            body: JSON.stringify({
                action: 'update_price',
                price_id: priceId,
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
    
    const inFormView = !document.getElementById('productFormSection').classList.contains('hidden');
    
    if (inFormView) {
        // Show in both top and bottom of form
        if (formMsgTop) {
            formMsgTop.textContent = msg;
            formMsgTop.className = cls;
        }
        if (formMsgBottom) {
            formMsgBottom.textContent = msg;
            formMsgBottom.className = cls;
        }
    } else if (listMsg) {
        // Show in list view
        listMsg.textContent = msg;
        listMsg.className = cls;
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

    for (const p of prices) {
      await fetch(API_BASE, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': token,
          'X-Client-Id': clientID
        },
        body: JSON.stringify({
          action: 'update_price',
          price_id: p.id,
          metadata: { is_default: "" }
        })
      });
    }
}

// Helper to set a single price as default
async function setDefault(priceId) {
    const clientID = getClientID();
    const token = await getIdToken();
    const url = window.CONFIG.apiBase;

    await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': token,
        'X-Client-Id': clientID
      },
      body: JSON.stringify({
        action: 'update_price',
        price_id: priceId,
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

// Initialize on load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        // Inject styles
        const style = document.createElement('style');
        style.textContent = imageUploadStyles;
        document.head.appendChild(style);
    });
} else {
    // Inject styles
    const style = document.createElement('style');
    style.textContent = imageUploadStyles;
    document.head.appendChild(style);
}

initProducts();