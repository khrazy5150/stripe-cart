### List pools (note the Id)
```bash
aws cognito-idp list-user-pools --max-results 60 --region us-west-2 \
  --query "UserPools[].{Name:Name,Id:Id}" --output table
```

### List app clients for a pool
```bash
POOL_ID=us-west-2_yUE8ZuobO
aws cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" --region us-west-2 \
  --query "UserPoolClients[].{Name:ClientName,Id:ClientId}" --output table
```

Override Template parameters:
```bash
sam deploy \
  --parameter-overrides \
    Environment=dev \
    StripeKmsKeyArn="$KEY_ARN" \
    AdminUserPoolName=juniorbay-tenants \
    AdminAppClientName=juniorbay-admin-web
```

Test public API:
```bash
curl -X POST https://api-dev.juniorbay.com/ \
  -H "Content-Type: application/json" \
  -H "X-Client-Id: f8810370-7021-7011-c0cc-6f22f52954d3" \
  -d '{"action":"get_product_info","product_id":"prod_SxJgASmAPdNEgf"}' -v
  ```

  Show all stack parameters:
  ```bash
  aws cloudformation describe-stacks \
  --stack-name stripe-cart-stack-dev \
  --query "Stacks[0].Parameters" \
  --output table
  ```