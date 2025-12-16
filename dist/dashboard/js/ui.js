/**
 * Admin Dashboard - Main JavaScript
 * Plain vanilla JS - no frameworks
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
        user: JSON.parse(localStorage.getItem('user') || '{}')
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
    }

    function toggleTheme() {
        const newTheme = state.theme === 'light' ? 'dark' : 'light';
        applyTheme(newTheme);
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

        // Mobile handling
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
            // Handle tab switching
            const tabId = navLink.dataset.tab;
            if (tabId) {
                e.preventDefault();
                switchTab(tabId);
                
                // Update active state
                $$('.nav-link').forEach(link => link.classList.remove('active'));
                navLink.classList.add('active');

                // Close mobile sidebar after navigation
                if (window.innerWidth <= 768) {
                    closeMobileSidebar();
                }
            }
        }
    }

    function handleSubmenuClick(e) {
        const submenuLink = e.target.closest('.submenu-link');
        if (!submenuLink) return;

        const tabId = submenuLink.dataset.tab;
        if (tabId) {
            e.preventDefault();
            switchTab(tabId);

            // Update active states
            $$('.submenu-link').forEach(link => link.classList.remove('active'));
            submenuLink.classList.add('active');

            // Also update parent nav-link
            const parentNavItem = submenuLink.closest('.nav-item');
            if (parentNavItem) {
                $$('.nav-link').forEach(link => link.classList.remove('active'));
                const parentNavLink = parentNavItem.querySelector('.nav-link');
                if (parentNavLink) parentNavLink.classList.add('active');
            }

            if (window.innerWidth <= 768) {
                closeMobileSidebar();
            }
        }
    }

    function switchTab(tabId) {
        // Hide all tab contents
        $$('.tab-content').forEach(tab => tab.classList.remove('active'));
        
        // Show selected tab
        const targetTab = $(`#${tabId}-tab`);
        if (targetTab) {
            targetTab.classList.add('active');
            state.currentTab = tabId;
            
            // Update page title
            updatePageHeader(tabId);
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
            showMessage('#loginMsg', 'Please fill in all fields', 'error');
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
        const whoSpan = $('#who');

        if (userName && state.user.name) {
            userName.textContent = state.user.name;
        }
        if (userAvatar && state.user.name) {
            userAvatar.textContent = state.user.name.charAt(0).toUpperCase();
        }
        if (whoSpan && state.user.email) {
            whoSpan.textContent = state.user.email;
        }
    }

    // ==================== LOGIN TABS ====================
    function handleLoginTabClick(e) {
        const tab = e.target.closest('.login-tab');
        if (!tab) return;

        const tabId = tab.dataset.authTab;
        if (!tabId) return;

        // Update tab active states
        $$('.login-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');

        // Show corresponding view
        $$('.login-view').forEach(v => v.classList.remove('active'));
        const targetView = $(`#auth-${tabId}`);
        if (targetView) targetView.classList.add('active');
    }

    // ==================== ENV TOGGLE ====================
    function toggleEnv() {
        state.env = state.env === 'dev' ? 'prod' : 'dev';
        localStorage.setItem('env', state.env);
        updateEnvBadge();
        
        // Add/remove env class from body
        document.body.classList.remove('env-dev', 'env-prod');
        document.body.classList.add(`env-${state.env}`);
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

    // ==================== MESSAGES ====================
    function showMessage(selector, message, type = 'info') {
        const el = $(selector);
        if (!el) return;

        el.textContent = message;
        el.className = `msg msg--${type}`;
        
        setTimeout(() => {
            el.textContent = '';
            el.className = '';
        }, 5000);
    }

    // ==================== DROPDOWNS ====================
    function handleDropdownToggle(e) {
        const trigger = e.target.closest('[data-dropdown]');
        if (!trigger) return;

        e.stopPropagation();
        
        const dropdown = trigger.closest('.dropdown');
        if (dropdown) {
            // Close other dropdowns
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
        // Theme toggle
        const themeToggle = $$('.theme-toggle');
        if (themeToggle) {
            themeToggle.addEventListener('click', toggleTheme);
        }

        // Sidebar toggle
        const sidebarToggle = $('.sidebar-toggle');
        if (sidebarToggle) {
            sidebarToggle.addEventListener('click', toggleSidebar);
        }

        // Sidebar overlay (mobile)
        const overlay = $('.sidebar-overlay');
        if (overlay) {
            overlay.addEventListener('click', closeMobileSidebar);
        }

        // Navigation clicks
        const sidebarNav = $('.sidebar-nav');
        if (sidebarNav) {
            sidebarNav.addEventListener('click', handleNavClick);
            sidebarNav.addEventListener('click', handleSubmenuClick);
        }

        // Login form
        const loginBtn = $('#btnLogin');
        if (loginBtn) {
            loginBtn.addEventListener('click', handleLogin);
        }

        // Login tabs
        const loginTabs = $('.login-tabs');
        if (loginTabs) {
            loginTabs.addEventListener('click', handleLoginTabClick);
        }

        // Logout button
        const logoutBtn = $('#btnSignOut');
        if (logoutBtn) {
            logoutBtn.addEventListener('click', handleLogout);
        }

        // Env toggle
        const envToggle = $('#envToggle');
        if (envToggle) {
            envToggle.addEventListener('change', toggleEnv);
        }

        // Dropdown handling
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
            // Ctrl/Cmd + K for search focus
            if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
                e.preventDefault();
                const searchInput = $('.search-input');
                if (searchInput) searchInput.focus();
            }
            
            // Escape to close dropdowns/mobile sidebar
            if (e.key === 'Escape') {
                closeAllDropdowns();
                closeMobileSidebar();
            }
        });

        // Window resize handling
        window.addEventListener('resize', () => {
            if (window.innerWidth > 768) {
                closeMobileSidebar();
            }
        });
    }

    // ==================== START APP ====================
    document.addEventListener('DOMContentLoaded', init);

    // Expose some functions globally for inline handlers if needed
    window.AdminDashboard = {
        toggleTheme,
        toggleSidebar,
        switchTab,
        handleLogout
    };

})();
