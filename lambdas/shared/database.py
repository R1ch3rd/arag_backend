# backend/lambdas/shared/database.py
# ENHANCED VERSION - Fixes session state and message tracking issues

import boto3
from boto3.dynamodb.conditions import Key, Attr
from typing import Dict, List, Optional
from datetime import datetime
import uuid
from decimal import Decimal
from .config import config
import json
import requests
from .utils import create_error_response, create_success_response

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
    
    def get_active_documents(self, user_id: str) -> List[str]:
        """Get list of active document IDs for a user - ENHANCED"""
        try:
            response = self.documents_table.query(
                KeyConditionExpression=Key('PK').eq(f'USER#{user_id}') & 
                                     Key('SK').begins_with('DOC#'),
                FilterExpression=Attr('active').eq(True)
            )
            
            active_doc_ids = []
            for item in response.get('Items', []):
                doc_id = item.get('document_id')
                if doc_id:
                    active_doc_ids.append(doc_id)
            
            print(f"Found {len(active_doc_ids)} active documents for user {user_id}: {active_doc_ids}")
            return active_doc_ids
            
        except Exception as e:
            print(f"Error getting active documents: {e}")
            return []
    
    def toggle_document_active(self, user_id: str, document_id: str, is_active: bool):
        """Activate/deactivate document for RAG"""
        self.documents_table.update_item(
            Key={'PK': f'USER#{user_id}', 'SK': f'DOC#{document_id}'},
            UpdateExpression="SET active = :active, last_modified = :timestamp",
            ExpressionAttributeValues={
                ':active': is_active,
                ':timestamp': datetime.utcnow().isoformat()
            }
        )
    
    def delete_document(self, user_id: str, document_id: str):
        """Delete document record"""
        self.documents_table.delete_item(
            Key={'PK': f'USER#{user_id}', 'SK': f'DOC#{document_id}'}
        )
    
    # 🔥 ENHANCED SESSION OPERATIONS
    def create_session(self, user_id: str, document_ids: List[str] = None) -> str:
        """Create chat session - ENHANCED with better document handling"""
        session_id = str(uuid.uuid4())
        current_time = datetime.utcnow().isoformat()
        
        # If no document_ids provided, get active documents
        if not document_ids:
            document_ids = self.get_active_documents(user_id)
        
        if not document_ids:
            print(f"WARNING: Creating session with no active documents for user {user_id}")
        
        item = {
            'PK': f'USER#{user_id}',
            'SK': f'SESSION#{session_id}',
            'session_id': session_id,
            'created_at': current_time,
            'last_accessed': current_time,
            'last_message_at': current_time,  # 🔥 NEW: Track last message time
            'document_set': document_ids,
            'message_count': 0,
            'title': 'New Chat',  # 🔥 NEW: Default title
            'status': 'active'     # 🔥 NEW: Session status
        }
        
        try:
            self.sessions_table.put_item(Item=item)
            print(f"✅ Session {session_id} created successfully for user {user_id}")
            print(f"   Document set: {document_ids}")
            return session_id
        except Exception as e:
            print(f"❌ Failed to create session: {e}")
            raise e
    
    def list_user_sessions(self, user_id: str, limit: int = 20) -> List[Dict]:
        """List user's chat sessions - ENHANCED with better sorting"""
        try:
            response = self.sessions_table.query(
                KeyConditionExpression=Key('PK').eq(f'USER#{user_id}') & 
                                     Key('SK').begins_with('SESSION#'),
                ScanIndexForward=False,  # Most recent first by SK
                Limit=limit * 2  # Get more to allow for filtering
            )
            
            sessions = response.get('Items', [])
            
            # Sort by last_message_at if available, otherwise by last_accessed
            sessions.sort(
                key=lambda x: x.get('last_message_at', x.get('last_accessed', '1900-01-01')), 
                reverse=True
            )
            
            return sessions[:limit]
            
        except Exception as e:
            print(f"Error listing sessions: {e}")
            return []
    
    # 🔥 ENHANCED MESSAGE OPERATIONS
    def add_message(self, user_id: str, session_id: str, role: str, content: str, sources: Optional[List[Dict]] = None):
        """Add message to session - ENHANCED with better state tracking"""
        timestamp = datetime.utcnow().isoformat()
        
        # Create message item
        item = {
            'PK': f'SESSION#{session_id}',
            'SK': f'MSG#{timestamp}',
            'role': role,
            'content': content,
            'timestamp': timestamp,
            'user_id': user_id,  # 🔥 NEW: Include user_id for easier querying
            'session_id': session_id  # 🔥 NEW: Include session_id for easier querying
        }
        
        if sources:
            item['sources'] = convert_floats_to_decimal(sources)
        
        try:
            # Add the message
            self.messages_table.put_item(Item=item)
            
            # 🔥 ENHANCED: Update session with better tracking
            update_expression = """
                SET last_accessed = :now, 
                    last_message_at = :now,
                    message_count = if_not_exists(message_count, :zero) + :inc
            """
            expression_values = {
                ':now': timestamp,
                ':inc': 1,
                ':zero': 0
            }
            
            # If this is the first user message, auto-generate title
            if role == 'user':
                try:
                    # Check if this is the first message in the session
                    current_count = self.get_session_message_count(session_id)
                    if current_count <= 1:  # First user message
                        title = self.generate_title_from_content(content)
                        update_expression += ", title = :title"
                        expression_values[':title'] = title
                        print(f"🏷️ Auto-generated title for session {session_id}: {title}")
                except Exception as e:
                    print(f"⚠️ Failed to auto-generate title: {e}")
            
            self.sessions_table.update_item(
                Key={'PK': f'USER#{user_id}', 'SK': f'SESSION#{session_id}'},
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values
            )
            
            print(f"✅ Message added to session {session_id} by {role}")
            
        except Exception as e:
            print(f"❌ Failed to add message: {e}")
            raise e
    
    def get_session_messages(self, session_id: str, limit: int = 50) -> List[Dict]:
        """Get messages for a session - ENHANCED with better ordering"""
        try:
            response = self.messages_table.query(
                KeyConditionExpression=Key('PK').eq(f'SESSION#{session_id}'),
                ScanIndexForward=True,  # Chronological order
                Limit=limit
            )
            
            messages = response.get('Items', [])
            print(f"Retrieved {len(messages)} messages for session {session_id}")
            return messages
            
        except Exception as e:
            print(f"Error getting session messages: {e}")
            return []
    
    def get_session_message_count(self, session_id: str) -> int:
        """Get count of messages in a session"""
        try:
            response = self.messages_table.query(
                KeyConditionExpression=Key('PK').eq(f'SESSION#{session_id}'),
                Select='COUNT'
            )
            return response.get('Count', 0)
        except Exception as e:
            print(f"Error getting message count: {e}")
            return 0
    
    def delete_session(self, user_id: str, session_id: str):
        """Delete a chat session and its messages - ENHANCED"""
        try:
            # First, delete all messages for this session
            messages = self.get_session_messages(session_id, limit=1000)  # Get all messages
            
            if messages:
                print(f"🗑️ Deleting {len(messages)} messages for session {session_id}")
                
                # Delete messages in batches
                with self.messages_table.batch_writer() as batch:
                    for msg in messages:
                        batch.delete_item(
                            Key={'PK': msg['PK'], 'SK': msg['SK']}
                        )
                
                print(f"✅ Deleted {len(messages)} messages")
            
            # Delete the session itself
            self.sessions_table.delete_item(
                Key={'PK': f'USER#{user_id}', 'SK': f'SESSION#{session_id}'}
            )
            
            print(f"✅ Session {session_id} deleted successfully")
            
        except Exception as e:
            print(f"❌ Failed to delete session: {e}")
            raise e
    
    def update_session_title(self, user_id: str, session_id: str, title: str):
        """Update session title"""
        try:
            self.sessions_table.update_item(
                Key={'PK': f'USER#{user_id}', 'SK': f'SESSION#{session_id}'},
                UpdateExpression="SET title = :title, last_accessed = :now",
                ExpressionAttributeValues={
                    ':title': title,
                    ':now': datetime.utcnow().isoformat()
                }
            )
            print(f"✅ Updated session {session_id} title to: {title}")
        except Exception as e:
            print(f"❌ Failed to update session title: {e}")
            raise e
    
    def generate_title_from_content(self, content: str) -> str:
        """Generate a session title from message content"""
        import re
        
        # Clean content
        cleaned = re.sub(r'\s+', ' ', content.strip())
        
        # Try to get first sentence
        sentences = re.split(r'[.!?]+', cleaned)
        first_sentence = sentences[0].strip()
        
        if first_sentence and len(first_sentence) <= 60:
            return first_sentence
        
        # Use first 60 characters
        if len(cleaned) > 60:
            return cleaned[:57] + '...'
        
        return cleaned if cleaned else 'New Chat'

    def get_session_with_last_message(self, user_id: str, session_id: str) -> Optional[Dict]:
        """Get session with the last message content"""
        session = self.get_session(user_id, session_id)
        if not session:
            return None
        
        # Get the most recent message
        messages = self.get_session_messages(session_id, limit=1)
        last_message = ""
        if messages:
            last_msg_content = messages[-1].get('content', '')
            last_message = last_msg_content[:100] + ('...' if len(last_msg_content) > 100 else '')
        
        session['last_message'] = last_message
        return session

    def list_user_sessions_with_preview(self, user_id: str, limit: int = 20) -> List[Dict]:
        """List user's chat sessions with message previews - ENHANCED"""
        try:
            sessions = self.list_user_sessions(user_id, limit)
            print(f"Got {len(sessions)} sessions from list_user_sessions")
            
            # Add last message preview to each session
            for session in sessions:
                try:
                    session_id = session.get('session_id')
                    if session_id:
                        # Get the most recent message
                        messages = self.get_session_messages(session_id, limit=2)  # Get last 2 messages
                        
                        if messages:
                            # Find the most recent user or assistant message
                            last_msg = None
                            for msg in reversed(messages):  # Start from most recent
                                if msg.get('role') in ['user', 'assistant']:
                                    last_msg = msg
                                    break
                            
                            if last_msg:
                                content = last_msg.get('content', '')
                                role = last_msg.get('role', '')
                                
                                # Create preview with role prefix
                                if role == 'user':
                                    preview = f"You: {content[:80]}"
                                else:
                                    preview = f"AI: {content[:80]}"
                                
                                if len(content) > 80:
                                    preview += '...'
                                
                                session['last_message'] = preview
                                session['last_message_role'] = role
                            else:
                                session['last_message'] = 'No messages yet'
                                session['last_message_role'] = None
                        else:
                            session['last_message'] = 'No messages yet'
                            session['last_message_role'] = None
                    else:
                        session['last_message'] = 'Invalid session'
                        session['last_message_role'] = None
                        
                except Exception as e:
                    print(f"Error getting preview for session {session.get('session_id', 'unknown')}: {e}")
                    session['last_message'] = 'Error loading preview'
                    session['last_message_role'] = None
            
            return sessions
            
        except Exception as e:
            print(f"Error in list_user_sessions_with_preview: {e}")
            import traceback
            traceback.print_exc()
            return []

    def search_sessions(self, user_id: str, query: str, limit: int = 20) -> List[Dict]:
        """Search sessions by title or message content - ENHANCED"""
        try:
            # Get all user sessions first
            sessions = self.list_user_sessions(user_id, limit=100)  # Get more for searching
            
            query_lower = query.lower()
            filtered_sessions = []
            
            for session in sessions:
                match_score = 0
                match_type = None
                match_preview = ""
                
                # Check title match
                title = session.get('title', '')
                if query_lower in title.lower():
                    match_score += 10
                    match_type = 'title'
                    match_preview = title
                
                # Check message content (limited search for performance)
                if match_score == 0:  # Only search messages if no title match
                    try:
                        session_id = session.get('session_id')
                        messages = self.get_session_messages(session_id, limit=20)  # Limit for performance
                        
                        for msg in messages:
                            content = msg.get('content', '').lower()
                            if query_lower in content:
                                match_score += 5
                                match_type = 'message'
                                # Show snippet around the match
                                match_pos = content.find(query_lower)
                                start = max(0, match_pos - 50)
                                end = min(len(content), match_pos + 50)
                                match_preview = msg.get('content', '')[start:end]
                                if start > 0:
                                    match_preview = '...' + match_preview
                                if end < len(content):
                                    match_preview = match_preview + '...'
                                break
                    except Exception as e:
                        print(f"Error searching messages in session {session.get('session_id')}: {e}")
                
                if match_score > 0:
                    session['match_type'] = match_type
                    session['match_preview'] = match_preview
                    session['match_score'] = match_score
                    filtered_sessions.append(session)
            
            # Sort by match score (title matches first, then message matches)
            filtered_sessions.sort(key=lambda x: x.get('match_score', 0), reverse=True)
            
            return filtered_sessions[:limit]
            
        except Exception as e:
            print(f"Error searching sessions: {e}")
            return []
    
    # 🔥 NEW: Session analytics and management
    def get_user_session_stats(self, user_id: str) -> Dict:
        """Get statistics about user's sessions"""
        try:
            sessions = self.list_user_sessions(user_id, limit=1000)
            
            total_sessions = len(sessions)
            active_sessions = len([s for s in sessions if s.get('status') == 'active'])
            total_messages = sum(s.get('message_count', 0) for s in sessions)
            
            # Calculate date ranges
            if sessions:
                created_dates = [s.get('created_at', '') for s in sessions if s.get('created_at')]
                oldest_session = min(created_dates) if created_dates else None
                newest_session = max(created_dates) if created_dates else None
            else:
                oldest_session = newest_session = None
            
            return {
                'total_sessions': total_sessions,
                'active_sessions': active_sessions,
                'total_messages': total_messages,
                'oldest_session': oldest_session,
                'newest_session': newest_session,
                'avg_messages_per_session': total_messages / max(total_sessions, 1)
            }
            
        except Exception as e:
            print(f"Error getting session stats: {e}")
            return {}
    
    def cleanup_empty_sessions(self, user_id: str) -> int:
        """Clean up sessions with no messages - returns count of deleted sessions"""
        try:
            sessions = self.list_user_sessions(user_id, limit=1000)
            deleted_count = 0
            
            for session in sessions:
                message_count = session.get('message_count', 0)
                if message_count == 0:
                    session_id = session.get('session_id')
                    if session_id:
                        try:
                            self.delete_session(user_id, session_id)
                            deleted_count += 1
                            print(f"🧹 Cleaned up empty session: {session_id}")
                        except Exception as e:
                            print(f"Failed to delete empty session {session_id}: {e}")
            
            print(f"✅ Cleaned up {deleted_count} empty sessions for user {user_id}")
            return deleted_count
            
        except Exception as e:
            print(f"Error cleaning up empty sessions: {e}")
            return 0

db = DynamoDBClient()