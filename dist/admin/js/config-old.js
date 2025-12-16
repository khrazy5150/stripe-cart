// config.js - Loads configuration from backend
(function () {
  // 1) Create a real variable and bind it to window
  const CONFIG = (window.CONFIG = window.CONFIG || {
    apiBase: null,
    uploadApiBase: null,
    environment: null,
    cdnBase: null,
    isLoaded: false,
  });

  // 2) Keep a stable reference to Cognito SDK + pool and export them at the end
  const Cognito = window.AmazonCognitoIdentity;
  let cognitoPool = null;

  async function loadConfig() {
    const CONFIG_ENDPOINT =
      "https://api-dev.juniorbay.com/config";
    try {
      const response = await fetch(CONFIG_ENDPOINT, { method: "GET" });
      const config = response.ok ? await response.json() : null;

      // 3) Fill CONFIG
      CONFIG.environment = config?.environment || "dev";
      CONFIG.region = config?.cognito_region || "us-west-2";
      CONFIG.userPoolId = config?.cognito_user_pool_id || "";
      CONFIG.userPoolWebClientId = config?.cognito_client_id || "";
      CONFIG.apiBase = config?.api_base_url || "";

      // 4) Init Cognito
      if (Cognito && CONFIG.userPoolId && CONFIG.userPoolWebClientId) {
        cognitoPool = new Cognito.CognitoUserPool({
          UserPoolId: CONFIG.userPoolId,
          ClientId: CONFIG.userPoolWebClientId,
        });
      }

      CONFIG.isLoaded = true;

      // 5) Let other scripts know
      window.dispatchEvent(new CustomEvent("configLoaded", { detail: config || {} }));

      return true;

    } catch (error) {
        CONFIG.isLoaded = true;
        console.error(`❌ Failed to load configuration`, error);
        return false;
    }
  }

  // 6) Kick off load
  loadConfig();

  // 7) Export safe globals from inside the IIFE (no outer-scope refs)
  window.Cognito = Cognito;
  window.cognitoPool = () => cognitoPool; // accessor to avoid stale refs
  window.loadConfiguration = loadConfig;  // fixed name
})();

// ======== HELPERS ========

/**
 * Get current Cognito user
 */
function getCurrentUser() {
  const pool = typeof window.cognitoPool === "function" ? window.cognitoPool() : null;
  return pool ? pool.getCurrentUser() : null;
}

/**
 * Get current session with JWT token
 */
function getSession() {
    return new Promise((resolve, reject) => {
        const user = getCurrentUser();
        if (!user) return reject(new Error('Not signed in'));
        
        user.getSession((err, session) => {
            if (err) return reject(err);
            if (!session || !session.isValid()) {
                return reject(new Error('Invalid session'));
            }
            resolve(session);
        });
    });
}

/**
 * Make authenticated API request
 */
async function authedFetch(path, opts = {}) {
  // Ensure config is loaded
  if (!window.CONFIG?.isLoaded) {
    await Promise.race([
        new Promise(res => window.addEventListener("configLoaded", () => res(), { once:true })),
        new Promise(res => setTimeout(res, 3000)) // don’t hang forever
    ]);
  }


  const session = await getSession();
  const idToken = session.getIdToken().getJwtToken();

  const headers = {
    Authorization: `Bearer ${idToken}`,
    "Content-Type": "application/json",
    ...(opts.headers || {}),
  };

  const init = {
    method: opts.method || "GET",
    headers,
    mode: "cors",
    credentials: "omit",
    body:
      opts.body != null
        ? (typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body))
        : undefined,
  };

  const resp = await fetch(`${window.CONFIG.apiBase}${path}`, init);
  const txt = await resp.text();

  let body;
  try { body = JSON.parse(txt); } catch { body = { raw: txt }; }

  return { ok: resp.ok, status: resp.status, body, headers: resp.headers };
}

/**
 * Show message in specified element
 */
function setMsg(id, text, kind = '') {
    const el = document.getElementById(id);
    if (el) {
        el.className = kind ? `message ${kind}` : 'muted';
        el.textContent = text || '';
    }
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(str) {
    return String(str || '').replace(/[&<>"'`=\/]/g, function (s) {
        return {
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
            "'": '&#39;', '/': '&#x2F;', '`': '&#x60;', '=': '&#x3D;'
        }[s];
    });
}

/**
 * Format currency
 */
function formatCurrency(amount, currency = 'usd') {
    const formatter = new Intl.NumberFormat('en-US', { 
        style: 'currency', 
        currency: (currency || 'usd').toUpperCase() 
    });
    return formatter.format((amount || 0) / 100);
}

/**
 * Format date for display
 */
function formatDate(date_string) {
    if (!date_string) return 'N/A';
    try {
        const dt = new Date(date_string.replace('Z', '+00:00'));
        return dt.toLocaleString('en-US', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            hour12: true
        });
    } catch {
        return date_string;
    }
}

// Export helper functions globally
window.getCurrentUser = getCurrentUser;
window.getSession = getSession;
window.authedFetch = authedFetch;
window.setMsg = setMsg;
window.escapeHtml = escapeHtml;
window.formatCurrency = formatCurrency;
window.formatDate = formatDate;