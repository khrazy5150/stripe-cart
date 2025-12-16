#!/usr/bin/env bash
# deploy.sh
# Purpose: Build & deploy the stripe-cart SAM stack per environment, with optional custom domain mapping.
# Reqs: bash, aws-cli v2, jq, sam-cli

set -euo pipefail
# set -x  # uncomment for debugging

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
SAM_EXTRA_ARGS="${SAM_EXTRA_ARGS:-}"        # pass-through to sam deploy

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

# ---------- Preflight: clean bad state ----------
STATUS=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "NOT_FOUND")
echo "Stack Status: $STATUS"
if [[ "$STATUS" == "ROLLBACK_COMPLETE" ]]; then
  echo "⚠️  Stack $STACK_NAME is in ROLLBACK_COMPLETE. Deleting it before redeploy…"
  aws cloudformation delete-stack --region "$REGION" --stack-name "$STACK_NAME"
  aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$STACK_NAME"
fi

# ---------- ensure artifacts bucket ----------
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

# ---------- outputs (null-safe) ----------
aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query "Stacks[0].Outputs" --output json > cfn_outputs.json

# Flatten to a Key->Value map
jq 'map({key:.OutputKey, value:.OutputValue}) | from_entries' cfn_outputs.json > outputs_map.json

API_BASE_URL="$(jq -r '.ApiBaseUrl // empty' outputs_map.json)"
REST_API_ID="$(jq -r '.RestApiId // empty' outputs_map.json)"
STAGE_NAME_OUT="$(jq -r '.StageName // empty' outputs_map.json)"

# Fallbacks if some outputs are missing (shouldn’t be, but safe)
if [[ -z "$REST_API_ID" ]]; then
  REST_API_ID="$(aws cloudformation list-stack-resources --region "$REGION" --stack-name "$STACK_NAME" \
    --query "StackResourceSummaries[?LogicalResourceId=='RestApi'].PhysicalResourceId" --output text 2>/dev/null || true)"
fi
if [[ -z "$STAGE_NAME_OUT" ]]; then
  STAGE_NAME_OUT="$ENV"
fi
if [[ -z "$API_BASE_URL" && -n "$REST_API_ID" ]]; then
  API_BASE_URL="https://${REST_API_ID}.execute-api.${REGION}.amazonaws.com/${STAGE_NAME_OUT}"
fi

# ---------- pretty endpoint list ----------
EXEC_BASE="$API_BASE_URL"
echo "==> Execute-API Base: ${EXEC_BASE}/"
echo "    Config:            ${EXEC_BASE}/config"
echo "    Admin Orders:      ${EXEC_BASE}/admin/orders"
echo "    Webhook pattern:   ${EXEC_BASE}/webhook/{token}"
echo

# ---------- optional custom domain mapping ----------
if [[ -n "$DOMAIN_NAME" ]]; then
  if [[ ! -x "$SCRIPT_DIR/add-apigw-domain.sh" ]]; then
    echo "❌ add-apigw-domain.sh not found or not executable at $SCRIPT_DIR" >&2
    exit 1
  fi
  echo "🌐 Mapping custom domain '${DOMAIN_NAME}' to API ${REST_API_ID} (stage ${STAGE_NAME_OUT})..."
  API_ID="$REST_API_ID" REGION="$REGION" DOMAIN_NAME="$DOMAIN_NAME" HOSTED_ZONE_ID="$HOSTED_ZONE_ID" \
  STAGE="$STAGE_NAME_OUT" ENDPOINT_TYPE="$ENDPOINT_TYPE" BASE_PATH="$BASE_PATH" \
  "$SCRIPT_DIR/add-apigw-domain.sh"

  if [[ -z "$BASE_PATH" ]]; then
    BASE_CUSTOM="https://${DOMAIN_NAME}/${STAGE_NAME_OUT}"
  else
    BASE_CUSTOM="https://${DOMAIN_NAME}/${BASE_PATH}/${STAGE_NAME_OUT}"
  fi
  echo "==> Custom Domain Base: ${BASE_CUSTOM}/"
  echo "    Config:             ${BASE_CUSTOM}/config"
  echo "    Admin Orders:       ${BASE_CUSTOM}/admin/orders"
  echo "    Webhook pattern:    ${BASE_CUSTOM}/webhook/{token}"
fi

# ---------- cleanup temp files ----------
rm -f cfn_outputs.json outputs_map.json

echo "✅ Done."
