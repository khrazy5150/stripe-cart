#!/usr/bin/env bash
# seed-stripe.sh
# Purpose: Add/update landing page configuration & plan per tenant in stripe-keys-{env}.
# Reqs: bash, aws-cli v2, jq

# Usage:
# ./seed-stripe.sh ../seeding/update_stripe_keys.json dev us-west-2
# ./seed-stripe.sh ../seeding/update_stripe_keys.json prod us-west-2

set -euo pipefail

SRC="${1:-update_stripe_keys.json}"   # path to input JSON (default stays same)
ENVIRONMENT="${2:-dev}"               # dev | prod
REGION="${3:-us-west-2}"

TABLE="stripe-keys-${ENVIRONMENT}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

echo "Using source file: ${SRC}"
echo "Environment: ${ENVIRONMENT}"
echo "Region: ${REGION}"
echo "DynamoDB table: ${TABLE}"

# 1) Values used by --expression-attribute-values
jq '{":lp": .landing_pages, ":plan": .plan}' "${SRC}" > update_values.json

# 2) Key file for --key
jq '
  if .clientID? and (.clientID|type=="object") and .clientID.S? then
    {"clientID":{"S": .clientID.S}}
  elif .clientID? then
    {"clientID":{"S": ( .clientID | tostring )}}
  else
    error("clientID not found in " + input_filename)
  end
' "${SRC}" > key.json

aws dynamodb update-item \
  --table-name "${TABLE}" \
  --key "file://$(pwd)/key.json" \
  --update-expression "SET #lp = :lp, #plan = :plan" \
  --expression-attribute-names '{"#lp":"landing_pages","#plan":"plan"}' \
  --expression-attribute-values "file://$(pwd)/update_values.json" \
  --return-values ALL_NEW \
  --region "${REGION}"

rm -f key.json update_values.json
echo "✅ stripe-keys seeding complete for ${ENVIRONMENT}"
