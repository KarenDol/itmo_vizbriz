"""
Bedrock Vector Database Routes
Blueprint for accessing Bedrock knowledge base and vector database functionality
"""

from flask import Blueprint, request, jsonify
from flask_login import login_required
import boto3
import json
import logging
from botocore.exceptions import ClientError
from flask_app.services.bedrock_service import BedrockService

# Create blueprint
bedrock_vector_bp = Blueprint('bedrock_vector', __name__)

# Get logger
logger = logging.getLogger(__name__)

# Bedrock configuration
BEDROCK_KNOWLEDGE_BASE_ID = "RMBAKBVMLL"  # Your knowledge base ID (from the image)
BEDROCK_KNOWLEDGE_BASE_NAME = "vizbriz-osa"  # Your knowledge base name
BEDROCK_REGION = "us-east-2"  # Your region

# Use BedrockService for model ID
bedrock_service = BedrockService()
BEDROCK_MODEL_ID = bedrock_service.MODELS[bedrock_service.DEFAULT_MODEL]

# Initialize Bedrock client
try:
    bedrock_agent_runtime = boto3.client(
        'bedrock-agent-runtime',
        region_name=BEDROCK_REGION
    )
    bedrock = boto3.client(
        'bedrock',
        region_name=BEDROCK_REGION
    )
    logger.info("Bedrock clients initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Bedrock clients: {str(e)}")
    bedrock_agent_runtime = None
    bedrock = None


@bedrock_vector_bp.route('/bedrock/query', methods=['POST'])
@login_required
def query_knowledge_base():
    """Query the Bedrock knowledge base with a user question"""
    try:
        if not bedrock_agent_runtime:
            return jsonify({
                'success': False,
                'message': 'Bedrock service not available'
            }), 500
        
        data = request.get_json()
        if not data or 'query' not in data:
            return jsonify({
                'success': False,
                'message': 'Query is required'
            }), 400
        
        query = data['query']
        logger.info(f"Processing Bedrock query: {query}")
        
        # Query the knowledge base with proper configuration
        response = bedrock_agent_runtime.retrieve_and_generate(
            input={
                'text': query
            },
            retrieveAndGenerateConfiguration={
                'knowledgeBaseId': BEDROCK_KNOWLEDGE_BASE_ID,
                'modelArn': f"arn:aws:bedrock:{BEDROCK_REGION}::foundation-model/{BEDROCK_MODEL_ID}",
                'type': 'KNOWLEDGE_BASE',
                'generationConfiguration': {'temperature': 0.2},
                'retrievalConfiguration': {
                    'vectorSearchConfiguration': {
                        'numberOfResults': 6, 
                        'overrideSearchType': 'HYBRID'
                    }
                }
            }
        )
        
        # Extract the response
        generated_text = response.get('output', {}).get('text', '')
        citations = response.get('citations', [])
        
        logger.info(f"Bedrock query successful, response length: {len(generated_text)}")
        
        return jsonify({
            'success': True,
            'response': generated_text,
            'citations': citations,
            'query': query
        })
        
    except ClientError as e:
        logger.error(f"Bedrock ClientError: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Bedrock service error: {str(e)}'
        }), 500
    except Exception as e:
        logger.error(f"Error querying Bedrock knowledge base: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error processing query: {str(e)}'
        }), 500


@bedrock_vector_bp.route('/bedrock/retrieve', methods=['POST'])
@login_required
def retrieve_documents():
    """Retrieve relevant documents from the knowledge base without generation"""
    try:
        if not bedrock_agent_runtime:
            return jsonify({
                'success': False,
                'message': 'Bedrock service not available'
            }), 500
        
        data = request.get_json()
        if not data or 'query' not in data:
            return jsonify({
                'success': False,
                'message': 'Query is required'
            }), 400
        
        query = data['query']
        max_results = data.get('max_results', 5)
        
        logger.info(f"Retrieving documents for query: {query}")
        
        # Retrieve documents from knowledge base
        response = bedrock_agent_runtime.retrieve(
            knowledgeBaseId=BEDROCK_KNOWLEDGE_BASE_ID,
            retrievalQuery={
                'text': query
            },
            retrievalConfiguration={
                'vectorSearchConfiguration': {
                    'numberOfResults': max_results
                }
            }
        )
        
        # Extract retrieved documents
        retrieved_references = response.get('retrievedReferences', [])
        
        # Format the results
        documents = []
        for ref in retrieved_references:
            content = ref.get('content', {})
            location = ref.get('location', {})
            
            documents.append({
                'content': content.get('text', ''),
                'source': location.get('s3Location', {}).get('uri', 'Unknown'),
                'score': ref.get('score', 0.0)
            })
        
        logger.info(f"Retrieved {len(documents)} documents")
        
        return jsonify({
            'success': True,
            'documents': documents,
            'query': query,
            'total_results': len(documents)
        })
        
    except ClientError as e:
        logger.error(f"Bedrock ClientError: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Bedrock service error: {str(e)}'
        }), 500
    except Exception as e:
        logger.error(f"Error retrieving documents: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error retrieving documents: {str(e)}'
        }), 500


@bedrock_vector_bp.route('/bedrock/chat', methods=['POST'])
@login_required
def chat_with_knowledge_base():
    """Chat with the knowledge base using conversation context"""
    try:
        if not bedrock_agent_runtime:
            return jsonify({
                'success': False,
                'message': 'Bedrock service not available'
            }), 500
        
        data = request.get_json()
        if not data or 'message' not in data:
            return jsonify({
                'success': False,
                'message': 'Message is required'
            }), 400
        
        message = data['message']
        session_id = data.get('session_id', 'default_session')
        
        logger.info(f"Processing chat message: {message}")
        
        # Use retrieve and generate for conversational AI with proper configuration
        response = bedrock_agent_runtime.retrieve_and_generate(
            input={
                'text': message
            },
            retrieveAndGenerateConfiguration={
                'knowledgeBaseId': BEDROCK_KNOWLEDGE_BASE_ID,
                'modelArn': f"arn:aws:bedrock:{BEDROCK_REGION}::foundation-model/{BEDROCK_MODEL_ID}",
                'type': 'KNOWLEDGE_BASE',
                'generationConfiguration': {'temperature': 0.2},
                'retrievalConfiguration': {
                    'vectorSearchConfiguration': {
                        'numberOfResults': 6, 
                        'overrideSearchType': 'HYBRID'
                    }
                }
            },
            sessionId=session_id
        )
        
        # Extract the response
        generated_text = response.get('output', {}).get('text', '')
        citations = response.get('citations', [])
        session_id = response.get('sessionId', session_id)
        
        logger.info(f"Chat response generated, length: {len(generated_text)}")
        
        return jsonify({
            'success': True,
            'response': generated_text,
            'citations': citations,
            'session_id': session_id,
            'message': message
        })
        
    except ClientError as e:
        logger.error(f"Bedrock ClientError: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Bedrock service error: {str(e)}'
        }), 500
    except Exception as e:
        logger.error(f"Error in chat with knowledge base: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error processing chat: {str(e)}'
        }), 500


@bedrock_vector_bp.route('/bedrock/status', methods=['GET'])
@login_required
def get_knowledge_base_status():
    """Get the status of the knowledge base"""
    try:
        if not bedrock:
            return jsonify({
                'success': False,
                'message': 'Bedrock service not available'
            }), 500
        
        # Get knowledge base details
        response = bedrock.get_knowledge_base(
            knowledgeBaseId=BEDROCK_KNOWLEDGE_BASE_ID
        )
        
        knowledge_base = response.get('knowledgeBase', {})
        
        return jsonify({
            'success': True,
            'knowledge_base': {
                'id': knowledge_base.get('knowledgeBaseId'),
                'name': knowledge_base.get('name'),
                'status': knowledge_base.get('status'),
                'description': knowledge_base.get('description'),
                'created_at': knowledge_base.get('createdAt'),
                'updated_at': knowledge_base.get('updatedAt')
            }
        })
        
    except ClientError as e:
        logger.error(f"Bedrock ClientError: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Bedrock service error: {str(e)}'
        }), 500
    except Exception as e:
        logger.error(f"Error getting knowledge base status: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error getting status: {str(e)}'
        }), 500


@bedrock_vector_bp.route('/bedrock/test-kb-simple', methods=['POST'])
@login_required
def test_knowledge_base_simple():
    """Simple test endpoint to test knowledge base query with current code"""
    try:
        from flask_app.services.bedrock_service import BedrockService
        
        data = request.get_json() or {}
        query = data.get('query', 'What is the Lambert protocol?')
        patient_id = data.get('patient_id')
        
        logger.info(f"Testing knowledge base query: {query}")
        
        bedrock_service = BedrockService()
        result = bedrock_service.query_knowledge_base(
            query=query,
            patient_id=patient_id,
            max_results=6
        )
        
        return jsonify({
            'success': result.get('success', False),
            'response': result.get('response', ''),
            'citations': result.get('citations', []),
            'error': result.get('error'),
            'query': query,
            'patient_id': patient_id
        })
        
    except Exception as e:
        logger.error(f"Error in knowledge base test: {str(e)}")
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

@bedrock_vector_bp.route('/bedrock/test', methods=['GET'])
@login_required
def test_bedrock_connection():
    """Test the Bedrock connection and knowledge base"""
    try:
        if not bedrock_agent_runtime:
            return jsonify({
                'success': False,
                'message': 'Bedrock service not available'
            }), 500
        
        # Test with a simple query
        test_query = "What is sleep apnea?"
        
        response = bedrock_agent_runtime.retrieve_and_generate(
            input={
                'text': test_query
            },
            retrieveAndGenerateConfiguration={
                'knowledgeBaseId': BEDROCK_KNOWLEDGE_BASE_ID,
                'modelArn': f"arn:aws:bedrock:{BEDROCK_REGION}::foundation-model/{BEDROCK_MODEL_ID}",
                'type': 'KNOWLEDGE_BASE',
                'generationConfiguration': {'temperature': 0.2},
                'retrievalConfiguration': {
                    'vectorSearchConfiguration': {
                        'numberOfResults': 6, 
                        'overrideSearchType': 'HYBRID'
                    }
                }
            }
        )
        
        generated_text = response.get('output', {}).get('text', '')
        
        return jsonify({
            'success': True,
            'message': 'Bedrock connection successful',
            'test_query': test_query,
            'response_length': len(generated_text),
            'knowledge_base_id': BEDROCK_KNOWLEDGE_BASE_ID,
            'knowledge_base_name': BEDROCK_KNOWLEDGE_BASE_NAME
        })
        
    except Exception as e:
        logger.error(f"Bedrock connection test failed: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Bedrock connection test failed: {str(e)}'
        }), 500


@bedrock_vector_bp.route('/bedrock/public-test', methods=['GET'])
def public_test_bedrock_connection():
    """Public test endpoint for Bedrock connection (no authentication required)"""
    try:
        if not bedrock_agent_runtime:
            return jsonify({
                'success': False,
                'message': 'Bedrock service not available'
            }), 500
        
        return jsonify({
            'success': True,
            'message': 'Bedrock connection successful',
            'knowledge_base_id': BEDROCK_KNOWLEDGE_BASE_ID,
            'knowledge_base_name': BEDROCK_KNOWLEDGE_BASE_NAME,
            'region': BEDROCK_REGION,
            'model': BEDROCK_MODEL_ID
        })
        
    except Exception as e:
        logger.error(f"Bedrock public test failed: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Bedrock public test failed: {str(e)}'
        }), 500


@bedrock_vector_bp.route('/bedrock/patient-query', methods=['POST'])
@login_required
def query_patient_specific():
    """Query the knowledge base for a specific patient with metadata filtering"""
    try:
        if not bedrock_agent_runtime:
            return jsonify({
                'success': False,
                'message': 'Bedrock service not available'
            }), 500
        
        data = request.get_json()
        if not data or 'query' not in data or 'patient_id' not in data:
            return jsonify({
                'success': False,
                'message': 'Query and patient_id are required'
            }), 400
        
        query = data['query']
        patient_id = data['patient_id']
        temperature = data.get('temperature', 0.2)
        max_results = data.get('max_results', 6)
        
        logger.info(f"Processing patient-specific query for patient {patient_id}: {query}")
        
        # Query the knowledge base with patient-specific metadata filtering
        response = bedrock_agent_runtime.retrieve_and_generate(
            input={
                'text': query
            },
            retrieveAndGenerateConfiguration={
                'knowledgeBaseId': BEDROCK_KNOWLEDGE_BASE_ID,
                'modelArn': f"arn:aws:bedrock:{BEDROCK_REGION}::foundation-model/{BEDROCK_MODEL_ID}",
                'type': 'KNOWLEDGE_BASE',
                'generationConfiguration': {'temperature': temperature},
                'retrievalConfiguration': {
                    'vectorSearchConfiguration': {
                        'numberOfResults': max_results, 
                        'overrideSearchType': 'HYBRID'
                    },
                    'metadataFilter': {
                        'equals': {
                            'key': 'patient_id', 
                            'value': str(patient_id)
                        }
                    }
                }
            }
        )
        
        # Extract the response
        generated_text = response.get('output', {}).get('text', '')
        citations = response.get('citations', [])
        
        logger.info(f"Patient-specific query successful for patient {patient_id}, response length: {len(generated_text)}")
        
        return jsonify({
            'success': True,
            'response': generated_text,
            'citations': citations,
            'query': query,
            'patient_id': patient_id
        })
        
    except ClientError as e:
        logger.error(f"Bedrock ClientError: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Bedrock service error: {str(e)}'
        }), 500
    except Exception as e:
        logger.error(f"Error in patient-specific query: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error processing patient query: {str(e)}'
        }), 500
