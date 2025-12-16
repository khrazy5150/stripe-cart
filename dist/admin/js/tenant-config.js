// ======== TENANT CONFIGURATION (EMAIL TEMPLATES) ========

const DEFAULT_MESSAGES = {
    just_for_you: "This exclusive offer is just for you! Take advantage of this special deal today.",
    order_fulfilled: "Great news! Your order has been shipped.\n\nTracking Number: {tracking_number}\nTrack your package: {tracking_url}\n\nThank you for your order!",
    refund: "Your refund has been processed successfully. Please allow 5-10 business days for the funds to appear in your account.\n\nIf you have any questions, please don't hesitate to contact us.",
    return_label: "A return label has been created for your order.\n\nReturn Tracking: {tracking_number}\nDownload Label: {label_url}\n\nPlease print the label and attach it to your package.",
    thank_you: "Thank you for your order! We truly appreciate your business.\n\nYour order is being processed and you'll receive a shipping confirmation soon."
};

// tenant-config.js - Handles SMS phone number configuration
// Add this to your existing tenant config script or create new file

let smsPhoneIti = null; // International Tel Input instance

// Initialize phone input with intl-tel-input
function initSmsPhoneInput() {
    const phoneInput = document.getElementById('smsNotificationPhone');
    if (!phoneInput || smsPhoneIti) return;
    
    // Wait for intl-tel-input library to load
    if (typeof window.intlTelInput === 'undefined') {
        setTimeout(initSmsPhoneInput, 100);
        return;
    }
    
    smsPhoneIti = window.intlTelInput(phoneInput, {
        utilsScript: "https://juniorbay.com/vendor/intl-tel-input/25.11.2/build/js/utils.js",
        preferredCountries: ['us', 'ca', 'gb', 'au'],
        nationalMode: false,
        autoPlaceholder: 'aggressive',
        formatOnDisplay: true,
        separateDialCode: true,
        initialCountry: 'us'
    });
}

// Load SMS phone number from tenant config
async function loadSmsPhone() {
    const clientID = window.currentClientID ? window.currentClientID() : null;
    if (!clientID) return;
    
    try {
        const result = await authedFetch(`/admin/tenant-config?clientID=${encodeURIComponent(clientID)}`, {
            method: 'GET'
        });
        
        if (result.ok && result.body) {
            // Check both root level and tenant_config for backward compatibility
            const smsPhone = result.body.sms_notification_phone 
                || result.body.tenant_config?.sms_notification_phone 
                || result.body.smsNotificationPhone;
            
            if (smsPhone) {
                const phoneInput = document.getElementById('smsNotificationPhone');
                if (phoneInput) {
                    phoneInput.value = smsPhone;
                    if (smsPhoneIti) {
                        smsPhoneIti.setNumber(smsPhone);
                    }
                }
            }
        }
    } catch (err) {
        console.error('Failed to load SMS phone:', err);
    }
}

// Save SMS phone number to tenant config
async function saveSmsPhone() {
    const clientID = window.currentClientID ? window.currentClientID() : null;
    if (!clientID) {
        showConfigError('Please select a client first');
        return;
    }
    
    const phoneInput = document.getElementById('smsNotificationPhone');
    const btn = document.getElementById('btnSaveSmsPhone');
    
    if (!phoneInput || !btn) return;
    
    // Get formatted phone number
    let phoneNumber = '';
    if (smsPhoneIti) {
        if (smsPhoneIti.isValidNumber()) {
            phoneNumber = smsPhoneIti.getNumber(); // E.164 format: +1234567890
        } else if (phoneInput.value.trim()) {
            showConfigError('Please enter a valid phone number');
            return;
        }
    } else {
        phoneNumber = phoneInput.value.trim();
        // Basic validation if intl-tel-input isn't loaded
        if (phoneNumber && !phoneNumber.match(/^\+?[1-9]\d{10,14}$/)) {
            showConfigError('Phone number must be in international format (e.g., +1234567890)');
            return;
        }
    }
    
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Saving...';
    
    try {
        const result = await authedFetch('/admin/tenant-config', {
            method: 'PUT',
            body: {
                clientID: clientID,
                sms_notification_phone: phoneNumber
            }
        });
        
        if (result.ok) {
            showConfigSuccess('✅ SMS notification phone saved!');
            console.log('SMS phone saved:', phoneNumber);
        } else {
            throw new Error(result.body?.error || 'Failed to save SMS phone');
        }
    } catch (err) {
        console.error('Save SMS phone error:', err);
        showConfigError('Failed to save SMS phone: ' + err.message);
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

// Helper functions for showing messages
function showConfigError(message) {
    const msgEl = document.getElementById('configMsg');
    if (msgEl) {
        msgEl.innerHTML = `<div class="message error">${escapeHtml(message)}</div>`;
        setTimeout(() => msgEl.innerHTML = '', 4000);
    }
}

function showConfigSuccess(message) {
    const msgEl = document.getElementById('configMsg');
    if (msgEl) {
        msgEl.innerHTML = `<div class="message success">${escapeHtml(message)}</div>`;
        setTimeout(() => msgEl.innerHTML = '', 3000);
    }
}

// Initialize when config tab is opened
function initConfigTab() {
    initSmsPhoneInput();
    loadSmsPhone();
}

// Setup event listeners
function setupSmsPhoneListeners() {
    const btnSave = document.getElementById('btnSaveSmsPhone');
    if (btnSave) {
        btnSave.addEventListener('click', saveSmsPhone);
    }
    
    // Initialize when switching to config tab
    const configTab = document.querySelector('[data-tab="config"]');
    if (configTab) {
        configTab.addEventListener('click', () => {
            setTimeout(initConfigTab, 100);
        });
    }
}

async function loadTenantConfig(clientID) {
    try {
        const res = await authedFetch(`/admin/tenant-config?clientID=${encodeURIComponent(clientID)}`);
        
        if (res.ok && res.body) {
            // Support both root level and tenant_config nested object
            const config = res.body.tenant_config || {};
            
            // Try root level first, then nested tenant_config
            document.getElementById('msgJustForYou').value = 
                res.body.just_for_you || config.just_for_you || '';
            document.getElementById('msgOrderFulfilled').value = 
                res.body.order_fulfilled || config.order_fulfilled || '';
            document.getElementById('msgRefund').value = 
                res.body.refund || config.refund || '';
            document.getElementById('msgReturnLabel').value = 
                res.body.return_label || config.return_label || '';
            document.getElementById('msgThankYou').value = 
                res.body.thank_you || config.thank_you || '';
            
            setMsg('configMsg', 'Configuration loaded', 'success');
        } else {
            loadAllDefaults();
            setMsg('configMsg', 'No saved configuration - defaults loaded', 'info');
        }
    } catch (error) {
        console.error('Error loading tenant config:', error);
        setMsg('configMsg', `Error: ${error.message}`, 'error');
    }
}

async function saveTenantConfig(clientID) {
    try {
        const config = {
            clientID: clientID,
            tenant_config: {
                just_for_you: document.getElementById('msgJustForYou').value.trim(),
                order_fulfilled: document.getElementById('msgOrderFulfilled').value.trim(),
                refund: document.getElementById('msgRefund').value.trim(),
                return_label: document.getElementById('msgReturnLabel').value.trim(),
                thank_you: document.getElementById('msgThankYou').value.trim()
            }
        };
        
        const res = await authedFetch('/admin/tenant-config', {
            method: 'PUT',
            body: config
        });
        
        if (res.ok) {
            setMsg('configMsg', 'Configuration saved successfully!', 'success');
        } else {
            setMsg('configMsg', `Save failed: ${res.body.error || 'Unknown error'}`, 'error');
        }
    } catch (error) {
        console.error('Error saving tenant config:', error);
        setMsg('configMsg', `Error: ${error.message}`, 'error');
    }
}

function loadDefaultMessage(messageType) {
    const fieldMap = {
        'just_for_you': 'msgJustForYou',
        'order_fulfilled': 'msgOrderFulfilled',
        'refund': 'msgRefund',
        'return_label': 'msgReturnLabel',
        'thank_you': 'msgThankYou'
    };
    
    const fieldId = fieldMap[messageType];
    if (fieldId) {
        document.getElementById(fieldId).value = DEFAULT_MESSAGES[messageType];
    }
}

function loadAllDefaults() {
    document.getElementById('msgJustForYou').value = DEFAULT_MESSAGES.just_for_you;
    document.getElementById('msgOrderFulfilled').value = DEFAULT_MESSAGES.order_fulfilled;
    document.getElementById('msgRefund').value = DEFAULT_MESSAGES.refund;
    document.getElementById('msgReturnLabel').value = DEFAULT_MESSAGES.return_label;
    document.getElementById('msgThankYou').value = DEFAULT_MESSAGES.thank_you;
}

function setupTenantConfigListeners() {
    const getCurrentClientID = () => {
        return window.currentClientID ? window.currentClientID() : document.getElementById('clientID').value.trim();
    };
    
    document.getElementById('btnDefaultJustForYou').addEventListener('click', () => loadDefaultMessage('just_for_you'));
    document.getElementById('btnSaveJustForYou').addEventListener('click', async () => {
        const clientID = getCurrentClientID();
        if (!clientID) return setMsg('configMsg', 'No client ID available', 'error');
        await saveTenantConfig(clientID);
    });

    document.getElementById('btnDefaultOrderFulfilled').addEventListener('click', () => loadDefaultMessage('order_fulfilled'));
    document.getElementById('btnSaveOrderFulfilled').addEventListener('click', async () => {
        const clientID = getCurrentClientID();
        if (!clientID) return setMsg('configMsg', 'No client ID available', 'error');
        await saveTenantConfig(clientID);
    });

    document.getElementById('btnDefaultRefund').addEventListener('click', () => loadDefaultMessage('refund'));
    document.getElementById('btnSaveRefund').addEventListener('click', async () => {
        const clientID = getCurrentClientID();
        if (!clientID) return setMsg('configMsg', 'No client ID available', 'error');
        await saveTenantConfig(clientID);
    });

    document.getElementById('btnDefaultReturnLabel').addEventListener('click', () => loadDefaultMessage('return_label'));
    document.getElementById('btnSaveReturnLabel').addEventListener('click', async () => {
        const clientID = getCurrentClientID();
        if (!clientID) return setMsg('configMsg', 'No client ID available', 'error');
        await saveTenantConfig(clientID);
    });

    document.getElementById('btnDefaultThankYou').addEventListener('click', () => loadDefaultMessage('thank_you'));
    document.getElementById('btnSaveThankYou').addEventListener('click', async () => {
        const clientID = getCurrentClientID();
        if (!clientID) return setMsg('configMsg', 'No client ID available', 'error');
        await saveTenantConfig(clientID);
    });

    document.getElementById('btnSaveAllConfig').addEventListener('click', async () => {
        const clientID = getCurrentClientID();
        if (!clientID) return setMsg('configMsg', 'No client ID available', 'error');
        await saveTenantConfig(clientID);
    });

    document.getElementById('btnLoadDefaultsAll').addEventListener('click', () => {
        loadAllDefaults();
        setMsg('configMsg', 'All defaults loaded (not saved yet)', 'info');
    });
}

// Export functions
window.initSmsPhoneInput = initSmsPhoneInput;
window.loadSmsPhone = loadSmsPhone;
window.saveSmsPhone = saveSmsPhone;
window.initConfigTab = initConfigTab;
window.setupSmsPhoneListeners = setupSmsPhoneListeners;
window.loadTenantConfig = loadTenantConfig;
window.setupTenantConfigListeners = setupTenantConfigListeners;

if (window.setupTenantConfigListeners) {
    window.setupTenantConfigListeners();
}

// Auto-initialize
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupSmsPhoneListeners);
} else {
    setupSmsPhoneListeners();
}