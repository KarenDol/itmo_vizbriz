#!/usr/bin/env python3
"""
Integration script to add Bedrock vector database support to existing chatbot
This shows how to modify your existing chatbot to use the Bedrock knowledge base
"""

def integrate_bedrock_with_existing_chatbot():
    """
    This shows how to modify your existing chatbot in osaagent_routes.py
    to use the Bedrock vector database
    """
    
    integration_code = '''
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

# Modify your existing chat_with_agent function
# Add this code after line 1050 (after building the prompt):

        # Query Bedrock knowledge base for additional context
        bedrock_context = query_bedrock_knowledge_base(user_message, patient_id)
        
        # Enhance the prompt with Bedrock knowledge
        if bedrock_context['success'] and bedrock_context['response']:
            enhanced_prompt = f"""
        {prompt}
        
        ADDITIONAL KNOWLEDGE BASE CONTEXT:
        Based on your extensive knowledge base of sleep medicine and OSA treatment:
        {bedrock_context['response']}
        
        Use this additional context to provide more comprehensive and accurate responses.
        If the knowledge base provides specific information that contradicts or supplements
        the patient data above, prioritize the most recent and relevant information.
        """
        else:
            enhanced_prompt = prompt
        
        # Use the enhanced prompt instead of the original prompt
        messages = [{"role": "user", "content": enhanced_prompt}]
        
        # Rest of your existing code remains the same...
        result = query_bedrock_claude_enhanced(
            messages, 
            max_tokens=600, 
            temperature=0.3
        )
        
        if result.get('success'):
            assistant_response = result.get('response', 'I\'m here to help with your patient\'s OSA treatment journey.')
            
            # Include Bedrock citations in the response
            response_data = {
                "success": True,
                "message": assistant_response,
                "patient_id": patient_id,
                "patient_name": patient.name,
                "data_source": "Canonical Schema + Bedrock Knowledge Base" if canonical_data else "Fallback Data + Bedrock Knowledge Base",
                "bedrock_citations": bedrock_context.get('citations', []),
                "bedrock_source": bedrock_context.get('source', 'None')
            }
            
            return jsonify(response_data)
        else:
            # Fallback with Bedrock context
            fallback_response = f"Hello! I'm Dr. Briz, your AI assistant. I can see that {patient.name} is currently in the OSA treatment workflow. "
            
            if bedrock_context['success']:
                fallback_response += f"Based on my knowledge base: {bedrock_context['response'][:200]}... "
            
            fallback_response += "I can help you with treatment planning, progress tracking, and answering questions about their case. What specific information would you like to know?"
            
            return jsonify({
                "success": True,
                "message": fallback_response,
                "patient_id": patient_id,
                "patient_name": patient.name,
                "data_source": "Fallback Response + Bedrock Knowledge Base",
                "bedrock_citations": bedrock_context.get('citations', []),
                "bedrock_source": bedrock_context.get('source', 'None')
            })
    '''
    
    print("🔧 Bedrock Integration for Existing Chatbot")
    print("=" * 50)
    print("\nTo integrate Bedrock with your existing chatbot:")
    print("\n1. Add the query_bedrock_knowledge_base function to osaagent_routes.py")
    print("2. Modify your existing chat_with_agent function as shown above")
    print("3. Your chatbot will now use both patient data AND Bedrock knowledge base")
    print("\n📋 Integration Code:")
    print("-" * 30)
    print(integration_code)
    
    return integration_code

def test_existing_chatbot_with_bedrock():
    """
    Test your existing chatbot to see if it's already using Bedrock
    """
    print("\n🧪 Testing Your Existing Chatbot")
    print("=" * 40)
    
    test_instructions = """
    To test if your existing chatbot is using Bedrock:
    
    1. Start your Flask app
    2. Go to your existing chatbot interface
    3. Ask a question like: "What is sleep apnea?"
    4. Look for these signs:
       ✅ Response mentions specific medical details
       ✅ Response includes citations or sources
       ✅ Response takes 2-10 seconds (Bedrock processing time)
       ✅ Response quality is high and medical
    
    If you see these signs, your chatbot is already using Bedrock!
    If not, you need to integrate the code above.
    """
    
    print(test_instructions)

if __name__ == "__main__":
    integrate_bedrock_with_existing_chatbot()
    test_existing_chatbot_with_bedrock()
