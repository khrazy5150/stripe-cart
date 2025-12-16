// ======== SHIPPING CONFIGURATION ========

// ======== LOAD SHIPPING CONFIG ========

async function loadShippingConfig(clientID) {
    try {
        const res = await authedFetch(`/admin/shipping-config?clientID=${encodeURIComponent(clientID)}`);
        
        if (res.ok && res.body) {
            const config = res.body;
            
            document.getElementById('shippingProvider').value = config.shipping_provider || '';
            document.getElementById('shippingProvider').dispatchEvent(new Event('change'));
            
            if (config.shipping_config) {
                const sc = config.shipping_config;
                
                if (sc.api_key) {
                    const masked = typeof sc.api_key === 'object' ? sc.api_key.masked : sc.api_key;
                    document.getElementById('shippingApiKey').value = masked || '';
                }
                if (sc.api_secret) {
                    const masked = typeof sc.api_secret === 'object' ? sc.api_secret.masked : sc.api_secret;
                    document.getElementById('shippingApiSecret').value = masked || '';
                }
                
                document.getElementById('shippingTestMode').checked = sc.test_mode !== false;
                document.getElementById('autoFulfill').checked = sc.auto_fulfill === true;
                
                const from = sc.default_from_address || {};
                document.getElementById('shipFromName').value = from.name || '';
                document.getElementById('shipFromStreet1').value = from.street1 || '';
                document.getElementById('shipFromStreet2').value = from.street2 || '';
                document.getElementById('shipFromCity').value = from.city || '';
                document.getElementById('shipFromState').value = from.state || '';
                document.getElementById('shipFromZip').value = from.zip || '';
                document.getElementById('shipFromCountry').value = from.country || 'US';
                document.getElementById('shipFromPhone').value = from.phone || '';
                
                const parcel = sc.default_parcel || {};
                document.getElementById('parcelLength').value = parcel.length || 10;
                document.getElementById('parcelWidth').value = parcel.width || 8;
                document.getElementById('parcelHeight').value = parcel.height || 4;
                document.getElementById('parcelWeight').value = parcel.weight || 1;
            }
            
            setMsg('shippingMsg', 'Shipping configuration loaded', 'success');
        } else {
            setMsg('shippingMsg', 'No shipping configuration found', 'info');
        }
    } catch (error) {
        console.error('Error loading shipping config:', error);
        setMsg('shippingMsg', `Error: ${error.message}`, 'error');
    }
}

// ======== SAVE SHIPPING CONFIG ========

async function handleSaveShipping() {
    try {
        const clientID = document.getElementById('clientID').value.trim();
        if (!clientID) return setMsg('shippingMsg', 'Enter clientID', 'error');
        
        const provider = document.getElementById('shippingProvider').value;
        
        const payload = {
            clientID: clientID,
            shipping_provider: provider || null,
            shipping_config: provider ? {
                api_key: document.getElementById('shippingApiKey').value.trim(),
                api_secret: document.getElementById('shippingApiSecret').value.trim() || null,
                test_mode: document.getElementById('shippingTestMode').checked,
                auto_fulfill: document.getElementById('autoFulfill').checked,
                default_from_address: {
                    name: document.getElementById('shipFromName').value.trim(),
                    street1: document.getElementById('shipFromStreet1').value.trim(),
                    street2: document.getElementById('shipFromStreet2').value.trim(),
                    city: document.getElementById('shipFromCity').value.trim(),
                    state: document.getElementById('shipFromState').value.trim(),
                    zip: document.getElementById('shipFromZip').value.trim(),
                    country: document.getElementById('shipFromCountry').value.trim() || 'US',
                    phone: document.getElementById('shipFromPhone').value.trim()
                },
                default_parcel: {
                    length: parseFloat(document.getElementById('parcelLength').value) || 10,
                    width: parseFloat(document.getElementById('parcelWidth').value) || 8,
                    height: parseFloat(document.getElementById('parcelHeight').value) || 4,
                    weight: parseFloat(document.getElementById('parcelWeight').value) || 1
                }
            } : null
        };
        
        const res = await authedFetch(`/admin/shipping-config`, { 
            method: 'PUT', 
            body: payload 
        });
        
        if (!res.ok) {
            return setMsg('shippingMsg', `Save failed: ${JSON.stringify(res.body)}`, 'error');
        }
        
        setMsg('shippingMsg', 'Shipping configuration saved successfully!', 'success');
        
        // Reload to show masked values
        setTimeout(() => {
            if (window.loadUserClientData) {
                window.loadUserClientData();
            }
        }, 1000);
        
    } catch (error) {
        console.error('Save shipping error:', error);
        setMsg('shippingMsg', `Error: ${error.message}`, 'error');
    }
}

// ======== TEST SHIPPING CONNECTION ========

async function handleTestShipping() {
    try {
        const clientID = document.getElementById('clientID').value.trim();
        const provider = document.getElementById('shippingProvider').value;
        
        if (!clientID) return setMsg('shippingMsg', 'Enter clientID', 'error');
        if (!provider) return setMsg('shippingMsg', 'Select a provider first', 'error');
        
        setMsg('shippingMsg', 'Testing connection...', 'info');
        
        const res = await authedFetch(`/admin/test-shipping`, {
            method: 'POST',
            body: { clientID: clientID }
        });
        
        if (res.ok && res.body.success) {
            setMsg('shippingMsg', `Connection successful! Provider: ${res.body.provider}`, 'success');
        } else {
            setMsg('shippingMsg', `Test failed: ${res.body.error || 'Unknown error'}`, 'error');
        }
        
    } catch (error) {
        console.error('Test shipping error:', error);
        setMsg('shippingMsg', `Error: ${error.message}`, 'error');
    }
}

// ======== SHIPPING MODAL (for orders) ========

function showShippingConfirmation(orderId, defaultRate, allRates) {
    const modal = document.getElementById('shippingModal');
    const modalTitle = document.getElementById('modalTitle');
    const modalBody = document.getElementById('modalBody');
    const modalEdit = document.getElementById('modalEdit');
    
    modalTitle.textContent = 'Confirm Shipping Label';
    modalEdit.style.display = 'inline-block';
    
    const price = `$${parseFloat(defaultRate.rate).toFixed(2)}`;
    const days = defaultRate.delivery_days ? `${defaultRate.delivery_days} days` : 'N/A';
    
    modalBody.innerHTML = `
        <p><strong>${defaultRate.carrier} ${defaultRate.service}</strong> - ${price} · ${days} selected.</p>
        <p>Click 'Create Label' to continue, or 'Change Carrier/Rate' for other options.</p>
    `;
    
    modal.classList.add('active');
    modal.dataset.orderId = orderId;
    modal.dataset.rates = JSON.stringify(allRates);
    modal.dataset.defaultRateId = defaultRate.rate_id;
    
    document.getElementById('modalCancel').onclick = () => modal.classList.remove('active');
    document.getElementById('modalOk').onclick = () => {
        modal.classList.remove('active');
        proceedWithSelectedRate(orderId, null, defaultRate.rate_id);
    };
    document.getElementById('modalEdit').onclick = () => showRateSelection(orderId, allRates);
}

async function showRateSelection(orderId, allRates) {
    const modal = document.getElementById('shippingModal');
    const modalTitle = document.getElementById('modalTitle');
    const modalBody = document.getElementById('modalBody');
    const modalEdit = document.getElementById('modalEdit');
    
    modalTitle.textContent = 'Select Carrier & Rate';
    modalEdit.style.display = 'none';
    
    if (!allRates) {
        modalBody.innerHTML = '<div class="loading-spinner"></div>';
        
        try {
            const response = await authedFetch('/admin/get-rates', {
                method: 'POST',
                body: { order_id: orderId }
            });

            if (!response.ok || !response.body.rates) {
                const msg = response.body && (response.body.error || response.body.raw) || 'Failed to fetch rates.';
                modalBody.innerHTML = `<p class="err">${escapeHtml(msg)}</p>`;
                return;
            }

            allRates = response.body.rates;
        } catch (err) {
            console.error('Error fetching rates:', err);
            modalBody.innerHTML = '<p class="err">Failed to load rates. Please try again.</p>';
            return;
        }
    }
    
    if (allRates.length === 0) {
        modalBody.innerHTML = '<p class="muted">No rates available for this shipment.</p>';
        return;
    }

    const ratesByCarrier = {};
    allRates.forEach(rate => {
        const carrier = rate.carrier || 'Unknown';
        if (!ratesByCarrier[carrier]) {
            ratesByCarrier[carrier] = [];
        }
        ratesByCarrier[carrier].push(rate);
    });

    Object.keys(ratesByCarrier).forEach(carrier => {
        ratesByCarrier[carrier].sort((a, b) => parseFloat(a.rate) - parseFloat(b.rate));
    });

    let ratesHtml = '<div style="max-height: 400px; overflow-y: auto;">';
    
    Object.keys(ratesByCarrier).sort().forEach(carrier => {
        ratesHtml += `<h3 style="margin-top: 1rem; color: var(--brand);">${carrier}</h3>`;
        
        ratesByCarrier[carrier].forEach((rate, idx) => {
            const rateId = `rate_${carrier.replace(/\s+/g, '_')}_${idx}`;
            const deliveryInfo = rate.delivery_days ? `${rate.delivery_days} days` : 'N/A';
            
            ratesHtml += `
                <div class="rate-option" onclick="selectRate('${rateId}')">
                    <label style="cursor: pointer; display: flex; align-items: center; margin: 0;">
                        <input type="radio" name="selectedRate" id="${rateId}" 
                            data-rate-id="${rate.rate_id || ''}"
                            value="${rate.rate}" style="margin-right: 0.5rem;">
                        <div style="flex: 1;">
                            <div style="font-weight: 600; color: var(--text);">${rate.service}</div>
                            <div style="font-size: 0.875rem; color: var(--muted);">
                                $${parseFloat(rate.rate).toFixed(2)} · ${deliveryInfo}
                            </div>
                        </div>
                    </label>
                </div>
            `;
        });
    });
    
    ratesHtml += '</div>';
    modalBody.innerHTML = ratesHtml;
    
    document.getElementById('modalCancel').onclick = () => {
        modal.classList.remove('active');
        modalTitle.textContent = 'Confirm Shipping Label';
        modalEdit.style.display = 'inline-block';
    };
    
    document.getElementById('modalOk').onclick = () => {
        const selected = document.querySelector('input[name="selectedRate"]:checked');
        if (!selected) {
            if (window.showOrderError) {
                window.showOrderError('Please select a rate');
            }
            return;
        }
        
        const rateId = selected.dataset.rateId;
        
        modal.classList.remove('active');
        modalTitle.textContent = 'Confirm Shipping Label';
        modalEdit.style.display = 'inline-block';
        
        proceedWithSelectedRate(orderId, null, rateId);
    };
}

async function proceedWithSelectedRate(orderId, shipmentId, rateId) {
    const btn = document.querySelector(`[data-order-id="${orderId}"] .fulfill-btn[onclick*="createShippingLabel"]`);
    if (!btn) return;
    
    const originalText = btn.textContent;
    try {
        btn.disabled = true;
        btn.textContent = 'Creating...';
        
        const response = await authedFetch('/admin/create-label', {
            method: 'POST',
            body: { 
                order_id: orderId,
                rate_id: rateId
            }
        });

        if (response.ok && response.body.success) {
            if (window.showOrderSuccess) {
                window.showOrderSuccess(`Label created! Tracking: ${response.body.tracking_number || 'N/A'}`);
            }
            
            // Refresh orders to show updated tracking info
            if (window.refreshOrders) {
                setTimeout(() => window.refreshOrders(), 500);
            }
        } else {
            if (window.showOrderError) {
                window.showOrderError(`Failed to create label: ${response.body.error || 'Unknown error'}`);
            }
            btn.disabled = false;
            btn.textContent = originalText;
        }
    } catch (err) {
        console.error('Create label error:', err);
        if (window.showOrderError) {
            window.showOrderError('Failed to create shipping label. Please try again.');
        }
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

function selectRate(rateId) {
    document.querySelectorAll('.rate-option').forEach(opt => {
        opt.classList.remove('selected');
    });
    
    const radio = document.getElementById(rateId);
    if (radio) {
        radio.checked = true;
        radio.closest('.rate-option').classList.add('selected');
    }
}

// ======== EVENT LISTENERS ========

function setupShippingListeners() {
    document.getElementById('shippingProvider').addEventListener('change', function() {
        const provider = this.value;
        const apiKeySection = document.getElementById('shippingApiKeySection');
        const secretSection = document.getElementById('shipstationSecretSection');
        
        if (provider) {
            apiKeySection.classList.remove('hidden');
            if (provider === 'shipstation') {
                secretSection.classList.remove('hidden');
            } else {
                secretSection.classList.add('hidden');
            }
        } else {
            apiKeySection.classList.add('hidden');
            secretSection.classList.add('hidden');
        }
    });
    
    document.getElementById('btnSaveShipping').addEventListener('click', handleSaveShipping);
    document.getElementById('btnTestShipping').addEventListener('click', handleTestShipping);
}

// Export functions
window.loadShippingConfig = loadShippingConfig;
window.setupShippingListeners = setupShippingListeners;
window.showShippingConfirmation = showShippingConfirmation;
window.showRateSelection = showRateSelection;
window.proceedWithSelectedRate = proceedWithSelectedRate;
window.selectRate = selectRate;

if (window.setupShippingListeners) {
    window.setupShippingListeners();
}