// --- Configuration (align with checkout.js) ---
const CLIENT_ID = 'f8810370-7021-7011-c0cc-6f22f52954d3'; // same client ID used on checkout
const API_URL   = 'https://api-dev.juniorbay.com/';

// --- Stripe bootstrap (publishable key via clientId, with safe fallback) ---
let stripe = null;
let elements = null;
async function initStripeWithClient() {
  try {
    const res = await fetch(API_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Client-Id': CLIENT_ID
      },
      body: JSON.stringify({ action: 'get_stripe_keys' }) // server may return {publishable_key,...}
    });
    const data = await res.json();
    const pk =
      data?.publishable_key ||
      data?.publishableKey ||
      'pk_test_mTW79VPNrNQEECSvj5AdC66n00UjfFE9kO'; // fallback (current key)
    stripe = Stripe(pk);
  } catch (e) {
    console.warn('Falling back to hardcoded publishable key:', e);
    stripe = Stripe('pk_test_mTW79VPNrNQEECSvj5AdC66n00UjfFE9kO');
  }
  elements = stripe.elements();
}

let originalOrderData = null;
let upsellCardElement = null;
let upsellConfig = null; // will hold metadata-driven upsell info

// ------------------ URL helpers ------------------
function getUrlParameter(name) {
  const urlParams = new URLSearchParams(window.location.search);
  return urlParams.get(name);
}
function getSessionIdFromURL() {
  const params = new URLSearchParams(window.location.search);
  return params.get('session_id') || params.get('sessionId');
}

// ------------------ COUNTDOWN ------------------
let upsellTimeLeft = 3 * 60;
const upsellCountdownElement = document.getElementById('upsell-countdown');

function updateUpsellCountdown() {
  if (upsellTimeLeft <= 0) {
    upsellCountdownElement.textContent = '00:00';
    window.location.href = 'thank-you.html';
    return;
  }
  const m = Math.floor(upsellTimeLeft / 60);
  const s = upsellTimeLeft % 60;
  upsellCountdownElement.textContent = `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  upsellTimeLeft--;
}
function startUpsellCountdown() {
  updateUpsellCountdown();
  setInterval(updateUpsellCountdown, 1000);
}

// ------------------ UI HELPERS ------------------
function showUpsellError(msg) {
  const el = document.getElementById('upsell-error');
  el.textContent = msg;
  el.style.display = 'block';
}
function hideUpsellError() {
  const el = document.getElementById('upsell-error');
  el.style.display = 'none';
}
function showUpsellSuccess(msg) {
  const el = document.getElementById('upsell-success');
  el.textContent = msg;
  el.style.display = 'block';
}

// ------------------ Backend calls (now with X-Client-Id) ------------------
async function loadCheckoutSessionDetails() {
  const sessionId = getSessionIdFromURL();
  if (!sessionId) throw new Error('Missing session_id in URL');

  const res = await fetch(API_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Client-Id': CLIENT_ID
    },
    body: JSON.stringify({
      action: 'get_checkout_session_details',
      session_id: sessionId
    })
  });

  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Failed to get session details');

  window.originalOrderData = {
    paymentIntentId: data.original_payment_intent_id,
    customerId: data.customer_id,
    productId: data.product_id,
    customerName: data.customer_name,
    customerEmail: data.customer_email,
    customerPhone: data.customer_phone,
    shippingAddress: data.shipping_address || {}
  };

  return data;
}

async function loadUpsellConfig() {
  try {
    const res = await fetch(API_URL, {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'X-Client-Id': CLIENT_ID
      },
      body: JSON.stringify({
        action: 'get_upsell_config',
        product_id: window.originalOrderData?.productId
      })
    });

    const config = await res.json();
    if (!res.ok || config.error) throw new Error(config.error || 'Failed to load upsell config');

    const acceptButton = document.getElementById('accept-upsell');
    if (acceptButton) acceptButton.textContent = config.upsell_offer_text || 'Yes! Add Another';
    upsellConfig = config;
  } catch (err) {
    console.error('Failed to load upsell config:', err);
  }
}

// ------------------ ONE-CLICK UPSELL ------------------
async function processOneClickUpsell() {
  hideUpsellError();
  const acceptButton = document.getElementById('accept-upsell');
  acceptButton.disabled = true;
  acceptButton.textContent = 'Processing...';

  try {
    const sessionId = getUrlParameter('session_id');

    // Step 1: attempt off-session using session_id and existing customer/payment details
    const response = await fetch(API_URL, {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'X-Client-Id': CLIENT_ID
      },
      body: JSON.stringify({
        action: 'process_one_click_upsell',
        product_id: window.originalOrderData.productId,
        session_id: sessionId,
        original_payment_intent_id: window.originalOrderData.paymentIntentId,
        customer_id: window.originalOrderData.customerId,
        customer_name: window.originalOrderData.customerName,
        customer_email: window.originalOrderData.customerEmail,
        customer_phone: window.originalOrderData.customerPhone,
        shipping_address: window.originalOrderData.shippingAddress
      })
    });

    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Failed to process upsell');

    // Success path
    if (result.success) {
      showUpsellSuccess('Upsell successful! Redirecting...');
      sessionStorage.setItem('upsellComplete', JSON.stringify({
        upsellPaymentIntentId: result.payment_intent_id,
        originalPaymentIntentId: originalOrderData.paymentIntentId
      }));
      setTimeout(() => (window.location.href = 'thank-you.html'), 2000);
      return;
    }

    // Requires action fallback
    if (result.requires_action && result.client_secret) {
      // Ask backend to create a PI we can confirm on-session
      const resp2 = await fetch(API_URL, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'X-Client-Id': CLIENT_ID
        },
        body: JSON.stringify({
          action: 'create_upsell_payment',
          product_id: originalOrderData.productId,
          original_payment_intent_id: originalOrderData.paymentIntentId,
          customer_id: originalOrderData.customerId
        })
      });

      const fallback = await resp2.json();
      if (!resp2.ok) throw new Error(fallback.error || 'Failed to create fallback upsell');

      const { error: confirmError, paymentIntent } = await stripe.confirmCardPayment(
        fallback.client_secret
      );

      if (confirmError) {
        showUpsellError(confirmError.message);
        acceptButton.disabled = false;
        acceptButton.textContent = upsellConfig?.upsell_offer_text || 'Yes! Add Another';
        return;
      }

      if (paymentIntent && paymentIntent.status === 'succeeded') {
        showUpsellSuccess('Upsell successful! Redirecting...');
        sessionStorage.setItem('upsellComplete', JSON.stringify({
          upsellPaymentIntentId: paymentIntent.id,
          originalPaymentIntentId: originalOrderData.paymentIntentId
        }));
        setTimeout(() => (window.location.href = 'thank-you.html'), 2000);
        return;
      }
    }

    throw new Error('Unexpected upsell response');
  } catch (err) {
    showUpsellError(err.message || 'An error occurred. Please try again.');
    const acceptButton = document.getElementById('accept-upsell');
    acceptButton.disabled = false;
    acceptButton.textContent = upsellConfig?.upsell_offer_text || 'Yes! Add Another';
  }
}

// ------------------ INIT ------------------
document.addEventListener('DOMContentLoaded', async () => {
  // 1) Stripe (via client-id aware endpoint)
  await initStripeWithClient();

  // 2) Order/session bootstrapping (compatible with existing behavior)
  const sessionId = getUrlParameter('session_id');
  const orderDataString = sessionStorage.getItem('originalOrder');

  if (!sessionId && !orderDataString) {
    window.location.href = 'index.html';
    return;
  }

  if (orderDataString) {
    originalOrderData = JSON.parse(orderDataString);
    window.originalOrderData = originalOrderData;
  } else {
    // Minimum shape; loadCheckoutSessionDetails will populate the rest
    originalOrderData = { productId: 'prod_SxJKn7l9FUAD0B', paymentIntentId: null, customerId: null };
    window.originalOrderData = originalOrderData;
  }

  // 3) Countdown
  startUpsellCountdown();

  // 4) Mount card element (for fallback auth if needed)
  upsellCardElement = elements.create('card', {
    style: { base: { fontSize: '16px', color: '#424770', '::placeholder': { color: '#aab7c4' } } }
  });
  upsellCardElement.mount('#upsell-card-element');

  // 5) Pull session details, then config (needs productId/customer info)
  try {
    await loadCheckoutSessionDetails();
  } catch (e) {
    // If session call fails but we have cached order data, continue gracefully
    console.warn('Proceeding with cached order data:', e);
  }
  await loadUpsellConfig();

  // 6) Wire actions
  document.getElementById('accept-upsell')?.addEventListener('click', () => {
    processOneClickUpsell();
  });
  document.getElementById('upsell-payment-form')?.addEventListener('submit', (e) => {
    e.preventDefault();
    processOneClickUpsell(); // reuse same flow; backend will handle fallback creation when needed
  });

  // 7) Auto-clear cache after 10 min
  setTimeout(() => sessionStorage.removeItem('originalOrder'), 10 * 60 * 1000);
});
