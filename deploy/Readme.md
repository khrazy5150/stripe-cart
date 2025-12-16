# Deployment Instructions

export KEY_ARN=$(aws kms describe-key --region us-west-2 --key-id alias/stripe-secrets --query 'KeyMetadata.Arn' --output text)

./deploy.sh --kms-arn "$KEY_ARN" --env dev --use-container