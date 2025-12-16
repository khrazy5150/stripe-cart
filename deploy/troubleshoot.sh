# Get role name
ROLE_NAME=$(aws lambda get-function-configuration \
  --function-name stripe-cart-stack-dev-AdminVerifyFunction-7oItkMOpfsfj \
  --query 'Role' --output text | awk -F'/' '{print $NF}')

echo $ROLE_NAME

# List attached policies
aws iam list-attached-role-policies --role-name $ROLE_NAME

# List inline policies
aws iam list-role-policies --role-name $ROLE_NAME

# Get policy document
aws iam get-role-policy \
  --role-name $ROLE_NAME \
  --policy-name KMSDecryptPolicy


# Check Policy 0
aws iam get-role-policy \
  --role-name stripe-cart-stack-dev-AdminVerifyFunctionRole-p41i1AS6FO92 \
  --policy-name AdminVerifyFunctionRolePolicy0

# Check Policy 1
aws iam get-role-policy \
  --role-name stripe-cart-stack-dev-AdminVerifyFunctionRole-p41i1AS6FO92 \
  --policy-name AdminVerifyFunctionRolePolicy1