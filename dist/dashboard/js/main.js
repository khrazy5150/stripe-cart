/**
 * Admin Dashboard - Main JavaScript
 * Integrated with existing auth, config, and module systems
 */

(function() {
    'use strict';

    const savedEnv = localStorage.getItem('env') || 'dev';

    const getEnvLabelText = (env) => (env === 'prod' ? 'Live' : 'Test');

    let slug = '';


    // ==================== STATE ====================
    const state = {
        currentTab: 'dashboard',
        theme: savedEnv === 'prod' ? 'dark' : 'light',
        sidebarCollapsed: localStorage.getItem('sidebarCollapsed') === 'true',
        env: savedEnv,
        isLoggedIn: localStorage.getItem('isLoggedIn') === 'true',
        user: JSON.parse(localStorage.getItem('user') || '{}'),
        stats: null,
        statsRangeDays: 30,
        statsLoading: false,
        recentTransactions: [],
        
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
        bindEvents();
        updateEnvBadge();
        fetchDashboardStats();
        
        // The existing auth.js will handle login/logout
        // We just need to set up our tab switching
    }

    // ==================== THEME ====================
    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        state.theme = theme;
        localStorage.setItem('theme', theme);
    }

    function deriveThemeFromEnv(env) {
        return env === 'prod' ? 'dark' : 'light';
    }

    function syncThemeWithEnv() {
        const desiredTheme = deriveThemeFromEnv(state.env);
        if (state.theme !== desiredTheme) {
            applyTheme(desiredTheme);
        }
    }

    function getCurrentClientId() {
        try {
            return typeof window.currentClientID === 'function' ? window.currentClientID() : null;
        } catch {
            return null;
        }
    }

    function setStatText(id, text) {
        const el = document.getElementById(id);
        if (el) {
            el.textContent = text;
        }
    }

    function formatNumber(value) {
        const num = typeof value === 'number' ? value : Number(value || 0);
        try {
            return new Intl.NumberFormat().format(num);
        } catch {
            return String(num);
        }
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

    function resetDashboardStats(message = 'No data available') {
        setStatText('statOrdersValue', '--');
        setStatText('statRevenueValue', '--');
        setStatText('statCustomersValue', '--');
        setStatText('statProductsValue', '--');

        setStatText('statOrdersMeta', message);
        setStatText('statRevenueMeta', message);
        setStatText('statCustomersMeta', message);
        setStatText('statProductsMeta', message);
        state.stats = null;
        setRecentOrdersPlaceholder(message);
    }

    function formatOrderDate(timestamp) {
        if (!timestamp) return '--';
        try {
            const date = new Date(timestamp * 1000);
            const month = date.toLocaleString('en-US', { month: 'short' });
            const day = date.getDate().toString().padStart(2, '0');
            const year = date.getFullYear();
            return `${month} ${day} ${year}`;
        } catch {
            return '--';
        }
    }

    function formatRiskScore(score) {
        if (score === null || score === undefined) return '—';
        if (typeof score === 'number' && Number.isFinite(score)) {
            return score.toFixed(0);
        }
        return String(score);
    }

    function setRecentOrdersPlaceholder(message) {
        const body = document.getElementById('recentOrdersBody');
        if (!body) return;
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 4;
        cell.className = 'text-muted';
        cell.textContent = message;
        row.appendChild(cell);
        body.innerHTML = '';
        body.appendChild(row);
    }

    function deriveRiskLevel(level, score) {
        const normalized = (level || '').toLowerCase();
        if (normalized) return normalized;

        const numeric = Number(score);
        if (!Number.isNaN(numeric)) {
            if (numeric >= 70) return 'highest';
            if (numeric >= 40) return 'elevated';
            return 'normal';
        }

        return 'unknown';
    }

    function formatRiskBadge(level, score) {
        const normalized = deriveRiskLevel(level, score);
        let label = normalized.charAt(0).toUpperCase() + normalized.slice(1);
        let cls = 'badge muted';

        if (normalized === 'normal') {
            cls = 'badge risk-normal';
        } else if (normalized === 'elevated') {
            cls = 'badge risk-elevated';
        } else if (normalized === 'highest') {
            cls = 'badge risk-highest';
        } else {
            label = 'Unknown';
        }

        if (score !== undefined && score !== null && score !== '' && !Number.isNaN(Number(score))) {
            label += ` (${formatRiskScore(score)})`;
        }

        return { cls, label };
    }

    function renderRecentTransactions(transactions) {
        const body = document.getElementById('recentOrdersBody');
        if (!body) return;

        body.innerHTML = '';

        if (!Array.isArray(transactions) || transactions.length === 0) {
            setRecentOrdersPlaceholder('No recent transactions');
            return;
        }

        transactions.slice(0, 10).forEach(tx => {
            const row = document.createElement('tr');

            const dateCell = document.createElement('td');
            dateCell.textContent = formatOrderDate(tx.created);

            const customerCell = document.createElement('td');
            customerCell.textContent = tx.customer || 'Unknown';

            const amountCell = document.createElement('td');
            amountCell.textContent = formatCurrency(tx.amount_cents || 0, tx.currency || 'USD');

            const riskCell = document.createElement('td');
            const { cls, label } = formatRiskBadge(tx.risk_level, tx.risk_score);
            const badge = document.createElement('span');
            badge.className = cls;
            badge.textContent = label;
            riskCell.appendChild(badge);

            row.appendChild(dateCell);
            row.appendChild(customerCell);
            row.appendChild(amountCell);
            row.appendChild(riskCell);

            body.appendChild(row);
        });
    }

    function renderDashboardStats(payload) {
        if (!payload || !payload.stats) {
            resetDashboardStats('No recent Stripe data');
            return;
        }

        const stats = payload.stats;
        const modeLabel = payload.mode === 'live' ? 'Live' : 'Test';
        const rangeLabel = `Last ${payload.range_days || state.statsRangeDays} days • ${modeLabel}`;

        setStatText('statOrdersValue', formatNumber(stats.orders || 0));
        setStatText('statRevenueValue', formatCurrency(stats.revenue_cents || 0, stats.currency));
        setStatText('statCustomersValue', formatNumber(stats.customers || 0));
        setStatText('statProductsValue', formatNumber(stats.products || 0));

        setStatText('statOrdersMeta', rangeLabel);
        setStatText('statRevenueMeta', rangeLabel);
        setStatText('statCustomersMeta', rangeLabel);
        setStatText('statProductsMeta', `Active Currently * ${modeLabel}`);
    }

    async function fetchDashboardStats() {
        const clientID = getCurrentClientId();
        const fetcher = window.authedFetch;

        if (!clientID || typeof fetcher !== 'function') {
            return;
        }

        const mode = state.env === 'prod' ? 'live' : 'test';
        const params = new URLSearchParams({
            clientID,
            mode,
            rangeDays: String(state.statsRangeDays)
        });

        state.statsLoading = true;
        resetDashboardStats(`Loading ${mode === 'live' ? 'Live' : 'Test'} stats…`);
        setRecentOrdersPlaceholder('Loading transactions…');

        try {
            const res = await fetcher(`/admin/stats?${params.toString()}`);
            if (!res.ok) {
                const errMsg = (res.body && (res.body.error || res.body.message)) || `HTTP ${res.status}`;
                if (typeof errMsg === 'string' && errMsg.toLowerCase().includes('no stripe keys')) {
                    resetDashboardStats('Connect your Stripe keys to view stats');
                    setRecentOrdersPlaceholder('Connect your Stripe keys to view transactions');
                    return;
                }
                throw new Error(errMsg);
            }
            state.stats = res.body;
            renderDashboardStats(res.body);

             const recent = Array.isArray(res.body?.recent_transactions) ? res.body.recent_transactions : [];
             state.recentTransactions = recent;
             renderRecentTransactions(recent);
        } catch (err) {
            console.error('Failed to load dashboard stats', err);
            resetDashboardStats('Unable to load Stripe stats');
            setRecentOrdersPlaceholder('Unable to load transactions');
        } finally {
            state.statsLoading = false;
        }
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
                if (window.ProductsDashboard && typeof window.ProductsDashboard.render === 'function') {
                    window.ProductsDashboard.render();
                }
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

    function handleTabSwitch(tabId) {
        console.log('handleTabSwitch:', tabId);
        
        const clientID = window.currentClientID ? window.currentClientID() : null;
        
        switch(tabId) {
            case 'keys':
                // Stripe Keys - auto-load if we have clientID
                if (clientID && window.loadStripeKeys) {
                    console.log('Auto-loading Stripe keys for:', clientID);
                    window.loadStripeKeys(clientID);
                }
                break;
                
            case 'products':
                if (window.initProducts) {
                    window.initProducts();
                }
                break;
                
            case 'offers':
                // Initialize the new OffersDashboard system
                if (window.OffersDashboard && typeof window.OffersDashboard.init === 'function') {
                    window.OffersDashboard.init();
                }
                break;
                
            case 'orders':
                if (window.setupOrdersListeners) {
                    window.setupOrdersListeners();
                }
                if (clientID && window.loadOrdersForClient) {
                    window.loadOrdersForClient();
                }
                break;
                
            case 'landing-pages':
                if (window.initLandingPages) {
                    window.initLandingPages();
                }
                break;
                
            case 'shipping':
                if (clientID && window.loadShippingConfig) {
                    window.loadShippingConfig(clientID);
                }
                break;
                
            case 'config':
                if (clientID && window.loadTenantConfig) {
                    window.loadTenantConfig(clientID);
                }
                break;
        }
    }

    // ==================== ENV TOGGLE ====================
    async function toggleEnv() {
        const nextEnv = state.env === 'dev' ? 'prod' : 'dev';

        if (typeof window.setEnvironment === 'function') {
            try {
                await window.setEnvironment(nextEnv);
                renderOffersTab();
                return;
            } catch (error) {
                console.error('Failed to switch environment via setEnvironment:', error);
                window.location.reload();
                return;
            }
        }

        const offerUrl = document.getElementById('offer-url');

        state.env = nextEnv;
        localStorage.setItem('env', state.env);
        updateEnvBadge();
        
        document.body.classList.remove('env-dev', 'env-prod');
        document.body.classList.add(`env-${state.env}`);
        
        updateBaseUrl();
        syncThemeWithEnv();
        fetchDashboardStats();
    }

    function updateEnvBadge() {
        const envLabel = $('#envLabel');
        const envToggle = $('#envToggle');
        
        if (envLabel) {
            envLabel.textContent = getEnvLabelText(state.env);
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
            if (window.ProductsDashboard && typeof window.ProductsDashboard.resetState === 'function') {
                window.ProductsDashboard.resetState({ keepFilters: true, silent: true });
            }
            if (window.ProductsDashboard && typeof window.ProductsDashboard.render === 'function') {
                window.ProductsDashboard.render();
            }
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

    // ==================== OFFERS TAB ====================
    function renderOffersTab() {
        const tab = $('#offers-tab');
        if (!tab) return;

        const cardBody = tab.querySelector('.card-body');
        if (!cardBody) return;

        const html = `
            <div id="offersListMessage"></div>
            <div id="offersListContainer" class="offer-card-grid empty">
                <div class="text-muted text-center" style="padding: 40px 0;">Click "Load Offers" to view your offers.</div>
            </div>
        `;

        cardBody.innerHTML = html;
        if (window.OffersDashboard && typeof window.OffersDashboard.init === 'function') {
            window.OffersDashboard.init();
        }
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

    function createSlug(text) {
        if (!text) {
            return '';
        }

        // Convert to lowercase
        let slug = text.toLowerCase();

        // Replace multiple spaces with a single space
        slug = slug.replace(/\s+/g, ' ');

        // Trim leading/trailing whitespace
        slug = slug.trim();

        // Replace all spaces with a dash
        slug = slug.replace(/ /g, '-');

        return slug;
    }

    function bindOfferSlugInput() {
        const offerKeyInput = $('#offerKeyInput');
        const slugDisplay = $('#offerSlugDisplay');
        if (!offerKeyInput || !slugDisplay) return;

        const updateSlugDisplay = () => {
            const rawInput = offerKeyInput.value;
            const slug = createSlug(rawInput);
            state.offerKey = slug;

            const baseApi = (window.CONFIG && window.CONFIG.apiBase) || state.baseUrl || window.location.origin;
            const clientID = $('#offerClientID')?.value || state.clientID || localStorage.getItem('clientID') || '';
            const safeBase = (baseApi || '').replace(/\/+$/, '');
            const displaySlug = slug || '[offer-slug]';
            const query = clientID ? `?clientID=${clientID}` : '';
            slugDisplay.textContent = `${safeBase}/offer/${displaySlug}${query}`;

            updateOfferPreviewUrl();
        };

        if (offerKeyInput._slugHandler) {
            offerKeyInput.removeEventListener('input', offerKeyInput._slugHandler);
        }
        offerKeyInput._slugHandler = updateSlugDisplay;
        offerKeyInput.addEventListener('input', updateSlugDisplay);
        updateSlugDisplay();
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
        // const logoutBtn = $('#btnSignOut');
        // if (logoutBtn) {
        //     logoutBtn.addEventListener('click', handleLogout);
        // }

        // Env toggle
        const envToggle = $('#envToggle');
        if (envToggle) {
            envToggle.addEventListener('change', toggleEnv);
        }

        const viewAllOrdersBtn = $('#btnViewAllOrders');
        if (viewAllOrdersBtn) {
            viewAllOrdersBtn.addEventListener('click', () => {
                switchTab('orders');
                handleTabSwitch('orders');
            });
        }

        const closeActivityBtn = $('#btnCloseActivity');
        if (closeActivityBtn) {
            closeActivityBtn.addEventListener('click', () => {
                const card = closeActivityBtn.closest('.card');
                if (card) card.remove();
                const contentGrid = document.querySelector('.content-grid');
                if (contentGrid) {
                    contentGrid.classList.add('single-column');
                }
            });
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

    window.addEventListener('environmentChanged', (event) => {
        const nextEnv = event?.detail?.env;
        const nextConfig = event?.detail?.config;

        if (nextConfig && typeof window.CONFIG === 'object') {
            window.CONFIG = nextConfig;
        }

        if (nextEnv && nextEnv !== state.env) {
            state.env = nextEnv;
            localStorage.setItem('env', state.env);
            updateEnvBadge();
            updateBaseUrl();
        }
        syncThemeWithEnv();
        fetchDashboardStats();
    });

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
    window.refreshDashboardStats = fetchDashboardStats;
    window.resetDashboardStats = resetDashboardStats;

    window.AdminDashboard = {
        toggleSidebar,
        switchTab,
        editProduct: (id) => console.log('Edit product:', id),
        copyOfferUrl,
        openOfferPreview,
        showMessage,
        getClientId: () => state.clientID,
        setClientId: (value) => { state.clientID = value; }
    };

})();
