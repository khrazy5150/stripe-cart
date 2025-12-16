# lambda_page_generator.py
# Strict, env-driven landing page renderer/uploader (preview & publish)

import os
import json
import base64
import boto3
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, timezone
from botocore.exceptions import ClientError

# ---------------- Strict env (no fallbacks for table names) ------------------

class ConfigError(RuntimeError):
    pass

def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise ConfigError(f"Missing required environment variable: {name}")
    return v

ENVIRONMENT       = _req("ENVIRONMENT")         # "dev" | "prod"
STRIPE_KEYS_TABLE = _req("STRIPE_KEYS_TABLE")   # e.g., "stripe-keys-dev"
APP_CONFIG_TABLE  = _req("APP_CONFIG_TABLE")    # e.g., "app-config-dev"

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"

# ---------------- AWS clients ------------------------------------------------

_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_table    = _dynamodb.Table(STRIPE_KEYS_TABLE)

_s3  = boto3.client("s3", region_name=REGION)
_cf  = boto3.client("cloudfront", region_name="us-east-1")  # CF is global; API lives in us-east-1

# ---------------- Light config loader (reads from app-config table) ----------

_cfg_table = _dynamodb.Table(APP_CONFIG_TABLE)

def _scan_cfg(env_key: str) -> List[Dict[str, Any]]:
    from boto3.dynamodb.conditions import Attr
    items: List[Dict[str, Any]] = []
    fe = Attr("environment").eq(env_key)
    kwargs = {"FilterExpression": fe}
    while True:
        resp = _cfg_table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items

def load_app_config() -> Dict[str, Any]:
    """Merge global + current ENVIRONMENT into a flat dict."""
    cfg: Dict[str, Any] = {}
    for it in _scan_cfg("global"):
        cfg[it["config_key"]] = it.get("value")
    for it in _scan_cfg(ENVIRONMENT):
        cfg[it["config_key"]] = it.get("value")
    cfg["environment"] = ENVIRONMENT
    return cfg

def cfg_get(cfg: Dict[str, Any], key: str, *, required: bool = False, default=None):
    if key in cfg and cfg[key] is not None:
        return cfg[key]
    if required:
        raise ConfigError(f"Missing required config key '{key}' in app-config-{ENVIRONMENT}")
    return default

# ---------------- HTTP helpers ----------------------------------------------

def _cors_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,Stripe-Signature,X-Client-Id,X-Offer-Name",
        "Access-Control-Allow-Methods": "OPTIONS,POST",
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

def _extract_client_id(event) -> Optional[str]:
    headers = event.get("headers") or {}
    cid = headers.get("X-Client-Id") or headers.get("x-client-id")
    if not cid:
        qs = event.get("queryStringParameters") or {}
        if isinstance(qs, dict):
            cid = qs.get("clientID") or qs.get("clientId") or qs.get("client_id")
    if not cid:
        try:
            body = _parse_json_body(event)
            cid = body.get("clientID") or body.get("clientId") or body.get("client_id")
        except Exception:
            pass
    return cid

# ---------------- Data access ------------------------------------------------

def _get_tenant_item(client_id: str) -> Dict[str, Any]:
    resp = _table.get_item(Key={"clientID": client_id})
    return resp.get("Item") or {}

def _find_landing_page(item: Dict[str, Any], landing_page_id: str) -> Optional[Dict[str, Any]]:
    for lp in item.get("landing_pages", []) or []:
        if lp.get("landing_page_id") == landing_page_id:
            return lp
    return None

# ---------------- Rendering --------------------------------------------------

def _render_html(lp: Dict[str, Any], tenant: Dict[str, Any]) -> str:
    """
    Minimal example renderer. Replace with your Jinja/Mako/template system if needed.
    """
    title = lp.get("hero_title") or lp.get("page_name") or "Landing Page"
    subtitle = lp.get("hero_subtitle", "")
    guarantee = lp.get("guarantee", "")
    products = lp.get("products", [])

    # super-basic, self-contained HTML
    lines = [
        "<!doctype html>",
        "<html><head>",
        f"<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{title}</title>",
        "<style>body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;padding:24px;max-width:880px;margin:0 auto}header{margin-bottom:16px}h1{margin:0 0 8px 0}ul{padding-left:18px}footer{margin-top:40px;font-size:12px;color:#666}</style>",
        "</head><body>",
        "<header>",
        f"<h1>{title}</h1>",
        f"<p>{subtitle}</p>" if subtitle else "",
        f"<p><em>{guarantee}</em></p>" if guarantee else "",
        "</header>",
        "<section><h2>Products</h2><ul>",
    ]
    for p in products:
        name = p.get("name") or p.get("product_name") or "Product"
        desc = p.get("description") or ""
        price = p.get("price_display") or p.get("price") or ""
        lines.append(f"<li><strong>{name}</strong> — {desc} <b>{price}</b></li>")
    lines.extend([
        "</ul></section>",
        "<footer>Generated by lambda_page_generator</footer>",
        "</body></html>"
    ])
    return "\n".join([s for s in lines if s != ""])

# ---------------- S3 + CloudFront -------------------------------------------

def _s3_put(bucket: str, key: str, html: str, *, cache_seconds: int, public: bool):
    extra = {
        "Bucket": bucket,
        "Key": key,
        "Body": html.encode("utf-8"),
        "ContentType": "text/html; charset=utf-8",
        "CacheControl": f"public, max-age={cache_seconds}" if public else "no-cache, no-store, must-revalidate",
    }
    if public:
        extra["ACL"] = "public-read"
    _s3.put_object(**extra)

def _cf_invalidate(distribution_id: str, paths: List[str]) -> str:
    caller_ref = f"lp-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    resp = _cf.create_invalidation(
        DistributionId=distribution_id,
        InvalidationBatch={
            "Paths": {"Quantity": len(paths), "Items": paths},
            "CallerReference": caller_ref,
        },
    )
    return resp["Invalidation"]["Id"]

# ---------------- Orchestration ---------------------------------------------

def _target_for_mode(cfg: Dict[str, Any], mode: str) -> Tuple[str, bool, int]:
    """
    Return (bucket_name, public, cache_seconds) for mode 'preview' or 'publish'.
    """
    if mode == "preview":
        bucket = cfg_get(cfg, "landing_page_preview_bucket", required=True)
        return bucket, True, 0  # immediate refresh
    if mode == "publish":
        bucket = cfg_get(cfg, "landing_page_bucket", required=True)
        # modest caching; adjust as needed
        return bucket, True, 300
    raise ConfigError("mode must be 'preview' or 'publish'")

def _s3_key_for(lp: Dict[str, Any], client_id: str) -> str:
    """
    Where to place the HTML in the bucket.
    Example: tenants/<client>/<seo>/index.html
    """
    seo = lp.get("seo_friendly_prefix") or lp.get("landing_page_id")
    return f"tenants/{client_id}/{seo}/index.html"

def _public_url(cfg: Dict[str, Any], lp: Dict[str, Any], client_id: str) -> str:
    """
    Construct a browser URL using offers_base_url pattern if present.
    Fallback: s3 website-style url is intentionally NOT returned (we assume CloudFront / site will front the bucket).
    """
    base = cfg_get(cfg, "offers_base_url", required=True)  # e.g., "https://juniorbay.com/tenants/"
    seo = lp.get("seo_friendly_prefix") or lp.get("landing_page_id")
    if not base.endswith("/"):
        base += "/"
    return f"{base}{client_id}/{seo}/"

def _maybe_invalidate(cfg: Dict[str, Any], url_path: str) -> Optional[str]:
    if str(cfg_get(cfg, "landing_page_invalidation_enabled", default="true")).lower() != "true":
        return None
    dist_id = cfg_get(cfg, "landing_page_cloudfront_id", default=None)
    if not dist_id:
        return None
    inv_id = _cf_invalidate(dist_id, [url_path.rstrip("/") + "/index.html"])
    return inv_id

# ---------------- Lambda handler --------------------------------------------

def lambda_handler(event, context):
    method = (event.get("httpMethod") or "").upper()
    path   = event.get("path") or ""

    # CORS preflight
    if method == "OPTIONS":
        return _ok({"ok": True})

    if method != "POST":
        return _bad("Only POST is supported.", 405)

    # Modes supported:
    # 1) POST /admin/landing-pages/generate  { clientId, landing_page_id, mode: "preview"|"publish" }
    # 2) POST /admin/landing-pages/preview/{id}   (clientId via header/query/body)
    # 3) POST /admin/landing-pages/publish/{id}   (clientId via header/query/body)

    body = {}
    try:
        body = _parse_json_body(event)
    except ValueError as e:
        return _bad(str(e))

    client_id = _extract_client_id(event) or body.get("clientId") or body.get("clientID")
    if not client_id:
        return _bad("clientID required (X-Client-Id header or clientId query/body)")

    mode = (body.get("mode") or "").lower()
    landing_page_id = body.get("landing_page_id")

    # Path-based mode/ID if not in body
    if not mode or not landing_page_id:
        if path.endswith("/generate"):
            pass  # require both in body
        elif "/preview/" in path:
            mode = mode or "preview"
            landing_page_id = landing_page_id or path.rsplit("/", 1)[-1]
        elif "/publish/" in path:
            mode = mode or "publish"
            landing_page_id = landing_page_id or path.rsplit("/", 1)[-1]

    if mode not in ("preview", "publish"):
        return _bad("mode must be 'preview' or 'publish'")
    if not landing_page_id:
        return _bad("landing_page_id is required")

    # Load config
    try:
        cfg = load_app_config()
    except Exception as e:
        return _bad(f"Failed to load app-config: {e}", 500)

    # Find the landing page record
    try:
        tenant = _get_tenant_item(client_id)
        if not tenant:
            return _bad("Client not found", 404)
        lp = _find_landing_page(tenant, landing_page_id)
        if not lp:
            return _bad("Landing page not found", 404)
    except ClientError as e:
        return _bad(f"DynamoDB error: {e.response['Error'].get('Message','unknown')}", 500)

    # Render
    html = _render_html(lp, tenant)

    # Upload
    try:
        bucket, public, cache_seconds = _target_for_mode(cfg, mode)
        key = _s3_key_for(lp, client_id)
        _s3_put(bucket, key, html, cache_seconds=cache_seconds, public=public)
    except ConfigError as e:
        return _bad(str(e), 500)
    except ClientError as e:
        return _bad(f"S3 error: {e.response['Error'].get('Message','unknown')}", 500)

    # Public URL and optional CloudFront invalidation
    url = _public_url(cfg, lp, client_id)
    inv = None
    try:
        # Convert the public URL to a path for invalidation (everything after domain)
        from urllib.parse import urlparse
        p = urlparse(url)
        path_for_inv = p.path if p.path else "/"
        inv = _maybe_invalidate(cfg, path_for_inv)
    except ClientError as e:
        # Invalidation is optional; return success with a warning
        return _ok({
            "success": True,
            "mode": mode,
            "environment": ENVIRONMENT,
            "bucket": bucket,
            "key": key,
            "url": url,
            "invalidation_error": e.response["Error"].get("Message", "unknown"),
        })

    return _ok({
        "success": True,
        "mode": mode,
        "environment": ENVIRONMENT,
        "bucket": bucket,
        "key": key,
        "url": url,
        "invalidation_id": inv
    })
