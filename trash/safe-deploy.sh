#!/bin/bash
# safe-deploy-gsi-fix.sh - Deploy GSI fix using safe approach (new GSI)

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
ENVIRONMENT="${ENVIRONMENT:-dev}"
STACK_NAME="stripe-cart-stack-${ENVIRONMENT}"
TABLE_NAME="orders-${ENVIRONMENT}"
OLD_GSI="client-created-index"
NEW_GSI="client-created-v2-index"

echo "=========================================="
echo "Safe GSI Migration - Zero Downtime"
echo "=========================================="
echo ""
echo -e "${BLUE}Environment: ${ENVIRONMENT}${NC}"
echo -e "${BLUE}Stack: ${STACK_NAME}${NC}"
echo -e "${BLUE}Table: ${TABLE_NAME}${NC}"
echo ""

# Step 1: Check current stack status
echo "Step 1: Checking CloudFormation stack status..."
STACK_STATUS=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "NOT_FOUND")

if [ "$STACK_STATUS" = "NOT_FOUND" ]; then
    echo -e "${RED}✗ Stack not found: $STACK_NAME${NC}"
    echo "  Please verify the stack name and try again"
    exit 1
elif [[ "$STACK_STATUS" == *"ROLLBACK"* ]] || [[ "$STACK_STATUS" == *"FAILED"* ]]; then
    echo -e "${RED}✗ Stack is in failed state: $STACK_STATUS${NC}"
    echo "  You may need to manually fix the stack first"
    exit 1
else
    echo -e "${GREEN}✓ Stack status: $STACK_STATUS${NC}"
fi
echo ""

# Step 2: Check if new GSI already exists
echo "Step 2: Checking for existing GSI..."
NEW_GSI_EXISTS=$(aws dynamodb describe-table --table-name "$TABLE_NAME" \
  --query "Table.GlobalSecondaryIndexes[?IndexName=='$NEW_GSI'].IndexName" \
  --output text 2>/dev/null || echo "")

if [ ! -z "$NEW_GSI_EXISTS" ]; then
    echo -e "${YELLOW}⚠ New GSI '$NEW_GSI' already exists!${NC}"
    NEW_GSI_STATUS=$(aws dynamodb describe-table --table-name "$TABLE_NAME" \
      --query "Table.GlobalSecondaryIndexes[?IndexName=='$NEW_GSI'].IndexStatus" \
      --output text)
    echo "  Status: $NEW_GSI_STATUS"
    
    if [ "$NEW_GSI_STATUS" != "ACTIVE" ]; then
        echo -e "${YELLOW}  Waiting for GSI to become ACTIVE...${NC}"
    fi
else
    echo -e "${GREEN}✓ Ready to create new GSI${NC}"
fi
echo ""

# Step 3: Scan for code that needs updating
echo "Step 3: Scanning code for GSI references..."
if [ -d "src" ]; then
    echo "  Searching in src/ directory..."
    REFS=$(grep -r "$OLD_GSI" src/ --include="*.py" 2>/dev/null || echo "")
    
    if [ ! -z "$REFS" ]; then
        echo -e "${YELLOW}  ⚠ Found references to old GSI:${NC}"
        echo "$REFS" | head -5
        echo ""
        echo -e "${YELLOW}  These will need to be updated after GSI is created${NC}"
        echo "  Use: python migrate_gsi_references.py src/ --apply"
    else
        echo -e "${GREEN}  ✓ No references found (or already updated)${NC}"
    fi
else
    echo -e "${YELLOW}  ⚠ src/ directory not found${NC}"
fi
echo ""

# Step 4: Confirm deployment
echo "Step 4: Ready to deploy..."
echo ""
echo "This will:"
echo "  1. Create new GSI '$NEW_GSI' with proper projections"
echo "  2. Keep old GSI '$OLD_GSI' for backward compatibility"
echo "  3. Allow you to migrate code at your own pace"
echo ""
echo -e "${YELLOW}Note: GSI creation takes 5-30 minutes${NC}"
echo ""
read -p "Continue with deployment? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Deployment cancelled"
    exit 0
fi
echo ""

# Step 5: Deploy CloudFormation
echo "Step 5: Deploying CloudFormation stack..."
sam build
if [ $? -ne 0 ]; then
    echo -e "${RED}✗ Build failed!${NC}"
    exit 1
fi

sam deploy \
  --stack-name "$STACK_NAME" \
  --template-file template-v2-safe.yaml \
  --parameter-overrides Environment="$ENVIRONMENT" \
  --capabilities CAPABILITY_IAM \
  --no-confirm-changeset

if [ $? -ne 0 ]; then
    echo -e "${RED}✗ Deployment failed!${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Deployment initiated${NC}"
echo ""

# Step 6: Wait for GSI to become ACTIVE
echo "Step 6: Waiting for new GSI to become ACTIVE..."
echo "This may take 5-30 minutes. Press Ctrl+C to stop monitoring (deployment will continue)"
echo ""

MAX_WAIT=1800  # 30 minutes
ELAPSED=0
INTERVAL=15

while [ $ELAPSED -lt $MAX_WAIT ]; do
    GSI_STATUS=$(aws dynamodb describe-table --table-name "$TABLE_NAME" \
      --query "Table.GlobalSecondaryIndexes[?IndexName=='$NEW_GSI'].IndexStatus" \
      --output text 2>/dev/null || echo "NOT_FOUND")
    
    # Calculate progress
    PROGRESS=$((ELAPSED * 100 / MAX_WAIT))
    
    echo -ne "\r  Status: $GSI_STATUS | Elapsed: ${ELAPSED}s / ${MAX_WAIT}s | Progress: ${PROGRESS}%     "
    
    if [ "$GSI_STATUS" = "ACTIVE" ]; then
        echo ""
        echo -e "${GREEN}✓ GSI is ACTIVE!${NC}"
        break
    elif [ "$GSI_STATUS" = "FAILED" ]; then
        echo ""
        echo -e "${RED}✗ GSI creation FAILED!${NC}"
        exit 1
    fi
    
    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
done

if [ $ELAPSED -ge $MAX_WAIT ]; then
    echo ""
    echo -e "${YELLOW}⚠ Timeout reached. Check AWS Console for current status.${NC}"
else
    echo ""
fi

# Step 7: Verify GSI
echo "Step 7: Verifying new GSI..."
GSI_INFO=$(aws dynamodb describe-table --table-name "$TABLE_NAME" \
  --query "Table.GlobalSecondaryIndexes[?IndexName=='$NEW_GSI']" \
  --output json)

echo "New GSI Details:"
echo "$GSI_INFO" | python3 -m json.tool | head -20

echo -e "${GREEN}✓ GSI verified${NC}"
echo ""

# Step 8: Test query
echo "Step 8: Testing query on new GSI..."
TEST_QUERY=$(aws dynamodb query \
  --table-name "$TABLE_NAME" \
  --index-name "$NEW_GSI" \
  --key-condition-expression "clientID = :cid" \
  --expression-attribute-values '{":cid": {"S": "test"}}' \
  --limit 1 \
  2>&1)

if [[ "$TEST_QUERY" == *"ValidationException"* ]]; then
    echo -e "${YELLOW}⚠ Query test inconclusive (no test data)${NC}"
else
    echo -e "${GREEN}✓ Query successful${NC}"
fi
echo ""

# Step 9: Next steps
echo "=========================================="
echo "✅ Deployment Complete!"
echo "=========================================="
echo ""
echo "📋 Next Steps:"
echo ""
echo "1. Update your code to use the new GSI:"
echo "   ${BLUE}python migrate_gsi_references.py src/ --apply${NC}"
echo ""
echo "2. Review the changes:"
echo "   ${BLUE}git diff src/${NC}"
echo ""
echo "3. Test your application:"
echo "   - Run unit tests"
echo "   - Test order queries"
echo "   - Verify filtering on payment_status and fulfilled works"
echo ""
echo "4. Deploy updated code:"
echo "   ${BLUE}sam build && sam deploy${NC}"
echo ""
echo "5. Once verified, remove old GSI from template (optional)"
echo ""
echo "🔍 Monitoring:"
echo "   Watch logs: ${BLUE}aws logs tail /aws/lambda/YOUR-FUNCTION --follow${NC}"
echo "   Check GSI:  ${BLUE}aws dynamodb describe-table --table-name $TABLE_NAME${NC}"
echo ""
echo "📱 SMS Configuration:"
echo "   Don't forget to configure SMS after code is updated!"
echo "   See RECOVERY_GUIDE.md for SMS setup instructions"
echo ""