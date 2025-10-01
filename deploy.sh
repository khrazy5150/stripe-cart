#!/usr/bin/env bash
# deploy.sh
# Purpose: Build & deploy the stripe-cart SAM stack per environment, with optional custom domain mapping.
# Reqs: bash, aws-cli v2, jq, sam-cli

set -euo pipefail
# Uncomment for one verbose run:
# set -x

# ---------- defaults ----------
ENV="dev"                                  # dev|prod (matches API stage)
REGION="${REGION:-us-west-2}"
STACK_NAME_PREFIX="${STACK_NAME_PREFIX:-stripe-cart-stack}"
STACK_NAME=""
KMS_ARN=""
S3_BUCKET=""                                # optional; if blank, script derives & ensures one
USE_CONTAINER=0
CONFIRM=0
DOMAIN_NAME=""
HOSTED_ZONE_ID=""
ENDPOINT_TYPE="REGIONAL"                    # REGIONAL|EDGE
BASE_PATH=""                                # "" = root mapping
SAM_CAPS="CAPABILITY_IAM"
SAM_EXTRA_ARGS="${SAM_EXTRA_ARGS:-}"        # pass-through to sam deploy (e.g., "--debug")

# Preflight: if a previous attempt left the stack in ROLLBACK_COMPLETE, delete it first.
STATUS=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "NOT_FOUND")
echo "Stack Status: $STATUS"
if [[ "$STATUS" == "ROLLBACK_COMPLETE" ]]; then
  echo "⚠️  Stack $STACK_NAME is in ROLLBACK_COMPLETE. Deleting it before redeploy…"
  aws cloudformation delete-stack --region "$REGION" --stack-name "$STACK_NAME"
  aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$STACK_NAME"
fi


# ---------- args ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) ENV="$2"; shift 2;;
    --region) REGION="$2"; shift 2;;
    --stack-prefix|--stack-name-prefix) STACK_NAME_PREFIX="$2"; shift 2;;
    --kms-arn) KMS_ARN="$2"; shift 2;;
    --s3-bucket) S3_BUCKET="$2"; shift 2;;
    --use-container) USE_CONTAINER=1; shift 1;;
    --confirm|--confirm-changeset) CONFIRM=1; shift 1;;
    --domain) DOMAIN_NAME="$2"; shift 2;;
    --hz-id|--hosted-zone-id) HOSTED_ZONE_ID="$2"; shift 2;;
    --endpoint) ENDPOINT_TYPE="$2"; shift 2;;
    --base-path) BASE_PATH="$2"; shift 2;;
    --caps) SAM_CAPS="$2"; shift 2;;
    --) shift; break;;
    *) echo "Unknown arg: $1" >&2; exit 1;;
  esac
done

if [[ -z "$KMS_ARN" ]]; then
  echo "ERROR: --kms-arn (KMS CMK ARN in ${REGION}) is required." >&2
  exit 1
fi
if [[ "$ENDPOINT_TYPE" != "REGIONAL" && "$ENDPOINT_TYPE" != "EDGE" ]]; then
  echo "ERROR: --endpoint must be REGIONAL or EDGE" >&2
  exit 1
fi

STACK_NAME="${STACK_NAME_PREFIX}-${ENV}"

# ---------- sanity checks ----------
command -v aws >/dev/null || { echo "❌ AWS CLI not installed" >&2; exit 1; }
command -v sam >/dev/null || { echo "❌ SAM CLI not installed" >&2; exit 1; }
command -v jq  >/dev/null || { echo "❌ jq not installed" >&2; exit 1; }

# ---------- ensure artifacts bucket (always) ----------
AWS_ACCT="$(aws sts get-caller-identity --query Account --output text)"
if [[ -z "$S3_BUCKET" ]]; then
  S3_BUCKET="sam-artifacts-${AWS_ACCT}-${REGION}-stripe-cart"
fi
echo "📦 Ensuring SAM artifacts bucket: s3://${S3_BUCKET}"
aws s3 mb "s3://${S3_BUCKET}" --region "$REGION" 2>/dev/null || true

# ---------- resolve paths ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE_FILE="$REPO_ROOT/template.yaml"
BUILD_DIR="$REPO_ROOT/.aws-sam/build"

echo "🚀 Deploying Stripe Cart"
echo "    Stack:        $STACK_NAME"
echo "    Env/Stage:    $ENV"
echo "    Region:       $REGION"
echo "    KMS ARN:      $KMS_ARN"
echo "    Artifacts:    s3://$S3_BUCKET"
echo "    Template:     $TEMPLATE_FILE"
echo "    BuildDir:     $BUILD_DIR"
echo "    Domain:       ${DOMAIN_NAME:-<none>} (${ENDPOINT_TYPE})"
echo

# ---------- build ----------
echo "🔨 SAM build..."
BUILD_ARGS=( --template-file "$TEMPLATE_FILE" --build-dir "$BUILD_DIR" )
[[ $USE_CONTAINER -eq 1 ]] && BUILD_ARGS+=( --use-container )
sam build "${BUILD_ARGS[@]}"

# ---------- deploy ----------
echo "🚀 SAM deploy..."
DEPLOY_ARGS=(
  --template-file "$BUILD_DIR/template.yaml"
  --region "$REGION"
  --stack-name "$STACK_NAME"
  --s3-bucket "$S3_BUCKET"
  --parameter-overrides Environment="$ENV" StripeKmsKeyArn="$KMS_ARN"
  --capabilities "$SAM_CAPS"
)
[[ $CONFIRM -eq 1 ]] && DEPLOY_ARGS+=( --confirm-changeset )
sam deploy "${DEPLOY_ARGS[@]}" $SAM_EXTRA_ARGS
echo "📋 Deployment complete."
echo

# ---------- API discovery ----------
get_api_id () {
  aws cloudformation list-stack-resources --region "$REGION" --stack-name "$STACK_NAME" \
    --query "StackResourceSummaries[?LogicalResourceId=='ApiGateway'].PhysicalResourceId" \
    --output text
}
API_ID="$(get_api_id)"
STAGE="$ENV"
EXEC_BASE="https://${API_ID}.execute-api.${REGION}.amazonaws.com/${STAGE}"
echo "==> Execute-API Base: ${EXEC_BASE}/"
echo "    Orders:            ${EXEC_BASE}/orders"
echo "    Webhook pattern:   ${EXEC_BASE}/webhook/{clientID}"
echo

# ---------- optional custom domain mapping ----------
if [[ -n "$DOMAIN_NAME" ]]; then
  if [[ ! -x "$SCRIPT_DIR/add-apigw-domain.sh" ]]; then
    echo "❌ add-apigw-domain.sh not found or not executable at $SCRIPT_DIR" >&2
    exit 1
  fi
  echo "🌐 Mapping custom domain '${DOMAIN_NAME}' to API ${API_ID} (stage ${STAGE})..."
  API_ID="$API_ID" REGION="$REGION" DOMAIN_NAME="$DOMAIN_NAME" HOSTED_ZONE_ID="$HOSTED_ZONE_ID" \
  STAGE="$STAGE" ENDPOINT_TYPE="$ENDPOINT_TYPE" BASE_PATH="$BASE_PATH" \
  "$SCRIPT_DIR/add-apigw-domain.sh"

  if [[ -z "$BASE_PATH" ]]; then
    BASE_CUSTOM="https://${DOMAIN_NAME}/${STAGE}"
  else
    BASE_CUSTOM="https://${DOMAIN_NAME}/${BASE_PATH}/${STAGE}"
  fi
  echo "==> Custom Domain Base: ${BASE_CUSTOM}/"
  echo "    Orders:             ${BASE_CUSTOM}/orders"
  echo "    Webhook pattern:    ${BASE_CUSTOM}/webhook/{clientID}"
fi

# ---------- optional cleanup prompt ----------
# NOTE: only do this if you passed a *temporary* bucket; normally KEEP it.
if [[ "${CLEANUP_ARTIFACTS:-0}" -eq 1 ]]; then
  read -p "🗑️  Clean up the SAM artifacts bucket s3://${S3_BUCKET}? (y/N): " -r
  echo
  if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    aws s3 rb "s3://${S3_BUCKET}" --force
    echo "✅ Deployment bucket cleaned up."
  fi
fi

echo "✅ Done."
echo "Next steps:"
echo "  • Insert/verify a tenant row in 'stripe_keys' (mode=test) and set the Stripe webhook to the URL above."
echo "  • Frontend calls anonymous shopper POSTs with 'X-Client-Id' for now."
echo "  • Flip tenant 'mode' to 'live' when ready—no redeploy needed."
