# stripe_cart.py
# Webhook-only handler with FIXED KMS decryption using shared kms_utils layer

import os
import json
import time
import logging
from typing import Any, Dict, Tuple
from datetime import datetime, timezone
from urllib.parse import unquote

import boto3
import stripe

# Import shared KMS utilities from layer
from kms_utils import kms_decrypt_wrapped

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")

# ════════════════════════════════════════════════════════════════════════════
# Response helper
# ════════════════════════════════════════════════════════════════════════════
def _resp(status: int, body: Dict[str, Any]):
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, stripe-signature, Stripe-Signature, X-Client-Id, X-Offer-Name, X-Amz-Date, X-Api-Key, X-Amz-Security-Token",
        "Access-Control-Max-Age": "600",
    }
    return {"statusCode": status, "headers": headers, "body": json.dumps(body)}

# ════════════════════════════════════════════════════════════════════════════
# Env resolution helpers (accept common aliases)
# ════════════════════════════════════════════════════════════════════════════
_CUSTOMERS_ENV_KEYS = ["CustomersTableName", "CUSTOMERS_TABLE", "CustomersTable", "CUSTOMERS"]
_SESSIONS_ENV_KEYS  = ["CheckoutSessionsTableName", "CHECKOUT_SESSIONS_TABLE", "CheckoutSessionsTable", "CHECKOUT_SESSIONS"]
_ORDERS_ENV_KEYS    = ["OrdersTableName", "ORDERS_TABLE", "OrdersTable", "ORDERS"]
_KEYS_ENV_KEYS      = ["StripeKeysTable", "STRIPE_KEYS_TABLE", "StripeKeysTableName"]

def _get_env_any(keys) -> str:
    for k in keys:
        v = os.environ.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""

def _get_table_and_name(kind: str) -> Tuple[Any, str]:
    """
    kind in {'customers', 'sessions', 'orders'}
    Returns (dynamodb.Table or None, table_name_str)
    """
    if kind == "customers":
        name = _get_env_any(_CUSTOMERS_ENV_KEYS)
    elif kind == "sessions":
        name = _get_env_any(_SESSIONS_ENV_KEYS)
    elif kind == "orders":
        name = _get_env_any(_ORDERS_ENV_KEYS)
    else:
        raise ValueError("kind must be 'customers', 'sessions', or 'orders'")

    if not name:
        logger.warning(f"[WH] {kind} table env var not set; skipping persistence")
        return None, ""
    try:
        return dynamodb.Table(name), name
    except Exception as e:
        logger.error(f"[WH] Could not init DDB table {name}: {e}")
        return None, name

def _get_keys_table():
    tname = _get_env_any(_KEYS_ENV_KEYS)
    if not tname:
        logger.warning("[WH] StripeKeysTable env var not set")
        return None
    try:
        return dynamodb.Table(tname)
    except Exception as e:
        logger.error(f"[WH] Could not init StripeKeysTable {tname}: {e}")
        return None

# ════════════════════════════════════════════════════════════════════════════
# Resolve webhook secret (prefer /webhook/{whsec_...}; else use table + KMS)
# ════════════════════════════════════════════════════════════════════════════
# def _resolve_webhook_secret(event, payload: str) -> str:
#     """
#     Resolve webhook secret in order of preference:
#     1. Path parameter (e.g., /webhook/whsec_...)
#     2. Environment variable STRIPE_WEBHOOK_SECRET
#     3. Lookup in StripeKeysTable by clientID and decrypt with KMS
#     """
#     # 1) path token preferred
#     path_params = event.get("pathParameters") or {}
#     token = (path_params.get("token") or "").strip()
#     if token.startswith("whsec_"):
#         logger.info("[WH] Using webhook secret from path parameter")
#         return token

#     # 2) optional global env fallback
#     env_whsec = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
#     if env_whsec.startswith("whsec_"):
#         logger.info("[WH] Using webhook secret from environment variable")
#         return env_whsec

#     # 3) resolve via clientID -> StripeKeysTable -> decrypt wrapped secret
#     logger.info("[WH] Attempting to resolve webhook secret from StripeKeysTable")
#     try:
#         tmp = json.loads(payload or "{}")
#         obj = (tmp.get("data") or {}).get("object") or {}
#         client_id = (obj.get("metadata") or {}).get("clientID") or (obj.get("metadata") or {}).get("client_id")
#     except Exception as e:
#         logger.error(f"[WH] Failed to parse payload for clientID: {e}")
#         client_id = None

#     if not client_id:
#         raise ValueError("Unable to resolve clientID for webhook secret lookup")

#     tbl = _get_keys_table()
#     if not tbl:
#         raise ValueError("StripeKeysTable env var not set")

#     # fetch item (try a few PK names)
#     item = None
#     for key_name in ("clientID", "client_id", "id"):
#         try:
#             resp = tbl.get_item(Key={key_name: client_id})
#             item = resp.get("Item")
#             if item:
#                 logger.info(f"[WH] Found StripeKeys item for clientID: {client_id}")
#                 break
#         except Exception as e:
#             logger.warning(f"[WH] Failed to lookup with key {key_name}: {e}")
#             pass
    
#     if not item:
#         raise ValueError(f"No Stripe keys row for clientID {client_id}")

#     # Try multiple possible field names for webhook secret
#     candidates = [
#         "webhook_secret_wrapped",
#         "whsec_wrapped",
#         "webhook_whsec_wrapped",
#         "webhook_secret_encrypted",
#         "webhook_secret",
#         "wh_secret_test",      # From admin_keys.py SECRET_FIELDS
#         "wh_secret_live",      # From admin_keys.py SECRET_FIELDS
#         "whsec_test",          # Alternative names
#         "whsec_live",
#     ]
    
#     wrapped = None
#     field_found = None
    
#     # First try direct fields
#     for f in candidates:
#         v = item.get(f)
#         if isinstance(v, str) and v:
#             wrapped = v
#             field_found = f
#             logger.info(f"[WH] Found webhook secret in field: {f}")
#             break
    
#     # If not found, try nested dicts
#     if not wrapped:
#         for nest in ("webhook", "stripe", "keys"):
#             s = item.get(nest)
#             if isinstance(s, dict):
#                 for f in candidates:
#                     v = s.get(f)
#                     if isinstance(v, str) and v:
#                         wrapped = v
#                         field_found = f"{nest}.{f}"
#                         logger.info(f"[WH] Found webhook secret in nested field: {field_found}")
#                         break
#             if wrapped:
#                 break
    
#     if not wrapped:
#         logger.error(f"[WH] Available fields in item: {list(item.keys())}")
#         raise ValueError("No webhook secret found in database")

#     # 🔐 Decrypt with KMS using shared utility
#     logger.info(f"[WH] Attempting to decrypt webhook secret from field: {field_found}")
    
#     try:
#         whsec = kms_decrypt_wrapped(wrapped)
        
#         if not isinstance(whsec, str) or not whsec.startswith("whsec_"):
#             logger.error(f"[WH] Decrypted value is invalid (doesn't start with whsec_)")
#             raise ValueError("Decrypted webhook secret invalid (should start with whsec_)")
        
#         logger.info(f"[WH] ✅ Successfully decrypted webhook secret (length: {len(whsec)})")
#         return whsec
        
#     except Exception as e:
#         logger.error(f"[WH] ❌ Failed to decrypt webhook secret: {e}")
#         raise ValueError(f"Failed to decrypt webhook secret from field '{field_found}': {e}")

def _resolve_webhook_secret(event, payload: str) -> str:
    """
    Resolve webhook secret in order of preference:
    1. Path parameter (e.g., /webhook/whsec_...)
    2. Environment variable STRIPE_WEBHOOK_SECRET
    3. Lookup in StripeKeysTable by clientID and decrypt with KMS
       - Determines mode from event.livemode in payload
       - Uses appropriate test/live secret
    """
    # 1) path token preferred
    path_params = event.get("pathParameters") or {}
    token = (path_params.get("token") or "").strip()
    if token.startswith("whsec_"):
        logger.info("[WH] Using webhook secret from path parameter")
        return token

    # 2) optional global env fallback
    env_whsec = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if env_whsec.startswith("whsec_"):
        logger.info("[WH] Using webhook secret from environment variable")
        return env_whsec

    # 3) resolve via clientID -> StripeKeysTable -> decrypt wrapped secret
    logger.info("[WH] Attempting to resolve webhook secret from StripeKeysTable")
    
    # Parse payload to get clientID AND livemode
    try:
        tmp = json.loads(payload or "{}")
        obj = (tmp.get("data") or {}).get("object") or {}
        client_id = (obj.get("metadata") or {}).get("clientID") or (obj.get("metadata") or {}).get("client_id")
        livemode = tmp.get("livemode", False)  # ← CRITICAL: check event.livemode
    except Exception as e:
        logger.error(f"[WH] Failed to parse payload for clientID/livemode: {e}")
        client_id = None
        livemode = False

    if not client_id:
        raise ValueError("Unable to resolve clientID for webhook secret lookup")

    tbl = _get_keys_table()
    if not tbl:
        raise ValueError("StripeKeysTable env var not set")

    # fetch item (try a few PK names)
    item = None
    for key_name in ("clientID", "client_id", "id"):
        try:
            resp = tbl.get_item(Key={key_name: client_id})
            item = resp.get("Item")
            if item:
                logger.info(f"[WH] Found StripeKeys item for clientID: {client_id}")
                break
        except Exception as e:
            logger.warning(f"[WH] Failed to lookup with key {key_name}: {e}")
            pass
    
    if not item:
        raise ValueError(f"No Stripe keys row for clientID {client_id}")

    # FIXED: Select webhook secret based on event.livemode
    # This ensures test webhooks use test secret, live webhooks use live secret
    mode = "live" if livemode else "test"
    logger.info(f"[WH] Event livemode={livemode}, using {mode} webhook secret")
    
    # Priority order for each mode
    if mode == "live":
        candidates = [
            "wh_secret_live",
            # Fallback to legacy/generic fields if mode-specific not found
            "webhook_secret_wrapped",
            "whsec_wrapped",
            "webhook_whsec_wrapped",
            "webhook_secret_encrypted",
            "webhook_secret",
        ]
    else:  # test mode
        candidates = [
            "wh_secret_test",
            # Fallback to legacy/generic fields if mode-specific not found
            "webhook_secret_wrapped",
            "whsec_wrapped",
            "webhook_whsec_wrapped",
            "webhook_secret_encrypted",
            "webhook_secret",
        ]
    
    wrapped = None
    field_found = None
    
    # Try fields in priority order for this mode
    for f in candidates:
        v = item.get(f)
        if isinstance(v, str) and v:
            wrapped = v
            field_found = f
            logger.info(f"[WH] Found {mode} webhook secret in field: {f}")
            break
    
    # If not found, try nested dicts (legacy structure)
    if not wrapped:
        for nest in ("webhook", "stripe", "keys"):
            nested = item.get(nest)
            if isinstance(nested, dict):
                for f in candidates:
                    v = nested.get(f)
                    if isinstance(v, str) and v:
                        wrapped = v
                        field_found = f"{nest}.{f}"
                        logger.info(f"[WH] Found {mode} webhook secret in nested field: {field_found}")
                        break
            if wrapped:
                break

    if not wrapped:
        raise ValueError(f"No {mode} webhook secret found in StripeKeys table for clientID {client_id}")

    # Decrypt if wrapped with ENCRYPTED()
    try:
        decrypted = kms_decrypt_wrapped(wrapped)
        logger.info(f"[WH] Decrypted {mode} webhook secret from field: {field_found}")
        return decrypted
    except Exception as e:
        logger.error(f"[WH] Failed to decrypt {mode} webhook secret from {field_found}: {e}")
        raise ValueError(f"Failed to decrypt {mode} webhook secret: {e}")

# ════════════════════════════════════════════════════════════════════════════
# Get Stripe API key for customer recovery
# ════════════════════════════════════════════════════════════════════════════
def _get_stripe_api_key(client_id: str) -> str:
    """
    Get Stripe API key for recovering customer_id from PaymentIntent.
    Tries in order:
    1. StripeKeysTable lookup by clientID
    2. Environment variables
    """
    # Try environment variable first
    env_keys = ["STRIPE_SECRET", "STRIPE_PLATFORM_SECRET", "STRIPE_SECRET_KEY"]
    for key in env_keys:
        val = os.environ.get(key, "").strip()
        if val and (val.startswith("sk_test_") or val.startswith("sk_live_")):
            logger.info(f"[WH] Using Stripe API key from env var: {key}")
            return val
    
    # Try database lookup if we have clientID
    if not client_id:
        logger.warning("[WH] No clientID provided for Stripe API key lookup")
        return ""
    
    tbl = _get_keys_table()
    if not tbl:
        logger.warning("[WH] StripeKeysTable not available for API key lookup")
        return ""
    
    try:
        # Try to get the keys for this client
        for key_name in ("clientID", "client_id", "id"):
            try:
                resp = tbl.get_item(Key={key_name: client_id})
                item = resp.get("Item")
                if item:
                    # Get the mode (test or live)
                    env = os.environ.get("ENVIRONMENT", "dev")
                    mode = "live" if env == "prod" else "test"
                    
                    # Try to get the secret key
                    candidates = [
                        f"sk_{mode}",
                        f"{mode}_secret_key",
                        "secret_key",
                        "sk",
                    ]
                    
                    for field in candidates:
                        sk = item.get(field)
                        if sk:
                            # Decrypt if wrapped using shared utility
                            sk = kms_decrypt_wrapped(sk)
                            if sk and (sk.startswith("sk_test_") or sk.startswith("sk_live_")):
                                logger.info(f"[WH] Using Stripe API key from database field: {field}")
                                return sk
                    break
            except Exception as e:
                logger.warning(f"[WH] Failed to lookup API key with key {key_name}: {e}")
        
        logger.warning(f"[WH] No valid Stripe API key found for clientID: {client_id}")
    except Exception as e:
        logger.error(f"[WH] Error looking up Stripe API key: {e}")
    
    return ""

# ════════════════════════════════════════════════════════════════════════════
# Send SMS notification for new order
# ════════════════════════════════════════════════════════════════════════════
def _send_order_sms(client_id: str, order_data: Dict[str, Any]):
    """
    Send SMS notification about new order to configured phone number.
    Looks up sms_notification_phone from tenant config.
    """
    try:
        # Get tenant config to find SMS notification phone
        app_config_table_name = _get_env_any(["APP_CONFIG_TABLE", "AppConfigTable"])
        if not app_config_table_name:
            logger.warning("[SMS] APP_CONFIG_TABLE not configured - skipping SMS notification")
            return
        
        app_config_table = dynamodb.Table(app_config_table_name)
        
        # Get tenant config
        env = os.environ.get("ENVIRONMENT", "dev")
        
        # Query for the sms_notification_phone config for this client
        # The config key format is: {clientID}:sms_notification_phone
        config_key = f"{client_id}:sms_notification_phone"
        
        response = app_config_table.get_item(
            Key={"config_key": config_key, "environment": env}
        )
        
        config = response.get("Item", {})
        sms_phone = config.get("value")  # The actual value is in the "value" field
        
        if not sms_phone:
            logger.info("[SMS] No SMS notification phone configured for this client - skipping SMS")
            return
        
        # Format SMS message
        customer_name = order_data.get("customer_name", "Unknown")
        customer_email = order_data.get("customer_email", "N/A")
        product_name = order_data.get("product_name", "Unknown Product")
        amount = order_data.get("amount_total", 0) / 100  # Convert cents to dollars
        currency = order_data.get("currency", "USD").upper()
        order_id = order_data.get("order_id", "N/A")
        
        message = f"""🎉 NEW ORDER RECEIVED!

Order: {order_id}
Customer: {customer_name}
Email: {customer_email}
Product: {product_name}
Amount: ${amount:.2f} {currency}

View orders in your admin dashboard."""
        
        # Send SMS via AWS SNS
        logger.info(f"[SMS] Sending order notification to {sms_phone}")
        
        response = sns.publish(
            PhoneNumber=sms_phone,
            Message=message,
            MessageAttributes={
                'AWS.SNS.SMS.SMSType': {
                    'DataType': 'String',
                    'StringValue': 'Transactional'  # Use transactional for order notifications
                }
            }
        )
        
        logger.info(f"[SMS] ✅ SMS sent successfully! MessageId: {response.get('MessageId')}")
        
    except Exception as e:
        # Don't fail the whole webhook if SMS fails
        logger.error(f"[SMS] ❌ Failed to send SMS notification: {e}", exc_info=True)

# ════════════════════════════════════════════════════════════════════════════
# Create Order in OrdersTable
# ════════════════════════════════════════════════════════════════════════════
def _create_order_from_session(session_data: Dict[str, Any], client_id: str):
    """
    Create an order record in OrdersTable from Stripe checkout session data.
    """
    orders_tbl, orders_name = _get_table_and_name("orders")
    if not orders_tbl:
        logger.warning("[WH] ⚠️ OrdersTable not configured - skipping order creation!")
        return
    
    if not client_id:
        logger.warning("[WH] ⚠️ Cannot create order without clientID")
        return
    
    try:
        # Extract data from session
        session_id = session_data.get("id")
        customer_id = session_data.get("customer")
        customer_details = session_data.get("customer_details") or {}
        
        # Get line items (if available in session object)
        line_items_data = session_data.get("line_items") or {}
        line_items = line_items_data.get("data", []) if isinstance(line_items_data, dict) else []
        
        # Extract product name from first line item
        product_name = "Unknown Product"
        if line_items and len(line_items) > 0:
            first_item = line_items[0]
            product_name = first_item.get("description") or first_item.get("price", {}).get("product", {}).get("name") or "Unknown Product"
        
        # Build shipping address string
        shipping = session_data.get("shipping") or session_data.get("shipping_details") or {}
        address = shipping.get("address") or {}
        shipping_address = "N/A"
        if address:
            parts = [
                address.get("line1"),
                address.get("line2"),
                address.get("city"),
                address.get("state"),
                address.get("postal_code"),
                address.get("country")
            ]
            shipping_address = ", ".join([p for p in parts if p])
        
        # Get customer info
        customer_name = shipping.get("name") or customer_details.get("name") or "N/A"
        customer_email = customer_details.get("email") or session_data.get("customer_email") or "N/A"
        customer_phone = customer_details.get("phone") or "N/A"
        
        # Create order_id from session_id
        order_id = f"ord_{session_id}"
        
        # Get current timestamp
        now_iso = datetime.now(timezone.utc).isoformat()
        now_unix = int(time.time())
        
        # Get metadata
        metadata = session_data.get("metadata") or {}
        offer = metadata.get("offer") or metadata.get("offer_name") or "N/A"
        
        # Build order item
        order_item = {
            "clientID": client_id,
            "order_id": order_id,
            "stripe_session_id": session_id,
            "stripe_customer_id": customer_id or "N/A",
            "stripe_payment_intent": session_data.get("payment_intent") or "N/A",
            
            # Customer info
            "customer_name": customer_name,
            "customer_email": customer_email,
            "customer_phone": customer_phone,
            
            # Product info
            "product_name": product_name,
            "offer": offer,
            
            # Shipping
            "shipping_address": shipping_address,
            
            # Payment info
            "amount_total": session_data.get("amount_total", 0),
            "amount": session_data.get("amount_total", 0),  # Duplicate for compatibility
            "currency": session_data.get("currency", "usd"),
            "payment_status": session_data.get("payment_status", "pending"),
            
            # Status
            "status": "created",
            "fulfilled": False,
            
            # Timestamps
            "created_at": now_iso,
            "updated_at": now_iso,
            "order_date": datetime.fromtimestamp(now_unix, tz=timezone.utc).strftime("%Y-%m-%d"),
            
            # Metadata
            "environment": os.environ.get("ENVIRONMENT", "dev"),
            "source": "webhook",
            "metadata": metadata,
        }
        
        # Save to DynamoDB
        orders_tbl.put_item(Item=order_item)
        logger.info(f"[WH] ✅ Created order {order_id} in {orders_name} for clientID {client_id}")
        logger.info(f"[WH]    Customer: {customer_name} ({customer_email})")
        logger.info(f"[WH]    Product: {product_name}")
        logger.info(f"[WH]    Amount: ${order_item['amount_total']/100:.2f} {order_item['currency'].upper()}")
        
        # Return order data for SMS notification
        return order_item
        
    except Exception as e:
        logger.error(f"[WH] ❌ Failed to create order: {e}", exc_info=True)
        return None

# ════════════════════════════════════════════════════════════════════════════
# Lambda entry point – webhook only
# ════════════════════════════════════════════════════════════════════════════
def lambda_handler(event, context):
    try:
        method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method")
        path   = event.get("path") or event.get("rawPath") or event.get("requestContext", {}).get("http", {}).get("path") or ""

        logger.info(f"[WH] Received {method} request to {path}")

        if method == "OPTIONS":
            return _resp(200, {"ok": True})

        # POST /webhook/{token}
        if method == "POST" and "/webhook" in path:
            payload = event.get("body") or ""
            headers = event.get("headers") or {}
            sig = headers.get("Stripe-Signature") or headers.get("stripe-signature")
            
            if not sig:
                logger.error("[WH] Missing Stripe-Signature header")
                return _resp(400, {"error": "Missing Stripe-Signature"})

            # Resolve whsec using path or keys table + KMS
            try:
                secret = _resolve_webhook_secret(event, payload)
            except Exception as e:
                logger.error(f"[WH] Webhook secret resolution failed: {e}")
                return _resp(400, {"error": "Could not resolve webhook secret", "details": str(e)})

            # Verify signature
            try:
                evt = stripe.Webhook.construct_event(payload=payload, sig_header=sig, secret=secret)
                logger.info("[WH] ✅ Signature verified successfully")
            except stripe.error.SignatureVerificationError as e:
                logger.error(f"[WH] ❌ Signature verification failed: {e}")
                return _resp(400, {"error": "Invalid signature"})

            etype = evt.get("type")
            data = (evt.get("data") or {}).get("object") or {}
            logger.info(f"[WH] Processing event type: {etype}")

            if etype == "checkout.session.completed":
                try:
                    # Extract data from the session
                    email = (data.get("customer_details") or {}).get("email") or data.get("customer_email")
                    customer_id = data.get("customer")
                    session_id  = data.get("id")
                    client_id   = (data.get("metadata") or {}).get("clientID") or (data.get("metadata") or {}).get("client_id")
                    offer       = (data.get("metadata") or {}).get("offer")

                    logger.info(f"[WH] Session data - ID: {session_id}, Customer: {customer_id}, Email: {email}, ClientID: {client_id}")

                    # (Optional) Recover customer_id from PaymentIntent if missing
                    pi_id = data.get("payment_intent")
                    if not customer_id and pi_id:
                        logger.info(f"[WH] Customer ID missing, attempting recovery from PaymentIntent: {pi_id}")
                        api_key = _get_stripe_api_key(client_id or "")
                        
                        if api_key:
                            try:
                                stripe.api_key = api_key
                                pi = stripe.PaymentIntent.retrieve(pi_id)
                                customer_id = pi.get("customer")
                                logger.info(f"[WH] ✅ Recovered customer_id from PI: {customer_id}")
                            except Exception as e:
                                logger.warning(f"[WH] PI recovery failed: {e}")
                        else:
                            logger.warning("[WH] No Stripe API key available for PI recovery")

                    now = int(time.time())

                    # ⭐ CREATE ORDER FIRST (most important!)
                    order_data = _create_order_from_session(data, client_id or "")
                    
                    # 📱 SEND SMS NOTIFICATION (uses order_data from above)
                    if order_data:
                        try:
                            _send_order_sms(client_id or "", order_data)
                        except Exception as sms_err:
                            logger.error(f"[WH] SMS notification failed (non-critical): {sms_err}")

                    # Upsert Customers (composite key: clientID + customer_id)
                    try:
                        cust_tbl, cust_name = _get_table_and_name("customers")
                        if cust_tbl and customer_id and client_id:
                            upd = "SET email=:e, updatedAt=:u"
                            vals = {":e": email or "", ":u": now}
                            if offer:
                                upd += ", lastOffer=:o"
                                vals[":o"] = offer
                            
                            cust_tbl.update_item(
                                Key={"clientID": client_id, "customer_id": customer_id},
                                UpdateExpression=upd,
                                ExpressionAttributeValues=vals
                            )
                            logger.info(f"[WH] ✅ Saved customer {customer_id} with email={email} to {cust_name}")
                        elif not cust_tbl:
                            logger.warning("[WH] ⚠️ Skipped Customers upsert (no table configured)")
                        elif not client_id:
                            logger.warning(f"[WH] ⚠️ Skipped Customers upsert (missing clientID in metadata)")
                        elif not customer_id:
                            logger.warning(f"[WH] ⚠️ Skipped Customers upsert (missing customer_id)")
                    except Exception as e:
                        logger.error(f"[WH] ❌ Customers upsert error: {e}")

                    # Save session → (customer_id, email)
                    try:
                        sess_tbl, sess_name = _get_table_and_name("sessions")
                        if sess_tbl and session_id:
                            sess_tbl.put_item(Item={
                                "session_id": session_id,
                                "customer_id": customer_id or "",
                                "email": email or "",
                                "clientID": client_id or "",
                                "offer": offer or "",
                                "createdAt": now,
                            })
                            logger.info(f"[WH] ✅ Saved session map {session_id} -> {customer_id} ({email}) to {sess_name}")
                        elif not sess_tbl:
                            logger.warning("[WH] ⚠️ Skipped Sessions put (no table configured)")
                    except Exception as e:
                        logger.error(f"[WH] ❌ Sessions put error: {e}")

                except Exception as e:
                    logger.exception(f"[WH] ❌ checkout.session.completed handler error: {e}")
                    return _resp(200, {"received": True, "warning": "handler error", "details": str(e)})

                return _resp(200, {"received": True})

            # Ack all other events
            logger.info(f"[WH] Acknowledged event type: {etype}")
            return _resp(200, {"received": True})

        # Any other route is not handled here
        logger.warning(f"[WH] No webhook route for {method} {path}")
        return _resp(404, {"success": False, "error": "Not found"})
        
    except Exception as e:
        logger.exception(f"[WH] ❌ Unexpected handler error: {e}")
        return _resp(500, {"success": False, "error": "Unexpected server error", "details": str(e)})
