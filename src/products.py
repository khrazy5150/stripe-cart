import json
import os
import re
import math
import boto3
from botocore.exceptions import ClientError
import logging
from decimal import Decimal
import base64


try:
    import stripe
    STRIPE_AVAILABLE = True
except Exception:
    stripe = None
    STRIPE_AVAILABLE = False

# Environment variables
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")
STRIPE_KEYS_TABLE = os.environ.get("STRIPE_KEYS_TABLE")
STRIPE_KMS_KEY_ARN = os.environ.get("STRIPE_KMS_KEY_ARN")

# AWS clients
dynamodb = boto3.resource("dynamodb")
kms = boto3.client("kms")
stripe_keys_table = dynamodb.Table(STRIPE_KEYS_TABLE) if STRIPE_KEYS_TABLE else None

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Encryption context - MUST match offers.py
ENC_CTX = {"app": "stripe-cart"}

DEFAULT_PRODUCT_LIMIT = 20
MAX_PRODUCT_LIMIT = 100


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o == int(o) else float(o)
        return super().default(o)


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PUT,DELETE",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Client-Id",
        },
        "body": json.dumps(body, cls=DecimalEncoder),
    }


def _q(event, name, default=None):
    return (event.get("queryStringParameters") or {}).get(name, default)


def _fetch_tenant_row(client_id: str):
    """Fetch tenant configuration from DynamoDB"""
    if not stripe_keys_table:
        logger.warning("STRIPE_KEYS_TABLE not configured")
        return None
    
    try:
        res = stripe_keys_table.get_item(Key={"clientID": client_id})
        item = res.get("Item")
        if item:
            logger.info(f"Found tenant row for clientID={client_id}")
        return item
    except Exception as e:
        logger.error(f"Failed to fetch tenant: {e}")
        return None
    

def _kms_decrypt_wrapped(blob: str) -> str:
    """Decrypt KMS-encrypted value"""
    if not blob:
        return ""
    
    # If not encrypted, return as-is
    if not (blob.startswith("ENCRYPTED(") and blob.endswith(")")):
        return blob
    
    try:
        # Extract base64 from ENCRYPTED(...)
        b64 = blob[len("ENCRYPTED("):-1]
        ct = base64.b64decode(b64)
        
        # Decrypt with encryption context
        resp = kms.decrypt(
            CiphertextBlob=ct,
            EncryptionContext=ENC_CTX
        )
        return resp['Plaintext'].decode('utf-8')
    except Exception as e:
        logger.error(f"KMS decrypt failed: {e}")
        return ""
    

def _stage_is_prod():
    stage = (os.getenv("STAGE") or "").lower()
    return stage in ("prod", "production", "live")

def _desired_mode_from(event) -> str:
    # X-Stripe-Mode header or stripeMode query overrides STAGE
    desired = None
    if event:
        hdrs = (event.get("headers") or {})
        qs = (event.get("queryStringParameters") or {})
        desired = hdrs.get("X-Stripe-Mode") or hdrs.get("x-stripe-mode") or qs.get("stripeMode")
        if desired:
            desired = str(desired).strip().lower()
            if desired not in ("live", "test"):
                desired = None
    return desired or ("live" if _stage_is_prod() else "test")

def _get_stripe_client(client_id: str, event=None):
    """
    Looks up the tenant row in STRIPE_KEYS_TABLE:
      PK: clientID
      SK: mode  (expected values: 'test' or 'live')
    Decrypts sk_{mode} with _kms_decrypt_wrapped(...) and returns the configured stripe module.
    """
    if not client_id:
        return None, "client_id is required"

    table_name = os.getenv("STRIPE_KEYS_TABLE")
    if not table_name:
        return None, "STRIPE_KEYS_TABLE env var is not set"

    table = dynamodb.Table(table_name)
    mode = _desired_mode_from(event)  # 'test' or 'live'

    # 1) Try composite key {clientID, mode}
    item = None
    try:
        resp = table.get_item(Key={"clientID": client_id, "mode": mode})
        item = resp.get("Item")
    except ClientError as e:
        # If the table doesn't have a sort key, ValidationException can happen; we'll fall back below.
        if e.response.get("Error", {}).get("Code") != "ValidationException":
            return None, f"DynamoDB error: {e}"

    # 2) Fallback to simple key {clientID} (in case some envs don’t use a sort key)
    if not item:
        try:
            resp = table.get_item(Key={"clientID": client_id})
            item = resp.get("Item")
        except ClientError as e:
            return None, f"DynamoDB error: {e}"

    if not item:
        return None, f"No stripe keys row found for clientID={client_id}"

    # Pick the right encrypted secret field based on mode
    field = "sk_live" if mode == "live" else "sk_test"
    # be tolerant to naming drift
    candidates = [field, field.upper(), field.replace("_", ""), f"{mode}_sk", f"{mode}_secret", f"{mode}Secret"]
    enc_secret = next((item.get(k) for k in candidates if item.get(k)), None)
    if not enc_secret:
        return None, f"Encrypted secret key not found in row for mode={mode}"

    # Decrypt using your existing helper
    try:
        secret = _kms_decrypt_wrapped(enc_secret)
    except NameError:
        return None, "Missing _kms_decrypt_wrapped(); import or define it in products.py"
    except Exception as e:
        return None, f"KMS decrypt failed: {e}"

    # Configure stripe and return the module
    try:
        stripe.api_key = secret
        api_version = os.getenv("STRIPE_API_VERSION")
        if api_version:
            stripe.api_version = api_version
        return stripe, None
    except Exception as e:
        return None, f"Stripe init failed: {e}"
    

def _stripe_client_from_tenant(tenant: dict):
    """Build Stripe client from tenant configuration"""
    if not STRIPE_AVAILABLE:
        return None, "Stripe package not available"

    if not tenant:
        return None, "No tenant configuration found"

    if ENVIRONMENT == "prod":
        mode = "live"
    else:
        mode = "test"
    
    # Try different field patterns
    possible_fields = [
        f"sk_{mode}",                    # sk_test, sk_live
        f"{mode}_secret_key",            # test_secret_key, live_secret_key
        f"{mode}_secret_key_encrypted"   # test_secret_key_encrypted
    ]
    
    encrypted_value = None
    for field in possible_fields:
        if field in tenant:
            encrypted_value = tenant[field]
            logger.info(f"Found key in field: {field}")
            break
    
    if not encrypted_value:
        return None, f"No Stripe secret key found for mode '{mode}'"

    # Decrypt the key
    secret = _kms_decrypt_wrapped(encrypted_value)
    
    if not secret:
        return None, "Failed to decrypt Stripe key"

    try:
        sc = stripe
        sc.api_key = secret
        logger.info("Successfully initialized Stripe client")
        return sc, None
    except Exception as e:
        return None, f"Failed to initialize Stripe client: {e}"


def _build_product_object(sp: dict, prices: list) -> dict:
    """Build standardized product object"""
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
                "recurring": p.get("recurring"),
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


def _sanitize_search_term(term: str) -> str:
    term = term or ""
    # Escape quotes per Stripe search syntax
    return term.replace("\\", "\\\\").replace("'", "\\'")


def _sanitize_metadata_key(key: str) -> str:
    if not key:
        return ""
    return re.sub(r"[^a-zA-Z0-9_:\\-]", "", key)


def _list_products_segment(sc, *, limit: int, starting_after: str | None, active_flag: bool | None):
    """List a single segment (active or archived) of Stripe products."""
    params = {"limit": limit}
    if starting_after:
        params["starting_after"] = starting_after
    if active_flag is True:
        params["active"] = True
    elif active_flag is False:
        params["active"] = False
    response = sc.Product.list(**params)
    data = response.get("data", [])
    has_more = bool(response.get("has_more"))
    return data, has_more


def _paginate_all_products(sc, limit: int, cursor: str | None):
    """
    Return products from both active and archived sets while maintaining a single cursor.
    Cursor format: "<segment>:<last_id>" where segment is "active" or "archived".
    """
    segment = "active"
    starting_after = None
    if cursor:
        parts = cursor.split(":", 1)
        if len(parts) == 2 and parts[0] in ("active", "archived"):
            segment = parts[0]
            starting_after = parts[1] or None
        else:
            starting_after = cursor

    results = []
    has_more = False
    next_cursor = None

    while segment and len(results) < limit:
        remaining = limit - len(results)
        active_flag = True if segment == "active" else False
        data, seg_has_more = _list_products_segment(
            sc,
            limit=remaining,
            starting_after=starting_after,
            active_flag=active_flag,
        )
        results.extend(data)

        if seg_has_more:
            has_more = True
            if data:
                next_cursor = f"{segment}:{data[-1]['id']}"
            else:
                next_cursor = f"{segment}:{starting_after or ''}"
            break

        # Exhausted this segment, move to the next one (active -> archived)
        if segment == "active":
            segment = "archived"
            starting_after = None
        else:
            segment = None
        starting_after = None

    if segment == "archived" and len(results) >= limit and not has_more:
        # We filled the page with active products; signal that archived remain.
        has_more = True
        next_cursor = "archived:"

    return results, has_more, next_cursor


def _list_and_filter_products(sc, limit, cursor, filters):
    """
    Fallback method when Stripe Search API is not available.
    Fetches products using list API and filters client-side.
    """
    logger.info("Using client-side filtering (Search API not available)")
    
    # Fetch more products than requested to account for filtering
    fetch_limit = min(limit * 5, 100)  # Fetch 5x as many to filter from
    
    params = {"limit": fetch_limit}
    if cursor:
        params["starting_after"] = cursor
    
    # Apply status filter at API level if not "all"
    status_val = filters["status"]
    if status_val == "active":
        params["active"] = True
    elif status_val == "archived":
        params["active"] = False
    # "all" - don't set active parameter
    
    try:
        response = sc.Product.list(**params)
        all_data = response.get("data", [])
    except Exception as e:
        logger.error(f"Failed to list products: {e}")
        return [], False, None
    
    # Client-side filtering
    search_term = filters["search"].lower() if filters["search"] else ""
    metadata_key = filters["metadata_key"]
    metadata_value = filters["metadata_value"]
    
    filtered_data = []
    for product in all_data:
        # Search filter
        if search_term:
            name_match = search_term in (product.get("name") or "").lower()
            desc_match = search_term in (product.get("description") or "").lower()
            id_match = search_term in (product.get("id") or "").lower()
            
            if not (name_match or desc_match or id_match):
                continue
        
        # Metadata filter
        if metadata_key and metadata_value:
            product_metadata = product.get("metadata") or {}
            if product_metadata.get(metadata_key) != metadata_value:
                continue
        
        filtered_data.append(product)
    
    # Return up to limit results
    result_data = filtered_data[:limit]
    
    # Check if there are more results
    has_more = len(filtered_data) > limit or response.get("has_more")
    next_cursor = result_data[-1]["id"] if result_data and has_more else None
    
    logger.info(f"Client-side filtering: {len(all_data)} fetched, {len(filtered_data)} matched, {len(result_data)} returned")
    
    return result_data, has_more, next_cursor


def _fetch_products_with_filters(
    client_id: str,
    limit: int = DEFAULT_PRODUCT_LIMIT,
    cursor: str | None = None,
    status: str = "active",
    search: str | None = None,
    metadata_key: str | None = None,
    metadata_value: str | None = None,
    event = None,
):
    """Fetch products from Stripe with filtering/search/pagination."""
    if not client_id:
        raise ValueError("clientID is required")

    limit = max(1, min(int(limit), MAX_PRODUCT_LIMIT))
    normalized_status = (status or "active").strip().lower()
    if normalized_status == "inactive":
        normalized_status = "archived"
    if normalized_status not in ("active", "archived", "all"):
        normalized_status = "active"

    filters = {
        "status": normalized_status,
        "search": (search or "").strip(),
        "metadata_key": (metadata_key or "").strip(),
        "metadata_value": (metadata_value or "").strip(),
    }

    sc, error = _get_stripe_client(client_id, event=event)
    if not sc:
        raise ValueError(error or "Unable to initialize Stripe client")

    use_search = bool(
        filters["search"] or (filters["metadata_key"] and filters["metadata_value"])
    )

    def build_query():
        clauses = []
        status_val = filters["status"]
        if status_val == "active":
            clauses.append("active:'true'")
        elif status_val == "archived":
            clauses.append("active:'false'")
        elif status_val == "all":
            clauses.append("(active:'true' OR active:'false')")

        if filters["search"]:
            escaped = _sanitize_search_term(filters["search"])
            field_clauses = [
                f"name~'{escaped}*'",
                f"description~'{escaped}*'",
                f"id:'{escaped}'",
            ]
            clauses.append("(" + " OR ".join(field_clauses) + ")")

        if filters["metadata_key"] and filters["metadata_value"]:
            key = _sanitize_metadata_key(filters["metadata_key"])
            if key:
                value = _sanitize_search_term(filters["metadata_value"])
                clauses.append(f"metadata['{key}']:'{value}'")

        if not clauses:
            # Stripe search requires at least one term, default to active true
            clauses.append("active:'true'")
        return " AND ".join(clauses)

    params = {"limit": limit}
    products = []
    has_more = False
    next_cursor = None

    if use_search:
        # Try using Stripe Search API first
        try:
            query = build_query()
            params = {"query": query, "limit": limit}
            if cursor:
                params["page"] = cursor
            
            logger.info(f"Attempting Stripe search with query: {query}")
            response = sc.Product.search(**params)
            data = response.get("data", [])
            next_cursor = response.get("next_page")
            has_more = bool(next_cursor)
            
        except stripe.error.InvalidRequestError as e:
            # Search API not available or query syntax error
            error_msg = str(e)
            logger.warning(f"Stripe search failed: {error_msg}")
            
            # Fall back to list API with client-side filtering
            logger.info("Falling back to list API with client-side filtering")
            data, has_more, next_cursor = _list_and_filter_products(
                sc, limit, cursor, filters
            )
                
        except Exception as e:
            logger.error(f"Unexpected error during Stripe search: {e}", exc_info=True)
            # Fall back to list API
            logger.warning("Falling back to list API due to unexpected error")
            data, has_more, next_cursor = _list_and_filter_products(
                sc, limit, cursor, filters
            )
    elif filters["status"] == "all":
        data, has_more, next_cursor = _paginate_all_products(sc, limit=limit, cursor=cursor)
    else:
        params = {"limit": limit}
        if cursor:
            params["starting_after"] = cursor
        status_val = filters["status"]
        if status_val == "active":
            params["active"] = True
        elif status_val == "archived":
            params["active"] = False
        response = sc.Product.list(**params)
        data = response.get("data", [])
        has_more = bool(response.get("has_more"))
        if has_more and data:
            next_cursor = data[-1]["id"]

    for sp in data:
        try:
            prices_response = sc.Price.list(product=sp["id"], limit=100)
            prices = [dict(p) for p in prices_response.get("data", [])]
            product_obj = _build_product_object(dict(sp), prices)
            products.append(product_obj)
        except Exception as exc:
            logger.warning(f"Failed to process product {sp.get('id')}: {exc}")

    return {
        "products": products,
        "hasMore": has_more,
        "nextCursor": next_cursor,
        "status": filters["status"],
        "usedSearch": use_search,
    }


def _admin_get_products(client_id: str, event=None):
    """
    Backwards-compatible helper used by other modules (e.g., offers.py).
    Returns the first page of active products only.
    """
    logger.info(f"_admin_get_products called for client: {client_id}")
    result = _fetch_products_with_filters(
        client_id,
        limit=25,
        status="active",
        search=None,
        metadata_key=None,
        metadata_value=None,
        cursor=None,
        event=event,
    )
    return result.get("products", [])
    

def _admin_get_product_detail(event):
    """
    GET /admin/products/{product_id}
    Also supports ?product_id=... as a fallback, and clientID via header or query.
    Returns: { product: {...}, prices: [...] }
    """
    headers = event.get("headers") or {}
    qs = event.get("queryStringParameters") or {}
    path_params = event.get("pathParameters") or {}

    # product_id from path or query
    product_id = path_params.get("product_id") or qs.get("product_id")
    if not product_id:
        return _resp(400, {"error": "Missing product_id"})

    # clientID from header or query
    client_id = headers.get("X-Client-Id") or headers.get("x-client-id") or qs.get("clientID")
    if not client_id:
        return _resp(400, {"error": "Missing clientID (header X-Client-Id or query clientID)"})

    # Stripe client (uses your existing helper + KMS decrypt)
    sc, err = _get_stripe_client(client_id)
    if err or not sc:
        return _resp(400, {"error": f"Unable to init Stripe: {err or 'unknown error'}"})

    try:
        # Fetch product (expand default_price so callers get that object too)
        s_product = sc.Product.retrieve(product_id, expand=["default_price"])

        # Fetch all prices for this product (paginate)
        prices = []
        params = {"product": product_id, "limit": 100, "active": None}  # include both active/inactive
        while True:
            page = sc.Price.list(**params)
            for p in page.get("data", []):
                prices.append({
                    "id": p.get("id"),
                    "active": p.get("active"),
                    "currency": p.get("currency"),
                    "unit_amount": p.get("unit_amount"),
                    "type": p.get("type"),
                    "nickname": p.get("nickname"),
                    "recurring": p.get("recurring"),       # interval, interval_count, usage_type, etc.
                    "metadata": p.get("metadata") or {},
                    "transform_quantity": p.get("transform_quantity"),
                    "billing_scheme": p.get("billing_scheme"),
                    "tax_behavior": p.get("tax_behavior"),
                    "lookup_key": p.get("lookup_key"),
                    "created": p.get("created"),
                })
            if not page.get("has_more"):
                break
            params["starting_after"] = page["data"][-1]["id"]

        # Build product payload similar to /admin/products, but focused on one product
        product_payload = {
            "id": s_product.get("id"),
            "active": s_product.get("active"),
            "name": s_product.get("name"),
            "description": s_product.get("description"),
            "images": s_product.get("images") or [],
            "metadata": s_product.get("metadata") or {},
            "default_price": s_product.get("default_price"),
            "shippable": s_product.get("shippable"),
            "statement_descriptor": s_product.get("statement_descriptor"),
            "unit_label": s_product.get("unit_label"),
            "updated": s_product.get("updated"),
            "created": s_product.get("created"),
            "url": s_product.get("url"),
            "package_dimensions": s_product.get("package_dimensions"),
        }

        return _resp(200, {"product": product_payload, "prices": prices})

    except Exception as e:
        logger.exception("Failed to retrieve product detail from Stripe")
        return _resp(500, {"error": f"Stripe error: {str(e)}"})


def _admin_create_product(event):
    """
    POST /admin/products
    Creates a new product in Stripe with prices.
    """
    headers = event.get("headers") or {}
    qs = event.get("queryStringParameters") or {}
    
    # Get clientID
    client_id = headers.get("X-Client-Id") or headers.get("x-client-id") or qs.get("clientID")
    if not client_id:
        return _resp(400, {"error": "Missing clientID"})
    
    # Parse body
    try:
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        product_data = json.loads(body)
    except Exception as e:
        return _resp(400, {"error": f"Invalid JSON body: {e}"})
    
    # Get Stripe client
    sc, err = _get_stripe_client(client_id, event=event)
    if err or not sc:
        return _resp(400, {"error": f"Unable to init Stripe: {err or 'unknown error'}"})
    
    try:
        logger.info(f"Creating product for client {client_id}")
        
        # Build product payload
        product_params = {
            "name": product_data.get("name"),
            "description": product_data.get("description", ""),
            "active": product_data.get("active", True),
            "images": product_data.get("images", []),
        }
        
        # Add metadata (mirror shipping dims for backwards compatibility)
        metadata = {}
        metadata_keys = [
            "product_type",
            "product_category",
            "upsell_product_id",
            "upsell_price_id",
            "upsell_offer_text",
            "hero_title",
            "hero_subtitle",
            "benefits",
            "guarantee",
            "package_length",
            "package_width",
            "package_height",
            "package_weight",
        ]
        for key in metadata_keys:
            value = product_data.get(key)
            if isinstance(value, float) and math.isnan(value):
                value = None
            if value not in (None, "", []):
                metadata[key] = str(value)
        
        if metadata:
            product_params["metadata"] = metadata
        
        # Package dimensions
        package_dimensions = {}
        for key in ["length", "width", "height", "weight"]:
            field_name = f"package_{key}"
            if product_data.get(field_name):
                package_dimensions[key] = product_data[field_name]
        
        if package_dimensions:
            product_params["package_dimensions"] = package_dimensions
        
        # Create product
        product = sc.Product.create(**product_params)
        logger.info(f"Created product {product.id}")
        
        # Create prices
        prices = product_data.get("prices", [])
        created_prices = []
        default_price_id = None
        
        for price_data in prices:
            price_params = {
                "product": product.id,
                "unit_amount": price_data.get("unit_amount"),
                "currency": price_data.get("currency", "usd"),
            }
            
            if price_data.get("nickname"):
                price_params["nickname"] = price_data["nickname"]
            
            if price_data.get("metadata"):
                price_params["metadata"] = price_data["metadata"]
            
            price = sc.Price.create(**price_params)
            created_prices.append(price.id)
            
            # Check if this should be the default
            if price_data.get("metadata", {}).get("is_default") == "true":
                default_price_id = price.id
            
            logger.info(f"Created price {price.id} for product {product.id}")
        
        # Set default price if specified
        if default_price_id:
            sc.Product.modify(product.id, default_price=default_price_id)
            logger.info(f"Set default price {default_price_id} for product {product.id}")
        
        return _resp(200, {
            "success": True,
            "product": {
                "id": product.id,
                "name": product.name,
                "prices": created_prices
            }
        })
        
    except stripe.error.InvalidRequestError as e:
        logger.error(f"Invalid request creating product: {e}")
        return _resp(400, {"error": str(e)})
    except Exception as e:
        logger.exception("Failed to create product")
        return _resp(500, {"error": f"Failed to create product: {str(e)}"})


def _admin_update_product(event):
    """
    PUT /admin/products/{product_id}
    Updates an existing product in Stripe.
    """
    headers = event.get("headers") or {}
    qs = event.get("queryStringParameters") or {}
    path_params = event.get("pathParameters") or {}
    
    # Get product_id
    product_id = path_params.get("product_id") or qs.get("product_id")
    if not product_id:
        return _resp(400, {"error": "Missing product_id"})
    
    # Get clientID
    client_id = headers.get("X-Client-Id") or headers.get("x-client-id") or qs.get("clientID")
    if not client_id:
        return _resp(400, {"error": "Missing clientID"})
    
    # Parse body
    try:
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        product_data = json.loads(body)
    except Exception as e:
        return _resp(400, {"error": f"Invalid JSON body: {e}"})
    
    # Get Stripe client
    sc, err = _get_stripe_client(client_id, event=event)
    if err or not sc:
        return _resp(400, {"error": f"Unable to init Stripe: {err or 'unknown error'}"})
    
    try:
        logger.info(f"Updating product {product_id} for client {client_id}")
        
        # Build update params
        update_params = {}
        
        for key in ["name", "description", "active", "images"]:
            if key in product_data:
                update_params[key] = product_data[key]
        
        # Update metadata (preserve legacy fields such as package_* in metadata)
        metadata = {}
        metadata_keys = [
            "product_type",
            "product_category",
            "upsell_product_id",
            "upsell_price_id",
            "upsell_offer_text",
            "hero_title",
            "hero_subtitle",
            "benefits",
            "guarantee",
            "package_length",
            "package_width",
            "package_height",
            "package_weight",
        ]
        for key in metadata_keys:
            if key in product_data:
                value = product_data.get(key)
                if isinstance(value, float) and math.isnan(value):
                    value = None
                if value in (None, "", []):
                    metadata[key] = ""
                else:
                    metadata[key] = str(value)
        
        if metadata:
            update_params["metadata"] = metadata
        
        # Package dimensions
        package_dimensions = {}
        for key in ["length", "width", "height", "weight"]:
            field_name = f"package_{key}"
            if field_name in product_data:
                package_dimensions[key] = product_data[field_name]
        
        if package_dimensions:
            update_params["package_dimensions"] = package_dimensions
        
        # Update product
        product = sc.Product.modify(product_id, **update_params)
        logger.info(f"Successfully updated product {product_id}")
        
        return _resp(200, {
            "success": True,
            "product": {
                "id": product.id,
                "name": product.name,
                "active": product.active
            }
        })
        
    except stripe.error.InvalidRequestError as e:
        logger.error(f"Invalid request updating product {product_id}: {e}")
        return _resp(400, {"error": str(e)})
    except Exception as e:
        logger.exception(f"Failed to update product {product_id}")
        return _resp(500, {"error": f"Failed to update product: {str(e)}"})


def _admin_archive_product(event):
    """
    DELETE /admin/products/{product_id}
    Archives (sets active=false) a product in Stripe.
    """
    headers = event.get("headers") or {}
    qs = event.get("queryStringParameters") or {}
    path_params = event.get("pathParameters") or {}
    
    # Get product_id
    product_id = path_params.get("product_id") or qs.get("product_id")
    if not product_id:
        return _resp(400, {"error": "Missing product_id"})
    
    # Get clientID
    client_id = headers.get("X-Client-Id") or headers.get("x-client-id") or qs.get("clientID")
    if not client_id:
        return _resp(400, {"error": "Missing clientID"})
    
    # Get Stripe client
    sc, err = _get_stripe_client(client_id, event=event)
    if err or not sc:
        return _resp(400, {"error": f"Unable to init Stripe: {err or 'unknown error'}"})
    
    try:
        logger.info(f"Archiving product {product_id} for client {client_id}")
        product = sc.Product.modify(product_id, active=False)
        logger.info(f"Successfully archived product {product_id}")
        
        return _resp(200, {
            "success": True,
            "product": {
                "id": product.id,
                "active": product.active,
                "name": product.name
            }
        })
        
    except stripe.error.InvalidRequestError as e:
        logger.error(f"Invalid request archiving product {product_id}: {e}")
        return _resp(400, {"error": str(e)})
    except Exception as e:
        logger.exception(f"Failed to archive product {product_id}")
        return _resp(500, {"error": f"Failed to archive product: {str(e)}"})


def _admin_update_price(event):
    """
    PUT /admin/prices/{price_id}
    Updates a price (typically for setting metadata or archiving).
    """
    headers = event.get("headers") or {}
    qs = event.get("queryStringParameters") or {}
    path_params = event.get("pathParameters") or {}
    
    # Get price_id
    price_id = path_params.get("price_id") or qs.get("price_id")
    if not price_id:
        return _resp(400, {"error": "Missing price_id"})
    
    # Get clientID
    client_id = headers.get("X-Client-Id") or headers.get("x-client-id") or qs.get("clientID")
    if not client_id:
        return _resp(400, {"error": "Missing clientID"})
    
    # Parse body
    try:
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        price_data = json.loads(body)
    except Exception as e:
        return _resp(400, {"error": f"Invalid JSON body: {e}"})
    
    # Get Stripe client
    sc, err = _get_stripe_client(client_id, event=event)
    if err or not sc:
        return _resp(400, {"error": f"Unable to init Stripe: {err or 'unknown error'}"})
    
    try:
        logger.info(f"Updating price {price_id} for client {client_id}")
        
        # Build update params
        update_params = {}
        
        if "active" in price_data:
            update_params["active"] = price_data["active"]
        
        if "metadata" in price_data:
            update_params["metadata"] = price_data["metadata"]
        
        if "nickname" in price_data:
            update_params["nickname"] = price_data["nickname"]
        
        # Update price
        price = sc.Price.modify(price_id, **update_params)
        logger.info(f"Successfully updated price {price_id}")
        
        return _resp(200, {
            "success": True,
            "price": {
                "id": price.id,
                "active": price.active,
                "metadata": price.metadata
            }
        })
        
    except stripe.error.InvalidRequestError as e:
        logger.error(f"Invalid request updating price {price_id}: {e}")
        return _resp(400, {"error": str(e)})
    except Exception as e:
        logger.exception(f"Failed to update price {price_id}")
        return _resp(500, {"error": f"Failed to update price: {str(e)}"})


def lambda_handler(event, context):
    """Main Lambda handler for products endpoint"""
    method = event.get("httpMethod")
    resource = event.get("resource") or event.get("path")
    path = event.get("path") or ""
    
    # Handle OPTIONS for CORS
    if method == "OPTIONS":
        return _resp(200, {"ok": True})
    
    # GET /admin/products
    if method == "GET" and resource == "/admin/products":
        client_id = _q(event, "clientID") or _q(event, "clientId")
        
        if not client_id:
            return _resp(400, {"error": "Missing clientID parameter"})
        
        raw_limit = _q(event, "limit")
        try:
            limit = int(raw_limit) if raw_limit is not None else DEFAULT_PRODUCT_LIMIT
        except ValueError:
            limit = DEFAULT_PRODUCT_LIMIT
        limit = max(1, min(limit, MAX_PRODUCT_LIMIT))

        cursor = _q(event, "cursor")
        status = (_q(event, "status") or "active").strip().lower()
        if status not in ("active", "archived", "inactive", "all"):
            status = "active"
        elif status == "inactive":
            status = "archived"

        search = (_q(event, "search") or "").strip()
        metadata_key = (_q(event, "metadataKey") or "").strip()
        metadata_value = (_q(event, "metadataValue") or "").strip()

        logger.info(f"GET /admin/products for client: {client_id} status={status} limit={limit}")
        try:
            result = _fetch_products_with_filters(
                client_id=client_id,
                limit=limit,
                cursor=cursor,
                status=status,
                search=search or None,
                metadata_key=metadata_key or None,
                metadata_value=metadata_value or None,
                event=event,
            )
            return _resp(200, result)
        except ValueError as exc:
            logger.error(f"Bad request fetching products: {exc}")
            return _resp(400, {"error": str(exc)})
        except Exception as exc:
            logger.exception("Error fetching products")
            return _resp(500, {"error": "Failed to fetch products"})
    
    # POST /admin/products - Create new product
    if method == "POST" and resource == "/admin/products":
        return _admin_create_product(event)
        
    # GET /admin/products/{product_id}
    if method == "GET" and (
        resource == "/admin/products/{product_id}" or
        (path.startswith("/admin/products/") and (event.get("pathParameters") or {}).get("product_id"))
    ):
        return _admin_get_product_detail(event)
    
    # PUT /admin/products/{product_id} - Update product
    if method == "PUT" and (
        resource == "/admin/products/{product_id}" or
        (path.startswith("/admin/products/") and (event.get("pathParameters") or {}).get("product_id"))
    ):
        return _admin_update_product(event)
    
    # DELETE /admin/products/{product_id} - Archive product
    if method == "DELETE" and (
        resource == "/admin/products/{product_id}" or
        (path.startswith("/admin/products/") and (event.get("pathParameters") or {}).get("product_id"))
    ):
        return _admin_archive_product(event)
    
    # PUT /admin/prices/{price_id} - Update price
    if method == "PUT" and (
        resource == "/admin/prices/{price_id}" or
        (path.startswith("/admin/prices/") and (event.get("pathParameters") or {}).get("price_id"))
    ):
        return _admin_update_price(event)
    
    # GET /public/prices - for public-facing price display
    if method == "GET" and resource == "/public/prices":
        client_id = _q(event, "clientID") or _q(event, "clientId")
        product_ids = _q(event, "product_ids", "").split(",")
        product_ids = [pid.strip() for pid in product_ids if pid.strip()]
        
        if not client_id:
            return _resp(400, {"error": "Missing clientID parameter"})
        
        # If specific products requested, filter
        products = _admin_get_products(client_id)
        
        if product_ids:
            products = [p for p in products if p.get("id") in product_ids]
        
        # Return simplified price info for public use
        price_info = []
        for product in products:
            for price in product.get("prices", []):
                price_info.append({
                    "product_id": product["id"],
                    "product_name": product["name"],
                    "price_id": price["id"],
                    "unit_amount": price["unit_amount"],
                    "currency": price["currency"],
                    "recurring": price.get("recurring")
                })
        
        return _resp(200, {"prices": price_info})
    
    # Return 404 for unmatched routes
    return _resp(404, {"error": "Not Found"})
