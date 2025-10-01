// Upsell page logic
const stripe = Stripe('pk_test_mTW79VPNrNQEECSvj5AdC66n00UjfFE9kO');
const elements = stripe.elements();
const API_URL = 'https://dgsek4sxg7.execute-api.us-west-2.amazonaws.com/Prod/';

let originalOrderData = null;
let upsellCardElement = null;
let upsellConfig = null; // will hold metadata-driven upsell info

// Add URL parameter helper function
function getUrlParameter(name) {
  const urlParams = new URLSearchParams(window.location.search);
  return urlParams.get(name);
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

// helper: read session_id from URL
function getSessionIdFromURL() {
  const params = new URLSearchParams(window.location.search);
  return params.get('session_id') || params.get('sessionId');
}

// call backend to load details we need for upsell
async function loadCheckoutSessionDetails() {
  const sessionId = getSessionIdFromURL();
  if (!sessionId) throw new Error('Missing session_id in URL');

  const res = await fetch(API_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      action: 'get_checkout_session_details',
      session_id: sessionId
    })
  });

  const data = await res.json();
  console.log(data);
  if (!res.ok) throw new Error(data.error || 'Failed to get session details');

  // Save for later (and to show on UI if you want)
  window.originalOrderData = {
    paymentIntentId: data.original_payment_intent_id,
    customerId: data.customer_id,
    productId: data.product_id,
    customerName: data.customer_name,
    customerEmail: data.customer_email,
    customerPhone: data.customer_phone,
    shippingAddress: data.shipping_address || {}
    // billing address is not available here; webhook will capture it later from charge
  };

  return data;
}

// modify your upsell init to wait for the session details
(async function initUpsell() {
  try {
    await loadCheckoutSessionDetails();
    await loadUpsellConfig(); // your existing function that calls action 'get_upsell_config'
    // enable the Accept button now that we have IDs
    const acceptBtn = document.getElementById('accept-upsell');
    if (acceptBtn) acceptBtn.disabled = false;
  } catch (err) {
    console.error('Failed to init upsell:', err);
    showUpsellError(err.message || 'Unable to load upsell.');
  }
})();


// ------------------ LOAD UPSELL CONFIG ------------------
async function loadUpsellConfig() {
  try {
    const response = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "get_upsell_config",
        product_id: originalOrderData.productId
      })
    });

    const config = await response.json();
    if (config.error) throw new Error(config.error);

    console.log(config.upsell_offer_text);

    // Update button dynamically
    const acceptButton = document.getElementById("accept-upsell");
    acceptButton.textContent = config.upsell_offer_text || "Yes! Add Another";

    upsellConfig = config; // save for later use
  } catch (err) {
    console.error("Failed to load upsell config:", err.message);
  }
}

// ------------------ ONE-CLICK UPSELL ------------------
async function processOneClickUpsell() {
  hideUpsellError();
  const acceptButton = document.getElementById("accept-upsell");
  acceptButton.disabled = true;
  acceptButton.textContent = "Processing...";

  try {
    // Get session ID from URL
    const sessionId = getUrlParameter('session_id');
    console.log('Session ID from URL:', sessionId); // Debug log

    // Step 1: Attempt off-session charge with session_id
    const response = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "process_one_click_upsell",
        product_id: window.originalOrderData.productId,
        session_id: sessionId, // Pass current session ID
        original_payment_intent_id: window.originalOrderData.paymentIntentId, // Fallback
        customer_id: window.originalOrderData.customerId,
        customer_name: window.originalOrderData.customerName,
        customer_email: window.originalOrderData.customerEmail,
        customer_phone: window.originalOrderData.customerPhone,
        shipping_address: window.originalOrderData.shippingAddress,
        // billing_address: window.originalOrderData.billingAddress
      })
    });

    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Failed to process upsell");

    // Success
    if (result.success) {
      showUpsellSuccess("Upsell successful! Redirecting...");
      sessionStorage.setItem("upsellComplete", JSON.stringify({
        upsellPaymentIntentId: result.payment_intent_id,
        originalPaymentIntentId: originalOrderData.paymentIntentId
      }));
      setTimeout(() => (window.location.href = "thank-you.html"), 2000);
      return;
    }

    // Requires authentication
    if (result.requires_action && result.client_secret) {
      console.log("Upsell requires authentication, falling back...");

      const resp2 = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "create_upsell_payment",
          product_id: originalOrderData.productId,
          original_payment_intent_id: originalOrderData.paymentIntentId,
          customer_id: originalOrderData.customerId
        })
      });

      const fallback = await resp2.json();
      if (!resp2.ok) throw new Error(fallback.error || "Failed to create fallback upsell");

      const { error: confirmError, paymentIntent } = await stripe.confirmCardPayment(
        fallback.client_secret
      );

      if (confirmError) {
        showUpsellError(confirmError.message);
        acceptButton.disabled = false;
        acceptButton.textContent = upsellConfig?.upsell_offer_text || "Yes! Add Another";
        return;
      }

      if (paymentIntent && paymentIntent.status === "succeeded") {
        showUpsellSuccess("Upsell successful! Redirecting...");
        sessionStorage.setItem("upsellComplete", JSON.stringify({
          upsellPaymentIntentId: paymentIntent.id,
          originalPaymentIntentId: originalOrderData.paymentIntentId
        }));
        setTimeout(() => (window.location.href = "thank-you.html"), 2000);
        return;
      }
    }

    throw new Error("Unexpected upsell response");
  } catch (err) {
    showUpsellError(err.message || "An error occurred. Please try again.");
    acceptButton.disabled = false;
    acceptButton.textContent = upsellConfig?.upsell_offer_text || "Yes! Add Another";
  }
}

// ------------------ INIT ------------------
document.addEventListener('DOMContentLoaded', () => {
  // Check if we have a session_id in the URL (from Stripe Checkout redirect)
  const sessionId = getUrlParameter('session_id');
  console.log('Found session_id in URL:', sessionId);

  // Get cached order data from sessionStorage (if available)
  const orderDataString = sessionStorage.getItem('originalOrder');
  
  if (!sessionId && !orderDataString) {
    // No session ID and no cached data - redirect to home
    console.log('No session_id or cached order data, redirecting to home');
    window.location.href = 'index.html';
    return;
  }

  if (orderDataString) {
    originalOrderData = JSON.parse(orderDataString);
  } else {
    // Create minimal order data structure if we only have session_id
    originalOrderData = {
      productId: 'prod_SxJKn7l9FUAD0B', // Default to basic product ID
      paymentIntentId: null, // Will be determined from session
      customerId: null // Will be determined from session
    };
  }

  console.log('Using order data:', originalOrderData);

  // Start countdown
  startUpsellCountdown();

  // Mount Stripe elements (in case fallback is needed)
  upsellCardElement = elements.create('card', {
    style: {
      base: { fontSize: '16px', color: '#424770', '::placeholder': { color: '#aab7c4' } }
    }
  });
  upsellCardElement.mount('#upsell-card-element');

  // Load upsell config dynamically
  loadUpsellConfig();

  // Handle accept click
  document.getElementById('accept-upsell').addEventListener('click', () => {
    processOneClickUpsell();
  });

  // Handle manual form submit (fallback card entry)
  document.getElementById('upsell-payment-form').addEventListener('submit', (e) => {
    e.preventDefault();
    processUpsellPayment();
  });

  // Auto-clear original order after 10 mins
  setTimeout(() => {
    sessionStorage.removeItem('originalOrder');
  }, 10 * 60 * 1000);
});