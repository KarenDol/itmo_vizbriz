#!/usr/bin/env python3
"""
LLM Call Logger Service
Tracks all LLM calls with user context, request/response content, and audit information.
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional
from flask import request, session, g
from flask_login import current_user

# Set up dedicated logger for LLM calls
llm_logger = logging.getLogger('llm_calls')
llm_logger.setLevel(logging.INFO)

# Create file handler for LLM calls
log_dir = '/home/ec2-user/vizbriz/logs'
os.makedirs(log_dir, exist_ok=True)

# Create file handler with rotation
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
llm_logger.addHandler(llm_file_handler)

class LLMCallLogger:
    """Logger for tracking LLM calls with user context"""
    
    @staticmethod
    def get_user_context() -> Dict[str, Any]:
        """Extract user context from Flask request"""
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
                'user_role': getattr(current_user, 'role', None)
            })
        else:
            context.update({
                'user_id': None,
                'user_email': None,
                'user_name': None,
                'user_role': None
            })
        
        # Get session info
        context.update({
            'session_id': session.get('_id', None),
            'is_admin': getattr(current_user, 'is_admin', False) if current_user and current_user.is_authenticated else False
        })
        
        return context
    
    @staticmethod
    def log_llm_call(
        patient_id: Optional[int] = None,
        model: str = 'unknown',
        prompt: str = '',
        response: str = '',
        success: bool = True,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Log an LLM call with full context"""
        
        # Get user context
        user_context = LLMCallLogger.get_user_context()
        
        # Build log entry
        log_entry = {
            'llm_call': {
                'patient_id': patient_id,
                'model': model,
                'success': success,
                'error': error,
                'prompt_length': len(prompt),
                'response_length': len(response),
                'prompt_preview': prompt[:200] + '...' if len(prompt) > 200 else prompt,
                'response_preview': response[:200] + '...' if len(response) > 200 else response,
                'metadata': metadata or {}
            },
            'user_context': user_context
        }
        
        # Log the call
        if success:
            llm_logger.info(f"LLM_CALL_SUCCESS: {json.dumps(log_entry, indent=2)}")
        else:
            llm_logger.error(f"LLM_CALL_FAILURE: {json.dumps(log_entry, indent=2)}")
    
    @staticmethod
    def log_llm_request(
        patient_id: Optional[int] = None,
        model: str = 'unknown',
        prompt: str = '',
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Log an LLM request (before the call)"""
        
        user_context = LLMCallLogger.get_user_context()
        
        log_entry = {
            'llm_request': {
                'patient_id': patient_id,
                'model': model,
                'prompt_length': len(prompt),
                'prompt_preview': prompt[:200] + '...' if len(prompt) > 200 else prompt,
                'metadata': metadata or {}
            },
            'user_context': user_context
        }
        
        llm_logger.info(f"LLM_REQUEST: {json.dumps(log_entry, indent=2)}")
    
    @staticmethod
    def log_llm_response(
        patient_id: Optional[int] = None,
        model: str = 'unknown',
        response: str = '',
        success: bool = True,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Log an LLM response (after the call)"""
        
        user_context = LLMCallLogger.get_user_context()
        
        log_entry = {
            'llm_response': {
                'patient_id': patient_id,
                'model': model,
                'success': success,
                'error': error,
                'response_length': len(response),
                'response_preview': response[:200] + '...' if len(response) > 200 else response,
                'metadata': metadata or {}
            },
            'user_context': user_context
        }
        
        if success:
            llm_logger.info(f"LLM_RESPONSE_SUCCESS: {json.dumps(log_entry, indent=2)}")
        else:
            llm_logger.error(f"LLM_RESPONSE_FAILURE: {json.dumps(log_entry, indent=2)}")

# Convenience functions
def log_llm_call(patient_id=None, model='unknown', prompt='', response='', success=True, error=None, metadata=None):
    """Convenience function for logging LLM calls"""
    LLMCallLogger.log_llm_call(
        patient_id=patient_id,
        model=model,
        prompt=prompt,
        response=response,
        success=success,
        error=error,
        metadata=metadata
    )

def log_llm_request(patient_id=None, model='unknown', prompt='', metadata=None):
    """Convenience function for logging LLM requests"""
    LLMCallLogger.log_llm_request(
        patient_id=patient_id,
        model=model,
        prompt=prompt,
        metadata=metadata
    )

def log_llm_response(patient_id=None, model='unknown', response='', success=True, error=None, metadata=None):
    """Convenience function for logging LLM responses"""
    LLMCallLogger.log_llm_response(
        patient_id=patient_id,
        model=model,
        response=response,
        success=success,
        error=error,
        metadata=metadata
    )
