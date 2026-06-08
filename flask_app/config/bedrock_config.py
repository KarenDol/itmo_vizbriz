"""
Bedrock Configuration and Throttling Management
Centralized configuration for AWS Bedrock API calls with advanced throttling handling
"""

import boto3
import time
import random
import logging
from botocore.config import Config
from botocore.exceptions import ClientError
import os
from datetime import datetime, timedelta
from collections import defaultdict
import threading
import json
import uuid
from flask_app.services.bedrock_service import BedrockService

logger = logging.getLogger(__name__)

class BedrockThrottlingManager:
    """Advanced throttling management for Bedrock API calls"""
    
    def __init__(self):
        self.request_timestamps = defaultdict(list)
        self.lock = threading.Lock()
        
        # Rate limiting configuration for us-west-2 (1 request per 12 seconds)
        self.requests_per_minute = 5   # 5 requests per minute (conservative)
        self.requests_per_second = 0.083  # 1 request per 12 seconds
        self.max_concurrent_requests = 1  # Single concurrent request
        
        # Retry configuration - AGGRESSIVE BACKOFF for us-west-2
        self.max_retries = 10  # Increased for better reliability
        self.base_delay = 12.0  # Base delay of 12 seconds (quota period)
        self.max_delay = 300.0  # Max delay of 5 minutes
        self.jitter_factor = 0.3  # Increased jitter to prevent thundering herd
        
        # Circuit breaker - MORE SENSITIVE
        self.failure_threshold = 5  # Reduced from 10
        self.recovery_timeout = 600  # Increased to 10 minutes
        self.circuit_breaker_state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.last_failure_time = None
        self.failure_count = 0
        
    def _add_jitter(self, delay):
        """Add random jitter to delay to prevent thundering herd"""
        jitter = delay * self.jitter_factor * random.random()
        return delay + jitter
    
    def _exponential_backoff(self, attempt):
        """Calculate exponential backoff delay"""
        delay = min(self.base_delay * (2 ** attempt), self.max_delay)
        return self._add_jitter(delay)
    
    def _check_rate_limit(self):
        """Check if we're within rate limits for us-west-2 (1 request per 12 seconds)"""
        now = time.time()
        minute_ago = now - 60
        twelve_seconds_ago = now - 12  # 12-second window for us-west-2 quota
        
        with self.lock:
            # Clean old timestamps
            self.request_timestamps['minute'] = [ts for ts in self.request_timestamps['minute'] if ts > minute_ago]
            self.request_timestamps['twelve_seconds'] = [ts for ts in self.request_timestamps['twelve_seconds'] if ts > twelve_seconds_ago]
            
            # Check limits
            if len(self.request_timestamps['minute']) >= self.requests_per_minute:
                return False, "Minute rate limit exceeded"
            
            if len(self.request_timestamps['twelve_seconds']) >= 1:  # Only 1 request per 12 seconds
                return False, "Twelve-second rate limit exceeded (us-west-2 quota)"
            
            return True, None
    
    def _record_request(self):
        """Record a successful request"""
        now = time.time()
        with self.lock:
            self.request_timestamps['minute'].append(now)
            self.request_timestamps['twelve_seconds'].append(now)
    
    def _check_circuit_breaker(self):
        """Check circuit breaker state"""
        now = time.time()
        
        with self.lock:
            if self.circuit_breaker_state == "OPEN":
                if now - self.last_failure_time > self.recovery_timeout:
                    self.circuit_breaker_state = "HALF_OPEN"
                    logger.info("Circuit breaker moved to HALF_OPEN")
                else:
                    return False, "Circuit breaker is OPEN"
            
            return True, None
    
    def _record_failure(self):
        """Record a failure for circuit breaker"""
        now = time.time()
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = now
            
            if self.failure_count >= self.failure_threshold:
                self.circuit_breaker_state = "OPEN"
                logger.warning(f"Circuit breaker opened after {self.failure_count} failures")
    
    def _record_success(self):
        """Record a success for circuit breaker"""
        with self.lock:
            if self.circuit_breaker_state == "HALF_OPEN":
                self.circuit_breaker_state = "CLOSED"
                self.failure_count = 0
                logger.info("Circuit breaker closed after successful request")

class BedrockCircuitBreaker:
    """Circuit breaker pattern for Bedrock API calls"""
    
    def __init__(self):
        self.failure_threshold = 5
        self.recovery_timeout = 600  # 10 minutes
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.last_failure_time = None
        self.failure_count = 0
        self.lock = threading.Lock()
    
    def is_open(self):
        """Check if circuit breaker is open"""
        now = time.time()
        
        with self.lock:
            if self.state == "OPEN":
                if now - self.last_failure_time > self.recovery_timeout:
                    self.state = "HALF_OPEN"
                    logger.info("Circuit breaker moved to HALF_OPEN")
                else:
                    return True
            
            return False
    
    def record_success(self):
        """Record a successful request"""
        with self.lock:
            if self.state == "HALF_OPEN":
                self.state = "CLOSED"
                self.failure_count = 0
                logger.info("Circuit breaker closed after successful request")
    
    def record_failure(self):
        """Record a failure"""
        now = time.time()
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = now
            
            if self.failure_count >= self.failure_threshold:
                self.state = "OPEN"
                logger.warning(f"Circuit breaker opened after {self.failure_count} failures")

class BedrockPromptLogger:
    """Logs prompts and responses to S3 for testing and reviewing"""
    
    def __init__(self):
        self.s3_client = boto3.client('s3')
        self.bucket_name = 'vizbrizpatients'
        self.prompts_prefix = 'prompts/'
        
    def generate_session_id(self):
        """Generate a unique session ID for linking prompts and responses"""
        return str(uuid.uuid4())
    
    def save_prompt(self, session_id, prompt_data, metadata=None):
        """Save prompt to S3"""
        try:
            timestamp = datetime.utcnow().isoformat()
            filename = f"{self.prompts_prefix}prompts/{session_id}_{timestamp}.json"
            
            prompt_record = {
                'session_id': session_id,
                'timestamp': timestamp,
                'prompt': prompt_data,
                'metadata': metadata or {}
            }
            
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=filename,
                Body=json.dumps(prompt_record, indent=2),
                ContentType='application/json'
            )
            
            logger.info(f"Prompt saved to S3: {filename}")
            return filename
            
        except Exception as e:
            logger.error(f"Error saving prompt to S3: {e}")
            return None
    
    def save_response(self, session_id, response_data, metadata=None):
        """Save response to S3"""
        try:
            timestamp = datetime.utcnow().isoformat()
            filename = f"{self.prompts_prefix}responses/{session_id}_{timestamp}.json"
            
            # Handle response data properly - extract the actual response content
            if isinstance(response_data, dict):
                # If response_data is already a dict, extract the body content
                if 'body' in response_data:
                    # Convert bytes to string if needed
                    body_content = response_data['body']
                    if isinstance(body_content, bytes):
                        body_content = body_content.decode('utf-8')
                    response_content = body_content
                else:
                    # Use the entire response_data as content
                    response_content = response_data
            else:
                # If it's not a dict, convert to string
                response_content = str(response_data)
            
            response_record = {
                'session_id': session_id,
                'timestamp': timestamp,
                'response': response_content,
                'metadata': metadata or {}
            }
            
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=filename,
                Body=json.dumps(response_record, indent=2, default=str),
                ContentType='application/json'
            )
            
            logger.info(f"Response saved to S3: {filename}")
            return filename
            
        except Exception as e:
            logger.error(f"Error saving response to S3: {e}")
            return None
    
    def save_prompt_response_pair(self, prompt_data, response_data, metadata=None):
        """Save both prompt and response with linked session ID"""
        session_id = self.generate_session_id()
        
        prompt_file = self.save_prompt(session_id, prompt_data, metadata)
        response_file = self.save_response(session_id, response_data, metadata)
        
        return {
            'session_id': session_id,
            'prompt_file': prompt_file,
            'response_file': response_file
        }
    
    def get_session_files(self, session_id):
        """Retrieve all files for a specific session ID"""
        try:
            # List objects with session ID prefix
            prompt_objects = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=f"{self.prompts_prefix}prompts/{session_id}"
            )
            
            response_objects = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=f"{self.prompts_prefix}responses/{session_id}"
            )
            
            files = {
                'prompts': [],
                'responses': []
            }
            
            # Get prompt files
            if 'Contents' in prompt_objects:
                for obj in prompt_objects['Contents']:
                    response = self.s3_client.get_object(
                        Bucket=self.bucket_name,
                        Key=obj['Key']
                    )
                    files['prompts'].append(json.loads(response['Body'].read()))
            
            # Get response files
            if 'Contents' in response_objects:
                for obj in response_objects['Contents']:
                    response = self.s3_client.get_object(
                        Bucket=self.bucket_name,
                        Key=obj['Key']
                    )
                    files['responses'].append(json.loads(response['Body'].read()))
            
            return files
            
        except Exception as e:
            logger.error(f"Error retrieving session files: {e}")
            return None
    
    def list_recent_sessions(self, hours=24):
        """List recent sessions within specified hours"""
        try:
            cutoff_time = datetime.utcnow().timestamp() - (hours * 3600)
            
            # List all prompt files
            prompt_objects = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=f"{self.prompts_prefix}prompts/"
            )
            
            sessions = {}
            
            if 'Contents' in prompt_objects:
                for obj in prompt_objects['Contents']:
                    # Extract session ID from filename
                    filename = obj['Key'].split('/')[-1]
                    session_id = filename.split('_')[0]
                    
                    # Check if file is recent enough
                    if obj['LastModified'].timestamp() > cutoff_time:
                        if session_id not in sessions:
                            sessions[session_id] = {
                                'prompt_files': [],
                                'response_files': [],
                                'last_modified': obj['LastModified']
                            }
                        sessions[session_id]['prompt_files'].append(obj['Key'])
            
            # List all response files
            response_objects = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=f"{self.prompts_prefix}responses/"
            )
            
            if 'Contents' in response_objects:
                for obj in response_objects['Contents']:
                    filename = obj['Key'].split('/')[-1]
                    session_id = filename.split('_')[0]
                    
                    if obj['LastModified'].timestamp() > cutoff_time:
                        if session_id not in sessions:
                            sessions[session_id] = {
                                'prompt_files': [],
                                'response_files': [],
                                'last_modified': obj['LastModified']
                            }
                        sessions[session_id]['response_files'].append(obj['Key'])
            
            return sessions
            
        except Exception as e:
            logger.error(f"Error listing recent sessions: {e}")
            return {}
    
    def analyze_session_performance(self, session_id):
        """Analyze performance metrics for a specific session"""
        try:
            files = self.get_session_files(session_id)
            if not files:
                return None
            
            analysis = {
                'session_id': session_id,
                'total_requests': len(files['prompts']),
                'total_responses': len(files['responses']),
                'response_times': [],
                'errors': [],
                'success_rate': 0
            }
            
            # Analyze responses
            for response in files['responses']:
                if 'response_time' in response.get('metadata', {}):
                    analysis['response_times'].append(response['metadata']['response_time'])
                
                if 'error_code' in response.get('metadata', {}):
                    analysis['errors'].append({
                        'error_code': response['metadata']['error_code'],
                        'error_message': response['metadata'].get('error_message', '')
                    })
            
            # Calculate success rate
            if analysis['total_requests'] > 0:
                analysis['success_rate'] = (analysis['total_responses'] / analysis['total_requests']) * 100
            
            # Calculate average response time
            if analysis['response_times']:
                analysis['avg_response_time'] = sum(analysis['response_times']) / len(analysis['response_times'])
                analysis['min_response_time'] = min(analysis['response_times'])
                analysis['max_response_time'] = max(analysis['response_times'])
            
            return analysis
            
        except Exception as e:
            logger.error(f"Error analyzing session performance: {e}")
            return None

class BedrockClient:
    """Enhanced Bedrock client with throttling management"""
    
    def __init__(self, region_name=None):
        self.region_name = region_name or os.getenv('BEDROCK_AWS_REGION', 'us-west-2')
        self.throttling_manager = BedrockThrottlingManager()
        self.circuit_breaker = BedrockCircuitBreaker()
        self.client = None
        self.prompt_logger = BedrockPromptLogger()
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize the Bedrock client with proper configuration"""
        try:
            config = Config(
                region_name=self.region_name,
                retries={
                    'max_attempts': 3,  # Allow some basic retries at boto3 level
                    'mode': 'adaptive'
                },
                connect_timeout=30,
                read_timeout=60,
                max_pool_connections=5
            )
            
            self.client = boto3.client('bedrock-runtime', config=config)
            logger.info(f"Bedrock client initialized for region: {self.region_name}")
            
        except Exception as e:
            logger.error(f"Error initializing Bedrock client: {e}")
            self.client = None
    
    def invoke_model(self, model_id, body, content_type="application/json", accept="application/json"):
        """Invoke Bedrock model with throttling management and prompt logging"""
        
        # Check circuit breaker first
        if self.circuit_breaker.is_open():
            raise Exception("Circuit breaker is open - Bedrock service is unavailable")
        
        # Check throttling limits
        can_proceed, reason = self.throttling_manager._check_rate_limit()
        if not can_proceed:
            raise Exception(f"Rate limit exceeded: {reason}")
        
        # Prepare metadata for logging
        metadata = {
            'model_id': model_id,
            'content_type': content_type,
            'accept': accept,
            'region': self.region_name,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        # Generate session ID early for error handling
        session_id = self.prompt_logger.generate_session_id()
        
        try:
            # Log the prompt before sending
            prompt_file = self.prompt_logger.save_prompt(session_id, body, metadata)
            
            # Record request start time
            start_time = time.time()
            
            # Use BedrockService for model ID
            bedrock_service = BedrockService()
            model_id = bedrock_service.MODELS[bedrock_service.DEFAULT_MODEL]
            
            # Make the API call
            response = self.client.invoke_model(
                modelId=model_id,
                contentType=content_type,
                accept=accept,
                body=body
            )
            
            # Calculate response time
            response_time = time.time() - start_time
            
            # Record successful request
            self.throttling_manager._record_request()
            self.throttling_manager._record_success()
            self.circuit_breaker.record_success()
            
            # Read the response body once
            response_body = response["body"].read()
            
            # Create a copy of the response for logging with the actual content
            response_for_logging = {
                'ResponseMetadata': response.get('ResponseMetadata', {}),
                'contentType': response.get('contentType'),
                'body': response_body  # Actual content, not StreamingBody
            }
            
            # Log the response
            response_metadata = {
                **metadata,
                'response_time': response_time,
                'status_code': response.get('ResponseMetadata', {}).get('HTTPStatusCode', 'unknown')
            }
            
            response_file = self.prompt_logger.save_response(session_id, response_for_logging, response_metadata)
            
            # Return the response with the body content
            return {
                'ResponseMetadata': response.get('ResponseMetadata', {}),
                'contentType': response.get('contentType'),
                'body': response_body
            }
            
            logger.info(f"Bedrock request successful - Session ID: {session_id}, Response time: {response_time:.2f}s")
            logger.info(f"Prompt logged: {prompt_file}, Response logged: {response_file}")
            
            return response
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code in ['ThrottlingException', 'TooManyRequestsException', 'ThrottledException']:
                # Handle throttling
                self.throttling_manager._record_failure()
                self.circuit_breaker.record_failure()
                
                # Log the failed request
                error_metadata = {
                    **metadata,
                    'error_code': error_code,
                    'error_message': str(e)
                }
                self.prompt_logger.save_prompt(session_id, body, error_metadata)
                
                logger.warning(f"Bedrock throttling detected: {error_code}")
                raise Exception(f"Bedrock throttling: {error_code}")
            else:
                # Log other errors
                error_metadata = {
                    **metadata,
                    'error_code': error_code,
                    'error_message': str(e)
                }
                self.prompt_logger.save_prompt(session_id, body, error_metadata)
                raise e
                
        except Exception as e:
            # Log generic errors
            error_metadata = {
                **metadata,
                'error_type': type(e).__name__,
                'error_message': str(e)
            }
            self.prompt_logger.save_prompt(session_id, body, error_metadata)
            
            if "throttling" in str(e).lower() or "too many requests" in str(e).lower():
                self.throttling_manager._record_failure()
                self.circuit_breaker.record_failure()
                logger.warning(f"Bedrock throttling detected: {e}")
                raise Exception(f"Bedrock throttling: {e}")
            else:
                self.circuit_breaker.record_failure()
                logger.error(f"Bedrock request failed: {e}")
                raise e

# Global Bedrock client instance
bedrock_client = BedrockClient()

def get_bedrock_client():
    """Get the global Bedrock client instance"""
    return bedrock_client

# Fallback responses for when Bedrock is unavailable
FALLBACK_RESPONSES = {
    "patient_summary": {
        "clinical": "Patient status summary temporarily unavailable. Please check back in a few minutes.",
        "operational": "Workflow status temporarily unavailable. Please refresh the page."
    },
    "guidance": "AI guidance is temporarily unavailable. Please follow the standard workflow steps below.",
    "chat": "Dr. Briz is temporarily busy. Please try again in a few minutes or contact support if urgent."
}

def get_fallback_response(use_case="chat"):
    """Get appropriate fallback response for use case"""
    return FALLBACK_RESPONSES.get(use_case, FALLBACK_RESPONSES["chat"])

def rate_limit_calls(func):
    """Decorator to rate limit function calls"""
    last_call_time = {}
    
    def wrapper(*args, **kwargs):
        func_name = func.__name__
        now = time.time()
        
        # Check if enough time has passed since last call
        if func_name in last_call_time:
            time_since_last = now - last_call_time[func_name]
            if time_since_last < 12.5:  # Minimum 12.5 seconds between calls
                sleep_time = 12.5 - time_since_last
                logger.info(f"Rate limiting: waiting {sleep_time:.2f}s before next call to {func_name}")
                time.sleep(sleep_time)
        
        last_call_time[func_name] = time.time()
        return func(*args, **kwargs)
    
    return wrapper

@rate_limit_calls
def query_bedrock_claude_enhanced(messages, max_tokens=300, temperature=0.2, top_p=0.9, patient_id=None, endpoint='bedrock_config', use_knowledge_base=False, knowledge_base_query=None):
    """
    Enhanced Bedrock Claude query - now routes through BedrockService for logging
    Knowledge base is opt-in (use_knowledge_base=True to enable)
    
    Args:
        messages: List of message dictionaries
        max_tokens: Maximum tokens to generate
        temperature: Sampling temperature
        top_p: Top-p sampling parameter
        patient_id: Optional patient ID for logging and KB filtering
        endpoint: Endpoint name for logging
        use_knowledge_base: Whether to query knowledge base (default: True)
        knowledge_base_query: Optional custom query for KB (defaults to extracting from user messages)
    """
    try:
        # Use BedrockService which handles logging automatically
        bedrock_service = BedrockService()
        
        # Convert to BedrockService format if needed
        formatted_messages = []
        for msg in messages:
            if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                formatted_messages.append(msg)
            else:
                # Handle legacy format
                formatted_messages.append({"role": "user", "content": str(msg)})
        
        # Call BedrockService.invoke_model which logs automatically and optionally uses knowledge base
        result = bedrock_service.invoke_model(
            messages=formatted_messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            patient_id=patient_id,
            endpoint=endpoint,
            use_knowledge_base=use_knowledge_base,
            knowledge_base_query=knowledge_base_query
        )
        
        # Return in expected format - include knowledge base citations if available
        if result.get("success"):
            response_data = {"success": True, "response": result.get("response")}
            # Include knowledge base citations if available
            if "knowledge_base_citations" in result:
                response_data["knowledge_base_citations"] = result.get("knowledge_base_citations", [])
                logger.info(f"Passing through {len(response_data.get('knowledge_base_citations', []))} knowledge base citations")
            return response_data
        else:
            logger.warning(f"Bedrock call failed: {result.get('error')}")
            return {"success": False, "message": result.get("error", get_fallback_response("chat"))}
            
    except Exception as e:
        logger.error(f"Bedrock API call failed: {e}")
        # Return appropriate fallback based on error type
        if "throttling" in str(e).lower() or "too many requests" in str(e).lower():
            return {"success": False, "message": "Service temporarily busy. Please try again in a few minutes."}
        else:
            return {"success": False, "message": get_fallback_response("chat")}

# Configuration for different use cases - OPTIMIZED FOR US-WEST-2 QUOTA
BEDROCK_CONFIGS = {
    "patient_summary": {
        "max_tokens": 100,  # Further reduced for us-west-2 quota
        "temperature": 0.1,
        "top_p": 0.9,
        "max_retries": 5
    },
    "clinical_analysis": {
        "max_tokens": 1000,  # Increased for verbose 2-3 sentence responses
        "temperature": 0.1,
        "top_p": 0.9,
        "max_retries": 8
    },
    "chat": {
        "max_tokens": 150,  # Reduced for us-west-2 quota
        "temperature": 0.2,
        "top_p": 0.9,
        "max_retries": 5
    },
    "workflow_generation": {
        "max_tokens": 1000,  # For workflow content generation
        "temperature": 0.1,
        "top_p": 0.9,
        "max_retries": 10
    }
}

def get_bedrock_config(use_case="chat"):
    """Get configuration for specific use case"""
    return BEDROCK_CONFIGS.get(use_case, BEDROCK_CONFIGS["chat"])
