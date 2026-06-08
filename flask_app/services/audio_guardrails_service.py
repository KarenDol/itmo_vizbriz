#!/usr/bin/env python3
"""
Bedrock Guardrails Service
For PII masking, content filtering, and safety compliance
"""

import json
import logging
import boto3
from typing import Dict, Any, Optional
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class AudioGuardrailsService:
    """Service for applying Bedrock Guardrails to audio scripts"""
    
    def __init__(self):
        self.client = None
        self.guardrail_id = None
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize Bedrock Runtime client for Guardrails"""
        try:
            import os
            region = os.getenv('BEDROCK_AWS_REGION', 'us-west-2')
            self.client = boto3.client(
                'bedrock-runtime',
                region_name=region,
                aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
            )
            logger.info(f"Bedrock Guardrails client initialized in region {region}")
        except Exception as e:
            logger.error(f"Failed to initialize Bedrock Guardrails client: {e}")
            self.client = None
    
    def is_available(self) -> bool:
        """Check if Guardrails service is available"""
        return self.client is not None
    
    def apply_guardrails(self, 
                        text: str, 
                        guardrail_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Apply Bedrock Guardrails to text
        
        Args:
            text: Text to process
            guardrail_id: Optional guardrail ID (if not set, uses content filtering API)
            
        Returns:
            Dict with filtered text and safety results
        """
        if not self.is_available():
            logger.warning("Guardrails service not available, returning original text")
            return {
                "success": True,
                "filtered_text": text,
                "pii_removed": False,
                "content_filtered": False,
                "note": "Guardrails not configured"
            }
        
        try:
            # For now, implement basic PII masking and safety checks
            # Full Guardrails API integration would require a configured guardrail
            # This is a simplified version that does basic filtering
            
            filtered_text = text
            pii_removed = False
            content_filtered = False
            
            # Basic PII patterns (phone, email, SSN-like patterns)
            import re
            
            # Phone numbers
            phone_pattern = r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'
            if re.search(phone_pattern, filtered_text):
                filtered_text = re.sub(phone_pattern, '[PHONE]', filtered_text)
                pii_removed = True
            
            # Email addresses
            email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
            if re.search(email_pattern, filtered_text):
                filtered_text = re.sub(email_pattern, '[EMAIL]', filtered_text)
                pii_removed = True
            
            # SSN-like patterns
            ssn_pattern = r'\b\d{3}-\d{2}-\d{4}\b'
            if re.search(ssn_pattern, filtered_text):
                filtered_text = re.sub(ssn_pattern, '[SSN]', filtered_text)
                pii_removed = True
            
            # Note: Safety disclaimer removed per user request
            # Previously added: "This isn't a diagnosis; confirm with your clinician."
            
            # Ensure "no guarantees" language
            guarantee_keywords = ["guarantee", "promise", "certain", "definitely"]
            has_guarantee_language = any(kw in filtered_text.lower() for kw in guarantee_keywords)
            if has_guarantee_language:
                # Add clarifying language
                if "results may vary" not in filtered_text.lower():
                    filtered_text += " Results may vary and should be discussed with your healthcare provider."
                    content_filtered = True
            
            logger.info(f"Guardrails applied: PII removed={pii_removed}, content filtered={content_filtered}")
            
            return {
                "success": True,
                "filtered_text": filtered_text,
                "pii_removed": pii_removed,
                "content_filtered": content_filtered
            }
            
        except Exception as e:
            logger.error(f"Error applying guardrails: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "filtered_text": text  # Return original on error
            }
    
    def filter_ssml(self, ssml: str) -> Dict[str, Any]:
        """
        Apply guardrails to SSML content
        
        Args:
            ssml: SSML-formatted text
            
        Returns:
            Dict with filtered SSML
        """
        # Extract text content from SSML (simple extraction)
        import re
        text_content = re.sub(r'<[^>]+>', '', ssml)  # Remove tags temporarily
        
        # Apply guardrails to text
        result = self.apply_guardrails(text_content)
        
        if not result.get("success"):
            return result
        
        # Reconstruct SSML with filtered text
        # This is a simplified approach - in production, you'd want proper SSML parsing
        filtered_text = result.get("filtered_text", text_content)
        
        # For now, replace the text content while preserving SSML structure
        # This is a basic implementation - full SSML parsing would be more robust
        filtered_ssml = ssml
        if result.get("pii_removed") or result.get("content_filtered"):
            # Simple replacement - replace text between tags
            # More sophisticated: parse SSML properly
            filtered_ssml = filtered_text
            # Try to preserve SSML structure by keeping the speak tags
            if not filtered_ssml.startswith("<speak>"):
                filtered_ssml = f"<speak>{filtered_ssml}</speak>"
        
        return {
            "success": True,
            "filtered_ssml": filtered_ssml,
            "pii_removed": result.get("pii_removed", False),
            "content_filtered": result.get("content_filtered", False)
        }
