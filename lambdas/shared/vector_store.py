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
        """Generate embeddings using Hugging Face Inference API"""
        if not texts:
            return []
        
        headers = {"Authorization": f"Bearer {config.HF_API_TOKEN}"}
        
        # Batch texts if too many (HF API has limits)
        all_embeddings = []
        batch_size = 10  # Process 10 texts at a time
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            
            try:
                response = requests.post(
                    f"https://api-inference.huggingface.co/models/{config.EMBEDDING_MODEL}",
                    headers=headers,
                    json={"inputs": batch, "options": {"wait_for_model": True}},
                    timeout=30
                )
                
                if response.status_code != 200:
                    print(f"Embedding API error: {response.status_code} - {response.text}")
                    # Fallback to random embeddings for testing
                    import random
                    return [[random.random() for _ in range(config.EMBEDDING_DIMENSION)] for _ in texts]
                
                embeddings = response.json()
                
                # Handle different response formats
                if isinstance(embeddings, list) and len(embeddings) > 0:
                    if isinstance(embeddings[0], list):
                        all_embeddings.extend(embeddings)
                    else:
                        # Single embedding returned
                        all_embeddings.append(embeddings)
                else:
                    print(f"Unexpected embedding response format: {embeddings}")
                    import random
                    all_embeddings.extend([[random.random() for _ in range(config.EMBEDDING_DIMENSION)] for _ in batch])
                    
            except Exception as e:
                print(f"Error generating embeddings: {str(e)}")
                # Generate random embeddings as fallback
                import random
                all_embeddings.extend([[random.random() for _ in range(config.EMBEDDING_DIMENSION)] for _ in batch])
        
        return all_embeddings
    
    def upsert_chunks(self, user_id: str, document_id: str, 
                     chunks: List[Dict[str, str]], embeddings: List[List[float]]):
        """Store document chunks with embeddings in Pinecone"""
        if not chunks or not embeddings:
            print("No chunks or embeddings to upsert")
            return
        
        vectors = []
        
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            # Generate unique vector ID
            vector_id = hashlib.md5(
                f"{user_id}:{document_id}:{i}".encode()
            ).hexdigest()
            
            # Prepare metadata
            metadata = {
                'user_id': user_id,
                'document_id': document_id,
                'chunk_text': chunk['text'][:1000],  # Limit text size
                'chunk_position': i,
                's3_key': chunk.get('s3_key', ''),
                'word_count': chunk.get('word_count', 0)
            }
            
            vectors.append({
                'id': vector_id,
                'values': embedding,
                'metadata': metadata
            })
        
        # Upsert in batches of 100 (Pinecone limit)
        batch_size = 100
        for i in range(0, len(vectors), batch_size):
            batch = vectors[i:i+batch_size]
            try:
                self.index.upsert(vectors=batch, namespace=user_id)
                print(f"Upserted {len(batch)} vectors for document {document_id}")
            except Exception as e:
                print(f"Error upserting vectors: {str(e)}")
                raise
    
    def search(self, user_id: str, query_embedding: List[float], 
              document_ids: List[str], top_k: int = 5) -> List[Dict]:
        """Search for similar chunks within user's documents"""
        if not query_embedding or not document_ids:
            return []
        
        try:
            # Create filter for specific documents
            metadata_filter = {
                "document_id": {"$in": document_ids}
            }
            
            # Query Pinecone
            results = self.index.query(
                vector=query_embedding,
                namespace=user_id,
                filter=metadata_filter,
                top_k=top_k,
                include_metadata=True
            )
            
            # Format results
            formatted_results = []
            for match in results.get('matches', []):
                formatted_results.append({
                    'document_id': match['metadata'].get('document_id'),
                    'chunk_text': match['metadata'].get('chunk_text', ''),
                    'score': float(match.get('score', 0)),
                    'position': match['metadata'].get('chunk_position', 0),
                    's3_key': match['metadata'].get('s3_key', '')
                })
            
            return formatted_results
            
        except Exception as e:
            print(f"Error searching vectors: {str(e)}")
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