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
