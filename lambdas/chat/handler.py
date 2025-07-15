# backend/lambdas/chat/handler.py

import json
import requests
from typing import Dict, List
from shared.config import config
from shared.database import db
from shared.vector_store import vector_store
from shared.cache import cache
from decimal import Decimal
from shared.utils import generate_response_prompt, create_success_response, create_error_response

def create_session_handler(event: Dict, context) -> Dict:
    """Create new chat session and clear user cache"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        body = json.loads(event.get('body', '{}'))
        
        print(f"Creating session for user: {user_id}")
        
        # Clear user cache when creating new session
        print("Clearing user cache for new session...")
        cache.clear_user_cache(user_id)
        
        # Get active documents
        document_ids = body.get('document_ids', [])
        if not document_ids:
            docs = db.list_user_documents(user_id)
            document_ids = [d['document_id'] for d in docs if d.get('active') and d.get('document_id')]
        
        if not document_ids:
            print("No active documents found for user:", user_id)
            return create_error_response(400, 'No active documents')
        
        print("Document IDs:", document_ids)
        
        # Create session
        session_id = db.create_session(user_id, document_ids)
        
        print("Session created with ID:", session_id)
        
        return create_success_response({
            'session_id': session_id,
            'document_ids': document_ids,
            'cache_cleared': True
        })
        
    except Exception as e:
        import traceback
        print(f"Create session error: {str(e)}")
        traceback.print_exc()
        return create_error_response(500, 'Failed to create session')

def list_sessions_handler(event: Dict, context) -> Dict:
    """List user's chat sessions with previews"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        print(f"Listing sessions for user: {user_id}")
        
        # Get query parameters
        query_params = event.get('queryStringParameters', {}) or {}
        limit = int(query_params.get('limit', 20))
        include_preview = query_params.get('include_preview', 'true').lower() == 'true'
        
        print(f"Query params - limit: {limit}, include_preview: {include_preview}")
        
        # Get sessions from database
        if include_preview:
            sessions = db.list_user_sessions_with_preview(user_id, limit)
        else:
            sessions = db.list_user_sessions(user_id)
        
        print(f"Retrieved {len(sessions)} sessions")
        
        # Convert Decimal objects to serializable types
        serializable_sessions = []
        for session in sessions:
            serializable_session = {}
            for key, value in session.items():
                if isinstance(value, Decimal):
                    # Convert to int if it's a whole number, otherwise float
                    if value % 1 == 0:
                        serializable_session[key] = int(value)
                    else:
                        serializable_session[key] = float(value)
                elif isinstance(value, list):
                    # Handle lists that might contain Decimals
                    serializable_session[key] = [
                        int(item) if isinstance(item, Decimal) and item % 1 == 0 
                        else float(item) if isinstance(item, Decimal)
                        else item 
                        for item in value
                    ]
                else:
                    serializable_session[key] = value
            serializable_sessions.append(serializable_session)
        
        return create_success_response({
            'sessions': serializable_sessions,
            'count': len(serializable_sessions)
        })
        
    except Exception as e:
        print(f"List sessions error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, 'Failed to list sessions')


def chat_handler(event: Dict, context) -> Dict:
    """Handle chat message"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        session_id = event['pathParameters']['session_id']
        body = json.loads(event['body'])
        query = body['message']
        
        print(f"Chat handler - User: {user_id}, Session: {session_id}, Query: {query}")
        
        # Check rate limit
        if not cache.check_rate_limit(user_id, 'chat', config.CHAT_RATE_LIMIT):
            return create_error_response(429, 'Chat rate limit exceeded')
        
        # Check cache
        cached_result = cache.get_cached_query(user_id, query)
        if cached_result:
            return create_success_response({
                'response': cached_result['response'],
                'sources': cached_result['sources'],
                'cached': True
            })
        
        # Get session info using direct lookup
        session = db.get_session(user_id, session_id)
        
        if not session:
            print(f"Session not found: {session_id} for user {user_id}")
            return create_error_response(404, f'Session not found: {session_id}')
        
        print(f"Session found: {session}")
        
        # Generate query embedding
        query_embedding = vector_store.generate_embeddings([query])[0]
        
        # Search vectors
        search_results = vector_store.search(
            user_id, 
            query_embedding, 
            session['document_set'], 
            config.TOP_K_RESULTS
        )
        
        if not search_results:
            response_text = "I couldn't find relevant information in your documents to answer this question."
            sources = []
        else:
            # Generate response using LLM
            response_text, sources = generate_llm_response(query, search_results)
        
        # Store messages in database
        db.add_message(user_id, session_id, 'user', query)
        db.add_message(user_id, session_id, 'assistant', response_text, sources)
        
        # Cache result
        result = {
            'response': response_text,
            'sources': sources
        }
        cache.cache_query_result(user_id, query, result)
        
        return create_success_response({
            'response': response_text,
            'sources': sources,
            'cached': False
        })
        
    except Exception as e:
        print(f"Chat error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, 'Failed to process message')

def get_cache_stats_handler(event: Dict, context) -> Dict:
    """Get user's cache statistics"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        
        stats = cache.get_cache_stats(user_id)
        
        return create_success_response({
            'user_id': user_id,
            'cache_stats': stats
        })
        
    except Exception as e:
        print(f"Get cache stats error: {str(e)}")
        return create_error_response(500, 'Failed to get cache stats')

def update_session_handler(event: Dict, context) -> Dict:
    """Update session (e.g., title)"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        session_id = event['pathParameters']['session_id']
        body = json.loads(event['body'])
        
        # Verify session belongs to user
        session = db.get_session(user_id, session_id)
        if not session:
            return create_error_response(404, 'Session not found')
        
        # Update title if provided
        if 'title' in body:
            db.update_session_title(user_id, session_id, body['title'])
        
        return create_success_response({
            'message': 'Session updated successfully',
            'session_id': session_id
        })
        
    except Exception as e:
        print(f"Update session error: {str(e)}")
        return create_error_response(500, 'Failed to update session')

def search_sessions_handler(event: Dict, context) -> Dict:
    """Search user's chat sessions"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        
        # Get search query from query parameters
        query_params = event.get('queryStringParameters', {}) or {}
        search_query = query_params.get('q', '').strip()
        
        if not search_query:
            return create_error_response(400, 'Search query is required')
        
        # Search sessions
        sessions = db.search_sessions(user_id, search_query)
        
        return create_success_response({
            'sessions': sessions,
            'query': search_query,
            'count': len(sessions)
        })
        
    except Exception as e:
        print(f"Search sessions error: {str(e)}")
        return create_error_response(500, 'Failed to search sessions')

def export_session_handler(event: Dict, context) -> Dict:
    """Export session as text/markdown"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        session_id = event['pathParameters']['session_id']
        
        # Verify session belongs to user
        session = db.get_session(user_id, session_id)
        if not session:
            return create_error_response(404, 'Session not found')
        
        # Get all messages
        messages = db.get_session_messages(session_id, limit=1000)
        
        # Generate export content
        export_content = f"# Chat Session Export\n\n"
        export_content += f"**Session ID:** {session_id}\n"
        export_content += f"**Created:** {session.get('created_at', 'Unknown')}\n"
        export_content += f"**Last Accessed:** {session.get('last_accessed', 'Unknown')}\n"
        export_content += f"**Message Count:** {len(messages)}\n\n"
        export_content += "---\n\n"
        
        for msg in messages:
            role = msg.get('role', 'unknown').title()
            content = msg.get('content', '')
            timestamp = msg.get('timestamp', '')
            
            export_content += f"## {role}\n"
            export_content += f"*{timestamp}*\n\n"
            export_content += f"{content}\n\n"
            
            # Add sources if available
            if msg.get('sources'):
                export_content += "**Sources:**\n"
                for source in msg['sources']:
                    export_content += f"- Document {source.get('document_id', 'Unknown')}: {source.get('text', '')[:100]}...\n"
                export_content += "\n"
            
            export_content += "---\n\n"
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'text/plain',
                'Content-Disposition': f'attachment; filename="chat-session-{session_id[:8]}.md"',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
                'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
            },
            'body': export_content
        }
        
    except Exception as e:
        print(f"Export session error: {str(e)}")
        return create_error_response(500, 'Failed to export session')

def generate_llm_response(query: str, contexts: List[Dict]) -> tuple:
    """Generate response using LLM"""
    prompt = generate_response_prompt(query, contexts)
    
    try:
        if config.LLM_PROVIDER == 'together':
            response_text = call_together_ai(prompt)
        elif config.LLM_PROVIDER == 'groq':
            response_text = call_groq(prompt)
        else:
            response_text = "LLM provider not configured. Please set LLM_PROVIDER environment variable."
    except Exception as e:
        print(f"LLM generation error: {str(e)}")
        response_text = "I encountered an error while generating a response. Please try again."
    
    # Format sources
    sources = [
        {
            'document_id': ctx['document_id'],
            'text': ctx['chunk_text'][:200] + '...' if len(ctx['chunk_text']) > 200 else ctx['chunk_text'],
            'relevance_score': ctx['score']
        }
        for ctx in contexts[:3]  # Top 3 sources
    ]
    
    return response_text, sources

def clear_cache_handler(event: Dict, context) -> Dict:
    """Clear user's cache"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        print(f"Clearing cache for user: {user_id}")
        
        # Get cache stats before clearing
        stats_before = cache.get_cache_stats(user_id)
        print(f"Cache stats before clearing: {stats_before}")
        
        # Clear user's cache
        cache.clear_user_cache(user_id)
        
        # Get cache stats after clearing
        stats_after = cache.get_cache_stats(user_id)
        print(f"Cache stats after clearing: {stats_after}")
        
        return create_success_response({
            'message': 'Cache cleared successfully',
            'user_id': user_id,
            'stats_before': stats_before,
            'stats_after': stats_after
        })
        
    except Exception as e:
        print(f"Clear cache error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, 'Failed to clear cache')

def delete_session_handler(event: Dict, context) -> Dict:
    """Delete a chat session with support for force empty deletion"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        session_id = event['pathParameters']['session_id']
        
        print(f"Delete session request - User: {user_id}, Session: {session_id}")
        
        # Check if this is a force delete for empty sessions
        query_params = event.get('queryStringParameters', {}) or {}
        force_empty = query_params.get('force_empty', 'false').lower() == 'true'
        
        print(f"Force empty deletion: {force_empty}")
        
        # Verify session belongs to user
        session = db.get_session(user_id, session_id)
        if not session:
            print(f"Session {session_id} not found for user {user_id}")
            return create_error_response(404, 'Session not found')
        
        # If force_empty is true, only delete if session has no messages
        if force_empty:
            message_count = session.get('message_count', 0)
            print(f"Session message count: {message_count}")
            
            if message_count > 0:
                print(f"Cannot force delete session with {message_count} messages")
                return create_error_response(400, 'Cannot force delete session with messages')
        
        # Clear session-specific cache before deletion
        cache.clear_session_cache(user_id, session_id)
        
        # Delete session and all its messages
        db.delete_session(user_id, session_id)
        
        print(f"Session {session_id} deleted successfully")
        
        return create_success_response({
            'message': 'Session deleted successfully',
            'session_id': session_id,
            'forced_empty': force_empty
        })
        
    except Exception as e:
        print(f"Delete session error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, 'Failed to delete session')

def auto_title_session_handler(event: Dict, context) -> Dict:
    """Auto-generate session title based on first message"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        session_id = event['pathParameters']['session_id']
        body = json.loads(event['body'])
        
        message = body.get('message', '').strip()
        if not message:
            return create_error_response(400, 'Message is required')
        
        # Verify session belongs to user
        session = db.get_session(user_id, session_id)
        if not session:
            return create_error_response(404, 'Session not found')
        
        # Generate title from message (first sentence or first 50 chars)
        title = generate_session_title_from_message(message)
        
        # Update session title
        db.update_session_title(user_id, session_id, title)
        
        return create_success_response({
            'title': title,
            'session_id': session_id
        })
        
    except Exception as e:
        print(f"Auto title session error: {str(e)}")
        return create_error_response(500, 'Failed to generate session title')

def generate_session_title_from_message(message: str) -> str:
    """Generate a session title from the first message"""
    import re
    
    # Remove extra whitespace and newlines
    cleaned_message = re.sub(r'\s+', ' ', message.strip())
    
    # Try to get first sentence
    sentences = re.split(r'[.!?]+', cleaned_message)
    first_sentence = sentences[0].strip()
    
    if first_sentence and len(first_sentence) <= 50:
        return first_sentence
    
    # If first sentence is too long or doesn't exist, use first 50 chars
    if len(cleaned_message) > 50:
        return cleaned_message[:47] + '...'
    
    return cleaned_message if cleaned_message else 'New Chat'

def call_together_ai(prompt: str) -> str:
    """Call Together AI API"""
    headers = {
        "Authorization": f"Bearer {config.TOGETHER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_tokens": config.MAX_TOKENS,
        "temperature": 0.7,
        "top_p": 0.9
    }
    
    try:
        response = requests.post(
            "https://api.together.xyz/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=30
        )
        if response.status_code == 200:
            result = response.json()
            return result['choices'][0]['message']['content'].strip()
        else:
            print(f"Together AI error: {response.status_code} - {response.text}")
            raise Exception(f"Together AI API error: {response.status_code} - {response.text}")
    except Exception as e:
        import traceback
        print("LLM generation error:", str(e))
        traceback.print_exc()
        return "I encountered an error while generating a response. Please try again."

def call_groq(prompt: str) -> str:
    """Call Groq API"""
    headers = {
        "Authorization": f"Bearer {config.GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "messages": [
            {
                "role": "system", 
                "content": "You are a helpful assistant that answers questions based on provided context."
            },
            {
                "role": "user", 
                "content": prompt
            }
        ],
        "model": "mixtral-8x7b-32768",
        "max_tokens": config.MAX_TOKENS,
        "temperature": 0.7,
        "top_p": 0.9,
        "stream": False
    }
    
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers=headers,
        json=data,
        timeout=30
    )
    
    if response.status_code == 200:
        result = response.json()
        return result['choices'][0]['message']['content'].strip()
    else:
        print(f"Groq error: {response.status_code} - {response.text}")
        raise Exception(f"Groq API error: {response.status_code}")

def get_messages_handler(event: Dict, context) -> Dict:
    """Get chat history"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        session_id = event['pathParameters']['session_id']
        
        print(f"Getting messages for user {user_id}, session {session_id}")
        
        # Verify session belongs to user - use direct lookup
        session = db.get_session(user_id, session_id)
        
        if not session:
            print(f"Session not found: {session_id} for user {user_id}")
            return create_error_response(404, f'Session not found: {session_id}')
        
        print(f"Session found: {session}")
        
        # Get messages
        messages = db.get_session_messages(session_id)
        print(f"Messages retrieved: {len(messages) if messages else 0}")
        
        # Transform messages to match frontend expectations
        formatted_messages = []
        if messages:
            for msg in messages:
                formatted_messages.append({
                    'id': msg.get('SK', ''),  # Use SK as ID
                    'content': msg.get('content', ''),
                    'role': msg.get('role', ''),
                    'timestamp': msg.get('timestamp', ''),
                    'sessionId': session_id
                })
        
        return create_success_response({
            'messages': formatted_messages,
            'session_id': session_id
        })
        
    except Exception as e:
        print(f"Get messages error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, 'Failed to get messages')





def handler_router(event: Dict, context):
    """Route to appropriate handler based on HTTP method and path"""
    try:
        original_path = event.get('path', '')
        method = event.get('httpMethod', '')

        print(f"Handler routing: {method} {original_path}")

        # Handle CORS preflight
        if method == 'OPTIONS':
            return create_success_response({"message": "CORS preflight success"})

        # Remove /api prefix if present for routing
        path = original_path
        if path.startswith('/api'):
            path = path[4:]  # Remove '/api' prefix
        
        print(f"Processed path for routing: {path}")

        # Cache management routes
        if path == '/cache/clear' and method == 'POST':
            return clear_cache_handler(event, context)
        elif path == '/cache/stats' and method == 'GET':
            return get_cache_stats_handler(event, context)
        
        # Session management routes
        elif path == '/chat/sessions' and method == 'POST':
            return create_session_handler(event, context)
        elif path == '/chat/sessions' and method == 'GET':
            return list_sessions_handler(event, context)
        elif path == '/chat/sessions/search' and method == 'GET':
            return search_sessions_handler(event, context)
        elif path.startswith('/chat/sessions/') and path.endswith('/messages') and method == 'POST':
            return chat_handler(event, context)
        elif path.startswith('/chat/sessions/') and path.endswith('/messages') and method == 'GET':
            return get_messages_handler(event, context)
        elif path.startswith('/chat/sessions/') and path.endswith('/export') and method == 'GET':
            return export_session_handler(event, context)
        elif path.startswith('/chat/sessions/') and path.endswith('/auto-title') and method == 'POST':
            return auto_title_session_handler(event, context)
        elif path.startswith('/chat/sessions/') and not path.endswith('/messages') and not path.endswith('/export') and not path.endswith('/auto-title') and method == 'PUT':
            return update_session_handler(event, context)
        elif path.startswith('/chat/sessions/') and not path.endswith('/messages') and not path.endswith('/export') and not path.endswith('/auto-title') and method == 'DELETE':
            return delete_session_handler(event, context)
        else:
            return create_error_response(404, f'Not found: {method} {original_path} (processed: {path})')
    
    except Exception as e:
        print(f"Handler router error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, 'Internal server error')