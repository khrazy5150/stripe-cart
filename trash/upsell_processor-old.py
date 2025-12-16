# upsell_processor.py
# Handles upsell operations - getting session details and processing one-click upsell
#
# Routes:
#   GET /api/upsell-session?session_id=xxx&clientID=xxx
#   POST /api/process-upsell

import json
import os
import logging
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
            "has_upsell": metadata.get("has_upsell") == "true",
            "upsell_product_id": metadata.get("upsell_product_id"),
            "upsell_price_id": metadata.get("upsell_price_id"),
            "upsell_offer_text": metadata.get("upsell_offer_text"),
            "original_product_id": metadata.get("product_id"),
        }
        
        logger.info(f"Retrieved upsell session details for {session_id}")
        return _resp(200, response_data)
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {e}")
        return _resp(400, {"error": f"Stripe error: {str(e)}"})
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return _resp(500, {"error": "Internal server error"})


def process_one_click_upsell(event):
    """
    POST /api/process-upsell
    
    Body:
    {
        "clientID": "xxx",
        "session_id": "xxx",
        "customer_id": "xxx",
        "payment_method_id": "xxx",
        "upsell_price_id": "xxx",
        "shipping_address": {...}
    }
    
    Creates a new payment intent using the saved payment method for the upsell product.
    """
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "Invalid JSON body"})
    
    client_id = body.get("clientID")
    customer_id = body.get("customer_id")
    payment_method_id = body.get("payment_method_id")
    upsell_price_id = body.get("upsell_price_id")
    shipping_address = body.get("shipping_address") or {}
    
    if not client_id:
        return _resp(400, {"error": "Missing clientID"})
    if not customer_id:
        return _resp(400, {"error": "Missing customer_id"})
    if not payment_method_id:
        return _resp(400, {"error": "Missing payment_method_id"})
    if not upsell_price_id:
        return _resp(400, {"error": "Missing upsell_price_id"})
    
    if not stripe:
        return _resp(500, {"error": "Stripe SDK not available"})
    
    try:
        # Get Stripe key for this client
        stripe_key = get_stripe_key_for_client(client_id)
        if not stripe_key:
            return _resp(500, {"error": "Stripe API key not configured"})
        
        stripe.api_key = stripe_key
        
        # Get the price to determine the amount
        price = stripe.Price.retrieve(upsell_price_id)
        amount = price.unit_amount
        currency = price.currency
        
        logger.info(f"Creating upsell payment intent: amount={amount}, currency={currency}")
        
        # Build shipping dict only if we have valid address data
        shipping_dict = None
        if shipping_address and shipping_address.get('line1'):
            shipping_dict = {
                "name": shipping_address.get("name", ""),
                "address": {
                    "line1": shipping_address.get("line1", ""),
                    "line2": shipping_address.get("line2"),
                    "city": shipping_address.get("city", ""),
                    "state": shipping_address.get("state", ""),
                    "postal_code": shipping_address.get("postal_code", ""),
                    "country": shipping_address.get("country", "US"),
                }
            }
        
        # Create payment intent with saved payment method
        payment_intent = stripe.PaymentIntent.create(
            amount=amount,
            currency=currency,
            customer=customer_id,
            payment_method=payment_method_id,
            off_session=True,  # Customer is not present
            confirm=True,  # Automatically confirm
            description=f"Upsell purchase",
            metadata={
                "clientID": client_id,
                "upsell": "true",
                "original_session_id": body.get("session_id"),
            },
            shipping=shipping_dict,
        )
        
        logger.info(f"Created upsell payment intent: {payment_intent.id}")
        
        return _resp(200, {
            "success": True,
            "payment_intent_id": payment_intent.id,
            "status": payment_intent.status,
            "amount": amount,
            "currency": currency,
        })
        
    except stripe.error.CardError as e:
        # Card was declined
        logger.error(f"Card declined: {e}")
        return _resp(400, {
            "success": False,
            "error": "Card was declined",
            "decline_code": e.code
        })
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {e}")
        return _resp(400, {
            "success": False,
            "error": f"Stripe error: {str(e)}"
        })
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return _resp(500, {
            "success": False,
            "error": "Internal server error"
        })


def lambda_handler(event, context):
    """Main Lambda handler routing to appropriate function"""
    
    # Handle OPTIONS for CORS
    if event.get("httpMethod") == "OPTIONS":
        return _resp(200, {"message": "OK"})
    
    resource = event.get("resource", "")
    method = event.get("httpMethod", "")
    
    # Route to appropriate handler
    if resource == "/api/upsell-session" and method == "GET":
        return get_upsell_session_details(event)
    elif resource == "/api/process-upsell" and method == "POST":
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