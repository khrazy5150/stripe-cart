DIST_ID=EKHWQQ9JA2EL8
PROFILE=default

# 1) Pull config + ETag
aws cloudfront get-distribution-config --id "$DIST_ID" --profile "$PROFILE" > current.json
ETAG=$(jq -r '.ETag' current.json)
jq '.DistributionConfig' current.json > cfg.json

# 2) Remove viewer-request CloudFront FunctionAssociation from Default & all CacheBehaviors (robust)
cat > remove_fn.jq <<'JQ'
# Helper to strip viewer-request function without touching anything else
def strip_vr_assoc:
  if (.FunctionAssociations? and .FunctionAssociations.Items?) then
    .FunctionAssociations.Items |= [ .[] | select(.EventType != "viewer-request") ]
    | .FunctionAssociations.Quantity = (.FunctionAssociations.Items | length)
  else
    .
  end ;

# Apply to DefaultCacheBehavior
.DefaultCacheBehavior |= ( . | strip_vr_assoc )

# Apply to each CacheBehavior (if present)
| if (.CacheBehaviors and (.CacheBehaviors.Quantity // 0) > 0) then
    .CacheBehaviors.Items |= ( map( . | strip_vr_assoc ) )
  else . end
JQ

jq -f remove_fn.jq cfg.json > cfg.nofunc.json


# 3) Ensure OriginPath is empty for your S3 origin (so /v1/* maps 1:1 to s3://bucket/v1/*)
ORIGIN_ID="S3-LandingPages"
cat > set_origin_path.jq <<'JQ'
# env: ORIGIN_ID
.Origins.Items |= map(
  if .Id == env.ORIGIN_ID
  then (.OriginPath = "")
  else . end
)
JQ

ORIGIN_ID="$ORIGIN_ID" jq -f set_origin_path.jq cfg.nofunc.json > cfg.final.json

# Sanity checks
echo "— DefaultCacheBehavior quick check —"
jq '{hasDCB:(.DefaultCacheBehavior|type), TargetOriginId:.DefaultCacheBehavior.TargetOriginId, ViewerProtocolPolicy:.DefaultCacheBehavior.ViewerProtocolPolicy, CachePolicyId:.DefaultCacheBehavior.CachePolicyId, HasFuncAssoc: (.DefaultCacheBehavior.FunctionAssociations|type)}' cfg.final.json


# 4) Update distribution
aws cloudfront update-distribution \
  --id "$DIST_ID" \
  --if-match "$ETAG" \
  --distribution-config file://cfg.final.json \
  --profile "$PROFILE"

# 5) Invalidate the /v1/* path to clear any cached 403s
aws cloudfront create-invalidation \
  --distribution-id "$DIST_ID" \
  --paths "/v1/*" \
  --profile "$PROFILE"
