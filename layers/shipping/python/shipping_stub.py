# layers/shipping/python/shipping_stub.py
"""
Compatibility wrapper around your original provider classes in shipping_providers.py.

Exposes a small, stable API used by shipping_api.py:
  - test_credentials(payload, secrets, environment) -> dict
  - get_rates(payload, secrets, environment) -> list[dict]

Delegates to shipping_providers.{ShippoProvider, EasyPostProvider, ShipStationProvider, EasyShipProvider}.
"""

from typing import Any, Dict, List, Optional

try:
    # Your original full library with provider classes
    import shipping_providers as core
except Exception as e:
    core = None

SUPPORTED = {"shippo", "easypost", "shipstation", "easyship"}
DEFAULT_CURRENCY = "USD"

# ---------------- Secrets mapping & validation ----------------

def _secret_key_names(provider: str) -> List[str]:
    # Return the secret field(s) expected in 'secrets' for each provider
    if provider == "shipstation":
        # ShipStation typically needs both key + secret
        return ["shipstation_api_key", "shipstation_api_secret"]
    return [f"{provider}_api_key"]

def _has_required_secrets(provider: str, secrets: Dict[str, Any]) -> bool:
    return all(secrets.get(k) for k in _secret_key_names(provider))

def _mask_tail(s: Optional[str], keep: int = 4) -> str:
    s = s or ""
    return "*" * max(0, len(s) - keep) + s[-keep:] if s else s

def _validate_format(provider: str, key: str) -> bool:
    # Lightweight format sanity checks (non-authoritative)
    if provider == "shippo":
        return isinstance(key, str) and key.startswith(("shippo_test_", "shippo_live_")) and len(key) > 20
    if provider == "easypost":
        return isinstance(key, str) and (key.startswith(("EZTK", "EZAK", "Easypost_")) or len(key) >= 20)
    if provider == "shipstation":
        return isinstance(key, str) and len(key) >= 16
    if provider == "easyship":
        return isinstance(key, str) and len(key) >= 24
    return bool(key)

# ---------------- Provider construction ----------------

def _make_provider(provider: str, secrets: Dict[str, Any], environment: str):
    """
    Instantiate your original provider classes with the proper constructor args:
      ShippoProvider(api_key, test_mode)
      EasyPostProvider(api_key, test_mode)
      ShipStationProvider(api_key, test_mode, api_secret)
      EasyShipProvider(api_key, test_mode)
    """
    if core is None:
        raise RuntimeError("shipping_providers module not available in the layer")

    test_mode = (environment != "prod")

    if provider == "shippo":
        return core.ShippoProvider(api_key=secrets["shippo_api_key"], test_mode=test_mode)
    if provider == "easypost":
        return core.EasyPostProvider(api_key=secrets["easypost_api_key"], test_mode=test_mode)
    if provider == "shipstation":
        # Support either split fields or combined "key:secret"
        api_key = secrets.get("shipstation_api_key")
        api_secret = secrets.get("shipstation_api_secret")
        if not api_secret and api_key and ":" in api_key:
            k, s = api_key.split(":", 1)
            api_key, api_secret = k, s
        return core.ShipStationProvider(api_key=api_key, test_mode=test_mode, api_secret=api_secret)
    if provider == "easyship":
        return core.EasyShipProvider(api_key=secrets["easyship_api_key"], test_mode=test_mode)

    raise ValueError(f"Unsupported provider: {provider}")

# ---------------- Payload normalization ----------------

def _norm_addr(addr: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(addr, dict):
        return {}
    return {
        "name": addr.get("name") or addr.get("full_name") or "",
        "company": addr.get("company") or "",
        "street1": addr.get("line1") or addr.get("address1") or addr.get("street1") or "",
        "street2": addr.get("line2") or addr.get("address2") or addr.get("street2") or "",
        "city": addr.get("city") or "",
        "state": addr.get("state") or addr.get("province") or "",
        "postal_code": addr.get("postal_code") or addr.get("zip") or "",
        "country": (addr.get("country") or "US").upper(),
        "phone": addr.get("phone") or "",
        "email": addr.get("email") or "",
    }

def _norm_parcel(parcels: Any) -> Dict[str, Any]:
    if isinstance(parcels, list) and parcels:
        p = parcels[0]
    elif isinstance(parcels, dict):
        p = parcels
    else:
        p = {}
    return {
        "weight_oz": p.get("weight_oz") or p.get("weight") or 16.0,
        "length_in": p.get("length_in") or p.get("length") or 6.0,
        "width_in":  p.get("width_in")  or p.get("width")  or 6.0,
        "height_in": p.get("height_in") or p.get("height") or 6.0,
    }

# ---------------- Public API expected by shipping_api.py ----------------

def test_credentials(payload: Dict[str, Any], *, secrets: Dict[str, Any], environment: str) -> Dict[str, Any]:
    """
    payload: { "provider": "shippo"|"easypost"|"shipstation"|"easyship", "live": bool,
               "from": {...}, "to": {...}, "parcels": [...] (optional for live) }
    secrets: decrypted secrets from DynamoDB (key names as in _secret_key_names)
    environment: "dev" | "prod"
    """
    provider = str(payload.get("provider", "")).lower().strip()
    if provider not in SUPPORTED:
        return {"success": False, "message": f"Unsupported provider: {provider or '<missing>'}",
                "details": {"supported": sorted(SUPPORTED)}}

    # Check required secret(s) exist
    if not _has_required_secrets(provider, secrets):
        return {"success": False, "message": f"Missing secrets for {provider}.",
                "details": {"required": _secret_key_names(provider), "environment": environment}}

    # Light format sanity on the primary key (helps UX before live calls)
    primary_key = secrets[_secret_key_names(provider)[0]]
    if not _validate_format(provider, str(primary_key)):
        return {"success": False, "message": f"{provider.capitalize()} key format looks invalid.",
                "details": {"provider": provider, "environment": environment, "key_tail": _mask_tail(primary_key)}}

    # Optional live probe: do a cheap get_rates() using a tiny parcel + sensible defaults.
    live = bool(payload.get("live"))
    if not live:
        return {"success": True, "message": f"{provider.capitalize()} key format validated.",
                "details": {"provider": provider, "environment": environment, "format_ok": True,
                            "key_tail": _mask_tail(primary_key)}}

    try:
        prov = _make_provider(provider, secrets, environment)
        from_addr = _norm_addr(payload.get("from") or {"postal_code": "94105", "country": "US"})
        to_addr   = _norm_addr(payload.get("to")   or {"postal_code": "10001", "country": "US"})
        parcel    = _norm_parcel(payload.get("parcels") or [{"weight_oz": 8}])
        _ = prov.get_rates(from_addr, to_addr, parcel)  # Should raise on auth/permission errors
        return {"success": True, "message": f"{provider.capitalize()} live check passed.",
                "details": {"provider": provider, "environment": environment, "format_ok": True,
                            "key_tail": _mask_tail(primary_key), "live": True}}
    except Exception as e:
        return {"success": False, "message": f"{provider.capitalize()} live check failed.",
                "details": {"error": str(e)}}

def get_rates(payload: Dict[str, Any], *, secrets: Dict[str, Any], environment: str) -> List[Dict[str, Any]]:
    """
    payload: { "provider": optional string; if omitted, we can merge across all with available secrets,
               "to": {...}, "from": {...}, "parcels": [ {...} ], "currency": "USD" }
    """
    to_addr   = _norm_addr(payload.get("to") or {})
    from_addr = _norm_addr(payload.get("from") or {})
    parcel    = _norm_parcel(payload.get("parcels") or {})

    requested = str(payload.get("provider", "")).lower().strip()
    providers = [requested] if requested in SUPPORTED else [p for p in SUPPORTED if _has_required_secrets(p, secrets)]
    if not providers:
        return []

    all_rates: List[Dict[str, Any]] = []
    for provider in providers:
        try:
            prov = _make_provider(provider, secrets, environment)
            rates = prov.get_rates(from_addr, to_addr, parcel) or []
            for r in rates:
                r.setdefault("provider", provider)
            all_rates.extend(rates)
        except Exception as e:
            all_rates.append({"provider": provider, "error": str(e)})

    def _price(x):
        try:
            return float(x.get("amount") or x.get("price") or 0.0)
        except Exception:
            return 0.0
    all_rates.sort(key=_price)
    return all_rates
