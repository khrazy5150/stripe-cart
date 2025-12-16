#!/usr/bin/env bash
# destroy.sh
# Purpose: Cleanly tear down the stripe-cart stack and related resources.
# Reqs: bash, aws-cli v2, jq
#
# Typical usage for a stuck or ROLLBACK_COMPLETE stack:
#   ./destroy.sh --env dev --region us-west-2 --domain api-dev.juniorbay.com --hz-id ZHOSTEDZONE --force
#
# If you deployed with a custom SAM artifacts bucket:
#   ./destroy.sh --env dev --sam-bucket my-sam-artifacts --purge-sam-bucket
#
# Notes:
# - Will call ./destroy-apigw-domain.sh (if --domain is provided) BEFORE deleting the stack.
# - Empties & deletes the Order Management website bucket discovered from stack outputs.
# - Auto-detects and deletes all Lambda layers from the stack.
# - Then deletes the stack and waits.
# - Safe to re-run; missing resources are treated as already gone.

set -euo pipefail

# ---------- defaults ----------
ENV="dev"                                # dev|prod (matches deploy.sh)
REGION="${REGION:-us-west-2}"
STACK_NAME_PREFIX="${STACK_NAME_PREFIX:-stripe-cart-stack}"
STACK_NAME=""                             # absolute override (e.g., "stripe-cart-stack")
DOMAIN_NAME=""                            # e.g., api-dev.juniorbay.com
HOSTED_ZONE_ID=""                         # optional; auto-discover in destroy-apigw-domain.sh if omitted
KEEP_DNS=0                                # pass to destroy-apigw-domain.sh
SKIP_DOMAIN=0                             # skip domain teardown even if --domain provided
SAM_BUCKET=""                             # optional artifacts bucket to remove
PURGE_SAM_BUCKET=0                        # empty + delete artifacts bucket
KEEP_WEB_BUCKET=0                         # keep the website bucket
DRY_RUN=0
FORCE=0
ACCT_ID=$(aws sts get-caller-identity --query "Account" --output text)

# ---------- args ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) ENV="$2"; shift 2;;
    --region) REGION="$2"; shift 2;;
    --stack-prefix|--stack-name-prefix) STACK_NAME_PREFIX="$2"; shift 2;;
    --stack-name) STACK_NAME="$2"; shift 2;;                   # NEW: absolute override
    --domain) DOMAIN_NAME="$2"; shift 2;;
    --hz-id|--hosted-zone-id) HOSTED_ZONE_ID="$2"; shift 2;;
    --keep-dns) KEEP_DNS=1; shift 1;;
    --skip-domain) SKIP_DOMAIN=1; shift 1;;
    --sam-bucket|--artifact-bucket) SAM_BUCKET="$2"; shift 2;;
    --purge-sam-bucket) PURGE_SAM_BUCKET=1; shift 1;;
    --keep-web-bucket) KEEP_WEB_BUCKET=1; shift 1;;
    --dry-run) DRY_RUN=1; shift 1;;
    --force|-f) FORCE=1; shift 1;;
    --) shift; break;;
    *) echo "Unknown arg: $1" >&2; exit 1;;
  esac
done

# ---------- compute effective stack name ----------
STACK_NAME_NOTE=""
if [[ -z "$STACK_NAME" ]]; then
  STACK_NAME="${STACK_NAME_PREFIX}-${ENV}"
else
  STACK_NAME_NOTE="(override)"
fi

# ---------- sanity ----------
command -v aws >/dev/null || { echo "❌ AWS CLI not installed" >&2; exit 1; }
command -v jq  >/dev/null || { echo "❌ jq not installed" >&2; exit 1; }

echo "🧹 Destroy plan"
echo "  Stack:          $STACK_NAME $STACK_NAME_NOTE"
echo "  Region:         $REGION"
echo "  Environment:    $ENV"
echo "  Account ID:     $ACCT_ID"
echo "  Domain:         ${DOMAIN_NAME:-<none>}"
echo "  Hosted Zone:    ${HOSTED_ZONE_ID:-<auto>}"
echo "  Keep DNS:       $KEEP_DNS"
echo "  Keep web bucket:$KEEP_WEB_BUCKET"
echo "  SAM bucket:     ${SAM_BUCKET:-<none>} purge=$PURGE_SAM_BUCKET"
echo "  Dry-run:        $DRY_RUN"
echo

if [[ $FORCE -ne 1 ]]; then
  read -p "Proceed with destroy of ${STACK_NAME} in ${REGION}? (type 'destroy' to continue): " -r
  echo
  [[ "$REPLY" == "destroy" ]] || { echo "Aborted."; exit 1; }
fi

# ---------- helpers ----------
get_stack_status () {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "NOT_FOUND"
}

get_output () {
  local key="$1"
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey==\`$key\`].OutputValue" --output text 2>/dev/null || true
}

get_resource_physical_id () {
  local logical_id="$1"
  aws cloudformation list-stack-resources --region "$REGION" --stack-name "$STACK_NAME" \
    --query "StackResourceSummaries[?LogicalResourceId=='$logical_id'].PhysicalResourceId" --output text 2>/dev/null || true
}

empty_bucket () {
  local bucket="$1"
  echo "   - Emptying s3://${bucket} …"
  # Remove versions if versioned
  aws s3api list-object-versions --bucket "$bucket" --output json >/dev/null 2>&1 && {
    aws s3api delete-objects --bucket "$bucket" --delete "$(aws s3api list-object-versions --bucket "$bucket" \
      --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}, Quiet: true}' --output json)" >/dev/null 2>&1 || true
    aws s3api delete-objects --bucket "$bucket" --delete "$(aws s3api list-object-versions --bucket "$bucket" \
      --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}, Quiet: true}' --output json)" >/dev/null 2>&1 || true
  }
  aws s3 rm "s3://${bucket}" --recursive >/dev/null 2>&1 || true
}

# ---------- 0) Delete Lambda Layers ----------
echo "🔧 Cleaning up Lambda layers..."
LAYER_ARNS=$(aws cloudformation describe-stack-resources \
    --region "$REGION" \
    --stack-name "$STACK_NAME" \
    --query "StackResources[?ResourceType=='AWS::Lambda::LayerVersion'].PhysicalResourceId" \
    --output text 2>/dev/null || true)

if [[ -n "$LAYER_ARNS" && "$LAYER_ARNS" != "None" ]]; then
  echo "   Found layers in stack"
  for LAYER_ARN in $LAYER_ARNS; do
    # Extract layer name and version from ARN
    # Format: arn:aws:lambda:region:account:layer:name:version
    LAYER_NAME=$(echo "$LAYER_ARN" | awk -F: '{print $(NF-1)}')
    VERSION=$(echo "$LAYER_ARN" | awk -F: '{print $NF}')
    
    echo "   - Deleting layer: $LAYER_NAME version $VERSION"
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "     (dry-run) Would delete layer version"
    else
      aws lambda delete-layer-version --region "$REGION" --layer-name "$LAYER_NAME" --version-number "$VERSION" 2>/dev/null || true
    fi
  done
else
  echo "   No layers found in stack, checking for orphaned layers..."
  
  # Fallback: Search for layers by environment naming pattern
  LAYER_NAMES=$(aws lambda list-layers --region "$REGION" --query "Layers[?contains(LayerName, '${ENV}')].LayerName" --output text 2>/dev/null || true)
  
  if [[ -n "$LAYER_NAMES" ]]; then
    for LAYER_NAME in $LAYER_NAMES; do
      echo "   - Found orphaned layer: $LAYER_NAME"
      VERSIONS=$(aws lambda list-layer-versions --region "$REGION" --layer-name "$LAYER_NAME" --query 'LayerVersions[*].Version' --output text 2>/dev/null || true)
      
      if [[ -n "$VERSIONS" ]]; then
        for VERSION in $VERSIONS; do
          echo "     Deleting version: $VERSION"
          if [[ $DRY_RUN -eq 1 ]]; then
            echo "       (dry-run) Would delete layer version"
          else
            aws lambda delete-layer-version --region "$REGION" --layer-name "$LAYER_NAME" --version-number "$VERSION" 2>/dev/null || true
          fi
        done
      fi
    done
  else
    echo "   No orphaned layers found"
  fi
fi

# ---------- 0.5) Delete Landing Pages S3 Buckets ----------
echo "🪣 Cleaning up landing pages S3 buckets..."
for bucket in landing-pages-${ACCT_ID} landing-pages-preview-${ACCT_ID} landing-pages-${ENV}-${ACCT_ID} landing-pages-preview-${ENV}-${ACCT_ID}; do
  echo "   Checking bucket: $bucket"
  if aws s3 ls "s3://${bucket}" 2>/dev/null; then
    echo "   - Emptying and deleting: $bucket"
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "     (dry-run) Would empty and delete bucket"
    else
      empty_bucket "$bucket"
      aws s3api delete-bucket --bucket "$bucket" --region "$REGION" 2>/dev/null || true
    fi
  fi
done

# ---------- 1) Tear down custom domain (optional) ----------
if [[ -n "$DOMAIN_NAME" && $SKIP_DOMAIN -eq 0 ]]; then
  if [[ ! -x "./destroy-apigw-domain.sh" ]]; then
    echo "⚠️  ./destroy-apigw-domain.sh not found or not executable; skipping API domain teardown."
  else
    echo "🌐 Deleting API Gateway custom domain + mappings for ${DOMAIN_NAME} …"
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "   (dry-run) Would call: REGION=$REGION DOMAIN_NAME=$DOMAIN_NAME HOSTED_ZONE_ID=$HOSTED_ZONE_ID KEEP_DNS=$KEEP_DNS ./destroy-apigw-domain.sh --yes"
    else
      REGION="$REGION" DOMAIN_NAME="$DOMAIN_NAME" HOSTED_ZONE_ID="$HOSTED_ZONE_ID" \
      KEEP_DNS="$KEEP_DNS" ./destroy-apigw-domain.sh --yes
    fi
  fi
fi

# ---------- 2) Empty & delete the website bucket from stack outputs ----------
WEB_BUCKET=""
if [[ $KEEP_WEB_BUCKET -eq 0 ]]; then
  # Also check for order-mgmt bucket with environment pattern
  ORDER_MGMT_BUCKET="order-mgmt-${ENV}-${ACCT_ID}-${REGION}"
  if aws s3 ls "s3://${ORDER_MGMT_BUCKET}" 2>/dev/null; then
    echo "🪣 Removing Order Management bucket: ${ORDER_MGMT_BUCKET}"
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "   (dry-run) Would empty and delete"
    else
      empty_bucket "$ORDER_MGMT_BUCKET"
      aws s3api delete-bucket --bucket "$ORDER_MGMT_BUCKET" --region "$REGION" 2>/dev/null || true
    fi
  fi
  
  # Prefer Output key first (set in template.yaml as S3BucketName)
  WEB_BUCKET="$(get_output 'S3BucketName')"
  if [[ -z "$WEB_BUCKET" || "$WEB_BUCKET" == "None" ]]; then
    # Fallback: look up by logical id (OrderManagementBucket)
    WEB_BUCKET="$(get_resource_physical_id 'OrderManagementBucket')"
  fi

  if [[ -n "$WEB_BUCKET" && "$WEB_BUCKET" != "None" ]]; then
    echo "🪣 Removing Order Management website bucket: ${WEB_BUCKET}"
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "   (dry-run) Would empty and delete s3://${WEB_BUCKET}"
    else
      empty_bucket "$WEB_BUCKET"
      aws s3api delete-bucket --bucket "$WEB_BUCKET" --region "$REGION" 2>/dev/null || true
      echo "   - Deleted bucket ${WEB_BUCKET} (or it was already gone)"
    fi
  else
    echo "🪣 No additional website bucket found via outputs/resources"
  fi
else
  echo "🪣 KEEP_WEB_BUCKET=1 → leaving website bucket as-is."
fi

# ---------- 3) Delete the CloudFormation stack ----------
STATUS_BEFORE="$(get_stack_status)"
echo "🧨 Deleting stack ${STACK_NAME} (status: ${STATUS_BEFORE}) …"
if [[ $DRY_RUN -eq 1 ]]; then
  echo "   (dry-run) Would run: aws cloudformation delete-stack --region $REGION --stack-name $STACK_NAME"
else
  aws cloudformation delete-stack --region "$REGION" --stack-name "$STACK_NAME" || true
  echo "   Waiting for deletion to complete (this can take a few minutes)…"
  aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$STACK_NAME" || {
    echo "⚠️  Stack deletion waiter reported a non-success status. If the stack is stuck, check dependent resources in the console."
  }
fi

# If deletion didn't complete, surface common blockers
if [[ "$(get_stack_status)" != "NOT_FOUND" ]]; then
  echo "🔎 Investigating deletion blockers…"
  aws cloudformation list-stack-resources --region "$REGION" --stack-name "$STACK_NAME" \
    --query "StackResourceSummaries[?ResourceStatus=='DELETE_FAILED'].[LogicalResourceId,ResourceType,ResourceStatusReason]" \
    --output table || true
fi

# ---------- 4) Optionally purge SAM artifacts bucket ----------
# If user passed a bucket name, honor it. Otherwise, derive the same default name used by deploy.sh.
if [[ $PURGE_SAM_BUCKET -eq 1 ]]; then
  if [[ -z "$SAM_BUCKET" ]]; then
    AWS_ACCT="$(aws sts get-caller-identity --query Account --output text)"
    SAM_BUCKET="sam-artifacts-${AWS_ACCT}-${REGION}-stripe-cart"
    echo "📦 Deriving SAM artifacts bucket: s3://${SAM_BUCKET}"
  else
    echo "📦 Using provided SAM artifacts bucket: s3://${SAM_BUCKET}"
  fi

  if aws s3api head-bucket --bucket "$SAM_BUCKET" --region "$REGION" 2>/dev/null; then
    echo "📦 Purging SAM artifacts bucket: s3://${SAM_BUCKET}"
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "   (dry-run) Would empty and delete s3://${SAM_BUCKET}"
    else
      empty_bucket "$SAM_BUCKET"
      aws s3api delete-bucket --bucket "$SAM_BUCKET" --region "$REGION" 2>/dev/null || true
      echo "   - Deleted artifacts bucket ${SAM_BUCKET} (or it was already gone)"
    fi
  else
    echo "📦 Artifacts bucket ${SAM_BUCKET} not found; skipping."
  fi
else
  if [[ -n "$SAM_BUCKET" ]]; then
    echo "📦 --sam-bucket provided but --purge-sam-bucket not set → leaving it in place."
  else
    echo "📦 No artifacts bucket cleanup requested."
  fi
fi

echo
echo "✅ Destroy finished (stack=${STACK_NAME})."
echo "If this was for a ROLLBACK_COMPLETE recovery, you can now re-run deploy.sh to recreate a fresh stack."