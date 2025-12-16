import os
import json
import boto3
import logging
from typing import Dict, Any, Optional
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from config_loader import load_config, get_config_value, get_ses_config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
ses_client = boto3.client('ses')

def _json_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,POST",
    }

def _ok(body: Dict[str, Any], code: int = 200) -> Dict[str, Any]:
    return {"statusCode": code, "headers": _json_headers(), "body": json.dumps(body)}

def _err(msg: str, code: int = 400) -> Dict[str, Any]:
    logger.warning(msg)
    return {"statusCode": code, "headers": _json_headers(), "body": json.dumps({"error": msg})}

def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body") or "{}"
    try:
        return json.loads(body)
    except Exception:
        return {}

def get_stripe_keys_table():
    """Get stripe_keys table."""
    table_name = os.environ.get('STRIPE_KEYS_TABLE', 'stripe_keys')
    return dynamodb.Table(table_name)

def get_orders_table():
    """Get orders table."""
    table_name = os.environ.get('ORDERS_TABLE', 'orders-dev')
    return dynamodb.Table(table_name)

def get_tenant_email_template(client_id: str, template_type: str) -> Optional[str]:
    """
    Get custom email template for a tenant.
    Returns None if no custom template exists.
    """
    try:
        table = get_stripe_keys_table()
        response = table.get_item(Key={'clientID': client_id})
        item = response.get('Item')
        
        if not item:
            return None
        
        tenant_config = item.get('tenant_config', {})
        return tenant_config.get(template_type)
        
    except Exception as e:
        logger.error(f"Failed to get tenant template: {str(e)}")
        return None

def build_email_html(template_content: str, variables: Dict[str, Any]) -> str:
    """
    Replace variables in email template.
    Supports: {customer_name}, {order_id}, {tracking_number}, {tracking_url}, etc.
    """
    for key, value in variables.items():
        placeholder = "{" + key + "}"
        template_content = template_content.replace(placeholder, str(value))
    
    return template_content

def send_fulfillment_email(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    POST /admin/send-fulfillment-email
    Body: {
        "order_id": "xxx",
        "template_type": "order_fulfilled" | "just_for_you" | "thank_you" | etc.
    }
    """
    try:
        data = _parse_body(event)
        order_id = data.get('order_id')
        template_type = data.get('template_type', 'order_fulfilled')
        
        if not order_id:
            return _err("order_id is required")
        
        # Get order details
        orders_table = get_orders_table()
        response = orders_table.get_item(Key={'order_id': order_id})
        order = response.get('Item')
        
        if not order:
            return _err("Order not found", 404)
        
        client_id = order.get('client_id')
        customer_email = order.get('customer_email')
        customer_name = order.get('customer_name', 'Customer')
        
        if not customer_email:
            return _err("Order has no customer email")
        
        # Get SES configuration
        ses_config = get_ses_config()
        from_email = ses_config.get('from_email', 'no-reply@juniorbay.com')
        from_name = ses_config.get('from_name', 'JuniorBay')
        reply_to = ses_config.get('reply_to_default', 'support@juniorbay.com')
        
        # Get tenant-specific template
        template_content = get_tenant_email_template(client_id, template_type)
        
        # If no custom template, use default
        if not template_content:
            template_content = get_default_template(template_type)
        
        # Build email variables
        variables = {
            'customer_name': customer_name,
            'order_id': order_id,
            'tracking_number': order.get('tracking_number', 'N/A'),
            'tracking_url': order.get('tracking_url', '#'),
            'product_name': order.get('product_name', 'Product'),
            'order_date': order.get('order_date', order.get('created_at', 'N/A')),
        }
        
        # Build email HTML
        html_body = build_email_html(template_content, variables)
        
        # Build plain text fallback
        text_body = build_plain_text_fallback(template_type, variables)
        
        # Get subject line
        subject = get_email_subject(template_type, variables)
        
        # Send email using SES
        message = MIMEMultipart('alternative')
        message['Subject'] = subject
        message['From'] = f"{from_name} <{from_email}>"
        message['To'] = customer_email
        message['Reply-To'] = reply_to
        
        part1 = MIMEText(text_body, 'plain')
        part2 = MIMEText(html_body, 'html')
        
        message.attach(part1)
        message.attach(part2)
        
        response = ses_client.send_raw_email(
            Source=from_email,
            Destinations=[customer_email],
            RawMessage={'Data': message.as_string()}
        )
        
        logger.info(f"Sent {template_type} email to {customer_email} for order {order_id}")
        
        return _ok({
            'success': True,
            'message_id': response['MessageId'],
            'recipient': customer_email
        })
        
    except Exception as e:
        logger.exception("Error sending fulfillment email")
        return _err(f"Failed to send email: {str(e)}", 500)

def get_default_template(template_type: str) -> str:
    """Get default email template HTML."""
    templates = {
        'order_fulfilled': """
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #2c3e50;">Your Order Has Shipped! 📦</h2>
                    <p>Hi {customer_name},</p>
                    <p>Great news! Your order <strong>#{order_id}</strong> has been shipped and is on its way to you.</p>
                    <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        <p style="margin: 5px 0;"><strong>Product:</strong> {product_name}</p>
                        <p style="margin: 5px 0;"><strong>Tracking Number:</strong> {tracking_number}</p>
                        <p style="margin: 5px 0;"><strong>Order Date:</strong> {order_date}</p>
                    </div>
                    <p>
                        <a href="{tracking_url}" 
                           style="display: inline-block; background-color: #3498db; color: white; 
                                  padding: 12px 24px; text-decoration: none; border-radius: 5px; 
                                  font-weight: bold;">
                            Track Your Package
                        </a>
                    </p>
                    <p>If you have any questions, feel free to reply to this email.</p>
                    <p>Thanks for your order!</p>
                    <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">
                    <p style="font-size: 12px; color: #7f8c8d;">
                        This email was sent regarding order #{order_id}
                    </p>
                </div>
            </body>
            </html>
        """,
        'just_for_you': """
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #2c3e50;">Just For You! 🎁</h2>
                    <p>Hi {customer_name},</p>
                    <p>We noticed you recently ordered from us and wanted to say thank you!</p>
                    <p>As a special thank you, we'd like to offer you an exclusive deal on your next purchase.</p>
                    <p>Stay tuned for more updates!</p>
                    <p>Best regards,<br>The Team</p>
                </div>
            </body>
            </html>
        """,
        'thank_you': """
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #2c3e50;">Thank You! 🙏</h2>
                    <p>Hi {customer_name},</p>
                    <p>Thank you for your order <strong>#{order_id}</strong>!</p>
                    <p>We're preparing your order for shipment and will notify you once it's on its way.</p>
                    <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        <p style="margin: 5px 0;"><strong>Product:</strong> {product_name}</p>
                        <p style="margin: 5px 0;"><strong>Order Date:</strong> {order_date}</p>
                    </div>
                    <p>We appreciate your business!</p>
                </div>
            </body>
            </html>
        """,
        'refund': """
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #2c3e50;">Refund Processed</h2>
                    <p>Hi {customer_name},</p>
                    <p>Your refund for order <strong>#{order_id}</strong> has been processed.</p>
                    <p>You should see the funds returned to your original payment method within 5-10 business days.</p>
                    <p>If you have any questions, please don't hesitate to reach out.</p>
                </div>
            </body>
            </html>
        """,
        'return_label': """
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #2c3e50;">Return Label Ready</h2>
                    <p>Hi {customer_name},</p>
                    <p>Your return label for order <strong>#{order_id}</strong> is ready.</p>
                    <p>Please print the label and attach it to your package.</p>
                    <p>
                        <a href="{tracking_url}" 
                           style="display: inline-block; background-color: #e74c3c; color: white; 
                                  padding: 12px 24px; text-decoration: none; border-radius: 5px; 
                                  font-weight: bold;">
                            Download Return Label
                        </a>
                    </p>
                </div>
            </body>
            </html>
        """
    }
    
    return templates.get(template_type, templates['order_fulfilled'])

def build_plain_text_fallback(template_type: str, variables: Dict[str, Any]) -> str:
    """Build plain text version of email."""
    customer_name = variables.get('customer_name', 'Customer')
    order_id = variables.get('order_id', 'N/A')
    tracking_number = variables.get('tracking_number', 'N/A')
    tracking_url = variables.get('tracking_url', '#')
    product_name = variables.get('product_name', 'Product')
    
    if template_type == 'order_fulfilled':
        return f"""
Hi {customer_name},

Great news! Your order #{order_id} has been shipped and is on its way to you.

Product: {product_name}
Tracking Number: {tracking_number}

Track your package here: {tracking_url}

If you have any questions, feel free to reply to this email.

Thanks for your order!
        """.strip()
    elif template_type == 'thank_you':
        return f"""
Hi {customer_name},

Thank you for your order #{order_id}!

We're preparing your order for shipment and will notify you once it's on its way.

Product: {product_name}

We appreciate your business!
        """.strip()
    else:
        return f"Hi {customer_name}, regarding your order #{order_id}."

def get_email_subject(template_type: str, variables: Dict[str, Any]) -> str:
    """Get email subject line."""
    subjects = {
        'order_fulfilled': f"Your order #{variables.get('order_id', '')} has shipped!",
        'just_for_you': "A special offer just for you!",
        'thank_you': f"Thank you for your order #{variables.get('order_id', '')}",
        'refund': f"Refund processed for order #{variables.get('order_id', '')}",
        'return_label': f"Return label ready for order #{variables.get('order_id', '')}"
    }
    
    return subjects.get(template_type, "Order Update")

def lambda_handler(event, context):
    """Main router for email service."""
    try:
        method = (event.get("httpMethod") or "").upper()
        path = event.get("path") or ""
        
        logger.info(f"Email Service: {method} {path}")
        
        # CORS preflight
        if method == "OPTIONS":
            return _ok({"ok": True})
        
        if path == "/admin/send-fulfillment-email" and method == "POST":
            return send_fulfillment_email(event, context)
        
        return _err(f"Unsupported route: {method} {path}", 405)
        
    except Exception as e:
        logger.exception("Unhandled error in email_service")
        return _err(f"Internal server error: {str(e)}", 500)