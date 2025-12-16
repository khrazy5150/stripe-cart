# stripe_cart.py
# Minimal Stripe webhook handler.
# Route: POST /webhook/{token}
#
# Verification strategy (in order):
#   1) If env STRIPE_WEBHOOK_SECRET is set, use it (single-tenant or shared).
#   2) Else, if {token} looks like "whsec_...", use it as the signing secret (explicit token-in-path).
#   3) Else, treat {token} as clientID and look up per-tenant secret in STRIPE_KEYS_TABLE
#      under attributes: "stripe_webhook_secret" (plaintext) or "stripe_webhook_secret_encrypted" (KMS).
#
# Environment:
#   ENVIRONMENT                (dev|prod)
#   STRIPE_KEYS_TABLE          (required if using per-tenant secrets)
#   STRIPE_WEBHOOK_SECRET      (optional, global secret)
#   STRIPE_KMS_KEY_ARN         (optional, if using encrypted secrets)

import json
import os
import base64
import logging
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError

try:
    import stripe  # pip install stripe
except Exception as e:  # pragma: no cover
    stripe = None

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ENV = os.environ.get("ENVIRONMENT", "dev")
STRIPE_KEYS_TABLE = os.environ.get("STRIPE_KEYS_TABLE")
GLOBAL_WH_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
KMS_KEY_ARN = os.environ.get("STRIPE_KMS_KEY_ARN")  # optional
ENC_CTX = {"app": "stripe-cart"}

_STRIPE_KEYS_TABLE = os.environ.get("STRIPE_KEYS_TABLE") or os.environ.get("StripeKeysTable") or os.environ.get("StripeKeysTableName")
_CUSTOMERS_TABLE   = os.environ.get("CustomersTableName") or os.environ.get("CUSTOMERS_TABLE")
_SESSIONS_TABLE    = os.environ.get("CheckoutSessionsTableName") or os.environ.get("CHECKOUT_SESSIONS_TABLE")

logger.info(f"KMS_KEY_ARN: {KMS_KEY_ARN}")

dynamodb = boto3.resource("dynamodb") if STRIPE_KEYS_TABLE else None
KMS = boto3.client("kms") if KMS_KEY_ARN else None
keys_table = dynamodb.Table(STRIPE_KEYS_TABLE) if dynamodb and STRIPE_KEYS_TABLE else None


# ---------- utilities ----------

def _resp(status: int, body: Dict[str, Any], cors: bool = False):
    headers = {"Content-Type": "application/json"}
    if cors:
        headers.update({
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "OPTIONS,POST",
            "Access-Control-Allow-Headers": "Content-Type",
        })
    return {"statusCode": status, "headers": headers, "body": json.dumps(body)}


def _get_header(event: Dict[str, Any], name: str) -> Optional[str]:
    hdrs = event.get("headers") or {}
    # normalize case-insensitive fetch
    for k, v in hdrs.items():
        if k.lower() == name.lower():
            return v
    return None


def _read_raw_body(event: Dict[str, Any]) -> bytes:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(raw)
    return raw.encode("utf-8")


def _looks_like_whsec(token: str) -> bool:
    return isinstance(token, str) and token.startswith("whsec_") and len(token) > 10


def _kms_encrypt_wrapped(plaintext: bytes) -> str:
    """Encrypt plaintext with KMS and wrap with ENCRYPTED() prefix"""
    if not KMS:
        raise RuntimeError("KMS client not configured")
    resp = KMS.encrypt(
        KeyId=KMS_KEY_ARN,
        Plaintext=plaintext,
        EncryptionContext=ENC_CTX,
    )
    return "ENCRYPTED(" + base64.b64encode(resp["CiphertextBlob"]).decode("utf-8") + ")"


def _kms_decrypt_wrapped(blob: str) -> str:
    """Decrypt KMS-encrypted value with error handling"""
    if not (blob and blob.startswith("ENCRYPTED(") and blob.endswith(")")):
        return blob
    if not KMS:
        raise RuntimeError("KMS client not configured but encrypted secret provided")
    try:
        b64 = blob[len("ENCRYPTED("):-1]
        ct = base64.b64decode(b64)
        resp = KMS.decrypt(CiphertextBlob=ct, EncryptionContext=ENC_CTX)
        return resp['Plaintext'].decode('utf-8')
    except Exception as e:
        # Log error but don't expose details
        return ""


def _get_tenant_webhook_secret_by_client(client_id: str) -> Optional[str]:
    """
    Look up per-tenant webhook secret in STRIPE_KEYS_TABLE item keyed by clientID.
    Supports the actual DynamoDB structure:
      - whsec_test / whsec_live (primary webhook secret fields)
      - wh_secret_test / wh_secret_live (alternative webhook secret fields)
      - mode field to determine test vs live
      - Values prefixed with ENCRYPTED(...) containing base64 KMS ciphertext
    """
    if not keys_table:
        return None
    try:
        res = keys_table.get_item(Key={"clientID": client_id})
        item = res.get("Item") or {}
        if not item:
            return None
        
        # Determine mode (test or live)
        mode = item.get("mode", ENV)  # Default to environment if not specified
        
        # Try primary webhook secret field first: whsec_test or whsec_live
        key_field = f"whsec_{mode}"
        encrypted_value = item.get(key_field)
        
        # If not found, try alternative field: wh_secret_test or wh_secret_live
        if not encrypted_value:
            key_field = f"wh_secret_{mode}"
            encrypted_value = item.get(key_field)
        
        if not encrypted_value:
            return None
        
        # Use the helper function to decrypt (handles ENCRYPTED() wrapper)
        return _kms_decrypt_wrapped(encrypted_value)
            
    except ClientError as e:
        raise RuntimeError(e.response["Error"]["Message"])


def _resolve_signing_secret(path_token: str) -> Optional[str]:
    """
    Resolve which signing secret to use for this request.
    Priority:
      1) GLOBAL_WH_SECRET env
      2) token itself if it looks like "whsec_..."
      3) per-tenant secret via clientID == token
    """
    if GLOBAL_WH_SECRET:
        return GLOBAL_WH_SECRET
    if _looks_like_whsec(path_token):
        return path_token
    # Treat token as clientID
    return _get_tenant_webhook_secret_by_client(path_token)


def _construct_event(raw_body: bytes, sig_header: str, wh_secret: str):
    if not stripe:
        raise RuntimeError("Stripe SDK not available—deploy with 'stripe' dependency.")
    try:
        # construct_event expects str payload
        payload = raw_body.decode("utf-8")
        return stripe.Webhook.construct_event(payload, sig_header, wh_secret)
    except Exception as e:
        raise RuntimeError(f"Stripe signature verification failed: {e}")
    

def _resolve_webhook_secret(event, payload: str) -> str:
    """
    Resolution order:
      1) Path param /webhook/{token} where token startswith 'whsec_'
      2) Env STRIPE_WEBHOOK_SECRET (optional, if you ever set it)
      3) Per-tenant from StripeKeysTable using clientID from event payload metadata,
         decrypted via _kms_decrypt_wrapped(blob)
    Returns: the whsec_* string or raises ValueError if none found.
    """
    # 1) Try path token
    path_params = event.get("pathParameters") or {}
    token = (path_params.get("token") or "").strip()
    if token.startswith("whsec_"):
        return token

    # 2) Optional global fallback
    env_whsec = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if env_whsec.startswith("whsec_"):
        return env_whsec

    # 3) Per-tenant (from payload -> clientID -> StripeKeysTable)
    try:
        tmp = json.loads(payload or "{}")
        obj = (tmp.get("data") or {}).get("object") or {}
        client_id = (obj.get("metadata") or {}).get("clientID") or (obj.get("metadata") or {}).get("client_id")
    except Exception:
        client_id = None

    if not client_id:
        raise ValueError("Unable to resolve clientID for webhook secret lookup")

    if not _STRIPE_KEYS_TABLE:
        raise ValueError("StripeKeysTable env var is not set")

    tbl = dynamodb.Table(_STRIPE_KEYS_TABLE)

    # Try a few common key shapes: PK by clientID or client_id
    item = None
    try:
        # Assume simple GetItem by clientID as primary key
        resp = tbl.get_item(Key={"clientID": client_id})
        item = resp.get("Item")
    except Exception:
        item = None

    if not item:
        try:
            resp = tbl.get_item(Key={"client_id": client_id})
            item = resp.get("Item")
        except Exception:
            item = None

    if not item:
        raise ValueError(f"No Stripe key record found for clientID {client_id}")

    # Candidate encrypted fields to decrypt
    cand_fields = [
        "webhook_secret_wrapped",
        "whsec_wrapped",
        "webhook_whsec_wrapped",
        "webhook_secret_encrypted",
    ]

    wrapped = None
    for f in cand_fields:
        v = item.get(f)
        if isinstance(v, str) and v:
            wrapped = v
            break

    if not wrapped:
        # maybe stored in nested dict
        s = item.get("webhook") or item.get("stripe") or {}
        for f in cand_fields:
            v = s.get(f) if isinstance(s, dict) else None
            if isinstance(v, str) and v:
                wrapped = v
                break

    if not wrapped:
        raise ValueError("No encrypted webhook secret found in Stripe keys record")

    # 🔐 Decrypt using your existing helper
    whsec = _kms_decrypt_wrapped(wrapped)
    if not isinstance(whsec, str) or not whsec.startswith("whsec_"):
        raise ValueError("Decrypted webhook secret is invalid")

    return whsec



# ---------- main handler ----------

def lambda_handler(event, _context):
    resource = event.get("resource")
    method = event.get("httpMethod")

    # Webhooks are POST only; API Gateway mock handles OPTIONS if configured.
    if resource == "/webhook/{token}" and method == "POST":
        try:
            path_params = event.get("pathParameters") or {}
            token = path_params.get("token") or ""
            if not token:
                return _resp(400, {"error": "Missing path token"})

            sig_header = _get_header(event, "Stripe-Signature")
            if not sig_header:
                return _resp(400, {"error": "Missing Stripe-Signature header"})

            wh_secret = _resolve_signing_secret(token)
            if not wh_secret:
                return _resp(401, {"error": "No webhook signing secret found for token"})

            raw_body = _read_raw_body(event)
            evt = _construct_event(raw_body, sig_header, wh_secret)

            # ---- Minimal routing on event types ----
            etype = evt.get("type", "")
            data = evt.get("data", {}).get("object", {})

            # Examples: add your own handlers here (create orders, fulfill, etc.)
            if etype == "checkout.session.completed":
                # --- capture email + session mapping ---
                # data = evt["data"]["object"]  (already set above as `data`)
                email = (data.get("customer_details") or {}).get("email") or data.get("customer_email")
                customer_id = data.get("customer")
                session_id  = data.get("id")
                client_id   = (data.get("metadata") or {}).get("clientID")
                offer       = (data.get("metadata") or {}).get("offer")
                payment_intent_id = data.get("payment_intent")

                # Fallback: if customer is missing but we have a PI, recover it
                if not customer_id and payment_intent_id:
                    try:
                        pi = stripe.PaymentIntent.retrieve(payment_intent_id)
                        customer_id = pi.get("customer")
                    except Exception:
                        logger.info(f"Could not retrieve PI {payment_intent_id} for customer recovery")

                now = int(time.time())

                # Upsert Customers table (email by customer_id)
                try:
                    cust_tbl_name = os.environ.get("CUSTOMERS_TABLE") or os.environ.get("CustomersTableName")
                    if cust_tbl_name:
                        ct = boto3.resource("dynamodb").Table(cust_tbl_name)
                        upd = "SET email=:e, updatedAt=:u"
                        vals = {":e": email or "", ":u": now}
                        if client_id:
                            upd += ", clientID=:c"; vals[":c"] = client_id
                        if offer:
                            upd += ", lastOffer=:o"; vals[":o"] = offer
                        if customer_id:
                            ct.update_item(Key={"customer_id": customer_id}, UpdateExpression=upd, ExpressionAttributeValues=vals)
                            logger.info(f"[WH] Saved customer {customer_id} email={email}")
                except Exception as e:
                    logger.error(f"[WH] DDB upsert Customers error: {e}")

                # Put session → customer/email mapping
                try:
                    sess_tbl_name = os.environ.get("CHECKOUT_SESSIONS_TABLE") or os.environ.get("CheckoutSessionsTableName")
                    if sess_tbl_name and session_id:
                        st = boto3.resource("dynamodb").Table(sess_tbl_name)
                        st.put_item(Item={
                            "session_id": session_id,
                            "customer_id": customer_id or "",
                            "email": email or "",
                            "clientID": client_id or "",
                            "offer": offer or "",
                            "createdAt": now,
                        })
                        logger.info(f"[WH] Saved session map {session_id} -> {customer_id} ({email})")
                except Exception as e:
                    logger.error(f"[WH] DDB put sessions error: {e}")

                # done handling this event type

                pass
            elif etype == "invoice.paid":
                pass
            elif etype == "payment_intent.succeeded":
                pass
            # ...add more cases as needed...

            return _resp(200, {"received": True, "type": etype})
        except Exception as e:
            # Do not expose secrets; return concise error
            return _resp(400, {"error": f"Webhook handling error: {str(e)}"})

    # Unknown route
    return _resp(405, {"error": "Method not allowed"})