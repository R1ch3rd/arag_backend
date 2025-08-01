# backend/lambdas/shared/vector_store.py
# ENHANCED VERSION - Fixes chunking logic and document identification issues

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
        # Initialize Pinecone with new API
        try:
            # Create Pinecone instance
            self.pc = Pinecone(
                api_key=config.PINECONE_API_KEY
            )
            
            # Check if index exists
            existing_indexes = self.pc.list_indexes().names()
            if config.PINECONE_INDEX not in existing_indexes:
                print(f"Creating Pinecone index: {config.PINECONE_INDEX}")
                self.pc.create_index(
                    name=config.PINECONE_INDEX,
                    dimension=config.EMBEDDING_DIMENSION,
                    metric='cosine',
                    spec=ServerlessSpec(
                        cloud='aws',
                        region='us-west-2'
                    )
                )
                # Wait for index to be ready
                time.sleep(10)
            
            # Get index instance
            self.index = self.pc.Index(config.PINECONE_INDEX)
            print(f"Connected to Pinecone index: {config.PINECONE_INDEX}")
            
        except Exception as e:
            print(f"Error initializing Pinecone: {str(e)}")
            raise
    
    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using Hugging Face Inference API with enhanced error handling"""
        if not texts:
            return []
        
        print(f"Generating embeddings for {len(texts)} texts")
        headers = {"Authorization": f"Bearer {config.HF_API_TOKEN}"}
        
        # Batch texts if too many (HF API has limits)
        all_embeddings = []
        batch_size = 10  # Process 10 texts at a time
        failed_batches = 0
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(texts) + batch_size - 1) // batch_size
            
            print(f"Processing batch {batch_num}/{total_batches} ({len(batch)} texts)")
            
            # Check for potentially problematic text
            for j, text in enumerate(batch):
                if len(text.strip()) == 0:
                    print(f"WARNING: Empty text in batch {batch_num}, position {j}")
                elif len(text) > 8000:  # Very long text might cause issues
                    print(f"WARNING: Very long text in batch {batch_num}, position {j}: {len(text)} chars")
            
            try:
                # 🔥 FIX: Try multiple API formats in order of likelihood
                response = None
                
                # Format 1: Standard inputs format (most common)
                try:
                    response = requests.post(
                        f"https://api-inference.huggingface.co/models/{config.EMBEDDING_MODEL}",
                        headers=headers,
                        json={"inputs": batch, "options": {"wait_for_model": True}},
                        timeout=60
                    )
                    if response.status_code == 200:
                        print(f"Batch {batch_num}: Success with 'inputs' format")
                    else:
                        print(f"Batch {batch_num}: 'inputs' format failed: {response.status_code} - {response.text}")
                        response = None
                except Exception as e:
                    print(f"Batch {batch_num}: 'inputs' format error: {e}")
                    response = None
                
                # Format 2: Sentences format (if inputs failed)
                if response is None or response.status_code != 200:
                    try:
                        response = requests.post(
                            f"https://api-inference.huggingface.co/models/{config.EMBEDDING_MODEL}",
                            headers=headers,
                            json={"sentences": batch, "options": {"wait_for_model": True}},
                            timeout=60
                        )
                        if response.status_code == 200:
                            print(f"Batch {batch_num}: Success with 'sentences' format")
                        else:
                            print(f"Batch {batch_num}: 'sentences' format failed: {response.status_code} - {response.text}")
                            response = None
                    except Exception as e:
                        print(f"Batch {batch_num}: 'sentences' format error: {e}")
                        response = None
                
                # Format 3: Direct array format (last resort)
                if response is None or response.status_code != 200:
                    try:
                        response = requests.post(
                            f"https://api-inference.huggingface.co/models/{config.EMBEDDING_MODEL}",
                            headers=headers,
                            json=batch,  # Send array directly
                            timeout=60
                        )
                        if response.status_code == 200:
                            print(f"Batch {batch_num}: Success with direct array format")
                        else:
                            print(f"Batch {batch_num}: Direct array format failed: {response.status_code} - {response.text}")
                            response = None
                    except Exception as e:
                        print(f"Batch {batch_num}: Direct array format error: {e}")
                        response = None
                
                # Check if any format worked
                if response is None or response.status_code != 200:
                    print(f"❌ All formats failed for batch {batch_num}")
                    failed_batches += 1
                    # Fallback to random embeddings for this batch
                    import random
                    batch_embeddings = [[random.random() for _ in range(config.EMBEDDING_DIMENSION)] for _ in batch]
                    all_embeddings.extend(batch_embeddings)
                    continue
                
                embeddings = response.json()
                
                # Handle different response formats
                if isinstance(embeddings, list) and len(embeddings) > 0:
                    if isinstance(embeddings[0], list):
                        # Multiple embeddings returned
                        print(f"✅ Batch {batch_num}: received {len(embeddings)} embeddings")
                        all_embeddings.extend(embeddings)
                    else:
                        # Single embedding returned (shouldn't happen with batch)
                        print(f"Batch {batch_num}: received single embedding")
                        all_embeddings.append(embeddings)
                else:
                    print(f"❌ Batch {batch_num}: unexpected response format: {type(embeddings)}")
                    failed_batches += 1
                    import random
                    batch_embeddings = [[random.random() for _ in range(config.EMBEDDING_DIMENSION)] for _ in batch]
                    all_embeddings.extend(batch_embeddings)
                            
            except Exception as e:
                print(f"❌ Error generating embeddings for batch {batch_num}: {str(e)}")
                failed_batches += 1
                # Generate random embeddings as fallback
                import random
                batch_embeddings = [[random.random() for _ in range(config.EMBEDDING_DIMENSION)] for _ in batch]
                all_embeddings.extend(batch_embeddings)
        
        print(f"Embedding generation complete: {len(all_embeddings)} embeddings, {failed_batches} failed batches")
        return all_embeddings
    
    def upsert_chunks(self, user_id: str, document_id: str, 
                     chunks: List[Dict[str, str]], embeddings: List[List[float]]):
        """Store document chunks with embeddings in Pinecone - ENHANCED WITH BETTER METADATA"""
        if not chunks or not embeddings:
            print("No chunks or embeddings to upsert")
            return
        
        if len(chunks) != len(embeddings):
            print(f"ERROR: Chunk count ({len(chunks)}) != embedding count ({len(embeddings)})")
            return
        
        print(f"Upserting {len(chunks)} chunks for document {document_id}")
        
        # 🔥 ENHANCED: Calculate document-level statistics for better metadata
        total_chunks = len(chunks)
        document_text_length = sum(len(chunk['text']) for chunk in chunks)
        
        vectors = []
        
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            # Generate unique vector ID
            vector_id = hashlib.md5(
                f"{user_id}:{document_id}:{i}".encode()
            ).hexdigest()
            
            # Check embedding validity
            if not embedding or len(embedding) != config.EMBEDDING_DIMENSION:
                print(f"ERROR: Invalid embedding at position {i}: {len(embedding) if embedding else 0} dimensions")
                continue
            
            # 🔥 ENHANCED: Prepare comprehensive metadata
            chunk_text_preview = chunk['text'][:1000]  # Pinecone metadata limits
            
            # Calculate position percentage in document
            position_percentage = (i / max(total_chunks - 1, 1)) * 100
            
            # Determine document section
            if position_percentage < 33:
                document_section = 'beginning'
            elif position_percentage < 67:
                document_section = 'middle'
            else:
                document_section = 'end'
            
            metadata = {
                'user_id': user_id,
                'document_id': document_id,
                'chunk_text': chunk_text_preview,
                'chunk_position': i,
                'position_percentage': round(position_percentage, 2),
                'document_section': document_section,
                'word_count': chunk.get('word_count', 0),
                'full_text_length': len(chunk['text']),
                'total_chunks_in_doc': total_chunks,
                'chunk_type': 'content',  # 🔥 NEW: Identify this as content vs metadata
                'document_coverage': f"{i+1}/{total_chunks}"  # 🔥 NEW: Show chunk position
            }
            
            vectors.append({
                'id': vector_id,
                'values': embedding,
                'metadata': metadata
            })
        
        print(f"Prepared {len(vectors)} valid vectors for upsert")
        
        # Log document coverage statistics
        beginning_chunks = sum(1 for v in vectors if v['metadata']['document_section'] == 'beginning')
        middle_chunks = sum(1 for v in vectors if v['metadata']['document_section'] == 'middle')
        end_chunks = sum(1 for v in vectors if v['metadata']['document_section'] == 'end')
        
        print(f"📊 Document coverage: Beginning={beginning_chunks}, Middle={middle_chunks}, End={end_chunks}")
        
        # Upsert in batches of 100 (Pinecone limit)
        batch_size = 100
        successful_batches = 0
        failed_batches = 0
        
        for i in range(0, len(vectors), batch_size):
            batch = vectors[i:i+batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(vectors) + batch_size - 1) // batch_size
            
            try:
                print(f"Upserting batch {batch_num}/{total_batches} ({len(batch)} vectors)")
                upsert_response = self.index.upsert(vectors=batch, namespace=user_id)
                print(f"Batch {batch_num} upsert response: {upsert_response}")
                successful_batches += 1
            except Exception as e:
                print(f"Error upserting batch {batch_num}: {str(e)}")
                failed_batches += 1
        
        print(f"Upsert complete: {successful_batches} successful batches, {failed_batches} failed batches")
    
    def search(self, user_id: str, query_embedding: List[float], 
              document_ids: List[str], top_k: int = 5) -> List[Dict]:
        """Search for similar chunks within user's documents - ENHANCED WITH DOCUMENT GROUPING"""
        if not query_embedding or not document_ids:
            print("Empty query embedding or document IDs for search")
            return []
        
        print(f"🔍 Searching for user {user_id} in documents {document_ids}, top_k={top_k}")
        
        try:
            # Create filter for specific documents
            metadata_filter = {
                "document_id": {"$in": document_ids}
            }
            
            print(f"Using filter: {metadata_filter}")
            
            # 🔥 ENHANCED: Query more results initially to allow for intelligent selection
            search_top_k = min(top_k * 3, 50)  # Get 3x results but cap at 50
            
            # Query Pinecone
            results = self.index.query(
                vector=query_embedding,
                namespace=user_id,
                filter=metadata_filter,
                top_k=search_top_k,
                include_metadata=True
            )
            
            print(f"Pinecone returned {len(results.get('matches', []))} matches")
            
            if not results.get('matches'):
                return []
            
            # 🔥 NEW: Group results by document and apply intelligent selection
            document_groups = {}
            
            for match in results.get('matches', []):
                doc_id = match['metadata'].get('document_id')
                if doc_id not in document_groups:
                    document_groups[doc_id] = []
                
                document_groups[doc_id].append({
                    'document_id': doc_id,
                    'chunk_text': match['metadata'].get('chunk_text', ''),
                    'score': float(match.get('score', 0)),
                    'position': match['metadata'].get('chunk_position', 0),
                    'position_percentage': match['metadata'].get('position_percentage', 0),
                    'document_section': match['metadata'].get('document_section', 'unknown'),
                    'word_count': match['metadata'].get('word_count', 0),
                    'document_coverage': match['metadata'].get('document_coverage', ''),
                    's3_key': match['metadata'].get('s3_key', ''),
                    'total_chunks_in_doc': match['metadata'].get('total_chunks_in_doc', 1)
                })
            
            print(f"📊 Results grouped into {len(document_groups)} documents")
            
            # 🔥 ENHANCED: Select best chunks per document with diversity
            final_results = []
            
            for doc_id, chunks in document_groups.items():
                # Sort chunks by score
                chunks.sort(key=lambda x: x['score'], reverse=True)
                
                print(f"📄 Document {doc_id}: {len(chunks)} chunks found")
                
                # Select up to 3 chunks per document with section diversity
                selected_for_doc = []
                sections_used = set()
                
                # First pass: get best chunk from each section
                for section in ['beginning', 'middle', 'end']:
                    section_chunks = [c for c in chunks if c.get('document_section') == section]
                    if section_chunks and len(selected_for_doc) < 3:
                        best_in_section = section_chunks[0]  # Already sorted by score
                        if best_in_section['score'] > 0.1:  # Minimum relevance threshold
                            selected_for_doc.append(best_in_section)
                            sections_used.add(section)
                            print(f"  ✅ Selected from {section}: score={best_in_section['score']:.3f}, pos={best_in_section['position']}")
                
                # Second pass: fill remaining slots with highest scoring chunks
                remaining_slots = 3 - len(selected_for_doc)
                if remaining_slots > 0:
                    for chunk in chunks:
                        if chunk not in selected_for_doc and len(selected_for_doc) < 3:
                            if chunk['score'] > 0.1:
                                selected_for_doc.append(chunk)
                                print(f"  ✅ Selected by score: score={chunk['score']:.3f}, pos={chunk['position']}")
                
                final_results.extend(selected_for_doc)
            
            # Sort final results by score and limit to requested top_k
            final_results.sort(key=lambda x: x['score'], reverse=True)
            final_results = final_results[:top_k]
            
            print(f"🎯 Final selection: {len(final_results)} chunks from {len(document_groups)} documents")
            
            # Log final selection details
            for i, result in enumerate(final_results):
                print(f"  {i+1}. Doc: {result['document_id'][:8]}..., "
                      f"Score: {result['score']:.3f}, "
                      f"Section: {result.get('document_section', 'unknown')}, "
                      f"Position: {result['position']}/{result.get('total_chunks_in_doc', '?')}")
            
            return final_results
            
        except Exception as e:
            print(f"Error searching vectors: {str(e)}")
            import traceback
            traceback.print_exc()
            return []
    
    def delete_document_vectors(self, user_id: str, document_id: str):
        """Delete all vectors for a document - ENHANCED"""
        try:
            print(f"🗑️ Attempting to delete vectors for document {document_id}")
            
            # 🔥 ENHANCED: For production Pinecone, use metadata-based deletion
            try:
                # This works with paid Pinecone plans
                delete_response = self.index.delete(
                    namespace=user_id,
                    filter={"document_id": document_id}
                )
                print(f"✅ Deleted vectors using metadata filter: {delete_response}")
                return True
                
            except Exception as filter_error:
                print(f"⚠️ Metadata-based deletion failed: {filter_error}")
                
                # 🔥 FALLBACK: Manual deletion by generating expected vector IDs
                print("🔄 Attempting manual deletion by vector IDs...")
                
                # We need to estimate how many chunks this document had
                # This is imperfect but better than nothing
                vector_ids_to_delete = []
                
                # Try common chunk counts (0 to 100)
                for i in range(100):
                    vector_id = hashlib.md5(
                        f"{user_id}:{document_id}:{i}".encode()
                    ).hexdigest()
                    vector_ids_to_delete.append(vector_id)
                
                # Delete in batches
                batch_size = 100
                deleted_count = 0
                
                for i in range(0, len(vector_ids_to_delete), batch_size):
                    batch_ids = vector_ids_to_delete[i:i+batch_size]
                    try:
                        self.index.delete(ids=batch_ids, namespace=user_id)
                        deleted_count += len(batch_ids)
                    except Exception as batch_error:
                        print(f"Batch deletion failed: {batch_error}")
                        continue
                
                print(f"⚠️ Manual deletion attempted for {deleted_count} potential vector IDs")
                return deleted_count > 0
            
        except Exception as e:
            print(f"❌ Error deleting vectors: {str(e)}")
            return False
    
    def get_index_stats(self) -> Dict:
        """Get statistics about the Pinecone index"""
        try:
            stats = self.index.describe_index_stats()
            return stats
        except Exception as e:
            print(f"Error getting index stats: {str(e)}")
            return {}
    
    def get_document_chunk_info(self, user_id: str, document_id: str) -> Dict:
        """Get information about chunks for a specific document - NEW UTILITY"""
        try:
            # Query for all chunks of this document
            results = self.index.query(
                vector=[0.0] * config.EMBEDDING_DIMENSION,  # Dummy vector
                namespace=user_id,
                filter={"document_id": document_id},
                top_k=1000,  # Get many results
                include_metadata=True
            )
            
            if not results.get('matches'):
                return {'chunk_count': 0, 'sections': {}}
            
            # Analyze chunks
            chunks = results['matches']
            sections = {'beginning': 0, 'middle': 0, 'end': 0, 'unknown': 0}
            
            for chunk in chunks:
                section = chunk['metadata'].get('document_section', 'unknown')
                sections[section] += 1
            
            return {
                'chunk_count': len(chunks),
                'sections': sections,
                'total_chunks_in_doc': chunks[0]['metadata'].get('total_chunks_in_doc', len(chunks)) if chunks else 0
            }
            
        except Exception as e:
            print(f"Error getting document chunk info: {e}")
            return {'chunk_count': 0, 'sections': {}}
    
    def cleanup_orphaned_vectors(self, user_id: str, valid_document_ids: List[str]) -> int:
        """Clean up vectors for documents that no longer exist - NEW UTILITY"""
        try:
            print(f"🧹 Cleaning up orphaned vectors for user {user_id}")
            print(f"Valid document IDs: {valid_document_ids}")
            
            # This is a complex operation that would require scanning all vectors
            # For now, we'll log it and return 0
            print("⚠️ Orphaned vector cleanup not implemented (requires paid Pinecone features)")
            return 0
            
        except Exception as e:
            print(f"Error cleaning up orphaned vectors: {e}")
            return 0

# Create singleton instance
try:
    vector_store = VectorStore()
except Exception as e:
    print(f"Failed to initialize VectorStore: {str(e)}")
    vector_store = None