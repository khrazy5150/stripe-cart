# config_api.py

import os
import json
import base64
import boto3
from typing import Dict, Any, List
from decimal import Decimal
from datetime import datetime, timezone
from botocore.exceptions import ClientError

# ---------- Strict env (no fallbacks) ----------------------------------------

class ConfigError(RuntimeError):
    pass

def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise ConfigError(f"Missing required environment variable: {name}")
    return v

ENVIRONMENT      = _req("ENVIRONMENT")        # "dev" | "prod"
APP_CONFIG_TABLE = _req("APP_CONFIG_TABLE")   # e.g., "app-config-dev"
REGION           = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"

_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_table     = _dynamodb.Table(APP_CONFIG_TABLE)

# ---------- HTTP helpers -----------------------------------------------------

def _cors_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,Stripe-Signature,X-Client-Id,X-Offer-Name",
        "Access-Control-Allow-Methods": "OPTIONS,GET,PUT",
    }

def _ok(body: Dict[str, Any], status: int = 200) -> Dict[str, Any]:
    return {"statusCode": status, "headers": _cors_headers(), "body": json.dumps(_to_jsonable(body))}

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

def _to_jsonable(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj

# ---------- Data access ------------------------------------------------------

def _get_item(config_key: str, environment: str) -> Dict[str, Any]:
    resp = _table.get_item(Key={"config_key": config_key, "environment": environment})
    return resp.get("Item") or {}

def _put_item(config_key: str, environment: str, value: Any, description: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat()
    _table.put_item(
        Item={
            "config_key": config_key,
            "environment": environment,
            "value": value,
            "description": description or "",
            "updated_at": now,
            "updated_by": "config_api",
        }
    )

def _scan_env(env: str) -> List[Dict[str, Any]]:
    # Use a Scan with a FilterExpression on 'environment' (table is small / config-only)
    from boto3.dynamodb.conditions import Attr
    items: List[Dict[str, Any]] = []
    fe = Attr("environment").eq(env)
    kwargs = {"FilterExpression": fe}
    while True:
        resp = _table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items

def _merge_global_and_env() -> Dict[str, Any]:
    """
    Build a flat config dict for the frontend:
    - Start with all GLOBAL keys (environment == 'global')
    - Overlay ENVIRONMENT-specific keys (environment == ENVIRONMENT)
    """
    global_items = _scan_env("global")
    env_items    = _scan_env(ENVIRONMENT)

    cfg: Dict[str, Any] = {}
    for it in global_items:
        cfg[it["config_key"]] = it.get("value")
    for it in env_items:
        cfg[it["config_key"]] = it.get("value")

    # Add a few convenience fields commonly used by frontend
    cfg["environment"] = ENVIRONMENT
    # Expect api_base_url to be stored under config_key "api_base_url" for each env
    # (Your seed script already has this.)
    api_base = cfg.get("api_base_url")
    if isinstance(api_base, str):
        cfg["apiBase"] = api_base  # camelCase alias used by your JS

    return cfg

# ---------- Lambda handler ---------------------------------------------------

def lambda_handler(event, context):
    method = (event.get("httpMethod") or "").upper()
    path   = event.get("path") or ""

    # CORS preflight
    if method == "OPTIONS":
        return _ok({"ok": True})

    # Public config for frontend
    if path.endswith("/config") and method == "GET":
        try:
            cfg = _merge_global_and_env()
            return _ok(cfg)
        except ClientError as e:
            return _bad(f"DynamoDB error: {e.response['Error'].get('Message','unknown')}")

    # Admin: get all config rows for current env (optionally include global)
    if path.endswith("/admin/app-config") and method == "GET":
        try:
            qs = event.get("queryStringParameters") or {}
            include_global = (isinstance(qs, dict) and (qs.get("includeGlobal", "false").lower() == "true"))
            envs = [ENVIRONMENT] + (["global"] if include_global else [])
            rows: List[Dict[str, Any]] = []
            for env in envs:
                rows.extend(_scan_env(env))
            return _ok({"environment": ENVIRONMENT, "items": rows})
        except ClientError as e:
            return _bad(f"DynamoDB error: {e.response['Error'].get('Message','unknown')}")

    # Admin: upsert a single config row
    if path.endswith("/admin/app-config") and method == "PUT":
        try:
            body = _parse_json_body(event)
        except ValueError as e:
            return _bad(str(e))

        config_key  = body.get("config_key")
        environment = body.get("environment") or ENVIRONMENT  # allow explicit override, default to current env
        value       = body.get("value")
        description = body.get("description") or ""

        if not config_key:
            return _bad("config_key is required")
        if environment not in ("global", "dev", "prod"):
            return _bad("environment must be one of: global, dev, prod")
        if value is None:
            return _bad("value is required")

        try:
            _put_item(config_key, environment, value, description)
            # return the updated item
            item = _get_item(config_key, environment)
            return _ok({"success": True, "item": item})
        except ClientError as e:
            return _bad(f"DynamoDB error: {e.response['Error'].get('Message','unknown')}")

    return _bad("Unsupported route or method.", 405)
