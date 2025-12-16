#!/usr/bin/env bash
set -euo pipefail

############################################
# === CONFIGURE THESE ===
############################################
DIST_ID="EKHWQQ9JA2EL8"
PROFILE="default"

# CloudFront Function name to (create and) attach everywhere
CF_FUNCTION_NAME="strip-v1-prefix"

# Managed policy IDs (AWS)
ORIGIN_POLICY_CORS_S3="88a5eaf4-2fd4-4709-b370-b4c650ea3fcf"   # CORS-S3Origin
RESP_HEADERS_SECURITY="67f7725c-6f97-4210-82d7-5512b31e9d03"    # SecurityHeadersPolicy
RESP_HEADERS_CORS_SEC="eaab4381-ed33-4a86-88ca-d9558dc6cd63"    # CORS-with-preflight-and-SecurityHeadersPolicy

# Default cache policy for generic content (choose ONE)
CACHE_POLICY_DEFAULT="658327ea-f89d-4fab-a63d-7e88639e58f6"     # CachingOptimized
# CACHE_POLICY_DEFAULT="83da9c7e-98b4-4e11-a168-04f0df8e2c65"   # UseOriginCacheControlHeaders

############################################
# === PRE-FLIGHT ===
############################################
command -v aws >/dev/null || { echo "aws CLI not found"; exit 1; }
command -v jq  >/dev/null || { echo "jq not found"; exit 1; }

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --profile "$PROFILE")"
CF_FUNCTION_ARN="arn:aws:cloudfront::${ACCOUNT_ID}:function/${CF_FUNCTION_NAME}"

workdir="$(mktemp -d)"; trap 'rm -rf "$workdir"' EXIT; cd "$workdir"

############################################
# === STEP 0: CF Function (idempotent)
############################################
cat > strip-v1.js <<'JS'
function handler(event) {
  var req = event.request;
  if (req && req.uri && req.uri.indexOf('/v1/') === 0) {
    req.uri = req.uri.replace(/^\/v1/, '');
  }
  return req;
}
JS

if ! aws cloudfront describe-function --name "$CF_FUNCTION_NAME" --profile "$PROFILE" >/dev/null 2>&1; then
  aws cloudfront create-function \
    --name "$CF_FUNCTION_NAME" \
    --function-config "Comment=Strip /v1 prefix,Runtime=cloudfront-js-1.0" \
    --function-code fileb://strip-v1.js \
    --profile "$PROFILE" >/dev/null
else
  ETag_CURR="$(aws cloudfront describe-function --name "$CF_FUNCTION_NAME" --query ETag --output text --profile "$PROFILE")"
  aws cloudfront update-function \
    --name "$CF_FUNCTION_NAME" \
    --if-match "$ETag_CURR" \
    --function-config "Comment=Strip /v1 prefix,Runtime=cloudfront-js-1.0" \
    --function-code fileb://strip-v1.js \
    --profile "$PROFILE" >/dev/null
fi
ETAG_DESC="$(aws cloudfront describe-function --name "$CF_FUNCTION_NAME" --query ETag --output text --profile "$PROFILE")"
aws cloudfront publish-function --name "$CF_FUNCTION_NAME" --if-match "$ETAG_DESC" --profile "$PROFILE" >/dev/null
echo "→ CloudFront Function ready: $CF_FUNCTION_ARN"

############################################
# === STEP 1: Pull current config
############################################
echo "→ Fetching distribution $DIST_ID config..."
aws cloudfront get-distribution-config --id "$DIST_ID" --profile "$PROFILE" > current.json
ETAG="$(jq -r '.ETag' current.json)"
jq '.DistributionConfig' current.json > cfg.json

# Sanity: make sure DefaultCacheBehavior.TargetOriginId exists before we touch it
DEFAULT_ORIGIN_ID="$(jq -r '.DistributionConfig.DefaultCacheBehavior.TargetOriginId // empty' current.json)"
if [ -z "$DEFAULT_ORIGIN_ID" ] || [ "$DEFAULT_ORIGIN_ID" = "null" ]; then
  echo "!! DefaultCacheBehavior.TargetOriginId is missing in the current distribution. Aborting."
  exit 1
fi

echo "Default origin from current distro:"
jq -r '.DistributionConfig.DefaultCacheBehavior.TargetOriginId' current.json


############################################
# === STEP 2: Modernize (PRESERVE TargetOriginId)
############################################
cat > modernize.jq <<'JQ'
# Ensures required fields remain present; merges modern policy fields on top.
def to_modern_behavior:
  . as $b
  | {
      # preserve required fields if present
      TargetOriginId: ($b.TargetOriginId // .TargetOriginId),
      ViewerProtocolPolicy: "redirect-to-https",
      Compress: true,
      AllowedMethods: { "Quantity": 2, "Items": ["GET","HEAD"], "CachedMethods": { "Quantity": 2, "Items": ["GET","HEAD"] } },
      SmoothStreaming: false
    }
  + ( $b
      | del(.ForwardedValues, .MinTTL, .DefaultTTL, .MaxTTL, .TrustedSigners, .TrustedKeyGroups,
            .CachePolicyId, .OriginRequestPolicyId, .ResponseHeadersPolicyId) )
  + {
      CachePolicyId: env.CACHE_POLICY_DEFAULT,
      OriginRequestPolicyId: env.ORIGIN_POLICY_CORS_S3,
      ResponseHeadersPolicyId: env.RESP_HEADERS_SECURITY
    };

.DefaultCacheBehavior |= to_modern_behavior
| if (.CacheBehaviors and (.CacheBehaviors.Quantity // 0) > 0)
  then .CacheBehaviors.Items |= map(to_modern_behavior)
  else . end
JQ

CACHE_POLICY_DEFAULT="$CACHE_POLICY_DEFAULT" \
ORIGIN_POLICY_CORS_S3="$ORIGIN_POLICY_CORS_S3" \
RESP_HEADERS_SECURITY="$RESP_HEADERS_SECURITY" \
jq -f modernize.jq cfg.json > cfg.modern.json

# === STEP 2b: Enforce required fields on behaviors ===
DEFAULT_ORIGIN="$(jq -r '.DefaultCacheBehavior.TargetOriginId' cfg.json)"

cat > enforce_required.jq <<'JQ'
# env: DEFAULT_ORIGIN
.DefaultCacheBehavior.TargetOriginId = env.DEFAULT_ORIGIN
| .DefaultCacheBehavior.ViewerProtocolPolicy = "redirect-to-https"
| if (.CacheBehaviors and (.CacheBehaviors.Quantity // 0) > 0)
  then .CacheBehaviors.Items |= map(
         .ViewerProtocolPolicy = "redirect-to-https"
       )
  else . end
JQ

# *** Pass env into jq ***
DEFAULT_ORIGIN="$DEFAULT_ORIGIN" jq -f enforce_required.jq cfg.modern.json > cfg.modern.enforced.json
mv cfg.modern.enforced.json cfg.modern.json


############################################
# === STEP 3: Ensure custom cache policies (robust 'None' handling)
############################################
echo "→ Ensuring custom cache policies exist..."

get_or_create_policy () {
  local NAME="$1" DEFAULT_TTL="$2" COMMENT="$3" OUTFILE="$4"
  local ID
  ID="$(aws cloudfront list-cache-policies --type custom --profile "$PROFILE" \
        --query "CachePolicyList.Items[?CachePolicy.CachePolicyConfig.Name==\`$NAME\`].CachePolicy.Id" \
        --output text || true)"
  if [ -z "$ID" ] || [ "$ID" = "None" ]; then
    ID="$(aws cloudfront create-cache-policy --profile "$PROFILE" --output json \
      --cache-policy-config "{
        \"Name\":\"$NAME\",\"Comment\":\"$COMMENT\",
        \"DefaultTTL\":$DEFAULT_TTL,\"MaxTTL\":31536000,\"MinTTL\":0,
        \"ParametersInCacheKeyAndForwardedToOrigin\":{
          \"EnableAcceptEncodingGzip\":true,
          \"EnableAcceptEncodingBrotli\":true,
          \"CookiesConfig\":{\"CookieBehavior\":\"none\"},
          \"HeadersConfig\":{\"HeaderBehavior\":\"none\"},
          \"QueryStringsConfig\":{\"QueryStringBehavior\":\"none\"}
        }}" | jq -r '.CachePolicy.Id')"
  fi
  echo -n "$ID" > "$OUTFILE"
}

get_or_create_policy "StaticAssets-7d" 604800 "CSS/JS cached 7 days"   cp-7d.id
get_or_create_policy "Images-30d"     2592000 "Images cached 30 days" cp-30d.id
get_or_create_policy "Fonts-180d"    15552000 "Fonts cached 180 days" cp-180d.id

CACHE_POLICY_7D="$(cat cp-7d.id)"
CACHE_POLICY_30D="$(cat cp-30d.id)"
CACHE_POLICY_180D="$(cat cp-180d.id)"

echo "   • StaticAssets-7d  => $CACHE_POLICY_7D"
echo "   • Images-30d       => $CACHE_POLICY_30D"
echo "   • Fonts-180d       => $CACHE_POLICY_180D"

############################################
# === STEP 4: Upsert file-type behaviors + /v1/*
############################################
cat > upsert_behavior.jq <<'JQ'
# env: PATH_PATTERN, ORIGIN_ID, CACHE_POLICY_ID, ORIGIN_POLICY_ID, RESP_HEADERS_POLICY_ID
def modernize(b):
  b
  | { TargetOriginId: env.ORIGIN_ID,
      ViewerProtocolPolicy: "redirect-to-https",
      Compress: true,
      AllowedMethods: {"Quantity":2,"Items":["GET","HEAD"],"CachedMethods":{"Quantity":2,"Items":["GET","HEAD"]}},
      CachePolicyId: env.CACHE_POLICY_ID,
      OriginRequestPolicyId: env.ORIGIN_POLICY_ID,
      ResponseHeadersPolicyId: env.RESP_HEADERS_POLICY_ID, 
      SmoothStreaming: false
    } ;

def upsert(items):
  ( [ items[]? | select(.PathPattern == env.PATH_PATTERN) ] | length ) as $exists
  | if $exists > 0 then
      ( items | map( if .PathPattern == env.PATH_PATTERN
                     then modernize(.)
                     else . end ))
    else
      ( items + [ modernize({ "PathPattern": env.PATH_PATTERN }) | .PathPattern = env.PATH_PATTERN ] )
    end ;

. as $root
| .CacheBehaviors.Items = upsert(.CacheBehaviors.Items // [])
| .CacheBehaviors.Quantity = (.CacheBehaviors.Items | length)
JQ

DEFAULT_ORIGIN="$(jq -r '.DefaultCacheBehavior.TargetOriginId' cfg.modern.json)"
cp cfg.modern.json cfg.work.json

# CSS/JS -> 7 days
for pat in "*.css" "*.js"; do
  PATH_PATTERN="$pat" ORIGIN_ID="$DEFAULT_ORIGIN" CACHE_POLICY_ID="$CACHE_POLICY_7D" \
  ORIGIN_POLICY_ID="$ORIGIN_POLICY_CORS_S3" RESP_HEADERS_POLICY_ID="$RESP_HEADERS_SECURITY" \
  jq -f upsert_behavior.jq cfg.work.json > cfg.tmp && mv cfg.tmp cfg.work.json
done

# Images + svg + ico -> 30 days
for pat in "*.jpg" "*.png" "*.webp" "*.svg" "*.ico"; do
  PATH_PATTERN="$pat" ORIGIN_ID="$DEFAULT_ORIGIN" CACHE_POLICY_ID="$CACHE_POLICY_30D" \
  ORIGIN_POLICY_ID="$ORIGIN_POLICY_CORS_S3" RESP_HEADERS_POLICY_ID="$RESP_HEADERS_SECURITY" \
  jq -f upsert_behavior.jq cfg.work.json > cfg.tmp && mv cfg.tmp cfg.work.json
done

# Fonts -> 180 days (with CORS+Security headers)
for pat in "*.woff2"; do
  PATH_PATTERN="$pat" ORIGIN_ID="$DEFAULT_ORIGIN" CACHE_POLICY_ID="$CACHE_POLICY_180D" \
  ORIGIN_POLICY_ID="$ORIGIN_POLICY_CORS_S3" RESP_HEADERS_POLICY_ID="$RESP_HEADERS_CORS_SEC" \
  jq -f upsert_behavior.jq cfg.work.json > cfg.tmp && mv cfg.tmp cfg.work.json
done

# /v1/* behavior using default cache policy + standard security headers
PATH_PATTERN="/v1/*" ORIGIN_ID="$DEFAULT_ORIGIN" CACHE_POLICY_ID="$CACHE_POLICY_DEFAULT" \
ORIGIN_POLICY_ID="$ORIGIN_POLICY_CORS_S3" RESP_HEADERS_POLICY_ID="$RESP_HEADERS_SECURITY" \
jq -f upsert_behavior.jq cfg.work.json > cfg.tmp && mv cfg.tmp cfg.work.json

############################################
# === STEP 5: Attach CF Function to ALL behaviors
############################################
cat > attach_func.jq <<'JQ'
# env: CF_FUNCTION_ARN
def attach(b):
  b + {
    FunctionAssociations:
      ( if b.FunctionAssociations? then
          ( b.FunctionAssociations
            | .Items =
                ( [ .Items[]? | select(.EventType != "viewer-request") ] +
                  [ { EventType: "viewer-request", FunctionARN: env.CF_FUNCTION_ARN } ] )
            | .Quantity = (.Items | length)
          )
        else
          { Quantity: 1, Items: [ { EventType: "viewer-request", FunctionARN: env.CF_FUNCTION_ARN } ] }
        end )
  };

.DefaultCacheBehavior |= attach(.DefaultCacheBehavior)
| if (.CacheBehaviors and (.CacheBehaviors.Quantity // 0) > 0)
  then .CacheBehaviors.Items |= map(attach(.))
  else . end
JQ

CF_FUNCTION_ARN="$CF_FUNCTION_ARN" jq -f attach_func.jq cfg.work.json > cfg.final.json

# === STEP 5b: FINAL ENFORCEMENT on DefaultCacheBehavior ===
cat > final_enforce_default_cb.jq <<'JQ'
# env: DEFAULT_ORIGIN_ID
.DefaultCacheBehavior.TargetOriginId = env.DEFAULT_ORIGIN_ID
| .DefaultCacheBehavior.ViewerProtocolPolicy = "redirect-to-https"
| .DefaultCacheBehavior.AllowedMethods = {
    "Quantity": 2,
    "Items": ["GET","HEAD"],
    "CachedMethods": { "Quantity": 2, "Items": ["GET","HEAD"] }
  }
| .DefaultCacheBehavior.SmoothStreaming = false
| .DefaultCacheBehavior.Compress = true
JQ

# *** Pass env into jq ***
DEFAULT_ORIGIN_ID="$DEFAULT_ORIGIN_ID" jq -f final_enforce_default_cb.jq cfg.final.json > cfg.final.enforced.json

echo "— DefaultCacheBehavior after final enforcement —"
jq '{TargetOriginId: .DefaultCacheBehavior.TargetOriginId,
     ViewerProtocolPolicy: .DefaultCacheBehavior.ViewerProtocolPolicy,
     HasAllowedMethods: (.DefaultCacheBehavior.AllowedMethods | type=="object")}' cfg.final.enforced.json

# === STEP 5c: FINAL POLICY GUARD (ensure CachePolicyId; remove legacy TTLs) ===
cat > final_policy_guard.jq <<'JQ'
# env: CACHE_POLICY_DEFAULT
# Ensure Default uses modern CachePolicy and no legacy TTL fields.
.DefaultCacheBehavior |= (
  del(.MinTTL, .DefaultTTL, .MaxTTL)
  | .CachePolicyId = env.CACHE_POLICY_DEFAULT
)

# Do the same for every cache behavior; if any behavior lacks CachePolicyId, give it the default.
| if (.CacheBehaviors and (.CacheBehaviors.Quantity // 0) > 0) then
    .CacheBehaviors.Items |= map(
      del(.MinTTL, .DefaultTTL, .MaxTTL)
      | ( .CachePolicyId //= env.CACHE_POLICY_DEFAULT )
    )
  else . end
JQ

CACHE_POLICY_DEFAULT="$CACHE_POLICY_DEFAULT" \
jq -f final_policy_guard.jq cfg.final.enforced.json > cfg.final.send.json

# Sanity checks
echo "— Any legacy TTLs left? (should be 0) —"
jq '[.DefaultCacheBehavior, (.CacheBehaviors.Items[]?)] 
    | map(has("MinTTL") or has("DefaultTTL") or has("MaxTTL")) 
    | map(select(. == true)) 
    | length' cfg.final.send.json

echo "— Default has CachePolicyId? —"
jq -r '.DefaultCacheBehavior.CachePolicyId' cfg.final.send.json

# === STEP 5d: FIELD-LEVEL ENCRYPTION GUARD (set empty id everywhere) ===
cat > final_fle_guard.jq <<'JQ'
.DefaultCacheBehavior.FieldLevelEncryptionId = (.DefaultCacheBehavior.FieldLevelEncryptionId // "")
| if (.CacheBehaviors and (.CacheBehaviors.Quantity // 0) > 0) then
    .CacheBehaviors.Items |= map(
      .FieldLevelEncryptionId = (.FieldLevelEncryptionId // "")
    )
  else . end
JQ

jq -f final_fle_guard.jq cfg.final.send.json > cfg.final.ready.json

# Sanity checks (optional)
echo "— Default FLE id (should be empty string) —"
jq -r '.DefaultCacheBehavior.FieldLevelEncryptionId | @json' cfg.final.ready.json
echo "— Any cache behavior missing FLE id? (should be 0) —"
jq '[.CacheBehaviors.Items[]? | select(has("FieldLevelEncryptionId")|not)] | length' cfg.final.ready.json

# === STEP 5e: L@E GUARD — ensure LambdaFunctionAssociations exists (even if empty) ===
cat > final_lfe_guard.jq <<'JQ'
.DefaultCacheBehavior.LambdaFunctionAssociations =
  (.DefaultCacheBehavior.LambdaFunctionAssociations // { "Quantity": 0, "Items": [] })
| if (.CacheBehaviors and (.CacheBehaviors.Quantity // 0) > 0) then
    .CacheBehaviors.Items |= map(
      .LambdaFunctionAssociations = (.LambdaFunctionAssociations // { "Quantity": 0, "Items": [] })
    )
  else . end
JQ

jq -f final_lfe_guard.jq cfg.final.ready.json > cfg.final.ready2.json

# (optional) sanity check
echo "— Default has LambdaFunctionAssociations? —"
jq -r '.DefaultCacheBehavior.LambdaFunctionAssociations | @json' cfg.final.ready2.json



############################################
# === STEP 6: Update distribution
############################################
echo "→ Updating distribution $DIST_ID ..."
aws cloudfront update-distribution \
  --id "$DIST_ID" \
  --if-match "$ETAG" \
  --distribution-config file://cfg.final.ready2.json \
  --profile "$PROFILE" >/dev/null

echo "✓ Submitted update for $DIST_ID."
echo "   Behaviors: /v1/*, *.css, *.js, *.jpg, *.png, *.webp, *.svg, *.ico, *.woff2"
echo "   CF Function attached: $CF_FUNCTION_NAME"
echo "Tip: watch → aws cloudfront get-distribution --id $DIST_ID --query 'Distribution.Status' --profile $PROFILE"
