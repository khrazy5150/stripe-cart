# ----- customize these -----
DIST_ID=EKHWQQ9JA2EL8
DOMAIN=templates.juniorbay.com
HOSTED_ZONE_ID=Z04737521W6DJSKP7TNPZ       # Your public hosted zone ID for juniorbay.com
CERT_ARN=arn:aws:acm:us-east-1:150544707159:certificate/1a72b7c6-bf14-40d2-8e07-c2d2df84c70a
PROFILE=default

# Get ETag (required for optimistic locking)
ETAG=$(aws cloudfront get-distribution-config \
  --id $DIST_ID \
  --query ETag \
  --output text \
  --profile $PROFILE)

# Get the current config as JSON
aws cloudfront get-distribution-config \
  --id $DIST_ID \
  --query DistributionConfig \
  --output json \
  --profile $PROFILE > cfg.json

jq --arg dom "$DOMAIN" --arg arn "$CERT_ARN" '
  .Aliases = { "Quantity": 1, "Items": [ $dom ] } |
  .ViewerCertificate = {
    "ACMCertificateArn": $arn,
    "SSLSupportMethod": "sni-only",
    "MinimumProtocolVersion": "TLSv1.2_2021"
  }
' cfg.json > cfg-updated.json


aws cloudfront update-distribution \
  --id $DIST_ID \
  --if-match "$ETAG" \
  --distribution-config file://cfg-updated.json \
  --profile $PROFILE


CF_DOMAIN=$(aws cloudfront get-distribution \
  --id $DIST_ID \
  --query 'Distribution.DomainName' \
  --output text \
  --profile $PROFILE)

cat > r53-change.json <<JSON
{
  "Comment": "Alias $DOMAIN -> $CF_DOMAIN",
  "Changes": [{
    "Action": "UPSERT",
    "ResourceRecordSet": {
      "Name": "$DOMAIN",
      "Type": "A",
      "AliasTarget": {
        "HostedZoneId": "Z2FDTNDATAQYW2",
        "DNSName": "$CF_DOMAIN",
        "EvaluateTargetHealth": false
      }
    }
  }]
}
JSON

aws route53 change-resource-record-sets \
  --hosted-zone-id "$HOSTED_ZONE_ID" \
  --change-batch file://r53-change.json \
  --profile $PROFILE


# Confirm the alias is on the distro (Aliases.Items should include your domain)
aws cloudfront get-distribution --id $DIST_ID --query 'Distribution.DistributionConfig.Aliases' --profile $PROFILE

# Watch for Deployed
aws cloudfront get-distribution --id $DIST_ID --query 'Distribution.Status' --profile $PROFILE

# DNS once propagated
dig +short $DOMAIN
curl -I https://$DOMAIN/
