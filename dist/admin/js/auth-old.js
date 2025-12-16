// ======== AUTHENTICATION MODULE ========

// Global state
let currentClientID = null;
let itiInstance = null; // Store the intlTelInput instance

// ======== BUTTON SPINNER UTILITIES ========

function startSpin(el, loadMsg = '') {
    const originalText = el.innerHTML;
    el.disabled = true;
    el.innerHTML = '<span class="button-spinner"></span>';
    if (loadMsg.length > 0) {
        el.innerHTML += ` ${loadMsg}`;
    }
    return {
        element: el,
        innerHtml: originalText
    };
}

function stopSpin(spinObj) {
    spinObj.element.disabled = false;
    spinObj.element.innerHTML = spinObj.innerHtml;
}

// ======== UI STATE MANAGEMENT ========

function showMainApp() {
    document.getElementById('login-section').classList.add('hidden');
    document.getElementById('main-app').classList.remove('hidden');
}

function showLogin() {
    document.getElementById('login-section').classList.remove('hidden');
    document.getElementById('main-app').classList.add('hidden');
}

async function loadPhoneElement() {
    console.log('Loading phone element...');
    const phoneInput = document.getElementById('regPhone');

    itiInstance = window.intlTelInput(phoneInput, {
        initialCountry: "us",
        separateDialCode: true,
        nationalMode: true,
        autoPlaceholder: "aggressive",
        placeholderNumberType: "MOBILE",
        loadUtilsOnInit: false // because we loaded utils.js statically
    });

    // Wait for utils to load, but don't block if it fails
    await itiInstance.promise.catch(err => {
        console.warn('intl-tel-input utils failed to load:', err);
    });
    
    console.log('Phone element loaded successfully');
}

function switchAuthTab(tabName) {
    document.querySelectorAll('.auth-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.authTab === tabName);
    });
    document.querySelectorAll('.auth-view').forEach(view => {
        view.classList.toggle('active', view.id === `auth-${tabName}`);
    });
}

// ======== PHONE NUMBER UTILITIES ========

function normalizeIfInternational(iti, rawValue) {
    const cleaned = rawValue.replace(/[\s().-]/g, '');
    if (/^\+\d{4,15}$/.test(cleaned)) {
        // Let the lib parse +E.164, auto-sets the country and fills national part
        iti.setNumber(cleaned);
    }
}

function validateAndGetPhoneData(iti, phoneInput) {
    // Ensure we have the latest value
    const rawValue = phoneInput.value.trim();
    normalizeIfInternational(iti, rawValue);

    // Try official validation first (works only if utils actually loaded)
    let valid = false;
    try { 
        valid = iti.isValidNumber(); 
    } catch (err) {
        console.warn('isValidNumber() not available, using fallback validation');
    }

    if (!valid) {
        // Fallback: do a minimal sanity check so users aren't blocked if utils failed
        const { dialCode, iso2 } = iti.getSelectedCountryData() || {};
        const national = rawValue.replace(/[^\d]/g, '');
        // Crude per-country fallback; expand as needed
        const minLen = (iso2 === 'us' || iso2 === 'ca') ? 10 : 6;
        const ok = dialCode && national.length >= minLen;
        
        if (!ok) {
            return { valid: false, error: 'Please enter a valid phone number.' };
        }
    }

    // Get E.164 format for backend
    const e164 = iti.getNumber();
    const { dialCode, iso2, name } = iti.getSelectedCountryData() || {};
    const nationalDigits = rawValue.replace(/[^\d]/g, '');
    
    // Fallback if getNumber() fails
    const numberToSend = e164 || (dialCode ? `+${dialCode}${nationalDigits}` : '');

    // Get formatted national number
    let nationalFormatted = rawValue;
    try {
        if (window.intlTelInputUtils) {
            nationalFormatted = iti.getNumber(window.intlTelInputUtils.numberFormat.NATIONAL);
        }
    } catch (err) {
        console.warn('Could not get national format:', err);
    }

    return {
        valid: true,
        e164: numberToSend,
        national: nationalDigits,
        formatted: nationalFormatted,
        countryIso2: iso2,
        countryName: name,
        dialCode: dialCode
    };
}

// ======== REGISTRATION ========

async function handleRegister() {
    // Wait for the phone input to be ready
    if (!itiInstance) {
        return setMsg('regMsg', 'Phone input not initialized. Please refresh the page.', 'error');
    }

    await itiInstance.promise.catch(() => {}); // Continue even if utils failed

    const phoneInput = document.getElementById('regPhone');
    const email = document.getElementById('regEmail').value.trim();
    const pass = document.getElementById('regPass').value;
    const firstName = document.getElementById('regFirstName').value.trim();
    const lastName = document.getElementById('regLastName').value.trim();
    
    if (!email || !pass) {
        return setMsg('regMsg', 'Email and password required', 'error');
    }
    
    if (!firstName || !lastName) {
        return setMsg('regMsg', 'First name and last name required', 'error');
    }
    
    if (!phoneInput.value.trim()) {
        return setMsg('regMsg', 'Phone number required', 'error');
    }

    // Validate and get phone data
    const phoneData = validateAndGetPhoneData(itiInstance, phoneInput);
    
    if (!phoneData.valid) {
        return setMsg('regMsg', phoneData.error, 'error');
    }

    console.log('Phone data:', phoneData); // Debug log

    const attrs = [
        new window.Cognito.CognitoUserAttribute({ Name: 'email', Value: email }),
        new window.Cognito.CognitoUserAttribute({ Name: 'given_name', Value: firstName }),
        new window.Cognito.CognitoUserAttribute({ Name: 'family_name', Value: lastName }),
        new window.Cognito.CognitoUserAttribute({ Name: 'phone_number', Value: phoneData.e164 })
    ];
    
    const pool = window.cognitoPool();
    if (!pool) {
        return setMsg('regMsg', 'Authentication not initialized. Please refresh the page.', 'error');
    }
    
    pool.signUp(email, pass, attrs, [], (err, data) => {
        if (err) {
            return setMsg('regMsg', err.message || String(err), 'error');
        }
        setMsg('regMsg', 'Sign-up successful. Check your email for the verification code.', 'success');
        document.getElementById('confEmail').value = email;
    });
}

// ======== CONFIRMATION EMAIL ========

async function handleConfirmEmail() {
    const email = document.getElementById('confEmail').value.trim();
    const code = document.getElementById('confCode').value.trim();
    
    if (!email || !code) {
        return setMsg('regMsg', 'Email and code required', 'error');
    }

    const pool = window.cognitoPool();
    if (!pool) {
        return setMsg('regMsg', 'Authentication not initialized. Please refresh the page.', 'error');
    }

    const user = new window.Cognito.CognitoUser({ 
        Username: email, 
        Pool: pool 
    });
    
    user.confirmRegistration(code, true, (err, res) => {
        if (err) {
            return setMsg('regMsg', err.message || String(err), 'error');
        }
        setMsg('regMsg', 'Email confirmed. You can sign in now.', 'success');
    });
}

async function handleResendCode() {
    const email = document.getElementById('confEmail').value.trim();
    
    if (!email) {
        return setMsg('regMsg', 'Email required to resend', 'error');
    }
    
    const pool = window.cognitoPool();
    if (!pool) {
        return setMsg('regMsg', 'Authentication not initialized. Please refresh the page.', 'error');
    }
    
    const user = new window.Cognito.CognitoUser({ 
        Username: email, 
        Pool: pool 
    });
    
    user.resendConfirmationCode((err) => {
        if (err) {
            return setMsg('regMsg', err.message || String(err), 'error');
        }
        setMsg('regMsg', 'Verification code resent.', 'success');
    });
}

// ======== LOGIN ========

async function handleLogin() {
    const email = document.getElementById('loginEmail').value.trim();
    const pass = document.getElementById('loginPass').value;
    
    if (!email || !pass) {
        return setMsg('loginMsg', 'Email and password required', 'error');
    }

    const pool = window.cognitoPool();  // ✅ CALL IT AS A FUNCTION
    if (!pool) {
        return setMsg('loginMsg', 'Authentication not initialized. Please refresh the page.', 'error');
    }

    const btnLogin = document.getElementById('btnLogin');
    const res = startSpin(btnLogin, 'Signing in...');  // ✅ USE STARTSPIN

    const auth = new window.Cognito.AuthenticationDetails({  // ✅ USE window.Cognito
        Username: email, 
        Password: pass 
    });
    
    const user = new window.Cognito.CognitoUser({  // ✅ USE window.Cognito
        Username: email, 
        Pool: pool  // ✅ USE THE POOL VARIABLE
    });
    
    user.authenticateUser(auth, {
        onSuccess: (result) => {
            stopSpin(res);  // ✅ USE STOPSPIN
            setMsg('loginMsg', 'Successfully signed in!', 'success');
            setTimeout(async () => {
                const cu = getCurrentUser();
                const username = cu ? cu.getUsername() : 'Unknown';
                document.getElementById('who').textContent = username;
                showMainApp();
                
                // Load user's client data
                await loadUserClientData();
                
                // Initialize app components
                if (window.initializeApp) {
                    window.initializeApp();
                }
            }, 1000);
        },
        onFailure: (err) => {
            setMsg('loginMsg', err.message || String(err), 'error');
            stopSpin(res);  // ✅ USE STOPSPIN
        }
    });
}

// ======== PASSWORD RESET ========

async function handleForgotPassword() {
    const email = document.getElementById('fpEmail').value.trim();
    
    if (!email) {
        return setMsg('fpMsg', 'Enter your email', 'error');
    }
    
    const pool = window.cognitoPool();
    if (!pool) {
        return setMsg('fpMsg', 'Authentication not initialized. Please refresh the page.', 'error');
    }
    
    const user = new window.Cognito.CognitoUser({ 
        Username: email, 
        Pool: pool 
    });
    
    user.forgotPassword({
        onSuccess: () => {
            setMsg('fpMsg', 'Reset code sent to your email.', 'success');
            document.getElementById('fpConfEmail').value = email;
        },
        onFailure: (err) => {
            setMsg('fpMsg', err.message || String(err), 'error');
        }
    });
}

async function handleConfirmNewPassword() {
    const email = document.getElementById('fpConfEmail').value.trim();
    const code = document.getElementById('fpCode').value.trim();
    const pass = document.getElementById('fpNewPass').value;
    
    if (!email || !code || !pass) {
        return setMsg('fpMsg', 'All fields required', 'error');
    }
    
    const pool = window.cognitoPool();
    if (!pool) {
        return setMsg('fpMsg', 'Authentication not initialized. Please refresh the page.', 'error');
    }
    
    const user = new window.Cognito.CognitoUser({ 
        Username: email, 
        Pool: pool 
    });
    
    user.confirmPassword(code, pass, {
        onSuccess: () => {
            setMsg('fpMsg', 'Password updated! You can now sign in.', 'success');
            setTimeout(() => {
                switchAuthTab('login');
                document.getElementById('loginEmail').value = email;
            }, 1500);
        },
        onFailure: (err) => {
            setMsg('fpMsg', err.message || String(err), 'error');
        }
    });
}

// ======== SIGN OUT ========

function handleSignOut() {
    const cu = getCurrentUser();
    if (cu) cu.signOut();
    
    // Reset global state
    currentClientID = null;
    if (window.resetOrdersData) {
        window.resetOrdersData();
    }
    
    showLogin();
}

// ======== LOAD USER DATA ========

async function loadUserClientData() {
    try {
        const session = await getSession();
        const idToken = session.getIdToken();
        const cognitoClientID = idToken.payload.sub;

        console.log('Loading data for clientID:', cognitoClientID);
        
        currentClientID = cognitoClientID;
        document.getElementById('clientID').value = cognitoClientID;
        
        // Load all client configuration
        await Promise.all([
            window.loadStripeKeys ? window.loadStripeKeys(cognitoClientID) : Promise.resolve(),
            window.loadOffers ? window.loadOffers(cognitoClientID) : Promise.resolve(),
            window.loadShippingConfig ? window.loadShippingConfig(cognitoClientID) : Promise.resolve(),
            window.loadTenantConfig ? window.loadTenantConfig(cognitoClientID) : Promise.resolve()
        ]);
        
        // Reset orders data
        if (window.resetOrdersData) {
            window.resetOrdersData();
        }
        
    } catch (error) {
        console.error('Error loading user client data:', error);
        setMsg('apiMsg', 'Failed to load client data', 'error');
    }
}

// ======== INITIALIZATION ========

async function initializeAuth() {
    try {
        const session = await getSession();
        const cu = getCurrentUser();
        const username = cu ? cu.getUsername() : 'Unknown';
        document.getElementById('who').textContent = username;
        showMainApp();
        await loadUserClientData();
        
        if (window.initializeApp) {
            window.initializeApp();
        }
    } catch (error) {
        showLogin();
    }
}

// ======== EVENT LISTENERS ========

function setupAuthListeners() {
    // Auth tab switching
    document.querySelectorAll('.auth-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            switchAuthTab(tab.dataset.authTab);
        });
    });

    // Registration
    document.getElementById('btnRegister').addEventListener('click', handleRegister);
    document.getElementById('btnConfirm').addEventListener('click', handleConfirmEmail);
    document.getElementById('btnResend').addEventListener('click', handleResendCode);

    // Login
    document.getElementById('btnLogin').addEventListener('click', handleLogin);
    
    // Enter key support for login
    ['loginEmail', 'loginPass'].forEach(id => {
        document.getElementById(id).addEventListener('keypress', (e) => {
            if (e.key === 'Enter') handleLogin();
        });
    });

    // Password reset
    document.getElementById('btnForgot').addEventListener('click', handleForgotPassword);
    document.getElementById('btnConfirmNew').addEventListener('click', handleConfirmNewPassword);

    // Sign out
    document.getElementById('btnSignOut').addEventListener('click', handleSignOut);
}

async function getIdToken() {
    try {
        const session = await getSession();
        return session.getIdToken().getJwtToken();
    } catch (error) {
        console.error('Error getting ID token:', error);
        throw error;
    }
}

// Export functions
window.showMainApp = showMainApp;
window.showLogin = showLogin;
window.switchAuthTab = switchAuthTab;
window.loadUserClientData = loadUserClientData;
window.initializeAuth = initializeAuth;
window.setupAuthListeners = setupAuthListeners;
window.currentClientID = () => currentClientID;
window.setCurrentClientID = (id) => { currentClientID = id; };
window.getIdToken = getIdToken;
window.startSpin = startSpin;
window.stopSpin = stopSpin;

// Inject button spinner CSS
const spinnerStyles = `
.button-spinner {
    display: inline-block;
    width: 14px;
    height: 14px;
    border: 2px solid rgba(255, 255, 255, 0.3);
    border-top-color: #fff;
    border-radius: 50%;
    animation: button-spin 0.6s linear infinite;
}

@keyframes button-spin {
    to { transform: rotate(360deg); }
}

button:disabled {
    opacity: 0.7;
    cursor: not-allowed;
}

/* International Phone Input - Country Dropdown Styling */
.iti__country-name,
.iti__dial-code {
    color: #333 !important;
}

.iti__country {
    color: #333 !important;
}

.iti__selected-dial-code {
    color: #333 !important;
}

.iti__country:hover {
    background-color: #f0f0f0 !important;
}

.iti__country.iti__active {
    background-color: #e8e8e8 !important;
}
`;

// Inject styles on load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        const style = document.createElement('style');
        style.textContent = spinnerStyles;
        document.head.appendChild(style);
        loadPhoneElement();
    });
} else {
    const style = document.createElement('style');
    style.textContent = spinnerStyles;
    document.head.appendChild(style);
    loadPhoneElement();
}