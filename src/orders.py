import json
import os
import boto3
import logging
from datetime import datetime
from typing import Dict, Any
from boto3.dynamodb.conditions import Attr
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
orders_table = boto3.resource('dynamodb').Table(os.environ.get('ORDERS_TABLE', 'orders-dev'))


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # CORS headers - always include these
    headers = {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Client-Id',
        'Access-Control-Allow-Methods': 'GET, POST, PUT, OPTIONS'
    }

    try:
        # Log the incoming event for debugging
        logger.info(f"Received event: {json.dumps(event)}")
        
        method = event.get('httpMethod', '').upper()
        path = event.get('path', '')
        
        # Handle OPTIONS preflight request first and simply
        if method == 'OPTIONS':
            logger.info("Handling OPTIONS preflight request")
            return {
                'statusCode': 200,
                'headers': headers,
                'body': json.dumps({'message': 'CORS preflight successful'})
            }

        # Handle GET requests
        if method == 'GET':
            if '/orders/' in path and path != '/orders':
                # Single order request: /orders/{order_id}
                order_id = path.rstrip('/').split('/')[-1]
                return handle_get_single_order(order_id, headers)
            else:
                # List orders request: /orders
                return handle_get_orders(event, headers)

        # Handle PUT requests
        if method == 'PUT' and '/orders/' in path:
            order_id = path.rstrip('/').split('/')[-1]
            body = json.loads(event['body']) if isinstance(event.get('body'), str) else (event.get('body') or {})
            return handle_update_order(order_id, body, headers)

        # Method not allowed
        return {
            'statusCode': 405,
            'headers': headers,
            'body': json.dumps({'error': 'Method not allowed'})
        }

    except Exception as e:
        logger.error(f"Unexpected error in orders.lambda_handler: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': 'Internal server error', 'details': str(e)})
        }


def _resp(status, body_obj, headers):
    return {
        'statusCode': status, 
        'headers': headers, 
        'body': json.dumps(body_obj, default=decimal_default)
    }


# ---- Helpers ----

def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError


def format_date(date_string: str) -> str:
    if not date_string:
        return 'N/A'
    try:
        dt = datetime.fromisoformat(date_string.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M:%S UTC')
    except Exception:
        return date_string


def format_address(address: Dict[str, Any]) -> str:
    if not address or not any(address.values()):
        return 'N/A'
    parts = [v for v in [address.get('line1'), address.get('line2')] if v]
    city_state = ", ".join(filter(None, [address.get('city'), address.get('state')]))
    if city_state:
        parts.append(city_state)
    if address.get('postal_code'):
        parts.append(address['postal_code'])
    if address.get('country') and address['country'] != 'US':
        parts.append(address['country'])
    return ", ".join(parts) if parts else 'N/A'


def get_client_id_from_event(event):
    """Extract clientID from query params, headers, or body"""
    try:
        # Check query string parameters first
        qs = event.get('queryStringParameters') or {}
        client_id = qs.get('clientID')
        
        if client_id:
            return client_id
        
        # Check headers (X-Client-Id)
        headers = event.get('headers') or {}
        client_id = headers.get('X-Client-Id') or headers.get('x-client-id')
        
        if client_id:
            return client_id
        
        # Check body for clientID (for POST requests)
        if event.get('body'):
            body = json.loads(event['body']) if isinstance(event.get('body'), str) else event.get('body')
            if isinstance(body, dict):
                client_id = body.get('clientID')
                if client_id:
                    return client_id
    except Exception as e:
        logger.warning(f"Error extracting clientID: {str(e)}")
    
    return None


# ---- Routes ----

def handle_get_orders(event, headers):
    try:
        qs = event.get('queryStringParameters') or {}
        limit = int(qs.get('limit', 10))
        last_key_raw = qs.get('lastKey')
        include_count = qs.get('includeCount', 'false').lower() == 'true'
        
        # Get clientID from various sources
        client_id = get_client_id_from_event(event)
        logger.info(f"Processing orders request with clientID: {client_id}")
        
        # Build filter expression
        if client_id:
            logger.info(f"Filtering orders for clientID: {client_id}")
            
            if client_id == 'f8810370-7021-7011-c0cc-6f22f52954d3':
                filter_expr = (
                    Attr('fulfilled').eq('false') & 
                    (Attr('clientID').eq(client_id) | Attr('clientID').not_exists())
                )
            else:
                filter_expr = (
                    Attr('fulfilled').eq('false') & 
                    Attr('clientID').eq(client_id)
                )
        else:
            logger.info("No clientID provided, returning all unfulfilled orders")
            filter_expr = Attr('fulfilled').eq('false')
        
        # Get total count if requested (only on first page load)
        total_count = None
        if include_count or not last_key_raw:
            logger.info("Getting total count...")
            count = 0
            count_kwargs = {'FilterExpression': filter_expr, 'Select': 'COUNT'}
            
            while True:
                count_response = orders_table.scan(**count_kwargs)
                count += count_response.get('Count', 0)
                
                if 'LastEvaluatedKey' not in count_response:
                    break
                count_kwargs['ExclusiveStartKey'] = count_response['LastEvaluatedKey']
            
            total_count = count
            logger.info(f"Total count: {total_count}")
        
        # Get paginated orders
        scan_kwargs = {
            'FilterExpression': filter_expr,
            'Limit': limit
        }

        # Support pagination
        if last_key_raw:
            try:
                scan_kwargs['ExclusiveStartKey'] = json.loads(last_key_raw)
            except Exception as e:
                logger.warning(f"Invalid lastKey: {last_key_raw}, error: {str(e)}")

        # Execute the scan
        response = orders_table.scan(**scan_kwargs)
        items = response.get('Items', [])
        
        # Format the response
        cooked = []
        for item in items:
            order = json.loads(json.dumps(item, default=decimal_default))
            cooked.append({
                "order_id": order.get("order_id"),
                "order_date": format_date(order.get("created_at")),
                "customer_name": order.get("customer_name") or 'N/A',
                "customer_email": order.get("customer_email") or 'N/A',
                "customer_phone": order.get("customer_phone") or 'N/A',
                "product_name": order.get("product_name") or 'Unknown Product',
                "amount": order.get("amount") or 0,
                "currency": order.get("currency", "usd"),
                "shipping_address": format_address(order.get("shipping_address", {})),
                "billing_address": format_address(order.get("billing_address", {})),
                "fulfilled": order.get("fulfilled", "false"),
                "payment_status": order.get("payment_status", "unknown"),
                "clientID": order.get("clientID")
            })

        # Sort by date (newest first)
        cooked.sort(key=lambda x: x.get("order_date", ""), reverse=True)

        next_key = response.get('LastEvaluatedKey')
        payload = {
            "orders": cooked,
            "nextKey": json.dumps(next_key, default=decimal_default) if next_key else None,
            "hasMore": bool(next_key),
            "filteredByClient": bool(client_id),
            "clientID": client_id
        }
        
        # Add total count if we calculated it
        if total_count is not None:
            payload['totalCount'] = total_count
        
        logger.info(f"Returning {len(cooked)} orders (filtered by client: {bool(client_id)})")
        return _resp(200, payload, headers)
        
    except Exception as e:
        logger.error(f"Error in handle_get_orders: {str(e)}", exc_info=True)
        return _resp(500, {'error': 'Failed to retrieve orders', 'details': str(e)}, headers)


def handle_get_single_order(order_id, headers):
    try:
        res = orders_table.get_item(Key={'order_id': order_id})
        if 'Item' not in res:
            return _resp(404, {'error': 'Order not found'}, headers)

        order = json.loads(json.dumps(res['Item'], default=decimal_default))
        formatted = {
            "order_id": order.get("order_id"),
            "order_date": format_date(order.get("created_at")),
            "customer_name": order.get("customer_name") or 'N/A',
            "customer_email": order.get("customer_email") or 'N/A',
            "customer_phone": order.get("customer_phone") or 'N/A',
            "product_name": order.get("product_name") or 'Unknown Product',
            "amount": order.get("amount") or 0,
            "currency": order.get("currency", "usd"),
            "shipping_address": format_address(order.get("shipping_address", {})),
            "billing_address": format_address(order.get("billing_address", {})),
            "fulfilled": order.get("fulfilled", "false"),
            "payment_status": order.get("payment_status", "unknown"),
            "clientID": order.get("clientID")
        }
        return _resp(200, {"order": formatted}, headers)
        
    except Exception as e:
        logger.error(f"Error in handle_get_single_order: {str(e)}", exc_info=True)
        return _resp(500, {'error': 'Failed to retrieve order', 'details': str(e)}, headers)


def handle_update_order(order_id, body, headers):
    try:
        fulfilled = body.get('fulfilled', False)
        orders_table.update_item(
            Key={'order_id': order_id},
            UpdateExpression='SET fulfilled = :f, updated_at = :u',
            ExpressionAttributeValues={
                ':f': 'true' if fulfilled else 'false',
                ':u': datetime.now().isoformat()
            }
        )
        return _resp(200, {'success': True}, headers)
        
    except Exception as e:
        logger.error(f"Error in handle_update_order: {str(e)}", exc_info=True)
        return _resp(500, {'error': 'Failed to update order', 'details': str(e)}, headers)