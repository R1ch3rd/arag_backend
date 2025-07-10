# backend/lambdas/documents/handler.py
# Fixed version with correct DynamoDB key schema

import json
import base64
import uuid
import boto3
from typing import Dict
import traceback
from datetime import datetime
import decimal
from shared.utils import extract_text_from_file, chunk_text
from shared.vector_store import vector_store
# Initialize AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

# Configuration
DOCUMENTS_BUCKET = 'rag-documents-536697240321'
DOCUMENTS_TABLE = 'rag-documents'

def create_response(status_code: int, body: dict) -> dict:
    """Create standardized response"""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
        },
        'body': json.dumps(body)
    }

def get_user_id(event: Dict) -> str:
    """Extract user ID from event"""
    try:
        # Try to get from authorizer context first
        if 'requestContext' not in event or 'authorizer' not in event['requestContext']:
            raise Exception("No authorizer context found")
        if 'claims' not in event['requestContext']['authorizer']:
            raise Exception("No claims found in authorizer")
        if 'sub' not in event['requestContext']['authorizer']['claims']:
            raise Exception("No sub claim found")
        return event['requestContext']['authorizer']['claims']['sub']
    except Exception as e:
        print(f"Error getting user ID: {str(e)}")
        print("Event structure:", json.dumps(event, indent=2))
        raise Exception("Unauthorized - invalid token")

def upload_handler(event: Dict, context) -> Dict:
    """Handle document upload"""
    try:
        user_id = get_user_id(event)
        print(f"Processing upload for user: {user_id}")
        
        # Parse request body
        try:
            body = json.loads(event['body'])
        except:
            return create_response(400, {'error': 'Invalid JSON body'})
        
        filename = body.get('filename', 'document.txt')
        content_b64 = body.get('content', '')
        
        if not content_b64:
            return create_response(400, {'error': 'No content provided'})
        
        # Decode content
        try:
            # Add padding if needed
            missing_padding = len(content_b64) % 4
            if missing_padding:
                content_b64 += '=' * (4 - missing_padding)
            file_content = base64.b64decode(content_b64)
        except Exception as e:
            return create_response(400, {'error': f'Invalid base64 content: {str(e)}'})
        
        # Validate file
        if len(file_content) == 0:
            return create_response(400, {'error': 'File is empty'})
            
        if len(file_content) > 10 * 1024 * 1024:  # 10MB limit
            return create_response(400, {'error': 'File too large (max 10MB)'})
        
        # Generate document ID
        document_id = str(uuid.uuid4())
        
        # Upload to S3
        s3_key = f"users/{user_id}/documents/{document_id}/{filename}"
        
        try:
            s3_client.put_object(
                Bucket=DOCUMENTS_BUCKET,
                Key=s3_key,
                Body=file_content,
                Metadata={
                    'user_id': user_id,
                    'document_id': document_id
                }
            )
        except Exception as e:
            print(f"S3 upload failed: {e}")
            return create_response(500, {'error': 'Failed to upload file'})
        
        # Save to DynamoDB using correct key schema (PK/SK)
        try:
            table = dynamodb.Table(DOCUMENTS_TABLE)
            table.put_item(Item={
                'PK': f'USER#{user_id}',           # Partition Key
                'SK': f'DOC#{document_id}',        # Sort Key
                'user_id': user_id,                # For easier querying
                'document_id': document_id,
                'filename': filename,
                's3_key': s3_key,
                'file_size': len(file_content),
                'status': 'ready',
                'created_at': datetime.utcnow().isoformat(),
                'active': True,
                'GSI1PK': f'USER#{user_id}',       # For GSI if needed
                'entity_type': 'document'
            })
        except Exception as e:
            print(f"DynamoDB save failed: {e}")
            traceback.print_exc()
            # Try to cleanup S3
            try:
                s3_client.delete_object(Bucket=DOCUMENTS_BUCKET, Key=s3_key)
            except:
                pass
            return create_response(500, {'error': 'Failed to save document metadata'})
        
        # Extract text and chunk the document
        text = extract_text_from_file(file_content, filename)
        chunks = chunk_text(text)  # Each chunk['text'] is plain text
        
        # Generate embeddings for each chunk
        chunk_texts = [chunk['text'] for chunk in chunks]
        embeddings = vector_store.generate_embeddings(chunk_texts)

        # Upsert to Pinecone
        vector_store.upsert_chunks(user_id, document_id, chunks, embeddings)
        
        return create_response(200, {
            'document_id': document_id,
            'filename': filename,
            'status': 'ready',
            'file_size': len(file_content),
            'message': 'Document uploaded successfully'
        })
        
    except Exception as e:
        print(f"Upload error: {str(e)}")
        traceback.print_exc()
        return create_response(500, {'error': 'Internal server error'})

def convert_decimal(obj):
    if isinstance(obj, list):
        return [convert_decimal(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: convert_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, decimal.Decimal):
        # Convert to int if possible, else float
        if obj % 1 == 0:
            return int(obj)
        else:
            return float(obj)
    else:
        return obj

def list_handler(event: Dict, context) -> Dict:
    """List user's documents"""
    try:
        user_id = get_user_id(event)
        print(f"Listing documents for user: {user_id}")
        
        # Query DynamoDB using correct key schema
        try:
            table = dynamodb.Table(DOCUMENTS_TABLE)
            
            # Query by PK (partition key) to get all documents for user
            response = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key('PK').eq(f'USER#{user_id}') &
                                     boto3.dynamodb.conditions.Key('SK').begins_with('DOC#')
            )
            
            documents = []
            for item in response.get('Items', []):
                documents.append({
                    'document_id': item.get('document_id', ''),
                    'filename': item.get('filename', ''),
                    'status': item.get('status', 'unknown'),
                    'created_at': item.get('created_at', ''),
                    'file_size': item.get('file_size', 0),
                    'active': item.get('active', True)
                })
            
            documents = convert_decimal(documents)
            return create_response(200, {
                'documents': documents,
                'total': len(documents),
                'user_id': user_id
            })
            
        except Exception as e:
            print(f"DynamoDB query failed: {e}")
            traceback.print_exc()
            return create_response(500, {'error': f'Failed to query documents: {str(e)}'})
        
    except Exception as e:
        print(f"List error: {str(e)}")
        traceback.print_exc()
        return create_response(500, {'error': 'Internal server error'})

def toggle_handler(event: Dict, context) -> Dict:
    """Toggle document active status"""
    try:
        user_id = get_user_id(event)
        document_id = event['pathParameters']['document_id']
        
        try:
            body = json.loads(event['body'])
            is_active = body.get('is_active', True)
        except:
            return create_response(400, {'error': 'Invalid JSON body'})
        
        table = dynamodb.Table(DOCUMENTS_TABLE)
        
        # Check if the document exists
        try:
            response = table.get_item(
                Key={
                    'PK': f'USER#{user_id}',
                    'SK': f'DOC#{document_id}'
                }
            )
            if 'Item' not in response:
                return create_response(404, {'error': 'Document not found'})
        except Exception as e:
            print(f"Failed to get document: {e}")
            return create_response(500, {'error': 'Failed to retrieve document'})
        
        # Update in DynamoDB using correct keys
        try:
            table.update_item(
                Key={
                    'PK': f'USER#{user_id}',
                    'SK': f'DOC#{document_id}'
                },
                UpdateExpression='SET active = :active',
                ExpressionAttributeValues={
                    ':active': is_active
                }
            )
            
            return create_response(200, {
                'document_id': document_id,
                'is_active': is_active,
                'message': f"Document {'activated' if is_active else 'deactivated'}"
            })
            
        except Exception as e:
            print(f"DynamoDB update failed: {e}")
            return create_response(500, {'error': 'Failed to update document'})
        
    except Exception as e:
        print(f"Toggle error: {str(e)}")
        return create_response(500, {'error': 'Internal server error'})

def delete_handler(event: Dict, context) -> Dict:
    """Delete document"""
    try:
        user_id = get_user_id(event)
        document_id = event['pathParameters']['document_id']
        
        # Get document info first using correct keys
        try:
            table = dynamodb.Table(DOCUMENTS_TABLE)
            response = table.get_item(
                Key={
                    'PK': f'USER#{user_id}',
                    'SK': f'DOC#{document_id}'
                }
            )
            
            if 'Item' not in response:
                return create_response(404, {'error': 'Document not found'})
            
            document = response['Item']
            s3_key = document['s3_key']
            
        except Exception as e:
            print(f"Failed to get document: {e}")
            return create_response(500, {'error': 'Failed to retrieve document'})
        
        # Delete from S3
        try:
            s3_client.delete_object(Bucket=DOCUMENTS_BUCKET, Key=s3_key)
        except Exception as e:
            print(f"S3 delete failed: {e}")
            # Continue anyway
        
        # Delete from DynamoDB using correct keys
        try:
            table.delete_item(
                Key={
                    'PK': f'USER#{user_id}',
                    'SK': f'DOC#{document_id}'
                }
            )
        except Exception as e:
            print(f"DynamoDB delete failed: {e}")
            return create_response(500, {'error': 'Failed to delete document record'})
        
        return create_response(200, {
            'message': 'Document deleted successfully',
            'document_id': document_id
        })
        
    except Exception as e:
        print(f"Delete error: {str(e)}")
        return create_response(500, {'error': 'Internal server error'})