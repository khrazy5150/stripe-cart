# upsell_processor.py
# Handles upsell operations - getting session details and processing one-click upsell
#
# Routes:
#   GET /api/upsell-session?session_id=xxx&clientID=xxx
#   POST /api/process-upsell

import json
import os
import logging
import time
from typing import Dict, Any

try:
    import stripe
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    stripe = None
    boto3 = None

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ENV = os.environ.get("ENVIRONMENT", "dev")
STRIPE_KEYS_TABLE = os.environ.get("STRIPE_KEYS_TABLE")
KMS_KEY_ARN = os.environ.get("STRIPE_KMS_KEY_ARN")

dynamodb = boto3.resource("dynamodb") if boto3 and STRIPE_KEYS_TABLE else None
KMS = boto3.client("kms") if boto3 and KMS_KEY_ARN else None
keys_table = dynamodb.Table(STRIPE_KEYS_TABLE) if dynamodb and STRIPE_KEYS_TABLE else None

# accept multiple env var aliases to be flexible
_CUSTOMERS_ENV_KEYS = ["CustomersTableName", "CUSTOMERS_TABLE", "CustomersTable", "CUSTOMERS"]
_SESSIONS_ENV_KEYS  = ["CheckoutSessionsTableName", "CHECKOUT_SESSIONS_TABLE", "CheckoutSessionsTable", "CHECKOUT_SESSIONS"]

def _get_env_any(keys):
    for k in keys:
        v = os.environ.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""

def _get_table_and_name(kind: str):
    if kind == "customers":
        name = _get_env_any(_CUSTOMERS_ENV_KEYS)
    elif kind == "sessions":
        name = _get_env_any(_SESSIONS_ENV_KEYS)
    else:
        raise ValueError("kind must be 'customers' or 'sessions'")
    if not name:
        logger.warning(f"[UpsellSession] {kind} table env var not set")
        return None, ""
    try:
        return dynamodb.Table(name), name
    except Exception as e:
        logger.error(f"[UpsellSession] Could not init DDB table {name}: {e}")
        return None, name

def _select_secret(keys: dict) -> str:
    # prefer platform secret (Connect platform)
    for n in ("platform_secret_key", "platform_sk", "platform_secret"):
        v = keys.get(n)
        if isinstance(v, str) and v.strip():
            return v.strip()
    mode = (keys.get("mode") or "").lower()
    if mode == "live":
        for n in ("live_secret_key", "live_sk"):
            v = keys.get(n)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if mode == "test":
        for n in ("test_secret_key", "test_sk"):
            v = keys.get(n)
            if isinstance(v, str) and v.strip():
                return v.strip()
    for n in ("secret_key", "sk"):
        v = keys.get(n)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _resp(status: int, body: Dict[str, Any]):
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }
    return {
        "statusCode": status,
        "headers": headers,
        "body": json.dumps(body)
    }

def _json_body(evt):
    try:
        return json.loads(evt.get("body") or "{}")
    except Exception:
        return {}
    

def _kms_decrypt_wrapped(blob: str) -> str:
    """Decrypt KMS-encrypted value with ENCRYPTED() wrapper"""
    if not (blob and blob.startswith("ENCRYPTED(") and blob.endswith(")")):
        return blob
    if not KMS:
        raise RuntimeError("KMS client not configured")
    try:
        import base64
        b64 = blob[len("ENCRYPTED("):-1]
        ct = base64.b64decode(b64)
        resp = KMS.decrypt(
            CiphertextBlob=ct,
            EncryptionContext={"app": "stripe-cart"}
        )
        return resp['Plaintext'].decode('utf-8')
    except Exception as e:
        logger.error(f"KMS decryption error: {e}")
        return ""


def get_stripe_key_for_client(client_id: str) -> str:
    """Get Stripe API key for a specific client"""
    if not keys_table:
        return os.environ.get("STRIPE_SECRET_KEY")
    
    try:
        res = keys_table.get_item(Key={"clientID": client_id})
        item = res.get("Item") or {}
        if not item:
            return os.environ.get("STRIPE_SECRET_KEY")
        
        mode = item.get("mode", ENV)
        sk_field = f"sk_{mode}"
        
        stripe_key = item.get(sk_field)
        if stripe_key:
            return _kms_decrypt_wrapped(stripe_key)
            
    except ClientError as e:
        logger.error(f"Error fetching Stripe key: {e}")
    
    return os.environ.get("STRIPE_SECRET_KEY")


# upsell_processor.py - FIXED VERSION
# Key changes:
# 1. Better validation of upsell metadata
# 2. Returns has_upsell=false if upsell_price_id is missing
# 3. Clearer error messages

def get_upsell_session_details(event):
    """
    GET /api/upsell-session?session_id=xxx&clientID=xxx
    
    Returns details about the original checkout session including:
    - Customer ID
    - Payment method ID
    - Upsell product/price info from metadata
    - Shipping address
    """
    params = event.get("queryStringParameters") or {}
    session_id = params.get("session_id")
    client_id = params.get("clientID")
    
    if not session_id:
        return _resp(400, {"error": "Missing session_id parameter"})
    if not client_id:
        return _resp(400, {"error": "Missing clientID parameter"})
    
    if not stripe:
        return _resp(500, {"error": "Stripe SDK not available"})
    
    try:
        # Get Stripe key for this client
        stripe_key = get_stripe_key_for_client(client_id)
        if not stripe_key:
            logger.error(f"No Stripe key found for client: {client_id}")
            return _resp(500, {"error": "Stripe API key not configured"})
        
        stripe.api_key = stripe_key
        
        # Retrieve the checkout session
        logger.info(f"Retrieving session: {session_id}")
        session = stripe.checkout.Session.retrieve(
            session_id,
            expand=['customer', 'payment_intent', 'payment_intent.payment_method']
        )
        
        logger.info(f"Session retrieved successfully: {session_id}")
        
        # Extract relevant information
        customer_id = session.customer if isinstance(session.customer, str) else session.customer.id if session.customer else None
        payment_intent_id = session.payment_intent if isinstance(session.payment_intent, str) else session.payment_intent.id if session.payment_intent else None
        
        # Get payment method ID
        payment_method_id = None
        if session.payment_intent and not isinstance(session.payment_intent, str):
            pm = session.payment_intent.payment_method
            payment_method_id = pm if isinstance(pm, str) else pm.id if pm else None
        
        logger.info(f"Extracted: customer={customer_id}, payment_intent={payment_intent_id}, payment_method={payment_method_id}")
        
        # Extract upsell info from metadata
        metadata = session.metadata or {}
        
        # ⚠️ FIX: Validate upsell data is complete
        has_upsell_flag = metadata.get("has_upsell") == "true"
        upsell_product_id = metadata.get("upsell_product_id")
        upsell_price_id = metadata.get("upsell_price_id")
        
        # If has_upsell flag is set but price_id is missing, log warning and set has_upsell to false
        if has_upsell_flag and not upsell_price_id:
            logger.warning(f"Session {session_id} has has_upsell=true but missing upsell_price_id")
            has_upsell_flag = False
        
        # Safely extract shipping address
        shipping_address = None
        try:
            if hasattr(session, 'shipping_details') and session.shipping_details:
                if hasattr(session.shipping_details, 'address'):
                    addr = session.shipping_details.address
                    if addr:
                        shipping_address = {
                            "line1": getattr(addr, 'line1', None),
                            "line2": getattr(addr, 'line2', None),
                            "city": getattr(addr, 'city', None),
                            "state": getattr(addr, 'state', None),
                            "postal_code": getattr(addr, 'postal_code', None),
                            "country": getattr(addr, 'country', None),
                        }
        except Exception as e:
            logger.warning(f"Could not extract shipping address: {e}")
            shipping_address = None
        
        response_data = {
            "session_id": session_id,
            "customer_id": customer_id,
            "payment_intent_id": payment_intent_id,
            "payment_method_id": payment_method_id,
            "customer_email": session.customer_details.email if hasattr(session, 'customer_details') and session.customer_details else None,
            "customer_name": session.customer_details.name if hasattr(session, 'customer_details') and session.customer_details else None,
            "customer_phone": session.customer_details.phone if hasattr(session, 'customer_details') and session.customer_details else None,
            "shipping_address": shipping_address,
            "has_upsell": has_upsell_flag,  # ✅ Will be false if price_id is missing
            "upsell_product_id": upsell_product_id if has_upsell_flag else None,
            "upsell_price_id": upsell_price_id if has_upsell_flag else None,
            "upsell_offer_text": metadata.get("upsell_offer_text") if has_upsell_flag else None,
            "original_product_id": metadata.get("product_id"),
        }
        
        logger.info(f"Retrieved upsell session details for {session_id}: has_upsell={has_upsell_flag}, upsell_price_id={upsell_price_id}")
        return _resp(200, response_data)
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {e}")
        return _resp(400, {"error": f"Stripe error: {str(e)}"})
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return _resp(500, {"error": "Internal server error"})
    
    
# def _resolve_customer_payment_method(stripe, customer_id: str, candidate_pm_id: str | None):
#     """
#     Returns a PaymentMethod ID that is ATTACHED to the given customer.
#     Strategy:
#       1) Try to attach the candidate PM to this customer (if provided).
#       2) Use the customer's default payment method (if set).
#       3) Fallback to the first attached card payment method.
#     Raises ValueError if no attached PM is available.
#     """
#     pm_id_to_use = None

#     # 1) Try to attach the candidate (from Checkout PI) to the customer
#     if candidate_pm_id:
#         try:
#             # If already attached to this customer, Stripe will raise an error we can safely ignore
#             stripe.PaymentMethod.attach(candidate_pm_id, customer=customer_id)
#             pm_id_to_use = candidate_pm_id
#         except stripe.error.InvalidRequestError as e:
#             # If it's already attached to *this* customer, ignore; if attached to a different customer, keep looking
#             if "already attached to a different customer" in str(e).lower():
#                 pass  # can't use this candidate, fall through to default/list
#             elif "already exists" in str(e).lower():
#                 pm_id_to_use = candidate_pm_id  # benign
#             else:
#                 # other attach errors (detached, not reusable, etc.) -> continue to defaults
#                 pass

#     # 2) Use the customer's default payment method if present
#     if not pm_id_to_use:
#         cust = stripe.Customer.retrieve(
#             customer_id,
#             expand=["invoice_settings.default_payment_method"],
#         )
#         default_pm = cust.get("invoice_settings", {}).get("default_payment_method")
#         if isinstance(default_pm, dict) and default_pm.get("id"):
#             pm_id_to_use = default_pm["id"]
#         elif isinstance(default_pm, str):
#             pm_id_to_use = default_pm

#     # 3) Fallback to first attached card
#     if not pm_id_to_use:
#         pm_list = stripe.PaymentMethod.list(customer=customer_id, type="card")
#         if pm_list.data:
#             pm_id_to_use = pm_list.data[0].id

#     if not pm_id_to_use:
#         raise ValueError("No saved payment method is attached to this customer")

#     return pm_id_to_use


# def process_one_click_upsell(event):
#     """
#     POST /api/process-upsell

#     Body (camel or snake accepted):
#     {
#         "clientID": "xxx",
#         "session_id": "cs_test_...",
#         "customer_id": "cus_...",
#         "payment_method_id": "pm_...",     # may be unattached; we'll attach or fallback
#         "upsell_price_id": "price_...",
#         "shipping_address": {
#             "name": "...",
#             "phone": "...",
#             "line1": "...",
#             "line2": "...",
#             "city": "...",
#             "state": "...",
#             "postal_code": "...",
#             "country": "US"
#         }
#     }
#     """

#     def _resp(status, obj):
#         return {
#             "statusCode": status,
#             "headers": {"Content-Type": "application/json"},
#             "body": json.dumps(obj),
#     }

#     body = _json_body(event)

#     client_id = (body.get("clientID") or body.get("client_id") or "").strip()
#     session_id = (body.get("session_id") or "").strip()
#     customer_id = (body.get("customer_id") or "").strip()
#     candidate_pm_id = (body.get("payment_method_id") or "").strip()
#     upsell_price_id = (body.get("upsell_price_id") or "").strip()
#     shipping_address = body.get("shipping_address") or {}

#     if not client_id or not session_id or not customer_id or not upsell_price_id:
#         return _resp(400, {"success": False, "error": "Missing required parameters"})

#     # --- Load keys using your helper ---
#     try:
#         keys = get_stripe_key_for_client(client_id)  # you already have this
#     except Exception as e:
#         logger.error(f"get_stripe_key_for_client failed: {e}")
#         return _resp(400, {"success": False, "error": "Stripe key lookup failed for client"})

#     # Decide between direct-tenant vs. Connect flow
#     connected_account_id = (keys.get("connected_account_id") or keys.get("account_id") or "").strip()

#     # Helper to pick a secret key
#     def _select_secret(k):
#         # Prefer explicit platform key for Connect; otherwise pick by mode
#         mode = (k.get("mode") or "").lower()
#         platform_secret = k.get("platform_secret_key") or k.get("platform_sk") or k.get("platform_secret")
#         if platform_secret:
#             return platform_secret
#         if mode == "live":
#             return k.get("live_secret_key") or k.get("live_sk")
#         if mode == "test":
#             return k.get("test_secret_key") or k.get("test_sk")
#         # generic fallback
#         return k.get("secret_key") or k.get("sk")

#     secret = _select_secret(keys)
#     if not secret:
#         return _resp(400, {"success": False, "error": "Stripe secret key not found for client"})

#     stripe.api_key = secret
#     REQ = {"stripe_account": connected_account_id} if connected_account_id else {}

#     # Normalize shipping (optional)
#     shipping_dict = None
#     if isinstance(shipping_address, dict) and (shipping_address.get("line1") or shipping_address.get("postal_code")):
#         shipping_dict = {
#             "name": shipping_address.get("name") or "",
#             "phone": shipping_address.get("phone"),
#             "address": {
#                 "line1": shipping_address.get("line1", ""),
#                 "line2": shipping_address.get("line2"),
#                 "city": shipping_address.get("city", ""),
#                 "state": shipping_address.get("state", ""),
#                 "postal_code": shipping_address.get("postal_code", ""),
#                 "country": shipping_address.get("country", "US"),
#             },
#         }

    # # ---- Helper: ensure we have a PM attached to this customer ----
    # def _resolve_customer_payment_method(customer_id: str, candidate_pm_id: str | None):
    #     pm_id_to_use = None

    #     # 1) Try to attach the candidate PM to this customer (if provided)
    #     if candidate_pm_id:
    #         try:
    #             stripe.PaymentMethod.attach(candidate_pm_id, customer=customer_id, **REQ)
    #             pm_id_to_use = candidate_pm_id
    #         except stripe.error.InvalidRequestError as e:
    #             # If it's attached to a different customer or cannot be attached, fall through
    #             msg = (str(e) or "").lower()
    #             if "already attached" in msg and ("to this customer" in msg or "already exists" in msg):
    #                 pm_id_to_use = candidate_pm_id
    #             # else: ignore, try defaults below

    #     # 2) Use customer's default PM if set
    #     if not pm_id_to_use:
    #         cust = stripe.Customer.retrieve(customer_id, expand=["invoice_settings.default_payment_method"], **REQ)
    #         default_pm = (cust.get("invoice_settings") or {}).get("default_payment_method")
    #         if isinstance(default_pm, dict) and default_pm.get("id"):
    #             pm_id_to_use = default_pm["id"]
    #         elif isinstance(default_pm, str) and default_pm:
    #             pm_id_to_use = default_pm

    #     # 3) Fallback: first attached card PM
    #     if not pm_id_to_use:
    #         pm_list = stripe.PaymentMethod.list(customer=customer_id, type="card", **REQ)
    #         if pm_list.data:
    #             pm_id_to_use = pm_list.data[0].id

    #     if not pm_id_to_use:
    #         raise ValueError("No saved payment method is attached to this customer")

    #     return pm_id_to_use

    # try:
    #     # Look up the upsell price in the right account
    #     price = stripe.Price.retrieve(upsell_price_id, **REQ)
    #     amount = price.unit_amount
    #     currency = price.currency

    #     # 🔑 Ensure the PM is attached to the customer (legacy-compliant)
    #     pm_to_use = _resolve_customer_payment_method(customer_id, candidate_pm_id or None)

    #     # Build an idempotency key to avoid duplicate charges on retries
    #     idempotency_key = f"upsell:{client_id}:{session_id}:{upsell_price_id}"

    #     # Create + confirm off-session PI against the attached PM
    #     pi = stripe.PaymentIntent.create(
    #         amount=amount,
    #         currency=currency,
    #         customer=customer_id,
    #         payment_method=pm_to_use,
    #         confirmation_method="automatic",
    #         confirm=True,
    #         off_session=True,
    #         description="One-click upsell",
    #         metadata={
    #             "clientID": client_id,
    #             "upsell": "true",
    #             "original_session_id": session_id,
    #             "upsell_price_id": upsell_price_id,
    #         },
    #         shipping=shipping_dict,
    #         idempotency_key=idempotency_key,  # request option recognized by stripe-python
    #         **REQ,
    #     )

    #     logger.info(f"[Upsell] PI {pi.id} status={pi.status} account={'platform' if not REQ else REQ['stripe_account']}")
    #     return _resp(200, {"success": True, "payment_intent_id": pi.id, "status": pi.status})

    # except stripe.error.CardError as e:
    #     logger.error(f"[Upsell] Card error: {e}")
    #     return _resp(400, {"success": False, "error": "Card was declined", "decline_code": getattr(e, 'code', None)})
    # except stripe.error.StripeError as e:
    #     msg = str(e)
    #     logger.error(f"[Upsell] Stripe error: {msg}")
    #     # Normalize the specific reuse error you observed
    #     if "used with a PaymentIntent without Customer attachment" in msg:
    #         return _resp(400, {
    #             "success": False,
    #             "error": "Payment method must be attached to the customer before reuse",
    #         })
    #     return _resp(400, {"success": False, "error": f"Stripe error: {msg}"})
    # except ValueError as e:
    #     logger.error(f"[Upsell] {e}")
    #     return _resp(409, {"success": False, "error": str(e)})
    # except Exception as e:
    #     logger.exception("[Upsell] Unexpected server error")
    #     return _resp(500, {"success": False, "error": "Unexpected server error"})

def _get_customer_payment_method(customer_id: str, stripe_request_kwargs: dict) -> str:
    """
    Returns a PaymentMethod ID that is already ATTACHED to the given customer.
    Strategy (matching legacy implementation):
      1) Use the customer's default payment method (if set)
      2) Fallback to the first attached card payment method
    
    This avoids the "PaymentMethod was previously used without Customer attachment" error
    by only using payment methods that are already attached to the customer.
    
    Raises ValueError if no attached PM is available.
    """
    pm_id_to_use = None
    
    # 1) Check customer's default payment method
    try:
        cust = stripe.Customer.retrieve(
            customer_id,
            expand=["invoice_settings.default_payment_method"],
            **stripe_request_kwargs
        )
        default_pm = cust.get("invoice_settings", {}).get("default_payment_method")
        
        if isinstance(default_pm, dict) and default_pm.get("id"):
            pm_id_to_use = default_pm["id"]
        elif isinstance(default_pm, str) and default_pm:
            pm_id_to_use = default_pm
            
        logger.info(f"Customer {customer_id} default PM: {pm_id_to_use}")
    except Exception as e:
        logger.warning(f"Error retrieving customer default payment method: {e}")
    
    # 2) Fallback to first attached card payment method
    if not pm_id_to_use:
        try:
            pm_list = stripe.PaymentMethod.list(
                customer=customer_id,
                type="card",
                **stripe_request_kwargs
            )
            if pm_list.data:
                pm_id_to_use = pm_list.data[0].id
                logger.info(f"Using first attached PM for customer {customer_id}: {pm_id_to_use}")
        except Exception as e:
            logger.warning(f"Error listing customer payment methods: {e}")
    
    if not pm_id_to_use:
        raise ValueError("No saved payment method is attached to this customer")
    
    return pm_id_to_use


def process_one_click_upsell(event):
    """
    POST /api/process-upsell

    Body (camel or snake accepted):
    {
        "clientID": "xxx",
        "session_id": "cs_test_...",
        "customer_id": "cus_...",
        "payment_method_id": "pm_...",     # may be unattached; we'll attach or fallback
        "upsell_price_id": "price_...",
        "shipping_address": { ... }        # optional
    }
    """
    # Parse body
    try:
        body = json.loads(event.get("body") or "{}")
    except Exception:
        body = {}

    client_id = (body.get("clientID") or body.get("client_id") or "").strip()
    session_id = (body.get("session_id") or "").strip()
    customer_id = (body.get("customer_id") or "").strip()
    candidate_pm_id = (body.get("payment_method_id") or "").strip()
    upsell_price_id = (body.get("upsell_price_id") or "").strip()
    shipping_address = body.get("shipping_address") or {}

    if not client_id or not session_id or not customer_id or not upsell_price_id:
        return _resp(400, {"success": False, "error": "Missing required parameters"})

    # --- Load keys using your helper (string or dict) ---
    try:
        raw_keys = get_stripe_key_for_client(client_id)
    except Exception as e:
        logger.error(f"get_stripe_key_for_client failed: {e}")
        return _resp(400, {"success": False, "error": "Stripe key lookup failed for client"})

    # Normalize keys into a dict
    if isinstance(raw_keys, str):
        keys = {"secret_key": raw_keys}
    elif isinstance(raw_keys, dict):
        keys = raw_keys
    else:
        logger.error(f"Unsupported key type from get_stripe_key_for_client: {type(raw_keys)}")
        return _resp(400, {"success": False, "error": "Invalid Stripe key format for client"})

    # Some users store keys under a nested object, try a gentle unwrap
    if "stripe" in keys and isinstance(keys["stripe"], dict):
        # Prefer nested stripe fields if present
        keys = {**keys, **keys["stripe"]}

    # Decide between direct-tenant vs. Connect flow (if these fields exist)
    connected_account_id = ""
    for k in ("connected_account_id", "account_id", "stripe_account"):
        v = keys.get(k)
        if isinstance(v, str) and v.strip():
            connected_account_id = v.strip()
            break

    # # Helper to pick a secret key robustly
    # def _select_secret(k: dict) -> str:
    #     # Prefer explicit platform key (Connect)
    #     for name in ("platform_secret_key", "platform_sk", "platform_secret"):
    #         v = k.get(name)
    #         if isinstance(v, str) and v.strip():
    #             return v.strip()

    #     # Try mode-specific keys
    #     mode = (k.get("mode") or "").lower()
    #     if mode == "live":
    #         for name in ("live_secret_key", "live_sk"):
    #             v = k.get(name)
    #             if isinstance(v, str) and v.strip():
    #                 return v.strip()
    #     if mode == "test":
    #         for name in ("test_secret_key", "test_sk"):
    #             v = k.get(name)
    #             if isinstance(v, str) and v.strip():
    #                 return v.strip()

    #     # Generic fallbacks
    #     for name in ("secret_key", "sk"):
    #         v = k.get(name)
    #         if isinstance(v, str) and v.strip():
    #             return v.strip()

    #     return ""

    secret = _select_secret(keys)
    if not secret:
        return _resp(400, {"success": False, "error": "Stripe secret key not found for client"})

    # Use the module-level `stripe` import
    stripe.api_key = secret
    REQ = {"stripe_account": connected_account_id} if connected_account_id else {}

    # Optional shipping
    shipping_dict = None
    if isinstance(shipping_address, dict) and (shipping_address.get("line1") or shipping_address.get("postal_code")):
        shipping_dict = {
            "name": shipping_address.get("name") or "",
            "phone": shipping_address.get("phone"),
            "address": {
                "line1": shipping_address.get("line1", ""),
                "line2": shipping_address.get("line2"),
                "city": shipping_address.get("city", ""),
                "state": shipping_address.get("state", ""),
                "postal_code": shipping_address.get("postal_code", ""),
                "country": shipping_address.get("country", "US"),
            },
        }

    # ---- Get payment method that's already attached to this customer ----
    # This matches the legacy implementation - we only use PMs already attached to the customer
    # to avoid the "PaymentMethod was previously used without Customer attachment" error
    try:
        pm_to_use = _get_customer_payment_method(customer_id, REQ)
        logger.info(f"Using payment method {pm_to_use} for upsell")
    except ValueError as e:
        logger.error(f"No payment method available for customer {customer_id}: {e}")
        return _resp(409, {"success": False, "error": "No saved payment method available for customer"})

    try:
        # Price in correct account scope
        price = stripe.Price.retrieve(upsell_price_id, **REQ)
        amount = price.unit_amount
        currency = price.currency

        # Idempotency to prevent double charges on retries
        idempotency_key = f"upsell:{client_id}:{session_id}:{upsell_price_id}"

        # Create + confirm off-session PI against the attached PM
        pi = stripe.PaymentIntent.create(
            amount=amount,
            currency=currency,
            customer=customer_id,
            payment_method=pm_to_use,
            confirmation_method="automatic",
            confirm=True,
            off_session=True,
            description="One-click upsell",
            metadata={
                "clientID": client_id,
                "upsell": "true",
                "original_session_id": session_id,
                "upsell_price_id": upsell_price_id,
            },
            shipping=shipping_dict,
            idempotency_key=idempotency_key,  # request option recognized by stripe-python
            **REQ,
        )

        logger.info(f"[Upsell] PI {pi.id} status={pi.status} account={'platform' if not REQ else REQ['stripe_account']}")
        return _resp(200, {"success": True, "payment_intent_id": pi.id, "status": pi.status})

    except stripe.error.CardError as e:
        logger.error(f"[Upsell] Card error: {e}")
        return _resp(400, {"success": False, "error": "Card was declined", "decline_code": getattr(e, 'code', None)})
    except stripe.error.StripeError as e:
        msg = str(e)
        logger.error(f"[Upsell] Stripe error: {msg}")
        if "used with a paymentintent without customer attachment" in msg.lower():
            return _resp(400, {
                "success": False,
                "error": "Payment method must be attached to the customer before reuse",
            })
        return _resp(400, {"success": False, "error": f"Stripe error: {msg}"})
    except ValueError as e:
        logger.error(f"[Upsell] {e}")
        return _resp(409, {"success": False, "error": str(e)})
    except Exception:
        logger.exception("[Upsell] Unexpected server error")
        return _resp(500, {"success": False, "error": "Unexpected server error"})


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/upsell-session helper
# ─────────────────────────────────────────────────────────────────────────────
# def get_upsell_session(event):
#     # Parse query params
#     qs = event.get("queryStringParameters") or {}
#     if not qs and event.get("rawQueryString"):
#         from urllib.parse import parse_qs
#         qs = {k: v[0] for k, v in parse_qs(event["rawQueryString"]).items()}

#     session_id = (qs.get("session_id") or qs.get("sessionId") or "").strip()
#     client_id  = (qs.get("clientID") or qs.get("client_id") or "").strip()

#     if not session_id:
#         return _resp(400, {"success": False, "error": "Missing session_id"})

#     # 1) Try cache (DDB)
#     try:
#         sess_tbl, _ = _get_table_and_name("sessions")
#         if sess_tbl:
#             r = sess_tbl.get_item(Key={"session_id": session_id})
#             item = r.get("Item")
#             if item and (item.get("customer_id") or item.get("email")):
#                 return _resp(200, {
#                     "success": True,
#                     "session_id": session_id,
#                     "customer_id": item.get("customer_id", ""),
#                     "email": item.get("email", "")
#                 })
#     except Exception as e:
#         logger.error(f"[UpsellSession] DDB get error: {e}")

#     # 2) Fallback: retrieve from Stripe
#     REQ = {}
#     try:
#         keys = _get_tenant_keys(client_id) if client_id else {}
#         secret = _select_secret(keys) or os.environ.get("STRIPE_SECRET", "")
#         if secret:
#             stripe.api_key = secret
#         acct = keys.get("connected_account_id") or ""
#         if acct:
#             REQ = {"stripe_account": acct}
#     except Exception as e:
#         logger.warning(f"[UpsellSession] key init warn: {e}")

#     customer_id = ""
#     email = ""

#     try:
#         if hasattr(stripe, "checkout") and hasattr(stripe.checkout, "Session"):
#             sess = stripe.checkout.Session.retrieve(session_id, expand=["customer_details", "payment_intent.customer"], **REQ)
#         else:
#             sess = stripe.Checkout.Session.retrieve(session_id, expand=["customer_details", "payment_intent.customer"], **REQ)  # type: ignore[attr-defined]

#         c = sess.get("customer")
#         customer_id = c if isinstance(c, str) else (c.get("id") if isinstance(c, dict) else "")
#         email = ((sess.get("customer_details") or {}).get("email")
#                  or sess.get("customer_email") or "")

#         if not customer_id and sess.get("payment_intent"):
#             pi_id = sess["payment_intent"]["id"] if isinstance(sess["payment_intent"], dict) else sess["payment_intent"]
#             try:
#                 pi = stripe.PaymentIntent.retrieve(pi_id, **REQ)
#                 c = pi.get("customer")
#                 customer_id = c if isinstance(c, str) else (c.get("id") if isinstance(c, dict) else "")
#             except Exception as e:
#                 logger.info(f"[UpsellSession] PI recovery failed: {e}")
#     except Exception as e:
#         logger.error(f"[UpsellSession] Stripe retrieve error: {e}")

#     # 3) Persist discoveries (best-effort)
#     now = int(time.time())
#     try:
#         cust_tbl, _ = _get_table_and_name("customers")
#         if cust_tbl and customer_id:
#             expr = "SET updatedAt=:u"
#             vals = {":u": now}
#             if email:
#                 expr += ", email=:e"; vals[":e"] = email
#             if client_id:
#                 expr += ", clientID=:c"; vals[":c"] = client_id
#             cust_tbl.update_item(Key={"customer_id": customer_id}, UpdateExpression=expr, ExpressionAttributeValues=vals)
#     except Exception as e:
#         logger.error(f"[UpsellSession] Customers upsert error: {e}")

#     try:
#         sess_tbl, _ = _get_table_and_name("sessions")
#         if sess_tbl:
#             sess_tbl.put_item(Item={
#                 "session_id": session_id,
#                 "customer_id": customer_id or "",
#                 "email": email or "",
#                 "clientID": client_id or "",
#                 "createdAt": now,
#             })
#     except Exception as e:
#         logger.error(f"[UpsellSession] Sessions put error: {e}")

#     if not (customer_id or email):
#         return _resp(404, {"success": False, "error": "Session not found or not completed yet", "session_id": session_id})

#     return _resp(200, {"success": True, "session_id": session_id, "customer_id": customer_id, "email": email})

def get_upsell_session_cached(event):
    """
    GET /api/upsell-session?session_id=cs_...&clientID=tenant-123
    Returns: { success, session_id, customer_id, email }
    """
    # Parse query params (APIGW v1/v2)
    qs = event.get("queryStringParameters") or {}
    if not qs and event.get("rawQueryString"):
        from urllib.parse import parse_qs
        qs = {k: v[0] for k, v in parse_qs(event["rawQueryString"]).items()}

    session_id = (qs.get("session_id") or qs.get("sessionId") or "").strip()
    client_id  = (qs.get("clientID") or qs.get("client_id") or "").strip()

    if not session_id:
        return _resp(400, {"success": False, "error": "Missing session_id"})

    # 1) Try cache in DDB
    try:
        sess_tbl, _ = _get_table_and_name("sessions")
        if sess_tbl:
            r = sess_tbl.get_item(Key={"session_id": session_id})
            item = r.get("Item")
            if item and (item.get("customer_id") or item.get("email")):
                return _resp(200, {
                    "success": True,
                    "session_id": session_id,
                    "customer_id": item.get("customer_id", ""),
                    "email": item.get("email", "")
                })
    except Exception as e:
        logger.error(f"[UpsellSession] DDB get error: {e}")

    # 2) Cache miss → recover from Stripe Checkout
    REQ = {}
    try:
        keys = get_stripe_key_for_client(client_id) if client_id else {}
        if isinstance(keys, str):
            keys = {"secret_key": keys}
        secret = _select_secret(keys) or os.environ.get("STRIPE_SECRET", "")
        if secret:
            stripe.api_key = secret
        acct = ""
        for k in ("connected_account_id", "account_id", "stripe_account"):
            v = keys.get(k)
            if isinstance(v, str) and v.strip():
                acct = v.strip(); break
        if acct:
            REQ = {"stripe_account": acct}
    except Exception as e:
        logger.warning(f"[UpsellSession] key init warn: {e}")

    customer_id = ""
    email = ""

    try:
        # retrieve Session (support modern + legacy alias)
        if hasattr(stripe, "checkout") and hasattr(stripe.checkout, "Session"):
            sess = stripe.checkout.Session.retrieve(
                session_id,
                expand=["customer_details", "payment_intent.customer"],
                **REQ,
            )
        else:
            sess = stripe.Checkout.Session.retrieve(  # type: ignore[attr-defined]
                session_id,
                expand=["customer_details", "payment_intent.customer"],
                **REQ,
            )

        c = sess.get("customer")
        customer_id = c if isinstance(c, str) else (c.get("id") if isinstance(c, dict) else "")
        email = ((sess.get("customer_details") or {}).get("email")
                 or sess.get("customer_email") or "")

        # If customer still missing, try PI
        if not customer_id and sess.get("payment_intent"):
            pi_id = sess["payment_intent"]["id"] if isinstance(sess["payment_intent"], dict) else sess["payment_intent"]
            try:
                pi = stripe.PaymentIntent.retrieve(pi_id, **REQ)
                c = pi.get("customer")
                customer_id = c if isinstance(c, str) else (c.get("id") if isinstance(c, dict) else "")
            except Exception as e:
                logger.info(f"[UpsellSession] PI recovery failed: {e}")

    except Exception as e:
        logger.error(f"[UpsellSession] Stripe retrieve error: {e}")

    # 3) Persist (best-effort). Your Customers table uses a composite PK (clientID, customer_id).
    now = int(time.time())
    try:
        cust_tbl, _ = _get_table_and_name("customers")
        if cust_tbl and customer_id and client_id:
            expr = "SET updatedAt=:u"
            vals = {":u": now}
            if email:
                expr += ", email=:e"; vals[":e"] = email
            cust_tbl.update_item(
                Key={"clientID": client_id, "customer_id": customer_id},
                UpdateExpression=expr,
                ExpressionAttributeValues=vals
            )
    except Exception as e:
        logger.error(f"[UpsellSession] Customers upsert error: {e}")

    try:
        sess_tbl, _ = _get_table_and_name("sessions")
        if sess_tbl:
            sess_tbl.put_item(Item={
                "session_id": session_id,
                "customer_id": customer_id or "",
                "email": email or "",
                "clientID": client_id or "",
                "createdAt": now,
            })
    except Exception as e:
        logger.error(f"[UpsellSession] Sessions put error: {e}")

    if not (customer_id or email):
        return _resp(404, {"success": False, "error": "Session not found or not completed yet", "session_id": session_id})

    return _resp(200, {
        "success": True,
        "session_id": session_id,
        "customer_id": customer_id,
        "email": email
    })



# def lambda_handler(event, context):
#     """Main Lambda handler routing to appropriate function"""
    
#     method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method")
#     path   = event.get("path") or event.get("rawPath") or event.get("requestContext", {}).get("http", {}).get("path") or ""

#     if method == "OPTIONS":
#         return _resp(200, {"ok": True})

#     if method == "GET" and path.endswith("/api/upsell-session"):
#         return get_upsell_session(event)

#     if method == "POST" and path.endswith("/api/process-upsell"):
#         return process_one_click_upsell(event)
#     else:
#         return _resp(404, {"error": "Not found"})

def lambda_handler(event, context):
    """Main Lambda handler routing to appropriate function"""
    
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method")
    path   = event.get("path") or event.get("rawPath") or event.get("requestContext", {}).get("http", {}).get("path") or ""

    if method == "OPTIONS":
        return _resp(200, {"ok": True})

    # ❌ PROBLEM: This calls get_upsell_session (DDB cache, no metadata)
    # ✅ SOLUTION: Should call get_upsell_session_details (Stripe, has metadata)
    if method == "GET" and path.endswith("/api/upsell-session"):
        return get_upsell_session_details(event)  # ✅ CHANGED THIS LINE

    if method == "POST" and path.endswith("/api/process-upsell"):
        return process_one_click_upsell(event)
    else:
        return _resp(404, {"error": "Not found"})

# For local testing
if __name__ == "__main__":
    # Test get session details
    test_event = {
        "httpMethod": "GET",
        "resource": "/api/upsell-session",
        "queryStringParameters": {
            "session_id": "cs_test_xxx",
            "clientID": "d831c360-00e1-706a-6d3a-2a5361c20df6",
        }
    }
    
    response = lambda_handler(test_event, None)
    print(json.dumps(response, indent=2))