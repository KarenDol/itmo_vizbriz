"""
Centralized LLM Service
All LLM calls should go through this service for consistency and maintainability
"""

import boto3
import json
import logging
from typing import Dict, List, Optional, Any
from flask_app.services.bedrock_service import get_bedrock_service
from flask_app.config.bedrock_config import get_fallback_response

logger = logging.getLogger(__name__)

class LLMService:
    """Centralized service for all LLM operations"""
    
    def __init__(self):
        self.bedrock_service = get_bedrock_service()
        
    def _make_bedrock_call(self, messages: List[Dict], max_tokens: int = 1000, 
                          temperature: float = 0.1, system: str = None, 
                          patient_id: Optional[int] = None, endpoint: str = 'llm_service') -> Dict[str, Any]:
        """Make a Bedrock API call with proper error handling"""
        try:
            if not self.bedrock_service.is_available():
                logger.warning("Bedrock service not available, using fallback")
                return {"success": False, "message": "Bedrock service not available"}
            
            # Use centralized Bedrock service with Claude 4.0 Sonnet
            result = self.bedrock_service.invoke_model(
                messages=messages,
                model="claude_4_sonnet",  # Using Claude 4.0 Sonnet (default model)
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=0.9,
                patient_id=patient_id,
                endpoint=endpoint
            )
            
            # bedrock_service.invoke_model() already processes the response
            # It returns: {"success": True/False, "response": text, "error": msg}
            if result.get("success"):
                answer = result.get("response", "")
                logger.info("Bedrock API call successful")
                return {"success": True, "response": answer}
            else:
                error_msg = result.get("error", "Unknown error")
                logger.warning(f"Bedrock returned error: {error_msg}")
                return {"success": False, "message": get_fallback_response("chat")}
                
        except Exception as e:
            logger.error(f"Bedrock API call failed: {e}")
            if "throttling" in str(e).lower() or "too many requests" in str(e).lower():
                return {"success": False, "message": "Service temporarily busy. Please try again in a few minutes."}
            else:
                return {"success": False, "message": get_fallback_response("chat")}
    
    def extract_from_document(self, document_text: str, extraction_schema: Dict, 
                            document_name: str = "document") -> Dict[str, Any]:
        """Extract structured data from a document"""
        system = f"""You are a medical document extraction AI. Extract structured data from medical documents according to the provided schema.

Schema: {json.dumps(extraction_schema, indent=2)}

Return ONLY a valid JSON object with the extracted data. Do not include any explanations or text outside the JSON."""
        
        messages = [{
            "role": "user",
            "content": f"Extract data from this {document_name}:\n\n{document_text}"
        }]
        
        return self._make_bedrock_call(
            messages=messages,
            max_tokens=4000,
            temperature=0.0,
            system=system
        )
    
    def extract_from_image(self, image_bytes: bytes, extraction_prompt: str, max_tokens: int = 200) -> Dict[str, Any]:
        """Extract data from an image using vision capabilities"""
        import base64
        
        # Convert image to base64
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
        
        system = "You are a medical document analysis AI. Extract specific information from medical images and documents."
        
        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": extraction_prompt
                },
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image_base64
                    }
                }
            ]
        }]
        
        return self._make_bedrock_call(
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.0,
            system=system
        )
    
    def generate_patient_narrative(self, patient_data: Dict, symptoms: List[str], 
                                 risk_level: str) -> Dict[str, Any]:
        """Generate a personalized patient communication narrative"""
        system = """You are creating a personalized script for a dental assistant to call a patient about their sleep apnea assessment.

Create a conversational script (2-3 sentences) that:
1. MENTIONS THE SPECIFIC SYMPTOMS the patient reported (be specific, not generic)
2. EXPLAINS what their risk level means in simple terms
3. GIVES A CLEAR NEXT STEP (schedule sleep test OR call dental sleep team)

Script should sound natural, like a caring dental team member who reviewed their specific answers."""
        
        symptoms_text = ", ".join(symptoms) if symptoms else "No specific symptoms reported"
        
        messages = [{
            "role": "user",
            "content": f"""
PATIENT: {patient_data.get('name', 'Patient')}
RISK LEVEL: {risk_level}
SPECIFIC SYMPTOMS REPORTED: {symptoms_text}

Create a personalized script for this patient.
"""
        }]
        
        return self._make_bedrock_call(
            messages=messages,
            max_tokens=200,
            temperature=0.3,
            system=system
        )
    
    def analyze_clinical_data(self, clinical_data: Dict) -> Dict[str, Any]:
        """Analyze clinical data and provide insights"""
        system = """You are a medical AI assistant specializing in sleep apnea analysis. 
        Analyze the provided clinical data and provide insights about the patient's condition."""
        
        messages = [{
            "role": "user",
            "content": f"Analyze this clinical data: {json.dumps(clinical_data, indent=2)}"
        }]
        
        return self._make_bedrock_call(
            messages=messages,
            max_tokens=1000,
            temperature=0.1,
            system=system
        )
    
    def generate_workflow_guidance(self, patient_stage: str, patient_data: Dict) -> Dict[str, Any]:
        """Generate workflow guidance for patient management"""
        system = f"""You are a dental practice workflow AI. Provide guidance for managing a patient at the {patient_stage} stage."""
        
        messages = [{
            "role": "user",
            "content": f"Provide workflow guidance for patient at {patient_stage} stage with data: {json.dumps(patient_data, indent=2)}"
        }]
        
        return self._make_bedrock_call(
            messages=messages,
            max_tokens=500,
            temperature=0.2,
            system=system
        )
    
    def chat_with_patient(self, user_message: str, context: Dict = None) -> Dict[str, Any]:
        """Handle patient chat interactions"""
        system = """You are Dr. Briz, a helpful dental AI assistant. Provide friendly, professional assistance to patients."""
        
        messages = [{
            "role": "user",
            "content": user_message
        }]
        
        if context:
            messages.insert(0, {
                "role": "user",
                "content": f"Context: {json.dumps(context, indent=2)}"
            })
        
        return self._make_bedrock_call(
            messages=messages,
            max_tokens=300,
            temperature=0.2,
            system=system
        )
    
    def extract_date_from_text(self, text: str) -> Dict[str, Any]:
        """Extract date information from text"""
        system = """Extract date information from text. Return ONLY a date in YYYY-MM-DD format, or "none" if no date found.
        Examples: 2025-09-15, 2024-11-27, none"""
        
        messages = [{
            "role": "user",
            "content": f"Extract date from: {text}"
        }]
        
        return self._make_bedrock_call(
            messages=messages,
            max_tokens=50,
            temperature=0.0,
            system=system
        )

# Global LLM service instance
llm_service = LLMService()

def get_llm_service() -> LLMService:
    """Get the global LLM service instance"""
    return llm_service

# Convenience functions for common operations
def extract_document_data(document_text: str, schema: Dict, document_name: str = "document") -> Dict[str, Any]:
    """Extract structured data from a document"""
    return llm_service.extract_from_document(document_text, schema, document_name)

def generate_patient_communication_narrative(patient_name: str, patient_email: str, 
                                           quiz_answers: Dict, risk_level: str, 
                                           risk_explanation: str, recommendations: str, 
                                           ai_analysis: str, specific_symptoms: List[str]) -> str:
    """Generate a personalized patient communication narrative"""
    patient_data = {
        'name': patient_name,
        'email': patient_email,
        'quiz_answers': quiz_answers
    }
    
    result = llm_service.generate_patient_narrative(
        patient_data=patient_data,
        symptoms=specific_symptoms,
        risk_level=risk_level
    )
    
    if result['success']:
        return result['response']
    else:
        return f"ERROR: Unable to generate personalized narrative for {patient_name}. Please try again or contact support."

def chat_with_ai(user_message: str, context: Dict = None) -> str:
    """Simple chat interface"""
    result = llm_service.chat_with_patient(user_message, context)
    
    if result['success']:
        return result['response']
    else:
        return result['message']
