import boto3
from boto3.dynamodb.conditions import Key, Attr
from typing import Dict, List, Optional
from datetime import datetime
import uuid
from decimal import Decimal
from .config import config
import json
import requests

def convert_floats_to_decimal(obj):
    if isinstance(obj, list):
        return [convert_floats_to_decimal(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: convert_floats_to_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, float):
        return Decimal(str(obj))
    else:
        return obj

class DynamoDBClient:
    def __init__(self):
        self.dynamodb = boto3.resource('dynamodb')
        self.users_table = self.dynamodb.Table(config.USERS_TABLE)
        self.documents_table = self.dynamodb.Table(config.DOCUMENTS_TABLE)
        self.sessions_table = self.dynamodb.Table(config.SESSIONS_TABLE)
        self.messages_table = self.dynamodb.Table(config.MESSAGES_TABLE)
    
    
    # User operations
    def create_user(self, user_id: str, email: str, username: str) -> Dict:
        """Create a new user"""
        item = {
            'PK': f'USER#{user_id}',
            'SK': 'PROFILE',
            'email': email,
            'username': username,
            'created_at': datetime.utcnow().isoformat(),
            'document_count': 0,
            'storage_used': 0
        }
        self.users_table.put_item(Item=item)
        return item
    
    def get_user(self, user_id: str) -> Optional[Dict]:
        """Get user profile"""
        response = self.users_table.get_item(
            Key={'PK': f'USER#{user_id}', 'SK': 'PROFILE'}
        )
        return response.get('Item')
    def get_session(self, user_id: str, session_id: str) -> Optional[Dict]:
        """Get a specific session for a user"""
        try:
            response = self.sessions_table.get_item(
                Key={'PK': f'USER#{user_id}', 'SK': f'SESSION#{session_id}'}
            )
            return response.get('Item')
        except Exception as e:
            print(f"Error getting session: {e}")
            return None
    # Document operations
    def create_document(self, user_id: str, document_id: str, metadata: Dict) -> Dict:
        """Create document record"""
        item = {
            'PK': f'USER#{user_id}',
            'SK': f'DOC#{document_id}',
            'document_id': document_id,
            'filename': metadata['filename'],
            's3_key': metadata['s3_key'],
            'upload_date': datetime.utcnow().isoformat(),
            'file_size': Decimal(str(metadata['file_size'])),
            'chunk_count': 0,
            'status': 'processing',
            'active': False
        }
        self.documents_table.put_item(Item=item)
        return item
    
    def update_document_status(self, user_id: str, document_id: str, 
                             status: str, chunk_count: Optional[int] = None):
        """Update document processing status"""
        update_expr = "SET #status = :status"
        expr_values = {':status': status}
        
        if chunk_count is not None:
            update_expr += ", chunk_count = :count"
            expr_values[':count'] = chunk_count
        
        self.documents_table.update_item(
            Key={'PK': f'USER#{user_id}', 'SK': f'DOC#{document_id}'},
            UpdateExpression=update_expr,
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues=expr_values
        )
    
    def list_user_documents(self, user_id: str) -> List[Dict]:
        """List all documents for a user"""
        response = self.documents_table.query(
            KeyConditionExpression=Key('PK').eq(f'USER#{user_id}') & 
                                 Key('SK').begins_with('DOC#')
        )
        return response.get('Items', [])
    
    def toggle_document_active(self, user_id: str, document_id: str, is_active: bool):
        """Activate/deactivate document for RAG"""
        self.documents_table.update_item(
            Key={'PK': f'USER#{user_id}', 'SK': f'DOC#{document_id}'},
            UpdateExpression="SET active = :active",
            ExpressionAttributeValues={':active': is_active}
        )
    
    def delete_document(self, user_id: str, document_id: str):
        """Delete document record"""
        self.documents_table.delete_item(
            Key={'PK': f'USER#{user_id}', 'SK': f'DOC#{document_id}'}
        )
    
    # Session operations
    def create_session(self, user_id: str, document_ids: List[str]) -> str:
        """Create chat session"""
        session_id = str(uuid.uuid4())
        item = {
            'PK': f'USER#{user_id}',
            'SK': f'SESSION#{session_id}',
            'session_id': session_id,
            'created_at': datetime.utcnow().isoformat(),
            'last_accessed': datetime.utcnow().isoformat(),
            'document_set': document_ids,
            'message_count': 0
        }
        self.sessions_table.put_item(Item=item)
        return session_id
    
    def list_user_sessions(self, user_id: str) -> List[Dict]:
        """List user's chat sessions"""
        response = self.sessions_table.query(
            KeyConditionExpression=Key('PK').eq(f'USER#{user_id}') & 
                                 Key('SK').begins_with('SESSION#'),
            ScanIndexForward=False,  # Most recent first
            Limit=20
        )
        return response.get('Items', [])
    
    # Message operations
    def add_message(self, user_id: str, session_id: str, role: str, content: str, sources: Optional[List[Dict]] = None):
        """Add message to session"""
        timestamp = datetime.utcnow().isoformat()
        item = {
            'PK': f'SESSION#{session_id}',
            'SK': f'MSG#{timestamp}',
            'role': role,
            'content': content,
            'timestamp': timestamp
        }
        if sources:
            item['sources'] = convert_floats_to_decimal(sources)
        
        self.messages_table.put_item(Item=item)
        
        # Update session last_accessed and message_count
        self.sessions_table.update_item(
            Key={'PK': f'USER#{user_id}', 'SK': f'SESSION#{session_id}'},
            UpdateExpression="SET last_accessed = :now, message_count = if_not_exists(message_count, :zero) + :inc",
            ExpressionAttributeValues={
                ':now': timestamp,
                ':inc': 1,
                ':zero': 0
            }
        )
    
    def get_session_messages(self, session_id: str, limit: int = 50) -> List[Dict]:
        """Get messages for a session"""
        response = self.messages_table.query(
            KeyConditionExpression=Key('PK').eq(f'SESSION#{session_id}'),
            ScanIndexForward=True,  # Chronological order
            Limit=limit
        )
        return response.get('Items', [])
    
    def delete_session(self, user_id: str, session_id: str):
        """Delete a chat session and its messages"""
        # Delete session
        self.sessions_table.delete_item(
            Key={'PK': f'USER#{user_id}', 'SK': f'SESSION#{session_id}'}
        )
        
        # Delete all messages for this session
        messages = self.get_session_messages(session_id)
        with self.messages_table.batch_writer() as batch:
            for msg in messages:
                batch.delete_item(
                    Key={'PK': msg['PK'], 'SK': msg['SK']}
                )

db = DynamoDBClient()

def create_session_handler(event: Dict, context) -> Dict:
    """Create new chat session"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        body = json.loads(event['body'])
        
        # Get active documents
        document_ids = body.get('document_ids', [])
        if not document_ids:
            docs = db.list_user_documents(user_id)
            document_ids = [d['document_id'] for d in docs if d.get('active') and d.get('document_id')]
        
        if not document_ids:
            print("No active documents found for user:", user_id)
            return create_error_response(400, 'No active documents')
        
        print("Creating session for user:", user_id)
        print("Document IDs:", document_ids)
        
        # Create session
        session_id = db.create_session(user_id, document_ids)
        
        print("Session created with ID:", session_id)
        return create_success_response({
            'session_id': session_id,
            'document_ids': document_ids
        })
        
    except Exception as e:
        import traceback
        print(f"Create session error: {str(e)}")
        traceback.print_exc()
        return create_error_response(500, 'Failed to create session')