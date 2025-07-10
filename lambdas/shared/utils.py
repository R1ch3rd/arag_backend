import boto3
import io
import json
from typing import List, Dict, Tuple
import re
from .config import config
from datetime import datetime

# Try to import document processing libraries with fallbacks
try:
    import PyPDF2
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
    print("PyPDF2 not available")

try:
    import docx
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    print("python-docx not available")

s3_client = boto3.client('s3')

def extract_text_from_file(file_content: bytes, filename: str) -> str:
    """Extract text from various file formats"""
    if filename.lower().endswith('.pdf'):
        if PDF_AVAILABLE:
            return extract_from_pdf(file_content)
        else:
            return "PDF processing not available. Please convert to text format."
    elif filename.lower().endswith('.docx'):
        if DOCX_AVAILABLE:
            return extract_from_docx(file_content)
        else:
            return "DOCX processing not available. Please convert to text format."
    elif filename.lower().endswith(('.txt', '.md')):
        return file_content.decode('utf-8', errors='ignore')
    else:
        # Try to decode as text anyway
        try:
            return file_content.decode('utf-8', errors='ignore')
        except:
            return "Unable to extract text from this file format."

def extract_from_pdf(content: bytes) -> str:
    """Extract text from PDF"""
    try:
        pdf_file = io.BytesIO(content)
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        
        text = ""
        for page_num in range(len(pdf_reader.pages)):
            page = pdf_reader.pages[page_num]
            text += page.extract_text() + "\n"
        
        return text
    except Exception as e:
        print(f"PDF extraction error: {e}")
        return "Error extracting text from PDF"

def extract_from_docx(content: bytes) -> str:
    """Extract text from DOCX"""
    try:
        doc_file = io.BytesIO(content)
        doc = docx.Document(doc_file)
        
        text = ""
        for paragraph in doc.paragraphs:
            text += paragraph.text + "\n"
        
        return text
    except Exception as e:
        print(f"DOCX extraction error: {e}")
        return "Error extracting text from DOCX"

def chunk_text(text: str, chunk_size: int = 512, overlap: int = 128) -> List[Dict[str, str]]:
    """Split text into overlapping chunks"""
    # Clean text
    text = re.sub(r'\s+', ' ', text).strip()
    
    if not text:
        return []
    
    # Simple word-based chunking for now
    words = text.split()
    chunks = []
    
    for i in range(0, len(words), chunk_size - overlap):
        chunk_words = words[i:i + chunk_size]
        chunk_text = ' '.join(chunk_words)
        
        chunks.append({
            'text': chunk_text,
            'word_count': len(chunk_words),
            'start_index': i
        })
        
        if i + chunk_size >= len(words):
            break
    
    return chunks

def upload_to_s3(user_id: str, document_id: str, content: bytes, filename: str) -> str:
    """Upload document to S3"""
    s3_key = f"users/{user_id}/documents/{document_id}/{sanitize_filename(filename)}"
    
    try:
        s3_client.put_object(
            Bucket=config.DOCUMENTS_BUCKET,
            Key=s3_key,
            Body=content,
            ServerSideEncryption='AES256',
            Metadata={
                'user_id': user_id,
                'document_id': document_id,
                'original_filename': filename
            }
        )
        return s3_key
    except Exception as e:
        print(f"S3 upload error: {e}")
        raise

def delete_from_s3(s3_key: str):
    """Delete document from S3"""
    try:
        s3_client.delete_object(
            Bucket=config.DOCUMENTS_BUCKET,
            Key=s3_key
        )
    except Exception as e:
        print(f"S3 delete error: {e}")
        raise

def create_error_response(status_code: int, message: str) -> Dict:
    """Create standardized error response"""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
        },
        'body': json.dumps({
            'error': message,
            'timestamp': datetime.utcnow().isoformat()
        })
    }

def create_success_response(data: Dict) -> Dict:
    """Create standardized success response"""
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
        },
        'body': json.dumps(data)
    }

def sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe storage"""
    # Remove any path components
    filename = filename.split('/')[-1].split('\\')[-1]
    
    # Replace spaces and special characters
    filename = re.sub(r'[^\w\-_\.]', '_', filename)
    
    # Limit length
    if '.' in filename:
        name, ext = filename.rsplit('.', 1)
        if len(name) > 50:
            name = name[:50]
        return f"{name}.{ext}"
    else:
        return filename[:50] if len(filename) > 50 else filename

def validate_file_type(filename: str) -> bool:
    """Validate if file type is supported"""
    supported_extensions = ['.pdf', '.txt', '.docx', '.md']
    return any(filename.lower().endswith(ext) for ext in supported_extensions)

def format_file_size(size_bytes: float) -> str:
    """Format file size in human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"

def calculate_file_hash(content: bytes) -> str:
    """Calculate SHA256 hash of file content"""
    import hashlib
    return hashlib.sha256(content).hexdigest()

def estimate_token_count(text: str) -> int:
    """Estimate token count for text (rough approximation)"""
    # Rough estimate: 1 token ≈ 4 characters
    return len(text) // 4

def parse_multipart_simple(body: bytes, content_type: str) -> Tuple[bytes, str]:
    """Simple multipart parsing without external dependencies"""
    try:
        # Extract boundary
        boundary = None
        for part in content_type.split(';'):
            if 'boundary=' in part:
                boundary = part.split('boundary=')[1].strip().strip('"')
                break
        
        if not boundary:
            raise ValueError("No boundary found")
        
        # Split by boundary
        boundary_bytes = f'--{boundary}'.encode()
        parts = body.split(boundary_bytes)
        
        for part in parts:
            if b'Content-Disposition' in part and b'filename=' in part:
                # Find the filename
                header_section = part.split(b'\r\n\r\n')[0]
                content_section = part.split(b'\r\n\r\n', 1)[1] if b'\r\n\r\n' in part else b''
                
                # Extract filename from headers
                header_text = header_section.decode('utf-8', errors='ignore')
                filename = 'uploaded_file.txt'
                
                for line in header_text.split('\n'):
                    if 'filename=' in line:
                        filename_part = line.split('filename=')[1].strip()
                        filename = filename_part.strip('"').strip("'")
                        break
                
                # Clean up content (remove trailing boundary markers)
                content_section = content_section.rstrip(b'\r\n--')
                
                return content_section, filename
        
        raise ValueError("No file found in multipart data")
        
    except Exception as e:
        print(f"Multipart parsing error: {e}")
        raise ValueError(f"Failed to parse multipart data: {str(e)}")

def generate_response_prompt(query: str, contexts: List[Dict]) -> str:
    """Generate prompt for LLM"""
    if not contexts:
        return f"Answer this question: {query}\n\nNote: No relevant context was found in the documents."
    
    context_text = "\n\n".join([
        f"[Source {i+1}] {ctx.get('text', ctx.get('chunk_text', ''))}" 
        for i, ctx in enumerate(contexts[:3])  # Limit to top 3 contexts
    ])
    
    prompt = f"""Based on the following context from documents, answer the user's question.
If the answer cannot be found in the context, say so honestly.

Context:
{context_text}

Question: {query}

Answer:"""
    
    return prompt