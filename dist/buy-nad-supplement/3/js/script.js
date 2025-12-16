// Countdown Timer
let timeLeft = 5 * 60; // 5 minutes in seconds

function updateCountdown() {
    const minutes = Math.floor(timeLeft / 60);
    const seconds = timeLeft % 60;
    
    const countdownElement = document.getElementById('countdown');
    if (countdownElement) {
        countdownElement.textContent = 
            String(minutes).padStart(2, '0') + ':' + String(seconds).padStart(2, '0');
    }
    
    if (timeLeft > 0) {
        timeLeft--;
    }
}

// Update countdown every second
setInterval(updateCountdown, 1000);

// Initialize countdown on page load
updateCountdown();

// --- Package Selection with Checkmark Toggle ---
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

// Click handlers for package cards
const packageCards = document.querySelectorAll('.package-card');
const purchaseBtn = document.getElementById('purchase-btn');

packageCards.forEach(card => {
    card.addEventListener('click', function () {
        packageCards.forEach(c => c.classList.remove('selected'));
        this.classList.add('selected');
        renderIcons();
    });
});

// Initialize icons on page load
renderIcons();

// Copyright Year
document.getElementById("year").textContent = new Date().getFullYear();