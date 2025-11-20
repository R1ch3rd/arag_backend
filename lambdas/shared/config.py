import os
from typing import Optional

class Config:
    # AWS
    AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
    
    # DynamoDB
    USERS_TABLE = os.environ.get('USERS_TABLE', 'rag-users')
    DOCUMENTS_TABLE = os.environ.get('DOCUMENTS_TABLE', 'rag-documents')
    SESSIONS_TABLE = os.environ.get('SESSIONS_TABLE', 'rag-sessions')
    MESSAGES_TABLE = os.environ.get('MESSAGES_TABLE', 'rag-messages')
    
    # S3
    DOCUMENTS_BUCKET = os.environ.get('DOCUMENTS_BUCKET', 'rag-user-documents')
    
    # Pinecone
    PINECONE_API_KEY = os.environ.get('PINECONE_API_KEY')
    PINECONE_ENV = os.environ.get('PINECONE_ENV', 'gcp-starter')
    PINECONE_INDEX = os.environ.get('PINECONE_INDEX', 'rag-documents')
    
    # Upstash Redis
    UPSTASH_REDIS_URL = os.environ.get('UPSTASH_REDIS_REST_URL')
    UPSTASH_REDIS_TOKEN = os.environ.get('UPSTASH_REDIS_REST_TOKEN')
    
    # Hugging Face (for embeddings)
    HF_API_TOKEN = os.environ.get('HF_API_TOKEN')
    EMBEDDING_MODEL = 'text-embedding-004'
    EMBEDDING_DIMENSION = 768
    
    # LLM (Together AI or Groq)
    LLM_PROVIDER = os.environ.get('LLM_PROVIDER', 'together')  # 'together' or 'groq'
    TOGETHER_API_KEY = os.environ.get('TOGETHER_API_KEY')
    # GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
    # RAG Config
    CHUNK_SIZE = 300
    CHUNK_OVERLAP = 75
    TOP_K_RESULTS = 20
    MAX_TOKENS = 1000
    
    # Rate Limiting
    UPLOAD_RATE_LIMIT = 10  # per hour
    CHAT_RATE_LIMIT = 100   # per hour
    
    # LLM Model
    LLM_MODEL = os.environ.get('LLM_MODEL', 'meta-llama/Llama-3.3-70B-Instruct-Turbo-Free')
    
    @classmethod
    def validate(cls):
        """Validate required environment variables"""
        required = ['PINECONE_API_KEY', 'UPSTASH_REDIS_URL', 'UPSTASH_REDIS_TOKEN', 'HF_API_TOKEN']
        missing = [var for var in required if not os.environ.get(var)]
        if missing:
            raise ValueError(f"Missing required environment variables: {missing}")

config = Config()