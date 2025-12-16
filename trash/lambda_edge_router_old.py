# lambda_edge_router.py
# Lambda@Edge function for CloudFront to route custom domains to S3 landing pages

import json
import boto3
import logging
from typing import Dict, Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')

def lambda_handler(event, context):
    """
    Lambda@Edge Origin Request handler
    
    Triggered when CloudFront receives a request
    Routes based on:
    - Host header (custom domain or {clientid}.juniorbay.com)
    - Path (/{seo-friendly-prefix}/)
    
    Returns modified origin request pointing to correct S3 object
    """
    request = event['Records'][0]['cf']['request']
    headers = request['headers']
    uri = request['uri']
    
    # Get host from headers
    host = headers.get('host', [{}])[0].get('value', '')
    
    logger.info(f"Lambda@Edge: host={host}, uri={uri}")
    
    # Determine clientID and landing page
    client_id, landing_page_id = resolve_route(host, uri)
    
    if not client_id or not landing_page_id:
        # Return 404 if route can't be resolved
        return generate_404_response()
    
    # Get landing page configuration
    landing_page = get_landing_page_config(client_id, landing_page_id)
    
    if not landing_page:
        return generate_404_response()
    
    # Determine S3 key based on landing page config
    seo_prefix = landing_page.get('seo_friendly_prefix', landing_page_id)
    checkout_in_subfolder = landing_page.get('checkout_in_subfolder', False)
    
    if checkout_in_subfolder:
        s3_key = f"/{client_id}/{seo_prefix}/checkout/index.html"
    else:
        s3_key = f"/{client_id}/{seo_prefix}/index.html"
    
    # Modify request URI to point to correct S3 object
    request['uri'] = s3_key
    
    logger.info(f"Routing to S3 key: {s3_key}")
    
    return request


def resolve_route(host: str, uri: str) -> tuple:
    """
    Resolve host + path to (clientID, landing_page_id)
    
    Scenarios:
    1. Custom domain: sweetpottery.com/summer-sale → lookup in app-config
    2. Subdomain: client123.juniorbay.com/summer-sale → extract from subdomain
    """
    
    # Remove trailing slash from URI
    uri = uri.rstrip('/')
    
    # Extract SEO prefix from path
    path_parts = [p for p in uri.split('/') if p]
    seo_prefix = path_parts[0] if path_parts else None
    
    # Check if custom domain
    if not host.endswith('.juniorbay.com'):
        # Custom domain - lookup in mapping
        client_id, landing_page_id = lookup_custom_domain(host, seo_prefix)
        return client_id, landing_page_id
    
    # Subdomain pattern: {clientID}.juniorbay.com
    subdomain = host.split('.')[0]
    
    if subdomain and subdomain != 'www':
        # Find landing page by clientID + seo_prefix
        landing_page_id = lookup_landing_page_by_prefix(subdomain, seo_prefix)
        return subdomain, landing_page_id
    
    return None, None


def lookup_custom_domain(domain: str, seo_prefix: str) -> tuple:
    """
    Lookup clientID and landing_page_id from custom domain mapping
    
    app-config structure:
    {
        "config_key": "custom_domain_mappings",
        "environment": "prod",
        "value": {
            "sweetpottery.com": {
                "clientID": "c85d5a3d...",
                "landing_page_map": {
                    "summer-sale": "lp_abc123",
                    "winter-promo": "lp_xyz789"
                }
            }
        }
    }
    """
    try:
        table = dynamodb.Table('app-config')
        
        response = table.get_item(
            Key={
                'config_key': 'custom_domain_mappings',
                'environment': 'prod'  # or get from environment variable
            }
        )
        
        mappings = response.get('Item', {}).get('value', {})
        domain_config = mappings.get(domain)
        
        if not domain_config:
            logger.warning(f"No mapping found for domain: {domain}")
            return None, None
        
        client_id = domain_config.get('clientID')
        landing_page_map = domain_config.get('landing_page_map', {})
        landing_page_id = landing_page_map.get(seo_prefix)
        
        return client_id, landing_page_id
        
    except Exception as e:
        logger.error(f"Error looking up custom domain: {e}")
        return None, None


def lookup_landing_page_by_prefix(client_id: str, seo_prefix: str) -> str:
    """
    Find landing_page_id by clientID and seo_prefix
    """
    try:
        table = dynamodb.Table('stripe_keys')
        
        response = table.get_item(Key={'clientID': client_id})
        item = response.get('Item')
        
        if not item:
            return None
        
        landing_pages = item.get('landing_pages', [])
        
        for page in landing_pages:
            if page.get('seo_friendly_prefix') == seo_prefix:
                return page.get('landing_page_id')
        
        return None
        
    except Exception as e:
        logger.error(f"Error looking up landing page: {e}")
        return None


def get_landing_page_config(client_id: str, landing_page_id: str) -> Dict[str, Any]:
    """
    Get landing page configuration
    """
    try:
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
        
    except Exception as e:
        logger.error(f"Error getting landing page config: {e}")
        return None


def generate_404_response() -> Dict[str, Any]:
    """
    Generate 404 response for Lambda@Edge
    """
    return {
        'status': '404',
        'statusDescription': 'Not Found',
        'headers': {
            'content-type': [{
                'key': 'Content-Type',
                'value': 'text/html'
            }]
        },
        'body': '''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Page Not Found</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                    background: #f5f5f5;
                }
                .container {
                    text-align: center;
                    padding: 40px;
                    background: white;
                    border-radius: 8px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }
                h1 { color: #333; }
                p { color: #666; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>404 - Page Not Found</h1>
                <p>The page you're looking for doesn't exist.</p>
            </div>
        </body>
        </html>
        '''
    }


# Notes for deployment:
# 
# 1. This Lambda@Edge function must be deployed to us-east-1 (N. Virginia)
# 2. Attach to CloudFront distribution as "Origin Request" trigger
# 3. Give Lambda execution role DynamoDB read permissions
# 4. Keep function size small (<1MB) for Lambda@Edge limits
# 5. Cold start latency matters - optimize imports and caching
# 
# CloudFront distribution configuration:
# - Origin: S3 bucket (landing-pages-prod)
# - Behavior: Default (*)
# - Origin Request: This Lambda function
# - Cache Policy: Managed-CachingOptimized
# - Origin Request Policy: Managed-AllViewer
# 
# For custom domains:
# - Add CNAME record in Route 53 pointing to CloudFront distribution
# - Add domain to CloudFront Alternate Domain Names (CNAMEs)
# - Attach ACM SSL certificate covering the custom domain