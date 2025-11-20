import boto3
import io
import json
from typing import List, Dict, Tuple
import re
from .config import config
from datetime import datetime
from decimal import Decimal
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


def convert_decimals(obj):
    if isinstance(obj, list):
        return [convert_decimals(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: convert_decimals(value) for key, value in obj.items()}
    elif isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    else:
        return obj

def extract_text_with_pages(file_content: bytes, filename: str) -> List[Dict]:
    """
    🔥 NEW: Extract text from file with page number tracking
    Returns: List[Dict] with structure:
    [
        {'page_number': 1, 'text': '...'},
        {'page_number': 2, 'text': '...'},
        ...
    ]
    """
    if filename.lower().endswith('.pdf'):
        if PDF_AVAILABLE:
            return extract_from_pdf_with_pages(file_content)
        else:
            return [{'page_number': None, 'text': "PDF processing not available. Please convert to text format."}]
    elif filename.lower().endswith('.docx'):
        if DOCX_AVAILABLE:
            # DOCX doesn't have clear page boundaries, treat as single page
            text = extract_from_docx(file_content)
            return [{'page_number': None, 'text': text}]
        else:
            return [{'page_number': None, 'text': "DOCX processing not available. Please convert to text format."}]
    elif filename.lower().endswith(('.txt', '.md')):
        text = file_content.decode('utf-8', errors='ignore')
        return [{'page_number': None, 'text': text}]
    else:
        # Try to decode as text anyway
        try:
            text = file_content.decode('utf-8', errors='ignore')
            return [{'page_number': None, 'text': text}]
        except:
            return [{'page_number': None, 'text': "Unable to extract text from this file format."}]

def extract_from_pdf_with_pages(content: bytes) -> List[Dict]:
    """
    🔥 NEW: Extract text from PDF with page numbers
    Returns list of {page_number, text} dicts
    """
    try:
        pdf_file = io.BytesIO(content)
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        
        pages = []
        for page_num in range(len(pdf_reader.pages)):
            page = pdf_reader.pages[page_num]
            text = page.extract_text()
            
            if text.strip():  # Only include pages with text
                pages.append({
                    'page_number': page_num + 1,  # 1-indexed
                    'text': text
                })
        
        print(f"📄 Extracted {len(pages)} pages from PDF")
        return pages if pages else [{'page_number': None, 'text': 'PDF appears to be empty or image-based'}]
        
    except Exception as e:
        print(f"PDF extraction error: {e}")
        return [{'page_number': None, 'text': "Error extracting text from PDF"}]

def extract_text_from_file(file_content: bytes, filename: str) -> str:
    """
    Original function - kept for backward compatibility
    🔥 NOTE: Use extract_text_with_pages() for new code
    """
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

def chunk_pages(pages: List[Dict], chunk_size: int = 400, overlap: int = 100) -> List[Dict]:
    """
    🔥 NEW: Chunk multiple pages while preserving page numbers
    
    Args:
        pages: List of {'page_number': int, 'text': str} dicts
        chunk_size: Words per chunk
        overlap: Words to overlap between chunks
    
    Returns:
        List of chunks with page numbers preserved
    """
    all_chunks = []
    
    for page_info in pages:
        page_num = page_info['page_number']
        text = page_info['text']
        
        if not text.strip():
            continue
        
        # Chunk this page
        page_chunks = chunk_text_with_overlap(text, chunk_size, overlap, page_num)
        all_chunks.extend(page_chunks)
    
    # Re-number positions globally
    for i, chunk in enumerate(all_chunks):
        chunk['position'] = i
    
    print(f"📊 Created {len(all_chunks)} chunks across {len(pages)} pages")
    
    # Log page distribution
    if all_chunks and 'page_number' in all_chunks[0] and all_chunks[0]['page_number'] is not None:
        page_counts = {}
        for chunk in all_chunks:
            page = chunk.get('page_number', 'Unknown')
            page_counts[page] = page_counts.get(page, 0) + 1
        
        print(f"📄 Chunks per page: {dict(sorted(page_counts.items()))}")
    
    return all_chunks


def chunk_text_with_overlap(text: str, chunk_size: int = 400, overlap: int = 100, 
                           page_number: int = None) -> List[Dict[str, any]]:
    """
    🔥 IMPROVED: Chunk text with overlap and optional page number
    
    Args:
        text: Text to chunk
        chunk_size: Target words per chunk
        overlap: Words to overlap between chunks
        page_number: Optional page number to attach to all chunks
    
    Returns:
        List of chunk dicts with 'text', 'word_count', 'page_number', etc.
    """
    # Clean text
    text = re.sub(r'\s+', ' ', text).strip()
    
    if not text:
        return []
    
    # Split into sentences for better boundaries
    sentence_delimiters = r'[.!?]+\s+'
    sentences = re.split(sentence_delimiters, text)
    sentences = [s.strip() for s in sentences if s.strip()]
    
    if len(sentences) < 2:
        # Fallback to word-based chunking
        return chunk_by_words_with_overlap(text, chunk_size, overlap, page_number)
    
    chunks = []
    current_chunk = ""
    current_word_count = 0
    sentence_index = 0
    
    for i, sentence in enumerate(sentences):
        sentence_words = len(sentence.split())
        
        # If adding this sentence would exceed chunk size, finalize current chunk
        if current_word_count + sentence_words > chunk_size and current_chunk:
            # Add current chunk
            chunk_dict = {
                'text': current_chunk.strip(),
                'word_count': current_word_count,
                'start_sentence': sentence_index,
                'end_sentence': i - 1,
                'position': len(chunks)
            }
            
            # 🔥 NEW: Add page number if available
            if page_number is not None:
                chunk_dict['page_number'] = page_number
            
            chunks.append(chunk_dict)
            
            # Start new chunk with overlap
            overlap_text = get_overlap_text(current_chunk, overlap)
            current_chunk = overlap_text + " " + sentence if overlap_text else sentence
            current_word_count = len(current_chunk.split())
            sentence_index = max(0, i - 1)
            
        else:
            # Add sentence to current chunk
            if current_chunk:
                current_chunk += " " + sentence
            else:
                current_chunk = sentence
                sentence_index = i
            current_word_count += sentence_words
    
    # Add final chunk
    if current_chunk.strip():
        chunk_dict = {
            'text': current_chunk.strip(),
            'word_count': current_word_count,
            'start_sentence': sentence_index,
            'end_sentence': len(sentences) - 1,
            'position': len(chunks)
        }
        
        # 🔥 NEW: Add page number if available
        if page_number is not None:
            chunk_dict['page_number'] = page_number
        
        chunks.append(chunk_dict)
    
    return chunks

def chunk_by_words_with_overlap(text: str, chunk_size: int = 400, overlap: int = 100,
                                page_number: int = None) -> List[Dict[str, any]]:
    """
    🔥 NEW: Word-based chunking with overlap support
    Fallback when sentence detection fails
    """
    words = text.split()
    chunks = []
    
    i = 0
    position = 0
    
    while i < len(words):
        # Get chunk
        chunk_words = words[i:i + chunk_size]
        chunk_text = ' '.join(chunk_words)
        
        chunk_dict = {
            'text': chunk_text,
            'word_count': len(chunk_words),
            'position': position,
            'word_start': i,
            'word_end': i + len(chunk_words)
        }
        
        # 🔥 NEW: Add page number if available
        if page_number is not None:
            chunk_dict['page_number'] = page_number
        
        chunks.append(chunk_dict)
        
        # Move forward with overlap
        i += chunk_size - overlap
        position += 1
        
        if i >= len(words):
            break
    
    return chunks

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

def get_overlap_text(text: str, overlap_words: int) -> str:
    """Get the last N words for overlap"""
    words = text.split()
    if len(words) <= overlap_words:
        return text
    return " ".join(words[-overlap_words:])

def chunk_by_words(text: str, chunk_size: int = 400, overlap: int = 100) -> List[Dict[str, str]]:
    """Fallback word-based chunking"""
    words = text.split()
    chunks = []
    
    for i in range(0, len(words), chunk_size - overlap):
        chunk_words = words[i:i + chunk_size]
        chunk_text = ' '.join(chunk_words)
        
        chunks.append({
            'text': chunk_text,
            'word_count': len(chunk_words),
            'start_index': i // (chunk_size - overlap),
            'word_start': i,
            'word_end': i + len(chunk_words)
        })
        
        if i + chunk_size >= len(words):
            break
    
    return chunks

def chunk_text(text: str, chunk_size: int = 400, overlap: int = 100) -> List[Dict[str, str]]:
    """Improved text chunking with better boundary detection"""
    
    # Clean text
    text = re.sub(r'\s+', ' ', text).strip()
    
    if not text:
        return []
    
    print(f"Chunking text: {len(text)} characters, {len(text.split())} words")
    
 
    # Split into sentences using multiple delimiters
    sentence_delimiters = r'[.!?]+\s+'
    sentences = re.split(sentence_delimiters, text)
    
    # Clean up sentences
    sentences = [s.strip() for s in sentences if s.strip()]
    
    if len(sentences) < 2:
        # Fallback to word-based chunking if sentence detection fails
        return chunk_by_words(text, chunk_size, overlap)
    
    print(f"Split into {len(sentences)} sentences")
    
    chunks = []
    current_chunk = ""
    current_word_count = 0
    sentence_index = 0
    
    for i, sentence in enumerate(sentences):
        sentence_words = len(sentence.split())
        
        # If adding this sentence would exceed chunk size, finalize current chunk
        if current_word_count + sentence_words > chunk_size and current_chunk:
            # Add current chunk
            chunks.append({
                'text': current_chunk.strip(),
                'word_count': current_word_count,
                'start_sentence': sentence_index,
                'end_sentence': i - 1,
                'start_index': len(chunks)  # Position in document
            })
            
            # Start new chunk with overlap
            overlap_text = get_overlap_text(current_chunk, overlap)
            current_chunk = overlap_text + " " + sentence if overlap_text else sentence
            current_word_count = len(current_chunk.split())
            sentence_index = max(0, i - 1)  # Start slightly before for context
            
        else:
            # Add sentence to current chunk
            if current_chunk:
                current_chunk += " " + sentence
            else:
                current_chunk = sentence
                sentence_index = i
            current_word_count += sentence_words
    
    # Add final chunk
    if current_chunk.strip():
        chunks.append({
            'text': current_chunk.strip(),
            'word_count': current_word_count,
            'start_sentence': sentence_index,
            'end_sentence': len(sentences) - 1,
            'start_index': len(chunks)
        })
    
    print(f"Created {len(chunks)} chunks using sentence-based splitting")
    
    # Debug: Print chunk distribution
    word_counts = [c['word_count'] for c in chunks]
    print(f"Chunk sizes - min: {min(word_counts)}, max: {max(word_counts)}, avg: {sum(word_counts)/len(word_counts):.1f}")
    
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

def create_error_response(status_code: int, message: str, details: str = None) -> Dict:
    """Create standardized error response with enhanced CORS headers"""
    error_body = {
        'error': message,
        'timestamp': datetime.utcnow().isoformat()
    }
    
    if details:
        error_body['details'] = details
    
    # Convert any Decimals in error details
    serializable_error = convert_decimals(error_body)
    
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS',
            'Access-Control-Allow-Credentials': 'false'
        },
        'body': json.dumps(serializable_error)
    }

def create_success_response(data: Dict, status_code: int = 200) -> Dict:
    """Create standardized success response with enhanced CORS headers"""
    # Convert Decimal objects to serializable types BEFORE json.dumps
    serializable_data = convert_decimals(data)
    
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS',
            'Access-Control-Allow-Credentials': 'false'
        },
        'body': json.dumps(serializable_data)  # Now this will work!
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