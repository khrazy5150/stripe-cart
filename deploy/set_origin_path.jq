# env: ORIGIN_ID
.Origins.Items |= map(
  if .Id == env.ORIGIN_ID
  then (.OriginPath = "")
  else . end
)
