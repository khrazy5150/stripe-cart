#!/usr/bin/env python3
"""
Seed script to populate the app-config DynamoDB table with initial configuration.
Run this once after creating the CloudFormation stack.

Usage:
    python seed_app_config.py --region us-west-2 --environment dev
    # or
    python seed_app_config.py --region us-west-2 --environment prod

    For post-deploy updates:
    python seed_app_config.py --region us-west-2 --environment prod \
      --update-post-deploy --user-pool-id <ID> --client-id <ID> --api-url <URL>
"""

import boto3
import botocore
import argparse
from datetime import datetime, timezone
from typing import Dict

# --------------------------- Helpers ---------------------------

def resolve_table_name(environment: str) -> str:
    """
    Map environment -> DynamoDB table name.
    Requirement: dev => app-config-dev, prod => app-config-prod.
    (Optional) staging supported if you ever add that table.
    """
    mapping: Dict[str, str] = {
        "dev":  "app-config-dev",
        "prod": "app-config-prod",
        "staging": "app-config-staging",  # Only if you actually have this table
    }
    if environment not in mapping:
        raise ValueError(f"Unsupported environment '{environment}'. Expected one of: {', '.join(mapping.keys())}")
    return mapping[environment]

def get_table(region: str, table_name: str):
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    # Verify table exists
    try:
        table.load()
    except botocore.exceptions.ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            raise RuntimeError(f"DynamoDB table '{table_name}' was not found in region '{region}'.") from e
        raise
    return table

# --------------------------- Seed ---------------------------

def seed_config(region: str, environment: str):
    """Seed the app-config table with initial configuration."""
    table_name = resolve_table_name(environment)
    table = get_table(region, table_name)

    now = datetime.now(timezone.utc).isoformat()

    # Configuration items to seed
    config_items = [
        # ==========================================
        # GLOBAL CONFIGURATION
        # ==========================================
        ('stripe_api_version', 'global', '2023-10-16', 'Stripe API version to use'),
        ('frontend_test_dir', 'global', 'dist', 'Frontend test directory name'),
        ('frontend_prod_dir', 'global', '', 'Frontend production directory name'),
        ('upload_base_url', 'global', 'https://dph4d1c6p8.execute-api.us-west-2.amazonaws.com/v3', 'Image upload URL'),
        ('stripe_keys', 'global', 'stripe_keys', 'Table name housing Stripe keys, shipping api keys, and tenant configuration'),

        # API configuration
        ('api_timeout_default', 'global', '10', 'Default API timeout in seconds'),
        ('stripe_api_base_url', 'global', 'https://api.stripe.com', 'Stripe API base URL'),
        ('shippo_api_base_url', 'global', 'https://api.goshippo.com', 'Shippo API base URL'),
        ('easypost_api_base_url', 'global', 'https://api.easypost.com/v2', 'EasyPost API base URL'),
        ('shipstation_api_base_url', 'global', 'https://ssapi.shipstation.com', 'ShipStation API base URL'),
        ('easyship_api_base_url', 'global', 'https://api.easyship.com/2023-01', 'EasyShip API base URL'),

        # ==========================================
        # SES (Email) CONFIGURATION
        # ==========================================
        ('ses_region', 'global', 'us-west-2', 'AWS SES region'),
        ('ses_from_email', 'global', 'no-reply@juniorbay.com', 'Default FROM email address'),
        ('ses_from_name', 'global', 'Junior Bay Support', 'Default FROM name'),
        ('ses_reply_to_default', 'global', 'support@juniorbay.net', 'Default REPLY-TO email'),
        ('ses_configuration_set', 'global', '', 'SES configuration set (optional)'),

        # IMPORTANT: Store SMTP credentials securely (rotate & encrypt in production!)
        ('ses_smtp_username', 'global', 'AKIASGDJJ5ZLXY6ZWYNK', 'SES SMTP username'),
        ('ses_smtp_password', 'global', 'BJKh1XvDOPwZrUyTcccfBAjaB1VBXG5iyrMAPuRK40bK', 'SES SMTP password'),

        # Email templates
        ('email_template_order_fulfilled', 'global', 'default', 'Use default order fulfilled template'),
        ('email_template_order_confirmation', 'global', 'default', 'Use default order confirmation template'),
        ('email_template_refund_processed', 'global', 'default', 'Use default refund template'),
        ('email_template_return_label', 'global', 'default', 'Use default return label template'),

        # ==========================================
        # PROD ENVIRONMENT CONFIGURATION
        # ==========================================
        ('public_config_url', 'prod', 'https://checkout.juniorbay.com/prod/config', 'Production only basic configuration needed by config.js'),
        ('admin_config_url', 'prod', 'https://checkout.juniorbay.com/prod/admin/app-config', 'Admin Configuration URL in Live mode'),
        ('cognito_region', 'prod', 'us-west-2', 'Cognito region for prod'),
        ('cognito_user_pool_id', 'prod', '', 'Cognito User Pool ID (to be filled after deployment)'),
        ('cognito_client_id', 'prod', '', 'Cognito App Client ID (to be filled after deployment)'),
        ('api_base_url', 'prod', '', 'API Gateway URL (to be filled after deployment)'),

        # Frontend URLs - Admin/Order Management
        ('frontend_base_url', 'prod', 'https://juniorbay.com', 'Frontend base URL for admin app (production)'),

        # Offers/Landing Pages
        ('offers_base_url', 'prod', 'https://juniorbay.com/tenants/', 'Base URL for offer landing pages (production)'),
        ('offers_url_pattern', 'prod', '{offers_base_url}{client_id}/{offer_path}/', 'URL pattern for constructing offer URLs'),

        # S3 Buckets
        ('landing_page_preview_bucket', 'prod', 'landing-pages-preview-prod-150544707159', 'S3 bucket for preview landing pages'),
        ('landing_page_bucket', 'prod', 'landing-pages-prod-150544707159', 'S3 bucket for published landing pages (offers)'),
        ('order_mgmt_bucket', 'prod', 'order-mgmt-prod-150544707159-us-west-2', 'S3 bucket for order management website'),

        # Offers S3
        ('offers_s3_bucket', 'prod', 'landing-pages-prod-150544707159', 'Primary S3 bucket for offer landing pages'),
        ('offers_s3_region', 'prod', 'us-west-2', 'S3 region for offers bucket'),
        ('offers_s3_public', 'prod', 'true', 'Whether offers bucket is publicly accessible (true for prod)'),

        # ==========================================
        # OFFERS SYSTEM CONFIGURATION
        # ==========================================
        ('offers_enabled', 'global', 'true', 'Enable/disable offers system globally'),
        ('offers_require_auth', 'global', 'false', 'Whether offers require authentication'),
        ('offers_default_countdown_minutes', 'global', '5', 'Default countdown timer duration for offers'),
        ('offers_analytics_enabled', 'global', 'true', 'Track offer analytics (views, conversions)'),
        ('offers_cache_ttl_seconds', 'global', '300', 'Cache TTL for offer configurations (5 minutes)'),

        # ==========================================
        # LANDING PAGE DEPLOYMENT
        # ==========================================
        ('landing_page_deploy_method', 'dev', 'local', 'Deployment method: local, s3, or cloudfront'),
        ('landing_page_deploy_method', 'prod', 's3', 'Deployment method: local, s3, or cloudfront'),
        ('landing_page_cloudfront_id', 'prod', '', 'CloudFront distribution ID for offers (if using CloudFront)'),
        ('landing_page_invalidation_enabled', 'prod', 'true', 'Auto-invalidate CloudFront cache on deploy'),

        # ==========================================
        # CHECKOUT FLOW CONFIGURATION
        # ==========================================
        ('checkout_success_url_template', 'global', '{offers_base_url}{client_id}/{offer_path}/thank-you.html?session_id={{CHECKOUT_SESSION_ID}}', 'Success URL template for Stripe checkout'),
        ('checkout_cancel_url_template', 'global', '{offers_base_url}{client_id}/{offer_path}/checkout.html', 'Cancel URL template for Stripe checkout'),
        ('checkout_upsell_url_template', 'global', '{offers_base_url}{client_id}/{offer_path}/upsell.html?session_id={{CHECKOUT_SESSION_ID}}', 'Upsell URL template for basic products'),

        # ==========================================
        # PROMOTION WORKFLOW (FUTURE)
        # ==========================================
        ('promotion_enabled', 'global', 'false', 'Enable test → prod promotion workflow (not yet implemented)'),
        ('promotion_require_approval', 'global', 'true', 'Require manual approval for promotions'),
        ('promotion_backup_enabled', 'global', 'true', 'Backup prod before promotion'),
    ]

    print(f"Seeding app-config table '{table_name}' with {len(config_items)} items...")
    print(f"Environment: {environment}")
    print(f"Region: {region}\n")

    success_count = 0
    error_count = 0

    for config_key, env, value, description in config_items:
        try:
            table.put_item(Item={
                'config_key': config_key,
                'environment': env,
                'value': value,
                'description': description,
                'updated_at': now,
                'updated_by': 'seed_script'
            })
            print(f"✓ Added: {config_key} ({env})")
            success_count += 1
        except Exception as e:
            print(f"✗ Failed: {config_key} ({env}) - {str(e)}")
            error_count += 1

    print("\nSeeding complete!")
    print(f"Success: {success_count}")
    print(f"Errors: {error_count}\n")

    print("=" * 60)
    print("IMPORTANT POST-DEPLOYMENT STEPS:")
    print("=" * 60)
    print("1. Update (from stack outputs): cognito_user_pool_id, cognito_client_id, api_base_url")
    print("2. Use the admin API to update values")
    print("3. SECURITY: Rotate & encrypt SES SMTP credentials!")
    print("=" * 60)

# --------------------------- Post-Deploy Update ---------------------------

def update_post_deployment(region: str, environment: str, user_pool_id: str, client_id: str, api_url: str):
    """Update configuration values after CloudFormation deployment."""
    table_name = resolve_table_name(environment)
    table = get_table(region, table_name)
    now = datetime.now(timezone.utc).isoformat()

    updates = [
        ('cognito_user_pool_id', environment, user_pool_id, 'Cognito User Pool ID'),
        ('cognito_client_id', environment, client_id, 'Cognito App Client ID'),
        ('api_base_url', environment, api_url, 'API Gateway base URL'),
    ]

    print(f"Updating post-deployment configuration for env '{environment}' in table '{table_name}'...")
    for config_key, env, value, description in updates:
        if value:
            table.put_item(Item={
                'config_key': config_key,
                'environment': env,
                'value': value,
                'description': description,
                'updated_at': now,
                'updated_by': 'post_deployment_script'
            })
            print(f"✓ Updated: {config_key} = {value}")

    print("Post-deployment update complete!")

# --------------------------- CLI ---------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Seed app-config DynamoDB table')
    parser.add_argument('--region', default='us-west-2', help='AWS region')
    parser.add_argument('--environment', default='dev', choices=['dev', 'prod', 'staging'],
                        help='Environment name')
    parser.add_argument('--update-post-deploy', action='store_true',
                        help='Update config after CloudFormation deployment')
    parser.add_argument('--user-pool-id', help='Cognito User Pool ID (for post-deploy update)')
    parser.add_argument('--client-id', help='Cognito Client ID (for post-deploy update)')
    parser.add_argument('--api-url', help='API Gateway URL (for post-deploy update)')

    args = parser.parse_args()

    try:
        if args.update_post_deploy:
            if not all([args.user_pool_id, args.client_id, args.api_url]):
                print("Error: --update-post-deploy requires --user-pool-id, --client-id, and --api-url")
                exit(1)
            update_post_deployment(args.region, args.environment, args.user_pool_id,
                                   args.client_id, args.api_url)
        else:
            seed_config(args.region, args.environment)
    except Exception as e:
        print(f"ERROR: {e}")
        exit(1)
