# shipping_api.py
# Merged shipping API: config, rates, label purchase, and provider abstraction.
# Providers: EasyPost, Shippo, ShipStation, Easyship, Mock
# Routes:
#   GET  /admin/shipping-config
#   PUT  /admin/shipping-config
#   POST /admin/get-rates
#   POST /admin/create-label
#   POST /admin/test-shipping

import json
import os
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

# ---- Optional provider SDKs ----
try:
    import easypost  # pip install easypost
except Exception:
    easypost = None

try:
    import shippo  # pip install shippo
except Exception:
    shippo = None

# ShipStation & Easyship via REST
import base64
import requests

# --------- Environment / AWS clients ----------
ENV = os.environ.get("ENVIRONMENT", "dev")
STRIPE_KEYS_TABLE = os.environ.get("STRIPE_KEYS_TABLE")  # shared table exists already
SHIPPING_TABLE = os.environ.get("SHIPPING_TABLE")       # set to use a dedicated table
MOCK_SHIPPING = os.environ.get("MOCK_SHIPPING", "false").lower() == "true"
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "30"))

dynamodb = boto3.resource("dynamodb")
table_name = SHIPPING_TABLE or STRIPE_KEYS_TABLE
table = dynamodb.Table(table_name)

# =============== HTTP helpers ===============

def _json_decimal(o):
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError

def _resp(status: int, body: Dict[str, Any]):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PUT,DELETE",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Client-Id",
        },
        "body": json.dumps(body, default=_json_decimal),
    }

def _qs(event) -> Dict[str, str]:
    return event.get("queryStringParameters") or {}

def _body(event) -> Dict[str, Any]:
    raw = event.get("body")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}

def _require_fields(obj: Dict[str, Any], fields: List[str]) -> Optional[str]:
    missing = [f for f in fields if obj.get(f) in (None, "", [])]
    if missing:
        return f"Missing required field(s): {', '.join(missing)}"
    return None

# =============== Config storage ===============
# Persist per-tenant shipping config under tenant item.
# If using a shared table with HASH=clientID, we store in attribute 'shipping_config'.

def _cfg_key(client_id: str) -> Dict[str, Any]:
    return {"clientID": client_id}

def _read_config(client_id: str) -> Dict[str, Any]:
    try:
        res = table.get_item(Key=_cfg_key(client_id))
        item = res.get("Item") or {}
        # direct attr
        cfg = item.get("shipping_config")
        if isinstance(cfg, dict):
            return cfg
        # sometimes nested under "data"
        data = item.get("data") or {}
        if isinstance(data.get("shipping_config"), dict):
            return data["shipping_config"]
        return {}
    except ClientError as e:
        raise RuntimeError(e.response["Error"]["Message"])

def _write_config(client_id: str, config: Dict[str, Any]) -> None:
    try:
        table.update_item(
            Key=_cfg_key(client_id),
            UpdateExpression="SET shipping_config = :cfg",
            ExpressionAttributeValues={":cfg": config},
        )
    except ClientError as e:
        raise RuntimeError(e.response["Error"]["Message"])

# =============== Normalization helpers ===============

def _norm_addr(addr: Dict[str, Any]) -> Dict[str, Any]:
    # Ensure required fields are present; downstream providers read these keys
    return {
        "name": addr.get("name") or addr.get("full_name") or "",
        "company": addr.get("company") or "",
        "street1": addr.get("street1") or addr.get("line1") or "",
        "street2": addr.get("street2") or addr.get("line2") or "",
        "city": addr.get("city") or "",
        "state": addr.get("state") or addr.get("province") or "",
        "zip": str(addr.get("zip") or addr.get("postal_code") or ""),
        "country": addr.get("country") or "US",
        "phone": addr.get("phone") or "",
        "email": addr.get("email") or "",
    }

def _norm_parcel(p: Dict[str, Any]) -> Dict[str, Any]:
    # Default units: inches + ounces
    return {
        "length": float(p.get("length") or 0),
        "width": float(p.get("width") or 0),
        "height": float(p.get("height") or 0),
        "distance_unit": (p.get("distance_unit") or "in").lower(),
        "weight": float(p.get("weight") or 0),
        "mass_unit": (p.get("mass_unit") or "oz").lower(),
    }

# =============== Provider abstraction ===============

class Provider:
    name: str

    def list_rates(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def purchase_label(self, rate_id: str, shipment: Dict[str, Any]) -> Dict[str, Any]:
        """Return {label_url, tracking_number, carrier, service}"""
        raise NotImplementedError

# ---------- Mock ----------

class MockProvider(Provider):
    name = "mock"

    def list_rates(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {"carrier": "UPS",   "service": "Ground",         "rate": "9.95",  "delivery_days": 5, "rate_id": "mock:UPS:Ground"},
            {"carrier": "USPS",  "service": "Priority Mail",  "rate": "7.50",  "delivery_days": 3, "rate_id": "mock:USPS:Priority"},
            {"carrier": "FedEx", "service": "Home Delivery",  "rate": "11.40", "delivery_days": 4, "rate_id": "mock:FedEx:Home"},
        ]

    def purchase_label(self, rate_id: str, shipment: Dict[str, Any]) -> Dict[str, Any]:
        carrier = (rate_id.split(":")[1] if ":" in rate_id else "MockCarrier")
        return {
            "label_url": f"https://example.com/labels/{rate_id.replace(':','_')}.pdf",
            "tracking_number": "TRACKMOCK1234567890",
            "carrier": carrier,
            "service": "Mock Service",
        }

# ---------- EasyPost ----------

class EasyPostProvider(Provider):
    name = "easypost"

    def __init__(self, api_key: str):
        if not easypost:
            raise RuntimeError("EasyPost SDK not available. Add 'easypost' to deployment.")
        easypost.api_key = api_key

    def list_rates(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        shipment = easypost.Shipment.create(
            to_address=payload["to_address"],
            from_address=payload["from_address"],
            parcel=_ep_parcel(payload["parcel"]),
        )
        out = []
        for r in shipment.rates:
            out.append({
                "carrier": r.get("carrier"),
                "service": r.get("service"),
                "rate": r.get("rate"),
                "delivery_days": r.get("delivery_days"),
                "rate_id": r.get("id"),  # EasyPost rate id
            })
        return out

    def purchase_label(self, rate_id: str, shipment: Dict[str, Any]) -> Dict[str, Any]:
        rate = easypost.Rate.retrieve(rate_id)
        sp = easypost.Shipment.retrieve(rate.shipment_id)
        bought = sp.buy(rate=rate)
        return {
            "label_url": bought.postage_label.get("label_url"),
            "tracking_number": bought.tracking_code,
            "carrier": bought.selected_rate.get("carrier"),
            "service": bought.selected_rate.get("service"),
        }

def _ep_parcel(p: Dict[str, Any]) -> Dict[str, Any]:
    # EasyPost reads inches/ounces as "in"/"oz"
    return {
        "length": p["length"],
        "width": p["width"],
        "height": p["height"],
        "weight": p["weight"],  # in oz
    }

# ---------- Shippo ----------

class ShippoProvider(Provider):
    name = "shippo"

    def __init__(self, api_key: str):
        if not shippo:
            raise RuntimeError("Shippo SDK not available. Add 'shippo' to deployment.")
        shippo.config.api_key = api_key

    def list_rates(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        shipment = shippo.Shipment.create(
            address_from=_shippo_addr(payload["from_address"]),
            address_to=_shippo_addr(payload["to_address"]),
            parcels=[_shippo_parcel(payload["parcel"])],
        )
        out = []
        for r in shipment.rates:
            out.append({
                "carrier": r.get("provider"),
                "service": (r.get("servicelevel") or {}).get("name"),
                "rate": r.get("amount"),
                "delivery_days": r.get("estimated_days"),
                "rate_id": r.get("object_id"),  # Shippo rate id
            })
        return out

    def purchase_label(self, rate_id: str, shipment: Dict[str, Any]) -> Dict[str, Any]:
        tx = shippo.Transaction.create(rate=rate_id, label_file_type="PDF")
        if tx.status != "SUCCESS":
            raise RuntimeError(f"Shippo purchase failed: {tx.messages}")
        return {
            "label_url": tx.label_url,
            "tracking_number": tx.tracking_number,
            "carrier": (tx.rate or {}).get("provider"),
            "service": ((tx.rate or {}).get("servicelevel") or {}).get("name"),
        }

def _shippo_addr(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": a["name"],
        "company": a.get("company") or "",
        "street1": a["street1"],
        "street2": a.get("street2") or "",
        "city": a["city"],
        "state": a["state"],
        "zip": a["zip"],
        "country": a.get("country", "US"),
        "phone": a.get("phone") or "",
        "email": a.get("email") or "",
    }

def _shippo_parcel(p: Dict[str, Any]) -> Dict[str, Any]:
    # Shippo expects weight in ounces if mass_unit=oz
    return {
        "length": p["length"],
        "width": p["width"],
        "height": p["height"],
        "distance_unit": p["distance_unit"],  # "in" expected
        "weight": p["weight"],
        "mass_unit": p["mass_unit"],          # "oz" expected
    }

# ---------- ShipStation (REST) ----------

class ShipStationProvider(Provider):
    """
    ShipStation REST (Basic Auth: api_key + api_secret).
    - Rates:   POST /shipments/getrates
    - Labels:  POST /labels/createlabel (requires full 'shipment' object)
    """
    name = "shipstation"

    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://ssapi.shipstation.com"):
        self.base = base_url.rstrip("/")
        self.auth = (api_key, api_secret)

    def list_rates(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        body = {
            "fromPostalCode": payload["from_address"]["zip"],
            "toState": payload["to_address"].get("state"),
            "toCountry": payload["to_address"].get("country"),
            "toPostalCode": payload["to_address"]["zip"],
            "weight": {
                "value": payload["parcel"]["weight"],
                "units": payload["parcel"]["mass_unit"]  # "oz"
            },
            "dimensions": {
                "units": payload["parcel"]["distance_unit"],  # "in"
                "length": payload["parcel"]["length"],
                "width": payload["parcel"]["width"],
                "height": payload["parcel"]["height"]
            },
            "confirmation": "none"
        }
        r = requests.post(f"{self.base}/shipments/getrates", auth=self.auth, json=body, timeout=HTTP_TIMEOUT)
        if not r.ok:
            raise RuntimeError(f"ShipStation rates {r.status_code}: {r.text}")
        out = []
        for it in r.json():
            carrier = it.get("carrierCode")
            svc_code = it.get("serviceCode")
            svc_name = it.get("serviceName") or svc_code
            rate = it.get("shipmentCost", 0)
            out.append({
                "carrier": carrier,
                "service": svc_name,
                "rate": str(rate),
                "delivery_days": it.get("estimatedTransitDays"),
                # Encode carrier/service so we can recreate shipment on purchase
                "rate_id": f"{carrier}:{svc_code or svc_name}",
            })
        return out

    def purchase_label(self, rate_id: str, shipment: Dict[str, Any]) -> Dict[str, Any]:
        carrier, service = _parse_ss_rate_id(rate_id)
        body = {
            "labelLayout": "4x6",
            "labelFormat": "PDF",
            "shipment": {
                "carrierCode": carrier,
                "serviceCode": service,
                # optional: "packageCode": "package"
                "shipFrom": _ss_addr(shipment["from_address"]),
                "shipTo": _ss_addr(shipment["to_address"]),
                "weight": {
                    "value": shipment["parcel"]["weight"],
                    "units": shipment["parcel"]["mass_unit"],  # "oz"
                },
                "dimensions": {
                    "units": shipment["parcel"]["distance_unit"],  # "in"
                    "length": shipment["parcel"]["length"],
                    "width": shipment["parcel"]["width"],
                    "height": shipment["parcel"]["height"],
                },
            }
        }
        r = requests.post(f"{self.base}/labels/createlabel", auth=self.auth, json=body, timeout=HTTP_TIMEOUT)
        if not r.ok:
            raise RuntimeError(f"ShipStation label {r.status_code}: {r.text}")
        data = r.json()
        return {
            "label_url": (data.get("labelDownload") or {}).get("pdf"),
            "tracking_number": data.get("trackingNumber"),
            "carrier": carrier,
            "service": service,
        }

def _parse_ss_rate_id(rate_id: str) -> Tuple[str, str]:
    # "carrierCode:serviceCodeOrName"
    if ":" not in rate_id:
        raise RuntimeError("Invalid ShipStation rate_id")
    a, b = rate_id.split(":", 1)
    return a, b

def _ss_addr(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": a["name"],
        "company": a.get("company") or "",
        "street1": a["street1"],
        "street2": a.get("street2") or "",
        "city": a["city"],
        "state": a["state"],
        "postalCode": a["zip"],
        "countryCode": a.get("country", "US"),
        "phone": a.get("phone") or "",
        "email": a.get("email") or "",
        "residential": a.get("residential", False),
    }

# ---------- Easyship (REST) ----------

class EasyshipProvider(Provider):
    """
    Easyship REST (Bearer token).
    - Rates:            POST /rates
    - Create Shipment:  POST /shipments         (select courier via courier_id)
    - Generate Label:   POST /shipments/label   (label for shipment)
    """
    name = "easyship"

    def __init__(self, api_key: str, base_url: str = "https://api.easyship.com"):
        self.base = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def list_rates(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        body = {
            "origin_country_alpha2": payload["from_address"].get("country"),
            "origin_postal_code": payload["from_address"].get("zip"),
            "destination_country_alpha2": payload["to_address"].get("country"),
            "destination_postal_code": payload["to_address"].get("zip"),
            "destination_city": payload["to_address"].get("city"),
            "source": "api",
            "courier_selection": {"allow_courier_fallback": True},
            "parcels": [{
                "total_actual_weight": payload["parcel"].get("weight", 0),
                "height": payload["parcel"].get("height", 0),
                "width": payload["parcel"].get("width", 0),
                "length": payload["parcel"].get("length", 0),
                "dimensions_unit": payload["parcel"].get("distance_unit", "in"),
                "weight_unit": payload["parcel"].get("mass_unit", "oz")
            }]
        }
        r = requests.post(f"{self.base}/rates", headers=self.headers, json=body, timeout=HTTP_TIMEOUT)
        if not r.ok:
            raise RuntimeError(f"Easyship rates {r.status_code}: {r.text}")
        data = r.json()
        out = []
        for it in data.get("rates", []):
            courier = it.get("courier_name")
            svc_name = it.get("service_level_name")
            amount = (it.get("total_charge") or {}).get("amount", 0)
            # Use courier_id as rate_id so we can create shipment using it
            out.append({
                "carrier": courier,
                "service": svc_name,
                "rate": str(amount),
                "delivery_days": (it.get("delivery_time") or {}).get("days"),
                "rate_id": it.get("courier_id"),  # NOT the shipment id; we'll create it during purchase
            })
        return out

    def purchase_label(self, rate_id: str, shipment: Dict[str, Any]) -> Dict[str, Any]:
        # Step 1: Create shipment selecting the courier by courier_id (rate_id)
        body = {
            "origin_address": _es_addr(shipment["from_address"]),
            "destination_address": _es_addr(shipment["to_address"]),
            "incoterms": "DDU",
            "insurance": {"is_insured": False},
            "courier_selection": {"courier_id": rate_id},
            "parcels": [{
                "total_actual_weight": shipment["parcel"]["weight"],
                "height": shipment["parcel"]["height"],
                "width": shipment["parcel"]["width"],
                "length": shipment["parcel"]["length"],
                "dimensions_unit": shipment["parcel"]["distance_unit"],
                "weight_unit": shipment["parcel"]["mass_unit"]
            }]
        }
        r = requests.post(f"{self.base}/shipments", headers=self.headers, json=body, timeout=HTTP_TIMEOUT)
        if not r.ok:
            raise RuntimeError(f"Easyship create shipment {r.status_code}: {r.text}")
        shipment_id = (r.json().get("shipment") or {}).get("easyship_shipment_id")
        if not shipment_id:
            raise RuntimeError("Easyship: missing shipment id")

        # Step 2: Generate label
        lr = requests.post(
            f"{self.base}/shipments/label",
            headers=self.headers,
            json={"easyship_shipment_id": shipment_id, "label_format": "PDF"},
            timeout=HTTP_TIMEOUT,
        )
        if not lr.ok:
            raise RuntimeError(f"Easyship label {lr.status_code}: {lr.text}")

        # Step 3: Retrieve shipment to get label/tracking
        gr = requests.get(f"{self.base}/shipments/{shipment_id}", headers=self.headers, timeout=HTTP_TIMEOUT)
        if not gr.ok:
            raise RuntimeError(f"Easyship get shipment {gr.status_code}: {gr.text}")
        s = gr.json().get("shipment") or {}
        docs = (s.get("documents") or {})
        label_url = (docs.get("label") or {}).get("url")
        tracking = (s.get("tracking_page") or {}).get("tracking_number") or s.get("tracking_number")

        return {
            "label_url": label_url,
            "tracking_number": tracking,
            "carrier": s.get("selected_courier", {}).get("name") or "Easyship",
            "service": s.get("selected_courier", {}).get("service_level_name"),
        }

def _es_addr(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "contact_name": a["name"] or "N/A",
        "company_name": a.get("company") or "",
        "street1": a["street1"],
        "street2": a.get("street2") or "",
        "city": a["city"],
        "state": a["state"],
        "postal_code": a["zip"],
        "country_alpha2": a.get("country", "US"),
        "phone_number": a.get("phone") or "",
        "email": a.get("email") or "",
    }

# =============== Provider selection ===============

def _provider_from_config(cfg: Dict[str, Any]) -> Provider:
    """
    shipping_config example:
    {
      "provider": "easypost" | "shippo" | "shipstation" | "easyship" | "mock",
      "api_key": "...",             # EP/Shippo/Easyship
      "api_secret": "...",          # ShipStation only
      "base_url": "...",            # optional override for ShipStation/Easyship
      ... other tenant defaults ...
    }
    """
    provider = (cfg.get("provider") or "").lower()

    if MOCK_SHIPPING or provider == "mock":
        return MockProvider()

    if provider == "easypost":
        api_key = cfg.get("api_key")
        if not api_key:
            raise RuntimeError("Missing EasyPost api_key in shipping_config")
        return EasyPostProvider(api_key)

    if provider == "shippo":
        api_key = cfg.get("api_key")
        if not api_key:
            raise RuntimeError("Missing Shippo api_key in shipping_config")
        return ShippoProvider(api_key)

    if provider == "shipstation":
        k = cfg.get("api_key"); s = cfg.get("api_secret")
        if not k or not s:
            raise RuntimeError("Missing ShipStation api_key/api_secret in shipping_config")
        return ShipStationProvider(k, s, cfg.get("base_url", "https://ssapi.shipstation.com"))

    if provider == "easyship":
        k = cfg.get("api_key")
        if not k:
            raise RuntimeError("Missing Easyship api_key in shipping_config")
        return EasyshipProvider(k, cfg.get("base_url", "https://api.easyship.com"))

    # Default: mock, so UI remains usable if config incomplete
    return MockProvider()

# =============== Route handlers ===============

def _handle_get_config(event):
    qs = _qs(event)
    client_id = qs.get("clientID") or qs.get("client_id")
    if not client_id:
        return _resp(400, {"error": "clientID is required"})
    cfg = _read_config(client_id)
    return _resp(200, {"clientID": client_id, "config": cfg})

def _handle_put_config(event):
    body = _body(event)
    err = _require_fields(body, ["clientID", "config"])
    if err:
        return _resp(400, {"error": err})
    client_id = body["clientID"]
    cfg = body["config"]
    if not isinstance(cfg, dict):
        return _resp(400, {"error": "config must be an object"})
    _write_config(client_id, cfg)
    return _resp(200, {"ok": True})

def _extract_shipment_fields(body: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    from_address = _norm_addr(body.get("from_address") or {})
    to_address = _norm_addr(body.get("to_address") or {})
    parcel = _norm_parcel(body.get("parcel") or {})
    # Validate minimal fields used by providers
    need = _require_fields(from_address, ["street1", "city", "state", "zip", "country"])
    if need:
        raise ValueError("from_address incomplete")
    need = _require_fields(to_address, ["street1", "city", "state", "zip", "country"])
    if need:
        raise ValueError("to_address incomplete")
    need = _require_fields(parcel, ["length", "width", "height", "weight", "distance_unit", "mass_unit"])
    if need:
        raise ValueError("parcel incomplete")
    return from_address, to_address, parcel

def _handle_get_rates(event):
    body = _body(event)
    err = _require_fields(body, ["clientID", "from_address", "to_address", "parcel"])
    if err:
        return _resp(400, {"error": err})

    client_id = body["clientID"]
    cfg = _read_config(client_id)
    provider = _provider_from_config(cfg)

    try:
        from_address, to_address, parcel = _extract_shipment_fields(body)
        rates = provider.list_rates({
            "from_address": from_address,
            "to_address": to_address,
            "parcel": parcel,
        })
        return _resp(200, {"rates": rates, "provider": provider.name})
    except Exception as e:
        return _resp(500, {"error": f"Failed to get rates: {e}", "provider": provider.name})

def _handle_create_label(event):
    body = _body(event)
    err = _require_fields(body, ["clientID", "rate_id", "from_address", "to_address", "parcel"])
    if err:
        return _resp(400, {"error": err})

    client_id = body["clientID"]
    cfg = _read_config(client_id)
    provider = _provider_from_config(cfg)

    try:
        from_address, to_address, parcel = _extract_shipment_fields(body)
        result = provider.purchase_label(body["rate_id"], {
            "from_address": from_address,
            "to_address": to_address,
            "parcel": parcel,
        })
        return _resp(200, {"success": True, **result, "provider": provider.name})
    except Exception as e:
        return _resp(500, {"error": f"Failed to create label: {e}", "provider": provider.name})

def _handle_test_shipping(event):
    body = _body(event)
    client_id = body.get("clientID")
    if not client_id:
        return _resp(400, {"error": "clientID is required"})

    cfg = _read_config(client_id)
    provider = _provider_from_config(cfg)

    # non-destructive test for real providers unless run=true
    if isinstance(provider, MockProvider) or body.get("run") is True:
        try:
            rates = provider.list_rates({
                "from_address": _norm_addr({
                    "name":"Sender", "street1":"123 Main St", "city":"Los Angeles", "state":"CA", "zip":"90001", "country":"US"
                }),
                "to_address": _norm_addr({
                    "name":"Receiver", "street1":"456 Pine St", "city":"San Francisco", "state":"CA", "zip":"94105", "country":"US"
                }),
                "parcel": _norm_parcel({"length":6,"width":4,"height":2,"distance_unit":"in","weight":10,"mass_unit":"oz"}),
            })
            return _resp(200, {"ok": True, "provider": provider.name, "sample_rates": rates[:3]})
        except Exception as e:
            return _resp(500, {"error": f"Provider test failed: {e}", "provider": provider.name})
    else:
        return _resp(200, {"ok": True, "provider": provider.name, "note":"Dry run (set run=true to execute a live test)"})


# =============== Lambda entry ===============

def lambda_handler(event, _context):
    resource = event.get("resource")
    method = event.get("httpMethod")

    try:
        if resource == "/admin/shipping-config" and method == "GET":
            return _handle_get_config(event)
        if resource == "/admin/shipping-config" and method == "PUT":
            return _handle_put_config(event)
        if resource == "/admin/get-rates" and method == "POST":
            return _handle_get_rates(event)
        if resource == "/admin/create-label" and method == "POST":
            return _handle_create_label(event)
        if resource == "/admin/test-shipping" and method == "POST":
            return _resp(200, {"ok": True}) if MOCK_SHIPPING else _handle_test_shipping(event)
        return _resp(405, {"error": "Method not allowed"})
    except Exception as e:
        return _resp(500, {"error": str(e)})
