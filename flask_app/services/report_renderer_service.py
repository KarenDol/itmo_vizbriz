"""
Report Renderer Service for PDF to HTML Conversion

Converts PDF reports to structured HTML using LLM (Bedrock Claude) with caching.
Maintains a cache in the database to avoid re-rendering on each request.
"""

import logging
import io
import base64
from typing import Dict, Optional, Tuple
from datetime import datetime
import json
import time

# PDF processing
try:
    from PyPDF2 import PdfReader
except ImportError:
    PdfReader = None

try:
    from PIL import Image
except ImportError:
    Image = None

from flask import current_app
from flask_app.extensions import db
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class ReportRenderer:
    """Service for rendering PDF reports to HTML using LLM"""
    
    # Guardrail system prompt for LLM
    SYSTEM_PROMPT = """You are a rendering assistant that converts medical report text into clean, structured HTML.

STRICT RULES:
1. Do NOT invent or change any medical facts, values, or findings
2. Do NOT add information that was not in the original text
3. Preserve all headings, lists, tables, and figure captions exactly as they appear
4. Use only semantic HTML tags: <h1>, <h2>, <h3>, <p>, <ul>, <ol>, <li>, <table>, <img>
5. Do NOT include any <style>, <script>, or external CSS/JS
6. For images, use: <img src="data:image/png;base64,{base64_data}" alt="description">
7. Structure the document logically with proper heading hierarchy
8. Preserve numeric values and measurements exactly as written

Output clean HTML only - no explanations, no markdown, just HTML."""
    
    @staticmethod
    def render_report_to_html(file_id: int, file_table: str, patient_id: int,
                             report_level: Optional[int] = None,
                             force_rerender: bool = False) -> Tuple[str, Dict]:
        """
        Render a PDF report to HTML with caching
        
        Args:
            file_id: File ID
            file_table: 'files' or 'adminfiles'
            patient_id: Patient ID
            report_level: Optional report level (1-7)
            force_rerender: Force re-rendering even if cached
            
        Returns:
            Tuple of (html_content, metadata_dict)
        """
        logger.info(f"Rendering report: file_id={file_id}, table={file_table}, patient={patient_id}")
        
        # Check cache first (unless force rerender)
        if not force_rerender:
            cached = ReportRenderer._get_cached_render(file_id, file_table)
            if cached:
                logger.info(f"Using cached render for file {file_id}")
                return cached['html_content'], {
                    'cached': True,
                    'render_method': cached.get('render_method'),
                    'created_at': cached.get('created_at')
                }
        
        # Get file from database
        file_obj, s3_key = ReportRenderer._get_file_object(file_id, file_table)
        if not file_obj:
            raise ValueError(f"File not found: {file_id} in {file_table}")
        
        # Download PDF from S3
        pdf_content = ReportRenderer._download_from_s3(s3_key)
        if not pdf_content:
            raise ValueError(f"Could not download file from S3: {s3_key}")
        
        # Extract text and images from PDF
        start_time = time.time()
        extracted_data = ReportRenderer._extract_pdf_content(pdf_content)
        
        # Build prompt for LLM
        llm_payload = ReportRenderer._build_llm_payload(
            extracted_data,
            report_level,
            file_obj.name
        )
        
        # Call Bedrock LLM to render HTML
        html_content, llm_metadata = ReportRenderer._call_bedrock_for_rendering(llm_payload)
        
        # Sanitize HTML
        html_content = ReportRenderer._sanitize_html(html_content)
        
        render_time_ms = int((time.time() - start_time) * 1000)
        
        # Cache the result
        ReportRenderer._cache_render(
            file_id=file_id,
            file_table=file_table,
            report_level=report_level,
            html_content=html_content,
            render_method='bedrock',
            provider=llm_metadata.get('model_name', 'claude'),
            token_count=llm_metadata.get('token_count'),
            render_time_ms=render_time_ms,
            status='success'
        )
        
        metadata = {
            'cached': False,
            'render_method': 'bedrock',
            'render_time_ms': render_time_ms,
            'token_count': llm_metadata.get('token_count'),
            'created_at': datetime.utcnow()
        }
        
        return html_content, metadata
    
    @staticmethod
    def _get_cached_render(file_id: int, file_table: str) -> Optional[Dict]:
        """Check if rendered HTML is cached - DISABLED for Phase 1 (no DB migration)"""
        # Phase 1: No database caching - always render fresh
        # TODO Phase 2: Enable database caching for performance
        logger.info("Cache disabled - rendering fresh (Phase 1 mode)")
        return None
    
    @staticmethod
    def _get_file_object(file_id: int, file_table: str) -> Tuple[Optional[object], Optional[str]]:
        """Get file object and S3 key from database"""
        try:
            if file_table == 'files':
                from flask_app.models import File
                file_obj = File.query.get(file_id)
            elif file_table == 'adminfiles':
                from flask_app.models import AdminFile
                file_obj = AdminFile.query.get(file_id)
            else:
                logger.error(f"Invalid file_table: {file_table}")
                return None, None
            
            if not file_obj:
                return None, None
            
            return file_obj, file_obj.s3_key
            
        except Exception as e:
            logger.error(f"Error getting file object: {e}")
            return None, None
    
    @staticmethod
    def _download_from_s3(s3_key: str) -> Optional[bytes]:
        """Download file content from S3"""
        try:
            s3_client = boto3.client('s3')
            bucket = current_app.config.get('S3_BUCKET')
            
            response = s3_client.get_object(Bucket=bucket, Key=s3_key)
            return response['Body'].read()
            
        except ClientError as e:
            logger.error(f"S3 download error for {s3_key}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error downloading from S3: {e}")
            return None
    
    @staticmethod
    def _extract_pdf_content(pdf_bytes: bytes) -> Dict:
        """Extract text and images from PDF"""
        extracted = {
            'title': '',
            'sections': [],
            'images': [],
            'raw_text': ''
        }
        
        if not PdfReader:
            logger.warning("PyPDF2 not available, returning empty extraction")
            return extracted
        
        try:
            pdf = PdfReader(io.BytesIO(pdf_bytes))
            
            # Extract text from all pages
            all_text = []
            for page_num, page in enumerate(pdf.pages, 1):
                try:
                    text = page.extract_text()
                    if text:
                        all_text.append(f"--- Page {page_num} ---\n{text}")
                except Exception as e:
                    logger.error(f"Error extracting text from page {page_num}: {e}")
            
            extracted['raw_text'] = '\n\n'.join(all_text)
            
            # Try to extract title (first line)
            if extracted['raw_text']:
                lines = extracted['raw_text'].split('\n')
                for line in lines:
                    if line.strip():
                        extracted['title'] = line.strip()[:200]
                        break
            
            # Note: Image extraction from PDF is complex and may require additional libraries
            # For now, we'll just note that images exist
            logger.info(f"Extracted {len(pdf.pages)} pages of text from PDF")
            
        except Exception as e:
            logger.error(f"Error extracting PDF content: {e}")
        
        return extracted
    
    @staticmethod
    def _build_llm_payload(extracted_data: Dict, report_level: Optional[int], 
                          filename: str) -> Dict:
        """Build payload for LLM rendering"""
        return {
            'report_level': report_level,
            'filename': filename,
            'title': extracted_data.get('title', ''),
            'content': extracted_data.get('raw_text', ''),
            'images_count': len(extracted_data.get('images', []))
        }
    
    @staticmethod
    def _call_bedrock_for_rendering(payload: Dict) -> Tuple[str, Dict]:
        """Call Bedrock Claude to render HTML"""
        try:
            bedrock_runtime = boto3.client(
                service_name='bedrock-runtime',
                region_name=current_app.config.get('AWS_REGION', 'us-east-1')
            )
            
            # Build user message
            user_message = f"""Convert this medical report to clean, structured HTML:

FILENAME: {payload['filename']}
REPORT LEVEL: {payload.get('report_level', 'Unknown')}

CONTENT:
{payload['content'][:15000]}  

Remember: Preserve all medical information exactly as written. Use only semantic HTML tags."""
            
            # Bedrock API call
            request_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4000,
                "temperature": 0.1,  # Low temperature for consistency
                "messages": [
                    {
                        "role": "user",
                        "content": user_message
                    }
                ],
                "system": ReportRenderer.SYSTEM_PROMPT
            }
            
            # Get model ID from config
            model_id = current_app.config.get('BEDROCK_MODEL_ID', 
                                             'us.anthropic.claude-sonnet-4-20250514-v1:0')
            
            response = bedrock_runtime.invoke_model(
                modelId=model_id,
                body=json.dumps(request_body)
            )
            
            response_body = json.loads(response['body'].read())
            
            # Extract HTML content
            html_content = ''
            if 'content' in response_body and len(response_body['content']) > 0:
                html_content = response_body['content'][0].get('text', '')
            
            # Get token usage
            usage = response_body.get('usage', {})
            token_count = usage.get('input_tokens', 0) + usage.get('output_tokens', 0)
            
            metadata = {
                'model_name': 'claude_sonnet_4',
                'token_count': token_count,
                'input_tokens': usage.get('input_tokens'),
                'output_tokens': usage.get('output_tokens')
            }
            
            logger.info(f"Bedrock rendering successful, tokens used: {token_count}")
            
            return html_content, metadata
            
        except Exception as e:
            logger.error(f"Bedrock API error: {e}")
            # Fallback to simple text rendering
            return ReportRenderer._fallback_simple_render(payload['content']), {
                'model_name': 'fallback',
                'token_count': 0
            }
    
    @staticmethod
    def _fallback_simple_render(text: str) -> str:
        """Simple fallback rendering if LLM fails"""
        # Basic HTML wrapping
        html = '<div class="report-content">\n'
        
        paragraphs = text.split('\n\n')
        for para in paragraphs:
            if para.strip():
                # Check if it looks like a heading (all caps, short)
                if para.isupper() and len(para) < 100:
                    html += f'<h2>{para.strip()}</h2>\n'
                else:
                    html += f'<p>{para.strip()}</p>\n'
        
        html += '</div>'
        return html
    
    @staticmethod
    def _sanitize_html(html_content: str) -> str:
        """Sanitize HTML to prevent XSS attacks"""
        # Basic sanitization - in production, use bleach or similar
        # For now, we trust the LLM output but could add more filtering
        
        # Remove any script tags
        import re
        html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        
        # Remove any inline event handlers
        html_content = re.sub(r'\s*on\w+\s*=\s*["\'][^"\']*["\']', '', html_content, flags=re.IGNORECASE)
        
        return html_content
    
    @staticmethod
    def _cache_render(file_id: int, file_table: str, report_level: Optional[int],
                     html_content: str, render_method: str, provider: Optional[str],
                     token_count: Optional[int], render_time_ms: int, status: str,
                     error_message: Optional[str] = None):
        """Cache rendered HTML in database - DISABLED for Phase 1 (no DB migration)"""
        # Phase 1: No database caching - just log the render
        # TODO Phase 2: Enable database caching for performance
        logger.info(f"Render completed for file {file_id} in {render_time_ms}ms (cache disabled - Phase 1 mode)")

