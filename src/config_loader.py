# config_loader.py
# Strict loader for app configuration (DynamoDB: app-config-<env>)

import os
import time
import boto3
from decimal import Decimal
from typing import Any, Dict, Optional, List
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

# ---------------- Strict env (no fallbacks for table names) ------------------

class ConfigError(RuntimeError):
    pass

def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise ConfigError(f"Missing required environment variable: {name}")
    return v

ENVIRONMENT      = _req("ENVIRONMENT")        # "dev" | "prod"
APP_CONFIG_TABLE = _req("APP_CONFIG_TABLE")   # e.g., "app-config-dev"

REGION  = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
TTL_SEC = int(os.environ.get("CONFIG_CACHE_TTL_SECONDS", "60"))  # cache TTL for this module only

# ---------------- AWS clients ------------------------------------------------

_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_table    = _dynamodb.Table(APP_CONFIG_TABLE)

# ---------------- In-memory cache -------------------------------------------

_cache_data: Optional[Dict[str, Any]] = None
_cache_expires_at: float = 0.0

# ---------------- Utilities --------------------------------------------------

def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj

def _scan_env(env: str) -> List[Dict[str, Any]]:
    """Scan config rows for a given environment key (small table, safe to scan)."""
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
    global_items = _scan_env("global")
    env_items    = _scan_env(ENVIRONMENT)

    cfg: Dict[str, Any] = {}
    for it in global_items:
        cfg[it["config_key"]] = it.get("value")
    for it in env_items:
        cfg[it["config_key"]] = it.get("value")

    # Convenience fields for frontends/services
    cfg["environment"] = ENVIRONMENT
    # If you store api_base_url per-env, expose apiBase alias:
    if isinstance(cfg.get("api_base_url"), str):
        cfg["apiBase"] = cfg["api_base_url"]

    return _to_jsonable(cfg)

# ---------------- Public API -------------------------------------------------

def load_config(force: bool = False) -> Dict[str, Any]:
    """
    Load merged config (global + current ENVIRONMENT). Uses a short in-memory cache.
    Set force=True to bypass cache.
    """
    global _cache_data, _cache_expires_at
    now = time.time()
    if not force and _cache_data is not None and now < _cache_expires_at:
        return _cache_data

    try:
        cfg = _merge_global_and_env()
    except ClientError as e:
        raise ConfigError(f"DynamoDB error loading config: {e.response['Error'].get('Message','unknown')}")

    _cache_data = cfg
    _cache_expires_at = now + max(TTL_SEC, 1)
    return cfg

def get_value(key: str, default: Any = None, *, required: bool = False) -> Any:
    """
    Get a single config value. When required=True, raise ConfigError if missing.
    """
    cfg = load_config()
    if key in cfg:
        return cfg[key]
    if required:
        raise ConfigError(f"Missing required config key: {key} (env={ENVIRONMENT}, table={APP_CONFIG_TABLE})")
    return default

def invalidate_cache() -> None:
    """Clear the in-memory cache so the next call re-reads DynamoDB."""
    global _cache_data, _cache_expires_at
    _cache_data = None
    _cache_expires_at = 0.0

def resolved_source() -> Dict[str, str]:
    """For diagnostics/logging."""
    return {"environment": ENVIRONMENT, "table": APP_CONFIG_TABLE, "region": REGION}
