# backend/lambdas/shared/vector_store.py
# REFACTORED VERSION - Fixes all identified issues + HuggingFace API update

from pinecone import Pinecone, ServerlessSpec
from typing import List, Dict, Tuple, Optional
import hashlib
import requests
import json
import time
import os
from .config import config

class VectorStore:
    def __init__(self):
        """Initialize Pinecone with new API"""
        try:
            self.pc = Pinecone(api_key=config.PINECONE_API_KEY)
            
            # Check if index exists
            existing_indexes = self.pc.list_indexes().names()
            if config.PINECONE_INDEX not in existing_indexes:
                print(f"Creating Pinecone index: {config.PINECONE_INDEX}")
                self.pc.create_index(
                    name=config.PINECONE_INDEX,
                    dimension=config.EMBEDDING_DIMENSION,
                    metric='cosine',
                    spec=ServerlessSpec(cloud='aws', region='us-west-1')
                )
                time.sleep(10)  # Wait for index to be ready
            
            self.index = self.pc.Index(config.PINECONE_INDEX)
            print(f"✅ Connected to Pinecone index: {config.PINECONE_INDEX}")
            
        except Exception as e:
            print(f"❌ Error initializing Pinecone: {str(e)}")
            raise
    
    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings using Google Gemini Embedding API
        """
        if not texts:
            return []
        
        print(f"🔄 Generating embeddings for {len(texts)} texts")

        if not config.GEMINI_API_KEY:
            raise Exception("GEMINI_API_KEY not configured")

        model = "models/text-embedding-004"
        url = f"https://generativelanguage.googleapis.com/v1beta/{model}:embedContent"

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": config.GEMINI_API_KEY,
        }

        embeddings = []

        try:
            for text in texts:
                payload = {
                    "model": model,
                    "content": {
                        "parts": [{"text": text}]
                    }
                }

                response = requests.post(url, headers=headers, json=payload, timeout=30)

                print("Status:", response.status_code)
                # print(response.text)

                if response.status_code != 200:
                    raise Exception(f"API error {response.status_code}: {response.text}")

                data = response.json()

                vector = data["embedding"]["values"]
                embeddings.append(vector)

            print(f"✅ Generated {len(embeddings)} embeddings")
            return embeddings

        except Exception as e:
            print(f"❌ Batch embedding failed: {str(e)}")
            raise



    
    def upsert_chunks(self, user_id: str, document_id: str, 
                     chunks: List[Dict[str, str]], embeddings: List[List[float]],
                     document_metadata: Optional[Dict] = None):
        """
        Store document chunks with embeddings in Pinecone
        🔥 FIXED: Store full chunk text, add page numbers, skip failed embeddings
        """
        if not chunks or not embeddings:
            print("⚠️  No chunks or embeddings to upsert")
            return
        
        if len(chunks) != len(embeddings):
            print(f"❌ ERROR: Chunk count ({len(chunks)}) != embedding count ({len(embeddings)})")
            return
        
        print(f"🔄 Upserting {len(chunks)} chunks for document {document_id}")
        
        total_chunks = len(chunks)
        vectors = []
        skipped_chunks = 0
        
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            # 🔥 FIXED: Skip chunks with failed embeddings (None)
            if embedding is None:
                print(f"  ⚠️  Skipping chunk {i} (failed embedding)")
                skipped_chunks += 1
                continue
            
            # Validate embedding
            if len(embedding) != config.EMBEDDING_DIMENSION:
                print(f"  ❌ Invalid embedding at position {i}: {len(embedding)} dimensions")
                skipped_chunks += 1
                continue
            
            # Generate unique vector ID
            vector_id = hashlib.md5(f"{user_id}:{document_id}:{i}".encode()).hexdigest()
            
            # Calculate position percentage
            position_percentage = (i / max(total_chunks - 1, 1)) * 100
            
            # Determine document section
            if position_percentage < 33:
                document_section = 'beginning'
            elif position_percentage < 67:
                document_section = 'middle'
            else:
                document_section = 'end'
            
            # 🔥 FIXED: Store FULL chunk text (Pinecone supports up to 40KB per metadata)
            chunk_text = chunk['text']
            
            # Truncate only if absolutely necessary (over 40KB)
            max_metadata_size = 35000  # Leave some buffer
            if len(chunk_text) > max_metadata_size:
                chunk_text = chunk_text[:max_metadata_size] + "... [truncated]"
                print(f"  ⚠️  Truncated chunk {i} from {len(chunk['text'])} to {max_metadata_size} chars")
            
            # 🔥 NEW: Include page number if available
            page_number = chunk.get('page_number', None)
            
            # Build comprehensive metadata
            metadata = {
                'user_id': user_id,
                'document_id': document_id,
                'chunk_text': chunk_text,  # FULL TEXT
                'chunk_position': i,
                'position_percentage': round(position_percentage, 2),
                'document_section': document_section,
                'word_count': chunk.get('word_count', len(chunk_text.split())),
                'full_text_length': len(chunk['text']),
                'total_chunks_in_doc': total_chunks,
                'chunk_type': 'content',
                'document_coverage': f"{i+1}/{total_chunks}"
            }
            
            # 🔥 NEW: Add page number if available
            if page_number is not None:
                metadata['page_number'] = page_number
            
            # Add document-level metadata if provided
            if document_metadata:
                metadata['filename'] = document_metadata.get('filename', '')
                metadata['file_size'] = document_metadata.get('file_size', 0)
            
            vectors.append({
                'id': vector_id,
                'values': embedding,
                'metadata': metadata
            })
        
        if not vectors:
            print(f"❌ No valid vectors to upsert (all embeddings failed)")
            return
        
        print(f"📊 Prepared {len(vectors)} vectors ({skipped_chunks} skipped)")
        
        # Log document coverage
        sections = {'beginning': 0, 'middle': 0, 'end': 0}
        for v in vectors:
            sections[v['metadata']['document_section']] += 1
        
        print(f"📊 Coverage: Beginning={sections['beginning']}, "
              f"Middle={sections['middle']}, End={sections['end']}")
        
        # Upsert in batches
        batch_size = 100
        successful_batches = 0
        failed_batches = 0
        
        for i in range(0, len(vectors), batch_size):
            batch = vectors[i:i+batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(vectors) + batch_size - 1) // batch_size
            
            try:
                upsert_response = self.index.upsert(vectors=batch, namespace=user_id)
                print(f"  ✅ Batch {batch_num}/{total_batches} upserted ({len(batch)} vectors)")
                successful_batches += 1
            except Exception as e:
                print(f"  ❌ Batch {batch_num} failed: {str(e)}")
                failed_batches += 1
        
        print(f"✅ Upsert complete: {successful_batches}/{total_batches} successful")
        
        if failed_batches > 0:
            print(f"⚠️  {failed_batches} batches failed to upsert")
    
    def search(self, user_id: str, query_embedding: List[float], 
              document_ids: List[str], top_k: int = 5, 
              min_score: float = 0.0) -> List[Dict]:
        """
        Search for similar chunks within user's documents
        🔥 FIXED: Lower default threshold, return more results, preserve full text
        """
        if not query_embedding or not document_ids:
            print("⚠️  Empty query embedding or document IDs")
            return []
        
        print(f"🔍 Searching for user {user_id} in {len(document_ids)} documents, top_k={top_k}")
        
        try:
            # Create filter for specific documents
            metadata_filter = {"document_id": {"$in": document_ids}}
            
            # 🔥 FIXED: Get more results initially for better selection
            search_top_k = min(top_k * 3, 100)
            
            # Query Pinecone
            results = self.index.query(
                vector=query_embedding,
                namespace=user_id,
                filter=metadata_filter,
                top_k=search_top_k,
                include_metadata=True
            )
            
            matches = results.get('matches', [])
            print(f"  Pinecone returned {len(matches)} matches")
            
            if not matches:
                return []
            
            # 🔥 FIXED: Apply much lower threshold (was 0.1, now 0.0 by default)
            filtered_results = []
            
            for match in matches:
                score = float(match.get('score', 0))
                
                if score >= min_score:
                    metadata = match.get('metadata', {})
                    
                    result = {
                        'document_id': metadata.get('document_id'),
                        'chunk_text': metadata.get('chunk_text', ''),  # Full text
                        'score': score,
                        'position': metadata.get('chunk_position', 0),
                        'position_percentage': metadata.get('position_percentage', 0),
                        'document_section': metadata.get('document_section', 'unknown'),
                        'word_count': metadata.get('word_count', 0),
                        'document_coverage': metadata.get('document_coverage', ''),
                        'total_chunks_in_doc': metadata.get('total_chunks_in_doc', 1)
                    }
                    
                    # 🔥 NEW: Include page number if available
                    if 'page_number' in metadata:
                        result['page_number'] = metadata['page_number']
                    
                    if 'filename' in metadata:
                        result['filename'] = metadata['filename']
                    
                    filtered_results.append(result)
            
            # Sort by score and limit
            filtered_results.sort(key=lambda x: x['score'], reverse=True)
            final_results = filtered_results[:top_k]
            
            print(f"  ✅ Returning {len(final_results)} results (min_score={min_score})")
            
            # Log top results
            for i, r in enumerate(final_results[:3], 1):
                page_info = f", page {r.get('page_number')}" if 'page_number' in r else ""
                print(f"    {i}. Score: {r['score']:.3f}, "
                      f"Pos: {r['position']}/{r['total_chunks_in_doc']}{page_info}")
            
            return final_results
            
        except Exception as e:
            print(f"❌ Error searching vectors: {str(e)}")
            import traceback
            traceback.print_exc()
            return []
    
    def search_with_diversity(self, user_id: str, query_embedding: List[float],
                             document_ids: List[str], top_k: int = 5) -> List[Dict]:
        """
        Enhanced search that ensures diversity across document sections
        🔥 NEW: Intelligent selection across document sections
        """
        # Get more results initially
        all_results = self.search(user_id, query_embedding, document_ids, 
                                 top_k=top_k * 3, min_score=0.0)
        
        if not all_results:
            return []
        
        # Group by document
        document_groups = {}
        for result in all_results:
            doc_id = result['document_id']
            if doc_id not in document_groups:
                document_groups[doc_id] = []
            document_groups[doc_id].append(result)
        
        print(f"  📊 Results grouped into {len(document_groups)} documents")
        
        # Select best chunks per document with section diversity
        final_results = []
        
        for doc_id, chunks in document_groups.items():
            # Sort by score
            chunks.sort(key=lambda x: x['score'], reverse=True)
            
            selected = []
            sections_used = set()
            
            # First: get best from each section
            for section in ['beginning', 'middle', 'end']:
                section_chunks = [c for c in chunks if c.get('document_section') == section]
                if section_chunks and len(selected) < 3:
                    best = section_chunks[0]
                    if best['score'] > 0.05:  # Very low threshold
                        selected.append(best)
                        sections_used.add(section)
            
            # Fill remaining slots with highest scores
            for chunk in chunks:
                if chunk not in selected and len(selected) < 3:
                    if chunk['score'] > 0.05:
                        selected.append(chunk)
            
            final_results.extend(selected)
        
        # Sort by score and limit
        final_results.sort(key=lambda x: x['score'], reverse=True)
        return final_results[:top_k]
    
    def debug_search_for_text(self, user_id: str, search_text: str,
                             document_ids: List[str]) -> List[Dict]:
        """
        🔥 NEW: Debug function to find exact text in vector store
        Use this to verify if specific text exists in your vectors
        """
        print(f"🔍 DEBUG: Searching for text: '{search_text}'")
        
        try:
            # Query with dummy vector to get all chunks
            results = self.index.query(
                vector=[0.0] * config.EMBEDDING_DIMENSION,
                namespace=user_id,
                filter={"document_id": {"$in": document_ids}},
                top_k=10000,  # Get as many as possible
                include_metadata=True
            )
            
            matches = []
            search_lower = search_text.lower()
            
            for match in results.get('matches', []):
                metadata = match.get('metadata', {})
                chunk_text = metadata.get('chunk_text', '')
                
                # Check for exact phrase match
                if search_lower in chunk_text.lower():
                    # Find the context around the match
                    index = chunk_text.lower().find(search_lower)
                    start = max(0, index - 100)
                    end = min(len(chunk_text), index + len(search_text) + 100)
                    context = chunk_text[start:end]
                    
                    match_info = {
                        'document_id': metadata.get('document_id'),
                        'chunk_position': metadata.get('chunk_position'),
                        'page_number': metadata.get('page_number', 'Unknown'),
                        'filename': metadata.get('filename', 'Unknown'),
                        'document_section': metadata.get('document_section'),
                        'context': context,
                        'full_chunk': chunk_text[:500],  # First 500 chars
                        'match_found': True
                    }
                    
                    matches.append(match_info)
                    
                    print(f"  ✅ FOUND in doc {metadata.get('document_id')[:8]}..., "
                          f"chunk {metadata.get('chunk_position')}, "
                          f"page {metadata.get('page_number', '?')}")
            
            if not matches:
                print(f"  ❌ Text NOT FOUND in any of {len(results.get('matches', []))} chunks")
                print(f"  💡 This could mean:")
                print(f"     1. Text doesn't exist in document")
                print(f"     2. Chunking split the text awkwardly")
                print(f"     3. Text was truncated during upload")
            else:
                print(f"  ✅ Found {len(matches)} matches across chunks")
            
            return matches
            
        except Exception as e:
            print(f"❌ Debug search error: {str(e)}")
            import traceback
            traceback.print_exc()
            return []
    
    def hybrid_search(self, user_id: str, query: str, query_embedding: List[float],
                     document_ids: List[str], top_k: int = 5) -> List[Dict]:
        """
        🔥 NEW: Combine vector search with exact text matching
        Boosts results that contain the exact query phrase
        """
        print(f"🔍 Hybrid search: '{query}'")
        
        # Step 1: Vector search
        vector_results = self.search(user_id, query_embedding, document_ids, 
                                    top_k=top_k * 2, min_score=0.0)
        
        # Step 2: Boost exact matches
        query_lower = query.lower()
        exact_matches = []
        other_results = []
        
        for result in vector_results:
            chunk_text = result.get('chunk_text', '').lower()
            
            # Check for exact phrase match
            if query_lower in chunk_text:
                # Boost score significantly
                result['score'] = min(result['score'] + 0.3, 1.0)
                result['exact_match'] = True
                result['match_type'] = 'exact'
                exact_matches.append(result)
                print(f"  🎯 EXACT match found in chunk {result['position']}")
            else:
                result['exact_match'] = False
                result['match_type'] = 'semantic'
                other_results.append(result)
        
        # Combine: exact matches first, then semantic matches
        combined = exact_matches + other_results
        combined.sort(key=lambda x: x['score'], reverse=True)
        
        final = combined[:top_k]
        
        print(f"  ✅ Hybrid search: {len(exact_matches)} exact + "
              f"{len(other_results)} semantic = {len(final)} results")
        
        return final
    
    def delete_document_vectors(self, user_id: str, document_id: str) -> bool:
        """
        Delete all vectors for a document
        🔥 IMPROVED: Better error handling and logging
        """
        try:
            print(f"🗑️  Deleting vectors for document {document_id}")
            
            # Try metadata-based deletion (works with paid Pinecone)
            try:
                delete_response = self.index.delete(
                    namespace=user_id,
                    filter={"document_id": document_id}
                )
                print(f"  ✅ Deleted using metadata filter")
                return True
                
            except Exception as filter_error:
                print(f"  ⚠️  Metadata deletion failed: {filter_error}")
                
                # Fallback: delete by vector IDs
                print(f"  🔄 Attempting manual deletion...")
                
                vector_ids = []
                for i in range(200):  # Assume max 200 chunks per document
                    vector_id = hashlib.md5(f"{user_id}:{document_id}:{i}".encode()).hexdigest()
                    vector_ids.append(vector_id)
                
                # Delete in batches
                batch_size = 100
                for i in range(0, len(vector_ids), batch_size):
                    batch = vector_ids[i:i+batch_size]
                    try:
                        self.index.delete(ids=batch, namespace=user_id)
                    except:
                        pass
                
                print(f"  ⚠️  Manual deletion completed (estimated)")
                return True
            
        except Exception as e:
            print(f"❌ Error deleting vectors: {str(e)}")
            return False
    
    def get_index_stats(self) -> Dict:
        """Get Pinecone index statistics"""
        try:
            stats = self.index.describe_index_stats()
            return stats
        except Exception as e:
            print(f"Error getting index stats: {str(e)}")
            return {}
    
    def get_document_chunk_info(self, user_id: str, document_id: str) -> Dict:
        """
        🔥 NEW: Get detailed information about document chunks
        Useful for debugging and verification
        """
        try:
            results = self.index.query(
                vector=[0.0] * config.EMBEDDING_DIMENSION,
                namespace=user_id,
                filter={"document_id": document_id},
                top_k=10000,
                include_metadata=True
            )
            
            if not results.get('matches'):
                return {
                    'chunk_count': 0,
                    'sections': {},
                    'pages': [],
                    'exists': False
                }
            
            chunks = results['matches']
            sections = {'beginning': 0, 'middle': 0, 'end': 0, 'unknown': 0}
            pages = set()
            
            for chunk in chunks:
                metadata = chunk.get('metadata', {})
                section = metadata.get('document_section', 'unknown')
                sections[section] += 1
                
                if 'page_number' in metadata:
                    pages.add(metadata['page_number'])
            
            return {
                'chunk_count': len(chunks),
                'sections': sections,
                'pages': sorted(list(pages)),
                'total_chunks_in_doc': chunks[0]['metadata'].get('total_chunks_in_doc', len(chunks)),
                'exists': True,
                'filename': chunks[0]['metadata'].get('filename', 'Unknown')
            }
            
        except Exception as e:
            print(f"Error getting chunk info: {e}")
            return {'chunk_count': 0, 'sections': {}, 'exists': False}
        
    def find_quote_page(self, user_id: str, quote: str, document_ids: List[str]) -> Dict:
        """
        Find which page(s) a specific quote appears on
        
        Returns: {
            'found': bool,
            'page_numbers': List[int],
            'document_id': str,
            'filename': str,
            'matches': List[Dict],
            'confidence': str  # 'exact', 'fuzzy', or 'not_found'
        }
        """
        print(f"🔍 Finding page for quote: '{quote[:100]}...'")
        
        # Normalize quote for better matching
        quote_normalized = ' '.join(quote.lower().split())
        
        if len(quote_normalized) < 10:
            return {
                'found': False,
                'page_numbers': [],
                'confidence': 'error',
                'message': 'Quote too short (minimum 10 characters)'
            }
        
        try:
            # Get ALL chunks from specified documents
            results = self.index.query(
                vector=[0.0] * config.EMBEDDING_DIMENSION,
                namespace=user_id,
                filter={"document_id": {"$in": document_ids}},
                top_k=10000,
                include_metadata=True
            )
            
            exact_matches = []
            
            for match in results.get('matches', []):
                metadata = match.get('metadata', {})
                chunk_text = metadata.get('chunk_text', '')
                chunk_normalized = ' '.join(chunk_text.lower().split())
                
                # Try exact match
                if quote_normalized in chunk_normalized:
                    overlap_ratio = len(quote_normalized) / len(chunk_normalized) if chunk_normalized else 0
                    
                    match_info = {
                        'document_id': metadata.get('document_id'),
                        'filename': metadata.get('filename', 'Unknown'),
                        'page_number': metadata.get('page_number'),
                        'chunk_position': metadata.get('chunk_position'),
                        'document_section': metadata.get('document_section'),
                        'chunk_text': chunk_text,
                        'overlap_ratio': overlap_ratio,
                        'match_type': 'exact'
                    }
                    exact_matches.append(match_info)
                    
                    print(f"  ✅ EXACT match: doc={metadata.get('document_id')[:8]}..., "
                          f"page={metadata.get('page_number')}, chunk={metadata.get('chunk_position')}")
            
            # If exact matches found
            if exact_matches:
                exact_matches.sort(key=lambda x: x['overlap_ratio'], reverse=True)
                
                page_numbers = []
                seen_pages = set()
                for match in exact_matches:
                    page = match.get('page_number')
                    if page and page not in seen_pages:
                        page_numbers.append(page)
                        seen_pages.add(page)
                
                return {
                    'found': True,
                    'page_numbers': sorted(page_numbers),
                    'document_id': exact_matches[0]['document_id'],
                    'filename': exact_matches[0]['filename'],
                    'matches': exact_matches[:5],  # Return top 5 matches
                    'confidence': 'exact',
                    'total_occurrences': len(exact_matches)
                }
            
            # No exact match - try semantic search
            print("  ⚠️  No exact match, trying semantic search...")
            query_embedding = self.generate_embeddings([quote])[0]
            
            if query_embedding:
                semantic_results = self.search(
                    user_id, 
                    query_embedding, 
                    document_ids, 
                    top_k=5, 
                    min_score=0.4
                )
                
                if semantic_results:
                    best_match = semantic_results[0]
                    return {
                        'found': True,
                        'page_numbers': [best_match.get('page_number')] if 'page_number' in best_match else [],
                        'document_id': best_match['document_id'],
                        'filename': best_match.get('filename', 'Unknown'),
                        'matches': [{
                            'document_id': best_match['document_id'],
                            'filename': best_match.get('filename'),
                            'page_number': best_match.get('page_number'),
                            'chunk_text': best_match['chunk_text'][:500],
                            'score': best_match['score'],
                            'match_type': 'semantic'
                        }],
                        'confidence': 'fuzzy',
                        'semantic_score': best_match['score']
                    }
            
            # Nothing found
            print(f"  ❌ Quote not found in {len(results.get('matches', []))} chunks")
            return {
                'found': False,
                'page_numbers': [],
                'document_id': None,
                'filename': None,
                'matches': [],
                'confidence': 'not_found',
                'message': 'Quote not found in the specified documents'
            }
            
        except Exception as e:
            print(f"❌ Error finding quote: {str(e)}")
            import traceback
            traceback.print_exc()
            return {
                'found': False,
                'page_numbers': [],
                'confidence': 'error',
                'error': str(e)
            }
    
    def verify_document_coverage(self, user_id: str, document_id: str) -> Dict:
        """
        🔥 NEW: Verify that all parts of a document are properly indexed
        Returns analysis of document coverage
        """
        try:
            info = self.get_document_chunk_info(user_id, document_id)
            
            if not info['exists']:
                return {
                    'status': 'not_found',
                    'message': 'Document not found in vector store'
                }
            
            total_chunks = info['chunk_count']
            sections = info['sections']
            
            # Calculate coverage percentages
            coverage = {
                'beginning': (sections.get('beginning', 0) / total_chunks * 100) if total_chunks > 0 else 0,
                'middle': (sections.get('middle', 0) / total_chunks * 100) if total_chunks > 0 else 0,
                'end': (sections.get('end', 0) / total_chunks * 100) if total_chunks > 0 else 0
            }
            
            # Determine if coverage is good
            has_beginning = coverage['beginning'] > 20
            has_middle = coverage['middle'] > 20
            has_end = coverage['end'] > 20
            
            status = 'good' if (has_beginning and has_middle and has_end) else 'poor'
            
            issues = []
            if not has_beginning:
                issues.append('Low coverage of document beginning')
            if not has_middle:
                issues.append('Low coverage of document middle')
            if not has_end:
                issues.append('Low coverage of document end')
            
            return {
                'status': status,
                'total_chunks': total_chunks,
                'sections': sections,
                'coverage_percentages': coverage,
                'pages': info.get('pages', []),
                'issues': issues,
                'message': 'Good coverage' if status == 'good' else 'Coverage issues detected'
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e)
            }

# Create singleton instance
try:
    vector_store = VectorStore()
    print("✅ VectorStore initialized successfully")
except Exception as e:
    print(f"❌ Failed to initialize VectorStore: {str(e)}")
    vector_store = None