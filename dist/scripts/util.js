packageCards.forEach(card => {
    card.addEventListener('click', function() {
        // Remove selected class from all cards
        packageCards.forEach(c => c.classList.remove('selected'));
        
        // Add selected class to clicked card
        this.classList.add('selected');

        // Set all svg icons from empty circle, except this one to blue circle check
        
        // Update selected package
        selectedPackage = this.dataset.package;
        updatePurchaseButton();
    });
});