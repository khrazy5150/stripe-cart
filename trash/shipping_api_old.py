# shipping_api.py
import os, json, base64, boto3
from typing import Any, Dict, Optional, Iterable
from datetime import datetime, timezone
from botocore.exceptions import ClientError

# ---- Optional: use your layer if present
try:
    import shipping_providers  # from ShippingProvidersLayer
except Exception:
    try:
        import shipping_stub as shipping_providers
    except Exception:
        shipping_providers = None

# ---- Strict env (no fallbacks)
class ConfigError(RuntimeError): pass
def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise ConfigError(f"Missing required environment variable: {name}")
    return v

ENVIRONMENT        = _req("ENVIRONMENT")          # dev | prod
STRIPE_KEYS_TABLE  = _req("STRIPE_KEYS_TABLE")    # stripe-keys-<env>
REGION             = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"

# Only required if you PUT secrets via this API
STRIPE_KMS_KEY_ARN = os.environ.get("STRIPE_KMS_KEY_ARN")
ENC_CTX = {"app": "shipping-api"}

# ---- AWS clients
_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_tenants  = _dynamodb.Table(STRIPE_KEYS_TABLE)
_kms      = boto3.client("kms", region_name=REGION) if STRIPE_KMS_KEY_ARN else None

# ---- HTTP helpers
def _cors() -> Dict[str,str]:
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,Stripe-Signature,X-Client-Id,X-Offer-Name",
        "Access-Control-Allow-Methods": "OPTIONS,GET,PUT,POST",
    }
def _ok(body: Any = None, status: int = 200) -> Dict[str,Any]:
    return {"statusCode": status, "headers": _cors(), "body": (body if isinstance(body,(str,type(None))) else json.dumps(body or {})) or ""}
def _bad(msg: str, code: int = 400) -> Dict[str,Any]: return _ok({"error": msg}, code)

def _parse_json(event) -> Dict[str,Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data

def _extract_client_id(event) -> Optional[str]:
    h = event.get("headers") or {}
    cid = h.get("X-Client-Id") or h.get("x-client-id")
    if not cid:
        qs = event.get("queryStringParameters") or {}
        if isinstance(qs, dict):
            cid = qs.get("clientID") or qs.get("clientId") or qs.get("client_id")
    if not cid:
        try:
            body = _parse_json(event)
            cid = body.get("clientID") or body.get("clientId") or body.get("client_id")
        except Exception:
            pass
    return cid

def _now_iso() -> str: return datetime.now(timezone.utc).isoformat()

# ---- Secrets helpers
SECRET_FIELDS = {
    # Add provider-specific secrets you support here:
    # Shippo / EasyPost / ShipStation / EasyShip / etc.
    "shippo_api_key",
    "easypost_api_key",
    "shipstation_api_key",
    "shipstation_api_secret",    # NEW
    "easyship_api_key",
}

def _mask_tail(s: str, keep: int = 4) -> str:
    s = s or ""
    return "*" * max(0, len(s) - keep) + s[-keep:] if s else s

def _unwrap_encrypted(s: str) -> str:
    if isinstance(s, str) and s.startswith("ENCRYPTED(") and s.endswith(")"):
        return s[len("ENCRYPTED("):-1]
    return s

def _kms_encrypt(plaintext: str) -> str:
    if not _kms or not STRIPE_KMS_KEY_ARN:
        raise ConfigError("STRIPE_KMS_KEY_ARN not set; cannot encrypt secrets via shipping API")
    resp = _kms.encrypt(KeyId=STRIPE_KMS_KEY_ARN, Plaintext=plaintext.encode("utf-8"), EncryptionContext=ENC_CTX)
    import base64 as _b64
    return f"ENCRYPTED({_b64.b64encode(resp['CiphertextBlob']).decode('utf-8')})"

def _kms_decrypt_wrapped(ciphertext_wrapped: str) -> Optional[str]:
    if not _kms or not STRIPE_KMS_KEY_ARN:
        return None
    import base64 as _b64
    inner = _unwrap_encrypted(ciphertext_wrapped)
    try:
        blob = _b64.b64decode(inner)
        resp = _kms.decrypt(CiphertextBlob=blob, EncryptionContext=ENC_CTX)
        return resp["Plaintext"].decode("utf-8")
    except Exception:
        return None

# ---- Data helpers
def _get_tenant(client_id: str) -> Dict[str,Any]:
    resp = _tenants.get_item(Key={"clientID": client_id})
    return resp.get("Item") or {}

def _mask_secrets_view(item: Dict[str,Any]) -> Dict[str,Any]:
    out = dict(item)
    for f in SECRET_FIELDS:
        if f in out and isinstance(out[f], str):
            out[f] = _mask_tail(out[f], 4)
    return out

def _upsert_partial(client_id: str, updates: Dict[str,Any]) -> None:
    keys = [k for k in updates.keys() if k != "clientID"]
    expr_names  = {f"#{k}": k for k in keys}
    expr_vals   = {f":{k}": updates[k] for k in keys}
    update_expr = "SET " + ", ".join(f"#{k} = :{k}" for k in keys)
    _tenants.update_item(
        Key={"clientID": client_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_vals,
    )

# ---- Handlers
def get_shipping_config(event):
    client_id = _extract_client_id(event)
    if not client_id:
        return _bad("clientID required (X-Client-Id header or clientId query/body)")
    item = _get_tenant(client_id)
    # Return only shipping-related fields + plan if you like
    shipping_view = {
        "clientID": client_id,
        "shippo_api_key": item.get("shippo_api_key"),
        "easypost_api_key": item.get("easypost_api_key"),
        "shipstation_api_key": item.get("shipstation_api_key"),
        "easyship_api_key": item.get("easyship_api_key"),
        "default_carrier": item.get("default_carrier"),
        "default_service": item.get("default_service"),
        "ship_from": item.get("ship_from", {}),   # address object if you store it
        "shipper_account": item.get("shipper_account", {}),
        "updated_at": item.get("updated_at"),
    }
    return _ok({"environment": ENVIRONMENT, "config": _mask_secrets_view(shipping_view)})

def put_shipping_config(event):
    if not STRIPE_KMS_KEY_ARN:
        return _bad("Cannot update secrets: STRIPE_KMS_KEY_ARN not configured", 500)

    client_id = _extract_client_id(event)
    if not client_id:
        return _bad("clientID required (X-Client-Id header or clientId query/body)")
    body = _parse_json(event)
    if not body:
        return _bad("Empty payload")

    updates: Dict[str,Any] = {"updated_at": _now_iso()}
    # non-secret fields copied as-is
    for k,v in body.items():
        if k in ("clientID","clientId","client_id"): continue
        if k not in SECRET_FIELDS:
            updates[k] = v

    # secret fields encrypted (accept pre-wrapped ENCRYPTED(...) as-is)
    for f in SECRET_FIELDS:
        if f in body and body[f]:
            if isinstance(body[f], str) and body[f].startswith("ENCRYPTED(") and body[f].endswith(")"):
                updates[f] = body[f]
            else:
                updates[f] = _kms_encrypt(str(body[f]))

    try:
        _upsert_partial(client_id, updates)
        return _ok({"success": True, "updated": list(updates.keys()), "environment": ENVIRONMENT})
    except ClientError as e:
        return _bad(f"DynamoDB error: {e.response['Error'].get('Message','unknown')}", 500)

def post_test_shipping(event):
    client_id = _extract_client_id(event)
    if not client_id:
        return _bad("clientID required (X-Client-Id header or clientId query/body)")
    body = _parse_json(event)

    if not shipping_providers or not hasattr(shipping_providers, "test_credentials"):
        return _bad("Shipping providers layer not installed or missing test_credentials()", 501)

    # Load decrypted secrets as needed by your providers
    tenant = _get_tenant(client_id)
    secrets = {f: _kms_decrypt_wrapped(tenant.get(f)) for f in SECRET_FIELDS}
    try:
        res = shipping_providers.test_credentials(body, secrets=secrets, environment=ENVIRONMENT)
        # expected shape: {"success": bool, "message": "...", "details": {...}}
        return _ok(res if isinstance(res, dict) else {"success": bool(res)})
    except Exception as e:
        return _bad(f"Shipping test failed: {e}", 500)

def post_get_rates(event):
    client_id = _extract_client_id(event)
    if not client_id:
        return _bad("clientID required (X-Client-Id header or clientId query/body)")
    body = _parse_json(event)

    if not shipping_providers or not hasattr(shipping_providers, "get_rates"):
        return _bad("Shipping providers layer not installed or missing get_rates()", 501)

    # Decrypt provider secrets and pass along
    tenant = _get_tenant(client_id)
    secrets = {f: _kms_decrypt_wrapped(tenant.get(f)) for f in SECRET_FIELDS}

    # Body should include shipment data (to/from address, parcels, carrier prefs)
    try:
        rates = shipping_providers.get_rates(body, secrets=secrets, environment=ENVIRONMENT)
        # expected: list of rate objects; return as-is
        return _ok({"environment": ENVIRONMENT, "count": len(rates) if isinstance(rates,list) else 0, "rates": rates})
    except Exception as e:
        return _bad(f"Get rates failed: {e}", 500)

# ---- Router
def lambda_handler(event, context):
    method = (event.get("httpMethod") or "").upper()
    path   = event.get("path") or ""

    if method == "OPTIONS":
        return _ok({"ok": True})

    try:
        if path.endswith("/admin/shipping-config"):
            if method == "GET":  return get_shipping_config(event)
            if method == "PUT":  return put_shipping_config(event)
            return _bad("Method not allowed", 405)

        if path.endswith("/admin/test-shipping") and method == "POST":
            return post_test_shipping(event)

        if path.endswith("/admin/get-rates") and method == "POST":
            return post_get_rates(event)

        return _bad(f"Unsupported route: {method} {path}", 405)

    except ValueError as e:
        return _bad(str(e), 400)
    except ConfigError as e:
        return _bad(str(e), 500)
    except ClientError as e:
        return _bad(f"AWS error: {e.response['Error'].get('Message','unknown')}", 500)
    except Exception as e:
        return _bad(f"Internal server error: {e}", 500)
