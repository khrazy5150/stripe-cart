import os
import json
import base64
import boto3
import logging
from botocore.exceptions import ClientError
from datetime import datetime, timezone
from typing import Dict, Any, Optional

# ---- Strict env (no table fallbacks) ----------------------------------------

class ConfigError(RuntimeError):
    pass

def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise ConfigError(f"Missing required environment variable: {name}")
    return v

ENVIRONMENT        = _req("ENVIRONMENT")                 # "dev" | "prod"
STRIPE_KEYS_TABLE  = _req("STRIPE_KEYS_TABLE")           # e.g., "stripe-keys-dev"
STRIPE_KMS_KEY_ARN = _req("STRIPE_KMS_KEY_ARN")          # KMS key for secrets
WEBHOOK_BASE_URL_TEST = os.environ.get("WEBHOOK_BASE_URL_TEST", "https://api-dev.juniorbay.com")
WEBHOOK_BASE_URL_LIVE = os.environ.get("WEBHOOK_BASE_URL_LIVE", "https://checkout.juniorbay.com")

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"

ENC_CTX = {"app": "stripe-cart"}  # stable context for KMS
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---- AWS clients ------------------------------------------------------------

_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_table = _dynamodb.Table(STRIPE_KEYS_TABLE)
_kms = boto3.client("kms", region_name=REGION)
PEER_STRIPE_KEYS_TABLE = (os.environ.get("PEER_STRIPE_KEYS_TABLE") or "").strip()
_peer_table = None
if PEER_STRIPE_KEYS_TABLE:
    try:
        _peer_table = _dynamodb.Table(PEER_STRIPE_KEYS_TABLE)
    except Exception as exc:
        logger.warning("Failed to initialize peer stripe keys table %s: %s", PEER_STRIPE_KEYS_TABLE, exc)
        _peer_table = None

# ---- HTTP helpers -----------------------------------------------------------

def _cors_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,Stripe-Signature,X-Client-Id,X-Offer-Name",
        "Access-Control-Allow-Methods": "OPTIONS,GET,PUT",
    }

def _ok(body: Dict[str, Any], status: int = 200) -> Dict[str, Any]:
    return {"statusCode": status, "headers": _cors_headers(), "body": json.dumps(body)}

def _bad_request(message: str, status: int = 400) -> Dict[str, Any]:
    return _ok({"error": message}, status=status)

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

# ---- KMS helpers ------------------------------------------------------------

def _mask_tail(s: str, keep: int = 4) -> str:
    s = s or ""
    if len(s) <= keep:
        return "*" * len(s)
    return "*" * (len(s) - keep) + s[-keep:]

def _unwrap_encrypted(s: str) -> str:
    """Remove ENCRYPTED() wrapper if present."""
    if isinstance(s, str) and s.startswith("ENCRYPTED(") and s.endswith(")"):
        return s[len("ENCRYPTED("):-1]
    return s

def kms_encrypt(plaintext: bytes) -> str:
    """Encrypt bytes with KMS; return base64 text wrapped as ENCRYPTED(...)."""
    resp = _kms.encrypt(
        KeyId=STRIPE_KMS_KEY_ARN,
        Plaintext=plaintext,
        EncryptionContext=ENC_CTX,
    )
    ct = base64.b64encode(resp["CiphertextBlob"]).decode("utf-8")
    return f"ENCRYPTED({ct})"

def kms_decrypt(ciphertext_wrapped: str) -> bytes:
    """Accept ENCRYPTED(base64) or raw base64; return plaintext bytes."""
    ct = _unwrap_encrypted(ciphertext_wrapped)
    blob = base64.b64decode(ct)
    resp = _kms.decrypt(CiphertextBlob=blob, EncryptionContext=ENC_CTX)
    return resp["Plaintext"]

def _default_webhook_url(client_id: str, mode: str) -> Optional[str]:
    base = WEBHOOK_BASE_URL_TEST if mode == "test" else WEBHOOK_BASE_URL_LIVE
    if not base or not client_id:
        return None
    return f"{base.rstrip('/')}/webhook/{client_id}"

def _apply_update(table, client_id: str, attributes: Dict[str, Any]):
    fields = {k: v for k, v in attributes.items() if k != "clientID"}
    if not fields:
        return
    update_expr = "SET " + ", ".join(f"#{k} = :{k}" for k in fields.keys())
    expr_names = {f"#{k}": k for k in fields.keys()}
    expr_values = {f":{k}": v for k, v in fields.items()}
    table.update_item(
        Key={"clientID": client_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )

# ---- Business logic ---------------------------------------------------------

SECRET_FIELDS = {
    "stripe_secret_key",
    "sk_test",           # Stripe test secret key
    "sk_live",           # Stripe live secret key
    "wh_secret_test",    # Stripe test webhook secret
    "wh_secret_live",    # Stripe live webhook secret
    "shippo_api_key",
    "easypost_api_key",
    "shipstation_api_key",
    "easyship_api_key",
}

NON_SECRET_FIELDS = {
    "stripe_publishable_key",
    "pk_test",           # Stripe test publishable key
    "pk_live",           # Stripe live publishable key
    "mode",              # "test" or "live"
    "plan",              # object: {type: "...", ...}
    "landing_pages",     # array; managed by other endpoints but allow overwrite if sent
    "updated_at",
    "created_at",
    "client_name",
    "webhook_url_test",
    "webhook_url_live",
}

def get_keys(event, client_id: str) -> Dict[str, Any]:
    try:
        resp = _table.get_item(Key={"clientID": client_id})
        item = resp.get("Item")
        if not item:
            return _ok({"clientID": client_id, "exists": False, "keys": {}})

        # Return masked secrets in the format the frontend expects
        out = {"clientID": client_id, "exists": True}
        
        for k, v in item.items():
            if k == "clientID":
                out[k] = v
            elif k in SECRET_FIELDS and isinstance(v, str):
                # Return as object with masked/encrypted properties
                is_encrypted = v.startswith("ENCRYPTED(") and v.endswith(")")
                if is_encrypted:
                    out[k] = {"encrypted": True, "masked": None}
                else:
                    out[k] = {"encrypted": False, "masked": _mask_tail(v, 4)}
            elif k in NON_SECRET_FIELDS:
                out[k] = v
            else:
                # Pass through other fields
                out[k] = v

        return _ok(out)
    except ClientError as e:
        return _bad_request(f"DynamoDB error: {e.response['Error'].get('Message','unknown')}")

def put_keys(client_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    item = {"clientID": client_id, "updated_at": now}
    existing: Dict[str, Any] = {}
    try:
        resp = _table.get_item(Key={"clientID": client_id})
        existing = resp.get("Item") or {}
    except ClientError:
        existing = {}

    # Merge non-secret fields if provided
    for f in NON_SECRET_FIELDS:
        if f in body and body[f] is not None:
            item[f] = body[f]

    # Encrypt and store secret fields if provided
    for f in SECRET_FIELDS:
        if f in body and body[f]:
            val = body[f]
            if isinstance(val, str) and val.startswith("ENCRYPTED(") and val.endswith(")"):
                item[f] = val  # already wrapped
            else:
                item[f] = kms_encrypt(str(val).encode("utf-8"))

    def _maybe_seed_webhook(field: str, mode: str):
        if field in item:
            return
        if existing.get(field):
            return
        default_url = _default_webhook_url(client_id, mode)
        if default_url:
            item[field] = default_url

    _maybe_seed_webhook("webhook_url_test", "test")
    _maybe_seed_webhook("webhook_url_live", "live")

    try:
        _apply_update(_table, client_id, item)
        peer_status = "skipped"
        if _peer_table:
            try:
                _apply_update(_peer_table, client_id, item)
                peer_status = "synced"
            except ClientError as peer_err:
                peer_status = "failed"
                peer_name = getattr(_peer_table, "name", PEER_STRIPE_KEYS_TABLE or "unknown")
                logger.warning("Peer stripe-keys sync failed for %s -> %s: %s", client_id, peer_name, peer_err)
        return _ok({"success": True, "updated": list(item.keys()), "peer_sync": peer_status})
    except ClientError as e:
        return _bad_request(f"DynamoDB error: {e.response['Error'].get('Message','unknown')}")

# ---- Auth helper (optional; wire to your authorizer if you want strictness) --

def _is_authenticated_owner(event, client_id: str) -> bool:
    """
    Ensure the caller's tenant matches client_id.
    Adjust to your authorizer shape; default to True if we can't determine identity.
    """
    ctx = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = ctx.get("claims") or {}
    token_client = claims.get("custom:clientId") or claims.get("clientId") or claims.get("clientID")
    if token_client:
        return str(token_client) == str(client_id)
    return True

# ---- Lambda handler ---------------------------------------------------------

def lambda_handler(event, context):
    http_method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or ""
    headers = _cors_headers()

    # Preflight
    if http_method == "OPTIONS":
        return {"statusCode": 200, "headers": headers, "body": json.dumps({"ok": True})}

    client_id = _extract_client_id(event)
    if not client_id:
        return _bad_request("clientID required (X-Client-Id header or clientId query/body)")

    if path.endswith("/admin/stripe-keys"):
        if http_method == "GET":
            return get_keys(event, client_id)
        elif http_method == "PUT":
            if not _is_authenticated_owner(event, client_id):
                return {"statusCode": 403, "headers": headers, "body": json.dumps({"error": "Can only update your own keys"})}
            try:
                body = _parse_json_body(event)
            except ValueError as e:
                return _bad_request(str(e))
            return put_keys(client_id, body)

    return _bad_request("Unsupported route or method.")
