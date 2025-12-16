// ======== STRIPE KEYS MANAGEMENT ========

// A tiny helper so we don't resend empty strings and accidentally wipe values
function _addIfPresent(obj, key, value) {
  const v = (value ?? '').trim();
  if (v !== '') obj[key] = v;
}

// Render encrypted/masked field state consistently
function _renderFieldState(inputEl, fieldData) {
  if (!inputEl) return;

  // reset visuals
  inputEl.style.borderColor = '';
  inputEl.title = '';
  inputEl.dataset.stored = '0';
  inputEl.dataset.encrypted = '0';
  inputEl.dataset.masked = '0';
  inputEl.placeholder = '';

  if (!fieldData) {
    inputEl.value = '';
    return;
  }

  // plain string (legacy/plaintext)
  if (typeof fieldData === 'string') {
    inputEl.value = fieldData;
    return;
  }

  // object shape from API
  const hasError    = !!fieldData.error;
  const isMasked    = !!fieldData.masked;
  const isEncrypted = !!fieldData.encrypted;

  if (hasError) {
    inputEl.value = '';
    inputEl.style.borderColor = 'var(--danger)';
    inputEl.title = `Error: ${fieldData.error}`;
    return;
  }

  if (isMasked) {
    // Show masked text and mark as stored
    inputEl.value = fieldData.masked;
    inputEl.dataset.stored = '1';
    inputEl.dataset.masked = '1';
    inputEl.title = 'Value is masked (already stored)';
    return;
  }

  if (isEncrypted) {
    // Don't show the secret; show a neutral placeholder and mark stored
    inputEl.value = '';
    inputEl.placeholder = 'Saved (hidden)';
    inputEl.dataset.stored = '1';
    inputEl.dataset.encrypted = '1';
    inputEl.title = 'Encrypted value is stored. Enter a new value to replace it.';
    inputEl.style.borderColor = 'var(--muted)';
    return;
  }

  // Unknown object → be safe: treat as present but hidden
  inputEl.value = '';
  inputEl.placeholder = 'Saved (hidden)';
  inputEl.dataset.stored = '1';
  inputEl.title = 'A value is stored for this field.';
}

// ======== LOAD STRIPE KEYS ========
async function loadStripeKeys(clientID) {
  try {
    const res = await authedFetch(`/admin/stripe-keys?clientID=${encodeURIComponent(clientID)}`);

    if (res.ok) {
      const v = typeof res.body === 'string' ? {} : (res.body || {});

      // Mode
      const modeEl = document.getElementById('mode');
      if (modeEl) modeEl.value = v.mode || 'test';

      // Map API payload → form fields
      const fieldMap = {
        'pk_test': v.pk_test,
        'sk_test': v.sk_test,
        'wh_test': v.wh_secret_test,
        'pk_live': v.pk_live,
        'sk_live': v.sk_live,
        'wh_live': v.wh_secret_live
      };

      Object.keys(fieldMap).forEach(id => {
        const inputEl = document.getElementById(id);
        _renderFieldState(inputEl, fieldMap[id]);
      });

      setMsg('apiMsg', 'Stripe keys loaded', 'success');
    } else {
      setMsg('apiMsg', `No stripe keys found (${res.status})`, 'info');
      // Clear fields to avoid stale UI
      ['pk_test','sk_test','wh_test','pk_live','sk_live','wh_live'].forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.value = ''; el.placeholder = ''; }
      });
    }
  } catch (error) {
    console.error('Error loading stripe keys:', error);
    setMsg('apiMsg', `Error loading keys: ${error.message}`, 'error');
  }
}

// ======== SAVE STRIPE KEYS ========
async function handleSaveStripeKeys() {
  try {
    const idEl = document.getElementById('clientID');
    const saveBtn = document.getElementById('btnSave');
    if (!idEl) return setMsg('apiMsg', 'Missing clientID input', 'error');

    const id = idEl.value.trim();
    if (!id) return setMsg('apiMsg', 'Enter clientID', 'error');

    // Build payload with ONLY values the user actually entered.
    // Leaving an input blank will NOT overwrite stored secrets.
    const payload = { clientID: id };
    const modeEl = document.getElementById('mode');
    if (modeEl && modeEl.value) payload.mode = modeEl.value;

    const pk_test = document.getElementById('pk_test')?.value;
    const pk_live = document.getElementById('pk_live')?.value;
    const sk_test = document.getElementById('sk_test')?.value;
    const sk_live = document.getElementById('sk_live')?.value;
    const wh_test = document.getElementById('wh_test')?.value;
    const wh_live = document.getElementById('wh_live')?.value;

    _addIfPresent(payload, 'pk_test', pk_test);
    _addIfPresent(payload, 'pk_live', pk_live);
    _addIfPresent(payload, 'sk_test', sk_test);
    _addIfPresent(payload, 'sk_live', sk_live);

    if ((wh_test ?? '').trim() !== '') {
      payload.wh_secret_test = wh_test.trim();
    }
    if ((wh_live ?? '').trim() !== '') {
      payload.wh_secret_live = wh_live.trim();
    }

    // Spinner
    const spin = startSpin(saveBtn, 'Saving…');

    const res = await authedFetch(`/admin/stripe-keys`, {
      method: 'PUT',
      body: payload
    });

    // Stop spinner asap
    stopSpin(spin);

    // Surface backend-provided failures even on HTTP 200
    if (!res.ok || (res.body && res.body.ok === false)) {
      const status = res.status || 0;
      const msg = (res.body && (res.body.error || res.body.message || JSON.stringify(res.body))) || 'Unknown error';
      return setMsg('apiMsg', `Save failed (${status}): ${msg}`, 'error');
    }

    setMsg('apiMsg', 'Stripe keys saved successfully!', 'success');

    // Reload to reflect masked/encrypted state without user overwriting it
    setTimeout(() => {
      if (typeof loadUserClientData === 'function') {
        loadUserClientData();
      } else {
        const id2 = document.getElementById('clientID')?.value.trim();
        if (id2) loadStripeKeys(id2);
      }
    }, 500);

  } catch (error) {
    console.error('Save stripe keys error:', error);
    setMsg('apiMsg', `Error: ${error.message}`, 'error');
  }
}

// ======== GET STRIPE KEYS ========
async function handleGetStripeKeys() {
  try {
    const id = document.getElementById('clientID')?.value.trim();
    if (!id) return setMsg('apiMsg', 'Enter clientID', 'error');

    const btn = document.getElementById('btnGet');
    const spin = startSpin(btn, 'Loading…');

    await loadStripeKeys(id);

    stopSpin(spin);
  } catch (error) {
    console.error('Get stripe keys error:', error);
    setMsg('apiMsg', `Error: ${error.message}`, 'error');
  }
}

// ======== VERIFY STRIPE KEYS ========
async function handleVerifyStripeKeys() {
  try {
    const id = document.getElementById('clientID')?.value.trim();
    const mode = document.getElementById('mode')?.value;

    if (!id) return setMsg('verifyMsg', 'Enter clientID', 'error');

    setMsg('verifyMsg', 'Verifying credentials...', 'info');

    const btn = document.getElementById('btnVerify');
    const spin = startSpin(btn, 'Verifying…');

    const res = await authedFetch(`/admin/verify`, {
      method: 'POST',
      body: { clientID: id, mode }
    });

    stopSpin(spin);

    if (res.ok) {
      const result = res.body || {};
      let msg = `Verification Results:\n`;
      msg += `• Publishable key: ${result.publishable_key_ok ? '✓ Valid' : '✗ Invalid'}\n`;
      msg += `• Secret key: ${result.secret_key_ok ? '✓ Valid' : '✗ Invalid'}\n`;
      msg += `• Webhook secret: ${result.webhook_secret_ok ? '✓ Valid' : '✗ Invalid'}\n`;

      if (result.stripe_account) msg += `• Stripe account: ${result.stripe_account}\n`;
      if (Array.isArray(result.notes) && result.notes.length) {
        msg += `\nNotes:\n${result.notes.join('\n')}`;
      }

      const allValid = !!(result.publishable_key_ok && result.secret_key_ok && result.webhook_secret_ok);
      const host = document.getElementById('verifyMsg');
      host.textContent = msg;
      host.className = allValid ? 'message success' : 'message error';
    } else {
      const errorMsg = `Verification failed (${res.status}): ${JSON.stringify(res.body)}`;
      document.getElementById('verifyMsg').textContent = errorMsg;
      document.getElementById('verifyMsg').className = 'message error';
    }

  } catch (error) {
    console.error('Verify keys error:', error);
    const host = document.getElementById('verifyMsg');
    host.textContent = `Error: ${error.message}`;
    host.className = 'message error';
  }
}

// ======== EVENT LISTENERS ========
function setupStripeKeysListeners() {
  const btnGet = document.getElementById('btnGet');
  const btnSave = document.getElementById('btnSave');
  const btnVerify = document.getElementById('btnVerify');

  if (btnGet)    btnGet.addEventListener('click', handleGetStripeKeys);
  if (btnSave)   btnSave.addEventListener('click', handleSaveStripeKeys);
  if (btnVerify) btnVerify.addEventListener('click', handleVerifyStripeKeys);
}

// ======== HELPER FUNCTIONS (exported) ========
function getClientID() {
  return document.getElementById('clientID')?.value.trim() || '';
}

async function getIdToken() {
  const session = await getSession();
  return session.getIdToken().getJwtToken();
}

function startSpin(btn, txt = 'Loading...') {
  const orig = btn && btn.textContent;
  if (btn) {
    btn.disabled = true;
    btn.textContent = txt;
  }
  return { btn, orig };
}

function stopSpin(res) {
  if (res && res.btn) {
    res.btn.disabled = false;
    res.btn.textContent = res.orig;
  }
}

// Export functions
window.loadStripeKeys = loadStripeKeys;
window.setupStripeKeysListeners = setupStripeKeysListeners;
window.getClientID = getClientID;
window.getIdToken = getIdToken;
window.startSpin = startSpin;
window.stopSpin = stopSpin;
