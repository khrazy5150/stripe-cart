import os
import json
import base64
import boto3
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

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"

ENC_CTX = {"app": "stripe-cart"}  # stable context for KMS

# ---- AWS clients ------------------------------------------------------------

_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_table = _dynamodb.Table(STRIPE_KEYS_TABLE)
_kms = boto3.client("kms", region_name=REGION)

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

# ---- Business logic ---------------------------------------------------------

SECRET_FIELDS = {
    "stripe_secret_key",
    "shippo_api_key",
    "easypost_api_key",
    "shipstation_api_key",
    "easyship_api_key",
}

NON_SECRET_FIELDS = {
    "stripe_publishable_key",
    "plan",              # object: {type: "...", ...}
    "landing_pages",     # array; managed by other endpoints but allow overwrite if sent
    "updated_at",
    "created_at",
    "client_name",
}

def get_keys(event, client_id: str) -> Dict[str, Any]:
    try:
        resp = _table.get_item(Key={"clientID": client_id})
        item = resp.get("Item")
        if not item:
            return _ok({"clientID": client_id, "exists": False, "keys": {}})

        # Return masked secrets by default
        out = {}
        for k, v in item.items():
            if k in SECRET_FIELDS and isinstance(v, str):
                out[k] = _mask_tail(v, 4)
            else:
                out[k] = v

        out["exists"] = True
        return _ok(out)
    except ClientError as e:
        return _bad_request(f"DynamoDB error: {e.response['Error'].get('Message','unknown')}")

def put_keys(client_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    item = {"clientID": client_id, "updated_at": now}

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

    try:
        _table.update_item(
            Key={"clientID": client_id},
            UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in item.keys() if k != "clientID"),
            ExpressionAttributeNames={f"#{k}": k for k in item.keys() if k != "clientID"},
            ExpressionAttributeValues={f":{k}": v for k, v in item.items() if k != "clientID"},
        )
        return _ok({"success": True, "updated": list(item.keys())})
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
