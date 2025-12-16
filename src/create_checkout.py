# create_checkout.py
# Sample endpoint for creating Stripe Checkout sessions
# This should be deployed as a Lambda function or API endpoint

import json
import os
import logging
from typing import Dict, Any
from urllib.parse import parse_qs

logger = logging.getLogger()
logger.setLevel(logging.INFO)

try:
    import stripe
except ImportError:
    stripe = None

# Environment variables
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_KEYS_TABLE = os.environ.get("STRIPE_KEYS_TABLE")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


def _resp(status: int, body: Dict[str, Any], redirect_url: str = None):
    """Helper to create API Gateway response"""
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }
    
    # If redirect_url is provided, return 303 redirect
    if redirect_url:
        return {
            "statusCode": 303,
            "headers": {
                **headers,
                "Location": redirect_url
            },
            "body": ""
        }
    
    return {
        "statusCode": status,
        "headers": headers,
        "body": json.dumps(body)
    }

def _env_to_mode(env: str) -> str:
    """Map runtime env → Stripe mode name used in keys: live|test"""
    return "live" if env == "prod" else "test"

def _derive_env_from_event(event) -> str:
    """
    Determine environment based on the URL/stage that invoked this Lambda.
    - API Gateway stage 'prod' => prod
    - Host checkout.juniorbay.com => prod
    - Else => dev
    """
    try:
        rc = (event or {}).get("requestContext", {}) or {}
        stage = (rc.get("stage") or "").lower()  # e.g., 'prod' or 'dev'
    except Exception:
        stage = ""

    logger.info(f"Stage: {stage}")

    headers = (event or {}).get("headers", {}) or {}
    host = (_get_header(headers, "x-forwarded-host") or
            _get_header(headers, "host")).lower()
    
    logger.info(f"Host: {host}")
    logger.info(f"Headers: {headers}")

    # Explicit prod checks
    if stage == "prod":
        return "prod"
    if host in ("checkout.juniorbay.com", "pisjx9wsu5.execute-api.us-west-2.amazonaws.com"):
        return "prod"

    # Everything else treated as dev/test
    return "dev"

def _get_header(headers: dict, name: str) -> str:
    if not headers:
        return ""
    # case-insensitive lookup
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v or ""
    return ""

def _read_key_field(item: dict, mode: str) -> str | None:
    # Support both naming schemes: sk_live / sk_test OR live_sk / test_sk
    for key in (f"sk_{mode}", f"{mode}_sk"):
        if item.get(key):
            return item[key]
    return None

def _decrypt_if_needed(value: str) -> str:
    if not isinstance(value, str):
        return value
    if value.startswith("ENCRYPTED("):
        # Prefer your existing helper already present in the file
        try:
            return decrypt_kms(value)
        except NameError:
            raise RuntimeError("Encrypted key present but no decrypt helper is available.")
    return value

def get_stripe_key_for_client(client_id: str, env: str) -> str:
    """
    Optionally fetch per-tenant Stripe key from DynamoDB.
    Falls back to environment variable if not found.
    """

    if not STRIPE_KEYS_TABLE:
        return STRIPE_SECRET_KEY
    
    try:
        import boto3
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(STRIPE_KEYS_TABLE)
        
        response = table.get_item(Key={"clientID": client_id})
        item = response.get("Item", {})

        if env == "prod":
            mode = "live"
        else:
            mode = "test"
        
        # Get the appropriate key based on mode
        #mode = item.get("mode", "test")
        sk_field = f"sk_{mode}"  # sk_test or sk_live

        # logger.log(f"mode: {mode}")
        # logger.log(f"sk_field: {sk_field}")
        
        stripe_key = item.get(sk_field)
        if stripe_key:
            # Handle encrypted keys if needed
            if stripe_key.startswith("ENCRYPTED("):
                # Decrypt using KMS (implementation depends on your setup)
                stripe_key = decrypt_kms(stripe_key)
            return stripe_key
    except Exception as e:
        print(f"Error fetching Stripe key: {e}")
    
    return STRIPE_SECRET_KEY


def get_or_create_customer(email: str, name: str = None, metadata: Dict[str, Any] = None) -> str:
    """
    Get existing customer by email or create a new one.
    Returns customer ID.
    
    This is CRITICAL for upsells - we need a customer ID to save payment methods.
    """
    if not email:
        raise ValueError("Email is required to create/find customer")
    
    # Try to find existing customer
    try:
        customers = stripe.Customer.list(email=email, limit=1)
        if customers.data:
            customer_id = customers.data[0].id
            print(f"Found existing customer: {customer_id} for {email}")
            return customer_id
    except Exception as e:
        print(f"Error searching for customer: {e}")
    
    # Create new customer
    try:
        customer_data = {"email": email}
        if name:
            customer_data["name"] = name
        if metadata:
            customer_data["metadata"] = metadata
        
        customer = stripe.Customer.create(**customer_data)
        print(f"Created new customer: {customer.id} for {email}")
        return customer.id
    except Exception as e:
        print(f"Error creating customer: {e}")
        raise


# create_checkout.py - FIXED VERSION
# Key fix: Ensure payment method is saved for upsells

def lambda_handler(event, context):
    """
    AWS Lambda handler for creating Stripe Checkout sessions.
    """
    env = _derive_env_from_event(event)  
    logger.info(f"Environment: {env}")
    

    # Handle OPTIONS for CORS preflight
    if event.get("httpMethod") == "OPTIONS":
        return _resp(200, {"message": "OK"})
    
    # Parse query parameters
    params = event.get("queryStringParameters") or {}
    
    price_id = params.get("price_id")
    product_id = params.get("product_id")
    client_id = params.get("clientID")
    offer = params.get("offer")
    success_url = params.get("success_url")
    cancel_url = params.get("cancel_url")
    quantity = int(params.get("quantity", "1"))
    
    # Customer information
    customer_email = params.get("customer_email")
    customer_name = params.get("customer_name")
    
    # Upsell parameters
    has_upsell = params.get("has_upsell") == "true"
    upsell_product_id = params.get("upsell_product_id")
    upsell_price_id = params.get("upsell_price_id")
    upsell_offer_text = params.get("upsell_offer_text")
    
    # 🔍 DEBUG LOGGING
    print("=" * 60)
    print("CREATE CHECKOUT - PARAMETERS")
    print(f"  price_id: {price_id}")
    print(f"  product_id: {product_id}")
    print(f"  client_id: {client_id}")
    print(f"  customer_email: {customer_email}")
    print(f"  has_upsell: {has_upsell}")
    print(f"  upsell_price_id: {upsell_price_id}")
    print("=" * 60)
    
    # Validate required parameters
    if not price_id:
        return _resp(400, {"error": "Missing required parameter: price_id"})
    if not client_id:
        return _resp(400, {"error": "Missing required parameter: clientID"})
    if not success_url:
        return _resp(400, {"error": "Missing required parameter: success_url"})
    if not cancel_url:
        return _resp(400, {"error": "Missing required parameter: cancel_url"})
    
    # Check if Stripe is available
    if not stripe:
        return _resp(500, {"error": "Stripe SDK not available"})
    
    try:
        # Get the appropriate Stripe key for this client
        stripe_key = get_stripe_key_for_client(client_id, env)
        if not stripe_key:
            return _resp(500, {"error": "Stripe API key not configured"})
        
        stripe.api_key = stripe_key
        
        # ================================================================
        # ✅ CRITICAL FIX: Get or create customer BEFORE checkout
        # This ensures payment method will be saved correctly
        # ================================================================
        customer_id = None
        if customer_email:
            try:
                customer_metadata = {
                    "clientID": client_id,
                }
                if offer:
                    customer_metadata["offer"] = offer
                
                customer_id = get_or_create_customer(
                    email=customer_email,
                    name=customer_name,
                    metadata=customer_metadata
                )
                print(f"✅ Using customer {customer_id} for checkout")
            except Exception as e:
                print(f"⚠️ Warning: Could not create/find customer: {e}")
        else:
            print("⚠️ WARNING: No customer_email provided - upsells will NOT work!")
        
        # Build metadata
        metadata = {
            "clientID": client_id,
        }
        if offer:
            metadata["offer"] = offer
        if product_id:
            metadata["product_id"] = product_id
        
        # Add upsell information to metadata
        if has_upsell:
            metadata["has_upsell"] = "true"
            if upsell_product_id:
                metadata["upsell_product_id"] = upsell_product_id
            if upsell_price_id:
                metadata["upsell_price_id"] = upsell_price_id
            if upsell_offer_text:
                metadata["upsell_offer_text"] = upsell_offer_text
        
        print(f"📦 Metadata: {metadata}")
        
        # ================================================================
        # ✅ CRITICAL: Configure session to save payment method
        # ================================================================
        session_config = {
            "mode": "payment",
            "line_items": [{
                "price": price_id,
                "quantity": quantity,
            }],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": metadata,
            "shipping_address_collection": {
                "allowed_countries": ["US", "CA"],
            },
            "payment_method_types": ["card"],
            "billing_address_collection": "required",
        }
        
        # ================================================================
        # ✅ KEY FIX: Add customer and payment_intent_data with setup_future_usage
        # This tells Stripe to save the payment method to the customer
        # ================================================================
        if customer_id:
            session_config["customer"] = customer_id
            
            # ✅ CRITICAL: This saves the payment method for future use
            session_config["payment_intent_data"] = {
                "setup_future_usage": "off_session",  # Saves PM for off-session charges
                "metadata": metadata  # Also add metadata to PaymentIntent
            }
            print(f"✅ Configured to save payment method for customer {customer_id}")
        else:
            # Fallback: let Stripe create customer (less reliable for upsells)
            session_config["customer_creation"] = "always"
            session_config["payment_intent_data"] = {
                "setup_future_usage": "off_session",
                "metadata": metadata
            }
            print("⚠️ Using customer_creation fallback")
        
        # Create the session
        session = stripe.checkout.Session.create(**session_config)
        
        print(f"✅ Created checkout session: {session.id}")
        print("=" * 60)
        
        # Redirect to Stripe Checkout
        return _resp(200, {"url": session.url}, redirect_url=session.url)
        
    except stripe.error.StripeError as e:
        print(f"❌ Stripe error: {e}")
        return _resp(400, {"error": f"Stripe error: {str(e)}"})
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return _resp(500, {"error": "Internal server error"})


def decrypt_kms(encrypted_value: str) -> str:
    """
    Decrypt KMS-encrypted value.
    Expects format: ENCRYPTED(base64_ciphertext)
    """
    import boto3
    import base64
    
    if not encrypted_value.startswith("ENCRYPTED("):
        return encrypted_value
    
    # Extract base64 ciphertext
    b64_ciphertext = encrypted_value[len("ENCRYPTED("):-1]
    ciphertext = base64.b64decode(b64_ciphertext)
    
    # Decrypt with KMS
    kms = boto3.client("kms")
    response = kms.decrypt(
        CiphertextBlob=ciphertext,
        EncryptionContext={"app": "stripe-cart"}
    )
    
    return response["Plaintext"].decode("utf-8")


# For local testing
if __name__ == "__main__":
    # Simulate API Gateway event
    test_event = {
        "httpMethod": "GET",
        "queryStringParameters": {
            "price_id": "price_1S1PQSEcxlWjis9iJx6Bxrde",
            "product_id": "prod_SxK4r0sm7rKRnx",
            "clientID": "d831c360-00e1-706a-6d3a-2a5361c20df6",
            "offer": "nad-supplement",
            "customer_email": "test@example.com",  # ← REQUIRED for upsells!
            "customer_name": "Test Customer",
            "success_url": "http://localhost:8000/success.html",
            "cancel_url": "http://localhost:8000/cancel.html",
            "has_upsell": "true",
            "upsell_product_id": "prod_xyz",
            "upsell_price_id": "price_xyz",
        }
    }
    
    response = lambda_handler(test_event, None)
    print(json.dumps(response, indent=2))