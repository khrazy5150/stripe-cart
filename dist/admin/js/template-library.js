// template-library.js - Shared Template Rendering Functions
// Used by both client-side preview and server-side Lambda generator

/**
 * Main render function - generates complete HTML for landing page
 * @param {Object} data - Landing page configuration data
 * @returns {string} - Complete HTML document
 */
function renderTemplate(data) {
    const {
        template_type,
        hero_title,
        hero_subtitle,
        guarantee,
        products = [],
        layout_options = {},
        custom_css = '',
        custom_js = '',
        analytics_pixels = {}
    } = data;
    
    const colorScheme = layout_options.color_scheme || { primary: '#007bff', accent: '#28a745' };
    
    // Sort products by display_order
    const sortedProducts = [...products].sort((a, b) => 
        (a.display_order || 0) - (b.display_order || 0)
    );
    
    // Generate template-specific content
    let contentHtml = '';
    switch (template_type) {
        case 'single-product-hero':
            contentHtml = renderSingleProductHero(sortedProducts[0], hero_title, hero_subtitle);
            break;
        case 'three-tier-pricing':
            contentHtml = renderTierPricing(sortedProducts.slice(0, 3), hero_title, hero_subtitle);
            break;
        case 'four-tier-pricing':
            contentHtml = renderFourTierPricing(sortedProducts, hero_title, hero_subtitle, layout_options);
            break;
        // case 'four-tier-pricing':
        //     contentHtml = renderTierPricing(sortedProducts.slice(0, 4), hero_title, hero_subtitle);
        //     break;
        case 'five-tier-pricing':
            contentHtml = renderTierPricing(sortedProducts.slice(0, 5), hero_title, hero_subtitle);
            break;
        case 'sales-letter':
            contentHtml = renderSalesLetter(sortedProducts[0], hero_title, hero_subtitle);
            break;
        case 'video-sales':
            contentHtml = renderVideoSales(sortedProducts[0], hero_title, hero_subtitle);
            break;
        case 'email-capture-lead':
            contentHtml = renderEmailCapture(sortedProducts[0], hero_title, hero_subtitle);
            break;
        case 'subscription-focus':
            contentHtml = renderSubscriptionFocus(sortedProducts[0], hero_title, hero_subtitle);
            break;
        case 'configurable-product':
            contentHtml = renderConfigurableProduct(sortedProducts[0], hero_title, hero_subtitle, data.custom_fields);
            break;
        case 'custom':
            contentHtml = renderCustomTemplate(sortedProducts, hero_title, hero_subtitle);
            break;
        default:
            contentHtml = renderSingleProductHero(sortedProducts[0], hero_title, hero_subtitle);
    }
    
    // Build complete HTML document
    return `
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>${escapeHtml(hero_title || 'Special Offer')}</title>
    
    <!-- Base Styles -->
    <link rel="stylesheet" href="https://templates.juniorbay.com/v1/base/styles.css">
    
    <!-- Template-specific Styles -->
    <link rel="stylesheet" href="https://templates.juniorbay.com/v1/${template_type}/styles.css">
    
    <style>
        :root {
            --primary-color: ${colorScheme.primary};
            --accent-color: ${colorScheme.accent};
        }
        ${custom_css}
    </style>
    
    ${analytics_pixels.google ? `<!-- Google Analytics -->
    <script async src="https://www.googletagmanager.com/gtag/js?id=${analytics_pixels.google}"></script>
    <script>
        window.dataLayer = window.dataLayer || [];
        function gtag(){dataLayer.push(arguments);}
        gtag('js', new Date());
        gtag('config', '${analytics_pixels.google}');
    </script>` : ''}
    
    ${analytics_pixels.meta ? `<!-- Meta Pixel -->
    <script>
        !function(f,b,e,v,n,t,s)
        {if(f.fbq)return;n=f.fbq=function(){n.callMethod?
        n.callMethod.apply(n,arguments):n.queue.push(arguments)};
        if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
        n.queue=[];t=b.createElement(e);t.async=!0;
        t.src=v;s=b.getElementsByTagName(e)[0];
        s.parentNode.insertBefore(t,s)}(window, document,'script',
        'https://connect.facebook.net/en_US/fbevents.js');
        fbq('init', '${analytics_pixels.meta}');
        fbq('track', 'PageView');
    </script>
    <noscript><img height="1" width="1" style="display:none"
        src="https://www.facebook.com/tr?id=${analytics_pixels.meta}&ev=PageView&noscript=1"
    /></noscript>` : ''}
</head>
<body>
    ${contentHtml}
    
    ${guarantee ? `
    <section class="guarantee-section">
        <div class="container">
            <div class="guarantee-badge">
                <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
                    <polyline points="9 12 11 14 15 10"></polyline>
                </svg>
            </div>
            <h3>Our Guarantee</h3>
            <p>${escapeHtml(guarantee)}</p>
        </div>
    </section>
    ` : ''}
    
    ${layout_options.show_testimonials ? renderTestimonials() : ''}
    ${layout_options.show_faq ? renderFAQ() : ''}
    
    <footer class="footer">
        <div class="container">
            <p>&copy; ${new Date().getFullYear()} All rights reserved.</p>
        </div>
    </footer>
    
    <!-- Base Scripts -->
    <script src="https://templates.juniorbay.com/v1/base/scripts.js"></script>
    
    <!-- Template-specific Scripts -->
    <script src="https://templates.juniorbay.com/v1/${template_type}/scripts.js"></script>
    
    ${custom_js ? `<script>${custom_js}</script>` : ''}
</body>
</html>
    `.trim();
}

/**
 * Single Product Hero Template
 */
function renderSingleProductHero(product, heroTitle, heroSubtitle) {
    if (!product || !product._product) return '<div class="error">Product not found</div>';
    
    const p = product._product;
    const description = product.custom_description_override || p.description || '';
    const benefits = p.metadata?.benefits ? p.metadata.benefits.split('|') : [];
    const images = p.images || [];
    const price = (p.lowest_price / 100).toFixed(2);
    
    return `
    <header class="hero hero-single">
        <div class="container">
            <div class="hero-content">
                <div class="hero-text">
                    <h1>${escapeHtml(heroTitle)}</h1>
                    <p class="hero-subtitle">${escapeHtml(heroSubtitle)}</p>
                    ${description ? `<p class="product-description">${escapeHtml(description)}</p>` : ''}
                    
                    ${benefits.length > 0 ? `
                    <ul class="benefits-list">
                        ${benefits.map(b => `<li>✓ ${escapeHtml(b.trim())}</li>`).join('')}
                    </ul>
                    ` : ''}
                    
                    <div class="price-cta">
                        <div class="price">${price}</div>
                        <button class="cta-button" onclick="checkout('${p.id}')">
                            Buy Now
                        </button>
                    </div>
                </div>
                
                ${images.length > 0 ? `
                <div class="hero-image">
                    <img src="${escapeHtml(images[0])}" alt="${escapeHtml(p.name)}">
                </div>
                ` : ''}
            </div>
        </div>
    </header>
    `;
}

/**
 * Tier Pricing Template (3 or 5 tiers)
 */
function renderTierPricing(products, heroTitle, heroSubtitle) {
    if (products.length === 0) return '<div class="error">No products configured</div>';
    
    return `
    <header class="hero hero-simple">
        <div class="container">
            <h1>${escapeHtml(heroTitle)}</h1>
            <p class="hero-subtitle">${escapeHtml(heroSubtitle)}</p>
        </div>
    </header>
    
    <section class="pricing-tiers">
        <div class="container">
            <div class="pricing-grid grid-${products.length}">
                ${products.map(product => {
                    if (!product._product) return '';
                    const p = product._product;
                    const price = (p.lowest_price / 100).toFixed(2);
                    const benefits = p.metadata?.benefits ? p.metadata.benefits.split('|') : [];
                    const images = p.images || [];
                    const featured = product.is_featured ? 'featured' : '';
                    
                    return `
                    <div class="pricing-card ${featured}">
                        ${product.is_featured ? '<div class="featured-badge">Most Popular</div>' : ''}
                        ${images.length > 0 ? `
                        <div class="card-image">
                            <img src="${escapeHtml(images[0])}" alt="${escapeHtml(p.name)}">
                        </div>
                        ` : ''}
                        <h3>${escapeHtml(product.tier_label || p.name)}</h3>
                        <div class="card-price">${price}</div>
                        <ul class="card-features">
                            ${benefits.map(b => `<li>✓ ${escapeHtml(b.trim())}</li>`).join('')}
                        </ul>
                        <button class="cta-button" onclick="checkout('${p.id}')">
                            Select ${escapeHtml(product.tier_label || 'Plan')}
                        </button>
                    </div>
                    `;
                }).join('')}
            </div>
        </div>
    </section>
    `;
}

/**
 * Four-Tier NAD-Style Pricing Template
 * Specifically designed for supplement/health product sales with countdown timer
 */
function renderFourTierPricing(products, heroTitle, heroSubtitle, layoutOptions = {}) {
    if (products.length === 0) return '<div class="error">No products configured</div>';
    
    // Pad with empty products if less than 4
    while (products.length < 4) {
        products.push(null);
    }
    
    const countdownMinutes = layoutOptions.countdown_minutes || 5;
    const showBenefits = layoutOptions.show_benefits !== false;
    
    return `
    <!-- Countdown Banner -->
    <div class="countdown-banner">
        <div class="container banner-content">
            <p class="banner-text">
                🔥 <span id="bannerText">Up to 50% off applied. Discount expires in:</span>
                <span class="countdown-timer" id="countdown">${String(countdownMinutes).padStart(2, '0')}:00</span>
            </p>
        </div>
    </div>

    <!-- Hero Section -->
    <section class="hero-section">
        <div class="container">
            <div class="hero-grid">
                <div class="hero-content">
                    <h1 class="hero-title">${escapeHtml(heroTitle)}</h1>
                    <p class="hero-description">${escapeHtml(heroSubtitle)}</p>
                    ${layoutOptions.hero_badges ? `
                    <div class="hero-badges">
                        ${layoutOptions.hero_badges.map(badge => 
                            `<div class="badge"><p>✓ ${escapeHtml(badge)}</p></div>`
                        ).join('')}
                    </div>
                    ` : ''}
                </div>
                ${products[0] && products[0]._product?.images?.length > 0 ? `
                <div class="hero-image-container">
                    <img src="${escapeHtml(products[0]._product.images[0])}" 
                         alt="${escapeHtml(products[0]._product.name)}" 
                         class="hero-image">
                </div>
                ` : ''}
            </div>
        </div>
    </section>

    <!-- Pricing Section -->
    <section class="pricing-section">
        <div class="container">
            <div class="section-header">
                <h2 class="section-title">Choose Your Package</h2>
                <p class="section-description">Bigger savings with larger quantities. All orders include free shipping.</p>
            </div>

            <section class="select-item">
                <div class="straight-container mx-auto">
                    <div class="box" id="packagesContainer">
                        ${products.map((product, index) => {
                            if (!product || !product._product) return '';
                            
                            const p = product._product;
                            const price = p.lowest_price;
                            const regularPrice = product.regular_price_override || Math.round(price * 1.5);
                            const savings = regularPrice - price;
                            const discountPct = Math.round((savings / regularPrice) * 100);
                            const isDefault = index === 2; // Default to third option
                            
                            return `
                            <div class="package-card ${isDefault ? 'selected' : ''}" data-product-id="${p.id}">
                                ${product.is_featured ? `
                                <div class="package-badge">
                                    <svg class="icon-pop" viewBox="0 0 20 20" fill="currentColor">
                                        <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"></path>
                                    </svg>
                                    ${escapeHtml(product.tier_label || 'Most Popular')}
                                </div>
                                ` : ''}
                                <div class="left">
                                    <div class="icon-check">
                                        ${isDefault ? getCheckedIcon() : getEmptyIcon()}
                                    </div>
                                    <div>
                                        <h4>${escapeHtml(product.tier_label || p.name)}</h4>
                                        <p class="text-xs muted txt-discount">${discountPct}% Discount!</p>
                                    </div>
                                </div>
                                <div class="right">
                                    <div>
                                        <span class="text-sm muted strike txt-reg">$${(regularPrice / 100).toFixed(2)}</span>
                                        <span class="text-2xl semi-bold text-blue txt-price">
                                            $${(price / 100).toFixed(2)}
                                        </span>
                                        <p class="text-xs text-green txt-save">
                                            You Save: $${(savings / 100).toFixed(2)}
                                        </p>
                                    </div>
                                </div>
                            </div>
                            `;
                        }).join('')}
                    </div>

                    <div class="center">
                        <button class="btn btn-primary text-lg" onclick="processCheckout()">
                            Order Now
                        </button>
                    </div>
                </div>
            </section>

            <div class="checkout-info">
                <p>🔒 Secure checkout • 💳 All major cards accepted • 📦 Ships within 24 hours</p>
            </div>
        </div>
    </section>

    ${showBenefits ? renderBenefitsSection() : ''}

    <style>
        /* Four-Tier Specific Styles */
        .countdown-banner {
            background: linear-gradient(90deg, hsl(0, 85%, 50%) 0%, hsl(10, 85%, 55%) 100%);
            color: white;
            padding: 0.75rem 1rem;
            text-align: center;
        }
        
        .countdown-timer {
            display: inline-block;
            min-width: 4rem;
            font-family: 'Courier New', monospace;
            font-size: 1.125rem;
            color: #ffef03;
            font-weight: bold;
        }
        
        .hero-section {
            padding: 3rem 1rem;
        }
        
        .hero-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 3rem;
            align-items: center;
        }
        
        @media (min-width: 768px) {
            .hero-grid {
                grid-template-columns: repeat(2, 1fr);
            }
        }
        
        .hero-title {
            font-size: 2.5rem;
            font-weight: 700;
            line-height: 1.2;
            margin-bottom: 1.5rem;
        }
        
        .hero-badges {
            display: flex;
            flex-wrap: wrap;
            gap: 1rem;
        }
        
        .badge {
            background-color: #3b82f6;
            color: white;
            padding: 0.5rem 1rem;
            border-radius: 9999px;
            font-size: 0.875rem;
            font-weight: 600;
        }
        
        .hero-image {
            width: 100%;
            max-width: 28rem;
            filter: drop-shadow(0 25px 25px rgba(0, 0, 0, 0.15));
        }
        
        .select-item {
            background-color: #f1f5f9;
            padding: 4rem 2rem;
            border-radius: 1rem;
        }
        
        .package-card {
            padding: 2rem;
            border: 2px solid #e2e8f0;
            border-radius: 1rem;
            cursor: pointer;
            transition: all 0.2s;
            position: relative;
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2rem;
            background-color: white;
        }
        
        .package-card:hover {
            border-color: #16a34a;
            background-color: rgba(22, 163, 74, 0.05);
            transform: translateY(-2px);
        }
        
        .package-card.selected {
            border-color: #3b84f6;
            background-color: rgba(59, 132, 246, 0.05);
        }
        
        .package-badge {
            position: absolute;
            top: -1rem;
            right: -1rem;
            background-color: #fbbf24;
            color: #1f2937;
            padding: 0.5rem 1rem;
            border-radius: 9999px;
            font-weight: 600;
            font-size: 0.875rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        
        .icon-pop {
            width: 1rem;
            height: 1rem;
            fill: currentColor;
        }
        
        .left {
            display: flex;
            align-items: center;
            gap: 1.5rem;
        }
        
        .right {
            text-align: right;
        }
        
        .txt-discount {
            background-color: rgba(251, 191, 36, 0.2);
            color: #92400e;
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.875rem;
            font-weight: 600;
            display: inline-block;
            margin-top: 0.5rem;
        }
        
        .txt-price {
            font-size: 1.5rem;
            font-weight: 600;
            color: #3b82f6;
            display: block;
            margin: 0.5rem 0;
        }
        
        .txt-save {
            color: #16a34a;
            font-weight: 500;
        }
        
        .btn-primary {
            background-color: #3b84f6;
            color: white;
            padding: 1.5rem 3rem;
            border-radius: 0.75rem;
            font-weight: 600;
            transition: all 0.2s;
            border: none;
            cursor: pointer;
            font-size: 1.125rem;
        }
        
        .btn-primary:hover {
            background-color: #16a34a;
            transform: translateY(-2px);
            box-shadow: 0 10px 30px -10px rgba(22, 163, 74, 0.5);
        }
    </style>

    <script>
        // Package selection and countdown timer
        let selectedPackageId = null;
        let timeLeft = ${countdownMinutes} * 60;
        
        document.addEventListener('DOMContentLoaded', function() {
            // Set default selected package
            const defaultCard = document.querySelector('.package-card.selected');
            if (defaultCard) {
                selectedPackageId = defaultCard.dataset.productId;
            }
            
            // Add click handlers to package cards
            document.querySelectorAll('.package-card').forEach(card => {
                card.addEventListener('click', function() {
                    selectPackage(this);
                });
            });
            
            // Start countdown
            updateCountdown();
            setInterval(updateCountdown, 1000);
        });
        
        function selectPackage(card) {
            // Remove selected class from all cards
            document.querySelectorAll('.package-card').forEach(c => {
                c.classList.remove('selected');
                c.querySelector('.icon-check').innerHTML = '${getEmptyIcon()}';
            });
            
            // Add selected class to clicked card
            card.classList.add('selected');
            card.querySelector('.icon-check').innerHTML = '${getCheckedIcon()}';
            selectedPackageId = card.dataset.productId;
        }
        
        function updateCountdown() {
            const minutes = Math.floor(timeLeft / 60);
            const seconds = timeLeft % 60;
            
            const countdownEl = document.getElementById('countdown');
            if (countdownEl) {
                countdownEl.textContent = 
                    String(minutes).padStart(2, '0') + ':' + String(seconds).padStart(2, '0');
            }
            
            if (timeLeft > 0) {
                timeLeft--;
            } else {
                expireDiscount();
            }
        }
        
        function expireDiscount() {
            const bannerText = document.getElementById('bannerText');
            if (bannerText) {
                bannerText.textContent = 'Discount has expired. Regular pricing applies.';
            }
            
            const countdownEl = document.getElementById('countdown');
            if (countdownEl) {
                countdownEl.textContent = '00:00';
                countdownEl.style.color = '#ff6b6b';
            }
        }
        
        function processCheckout() {
            if (selectedPackageId) {
                checkout(selectedPackageId);
            } else {
                alert('Please select a package');
            }
        }
    </script>
    `;
}

// Helper functions for icons
function getCheckedIcon() {
    return `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="12" fill="#3b84f6" />
        <path d="M20 6 9 17l-5-5" fill="none" stroke="#ffffff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
}

function getEmptyIcon() {
    return `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="11" fill="none" stroke="#64758b" stroke-width="2" />
    </svg>`;
}

// Benefits section for four-tier template
function renderBenefitsSection() {
    return `
    <section class="benefits-section">
        <div class="container">
            <h2 class="section-title">Why Choose Our Product</h2>
            <div class="benefits-grid">
                <div class="benefit-item">
                    <div class="benefit-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/>
                        </svg>
                    </div>
                    <h3 class="benefit-title">Premium Quality</h3>
                    <p class="benefit-description">Lab tested and verified for purity</p>
                </div>
                <div class="benefit-item">
                    <div class="benefit-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect width="16" height="10" x="2" y="7" rx="2" ry="2"/>
                            <line x1="22" x2="22" y1="11" y2="13"/>
                        </svg>
                    </div>
                    <h3 class="benefit-title">Fast Results</h3>
                    <p class="benefit-description">Feel the difference in just days</p>
                </div>
                <div class="benefit-item">
                    <div class="benefit-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>
                        </svg>
                    </div>
                    <h3 class="benefit-title">Guaranteed Safe</h3>
                    <p class="benefit-description">30-day money-back guarantee</p>
                </div>
                <div class="benefit-item">
                    <div class="benefit-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/>
                            <path d="M3 6h18"/>
                        </svg>
                    </div>
                    <h3 class="benefit-title">Free Shipping</h3>
                    <p class="benefit-description">On all orders, ships in 24 hours</p>
                </div>
            </div>
        </div>
    </section>
    `;
}

// Add this to your main renderTemplate function's switch statement:


/**
 * Sales Letter Template
 */
function renderSalesLetter(product, heroTitle, heroSubtitle) {
    if (!product || !product._product) return '<div class="error">Product not found</div>';
    
    const p = product._product;
    const description = product.custom_description_override || p.description || '';
    const benefits = p.metadata?.benefits ? p.metadata.benefits.split('|') : [];
    const price = (p.lowest_price / 100).toFixed(2);
    
    return `
    <div class="sales-letter">
        <div class="container-narrow">
            <header class="letter-header">
                <h1>${escapeHtml(heroTitle)}</h1>
                <p class="subtitle">${escapeHtml(heroSubtitle)}</p>
            </header>
            
            <article class="letter-body">
                <p class="lead">${escapeHtml(description)}</p>
                
                <h2>Here's What You'll Get:</h2>
                <ul class="benefits-list-large">
                    ${benefits.map(b => `<li>✓ ${escapeHtml(b.trim())}</li>`).join('')}
                </ul>
                
                <div class="letter-cta">
                    <h2>Special Price Today</h2>
                    <div class="price-box">
                        <span class="price-large">${price}</span>
                    </div>
                    <button class="cta-button-large" onclick="checkout('${p.id}')">
                        Yes! I Want This Now
                    </button>
                </div>
            </article>
        </div>
    </div>
    `;
}

/**
 * Video Sales Template
 */
function renderVideoSales(product, heroTitle, heroSubtitle) {
    if (!product || !product._product) return '<div class="error">Product not found</div>';
    
    const p = product._product;
    const price = (p.lowest_price / 100).toFixed(2);
    
    return `
    <div class="video-sales">
        <div class="container">
            <header class="video-header">
                <h1>${escapeHtml(heroTitle)}</h1>
                <p class="subtitle">${escapeHtml(heroSubtitle)}</p>
            </header>
            
            <div class="video-container">
                <div class="video-placeholder">
                    <p>Video placeholder - configure video URL in settings</p>
                </div>
            </div>
            
            <div class="video-cta">
                <div class="price">${price}</div>
                <button class="cta-button-large" onclick="checkout('${p.id}')">
                    Get Instant Access
                </button>
            </div>
        </div>
    </div>
    `;
}

/**
 * Email Capture Template
 */
function renderEmailCapture(product, heroTitle, heroSubtitle) {
    return `
    <div class="email-capture">
        <div class="container-narrow">
            <header class="capture-header">
                <h1>${escapeHtml(heroTitle)}</h1>
                <p class="subtitle">${escapeHtml(heroSubtitle)}</p>
            </header>
            
            <form class="email-form" onsubmit="captureEmail(event)">
                <input type="email" 
                       name="email" 
                       placeholder="Enter your email address" 
                       required
                       class="email-input">
                <button type="submit" class="cta-button-large">
                    Get Instant Access
                </button>
            </form>
            
            <p class="privacy-note">We respect your privacy. Unsubscribe at any time.</p>
        </div>
    </div>
    `;
}

/**
 * Subscription Focus Template
 */
function renderSubscriptionFocus(product, heroTitle, heroSubtitle) {
    if (!product || !product._product) return '<div class="error">Product not found</div>';
    
    const p = product._product;
    const benefits = p.metadata?.benefits ? p.metadata.benefits.split('|') : [];
    const price = (p.lowest_price / 100).toFixed(2);
    
    return `
    <div class="subscription-page">
        <div class="container">
            <header class="subscription-header">
                <h1>${escapeHtml(heroTitle)}</h1>
                <p class="subtitle">${escapeHtml(heroSubtitle)}</p>
            </header>
            
            <div class="subscription-benefits">
                <h2>Member Benefits</h2>
                <div class="benefits-grid">
                    ${benefits.map(b => `
                    <div class="benefit-card">
                        <div class="benefit-icon">✓</div>
                        <p>${escapeHtml(b.trim())}</p>
                    </div>
                    `).join('')}
                </div>
            </div>
            
            <div class="subscription-cta">
                <div class="price-info">
                    <span class="price">${price}</span>
                    <span class="interval">/month</span>
                </div>
                <button class="cta-button-large" onclick="checkout('${p.id}')">
                    Start Your Membership
                </button>
                <p class="cancel-note">Cancel anytime. No long-term commitments.</p>
            </div>
        </div>
    </div>
    `;
}

/**
 * Configurable Product Template (with custom fields)
 */
function renderConfigurableProduct(product, heroTitle, heroSubtitle, customFields = []) {
    if (!product || !product._product) return '<div class="error">Product not found</div>';
    
    const p = product._product;
    const price = (p.lowest_price / 100).toFixed(2);
    const images = p.images || [];
    
    return `
    <div class="configurable-product">
        <div class="container">
            <header class="config-header">
                <h1>${escapeHtml(heroTitle)}</h1>
                <p class="subtitle">${escapeHtml(heroSubtitle)}</p>
            </header>
            
            <div class="product-config">
                ${images.length > 0 ? `
                <div class="product-image">
                    <img src="${escapeHtml(images[0])}" alt="${escapeHtml(p.name)}">
                </div>
                ` : ''}
                
                <div class="config-form">
                    <h2>Customize Your Order</h2>
                    <form id="customForm" onsubmit="checkoutWithOptions(event, '${p.id}')">
                        ${customFields.map(field => renderCustomField(field)).join('')}
                        
                        <div class="config-price">
                            <span class="price">${price}</span>
                        </div>
                        
                        <button type="submit" class="cta-button-large">
                            Add to Cart
                        </button>
                    </form>
                </div>
            </div>
        </div>
    </div>
    `;
}

/**
 * Custom Template (flexible layout)
 */
function renderCustomTemplate(products, heroTitle, heroSubtitle) {
    return `
    <div class="custom-template">
        <div class="container">
            <header class="custom-header">
                <h1>${escapeHtml(heroTitle)}</h1>
                <p class="subtitle">${escapeHtml(heroSubtitle)}</p>
            </header>
            
            <div class="custom-products">
                ${products.map(product => {
                    if (!product._product) return '';
                    const p = product._product;
                    const price = (p.lowest_price / 100).toFixed(2);
                    const images = p.images || [];
                    
                    return `
                    <div class="custom-product-card">
                        ${images.length > 0 ? `
                        <img src="${escapeHtml(images[0])}" alt="${escapeHtml(p.name)}">
                        ` : ''}
                        <h3>${escapeHtml(product.tier_label || p.name)}</h3>
                        <div class="price">${price}</div>
                        <button class="cta-button" onclick="checkout('${p.id}')">
                            Buy Now
                        </button>
                    </div>
                    `;
                }).join('')}
            </div>
        </div>
    </div>
    `;
}

/**
 * Render custom form field
 */
function renderCustomField(field) {
    const required = field.required ? 'required' : '';
    const fieldId = `field_${field.field_id}`;
    
    switch (field.type) {
        case 'text':
            return `
            <div class="form-field">
                <label for="${fieldId}">${escapeHtml(field.label)}</label>
                <input type="text" 
                       id="${fieldId}" 
                       name="${field.field_id}"
                       maxlength="${field.max_length || 100}"
                       ${required}>
            </div>
            `;
        
        case 'dropdown':
            return `
            <div class="form-field">
                <label for="${fieldId}">${escapeHtml(field.label)}</label>
                <select id="${fieldId}" name="${field.field_id}" ${required}>
                    <option value="">-- Select --</option>
                    ${field.options.map(opt => 
                        `<option value="${escapeHtml(opt)}">${escapeHtml(opt)}</option>`
                    ).join('')}
                </select>
            </div>
            `;
        
        case 'radio':
            return `
            <div class="form-field">
                <label>${escapeHtml(field.label)}</label>
                <div class="radio-group">
                    ${field.options.map(opt => `
                    <label class="radio-label">
                        <input type="radio" 
                               name="${field.field_id}" 
                               value="${escapeHtml(opt)}"
                               ${required}>
                        ${escapeHtml(opt)}
                    </label>
                    `).join('')}
                </div>
            </div>
            `;
        
        case 'checkbox':
            return `
            <div class="form-field">
                <label class="checkbox-label">
                    <input type="checkbox" 
                           name="${field.field_id}"
                           ${required}>
                    ${escapeHtml(field.label)}
                </label>
            </div>
            `;
        
        case 'textarea':
            return `
            <div class="form-field">
                <label for="${fieldId}">${escapeHtml(field.label)}</label>
                <textarea id="${fieldId}" 
                          name="${field.field_id}"
                          rows="4"
                          ${required}></textarea>
            </div>
            `;
        
        default:
            return '';
    }
}

/**
 * Render testimonials section
 */
function renderTestimonials() {
    return `
    <section class="testimonials-section">
        <div class="container">
            <h2>What Our Customers Say</h2>
            <div class="testimonials-grid">
                <div class="testimonial-card">
                    <p class="testimonial-text">"Amazing product! Exceeded all my expectations."</p>
                    <p class="testimonial-author">- Sarah M.</p>
                </div>
                <div class="testimonial-card">
                    <p class="testimonial-text">"Best purchase I've made this year. Highly recommend!"</p>
                    <p class="testimonial-author">- John D.</p>
                </div>
                <div class="testimonial-card">
                    <p class="testimonial-text">"Outstanding quality and great customer service."</p>
                    <p class="testimonial-author">- Emily R.</p>
                </div>
            </div>
        </div>
    </section>
    `;
}

/**
 * Render FAQ section
 */
function renderFAQ() {
    return `
    <section class="faq-section">
        <div class="container">
            <h2>Frequently Asked Questions</h2>
            <div class="faq-list">
                <div class="faq-item">
                    <h3>How does shipping work?</h3>
                    <p>We ship within 1-2 business days. Tracking information will be emailed to you.</p>
                </div>
                <div class="faq-item">
                    <h3>What's your return policy?</h3>
                    <p>30-day money-back guarantee. No questions asked.</p>
                </div>
                <div class="faq-item">
                    <h3>Is this secure?</h3>
                    <p>Yes! All transactions are processed through Stripe with bank-level security.</p>
                </div>
            </div>
        </div>
    </section>
    `;
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Make functions available globally for preview and Lambda
if (typeof window !== 'undefined') {
    window.renderTemplate = renderTemplate;
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { renderTemplate };
}