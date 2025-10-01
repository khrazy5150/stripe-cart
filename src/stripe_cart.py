import os
import json
import base64
import boto3
import stripe
import logging
from datetime import datetime, timezone
from typing import Tuple, Dict, Any
from boto3.dynamodb.conditions import Attr
from decimal import Decimal

# stripe_cart.py - Enhanced Multi-Tenant, Multi-Offer E-commerce System with Shipping

# Import shipping module
try:
    from shipping_providers import get_shipping_provider
except ImportError:
    logger.warning("shipping_providers module not found - shipping features disabled")
    get_shipping_provider = None

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# --- env & clients ---
ENV = os.getenv("ENVIRONMENT", "dev")
ORDERS_TABLE_NAME = os.getenv("ORDERS_TABLE")
CUSTOMERS_TABLE_NAME = os.getenv("CUSTOMERS_TABLE")
STRIPE_KEYS_TABLE_NAME = os.getenv("STRIPE_KEYS_TABLE", "stripe_keys")

dynamodb = boto3.resource("dynamodb")
KMS = boto3.client("kms")
KMS_KEY_ARN = os.environ["STRIPE_KMS_KEY_ARN"]
ENC_CTX = {"app": "stripe-cart"}

orders_table = dynamodb.Table(ORDERS_TABLE_NAME) if ORDERS_TABLE_NAME else None
customers_table = dynamodb.Table(CUSTOMERS_TABLE_NAME) if CUSTOMERS_TABLE_NAME else None
stripe_keys_table = dynamodb.Table(STRIPE_KEYS_TABLE_NAME)

# ---------- helpers ----------

def _json_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,Stripe-Signature,X-Client-Id,X-Offer-Name",
        "Access-Control-Allow-Methods": "OPTIONS,POST,GET,PUT",
    }

def _ok(body: Dict[str, Any], code: int = 200) -> Dict[str, Any]:
    return {"statusCode": code, "headers": _json_headers(), "body": json.dumps(body)}

def _err(msg: str, code: int = 400) -> Dict[str, Any]:
    logger.warning(msg)
    return {"statusCode": code, "headers": _json_headers(), "body": json.dumps({"error": msg})}

def _require_claim(event, claim):
    try:
        return event['requestContext']['authorizer']['claims'][claim]
    except KeyError:
        raise RuntimeError(f"Missing auth claim: {claim}")

def _extract_client_and_offer_id(event: Dict[str, Any]) -> Tuple[str, str]:
    """Extract client ID and offer name from various sources."""
    headers = event.get("headers") or {}
    
    # Primary: separate headers
    client_id = headers.get("X-Client-Id") or headers.get("x-client-id")
    offer_name = headers.get("X-Offer-Name") or headers.get("x-offer-name") or "default"
    
    # Fallback: path parameters
    if not client_id:
        pp = event.get("pathParameters") or {}
        client_id = pp.get("clientID")
        offer_name = pp.get("offerName") or "default"
    
    # Fallback: query parameters
    if not client_id:
        qs = event.get("queryStringParameters") or {}
        client_id = qs.get("clientID")
        offer_name = qs.get("offerName") or "default"
    
    # Fallback: request body
    if not client_id:
        data = _parse_body(event)
        client_id = data.get("client_id")
        offer_name = data.get("offer_name") or "default"
    
    # Fallback: Cognito
    if not client_id:
        try:
            sub = event.get("requestContext", {}).get("authorizer", {}).get("claims", {}).get("sub")
            if sub:
                client_id = sub
        except:
            pass
    
    return client_id or "default-client", offer_name


def _kms_encrypt_wrapped(plaintext: bytes) -> str:
    resp = KMS.encrypt(
        KeyId=KMS_KEY_ARN,
        Plaintext=plaintext,
        EncryptionContext=ENC_CTX,
    )
    return "ENCRYPTED(" + base64.b64encode(resp["CiphertextBlob"]).decode("utf-8") + ")"


def _kms_decrypt_wrapped(blob: str) -> str:
    """Decrypt KMS-encrypted value with error handling"""
    if not (blob and blob.startswith("ENCRYPTED(") and blob.endswith(")")):
        return blob  # Return as-is if not encrypted format
    try:
        b64 = blob[len("ENCRYPTED("):-1]
        ct = base64.b64decode(b64)
        resp = KMS.decrypt(
            CiphertextBlob=ct, 
            EncryptionContext=ENC_CTX
        )
        return resp['Plaintext'].decode('utf-8')
    except Exception as e:
        logger.error(f"KMS decrypt error: {e}")
        return ""

def load_stripe_tenant_with_offer(event: Dict[str, Any]) -> Tuple[str, str, Dict, str, str, str, str]:
    """
    Returns (clientID, mode, offer_config, publishable_key, secret_key, webhook_secret, full_offer_url)
    """
    client_id, offer_name = _extract_client_and_offer_id(event)
    
    resp = stripe_keys_table.get_item(Key={"clientID": client_id})
    item = resp.get("Item")
    if not item or not item.get("active", True):
        raise ValueError(f"Stripe tenant not found for clientID={client_id}")

    mode = item.get("mode", "test")
    if mode not in ("live", "test"):
        mode = "test"

    pk = item["pk_live"] if mode == "live" else item["pk_test"]
    sk_enc = item["sk_live"] if mode == "live" else item["sk_test"]
    wh = item["wh_secret_live"] if mode == "live" else item["wh_secret_test"]
    
    # Get base URL and offer configurations
    base_frontend_url = item.get("base_frontend_url", f"https://juniorbay.com/dist/{client_id}/")
    offers = item.get("offers", {})
    
    # Get specific offer config
    offer_config = offers.get(offer_name, offers.get("default", {}))
    if not offer_config:
        # Create a default offer config if none exists
        offer_config = {
            "path": "default",
            "product_ids": [],
            "active": True
        }
    
    # Build full offer URL
    offer_path = offer_config.get("path", offer_name)
    full_offer_url = f"{base_frontend_url}{offer_path}/"
    
    sk = _kms_decrypt_wrapped(sk_enc)
    whook = _kms_decrypt_wrapped(wh)
    
    return client_id, mode, offer_config, pk, sk, whook, full_offer_url

def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    try:
        return json.loads(body)
    except Exception:
        return {}

def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def format_date(date_string: str) -> str:
    """Format ISO date string for display."""
    if not date_string:
        return 'N/A'
    try:
        dt = datetime.fromisoformat(date_string.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M:%S UTC')
    except:
        return date_string

def save_or_update_customer(customer_info: Dict[str, Any]) -> None:
    """Save or update customer in DynamoDB."""
    if not customers_table:
        return
        
    try:
        email = (customer_info.get('email') or '').strip().lower()
        if not email:
            return

        now = _iso_now()
        try:
            resp = customers_table.get_item(Key={'email': email})
            existing = resp.get('Item')
        except Exception:
            existing = None

        if existing:
            customers_table.update_item(
                Key={'email': email},
                UpdateExpression='SET #name = :name, phone = :phone, shipping_address = :shipping, updated_at = :updated',
                ExpressionAttributeNames={'#name': 'name'},
                ExpressionAttributeValues={
                    ':name': customer_info.get('name', ''),
                    ':phone': customer_info.get('phone', ''),
                    ':shipping': customer_info.get('shipping_address', {}),
                    ':updated': now
                }
            )
        else:
            customers_table.put_item(Item={
                'email': email,
                'name': customer_info.get('name', ''),
                'phone': customer_info.get('phone', ''),
                'shipping_address': customer_info.get('shipping_address', {}),
                'first_purchase_date': now,
                'created_at': now,
                'updated_at': now
            })
    except Exception as e:
        logger.error(f"Failed to save/update customer: {str(e)}")

# ---------- core flows ----------

def get_product_info(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Fetch product and price information from Stripe."""
    try:
        client_id, mode, offer_config, pk, sk, wh, full_offer_url = load_stripe_tenant_with_offer(event)
        stripe.api_key = sk
        
        data = _parse_body(event)
        product_id = data.get("product_id")
        if not product_id:
            return _err("Missing product_id")

        try:
            product = stripe.Product.retrieve(product_id)
        except stripe.error.InvalidRequestError:
            return _err(f"Product {product_id} not found")

        prices = stripe.Price.list(product=product_id, limit=100, active=True)
        
        return _ok({
            "product": {
                "id": product.id,
                "name": product.name,
                "description": product.description,
                "images": product.images,
                "metadata": product.metadata
            },
            "prices": [
                {
                    "id": p.id,
                    "currency": p.currency,
                    "unit_amount": p.unit_amount,
                    "recurring": p.recurring,
                    "metadata": p.metadata or {}
                }
                for p in prices.data
            ]
        })
        
    except Exception as e:
        logger.exception("Error in get_product_info")
        return _err(f"Failed to fetch product information: {str(e)}")

def get_offer_configuration(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    Get complete offer configuration by pulling data from Stripe products.
    This eliminates dual entry - everything comes from Stripe.
    """
    try:
        client_id, mode, offer_config, pk, sk, whook, full_offer_url = load_stripe_tenant_with_offer(event)
        stripe.api_key = sk
        
        data = _parse_body(event)
        offer_name = data.get("offer_name", "default")
        
        # Get product IDs for this offer
        product_ids = offer_config.get("product_ids", [])
        
        offer_data = {
            "offer_name": offer_name,
            "offer_url": full_offer_url,
            "client_id": client_id,
            "mode": mode,
            "publishable_key": pk,
            "products": [],
            "page_config": {}
        }
        
        for product_id in product_ids:
            try:
                # Get product with all pricing
                product = stripe.Product.retrieve(product_id)
                prices = stripe.Price.list(product=product_id, active=True, limit=100)
                
                # Extract rich metadata for page generation
                product_meta = product.metadata or {}
                
                product_info = {
                    "id": product.id,
                    "name": product.name,
                    "description": product.description,
                    "images": product.images,
                    "prices": [
                        {
                            "id": p.id,
                            "unit_amount": p.unit_amount,
                            "currency": p.currency,
                            "metadata": p.metadata or {}
                        }
                        for p in prices.data
                    ],
                    # Page generation metadata
                    "display_config": {
                        "hero_title": product_meta.get("hero_title", product.name),
                        "hero_subtitle": product_meta.get("hero_subtitle", product.description),
                        "benefits": product_meta.get("benefits", "").split("|") if product_meta.get("benefits") else [],
                        "testimonials": json.loads(product_meta.get("testimonials", "[]")),
                        "faq": json.loads(product_meta.get("faq", "[]")),
                        "guarantee": product_meta.get("guarantee", "30-day money-back guarantee"),
                        "shipping": product_meta.get("shipping", "Free shipping on all orders"),
                        "color_scheme": product_meta.get("color_scheme", "blue"),
                        "countdown_duration": int(product_meta.get("countdown_minutes", "5")),
                        "urgency_message": product_meta.get("urgency_message", "Limited time offer!")
                    },
                    # Upsell configuration
                    "upsell_config": {
                        "upsell_product_id": product_meta.get("upsell_product_id"),
                        "upsell_price_id": product_meta.get("upsell_price_id"), 
                        "upsell_offer_text": product_meta.get("upsell_offer_text", "Add another for just $27!"),
                        "upsell_savings_text": product_meta.get("upsell_savings_text", "Save $6!")
                    }
                }
                
                offer_data["products"].append(product_info)
                
            except Exception as e:
                logger.warning(f"Failed to load product {product_id}: {e}")
        
        return _ok(offer_data)
        
    except Exception as e:
        logger.exception("Error in get_offer_configuration")
        return _err(f"Failed to get offer configuration: {str(e)}")

def create_checkout_session(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Create Stripe checkout session with shipping and upsell logic."""
    try:
        data = _parse_body(event)
        
        # Extract offer name explicitly from the request
        offer_name = data.get("offer_name", "default")
        
        # If no offer_name in request, try to extract from other sources
        if offer_name == "default":
            _, extracted_offer = _extract_client_and_offer_id(event)
            if extracted_offer != "default":
                offer_name = extracted_offer
        
        logger.info(f"Using offer_name: {offer_name}")
        
        client_id, mode, offer_config, pk, sk, wh, full_offer_url = load_stripe_tenant_with_offer(event)
        stripe.api_key = sk
        
        logger.info(f"Loaded offer config: {offer_config}")
        logger.info(f"Full offer URL: {full_offer_url}")
        
        # Handle action-based request (new format)
        if data.get("action") == "create_checkout_session":
            price_id = data.get("price_id")
            product_id = data.get("product_id")
            quantity = int(data.get("quantity", 1))
            customer_id = data.get("customer_id")
        else:
            # Handle legacy direct format
            price_id = data.get("priceId") or data.get("price_id")
            product_id = data.get("product_id")
            quantity = int(data.get("quantity", 1))
            customer_id = data.get("customer_id")
            
        if not price_id:
            return _err("Missing price_id or priceId")

        # Get product info for upsell logic
        if product_id:
            product = stripe.Product.retrieve(product_id)
            product_type = product.metadata.get("product_type", "").lower()
        else:
            # Try to get product from price
            price_obj = stripe.Price.retrieve(price_id, expand=["product"])
            product = price_obj.product
            product_id = product.id
            product_type = product.metadata.get("product_type", "").lower()

        # Determine success URL based on product type - USE THE DYNAMIC BASE URL
        success_url = data.get("successUrl")
        cancel_url = data.get("cancelUrl")
        
        if not success_url:
            if product_type == "basic":
                success_url = f"{full_offer_url}upsell.html?session_id={{CHECKOUT_SESSION_ID}}"
            else:
                success_url = f"{full_offer_url}thank-you.html?session_id={{CHECKOUT_SESSION_ID}}"
        
        if not cancel_url:
            cancel_url = f"{full_offer_url}checkout.html"
            
        logger.info(f"Success URL: {success_url}")
        logger.info(f"Cancel URL: {cancel_url}")

        # Build session parameters
        session_data = {
            "mode": "payment",
            "payment_method_types": ["card"],
            "billing_address_collection": "required",
            "phone_number_collection": {"enabled": True},
            "line_items": [{"price": price_id, "quantity": quantity}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": {
                "client_id": client_id, 
                "mode": mode,
                "product_id": product_id, 
                "price_id": price_id,
                "offer_name": offer_name  # Use the correct offer name
            },
            "shipping_address_collection": {"allowed_countries": ["US", "CA"]},
            "shipping_options": [{
                "shipping_rate_data": {
                    "display_name": "Standard Shipping",
                    "type": "fixed_amount",
                    "fixed_amount": {"amount": 0, "currency": "usd"},
                    "delivery_estimate": {
                        "minimum": {"unit": "business_day", "value": 3},
                        "maximum": {"unit": "business_day", "value": 7}
                    }
                }
            }]
        }

        if customer_id:
            session_data["customer"] = customer_id
            session_data["payment_intent_data"] = {"setup_future_usage": "off_session"}
        else:
            session_data["customer_creation"] = "always"
            session_data["payment_intent_data"] = {"setup_future_usage": "off_session"}

        logger.info(f"Session data being sent: {json.dumps(session_data, default=str)}")
        session = stripe.checkout.Session.create(**session_data)

        return _ok({
            "id": session.id, 
            "mode": mode, 
            "publishableKey": pk,
            "url": session.url
        })
        
    except Exception as e:
        logger.exception("Error creating checkout session")
        return _err(f"Failed to create checkout session: {str(e)}")

def get_checkout_session_details(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Get checkout session details for upsell flow."""
    try:
        client_id, mode, offer_config, pk, sk, wh, full_offer_url = load_stripe_tenant_with_offer(event)
        stripe.api_key = sk

        data = _parse_body(event)
        session_id = data.get("session_id")
        if not session_id:
            return _err("Missing session_id")

        session = stripe.checkout.Session.retrieve(
            session_id,
            expand=["payment_intent", "customer", "line_items.data.price.product"]
        )

        original_payment_intent_id = session.payment_intent["id"] if isinstance(session.payment_intent, dict) else session.payment_intent
        customer_id = session.customer["id"] if isinstance(session.customer, dict) else session.customer

        product_id = (session.get("metadata") or {}).get("product_id")
        if not product_id:
            li = (session.get("line_items") or {}).get("data", [])
            if li:
                price = li[0].get("price", {})
                prod = price.get("product")
                product_id = prod.get("id") if isinstance(prod, dict) else prod

        cd = session.get("customer_details") or {}
        sd = session.get("shipping_details") or session.get("shipping") or {}
        customer_name = (sd.get("name") or cd.get("name") or "")
        customer_email = cd.get("email") or ""
        customer_phone = cd.get("phone") or ""

        shipping_address = {
            "line1": (sd.get("address", {}) or {}).get("line1", ""),
            "line2": (sd.get("address", {}) or {}).get("line2", ""),
            "city": (sd.get("address", {}) or {}).get("city", ""),
            "state": (sd.get("address", {}) or {}).get("state", ""),
            "postal_code": (sd.get("address", {}) or {}).get("postal_code", ""),
            "country": (sd.get("address", {}) or {}).get("country", "US"),
        }

        return _ok({
            "session_id": session.id,
            "original_payment_intent_id": original_payment_intent_id,
            "customer_id": customer_id,
            "product_id": product_id,
            "customer_name": customer_name,
            "customer_email": customer_email,
            "customer_phone": customer_phone,
            "shipping_address": shipping_address,
        })
    except Exception as e:
        logger.exception("Error in get_checkout_session_details")
        return _err(f"Failed to get checkout session details: {str(e)}")

def process_one_click_upsell(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Handle one-click upsell processing."""
    try:
        client_id, mode, offer_config, pk, sk, whook, full_offer_url = load_stripe_tenant_with_offer(event)
        stripe.api_key = sk

        data = _parse_body(event)
        original_pi_id = data.get('original_payment_intent_id')
        product_id = data.get('product_id')
        customer_id = data.get('customer_id')

        if not original_pi_id or not product_id or not customer_id:
            return _err("Missing required parameters: product_id, original_payment_intent_id, customer_id")

        original_pi = stripe.PaymentIntent.retrieve(
            original_pi_id,
            expand=["charges", "shipping", "customer"]
        )
        if not original_pi:
            return _err("Original payment intent not found")

        parent_product = stripe.Product.retrieve(product_id)
        upsell_product_id = parent_product.metadata.get("upsell_product_id")
        upsell_price_id = parent_product.metadata.get("upsell_price_id")
        if not upsell_product_id or not upsell_price_id:
            return _err(f"No upsell configured for product {product_id}")

        upsell_price = stripe.Price.retrieve(upsell_price_id)

        cust = stripe.Customer.retrieve(customer_id, expand=["invoice_settings.default_payment_method"])
        pm_to_use = None
        default_pm = cust.get("invoice_settings", {}).get("default_payment_method")
        if isinstance(default_pm, dict) and default_pm.get("id"):
            pm_to_use = default_pm["id"]
        elif isinstance(default_pm, str):
            pm_to_use = default_pm
        if not pm_to_use:
            pm_list = stripe.PaymentMethod.list(customer=customer_id, type="card")
            if pm_list.data:
                pm_to_use = pm_list.data[0].id
        if not pm_to_use:
            return _err("No saved payment method available for this customer", 409)

        charges = original_pi.get("charges", {}).get("data", [])
        orig_billing = (charges[0].get("billing_details") if charges else {}) or {}
        orig_bill_addr = (orig_billing.get("address") or {})

        orig_ship = original_pi.get("shipping") or {}
        orig_ship_addr = (orig_ship.get("address") or {})
        orig_ship_name = orig_ship.get("name") or ""
        orig_ship_phone = orig_ship.get("phone") or ""

        upsell_meta = {
            "client_id": client_id,
            "mode": mode,
            "product_id": upsell_product_id,
            "price_id": upsell_price_id,
            "upsell": "true",
            "original_payment_intent_id": original_pi_id,
            "customer_name": orig_ship_name or orig_billing.get("name") or "",
            "customer_email": orig_billing.get("email") or "",
            "customer_phone": orig_ship_phone or orig_billing.get("phone") or "",
            "offer_name": _extract_client_and_offer_id(event)[1]
        }

        upsell_pi = stripe.PaymentIntent.create(
            amount=upsell_price.unit_amount,
            currency=upsell_price.currency,
            customer=customer_id,
            payment_method=pm_to_use,
            confirm=True,
            off_session=True,
            shipping={
                "name": upsell_meta["customer_name"],
                "phone": upsell_meta["customer_phone"],
                "address": {
                    "line1": orig_ship_addr.get("line1", ""),
                    "line2": orig_ship_addr.get("line2", ""),
                    "city": orig_ship_addr.get("city", ""),
                    "state": orig_ship_addr.get("state", ""),
                    "postal_code": orig_ship_addr.get("postal_code", ""),
                    "country": orig_ship_addr.get("country", "US"),
                }
            },
            metadata=upsell_meta
        )

        logger.info(f"Created one-click upsell PI {upsell_pi.id} ({upsell_pi.status})")

        return _ok({
            'payment_intent_id': upsell_pi.id,
            'status': upsell_pi.status,
            'success': upsell_pi.status == 'succeeded',
            'requires_action': upsell_pi.status == 'requires_action',
            'client_secret': getattr(upsell_pi, "client_secret", None)
        })

    except Exception as e:
        logger.exception("Error in process_one_click_upsell")
        return _err(f"Failed to process one-click upsell: {str(e)}")

def get_upsell_config(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Get upsell configuration for a product."""
    try:
        client_id, mode, offer_config, pk, sk, wh, full_offer_url = load_stripe_tenant_with_offer(event)
        stripe.api_key = sk

        data = _parse_body(event)
        product_id = data.get("product_id")
        if not product_id:
            return _err("Missing product_id")

        product = stripe.Product.retrieve(product_id)
        upsell_product_id = product.metadata.get("upsell_product_id")
        upsell_price_id = product.metadata.get("upsell_price_id")
        upsell_offer_text = product.metadata.get("upsell_offer_text", "Yes! Add Another for $27")

        if not upsell_product_id or not upsell_price_id:
            return _err(f"No upsell configured for product {product_id}")

        upsell_price = stripe.Price.retrieve(upsell_price_id)

        return _ok({
            "upsell_product_id": upsell_product_id,
            "upsell_price_id": upsell_price_id,
            "upsell_offer_text": upsell_offer_text,
            "amount": upsell_price.unit_amount,
            "currency": upsell_price.currency
        })
    except Exception as e:
        logger.exception("Error in get_upsell_config")
        return _err(f"Failed to get upsell config: {str(e)}")

# ---------- Admin Functions for Offer Management ----------
def get_client_offers(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Get all offers for a client, with live Stripe data integration."""
    try:
        data = _parse_body(event)
        
        # Get client_id from multiple sources, prioritizing the explicit clientID from request
        client_id = None
        
        # First priority: explicit clientID in request body
        if data.get("clientID"):
            client_id = data["clientID"]
            logger.info(f"Got client_id from request body: {client_id}")
        
        # Second priority: try Cognito claims  
        if not client_id:
            try:
                client_id = _require_claim(event, 'sub')
                logger.info(f"Got client_id from Cognito claims: {client_id}")
            except RuntimeError:
                logger.warning("No Cognito claims available")
        
        # Third priority: try extraction methods
        if not client_id:
            client_id, _ = _extract_client_and_offer_id(event)
            logger.info(f"Got client_id from extraction: {client_id}")
        
        if not client_id or client_id == "default-client":
            logger.error("No valid client_id found")
            return _err("Unable to determine client ID")
        
        logger.info(f"Loading offers for client: {client_id}")
        
        # Get client configuration
        resp = stripe_keys_table.get_item(Key={"clientID": client_id})
        item = resp.get("Item")
        if not item:
            logger.warning(f"Client not found: {client_id}")
            return _err("Client not found")
        
        # Set up Stripe
        mode = item.get("mode", "test")
        sk_enc = item["sk_live"] if mode == "live" else item["sk_test"]
        sk = _kms_decrypt_wrapped(sk_enc)
        
        if not sk:
            return _err(f"Failed to decrypt Stripe secret key for mode: {mode}")
            
        stripe.api_key = sk
        
        offers = item.get("offers", {})
        base_url = item.get("base_frontend_url", "")
        
        logger.info(f"Found {len(offers)} offers for client {client_id}")
        
        # Enhance offers with live Stripe data
        enhanced_offers = {}
        for offer_name, offer_config in offers.items():
            try:
                product_ids = offer_config.get("product_ids", [])
                products = []
                
                for product_id in product_ids:
                    try:
                        # Get product and pricing from Stripe
                        product = stripe.Product.retrieve(product_id)
                        prices = stripe.Price.list(product=product_id, active=True)
                        
                        products.append({
                            "id": product.id,
                            "name": product.name,
                            "active": product.active,
                            "price_count": len(prices.data),
                            "images": product.images,
                            "metadata": product.metadata
                        })
                    except Exception as e:
                        logger.warning(f"Failed to load product {product_id}: {e}")
                        products.append({
                            "id": product_id,
                            "name": "Unknown Product",
                            "active": False,
                            "error": str(e)
                        })
                
                enhanced_offers[offer_name] = {
                    **offer_config,
                    "full_url": f"{base_url}{offer_config.get('path', offer_name)}/",
                    "products": products,
                    "product_count": len(products)
                }
                
            except Exception as e:
                logger.warning(f"Failed to process offer {offer_name}: {e}")
                enhanced_offers[offer_name] = {
                    **offer_config,
                    "full_url": f"{base_url}{offer_config.get('path', offer_name)}/",
                    "products": [],
                    "product_count": 0,
                    "error": str(e)
                }
        
        return _ok({
            "client_id": client_id,
            "base_url": base_url,
            "offers": enhanced_offers
        })
        
    except Exception as e:
        logger.exception("Error getting client offers")
        return _err(f"Failed to get offers: {str(e)}")


def update_offer(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Update or create an offer for a client."""
    try:
        data = _parse_body(event)
        
        # Get client_id from multiple sources, prioritizing the explicit clientID from request
        client_id = None
        
        # First priority: explicit clientID in request body
        if data.get("clientID"):
            client_id = data["clientID"]
            logger.info(f"Got client_id from request body: {client_id}")
        
        # Second priority: try Cognito claims  
        if not client_id:
            try:
                client_id = _require_claim(event, 'sub')
                logger.info(f"Got client_id from Cognito claims: {client_id}")
            except RuntimeError:
                logger.warning("No Cognito claims available")
        
        # Third priority: try extraction methods
        if not client_id:
            client_id, _ = _extract_client_and_offer_id(event)
            logger.info(f"Got client_id from extraction: {client_id}")
        
        if not client_id or client_id == "default-client":
            logger.error("No valid client_id found")
            return _err("Unable to determine client ID - please ensure clientID is included in request")
        
        logger.info(f"Using client_id: {client_id}")
        
        offer_name = data.get("offer_name")
        if not offer_name:
            return _err("offer_name is required")
        
        logger.info(f"Updating offer '{offer_name}' for client '{client_id}'")
        
        # Get current configuration
        resp = stripe_keys_table.get_item(Key={"clientID": client_id})
        item = resp.get("Item", {})
        logger.info(f"Found existing item: {bool(item)}")
        
        if not item:
            logger.warning(f"No existing record found for clientID: {client_id}")
            # For offers, we should not create a new client record - client must exist first
            return _err(f"Client record not found for clientID: {client_id}. Please set up Stripe keys first.")
        
        current_offers = item.get("offers", {})
        logger.info(f"Current offers count: {len(current_offers)}")
        
        # Check for path changes (for S3 folder management)
        old_path = None
        if offer_name in current_offers:
            old_path = current_offers[offer_name].get("path")
        
        new_path = data.get("path", offer_name)
        path_changed = old_path and old_path != new_path
        
        # Update offer configuration
        offer_config = {
            "path": new_path,
            "product_ids": data.get("product_ids", []),
            "active": data.get("active", True),
            "updated_at": _iso_now()
        }
        
        current_offers[offer_name] = offer_config
        logger.info(f"Updated offers structure: {list(current_offers.keys())}")
        
        # Prepare update expression and values
        update_expression = "SET offers = :offers, updated_at = :updated_at"
        expression_values = {
            ":offers": current_offers,
            ":updated_at": _iso_now()
        }
        
        # Check if base_frontend_url is provided and needs to be updated
        base_frontend_url = data.get("base_frontend_url")
        if base_frontend_url is not None:  # Allow empty string to clear the field
            update_expression += ", base_frontend_url = :base_url"
            expression_values[":base_url"] = base_frontend_url
            logger.info(f"Updating base_frontend_url to: {base_frontend_url}")
        
        # Update database
        update_result = stripe_keys_table.update_item(
            Key={"clientID": client_id},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_values,
            ReturnValues="ALL_NEW"
        )
        
        logger.info(f"DynamoDB update successful for client: {client_id}")
        logger.info(f"Update result: {json.dumps(update_result.get('Attributes', {}), default=str)}")
        
        response_data = {"success": True, "offer": offer_config, "client_id": client_id}
        
        # Include base URL in response if it was updated
        if base_frontend_url is not None:
            response_data["base_frontend_url_updated"] = base_frontend_url
            logger.info(f"Including base_frontend_url in response: {base_frontend_url}")
        
        # Also include what was actually saved to database
        final_base_url = update_result.get("Attributes", {}).get("base_frontend_url")
        if final_base_url:
            response_data["base_frontend_url_saved"] = final_base_url
        
        # If path changed, trigger S3 folder rename (async)
        if path_changed:
            try:
                sqs = boto3.client('sqs')
                queue_url = os.environ.get('S3_RENAME_QUEUE_URL')
                if queue_url:
                    sqs.send_message(
                        QueueUrl=queue_url,
                        MessageBody=json.dumps({
                            "client_id": client_id,
                            "offer_name": offer_name,
                            "old_path": old_path,
                            "new_path": new_path
                        })
                    )
                    response_data["s3_rename_queued"] = True
                else:
                    response_data["s3_rename_warning"] = "S3_RENAME_QUEUE_URL not configured"
            except Exception as e:
                logger.warning(f"Failed to queue S3 rename: {e}")
                response_data["s3_rename_error"] = str(e)
        
        return _ok(response_data)
        
    except Exception as e:
        logger.exception("Error updating offer")
        return _err(f"Failed to update offer: {str(e)}")
    

def get_available_products(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Get all available Stripe products for a client to choose from."""
    try:
        client_id, mode, offer_config, pk, sk, wh, full_offer_url = load_stripe_tenant_with_offer(event)
        stripe.api_key = sk
        
        # Get all active products
        products = stripe.Product.list(active=True, limit=100)
        
        product_list = []
        for product in products.data:
            # Get pricing info
            prices = stripe.Price.list(product=product.id, active=True)
            
            # Extract key metadata for admin display
            meta = product.metadata or {}
            
            product_list.append({
                "id": product.id,
                "name": product.name,
                "description": product.description,
                "active": product.active,
                "images": product.images[:2],  # Just first 2 images for admin
                "price_count": len(prices.data),
                "lowest_price": min(p.unit_amount for p in prices.data) if prices.data else 0,
                "product_type": meta.get("product_type", "unknown"),
                "has_upsell": bool(meta.get("upsell_product_id")),
                "created": product.created
            })
        
        # Sort by creation date, newest first
        product_list.sort(key=lambda x: x["created"], reverse=True)
        
        return _ok({"products": product_list})
        
    except Exception as e:
        logger.exception("Error getting available products")
        return _err(f"Failed to get products: {str(e)}")

# ---------- Shipping Management ----------

def get_shipping_config(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Get shipping configuration for a client."""
    try:
        qs = event.get("queryStringParameters") or {}
        client_id = qs.get("clientID")
        
        if not client_id:
            try:
                client_id = _require_claim(event, 'sub')
            except RuntimeError:
                return _err("Unable to determine client ID")
        
        resp = stripe_keys_table.get_item(Key={"clientID": client_id})
        item = resp.get("Item")
        
        if not item:
            return _err("Client not found")
        
        # Extract shipping config with decryption hints
        shipping_config = item.get("shipping_config", {})

        # Convert Decimal to float for JSON serialization
        if shipping_config and shipping_config.get("default_parcel"):
            parcel = shipping_config["default_parcel"]
            shipping_config["default_parcel"] = {
                "length": float(parcel.get("length", 10)),
                "width": float(parcel.get("width", 8)),
                "height": float(parcel.get("height", 4)),
                "weight": float(parcel.get("weight", 1))
            }

        if shipping_config and shipping_config.get("api_key"):
            # Return masked version for security
            api_key = shipping_config["api_key"]
            if api_key.startswith("ENCRYPTED("):
                shipping_config["api_key"] = {"masked": "****", "encrypted": True}
            else:
                shipping_config["api_key"] = {"masked": api_key[-4:] if len(api_key) > 4 else "****"}
        
        if shipping_config and shipping_config.get("api_secret"):
            api_secret = shipping_config["api_secret"]
            if api_secret.startswith("ENCRYPTED("):
                shipping_config["api_secret"] = {"masked": "****", "encrypted": True}
            else:
                shipping_config["api_secret"] = {"masked": "****"}
        
        return _ok({
            "shipping_provider": item.get("shipping_provider"),
            "shipping_config": shipping_config
        })
        
    except Exception as e:
        logger.exception("Error getting shipping config")
        return _err(f"Failed to get shipping config: {str(e)}")
    

def save_shipping_config(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Save shipping configuration for a client."""
    try:
        data = _parse_body(event)
        client_id = data.get("clientID")
        
        if not client_id:
            try:
                client_id = _require_claim(event, 'sub')
            except RuntimeError:
                return _err("Unable to determine client ID")
        
        # Verify client exists
        resp = stripe_keys_table.get_item(Key={"clientID": client_id})
        if not resp.get("Item"):
            return _err("Client not found. Please set up Stripe keys first.")
        
        provider = data.get("shipping_provider")
        shipping_config = data.get("shipping_config")
        
        # Convert float values to Decimal for DynamoDB
        if shipping_config and shipping_config.get("default_parcel"):
            parcel = shipping_config["default_parcel"]
            shipping_config["default_parcel"] = {
                "length": Decimal(str(parcel.get("length", 10))),
                "width": Decimal(str(parcel.get("width", 8))),
                "height": Decimal(str(parcel.get("height", 4))),
                "weight": Decimal(str(parcel.get("weight", 1)))
            }
        
        # Encrypt API keys using KMS
        if shipping_config and shipping_config.get("api_key"):
            api_key = shipping_config["api_key"]
            # Only encrypt if it's not already encrypted or masked
            if not api_key.startswith("ENCRYPTED(") and api_key != "****":
                try:
                    encrypted = _kms_encrypt_wrapped(api_key)
                    shipping_config["api_key"] = encrypted
                except Exception as e:
                    logger.error(f"KMS encryption failed: {e}")
                    return _err("Failed to encrypt API key")
        
        # Update database
        stripe_keys_table.update_item(
            Key={"clientID": client_id},
            UpdateExpression="SET shipping_provider = :provider, shipping_config = :config, updated_at = :updated",
            ExpressionAttributeValues={
                ":provider": provider,
                ":config": shipping_config,
                ":updated": _iso_now()
            }
        )
        
        return _ok({"success": True})
        
    except Exception as e:
        logger.exception("Error saving shipping config")
        return _err(f"Failed to save shipping config: {str(e)}")
    


def test_shipping_connection(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Test shipping provider connection."""
    try:
        if not get_shipping_provider:
            return _err("Shipping module not available")
            
        data = _parse_body(event)
        client_id = data.get("clientID")
        
        if not client_id:
            return _err("clientID required")
        
        # Load client config
        resp = stripe_keys_table.get_item(Key={"clientID": client_id})
        item = resp.get("Item")
        
        if not item:
            return _err("Client not found")
        
        provider_name = item.get("shipping_provider")
        if not provider_name:
            return _err("No shipping provider configured")
        
        shipping_config = item.get("shipping_config", {})
        
        # Decrypt API keys
        api_key = _kms_decrypt_wrapped(shipping_config.get("api_key", ""))
        api_secret = _kms_decrypt_wrapped(shipping_config.get("api_secret", ""))
        
        config = {
            "api_key": api_key,
            "api_secret": api_secret,
            "test_mode": shipping_config.get("test_mode", True)
        }
        
        # Initialize provider
        provider = get_shipping_provider(provider_name, config)
        if not provider:
            return _err(f"Failed to initialize {provider_name}")
        
        # Test with a simple rate request
        test_from = shipping_config.get("default_from_address", {
            "street1": "123 Main St",
            "city": "San Francisco",
            "state": "CA",
            "zip": "94105",
            "country": "US"
        })
        
        test_to = {
            "street1": "1600 Pennsylvania Ave",
            "city": "Washington",
            "state": "DC",
            "zip": "20500",
            "country": "US"
        }
        
        test_parcel = shipping_config.get("default_parcel", {
            "length": 10,
            "width": 8,
            "height": 4,
            "weight": 1
        })
        
        rates = provider.get_rates(test_from, test_to, test_parcel)
        
        if rates:
            return _ok({
                "success": True,
                "provider": provider_name,
                "test_result": f"Found {len(rates)} rate(s)",
                "sample_rate": rates[0] if rates else None
            })
        else:
            return _ok({
                "success": False,
                "provider": provider_name,
                "error": "No rates returned - check configuration"
            })
        
    except Exception as e:
        logger.exception("Error testing shipping")
        return _err(f"Test failed: {str(e)}")
    

def get_shipping_rates(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Get available shipping rates for an order (robust to string/obj shipping_address)."""
    try:
        if not get_shipping_provider:
            return _err("Shipping module not available")

        data = _parse_body(event)
        order_id = data.get("order_id")
        if not order_id:
            return _err("order_id required")

        # 1) Fetch order
        resp = orders_table.get_item(Key={"order_id": order_id})
        order = resp.get("Item")
        if not order:
            return _err("Order not found")

        client_id = order.get("client_id")
        if not client_id:
            return _err("Order missing client_id")

        # 2) Load tenant shipping config
        resp = stripe_keys_table.get_item(Key={"clientID": client_id})
        tenant_cfg = resp.get("Item")
        if not tenant_cfg:
            return _err("Client config not found")

        provider_name = (tenant_cfg.get("shipping_provider") or "").strip()
        if not provider_name:
            return _err("No shipping provider configured")

        shipping_config = tenant_cfg.get("shipping_config", {}) or {}

        # 3) Build inputs BEFORE initializing provider

        # from_address straight from config (already structured)
        from_address = shipping_config.get("default_from_address", {}) or {}

        # --- to_address: handle string OR object
        raw_addr = order.get("shipping_address", {})
        if isinstance(raw_addr, dict):
            to_address = {
                "name": (order.get("customer_name") or "").strip(),
                "street1": raw_addr.get("line1", "") or raw_addr.get("street1", ""),
                "street2": raw_addr.get("line2", "") or raw_addr.get("street2", ""),
                "city": raw_addr.get("city", ""),
                "state": raw_addr.get("state", ""),
                "zip": raw_addr.get("postal_code", "") or raw_addr.get("zip", ""),
                "country": raw_addr.get("country", "US"),
                "phone": raw_addr.get("phone", ""),
            }
        else:
            # Parse a string like "123 Main St, City, ST, 90210[, Country]"
            # Very forgiving split with cleanup
            parts = [p.strip() for p in str(raw_addr or "").split(",") if p.strip()]
            street1 = parts[0] if len(parts) >= 1 else ""
            city = parts[1] if len(parts) >= 2 else ""
            state = ""
            zip_code = ""
            country = "US"

            if len(parts) >= 3:
                # Try to split "ST 90210" or "ST, 90210"
                state_zip = parts[2].replace(",", " ").split()
                if len(state_zip) >= 1:
                    state = state_zip[0]
                if len(state_zip) >= 2:
                    zip_code = state_zip[1]

            if len(parts) >= 4:
                # If a country appears as the 4th token, use it
                country = parts[3] or "US"

            to_address = {
                "name": (order.get("customer_name") or "").strip(),
                "street1": street1,
                "street2": "",
                "city": city,
                "state": state,
                "zip": zip_code,
                "country": country or "US",
                "phone": (order.get("customer_phone") or "").strip(),
            }

        # Basic validation
        missing = [k for k in ("street1", "city", "state", "zip") if not (to_address.get(k) or "").strip()]
        if missing:
            return _err(f"Shipping address incomplete: missing {', '.join(missing)}")

        # Parcel (ensure Decimals -> float)
        p = shipping_config.get("default_parcel", {}) or {}
        parcel = {
            "length": float(p.get("length", 10)),
            "width":  float(p.get("width", 8)),
            "height": float(p.get("height", 4)),
            "weight": float(p.get("weight", 1)),
        }

        # 4) Initialize provider AFTER inputs are ready
        api_key = _kms_decrypt_wrapped(shipping_config.get("api_key", "") or "")
        api_secret = _kms_decrypt_wrapped(shipping_config.get("api_secret", "") or "")
        config = {
            "api_key": api_key,
            "api_secret": api_secret,
            "test_mode": bool(shipping_config.get("test_mode", True)),
        }

        provider = get_shipping_provider(provider_name, config)
        if not provider:
            return _err(f"Failed to initialize {provider_name}")

        # 5) Fetch rates
        if provider_name.lower() == "shippo":
            result = provider.get_rates_with_shipment(from_address, to_address, parcel)
            return _ok({
                "order_id": order_id,
                "shipment_id": result.get("shipment_id"),
                "rates": result.get("rates", []),
            })
        else:
            rates = provider.get_rates(from_address, to_address, parcel)
            return _ok({
                "order_id": order_id,
                "rates": rates,
            })

    except Exception as e:
        logger.exception("Error getting shipping rates")
        return _err(f"Failed to get rates: {str(e)}")


def create_shipping_label(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Create a shipping label for an order."""
    try:
        if not get_shipping_provider:
            return _err("Shipping module not available")
            
        data = _parse_body(event)
        order_id = data.get("order_id")
        rate_id = data.get("rate_id")
        
        if not order_id:
            return _err("order_id required")
        
        # Get order details
        resp = orders_table.get_item(Key={"order_id": order_id})
        order = resp.get("Item")
        
        if not order:
            return _err("Order not found")
        
        client_id = order.get("client_id")
        
        # Load shipping config
        resp = stripe_keys_table.get_item(Key={"clientID": client_id})
        item = resp.get("Item")
        
        if not item:
            return _err("Client config not found")
        
        provider_name = item.get("shipping_provider")
        if not provider_name:
            return _err("No shipping provider configured")
        
        shipping_config = item.get("shipping_config", {})
        
        # Get default carrier/service preferences
        default_carrier = shipping_config.get("default_carrier", "USPS")
        default_service = shipping_config.get("default_service", "Priority")
        
        # Decrypt credentials
        api_key = _kms_decrypt_wrapped(shipping_config.get("api_key", ""))
        api_secret = _kms_decrypt_wrapped(shipping_config.get("api_secret", ""))
        
        config = {
            "api_key": api_key,
            "api_secret": api_secret,
            "test_mode": shipping_config.get("test_mode", True)
        }
        
        # Initialize provider
        provider = get_shipping_provider(provider_name, config)
        if not provider:
            return _err(f"Failed to initialize {provider_name}")
        
        # If rate_id provided, purchase that specific rate
        if rate_id:
            result = provider.purchase_rate(rate_id)
        else:
            # Build shipment data with address parsing
            from_address = shipping_config.get("default_from_address", {})
            
            # --- Parse shipping address (handle string OR dict)
            raw_addr = order.get("shipping_address", {})
            if isinstance(raw_addr, dict):
                to_address = {
                    "name": (order.get("customer_name") or "").strip(),
                    "street1": raw_addr.get("line1", "") or raw_addr.get("street1", ""),
                    "street2": raw_addr.get("line2", "") or raw_addr.get("street2", ""),
                    "city": raw_addr.get("city", ""),
                    "state": raw_addr.get("state", ""),
                    "zip": raw_addr.get("postal_code", "") or raw_addr.get("zip", ""),
                    "country": raw_addr.get("country", "US"),
                    "phone": raw_addr.get("phone", "") or order.get("customer_phone", ""),
                    "email": order.get("customer_email", "")
                }
            else:
                # Parse string address
                parts = [p.strip() for p in str(raw_addr or "").split(",") if p.strip()]
                street1 = parts[0] if len(parts) >= 1 else ""
                city = parts[1] if len(parts) >= 2 else ""
                state = ""
                zip_code = ""
                country = "US"

                if len(parts) >= 3:
                    state_zip = parts[2].replace(",", " ").split()
                    if len(state_zip) >= 1:
                        state = state_zip[0]
                    if len(state_zip) >= 2:
                        zip_code = state_zip[1]

                if len(parts) >= 4:
                    country = parts[3] or "US"

                to_address = {
                    "name": (order.get("customer_name") or "").strip(),
                    "street1": street1,
                    "street2": "",
                    "city": city,
                    "state": state,
                    "zip": zip_code,
                    "country": country or "US",
                    "phone": (order.get("customer_phone") or "").strip(),
                    "email": order.get("customer_email", "")
                }
            
            # Validate required fields
            missing = [k for k in ("street1", "city", "state", "zip") if not (to_address.get(k) or "").strip()]
            if missing:
                return _err(f"Shipping address incomplete: missing {', '.join(missing)}")
            
            # Convert Decimal to float for parcel
            p = shipping_config.get("default_parcel", {}) or {}
            parcel = {
                "length": float(p.get("length", 10)),
                "width": float(p.get("width", 8)),
                "height": float(p.get("height", 4)),
                "weight": float(p.get("weight", 1))
            }
            
            order_data = {
                "order_id": order_id,
                "from_address": from_address,
                "to_address": to_address,
                "parcel": parcel,
                "product_id": order.get("product_id"),
                "preferred_carrier": default_carrier,
                "preferred_service": default_service
            }
            
            result = provider.create_shipment(order_data)
        
        if result.get("success"):
            # Update order with tracking info
            orders_table.update_item(
                Key={"order_id": order_id},
                UpdateExpression="SET tracking_number = :tracking, tracking_url = :url, label_url = :label, shipping_carrier = :carrier, fulfilled = :fulfilled, updated_at = :updated",
                ExpressionAttributeValues={
                    ":tracking": result.get("tracking_number", ""),
                    ":url": result.get("tracking_url", ""),
                    ":label": result.get("label_url", ""),
                    ":carrier": result.get("carrier", ""),
                    ":fulfilled": "true",
                    ":updated": _iso_now()
                }
            )
            
            return _ok(result)
        else:
            return _err(result.get("error", "Failed to create label"))
        
    except Exception as e:
        logger.exception("Error creating shipping label")
        return _err(f"Failed to create shipping label: {str(e)}")

# ---------- Webhook Handling ----------

def handle_webhook(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Verifies webhook signature and processes payment events."""
    if not orders_table:
        return _err("Orders table not configured", 500)

    try:
        # Load tenant & secret BEFORE verifying signature
        client_id, mode, offer_config, pk, sk, wh, full_offer_url = load_stripe_tenant_with_offer(event)

        payload = event.get("body", "")
        if event.get("isBase64Encoded"):
            payload = base64.b64decode(payload)

        sig = (event.get("headers") or {}).get("Stripe-Signature")
        if not sig:
            return _err("Missing Stripe-Signature", 400)

        try:
            stripe_event = stripe.Webhook.construct_event(payload, sig, wh)
        except stripe.error.SignatureVerificationError as e:
            logger.warning(f"Webhook signature verification failed: {e}")
            return _err("Bad signature", 400)

        stripe.api_key = sk  # Set for subsequent Stripe calls

        event_type = stripe_event.get("type")
        obj = stripe_event.get("data", {}).get("object", {})

        logger.info(f"Webhook received: {event_type}")

        if event_type == 'checkout.session.completed':
            session_id = obj['id']
            # Re-fetch with expansions
            session = stripe.checkout.Session.retrieve(
                session_id,
                expand=["customer_details", "shipping", "payment_intent", "line_items.data.price.product"]
            )
            pi_id = session.get('payment_intent')
            if pi_id:
                pi = stripe.PaymentIntent.retrieve(
                    pi_id,
                    expand=["charges.data.billing_details", "charges.data.shipping", "customer", "payment_method"]
                )
                _process_payment_intent(pi, client_id, mode)

        elif event_type == 'payment_intent.succeeded':
            _process_payment_intent(obj, client_id, mode)

        return _ok({"received": True})

    except Exception as e:
        logger.exception("Webhook error")
        return _err(f"Webhook error: {str(e)}", 400)

def _process_payment_intent(payment_intent, client_id: str, mode: str):
    """Process payment intent and save order."""
    try:
        logger.info(f"Processing payment intent: {payment_intent['id']}")
        
        charges = payment_intent.get("charges", {}).get("data", [])
        metadata = payment_intent.get("metadata", {}) or {}
        customer_details = payment_intent.get("customer_details", {}) or {}

        # Extract customer info
        billing = (charges[0].get("billing_details") if charges else {}) or {}
        customer_email = billing.get("email") or metadata.get("customer_email") or ""
        
        charge_ship = (charges[0].get("shipping") if charges else {}) or {}
        ship_name = charge_ship.get("name") or (payment_intent.get("shipping") or {}).get("name") or ""
        customer_name = ship_name or metadata.get("customer_name") or billing.get("name") or ""
        
        # ---------- PHONE ----------
        # 1) metadata
        customer_phone = metadata.get("customer_phone")
        source = "metadata"

        # 2) checkout customer_details
        if not customer_phone:
            customer_phone = customer_details.get("phone")
            if customer_phone:
                source = "checkout_customer_details"

        # 3) charge.shipping.phone
        if not customer_phone and charges:
            ch_ship = (charges[0].get("shipping") or {})
            if ch_ship.get("phone"):
                customer_phone = ch_ship.get("phone")
                source = "charge_shipping"

        # 4) charge.billing_details.phone
        if not customer_phone and charges:
            ch_bill = (charges[0].get("billing_details") or {})
            if ch_bill.get("phone"):
                customer_phone = ch_bill.get("phone")
                source = "charge_billing"

        # 4.5) Customer object phone (Stripe may save Checkout phone there)
        if not customer_phone:
            cust_id = payment_intent.get("customer")
            if cust_id:
                try:
                    cust_obj = stripe.Customer.retrieve(cust_id)
                    if cust_obj and cust_obj.get("phone"):
                        customer_phone = cust_obj["phone"]
                        source = "customer_object"
                except Exception as e:
                    logger.warning(f"Customer retrieve failed for {cust_id}: {str(e)}")

        # 5) customers table by email (last resort)
        if not customer_phone and customer_email and customers_table:
            try:
                resp = customers_table.get_item(Key={'email': customer_email.lower()})
                itm = resp.get('Item')
                if itm and itm.get('phone'):
                    customer_phone = itm['phone']
                    source = "customers_table"
            except Exception as e:
                logger.warning(f"Customers lookup failed: {str(e)}")

        logger.info(f"[PROCESS] Phone resolved from {source}, value={customer_phone}")

        # Addresses
        ship_addr = (charge_ship.get("address") or (payment_intent.get("shipping") or {}).get("address") or {})
        shipping_address = {
            'line1': ship_addr.get('line1', ''),
            'line2': ship_addr.get('line2', ''),
            'city': ship_addr.get('city', ''),
            'state': ship_addr.get('state', ''),
            'postal_code': ship_addr.get('postal_code', ''),
            'country': ship_addr.get('country', 'US')
        }

        bill_addr = billing.get("address") or {}
        billing_address = {
            'line1': bill_addr.get('line1', ''),
            'line2': bill_addr.get('line2', ''),
            'city': bill_addr.get('city', ''),
            'state': bill_addr.get('state', ''),
            'postal_code': bill_addr.get('postal_code', ''),
            'country': bill_addr.get('country', 'US'),
        }

        # Product info
        price_id = metadata.get("price_id", "")
        product_id = metadata.get("product_id", "")
        offer_name = metadata.get("offer_name", "default")
        product_name = "Product"  # Default
        
        try:
            if price_id:
                price_obj = stripe.Price.retrieve(price_id, expand=["product"])
                product_name = price_obj.get("nickname") or (
                    price_obj.get("product", {}).get("name") if isinstance(price_obj.get("product"), dict) else None
                ) or product_name
        except Exception as e:
            logger.warning(f"Failed to retrieve product info: {e}")

        # Save customer
        customer_info = {
            'name': customer_name,
            'email': customer_email,
            'phone': customer_phone,
            'billing_address': billing_address,
            'shipping_address': shipping_address
        }

        if customer_email and customers_table:
            save_or_update_customer(customer_info)

        # Save order
        order_record = {
            'order_id': payment_intent['id'],
            'order_date': _iso_now(),
            'client_id': client_id,
            'mode': mode,
            'offer_name': offer_name,
            'payment_status': 'succeeded',
            'amount': payment_intent.get('amount', 0),
            'currency': payment_intent.get('currency', 'usd'),
            'customer_email': customer_email or 'N/A',
            'customer_name': customer_name or 'N/A',
            'customer_phone': customer_phone or 'N/A',
            'product_name': product_name,
            'product_id': product_id or 'N/A',
            'price_id': price_id or 'N/A',
            'fulfilled': 'false',
            'created_at': _iso_now(),
            'shipping_address': shipping_address,
            'billing_address': billing_address,
            'stripe_customer_id': payment_intent.get('customer', ''),
            'metadata': metadata,
            'updated_at': _iso_now()
        }
        
        orders_table.put_item(Item=order_record)
        logger.info(f"Saved order {payment_intent['id']} to DynamoDB")
        
        # Auto-create shipping label if enabled
        if get_shipping_provider:
            try:
                resp = stripe_keys_table.get_item(Key={"clientID": client_id})
                item = resp.get("Item")
                if item:
                    shipping_config = item.get("shipping_config", {})
                    if shipping_config.get("auto_fulfill") and item.get("shipping_provider"):
                        logger.info(f"Auto-fulfillment enabled, creating label for {payment_intent['id']}")
                        
                        # Decrypt credentials
                        api_key = _kms_decrypt_wrapped(shipping_config.get("api_key", ""))
                        api_secret = _kms_decrypt_wrapped(shipping_config.get("api_secret", ""))
                        
                        config = {
                            "api_key": api_key,
                            "api_secret": api_secret,
                            "test_mode": shipping_config.get("test_mode", True)
                        }
                        
                        provider_name = item.get("shipping_provider")
                        provider = get_shipping_provider(provider_name, config)
                        
                        if provider:
                            from_address = shipping_config.get("default_from_address", {})
                            to_address = {
                                "name": customer_name,
                                "street1": shipping_address.get("line1", ""),
                                "street2": shipping_address.get("line2", ""),
                                "city": shipping_address.get("city", ""),
                                "state": shipping_address.get("state", ""),
                                "zip": shipping_address.get("postal_code", ""),
                                "country": shipping_address.get("country", "US"),
                                "phone": customer_phone,
                                "email": customer_email
                            }
                            
                            parcel = shipping_config.get("default_parcel", {
                                "length": 10,
                                "width": 8,
                                "height": 4,
                                "weight": 1
                            })
                            
                            order_data = {
                                "order_id": payment_intent['id'],
                                "from_address": from_address,
                                "to_address": to_address,
                                "parcel": parcel,
                                "product_id": product_id
                            }
                            
                            result = provider.create_shipment(order_data)
                            
                            if result.get("success"):
                                # Update order with tracking
                                orders_table.update_item(
                                    Key={"order_id": payment_intent['id']},
                                    UpdateExpression="SET tracking_number = :tracking, tracking_url = :url, label_url = :label, shipping_carrier = :carrier, fulfilled = :fulfilled",
                                    ExpressionAttributeValues={
                                        ":tracking": result.get("tracking_number", ""),
                                        ":url": result.get("tracking_url", ""),
                                        ":label": result.get("label_url", ""),
                                        ":carrier": result.get("carrier", ""),
                                        ":fulfilled": "true"
                                    }
                                )
                                logger.info(f"Auto-created label for {payment_intent['id']}: {result.get('tracking_number')}")
                            else:
                                logger.warning(f"Auto-fulfillment failed for {payment_intent['id']}: {result.get('error')}")
                        else:
                            logger.warning(f"Could not initialize shipping provider: {provider_name}")
            except Exception as e:
                logger.error(f"Auto-fulfillment error: {str(e)}")
                # Don't fail the webhook if shipping fails

    except Exception as e:
        logger.error(f"Failed to process payment intent: {str(e)}")

# ---------- Order Management ----------

def get_orders(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Get orders list."""
    try:
        scan_kwargs = {"FilterExpression": Attr('fulfilled').eq('false')}
        items = orders_table.scan(**scan_kwargs).get('Items', [])
        
        def fmt_addr(addr):
            if isinstance(addr, dict):
                parts = [addr.get('line1'), addr.get('line2'),
                        addr.get('city'), addr.get('state'), addr.get('postal_code')]
                parts = [p for p in parts if p]
                return ', '.join(parts) if parts else 'N/A'
            return addr if addr else 'N/A'

        orders = []
        for item in sorted(items, key=lambda x: x.get('created_at', ''), reverse=True):
            orders.append({
                "order_id": item.get("order_id"),
                "order_date": format_date(item.get("created_at")),
                "client_id": item.get("client_id", "N/A"),
                "offer_name": item.get("offer_name", "N/A"),
                "customer_name": item.get("customer_name") or "N/A",
                "customer_email": item.get("customer_email") or "N/A", 
                "customer_phone": item.get("customer_phone") or "N/A",
                "product_name": item.get("product_name") or "Product",
                "amount": item.get("amount") or 0,
                "currency": item.get("currency") or "usd",
                "shipping_address": fmt_addr(item.get("shipping_address")),
                "billing_address": fmt_addr(item.get("billing_address")),
                "fulfilled": item.get("fulfilled", "false"),
                "payment_status": item.get("payment_status", ""),
                "tracking_number": item.get("tracking_number", "N/A"),
                "tracking_url": item.get("tracking_url", "")
            })
        
        return _ok({"orders": orders, "nextKey": None, "hasMore": False})
    except Exception as e:
        logger.exception("Error in get_orders")
        return _err(f"Failed to get orders: {str(e)}")

def get_single_order(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Get single order by ID."""
    try:
        path = event.get('path', '')
        order_id = path.split('/')[-1]
        
        resp = orders_table.get_item(Key={'order_id': order_id})
        item = resp.get('Item')
        if not item:
            return _err('Order not found', 404)
        
        return _ok(item)
    except Exception as e:
        logger.exception("Error in get_single_order")
        return _err(f"Failed to get order: {str(e)}")

def update_order(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Update order fulfillment status."""
    try:
        path = event.get('path', '')
        order_id = path.split('/')[-1]
        
        data = _parse_body(event)
        fulfilled = data.get('fulfilled')
        if fulfilled is None:
            return _err('fulfilled field is required')
        
        orders_table.update_item(
            Key={'order_id': order_id},
            UpdateExpression='SET fulfilled = :fulfilled, updated_at = :updated_at',
            ExpressionAttributeValues={
                ':fulfilled': 'true' if fulfilled else 'false',
                ':updated_at': _iso_now()
            }
        )
        
        return _ok({'success': True})
    except Exception as e:
        logger.exception("Error in update_order")
        return _err(f"Failed to update order: {str(e)}")

# ---------- router ----------

def lambda_handler(event, context):
    """
    Main router for all requests.
    """
    try:
        method = (event.get("httpMethod") or "").upper()
        path = event.get("path") or ""

        # CORS preflight
        if method == "OPTIONS":
            return _ok({"ok": True})

        # GET requests - orders management and shipping config
        if method == "GET":
            if '/orders' in path:
                parts = path.split('/')
                if len(parts) > 2 and parts[-1] != 'orders':
                    return get_single_order(event, context)
                else:
                    return get_orders(event, context)
            elif '/admin/shipping-config' in path:
                return get_shipping_config(event, context)

        # PUT requests - order updates and shipping config
        if method == "PUT":
            if '/orders/' in path:
                return update_order(event, context)
            elif '/admin/shipping-config' in path:
                return save_shipping_config(event, context)

        # POST requests - webhooks, API actions, and shipping
        if method == "POST":
            # Webhook handling
            if '/webhook' in path or event.get('headers', {}).get('stripe-signature'):
                return handle_webhook(event, context)

            # API actions
            data = _parse_body(event)
            action = data.get("action")

            if action == "get_product_info":
                return get_product_info(event, context)
            elif action == "get_offer_configuration":
                return get_offer_configuration(event, context)
            elif action == "create_checkout_session":
                return create_checkout_session(event, context)
            elif action == "get_checkout_session_details":
                return get_checkout_session_details(event, context)
            elif action == "process_one_click_upsell":
                return process_one_click_upsell(event, context)
            elif action == "get_upsell_config":
                return get_upsell_config(event, context)
            elif action == "get_client_offers":
                return get_client_offers(event, context)
            elif action == "update_offer":
                return update_offer(event, context)
            elif action == "get_available_products":
                return get_available_products(event, context)
            elif '/admin/test-shipping' in path:
                return test_shipping_connection(event, context)
            elif '/admin/get-rates' in path or action == "get_shipping_rates":
                return get_shipping_rates(event, context)
            elif '/admin/create-label' in path or action == "create_shipping_label":
                return create_shipping_label(event, context)
            else:
                # Legacy support - assume checkout session creation
                return create_checkout_session(event, context)

        return _err(f"Unsupported route: {method} {path}", 405)

    except Exception as e:
        logger.exception("Unhandled error in lambda_handler")
        return _err(f"Internal server error: {str(e)}", 500)