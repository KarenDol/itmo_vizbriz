#!/usr/bin/env python3
"""
Centralized Bedrock service for all LLM calls
This ensures consistent model usage, error handling, and configuration across the entire application.
"""

import json
import time
import logging
import uuid
import re
from typing import Dict, List, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Import configuration
try:
    from flask_app.config import Config
    DISABLE_LLM_CALLS = Config.DISABLE_LLM_CALLS
except ImportError:
    # Fallback if config import fails
    import os
    DISABLE_LLM_CALLS = os.getenv('DISABLE_LLM_CALLS', 'False').lower() in ('true', '1', 'yes', 'on')

# Import LLM Logger Service
try:
    from flask_app.services.llm_logger_service import LLMLoggerService
    LLM_LOGGING_ENABLED = True
except ImportError as e:
    logger.warning(f"LLMLoggerService not available: {e}")
    LLM_LOGGING_ENABLED = False

# Set up dedicated LLM call logger
llm_call_logger = logging.getLogger('llm_calls')
llm_call_logger.setLevel(logging.WARNING)  # Only show warnings and errors

# Create file handler for LLM calls
import os
log_dir = os.getenv('LLM_LOG_DIR', os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'LLM_logs'))
os.makedirs(log_dir, exist_ok=True)

from logging.handlers import RotatingFileHandler
llm_file_handler = RotatingFileHandler(
    os.path.join(log_dir, 'llm_calls.log'),
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5
)

# Set formatter
formatter = logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
llm_file_handler.setFormatter(formatter)
llm_call_logger.addHandler(llm_file_handler)

class BedrockService:
    """Centralized Bedrock service for all LLM calls"""
    
    # Centralized model configuration
    MODELS = {
        "claude_37_sonnet": "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
        "claude_35_sonnet_v2": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "claude_4_sonnet": "us.anthropic.claude-sonnet-4-20250514-v1:0",
        "claude_4_opus": "us.anthropic.claude-opus-4-20250514-v1:0",  # Claude 4 Opus inference profile (1M context)
    }
    
    # Default model to use
    DEFAULT_MODEL = "claude_4_sonnet"  # Migrated to Claude 4 Sonnet
    
    # Knowledge Base configuration
    KNOWLEDGE_BASE_ID = "RMBAKBVMLL"  # Default/legacy KB
    KNOWLEDGE_BASE_REGION = "us-east-2"
    
    # Level-4 Report Knowledge Bases
    KB_LEVEL4_STYLE_ID = "IJZCYAHWYL"  # Level-4-reports-style
    KB_LEVEL4_CLINIC_ID = "4LPX58EZ9T"  # Level-4-reports-clinic
    
    def _get_user_context(self) -> Dict[str, Any]:
        """Get user context for logging"""
        try:
            from flask import request, session, g
            from flask_login import current_user
            
            context = {
                'timestamp': datetime.utcnow().isoformat(),
                'request_id': getattr(g, 'request_id', None),
                'user_agent': request.headers.get('User-Agent', 'Unknown'),
                'ip_address': request.remote_addr,
                'endpoint': request.endpoint,
                'method': request.method
            }
            
            # Get current user info
            if current_user and current_user.is_authenticated:
                context.update({
                    'user_id': getattr(current_user, 'id', None),
                    'user_email': getattr(current_user, 'email', None),
                    'user_name': getattr(current_user, 'name', None),
                    'user_role': getattr(current_user, 'role', None),
                    'is_admin': getattr(current_user, 'is_admin', False)
                })
            else:
                context.update({
                    'user_id': None,
                    'user_email': None,
                    'user_name': None,
                    'user_role': None,
                    'is_admin': False
                })
            
            # Get session info
            context.update({
                'session_id': session.get('_id', None)
            })
            
            return context
        except Exception as e:
            logger.warning(f"Could not get user context: {e}")
            return {
                'timestamp': datetime.utcnow().isoformat(),
                'user_id': None,
                'user_email': None,
                'user_name': None,
                'is_admin': False
            }
    
    def __init__(self):
        self.client = None
        self.knowledge_base_client = None
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize the Bedrock client"""
        try:
            import boto3
            from botocore.config import Config
            
            timeout_seconds = int(os.getenv('BEDROCK_READ_TIMEOUT', '120'))
            config = Config(
                region_name='us-west-2',
                retries={
                    'max_attempts': 3,
                    'mode': 'adaptive'
                },
                connect_timeout=30,
                read_timeout=timeout_seconds,
                max_pool_connections=5
            )
            
            self.client = boto3.client('bedrock-runtime', config=config)
            
            # Initialize knowledge base client (different region)
            kb_config = Config(
                region_name=self.KNOWLEDGE_BASE_REGION,
                retries={
                    'max_attempts': 3,
                    'mode': 'adaptive'
                },
                connect_timeout=30,
                read_timeout=60,
                max_pool_connections=5
            )
            self.knowledge_base_client = boto3.client('bedrock-agent-runtime', config=kb_config)
            
            logger.info("Bedrock service initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing Bedrock service: {e}")
            self.client = None
            self.knowledge_base_client = None
    
    def is_available(self) -> bool:
        """Check if Bedrock service is available"""
        return self.client is not None
    
    def get_kb_document_counts(self) -> Dict[str, Any]:
        """
        Get document/file counts for each Knowledge Base using the Bedrock Agent API.
        Uses list_data_sources and list_knowledge_base_documents (paginated).
        
        Returns:
            Dict with 'success', 'knowledge_bases' (list of {id, name, document_count, data_sources}),
            and 'error' if something failed.
        """
        try:
            import boto3
            agent_client = boto3.client('bedrock-agent', region_name=self.KNOWLEDGE_BASE_REGION)
        except Exception as e:
            logger.warning(f"Could not create bedrock-agent client: {e}")
            return {"success": False, "error": str(e), "knowledge_bases": []}
        
        kb_configs = [
            ("default", self.KNOWLEDGE_BASE_ID, "Default KB"),
            ("level4_style", self.KB_LEVEL4_STYLE_ID, "Level 4 Style"),
            ("level4_clinic", self.KB_LEVEL4_CLINIC_ID, "Level 4 Clinic"),
        ]
        result = {"success": True, "knowledge_bases": []}
        
        for key, kb_id, label in kb_configs:
            try:
                # List data sources for this KB
                ds_response = agent_client.list_data_sources(
                    knowledgeBaseId=kb_id,
                    maxResults=20
                )
                data_sources = ds_response.get("dataSourceSummaries", [])
                total_docs = 0
                ds_details = []
                for ds in data_sources:
                    ds_id = ds.get("dataSourceId", "")
                    ds_name = ds.get("name", "unknown")
                    count = 0
                    next_token = None
                    try:
                        while True:
                            params = {
                                "knowledgeBaseId": kb_id,
                                "dataSourceId": ds_id,
                                "maxResults": 100
                            }
                            if next_token:
                                params["nextToken"] = next_token
                            doc_response = agent_client.list_knowledge_base_documents(**params)
                            doc_list = doc_response.get("documentDetailList") or doc_response.get("documentDetails") or []
                            count += len(doc_list)
                            next_token = doc_response.get("nextToken")
                            if not next_token:
                                break
                    except Exception as doc_err:
                        logger.warning(f"List documents failed for KB {kb_id} DS {ds_id}: {doc_err}")
                    ds_details.append({"id": ds_id, "name": ds_name, "document_count": count})
                    total_docs += count
                result["knowledge_bases"].append({
                    "id": kb_id,
                    "key": key,
                    "label": label,
                    "document_count": total_docs,
                    "data_sources": ds_details
                })
            except Exception as e:
                logger.warning(f"KB {kb_id} ({label}): {e}")
                result["knowledge_bases"].append({
                    "id": kb_id,
                    "key": key,
                    "label": label,
                    "document_count": None,
                    "error": str(e),
                    "data_sources": []
                })
        
        return result
    
    def query_knowledge_base(self, query: str, patient_id: Optional[int] = None, max_results: int = 6, knowledge_base_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Query the Bedrock knowledge base for retrieval only (not generation)
        The KB is used only for retrieval - response generation is done by the chatbot
        
        Args:
            query: The query text to search in the knowledge base
            patient_id: Optional patient ID for patient-specific filtering
            max_results: Maximum number of results to retrieve
            knowledge_base_id: Optional KB ID (defaults to KNOWLEDGE_BASE_ID)
            
        Returns:
            Dict with 'success' boolean, 'retrieved_texts' (list of retrieved content), 
            and 'citations' (list of citation metadata)
        """
        if not self.knowledge_base_client:
            return {
                "success": False,
                "error": "Knowledge base client not available"
            }
        
        # Use provided KB ID or default
        kb_id = knowledge_base_id or self.KNOWLEDGE_BASE_ID
        
        try:
            # Optional metadata filter for per-patient scoping
            # Note: API uses tagged union structure with operation types (equals, notEquals, etc.)
            # Use "equals" for exact match on patient_id
            filter_config = None
            if patient_id:
                filter_config = {
                    "equals": {
                        "key": "metadata.patient_id",
                        "value": str(patient_id)
                    }
                }

            # Build retrieval config - filter must be inside vectorSearchConfiguration
            vector_search_config = {
                "numberOfResults": max_results
                # Note: overrideSearchType not supported for retrieve operation
            }
            # Add filter if patient_id is provided - must be inside vectorSearchConfiguration
            # Use "filter" (singular) as required by API
            if filter_config:
                vector_search_config["filter"] = filter_config
            
            retrieval_config = {
                "vectorSearchConfiguration": vector_search_config
            }
            
            # Log the config for debugging
            logger.info(f"🔍 Knowledge base retrieval: KB ID={kb_id}, query='{query[:100]}...', max_results={max_results}, patient_id={patient_id}")
            
            # Retrieve only (no generation) - KB returns relevant documents
            # The retrieve() method takes knowledgeBaseId and retrievalQuery as separate parameters
            # retrievalConfiguration is passed separately
            response = self.knowledge_base_client.retrieve(
                knowledgeBaseId=kb_id,
                retrievalQuery={
                    "text": query
                },
                retrievalConfiguration=retrieval_config
            )
            
            logger.info(f"🔍 KB retrieve() response keys: {list(response.keys()) if response else 'None'}")
            logger.info(f"🔍 KB retrieve() has retrievalResults: {'retrievalResults' in response if response else False}")
            
            # Extract retrieved results
            if response and 'retrievalResults' in response:
                retrieval_results = response['retrievalResults']
                logger.info(f"🔍 KB retrieve() returned {len(retrieval_results)} results")
                logger.info(f"🔍 Processing {len(retrieval_results)} retrieval results")
                retrieved_texts = []
                citations = []
                
                for i, result in enumerate(retrieval_results):
                    logger.info(f"🔍 Result {i+1} keys: {list(result.keys())}")
                    # Extract content
                    content = result.get('content', {}).get('text', '')
                    score = result.get('score', 0.0)  # Relevance score from KB
                    logger.info(f"🔍 Result {i+1} content length: {len(content)} chars, score: {score}")
                    if content:
                        retrieved_texts.append(content)
                    else:
                        logger.warning(f"🔍 Result {i+1} has no content.text field")
                    
                    # Extract citation metadata - match AWS Console format
                    location = result.get('location', {})
                    metadata = result.get('metadata', {})
                    
                    if location:
                        # Extract URI from location or metadata
                        uri = None
                        if location.get('s3Location'):
                            uri = location.get('s3Location', {}).get('uri', '')
                        elif metadata.get('x-amz-bedrock-kb-source-uri'):
                            uri = metadata.get('x-amz-bedrock-kb-source-uri')
                        
                        # Build citation object matching AWS format
                        citation = {
                            "type": location.get('type', 'S3'),
                            "uri": uri or '',
                            "s3Location": location.get('s3Location', {}),
                            "score": score,  # Include relevance score for filtering
                        }
                        
                        # Add metadata fields if available
                        if metadata.get('x-amz-bedrock-kb-document-page-number'):
                            citation["pageNumber"] = metadata.get('x-amz-bedrock-kb-document-page-number')
                        if metadata.get('x-amz-bedrock-kb-chunk-id'):
                            citation["chunkId"] = metadata.get('x-amz-bedrock-kb-chunk-id')
                        
                        citations.append(citation)
                
                # Combine retrieved texts into a single context string
                combined_context = "\n\n".join(retrieved_texts)
                
                # Log retrieval results
                logger.info(f"Knowledge base retrieval successful: {len(retrieved_texts)} results, {len(citations)} citations")
                
                return {
                    "success": True,
                    "response": combined_context,  # Combined retrieved context for use by chatbot
                    "retrieved_texts": retrieved_texts,  # Individual retrieved documents
                    "citations": citations  # Citation metadata
                }
            else:
                logger.warning("Knowledge base returned empty retrieval results")
                return {
                    "success": False,
                    "error": "Empty retrieval results from knowledge base"
                }
                
        except Exception as e:
            logger.error(f"Knowledge base retrieval failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def invoke_model(self, 
                    messages: List[Dict[str, str]], 
                    model: str = None,
                    max_tokens: int = 4000,
                    temperature: float = 0.1,
                    top_p: float = 0.9,
                    patient_id: Optional[int] = None,
                    endpoint: str = 'unknown',
                    use_knowledge_base: bool = False,
                    knowledge_base_query: Optional[str] = None) -> Dict[str, Any]:
        """
        Centralized method for all Bedrock model calls
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            model: Model to use (defaults to DEFAULT_MODEL)
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            top_p: Top-p sampling parameter
            patient_id: Optional patient ID for logging and knowledge base filtering
            endpoint: Endpoint name for logging
            use_knowledge_base: Whether to query knowledge base and enhance prompt
            knowledge_base_query: Optional custom query for knowledge base (defaults to extracting from user messages)
            
        Returns:
            Dict with 'success' boolean and 'response' or 'error' message
        """
        # Check if LLM calls are disabled
        if DISABLE_LLM_CALLS:
            logger.info(f"LLM calls disabled for endpoint: {endpoint}")
            return {
                "success": False,
                "error": "LLM disabled"
            }
        
        if not self.is_available():
            return {
                "success": False,
                "error": "Bedrock service not available"
            }
        
        # Use default model if not specified
        if model is None:
            model = self.DEFAULT_MODEL
        
        # Get model ID
        model_id = self.MODELS.get(model)
        if not model_id:
            return {
                "success": False,
                "error": f"Unknown model: {model}"
            }
        
        # Query knowledge base if enabled
        knowledge_base_context = None
        knowledge_base_citations = []
        logger.info(f"🔍 invoke_model called with use_knowledge_base={use_knowledge_base}, endpoint={endpoint}, knowledge_base_query={knowledge_base_query[:100] if knowledge_base_query else None}...")
        if use_knowledge_base:
            logger.info(f"🔍 Knowledge base is ENABLED for endpoint: {endpoint}")
            try:
                # Extract query from messages if not provided
                if not knowledge_base_query:
                    # Find the last user message to use as query
                    for msg in reversed(messages):
                        if msg.get('role') == 'user':
                            full_content = msg.get('content', '')
                            # Extract just the USER QUESTION section if present, otherwise use last 500 chars
                            if "========== USER QUESTION ==========" in full_content:
                                parts = full_content.split("========== USER QUESTION ==========", 1)
                                if len(parts) == 2:
                                    knowledge_base_query = parts[1].strip()[:500]  # Limit to 500 chars
                                else:
                                    knowledge_base_query = full_content[-500:]  # Last 500 chars
                            else:
                                # If no USER QUESTION marker, try to extract the actual question
                                # Look for common patterns or use last 500 chars
                                knowledge_base_query = full_content[-500:]  # Last 500 chars as fallback
                            break
                
                if knowledge_base_query:
                    logger.info(f"🔍 Querying knowledge base for endpoint: {endpoint}")
                    logger.info(f"🔍 KB Query text (first 200 chars): {knowledge_base_query[:200]}...")
                    # For general knowledge queries, don't filter by patient_id as it may exclude relevant documents
                    # Only use patient_id filter for explicitly patient-specific queries
                    # For now, don't filter by patient_id to allow general KB queries to work
                    kb_result = self.query_knowledge_base(
                        query=knowledge_base_query,
                        patient_id=None,  # Don't filter by patient_id for general queries
                        max_results=6
                    )
                    
                    logger.info(f"🔍 KB query result: success={kb_result.get('success')}, has_error={bool(kb_result.get('error'))}")
                    
                    if kb_result.get('success'):
                        knowledge_base_context = kb_result.get('response', '')
                        all_citations = kb_result.get('citations', [])
                        
                        # Filter citations to only include the most relevant ones, deduplicated by source
                        # Strategy: Deduplicate by URI/filename, then take top 3 unique sources by highest relevance score
                        if all_citations:
                            # Sort by relevance score (higher is better)
                            sorted_citations = sorted(all_citations, key=lambda x: x.get('score', 0.0), reverse=True)
                            
                            # Deduplicate by URI - keep only the highest-scoring citation for each unique source
                            seen_uris = {}
                            unique_citations = []
                            for citation in sorted_citations:
                                uri = citation.get('uri', '') or citation.get('s3Location', {}).get('uri', '')
                                if uri:
                                    # Extract filename for deduplication (handle both full URI and just filename)
                                    # Normalize URI to use as key (remove s3://bucket/ prefix if present)
                                    # Remove s3://bucket/ prefix and get just the filename
                                    uri_normalized = re.sub(r'^s3://[^/]+/', '', uri)
                                    uri_key = uri_normalized.split('/')[-1] if '/' in uri_normalized else uri_normalized
                                    # Use full URI path as fallback key if needed
                                    uri_key = uri_key or uri_normalized
                                    
                                    # If we haven't seen this source, or this citation has a higher score
                                    if uri_key not in seen_uris:
                                        seen_uris[uri_key] = citation
                                        unique_citations.append(citation)
                                    elif citation.get('score', 0.0) > seen_uris[uri_key].get('score', 0.0):
                                        # Replace with higher-scoring citation
                                        idx = unique_citations.index(seen_uris[uri_key])
                                        unique_citations[idx] = citation
                                        seen_uris[uri_key] = citation
                            
                            # Take top 3 unique sources (they're already sorted by score)
                            knowledge_base_citations = unique_citations[:3]
                            
                            logger.info(f"✅ Filtered citations: {len(knowledge_base_citations)} unique sources from {len(all_citations)} total citations (deduplicated, showing top 3)")
                            if knowledge_base_citations:
                                scores = [c.get('score', 0.0) for c in knowledge_base_citations]
                                logger.info(f"   Citation scores: {scores}")
                                logger.info(f"   Unique URIs: {[c.get('uri', 'N/A').split('/').pop() for c in knowledge_base_citations]}")
                        else:
                            knowledge_base_citations = []
                        
                        logger.info(f"✅ Knowledge base query SUCCESSFUL: {len(knowledge_base_context)} chars retrieved, {len(knowledge_base_citations)} citations")
                        logger.info(f"Knowledge base response preview: {knowledge_base_context[:300]}...")
                        if knowledge_base_citations:
                            logger.info(f"Knowledge base citations (filtered): {json.dumps(knowledge_base_citations, indent=2)}")
                    else:
                        error_msg = kb_result.get('error', 'Unknown error')
                        logger.warning(f"❌ Knowledge base query FAILED: {error_msg}")
                        logger.warning(f"Full KB error details: {json.dumps(kb_result, indent=2)}")
                else:
                    logger.warning("⚠️ Knowledge base enabled but no query available - could not extract query from messages")
            except Exception as kb_error:
                import traceback
                logger.error(f"❌ Exception while querying knowledge base: {kb_error}")
                logger.error(f"Full traceback: {traceback.format_exc()}")
        else:
            logger.info(f"ℹ️ Knowledge base is DISABLED for endpoint: {endpoint}")
        
        # Enhance messages with knowledge base context if available
        enhanced_messages = messages.copy()
        if knowledge_base_context:
            # Find the last user message and enhance it
            for i in range(len(enhanced_messages) - 1, -1, -1):
                if enhanced_messages[i].get('role') == 'user':
                    original_content = enhanced_messages[i].get('content', '')
                    # Insert KB context following the framework: PATIENT DATA -> KNOWLEDGE CONTEXT -> USER QUESTION
                    # Match the simple framework pattern
                    if "========== USER QUESTION ==========" in original_content or "USER QUESTION:" in original_content:
                        # Split at USER QUESTION to insert KB context before it
                        separator = "========== USER QUESTION ==========" if "========== USER QUESTION ==========" in original_content else "USER QUESTION:"
                        parts = original_content.split(separator, 1)
                        if len(parts) == 2:
                            enhanced_content = f"""{parts[0]}

KNOWLEDGE CONTEXT:
{knowledge_base_context}

{separator}{parts[1]}"""
                        else:
                            enhanced_content = f"""{original_content}

KNOWLEDGE CONTEXT:
{knowledge_base_context}"""
                    else:
                        # If no USER QUESTION marker, append KB context at the end
                        enhanced_content = f"""{original_content}

KNOWLEDGE CONTEXT:
{knowledge_base_context}"""
                    enhanced_messages[i] = {
                        'role': 'user',
                        'content': enhanced_content
                    }
                    break
        
        try:
            # Separate legacy system prompts to comply with Bedrock Messages API
            system_prompt_blocks = []
            filtered_messages = []
            for msg in enhanced_messages:
                if msg.get("role") == "system":
                    content = msg.get("content")
                    if content:
                        system_prompt_blocks.append(str(content))
                else:
                    filtered_messages.append(msg)
            enhanced_messages = filtered_messages

            # Prepare payload with enhanced messages (includes knowledge base context if available)
            payload = {
                "anthropic_version": "bedrock-2023-05-31",
                "messages": enhanced_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p
            }
            
            # Check if prompt is too large for Claude 3.7
            prompt_length = len(str(enhanced_messages))
            MAX_PROMPT_LENGTH = 500000  # ~125k tokens
            
            if prompt_length > MAX_PROMPT_LENGTH:
                logger.info(f"Large prompt detected ({prompt_length} chars), will try Claude 4.0 fallback")
                # Try Claude 4.0 as fallback for large prompts
                return self._try_claude4_fallback(enhanced_messages, max_tokens, temperature, top_p, patient_id, endpoint)
            
            # Only use Extended Thinking Mode for very large/complex requests
            # This is more cost-effective than using it for all requests
            if model == "claude_37_sonnet" and max_tokens > 8000:
                payload["thinking"] = "extended"
                logger.info(f"Using Extended Thinking Mode for large request (max_tokens: {max_tokens})")

            if system_prompt_blocks:
                payload["system"] = "\n\n".join(system_prompt_blocks)
            
            # Log the request
            prompt_text = enhanced_messages[0].get('content', '') if enhanced_messages else ''
            user_context = self._get_user_context()
            
            # Safely create log entry
            try:
                log_entry = {
                    'llm_request': {
                        'patient_id': patient_id,
                        'model': model,
                        'model_id': model_id,
                        'prompt_length': len(prompt_text),
                        'prompt_preview': prompt_text[:200] + '...' if len(prompt_text) > 200 else prompt_text,
                        'max_tokens': max_tokens,
                        'temperature': temperature,
                        'top_p': top_p,
                        'endpoint': endpoint
                    },
                    'user_context': user_context
                }
                llm_call_logger.info(f"LLM_REQUEST: {json.dumps(log_entry, indent=2, ensure_ascii=False)}")
            except Exception as log_error:
                logger.warning(f"Failed to log LLM request: {log_error}")
                llm_call_logger.info(f"LLM_REQUEST: Basic info - Patient: {patient_id}, Model: {model}, Endpoint: {endpoint}")
            
            # logger.info(f"Making Bedrock API call with model {model} ({model_id})")  # Reduced verbosity
            
            # Generate session ID for logging
            session_id = str(uuid.uuid4())
            
            # Log prompt to database
            if LLM_LOGGING_ENABLED:
                try:
                    LLMLoggerService.log_prompt(
                        session_id=session_id,
                        model_name=model,
                        model_id=model_id,
                        prompt_content=messages,
                        patient_id=patient_id,
                        page_endpoint=endpoint,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=top_p
                    )
                except Exception as log_err:
                    logger.warning(f"Failed to log prompt to database: {log_err}")
            
            # Start timing
            start_time = time.time()
            
            # Make the API call
            response = self.client.invoke_model(
                modelId=model_id,
                body=json.dumps(payload)
            )
            
            # Calculate response time
            response_time_ms = int((time.time() - start_time) * 1000)
            
            # Read and parse response
            response_body = response["body"].read()
            if isinstance(response_body, bytes):
                response_body = response_body.decode('utf-8')
            
            result = json.loads(response_body)
            
            # Extract Claude's response
            if result.get("content") and len(result["content"]) > 0:
                answer = result["content"][0]["text"]
                # logger.info("Bedrock API call successful")  # Reduced verbosity
                
                # Log response to database
                if LLM_LOGGING_ENABLED:
                    try:
                        LLMLoggerService.log_response(
                            session_id=session_id,
                            response_text=answer,
                            status='success',
                            response_time_ms=response_time_ms,
                            response_data=result
                        )
                    except Exception as log_err:
                        logger.warning(f"Failed to log response to database: {log_err}")
                
                # Log successful response
                try:
                    response_log_entry = {
                        'llm_response': {
                            'patient_id': patient_id,
                            'model': model,
                            'model_id': model_id,
                            'success': True,
                            'response_length': len(answer),
                            'response_preview': answer[:200] + '...' if len(answer) > 200 else answer,
                            'response_tokens': len(answer.split()),
                            'endpoint': endpoint
                        },
                        'user_context': user_context
                    }
                    llm_call_logger.info(f"LLM_RESPONSE_SUCCESS: {json.dumps(response_log_entry, indent=2, ensure_ascii=False)}")
                except Exception as log_error:
                    logger.warning(f"Failed to log LLM response: {log_error}")
                    llm_call_logger.info(f"LLM_RESPONSE_SUCCESS: Basic info - Patient: {patient_id}, Model: {model}, Success: True")
                
                # Include knowledge base citations if available
                response_data = {
                    "success": True,
                    "response": answer,
                    "model_used": model
                }
                if knowledge_base_citations:
                    response_data["knowledge_base_citations"] = knowledge_base_citations
                    logger.info(f"Including {len(knowledge_base_citations)} citations in response")
                
                return response_data
            else:
                logger.warning("Bedrock returned empty response")
                
                # Log empty response to database
                if LLM_LOGGING_ENABLED:
                    try:
                        LLMLoggerService.log_response(
                            session_id=session_id,
                            response_text='',
                            status='error',
                            response_time_ms=response_time_ms,
                            error_message='Empty response from model'
                        )
                    except Exception as log_err:
                        logger.warning(f"Failed to log error to database: {log_err}")
                
                # Log empty response
                try:
                    error_log_entry = {
                        'llm_response': {
                            'patient_id': patient_id,
                            'model': model,
                            'model_id': model_id,
                            'success': False,
                            'error': 'Empty response from model',
                            'response_length': 0,
                            'endpoint': endpoint
                        },
                        'user_context': user_context
                    }
                    llm_call_logger.error(f"LLM_RESPONSE_FAILURE: {json.dumps(error_log_entry, indent=2, ensure_ascii=False)}")
                except Exception as log_error:
                    logger.warning(f"Failed to log LLM error: {log_error}")
                    llm_call_logger.error(f"LLM_RESPONSE_FAILURE: Basic info - Patient: {patient_id}, Model: {model}, Error: Empty response")
                
                return {
                    "success": False,
                    "error": "Empty response from model"
                }
                
        except Exception as e:
            logger.error(f"Bedrock API call failed: {e}")
            
            # Log exception to database
            if LLM_LOGGING_ENABLED:
                try:
                    # Determine status based on error type
                    error_str = str(e).lower()
                    if 'throttl' in error_str:
                        status = 'throttled'
                    elif 'timeout' in error_str or 'timed out' in error_str:
                        status = 'timeout'
                    else:
                        status = 'error'
                    
                    LLMLoggerService.log_response(
                        session_id=session_id,
                        response_text='',
                        status=status,
                        response_time_ms=None,
                        error_message=str(e)
                    )
                except Exception as log_err:
                    logger.warning(f"Failed to log exception to database: {log_err}")
            
            # Log error response
            try:
                exception_log_entry = {
                    'llm_response': {
                        'patient_id': patient_id,
                        'model': model,
                        'model_id': model_id,
                        'success': False,
                        'error': str(e),
                        'response_length': 0,
                        'exception_type': type(e).__name__,
                        'endpoint': endpoint
                    },
                    'user_context': user_context
                }
                llm_call_logger.error(f"LLM_RESPONSE_FAILURE: {json.dumps(exception_log_entry, indent=2, ensure_ascii=False)}")
            except Exception as log_error:
                logger.warning(f"Failed to log LLM exception: {log_error}")
                llm_call_logger.error(f"LLM_RESPONSE_FAILURE: Basic info - Patient: {patient_id}, Model: {model}, Error: {str(e)}")
            
            return {
                "success": False,
                "error": str(e)
            }
    
    def converse_model(self, 
                      messages: List[Dict[str, Any]], 
                      model: str = None,
                      inference_config: Dict[str, Any] = None,
                      patient_id: str = None,
                      endpoint: str = "converse") -> Dict[str, Any]:
        """
        Use the Converse API for models that support it (like Claude 4.0 with PDF support)
        """
        # Check if LLM calls are disabled
        if DISABLE_LLM_CALLS:
            logger.info(f"LLM calls disabled for converse endpoint: {endpoint}")
            return {
                "success": False,
                "error": "LLM disabled"
            }
        
        try:
            # Use default model if not specified
            if model is None:
                model = self.DEFAULT_MODEL
            
            # Get model ID
            model_id = self.MODELS.get(model)
            if not model_id:
                raise ValueError(f"Unknown model: {model}")
            
            # Default inference config
            if inference_config is None:
                inference_config = {
                    "maxTokens": 4000,
                    "temperature": 0.1
                }
            
            # Get user context for logging
            user_context = self._get_user_context()
            
            # Log the request
            try:
                log_entry = {
                    'llm_request': {
                        'patient_id': patient_id,
                        'model': model,
                        'model_id': model_id,
                        'messages_count': len(messages),
                        'inference_config': inference_config,
                        'endpoint': endpoint
                    },
                    'user_context': user_context
                }
                llm_call_logger.info(f"LLM_CONVERSE_REQUEST: {json.dumps(log_entry, indent=2, ensure_ascii=False)}")
            except Exception as log_error:
                logger.warning(f"Failed to log Converse request: {log_error}")
                llm_call_logger.info(f"LLM_CONVERSE_REQUEST: Basic info - Patient: {patient_id}, Model: {model}, Endpoint: {endpoint}")
            
            # logger.info(f"Making Bedrock Converse API call with model {model} ({model_id})")  # Reduced verbosity
            
            # Make the Converse API call
            response = self.client.converse(
                modelId=model_id,
                messages=messages,
                inferenceConfig=inference_config
            )
            
            # Extract response content
            if response.get("output") and response["output"].get("message"):
                content = response["output"]["message"]["content"]
                if content and len(content) > 0:
                    answer = content[0].get("text", "")
                    # logger.info("Bedrock Converse API call successful")  # Reduced verbosity
                    
                    # Log successful response
                    try:
                        response_log_entry = {
                            'llm_response': {
                                'patient_id': patient_id,
                                'model': model,
                                'model_id': model_id,
                                'success': True,
                                'response_length': len(answer),
                                'response_preview': answer[:200] + '...' if len(answer) > 200 else answer,
                                'endpoint': endpoint
                            },
                            'user_context': user_context
                        }
                        llm_call_logger.info(f"LLM_CONVERSE_RESPONSE_SUCCESS: {json.dumps(response_log_entry, indent=2, ensure_ascii=False)}")
                    except Exception as log_error:
                        logger.warning(f"Failed to log Converse response: {log_error}")
                        llm_call_logger.info(f"LLM_CONVERSE_RESPONSE_SUCCESS: Basic info - Patient: {patient_id}, Model: {model}, Response length: {len(answer)}")
                    
                    return {
                        "success": True,
                        "content": answer,
                        "model": model,
                        "model_id": model_id
                    }
                else:
                    logger.warning("Empty content in Converse response")
                    return {
                        "success": False,
                        "error": "Empty content in response"
                    }
            else:
                logger.warning("No output in Converse response")
                return {
                    "success": False,
                    "error": "No output in response"
                }
                
        except Exception as e:
            logger.error(f"Bedrock Converse API call failed: {e}")
            
            # Log error response
            try:
                exception_log_entry = {
                    'llm_response': {
                        'patient_id': patient_id,
                        'model': model,
                        'model_id': model_id,
                        'success': False,
                        'error': str(e),
                        'response_length': 0,
                        'exception_type': type(e).__name__,
                        'endpoint': endpoint
                    },
                    'user_context': user_context
                }
                llm_call_logger.error(f"LLM_CONVERSE_RESPONSE_FAILURE: {json.dumps(exception_log_entry, indent=2, ensure_ascii=False)}")
            except Exception as log_error:
                logger.warning(f"Failed to log Converse exception: {log_error}")
                llm_call_logger.error(f"LLM_CONVERSE_RESPONSE_FAILURE: Basic info - Patient: {patient_id}, Model: {model}, Error: {str(e)}")
            
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_available_models(self) -> List[str]:
        """Get list of available models"""
        return list(self.MODELS.keys())
    
    def get_model_id(self, model: str) -> Optional[str]:
        """Get the model ID for a given model name"""
        return self.MODELS.get(model)
    
    def _try_claude4_fallback(self, messages, max_tokens, temperature, top_p, patient_id, endpoint):
        """Try Claude 4.0 as fallback for large prompts"""
        try:
            logger.info("Attempting Claude 4.0 Sonnet fallback for large prompt")
            
            # Use Claude 4.0 Sonnet model (better availability)
            model_id = self.MODELS["claude_4_sonnet"]
            
            # Prepare payload for Claude 4.0
            payload = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": messages,
                "temperature": temperature,
                "top_p": top_p
            }
            
            # Make the API call
            response = self.client.invoke_model(
                modelId=model_id,
                body=json.dumps(payload)
            )
            
            # Parse response
            response_body = json.loads(response['body'].read())
            content = response_body['content'][0]['text']
            
            logger.info("Claude 4.0 fallback successful")
            return {'content': content}
            
        except Exception as e:
            logger.error(f"Claude 4.0 fallback also failed: {str(e)}")
            return None

# Global instance
bedrock_service = BedrockService()

def get_bedrock_service() -> BedrockService:
    """Get the global Bedrock service instance"""
    return bedrock_service

def invoke_bedrock_model(messages: List[Dict[str, str]], 
                        model: str = None,
                        max_tokens: int = 1000,
                        temperature: float = 0.1,
                        top_p: float = 0.9) -> Dict[str, Any]:
    """
    Convenience function for making Bedrock calls
    
    Args:
        messages: List of message dictionaries with 'role' and 'content'
        model: Model to use (defaults to DEFAULT_MODEL)
        max_tokens: Maximum tokens to generate
        temperature: Sampling temperature
        top_p: Top-p sampling parameter
        
    Returns:
        Dict with 'success' boolean and 'response' or 'error' message
    """
    return bedrock_service.invoke_model(
        messages=messages,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p
    )
