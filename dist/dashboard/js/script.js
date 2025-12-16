/**
 * Admin Dashboard - Enhanced Main JavaScript
 * Integrated with multi-tenant offer/product management
 */

(function() {
    'use strict';

    // ==================== STATE ====================
    const state = {
        currentTab: 'dashboard',
        theme: localStorage.getItem('theme') || 'light',
        sidebarCollapsed: localStorage.getItem('sidebarCollapsed') === 'true',
        env: localStorage.getItem('env') || 'dev',
        isLoggedIn: localStorage.getItem('isLoggedIn') === 'true',
        user: JSON.parse(localStorage.getItem('user') || '{}'),
        
        // Multi-tenant config
        clientID: '',
        offerKey: '',
        baseUrl: '',
        currentOffer: null,
        products: [],
        offers: []
    };

    // ==================== DOM ELEMENTS ====================
    const $ = (selector) => document.querySelector(selector);
    const $$ = (selector) => document.querySelectorAll(selector);

    // ==================== INITIALIZATION ====================
    function init() {
        applyTheme(state.theme);
        applySidebarState();
        
        if (state.isLoggedIn) {
            showDashboard();
            loadSavedConfig();
        } else {
            showLogin();
        }

        bindEvents();
        updateEnvBadge();
    }

    // ==================== THEME ====================
    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        state.theme = theme;
        localStorage.setItem('theme', theme);

        updateThemeToggleTitles(theme);
    }

    function toggleTheme() {
        const newTheme = state.theme === 'light' ? 'dark' : 'light';
        applyTheme(newTheme);
    }

    function updateThemeToggleTitles(theme) {
        const isLight = theme === 'light';
        const label = isLight ? 'Switch to dark theme' : 'Switch to light theme';

        $$('.theme-toggle').forEach(btn => {
            btn.title = label;                    // tooltip
            btn.setAttribute('aria-label', label); // for screen readers
        });
    }

    // ==================== SIDEBAR ====================
    function applySidebarState() {
        const sidebar = $('.sidebar');
        if (sidebar && state.sidebarCollapsed) {
            sidebar.classList.add('collapsed');
        }
    }

    function toggleSidebar() {
        const sidebar = $('.sidebar');
        if (!sidebar) return;

        if (window.innerWidth <= 768) {
            sidebar.classList.toggle('mobile-open');
            const overlay = $('.sidebar-overlay');
            if (overlay) {
                overlay.classList.toggle('active');
            }
        } else {
            sidebar.classList.toggle('collapsed');
            state.sidebarCollapsed = sidebar.classList.contains('collapsed');
            localStorage.setItem('sidebarCollapsed', state.sidebarCollapsed);
        }
    }

    function closeMobileSidebar() {
        const sidebar = $('.sidebar');
        const overlay = $('.sidebar-overlay');
        if (sidebar) sidebar.classList.remove('mobile-open');
        if (overlay) overlay.classList.remove('active');
    }

    // ==================== NAVIGATION ====================
    function handleNavClick(e) {
        const navLink = e.target.closest('.nav-link');
        if (!navLink) return;

        const navItem = navLink.closest('.nav-item');
        const hasSubmenu = navItem && navItem.querySelector('.submenu');

        if (hasSubmenu) {
            e.preventDefault();
            navItem.classList.toggle('open');
        } else {
            const tabId = navLink.dataset.tab;
            if (tabId) {
                e.preventDefault();
                switchTab(tabId);
                
                $$('.nav-link').forEach(link => link.classList.remove('active'));
                navLink.classList.add('active');

                if (window.innerWidth <= 768) {
                    closeMobileSidebar();
                }
            }
        }
    }

    function switchTab(tabId) {
        $$('.tab-content').forEach(tab => tab.classList.remove('active'));
        
        const targetTab = $(`#${tabId}-tab`);
        if (targetTab) {
            targetTab.classList.add('active');
            state.currentTab = tabId;
            updatePageHeader(tabId);
            
            // Load data when switching to specific tabs
            if (tabId === 'products') {
                renderProductsTab();
            } else if (tabId === 'offers') {
                renderOffersTab();
            }
        }
    }

    function updatePageHeader(tabId) {
        const titles = {
            dashboard: { title: 'Dashboard', subtitle: 'Overview of your business metrics' },
            keys: { title: 'Stripe Keys', subtitle: 'Manage your tenant Stripe API keys' },
            products: { title: 'Products', subtitle: 'Manage your Stripe products and pricing' },
            offers: { title: 'Offers', subtitle: 'Configure offers for your clients' },
            'landing-pages': { title: 'Landing Pages', subtitle: 'Create and manage landing pages' },
            orders: { title: 'Orders', subtitle: 'View and manage customer orders' },
            shipping: { title: 'Shipping', subtitle: 'Configure shipping options and rates' },
            config: { title: 'Configuration', subtitle: 'System settings and preferences' }
        };

        const headerData = titles[tabId] || { title: 'Dashboard', subtitle: '' };
        const pageTitle = $('.page-title');
        const pageSubtitle = $('.page-subtitle');

        if (pageTitle) pageTitle.textContent = headerData.title;
        if (pageSubtitle) pageSubtitle.textContent = headerData.subtitle;
    }

    // ==================== LOGIN / AUTH ====================
    function showLogin() {
        const loginSection = $('#login-section');
        const mainApp = $('#main-app');
        
        if (loginSection) loginSection.classList.remove('hidden');
        if (mainApp) mainApp.classList.add('hidden');
    }

    function showDashboard() {
        const loginSection = $('#login-section');
        const mainApp = $('#main-app');
        
        if (loginSection) loginSection.classList.add('hidden');
        if (mainApp) mainApp.classList.remove('hidden');

        updateUserDisplay();
    }

    function handleLogin(e) {
        e.preventDefault();
        
        const email = $('#loginEmail')?.value;
        const password = $('#loginPass')?.value;

        if (!email || !password) {
            showMessage('#loginMsg', 'Please fill in all fields', 'danger');
            return;
        }

        // Simulate login (replace with actual API call)
        state.isLoggedIn = true;
        state.user = { email, name: email.split('@')[0] };
        
        localStorage.setItem('isLoggedIn', 'true');
        localStorage.setItem('user', JSON.stringify(state.user));

        showDashboard();
    }

    function handleLogout() {
        state.isLoggedIn = false;
        state.user = {};
        
        localStorage.removeItem('isLoggedIn');
        localStorage.removeItem('user');

        showLogin();
    }

    function updateUserDisplay() {
        const userName = $('.user-name');
        const userAvatar = $('.user-avatar');

        if (userName && state.user.name) {
            userName.textContent = state.user.name;
        }
        if (userAvatar && state.user.name) {
            userAvatar.textContent = state.user.name.charAt(0).toUpperCase();
        }
    }

    // ==================== LOGIN TABS ====================
    function handleLoginTabClick(e) {
        const tab = e.target.closest('.login-tab');
        if (!tab) return;

        const tabId = tab.dataset.authTab;
        if (!tabId) return;

        $$('.login-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');

        $$('.login-view').forEach(v => v.classList.remove('active'));
        const targetView = $(`#auth-${tabId}`);
        if (targetView) targetView.classList.add('active');
    }

    // ==================== ENV TOGGLE ====================
    function toggleEnv() {
        state.env = state.env === 'dev' ? 'prod' : 'dev';
        localStorage.setItem('env', state.env);
        updateEnvBadge();
        
        document.body.classList.remove('env-dev', 'env-prod');
        document.body.classList.add(`env-${state.env}`);
        
        // Update base URL when env changes
        updateBaseUrl();
    }

    function updateEnvBadge() {
        const envLabel = $('#envLabel');
        const envToggle = $('#envToggle');
        
        if (envLabel) {
            envLabel.textContent = state.env.toUpperCase();
            envLabel.className = `env-badge ${state.env}`;
        }
        
        if (envToggle) {
            envToggle.checked = state.env === 'prod';
        }

        document.body.classList.remove('env-dev', 'env-prod');
        document.body.classList.add(`env-${state.env}`);
    }

    function updateBaseUrl() {
        state.baseUrl = state.env === 'prod' 
            ? 'https://checkout.juniorbay.com'
            : 'https://api-dev.juniorbay.com'; // Update with dev URL if different
    }

    // ==================== MULTI-TENANT CONFIG ====================
    function loadSavedConfig() {
        state.clientID = localStorage.getItem('clientID') || '';
        state.offerKey = localStorage.getItem('offerKey') || '';
        updateBaseUrl();
        
        const clientIDInput = $('#clientID');
        if (clientIDInput && state.clientID) {
            clientIDInput.value = state.clientID;
        }
    }

    function saveConfig() {
        const clientIDInput = $('#clientID');
        if (clientIDInput) {
            state.clientID = clientIDInput.value.trim();
            localStorage.setItem('clientID', state.clientID);
        }
    }

    // ==================== API HELPERS ====================
    async function fetchOffer(clientID, offerKey) {
        const api = `${state.baseUrl}/public/offer?clientID=${encodeURIComponent(clientID)}&offer=${encodeURIComponent(offerKey)}`;
        
        try {
            const res = await fetch(api, {
                credentials: 'omit',
                mode: 'cors',
                cache: 'no-store'
            });
            
            if (!res.ok) throw new Error('HTTP ' + res.status);
            
            const data = await res.json();
            return data;
        } catch (err) {
            console.error('Error fetching offer:', err);
            throw err;
        }
    }

    function normalizeProducts(json, offerKey) {
        let node = json;
        if (node && node.offers && node.offers[offerKey]) {
            node = node.offers[offerKey];
        }

        const arr = Array.isArray(node?.product_ids) ? node.product_ids : [];

        return arr.map((p, i) => {
            const price = p.lowest_price != null
                ? (p.lowest_price / 100)
                : (Array.isArray(p.prices) && p.prices.length
                    ? Math.min(...p.prices.map(x => (x.unit_amount || 0) / 100))
                    : 0);

            let compareAt = 0;
            if (Array.isArray(p.prices) && p.prices.length) {
                const maxUnit = Math.max(...p.prices.map(x => Number(x.unit_amount || 0)));
                const maxDollars = maxUnit / 100;
                compareAt = maxDollars > price ? maxDollars : 0;
            }

            return {
                id: p.id || `prod-${i+1}`,
                price_id: (Array.isArray(p.prices) && p.prices.length) 
                    ? (p.prices.find(pr => pr.unit_amount === p.lowest_price) || p.prices[0]).id 
                    : null,
                name: p.name || `Package ${i+1}`,
                description: p.description || '',
                price,
                compare_at: compareAt,
                badge: p.badge || p.label || '',
                prices: p.prices || [],
                metadata: p.metadata || {}
            };
        });
    }

    // ==================== PRODUCTS TAB ====================
    function renderProductsTab() {
        const tab = $('#products-tab');
        if (!tab) return;

        const cardBody = tab.querySelector('.card-body');
        if (!cardBody) return;

        if (!state.products.length) {
            cardBody.innerHTML = `
                <p class="text-muted text-center" style="padding: 40px 0;">
                    Click "Load Products" to view your products
                </p>
            `;
            return;
        }

        const html = `
            <div class="table-container">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Price</th>
                            <th>Compare At</th>
                            <th>Badge</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${state.products.map(p => `
                            <tr>
                                <td>
                                    <strong>${p.name}</strong>
                                    ${p.description ? `<br><small class="text-muted">${p.description}</small>` : ''}
                                </td>
                                <td class="font-mono">$${p.price.toFixed(2)}</td>
                                <td class="font-mono">${p.compare_at ? `$${p.compare_at.toFixed(2)}` : '-'}</td>
                                <td>${p.badge ? `<span class="badge info">${p.badge}</span>` : '-'}</td>
                                <td>
                                    <button class="btn btn-secondary btn-sm" onclick="AdminDashboard.editProduct('${p.id}')">Edit</button>
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        `;

        cardBody.innerHTML = html;
    }

    async function loadProducts() {
        saveConfig();
        
        if (!state.clientID) {
            alert('Please enter a Client ID first');
            switchTab('keys');
            return;
        }

        const offerKey = prompt('Enter offer key:', 'creatine-gummies');
        if (!offerKey) return;

        try {
            const data = await fetchOffer(state.clientID, offerKey);
            state.products = normalizeProducts(data, offerKey);
            state.offerKey = offerKey;
            localStorage.setItem('offerKey', offerKey);
            
            renderProductsTab();
            showMessage('.card-body', `Loaded ${state.products.length} products`, 'success');
        } catch (err) {
            alert('Failed to load products: ' + err.message);
        }
    }

    // ==================== OFFERS TAB ====================
    function renderOffersTab() {
        const tab = $('#offers-tab');
        if (!tab) return;

        const cardBody = tab.querySelector('.card-body');
        if (!cardBody) return;

        // Show offer configuration UI
        const html = `
            <div class="form-group">
                <label class="form-label">Offer Key</label>
                <input type="text" id="offerKeyInput" class="form-input" 
                       placeholder="creatine-gummies" value="${state.offerKey || ''}">
            </div>
            
            <div class="form-group">
                <label class="form-label">Client ID</label>
                <input type="text" id="offerClientID" class="form-input font-mono" 
                       placeholder="Enter client ID" value="${state.clientID || ''}">
            </div>

            <div class="form-group">
                <label class="form-label">Preview URL</label>
                <div class="flex gap-8">
                    <input type="text" id="offerPreviewUrl" class="form-input font-mono" readonly>
                    <button class="btn btn-secondary" onclick="AdminDashboard.copyOfferUrl()">Copy</button>
                    <button class="btn btn-primary" onclick="AdminDashboard.openOfferPreview()">Preview</button>
                </div>
            </div>

            <div style="margin-top: 16px; padding: 12px; background: var(--bg-hover); border-radius: var(--radius-md);">
                <strong>URLs will be generated as:</strong>
                <code class="font-mono" style="display: block; margin-top: 8px;">
                    ${window.location.origin}/offer/[offer-slug]?clientID=[client-id]
                </code>
            </div>
        `;

        cardBody.innerHTML = html;
        updateOfferPreviewUrl();
    }

    function updateOfferPreviewUrl() {
        const input = $('#offerPreviewUrl');
        const offerKey = $('#offerKeyInput')?.value || state.offerKey;
        const clientID = $('#offerClientID')?.value || state.clientID;
        
        if (input && offerKey && clientID) {
            const url = `${window.location.origin}/offer/${offerKey}?clientID=${clientID}&baseUrl=${state.baseUrl}`;
            input.value = url;
        }
    }

    function copyOfferUrl() {
        const input = $('#offerPreviewUrl');
        if (input) {
            input.select();
            document.execCommand('copy');
            showMessage('.card-body', 'URL copied to clipboard!', 'success');
        }
    }

    function openOfferPreview() {
        const url = $('#offerPreviewUrl')?.value;
        if (url) {
            window.open(url, '_blank');
        }
    }

    // ==================== MESSAGES ====================
    function showMessage(selector, message, type = 'info') {
        const container = $(selector);
        if (!container) return;

        const msgEl = document.createElement('div');
        msgEl.className = `badge ${type}`;
        msgEl.textContent = message;
        msgEl.style.marginTop = '12px';
        
        container.appendChild(msgEl);
        
        setTimeout(() => {
            msgEl.remove();
        }, 5000);
    }

    // ==================== DROPDOWNS ====================
    function handleDropdownToggle(e) {
        const trigger = e.target.closest('[data-dropdown]');
        if (!trigger) return;

        e.stopPropagation();
        
        const dropdown = trigger.closest('.dropdown');
        if (dropdown) {
            $$('.dropdown.open').forEach(d => {
                if (d !== dropdown) d.classList.remove('open');
            });
            dropdown.classList.toggle('open');
        }
    }

    function closeAllDropdowns() {
        $$('.dropdown.open').forEach(d => d.classList.remove('open'));
    }

    // ==================== EVENT BINDING ====================
    function bindEvents() {
        // Theme toggle - bind to ALL theme toggle buttons
        $$('.theme-toggle').forEach(btn => {
            btn.addEventListener('click', toggleTheme);
        });

        // Sidebar toggle
        const sidebarToggle = $('.sidebar-toggle');
        if (sidebarToggle) {
            sidebarToggle.addEventListener('click', toggleSidebar);
        }

        // Sidebar overlay
        const overlay = $('.sidebar-overlay');
        if (overlay) {
            overlay.addEventListener('click', closeMobileSidebar);
        }

        // Navigation
        const sidebarNav = $('.sidebar-nav');
        if (sidebarNav) {
            sidebarNav.addEventListener('click', handleNavClick);
        }

        // Login
        const loginBtn = $('#btnLogin');
        if (loginBtn) {
            loginBtn.addEventListener('click', handleLogin);
        }

        // Login tabs
        const loginTabs = $('.login-tabs');
        if (loginTabs) {
            loginTabs.addEventListener('click', handleLoginTabClick);
        }

        // Logout
        const logoutBtn = $('#btnSignOut');
        if (logoutBtn) {
            logoutBtn.addEventListener('click', handleLogout);
        }

        // Env toggle
        const envToggle = $('#envToggle');
        if (envToggle) {
            envToggle.addEventListener('change', toggleEnv);
        }

        // Products - Load button
        const loadProductsBtn = $('#products-tab .btn-secondary');
        if (loadProductsBtn) {
            loadProductsBtn.addEventListener('click', loadProducts);
        }

        // Offers - Input listeners
        document.addEventListener('input', (e) => {
            if (e.target.id === 'offerKeyInput' || e.target.id === 'offerClientID') {
                updateOfferPreviewUrl();
            }
        });

        // Dropdowns
        document.addEventListener('click', (e) => {
            const dropdown = e.target.closest('[data-dropdown]');
            if (dropdown) {
                handleDropdownToggle(e);
            } else {
                closeAllDropdowns();
            }
        });

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
                e.preventDefault();
                const searchInput = $('.search-input');
                if (searchInput) searchInput.focus();
            }
            
            if (e.key === 'Escape') {
                closeAllDropdowns();
                closeMobileSidebar();
            }
        });

        // Window resize
        window.addEventListener('resize', () => {
            if (window.innerWidth > 768) {
                closeMobileSidebar();
            }
        });
    }

    // ==================== START APP ====================
    document.addEventListener('DOMContentLoaded', init);

    // Wait for config to load, then set up auth
    window.addEventListener('configLoaded', function() {
        console.log('Config loaded, setting up auth...');
        
        // Set up all auth event listeners
        if (window.setupAuthListeners) {
            window.setupAuthListeners();
        }
        
        // Set up Stripe keys listeners
        if (window.setupStripeKeysListeners) {
            window.setupStripeKeysListeners();
        }
        
        // Check if user is already logged in
        if (window.initializeAuth) {
            window.initializeAuth();
        }
    });
    
    // Also set up if config is already loaded
    if (window.CONFIG && window.CONFIG.isLoaded) {
        console.log('Config already loaded, setting up auth immediately...');
        
        if (window.setupAuthListeners) {
            window.setupAuthListeners();
        }
        
        if (window.setupStripeKeysListeners) {
            window.setupStripeKeysListeners();
        }
        
        if (window.initializeAuth) {
            window.initializeAuth();
        }
    }

    // Expose functions globally
    window.AdminDashboard = {
        toggleTheme,
        toggleSidebar,
        switchTab,
        handleLogout,
        loadProducts,
        editProduct: (id) => console.log('Edit product:', id),
        copyOfferUrl,
        openOfferPreview
    };

})();