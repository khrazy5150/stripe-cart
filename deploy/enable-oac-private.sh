#!/usr/bin/env bash
set -euo pipefail

# ========= CONFIG =========
PROFILE=default
DIST_ID=EKHWQQ9JA2EL8
ORIGIN_ID="S3-LandingPages"
BUCKET="landing-pages-dev-150544707159"
OAC_NAME="OAC-templates-juniorbay"

# ========= PRECHECKS =========
command -v aws >/dev/null || { echo "aws CLI not found"; exit 1; }
command -v jq  >/dev/null || { echo "jq not found"; exit 1; }

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --profile "$PROFILE")"
DIST_ARN="arn:aws:cloudfront::${ACCOUNT_ID}:distribution/${DIST_ID}"

echo "→ Ensuring Origin Access Control (OAC) exists…"
# Try to find existing OAC by name
OAC_ID="$(aws cloudfront list-origin-access-controls --profile "$PROFILE" --output json \
  | jq -r --arg name "$OAC_NAME" '.OriginAccessControlList.Items[]? | select(.Name==$name) | .Id' | head -n1)"

# Create if missing
if [ -z "${OAC_ID:-}" ]; then
  OAC_ID="$(aws cloudfront create-origin-access-control --profile "$PROFILE" \
    --origin-access-control-config "{
      \"Name\":\"$OAC_NAME\",
      \"Description\":\"OAC for $DIST_ID / $ORIGIN_ID\",
      \"SigningProtocol\":\"sigv4\",
      \"SigningBehavior\":\"always\",
      \"OriginAccessControlOriginType\":\"s3\"
    }" --query OriginAccessControl.Id --output text)"
  echo "   Created OAC: $OAC_ID"
else
  echo "   Reusing OAC: $OAC_ID"
fi

echo "→ Fetching current distribution config…"
aws cloudfront get-distribution-config --id "$DIST_ID" --profile "$PROFILE" > dist.json
jq '.DistributionConfig' dist.json > cfg.json
ETAG="$(jq -r '.ETag' dist.json)"

echo "→ Attaching OAC to origin '$ORIGIN_ID' and clearing any OAI…"
jq --arg oac "$OAC_ID" --arg origin_id "$ORIGIN_ID" '
  .Origins.Items |= map(
    if .Id == $origin_id then
      # attach OAC, clear any OAI, ensure REST endpoint (not website) and keep OriginPath as-is
      .OriginAccessControlId = $oac
      | .S3OriginConfig.OriginAccessIdentity = ""
    else . end
  )
' cfg.json > cfg.oac.json

# (Optional sanity: show the origin block)
echo "— Origin after OAC attach —"
jq '.Origins.Items[] | select(.Id=="'"$ORIGIN_ID"'")' cfg.oac.json

echo "→ Updating distribution to use OAC…"
aws cloudfront update-distribution \
  --id "$DIST_ID" \
  --if-match "$ETAG" \
  --distribution-config file://cfg.oac.json \
  --profile "$PROFILE" >/dev/null
echo "   Submitted distribution update."

echo "→ Locking down S3 bucket (block public access & private ACL)…"
aws s3api put-public-access-block --bucket "$BUCKET" --profile "$PROFILE" \
  --public-access-block-configuration '{
    "BlockPublicAcls": true,
    "IgnorePublicAcls": true,
    "BlockPublicPolicy": true,
    "RestrictPublicBuckets": true
  }'


echo "→ Setting bucket policy to allow ONLY this distribution (via SourceArn)…"
cat > bucket-oac-policy.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "AllowCloudFrontOACOnly",
    "Effect": "Allow",
    "Principal": { "Service": "cloudfront.amazonaws.com" },
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::$BUCKET/*",
    "Condition": { "StringEquals": { "AWS:SourceArn": "$DIST_ARN" } }
  }]
}
JSON

aws s3api put-bucket-policy --bucket "$BUCKET" --policy file://bucket-oac-policy.json --profile "$PROFILE"

echo "→ Invalidate /v1/* (clear any cached 403/old responses)…"
aws cloudfront create-invalidation \
  --distribution-id "$DIST_ID" \
  --paths "/v1/*" \
  --profile "$PROFILE" >/dev/null

echo "✓ Done. Wait for the distribution status to be Deployed, then test a file:"
echo "  curl -I https://templates.juniorbay.com/v1/base/styles.css"
