import os
import json
import base64
import boto3
import time
import uuid
import hmac
import hashlib
import logging
from urllib import request, error

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")
STRIPE_KEYS_TABLE = os.environ["STRIPE_KEYS_TABLE"]
STRIPE_KMS_KEY_ARN = os.environ["STRIPE_KMS_KEY_ARN"]
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"

dynamodb = boto3.resource("dynamodb", region_name=REGION)
keys_table = dynamodb.Table(STRIPE_KEYS_TABLE)
kms = boto3.client("kms", region_name=REGION)
ENC_CTX = {"app": "stripe-cart"}


def _cors_headers():
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,POST",
    }


def _ok(body, status=200):
    return {"statusCode": status, "headers": _cors_headers(), "body": json.dumps(body)}


def _bad(message, status=400):
    return _ok({"error": message}, status=status)


def _parse_body(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Body must be a JSON object")
    return data


def _unwrap(value: str) -> str:
    if isinstance(value, str) and value.startswith("ENCRYPTED(") and value.endswith(")"):
        return value[len("ENCRYPTED("):-1]
    return value


def _decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if not (isinstance(value, str) and value.startswith("ENCRYPTED(")):
        return value
    blob = base64.b64decode(_unwrap(value))
    resp = kms.decrypt(CiphertextBlob=blob, EncryptionContext=ENC_CTX)
    return resp["Plaintext"].decode("utf-8")


def _get_webhook_details(client_id: str, mode: str):
    logger.info("Fetching webhook details client=%s mode=%s env=%s", client_id, mode, ENVIRONMENT)
    resp = keys_table.get_item(Key={"clientID": client_id})
    item = resp.get("Item")
    if not item:
        raise ValueError("Stripe keys not found for this client.")

    candidates = []
    if mode == "live":
        candidates = ["wh_secret_live"]
    else:
        candidates = ["wh_secret_test"]

    for key in candidates:
        secret = item.get(key)
        if secret:
            logger.info("Using webhook secret field '%s' for client=%s mode=%s", key, client_id, mode)
            decrypted = _decrypt_secret(secret)
            if decrypted:
                hash_preview = hashlib.sha256(decrypted.encode("utf-8")).hexdigest()[:12]
                logger.info(
                    "Decrypted secret stats client=%s mode=%s length=%d sha256=%s…",
                    client_id,
                    mode,
                    len(decrypted),
                    hash_preview,
                )
            url_field = f"webhook_url_{mode}"
            url = item.get(url_field)
            if not url:
                raise ValueError(f"No {url_field} configured for this client.")
            logger.info("Resolved webhook endpoint %s for client=%s mode=%s", url, client_id, mode)
            return decrypted, url

    raise ValueError(f"No webhook secret found for mode '{mode}'.")


def _build_test_event(client_id: str, mode: str) -> str:
    now = int(time.time())
    event_id = f"evt_test_{uuid.uuid4().hex[:24]}"
    pi_id = f"pi_test_{uuid.uuid4().hex[:24]}"
    payload = {
        "id": event_id,
        "object": "event",
        "api_version": "2020-03-02",
        "created": now,
        "livemode": mode == "live",
        "type": "payment_intent.succeeded",
        "pending_webhooks": 1,
        "data": {
            "object": {
                "id": pi_id,
                "object": "payment_intent",
                "amount": 4200,
                "currency": "usd",
                "metadata": {
                    "clientID": client_id,
                    "testWebhook": "true",
                },
                "description": "Test webhook payment intent",
            }
        }
    }
    return json.dumps(payload, separators=(",", ":"))


def _deliver_webhook(endpoint_url: str, secret: str, payload: str) -> int:
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.{payload}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "Stripe-Signature": f"t={timestamp},v1={signature}",
        "User-Agent": "AdminWebhookTester/1.0",
    }

    stripe_header = headers["Stripe-Signature"]
    logger.info(
        "Constructed Stripe-Signature header %s (%d chars payload)",
        stripe_header,
        len(payload),
    )
    logger.debug("Test payload preview: %s", payload[:256])

    req = request.Request(endpoint_url, data=payload.encode("utf-8"), headers=headers, method="POST")
    logger.info("Sending webhook test event to %s", endpoint_url)
    try:
        with request.urlopen(req, timeout=10) as resp:
            resp.read()
            logger.info("Webhook endpoint responded with status %s", resp.status)
            return resp.status
    except error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        logger.warning("Webhook endpoint returned HTTP %s body=%s", e.code, body)
        raise RuntimeError(f"Webhook responded with {e.code}: {body}")
    except Exception as exc:
        logger.exception("Failed to call webhook endpoint %s", endpoint_url)
        raise RuntimeError(f"Failed to call webhook: {exc}") from exc


def lambda_handler(event, context):
    method = (event.get("httpMethod") or "").upper()
    if method == "OPTIONS":
        return _ok({"ok": True})
    if method != "POST":
        return _bad("Method not allowed", 405)

    try:
        body = _parse_body(event)
    except ValueError as exc:
        return _bad(str(exc))

    client_id = (body.get("clientID") or "").strip()
    mode = (body.get("mode") or "test").lower()

    if not client_id:
        return _bad("clientID is required")
    if mode not in ("test", "live"):
        mode = "test"

    try:
        logger.info("Test webhook request client=%s mode=%s", client_id, mode)
        secret, endpoint_url = _get_webhook_details(client_id, mode)
        payload = _build_test_event(client_id, mode)
        status = _deliver_webhook(endpoint_url, secret, payload)
        return _ok({"success": True, "status": status})
    except ValueError as exc:
        logger.warning("Webhook test validation failed: %s", exc)
        return _bad(str(exc))
    except RuntimeError as exc:
        logger.warning("Webhook delivery failed: %s", exc)
        return _bad(str(exc), status=502)
