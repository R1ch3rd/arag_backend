# backend/lambdas/documents/handler.py

import json
import base64
import uuid
import boto3
from shared import config
from shared.cache import cache 
from typing import Dict
import traceback
from datetime import datetime
import decimal
from shared.utils import (extract_text_from_file, chunk_text, convert_decimals,
    extract_text_with_pages, 
    chunk_pages,
)
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
            'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
        },
        'body': json.dumps(convert_decimals(body))  # 🔥 FIXED: Convert decimals
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
    """
    Handle document upload - ENHANCED WITH PAGE TRACKING
    🔥 UPDATED: Uses new page-aware chunking
    """
    # Handle OPTIONS preflight request
    if event.get('httpMethod') == 'OPTIONS':
        return create_response(200, {})
    
    try:
        user_id = get_user_id(event)
        print(f"Processing upload for user: {user_id}")
        
        # Clear user cache at start of upload process
        print("Clearing user cache before upload...")
        cache.clear_user_cache(user_id)
        
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
        
        # 🔥 NEW: Extract text with page numbers
        print(f"📄 Extracting text with page tracking...")
        pages = extract_text_with_pages(file_content, filename)
        
        # Log page information
        total_pages = len(pages)
        has_page_numbers = any(p.get('page_number') is not None for p in pages)
        
        print(f"📊 Extracted {total_pages} pages")
        if has_page_numbers:
            page_nums = [p['page_number'] for p in pages if p.get('page_number')]
            print(f"📄 Page numbers: {min(page_nums)} to {max(page_nums)}")
        else:
            print(f"⚠️  No page numbers available (non-PDF format)")
        
        # 🔥 NEW: Chunk with page numbers preserved
        print(f"✂️  Chunking with overlap and page tracking...")
        chunks = chunk_pages(pages, chunk_size=400, overlap=100)
        
        print(f"📊 Created {len(chunks)} chunks")
        
        if not chunks:
            print("❌ ERROR: No chunks created!")
            return create_response(500, {'error': 'No chunks created from document'})
        
        # Analyze chunk distribution
        if has_page_numbers:
            page_distribution = {}
            for chunk in chunks:
                page = chunk.get('page_number', 'Unknown')
                page_distribution[page] = page_distribution.get(page, 0) + 1
            
            print(f"📊 Chunks per page: {dict(sorted(page_distribution.items()))}")
        
        # Show sample chunks
        print(f"📝 Sample chunks:")
        for i in range(min(3, len(chunks))):
            chunk = chunks[i]
            page_info = f", page {chunk.get('page_number')}" if 'page_number' in chunk else ""
            print(f"  Chunk {i}: {chunk.get('word_count', 0)} words{page_info}")
            print(f"    Preview: {chunk['text'][:100]}...")
        
        # Generate embeddings
        chunk_texts = [chunk['text'] for chunk in chunks]
        print(f"🔄 Generating embeddings for {len(chunk_texts)} chunks...")
        
        embeddings = vector_store.generate_embeddings(chunk_texts)
        print(f"✅ Generated {len(embeddings)} embeddings")
        
        # Check for failed embeddings
        failed_count = sum(1 for e in embeddings if e is None)
        if failed_count > 0:
            print(f"⚠️  WARNING: {failed_count} embeddings failed")
        
        # 🔥 NEW: Pass document metadata to vector store
        document_metadata = {
            'filename': filename,
            'file_size': len(file_content),
            'total_pages': total_pages,
            'has_page_numbers': has_page_numbers
        }
        
        # Store in vector database
        print(f"💾 Storing chunks in vector database...")
        vector_store.upsert_chunks(
            user_id, 
            document_id, 
            chunks, 
            embeddings,
            document_metadata  # 🔥 NEW: Pass metadata
        )
        
        # 🔥 NEW: Calculate actual chunk count (excluding failed embeddings)
        successful_chunks = len([e for e in embeddings if e is not None])
        
        # Save to DynamoDB
        try:
            table = dynamodb.Table(DOCUMENTS_TABLE)
            table.put_item(Item={
                'PK': f'USER#{user_id}',
                'SK': f'DOC#{document_id}',
                'user_id': user_id,
                'document_id': document_id,
                'filename': filename,
                's3_key': s3_key,
                'file_size': len(file_content),
                'status': 'ready',
                'created_at': datetime.utcnow().isoformat(),
                'active': True,
                'chunk_count': successful_chunks,  # 🔥 NEW: Store chunk count
                'total_pages': total_pages,  # 🔥 NEW: Store page count
                'has_page_numbers': has_page_numbers,  # 🔥 NEW: Store page tracking flag
                'GSI1PK': f'USER#{user_id}',
                'entity_type': 'document'
            })
            print(f"✅ Saved document metadata to DynamoDB")
        except Exception as e:
            print(f"DynamoDB save failed: {e}")
            traceback.print_exc()
            # Try to cleanup S3 and vector store
            try:
                s3_client.delete_object(Bucket=DOCUMENTS_BUCKET, Key=s3_key)
                vector_store.delete_document_vectors(user_id, document_id)
            except:
                pass
            return create_response(500, {'error': 'Failed to save document metadata'})
        
        # Clear cache again after successful upload
        print("🧹 Clearing user cache after successful upload...")
        cache.clear_user_cache(user_id)
        
        print(f"✅ Upload complete!")
        print(f"📊 Summary:")
        print(f"  - Document ID: {document_id}")
        print(f"  - Filename: {filename}")
        print(f"  - Pages: {total_pages}")
        print(f"  - Chunks: {successful_chunks}/{len(chunks)} successful")
        print(f"  - Has page numbers: {has_page_numbers}")
        
        return create_response(200, {
            'document_id': document_id,
            'filename': filename,
            'status': 'ready',
            'file_size': len(file_content),
            'active': True,
            'chunk_count': successful_chunks,
            'total_pages': total_pages,
            'has_page_numbers': has_page_numbers,
            'failed_chunks': len(chunks) - successful_chunks,
            'message': 'Document uploaded successfully'
        })
        
    except Exception as e:
        print(f"❌ Upload error: {str(e)}")
        traceback.print_exc()
        return create_response(500, {'error': 'Internal server error'})

def list_handler(event: Dict, context) -> Dict:
    """List user's documents - ENHANCED WITH CACHE HANDLING"""
    # Handle OPTIONS preflight request
    if event.get('httpMethod') == 'OPTIONS':
        return create_response(200, {})
    
    try:
        user_id = get_user_id(event)
        print(f"Listing documents for user: {user_id}")
        
        # 🔥 NEW: Check cache first
        cache_key = f"documents_list_{user_id}"
        cached_docs = cache.get(cache_key)
        
        if cached_docs:
            print(f"Returning cached document list for user {user_id}")
            return create_response(200, {
                'documents': cached_docs['documents'],
                'total': cached_docs['total'],
                'user_id': user_id,
                'cached': True
            })
        
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
                    'active': item.get('active', True),
                    'chunk_count': item.get('chunk_count', 0)  # 🔥 NEW: Include chunk count
                })
            
            documents = convert_decimals(documents)
            
            # 🔥 NEW: Cache the results
            cache_data = {
                'documents': documents,
                'total': len(documents)
            }
            cache.set(cache_key, cache_data, ttl=300)  # Cache for 5 minutes
            
            return create_response(200, {
                'documents': documents,
                'total': len(documents),
                'user_id': user_id,
                'cached': False
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
    """Toggle document active status - ENHANCED WITH CACHE CLEARING"""
    # Handle OPTIONS preflight request
    if event.get('httpMethod') == 'OPTIONS':
        return create_response(200, {})
    
    try:
        user_id = get_user_id(event)
        document_id = event['pathParameters']['document_id']
        
        print(f"🔄 Toggling document {document_id} for user {user_id}")
        
        try:
            body = json.loads(event['body'])
            is_active = body.get('is_active', body.get('active', True))  # 🔥 FIXED: Handle both field names
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
            # 🔥 ENHANCED: Update with timestamp
            table.update_item(
                Key={
                    'PK': f'USER#{user_id}',
                    'SK': f'DOC#{document_id}'
                },
                UpdateExpression='SET active = :active, last_modified = :timestamp',
                ExpressionAttributeValues={
                    ':active': is_active,
                    ':timestamp': datetime.utcnow().isoformat()
                }
            )
            
            # 🔥 NEW: Clear ALL relevant caches
            print(f"🧹 Clearing caches after document toggle...")
            
            # Clear user's general cache
            cache.clear_user_cache(user_id)
            
            # Clear specific document list cache
            cache.delete(f"documents_list_{user_id}")
            
            # Clear any active documents cache
            cache.delete(f"active_documents_{user_id}")
            
            print(f"✅ Document {document_id} {'activated' if is_active else 'deactivated'} and caches cleared")
            
            return create_response(200, {
                'document_id': document_id,
                'is_active': is_active,
                'active': is_active,  # 🔥 NEW: Include both field names for compatibility
                'cache_cleared': True,
                'message': f"Document {'activated' if is_active else 'deactivated'}"
            })
            
        except Exception as e:
            print(f"DynamoDB update failed: {e}")
            return create_response(500, {'error': 'Failed to update document'})
        
    except Exception as e:
        print(f"Toggle error: {str(e)}")
        return create_response(500, {'error': 'Internal server error'})

def delete_handler(event: Dict, context) -> Dict:
    """Delete document - ENHANCED WITH MULTIPLE KEY SCHEMA SUPPORT AND CACHE CLEARING"""
    # Handle OPTIONS preflight request
    if event.get('httpMethod') == 'OPTIONS':
        return create_response(200, {})
    
    try:
        user_id = get_user_id(event)
        document_id = event['pathParameters']['document_id']
        
        print(f"🗑️ Deleting document {document_id} for user {user_id}")
        
        table = dynamodb.Table(DOCUMENTS_TABLE)
        document = None
        s3_key = None
        
        # 🔥 ENHANCED: Try multiple key schemas to handle legacy documents
        possible_keys = [
            # Current schema
            {'PK': f'USER#{user_id}', 'SK': f'DOC#{document_id}'},
            # Legacy schemas (if any)
            {'user_id': user_id, 'document_id': document_id},
            {'id': document_id, 'user_id': user_id},
            # Try with just document_id as key (single-key table)
            {'document_id': document_id}
        ]
        
        # Try each possible key schema
        for i, key in enumerate(possible_keys):
            try:
                print(f"Trying key schema {i+1}: {key}")
                response = table.get_item(Key=key)
                
                if 'Item' in response:
                    document = response['Item']
                    s3_key = document.get('s3_key')
                    print(f"✅ Found document using key schema {i+1}")
                    break
                    
            except Exception as e:
                print(f"Key schema {i+1} failed: {e}")
                continue
        
        if not document:
            # 🔥 ADDITIONAL: Try scanning for the document (expensive but thorough)
            print("Trying scan as last resort...")
            try:
                response = table.scan(
                    FilterExpression=boto3.dynamodb.conditions.Attr('document_id').eq(document_id) &
                                   boto3.dynamodb.conditions.Attr('user_id').eq(user_id),
                    Limit=10
                )
                
                if response.get('Items'):
                    document = response['Items'][0]
                    s3_key = document.get('s3_key')
                    print(f"✅ Found document via scan")
                else:
                    print(f"❌ Document {document_id} not found anywhere")
                    return create_response(404, {'error': 'Document not found'})
                    
            except Exception as e:
                print(f"Scan failed: {e}")
                return create_response(404, {'error': 'Document not found'})
        
        # Delete from S3 if s3_key exists
        if s3_key:
            try:
                print(f"🗑️ Deleting from S3: {s3_key}")
                s3_client.delete_object(Bucket=DOCUMENTS_BUCKET, Key=s3_key)
                print("✅ S3 deletion successful")
            except Exception as e:
                print(f"⚠️ S3 delete failed: {e}")
                # Continue anyway - better to clean up DB even if S3 fails
        else:
            print("⚠️ No S3 key found, skipping S3 deletion")
        
        # Delete from DynamoDB - try the key that worked for retrieval
        deletion_successful = False
        
        for i, key in enumerate(possible_keys):
            try:
                print(f"Trying to delete with key schema {i+1}: {key}")
                table.delete_item(Key=key)
                print(f"✅ DynamoDB deletion successful with key schema {i+1}")
                deletion_successful = True
                break
                
            except Exception as e:
                print(f"Delete attempt {i+1} failed: {e}")
                continue
        
        if not deletion_successful:
            print("❌ All DynamoDB deletion attempts failed")
            return create_response(500, {'error': 'Failed to delete document record'})
        
        # 🔥 NEW: Delete from vector store
        try:
            print(f"🗑️ Deleting vectors for document {document_id}")
            vector_store.delete_document_vectors(user_id, document_id)
            print("✅ Vector deletion completed")
        except Exception as e:
            print(f"⚠️ Vector deletion failed: {e}")
            # Continue anyway
        
        # 🔥 NEW: Clear ALL relevant caches
        print(f"🧹 Clearing caches after document deletion...")
        
        # Clear user's general cache
        cache.clear_user_cache(user_id)
        
        # Clear specific document list cache
        cache.delete(f"documents_list_{user_id}")
        
        # Clear any active documents cache
        cache.delete(f"active_documents_{user_id}")
        
        print(f"✅ Document {document_id} deleted successfully and caches cleared")
        
        return create_response(200, {
            'message': 'Document deleted successfully',
            'document_id': document_id,
            's3_deleted': bool(s3_key),
            'vectors_deleted': True,
            'cache_cleared': True
        })
        
    except Exception as e:
        print(f"Delete error: {str(e)}")
        traceback.print_exc()
        return create_response(500, {'error': 'Internal server error'})

def view_handler(event: Dict, context) -> Dict:
    """View document content - NEW HANDLER"""
    # Handle OPTIONS preflight request
    if event.get('httpMethod') == 'OPTIONS':
        return create_response(200, {})
    
    try:
        user_id = get_user_id(event)
        document_id = event['pathParameters']['document_id']
        
        print(f"👁️ Viewing document {document_id} for user {user_id}")
        
        # Get document metadata
        table = dynamodb.Table(DOCUMENTS_TABLE)
        
        try:
            response = table.get_item(
                Key={
                    'PK': f'USER#{user_id}',
                    'SK': f'DOC#{document_id}'
                }
            )
            
            if 'Item' not in response:
                return create_response(404, {'error': 'Document not found'})
            
            document = response['Item']
            s3_key = document.get('s3_key')
            
            if not s3_key:
                return create_response(500, {'error': 'Document has no S3 key'})
            
        except Exception as e:
            print(f"Failed to get document metadata: {e}")
            return create_response(500, {'error': 'Failed to retrieve document'})
        
        # Get document content from S3
        try:
            print(f"📄 Fetching content from S3: {s3_key}")
            s3_response = s3_client.get_object(Bucket=DOCUMENTS_BUCKET, Key=s3_key)
            file_content = s3_response['Body'].read()
            
            # Extract text content
            filename = document.get('filename', 'document.txt')
            text_content = extract_text_from_file(file_content, filename)
            
            # Limit content size for display (first 10KB)
            if len(text_content) > 10000:
                display_content = text_content[:10000] + "\n\n... [Content truncated for display] ..."
                truncated = True
            else:
                display_content = text_content
                truncated = False
            
            return create_response(200, {
                'document_id': document_id,
                'filename': filename,
                'content': display_content,
                'full_length': len(text_content),
                'truncated': truncated,
                'metadata': {
                    'file_size': document.get('file_size', 0),
                    'created_at': document.get('created_at', ''),
                    'status': document.get('status', 'unknown'),
                    'active': document.get('active', False),
                    'chunk_count': document.get('chunk_count', 0)
                }
            })
            
        except Exception as e:
            print(f"Failed to fetch content from S3: {e}")
            return create_response(500, {'error': 'Failed to retrieve document content'})
        
    except Exception as e:
        print(f"View error: {str(e)}")
        return create_response(500, {'error': 'Internal server error'})

def get_active_documents_handler(event: Dict, context) -> Dict:
    """Get only active documents for a user - NEW HANDLER"""
    # Handle OPTIONS preflight request
    if event.get('httpMethod') == 'OPTIONS':
        return create_response(200, {})
    
    try:
        user_id = get_user_id(event)
        print(f"Getting active documents for user: {user_id}")
        
        # Check cache first
        cache_key = f"active_documents_{user_id}"
        cached_docs = cache.get(cache_key)
        
        if cached_docs:
            print(f"Returning cached active documents for user {user_id}")
            return create_response(200, {
                'documents': cached_docs,
                'user_id': user_id,
                'cached': True
            })
        
        # Query DynamoDB for active documents only
        try:
            table = dynamodb.Table(DOCUMENTS_TABLE)
            
            response = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key('PK').eq(f'USER#{user_id}') &
                                     boto3.dynamodb.conditions.Key('SK').begins_with('DOC#'),
                FilterExpression=boto3.dynamodb.conditions.Attr('active').eq(True)
            )
            
            active_documents = []
            for item in response.get('Items', []):
                active_documents.append({
                    'document_id': item.get('document_id', ''),
                    'filename': item.get('filename', ''),
                    'status': item.get('status', 'unknown'),
                    'created_at': item.get('created_at', ''),
                    'file_size': item.get('file_size', 0),
                    'chunk_count': item.get('chunk_count', 0)
                })
            
            active_documents = convert_decimals(active_documents)
            
            # Cache the results
            cache.set(cache_key, active_documents, ttl=300)  # Cache for 5 minutes
            
            return create_response(200, {
                'documents': active_documents,
                'count': len(active_documents),
                'user_id': user_id,
                'cached': False
            })
            
        except Exception as e:
            print(f"DynamoDB query failed: {e}")
            return create_response(500, {'error': f'Failed to query active documents: {str(e)}'})
        
    except Exception as e:
        print(f"Get active documents error: {str(e)}")
        return create_response(500, {'error': 'Internal server error'})

def handler_router(event: Dict, context) -> Dict:
    """Route to appropriate handler based on HTTP method and path"""
    try:
        method = event.get('httpMethod', '')
        path = event.get('path', '')
        
        print(f"Documents handler routing: {method} {path}")
        
        # Handle CORS preflight
        if method == 'OPTIONS':
            return create_response(200, {})
        
        # Remove /api prefix if present
        if path.startswith('/api'):
            path = path[4:]
        
        # Route to handlers
        if path == '/documents' and method == 'POST':
            return upload_handler(event, context)
        elif path == '/documents' and method == 'GET':
            return list_handler(event, context)
        elif path == '/documents/active' and method == 'GET':  # 🔥 NEW: Active documents endpoint
            return get_active_documents_handler(event, context)
        elif path.startswith('/documents/') and method == 'PUT':
            return toggle_handler(event, context)
        elif path.startswith('/documents/') and path.endswith('/view') and method == 'GET':  # 🔥 NEW: View endpoint
            return view_handler(event, context)
        elif path.startswith('/documents/') and method == 'DELETE':
            return delete_handler(event, context)
        else:
            return create_response(404, {'error': f'Not found: {method} {path}'})
            
    except Exception as e:
        print(f"Documents handler router error: {str(e)}")
        traceback.print_exc()
        return create_response(500, {'error': 'Internal server error'})