// --- Configuration ---
const CLIENT_ID = 'f8810370-7021-7011-c0cc-6f22f52954d3'; // Your actual client ID
const API_URL = 'https://api-dev.juniorbay.com/';

// --- Countdown Timer (unchanged) ---
let timeLeft = 5 * 60;
const productPrices = {};  
const countdownElement = document.getElementById('countdown');
let discountExpired = false;

function updateCountdown() {
  if (timeLeft <= 0) {
    countdownElement.textContent = '00:00';

    if (!discountExpired) {
      discountExpired = true;

      document.querySelectorAll('.txt-reg').forEach(el => el.style.opacity = '0');
      document.querySelectorAll('.txt-save').forEach(el => el.textContent = '');
      document.querySelectorAll('.txt-discount').forEach(el => el.textContent = '');

      // Update each card with its regular price from Stripe
      document.querySelectorAll('.package-card').forEach(card => {
        const product_id = card.id;
        const pricing = productPrices[product_id];
        const regular = pricing?.regular;

        const priceEl = card.querySelector('.txt-price');
        if (priceEl && regular) {
          priceEl.textContent = formatCurrency(regular.unit_amount, regular.currency);
        }
      });

      updatePurchaseButton();
      renderOrderSummary();
      updateOrderSummary();
    }
    return;
  }

  const m = Math.floor(timeLeft / 60);
  const s = timeLeft % 60;
  countdownElement.textContent = `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  timeLeft--;
}
setInterval(updateCountdown, 1000);


let selectedPackage = document.querySelector('.package-card.selected')?.dataset.package || 'popular';
const purchaseBtn    = document.getElementById('purchase-btn');
const packageCards   = document.querySelectorAll('.package-card');

function updatePurchaseButton() {
  const product_id = getSelectedProductId();
  const pricing = productPrices[product_id];
  if (!pricing) return; // not loaded yet

  const chosen = (timeLeft > 0 ? pricing.discounted : pricing.regular) || pricing.discounted;
  if (!chosen) return;

  const priceText = formatCurrency(chosen.unit_amount, chosen.currency);
  purchaseBtn.textContent = `Order Now - ${priceText} + FREE Shipping`;
}


// --- Icons (checked vs empty) ---
const CHECKED_SVG = `
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" class="h-3 w-3">
  <circle cx="12" cy="12" r="12" fill="#3b84f6" />
  <path d="M20 6 9 17l-5-5" fill="none" stroke="#ffffff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>`;
const EMPTY_SVG = `
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" class="h-3 w-3">
  <circle cx="12" cy="12" r="11" fill="none" stroke="#64758b" stroke-width="2" />
</svg>`;
function renderIcons() {
  document.querySelectorAll('.package-card').forEach(card => {
    const icon = card.querySelector('.icon-check');
    if (!icon) return;
    icon.innerHTML = card.classList.contains('selected') ? CHECKED_SVG : EMPTY_SVG;
  });
}

// --- Order Summary ---
function moneyToNumber(text) {
  if (!text) return 0;
  // strips $, commas, spaces; keeps decimals
  return parseFloat(String(text).replace(/[^0-9.]/g, '')) || 0;
}

function getSelectedCard() {
  return document.querySelector('.package-card.selected');
}

function readPricesFromCard(card) {
  // Original price = crossed-out span, Discounted price = bold primary span
  const originalText  = card?.querySelector('.right .line-through')?.textContent || '';
  const discountedText = card?.querySelector('.right .text-primary')?.textContent || '';
  const nameText = card?.querySelector('.left h4')?.textContent?.trim() || '';
  const original = moneyToNumber(originalText);
  const price    = moneyToNumber(discountedText);
  const saved    = Math.max(original - price, 0);
  return { nameText, original, price, saved };
}

function renderOrderSummary() {
  const card = getSelectedCard();
  const { nameText, price, saved } = readPricesFromCard(card);

  const nameEl   = document.getElementById('os-package-name');
  const priceEl  = document.getElementById('os-price');
  const saveEl   = document.getElementById('os-savings');

  if (nameEl)  nameEl.textContent  = nameText || '—';
  if (priceEl) priceEl.textContent = price ? `$${price.toFixed(2)}` : '—';
  if (saveEl)  saveEl.textContent  = saved ? `$${saved.toFixed(2)}` : '$0.00';
}

function updateOrderSummary() {
  const selectedCard = document.querySelector('.package-card.selected');
  if (!selectedCard) return;

  const regText = selectedCard.querySelector('.right .txt-reg')?.textContent || '';
  const finalText = selectedCard.querySelector('.right .txt-price')?.textContent || '';

  const regPrice = parseFloat(regText.replace(/[^0-9.]/g, '')) || 0;
  const finalPrice = parseFloat(finalText.replace(/[^0-9.]/g, '')) || 0;
  const discount = regPrice && finalPrice ? regPrice - finalPrice : 0;

  // Update DOM
  document.getElementById('os-regular').textContent = regPrice ? `$${regPrice.toFixed(2)}` : '—';
  document.getElementById('os-discount').textContent = discount ? `- $${discount.toFixed(2)}` : '—';
  document.getElementById('os-final').textContent = finalPrice ? `$${finalPrice.toFixed(2)}` : '—';

  // Tax is calculated by Stripe at checkout
  const taxEl = document.getElementById('os-tax');
  if (taxEl) taxEl.textContent = 'Tax will be settled at checkout';
}

// --- Stripe Init (same style as checkout.html) ---
const stripe   = Stripe('pk_test_mTW79VPNrNQEECSvj5AdC66n00UjfFE9kO'); // publishable
const elements = stripe.elements();
const cardElement = elements.create('card', {
  style: { base: { fontSize: '16px', color: '#424770', '::placeholder': { color: '#aab7c4' } } }
});
cardElement.mount('#card-element');

// --- Product/Price state ---
let productData = null;   // { product, price, tax_amount, shipping_cost, total_amount }
let activePriceId = null; // track which price ID is active

function getSelectedProductId() {
  // Each card's id already equals the Stripe product id in your HTML
  const selectedCard = document.querySelector('.package-card.selected');
  return selectedCard ? selectedCard.id : 'prod_SxJgASmAPdNEgf';
}

// --- API helpers ---
function formatCurrency(amount, currency = 'usd') {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: currency.toUpperCase() })
    .format(amount / 100);
}

async function preloadAllProductPrices() {
  const cards = document.querySelectorAll('.package-card');

  for (const card of cards) {
    const product_id = card.id;
    try {
      const res = await fetch(API_URL, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'X-Client-Id': CLIENT_ID  // Add the client ID header
        },
        body: JSON.stringify({ action: 'get_product_info', product_id })
      });
      if (!res.ok) throw new Error('Failed to fetch product information');
      const data = await res.json();

      const prices = data.prices || [];
      const discounted = prices.find(p => p.metadata.type === "discounted");
      const regular    = prices.find(p => p.metadata.type === "regular");

      productPrices[product_id] = { discounted, regular };
    } catch (err) {
      console.error(`Error loading prices for product ${product_id}:`, err);
    }
  }
}

async function loadProductInfo() {
  const product_id = getSelectedProductId();
  try {
    const res = await fetch(API_URL, {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'X-Client-Id': CLIENT_ID  // Add the client ID header
      },
      body: JSON.stringify({ action: 'get_product_info', product_id })
    });

    if (!res.ok) throw new Error('Failed to fetch product information');
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    // --- Find discounted/regular from Stripe metadata ---
    const prices = Array.isArray(data.prices) ? data.prices : [];
    const discounted = prices.find(p => p?.metadata?.type === 'discounted') || null;
    const regular    = prices.find(p => p?.metadata?.type === 'regular')    || null;

    if (!discounted && !regular && prices.length === 0) {
      throw new Error('No matching price found');
    }

    // Cache them for this product (your existing structure)
    productPrices[product_id] = { discounted, regular, all: prices };

    // Helper: choose which price should be active *right now*
    const pickActivePrice = () => {
      // if countdown still running and we have discounted, use it
      if (timeLeft > 0 && discounted) return discounted;
      // otherwise prefer regular, fall back to discounted, then any price
      return regular || discounted || prices[0] || null;
    };

    // Build productData fresh
    const baseProductData = {
      product: data.product,
      prices: prices
      // we will add a dynamic getter for .price below
    };

    // Make productData.price a dynamic getter that always picks based on current timer
    productData = baseProductData;
    Object.defineProperty(productData, 'price', {
      get() { return pickActivePrice(); },
      enumerable: true
    });

    // --- Update UI using the *current* active price ---
    const chosenPrice = pickActivePrice();
    if (!chosenPrice) throw new Error('No matching price found');

    const priceText = formatCurrency(chosenPrice.unit_amount, chosenPrice.currency);
    purchaseBtn.textContent = `Order Now - ${priceText} + FREE Shipping`;

    // Order summary: prefer regular for "compare at", fallback gracefully
    const regAmt   = (regular?.unit_amount) ?? chosenPrice.unit_amount;
    const finalAmt = chosenPrice.unit_amount;
    const discount = regAmt - finalAmt;

    document.getElementById('os-regular').textContent  = regAmt ? formatCurrency(regAmt, chosenPrice.currency) : '—';
    document.getElementById('os-final').textContent    = finalAmt ? formatCurrency(finalAmt, chosenPrice.currency) : '—';
    document.getElementById('os-discount').textContent = discount > 0 ? `- ${formatCurrency(discount, chosenPrice.currency)}` : '—';
    document.getElementById('os-tax').textContent      = 'Tax will be settled at checkout';

    // (Optional) expose active id if you want to debug elsewhere
    window.activePriceId = chosenPrice.id;

  } catch (err) {
    console.error(err);
    showError('Unable to load product info.');
  }
}


function showError(msg) {
  const el = document.getElementById('error-message');
  el.textContent = msg;
  el.style.display = 'block';
}
function hideError() {
  const el = document.getElementById('error-message');
  el.style.display = 'none';
}

function showCopyright() {
  const el = document.getElementById('copyright');
  const yyyy = new Date().getFullYear();
  let msg = `\u00A9 ${yyyy} Junior Bay Corporation - All rights reserved.`;
  el.textContent = msg
}

function updateLinkText() {
    const mq = window.matchMedia("(max-width: 359px)");

    const link1 = document.getElementById("foot-link1");
    const link2 = document.getElementById("foot-link2");
    const link3 = document.getElementById("foot-link3");

    if (!link1 || !link2 || !link3) return; // safety check

    if (mq.matches) {
      // Small mobile screens
      link1.innerHTML = "Contact";
      link2.innerHTML = "Terms";
      link3.innerHTML = "Privacy";
    } else {
      // Default text for larger screens
      link1.innerHTML = "Contact Us";
      link2.innerHTML = "Terms and Conditions";
      link3.innerHTML = "Privacy Policy";
    }
  }

window.addEventListener("resize", updateLinkText);

// --- Helper function to determine if upsell should be shown ---
function shouldShowUpsell(productId) {
  // Show upsell only for the basic package (1 item)
  return productId === 'prod_SxJKn7l9FUAD0B';
}

// --- Click handlers for package cards ---
packageCards.forEach(card => {
  card.addEventListener('click', async function () {
    packageCards.forEach(c => c.classList.remove('selected'));
    this.classList.add('selected');
    selectedPackage = this.dataset.package;
    renderIcons();
    updatePurchaseButton();
    renderOrderSummary();
    updateOrderSummary();
    // Load the newly selected product's info (updates pricing)
    await loadProductInfo();
  });
});

// --- Purchase button scroll (keep your UX) ---
purchaseBtn.addEventListener('click', async () => {
  try {
    const product_id = getSelectedProductId();
    if (!productData) {
      console.warn("Product data missing, retrying load for:", selectedPackage);
      await loadProductInfo(); // fallback load if not ready
    }

    if (!productData || !productData.price) {
      throw new Error("Price not loaded. Please try again.");
    }

    const price_id = productData.price.id;

    if (!price_id) throw new Error("Price not loaded. Please try again.");

    const res = await fetch(API_URL, {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'X-Client-Id': CLIENT_ID  // Add the client ID header
      },
      body: JSON.stringify({
        action: 'create_checkout_session',
        product_id,
        price_id
      })
    });

    const data = await res.json();
    if (!data.id) {
      throw new Error(data.error || 'Failed to create checkout session');
    }

    // Redirect to Stripe Checkout
    const { error } = await stripe.redirectToCheckout({ sessionId: data.id });
    if (error) {
      console.error('Stripe Checkout redirect error:', error.message);
      showError(error.message);
    }
  } catch (err) {
    console.error('Error starting checkout:', err);
    showError('Unable to start checkout. Please try again.');
  }
});


// --- Same-as-billing toggle + live sync + show/hide shipping section ---
const sameAsBillingCheckbox = document.getElementById('same-as-billing');
const SHIP_SECTION = document.getElementById('shipping-section');

function syncShippingFields(checked) {
  const map = { address: 'shipping-address', address2: 'shipping-address2', city: 'shipping-city', state: 'shipping-state', zip: 'shipping-zip' };
  Object.entries(map).forEach(([bill, ship]) => {
    const b = document.getElementById(bill), s = document.getElementById(ship);
    if (!b || !s) return;
    if (checked) {
      s.value = b.value;
      s.disabled = true;
      s.style.backgroundColor = '#f8f9fa';
    } else {
      s.disabled = false;
      s.style.backgroundColor = 'white';
    }
  });
}

function toggleShippingSection() {
  if (!SHIP_SECTION) return;
  SHIP_SECTION.style.display = sameAsBillingCheckbox.checked ? 'none' : 'block';
}

sameAsBillingCheckbox.addEventListener('change', () => {
  toggleShippingSection();
  syncShippingFields(sameAsBillingCheckbox.checked);
});

// Keep live sync while checked
['address','address2','city','state','zip'].forEach(id => {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener('input', function() {
    if (!sameAsBillingCheckbox.checked) return;
    const map = { address: 'shipping-address', address2: 'shipping-address2', city: 'shipping-city', state: 'shipping-state', zip: 'shipping-zip' };
    const target = document.getElementById(map[id]);
    if (target) target.value = this.value;
  });
});

// --- Helper function to get the correct shipping address ---
function getShippingAddress() {
  // If "same as billing" is checked, use billing address
  if (sameAsBillingCheckbox.checked) {
    return {
      line1: document.getElementById('address').value,
      line2: document.getElementById('address2').value || undefined,
      city: document.getElementById('city').value,
      state: document.getElementById('state').value,
      postal_code: document.getElementById('zip').value,
      country: 'US'
    };
  } else {
    // Use the shipping form fields
    return {
      line1: document.getElementById('shipping-address').value,
      line2: document.getElementById('shipping-address2').value || undefined,
      city: document.getElementById('shipping-city').value,
      state: document.getElementById('shipping-state').value,
      postal_code: document.getElementById('shipping-zip').value,
      country: 'US'
    };
  }
}

// --- Form submit: create PaymentIntent then confirm with Stripe ---
const form = document.getElementById('payment-form');
const submitButton = document.getElementById('submit-button');
const successMessage = document.getElementById('success-message');

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideError();
  successMessage.style.display = 'none';

  if (!productData) {
    showError('Product information not loaded. Please try again.');
    return;
  }

  submitButton.disabled = true;
  submitButton.textContent = 'Processing...';

  const name  = `${document.getElementById('firstName').value} ${document.getElementById('lastName').value}`.trim();
  const email = document.getElementById('email').value;
  const phone = document.getElementById('phone').value;

  const billing_address = {
    line1: document.getElementById('address').value,
    line2: document.getElementById('address2').value || undefined,
    city:  document.getElementById('city').value,
    state: document.getElementById('state').value,
    postal_code: document.getElementById('zip').value,
    country: 'US'
  };

  // Use the helper function to get the correct shipping address
  const shipping_address = getShippingAddress();

  console.log('Shipping address being sent to Stripe:', shipping_address); // Debug log

  try {
    // 1) Create PaymentIntent on your server (Lambda @ /create-payment-intent)
    const response = await fetch(API_URL, {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'X-Client-Id': CLIENT_ID  // Add the client ID header
      },
      body: JSON.stringify({
        amount:       productData.total_amount,
        currency:     productData.price.currency,
        product_id:   productData.product.id,
        customer_info: { 
          email, 
          name, 
          phone,
          billing_address, 
          shipping_address 
        }
      })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Failed to create payment intent');

    // 2) Confirm with Stripe.js - make sure shipping address is properly formatted
    const confirmParams = {
      payment_method: {
        card: cardElement,
        billing_details: { 
          name, 
          email, 
          phone,
          address: billing_address 
        }
      }
    };

    // Only add shipping if we have a valid address
    if (shipping_address.line1 && shipping_address.city && shipping_address.state && shipping_address.postal_code) {
      confirmParams.shipping = {
        name: name,
        phone: phone,
        address: shipping_address
      };
      console.log('Adding shipping to confirmParams:', confirmParams.shipping); // Debug log
    } else {
      console.log('Shipping address incomplete, not adding to confirmParams'); // Debug log
    }

    const { error, paymentIntent } = await stripe.confirmCardPayment(result.client_secret, confirmParams);

    if (error) {
      showError(error.message);
    } else if (paymentIntent && paymentIntent.status === 'succeeded') {
      console.log('Payment succeeded! PaymentIntent:', paymentIntent); // Debug log
      
      // Store order data for potential upsell
      const orderData = {
        paymentIntentId: paymentIntent.id,
        customerId: result.customer_id,
        customerName: name,
        customerEmail: email,
        customerPhone: phone,
        productId: productData.product.id,
        amount: productData.total_amount,
        shippingAddress: shipping_address,  // add
        billingAddress: billing_address     // add
      };

      // Check if upsell should be shown
      if (shouldShowUpsell(productData.product.id)) {
        // Store original order data in session storage
        sessionStorage.setItem('originalOrder', JSON.stringify(orderData));
        
        // Redirect to upsell page
        window.location.href = 'upsell.html';
      } else {
        // Redirect to thank you page for multi-item orders
        window.location.href = 'thank-you.html';
      }
    } else {
      showError('Payment was not completed. Please try again.');
    }
  } catch (err) {
    console.error(err);
    showError(err.message || 'An error occurred. Please try again.');
  }

  submitButton.disabled = false;
  submitButton.textContent = 'Complete Order';
});

// --- Init once DOM is ready ---
document.addEventListener('DOMContentLoaded', async () => {
  showCopyright();
  renderIcons();

  await preloadAllProductPrices();

  // Force-load default selected card
  selectedPackage = document.querySelector('.package-card.selected')?.dataset.package || 'popular';
  await loadProductInfo();

  if (!productData) {
    console.error("Failed to load default product info for:", selectedPackage);
  }

  updatePurchaseButton();
  renderOrderSummary();
  updateOrderSummary();
  updateLinkText();

  if (sameAsBillingCheckbox) {
    sameAsBillingCheckbox.checked = true;
    toggleShippingSection();
    syncShippingFields(true);
  }
});