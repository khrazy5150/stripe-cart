import json
import os
import logging
from decimal import Decimal

import boto3

# Optional dependency: ensure 'stripe' in your Lambda layer/requirements
try:
    import stripe
    STRIPE_AVAILABLE = True
except Exception:
    stripe = None
    STRIPE_AVAILABLE = False

# ---- ENV ----
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")
APP_CONFIG_TABLE = os.environ.get("APP_CONFIG_TABLE")

# For Stripe fallback
STRIPE_KEYS_TABLE = os.environ.get("STRIPE_KEYS_TABLE")
STRIPE_KMS_KEY_ARN = os.environ.get("STRIPE_KMS_KEY_ARN")

dynamodb = boto3.resource("dynamodb")
appcfg = dynamodb.Table(APP_CONFIG_TABLE)
kms = boto3.client("kms")
stripe_keys_table = dynamodb.Table(STRIPE_KEYS_TABLE) if STRIPE_KEYS_TABLE else None

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Log configuration on startup
logger.info(f"=== Offers Lambda Configuration ===")
logger.info(f"Environment: {ENVIRONMENT}")
logger.info(f"APP_CONFIG_TABLE: {APP_CONFIG_TABLE}")
logger.info(f"STRIPE_KEYS_TABLE: {STRIPE_KEYS_TABLE}")
logger.info(f"STRIPE_KMS_KEY_ARN: {STRIPE_KMS_KEY_ARN}")
logger.info(f"Stripe available: {STRIPE_AVAILABLE}")

# Encryption context - MUST match what products.py uses
ENC_CTX = {"app": "stripe-cart"}
logger.info(f"Encryption context: {ENC_CTX}")


# ---------- utils ----------
class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            try:
                return int(o) if o == int(o) else float(o)
            except Exception:
                return float(o)
        return super().default(o)


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "OPTIONS,GET,PUT",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Client-Id",
        },
        "body": json.dumps(body, cls=DecimalEncoder),
    }


def _q(event, name, default=None):
    return (event.get("queryStringParameters") or {}).get(name, default)


# ---------- data access ----------
def _load_offers_document():
    """
    Item shape (single doc per env):
    {
      "config_key": "offers",
      "environment": "<env>",
      "offers": {
        "<offer>": {
          "product_ids": ["prod_…", ...],
          "path": "buy-…",
          "active": true
        }
      }
    }
    """
    try:
        res = appcfg.get_item(Key={"config_key": "offers", "environment": ENVIRONMENT})
        return res.get("Item") or {}
    except Exception as e:
        logger.error(f"Failed to read offers doc: {e}")
        return {}


# ---------- primary: reuse Products API ----------
def _get_admin_products_for_client(client_id: str):
    """
    First attempt: import Products API's serializer (single source of truth).
    Returns tuple: (products_list, error_message)
    """
    try:
        import products as products_module
    except Exception as e:
        error_msg = f"Import products.py failed: {e}"
        logger.warning(error_msg)
        return [], error_msg

    if not hasattr(products_module, "_admin_get_products"):
        error_msg = "products._admin_get_products not found"
        logger.warning(error_msg)
        return [], error_msg

    try:
        items = products_module._admin_get_products(client_id)
        products = list(items) if items else []
        return products, None
    except Exception as e:
        error_msg = f"_admin_get_products failed: {e}"
        logger.error(error_msg)
        return [], error_msg


# ---------- Direct Stripe product fetching ----------
def _fetch_all_products_from_stripe(client_id: str):
    """
    Fetch ALL products from Stripe for a given client
    Returns tuple: (products_list, error_message)
    """
    tenant = _fetch_tenant_row(client_id)
    sc, error = _stripe_client_from_tenant(tenant)
    if not sc:
        logger.error(f"Cannot create Stripe client: {error}")
        return [], error

    try:
        logger.info(f"Fetching all products from Stripe for client: {client_id}")
        
        # Fetch all products (up to 100)
        products_response = sc.Product.list(limit=100, active=True)
        all_products = []
        
        for sp in products_response.get("data", []):
            try:
                # Get prices for this product
                prices_response = sc.Price.list(product=sp["id"], limit=100)
                prices = [dict(p) for p in prices_response.get("data", [])]
                
                # Build product object
                product_obj = _build_product_object(dict(sp), prices)
                all_products.append(product_obj)
                
            except Exception as e:
                logger.warning(f"Failed to process product {sp.get('id')}: {e}")
                continue
        
        logger.info(f"Successfully fetched {len(all_products)} products from Stripe")
        return all_products, None
        
    except Exception as e:
        error_msg = f"Failed to fetch products from Stripe: {e}"
        logger.error(error_msg)
        return [], error_msg


# ---------- fallback: go to Stripe directly ----------
def _fetch_tenant_row(client_id: str):
    """
    Adjust this to your actual stripe_keys schema.
    Expected fields (typical patterns from our prior work):
      PK = clientID (note: capital ID)
      mode = 'test'|'live'
      test_secret_key_encrypted | live_secret_key_encrypted (base64 KMS blobs) OR plaintext if you store them that way
    """
    if not stripe_keys_table:
        logger.warning("STRIPE_KEYS_TABLE not configured; cannot fallback to Stripe")
        return None
    
    table_name = stripe_keys_table.table_name
    logger.info(f"Fetching tenant from table: {table_name} for clientID: {client_id}")
    
    try:
        res = stripe_keys_table.get_item(Key={"clientID": client_id})
        item = res.get("Item")
        if not item:
            logger.warning(f"No tenant row found for clientID={client_id} in table {table_name}")
        else:
            logger.info(f"Found tenant row in {table_name} with keys: {list(item.keys())}")
        return item
    except Exception as e:
        logger.error(f"stripe_keys get_item failed for table {table_name}: {e}")
        return None


def _kms_decrypt_wrapped(blob: str) -> str:
    """
    Decrypt KMS-encrypted value with error handling.
    Expects format: ENCRYPTED(base64-encoded-ciphertext)
    If not wrapped, returns the value as-is (plaintext).
    """
    import base64
    
    if not blob:
        logger.warning("Decrypt called with empty/None value")
        return ""
    
    logger.info(f"Attempting to decrypt value (length: {len(blob)})")
    logger.info(f"Value starts with: {blob[:20]}... ends with: ...{blob[-20:]}")
    
    # If not encrypted, return as-is (plaintext)
    if not (blob.startswith("ENCRYPTED(") and blob.endswith(")")):
        logger.info("Value is NOT wrapped with ENCRYPTED() - treating as plaintext")
        logger.info(f"Plaintext value starts with: {blob[:10]}...")
        return blob
    
    logger.info("Value IS wrapped with ENCRYPTED() - attempting KMS decryption")
    
    try:
        # Extract base64 from ENCRYPTED(...)
        b64 = blob[len("ENCRYPTED("):-1]
        logger.info(f"Extracted base64 length: {len(b64)}")
        
        ct = base64.b64decode(b64)
        logger.info(f"Decoded ciphertext length: {len(ct)} bytes")
        
        # Decrypt with encryption context
        logger.info(f"Calling KMS decrypt with context: {ENC_CTX}")
        resp = kms.decrypt(
            CiphertextBlob=ct,
            EncryptionContext=ENC_CTX
        )
        decrypted = resp['Plaintext'].decode('utf-8')
        logger.info(f"Successfully decrypted KMS value (result length: {len(decrypted)})")
        return decrypted
    except Exception as e:
        logger.error(f"KMS decrypt failed with error: {type(e).__name__}: {str(e)}")
        logger.error(f"Encryption context used: {ENC_CTX}")
        return ""


def _stripe_client_from_tenant(tenant: dict):
    """
    Build stripe client using tenant row. Matches products.py logic exactly.
    Returns tuple: (stripe_client, error_message)
    """
    logger.info("calling function _stripe_client_from_tenant")

    if not STRIPE_AVAILABLE:
        return None, "stripe package not available"

    if not tenant:
        return None, "No tenant configuration found"

    table_name = stripe_keys_table.table_name if stripe_keys_table else "UNKNOWN"

    if ENVIRONMENT == "prod":
        mode = "live"
    else:
        mode = "test"

    logger.info(f"Building Stripe client from table: {table_name}")
    logger.info(f"Mode: {mode}, Available tenant keys: {list(tenant.keys())}")
    
    # Try field name patterns in same order as products.py
    possible_fields = [
        f"sk_{mode}",                    # Pattern 1: sk_test, sk_live (try FIRST)
        f"{mode}_secret_key",            # Pattern 2: test_secret_key, live_secret_key
        f"{mode}_secret_key_encrypted"   # Pattern 3: test_secret_key_encrypted
    ]
    
    encrypted_value = None
    key_field = None
    
    for field in possible_fields:
        if field in tenant:
            encrypted_value = tenant[field]
            key_field = field
            logger.info(f"Found key in field: {key_field} from table: {table_name}")
            break
    
    if not encrypted_value:
        error_msg = f"No Stripe secret key available for tenant in mode '{mode}'. Available keys: {list(tenant.keys())}"
        logger.error(error_msg)
        return None, error_msg

    # ALWAYS decrypt (function handles plaintext by returning as-is)
    logger.info(f"Attempting to decrypt key from field: {key_field} (table: {table_name})")
    secret = _kms_decrypt_wrapped(encrypted_value)
    
    if not secret:
        error_msg = f"Failed to decrypt key from field '{key_field}' in table '{table_name}'"
        logger.error(error_msg)
        return None, error_msg

    try:
        sc = stripe
        sc.api_key = secret
        logger.info(f"Successfully initialized Stripe client (key length: {len(secret)}, from table: {table_name})")
        return sc, None
    except Exception as e:
        return None, f"Failed to initialize Stripe client: {e}"


def _build_product_object(sp: dict, prices: list) -> dict:
    """
    Conform to your product detail shape from Products API.
    """
    # compute helpers
    active_prices = [p for p in prices if p.get("active") and p.get("unit_amount") is not None]
    lowest = min((p["unit_amount"] for p in active_prices), default=None)

    metadata = sp.get("metadata") or {}
    has_upsell = bool(metadata.get("upsell_product_id") or metadata.get("upsell_price_id"))

    return {
        "id": sp.get("id"),
        "name": sp.get("name"),
        "description": sp.get("description"),
        "active": sp.get("active"),
        "images": sp.get("images") or [],
        "prices": [
            {
                "id": p.get("id"),
                "unit_amount": p.get("unit_amount"),
                "currency": p.get("currency"),
                "recurring": p.get("recurring"),  # keep null or object
            }
            for p in prices
        ],
        "price_count": len(prices),
        "lowest_price": lowest,
        "product_type": metadata.get("product_type"),
        "product_category": metadata.get("product_category"),
        "has_upsell": has_upsell,
        "created": sp.get("created"),
        "metadata": metadata,
    }


def _fetch_products_from_stripe_by_ids(client_id: str, product_ids: list):
    """
    Returns tuple: (products_list, error_message)
    """
    tenant = _fetch_tenant_row(client_id)
    sc, error = _stripe_client_from_tenant(tenant)
    if not sc:
        logger.error(f"Cannot create Stripe client: {error}")
        return [], error

    out = []
    errors = []
    for pid in product_ids:
        try:
            sp = sc.Product.retrieve(pid)
            # list all prices for product (you may restrict to active only if desired)
            plist = sc.Price.list(product=pid, limit=100)
            prices = [dict(p) for p in plist.get("data", [])]
            out.append(_build_product_object(dict(sp), prices))
        except Exception as e:
            error_msg = f"Stripe fetch failed for product {pid}: {e}"
            logger.error(error_msg)
            errors.append(error_msg)
            continue
    
    combined_error = "; ".join(errors) if errors else None
    return out, combined_error


# ---------- handler ----------
def lambda_handler(event, context):
    method = event.get("httpMethod")
    resource = event.get("resource") or event.get("path")

    # NEW: GET /admin/products endpoint - fetch ALL products
    if method == "GET" and resource == "/admin/products":
        client_id = _q(event, "clientID") or _q(event, "clientId")
        
        if not client_id:
            return _resp(400, {"error": "Missing clientID parameter"})
        
        logger.info(f"Fetching products for client: {client_id}")
        
        # Try primary source first (if products.py exists)
        products, error = _get_admin_products_for_client(client_id)
        
        # If primary source fails or returns empty, fetch directly from Stripe
        if error or not products:
            logger.info(f"Primary source failed/empty. Falling back to direct Stripe fetch. Error: {error}")
            products, stripe_error = _fetch_all_products_from_stripe(client_id)
            
            if stripe_error:
                logger.error(f"Both sources failed. Primary: {error}, Stripe: {stripe_error}")
                return _resp(500, {
                    "products": [], 
                    "error": stripe_error,
                    "debug": {
                        "primary_error": error,
                        "stripe_error": stripe_error,
                        "stripe_available": STRIPE_AVAILABLE,
                        "stripe_keys_table": STRIPE_KEYS_TABLE is not None
                    }
                })
        
        logger.info(f"Returning {len(products)} products")
        return _resp(200, {"products": products})

    # NEW: GET /public/offer
    if method == "GET" and resource == "/public/offer":
        client_id = _q(event, "clientID") or _q(event, "clientId")
        offer_name = _q(event, "offer")
        debug = _q(event, "debug") == "true"  # Add ?debug=true to see error details
        
        if not client_id or not offer_name:
            return _resp(400, {"error": "Missing required query params: clientID and offer"})

        doc = _load_offers_document()
        offers_map = (doc or {}).get("offers") or {}
        offer_cfg = offers_map.get(offer_name)
        
        if not offer_cfg:
            return _resp(404, {
                "clientID": client_id, 
                "offers": {offer_name: None}, 
                "error": "Offer not found"
            })

        ids = list(offer_cfg.get("product_ids") or [])
        if not ids:
            # Return empty but valid structure
            enriched = dict(offer_cfg)
            enriched["product_ids"] = []
            return _resp(200, {"clientID": client_id, "offers": {offer_name: enriched}})

        # Track errors for debug mode
        debug_info = {
            "stripe_available": STRIPE_AVAILABLE,
            # "stripe_keys_table_configured": STRIPE_KEYS_TABLE is not None,
            "stripe_keys_table_configured": STRIPE_KEYS_TABLE,
            "product_ids_requested": ids,
            "errors": []
        }

        # Try canonical source (Products API)
        products, products_error = _get_admin_products_for_client(client_id)
        if products_error:
            debug_info["errors"].append(f"Products API: {products_error}")
        
        by_id = {p.get("id"): p for p in products if p.get("id")}
        detailed = [by_id[i] for i in ids if i in by_id]
        
        debug_info["products_api_found"] = len(detailed)

        # Fallback to Stripe if nothing (or some) found
        if len(detailed) < len(ids):
            missing = [i for i in ids if i not in by_id]
            debug_info["missing_product_ids"] = missing
            
            stripe_details, stripe_error = _fetch_products_from_stripe_by_ids(client_id, missing)
            if stripe_error:
                debug_info["errors"].append(f"Stripe API: {stripe_error}")
            
            debug_info["stripe_api_found"] = len(stripe_details)
            detailed += stripe_details

        # Preserve the offer, swap product_ids -> detailed objects
        enriched_offer = dict(offer_cfg)
        enriched_offer["product_ids"] = detailed

        response_body = {"clientID": client_id, "offers": {offer_name: enriched_offer}}
        
        # Include debug info if requested
        if debug:
            response_body["_debug"] = debug_info

        return _resp(200, response_body)

    # DEBUG: Check stripe_keys table structure
    if method == "GET" and resource == "/debug/stripe-keys":
        client_id = _q(event, "clientID") or _q(event, "clientId")
        if not client_id:
            return _resp(400, {"error": "Missing clientID parameter"})
        
        if not stripe_keys_table:
            return _resp(500, {"error": "STRIPE_KEYS_TABLE not configured"})
        
        # Try the correct key
        try:
            res = stripe_keys_table.get_item(Key={"clientID": client_id})
            if "Item" in res:
                # Mask sensitive data
                item = res["Item"]
                masked = {k: ("***MASKED***" if "secret" in k.lower() or "key" in k.lower() else v) 
                         for k, v in item.items()}
                return _resp(200, {
                    "clientID": client_id,
                    "found": True,
                    "item_keys": list(item.keys()),
                    "item": masked
                })
            else:
                return _resp(404, {"clientID": client_id, "found": False})
        except Exception as e:
            return _resp(500, {"clientID": client_id, "error": str(e)})

    # Existing admin endpoints with enhanced error handling
    if method == "GET" and resource == "/admin/offers":
        client_id = _q(event, "clientID")
        doc = _load_offers_document()
        return _resp(200, {"clientID": client_id, "offers": doc.get("offers") or {}})

    if method == "PUT" and resource == "/admin/offers":
        try:
            # Parse request body
            body = json.loads(event.get("body") or "{}")
            logger.info(f"PUT /admin/offers - Body keys: {list(body.keys())}")
            
        except Exception as e:
            logger.error(f"Failed to parse JSON body: {e}")
            return _resp(400, {"error": f"Invalid JSON body: {str(e)}"})

        # Extract offers from body
        offers = body.get("offers")
        if offers is None:
            logger.error("Body missing 'offers' field")
            return _resp(400, {"error": "Body must include 'offers'"})
        
        # Log offers structure for debugging
        logger.info(f"Saving offers for environment: {ENVIRONMENT}")
        logger.info(f"Number of offers: {len(offers) if isinstance(offers, dict) else 'invalid'}")
        
        try:
            # Prepare item for DynamoDB
            item = {
                "config_key": "offers",
                "environment": ENVIRONMENT,
                "offers": offers
            }
            
            # Save to DynamoDB
            logger.info(f"Attempting to save to table: {APP_CONFIG_TABLE}")
            appcfg.put_item(Item=item)
            logger.info("Successfully saved offers to DynamoDB")
            
            return _resp(200, {"ok": True, "offers": offers})
            
        except Exception as e:
            logger.error(f"Failed to save offers to DynamoDB: {e}")
            logger.error(f"Error type: {type(e).__name__}")
            
            # Check if it's a permissions error
            if "AccessDenied" in str(e) or "UnauthorizedOperation" in str(e):
                return _resp(500, {
                    "error": "Permission denied: Lambda doesn't have write access to DynamoDB table",
                    "details": str(e),
                    "table": APP_CONFIG_TABLE
                })
            else:
                return _resp(500, {
                    "error": f"Failed to save offers: {str(e)}",
                    "table": APP_CONFIG_TABLE
                })

    if method == "OPTIONS":
        # Handle CORS preflight
        return _resp(200, {"ok": True})

    return _resp(404, {"error": "Not Found"})