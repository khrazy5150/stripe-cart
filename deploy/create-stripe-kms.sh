#!/usr/bin/env bash
# create-stripe-kms.sh
# Purpose: Create/reuse a customer-managed KMS key for Stripe secrets, attach alias, enable rotation,
#          optionally grant your Lambda role Decrypt, PRINT/APPLY a minimal key policy,
#          and ENCRYPT plaintext (outputs ENCRYPTED(<Base64Ciphertext>)).
# Reqs: bash, aws-cli v2, jq
#
# Quick start:
#   ./create-stripe-kms.sh
#
# Encrypt a secret (prints ENCRYPTED(...)):
#   ./create-stripe-kms.sh --encrypt "sk_test_************************"
#
# With stack discovery for Lambda role:
#   ./create-stripe-kms.sh --stack-name stripe-cart-dev
#
# Print policy (no changes):
#   ./create-stripe-kms.sh --print-policy [--role-arn <arn>] [--no-context]
#
# APPLY policy (DANGER: replaces existing 'default' policy):
#   ./create-stripe-kms.sh --apply-policy --role-arn <arn> [--no-context] [--force]

set -euo pipefail

ALIAS_NAME="alias/stripe-secrets"
REGION="${REGION:-us-west-2}"
STACK_NAME=""
ROLE_ARN=""
ENABLE_ROTATION=1
PRINT_POLICY=0
APPLY_POLICY=0
FORCE=0
USE_CONTEXT=1                 # adds an EncryptionContext condition
CONTEXT_KEY="app"
CONTEXT_VAL="stripe-cart"
ENCRYPT_VALUE=""              # if set, we perform kms encrypt and print ENCRYPTED(...)
TMP_DIR="$(mktemp -d)"; trap 'rm -rf "$TMP_DIR"' EXIT

# ---------- args ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --alias) ALIAS_NAME="alias/${2#alias/}"; shift 2;;
    --region) REGION="$2"; shift 2;;
    --stack-name) STACK_NAME="$2"; shift 2;;
    --role-arn) ROLE_ARN="$2"; shift 2;;
    --no-rotation) ENABLE_ROTATION=0; shift 1;;
    --print-policy) PRINT_POLICY=1; shift 1;;
    --apply-policy) APPLY_POLICY=1; shift 1;;
    --force) FORCE=1; shift 1;;
    --no-context) USE_CONTEXT=0; shift 1;;
    --context-key) CONTEXT_KEY="$2"; shift 2;;
    --context-val) CONTEXT_VAL="$2"; shift 2;;
    --encrypt) ENCRYPT_VALUE="$2"; shift 2;;
    --help|-h)
      sed -n '1,220p' "$0"; exit 0;;
    *) echo "Unknown arg: $1" >&2; exit 1;;
  esac
done

# ---------- prereqs ----------
command -v aws >/dev/null || { echo "❌ AWS CLI v2 required" >&2; exit 1; }
command -v jq  >/dev/null || { echo "❌ jq required" >&2; exit 1; }

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ACCOUNT_ROOT_ARN="arn:aws:iam::${ACCOUNT_ID}:root"

# If stack provided, discover role ARN from Outputs (OutputKey: StripeCartFunctionIamRole)
if [[ -n "$STACK_NAME" && -z "$ROLE_ARN" ]]; then
  ROLE_ARN="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
    --query 'Stacks[0].Outputs[?OutputKey==`StripeCartFunctionIamRole`].OutputValue' --output text || true)"
  [[ "$ROLE_ARN" == "None" ]] && ROLE_ARN=""
fi

# ---------- helpers ----------
build_policy_json() {
  local role="${ROLE_ARN:-arn:aws:iam::${ACCOUNT_ID}:role/YOUR-LAMBDA-ROLE}"
  if [[ $USE_CONTEXT -eq 1 ]]; then
    jq -n --arg root "$ACCOUNT_ROOT_ARN" --arg role "$role" --arg ck "$CONTEXT_KEY" --arg cv "$CONTEXT_VAL" '
    {"Version":"2012-10-17","Statement":[
      {"Sid":"AllowAccountRootKeyAdmin","Effect":"Allow","Principal":{"AWS":$root},"Action":"kms:*","Resource":"*"},
      {"Sid":"AllowUseOfKeyFromLambdaRole","Effect":"Allow","Principal":{"AWS":$role},
       "Action":["kms:Decrypt","kms:DescribeKey"],"Resource":"*",
       "Condition":{"StringEquals":{("kms:EncryptionContext:"+$ck):$cv}}}
    ]}'
  else
    jq -n --arg root "$ACCOUNT_ROOT_ARN" --arg role "$role" '
    {"Version":"2012-10-17","Statement":[
      {"Sid":"AllowAccountRootKeyAdmin","Effect":"Allow","Principal":{"AWS":$root},"Action":"kms:*","Resource":"*"},
      {"Sid":"AllowUseOfKeyFromLambdaRole","Effect":"Allow","Principal":{"AWS":$role},
       "Action":["kms:Decrypt","kms:DescribeKey"],"Resource":"*"}
    ]}'
  fi
}

ensure_key_and_alias() {
  local existing_arn
  existing_arn="$(aws kms describe-key --region "$REGION" --key-id "$ALIAS_NAME" \
                  --query 'KeyMetadata.Arn' --output text 2>/dev/null || true)"
  if [[ -n "$existing_arn" && "$existing_arn" != "None" ]]; then
    echo "$existing_arn"; return 0
  fi
  >&2 echo "==> Creating new KMS key in $REGION…"
  local key_id
  key_id="$(aws kms create-key --region "$REGION" \
              --description "Stripe secrets CMK (encrypt Stripe sk_* blobs)" \
              --key-usage ENCRYPT_DECRYPT --origin AWS_KMS \
              --query 'KeyMetadata.KeyId' --output text)"
  >&2 echo "==> Attaching alias $ALIAS_NAME -> $key_id"
  aws kms create-alias --region "$REGION" --alias-name "$ALIAS_NAME" --target-key-id "$key_id"
  aws kms describe-key --region "$REGION" --key-id "$key_id" --query 'KeyMetadata.Arn' --output text
}

enable_rotation() {
  >&2 echo "==> Enabling key rotation (yearly)…"
  aws kms enable-key-rotation --region "$REGION" --key-id "$1" >/dev/null || true
}

grant_decrypt() {
  local key_arn="$1"; local principal="$2"
  [[ -z "$principal" ]] && return 0
  >&2 echo "==> Creating Decrypt grant for principal: $principal"
  aws kms create-grant --region "$REGION" \
    --key-id "$key_arn" --grantee-principal "$principal" \
    --operations Decrypt >/dev/null || true
}

backup_existing_policy() {
  local key_arn="$1"; local out="$TMP_DIR/current-policy.json"
  aws kms get-key-policy --region "$REGION" --key-id "$key_arn" --policy-name default \
    --query 'Policy' --output text 2>/dev/null > "$out" || true
  [[ -s "$out" ]] || echo "{}" > "$out"
  echo "$out"
}

apply_policy() {
  local key_arn="$1"; local policy_file="$2"
  echo "==> WARNING: This will REPLACE the key's 'default' policy." >&2
  if [[ $FORCE -ne 1 ]]; then
    read -p "Proceed to apply policy to ${key_arn}? (type 'apply' to continue): " -r
    echo
    [[ "$REPLY" == "apply" ]] || { echo "Aborted." >&2; exit 1; }
  fi
  aws kms put-key-policy --region "$REGION" \
    --key-id "$key_arn" --policy-name default --policy "file://$policy_file"
  >&2 echo "==> Policy applied."
}

encrypt_value() {
  local plaintext="$1"
  local args=(--region "$REGION" --key-id "$ALIAS_NAME" --plaintext "$plaintext" --query CiphertextBlob --output text)
  if [[ $USE_CONTEXT -eq 1 ]]; then
    # Add encryption context for policy condition (must match on decrypt)
    args+=(--encryption-context "${CONTEXT_KEY}=${CONTEXT_VAL}")
  fi
  local ct_b64
  ct_b64="$(aws kms encrypt "${args[@]}")"
  echo "ENCRYPTED(${ct_b64})"
}

# ---------- main ----------
>&2 echo "==> Region:    $REGION"
>&2 echo "==> Alias:     $ALIAS_NAME"
[[ -n "$ROLE_ARN" ]] && >&2 echo "==> Role ARN:  $ROLE_ARN"
[[ $USE_CONTEXT -eq 1 ]] && >&2 echo "==> Context:   kms:EncryptionContext:${CONTEXT_KEY}=${CONTEXT_VAL}"
[[ $APPLY_POLICY -eq 1 ]] && >&2 echo "==> Mode:      APPLY policy"
[[ $PRINT_POLICY -eq 1 ]] && >&2 echo "==> Mode:      PRINT policy"
[[ -n "$ENCRYPT_VALUE" ]] && >&2 echo "==> Mode:      ENCRYPT value"

KEY_ARN="$(ensure_key_and_alias | tail -n1)"
>&2 echo "==> Key ARN:   $KEY_ARN"

if [[ $ENABLE_ROTATION -eq 1 ]]; then
  enable_rotation "$KEY_ARN"
fi
[[ -n "$ROLE_ARN" ]] && grant_decrypt "$KEY_ARN" "$ROLE_ARN"

# If asked to encrypt, do it now and exit (no policy steps required)
if [[ -n "$ENCRYPT_VALUE" ]]; then
  encrypt_value "$ENCRYPT_VALUE"
  exit 0
fi

# Build/print/apply policy as requested
POLICY_JSON="$(build_policy_json)"
echo "$POLICY_JSON" | jq . > "$TMP_DIR/new-policy.json"

if [[ $PRINT_POLICY -eq 1 && $APPLY_POLICY -eq 0 ]]; then
  echo "----- BEGIN POLICY (preview) -----"
  cat "$TMP_DIR/new-policy.json"
  echo "----- END POLICY -----"
  echo "Tip: Re-run with --apply-policy to set this on the key. (This replaces the 'default' key policy.)"
  exit 0
fi

if [[ $APPLY_POLICY -eq 1 ]]; then
  CUR_FILE="$(backup_existing_policy "$KEY_ARN")"
  >&2 echo "==> Backed up existing policy to: $CUR_FILE"
  apply_policy "$KEY_ARN" "$TMP_DIR/new-policy.json"
fi

echo
echo "✅ KMS ready."
echo "   Alias:   $ALIAS_NAME"
echo "   KeyArn:  $KEY_ARN"
echo
echo "Helpful exports:"
echo "  export STRIPE_KMS_ARN=\"$KEY_ARN\""
echo
cat <<'EOF'
Encrypt a Stripe secret later:
./create-stripe-kms.sh --encrypt "sk_test_****************"

# You’ll get: ENCRYPTED(<Base64Ciphertext>)  ← paste into DynamoDB stripe_keys.sk_test
EOF
