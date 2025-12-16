import os
import json
import base64
import boto3
from botocore.exceptions import ClientError
from datetime import datetime, timezone

DDB_TABLE = os.environ["STRIPE_KEYS_TABLE"]
KMS_KEY_ARN = os.environ["STRIPE_KMS_KEY_ARN"]
REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-west-2"))

ENC_CTX = {"app": "stripe-cart"}

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(DDB_TABLE)
kms = boto3.client("kms", region_name=REGION)


def _cors_headers():
    """Standard CORS headers for all responses."""
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,Stripe-Signature,X-Client-Id",
        "Access-Control-Allow-Methods": "OPTIONS,POST,GET,PUT",
    }

def _parse_json_body(event):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode("utf-8", "replace")
        except Exception:
            # If decode fails, treat like empty/invalid JSON
            raw = ""
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON body.")


def _ok(body):
    """Success response."""
    return {"statusCode": 200, "headers": _cors_headers(), "body": json.dumps(body)}


def _bad_request(msg):
    """Bad request error response."""
    return {"statusCode": 400, "headers": _cors_headers(), "body": json.dumps({"error": msg})}


def _mask(s: str, keep=4):
    """Mask sensitive string, showing only last 'keep' characters."""
    if not s:
        return s
    if len(s) <= keep:
        return "*" * len(s)
    return "*" * (len(s) - keep) + s[-keep:]


def _unwrap_encrypted(s: str) -> str:
    """Remove ENCRYPTED() wrapper if present."""
    if s and s.startswith("ENCRYPTED(") and s.endswith(")"):
        return s[len("ENCRYPTED("):-1]
    return s


def kms_encrypt(plaintext: bytes) -> str:
    """
    Encrypt plaintext using KMS and wrap in ENCRYPTED() format.
    
    Args:
        plaintext: Bytes to encrypt
        
    Returns:
        String in format: ENCRYPTED(base64_ciphertext)
    """
    resp = kms.encrypt(
        KeyId=KMS_KEY_ARN,
        Plaintext=plaintext,
        EncryptionContext=ENC_CTX,
    )
    return "ENCRYPTED(" + base64.b64encode(resp["CiphertextBlob"]).decode("utf-8") + ")"


def kms_decrypt_with_fallback(enc_str: str) -> bytes:
    """
    Decrypt KMS-encrypted value with fallback.
    
    First tries with encryption context, then without (for legacy keys).
    
    Args:
        enc_str: Encrypted string (may have ENCRYPTED() wrapper)
        
    Returns:
        Decrypted bytes
    """
    raw_b64 = _unwrap_encrypted(enc_str)
    blob = base64.b64decode(raw_b64)
    try:
        # Try with encryption context first
        out = kms.decrypt(CiphertextBlob=blob, EncryptionContext=ENC_CTX)
        return out["Plaintext"]
    except kms.exceptions.InvalidCiphertextException:
        # Fallback: try without encryption context (legacy keys)
        out = kms.decrypt(CiphertextBlob=blob)
        return out["Plaintext"]


def _require_client_id(event):
    """
    Extract client ID from request.
    Priority: query params > body > Cognito claims
    """
    # Try query params (for GET)
    qs = event.get("queryStringParameters") or {}
    if qs.get("clientID"):
        return qs["clientID"]
    
    # Try body (for PUT)
    if event.get("body"):
        try:
            body = _parse_json_body(event)
            if body.get("clientID"):
                return body["clientID"]
        except:
            pass
    
    # Try Cognito claims
    claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    if claims.get("sub"):
        return claims["sub"]
    
    raise ValueError("Missing clientID")


def _is_authenticated_owner(event, client_id):
    """Check if the authenticated user is the owner of this client_id."""
    try:
        claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
        auth_client_id = claims.get("sub")
        return auth_client_id == client_id
    except:
        return False


def get_keys(event, client_id: str):
    """
    GET /admin/stripe-keys?clientID=xxx
    Get Stripe keys for a client.
    
    Returns:
    - For owner: full plaintext keys
    - For others: masked keys
    """
    item = table.get_item(Key={"clientID": client_id}).get("Item")
    if not item:
        return _ok({"clientID": client_id, "message": "No keys found"})

    is_owner = _is_authenticated_owner(event, client_id)
    
    def process_field(field):
        """Process a key field - return plaintext for owner, masked for others."""
        v = item.get(field)
        if not v:
            return None
        
        # Publishable keys are always returned as-is (they're public)
        if field.startswith("pk_"):
            return v
        
        # Secret keys and webhook secrets
        try:
            plaintext = kms_decrypt_with_fallback(v).decode("utf-8")
            if is_owner:
                # Owner gets plaintext
                return plaintext
            else:
                # Third parties get masked version
                return {"masked": _mask(plaintext), "encrypted": True}
        except ClientError as e:
            if is_owner:
                # Owner gets error details
                return {"masked": None, "encrypted": True, "error": e.response["Error"]["Code"]}
            else:
                # Third parties just get masked null
                return {"masked": None, "encrypted": True}

    return _ok({
        "clientID": client_id,
        "mode": item.get("mode", "test"),
        "pk_test": process_field("pk_test"),
        "pk_live": process_field("pk_live"),
        "sk_test": process_field("sk_test"),
        "sk_live": process_field("sk_live"),
        "wh_secret_test": process_field("wh_secret_test"),
        "wh_secret_live": process_field("wh_secret_live"),
        "updatedAt": item.get("updated_at"),
        "active": item.get("active", "true") == "true"
    })


def put_keys(client_id: str, body: dict):
    """
    PUT /admin/stripe-keys
    Update Stripe keys for a client.
    
    Body: {
        "clientID": "xxx",
        "mode": "test" | "live",
        "pk_test": "pk_test_...",
        "sk_test": "sk_test_...",
        "wh_secret_test": "whsec_...",
        "pk_live": "pk_live_...",
        "sk_live": "sk_live_...",
        "wh_secret_live": "whsec_...",
        "active": true/false
    }
    """
    now = datetime.now(timezone.utc).isoformat()
    
    # Get existing item for merge
    existing = table.get_item(Key={"clientID": client_id}).get("Item", {})
    
    # Build update item
    item = {
        "clientID": client_id,
        "active": body.get("active", existing.get("active", True)),
        "mode": body.get("mode", existing.get("mode", "test")),
        "updated_at": now
    }
    
    # Add created_at if new
    if not existing:
        item["created_at"] = now
    else:
        item["created_at"] = existing.get("created_at", now)
    
    # Handle all keys - only encrypt if new value provided
    for key_type in ["pk_test", "pk_live", "sk_test", "sk_live", "wh_secret_test", "wh_secret_live"]:
        if key_type in body and body[key_type]:
            if key_type.startswith("pk_"):
                # Publishable keys stored as plaintext
                item[key_type] = body[key_type]
            else:
                # Secret keys encrypted
                item[key_type] = kms_encrypt(body[key_type].encode("utf-8"))
        elif existing.get(key_type):
            # Keep existing value
            item[key_type] = existing[key_type]
    
    table.put_item(Item=item)
    
    # Return masked response for security
    response = {"clientID": client_id, "updated_at": now, "success": True}
    return _ok(response)


def lambda_handler(event, context):
    """
    Main handler for Stripe keys management.
    
    Routes:
    - GET /admin/stripe-keys?clientID=xxx - Get keys
    - PUT /admin/stripe-keys - Update keys
    - OPTIONS /admin/stripe-keys - CORS preflight
    """

    print({
    "ct": (event.get("headers") or {}).get("content-type"),
    "isB64": event.get("isBase64Encoded"),
    "bodySample": (event.get("body") or "")[:60]
    })


    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": _cors_headers(), "body": ""}

    try:
        client_id = _require_client_id(event)
    except ValueError as e:
        return _bad_request(str(e))

    path = event.get("path", "")
    http_method = event.get("httpMethod", "GET").upper()

    if path.endswith("/admin/stripe-keys"):
        if http_method == "GET":
            return get_keys(event, client_id)
        elif http_method == "PUT":
            # Only allow owners to update their own keys
            if not _is_authenticated_owner(event, client_id):
                return {"statusCode": 403, "headers": _cors_headers(), 
                       "body": json.dumps({"error": "Can only update your own keys"})}
            try:
                body = _parse_json_body(event)
            except ValueError as e:
                return _bad_request(str(e))
            return put_keys(client_id, body)

    return _bad_request("Unsupported route or method.")