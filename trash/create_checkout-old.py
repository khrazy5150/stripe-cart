# create_checkout.py
# Sample endpoint for creating Stripe Checkout sessions
# This should be deployed as a Lambda function or API endpoint

import json
import os
from typing import Dict, Any
from urllib.parse import parse_qs

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


def get_stripe_key_for_client(client_id: str) -> str:
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
        
        # Get the appropriate key based on mode
        mode = item.get("mode", "test")
        sk_field = f"sk_{mode}"  # sk_test or sk_live
        
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


def lambda_handler(event, context):
    """
    AWS Lambda handler for creating Stripe Checkout sessions.
    
    Expected query parameters:
    - price_id (required): Stripe price ID
    - product_id (optional): Stripe product ID for metadata
    - clientID (required): Client identifier
    - offer (optional): Offer key for tracking
    - success_url (required): URL to redirect after successful payment
    - cancel_url (required): URL to redirect if user cancels
    - quantity (optional): Quantity to purchase (default: 1)
    """
    
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
    
    # Upsell parameters
    has_upsell = params.get("has_upsell") == "true"
    upsell_product_id = params.get("upsell_product_id")
    upsell_price_id = params.get("upsell_price_id")
    upsell_offer_text = params.get("upsell_offer_text")
    
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
        stripe_key = get_stripe_key_for_client(client_id)
        if not stripe_key:
            return _resp(500, {"error": "Stripe API key not configured"})
        
        stripe.api_key = stripe_key
        
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
        
        # Create Stripe Checkout Session
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price": price_id,
                "quantity": quantity,
            }],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=metadata,
            # Optional: enable customer data collection
            customer_creation="always",
            # Optional: enable shipping address collection for physical products
            shipping_address_collection={
                "allowed_countries": ["US", "CA"],
            },
            # Optional: set payment method types
            payment_method_types=["card"],
            # Optional: configure billing address collection
            billing_address_collection="required",
        )
        
        print(f"Created checkout session: {session.id} for client: {client_id}")
        
        # Redirect to Stripe Checkout
        return _resp(200, {"url": session.url}, redirect_url=session.url)
        
    except stripe.error.StripeError as e:
        print(f"Stripe error: {e}")
        return _resp(400, {"error": f"Stripe error: {str(e)}"})
    except Exception as e:
        print(f"Unexpected error: {e}")
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
            "success_url": "http://localhost:8000/success.html",
            "cancel_url": "http://localhost:8000/cancel.html",
        }
    }
    
    response = lambda_handler(test_event, None)
    print(json.dumps(response, indent=2))