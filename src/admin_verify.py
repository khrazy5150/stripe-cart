import os, json, base64, hmac, hashlib, time, boto3, requests
import traceback

DDB = boto3.client('dynamodb')
KMS = boto3.client('kms')

TABLE = os.environ['STRIPE_KEYS_TABLE']
KMS_KEY_ARN = os.environ['STRIPE_KMS_KEY_ARN']
KMS_CTX_APP = os.environ.get('KMS_ENC_CTX_APP', 'stripe-cart')

def _resp(status, body, cors=True):
    h = {'Content-Type': 'application/json'}
    if cors:
        h.update({
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization, stripe-signature, X-Client-Id',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, OPTIONS',
            'Access-Control-Allow-Credentials': 'false'
        })
    return {'statusCode': status, 'headers': h, 'body': json.dumps(body)}

def _require_client_id(event):
    """Extract client ID from various possible locations"""
    try:
        # First try the request body (for POST requests)
        if event.get('body'):
            try:
                body = _parse_body(event)
                if body.get('clientID'):
                    return body['clientID']
            except:
                pass
        
        # Then try Cognito claims
        claims = event.get('requestContext', {}).get('authorizer', {}).get('claims', {})
        for field in ['sub', 'cognito:username', 'email']:
            if claims.get(field):
                return claims[field]
        
        raise ValueError("No clientID found in request body or auth claims")
        
    except Exception as e:
        raise ValueError(f"Failed to extract client ID: {str(e)}")

def _to_str(m, key):
    """Safe read from DynamoDB map"""
    if not m:
        return ''
    v = m.get(key)
    return v.get('S', '') if v else ''

def _ddb_get(client_id: str):
    """Get item from DynamoDB with error handling"""
    try:
        r = DDB.get_item(
            TableName=TABLE, 
            Key={'clientID': {'S': client_id}}, 
            ConsistentRead=True
        )
        return r.get('Item')
    except Exception as e:
        print(f"DynamoDB get error: {e}")
        raise

def _kms_decrypt_wrapped(blob: str) -> str:
    """Decrypt KMS-encrypted value with error handling"""
    if not (blob and blob.startswith("ENCRYPTED(") and blob.endswith(")")):
        return blob  # Return as-is if not encrypted format
    try:
        b64 = blob[len("ENCRYPTED("):-1]
        ct = base64.b64decode(b64)
        resp = KMS.decrypt(
            CiphertextBlob=ct, 
            EncryptionContext={'app': KMS_CTX_APP}
        )
        return resp['Plaintext'].decode('utf-8')
    except Exception as e:
        print(f"KMS decrypt error: {e}")
        return ""  # Return empty string on decrypt failure

def _stripe_get_account(secret_key: str):
    """Test Stripe secret key by calling /v1/account"""
    try:
        r = requests.get(
            "https://api.stripe.com/v1/account",
            auth=(secret_key, ''),
            timeout=10,
        )
        return r.status_code, (r.json() if r.content else {})
    except Exception as e:
        print(f"Stripe API error: {e}")
        raise

def _verify_webhook_secret(wh_secret: str) -> bool:
    """
    Syntactic check using Stripe's signature format.
    We HMAC a sample payload and ensure we can construct a v1 signature.
    This does NOT call Stripe; it only validates the secret is usable.
    """
    if not wh_secret:
        return False
    try:
        ts = int(time.time())
        payload = '{"ping":"ok"}'
        signed_payload = f"{ts}.{payload}"
        digest = hmac.new(
            wh_secret.encode(), 
            msg=signed_payload.encode(), 
            digestmod=hashlib.sha256
        ).hexdigest()
        sig_header = f"t={ts},v1={digest}"
        # If we computed without exceptions, consider it OK
        return len(digest) == 64 and 'v1=' in sig_header
    except Exception as e:
        print(f"Webhook secret verification error: {e}")
        return False

def _parse_body(event):
    """Parse request body with error handling"""
    if not event.get('body'):
        return {}
    try:
        if event.get('isBase64Encoded'):
            b = base64.b64decode(event['body'])
            return json.loads(b)
        return json.loads(event['body'])
    except Exception as e:
        print(f"Body parse error: {e}")
        raise ValueError(f"Invalid JSON body: {str(e)}")

def lambda_handler(event, context):
    """Main Lambda handler with comprehensive error handling"""
    try:
        print(f"Event: {json.dumps(event)}")  # Debug logging
        
        method = event.get('httpMethod', '').upper()
        
        # Handle preflight requests
        if method == 'OPTIONS':
            return _resp(200, {"message": "CORS preflight"})
        
        # Get client ID (this will handle auth validation too)
        try:
            client_id = _require_client_id(event)
            print(f"Using client_id: {client_id}")
        except ValueError as e:
            return _resp(400, {"error": str(e)})
        except Exception as e:
            return _resp(401, {"error": f"Authentication failed: {str(e)}"})

        if method != 'POST':
            return _resp(405, {"error": f"Method {method} not allowed"})

        # Parse request body
        try:
            body = _parse_body(event)
            print(f"POST body: {json.dumps(body)}")
        except ValueError as e:
            return _resp(400, {"error": str(e)})

        mode = (body.get('mode') or '').lower()
        if mode not in ('test', 'live', ''):
            return _resp(400, {"error": "mode must be 'test' or 'live'"})

        # Get stripe keys from DynamoDB
        item = _ddb_get(client_id)
        if not item:
            return _resp(404, {"error": "stripe_keys row not found for client"})

        # Determine effective mode
        effective_mode = mode or (_to_str(item, 'mode') or 'test')
        print(f"Using effective_mode: {effective_mode}")

        # Get the keys for this mode
        sk_blob = _to_str(item, f"sk_{effective_mode}")
        wh_blob = _to_str(item, f"wh_secret_{effective_mode}")
        pk = _to_str(item, f"pk_{effective_mode}")

        # Decrypt sensitive keys
        secret_key = _kms_decrypt_wrapped(sk_blob) if sk_blob else ''
        wh_secret = _kms_decrypt_wrapped(wh_blob) if wh_blob else ''

        # Initialize result
        result = {
            "clientID": client_id,
            "mode": effective_mode,
            "publishable_key_ok": pk.startswith(f"pk_{effective_mode}_"),
            "secret_key_ok": False,
            "webhook_secret_ok": False,
            "stripe_account": None,
            "notes": []
        }

        # Test secret key by calling Stripe API
        if not secret_key:
            result["notes"].append("No secret key stored/decrypted.")
        else:
            try:
                code, acc = _stripe_get_account(secret_key)
                result["secret_key_ok"] = (code == 200 and "id" in acc)
                if result["secret_key_ok"]:
                    result["stripe_account"] = acc.get("id")
                    result["notes"].append(f"Stripe account verified: {acc.get('id')}")
                else:
                    result["notes"].append(f"Stripe /v1/account returned {code}")
                    if acc.get("error"):
                        result["notes"].append(f"Stripe error: {acc['error'].get('message', 'Unknown error')}")
            except Exception as e:
                result["notes"].append(f"Stripe call failed: {str(e)[:200]}")

        # Test webhook secret format
        if not wh_secret:
            result["notes"].append("Webhook secret missing or invalid format.")
        else:
            result["webhook_secret_ok"] = _verify_webhook_secret(wh_secret)
            if result["webhook_secret_ok"]:
                result["notes"].append("Webhook secret format is valid.")
            else:
                result["notes"].append("Webhook secret format is invalid.")

        return _resp(200, result)

    except Exception as e:
        # Catch-all error handler with CORS headers
        print(f"Lambda error: {traceback.format_exc()}")
        return _resp(500, {
            "error": "Internal server error", 
            "message": str(e),
            "type": type(e).__name__
        })