# import pinecone
# from typing import List, Dict, Tuple
# import hashlib
# import requests
# from .config import config

# class VectorStore:
#     def __init__(self):
#         # Initialize Pinecone
#         pinecone.init(
#             api_key=config.PINECONE_API_KEY,
#             environment=config.PINECONE_ENV
#         )
#         self.index = pinecone.Index(config.PINECONE_INDEX)
        
#         # Create index if it doesn't exist
#         if config.PINECONE_INDEX not in pinecone.list_indexes():
#             pinecone.create_index(
#                 config.PINECONE_INDEX,
#                 dimension=config.EMBEDDING_DIMENSION,
#                 metric='cosine'
#             )
    
#     def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
#         """Generate embeddings using Hugging Face Inference API"""
#         headers = {"Authorization": f"Bearer {config.HF_API_TOKEN}"}
        
#         response = requests.post(
#             f"https://api-inference.huggingface.co/models/{config.EMBEDDING_MODEL}",
#             headers=headers,
#             json={"inputs": texts, "options": {"wait_for_model": True}}
#         )
        
#         if response.status_code != 200:
#             raise Exception(f"Embedding API error: {response.text}")
        
#         return response.json()
    
#     def upsert_chunks(self, user_id: str, document_id: str, 
#                      chunks: List[Dict[str, str]], embeddings: List[List[float]]):
#         """Store document chunks with embeddings"""
#         vectors = []
        
#         for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
#             vector_id = hashlib.md5(
#                 f"{user_id}:{document_id}:{i}".encode()
#             ).hexdigest()
            
#             vectors.append({
#                 'id': vector_id,
#                 'values': embedding,
#                 'metadata': {
#                     'user_id': user_id,
#                     'document_id': document_id,
#                     'chunk_text': chunk['text'],
#                     'chunk_position': i,
#                     's3_key': chunk.get('s3_key', '')
#                 }
#             })
        
#         # Upsert in batches of 100
#         for i in range(0, len(vectors), 100):
#             batch = vectors[i:i+100]
#             self.index.upsert(vectors=batch, namespace=user_id)
    
#     def search(self, user_id: str, query_embedding: List[float], 
#               document_ids: List[str], top_k: int = 5) -> List[Dict]:
#         """Search for similar chunks within user's documents"""
#         # Create filter for specific documents
#         metadata_filter = {
#             "document_id": {"$in": document_ids}
#         }
        
#         results = self.index.query(
#             vector=query_embedding,
#             namespace=user_id,
#             filter=metadata_filter,
#             top_k=top_k,
#             include_metadata=True
#         )
        
#         return [
#             {
#                 'document_id': match['metadata']['document_id'],
#                 'chunk_text': match['metadata']['chunk_text'],
#                 'score': match['score'],
#                 'position': match['metadata']['chunk_position']
#             }
#             for match in results['matches']
#         ]
    
#     def delete_document_vectors(self, user_id: str, document_id: str):
#         """Delete all vectors for a document"""

#         pass

# vector_store = VectorStore()