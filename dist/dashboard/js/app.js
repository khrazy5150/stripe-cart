// ======== MAIN APPLICATION INITIALIZATION ========

// ======== LAZY LOADING SYSTEM ========

const loadedScripts = new Set();
const preloadingScripts = new Set();

// Map of tabs to their required scripts
const tabScripts = {
    'products': ['js/products.js'],
    'landing-pages': ['js/landing-pages.js'], 
    'offers': ['js/offers.js'],
    'orders': ['js/orders.js'],
    'shipping': ['js/shipping.js'],
    'config': ['js/tenant-config.js']
};

/**
 * Load a script dynamically
 */
function loadScript(src) {
    return new Promise((resolve, reject) => {
        // Check if already loaded
        if (loadedScripts.has(src)) {
            resolve();
            return;
        }

        // Check if currently loading
        if (preloadingScripts.has(src)) {
            // Wait for existing load to complete
            const checkInterval = setInterval(() => {
                if (loadedScripts.has(src)) {
                    clearInterval(checkInterval);
                    resolve();
                }
            }, 50);
            return;
        }

        preloadingScripts.add(src);

        const script = document.createElement('script');
        script.src = src;
        script.async = true;
        
        script.onload = () => {
            loadedScripts.add(src);
            preloadingScripts.delete(src);
            console.log(`✓ Loaded: ${src}`);
            
            // Initialize landing pages after script loads
            if (src.includes('landing-pages.js') && typeof window.initLandingPages === 'function') {
                window.initLandingPages();
            }
            
            resolve();
        };
        
        script.onerror = () => {
            preloadingScripts.delete(src);
            console.error(`✗ Failed to load: ${src}`);
            reject(new Error(`Script load failed: ${src}`));
        };
        
        document.body.appendChild(script);
    });
}

/**
 * Load all scripts required for a tab
 */
async function loadTabScripts(tabName, silent = false) {
    const scripts = tabScripts[tabName];
    
    if (!scripts || scripts.length === 0) {
        return; // No scripts needed for this tab
    }

    // Check if all scripts already loaded
    if (scripts.every(src => loadedScripts.has(src))) {
        return;
    }

    if (!silent) {
        console.log(`Loading scripts for tab: ${tabName}`);
    }

    try {
        // Load all scripts for this tab in parallel
        await Promise.all(scripts.map(src => loadScript(src)));
        
        if (!silent) {
            console.log(`✓ Tab '${tabName}' ready`);
        }
    } catch (error) {
        console.error(`Error loading scripts for tab '${tabName}':`, error);
        
        if (!silent) {
            // Show user-friendly error
            const tabContent = document.getElementById(`${tabName}-tab`);
            if (tabContent) {
                const errorDiv = document.createElement('div');
                errorDiv.className = 'message error';
                errorDiv.textContent = `Failed to load ${tabName} functionality. Please refresh the page.`;
                errorDiv.style.marginTop = '1rem';
                tabContent.insertBefore(errorDiv, tabContent.firstChild);
            }
        }
    }
}

// ======== TAB SWITCHING ========

async function switchMainTab(tabName) {
    // Remove active from all tabs
    document.querySelectorAll('.nav-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.tab === tabName);
    });
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.remove('active');
    });
    
    const tabContent = document.getElementById(`${tabName}-tab`);
    
    // Show loading indicator if scripts not loaded
    const scripts = tabScripts[tabName];
    const needsLoading = scripts && !scripts.every(src => loadedScripts.has(src));
    
    if (needsLoading) {
        const loadingDiv = document.createElement('div');
        loadingDiv.className = 'loading-spinner';
        loadingDiv.id = 'tab-loading-spinner';
        loadingDiv.style.margin = '2rem auto';
        tabContent.insertBefore(loadingDiv, tabContent.firstChild);
    }
    
    // Load scripts if needed
    await loadTabScripts(tabName);
    
    // Remove loading indicator
    const loadingSpinner = document.getElementById('tab-loading-spinner');
    if (loadingSpinner) {
        loadingSpinner.remove();
    }
    
    // Show tab content
    tabContent.classList.add('active');
    
    // Special handling for orders tab - load data after scripts are ready
    if (tabName === 'orders') {
        const clientID = window.currentClientID ? window.currentClientID() : null;
        if (clientID && window.loadOrdersForClient) {
            window.loadOrdersForClient();
        }
    }
}

// ======== SHIPPING MODAL MANAGEMENT ========

function showShippingConfirmation(orderId, defaultRate, allRates) {
    const modal = document.getElementById('shippingModal');
    const modalTitle = document.getElementById('modalTitle');
    const modalBody = document.getElementById('modalBody');
    const modalEdit = document.getElementById('modalEdit');
    
    // Reset modal state
    modalTitle.textContent = 'Confirm Shipping Label';
    modalEdit.style.display = 'inline-block';
    
    const price = `$${parseFloat(defaultRate.rate).toFixed(2)}`;
    const days = defaultRate.delivery_days ? `${defaultRate.delivery_days} days` : 'N/A';
    
    modalBody.innerHTML = `
        <p><strong>${defaultRate.carrier} ${defaultRate.service}</strong> - ${price} &middot; ${days} selected.</p>
        <p>Click 'Create Label' to continue with this carrier and service, or 'Change Carrier/Rate' to see other options.</p>
    `;
    
    modal.classList.add('active');
    
    // Store rates for later use
    modal.dataset.orderId = orderId;
    modal.dataset.rates = JSON.stringify(allRates);
    modal.dataset.defaultRateId = defaultRate.rate_id;
    
    // Set up button handlers
    document.getElementById('modalCancel').onclick = () => {
        modal.classList.remove('active');
    };
    
    document.getElementById('modalOk').onclick = () => {
        modal.classList.remove('active');
        proceedWithSelectedRate(orderId, null, defaultRate.rate_id);
    };
    
    document.getElementById('modalEdit').onclick = () => {
        showRateSelection(orderId, allRates);
    };
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

    // Group and sort rates by carrier
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
                                $${parseFloat(rate.rate).toFixed(2)} &middot; ${deliveryInfo}
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

// ======== ENVIRONMENT LABEL ========

function updateEnvironmentLabel() {
    const envLabel = document.getElementById('envLabel');
    if (envLabel && window.CONFIG) {
        const env = window.CONFIG.environment || (window.CONFIG.apiBase && window.CONFIG.apiBase.includes('checkout.juniorbay.com') ? 'prod' : 'dev');
        envLabel.textContent = env;
    }
}

// ======== EVENT LISTENERS SETUP ========

function setupMainTabListeners() {
    // Tab click handlers with lazy loading
    document.querySelectorAll('.nav-tab').forEach(tab => {
        // Preload on hover
        let hoverTimeout;
        
        tab.addEventListener('mouseenter', () => {
            const tabName = tab.dataset.tab;
            
            // Preload after 200ms hover (prevents loading on quick mouse-overs)
            hoverTimeout = setTimeout(() => {
                const scripts = tabScripts[tabName];
                if (scripts && !scripts.every(src => loadedScripts.has(src))) {
                    console.log(`⚡ Preloading: ${tabName}`);
                    loadTabScripts(tabName, true); // Silent preload
                }
            }, 200);
        });
        
        tab.addEventListener('mouseleave', () => {
            clearTimeout(hoverTimeout);
        });
        
        // Tab click
        tab.addEventListener('click', () => {
            switchMainTab(tab.dataset.tab);
        });
    });
}

// ======== IDLE PRELOADING ========

let idleTimer;
function resetIdleTimer() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
        console.log('🔄 Preloading remaining scripts...');
        Object.keys(tabScripts).forEach(tabName => {
            loadTabScripts(tabName, true);
        });
    }, 5000);
}

// Track user activity for idle preloading
function setupIdlePreloading() {
    ['mousedown', 'mousemove', 'keypress', 'scroll', 'touchstart'].forEach(event => {
        document.addEventListener(event, resetIdleTimer, { passive: true });
    });
    
    // Start idle timer
    resetIdleTimer();
}

// ======== INITIALIZATION ========

async function initializeApp() {
    console.log('Initializing app...');
    
    // Update environment label
    updateEnvironmentLabel();
    
    // Setup main tab switching with lazy loading
    setupMainTabListeners();
    
    // Setup idle preloading
    setupIdlePreloading();

    // Keep env label synchronized with config/env changes
    window.addEventListener('configLoaded', () => updateEnvironmentLabel());

    // ⬇️ On environment switch, update label AND re-pull tenant-scoped data (no logout)
    window.addEventListener('environmentChanged', async () => {
        try {
            updateEnvironmentLabel();
            if (typeof window.loadUserClientData === 'function') {
                await window.loadUserClientData();
            }
            // Let tab modules refresh if they listen for this
            document.dispatchEvent(new Event('data:refresh'));
        } catch (e) {
            console.error('Post-env-switch refresh failed:', e);
        }
    });
    
    // Setup module-specific listeners (only for always-loaded modules)
    if (window.setupAuthListeners) {
        window.setupAuthListeners();
    }
    
    if (window.setupStripeKeysListeners) {
        window.setupStripeKeysListeners();
    }
    
    // Other module listeners will be set up when their scripts load
    
    console.log('App initialized successfully');
}

// --- Env toggle & badge wiring (no inline HTML script needed)
(function () {
    function currentEnv() {
        const cfg = window.CONFIG || {};
        return cfg.environment || localStorage.getItem('env') || 'dev';
    }

    function syncEnvBadge(env) {
        const label = document.getElementById('envLabel');
        if (label) label.textContent = env;
    }

    function updateToggle(env) {
        const btn = document.getElementById('envToggle');
        if (!btn) return;
        const next = env === 'prod' ? 'dev' : 'prod';
        btn.setAttribute('aria-pressed', env === 'prod' ? 'true' : 'false');
        const nextLabel = document.getElementById('envNextLabel');
        if (nextLabel) nextLabel.textContent = next;
    }

    // Initial sync (even before config fetch resolves)
    document.addEventListener('DOMContentLoaded', () => {
        const env = currentEnv();
        syncEnvBadge(env);
        updateToggle(env);
    });

    // React to config load + explicit env changes
    window.addEventListener('configLoaded', (e) => {
        const env = (e && e.detail && e.detail.env) || currentEnv();
        syncEnvBadge(env);
        updateToggle(env);
    });
    window.addEventListener('environmentChanged', (e) => {
        const env = (e && e.detail && e.detail.env) || currentEnv();
        syncEnvBadge(env);
        updateToggle(env);
    });

    // Click → toggle environment using the global setter from config.js
    document.addEventListener('click', async (evt) => {
        const btn = evt.target.closest ? evt.target.closest('#envToggle') : null;
        if (!btn) return;
        const env = currentEnv();
        const next = env === 'prod' ? 'dev' : 'prod';
        if (typeof window.setEnvironment === 'function') {
            await window.setEnvironment(next);
        }
    });

    // Provide a no-op global for any legacy calls
    if (typeof window.updateEnvironmentLabel !== 'function') {
        window.updateEnvironmentLabel = function () {
            syncEnvBadge(currentEnv());
        };
    }
})();

// ======== STARTUP ========

async function startApp() {
    try {
        // Load configuration first
        if (window.loadConfiguration) {
            console.log('Loading configuration...');
            const configLoaded = await window.loadConfiguration();
            if (!configLoaded) {
                console.error('Failed to load configuration');
                // Show error to user
                document.body.innerHTML = `
                    <div style="display: flex; align-items: center; justify-content: center; min-height: 100vh; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
                        <div style="max-width: 400px; text-align: center; padding: 2rem;">
                            <h1 style="color: var(--danger); margin-bottom: 1rem;">Configuration Error</h1>
                            <p style="color: var(--muted); margin-bottom: 1rem;">Failed to load application configuration. Please check your network connection and try again.</p>
                            <button onclick="location.reload()" style="padding: 0.75rem 1.5rem; background: var(--brand); color: var(--bg); border: none; border-radius: 0.375rem; cursor: pointer; font-weight: 600;">Retry</button>
                        </div>
                    </div>
                `;
                return;
            }
            console.log('Configuration loaded successfully');
        }
        
        // Initialize app
        await initializeApp();
        
        // Check if user is already authenticated
        if (window.initializeAuth) {
            await window.initializeAuth();
        }
        
    } catch (error) {
        console.error('Error starting app:', error);
        // Show error to user
        document.body.innerHTML = `
            <div style="display: flex; align-items: center; justify-content: center; min-height: 100vh; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
                <div style="max-width: 400px; text-align: center; padding: 2rem;">
                    <h1 style="color: var(--danger); margin-bottom: 1rem;">Application Error</h1>
                    <p style="color: var(--muted); margin-bottom: 1rem;">${error.message || 'An unexpected error occurred.'}</p>
                    <button onclick="location.reload()" style="padding: 0.75rem 1.5rem; background: var(--brand); color: var(--bg); border: none; border-radius: 0.375rem; cursor: pointer; font-weight: 600;">Reload</button>
                </div>
            </div>
        `;
    }
}

// Export functions
window.switchMainTab = switchMainTab;
window.showShippingConfirmation = showShippingConfirmation;
window.showRateSelection = showRateSelection;
window.proceedWithSelectedRate = proceedWithSelectedRate;
window.selectRate = selectRate;
window.initializeApp = initializeApp;
window.startApp = startApp;

// Start the app when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startApp);
} else {
    startApp();
}
