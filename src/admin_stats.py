import os
import json
import base64
import time
from typing import Any, Dict, Optional, Set, List

import boto3

try:
    import stripe
except ImportError:  # pragma: no cover - deployed env always has stripe
    stripe = None


# ───── Environment -----------------------------------------------------------

def _req(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")
STRIPE_KEYS_TABLE = _req("STRIPE_KEYS_TABLE")
STRIPE_KMS_KEY_ARN = _req("STRIPE_KMS_KEY_ARN")
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"


dynamodb = boto3.resource("dynamodb", region_name=REGION)
keys_table = dynamodb.Table(STRIPE_KEYS_TABLE)
kms = boto3.client("kms", region_name=REGION)


# ───── HTTP helpers ---------------------------------------------------------

def _cors_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,Stripe-Signature,X-Client-Id,X-Offer-Name",
        "Access-Control-Allow-Methods": "OPTIONS,GET",
    }


def _ok(body: Dict[str, Any], status: int = 200) -> Dict[str, Any]:
    return {"statusCode": status, "headers": _cors_headers(), "body": json.dumps(body)}


def _bad(message: str, status: int = 400) -> Dict[str, Any]:
    return _ok({"error": message}, status=status)


def _parse_json_body(event: Dict[str, Any]) -> Dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def _extract_client_id(event: Dict[str, Any]) -> Optional[str]:
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


# ───── KMS helpers ----------------------------------------------------------

def _unwrap_encrypted(value: str) -> str:
    if isinstance(value, str) and value.startswith("ENCRYPTED(") and value.endswith(")"):
        return value[len("ENCRYPTED(") : -1]
    return value


def _decrypt(ciphertext_wrapped: str) -> str:
    blob = base64.b64decode(_unwrap_encrypted(ciphertext_wrapped))
    resp = kms.decrypt(CiphertextBlob=blob, EncryptionContext={"app": "stripe-cart"})
    return resp["Plaintext"].decode("utf-8")


def _stripe_secret_for_client(client_id: str, mode: str) -> str:
    resp = keys_table.get_item(Key={"clientID": client_id})
    item = resp.get("Item")
    if not item:
        raise ValueError(f"No Stripe keys found for client {client_id}")

    field_candidates = [
        f"sk_{mode}",
        f"{mode}_secret_key",
        "stripe_secret_key",
        "sk",
    ]
    secret = ""
    for field in field_candidates:
        secret = item.get(field)
        if secret:
            break
    if not secret:
        raise ValueError(f"Stripe secret key missing for {mode} mode")
    if secret.startswith("ENCRYPTED("):
        secret = _decrypt(secret)
    if not secret.startswith("sk_test_") and not secret.startswith("sk_live_"):
        raise ValueError("Invalid Stripe secret key format")
    return secret


# ───── Stripe data helpers --------------------------------------------------

def _list_checkout_sessions(since_epoch: int):
    params = {"limit": 100, "created": {"gte": since_epoch}}
    return stripe.checkout.Session.list(**params).auto_paging_iter()


def _list_products():
    params = {"limit": 100, "active": True}
    return stripe.Product.list(**params).auto_paging_iter()


def _collect_stats(secret_key: str, range_days: int) -> Dict[str, Any]:
    if not stripe:
        raise RuntimeError("Stripe SDK not available")

    stripe.api_key = secret_key

    now = int(time.time())
    since = now - range_days * 86400

    total_orders = 0
    revenue_cents = 0
    customers: Set[str] = set()
    currency = None

    for session in _list_checkout_sessions(since):
        status = session.get("status")
        payment_status = session.get("payment_status")
        if status != "complete" or payment_status not in ("paid", "no_payment_required"):
            continue
        total_orders += 1
        amount = session.get("amount_total") or 0
        revenue_cents += amount
        currency = currency or session.get("currency") or "usd"

        customer_id = session.get("customer")
        if customer_id:
            customers.add(customer_id)
        else:
            email = ((session.get("customer_details") or {}).get("email") or "").strip().lower()
            if email:
                customers.add(email)

    product_count = 0
    for _ in _list_products():
        product_count += 1

    return {
        "range_days": range_days,
        "period_start": since,
        "period_end": now,
        "stats": {
            "orders": total_orders,
            "revenue_cents": revenue_cents,
            "currency": (currency or "usd").upper(),
            "customers": len(customers),
            "products": product_count,
        },
    }


def _recent_transactions(secret_key: str, limit: int = 10) -> List[Dict[str, Any]]:
    if not stripe:
        raise RuntimeError("Stripe SDK not available")

    stripe.api_key = secret_key

    charges = stripe.Charge.list(limit=limit)
    items: List[Dict[str, Any]] = []

    for charge in charges.get("data", []):
        outcome = charge.get("outcome") or {}
        billing = charge.get("billing_details") or {}
        shipping = charge.get("shipping") or {}

        customer_name = shipping.get("name") or billing.get("name") or ""
        customer_email = billing.get("email") or ""
        customer_id = charge.get("customer")

        customer_label = customer_name or customer_email or customer_id or "Unknown"

        items.append({
            "id": charge.get("payment_intent") or charge.get("id"),
            "created": charge.get("created"),
            "customer": customer_label,
            "amount_cents": charge.get("amount") or 0,
            "currency": (charge.get("currency") or "usd").upper(),
            "risk_score": outcome.get("risk_score"),
            "risk_level": outcome.get("risk_level"),
        })

    return items


# ───── Lambda handler -------------------------------------------------------

def lambda_handler(event, context):
    method = (event.get("httpMethod") or "").upper()
    if method == "OPTIONS":
        return _ok({"ok": True})
    if method != "GET":
        return _bad("Method not allowed", status=405)

    client_id = _extract_client_id(event)
    if not client_id:
        return _bad("clientID required (header, query, or body)")

    params = event.get("queryStringParameters") or {}
    mode = (params.get("mode") or ("live" if ENVIRONMENT == "prod" else "test")).lower()
    if mode not in ("test", "live"):
        mode = "test"

    try:
        range_days = int(params.get("rangeDays") or params.get("range_days") or 30)
    except (TypeError, ValueError):
        range_days = 30
    range_days = max(1, min(range_days, 90))

    try:
        secret = _stripe_secret_for_client(client_id, mode)
        stats = _collect_stats(secret, range_days)
        recent = _recent_transactions(secret, 10)
        response = {
            "clientID": client_id,
            "mode": mode,
            "environment": ENVIRONMENT,
            "fetched_at": int(time.time()),
            **stats,
            "recent_transactions": recent,
        }
        return _ok(response)
    except ValueError as e:
        return _bad(str(e), status=400)
    except RuntimeError as e:
        return _bad(str(e), status=500)
    except Exception as e:  # pragma: no cover
        return _bad(f"Failed to fetch stats: {e}", status=500)
