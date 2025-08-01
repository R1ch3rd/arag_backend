# RAG System Backend

A robust, scalable Retrieval-Augmented Generation (RAG) system backend built on AWS serverless architecture. This system enables intelligent document-based conversations using advanced LLM integration and vector search capabilities.

## 🏗️ Architecture Overview

The backend follows a microservices architecture using AWS Lambda functions, DynamoDB for data persistence, Pinecone for vector storage, and Redis for caching.

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Frontend      │────│   API Gateway    │────│  Lambda Functions│
│   (React)       │    │   (CORS + Auth)  │    │   (Serverless)   │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                │                        │
                       ┌────────┴────────┐              │
                       │   AWS Cognito   │              │
                       │ (Authentication)│              │
                       └─────────────────┘              │
                                                        │
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Amazon S3     │────│    DynamoDB      │────│   Pinecone DB   │
│ (File Storage)  │    │ (Metadata/Chat)  │    │ (Vector Search) │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                │
                       ┌────────┴────────┐
                       │   Upstash Redis │
                       │    (Caching)    │
                       └─────────────────┘
```

## ✨ Core Features

### 🔐 Authentication & Authorization
- **AWS Cognito Integration**: Secure user authentication and authorization
- **JWT Token Management**: Automatic token validation and refresh
- **User Pool Management**: Email-based registration with verification
- **Session Management**: Secure session handling across all endpoints

### 📄 Document Management
- **Multi-format Support**: PDF, TXT, DOCX, and more
- **Intelligent Chunking**: Advanced text segmentation with overlap handling
- **S3 Storage**: Scalable file storage with metadata tracking
- **Document Status Tracking**: Upload, processing, ready, and error states
- **Active/Inactive Toggle**: Control which documents are searchable
- **Document Analytics**: Chunk count, file size, and processing statistics

### 🔍 Vector Search & Embeddings
- **Pinecone Integration**: High-performance vector database
- **HuggingFace Embeddings**: Multiple embedding model support
- **Enhanced Search Strategy**: Document-aware ranking and diversity
- **Batch Processing**: Efficient embedding generation with fallback handling
- **Position-Aware Chunking**: Maintains document structure and context
- **Multi-document Search**: Intelligent results from multiple sources

### 💬 Chat System
- **Session Management**: Persistent conversation threads
- **Context-Aware Responses**: Maintains conversation history
- **Multi-LLM Support**: Together AI and Google Gemini integration
- **Model Locking**: Prevents model switching mid-conversation
- **Source Attribution**: Citations with document references
- **Export Functionality**: Markdown export of chat sessions

### ⚡ Performance & Caching
- **Redis Caching**: Multi-layer caching strategy
- **Query Result Caching**: Intelligent cache invalidation
- **Rate Limiting**: Per-user API rate limiting
- **Background Processing**: Async document processing
- **Batch Operations**: Efficient bulk operations

### 🛠️ Advanced Features
- **Auto-title Generation**: AI-powered chat session titles
- **Search & Filter**: Advanced session and document search
- **Empty Session Cleanup**: Automatic maintenance tasks
- **Comprehensive Logging**: Detailed debug and performance logging
- **Error Handling**: Robust error recovery and user feedback

## 🚀 Technology Stack

### Core Infrastructure
- **AWS Lambda**: Serverless compute (Python 3.13)
- **AWS API Gateway**: RESTful API with CORS support
- **AWS DynamoDB**: NoSQL database with Pay-per-Request billing
- **AWS S3**: Object storage for documents
- **AWS Cognito**: Authentication and user management
- **AWS CloudFormation**: Infrastructure as Code (SAM)

### External Services
- **Pinecone**: Vector database for embeddings
- **Upstash Redis**: Serverless Redis for caching
- **HuggingFace**: Embedding generation API
- **Together AI**: LLM API for chat responses
- **Google Gemini**: Alternative LLM provider

### Development Tools
- **AWS SAM**: Serverless Application Model
- **Python Libraries**: boto3, requests, pinecone-client, redis
- **JSON Schema**: API contract validation

## 📁 Project Structure

```
backend/
├── template.yaml                 # SAM CloudFormation template
├── lambdas/
│   ├── shared/
│   │   ├── config.py             # Configuration management
│   │   ├── database.py           # DynamoDB operations
│   │   ├── vector_store.py       # Pinecone integration
│   │   ├── cache.py              # Redis caching layer
│   │   └── utils.py              # Shared utilities
│   ├── documents/
│   │   └── handler.py            # Document management APIs
│   └── chat/
│       └── handler.py            # Chat and session APIs
├── requirements.txt              # Python dependencies
└── README.md                     # This file
```

## 🔧 Configuration

### Environment Variables
```bash
# Database Tables
USERS_TABLE=rag-users
DOCUMENTS_TABLE=rag-documents
SESSIONS_TABLE=rag-sessions
MESSAGES_TABLE=rag-messages

# Storage
DOCUMENTS_BUCKET=rag-documents-bucket
PINECONE_API_KEY=your-pinecone-key
PINECONE_INDEX=rag-documents

# Caching
UPSTASH_REDIS_REST_URL=your-redis-url
UPSTASH_REDIS_REST_TOKEN=your-redis-token

# AI Services
HF_API_TOKEN=your-huggingface-token
TOGETHER_API_KEY=your-together-api-key
GEMINI_API_KEY=your-gemini-api-key
LLM_PROVIDER=together
```

## 📊 API Documentation

### Authentication Endpoints
- `POST /auth/login` - User authentication
- `POST /auth/register` - User registration
- `POST /auth/confirm` - Email verification

### Document Management
- `POST /documents/upload` - Upload new document
- `GET /documents` - List user documents
- `GET /documents/active` - Get active documents only
- `PUT /documents/{id}/toggle` - Toggle document active status
- `DELETE /documents/{id}` - Delete document
- `GET /documents/{id}/view` - View document content

### Chat System
- `POST /chat/sessions` - Create new chat session
- `GET /chat/sessions` - List user sessions
- `PUT /chat/sessions/{id}` - Update session (title)
- `DELETE /chat/sessions/{id}` - Delete session
- `GET /chat/sessions/search` - Search sessions
- `POST /chat/sessions/{id}/messages` - Send message
- `GET /chat/sessions/{id}/messages` - Get chat history
- `GET /chat/sessions/{id}/export` - Export session

### Utility Endpoints
- `POST /cache/clear` - Clear user cache
- `GET /cache/stats` - Get cache statistics

## 🗄️ Database Schema

### DynamoDB Tables

#### Documents Table
```
PK: USER#{user_id}
SK: DOC#{document_id}
Attributes:
- document_id: string
- filename: string
- s3_key: string
- file_size: number
- status: string (processing|ready|error)
- active: boolean
- created_at: ISO timestamp
```

#### Sessions Table
```
PK: USER#{user_id}
SK: SESSION#{session_id}
Attributes:
- session_id: string
- title: string
- document_set: string[]
- locked_model: string
- created_at: ISO timestamp
- last_accessed: ISO timestamp
- message_count: number
```

#### Messages Table
```
PK: SESSION#{session_id}
SK: MSG#{timestamp}#{message_id}
Attributes:
- message_id: string
- role: string (user|assistant)
- content: string
- sources: object[]
- timestamp: ISO timestamp
```

## 🚀 Deployment

### Prerequisites
- AWS CLI configured
- SAM CLI installed
- Python 3.13+
- Valid AWS account with appropriate permissions

### Deployment Steps

1. **Clone and Navigate**
   ```bash
   git clone <repository>
   cd backend/
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Parameters**
   Create `samconfig.toml` or use parameter overrides:
   ```bash
   sam deploy --guided
   ```

4. **Deploy Infrastructure**
   ```bash
   sam build
   sam deploy
   ```

5. **Verify Deployment**
   ```bash
   # Check API Gateway endpoint
   curl https://your-api-id.execute-api.region.amazonaws.com/prod/
   ```

### Environment-Specific Deployment
```bash
# Development
sam deploy --config-env dev

# Production
sam deploy --config-env prod --no-confirm-changeset
```

## 🧪 Testing

### Local Testing
```bash
# Start local API
sam local start-api

# Test specific function
sam local invoke DocumentUploadFunction -e events/upload.json
```

### Integration Testing
```bash
# Run test suite
python -m pytest tests/

# Test specific endpoint
curl -X POST http://localhost:3000/documents/upload \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @test-document.json
```

## 📈 Performance Considerations

### Optimization Features
- **Connection Pooling**: Reused database connections
- **Batch Processing**: Efficient bulk operations
- **Lazy Loading**: On-demand resource initialization
- **Caching Strategy**: Multi-level caching with TTL
- **Async Processing**: Background document processing

### Scaling Considerations
- **Lambda Concurrency**: Configured reserved concurrency
- **DynamoDB Scaling**: Auto-scaling enabled
- **S3 Performance**: Optimized key patterns
- **Vector Database**: Pinecone serverless scaling
- **Cache Distribution**: Redis cluster support

## 🔒 Security Features

### Data Protection
- **Encryption at Rest**: All data encrypted
- **Encryption in Transit**: TLS 1.2+ required
- **Access Controls**: IAM role-based permissions
- **Input Validation**: Comprehensive request validation
- **Rate Limiting**: Per-user request limits

### Authentication Security
- **JWT Validation**: Secure token verification
- **Token Expiration**: Automatic session timeout
- **CORS Protection**: Configurable origin restrictions
- **User Isolation**: Namespace-based data separation

## 🐛 Troubleshooting

### Common Issues

#### CORS Errors
```bash
# Check OPTIONS endpoints
curl -X OPTIONS https://api-url/documents/upload \
  -H "Origin: https://your-frontend.com"
```

#### Authentication Issues
```bash
# Verify token
aws cognito-idp get-user --access-token $TOKEN
```

#### Vector Search Problems
```bash
# Check Pinecone index stats
# Monitor embedding generation logs
```

### Logging & Monitoring
- **CloudWatch Logs**: Centralized logging
- **Custom Metrics**: Performance tracking
- **Error Tracking**: Comprehensive error logging
- **Debug Mode**: Detailed request/response logging

## 🤝 Contributing

### Development Setup
1. Fork the repository
2. Create feature branch: `git checkout -b feature/new-feature`
3. Install dev dependencies: `pip install -r requirements-dev.txt`
4. Run tests: `python -m pytest`
5. Submit pull request

### Code Standards
- **Python Style**: Follow PEP 8
- **Type Hints**: Use type annotations
- **Documentation**: Comprehensive docstrings
- **Testing**: Unit tests for all functions
- **Error Handling**: Proper exception handling

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.
