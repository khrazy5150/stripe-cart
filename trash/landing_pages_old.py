# landing_pages.py
# Lambda handler for landing pages CRUD operations

import os
import json
import boto3
import logging
import uuid
import base64
from typing import Dict, Any
from datetime import datetime, timezone
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')

class ConfigError(RuntimeError): pass

def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise ConfigError(f"Missing required environment variable: {name}")
    return v

ENVIRONMENT       = _req("ENVIRONMENT")        # "dev" | "prod"
STRIPE_KEYS_TABLE = _req("STRIPE_KEYS_TABLE")  # e.g., "stripe-keys-dev"
APP_CONFIG_TABLE  = _req("APP_CONFIG_TABLE")   # e.g., "app-config-dev"

_dynamodb = boto3.resource("dynamodb")

def get_stripe_keys_table():
    return _dynamodb.Table(STRIPE_KEYS_TABLE)

def get_app_config_table():
    return _dynamodb.Table(APP_CONFIG_TABLE)

def _json_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,Stripe-Signature,X-Client-Id,X-Offer-Name",
        "Access-Control-Allow-Methods": "OPTIONS,POST,GET,PUT,DELETE",
    }

def _ok(body: Dict[str, Any], code: int = 200) -> Dict[str, Any]:
    # Convert any Decimal objects before serialization
    body = decimal_to_float(body)
    return {"statusCode": code, "headers": _json_headers(), "body": json.dumps(body)}

def _err(msg: str, code: int = 400) -> Dict[str, Any]:
    logger.warning(msg)
    return {"statusCode": code, "headers": _json_headers(), "body": json.dumps({"error": msg})}

def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body") or "{}"
    # Handle base64 encoded body from API Gateway
    if event.get("isBase64Encoded"):
        try:
            body = base64.b64decode(body).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to decode base64 body: {e}")
            return {}
    try:
        return json.loads(body)
    except Exception as e:
        logger.error(f"Failed to parse JSON body: {e}")
        return {}

def decimal_to_float(obj):
    """Convert Decimal objects to float for JSON serialization"""
    if isinstance(obj, list):
        return [decimal_to_float(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: decimal_to_float(value) for key, value in obj.items()}
    elif isinstance(obj, Decimal):
        return float(obj)
    else:
        return obj
    
def _extract_client_id(event: Dict[str, Any]) -> str:
    """Extract client ID from headers, querystring, or body (in that order)."""
    headers = event.get('headers') or {}
    client_id = headers.get('X-Client-Id') or headers.get('x-client-id')

    if not client_id:
        qs = event.get('queryStringParameters') or {}
        if isinstance(qs, dict):
            client_id = qs.get('clientID') or qs.get('clientId') or qs.get('client_id')

    if not client_id:
        data = _parse_body(event)
        client_id = data.get('clientID') or data.get('clientId') or data.get('client_id')

    return client_id

def get_stripe_keys_table():
    """Return the DynamoDB table for Stripe keys. No defaults."""
    try:
        table_name = os.environ["STRIPE_KEYS_TABLE"]
    except KeyError:
        raise RuntimeError(
            "Missing required env var STRIPE_KEYS_TABLE "
            "(e.g., stripe-keys-dev or stripe-keys-prod)."
        )
    return dynamodb.Table(table_name)


def lambda_handler(event, context):
    """Main router for landing pages API"""
    try:
        method = (event.get("httpMethod") or "").upper()
        path = event.get("path") or ""
        
        logger.info(f"Landing Pages API: {method} {path}")
        
        # CORS preflight
        if method == "OPTIONS":
            return _ok({"ok": True})
        
        # Route to appropriate handler
        if path == "/admin/landing-pages":
            if method == "GET":
                return get_landing_pages(event, context)
            elif method == "POST":
                return create_landing_page(event, context)
        
        elif "/admin/landing-pages/" in path:
            landing_page_id = path.split('/')[-1]
            if method == "GET":
                return get_landing_page_details(event, context, landing_page_id)
            elif method == "PUT":
                return update_landing_page(event, context, landing_page_id)
            elif method == "DELETE":
                return archive_landing_page(event, context, landing_page_id)
        
        return _err(f"Unsupported route: {method} {path}", 405)
        
    except Exception as e:
        logger.exception("Unhandled error in landing_pages")
        return _err(f"Internal server error: {str(e)}", 500)


def get_landing_pages(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    GET /admin/landing-pages
    Get all landing pages for a client
    """
    try:
        client_id = _extract_client_id(event)
        if not client_id:
            return _err("clientID required", 400)
        
        logger.info(f"Getting landing pages for: {client_id}")
        
        table = get_stripe_keys_table()
        logger.info(f"Using Stripe keys table: {table.name}")

        response = table.get_item(Key={'clientID': client_id})
        item = response.get('Item')
        
        if not item:
            return _ok({'landing_pages': []})
        
        landing_pages = item.get('landing_pages', [])
        
        # Convert Decimals to float for JSON serialization
        landing_pages = decimal_to_float(landing_pages)
        
        # Sort by created_at descending
        landing_pages.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return _ok({
            'landing_pages': landing_pages,
            'count': len(landing_pages)
        })
        
    except Exception as e:
        logger.exception("Error getting landing pages")
        return _err(f"Failed to get landing pages: {str(e)}", 500)


def create_landing_page(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    POST /admin/landing-pages
    Create a new landing page
    """
    try:
        data = _parse_body(event)
        client_id = _extract_client_id(event)
        
        if not client_id:
            return _err("clientID required", 400)
        
        # Log the parsed data for debugging
        logger.info(f"Parsed request body: {json.dumps(data)}")
        
        # Validate required fields
        required_fields = ['page_name', 'seo_friendly_prefix', 'template_type', 'products']
        for field in required_fields:
            if not data.get(field):
                return _err(f"{field} is required", 400)
        
        # Validate products array
        if not isinstance(data['products'], list) or len(data['products']) == 0:
            return _err("At least one product is required", 400)
        
        # Check plan limits
        plan_check = check_plan_limits(client_id, 'landing_pages')
        if not plan_check['allowed']:
            return _err(plan_check['message'], 403)
        
        # Generate landing page ID
        landing_page_id = f"lp_{uuid.uuid4().hex[:12]}"
        
        # Build landing page object
        now = datetime.now(timezone.utc).isoformat()
        landing_page = {
            'landing_page_id': landing_page_id,
            'page_name': data['page_name'],
            'seo_friendly_prefix': data['seo_friendly_prefix'],
            'template_type': data['template_type'],
            'hero_title': data.get('hero_title', ''),
            'hero_subtitle': data.get('hero_subtitle', ''),
            'guarantee': data.get('guarantee', ''),
            'products': data['products'],
            'source_offer_id': data.get('source_offer_id'),
            'status': data.get('status', 'draft'),
            's3_url': None,
            'checkout_in_subfolder': data.get('checkout_in_subfolder', False),
            'analytics_pixels': data.get('analytics_pixels', {}),
            'custom_css': data.get('custom_css', ''),
            'custom_js': data.get('custom_js', ''),
            'layout_options': data.get('layout_options', {}),
            'custom_fields': data.get('custom_fields', []),
            'email_capture_config': data.get('email_capture_config', {}),
            'analytics': {
                'views': 0,
                'conversions': 0,
                'revenue': 0,
                'last_updated': now
            },
            'ab_test_id': None,
            'created_at': now,
            'updated_at': now,
            'published_at': None
        }
        
        # Add to tenant's landing pages
        table = get_stripe_keys_table()
        logger.info(f"Using Stripe keys table: {table.name}")

        response = table.get_item(Key={'clientID': client_id})
        item = response.get('Item', {})
        
        landing_pages = item.get('landing_pages', [])
        landing_pages.append(landing_page)
        landing_pages = decimal_to_float(landing_pages)
        
        table.update_item(
            Key={'clientID': client_id},
            UpdateExpression='SET landing_pages = :pages, updated_at = :updated',
            ExpressionAttributeValues={
                ':pages': landing_pages,
                ':updated': now
            }
        )
        
        logger.info(f"Created landing page {landing_page_id} for {client_id}")
        
        return _ok({
            'success': True,
            'landing_page_id': landing_page_id,
            'landing_page': landing_page
        })
        
    except Exception as e:
        logger.exception("Error creating landing page")
        return _err(f"Failed to create landing page: {str(e)}", 500)


def get_landing_page_details(event: Dict[str, Any], context, landing_page_id: str) -> Dict[str, Any]:
    """
    GET /admin/landing-pages/{landing_page_id}
    Get details for a specific landing page
    """
    try:
        client_id = _extract_client_id(event)
        if not client_id:
            return _err("clientID required", 400)
        
        table = get_stripe_keys_table()
        logger.info(f"Using Stripe keys table: {table.name}")

        response = table.get_item(Key={'clientID': client_id})
        item = response.get('Item')
        
        if not item:
            return _err("Client not found", 404)
        
        landing_pages = item.get('landing_pages', [])
        landing_pages = decimal_to_float(landing_pages)
        
        for page in landing_pages:
            if page.get('landing_page_id') == landing_page_id:
                return _ok({'landing_page': page})
        
        return _err("Landing page not found", 404)
        
    except Exception as e:
        logger.exception("Error getting landing page details")
        return _err(f"Failed to get landing page: {str(e)}", 500)


def update_landing_page(event: Dict[str, Any], context, landing_page_id: str) -> Dict[str, Any]:
    """
    PUT /admin/landing-pages/{landing_page_id}
    Update an existing landing page
    """
    try:
        data = _parse_body(event)
        client_id = _extract_client_id(event)
        
        if not client_id:
            return _err("clientID required", 400)
        
        # Log the parsed data for debugging
        logger.info(f"Parsed update body: {json.dumps(data)}")
        
        table = get_stripe_keys_table()
        logger.info(f"Using Stripe keys table: {table.name}")

        response = table.get_item(Key={'clientID': client_id})
        item = response.get('Item')
        
        if not item:
            return _err("Client not found", 404)
        
        landing_pages = item.get('landing_pages', [])
        landing_pages = decimal_to_float(landing_pages)
        
        # Find and update the landing page
        found = False
        for i, page in enumerate(landing_pages):
            if page.get('landing_page_id') == landing_page_id:
                found = True
                
                # Update fields
                now = datetime.now(timezone.utc).isoformat()
                landing_pages[i].update({
                    'page_name': data.get('page_name', page.get('page_name')),
                    'seo_friendly_prefix': data.get('seo_friendly_prefix', page.get('seo_friendly_prefix')),
                    'template_type': data.get('template_type', page.get('template_type')),
                    'hero_title': data.get('hero_title', page.get('hero_title')),
                    'hero_subtitle': data.get('hero_subtitle', page.get('hero_subtitle')),
                    'guarantee': data.get('guarantee', page.get('guarantee')),
                    'products': data.get('products', page.get('products')),
                    'source_offer_id': data.get('source_offer_id', page.get('source_offer_id')),
                    'checkout_in_subfolder': data.get('checkout_in_subfolder', page.get('checkout_in_subfolder')),
                    'analytics_pixels': data.get('analytics_pixels', page.get('analytics_pixels')),
                    'custom_css': data.get('custom_css', page.get('custom_css')),
                    'custom_js': data.get('custom_js', page.get('custom_js')),
                    'layout_options': data.get('layout_options', page.get('layout_options')),
                    'custom_fields': data.get('custom_fields', page.get('custom_fields')),
                    'email_capture_config': data.get('email_capture_config', page.get('email_capture_config')),
                    'updated_at': now
                })
                
                # If status changed to draft, clear s3_url
                if data.get('status') == 'draft' and page.get('status') == 'published':
                    landing_pages[i]['status'] = 'draft'
                    landing_pages[i]['s3_url'] = None
                
                break
        
        if not found:
            return _err("Landing page not found", 404)
        
        # Save back to DynamoDB
        table.update_item(
            Key={'clientID': client_id},
            UpdateExpression='SET landing_pages = :pages, updated_at = :updated',
            ExpressionAttributeValues={
                ':pages': landing_pages,
                ':updated': datetime.now(timezone.utc).isoformat()
            }
        )
        
        logger.info(f"Updated landing page {landing_page_id} for {client_id}")
        
        return _ok({
            'success': True,
            'landing_page_id': landing_page_id
        })
        
    except Exception as e:
        logger.exception("Error updating landing page")
        return _err(f"Failed to update landing page: {str(e)}", 500)


def archive_landing_page(event: Dict[str, Any], context, landing_page_id: str) -> Dict[str, Any]:
    """
    DELETE /admin/landing-pages/{landing_page_id}
    Archive (soft delete) a landing page
    """
    try:
        client_id = _extract_client_id(event)
        if not client_id:
            return _err("clientID required", 400)
        
        table = get_stripe_keys_table()
        logger.info(f"Using Stripe keys table: {table.name}")

        response = table.get_item(Key={'clientID': client_id})
        item = response.get('Item')
        
        if not item:
            return _err("Client not found", 404)
        
        landing_pages = item.get('landing_pages', [])
        landing_pages = decimal_to_float(landing_pages)
        
        # Find and mark as archived
        found = False
        for i, page in enumerate(landing_pages):
            if page.get('landing_page_id') == landing_page_id:
                found = True
                landing_pages[i]['status'] = 'archived'
                landing_pages[i]['archived_at'] = datetime.now(timezone.utc).isoformat()
                break
        
        if not found:
            return _err("Landing page not found", 404)
        
        # Save back to DynamoDB
        table.update_item(
            Key={'clientID': client_id},
            UpdateExpression='SET landing_pages = :pages, updated_at = :updated',
            ExpressionAttributeValues={
                ':pages': landing_pages,
                ':updated': datetime.now(timezone.utc).isoformat()
            }
        )
        
        logger.info(f"Archived landing page {landing_page_id} for {client_id}")
        
        return _ok({
            'success': True,
            'landing_page_id': landing_page_id
        })
        
    except Exception as e:
        logger.exception("Error archiving landing page")
        return _err(f"Failed to archive landing page: {str(e)}", 500)


def check_plan_limits(client_id: str, resource_type: str) -> Dict[str, Any]:
    """
    Check if client can create more of a resource based on their plan
    """
    try:
        # Get app-config for plan limits
        config_table = dynamodb.Table(os.environ.get('APP_CONFIG_TABLE', 'app-config'))
        env = os.environ.get('ENVIRONMENT', 'dev')
        
        config_response = config_table.get_item(
            Key={
                'config_key': 'landing_page_config',
                'environment': env
            }
        )
        
        plan_limits = config_response.get('Item', {}).get('value', {}).get('plan_limits', {})
        
        # Get tenant's plan (default to enterprise for now)
        table = get_stripe_keys_table()
        logger.info(f"Using Stripe keys table: {table.name}")
        
        response = table.get_item(Key={'clientID': client_id})
        item = response.get('Item', {})
        
        plan = item.get('plan', {}).get('type', 'enterprise')
        limits = plan_limits.get(plan, plan_limits.get('enterprise', {}))
        
        max_allowed = limits.get(resource_type, -1)
        
        # -1 means unlimited
        if max_allowed == -1:
            return {'allowed': True}
        
        # Count current resources
        current_count = 0
        if resource_type == 'landing_pages':
            landing_pages = item.get('landing_pages', [])
            landing_pages = decimal_to_float(landing_pages)
            # Count non-archived pages
            current_count = len([p for p in landing_pages if p.get('status') != 'archived'])
        
        if current_count >= max_allowed:
            return {
                'allowed': False,
                'message': f"Plan limit reached: {plan} plan allows {max_allowed} {resource_type}"
            }
        
        return {'allowed': True}
        
    except Exception as e:
        logger.error(f"Error checking plan limits: {e}")
        # Default to allowing if check fails
        return {'allowed': True}