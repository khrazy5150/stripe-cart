# lambda_edge_router.py
# Lambda@Edge (Viewer Request + optional Viewer Response) path/headers router
# Strict env: no table or service fallbacks.

import os
import json
import re

# ---------- Strict env (no fallbacks) ----------------------------------------

class ConfigError(RuntimeError):
    pass

def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise ConfigError(f"Missing required environment variable: {name}")
    return v

# The stage path you want the origin to see, e.g. "/prod" or "/dev"
STAGE_PREFIX = _req("STAGE_PREFIX")  # e.g., "/prod" (must start with '/')

# Header names you expect from clients (canonical case is fine; matching is case-insensitive)
TENANT_HEADER = os.environ.get("TENANT_HEADER", "X-Client-Id")
OFFER_HEADER  = os.environ.get("OFFER_HEADER", "X-Offer-Name")

# Whether to force HTTPS redirect at viewer (recommended true)
ENFORCE_HTTPS = os.environ.get("ENFORCE_HTTPS", "true").lower() == "true"

# Strip cookies from viewer requests to increase cache hit ratio
STRIP_COOKIES = os.environ.get("STRIP_COOKIES", "true").lower() == "true"

# Forward only these headers to the origin (case-insensitive compare)
FORWARD_HEADER_WHITELIST = [
    "Host", "CloudFront-Viewer-Address",  # Host is required
    TENANT_HEADER,
    OFFER_HEADER,
    "Authorization",
    "Stripe-Signature",
    "Content-Type",
    "Origin",
    "Accept",
    "Accept-Encoding",
    "Accept-Language",
    "User-Agent",
]

# Optional: remove these query params from cache key to avoid variance
DROP_QUERY_PARAMS = [ "utm_source", "utm_medium", "utm_campaign", "gclid", "fbclid" ]

# -----------------------------------------------------------------------------

def _hdr_dict_to_list(h: dict) -> list:
    """CloudFront headers are dict[str] -> [ {key, value}, ... ]. Ensure list form."""
    out = []
    for k, vals in h.items():
        for v in vals:
            out.append({"key": v.get("key", k), "value": v["value"]})
    return out

def _normalize_headers(headers: dict) -> dict:
    # CloudFront lowercase header keys; preserve original casing in 'key'
    norm = {}
    for k, vals in headers.items():
        lk = k.lower()
        norm[lk] = [{"key": vals[0].get("key", k), "value": vals[0]["value"]}]
    return norm

def _get_header(headers: dict, name: str) -> str | None:
    v = headers.get(name.lower())
    return v[0]["value"] if v else None

def _set_header(headers: dict, name: str, value: str):
    headers[name.lower()] = [{"key": name, "value": value}]

def _filter_headers(headers: dict) -> dict:
    allowed = {h.lower() for h in FORWARD_HEADER_WHITELIST}
    return {k: v for k, v in headers.items() if k in allowed}

def _drop_query_params(qs: str) -> str:
    if not qs:
        return ""
    if not DROP_QUERY_PARAMS:
        return qs
    pairs = []
    for part in qs.split("&"):
        if not part:
            continue
        k = part.split("=", 1)[0]
        if k in DROP_QUERY_PARAMS:
            continue
        pairs.append(part)
    return "&".join(pairs)

def _ensure_single_stage(path: str) -> str:
    """
    Ensure the path begins with exactly STAGE_PREFIX (e.g. '/prod'),
    removing duplicates if the viewer already included it.
    """
    if not STAGE_PREFIX.startswith("/"):
        raise ConfigError("STAGE_PREFIX must start with '/'")
    # Remove repeated stage occurrences
    # Example: if STAGE_PREFIX='/prod', '/prod/prod/foo' -> '/prod/foo'
    stage = STAGE_PREFIX.rstrip("/")
    pat = re.compile(rf"^(?:{re.escape(stage)})+(?=/|$)", re.IGNORECASE)
    # Strip all stage prefixes, then add one back
    stripped = pat.sub("", path)
    if not stripped.startswith("/"):
        stripped = "/" + stripped
    normalized = stage + stripped
    return normalized

def _is_https(request: dict) -> bool:
    # CloudFront sets 'cloudfront-forwarded-proto' to 'https' when appropriate
    hdrs = request.get("headers", {})
    proto = _get_header(hdrs, "cloudfront-forwarded-proto")
    return (proto or "").lower() == "https"

# ---------- Viewer Request handler -------------------------------------------

def viewer_request_handler(event, context):
    """
    - Optionally redirect to HTTPS
    - Enforce exactly one STAGE_PREFIX
    - Normalize/whitelist headers
    - Optionally strip cookies
    - Drop noisy query params that hurt cache hit rate
    """
    req = event["Records"][0]["cf"]["request"]
    headers = req.get("headers") or {}
    headers = _normalize_headers(headers)

    # Force HTTPS
    if ENFORCE_HTTPS and not _is_https(req):
        host = _get_header(headers, "host") or ""
        location = f"https://{host}{req.get('uri') or '/'}"
        qs = req.get("querystring")
        if qs:
            location += f"?{qs}"
        return {
            "status": "301",
            "statusDescription": "Moved Permanently",
            "headers": {
                "location": [{"key": "Location", "value": location}],
                "cache-control": [{"key": "Cache-Control", "value": "max-age=3600"}],
            },
        }

    # Normalize path so origin sees exactly one stage prefix
    old_uri = req.get("uri", "/")
    new_uri = _ensure_single_stage(old_uri)
    req["uri"] = new_uri

    # Forward only whitelisted headers
    req["headers"] = _filter_headers(headers)

    # Normalize tenant header to canonical case if present
    tenant_val = _get_header(req["headers"], TENANT_HEADER)
    if tenant_val:
        _set_header(req["headers"], TENANT_HEADER, tenant_val.strip())

    # Normalize offer header
    offer_val = _get_header(req["headers"], OFFER_HEADER)
    if offer_val:
        _set_header(req["headers"], OFFER_HEADER, offer_val.strip())

    # Optionally drop cookies (huge cache win for public endpoints)
    if STRIP_COOKIES and "cookie" in req["headers"]:
        del req["headers"]["cookie"]

    # Trim tracking params from query string (optional)
    req["querystring"] = _drop_query_params(req.get("querystring", ""))

    return req

# ---------- Viewer Response handler (optional security headers) --------------

SECURITY_HEADERS = {
    "strict-transport-security": "max-age=31536000; includeSubDomains; preload",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer-when-downgrade",
    "permissions-policy": "geolocation=(), microphone=(), camera=()",
}

def viewer_response_handler(event, context):
    resp = event["Records"][0]["cf"]["response"]
    headers = resp.get("headers") or {}
    # Add/overwrite security headers
    for k, v in SECURITY_HEADERS.items():
        headers[k] = [{"key": k.title(), "value": v}]
    resp["headers"] = headers
    return resp

# ---------- Entrypoints ------------------------------------------------------

# Configure these as separate Lambda@Edge behaviors if desired:
def handler(event, context):
    """Attach this as Viewer Request by default."""
    return viewer_request_handler(event, context)

def on_viewer_response(event, context):
    """Attach this as Viewer Response if you want security headers at edge."""
    return viewer_response_handler(event, context)
