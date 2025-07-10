#!/bin/bash
# debug_api.sh - Debug and test API connectivity

echo "=== Debugging RAG API ==="

# Step 1: Check CloudFormation stack
echo "1. Checking CloudFormation stack..."
STACK_INFO=$(aws cloudformation describe-stacks --stack-name rag-system-backend --query 'Stacks[0].Outputs' --output json 2>/dev/null)

if [ $? -eq 0 ]; then
    echo "Stack found:"
    echo $STACK_INFO | jq '.'
    
    # Extract API URL if available
    API_URL=$(echo $STACK_INFO | jq -r '.[] | select(.OutputKey=="ApiGatewayUrl" or .OutputKey=="RagApiUrl" or .OutputKey=="ApiUrl") | .OutputValue' | head -1)
    
    if [ "$API_URL" != "null" ] && [ -n "$API_URL" ]; then
        echo "Found API URL from stack: $API_URL"
    fi
else
    echo "Stack not found or error accessing it"
fi

# Step 2: Check API Gateway directly
echo -e "\n2. Checking API Gateway..."
API_LIST=$(aws apigateway get-rest-apis --query 'items[*].{Name:name,Id:id,CreatedDate:createdDate}' --output table)
echo "$API_LIST"

# Get the API ID
API_ID=$(aws apigateway get-rest-apis --query 'items[0].id' --output text)
if [ "$API_ID" != "None" ] && [ -n "$API_ID" ]; then
    # Construct the API URL if we don't have it from stack
    if [ -z "$API_URL" ]; then
        REGION=$(aws configure get region)
        API_URL="https://${API_ID}.execute-api.${REGION}.amazonaws.com/prod"
        echo "Constructed API URL: $API_URL"
    fi
    
    # Test basic connectivity
    echo -e "\n3. Testing API connectivity..."
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/documents" -H "Content-Type: application/json")
    echo "HTTP Status (without auth): $HTTP_STATUS"
    
    if [ "$HTTP_STATUS" = "000" ]; then
        echo "ERROR: Cannot connect to API. Check if the URL is correct."
        echo "Trying to ping the domain..."
        API_DOMAIN=$(echo $API_URL | sed 's|https://||' | sed 's|/.*||')
        nslookup $API_DOMAIN
    fi
else
    echo "No API Gateway found"
fi

# Step 4: Check Cognito setup
echo -e "\n4. Checking Cognito User Pool..."
USER_POOLS=$(aws cognito-idp list-user-pools --max-items 10 --query 'UserPools[*].{Name:Name,Id:Id}' --output table)
echo "$USER_POOLS"

USER_POOL_ID="us-east-1_CKJtbnm48"
CLIENT_ID="7qu59bcn8kbc89ct9p74pjcqh9"

# Step 5: Test authentication
echo -e "\n5. Testing authentication..."
echo "User Pool ID: $USER_POOL_ID"
echo "Client ID: $CLIENT_ID"

AUTH_RESPONSE=$(aws cognito-idp initiate-auth \
    --auth-flow USER_PASSWORD_AUTH \
    --client-id $CLIENT_ID \
    --auth-parameters USERNAME=tester@example.com,PASSWORD=TestPass123! \
    --output json 2>/dev/null)

if [ $? -eq 0 ]; then
    ID_TOKEN=$(echo $AUTH_RESPONSE | jq -r '.AuthenticationResult.IdToken')
    if [ "$ID_TOKEN" != "null" ] && [ -n "$ID_TOKEN" ]; then
        echo "✓ Authentication successful"
        echo "Token (first 50 chars): ${ID_TOKEN:0:50}..."
        
        # Step 6: Test authenticated request
        if [ -n "$API_URL" ]; then
            echo -e "\n6. Testing authenticated request..."
            
            # Test the documents endpoint
            RESPONSE=$(curl -s -w "\nHTTP_STATUS:%{http_code}\n" \
                -X GET "$API_URL/documents" \
                -H "Authorization: Bearer $ID_TOKEN" \
                -H "Content-Type: application/json")
            
            echo "GET /documents response:"
            echo "$RESPONSE"
            
            # Test upload endpoint
            echo -e "\n7. Testing upload..."
            UPLOAD_RESPONSE=$(curl -s -w "\nHTTP_STATUS:%{http_code}\n" \
                -X POST "$API_URL/documents" \
                -H "Authorization: Bearer $ID_TOKEN" \
                -H "Content-Type: application/json" \
                -d '{
                    "filename": "test.txt",
                    "content": "VGhpcyBpcyBhIHRlc3QgZG9jdW1lbnQgZm9yIHRoZSBSQUcgc3lzdGVt"
                }')
            
            echo "POST /documents response:"
            echo "$UPLOAD_RESPONSE"
        fi
    else
        echo "✗ Authentication failed - no token received"
        echo "Auth response: $AUTH_RESPONSE"
    fi
else
    echo "✗ Authentication failed"
    echo "Error details:"
    aws cognito-idp initiate-auth \
        --auth-flow USER_PASSWORD_AUTH \
        --client-id $CLIENT_ID \
        --auth-parameters USERNAME=tester@example.com,PASSWORD=TestPass123! 2>&1
fi

echo -e "\n=== Debug Complete ==="

# Export variables for manual testing
if [ -n "$API_URL" ] && [ -n "$ID_TOKEN" ]; then
    echo -e "\nFor manual testing, use:"
    echo "export API_URL='$API_URL'"
    echo "export ID_TOKEN='$ID_TOKEN'"
    echo ""
    echo "Then test with:"
    echo "curl -X GET \"\$API_URL/documents\" -H \"Authorization: Bearer \$ID_TOKEN\" -H \"Content-Type: application/json\""
fi