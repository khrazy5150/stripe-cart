cd /Users/morpheus/Documents/Development/aws/sam/stripe-cart

# Defaults
REGION="us-west-2"
ENVIRONMENT="dev"
POOL_ID="us-west-2_gBemPnEF8"
CLIENT_ID="2cusn1634kl6hqgn78r0h346m0"
API_URL="https://api-dev.juniorbay.com"


# create a throwaway venv inside seeding/
python3 -m venv seeding/.venv
source seeding/.venv/bin/activate
python -m pip install --upgrade pip
python -m pip install boto3

# run your post-deploy update
python seeding/seed_app_config.py --region "$REGION" --environment "$ENVIRONMENT" \
  --update-post-deploy --user-pool-id "$POOL_ID" \
  --client-id "$CLIENT_ID" --api-url "$API_URL"

# when done
deactivate
