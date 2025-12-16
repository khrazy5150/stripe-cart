// Smooth scrolling for anchor links
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
        const href = this.getAttribute('href');
        if (href !== '#') {
            e.preventDefault();
            const target = document.querySelector(href);
            if (target) {
                target.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        }
    });
});

// Form submission handler
function handleSubmit(event) {
    event.preventDefault();
    const email = document.getElementById('emailInput').value;
    alert('Thank you for your interest! Email: ' + email);
    // In production, this would submit to a backend service
    event.target.reset();
}

// Add scroll effect to navigation
let lastScroll = 0;
window.addEventListener('scroll', () => {
    const nav = document.querySelector('.main-navigation');
    const currentScroll = window.pageYOffset;
    
    if (currentScroll > 100) {
        nav.style.boxShadow = '0 2px 10px rgba(0, 0, 0, 0.1)';
    } else {
        nav.style.boxShadow = 'none';
    }
    
    lastScroll = currentScroll;
});