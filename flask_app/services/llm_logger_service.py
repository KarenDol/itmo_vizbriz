"""
LLM Logger Service
Centralized service for logging all LLM prompt/response interactions to database
"""

import logging
import json
import uuid
from datetime import datetime
from typing import Optional, Dict, List, Any
from flask import has_request_context, request
from flask_login import current_user

logger = logging.getLogger(__name__)


class LLMLoggerService:
    """Service for logging LLM interactions to database"""
    
    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """
        Estimate token count for Claude models.
        Uses tiktoken library if available, otherwise falls back to char count / 4
        """
        try:
            import tiktoken
            # Use GPT-4 tokenizer (cl100k_base) as approximation for Claude
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except ImportError:
            logger.debug("tiktoken not available, using fallback token estimation")
            # Fallback: rough estimate (1 token ≈ 4 characters)
            return max(1, len(text) // 4)
        except Exception as e:
            logger.warning(f"Token estimation error: {e}, using fallback")
            return max(1, len(text) // 4)
    
    @staticmethod
    def _get_user_id() -> Optional[int]:
        """Get current user ID from Flask context"""
        try:
            if has_request_context() and current_user and current_user.is_authenticated:
                return current_user.id
        except Exception as e:
            logger.debug(f"Could not get user_id: {e}")
        return None
    
    @staticmethod
    def _extract_prompt_text(messages: List[Dict[str, Any]]) -> str:
        """Extract text content from messages array"""
        try:
            if isinstance(messages, str):
                return messages
            
            if isinstance(messages, list):
                text_parts = []
                for msg in messages:
                    if isinstance(msg, dict):
                        # Handle different message formats
                        if 'content' in msg:
                            content = msg['content']
                            if isinstance(content, str):
                                text_parts.append(content)
                            elif isinstance(content, list):
                                for item in content:
                                    if isinstance(item, dict) and 'text' in item:
                                        text_parts.append(item['text'])
                        elif 'text' in msg:
                            text_parts.append(msg['text'])
                return '\n'.join(text_parts)
            
            return str(messages)
        except Exception as e:
            logger.warning(f"Error extracting prompt text: {e}")
            return str(messages)[:1000]  # Truncate if error
    
    @staticmethod
    def log_prompt(
        session_id: str,
        model_name: str,
        model_id: str,
        prompt_content: Any,
        patient_id: Optional[int] = None,
        page_endpoint: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        user_id: Optional[int] = None
    ) -> Optional[int]:
        """
        Log a prompt to the database.
        
        Args:
            session_id: UUID for this interaction session
            model_name: Short model name (e.g., 'claude_4_sonnet')
            model_id: Full Bedrock model ID
            prompt_content: The prompt (can be str, list of messages, or dict)
            patient_id: Associated patient ID
            page_endpoint: Flask endpoint that triggered the call
            max_tokens: Max tokens requested
            temperature: Temperature setting
            top_p: Top-p setting
            user_id: User who triggered the call (auto-detected if None)
            
        Returns:
            Database ID of the created record, or None if failed
        """
        try:
            # Lazy import to avoid circular dependency
            from flask_app.extensions import db
            from flask_app.models import LLMInteraction
        except ImportError as e:
            logger.error(f"Cannot import database models: {e}")
            return None
        
        try:
            # Extract text content
            content_text = LLMLoggerService._extract_prompt_text(prompt_content)
            
            # Estimate tokens
            token_count_estimated = LLMLoggerService._estimate_tokens(content_text)
            
            # Get patient_id from document extraction script context if not provided
            if patient_id is None:
                try:
                    from flask_app.config.document_observation_extractor_phase2 import get_current_patient_id
                    patient_id = get_current_patient_id()
                except:
                    pass
            
            # Get user_id if not provided
            if user_id is None:
                user_id = LLMLoggerService._get_user_id()
            
            # Get page_endpoint from Flask context if not provided
            if page_endpoint is None:
                try:
                    if has_request_context():
                        page_endpoint = request.endpoint
                    else:
                        page_endpoint = 'script'  # Running as script, not HTTP request
                except Exception:
                    page_endpoint = 'unknown'
            
            # Create database entry
            interaction = LLMInteraction(
                session_id=session_id,
                interaction_type='prompt',
                patient_id=patient_id,
                page_endpoint=page_endpoint or 'unknown',
                user_id=user_id,
                model_name=model_name,
                model_id=model_id,
                content_text=content_text,
                content_json=prompt_content if isinstance(prompt_content, (dict, list)) else None,
                token_count_estimated=token_count_estimated,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                status='success',
                created_at=datetime.utcnow()
            )
            
            db.session.add(interaction)
            db.session.commit()
            
            logger.debug(f"Logged prompt for session {session_id[:8]}... (patient: {patient_id}, endpoint: {page_endpoint})")
            return interaction.id
            
        except Exception as e:
            logger.error(f"Error logging prompt to database: {e}", exc_info=True)
            try:
                db.session.rollback()
            except Exception:
                pass
            return None
    
    @staticmethod
    def log_response(
        session_id: str,
        response_text: str,
        status: str = 'success',
        response_time_ms: Optional[int] = None,
        error_message: Optional[str] = None,
        response_data: Optional[Dict] = None
    ) -> Optional[int]:
        """
        Log a response to the database.
        
        Args:
            session_id: UUID for this interaction session (same as prompt)
            response_text: The actual response text from the LLM
            status: Status of the call ('success', 'error', 'throttled', 'timeout')
            response_time_ms: How long the call took in milliseconds
            error_message: Error details if status is not 'success'
            response_data: Full response data structure (optional)
            
        Returns:
            Database ID of the created record, or None if failed
        """
        try:
            # Lazy import to avoid circular dependency
            from flask_app.extensions import db
            from flask_app.models import LLMInteraction
        except ImportError as e:
            logger.error(f"Cannot import database models: {e}")
            return None
        
        try:
            # Get the prompt entry to copy model info
            prompt_entry = LLMInteraction.query.filter_by(
                session_id=session_id,
                interaction_type='prompt'
            ).first()
            
            if not prompt_entry:
                logger.warning(f"No prompt found for session {session_id}, creating standalone response")
                # Create standalone response (shouldn't happen, but handle gracefully)
                model_name = 'unknown'
                model_id = 'unknown'
                patient_id = None
                page_endpoint = 'unknown'
                user_id = None
            else:
                model_name = prompt_entry.model_name
                model_id = prompt_entry.model_id
                patient_id = prompt_entry.patient_id
                page_endpoint = prompt_entry.page_endpoint
                user_id = prompt_entry.user_id
            
            # Estimate tokens in response
            token_count_estimated = LLMLoggerService._estimate_tokens(response_text)
            
            # Create database entry
            interaction = LLMInteraction(
                session_id=session_id,
                interaction_type='response',
                patient_id=patient_id,
                page_endpoint=page_endpoint,
                user_id=user_id,
                model_name=model_name,
                model_id=model_id,
                content_text=response_text,
                content_json=response_data,
                token_count_estimated=token_count_estimated,
                response_time_ms=response_time_ms,
                status=status,
                error_message=error_message,
                created_at=datetime.utcnow()
            )
            
            db.session.add(interaction)
            db.session.commit()
            
            logger.debug(f"Logged response for session {session_id[:8]}... (status: {status}, time: {response_time_ms}ms)")
            return interaction.id
            
        except Exception as e:
            logger.error(f"Error logging response to database: {e}", exc_info=True)
            try:
                db.session.rollback()
            except Exception:
                pass
            return None
    
    @staticmethod
    def get_patient_history(patient_id: int, limit: int = 50) -> List:
        """Get LLM interaction history for a patient"""
        try:
            from flask_app.models import LLMInteraction
            return LLMInteraction.get_patient_history(patient_id, limit)
        except Exception as e:
            logger.error(f"Error getting patient LLM history: {e}")
            return []
    
    @staticmethod
    def get_session_pair(session_id: str) -> Dict[str, Optional[Any]]:
        """Get both prompt and response for a session"""
        try:
            from flask_app.models import LLMInteraction
            return LLMInteraction.get_session_pair(session_id)
        except Exception as e:
            logger.error(f"Error getting session pair: {e}")
            return {'prompt': None, 'response': None}
    
    @staticmethod
    def get_recent_errors(hours: int = 24, limit: int = 100) -> List:
        """Get recent failed LLM calls"""
        try:
            from flask_app.models import LLMInteraction
            return LLMInteraction.get_recent_errors(hours, limit)
        except Exception as e:
            logger.error(f"Error getting recent errors: {e}")
            return []
    
    @staticmethod
    def get_stats(start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Get LLM usage statistics for a date range
        
        Returns dict with:
        - total_prompts: Total number of prompts
        - total_responses: Total number of responses
        - total_tokens_estimated: Estimated total tokens used
        - avg_response_time_ms: Average response time
        - success_rate: Percentage of successful calls
        - by_model: Stats broken down by model
        - by_endpoint: Stats broken down by endpoint
        """
        try:
            from flask_app.models import LLMInteraction
            query = LLMInteraction.query
            
            if start_date:
                query = query.filter(LLMInteraction.created_at >= start_date)
            if end_date:
                query = query.filter(LLMInteraction.created_at <= end_date)
            
            all_interactions = query.all()
            
            prompts = [i for i in all_interactions if i.interaction_type == 'prompt']
            responses = [i for i in all_interactions if i.interaction_type == 'response']
            successful_responses = [r for r in responses if r.status == 'success']
            
            total_tokens = sum(i.token_count_estimated or 0 for i in all_interactions)
            
            response_times = [r.response_time_ms for r in responses if r.response_time_ms]
            avg_response_time = sum(response_times) / len(response_times) if response_times else 0
            
            success_rate = (len(successful_responses) / len(responses) * 100) if responses else 0
            
            # Stats by model
            by_model = {}
            for interaction in all_interactions:
                model = interaction.model_name
                if model not in by_model:
                    by_model[model] = {'count': 0, 'tokens': 0}
                by_model[model]['count'] += 1
                by_model[model]['tokens'] += interaction.token_count_estimated or 0
            
            # Stats by endpoint
            by_endpoint = {}
            for interaction in prompts:  # Only count prompts to avoid double-counting
                endpoint = interaction.page_endpoint or 'unknown'
                if endpoint not in by_endpoint:
                    by_endpoint[endpoint] = {'count': 0, 'tokens': 0}
                by_endpoint[endpoint]['count'] += 1
                by_endpoint[endpoint]['tokens'] += interaction.token_count_estimated or 0
            
            return {
                'total_prompts': len(prompts),
                'total_responses': len(responses),
                'total_tokens_estimated': total_tokens,
                'avg_response_time_ms': round(avg_response_time, 2),
                'success_rate': round(success_rate, 2),
                'by_model': by_model,
                'by_endpoint': by_endpoint
            }
            
        except Exception as e:
            logger.error(f"Error getting LLM stats: {e}")
            return {
                'error': str(e),
                'total_prompts': 0,
                'total_responses': 0,
                'total_tokens_estimated': 0
            }

