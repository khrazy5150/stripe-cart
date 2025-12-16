#!/usr/bin/env python3
"""
Seed script for app-config-{env} DynamoDB tables.

- Seeds all 'global' keys
- Seeds only the current env's keys ('dev' -> app-config-dev, 'prod' -> app-config-prod)
- Supports a post-deploy update step to fill values that are only known after deployment

Usage:
  python seed_app_config.py --region us-west-2 --environment dev
  python seed_app_config.py --region us-west-2 --environment prod

  # Post-deploy updates for the same env table:
  python seed_app_config.py --region us-west-2 --environment dev \
      --update-post-deploy --user-pool-id <ID> --client-id <ID> --api-url https://api-dev.juniorbay.com
"""

import argparse
import sys
from datetime import datetime, timezone
import boto3

VALID_ENVS = {"dev", "prod"}

def table_name_for_app_config(env: str) -> str:
    if env not in VALID_ENVS:
        raise ValueError(f"Unsupported environment '{env}'. Choose from {sorted(VALID_ENVS)}.")
    return f"app-config-{env}"

def seed_config(region: str, environment: str) -> None:
    table_name = table_name_for_app_config(environment)
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)
    now = datetime.now(timezone.utc).isoformat()

    # ----------------------------
    # GLOBAL KEYS (inserted into both tables)
    # ----------------------------
    global_items = [
        ('stripe_api_version', 'global', '2023-10-16', 'Stripe API version to use'),
        ('frontend_test_dir',  'global', 'dist',        'Frontend test directory name'),
        ('stripe_api_base_url','global', 'https://api.stripe.com', 'Stripe API base URL'),
        ('offers_enabled',     'global', 'true',        'Enable/disable offers system globally'),
        ('offers_require_auth','global', 'false',       'Offers require authentication'),
        ('offers_default_countdown_minutes', 'global', '5',   'Default countdown timer in minutes'),
        ('offers_analytics_enabled', 'global', 'true',       'Track offer analytics'),
        ('offers_cache_ttl_seconds', 'global', '300',         'Cache TTL for offer configurations'),
        # Checkout URL templates stay global and resolve placeholders at runtime
        ('checkout_success_url_template', 'global',
         '{offers_base_url}{client_id}/{offer_path}/thank-you.html?session_id={{CHECKOUT_SESSION_ID}}',
         'Success URL template for Stripe checkout'),
        ('checkout_cancel_url_template', 'global',
         '{offers_base_url}{client_id}/{offer_path}/checkout.html',
         'Cancel URL template for Stripe checkout'),
        ('checkout_upsell_url_template', 'global',
         '{offers_base_url}{client_id}/{offer_path}/upsell.html?session_id={{CHECKOUT_SESSION_ID}}',
         'Upsell URL template'),
    ]

    # ----------------------------
    # ENV-SPECIFIC KEYS
    # Only the block matching `environment` will be written.
    # ----------------------------
    dev_items = [
        # Public/admin config endpoints (DEV custom domain; adjust if your base-path mapping is (none))
        ('public_config_url', 'dev', 'https://api-dev.juniorbay.com/dev/config',
         'Dev config endpoint consumed by config.js'),
        ('admin_config_url',  'dev', 'https://api-dev.juniorbay.com/dev/admin/app-config',
         'Admin Configuration URL in Dev'),
        ('cognito_region',    'dev', 'us-west-2', 'Cognito region (dev)'),

        # Frontend bases (dev)
        ('frontend_base_url', 'dev', 'http://127.0.0.1:5500', 'Admin app base URL (local dev)'),
        ('offers_base_url',   'dev', 'http://127.0.0.1:5500/dist/', 'Offer pages base (local dev)'),
        ('offers_url_pattern','dev', '{offers_base_url}{client_id}/{offer_path}/', 'Offer URL pattern'),

        # S3 buckets (dev)
        ('landing_page_preview_bucket', 'dev', 'landing-pages-preview-dev-150544707159', 'Preview LP bucket (dev)'),
        ('landing_page_bucket',         'dev', 'landing-pages-dev-150544707159',         'Published LP bucket (dev)'),
        ('order_mgmt_bucket',           'dev', 'order-mgmt-dev-150544707159-us-west-2',  'Order mgmt site bucket (dev)'),

        # Deployment method (dev)
        ('landing_page_deploy_method',  'dev', 'local',  'Deployment method: local|s3|cloudfront'),
    ]

    prod_items = [
        # Public/admin config endpoints (PROD)
        ('public_config_url', 'prod', 'https://checkout.juniorbay.com/prod/config',
         'Prod config endpoint consumed by config.js'),
        ('admin_config_url',  'prod', 'https://checkout.juniorbay.com/prod/admin/app-config',
         'Admin Configuration URL in Prod'),
        ('cognito_region',    'prod', 'us-west-2', 'Cognito region (prod)'),

        # Frontend bases (prod)
        ('frontend_base_url', 'prod', 'https://juniorbay.com', 'Admin app base URL (prod)'),
        ('offers_base_url',   'prod', 'https://juniorbay.com/tenants/', 'Offer pages base (prod)'),
        ('offers_url_pattern','prod', '{offers_base_url}{client_id}/{offer_path}/', 'Offer URL pattern'),

        # S3 buckets (prod)
        ('landing_page_preview_bucket', 'prod', 'landing-pages-preview-prod-150544707159', 'Preview LP bucket (prod)'),
        ('landing_page_bucket',         'prod', 'landing-pages-prod-150544707159',         'Published LP bucket (prod)'),
        ('order_mgmt_bucket',           'prod', 'order-mgmt-prod-150544707159-us-west-2',  'Order mgmt site bucket (prod)'),

        # Deployment method (prod)
        ('landing_page_deploy_method',  'prod', 's3', 'Deployment method: local|s3|cloudfront'),
        ('landing_page_cloudfront_id',  'prod', '',   'CloudFront distribution ID for offers (if using CloudFront)'),
        ('landing_page_invalidation_enabled', 'prod', 'true', 'Auto-invalidate CloudFront cache on deploy'),
    ]

    # Choose env block
    env_items = dev_items if environment == "dev" else prod_items

    items = global_items + env_items

    print(f"Seeding table '{table_name}' with {len(items)} items "
          f"(env={environment}) in region {region}...\n")

    ok = err = 0
    for config_key, env, value, description in items:
        try:
            table.put_item(Item={
                "config_key":  config_key,
                "environment": env,           # NOTE: 'global' or the env we’re inserting (dev|prod)
                "value":       value,
                "description": description,
                "updated_at":  now,
                "updated_by":  "seed_script",
            })
            print(f"✓ {config_key} ({env})")
            ok += 1
        except Exception as e:
            print(f"✗ {config_key} ({env}) -> {e}")
            err += 1

    print(f"\nSeeding complete. Success={ok}, Errors={err}")

def update_post_deployment(region: str, environment: str, user_pool_id: str, client_id: str, api_url: str) -> None:
    table_name = table_name_for_app_config(environment)
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)
    now = datetime.now(timezone.utc).isoformat()

    updates = [
        ("cognito_user_pool_id", environment, user_pool_id, "Cognito User Pool ID"),
        ("cognito_client_id",    environment, client_id,    "Cognito App Client ID"),
        ("api_base_url",         environment, api_url,      "API Gateway base URL (custom domain)"),
    ]

    print(f"Updating post-deployment values in '{table_name}' for env={environment}...")
    for config_key, env, value, description in updates:
        if value:
            table.put_item(Item={
                "config_key":  config_key,
                "environment": env,
                "value":       value,
                "description": description,
                "updated_at":  now,
                "updated_by":  "post_deploy",
            })
            print(f"✓ {config_key} = {value}")
    print("Post-deployment update complete.")

def main():
    ap = argparse.ArgumentParser(description="Seed app-config DynamoDB table")
    ap.add_argument("--region", default="us-west-2")
    ap.add_argument("--environment", default="dev", choices=sorted(VALID_ENVS))
    ap.add_argument("--update-post-deploy", action="store_true")
    ap.add_argument("--user-pool-id")
    ap.add_argument("--client-id")
    ap.add_argument("--api-url")
    args = ap.parse_args()

    if args.update_post_deploy:
        missing = [n for n, v in {
            "--user-pool-id": args.user_pool_id,
            "--client-id":    args.client_id,
            "--api-url":      args.api_url,
        }.items() if not v]
        if missing:
            print(f"Error: --update-post-deploy requires {', '.join(missing)}")
            sys.exit(1)
        update_post_deployment(args.region, args.environment, args.user_pool_id, args.client_id, args.api_url)
    else:
        seed_config(args.region, args.environment)

if __name__ == "__main__":
    main()