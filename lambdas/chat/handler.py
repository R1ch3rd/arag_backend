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
from datetime import datetime

def enhanced_search_strategy(user_id: str, query: str, query_embedding: List[float], document_ids: List[str]):
    """Enhanced search strategy to get better document coverage"""
    
    print(f"🔍 Enhanced search for query: '{query}'")
    
    # Step 1: Get more results initially
    initial_results = vector_store.search(
        user_id, 
        query_embedding, 
        document_ids, 
        top_k=30  # Get many results initially
    )
    
    print(f"Initial search returned {len(initial_results)} results")
    
    if not initial_results:
        return []
    
    # Step 2: Analyze position distribution
    positions = [(r.get('position', -1), r) for r in initial_results if r.get('position', -1) >= 0]
    positions.sort(key=lambda x: x[0])  # Sort by position
    
    if not positions:
        # Fallback to score-based selection
        return sorted(initial_results, key=lambda x: x.get('score', 0), reverse=True)[:10]
    
    print(f"Position range: {positions[0][0]} to {positions[-1][0]}")
    
    # Step 3: Ensure diverse position coverage
    selected_results = []
    
    # Always include top scorer
    best_result = max(initial_results, key=lambda x: x.get('score', 0))
    selected_results.append(best_result)
    print(f"Added best scorer: position {best_result.get('position', -1)}, score {best_result.get('score', 0):.4f}")
    
    # Divide document into sections and get best from each
    if len(positions) > 1:
        min_pos = positions[0][0]
        max_pos = positions[-1][0]
        position_range = max_pos - min_pos
        
        if position_range > 0:
            # Create 3 sections: beginning, middle, end
            section_size = position_range / 3
            sections = [
                (min_pos, min_pos + section_size),  # Beginning
                (min_pos + section_size, min_pos + 2 * section_size),  # Middle  
                (min_pos + 2 * section_size, max_pos)  # End
            ]
            
            for i, (section_start, section_end) in enumerate(sections):
                section_name = ["beginning", "middle", "end"][i]
                section_results = [
                    result for pos, result in positions 
                    if section_start <= pos <= section_end
                ]
                
                if section_results:
                    # Get best result from this section
                    best_in_section = max(section_results, key=lambda x: x.get('score', 0))
                    
                    # Only add if not already included and meets minimum score threshold
                    if (best_in_section not in selected_results and 
                        best_in_section.get('score', 0) > 0.1):  # Minimum relevance threshold
                        selected_results.append(best_in_section)
                        print(f"Added from {section_name}: position {best_in_section.get('position', -1)}, score {best_in_section.get('score', 0):.4f}")
    
    # Step 4: Fill remaining slots with highest scores
    remaining_slots = 10 - len(selected_results)
    if remaining_slots > 0:
        remaining_results = [
            r for r in initial_results 
            if r not in selected_results and r.get('score', 0) > 0.1
        ]
        remaining_results.sort(key=lambda x: x.get('score', 0), reverse=True)
        
        for result in remaining_results[:remaining_slots]:
            selected_results.append(result)
            print(f"Added by score: position {result.get('position', -1)}, score {result.get('score', 0):.4f}")
    
    print(f"Final selection: {len(selected_results)} results")
    return selected_results

def create_session_handler(event: Dict, context) -> Dict:
    """Create new chat session and clear user cache - ENHANCED WITH DEBUGGING"""
    try:
        # Debug the entire event structure
        print(f"=== CREATE SESSION DEBUG ===")
        print(f"Full event: {json.dumps(event, indent=2, default=str)}")
        
        # Try to safely get user_id
        request_context = event.get('requestContext', {})
        authorizer = request_context.get('authorizer', {})
        claims = authorizer.get('claims', {})
        
        print(f"Request context: {request_context}")
        print(f"Authorizer: {authorizer}")
        print(f"Claims: {claims}")
        
        user_id = claims.get('sub')
        
        if not user_id:
            # Try alternative paths
            if 'principalId' in authorizer:
                user_id = authorizer['principalId']
            else:
                print("ERROR: No user_id found in authorization context")
                return create_error_response(401, 'User not authenticated')
        
        print(f"User ID: {user_id}")
        body = json.loads(event.get('body', '{}'))
        
        print(f"=== CREATE SESSION DEBUG ===")
        print(f"User ID: {user_id}")
        print(f"Body: {body}")
        print(f"Timestamp: {datetime.now()}")
        
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
        print(f"Session created in DB with ID: {session_id}")
        
        # IMPORTANT: Verify the session was actually created
        created_session = db.get_session(user_id, session_id)
        print(f"Verification - session retrieved: {created_session}")
        
        # Check if session appears in list immediately
        all_sessions = db.list_user_sessions(user_id)
        new_session_in_list = any(s.get('session_id') == session_id for s in all_sessions)
        print(f"New session appears in list: {new_session_in_list}")
        
        if not new_session_in_list:
            print("WARNING: Newly created session does not appear in session list!")
            print(f"All session IDs: {[s.get('session_id', 'NO_ID')[:8] for s in all_sessions[:5]]}")
        
        return create_success_response({
            'session_id': session_id,
            'document_ids': document_ids,
            'cache_cleared': True,
            'debug_info': {
                'created_session': created_session,
                'appears_in_list': new_session_in_list,
                'total_sessions': len(all_sessions),
                'timestamp': datetime.now().isoformat()
            }
        })
        
    except Exception as e:
        import traceback
        print(f"Create session error: {str(e)}")
        traceback.print_exc()
        return create_error_response(500, 'Failed to create session')


def test_auth_handler(event: Dict, context) -> Dict:
    """Test authentication and return user info"""
    try:
        print(f"Test auth event: {json.dumps(event, indent=2, default=str)}")
        
        # Try all possible ways to get user_id
        user_id = None
        auth_info = {}
        
        # Method 1: From authorizer claims
        request_context = event.get('requestContext', {})
        authorizer = request_context.get('authorizer', {})
        claims = authorizer.get('claims', {})
        
        if claims and 'sub' in claims:
            user_id = claims['sub']
            auth_info['method'] = 'claims'
            auth_info['claims'] = claims
        elif 'principalId' in authorizer:
            user_id = authorizer['principalId']
            auth_info['method'] = 'principalId'
        
        # Method 2: From headers
        headers = event.get('headers', {})
        auth_header = headers.get('Authorization', '')
        auth_info['has_auth_header'] = bool(auth_header)
        
        return create_success_response({
            'user_id': user_id,
            'auth_info': auth_info,
            'request_context': request_context,
            'headers': {k: v[:50] + '...' if len(v) > 50 else v for k, v in headers.items()}
        })
        
    except Exception as e:
        print(f"Test auth error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, f'Test auth failed: {str(e)}')

def list_sessions_handler(event: Dict, context) -> Dict:
    """List user's chat sessions with previews - ENHANCED WITH DEBUGGING"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        print(f"=== LIST SESSIONS DEBUG ===")
        print(f"User ID: {user_id}")
        print(f"Timestamp: {datetime.now()}")
        
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
        
        print(f"Raw sessions from DB: {len(sessions)}")
        
        # DEBUG: Print first few sessions
        if sessions:
            print(f"First 3 sessions from DB:")
            for i, session in enumerate(sessions[:3]):
                print(f"  {i+1}. ID: {session.get('session_id', 'NO_ID')[:8]}...")
                print(f"     Title: {session.get('title', 'NO_TITLE')}")
                print(f"     Created: {session.get('created_at', 'NO_CREATED')}")
                print(f"     Last accessed: {session.get('last_accessed', 'NO_ACCESSED')}")
                print(f"     Last message at: {session.get('last_message_at', 'NO_LAST_MSG')}")
                print(f"     Message count: {session.get('message_count', 'NO_COUNT')}")
                print(f"     Raw session: {session}")
                print()
        
        # Convert Decimal objects to serializable types
        serializable_sessions = []
        for session in sessions:
            serializable_session = {}
            for key, value in session.items():
                if isinstance(value, Decimal):
                    if value % 1 == 0:
                        serializable_session[key] = int(value)
                    else:
                        serializable_session[key] = float(value)
                elif isinstance(value, list):
                    serializable_session[key] = [
                        int(item) if isinstance(item, Decimal) and item % 1 == 0 
                        else float(item) if isinstance(item, Decimal)
                        else item 
                        for item in value
                    ]
                else:
                    serializable_session[key] = value
            serializable_sessions.append(serializable_session)
        
        # DEBUG: Print serialized sessions
        print(f"Serialized sessions: {len(serializable_sessions)}")
        if serializable_sessions:
            print(f"First serialized session: {serializable_sessions[0]}")
        
        # Sort sessions by last_message_at or last_accessed (most recent first)
        serializable_sessions.sort(key=lambda x: x.get('last_message_at', x.get('last_accessed', '1900-01-01')), reverse=True)
        
        print(f"After sorting - first session: {serializable_sessions[0] if serializable_sessions else 'NONE'}")
        
        return create_success_response({
            'sessions': serializable_sessions,
            'count': len(serializable_sessions),
            'debug_info': {
                'user_id': user_id,
                'raw_count': len(sessions),
                'serialized_count': len(serializable_sessions),
                'timestamp': datetime.now().isoformat()
            }
        })
        
    except Exception as e:
        print(f"List sessions error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, 'Failed to list sessions')

def chat_handler(event: Dict, context) -> Dict:
    """Handle chat message with ENHANCED SEARCH STRATEGY"""
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        session_id = event['pathParameters']['session_id']
        body = json.loads(event['body'])
        query = body['message']
        
        selected_model = body.get('model', 'together-meta-llama/Llama-3.3-70B-Instruct-Turbo-Free')
        
        print(f"💬 Chat request - User: {user_id}, Session: {session_id}")
        print(f"🎯 Query: {query}")
        print(f"🤖 Selected model: {selected_model}")
        
        # Check rate limit
        if not cache.check_rate_limit(user_id, 'chat', config.CHAT_RATE_LIMIT):
            return create_error_response(429, 'Chat rate limit exceeded')
        
        # Check cache
        cache_key = f"{query}_{selected_model}"
        cached_result = cache.get_cached_query(user_id, cache_key)
        
        if cached_result:
            print(f"💾 Returning cached response for {selected_model}")
            return create_success_response({
                'response': cached_result['response'],
                'sources': cached_result['sources'],
                'cached': True,
                'model_used': selected_model
            })
        
        # Get session info
        session = db.get_session(user_id, session_id)
        if not session:
            return create_error_response(404, f'Session not found: {session_id}')
        
        # Generate query embedding
        query_embedding = vector_store.generate_embeddings([query])[0]
        
        # 🔥 REPLACE THE OLD SEARCH WITH ENHANCED SEARCH:
        # OLD CODE (replace this):
        # search_results = vector_store.search(
        #     user_id, 
        #     query_embedding, 
        #     session['document_set'], 
        #     config.TOP_K_RESULTS
        # )
        
        # NEW CODE (use this instead):
        search_results = enhanced_search_strategy(
            user_id,
            query,
            query_embedding,
            session['document_set']
        )
        
        if not search_results:
            response_text = "I couldn't find relevant information in your documents to answer this question."
            sources = []
        else:
            # Generate response with selected model
            response_text, sources = generate_llm_response(query, search_results, selected_model)
        
        # Store messages in database
        db.add_message(user_id, session_id, 'user', query)
        db.add_message(user_id, session_id, 'assistant', response_text, sources)
        
        # Cache result
        result = {
            'response': response_text,
            'sources': sources,
            'model_used': selected_model
        }
        cache.cache_query_result(user_id, cache_key, result)
        
        print(f"✅ Response generated successfully with {selected_model}")
        
        return create_success_response({
            'response': response_text,
            'sources': sources,
            'cached': False,
            'model_used': selected_model
        })
        
    except Exception as e:
        print(f"❌ Chat handler error: {str(e)}")
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

def generate_llm_response(query: str, contexts: List[Dict], model: str = None) -> tuple:
    """Generate response using LLM with DYNAMIC model selection"""
    prompt = generate_response_prompt(query, contexts)
    
    # Extract provider from model string (not from static config!)
    if not model:
        model = "together-meta-llama/Llama-3.3-70B-Instruct-Turbo-Free"  # Default
    
    print(f"🤖 LLM Request - Full model: {model}")
    
    try:
        # ✅ DYNAMIC ROUTING based on model prefix
        if model.startswith("together-"):
            # Extract model name: "together-meta-llama/Llama-3.3-70B" → "meta-llama/Llama-3.3-70B"
            model_name = model.replace("together-", "")
            print(f"🟢 Using Together AI with model: {model_name}")
            response_text = call_together_ai(prompt, model_name)
            
        elif model.startswith("gemini-"):
            
            model_name = model.replace("gemini-", "")
            
            actual_model_name = f"gemini-{model_name}"
            print(f"🔵 Using Gemini with model: {actual_model_name}")
            response_text = call_gemini(prompt, actual_model_name)
            
        else:
            # Fallback for unknown models
            print(f"⚠️ Unknown model format: {model}, using default Together AI")
            response_text = call_together_ai(prompt, "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free")
            
    except Exception as e:
        print(f"❌ LLM generation error with {model}: {str(e)}")
        import traceback
        traceback.print_exc()
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
    """Clear user's cache with enhanced debugging"""
    try:
        # DEBUG: Print the entire event structure
        print("=== CLEAR CACHE DEBUG ===")
        print(f"Full event: {json.dumps(event, indent=2, default=str)}")
        print(f"Context: {context}")
        
        # Check if we have proper authorization context
        request_context = event.get('requestContext', {})
        authorizer = request_context.get('authorizer', {})
        claims = authorizer.get('claims', {})
        
        print(f"Request context: {request_context}")
        print(f"Authorizer: {authorizer}")
        print(f"Claims: {claims}")
        
        # Try to get user_id from different possible locations
        user_id = None
        
        # Method 1: From authorizer claims (API Gateway + Cognito)
        if claims and 'sub' in claims:
            user_id = claims['sub']
            print(f"Got user_id from authorizer claims: {user_id}")
        
        # Method 2: From authorizer directly (custom authorizer)
        elif authorizer and 'principalId' in authorizer:
            user_id = authorizer['principalId']
            print(f"Got user_id from authorizer principalId: {user_id}")
        
        # Method 3: From headers (if using custom auth)
        elif 'headers' in event:
            auth_header = event['headers'].get('Authorization', '')
            print(f"Authorization header: {auth_header[:50]}..." if auth_header else "No Authorization header")
            
            # Try to decode JWT manually if needed
            if auth_header.startswith('Bearer '):
                token = auth_header[7:]
                try:
                    # Basic JWT decode without verification (for debugging)
                    import base64
                    import json
                    
                    # Split JWT parts
                    parts = token.split('.')
                    if len(parts) == 3:
                        # Decode payload
                        payload = parts[1]
                        # Add padding if needed
                        payload += '=' * (4 - len(payload) % 4)
                        decoded = base64.b64decode(payload)
                        payload_data = json.loads(decoded)
                        user_id = payload_data.get('sub')
                        print(f"Decoded user_id from JWT: {user_id}")
                        print(f"JWT payload: {payload_data}")
                except Exception as jwt_error:
                    print(f"JWT decode error: {jwt_error}")
        
        if not user_id:
            print("ERROR: Could not determine user_id from any method")
            return create_error_response(403, 'Unable to identify user - missing or invalid authorization')
        
        print(f"Final user_id: {user_id}")
        
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
            'stats_after': stats_after,
            'debug_info': {
                'auth_method': 'claims' if claims else 'header' if 'headers' in event else 'unknown',
                'had_claims': bool(claims),
                'had_auth_header': bool(event.get('headers', {}).get('Authorization'))
            }
        })
        
    except Exception as e:
        print(f"Clear cache error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, f'Failed to clear cache: {str(e)}')

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

def call_together_ai(prompt: str, model: str = "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free") -> str:
    """Call Together AI API with specific model"""
    
    print(f"🟢 Together AI - Checking configuration...")
    
    # ✅ CHECK IF API KEY EXISTS
    if not config.TOGETHER_API_KEY:
        error_msg = "Together AI API key not configured"
        print(f"❌ {error_msg}")
        raise Exception(error_msg)
        
    print(f"🟢 Together AI - API key configured: {config.TOGETHER_API_KEY[:10]}...")
    
    headers = {
        "Authorization": f"Bearer {config.TOGETHER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": model,  # Use the specific model passed in
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": config.MAX_TOKENS,
        "temperature": 0.7,
        "top_p": 0.9
    }
    
    print(f"🟢 Together AI - Request data: {json.dumps(data, indent=2)}")
    
    try:
        print(f"🟢 Together AI - Making API request...")
        response = requests.post(
            "https://api.together.xyz/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=30
        )
        
        print(f"🟢 Together AI - Response status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            content = result['choices'][0]['message']['content'].strip()
            print(f"🟢 Together AI - Success! Response length: {len(content)}")
            return content
        else:
            error_text = response.text
            print(f"❌ Together AI error: {response.status_code} - {error_text}")
            raise Exception(f"Together AI API error: {response.status_code} - {error_text}")
            
    except Exception as e:
        print(f"❌ Together AI request failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return "I encountered an error while generating a response. Please try again."


def call_gemini(prompt: str, model: str = "gemini-2.5-flash") -> str:
    """Call Google Gemini API with specific model"""
    
    print(f"🔵 Gemini - Starting request with model: {model}")
    
    # ✅ CHECK IF API KEY EXISTS
    if not hasattr(config, 'GEMINI_API_KEY') or not config.GEMINI_API_KEY:
        error_msg = "Gemini API key not configured"
        print(f"❌ {error_msg}")
        raise Exception(error_msg)
    
    print(f"🔵 Gemini - API key configured: {config.GEMINI_API_KEY[:10]}...")
    
    try:
        print(f"🔵 Gemini - Importing google.generativeai...")
        import google.generativeai as genai
        
        print(f"🔵 Gemini - Configuring API...")
        # Configure Gemini with API key
        genai.configure(api_key=config.GEMINI_API_KEY)
        
        print(f"🔵 Gemini - Creating model instance: {model}")
        # Create model instance with specific model
        model_instance = genai.GenerativeModel(model)
        
        print(f"🔵 Gemini - Generating content...")
        print(f"🔵 Gemini - Prompt length: {len(prompt)}")
        
        # Generate response
        response = model_instance.generate_content(prompt)
        
        print(f"🔵 Gemini - Raw response: {response}")
        
        if hasattr(response, 'text') and response.text:
            content = response.text.strip()
            print(f"🔵 Gemini - Success! Response length: {len(content)}")
            return content
        else:
            error_msg = f"Gemini returned empty response: {response}"
            print(f"❌ {error_msg}")
            raise Exception(error_msg)
        
    except ImportError as e:
        error_msg = "google-generativeai library not installed. Run: pip install google-generativeai"
        print(f"❌ Import error: {error_msg}")
        raise Exception(error_msg)
    except Exception as e:
        print(f"❌ Gemini request failed: {str(e)}")
        print(f"❌ Gemini error type: {type(e)}")
        import traceback
        traceback.print_exc()
        raise Exception(f"Gemini API error: {str(e)}")


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
        print(f"Event path parameters: {event.get('pathParameters', {})}")
        print(f"Event query parameters: {event.get('queryStringParameters', {})}")

        # Handle CORS preflight
        if method == 'OPTIONS':
            return create_success_response({"message": "CORS preflight success"})

        # Remove /api prefix if present for routing
        path = original_path
        if path.startswith('/api'):
            path = path[4:]  # Remove '/api' prefix
        
        print(f"Processed path for routing: {path}")

        # Cache management routes - ADD MORE FLEXIBLE MATCHING
        if path.endswith('/cache/clear') and method == 'POST':
            print("Routing to clear_cache_handler")
            return clear_cache_handler(event, context)
        elif path.endswith('/cache/stats') and method == 'GET':
            print("Routing to get_cache_stats_handler")
            return get_cache_stats_handler(event, context)
        
        # Session management routes
        elif path == '/chat/sessions' and method == 'POST':
            print("Routing to create_session_handler")
            return create_session_handler(event, context)
        elif path == '/chat/sessions' and method == 'GET':
            print("Routing to list_sessions_handler")
            return list_sessions_handler(event, context)
        elif path == '/chat/sessions/search' and method == 'GET':
            print("Routing to search_sessions_handler")
            return search_sessions_handler(event, context)
        elif path.startswith('/chat/sessions/') and path.endswith('/messages') and method == 'POST':
            print("Routing to chat_handler")
            return chat_handler(event, context)
        elif path.startswith('/chat/sessions/') and path.endswith('/messages') and method == 'GET':
            print("Routing to get_messages_handler")
            return get_messages_handler(event, context)
        elif path.startswith('/chat/sessions/') and path.endswith('/export') and method == 'GET':
            print("Routing to export_session_handler")
            return export_session_handler(event, context)
        elif path.startswith('/chat/sessions/') and path.endswith('/auto-title') and method == 'POST':
            print("Routing to auto_title_session_handler")
            return auto_title_session_handler(event, context)
        elif path.startswith('/chat/sessions/') and not path.endswith('/messages') and not path.endswith('/export') and not path.endswith('/auto-title') and method == 'PUT':
            print("Routing to update_session_handler")
            return update_session_handler(event, context)
        elif path.startswith('/chat/sessions/') and not path.endswith('/messages') and not path.endswith('/export') and not path.endswith('/auto-title') and method == 'DELETE':
            print("Routing to delete_session_handler")
            return delete_session_handler(event, context)
        elif path == '/test/models' and method == 'GET':
            print("Routing to test_models_handler")
            return test_models_handler(event, context)
        elif path == '/test/auth' and method == 'GET':
            print("Routing to test_auth_handler")
            return test_auth_handler(event, context)
        else:
            print(f"No route found for: {method} {original_path} (processed: {path})")
            return create_error_response(404, f'Not found: {method} {original_path} (processed: {path})')
    
    except Exception as e:
        print(f"Handler router error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, 'Internal server error')
    
# def test_models_handler(event: Dict, context) -> Dict:
#     """Test endpoint to verify all models are working"""
#     try:
#         user_id = event['requestContext']['authorizer']['claims']['sub']
        
#         test_prompt = "Hello, please respond with 'Test successful' if you can understand this message."
#         test_results = {}
        
#         print(f"🧪 Testing models for user: {user_id}")
        
#         # Test Together AI models
#         together_models = [
#             "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
#             "meta-llama/Llama-3.1-8B-Instruct-Turbo"
#         ]
        
#         for model in together_models:
#             try:
#                 print(f"🟢 Testing Together AI model: {model}")
#                 response = call_together_ai(test_prompt, model)
#                 test_results[f"together-{model}"] = {
#                     "status": "success",
#                     "response": response[:100] + "..." if len(response) > 100 else response
#                 }
#                 print(f"✅ Together AI {model}: SUCCESS")
#             except Exception as e:
#                 print(f"❌ Together AI {model}: FAILED - {str(e)}")
#                 test_results[f"together-{model}"] = {
#                     "status": "error",
#                     "error": str(e)
#                 }
        
#         # Test Gemini models
#         gemini_models = [
#             "gemini-2.5-flash",
#             "gemini-2.5-pro"
#         ]
        
#         for model in gemini_models:
#             try:
#                 print(f"🔵 Testing Gemini model: {model}")
#                 response = call_gemini(test_prompt, model)
#                 test_results[f"gemini-{model}"] = {
#                     "status": "success", 
#                     "response": response[:100] + "..." if len(response) > 100 else response
#                 }
#                 print(f"✅ Gemini {model}: SUCCESS")
#             except Exception as e:
#                 print(f"❌ Gemini {model}: FAILED - {str(e)}")
#                 test_results[f"gemini-{model}"] = {
#                     "status": "error",
#                     "error": str(e)
#                 }
        
#         return create_success_response({
#             "test_results": test_results,
#             "config_check": {
#                 "together_api_key_exists": bool(getattr(config, 'TOGETHER_API_KEY', None)),
#                 "gemini_api_key_exists": bool(getattr(config, 'GEMINI_API_KEY', None)),
#                 "together_key_preview": config.TOGETHER_API_KEY[:10] + "..." if getattr(config, 'TOGETHER_API_KEY', None) else "NOT SET",
#                 "gemini_key_preview": config.GEMINI_API_KEY[:10] + "..." if getattr(config, 'GEMINI_API_KEY', None) else "NOT SET"
#             }
#         })
        
#     except Exception as e:
#         print(f"❌ Model test error: {str(e)}")
#         import traceback
#         traceback.print_exc()
#         return create_error_response(500, f'Model test failed: {str(e)}')