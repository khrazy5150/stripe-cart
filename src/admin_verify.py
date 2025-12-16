# admin_verify.py
# Verifies Stripe API keys by making actual API calls to Stripe

import os
import json
import base64
import boto3
from botocore.exceptions import ClientError
from typing import Dict, Any, Optional

try:
    import stripe
except ImportError:
    stripe = None

# ---------- Environment setup ------------------------------------------------

class ConfigError(RuntimeError):
    pass

def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise ConfigError(f"Missing required environment variable: {name}")
    return v

ENVIRONMENT = _req("ENVIRONMENT")
STRIPE_KEYS_TABLE = _req("STRIPE_KEYS_TABLE")
STRIPE_KMS_KEY_ARN = _req("STRIPE_KMS_KEY_ARN")
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"

_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_table = _dynamodb.Table(STRIPE_KEYS_TABLE)
_kms = boto3.client("kms", region_name=REGION)

ENC_CTX = {"app": "stripe-cart"}

# ---------- HTTP helpers -----------------------------------------------------

def _cors_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET,POST",
    }

def _ok(body: Dict[str, Any], status: int = 200) -> Dict[str, Any]:
    return {"statusCode": status, "headers": _cors_headers(), "body": json.dumps(body)}

def _bad(message: str, status: int = 400) -> Dict[str, Any]:
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

# ---------- Crypto helpers ---------------------------------------------------

def _unwrap_encrypted(s: str) -> str:
    """Remove ENCRYPTED() wrapper if present."""
    if isinstance(s, str) and s.startswith("ENCRYPTED(") and s.endswith(")"):
        return s[len("ENCRYPTED("):-1]
    return s

def _decrypt_kms(ciphertext_wrapped: str) -> str:
    """Decrypt KMS-encrypted value and return plaintext."""
    try:
        blob_b64 = _unwrap_encrypted(ciphertext_wrapped)
        blob = base64.b64decode(blob_b64)
        resp = _kms.decrypt(CiphertextBlob=blob, EncryptionContext=ENC_CTX)
        return resp["Plaintext"].decode("utf-8")
    except Exception as e:
        raise ValueError(f"Failed to decrypt: {e}")

# ---------- Get Stripe keys --------------------------------------------------

def _get_stripe_keys(client_id: str, mode: str = "test") -> Dict[str, Optional[str]]:
    """
    Retrieve and decrypt Stripe keys for a client.
    Returns dict with pk, sk, wh keys (decrypted if needed).
    """
    try:
        response = _table.get_item(Key={"clientID": client_id})
        item = response.get("Item")
        
        if not item:
            raise ValueError(f"No keys found for client {client_id}")
        
        # Get keys based on mode
        pk_field = f"pk_{mode}"
        sk_field = f"sk_{mode}"
        
        # Try canonical field (legacy webhook_secret_* kept for compatibility)
        wh_fields = [f"wh_secret_{mode}", f"webhook_secret_{mode}"]
        
        pk = item.get(pk_field, "")
        sk = item.get(sk_field, "")
        
        # Find webhook secret (try all possible field names)
        wh = ""
        for field in wh_fields:
            wh = item.get(field, "")
            if wh:
                break
        
        # Decrypt if needed
        if pk and pk.startswith("ENCRYPTED("):
            pk = _decrypt_kms(pk)
        if sk and sk.startswith("ENCRYPTED("):
            sk = _decrypt_kms(sk)
        if wh and wh.startswith("ENCRYPTED("):
            wh = _decrypt_kms(wh)
        
        return {
            "publishable_key": pk,
            "secret_key": sk,
            "webhook_secret": wh
        }
    
    except ClientError as e:
        raise ValueError(f"DynamoDB error: {e.response['Error']['Message']}")

# ---------- Stripe verification ----------------------------------------------

def _verify_publishable_key(pk: str) -> tuple[bool, str]:
    """Verify Stripe publishable key format."""
    if not pk:
        return False, "Publishable key not provided"
    
    if not pk.startswith("pk_test_") and not pk.startswith("pk_live_"):
        return False, "Invalid publishable key format"
    
    # Publishable keys can't be validated via API (they're public)
    return True, "Format valid"

def _verify_secret_key(sk: str) -> tuple[bool, str, Optional[str]]:
    """
    Verify Stripe secret key by making a test API call.
    Returns: (is_valid, message, account_id)
    """
    if not stripe:
        return False, "Stripe SDK not available", None
    
    if not sk:
        return False, "Secret key not provided", None
    
    if not sk.startswith("sk_test_") and not sk.startswith("sk_live_"):
        return False, "Invalid secret key format", None
    
    try:
        # Set the key and make a test call
        stripe.api_key = sk
        
        # Retrieve account info (lightweight call)
        account = stripe.Account.retrieve()
        account_id = account.get("id")
        
        return True, "Valid", account_id
    
    except stripe.error.AuthenticationError:
        return False, "Authentication failed - invalid key", None
    except stripe.error.PermissionError:
        return False, "Permission denied - key lacks access", None
    except Exception as e:
        return False, f"Error: {str(e)}", None

def _verify_webhook_secret(wh: str) -> tuple[bool, str]:
    """
    Verify webhook secret format.
    Note: Can't fully verify without receiving an actual webhook event.
    """
    if not wh:
        return False, "Webhook secret not provided"
    
    if not wh.startswith("whsec_"):
        return False, "Invalid webhook secret format (should start with whsec_)"
    
    # Webhook secrets can't be validated without an actual webhook event
    return True, "Format valid (full validation requires webhook event)"

# ---------- Main verification logic ------------------------------------------

def verify_keys(client_id: str, mode: str = "test") -> Dict[str, Any]:
    """
    Verify all Stripe keys for a client by making actual Stripe API calls.
    """
    try:
        # Get the keys
        keys = _get_stripe_keys(client_id, mode)
        
        pk = keys.get("publishable_key", "")
        sk = keys.get("secret_key", "")
        wh = keys.get("webhook_secret", "")
        
        # Verify each key
        pk_valid, pk_msg = _verify_publishable_key(pk)
        sk_valid, sk_msg, account_id = _verify_secret_key(sk)
        wh_valid, wh_msg = _verify_webhook_secret(wh)
        
        notes = []
        if not pk:
            notes.append(f"No publishable key found for {mode} mode")
        if not sk:
            notes.append(f"No secret key found for {mode} mode")
        if not wh:
            notes.append(f"No webhook secret found for {mode} mode")
        
        result = {
            "clientID": client_id,
            "mode": mode,
            "publishable_key_ok": pk_valid,
            "publishable_key_message": pk_msg,
            "secret_key_ok": sk_valid,
            "secret_key_message": sk_msg,
            "webhook_secret_ok": wh_valid,
            "webhook_secret_message": wh_msg,
            "notes": notes
        }
        
        if account_id:
            result["stripe_account"] = account_id
        
        return _ok(result, 200)
    
    except ValueError as e:
        return _bad(str(e), 400)
    except Exception as e:
        print(f"Unexpected error in verify_keys: {str(e)}")
        import traceback
        traceback.print_exc()
        return _bad(f"Unexpected error: {str(e)}", 500)

# ---------- Lambda handler ---------------------------------------------------

def lambda_handler(event, context):
    """
    Main Lambda handler for key verification.
    
    Routes:
      GET  /admin/verify?clientID=xxx&mode=test|live
      POST /admin/verify with body: {clientID: "xxx", mode: "test|live"}
    """
    print(f"Event: {json.dumps(event)}")
    
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or ""
    
    # CORS preflight
    if method == "OPTIONS":
        return _ok({"ok": True})
    
    # Check path
    if not path.endswith("/admin/verify"):
        return _bad("Unsupported route", 404)
    
    # Accept both GET and POST
    if method not in ["GET", "POST"]:
        return _bad(f"Method {method} not allowed", 405)
    
    # Extract parameters
    client_id = None
    mode = "test"
    
    if method == "GET":
        # Get from query string
        params = event.get("queryStringParameters") or {}
        client_id = params.get("clientID") or params.get("clientId")
        mode = params.get("mode", "test")
    
    elif method == "POST":
        # Get from body
        try:
            body = _parse_json_body(event)
            client_id = body.get("clientID") or body.get("clientId")
            mode = body.get("mode", "test")
        except ValueError as e:
            return _bad(str(e), 400)
    
    if not client_id:
        return _bad("clientID required", 400)
    
    if mode not in ["test", "live"]:
        return _bad("mode must be 'test' or 'live'", 400)
    
    return verify_keys(client_id, mode)


# For local testing
if __name__ == "__main__":
    test_event = {
        "httpMethod": "POST",
        "path": "/admin/verify",
        "body": json.dumps({
            "clientID": "test-client",
            "mode": "test"
        })
    }
    result = lambda_handler(test_event, None)
    print(json.dumps(result, indent=2))
