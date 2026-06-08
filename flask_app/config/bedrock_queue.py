"""
Bedrock Request Queue System
Manages Bedrock API calls to work within strict quotas
"""

import time
import threading
import queue
import logging
from datetime import datetime, timedelta
from collections import defaultdict
import json

logger = logging.getLogger(__name__)

class BedrockRequestQueue:
    """Queue system for Bedrock requests to respect quotas"""
    
    def __init__(self):
        self.request_queue = queue.Queue()
        self.results = {}
        self.request_counter = 0
        self.lock = threading.Lock()
        
        # Very conservative rate limiting
        self.requests_per_minute = 5  # Very low to work within quotas
        self.requests_per_second = 1
        self.last_request_time = 0
        self.request_timestamps = []
        
        # Start worker thread
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()
        
        logger.info("Bedrock request queue initialized with conservative limits")
    
    def _worker(self):
        """Worker thread that processes requests from queue"""
        while True:
            try:
                # Get request from queue
                request_data = self.request_queue.get(timeout=1)
                request_id = request_data['id']
                messages = request_data['messages']
                max_tokens = request_data['max_tokens']
                temperature = request_data['temperature']
                top_p = request_data['top_p']
                
                # Rate limiting
                self._wait_for_rate_limit()
                
                # Make the actual Bedrock call
                from flask_app.config.bedrock_config import query_bedrock_claude_enhanced
                result = query_bedrock_claude_enhanced(
                    messages, max_tokens, temperature, top_p
                )
                
                # Store result
                with self.lock:
                    self.results[request_id] = result
                
                logger.info(f"Processed request {request_id}")
                
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in worker thread: {e}")
                # Store error result
                if 'request_id' in locals():
                    with self.lock:
                        self.results[request_id] = {
                            "success": False,
                            "message": f"Queue processing error: {str(e)}"
                        }
    
    def _wait_for_rate_limit(self):
        """Wait if we're hitting rate limits"""
        now = time.time()
        
        # Clean old timestamps
        self.request_timestamps = [ts for ts in self.request_timestamps if now - ts < 60]
        
        # Check minute limit
        if len(self.request_timestamps) >= self.requests_per_minute:
            wait_time = 60 - (now - self.request_timestamps[0])
            if wait_time > 0:
                logger.info(f"Rate limit hit, waiting {wait_time:.1f} seconds")
                time.sleep(wait_time)
        
        # Check second limit
        if now - self.last_request_time < 1.0:
            time.sleep(1.0 - (now - self.last_request_time))
        
        # Record this request
        self.request_timestamps.append(time.time())
        self.last_request_time = time.time()
    
    def submit_request(self, messages, max_tokens=300, temperature=0.2, top_p=0.9):
        """Submit a request to the queue"""
        with self.lock:
            self.request_counter += 1
            request_id = f"req_{self.request_counter}_{int(time.time())}"
        
        # Add to queue
        self.request_queue.put({
            'id': request_id,
            'messages': messages,
            'max_tokens': max_tokens,
            'temperature': temperature,
            'top_p': top_p
        })
        
        logger.info(f"Submitted request {request_id} to queue")
        return request_id
    
    def get_result(self, request_id, timeout=30):
        """Get result for a request, with timeout"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            with self.lock:
                if request_id in self.results:
                    result = self.results[request_id]
                    # Clean up old results
                    if len(self.results) > 100:
                        old_keys = list(self.results.keys())[:50]
                        for key in old_keys:
                            del self.results[key]
                    return result
            
            time.sleep(0.1)
        
        return {
            "success": False,
            "message": f"Request {request_id} timed out after {timeout} seconds"
        }

# Global queue instance
bedrock_queue = BedrockRequestQueue()

def submit_bedrock_request(messages, max_tokens=300, temperature=0.2, top_p=0.9):
    """Submit a request to the Bedrock queue"""
    return bedrock_queue.submit_request(messages, max_tokens, temperature, top_p)

def get_bedrock_result(request_id, timeout=30):
    """Get result for a queued request"""
    return bedrock_queue.get_result(request_id, timeout)







