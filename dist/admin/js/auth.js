// ======== AUTHENTICATION MODULE ========
// --- Message helpers (polyfill) --------------------------------------------
// Creates simple status messages inside an element by id (e.g., 'regMsg').
// Safe: only defines if not already provided elsewhere.
(function () {
  if (typeof window.setMsg !== 'function') {
    window.setMsg = function setMsg(targetId, text, type = 'info') {
      const host = document.getElementById(targetId);
      if (!host) return;
      // ensure a message container exists
      let box = host.querySelector('.message');
      if (!box) {
        box = document.createElement('div');
        box.className = 'message';
        host.appendChild(box);
      }
      box.className = `message ${type}`; // expects .message.error / .message.success in CSS
      box.textContent = text || '';
      box.style.display = text ? '' : 'none';
    };
  }

  if (typeof window.clearMsg !== 'function') {
    window.clearMsg = function clearMsg(targetId) {
      const host = document.getElementById(targetId);
      if (!host) return;
      const box = host.querySelector('.message');
      if (box) {
        box.textContent = '';
        box.style.display = 'none';
        box.className = 'message';
      }
    };
  }
})();

// --- Global message helper expected by auth.js (safe if already defined) ---
(function () {
  if (typeof window.setMsg === "function") return;

  /**
   * setMsg(targetId, text, kind?)
   *    targetId: element id to write to (e.g., 'loginMsg', 'regMsg', 'fpMsg', 'apiMsg')
   *    text: message content
   *    kind: 'error' | 'success' | 'info' (defaults to 'info')
   */
  window.setMsg = function setMsg(targetId, text, kind = 'info') {
    const id = String(targetId || '').trim() || 'loginMsg';
    let el = document.getElementById(id);

    // Create the container if it doesn't exist (keeps HTML uncluttered)
    if (!el) {
      el = document.createElement('div');
      el.id = id;
      el.setAttribute('role', 'status');
      el.setAttribute('aria-live', 'polite');
      el.className = 'msg';
      // Try to place near login form; fallback to body
      (document.getElementById('login-section') || document.body).appendChild(el);
    }

    const base = 'msg';
    const cls =
      kind === 'error'   ? `${base} ${base}--error` :
      kind === 'success' ? `${base} ${base}--success` :
                           `${base}`;
    el.className = cls;
    el.textContent = String(text ?? '');
  };
})();

// ======== COGNITO HELPERS ========

function getCurrentUser() {
  try {
    const pool = typeof window.cognitoPool === 'function' ? window.cognitoPool() : null;
    return pool ? pool.getCurrentUser() : null;
  } catch {
    return null;
  }
}

function getSession() {
  return new Promise((resolve, reject) => {
    const cu = getCurrentUser();
    if (!cu) return reject(new Error('No current user'));
    cu.getSession((err, session) => {
      if (err) return reject(err);
      if (!session || !session.isValid()) return reject(new Error('Invalid session'));
      resolve(session);
    });
  });
}

// Expose for other modules
window.getCurrentUser = getCurrentUser;
window.getSession = getSession;

// ======== GLOBAL STATE ========

let currentClientID = null;
let itiInstance = null; // intl-tel-input instance

// ======== UTILS (NEW) ========

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/**
 * authedFetch(pathOrUrl: string, options?: { method?: string, headers?: object, body?: any })
 * - Prefixes relative paths with CONFIG.apiBase
 * - Adds Authorization: Bearer <idToken>
 * - JSON encodes body by default (unless FormData / Blob / string)
 * - Returns { ok, status, headers, body }
 */
async function authedFetch(pathOrUrl, options = {}) {
  const cfg = window.CONFIG || {};
  const base = cfg.apiBase || '';

  // Build URL
  const isAbsolute = /^https?:\/\//i.test(pathOrUrl);
  const url = isAbsolute ? pathOrUrl : (base.replace(/\/+$/,'') + '/' + String(pathOrUrl).replace(/^\/+/, ''));

  // Method & headers
  const method = (options.method || 'GET').toUpperCase();
  const headers = new Headers(options.headers || {});

  // Add auth header
  const token = await (async () => {
    try { return await getIdToken(); } catch { return null; }
  })();

  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  let body = options.body;

  // JSON encode plain objects
  const isBodyAllowed = method !== 'GET' && method !== 'HEAD';
  const looksLikePlainObject = body && typeof body === 'object' && !(body instanceof FormData) && !(body instanceof Blob) && !(body instanceof ArrayBuffer);
  if (isBodyAllowed && looksLikePlainObject) {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(body);
  }

  // Fire request
  const resp = await fetch(url, {
    method,
    headers,
    body: isBodyAllowed ? body : undefined,
    // credentials: 'include', // enable if your API requires cookies
  });

  // Parse body safely
  const ct = resp.headers.get('content-type') || '';
  let parsed;
  try {
    if (ct.includes('application/json')) {
      parsed = await resp.json();
    } else {
      parsed = await resp.text();
    }
  } catch {
    parsed = null;
  }

  return {
    ok: resp.ok,
    status: resp.status,
    headers: resp.headers,
    body: parsed
  };
}

// Expose globally for other modules (e.g., stripe-keys.js)
window.authedFetch = authedFetch;

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

// --- Generic button loading helpers (reuses your login style if present)
function startBtnLoading(btn, loadingText) {
  if (!btn) return () => {};
  // if we already started, return a no-op stopper
  if (btn.dataset.loading === "1") return () => {};

  btn.dataset.loading = "1";
  btn.dataset.originalText = btn.dataset.originalText || btn.textContent;

  // Prefer existing login styling if your CSS uses a class like "loading" or "is-loading"
  btn.classList.add('loading'); // harmless if your login button uses this
  btn.disabled = true;

  if (loadingText) {
    btn.textContent = loadingText;
  }

  // Return stopper so callers can do: const stop = startBtnLoading(...); try {..} finally { stop(); }
  return function stop() {
    // Only stop if we still think we're loading
    if (btn.dataset.loading !== "1") return;
    btn.dataset.loading = "0";
    btn.disabled = false;
    btn.classList.remove('loading');
    btn.textContent = btn.dataset.originalText || btn.textContent;
  };
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
  if (tabName === 'register') {
    setRegistrationStage('profile');
  }
  document.querySelectorAll('.auth-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.authTab === tabName);
  });
  document.querySelectorAll('.auth-view').forEach(view => {
    view.classList.toggle('active', view.id === `auth-${tabName}`);
  });
}

// --- Registration stage toggles: 'profile' | 'confirm' | 'done'
function setRegistrationStage(stage) {
  const idsProfile = ['regFirstName','regLastName','regEmail','regPass','regPhone','btnRegister'];
  const idsConfirm = ['confEmail','confCode','btnConfirm','btnResend'];

  const show = (ids, on) => ids.forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      // hide the row/container that holds the element if possible
      const row = el.closest('.form-row') || el.closest('.row') || el;
      row.style.display = on ? '' : 'none';
    }
  });

  if (stage === 'profile') {
    show(idsProfile, true);
    show(idsConfirm, false);
  } else if (stage === 'confirm') {
    show(idsProfile, false);
    show(idsConfirm, true);
  } else { // done
    show(idsProfile, false);
    show(idsConfirm, false);
  }
}

// Call once at startup to ensure the right initial state when the Register tab is opened
function initRegistrationView() {
  // default to profile form hidden/visible arrangement
  setRegistrationStage('profile');
}

// ======== PHONE NUMBER UTILITIES ========

function normalizeIfInternational(iti, rawValue) {
  const cleaned = rawValue.replace(/[\s().-]/g, '');
  if (/^\+\d{4,15}$/.test(cleaned)) {
    iti.setNumber(cleaned);
  }
}

function validateAndGetPhoneData(iti, phoneInput) {
  const rawValue = phoneInput.value.trim();
  normalizeIfInternational(iti, rawValue);

  let valid = false;
  try {
    valid = iti.isValidNumber();
  } catch (err) {
    console.warn('isValidNumber() not available, using fallback validation');
  }

  if (!valid) {
    const { dialCode, iso2 } = iti.getSelectedCountryData() || {};
    const national = rawValue.replace(/[^\d]/g, '');
    const minLen = (iso2 === 'us' || iso2 === 'ca') ? 10 : 6;
    const ok = dialCode && national.length >= minLen;

    if (!ok) {
      return { valid: false, error: 'Please enter a valid phone number.' };
    }
  }

  const e164 = iti.getNumber();
  const { dialCode, iso2, name } = iti.getSelectedCountryData() || {};
  const nationalDigits = rawValue.replace(/[^\d]/g, '');
  const numberToSend = e164 || (dialCode ? `+${dialCode}${nationalDigits}` : '');

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
  const first = (document.getElementById('regFirstName') || {}).value || '';
  const last  = (document.getElementById('regLastName')  || {}).value || '';
  const email = (document.getElementById('regEmail')     || {}).value || '';
  const pass  = (document.getElementById('regPass')      || {}).value || '';
  const phoneEl = document.getElementById('regPhone');

  const btn = document.getElementById('btnRegister');
  const stop = startBtnLoading(btn, 'Creating...');

  try {
    clearMsg('regMsg');

    if (!first || !last || !email || !pass) {
      setMsg('regMsg', 'Please fill in all required fields.', 'error');
      return;
    }

    // Resolve Cognito pool
    const pool = window.cognitoPool && window.cognitoPool();
    if (!pool) {
      setMsg('regMsg', 'Configuration not loaded. Try again.', 'error');
      return;
    }

    // Build attributes
    const attrList = [];
    const Cognito = window.Cognito;
    attrList.push(new Cognito.CognitoUserAttribute({ Name: 'given_name',  Value: first }));
    attrList.push(new Cognito.CognitoUserAttribute({ Name: 'family_name', Value: last  }));

    // Phone: validate + format to E.164 (e.g., +15551234567). Omit if blank.
    if (phoneEl && phoneEl.value.trim()) {
      if (!itiInstance) {
        // best effort init if not yet initialized
        try { await loadPhoneElement(); } catch (_) {}
      }
      const res = validateAndGetPhoneData(itiInstance, phoneEl); // uses intl-tel-input if available
      const e164 = (res && res.valid && res.e164) ? res.e164 : '';

      if (!/^\+\d{6,15}$/.test(e164)) {
        setMsg('regMsg', 'Please enter a valid phone number (include country code).', 'error');
        return;
      }
      attrList.push(new Cognito.CognitoUserAttribute({ Name: 'phone_number', Value: e164 }));
    }

    // Sign up
    await new Promise((resolve, reject) => {
      pool.signUp(email, pass, attrList, null, (err, result) => {
        if (err) return reject(err);
        resolve(result);
      });
    });

    setMsg('regMsg', 'Sign-up successful. Check your email for the verification code.', 'success');

    // Move to confirmation step
    const confEmail = document.getElementById('confEmail');
    if (confEmail) confEmail.value = email;
    if (typeof setRegistrationStage === 'function') setRegistrationStage('confirm');
    const confirmField = document.getElementById('confEmail');
    if (confirmField && confirmField.scrollIntoView) confirmField.scrollIntoView({ behavior: 'smooth', block: 'center' });

  } catch (err) {
    console.error('Register error:', err);
    setMsg('regMsg', err.message || 'Registration failed.', 'error');
  } finally {
    stop();
  }
}

// ======== CONFIRMATION EMAIL ========

async function handleConfirmEmail() {
  const email = (document.getElementById('confEmail') || {}).value || '';
  const code  = (document.getElementById('confCode')  || {}).value || '';

  const btn = document.getElementById('btnConfirm');
  const stop = startBtnLoading(btn, 'Confirming...');

  try {
    clearMsg('regMsg');

    if (!email || !code) {
      setMsg('regMsg', 'Please enter your email and the verification code.', 'error');
      return;
    }

    const pool = window.cognitoPool && window.cognitoPool();
    if (!pool) {
      setMsg('regMsg', 'Configuration not loaded. Try again.', 'error');
      return;
    }

    const cognitoUser = new window.Cognito.CognitoUser({ Username: email, Pool: pool });
    await new Promise((resolve, reject) => {
      cognitoUser.confirmRegistration(code, true, (err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    });

    setMsg('regMsg', 'Email confirmed. You can sign in now.', 'success');

    // Hide register forms, switch to login, pre-fill email
    if (typeof setRegistrationStage === 'function') setRegistrationStage('done');
    if (typeof switchAuthTab === 'function') switchAuthTab('login');
    const loginEmail = document.getElementById('loginEmail');
    if (loginEmail) loginEmail.value = email;
    const loginPass = document.getElementById('loginPass');
    if (loginPass) loginPass.focus();

  } catch (err) {
    console.error('Confirm email error:', err);
    setMsg('regMsg', err.message || 'Confirmation failed.', 'error');
  } finally {
    stop();
  }
}

async function handleResendCode() {
  const email = (document.getElementById('confEmail') || {}).value || '';

  const btn = document.getElementById('btnResend');
  const stop = startBtnLoading(btn, 'Resending...');

  try {
    clearMsg('regMsg');

    if (!email) {
      setMsg('regMsg', 'Enter your email first.', 'error');
      return;
    }

    const pool = window.cognitoPool && window.cognitoPool();
    if (!pool) {
      setMsg('regMsg', 'Configuration not loaded. Try again.', 'error');
      return;
    }

    const cognitoUser = new window.Cognito.CognitoUser({ Username: email, Pool: pool });
    await new Promise((resolve, reject) => {
      cognitoUser.resendConfirmationCode((err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    });

    // Keep user on confirm step, but show success
    if (typeof setRegistrationStage === 'function') setRegistrationStage('confirm');
    setMsg('regMsg', 'A new verification code has been sent.', 'success');

  } catch (err) {
    console.error('Resend code error:', err);
    setMsg('regMsg', err.message || 'Resend failed.', 'error');
  } finally {
    stop();
  }
}

// ======== LOGIN ========

async function handleLogin() {
  const email = document.getElementById('loginEmail').value.trim();
  const pass = document.getElementById('loginPass').value;

  if (!email || !pass) {
    return setMsg('loginMsg', 'Email and password required', 'error');
  }

  const pool = window.cognitoPool();
  if (!pool) {
    return setMsg('loginMsg', 'Authentication not initialized. Please refresh the page.', 'error');
  }

  const btnLogin = document.getElementById('btnLogin');
  const res = startSpin(btnLogin, 'Signing in...');

  const auth = new window.Cognito.AuthenticationDetails({
    Username: email,
    Password: pass
  });

  const user = new window.Cognito.CognitoUser({
    Username: email,
    Pool: pool
  });

  user.authenticateUser(auth, {
    onSuccess: async () => {
      stopSpin(res);
      setMsg('loginMsg', 'Successfully signed in!', 'success');
      setTimeout(async () => {
        const cu = getCurrentUser();
        const username = cu ? cu.getUsername() : 'Unknown';
        document.getElementById('who').textContent = username;

        // Pin the app client used for this session so env switches don't force re-login
        if (window.CONFIG?.userPoolWebClientId && typeof window.pinClientId === 'function') {
          window.pinClientId(window.CONFIG.userPoolWebClientId);
        }

        showMainApp();
        syncEnvChrome(); // reflect current env in header color

        await loadUserClientData();

        if (window.initializeApp) {
          window.initializeApp();
        }
      }, 1000);
    },
    onFailure: (err) => {
      setMsg('loginMsg', err.message || String(err), 'error');
      stopSpin(res);
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

  // Unpin so a new session can adopt the env’s client ID
  if (typeof window.unpinClientId === 'function') {
    window.unpinClientId();
  }

  currentClientID = null;
  if (window.resetOrdersData) {
    window.resetOrdersData();
  }

  // Remove dev chrome on logout
  document.body.classList.remove('env-dev');

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

    // Load all client configuration (Stripe, Offers, Shipping, Tenant Config)
    await Promise.all([
      window.loadStripeKeys ? window.loadStripeKeys(cognitoClientID) : Promise.resolve(),
      window.loadOffers ? window.loadOffers(cognitoClientID) : Promise.resolve(),
      window.loadShippingConfig ? window.loadShippingConfig(cognitoClientID) : Promise.resolve(),
      window.loadTenantConfig ? window.loadTenantConfig(cognitoClientID) : Promise.resolve()
    ]);

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

  initRegistrationView();
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

// --- ENV CHROME (header color + body class) ---
function syncEnvChrome() {
  const env =
    (window.CONFIG && window.CONFIG.environment) ||
    localStorage.getItem('env') ||
    'dev';
  document.body.classList.toggle('env-dev', env === 'dev');
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
// keep UI in sync whenever config/env changes
window.addEventListener('configLoaded', syncEnvChrome);
window.addEventListener('environmentChanged', syncEnvChrome);
document.addEventListener('DOMContentLoaded', syncEnvChrome);


// Inject button spinner CSS + message styles
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
.iti__dial-code { color: #333 !important; }
.iti__country { color: #333 !important; }
.iti__selected-dial-code { color: #333 !important; }
.iti__country:hover { background-color: #f0f0f0 !important; }
.iti__country.iti__active { background-color: #e8e8e8 !important; }

/* Auth / status messages */
.msg { font-size: 0.95rem; line-height: 1.35; }
.msg--error { color: #c62828; }
.msg--success { color: #2e7d32; }
`;

// Inject styles on load + init phone input
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
