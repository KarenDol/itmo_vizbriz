#!/usr/bin/env python3
"""
Modify your existing chatbot to actually use the Bedrock vector database
"""

def create_enhanced_chatbot_function():
    """
    This shows how to modify your existing chat_with_agent function
    to actually query the Bedrock knowledge base
    """
    
    enhanced_code = '''
# Add this import at the top of osaagent_routes.py
import requests
import json

# Add this function to osaagent_routes.py
def query_bedrock_knowledge_base(user_message, patient_id=None):
    """
    Query the Bedrock knowledge base for additional context
    """
    try:
        # If patient_id is provided, use patient-specific query
        if patient_id:
            response = requests.post(
                f"{request.url_root}bedrock/patient-query",
                json={
                    "query": user_message,
                    "patient_id": str(patient_id),
                    "temperature": 0.2,
                    "max_results": 6
                },
                headers={"Content-Type": "application/json"},
                timeout=30
            )
        else:
            # Use general knowledge base query
            response = requests.post(
                f"{request.url_root}bedrock/query",
                json={"query": user_message},
                headers={"Content-Type": "application/json"},
                timeout=30
            )
        
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                return {
                    'success': True,
                    'response': data.get('response', ''),
                    'citations': data.get('citations', []),
                    'source': 'Bedrock Knowledge Base'
                }
        
        return {'success': False, 'response': '', 'citations': [], 'source': 'None'}
        
    except Exception as e:
        logger.error(f"Error querying Bedrock knowledge base: {str(e)}")
        return {'success': False, 'response': '', 'citations': [], 'source': 'Error'}

# REPLACE your existing chat_with_agent function with this enhanced version:
@osaagent.route('/agent/chat/<int:patient_id>', methods=['POST'])
@login_required
def chat_with_agent_enhanced(patient_id):
    """Enhanced endpoint for chatting with the agent using Bedrock knowledge base"""
    try:
        data = request.get_json()
        if not data or 'message' not in data:
            return jsonify({"success": False, "message": "Message is required"}), 400
        
        user_message = data['message']
        
        # Get patient information
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({"success": False, "message": "Patient not found"}), 404
        
        # FIRST: Query the Bedrock knowledge base for medical context
        logger.info(f"Querying Bedrock knowledge base for: {user_message}")
        bedrock_context = query_bedrock_knowledge_base(user_message, patient_id)
        
        # Get canonical data from PatientCaseEnvelope (your existing code)
        canonical_data = None
        try:
            from flask_app.models import PatientCaseEnvelope
            canonical_envelope = PatientCaseEnvelope.query.filter_by(
                patient_id=patient_id, 
                report_id='canonical'
            ).first()
            
            if canonical_envelope and canonical_envelope.case_json:
                canonical_data = canonical_envelope.case_json
                logger.info(f"Loaded canonical data for patient {patient_id}")
            else:
                logger.info(f"No canonical data found for patient {patient_id}")
        except Exception as e:
            logger.error(f"Error loading canonical data for patient {patient_id}: {e}")
            canonical_data = None
        
        # Get observation store data (your existing code)
        observation_data = []
        try:
            import mysql.connector
            conn = mysql.connector.connect(
                host='vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com',
                user='admin',
                password='Vizbriz2025!',
                database='vizbriz',
                port=3306
            )
            cursor = conn.cursor(dictionary=True)
            
            query = """
                SELECT source_type, source_text, extracted_observations, created_at
                FROM observation_store 
                WHERE patient_id = %s 
                ORDER BY created_at DESC
            """
            cursor.execute(query, (patient_id,))
            db_observations = cursor.fetchall()
            
            logger.info(f"Found {len(db_observations)} observations in observation store for patient {patient_id}")
            
            if db_observations:
                for obs in db_observations:
                    try:
                        obs_data = json.loads(obs['extracted_observations']) if obs['extracted_observations'] else {}
                        observation_data.append({
                            'source_type': obs['source_type'],
                            'observation': obs_data.get('observation', ''),
                            'value': obs_data.get('value', ''),
                            'evidence': obs_data.get('evidence', ''),
                            'confidence': obs_data.get('confidence', 0),
                            'created_at': obs['created_at'].isoformat() if obs['created_at'] else None
                        })
                    except Exception as e:
                        logger.warning(f"Error parsing observation for patient {patient_id}: {e}")
                        continue
            
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error loading observation store data for patient {patient_id}: {e}")
            observation_data = []
        
        # Get execution manifest data (your existing code)
        from flask_app.routes.cursor_routes import get_execution_manifest
        execution_manifest_response = get_execution_manifest(patient_id)
        
        if hasattr(execution_manifest_response, 'get_json'):
            execution_manifest = execution_manifest_response.get_json()
        else:
            execution_manifest = execution_manifest_response
        
        # Build standardized packet (your existing code)
        packet = {
            "patient": {
                "id": str(patient.id),
                "name": patient.name or "Unknown",
                "age": None
            },
            "canonical_clinical_data": canonical_data if canonical_data else {
                "demographics": {"sex": patient.gender, "age_years": None},
                "sleep_study": {"study_type": "unknown", "ahi": None, "odi": None, "o2_nadir_pct": None},
                "observations": {"summary": [], "anatomy_imaging": {}},
                "treatment_considerations": {"primary_pathway": [], "adjuncts": [], "cautions": []},
                "device_design": {"mandibular_advancement_mm": None, "vertical_opening_mm": None}
            },
            "observation_store_data": observation_data,
            "operational_data": {
                "workflow_progress": {
                    "current_stage": "Unknown",
                    "completion_pct": 0,
                    "total_stages": 0,
                    "current_stage_index": 0
                },
                "pending_actions": [],
                "alerts": []
            } if not execution_manifest else {
                "workflow_progress": {
                    "current_stage": execution_manifest.get('current_stage', 'Unknown'),
                    "completion_pct": execution_manifest.get('progress_percentage', 0),
                    "total_stages": len(execution_manifest.get('stage_manifest', [])),
                    "current_stage_index": sum(1 for stage in execution_manifest.get('stage_manifest', []) if stage.get('value') == 'yes')
                },
                "pending_actions": [{"action": a.get('label', 'Unknown action'), "priority": "normal"} for a in execution_manifest.get('eligible_actions', [])[:3]],
                "alerts": []
            }
        }
        
        # ENHANCED PROMPT: Include Bedrock knowledge base context
        enhanced_prompt = f"""
        You are Dr. Briz, an expert sleep medicine AI assistant specializing in OSA treatment and dental sleep therapy.
        
        CRITICAL: Use BOTH the patient data provided below AND the medical knowledge from your knowledge base.
        
        PATIENT INFORMATION:
        Name: {patient.name}
        ID: {patient.id}
        
        STANDARDIZED CLINICAL DATA:
        {json.dumps(packet['canonical_clinical_data'], indent=2)}
        
        OBSERVATION STORE DATA (Quiz responses, extracted observations, etc.):
        {json.dumps(packet['observation_store_data'], indent=2)}
        
        OPERATIONAL WORKFLOW DATA:
        {json.dumps(packet['operational_data'], indent=2)}
        
        MEDICAL KNOWLEDGE BASE CONTEXT:
        {bedrock_context['response'] if bedrock_context['success'] else 'No additional medical context available from knowledge base.'}
        
        USER QUESTION: {user_message}
        
        IMPORTANT RULES:
        1. Use the patient data provided above for specific patient information
        2. Use the medical knowledge base context for general medical information and best practices
        3. Combine both sources to provide comprehensive, accurate responses
        4. If the knowledge base provides relevant medical information, incorporate it into your response
        5. Always cite that you're using both patient data and medical knowledge base
        
        Please provide a helpful, professional response as Dr. Briz that combines:
        1. The patient's actual clinical data
        2. Relevant medical knowledge from your knowledge base
        3. Current best practices for OSA treatment
        4. Specific recommendations based on their clinical profile
        """
        
        # Use Bedrock for response generation (your existing code)
        from flask_app.config.bedrock_config import query_bedrock_claude_enhanced
        messages = [{"role": "user", "content": enhanced_prompt}]
        
        result = query_bedrock_claude_enhanced(
            messages, 
            max_tokens=800,  # Increased for more comprehensive responses
            temperature=0.3
        )
        
        if result.get('success'):
            assistant_response = result.get('response', 'I\'m here to help with your patient\'s OSA treatment journey.')
            
            # Include Bedrock knowledge base information in response
            response_data = {
                "success": True,
                "message": assistant_response,
                "patient_id": patient_id,
                "patient_name": patient.name,
                "data_source": "Canonical Schema + Bedrock Knowledge Base" if canonical_data else "Fallback Data + Bedrock Knowledge Base",
                "bedrock_used": bedrock_context['success'],
                "bedrock_citations": bedrock_context.get('citations', []),
                "bedrock_source": bedrock_context.get('source', 'None')
            }
            
            logger.info(f"Enhanced chatbot response generated using Bedrock knowledge base: {bedrock_context['success']}")
            return jsonify(response_data)
        else:
            # Fallback with Bedrock context
            fallback_response = f"Hello! I'm Dr. Briz, your AI assistant. "
            
            if bedrock_context['success']:
                fallback_response += f"Based on my medical knowledge base: {bedrock_context['response'][:200]}... "
            
            fallback_response += f"I can see that {patient.name} is currently in the OSA treatment workflow. I can help you with treatment planning, progress tracking, and answering questions about their case. What specific information would you like to know?"
            
            return jsonify({
                "success": True,
                "message": fallback_response,
                "patient_id": patient_id,
                "patient_name": patient.name,
                "data_source": "Fallback Response + Bedrock Knowledge Base",
                "bedrock_used": bedrock_context['success'],
                "bedrock_citations": bedrock_context.get('citations', []),
                "bedrock_source": bedrock_context.get('source', 'None')
            })
    
    except Exception as e:
        logger.error(f"Error in enhanced chat endpoint: {str(e)}")
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500
    '''
    
    print("🔧 Enhanced Chatbot with Bedrock Vector Database")
    print("=" * 60)
    print("\nThis code will modify your existing chatbot to actually use the Bedrock knowledge base.")
    print("\nKey changes:")
    print("✅ Queries Bedrock knowledge base BEFORE generating response")
    print("✅ Combines patient data with medical knowledge")
    print("✅ Includes citations from knowledge base")
    print("✅ Logs when Bedrock is used")
    print("✅ Provides fallback if Bedrock fails")
    
    return enhanced_code

if __name__ == "__main__":
    create_enhanced_chatbot_function()
