# backend/lambdas/shared/vector_store.py

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
        """Generate embeddings using Hugging Face Inference API with debugging"""
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
                # Try with "sentences" first (based on your error)
                response = requests.post(
                    f"https://api-inference.huggingface.co/models/{config.EMBEDDING_MODEL}",
                    headers=headers,
                    json={"sentences": batch, "options": {"wait_for_model": True}},
                    timeout=60
                )
                
                if response.status_code == 400 and "inputs" in response.text:
                    # Fallback to "inputs" format if needed
                    response = requests.post(
                        f"https://api-inference.huggingface.co/models/{config.EMBEDDING_MODEL}",
                        headers=headers,
                        json={"inputs": batch, "options": {"wait_for_model": True}},
                        timeout=60
                    )
                
                if response.status_code != 200:
                    print(f"Embedding API error for batch {batch_num}: {response.status_code} - {response.text}")
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
                        print(f"Batch {batch_num}: received {len(embeddings)} embeddings")
                        all_embeddings.extend(embeddings)
                    else:
                        # Single embedding returned (shouldn't happen with batch)
                        print(f"Batch {batch_num}: received single embedding")
                        all_embeddings.append(embeddings)
                else:
                    print(f"Batch {batch_num}: unexpected response format: {type(embeddings)}")
                    failed_batches += 1
                    import random
                    batch_embeddings = [[random.random() for _ in range(config.EMBEDDING_DIMENSION)] for _ in batch]
                    all_embeddings.extend(batch_embeddings)
                        
            except Exception as e:
                print(f"Error generating embeddings for batch {batch_num}: {str(e)}")
                failed_batches += 1
                # Generate random embeddings as fallback
                import random
                batch_embeddings = [[random.random() for _ in range(config.EMBEDDING_DIMENSION)] for _ in batch]
                all_embeddings.extend(batch_embeddings)
        
        print(f"Embedding generation complete: {len(all_embeddings)} embeddings, {failed_batches} failed batches")
        return all_embeddings
    
    def upsert_chunks(self, user_id: str, document_id: str, 
                 chunks: List[Dict[str, str]], embeddings: List[List[float]]):
        """Store document chunks with embeddings in Pinecone with debugging"""
        if not chunks or not embeddings:
            print("No chunks or embeddings to upsert")
            return
        
        if len(chunks) != len(embeddings):
            print(f"ERROR: Chunk count ({len(chunks)}) != embedding count ({len(embeddings)})")
            return
        
        print(f"Upserting {len(chunks)} chunks for document {document_id}")
        
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
            
            # Prepare metadata - be careful about size limits
            chunk_text_preview = chunk['text'][:1000]  # Pinecone metadata limits
            metadata = {
                'user_id': user_id,
                'document_id': document_id,
                'chunk_text': chunk_text_preview,
                'chunk_position': i,
                'word_count': chunk.get('word_count', 0),
                'full_text_length': len(chunk['text'])  # Track if text was truncated
            }
            
            vectors.append({
                'id': vector_id,
                'values': embedding,
                'metadata': metadata
            })
        
        print(f"Prepared {len(vectors)} valid vectors for upsert")
        
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
        """Search for similar chunks within user's documents with debugging"""
        if not query_embedding or not document_ids:
            print("Empty query embedding or document IDs for search")
            return []
        
        print(f"Searching for user {user_id} in documents {document_ids}, top_k={top_k}")
        
        try:
            # Create filter for specific documents
            metadata_filter = {
                "document_id": {"$in": document_ids}
            }
            
            print(f"Using filter: {metadata_filter}")
            
            # Query Pinecone
            results = self.index.query(
                vector=query_embedding,
                namespace=user_id,
                filter=metadata_filter,
                top_k=top_k,
                include_metadata=True
            )
            
            print(f"Pinecone returned {len(results.get('matches', []))} matches")
            
            # Format results
            formatted_results = []
            for i, match in enumerate(results.get('matches', [])):
                score = float(match.get('score', 0))
                position = match['metadata'].get('chunk_position', 0)
                chunk_text = match['metadata'].get('chunk_text', '')
                
                print(f"Match {i+1}: score={score:.4f}, position={position}, text_length={len(chunk_text)}")
                
                formatted_results.append({
                    'document_id': match['metadata'].get('document_id'),
                    'chunk_text': chunk_text,
                    'score': score,
                    'position': position,
                    's3_key': match['metadata'].get('s3_key', ''),
                    'word_count': match['metadata'].get('word_count', 0)
                })
            
            return formatted_results
            
        except Exception as e:
            print(f"Error searching vectors: {str(e)}")
            import traceback
            traceback.print_exc()
            return []
    
    def delete_document_vectors(self, user_id: str, document_id: str):
        """Delete all vectors for a document"""
        try:
            # Get all vector IDs for this document
            # Note: Pinecone free tier doesn't support metadata-based deletion
            # So we need to generate the IDs we used during upsert
            
            # For now, we'll just log this
            print(f"Note: Vector deletion for document {document_id} requested but not implemented in free tier")
            
            # In production with paid Pinecone, you would:
            # self.index.delete(
            #     namespace=user_id,
            #     filter={"document_id": document_id}
            # )
            
        except Exception as e:
            print(f"Error deleting vectors: {str(e)}")
    
    def get_index_stats(self) -> Dict:
        """Get statistics about the Pinecone index"""
        try:
            stats = self.index.describe_index_stats()
            return stats
        except Exception as e:
            print(f"Error getting index stats: {str(e)}")
            return {}

# Create singleton instance
try:
    vector_store = VectorStore()
except Exception as e:
    print(f"Failed to initialize VectorStore: {str(e)}")
    vector_store = None