// config.js - Loads configuration from backend (env-aware, session-aware client pinning)
(function () {
  // ===== ENV MAP & RESOLUTION =====
  const ENV_MAP = {
    dev:  { configUrl: "https://api-dev.juniorbay.com/config" },
    prod: { configUrl: "https://checkout.juniorbay.com/config" }
  };
  const envBadgeText = (env) => (env === "prod" ? "Live" : "Test");

  // Highest → lowest precedence: ?env=, localStorage, hostname heuristic, default
  function resolveEnv() {
    const urlEnv = new URLSearchParams(location.search).get("env");
    if (urlEnv === "dev" || urlEnv === "prod") return urlEnv;

    const lsEnv = localStorage.getItem("env");
    if (lsEnv === "dev" || lsEnv === "prod") return lsEnv;

    return /checkout\.juniorbay\.com$/i.test(location.hostname) ? "prod" : "dev";
  }

  // ---- Session-aware client pinning ----
  const SESSION_CLIENT_KEY = "app:sessionClientId";
  function getPinnedClientId() {
    return localStorage.getItem(SESSION_CLIENT_KEY) || null;
  }
  function pinClientId(clientId) {
    if (clientId) localStorage.setItem(SESSION_CLIENT_KEY, clientId);
  }
  function unpinClientId() {
    localStorage.removeItem(SESSION_CLIENT_KEY);
  }

  // Public setter to switch env on the fly (persists + reloads config)
  async function setEnvironment(nextEnv) {
    const env = nextEnv === "prod" ? "prod" : "dev";
    localStorage.setItem("env", env);

    // reflect immediately in badge if present
    const badge = document.getElementById("envLabel");
    if (badge) badge.textContent = envBadgeText(env);

    // re-load configuration (keeps pinned client id unchanged)
    await loadConfig(env);
    window.dispatchEvent(new CustomEvent("environmentChanged", { detail: { env, config: window.CONFIG } }));
  }

  // Bind CONFIG once
  const CONFIG = (window.CONFIG = window.CONFIG || {
    apiBase: null,
    uploadApiBase: null,
    cdnBase: null,
    environment: null,
    region: null,
    userPoolId: null,
    userPoolWebClientId: null,
    isLoaded: false,
  });

  const Cognito = window.AmazonCognitoIdentity;
  let cognitoPool = null;
  let lastEnv = null;

  async function loadConfig(forcedEnv) {
    const env = forcedEnv || resolveEnv();
    lastEnv = env;

    const CONFIG_ENDPOINT = ENV_MAP[env].configUrl;

    try {
      CONFIG.isLoaded = false;

      const response = await fetch(CONFIG_ENDPOINT, { method: "GET" });
      const cfg = response.ok ? await response.json() : null;

      // Fill CONFIG from server (fallbacks keep app usable if keys are missing)
      CONFIG.environment = cfg?.environment || env;
      CONFIG.region      = cfg?.cognito_region || "us-west-2";
      CONFIG.userPoolId  = cfg?.cognito_user_pool_id || "";
      const envClientId  = cfg?.cognito_client_id || "";
      CONFIG.apiBase     = cfg?.api_base_url || "";
      CONFIG.uploadApiBase = cfg?.upload_base_url || null;
      CONFIG.cdnBase       = cfg?.cdn_base_url || null;

      // Choose Cognito client id:
      //  - Prefer the one pinned for the current authenticated session
      //  - If none pinned yet (fresh boot), pin the env's client id now
      let chosenClientId = getPinnedClientId() || envClientId;
      if (!getPinnedClientId() && envClientId) {
        pinClientId(envClientId);
        chosenClientId = envClientId;
      }
      CONFIG.userPoolWebClientId = chosenClientId;

      // (Re)initialize the pool if we have the necessary bits
      if (Cognito && CONFIG.userPoolId && chosenClientId) {
        cognitoPool = new Cognito.CognitoUserPool({
          UserPoolId: CONFIG.userPoolId,
          ClientId: chosenClientId,
        });
      } else {
        cognitoPool = null;
      }

      CONFIG.isLoaded = true;

      // Update badge if present
      const badge = document.getElementById("envLabel");
      if (badge) badge.textContent = envBadgeText(env);

      // Notify listeners that config is loaded
      window.dispatchEvent(new CustomEvent("configLoaded", { detail: { env, config: cfg || {} } }));
      return true;
    } catch (error) {
      // Don't brick the app—mark loaded so UI can show a retry
      CONFIG.isLoaded = true;
      console.error(`❌ Failed to load configuration for env=${lastEnv}`, error);
      return false;
    }
  }

  // Kick off initial load
  loadConfig();

  // Export safe globals
  window.Cognito = Cognito;
  window.cognitoPool = () => cognitoPool;
  window.loadConfiguration = loadConfig;
  window.setEnvironment = setEnvironment;
  window.pinClientId = pinClientId;     // call after successful login
  window.unpinClientId = unpinClientId; // call on logout
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
        new Promise(res => setTimeout(res, 3000)) // don't hang forever
    ]);
  }

  const session = await getSession();
  const idToken = session.getIdToken().getJwtToken();

  const baseOverride = opts.baseOverride;
  if (baseOverride) {
    delete opts.baseOverride;
  }

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

  const apiBase = baseOverride || window.CONFIG.apiBase || "";
  const resp = await fetch(`${apiBase}${path}`, init);
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
