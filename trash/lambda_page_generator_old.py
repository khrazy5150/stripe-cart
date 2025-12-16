# lambda_page_generator.py
# Lambda function to generate static landing pages and upload to S3

import json
import boto3
import os
from datetime import datetime, timezone
import logging
from typing import Dict, Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

def lambda_handler(event, context):
    """
    Triggered by API Gateway POST /admin/publish_landing_page
    
    Expected event:
    {
        "action": "publish_landing_page",
        "landing_page_id": "lp_abc123",
        "clientID": "c85d5a3d..."
    }
    """
    try:
        body = json.loads(event.get('body', '{}'))
        action = body.get('action')
        
        if action == 'publish_landing_page':
            return publish_landing_page(body, context)
        else:
            return error_response(f"Unknown action: {action}", 400)
            
    except Exception as e:
        logger.exception("Error in page generator")
        return error_response(str(e), 500)


def publish_landing_page(data: Dict[str, Any], context) -> Dict[str, Any]:
    """
    Generate static HTML and upload to S3
    """
    landing_page_id = data.get('landing_page_id')
    client_id = data.get('clientID')
    
    if not landing_page_id or not client_id:
        return error_response("landing_page_id and clientID required", 400)
    
    # 1. Get app-config for S3 bucket and CDN settings
    app_config = get_app_config()
    bucket_name = app_config.get('landing_pages_bucket', 'landing-pages-prod')
    cdn_base = app_config.get('template_cdn_base', 'https://templates.juniorbay.com/v1')
    
    # 2. Get landing page data from stripe_keys table
    landing_page = get_landing_page_data(client_id, landing_page_id)
    if not landing_page:
        return error_response("Landing page not found", 404)
    
    # 3. Get product details from Stripe
    products_with_details = fetch_product_details(landing_page['products'], client_id)
    landing_page['products'] = products_with_details
    
    # 4. Generate static HTML
    html_content = generate_html(landing_page, cdn_base)
    
    # 5. Minify HTML and inline critical CSS
    html_content = minify_html(html_content)
    
    # 6. Determine S3 path
    seo_prefix = landing_page.get('seo_friendly_prefix', landing_page_id)
    checkout_in_subfolder = landing_page.get('checkout_in_subfolder', False)
    
    if checkout_in_subfolder:
        s3_key = f"{client_id}/{seo_prefix}/checkout/index.html"
    else:
        s3_key = f"{client_id}/{seo_prefix}/index.html"
    
    # 7. Upload to S3
    s3_client.put_object(
        Bucket=bucket_name,
        Key=s3_key,
        Body=html_content.encode('utf-8'),
        ContentType='text/html',
        CacheControl='public, max-age=3600',
        Metadata={
            'landing_page_id': landing_page_id,
            'client_id': client_id,
            'published_at': datetime.now(timezone.utc).isoformat()
        }
    )
    
    # 8. Generate S3 URL
    s3_url = f"https://{bucket_name}.s3.amazonaws.com/{s3_key}"
    # Or CloudFront URL if configured:
    # s3_url = f"https://d1234567890abc.cloudfront.net/{s3_key}"
    
    # 9. Update landing page record with s3_url and status
    update_landing_page_status(client_id, landing_page_id, s3_url)
    
    logger.info(f"Published landing page {landing_page_id} to {s3_url}")
    
    return {
        'statusCode': 200,
        'headers': cors_headers(),
        'body': json.dumps({
            'success': True,
            'landing_page_id': landing_page_id,
            's3_url': s3_url,
            'published_at': datetime.now(timezone.utc).isoformat()
        })
    }


def get_app_config() -> Dict[str, Any]:
    """
    Get configuration from app-config table
    """
    table = dynamodb.Table('app-config')
    env = os.environ.get('ENVIRONMENT', 'dev')
    
    response = table.get_item(
        Key={
            'config_key': 'landing_page_config',
            'environment': env
        }
    )
    
    return response.get('Item', {}).get('value', {})


def get_landing_page_data(client_id: str, landing_page_id: str) -> Dict[str, Any]:
    """
    Get landing page configuration from stripe_keys table
    """
    table = dynamodb.Table('stripe_keys')
    
    response = table.get_item(Key={'clientID': client_id})
    item = response.get('Item')
    
    if not item:
        return None
    
    landing_pages = item.get('landing_pages', [])
    
    for page in landing_pages:
        if page.get('landing_page_id') == landing_page_id:
            return page
    
    return None


def fetch_product_details(products: list, client_id: str) -> list:
    """
    Fetch full product details from Stripe for each product
    """
    # This would call Stripe API to get product details
    # For now, return products as-is
    # In production, you'd:
    # 1. Get Stripe keys for client_id
    # 2. Call Stripe API for each product_id
    # 3. Enrich products list with full details (images, prices, descriptions, benefits)
    
    import stripe
    
    # Get Stripe keys
    table = dynamodb.Table('stripe_keys')
    response = table.get_item(Key={'clientID': client_id})
    item = response.get('Item', {})
    
    mode = item.get('mode', 'test')
    sk_key = item.get(f'sk_{mode}')
    
    if not sk_key:
        logger.error(f"No Stripe key found for {client_id}")
        return products
    
    # Decrypt if encrypted
    sk_key = decrypt_stripe_key(sk_key)
    
    stripe.api_key = sk_key
    
    enriched_products = []
    for product in products:
        product_id = product.get('product_id')
        try:
            stripe_product = stripe.Product.retrieve(product_id)
            prices = stripe.Price.list(product=product_id, active=True)
            
            product['_product'] = {
                'id': stripe_product.id,
                'name': stripe_product.name,
                'description': stripe_product.description,
                'images': stripe_product.images,
                'metadata': stripe_product.metadata,
                'lowest_price': min([p.unit_amount for p in prices.data]) if prices.data else 0
            }
            enriched_products.append(product)
        except Exception as e:
            logger.error(f"Error fetching product {product_id}: {e}")
            enriched_products.append(product)
    
    return enriched_products


def generate_html(landing_page: Dict[str, Any], cdn_base: str) -> str:
    """
    Generate static HTML from landing page configuration
    Uses same template logic as client-side preview
    """
    template_type = landing_page.get('template_type', 'single-product-hero')
    hero_title = landing_page.get('hero_title', '')
    hero_subtitle = landing_page.get('hero_subtitle', '')
    guarantee = landing_page.get('guarantee', '')
    products = landing_page.get('products', [])
    layout_options = landing_page.get('layout_options', {})
    custom_css = landing_page.get('custom_css', '')
    custom_js = landing_page.get('custom_js', '')
    analytics_pixels = landing_page.get('analytics_pixels', {})
    
    color_scheme = layout_options.get('color_scheme', {
        'primary': '#007bff',
        'accent': '#28a745'
    })
    
    # Sort products
    sorted_products = sorted(products, key=lambda p: p.get('display_order', 0))
    
    # Generate template-specific content
    # (This would import the template rendering logic from template-library.js
    # but adapted for Python)
    
    content_html = render_template_content(template_type, sorted_products, hero_title, hero_subtitle, landing_page)
    
    # Build complete HTML
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{escape_html(hero_title or 'Special Offer')}</title>
    
    <!-- Base Styles -->
    <link rel="stylesheet" href="{cdn_base}/base/styles.css">
    
    <!-- Template-specific Styles -->
    <link rel="stylesheet" href="{cdn_base}/{template_type}/styles.css">
    
    <style>
        :root {{
            --primary-color: {color_scheme['primary']};
            --accent-color: {color_scheme['accent']};
        }}
        {custom_css}
    </style>
    
    {generate_analytics_scripts(analytics_pixels)}
</head>
<body>
    {content_html}
    
    {generate_guarantee_section(guarantee) if guarantee else ''}
    {generate_testimonials_section() if layout_options.get('show_testimonials') else ''}
    {generate_faq_section() if layout_options.get('show_faq') else ''}
    
    <footer class="footer">
        <div class="container">
            <p>&copy; {datetime.now().year} All rights reserved.</p>
        </div>
    </footer>
    
    <!-- Base Scripts -->
    <script src="{cdn_base}/base/scripts.js"></script>
    <script src="{cdn_base}/{template_type}/scripts.js"></script>
    
    {f"<script>{custom_js}</script>" if custom_js else ''}
</body>
</html>"""
    
    return html


def render_template_content(template_type: str, products: list, hero_title: str, 
                            hero_subtitle: str, landing_page: Dict) -> str:
    """
    Render template-specific HTML content
    This mirrors the JavaScript template rendering logic
    """
    # Implementation would match the template-library.js functions
    # For brevity, showing single-product-hero example:
    
    if template_type == 'single-product-hero':
        if not products or not products[0].get('_product'):
            return '<div class="error">Product not found</div>'
        
        product = products[0]
        p = product['_product']
        description = product.get('custom_description_override') or p.get('description', '')
        benefits = p.get('metadata', {}).get('benefits', '').split('|') if p.get('metadata', {}).get('benefits') else []
        images = p.get('images', [])
        price = f"{p.get('lowest_price', 0) / 100:.2f}"
        
        benefits_html = ''.join([f'<li>✓ {escape_html(b.strip())}</li>' for b in benefits])
        
        return f"""
        <header class="hero hero-single">
            <div class="container">
                <div class="hero-content">
                    <div class="hero-text">
                        <h1>{escape_html(hero_title)}</h1>
                        <p class="hero-subtitle">{escape_html(hero_subtitle)}</p>
                        {f'<p class="product-description">{escape_html(description)}</p>' if description else ''}
                        
                        {f'<ul class="benefits-list">{benefits_html}</ul>' if benefits_html else ''}
                        
                        <div class="price-cta">
                            <div class="price">${price}</div>
                            <button class="cta-button" onclick="checkout('{p['id']}')">
                                Buy Now
                            </button>
                        </div>
                    </div>
                    
                    {f'<div class="hero-image"><img src="{escape_html(images[0])}" alt="{escape_html(p['name'])}"></div>' if images else ''}
                </div>
            </div>
        </header>
        """
    
    # Add other template types similarly
    return ''


def generate_analytics_scripts(pixels: Dict[str, str]) -> str:
    """Generate analytics tracking scripts"""
    scripts = []
    
    if pixels.get('google'):
        scripts.append(f"""
        <!-- Google Analytics -->
        <script async src="https://www.googletagmanager.com/gtag/js?id={pixels['google']}"></script>
        <script>
            window.dataLayer = window.dataLayer || [];
            function gtag(){{dataLayer.push(arguments);}}
            gtag('js', new Date());
            gtag('config', '{pixels['google']}');
        </script>
        """)
    
    if pixels.get('meta'):
        scripts.append(f"""
        <!-- Meta Pixel -->
        <script>
            !function(f,b,e,v,n,t,s)
            {{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?
            n.callMethod.apply(n,arguments):n.queue.push(arguments)}};
            if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
            n.queue=[];t=b.createElement(e);t.async=!0;
            t.src=v;s=b.getElementsByTagName(e)[0];
            s.parentNode.insertBefore(t,s)}}(window, document,'script',
            'https://connect.facebook.net/en_US/fbevents.js');
            fbq('init', '{pixels['meta']}');
            fbq('track', 'PageView');
        </script>
        """)
    
    return '\n'.join(scripts)


def generate_guarantee_section(guarantee: str) -> str:
    """Generate guarantee section HTML"""
    return f"""
    <section class="guarantee-section">
        <div class="container">
            <div class="guarantee-badge">
                <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
                    <polyline points="9 12 11 14 15 10"></polyline>
                </svg>
            </div>
            <h3>Our Guarantee</h3>
            <p>{escape_html(guarantee)}</p>
        </div>
    </section>
    """


def generate_testimonials_section() -> str:
    """Generate testimonials section"""
    return """
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
    """


def generate_faq_section() -> str:
    """Generate FAQ section"""
    return """
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
    """


def minify_html(html: str) -> str:
    """
    Minify HTML by removing unnecessary whitespace
    In production, use a proper minifier library
    """
    import re
    # Remove comments
    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
    # Remove whitespace between tags
    html = re.sub(r'>\s+<', '><', html)
    # Remove leading/trailing whitespace
    html = html.strip()
    return html


def decrypt_stripe_key(encrypted_key: str) -> str:
    """
    Decrypt Stripe secret key using KMS
    """
    kms_client = boto3.client('kms')
    kms_key_arn = os.environ.get('STRIPE_KMS_KEY_ARN')
    
    try:
        response = kms_client.decrypt(
            CiphertextBlob=bytes.fromhex(encrypted_key),
            KeyId=kms_key_arn,
            EncryptionContext={'app': 'stripe-cart'}
        )
        return response['Plaintext'].decode('utf-8')
    except Exception as e:
        logger.error(f"KMS decrypt error: {e}")
        # If not encrypted, return as-is (for backward compatibility)
        return encrypted_key


def update_landing_page_status(client_id: str, landing_page_id: str, s3_url: str):
    """
    Update landing page status to 'published' and save S3 URL
    """
    table = dynamodb.Table('stripe_keys')
    
    # Get current item
    response = table.get_item(Key={'clientID': client_id})
    item = response.get('Item', {})
    
    landing_pages = item.get('landing_pages', [])
    
    # Find and update the landing page
    for i, page in enumerate(landing_pages):
        if page.get('landing_page_id') == landing_page_id:
            landing_pages[i]['status'] = 'published'
            landing_pages[i]['s3_url'] = s3_url
            landing_pages[i]['published_at'] = datetime.now(timezone.utc).isoformat()
            break
    
    # Update DynamoDB
    table.update_item(
        Key={'clientID': client_id},
        UpdateExpression='SET landing_pages = :pages, updated_at = :updated',
        ExpressionAttributeValues={
            ':pages': landing_pages,
            ':updated': datetime.now(timezone.utc).isoformat()
        }
    )


def escape_html(text: str) -> str:
    """Escape HTML special characters"""
    if not text:
        return ''
    return (text
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace("'", '&#39;'))


def cors_headers() -> Dict[str, str]:
    """Return CORS headers"""
    return {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,Authorization,X-Client-Id',
        'Access-Control-Allow-Methods': 'OPTIONS,POST,GET,PUT,DELETE'
    }


def error_response(message: str, status_code: int = 400) -> Dict[str, Any]:
    """Return error response"""
    return {
        'statusCode': status_code,
        'headers': cors_headers(),
        'body': json.dumps({'error': message})
    }