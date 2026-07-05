# backend/lambdas/chat/handler.py - FIXED VERSION

import json
import requests
from typing import Dict, List, Tuple
from shared.config import config
from shared.database import db
from shared.vector_store import vector_store
from shared.cache import cache
from decimal import Decimal
from shared.utils import generate_response_prompt, create_success_response, create_error_response
from datetime import datetime
import re

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
    
    # Step 2: Group by document and select best chunks per document
    document_chunks = {}
    for result in initial_results:
        doc_id = result.get('document_id', 'unknown')
        if doc_id not in document_chunks:
            document_chunks[doc_id] = []
        document_chunks[doc_id].append(result)
    
    # Step 3: Select best chunks from each document
    selected_results = []
    for doc_id, chunks in document_chunks.items():
        # Sort chunks by score (best first)
        chunks.sort(key=lambda x: x.get('score', 0), reverse=True)
        
        # Take top 3 chunks per document, but ensure good diversity
        doc_chunks = []
        positions_used = set()
        
        for chunk in chunks:
            position = chunk.get('position', -1)
            score = chunk.get('score', 0)
            
            # Only add if score is decent and position not too close to existing
            if score > 0.1:  # Minimum relevance threshold
                if position == -1 or not any(abs(position - used_pos) < 5 for used_pos in positions_used):
                    doc_chunks.append(chunk)
                    if position != -1:
                        positions_used.add(position)
                    
                    if len(doc_chunks) >= 3:  # Max 3 chunks per document
                        break
        
        selected_results.extend(doc_chunks)
        print(f"Selected {len(doc_chunks)} chunks from document {doc_id}")
    
    # Step 4: Sort final results by score and limit total
    selected_results.sort(key=lambda x: x.get('score', 0), reverse=True)
    final_results = selected_results[:10]  # Max 10 total chunks
    
    print(f"Final selection: {len(final_results)} results from {len(document_chunks)} documents")
    return final_results

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

def is_quote_page_query(query: str) -> Tuple[bool, str]:
    """
    Detect if query is asking "which page is this quote on"
    Returns: (is_quote_query, extracted_quote)
    """
    query_lower = query.lower()
    
    # Patterns that indicate page number queries
    page_patterns = [
        'which page',
        'what page',
        'page number',
        'on which page',
        'on what page',
        'find page',
        'locate page',
        'where can i find',
        'where is',
        'locate this',
        'find this text'
    ]
    
    # Check if query contains page-related keywords
    has_page_keyword = any(pattern in query_lower for pattern in page_patterns)
    
    if has_page_keyword:
        # Try to extract quoted text using regex
        # Look for text in double quotes
        quoted = re.findall(r'"([^"]+)"', query)
        if quoted and len(quoted[0]) > 10:
            return True, quoted[0]
        
        # Look for text in single quotes
        quoted = re.findall(r"'([^']+)'", query)
        if quoted and len(quoted[0]) > 10:
            return True, quoted[0]
        
        # If no quotes but has page keywords, try to extract the rest
        for pattern in page_patterns:
            if pattern in query_lower:
                idx = query_lower.find(pattern)
                # Get text after the pattern
                potential_quote = query[idx + len(pattern):].strip()
                
                # Clean up common question words
                potential_quote = re.sub(r'^(is|for|:|the|this|about)\s+', '', potential_quote, flags=re.IGNORECASE)
                potential_quote = potential_quote.strip('?!.,;')
                
                # Only return if it's substantial enough
                if len(potential_quote) > 15:
                    return True, potential_quote
    
    return False, None

def chat_handler(event: Dict, context) -> Dict:
    """
    Handle chat message with HYBRID SEARCH + QUOTE-TO-PAGE detection
    🔥 NEW: Automatically detects and handles "which page" queries
    """
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        session_id = event['pathParameters']['session_id']
        body = json.loads(event['body'])
        query = body['message']
        
        selected_model = body.get('model', 'gemini-flash-latest')
        use_hybrid = body.get('use_hybrid_search', True)
        
        print(f"💬 Chat request - User: {user_id}, Session: {session_id}")
        print(f"🎯 Query: {query}")
        print(f"🤖 Model: {selected_model}")
        
        # Check rate limit
        if not cache.check_rate_limit(user_id, 'chat', config.CHAT_RATE_LIMIT):
            return create_error_response(429, 'Chat rate limit exceeded')
        
        # Get session info
        session = db.get_session(user_id, session_id)
        if not session:
            return create_error_response(404, f'Session not found: {session_id}')
        
        # 🔥 NEW: Check if this is a quote-to-page query
        is_quote_query, extracted_quote = is_quote_page_query(query)
        
        if is_quote_query and extracted_quote:
            print(f"📖 QUOTE-TO-PAGE QUERY DETECTED")
            print(f"   Extracted quote: '{extracted_quote[:100]}...'")
            
            # Use the specialized quote finding function
            result = vector_store.find_quote_page(
                user_id,
                extracted_quote,
                session['document_set']
            )
            
            # Format response based on result
            if result['found']:
                if result['confidence'] == 'exact':
                    if len(result['page_numbers']) == 1:
                        response_text = f'The text "{extracted_quote}" is found on **page {result["page_numbers"][0]}** in the document "{result["filename"]}".'
                    else:
                        pages_str = ", ".join(str(p) for p in result['page_numbers'])
                        response_text = f'The text "{extracted_quote}" appears on multiple pages: **{pages_str}** in the document "{result["filename"]}".'
                        
                        if result.get('total_occurrences', 0) > len(result['page_numbers']):
                            response_text += f' (Found {result["total_occurrences"]} total occurrences across these pages.)'
                
                elif result['confidence'] == 'fuzzy':
                    page_num = result['page_numbers'][0] if result['page_numbers'] else 'unknown'
                    response_text = f'I found similar content on **page {page_num}** in "{result["filename"]}", though it may not be an exact word-for-word match. The semantic similarity score is {result.get("semantic_score", 0):.2f}.'
                
                # Format sources
                sources = []
                for match in result['matches'][:3]:  # Top 3 matches
                    source_info = {
                        'document_id': match['document_id'],
                        'filename': match.get('filename', 'Unknown'),
                        'text': match['chunk_text'][:300] + '...',
                        'relevance_score': 1.0 if match['match_type'] == 'exact' else match.get('score', 0.8),
                        'exact_match': match['match_type'] == 'exact',
                        'match_type': match['match_type']
                    }
                    
                    if match.get('page_number'):
                        source_info['pages'] = [match['page_number']]
                    
                    sources.append(source_info)
            
            else:
                response_text = f'I couldn\'t find the text "{extracted_quote}" in your documents. It might be phrased differently, or it may not exist in the uploaded documents.'
                sources = []
            
            # Store messages
            db.add_message(user_id, session_id, 'user', query)
            db.add_message(user_id, session_id, 'assistant', response_text, sources)
            
            print(f"✅ Quote-to-page query handled successfully")
            
            return create_success_response({
                'response': response_text,
                'sources': sources,
                'query_type': 'quote_page_lookup',
                'confidence': result.get('confidence', 'not_found'),
                'cached': False,
                'model_used': selected_model
            })
        
        # 🔥 REGULAR CHAT FLOW (if not a quote query)
        print(f"🔍 Search mode: {'Hybrid' if use_hybrid else 'Vector only'}")
        
        # Get conversation history
        previous_messages = db.get_session_messages(session_id, limit=10)
        
        # Create cache key
        history_hash = hash(str([(m.get('role'), m.get('content', '')[:100]) 
                                for m in previous_messages[-6:]]))
        cache_key = f"{query}_{selected_model}_{history_hash}_{use_hybrid}"
        
        # Check cache
        cached_result = cache.get_cached_query(user_id, cache_key)
        if cached_result:
            print(f"💾 Returning cached response")
            return create_success_response({
                'response': cached_result['response'],
                'sources': cached_result['sources'],
                'cached': True,
                'model_used': selected_model
            })
        
        # Generate query embedding
        print(f"🔄 Generating query embedding...")
        query_embedding = vector_store.generate_embeddings([query])[0]
        
        if query_embedding is None:
            print(f"❌ Failed to generate query embedding")
            response_text = "I encountered an error generating the search query. Please try again."
            sources = []
        else:
            # Use hybrid or standard search
            if use_hybrid:
                print(f"🔍 Using hybrid search (vector + exact match)...")
                search_results = vector_store.hybrid_search(
                    user_id,
                    query,
                    query_embedding,
                    session['document_set'],
                    top_k=15
                )
            else:
                print(f"🔍 Using standard vector search...")
                search_results = vector_store.search(
                    user_id,
                    query_embedding,
                    session['document_set'],
                    top_k=15,
                    min_score=0.0
                )
            
            print(f"📊 Search returned {len(search_results)} results")
            
            for i, result in enumerate(search_results[:3], 1):
                match_type = result.get('match_type', 'semantic')
                page_info = f", page {result.get('page_number')}" if 'page_number' in result else ""
                print(f"  {i}. [{match_type}] Score: {result['score']:.3f}{page_info}")
            
            if not search_results:
                response_text = "I couldn't find relevant information in your documents to answer this question."
                sources = []
            else:
                # Generate response with context
                response_text, sources = generate_llm_response_with_history(
                    query, 
                    search_results, 
                    previous_messages,
                    selected_model
                )
        
        # Store messages
        db.add_message(user_id, session_id, 'user', query)
        db.add_message(user_id, session_id, 'assistant', response_text, sources)
        
        # Cache result
        result = {
            'response': response_text,
            'sources': sources,
            'model_used': selected_model
        }
        cache.cache_query_result(user_id, cache_key, result)
        
        print(f"✅ Response generated successfully")
        
        return create_success_response({
            'response': response_text,
            'sources': sources,
            'cached': False,
            'model_used': selected_model,
            'search_mode': 'hybrid' if use_hybrid else 'vector',
            'results_count': len(search_results) if 'search_results' in locals() else 0
        })
        
    except Exception as e:
        print(f"❌ Chat handler error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, 'Failed to process message')

def generate_llm_response_with_history(query: str, contexts: List[Dict], 
                                     previous_messages: List[Dict], model: str = None) -> tuple:
    """
    Generate response using LLM - IMPROVED with better context formatting
    🔥 IMPROVED: Show page numbers, highlight exact matches, better grouping
    """
    
    # Group contexts by document
    document_groups = {}
    for ctx in contexts:
        doc_id = ctx.get('document_id', 'unknown')
        if doc_id not in document_groups:
            document_groups[doc_id] = {
                'document_id': doc_id,
                'filename': ctx.get('filename', 'Unknown'),
                'chunks': [],
                'best_score': 0,
                'has_exact_match': False
            }
        document_groups[doc_id]['chunks'].append(ctx)
        document_groups[doc_id]['best_score'] = max(
            document_groups[doc_id]['best_score'], 
            ctx.get('score', 0)
        )
        if ctx.get('exact_match', False):
            document_groups[doc_id]['has_exact_match'] = True
    
    # Sort documents: exact matches first, then by score
    sorted_docs = sorted(
        document_groups.values(), 
        key=lambda x: (x['has_exact_match'], x['best_score']), 
        reverse=True
    )
    
    # Take top 3 documents
    top_documents = sorted_docs[:3]
    
    # Build conversation history
    conversation_context = ""
    if previous_messages:
        conversation_context = "\n\nPrevious conversation:\n"
        recent_messages = previous_messages[-6:]
        for msg in recent_messages:
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')[:200]
            if role == 'user':
                conversation_context += f"User: {content}\n"
            elif role == 'assistant':
                conversation_context += f"Assistant: {content}\n"
    
    # Build document context with page numbers
    document_context = ""
    if top_documents:
        document_context = "\n\nRelevant information from your documents:\n\n"
        
        for i, doc_group in enumerate(top_documents, 1):
            doc_id = doc_group['document_id']
            filename = doc_group['filename']
            chunks = doc_group['chunks']
            has_exact = doc_group['has_exact_match']
            
            # Sort chunks by position to maintain document flow
            chunks.sort(key=lambda x: x.get('position', 0))
            
            exact_marker = " [EXACT MATCH]" if has_exact else ""
            document_context += f"Document {i}: {filename}{exact_marker}\n"
            
            # Combine chunks intelligently
            combined_text = ""
            pages_covered = set()
            
            for chunk in chunks[:3]:  # Top 3 chunks per document
                chunk_text = chunk.get('chunk_text', '')
                page_num = chunk.get('page_number')
                
                if page_num:
                    pages_covered.add(page_num)
                
                if chunk_text and chunk_text not in combined_text:
                    if combined_text:
                        combined_text += " [...] "
                    combined_text += chunk_text
            
            # Limit combined text
            if len(combined_text) > 1000:
                combined_text = combined_text[:1000] + "..."
            
            # Add page information if available
            if pages_covered:
                pages_str = ", ".join(str(p) for p in sorted(pages_covered))
                document_context += f"(Pages: {pages_str})\n"
            
            document_context += f"{combined_text}\n\n"
    
    # Create comprehensive prompt
    prompt = f"""You are a helpful AI assistant. Based on the provided documents and conversation history, answer the user's question accurately and helpfully.

{conversation_context}

{document_context}

Current question: {query}

Instructions:
- Answer based on the information provided in the documents
- If documents are marked with [EXACT MATCH], prioritize that information
- Include page numbers when referencing specific information
- If the information isn't in the documents, say so clearly
- Maintain context from the previous conversation when relevant
- Be concise but thorough

Answer:"""
    
    # Extract provider from model string
    if not model:
        model = "gemini-flash-latest"
    
    print(f"🤖 LLM Request - Model: {model}")
    
    try:
        # Route to appropriate LLM
        if model.startswith("together-"):
            model_name = model.replace("together-", "")
            response_text = call_together_ai(prompt, model_name)
        elif model.startswith("gemini-"):
            model_name = model.replace("gemini-", "")
            actual_model_name = f"gemini-{model_name}"
            response_text = call_gemini(prompt, actual_model_name)
        else:
            response_text = call_gemini(prompt, "gemini-flash-latest")
            
    except Exception as e:
        print(f"❌ LLM generation error: {str(e)}")
        import traceback
        traceback.print_exc()
        response_text = "I encountered an error while generating a response. Please try again."
    
    # Format sources by document with page numbers
    sources = []
    for doc_group in top_documents:
        doc_id = doc_group['document_id']
        filename = doc_group['filename']
        chunks = doc_group['chunks']
        has_exact = doc_group['has_exact_match']
        
        # Get pages from chunks
        pages = set()
        for chunk in chunks:
            if 'page_number' in chunk:
                pages.add(chunk['page_number'])
        
        # Create combined preview
        combined_preview = ""
        for chunk in chunks[:2]:
            chunk_text = chunk.get('chunk_text', '')
            if chunk_text:
                if combined_preview:
                    combined_preview += " ... "
                combined_preview += chunk_text[:150]
        
        if len(combined_preview) > 250:
            combined_preview = combined_preview[:250] + "..."
        
        source_info = {
            'document_id': doc_id,
            'filename': filename,
            'text': combined_preview,
            'relevance_score': doc_group['best_score'],
            'chunk_count': len(chunks),
            'exact_match': has_exact
        }
        
        # Add page info if available
        if pages:
            source_info['pages'] = sorted(list(pages))
        
        sources.append(source_info)
    
    return response_text, sources

# Keep existing functions
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

# Continue with rest of existing functions...
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

def call_gemini(prompt: str, model: str = "gemini-flash-latest") -> str:
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

def debug_find_text_handler(event: Dict, context) -> Dict:
    """
    🔥 NEW: Find exact text in vector store
    POST /debug/find-text
    Body: {"text": "your search text", "document_ids": ["doc1", "doc2"]}
    """
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        body = json.loads(event['body'])
        
        search_text = body.get('text', '').strip()
        document_ids = body.get('document_ids', [])
        
        if not search_text:
            return create_error_response(400, 'Search text is required')
        
        # If no document IDs provided, get all active documents
        if not document_ids:
            docs = db.list_user_documents(user_id)
            document_ids = [d['document_id'] for d in docs if d.get('active')]
        
        if not document_ids:
            return create_error_response(400, 'No active documents found')
        
        print(f"🔍 Searching for text: '{search_text}' in {len(document_ids)} documents")
        
        # Use the debug search function
        matches = vector_store.debug_search_for_text(user_id, search_text, document_ids)
        
        return create_success_response({
            'query': search_text,
            'matches': matches,
            'count': len(matches),
            'searched_documents': document_ids
        })
        
    except Exception as e:
        print(f"Debug find text error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, str(e))

def debug_document_coverage_handler(event: Dict, context) -> Dict:
    """
    🔥 NEW: Check document coverage in vector store
    GET /debug/document/{document_id}/coverage
    """
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        document_id = event['pathParameters']['document_id']
        
        print(f"🔍 Checking coverage for document {document_id}")
        
        # Get document info
        doc_info = vector_store.get_document_chunk_info(user_id, document_id)
        
        # Verify coverage
        coverage = vector_store.verify_document_coverage(user_id, document_id)
        
        return create_success_response({
            'document_id': document_id,
            'info': doc_info,
            'coverage': coverage
        })
        
    except Exception as e:
        print(f"Debug coverage error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, str(e))

def debug_hybrid_search_handler(event: Dict, context) -> Dict:
    """
    🔥 NEW: Test hybrid search (vector + exact match)
    POST /debug/hybrid-search
    Body: {"query": "your query", "document_ids": ["doc1"]}
    """
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        body = json.loads(event['body'])
        
        query = body.get('query', '').strip()
        document_ids = body.get('document_ids', [])
        
        if not query:
            return create_error_response(400, 'Query is required')
        
        # Get active documents if not specified
        if not document_ids:
            docs = db.list_user_documents(user_id)
            document_ids = [d['document_id'] for d in docs if d.get('active')]
        
        if not document_ids:
            return create_error_response(400, 'No active documents')
        
        print(f"🔍 Hybrid search: '{query}'")
        
        # Generate embedding
        query_embedding = vector_store.generate_embeddings([query])[0]
        
        if query_embedding is None:
            return create_error_response(500, 'Failed to generate query embedding')
        
        # Perform hybrid search
        results = vector_store.hybrid_search(
            user_id, query, query_embedding, document_ids, top_k=10
        )
        
        return create_success_response({
            'query': query,
            'results': results,
            'count': len(results)
        })
        
    except Exception as e:
        print(f"Hybrid search error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, str(e))

def debug_vector_stats_handler(event: Dict, context) -> Dict:
    """
    🔥 NEW: Get Pinecone index statistics
    GET /debug/vector-stats
    """
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        
        # Get index stats
        stats = vector_store.get_index_stats()
        
        # Get user's document count
        docs = db.list_user_documents(user_id)
        active_docs = [d for d in docs if d.get('active')]
        
        # Get chunk info for each document
        document_stats = []
        for doc in active_docs[:5]:  # Limit to first 5 docs
            doc_id = doc['document_id']
            chunk_info = vector_store.get_document_chunk_info(user_id, doc_id)
            document_stats.append({
                'document_id': doc_id,
                'filename': doc.get('filename', 'Unknown'),
                'chunk_count': chunk_info.get('chunk_count', 0),
                'sections': chunk_info.get('sections', {}),
                'pages': chunk_info.get('pages', [])
            })
        
        return create_success_response({
            'user_id': user_id,
            'index_stats': stats,
            'user_documents': len(docs),
            'active_documents': len(active_docs),
            'document_stats': document_stats
        })
        
    except Exception as e:
        print(f"Vector stats error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, str(e))

def test_search_quality_handler(event: Dict, context) -> Dict:
    """
    🔥 NEW: Test search quality with various thresholds
    POST /debug/test-search
    Body: {"query": "your query", "document_ids": ["doc1"], "thresholds": [0.0, 0.05, 0.1]}
    """
    try:
        user_id = event['requestContext']['authorizer']['claims']['sub']
        body = json.loads(event['body'])
        
        query = body.get('query', '').strip()
        document_ids = body.get('document_ids', [])
        thresholds = body.get('thresholds', [0.0, 0.05, 0.1, 0.15, 0.2])
        
        if not query:
            return create_error_response(400, 'Query is required')
        
        # Get active documents if not specified
        if not document_ids:
            docs = db.list_user_documents(user_id)
            document_ids = [d['document_id'] for d in docs if d.get('active')]
        
        print(f"🧪 Testing search quality for: '{query}'")
        
        # Generate embedding
        query_embedding = vector_store.generate_embeddings([query])[0]
        
        if query_embedding is None:
            return create_error_response(500, 'Failed to generate embedding')
        
        # Test with different thresholds
        results_by_threshold = {}
        
        for threshold in thresholds:
            results = vector_store.search(
                user_id, query_embedding, document_ids,
                top_k=20, min_score=threshold
            )
            
            results_by_threshold[f"threshold_{threshold}"] = {
                'count': len(results),
                'top_5': results[:5] if results else []
            }
            
            print(f"  Threshold {threshold}: {len(results)} results")
        
        return create_success_response({
            'query': query,
            'results_by_threshold': results_by_threshold,
            'recommendation': 'Use threshold 0.0 for best recall, 0.1+ for precision'
        })
        
    except Exception as e:
        print(f"Test search error: {str(e)}")
        import traceback
        traceback.print_exc()
        return create_error_response(500, str(e))

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
            print("→ Handling OPTIONS preflight request")
            return {
                'statusCode': 200,
                'headers': {
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type,Authorization,X-Amz-Date,X-Api-Key,X-Amz-Security-Token',
                    'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS',
                    'Access-Control-Allow-Credentials': 'true'
                },
                'body': json.dumps({})
            }

        # Remove /api prefix if present for routing
        path = original_path
        if path.startswith('/api'):
            path = path[4:]  # Remove '/api' prefix
        
        print(f"Processed path for routing: {path}")

        # Public guest chat (no auth; rate-limited per IP inside the handler)
        if path == '/guest/chat' and method == 'POST':
            from chat.guest import guest_chat_handler
            return guest_chat_handler(event, context)

        # Cache management routes - ADD MORE FLEXIBLE MATCHING
        if path == '/debug/find-text' and method == 'POST':
            return debug_find_text_handler(event, context)
        elif path.startswith('/debug/document/') and path.endswith('/coverage') and method == 'GET':
            return debug_document_coverage_handler(event, context)
        elif path == '/debug/hybrid-search' and method == 'POST':
            return debug_hybrid_search_handler(event, context)
        elif path == '/debug/vector-stats' and method == 'GET':
            return debug_vector_stats_handler(event, context)
        elif path == '/debug/test-search' and method == 'POST':
            return test_search_quality_handler(event, context)
        elif path.endswith('/cache/clear') and method == 'POST':
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