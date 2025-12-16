import json
import os
import boto3
from botocore.exceptions import ClientError
from decimal import Decimal

ENV = os.environ.get("ENVIRONMENT", "dev")
APP_CONFIG_TABLE = os.environ.get("APP_CONFIG_TABLE")

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(APP_CONFIG_TABLE)

def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "OPTIONS,GET,PUT",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Client-Id",
        },
        "body": json.dumps(body, default=_json_decimal),
    }

def _json_decimal(o):
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError

def _require(method, event):
    if event.get("httpMethod") != method:
        return _resp(405, {"error": "Method not allowed"})
    return None

def _get_qs(event):
    return (event.get("queryStringParameters") or {}) if isinstance(event, dict) else {}

def _parse_body(event):
    raw = event.get("body")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}

def _offers_key(client_id):
    # Store per-tenant offers in app-config table
    return f"offers#{client_id}"

def _get_offers(client_id):
    try:
        res = table.get_item(
            Key={"config_key": _offers_key(client_id), "environment": ENV}
        )
        item = res.get("Item")
        return (item or {}).get("data", {}).get("offers", [])
    except ClientError as e:
        raise RuntimeError(e.response["Error"]["Message"])

def _put_offers(client_id, offers):
    item = {
        "config_key": _offers_key(client_id),
        "environment": ENV,
        "data": {"offers": offers},
    }
    try:
        table.put_item(Item=item)
    except ClientError as e:
        raise RuntimeError(e.response["Error"]["Message"])

def lambda_handler(event, _context):
    method = event.get("httpMethod")
    try:
        if method == "GET":
            qs = _get_qs(event)
            client_id = qs.get("clientID")
            if not client_id:
                return _resp(400, {"error": "clientID is required"})
            offers = _get_offers(client_id)
            return _resp(200, {"clientID": client_id, "offers": offers})

        if method == "PUT":
            body = _parse_body(event)
            client_id = body.get("clientID")
            offers = body.get("offers")
            if not client_id or offers is None:
                return _resp(400, {"error": "clientID and offers are required"})
            _put_offers(client_id, offers)
            return _resp(200, {"ok": True})

        # Let API Gateway's mock handle OPTIONS; return 405 otherwise
        return _resp(405, {"error": "Method not allowed"})
    except Exception as e:
        return _resp(500, {"error": str(e)})
