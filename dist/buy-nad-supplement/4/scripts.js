/* Four-Tier Pricing Template Specific JavaScript */

// Template configuration
const FOUR_TIER_CONFIG = {
    defaultSelectedIndex: 2, // Default to third package (0-indexed)
    countdownMinutes: 5,
    expiredBehavior: 'show_regular_price' // or 'hide_discount'
};

// State management for four-tier template
let selectedPackageId = null;
let selectedPriceId = null;
let timeLeft = FOUR_TIER_CONFIG.countdownMinutes * 60;
let countdownExpired = false;
let countdownInterval = null;

// Initialize four-tier template
document.addEventListener('DOMContentLoaded', function() {
    initializeFourTierTemplate();
});

function initializeFourTierTemplate() {
    // Set default selected package
    const packageCards = document.querySelectorAll('.package-card');
    if (packageCards.length > 0) {
        const defaultIndex = Math.min(FOUR_TIER_CONFIG.defaultSelectedIndex, packageCards.length - 1);
        const defaultCard = packageCards[defaultIndex];
        if (defaultCard) {
            defaultCard.classList.add('selected');
            selectedPackageId = defaultCard.dataset.productId;
            selectedPriceId = defaultCard.dataset.priceId;
        }
    }
    
    // Add click handlers to package cards
    packageCards.forEach(card => {
        card.addEventListener('click', function() {
            selectPackage(this);
        });
    });
    
    // Initialize countdown if element exists
    if (document.getElementById('countdown')) {
        startCountdown();
    }
    
    // Setup purchase button handler
    const purchaseBtn = document.querySelector('.btn-primary');
    if (purchaseBtn) {
        purchaseBtn.addEventListener('click', handleFourTierPurchase);
    }
    
    // Add animation on scroll
    setupScrollAnimations();
}

// Select package handler
function selectPackage(card) {
    // Remove selected class from all cards
    document.querySelectorAll('.package-card').forEach(c => {
        c.classList.remove('selected');
        const iconCheck = c.querySelector('.icon-check');
        if (iconCheck) {
            iconCheck.innerHTML = getEmptyCircleIcon();
        }
    });
    
    // Add selected class to clicked card
    card.classList.add('selected');
    const iconCheck = card.querySelector('.icon-check');
    if (iconCheck) {
        iconCheck.innerHTML = getCheckedCircleIcon();
    }
    
    // Update selected IDs
    selectedPackageId = card.dataset.productId;
    selectedPriceId = card.dataset.priceId;
    
    // Track selection event
    if (typeof gtag !== 'undefined') {
        gtag('event', 'select_item', {
            item_list_name: 'Four Tier Pricing',
            items: [{
                item_id: selectedPackageId,
                item_name: card.querySelector('h4')?.textContent || 'Package',
                price: extractPriceFromCard(card)
            }]
        });
    }
}

// Extract price from card element
function extractPriceFromCard(card) {
    const priceElement = card.querySelector('.txt-price');
    if (priceElement) {
        const priceText = priceElement.textContent.replace(/[^0-9.]/g, '');
        return parseFloat(priceText) || 0;
    }
    return 0;
}

// Handle purchase button click
function handleFourTierPurchase(event) {
    event.preventDefault();
    
    if (!selectedPackageId) {
        showNotification('Please select a package', 'warning');
        return;
    }
    
    // Track begin checkout event
    if (typeof gtag !== 'undefined') {
        gtag('event', 'begin_checkout', {
            value: extractPriceFromSelectedCard(),
            items: [{
                item_id: selectedPackageId,
                quantity: 1
            }]
        });
    }
    
    if (typeof fbq !== 'undefined') {
        fbq('track', 'InitiateCheckout', {
            content_ids: [selectedPackageId],
            content_type: 'product',
            value: extractPriceFromSelectedCard(),
            currency: 'USD'
        });
    }
    
    // Redirect to checkout
    window.checkout(selectedPackageId, selectedPriceId);
}

// Get price from selected card
function extractPriceFromSelectedCard() {
    const selectedCard = document.querySelector('.package-card.selected');
    return selectedCard ? extractPriceFromCard(selectedCard) : 0;
}

// Countdown timer functionality
function startCountdown() {
    updateCountdownDisplay();
    countdownInterval = setInterval(updateCountdown, 1000);
}

function updateCountdown() {
    if (countdownExpired) {
        clearInterval(countdownInterval);
        return;
    }
    
    if (timeLeft > 0) {
        timeLeft--;
        updateCountdownDisplay();
    } else {
        expireDiscount();
    }
}

function updateCountdownDisplay() {
    const minutes = Math.floor(timeLeft / 60);
    const seconds = timeLeft % 60;
    
    const countdownEl = document.getElementById('countdown');
    if (countdownEl) {
        countdownEl.textContent = 
            String(minutes).padStart(2, '0') + ':' + String(seconds).padStart(2, '0');
    }
}

function expireDiscount() {
    countdownExpired = true;
    clearInterval(countdownInterval);
    
    // Update banner text
    const bannerText = document.getElementById('bannerText');
    if (bannerText) {
        bannerText.textContent = 'Discount has expired. Regular pricing applies.';
    }
    
    // Update countdown display
    const countdownEl = document.getElementById('countdown');
    if (countdownEl) {
        countdownEl.textContent = '00:00';
        countdownEl.classList.add('expired');
    }
    
    // Handle expired behavior
    if (FOUR_TIER_CONFIG.expiredBehavior === 'show_regular_price') {
        updatePricesToRegular();
    } else if (FOUR_TIER_CONFIG.expiredBehavior === 'hide_discount') {
        hideDiscountElements();
    }
}

function updatePricesToRegular() {
    // Update all prices to regular prices
    document.querySelectorAll('.package-card').forEach(card => {
        const regularPriceEl = card.querySelector('.txt-reg');
        const currentPriceEl = card.querySelector('.txt-price');
        const savingsEl = card.querySelector('.txt-save');
        const discountEl = card.querySelector('.txt-discount');
        
        if (regularPriceEl && currentPriceEl) {
            // Move regular price to current price
            const regularPrice = regularPriceEl.textContent.replace(/[^0-9.]/g, '');
            currentPriceEl.textContent = '$' + regularPrice;
            
            // Hide discount elements
            if (regularPriceEl) regularPriceEl.style.display = 'none';
            if (savingsEl) savingsEl.textContent = 'Regular Price';
            if (discountEl) discountEl.style.display = 'none';
        }
    });
}

function hideDiscountElements() {
    // Hide all discount-related elements
    document.querySelectorAll('.txt-reg, .txt-save, .txt-discount').forEach(el => {
        el.style.display = 'none';
    });
}

// Icon helpers
function getCheckedCircleIcon() {
    return `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="12" fill="#3b84f6" />
        <path d="M20 6 9 17l-5-5" fill="none" stroke="#ffffff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
}

function getEmptyCircleIcon() {
    return `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="11" fill="none" stroke="#64758b" stroke-width="2" />
    </svg>`;
}

// Scroll animations
function setupScrollAnimations() {
    const observerOptions = {
        threshold: 0.1,
        rootMargin: '0px 0px -50px 0px'
    };
    
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.style.opacity = '1';
                entry.target.style.transform = 'translateY(0)';
            }
        });
    }, observerOptions);
    
    // Observe benefit items
    document.querySelectorAll('.benefit-item').forEach((item, index) => {
        item.style.opacity = '0';
        item.style.transform = 'translateY(20px)';
        item.style.transition = `all 0.5s ease-out ${index * 0.1}s`;
        observer.observe(item);
    });
}

// Show notification helper
function showNotification(message, type = 'info') {
    // Create notification element
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 1rem 1.5rem;
        background: ${type === 'warning' ? '#fbbf24' : '#3b84f6'};
        color: ${type === 'warning' ? '#1f2937' : 'white'};
        border-radius: 0.5rem;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        z-index: 1000;
        animation: slideIn 0.3s ease-out;
    `;
    
    document.body.appendChild(notification);
    
    // Remove after 3 seconds
    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease-out';
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}

// Add CSS for animations if not already present
if (!document.querySelector('#four-tier-animations')) {
    const style = document.createElement('style');
    style.id = 'four-tier-animations';
    style.textContent = `
        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        @keyframes slideOut {
            from { transform: translateX(0); opacity: 1; }
            to { transform: translateX(100%); opacity: 0; }
        }
    `;
    document.head.appendChild(style);
}