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

def get_config_table():
    """Get app-config table."""
    table_name = os.environ.get('APP_CONFIG_TABLE', 'app-config')
    return dynamodb.Table(table_name)

def get_environment():
    """Get current environment."""
    return os.environ.get('ENVIRONMENT', 'dev')

def get_public_config(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    GET /config - Public endpoint for frontend configuration.
    Returns only non-sensitive config values needed by the frontend.
    """
    try:
        table = get_config_table()
        env = get_environment()
        
        # Scan for config items
        response = table.scan()
        items = response.get('Items', [])
        
        while 'LastEvaluatedKey' in response:
            response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            items.extend(response.get('Items', []))
        
        # Build public config (environment-specific values override global)
        config = {}
        for item in items:
            key = item['config_key']
            item_env = item.get('environment', 'global')
            value = item['value']
            
            if item_env == env:
                config[key] = value
            elif item_env == 'global' and key not in config:
                config[key] = value
        
        # Filter to only public-safe values
        public_keys = [
            'cognito_region',
            'cognito_user_pool_id',
            'cognito_client_id',
            'api_base_url',
            'frontend_base_url',
            'frontend_test_dir',
            'frontend_prod_dir',
            'stripe_api_version',
            'environment'
        ]
        
        public_config = {
            'environment': env,
            **{k: config.get(k) for k in public_keys if k in config}
        }
        
        logger.info(f"Returning public config with {len(public_config)} values")
        return _ok(public_config)
        
    except Exception as e:
        logger.exception("Error getting public config")
        return _err(f"Failed to get config: {str(e)}", 500)

def get_admin_config(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    GET /admin/app-config - Get all configuration (admin only).
    Returns complete configuration including sensitive values.
    """
    try:
        # Verify admin access
        try:
            user_id = _require_claim(event, 'sub')
            logger.info(f"Admin config access by user: {user_id}")
        except RuntimeError:
            return _err("Unauthorized", 401)
        
        table = get_config_table()
        env = get_environment()
        
        # Get all config items
        response = table.scan()
        items = response.get('Items', [])
        
        while 'LastEvaluatedKey' in response:
            response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            items.extend(response.get('Items', []))
        
        # Group by environment
        config_by_env = {}
        for item in items:
            item_env = item.get('environment', 'global')
            if item_env not in config_by_env:
                config_by_env[item_env] = {}
            config_by_env[item_env][item['config_key']] = {
                'value': item['value'],
                'description': item.get('description', ''),
                'updated_at': item.get('updated_at', '')
            }
        
        return _ok({
            'current_environment': env,
            'config': config_by_env
        })
        
    except Exception as e:
        logger.exception("Error getting admin config")
        return _err(f"Failed to get admin config: {str(e)}", 500)

def update_admin_config(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    PUT /admin/app-config - Update configuration (admin only).
    Body: {
        "config_key": "some_key",
        "environment": "dev|prod|global",
        "value": "some_value",
        "description": "optional description"
    }
    """
    try:
        # Verify admin access
        try:
            user_id = _require_claim(event, 'sub')
            logger.info(f"Config update by user: {user_id}")
        except RuntimeError:
            return _err("Unauthorized", 401)
        
        data = _parse_body(event)
        
        config_key = data.get('config_key')
        environment = data.get('environment', 'global')
        value = data.get('value')
        description = data.get('description', '')
        
        if not config_key:
            return _err("config_key is required")
        
        if value is None:
            return _err("value is required")
        
        if environment not in ['dev', 'prod', 'staging', 'global']:
            return _err("environment must be one of: dev, prod, staging, global")
        
        table = get_config_table()
        now = datetime.now(timezone.utc).isoformat()
        
        # Update or create config item
        table.put_item(Item={
            'config_key': config_key,
            'environment': environment,
            'value': value,
            'description': description,
            'updated_at': now,
            'updated_by': user_id
        })
        
        logger.info(f"Updated config: {config_key} for environment: {environment}")
        
        return _ok({
            'success': True,
            'config_key': config_key,
            'environment': environment,
            'updated_at': now
        })
        
    except Exception as e:
        logger.exception("Error updating config")
        return _err(f"Failed to update config: {str(e)}", 500)

def lambda_handler(event, context):
    """Main router for config API."""
    try:
        method = (event.get("httpMethod") or "").upper()
        path = event.get("path") or ""
        
        logger.info(f"Config API: {method} {path}")
        
        # CORS preflight
        if method == "OPTIONS":
            return _ok({"ok": True})
        
        # Public config endpoint
        if path == "/config" and method == "GET":
            return get_public_config(event, context)
        
        # Admin config endpoints
        if path == "/admin/app-config":
            if method == "GET":
                return get_admin_config(event, context)
            elif method == "PUT":
                return update_admin_config(event, context)
        
        return _err(f"Unsupported route: {method} {path}", 405)
        
    except Exception as e:
        logger.exception("Unhandled error in config_api")
        return _err(f"Internal server error: {str(e)}", 500)