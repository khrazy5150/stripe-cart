# tenant_config.py
# Strict, env-driven tenant configuration API (no legacy table fallbacks)

import os
import json
import base64
import boto3
from typing import Dict, Any, Optional, Iterable
from datetime import datetime, timezone
from botocore.exceptions import ClientError

# ---------- Strict env (no fallbacks) ----------------------------------------

class ConfigError(RuntimeError): pass

def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise ConfigError(f"Missing required environment variable: {name}")
    return v

ENVIRONMENT       = _req("ENVIRONMENT")          # "dev" | "prod"
STRIPE_KEYS_TABLE = _req("STRIPE_KEYS_TABLE")    # e.g., "stripe-keys-dev"
APP_CONFIG_TABLE  = _req("APP_CONFIG_TABLE")     # e.g., "app-config-dev"

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"

# Only needed if you allow secret updates via this endpoint:
STRIPE_KMS_KEY_ARN = os.environ.get("STRIPE_KMS_KEY_ARN")
ENC_CTX = {"app": "stripe-cart"}

# ---------- AWS clients ------------------------------------------------------

_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_keys_tbl = _dynamodb.Table(STRIPE_KEYS_TABLE)
_cfg_tbl  = _dynamodb.Table(APP_CONFIG_TABLE)
_kms      = boto3.client("kms", region_name=REGION) if STRIPE_KMS_KEY_ARN else None

# ---------- HTTP helpers -----------------------------------------------------

def _cors_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,Stripe-Signature,X-Client-Id,X-Offer-Name",
        "Access-Control-Allow-Methods": "OPTIONS,GET,PUT",
    }

def _ok(body: Dict[str, Any] | list | str | None = None, status: int = 200) -> Dict[str, Any]:
    out = body if isinstance(body, (str, type(None))) else json.dumps(body or {})
    return {"statusCode": status, "headers": _cors_headers(), "body": out or ""}

def _bad(message: str, status: int = 400) -> Dict[str, Any]:
    return _ok({"error": message}, status)

def _parse_json_body(event) -> Dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data
    except Exception as e:
        raise ValueError(f"Invalid JSON body: {e}")

def _extract_client_id(event) -> Optional[str]:
    headers = event.get("headers") or {}
    cid = headers.get("X-Client-Id") or headers.get("x-client-id")
    if not cid:
        qs = event.get("queryStringParameters") or {}
        if isinstance(qs, dict):
            cid = qs.get("clientID") or qs.get("clientId") or qs.get("client_id")
    if not cid:
        try:
            body = _parse_json_body(event)
            cid = body.get("clientID") or body.get("clientId") or body.get("client_id")
        except Exception:
            pass
    return cid

# ---------- Config loader (global + env) ------------------------------------

def _scan_cfg(env: str):
    from boto3.dynamodb.conditions import Attr
    items = []
    fe = Attr("environment").eq(env)
    kwargs = {"FilterExpression": fe}
    while True:
        resp = _cfg_tbl.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items

def load_app_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    for it in _scan_cfg("global"):
        cfg[it["config_key"]] = it.get("value")
    for it in _scan_cfg(ENVIRONMENT):
        cfg[it["config_key"]] = it.get("value")
    cfg["environment"] = ENVIRONMENT
    return cfg

# ---------- Secrets helpers (optional) --------------------------------------

SECRET_FIELDS = {
    "stripe_secret_key",
    "stripe_webhook_secret",
    "shippo_api_key",
    "easypost_api_key",
    "shipstation_api_key",
    "easyship_api_key",
}

def _mask_tail(s: str, keep: int = 4) -> str:
    s = s or ""
    return "*" * max(0, len(s) - keep) + s[-keep:] if s else s

def _unwrap_encrypted(s: str) -> str:
    if isinstance(s, str) and s.startswith("ENCRYPTED(") and s.endswith(")"):
        return s[len("ENCRYPTED("):-1]
    return s

def kms_encrypt(plaintext: str) -> str:
    if not _kms or not STRIPE_KMS_KEY_ARN:
        raise ConfigError("STRIPE_KMS_KEY_ARN not set; cannot encrypt secrets via tenant_config")
    resp = _kms.encrypt(KeyId=STRIPE_KMS_KEY_ARN, Plaintext=plaintext.encode("utf-8"), EncryptionContext=ENC_CTX)
    import base64 as _b64
    return f"ENCRYPTED({_b64.b64encode(resp['CiphertextBlob']).decode('utf-8')})"

# ---------- Data helpers -----------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _get_tenant(client_id: str) -> Dict[str, Any]:
    """Get tenant data from stripe-keys table"""
    resp = _keys_tbl.get_item(Key={"clientID": client_id})
    return resp.get("Item") or {}

def _get_tenant_config_from_app_config(client_id: str) -> Dict[str, Any]:
    """Get tenant-specific config from app-config table"""
    config = {}
    try:
        # Query for all config items for this client
        from boto3.dynamodb.conditions import Key, Attr
        
        # Get items where config_key starts with clientID
        response = _cfg_tbl.query(
            KeyConditionExpression=Key('config_key').begins_with(f"{client_id}:") & Key('environment').eq(ENVIRONMENT)
        )
        
        for item in response.get('Items', []):
            # Extract the key part after "clientID:"
            key = item['config_key'].replace(f"{client_id}:", "", 1)
            config[key] = item.get('value')
            
    except Exception as e:
        print(f"Error loading tenant config from app-config: {e}")
    
    return config

def _mask_secrets_view(item: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(item)
    for f in SECRET_FIELDS:
        if f in out and isinstance(out[f], str):
            out[f] = _mask_tail(out[f], 4)
    return out

# ---------- Handlers ---------------------------------------------------------

def handle_admin_get(event):
    """
    GET /admin/tenant-config
    Returns the tenant row with secrets masked by default.
    Use ?includeSecrets=true to include masked fields (still masked).
    """
    client_id = _extract_client_id(event)
    if not client_id:
        return _bad("clientID required (X-Client-Id header or clientId query/body)")
    
    # Get data from stripe-keys table
    stripe_keys_item = _get_tenant(client_id)
    
    # Get tenant config from app-config table
    tenant_config = _get_tenant_config_from_app_config(client_id)
    
    if not stripe_keys_item and not tenant_config:
        return _ok({"clientID": client_id, "exists": False, "tenant": {}, "tenant_config": {}})

    qs = event.get("queryStringParameters") or {}
    include = (qs.get("includeSecrets", "true").lower() == "true") if isinstance(qs, dict) else True
    data = _mask_secrets_view(stripe_keys_item) if include else stripe_keys_item

    return _ok({
        "clientID": client_id, 
        "environment": ENVIRONMENT, 
        "exists": True, 
        "tenant": data,
        "tenant_config": tenant_config,
        # Also include individual fields at root for backward compatibility
        "sms_notification_phone": tenant_config.get("sms_notification_phone", ""),
        "just_for_you": tenant_config.get("just_for_you", ""),
        "order_fulfilled": tenant_config.get("order_fulfilled", ""),
        "refund": tenant_config.get("refund", ""),
        "return_label": tenant_config.get("return_label", ""),
        "thank_you": tenant_config.get("thank_you", "")
    })

def handle_admin_put(event):
    """
    PUT /admin/tenant-config
    Upsert tenant config fields.
    - Stripe keys and secrets go to stripe-keys table
    - Other config (tenant_config, sms_notification_phone, etc.) goes to app-config table
    """
    client_id = _extract_client_id(event)
    if not client_id:
        return _bad("clientID required (X-Client-Id header or clientId query/body)")
    try:
        body = _parse_json_body(event)
    except ValueError as e:
        return _bad(str(e))

    if not body:
        return _bad("Empty payload")

    stripe_keys_updates: Dict[str, Any] = {}
    app_config_updates: Dict[str, Any] = {}
    
    # Separate fields into stripe-keys vs app-config
    for k, v in body.items():
        if k in ("clientID", "clientId", "client_id"):
            continue
            
        # These go to stripe-keys table
        if k in SECRET_FIELDS or k.startswith("stripe_") or k.startswith("pk_") or k.startswith("sk_") or k.startswith("wh_"):
            if k in SECRET_FIELDS:
                # Encrypt secret fields
                if not STRIPE_KMS_KEY_ARN:
                    return _bad("Cannot update secrets: STRIPE_KMS_KEY_ARN not configured", 500)
                if isinstance(v, str) and v.startswith("ENCRYPTED(") and v.endswith(")"):
                    stripe_keys_updates[k] = v  # already wrapped
                else:
                    stripe_keys_updates[k] = kms_encrypt(str(v))
            else:
                stripe_keys_updates[k] = v
        else:
            # Everything else goes to app-config table
            app_config_updates[k] = v
    
    # Handle tenant_config nested object
    if "tenant_config" in body:
        tenant_config = body["tenant_config"]
        if isinstance(tenant_config, dict):
            for k, v in tenant_config.items():
                app_config_updates[k] = v

    updated_fields = []

    # Update stripe-keys table if needed
    if stripe_keys_updates:
        stripe_keys_updates["updated_at"] = _now_iso()
        upd_keys = list(stripe_keys_updates.keys())
        expr_names = {f"#{k}": k for k in upd_keys}
        expr_vals = {f":{k}": stripe_keys_updates[k] for k in upd_keys}
        expr = "SET " + ", ".join(f"#{k} = :{k}" for k in upd_keys)

        try:
            _keys_tbl.update_item(
                Key={"clientID": client_id},
                UpdateExpression=expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_vals,
            )
            updated_fields.extend(upd_keys)
        except ClientError as e:
            return _bad(f"DynamoDB error (stripe-keys): {e.response['Error'].get('Message','unknown')}", 500)

    # Update app-config table if needed
    if app_config_updates:
        try:
            for key, value in app_config_updates.items():
                _cfg_tbl.put_item(
                    Item={
                        'config_key': f"{client_id}:{key}",
                        'environment': ENVIRONMENT,
                        'value': value,
                        'updated_at': _now_iso()
                    }
                )
                updated_fields.append(key)
        except ClientError as e:
            return _bad(f"DynamoDB error (app-config): {e.response['Error'].get('Message','unknown')}", 500)

    return _ok({"success": True, "updated": updated_fields, "environment": ENVIRONMENT})

def handle_public_get(event):
    """
    GET /public/tenant-config
    Safe subset for public consumption (e.g., landing pages).
    """
    client_id = _extract_client_id(event)
    if not client_id:
        return _bad("clientID required (X-Client-Id header or clientId query/body)")

    item = _get_tenant(client_id)
    if not item:
        return _ok({"clientID": client_id, "exists": False, "config": {}})

    # Whitelist only safe, non-sensitive fields here
    public = {
        "clientID": client_id,
        "stripe_publishable_key": item.get("stripe_publishable_key"),
        "plan": item.get("plan", {}),
        # You can expose additional branding/support fields if you store them:
        "brand": item.get("brand", {}),
        "support": item.get("support", {}),
    }
    return _ok({"environment": ENVIRONMENT, "exists": True, "config": public})

# ---------- Lambda router ----------------------------------------------------

def lambda_handler(event, context):
    method = (event.get("httpMethod") or "").upper()
    path   = event.get("path") or ""

    # CORS preflight
    if method == "OPTIONS":
        return _ok({"ok": True})

    if path.endswith("/admin/tenant-config"):
        if method == "GET":
            return handle_admin_get(event)
        if method == "PUT":
            return handle_admin_put(event)
        return _bad("Method not allowed", 405)

    if path.endswith("/public/tenant-config") and method == "GET":
        return handle_public_get(event)

    return _bad(f"Unsupported route: {method} {path}", 405)