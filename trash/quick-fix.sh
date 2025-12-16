#!/bin/bash
# quick-fix.sh - One command to fix everything

set -e

STACK_NAME="stripe-cart-stack-dev"

echo "🚨 Fixing rollback error..."
echo ""

# Check current status
STACK_STATUS=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null)

echo "Current status: $STACK_STATUS"
echo ""

if [[ "$STACK_STATUS" == *"ROLLBACK"* ]]; then
    echo "✅ Skipping OrdersTable and continuing rollback..."
    
    aws cloudformation continue-update-rollback \
      --stack-name "$STACK_NAME" \
      --resources-to-skip OrdersTable 2>&1 || {
        echo ""
        echo "⚠️  If you see 'No updates are to be performed', that's OK!"
        echo "    It means the stack is already stable."
    }
    
    echo ""
    echo "⏳ Waiting for rollback to complete (this takes 1-2 minutes)..."
    
    # Wait with timeout
    timeout 180 aws cloudformation wait stack-rollback-complete --stack-name "$STACK_NAME" 2>/dev/null || {
        echo ""
        echo "⚠️  Timeout or already complete. Checking status..."
    }
    
    # Check final status
    FINAL_STATUS=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
      --query 'Stacks[0].StackStatus' --output text)
    
    echo ""
    echo "✅ Stack status: $FINAL_STATUS"
    echo ""
    
    if [ "$FINAL_STATUS" = "UPDATE_ROLLBACK_COMPLETE" ]; then
        echo "============================================"
        echo "✅ FIXED! Ready to deploy again"
        echo "============================================"
        echo ""
        echo "Next steps:"
        echo ""
        echo "1. Make sure your template.yaml line 115 has:"
        echo "   NonKeyAttributes: [ amount_total, currency, status, payment_status, fulfilled ]"
        echo ""
        echo "2. Deploy:"
        echo "   sam build && sam deploy"
        echo ""
        echo "3. Configure SMS:"
        echo "   aws dynamodb put-item --table-name app-config-dev --item '..."
        echo ""
    else
        echo "⚠️  Stack is in status: $FINAL_STATUS"
        echo "    You may need to run this again or try: ./fix-rollback.sh"
    fi
else
    echo "✅ Stack is not in rollback state"
    echo ""
    echo "Current status: $STACK_STATUS"
    echo ""
    
    if [ "$STACK_STATUS" = "UPDATE_COMPLETE" ] || [ "$STACK_STATUS" = "CREATE_COMPLETE" ]; then
        echo "Stack is healthy! You can deploy normally:"
        echo "  sam build && sam deploy"
    else
        echo "Stack status unclear. Check with:"
        echo "  ./diagnose-stack.sh"
    fi
fi