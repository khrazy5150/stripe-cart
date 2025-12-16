#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# Locate repo root (directory containing template.yaml)
###############################################################################

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$SCRIPT_DIR"

if [[ ! -f "$REPO_ROOT/template.yaml" ]]; then
  # If script is in e.g. scripts/, go one level up
  if [[ -f "$SCRIPT_DIR/../template.yaml" ]]; then
    REPO_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
  fi
fi

if [[ ! -f "$REPO_ROOT/template.yaml" ]]; then
  echo "❌ Could not find template.yaml near this script."
  echo "   Place deploy.sh in the repo root or in a subfolder directly under it (e.g. scripts/)."
  exit 1
fi

CORE_TEMPLATE="$REPO_ROOT/template.yaml"          # core: Stripe + landing pages
VIDEO_TEMPLATE="$REPO_ROOT/template-new.yaml"     # video extension

###############################################################################
# Config – adjust naming as you like
###############################################################################

REGION="${AWS_REGION:-us-west-2}"

CORE_STACK_BASE="stripe-cart-stack"
VIDEO_STACK_BASE="video-extension-stack"

# KMS key is passed in via environment variable for security
: "${STRIPE_KMS_KEY_ARN:?You must set STRIPE_KMS_KEY_ARN in your environment}"

###############################################################################
# Helpers
###############################################################################

usage() {
  cat <<EOF
Usage: $(basename "$0") <core|video|all> <dev|prod> [sam-build-flags...]

Examples:
  $(basename "$0") core dev        # deploy only core stack to dev
  $(basename "$0") video prod      # deploy only video stack to prod
  $(basename "$0") all dev         # deploy core then video to dev

Environment variables:
  AWS_REGION           (default: us-west-2)
  STRIPE_KMS_KEY_ARN   (required)
EOF
  exit 1
}

if [[ $# -lt 2 ]]; then
  usage
fi

TARGET="$1"   # core | video | all
ENV="$2"      # dev | prod
shift 2
SAM_BUILD_FLAGS=("$@")

if [[ "$ENV" != "dev" && "$ENV" != "prod" ]]; then
  echo "Environment must be dev or prod, got: $ENV" >&2
  exit 1
fi

CORE_STACK_NAME="${CORE_STACK_BASE}-${ENV}"
VIDEO_STACK_NAME="${VIDEO_STACK_BASE}-${ENV}"

echo "➡️  Repo root:  $REPO_ROOT"
echo "➡️  Target:     $TARGET"
echo "➡️  Environment:$ENV"
echo "➡️  Region:     $REGION"
echo "➡️  Core stack: $CORE_STACK_NAME"
echo "➡️  Video stack:$VIDEO_STACK_NAME"
echo

###############################################################################
# Deploy core (Stripe + landing pages)
###############################################################################
deploy_core() {
  echo "=============================="
  echo " Building CORE stack: $CORE_STACK_NAME"
  echo "=============================="

  CORE_BUILD_DIR="$REPO_ROOT/.aws-sam/core-$ENV"

  sam build \
    --template-file "$CORE_TEMPLATE" \
    --build-dir "$CORE_BUILD_DIR" \
    "${SAM_BUILD_FLAGS[@]}"

  echo "=============================="
  echo " Deploying CORE stack: $CORE_STACK_NAME"
  echo "=============================="

  sam deploy \
    --template-file "$CORE_BUILD_DIR/template.yaml" \
    --stack-name "$CORE_STACK_NAME" \
    --region "$REGION" \
    --resolve-s3 \
    --capabilities CAPABILITY_NAMED_IAM \
    --no-confirm-changeset \
    --no-fail-on-empty-changeset \
    --parameter-overrides \
      Environment="$ENV" \
      StripeKmsKeyArn="$STRIPE_KMS_KEY_ARN"

  echo "✅ Core stack deployed."

  echo "Fetching RestApiId output..."
  REST_API_ID=$(aws cloudformation describe-stacks \
    --stack-name "$CORE_STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='RestApiId'].OutputValue" \
    --output text)

  if [[ -z "$REST_API_ID" || "$REST_API_ID" == "None" ]]; then
    echo "❌ Could not find RestApiId output on core stack." >&2
    exit 1
  fi

  echo "Core RestApiId: $REST_API_ID"

  # Try to fetch a user pool ID if your core stack exports one.
  EXISTING_USER_POOL_ID=$(aws cloudformation describe-stacks \
    --stack-name "$CORE_STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='UserPoolId' || OutputKey=='AdminUserPoolId'].OutputValue | [0]" \
    --output text 2>/dev/null || echo "")

  if [[ "$EXISTING_USER_POOL_ID" == "None" ]]; then
    EXISTING_USER_POOL_ID=""
  fi

  echo "ExistingUserPoolId (if any): ${EXISTING_USER_POOL_ID:-<none>}"

  # Export for later functions in this shell
  export REST_API_ID
  export EXISTING_USER_POOL_ID
}

###############################################################################
# Deploy video extension
###############################################################################
deploy_video() {
  echo "=============================="
  echo " Building VIDEO stack: $VIDEO_STACK_NAME"
  echo "=============================="

  VIDEO_BUILD_DIR="$REPO_ROOT/.aws-sam/video-$ENV"

  sam build \
    --template-file "$VIDEO_TEMPLATE" \
    --build-dir "$VIDEO_BUILD_DIR" \
    "${SAM_BUILD_FLAGS[@]}"

  echo "=============================="
  echo " Deploying VIDEO stack: $VIDEO_STACK_NAME"
  echo "=============================="

  # If REST_API_ID is not already set (e.g. user ran 'video' only),
  # pull it from the core stack outputs.
  if [[ -z "${REST_API_ID:-}" || "$REST_API_ID" == "None" ]]; then
    echo "Looking up RestApiId from core stack: $CORE_STACK_NAME"
    REST_API_ID=$(aws cloudformation describe-stacks \
      --stack-name "$CORE_STACK_NAME" \
      --region "$REGION" \
      --query "Stacks[0].Outputs[?OutputKey=='RestApiId'].OutputValue" \
      --output text)

    if [[ -z "$REST_API_ID" || "$REST_API_ID" == "None" ]]; then
      echo "❌ Could not resolve RestApiId from core stack. Is the core stack deployed?" >&2
      exit 1
    fi
  fi

  # Same for ExistingUserPoolId – optional
  if [[ -z "${EXISTING_USER_POOL_ID:-}" ]]; then
    EXISTING_USER_POOL_ID=$(aws cloudformation describe-stacks \
      --stack-name "$CORE_STACK_NAME" \
      --region "$REGION" \
      --query "Stacks[0].Outputs[?OutputKey=='UserPoolId' || OutputKey=='AdminUserPoolId'].OutputValue | [0]" \
      --output text 2>/dev/null || echo "")

    if [[ "$EXISTING_USER_POOL_ID" == "None" ]]; then
      EXISTING_USER_POOL_ID=""
    fi
  fi

  echo "Using RestApiId:          $REST_API_ID"
  echo "Using ExistingUserPoolId: ${EXISTING_USER_POOL_ID:-<none>}"
  echo

  sam deploy \
    --template-file "$VIDEO_BUILD_DIR/template.yaml" \
    --stack-name "$VIDEO_STACK_NAME" \
    --region "$REGION" \
    --resolve-s3 \
    --capabilities CAPABILITY_NAMED_IAM \
    --no-confirm-changeset \
    --no-fail-on-empty-changeset \
    --parameter-overrides \
      Environment="$ENV" \
      StripeKmsKeyArn="$STRIPE_KMS_KEY_ARN" \
      ExistingRestApiId="$REST_API_ID" \
      ExistingUserPoolId="${EXISTING_USER_POOL_ID:-}"

  echo "✅ Video stack deployed."
}

###############################################################################
# Dispatch
###############################################################################

case "$TARGET" in
  core)
    deploy_core
    ;;
  video)
    deploy_video
    ;;
  all)
    deploy_core
    deploy_video
    ;;
  *)
    usage
    ;;
esac
