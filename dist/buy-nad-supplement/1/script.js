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