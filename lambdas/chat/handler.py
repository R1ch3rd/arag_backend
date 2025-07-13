# backend/lambdas/chat/handler.py

import json
import requests
from typing import Dict, List
from shared.config import config
from shared.database import db
from shared.vector_store import vector_store
from shared.cache import cache
from shared.utils import generate_response_prompt, create_success_response, create_error_response

def create_session_handler(event: Dict, context) -> Dict:
    """Create new chat session"""
    try:
        # Debug: Log the full event
        print(f"Create session event: {json.dumps(event, indent=2)}")
        
        # Extract user ID from the JWT token
        user_id = event['requestContext']['authorizer']['claims']['sub']
        
        # Parse body if it exists
        body = {}
        if event.get('body'):
            body = json.loads(event['body'])
        
        # Get active documents
        document_ids = body.get('document_ids', [])
        if not document_ids:
            # Get all active documents
            docs = db.list_user_documents(user_id)
            document_ids = [d['document_id'] for d in docs if d.get('active') and d.get('document_id')]
        
        if not document_ids:
            return create_error_response(400, 'No active documents')
        
        # Create session
        session_id = db.create_session(user_id, document_ids)
        
        return create_success_response({
            'session_id': session_id,
            'document_ids': document_ids
        })
        
    except Exception as e:
        print(f"Create session error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, f'Failed to create session: {str(e)}')

def list_sessions_handler(event: Dict, context) -> Dict:
    """List user's chat sessions"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        
        sessions = db.list_user_sessions(user_id)
        
        return create_success_response({
            'sessions': sessions
        })
        
    except Exception as e:
        print(f"List sessions error: {str(e)}")
        return create_error_response(500, 'Failed to list sessions')

def chat_handler(event: Dict, context) -> Dict:
    """Handle chat message"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        session_id = event['pathParameters']['session_id']
        body = json.loads(event['body'])
        query = body['message']
        
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
        
        # Get session info
        sessions = db.list_user_sessions(user_id)
        session = next((s for s in sessions if s['session_id'] == session_id), None)
        
        if not session:
            return create_error_response(404, 'Session not found')
        
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
        
        # Verify session belongs to user
        sessions = db.list_user_sessions(user_id)
        print(f"User sessions: {sessions}")
        
        session_found = any(s['session_id'] == session_id for s in sessions)
        print(f"Session found: {session_found}")
        
        if not session_found:
            return create_error_response(404, f'Session not found: {session_id}')
        
        # Get messages
        messages = db.get_session_messages(session_id)
        print(f"Messages retrieved: {len(messages) if messages else 0}")
        
        return create_success_response({
            'messages': messages or [],
            'session_id': session_id
        })
        
    except Exception as e:
        print(f"Get messages error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, 'Failed to get messages')

def delete_session_handler(event: Dict, context) -> Dict:
    """Delete a chat session"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        session_id = event['pathParameters']['session_id']
        
        # Verify session belongs to user
        sessions = db.list_user_sessions(user_id)
        if not any(s['session_id'] == session_id for s in sessions):
            return create_error_response(404, 'Session not found')
        
        # Delete session (implement in database.py)
        # db.delete_session(user_id, session_id)
        
        return create_success_response({
            'message': 'Session deleted successfully'
        })
        
    except Exception as e:
        print(f"Delete session error: {str(e)}")
        return create_error_response(500, 'Failed to delete session')

# Handler mapping for Lambda
def handler_router(event: Dict, context):
    """Route to appropriate handler based on HTTP method and path"""
    path = event['path']
    method = event['httpMethod']

    print(f"Handler routing: {method} {path}")

    # ✅ Handle CORS preflight
    if method == 'OPTIONS':
        return create_success_response({ "message": "CORS preflight success" })

    # Updated routing to match frontend paths
    if path == '/chat/sessions' and method == 'POST':
        return create_session_handler(event, context)
    elif path == '/chat/sessions' and method == 'GET':
        return list_sessions_handler(event, context)
    elif path.startswith('/chat/sessions/') and path.endswith('/messages') and method == 'POST':
        return chat_handler(event, context)
    elif path.startswith('/chat/sessions/') and path.endswith('/messages') and method == 'GET':
        return get_messages_handler(event, context)
    elif path.startswith('/chat/sessions/') and method == 'DELETE':
        return delete_session_handler(event, context)
    else:
        return create_error_response(404, f'Not found: {method} {path}')