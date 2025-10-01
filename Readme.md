# Stripe Cart — Deployment Helper

## IMPORTANT: To deploy you must first navigate to the deploy folder in this project

This project includes a one-stop `deploy.sh` to build & deploy the **stripe-cart** SAM stack
per environment (`dev`, `prod`) and (optionally) map a **custom API domain** using the separate
`add-apigw-domain.sh` utility.

> **Why this script?**  
> Keep the deployment consistent: API **stage name = Environment** (`/dev`, `/prod`), stable custom
> domains per env, and easy promotion from test → live via `stripe_keys.mode` without redeploys.

---

## Prerequisites

- **AWS CLI v2**, **SAM CLI**, **jq**
- IAM permissions for:
  - CloudFormation/SAM deploy
  - API Gateway, DynamoDB, KMS, Route 53, ACM
- A **KMS CMK ARN in us-west-2** (for decrypting Stripe secret keys stored in `stripe_keys`)
- Optional (for custom domains): the helper scripts in a sibling folder or same repo:
  - `add-apigw-domain.sh` (create/attach domain)
  - `destroy-apigw-domain.sh` (remove domain & DNS)

> Certificates: Regional API custom domains require an ACM cert in **the same region** as the API (us-west-2);
> Edge-Optimized require ACM in **us-east-1**. The add script will request & validate if missing.

---

## Quick Guide: Create the KMS key & alias for Stripe if it doesn't already exist

### One-liner to provision KMS for Stripe (create, alias, rotation, grants, policy)

1) Run `create-stripe-kms.sh` to set up a **customer-managed CMK** for Stripe secrets.

2) `aws kms describe-key --region us-west-2 \
  --key-id alias/stripe-secrets \
  --query 'KeyMetadata.Arn' --output text`
  to get the ARN to use in deploy.sh.

**IMPORTANT: Keys must be created in the same region in which the stack is deployed in order to be successfully decrypted.**

## Deploy the stack

**Dev (Regional):**
./deploy.sh \
  --env dev \
  --kms-arn arn:aws:kms:us-west-2:150544707159:key/REPLACE-WITH-YOUR-CMK \
  --region us-west-2

### Quick:
```bash
export KEY_ARN=$(aws kms describe-key --region us-west-2 --key-id alias/stripe-secrets --query 'KeyMetadata.Arn' --output text)

./deploy.sh --kms-arn "$KEY_ARN"
```

deploys to "dev" in "us-west-2" region under stack name: "stripe-cart-stack-dev"

```bash
export KEY_ARN=$(aws kms describe-key --region us-west-2 --key-id alias/stripe-secrets --query 'KeyMetadata.Arn' --output text)

./deploy.sh --env=prod --kms-arn "$KEY_ARN"
```

deploys to "prod" in "us-west-2" region under stack name: "stripe-cart-stack-prod"

**See deploy.sh for additional options that change the stack name, region, etc.**

## Once Deployment via deploy.sh is successful:
### Create/reuse the key and alias (no policy changes):**
```bash
./create-stripe-kms.sh
# -> creates CMK in us-west-2 if missing, alias/stripe-secrets, enables rotation
```

## Encrypt a Stripe secret key for use in DynamoDB:
```bash
./create-stripe-kms.sh --encrypt "sk_test_************************"
# -> prints: ENCRYPTED(<Base64Ciphertext>)   # paste into DynamoDB
```

## Grant the Lambda role automatically (discover from a deployed stack)
```bash
./create-stripe-kms.sh --stack-name stripe-cart-stack-dev
# discovers Output 'StripeCartFunctionIamRole' and creates a Decrypt grant
```

## Preview minimal key policy (no changes):
```bash
./create-stripe-kms.sh --print-policy
./create-stripe-kms.sh --print-policy --role-arn arn:aws:iam::123:role/StripeCartFunctionRole-ABC
./create-stripe-kms.sh --print-policy --no-context
```

## Apply the policy (DANGER: replaces existing 'default' policy):
### Strongly recommended: preview first
```bash
./create-stripe-kms.sh --print-policy --role-arn arn:aws:iam::123:role/StripeCartFunctionRole-ABC
```

### Then apply (you will be prompted to type 'apply' unless you add --force)
```bash
./create-stripe-kms.sh --apply-policy --role-arn arn:aws:iam::123:role/StripeCartFunctionRole-ABC
```

### Optional: remove the context condition
```bash
./create-stripe-kms.sh --apply-policy --role-arn arn:aws:iam::123:role/StripeCartFunctionRole-ABC --no-context
```

## Use the key in deployment:
```bash
export STRIPE_KMS_ARN=$(aws kms describe-key --region us-west-2 --key-id alias/stripe-secrets --query 'KeyMetadata.Arn' --output text)
./deploy.sh --env dev --kms-arn "$STRIPE_KMS_ARN" --region us-west-2
```

## Custom alias/region:
```bash
./create-stripe-kms.sh --alias stripe-secrets --region us-west-2
```

## Explicit role ARN (instead of stack discovery):
```bash
./create-stripe-kms.sh --role-arn arn:aws:iam::123456789012:role/StripeCartFunctionRole-ABC123
```

### When finished, the script prints the KeyArn. Use it in deploy:
```bash
./deploy.sh --env dev --kms-arn [YOUR_PRINTED_ARN_KEY] --region us-west-2
```
Alternatively, see the "Use the key in deployment" technique above to avoid having to copy/paste keys into bash commands.

## Encrypt a Stripe secret for stripe_keys using the AWS Command:
```bash
PLAINTEXT="sk_test_****************"
CT_B64=$(aws kms encrypt --region us-west-2 \
  --key-id alias/stripe-secrets \
  --plaintext "$PLAINTEXT" \
  --query CiphertextBlob --output text)
```
Alternatively, you can simplify this by following the "Encrypt a Stripe secret key for use in DynamoDB" section above.

## Example put-item (wrap as ENCRYPTED(...))
```bash
aws dynamodb put-item --region us-west-2 --table-name stripe_keys --item '{
  "clientID": {"S":"YOUR-CLIENT-ID"},
  "active": {"BOOL": true},
  "mode": {"S":"test"},
  "pk_test": {"S":"pk_test_..."},
  "pk_live": {"S":"pk_live_..."},
  "sk_test": {"S":"ENCRYPTED('"$CT_B64"')"},
  "sk_live": {"S":"ENCRYPTED(...)"},
  "wh_secret_test": {"S":"whsec_..."},
  "wh_secret_live": {"S":"whsec_..."}
}'
```

## Optional: Print a minimal KMS key policy (for review/apply)

You can print a **delegation-style** key policy template (no changes made) with:
```bash
./create-stripe-kms.sh --print-policy

# Discover role from stack output (Output: StripeCartFunctionIamRole)
./create-stripe-kms.sh --print-policy --stack-name stripe-cart-stack-dev

# Or provide explicitly
./create-stripe-kms.sh --print-policy --role-arn arn:aws:iam::123456789012:role/StripeCartFunctionRole-ABC123
```

## Optional: Clean up (only if you care about duplicate grants)
### If you ran the grant step many times and want to tidy up:
```bash
KEY_ARN=$(aws kms describe-key --region us-west-2 --key-id alias/stripe-secrets --query 'KeyMetadata.Arn' --output text)

# List grants
aws kms list-grants --region us-west-2 --key-id "$KEY_ARN" --output table

# Revoke a specific grant (copy GrantId from list output)
aws kms revoke-grant --region us-west-2 --key-id "$KEY_ARN" --grant-id <GrantId>
```

## Optional: If stack needs to be deleted (due to ROLLBACK_COMPLETE or some other reason):
### Destroy stack without a custom domain:
```bash
./destroy.sh --env dev --region us-west-2 --force
```

### Forcefully destroy a stack by name:
```bash
./destroy.sh --stack-name stripe-cart-stack --region us-west-2 --force
```

### Destroy prod but keep DNS records (e.g., for later remap):
```bash
./destroy.sh --env prod --region us-west-2 \
  --domain api.juniorbay.com --hz-id Z04737521W6DJSKP7TNPZ \
  --keep-dns --force
```

### Destroy dev with domain & DNS cleanup:
```bash
./destroy.sh --env dev --region us-west-2 \
  --domain api-dev.juniorbay.com --hz-id Z04737521W6DJSKP7TNPZ \
  --force
```
**IMPORTANT:** This command using the flag --hz-id WITHOUT the --keep-dns flag, so it requires the presence of `destroy-apigw-domain.sh`, which will first remove BasePathMappings, DomainName from Route 53. 

You must copy `destroy-apigw-domain.sh` from the `custom-api-gateway-domains` project under `aws/bash-files` directory to the deploy directory for it to work. 

Or alternatively, run `destroy-apigw-domain.sh` first before proceeding to destroy the stack:
```bash
# Delete mappings + domain + DNS (A/AAAA). Cert is kept.
REGION=us-west-2 \
DOMAIN_NAME=api-dev.juniorbay.com \
HOSTED_ZONE_ID=Z04737521W6DJSKP7TNPZ \
./destroy-apigw-domain.sh --yes
```
Then destroy the stack with the simple command:
```bash
./destroy.sh --env dev --region us-west-2 --skip-domain --force
```
- This avoids any chance the stack delete gets hung on domain mappings.
- Cleans up DNS early, so external callers stop hitting the old API right away.
- Leaves your ACM cert intact either way (the domain script doesn’t delete it).

### Purge the SAM artifacts bucket you used at deploy:
```bash
./destroy.sh --env dev --region us-west-2 \
  --sam-bucket stripe-cart-sam-deployments-123456 \
  --purge-sam-bucket --force
```
Use this command if deployment was made in the wrong region and you want to completely purge the SAM artifacts bucket from it. Not needed if re-deployment happens in the same region.