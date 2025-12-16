import os
import json
import boto3
import logging
from typing import Dict, Any
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')

def _json_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,POST,GET,PUT",
    }

def _ok(body: Dict[str, Any], code: int = 200) -> Dict[str, Any]:
    return {"statusCode": code, "headers": _json_headers(), "body": json.dumps(body)}

def _err(msg: str, code: int = 400) -> Dict[str, Any]:
    logger.warning(msg)
    return {"statusCode": code, "headers": _json_headers(), "body": json.dumps({"error": msg})}

def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body") or "{}"
    try:
        return json.loads(body)
    except Exception:
        return {}

def _require_claim(event, claim):
    """Extract claim from Cognito authorizer."""
    try:
        return event['requestContext']['authorizer']['claims'][claim]
    except KeyError:
        raise RuntimeError(f"Missing auth claim: {claim}")

def get_stripe_keys_table():
    """Get stripe_keys table."""
    table_name = os.environ.get('STRIPE_KEYS_TABLE', 'stripe_keys')
    return dynamodb.Table(table_name)

def _extract_client_id(event: Dict[str, Any]) -> str:
    """
    Extract client ID with priority:
    1. Query string parameter
    2. Request body
    3. Cognito JWT sub claim
    """
    # Try query string
    qs = event.get('queryStringParameters') or {}
    if qs.get('clientID'):
        return qs['clientID']
    
    # Try request body
    data = _parse_body(event)
    if data.get('clientID'):
        return data['clientID']
    
    # Try Cognito claims
    try:
        return _require_claim(event, 'sub')
    except RuntimeError:
        raise ValueError("Unable to determine client ID")

def get_tenant_config(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    GET /admin/tenant-config?clientID=xxx
    Get tenant email message configuration.
    """
    try:
        client_id = _extract_client_id(event)
        logger.info(f"Getting tenant config for: {client_id}")
        
        table = get_stripe_keys_table()
        response = table.get_item(Key={'clientID': client_id})
        item = response.get('Item')
        
        if not item:
            return _err("Tenant not found", 404)
        
        # Extract tenant_config or return empty structure
        tenant_config = item.get('tenant_config', {})
        
        return _ok({
            'clientID': client_id,
            'tenant_config': tenant_config
        })
        
    except ValueError as e:
        return _err(str(e), 400)
    except Exception as e:
        logger.exception("Error getting tenant config")
        return _err(f"Failed to get tenant config: {str(e)}", 500)

def update_tenant_config(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    PUT /admin/tenant-config
    Body: {
        "clientID": "xxx",
        "tenant_config": {
            "just_for_you": "...",
            "order_fulfilled": "...",
            "refund": "...",
            "return_label": "...",
            "thank_you": "..."
        }
    }
    """
    try:
        data = _parse_body(event)
        client_id = data.get('clientID')
        
        if not client_id:
            # Try to get from Cognito
            try:
                client_id = _require_claim(event, 'sub')
            except RuntimeError:
                return _err("clientID required", 400)
        
        tenant_config = data.get('tenant_config')
        if not tenant_config:
            return _err("tenant_config is required", 400)
        
        # Validate tenant_config structure
        expected_keys = ['just_for_you', 'order_fulfilled', 'refund', 'return_label', 'thank_you']
        for key in expected_keys:
            if key not in tenant_config:
                logger.warning(f"Missing config key: {key}")
        
        logger.info(f"Updating tenant config for: {client_id}")
        
        table = get_stripe_keys_table()
        now = datetime.now(timezone.utc).isoformat()
        
        # Update tenant_config in stripe_keys table
        table.update_item(
            Key={'clientID': client_id},
            UpdateExpression='SET tenant_config = :config, updated_at = :updated',
            ExpressionAttributeValues={
                ':config': tenant_config,
                ':updated': now
            }
        )
        
        logger.info(f"Successfully updated tenant config for {client_id}")
        
        return _ok({
            'success': True,
            'clientID': client_id,
            'updated_at': now
        })
        
    except Exception as e:
        logger.exception("Error updating tenant config")
        return _err(f"Failed to update tenant config: {str(e)}", 500)

def lambda_handler(event, context):
    """Main router for tenant config API."""
    try:
        method = (event.get("httpMethod") or "").upper()
        path = event.get("path") or ""
        
        logger.info(f"Tenant Config API: {method} {path}")
        
        # CORS preflight
        if method == "OPTIONS":
            return _ok({"ok": True})
        
        if path == "/admin/tenant-config":
            if method == "GET":
                return get_tenant_config(event, context)
            elif method == "PUT":
                return update_tenant_config(event, context)
        
        return _err(f"Unsupported route: {method} {path}", 405)
        
    except Exception as e:
        logger.exception("Unhandled error in tenant_config")
        return _err(f"Internal server error: {str(e)}", 500)