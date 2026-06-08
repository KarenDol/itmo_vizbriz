from flask import (
    Blueprint,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_app.extensions import db
from flask_login import login_required, current_user
from ..models import db, Patient, File, Dentist, AdminFile, Claim, Comment, StatusOption, PatientComment, Clinic, dentist_clinic_association
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func, desc
from datetime import datetime, timedelta
import logging
import os
from werkzeug.utils import secure_filename
import zipfile
from io import BytesIO
import mimetypes
import trimesh
import numpy as np
import json
import traceback
from .conversion_quiz_agent import analyze_quiz
from flask_app.config.manifest_config import get_manifest_definition
import mysql.connector
from collections import defaultdict
from sqlalchemy import text
from flask_app.config.action_manifest import get_all_actions
from flask_app.routes.osaagent_helpers import (
    S3_CLIENT,
    get_patient_status_from_bedrock,
    query_bedrock_claude,
    query_bedrock_claude_enhanced,
)
from flask_app.routes.osaagent_chat import register_chat_routes

# Set up logging
osaagent = Blueprint('osaagent', __name__)

logger = logging.getLogger(__name__)

s3_client = S3_CLIENT

_chat_exports = register_chat_routes(osaagent, s3_client)
globals().update(_chat_exports)

# MOVED TO conversion_quiz_agent.py - keeping for reference
# @osaagent.route('/quiz', methods=['GET'])
# def show_quiz():
#     from flask_app.models import DSO, Clinic
#     
#     # Get DSO parameter from URL (default to 1 if not provided)
#     dso_id = request.args.get('dso_id', 1, type=int)
#     
#     # Get DSO information
#     dso = DSO.query.get(dso_id)
#     if not dso:
#         # Fallback to first available DSO
#         dso = DSO.query.first()
#     
#     # Get clinics for this DSO
#     clinics = []
#     if dso:
#         clinics = Clinic.query.filter_by(dso_id=dso.id, status='active').all()
#     
#     return render_template('conversion_quiz.html', dso=dso, clinics=clinics)

@osaagent.route('/agent/analyze_quiz', methods=['POST'])
@login_required
def analyze_quiz_route():
    try:
        data = request.get_json()
        quiz_input = data.get("input", "")
        if not quiz_input:
            return jsonify({"success": False, "message": "Missing input"}), 400

        result = analyze_quiz(quiz_input)

        try:
            result_json = json.loads(result["result"])
        except Exception:
            result_json = {"raw_output": result["result"]}

        return jsonify({
            "success": result["success"],
            "result": result_json
        })
    except Exception as e:
        logger.error(f"Error in /agent/analyze_quiz: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

# Bedrock Integration Endpoints

@osaagent.route('/bedrock/test_connection', methods=['GET'])
@login_required
def test_bedrock_connection():
    """Test Bedrock Claude connection"""
    try:
        test_prompt = "Hello, this is a test message. Please respond with 'Connection successful' if you can read this."
        result = query_bedrock_claude(None, {}, test_prompt, max_tokens=50, temperature=0.1)
        
        if result["success"]:
            return jsonify({
                "success": True,
                "message": "Bedrock connection successful",
                "response": result["response"]
            })
        else:
            return jsonify({
                "success": False,
                "message": "Bedrock connection failed",
                "error": result["message"]
            }), 500
            
    except Exception as e:
        logger.error(f"Error testing Bedrock connection: {e}")
        return jsonify({
            "success": False,
            "message": f"Error testing Bedrock connection: {str(e)}"
        }), 500

@osaagent.route('/bedrock/patient_status/<int:patient_id>', methods=['GET'])
@login_required
def get_patient_status_bedrock(patient_id):
    """Get patient status using Bedrock Claude"""
    try:
        # Get manifest content from S3 (if available)
        manifest_content = None
        try:
            manifest_key = "treatment-manifest.json"  # Adjust path as needed
            manifest_obj = s3_client.get_object(
                Bucket=os.getenv('S3_BUCKET_NAME'),
                Key=manifest_key
            )
            manifest_content = manifest_obj['Body'].read().decode('utf-8')
        except Exception as e:
            logger.warning(f"Could not fetch manifest file: {e}")
        
        # Get patient file content from S3 (if available)
        patient_file_content = None
        try:
            patient_file_key = f"patients/{patient_id}/patient-status.json"  # Adjust path as needed
            patient_obj = s3_client.get_object(
                Bucket=os.getenv('S3_BUCKET_NAME'),
                Key=patient_file_key
            )
            patient_file_content = patient_obj['Body'].read().decode('utf-8')
        except Exception as e:
            logger.warning(f"Could not fetch patient file: {e}")
        
        # Get status from Bedrock
        result = get_patient_status_from_bedrock(
            patient_id, 
            manifest_content, 
            patient_file_content
        )
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error getting patient status from Bedrock: {e}")
        return jsonify({
            "success": False,
            "message": f"Error: {str(e)}"
        }), 500

@osaagent.route('/bedrock/upload_manifest', methods=['POST'])
@login_required
def upload_manifest_file():
    """Upload manifest file to S3"""
    try:
        if 'file' not in request.files:
            return jsonify({"success": False, "message": "No file provided"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"success": False, "message": "No file selected"}), 400
        
        # Validate file type
        if not file.filename.endswith('.json'):
            return jsonify({"success": False, "message": "Only JSON files are allowed"}), 400
        
        # Upload to S3
        manifest_key = "treatment-manifest.json"
        s3_client.put_object(
            Bucket=os.getenv('S3_BUCKET_NAME'),
            Key=manifest_key,
            Body=file.read(),
            ContentType='application/json'
        )
        
        return jsonify({
            "success": True,
            "message": "Manifest file uploaded successfully",
            "key": manifest_key
        })
        
    except Exception as e:
        logger.error(f"Error uploading manifest file: {e}")
        return jsonify({
            "success": False,
            "message": f"Error uploading manifest file: {str(e)}"
        }), 500

@osaagent.route('/bedrock/upload_patient_file/<int:patient_id>', methods=['POST'])
@login_required
def upload_patient_file(patient_id):
    """Upload patient status file to S3"""
    try:
        if 'file' not in request.files:
            return jsonify({"success": False, "message": "No file provided"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"success": False, "message": "No file selected"}), 400
        
        # Validate file type
        if not file.filename.endswith('.json'):
            return jsonify({"success": False, "message": "Only JSON files are allowed"}), 400
        
        # Upload to S3
        patient_file_key = f"patients/{patient_id}/patient-status.json"
        s3_client.put_object(
            Bucket=os.getenv('S3_BUCKET_NAME'),
            Key=patient_file_key,
            Body=file.read(),
            ContentType='application/json'
        )
        
        return jsonify({
            "success": True,
            "message": "Patient file uploaded successfully",
            "key": patient_file_key
        })
        
    except Exception as e:
        logger.error(f"Error uploading patient file: {e}")
        return jsonify({
            "success": False,
            "message": f"Error uploading patient file: {str(e)}"
        }), 500

@osaagent.route('/bedrock/upload_policy/<int:patient_id>', methods=['POST'])
@login_required
def upload_patient_policy(patient_id):
    """Upload per-patient clinic policy JSON to S3 under manifests folder"""
    try:
        if 'file' not in request.files:
            return jsonify({"success": False, "message": "No file provided"}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({"success": False, "message": "No file selected"}), 400

        if not file.filename.endswith('.json'):
            return jsonify({"success": False, "message": "Only JSON files are allowed"}), 400

        s3_key = f"patients/{patient_id}/manifests/osa_policy_v2.json"
        s3_client.put_object(
            Bucket=os.getenv('S3_BUCKET_NAME'),
            Key=s3_key,
            Body=file.read(),
            ContentType='application/json',
            CacheControl='no-cache'
        )

        return jsonify({
            "success": True,
            "message": "Policy uploaded successfully",
            "key": s3_key
        })
    except Exception as e:
        logger.error(f"Error uploading patient policy: {e}")
        return jsonify({
            "success": False,
            "message": f"Error uploading policy: {str(e)}"
        }), 500

@osaagent.route('/bedrock/simple_query', methods=['POST'])
@login_required
def simple_bedrock_query():
    """Simple query endpoint for testing Bedrock Claude"""
    try:
        data = request.get_json()
        if not data or 'prompt' not in data:
            return jsonify({"success": False, "message": "Prompt is required"}), 400
        
        prompt = data['prompt']
        max_tokens = data.get('max_tokens', 300)
        temperature = data.get('temperature', 0.7)
        
        result = query_bedrock_claude(None, {}, prompt, max_tokens, temperature)
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error in simple Bedrock query: {e}")
        return jsonify({
            "success": False,
            "message": f"Error: {str(e)}"
        }), 500

@osaagent.route('/bedrock/test_browser/<int:patient_id>', methods=['GET'])
@login_required
def test_bedrock_browser(patient_id):
    """Test Bedrock integration with hardcoded files for browser viewing"""
    try:
        # Hardcoded manifest file (using the user's structure)
        hardcoded_manifest = [
            {
                "stage": "Quiz Completed",
                "prerequisites": ["quiz_filled"],
                "next_step": "Contact patient to initiate sleep test referral",
                "description": "Patient has completed the basic or advanced sleep quiz."
            },
            {
                "stage": "Sleep Test Consent",
                "prerequisites": ["quiz_filled", "patient_agreed_to_sleep_test"],
                "next_step": "Schedule an online sleep test with a partner lab",
                "description": "Patient has agreed to proceed with sleep testing after initial quiz."
            },
            {
                "stage": "ENT Consultation Scheduled",
                "prerequisites": ["quiz_filled", "ent_consultation_scheduled"],
                "next_step": "Confirm ENT consult and request sleep test order",
                "description": "Patient has scheduled a consultation with a sleep specialist (ENT)."
            },
            {
                "stage": "Sleep Test Uploaded",
                "prerequisites": ["quiz_filled", "sleep_test_uploaded"],
                "next_step": "Schedule sleep test result review with sleep specialist",
                "description": "Home sleep test result has been uploaded."
            },
            {
                "stage": "Sleep Specialist Reviewed",
                "prerequisites": ["sleep_test_uploaded", "soap_note_received"],
                "next_step": "Route to dental team for treatment planning",
                "description": "SOAP note received from specialist; OSA diagnosis confirmed."
            },
            {
                "stage": "CBCT Uploaded",
                "prerequisites": ["cbct_uploaded"],
                "next_step": "Review airway and structural imaging before appliance planning",
                "description": "CBCT imaging file has been received."
            },
            {
                "stage": "Treatment Plan Ready",
                "prerequisites": ["soap_note_received", "cbct_uploaded", "treatment_plan_ready"],
                "next_step": "Confirm oral appliance order",
                "description": "Dentist has reviewed all inputs and generated a treatment plan."
            },
            {
                "stage": "Oral Appliance Ordered",
                "prerequisites": ["treatment_plan_ready", "appliance_ordered"],
                "next_step": "Schedule fitting appointment with dentist",
                "description": "Oral appliance has been ordered for the patient."
            },
            {
                "stage": "Device Fitted",
                "prerequisites": ["appliance_delivered"],
                "next_step": "Schedule 30-day follow-up and begin monitoring",
                "description": "Appliance was delivered and fitted at the clinic."
            },
            {
                "stage": "Follow-up Completed",
                "prerequisites": ["device_fitted", "followup_visit_completed"],
                "next_step": "Assess need for titration or adherence coaching",
                "description": "Dentist has completed initial post-fit follow-up consultation."
            }
        ]
        
        # Hardcoded patient status file (simulating a patient in "Sleep Test Uploaded" stage)
        hardcoded_patient_status = {
            "patient_id": patient_id,
            "current_stage": "Sleep Test Uploaded",
            "completed_prerequisites": ["quiz_filled", "sleep_test_uploaded"],
            "pending_prerequisites": ["soap_note_received"],
            "completed_stages": ["Quiz Completed", "Sleep Test Consent", "Sleep Test Uploaded"],
            "next_stage": "Sleep Specialist Reviewed",
            "last_updated": datetime.now().isoformat(),
            "notes": "Patient uploaded sleep test results. Waiting for SOAP note from sleep specialist.",
            "files_uploaded": {
                "sleep_test": f"patients/{patient_id}/medical/sleep_test_results.pdf",
                "quiz": f"patients/{patient_id}/questionnaire/quiz_completed.json"
            },
            "diagnosis": {
                "osa_severity": "Moderate",
                "ahi_score": 18.5,
                "recommended_appliance": "Mandibular Advancement Device"
            }
        }
        
        # Get patient information (or use hardcoded data if patient not found)
        patient = Patient.query.get(patient_id)
        if not patient:
            # Use hardcoded patient data for testing
            patient_name = f"Test Patient {patient_id}"
        else:
            patient_name = patient.name
        
        # Convert to JSON strings for the function
        manifest_content = json.dumps(hardcoded_manifest, indent=2)
        patient_file_content = json.dumps(hardcoded_patient_status, indent=2)
        
        # Get status from Bedrock
        result = get_patient_status_from_bedrock(
            patient_id, 
            manifest_content, 
            patient_file_content,
            patient_name
        )
        
        if result["success"]:
            # Extract the Claude response text
            status_analysis = result.get('status_analysis', {})
            claude_response = "No response available"
            if status_analysis:
                content = status_analysis.get('content', [{}])[0]
                claude_response = content.get('text', 'No text available')
            
            return render_template('bedrock_test_result.html',
                                 success=True,
                                 patient_id=patient_id,
                                 patient_name=patient_name,
                                 manifest_data=hardcoded_manifest,
                                 patient_data=hardcoded_patient_status,
                                 manifest_json=json.dumps(hardcoded_manifest, indent=2),
                                 patient_json=json.dumps(hardcoded_patient_status, indent=2),
                                 claude_response=claude_response,
                                 timestamp=result.get('timestamp'),
                                 manifest_stages=result.get('manifest_stages', 0),
                                 patient_data_available=result.get('patient_data_available', False))
        else:
            return render_template('bedrock_test_result.html',
                                 success=False,
                                 error=result.get('message', 'Unknown error'),
                                 patient_id=patient_id,
                                 patient_name=patient_name)
        
    except Exception as e:
        logger.error(f"Error in browser test: {e}")
        return render_template('bedrock_test_result.html',
                             success=False,
                             error=f"Error: {str(e)}",
                             patient_id=patient_id)

@osaagent.route('/bedrock/test_advanced/<int:patient_id>', methods=['GET'])
@login_required
def test_bedrock_advanced(patient_id):
    """Test Bedrock integration with advanced patient scenario for browser viewing"""
    try:
        # Same hardcoded manifest as above
        hardcoded_manifest = [
            {
                "stage": "Quiz Completed",
                "prerequisites": ["quiz_filled"],
                "next_step": "Contact patient to initiate sleep test referral",
                "description": "Patient has completed the basic or advanced sleep quiz."
            },
            {
                "stage": "Sleep Test Consent",
                "prerequisites": ["quiz_filled", "patient_agreed_to_sleep_test"],
                "next_step": "Schedule an online sleep test with a partner lab",
                "description": "Patient has agreed to proceed with sleep testing after initial quiz."
            },
            {
                "stage": "ENT Consultation Scheduled",
                "prerequisites": ["quiz_filled", "ent_consultation_scheduled"],
                "next_step": "Confirm ENT consult and request sleep test order",
                "description": "Patient has scheduled a consultation with a sleep specialist (ENT)."
            },
            {
                "stage": "Sleep Test Uploaded",
                "prerequisites": ["quiz_filled", "sleep_test_uploaded"],
                "next_step": "Schedule sleep test result review with sleep specialist",
                "description": "Home sleep test result has been uploaded."
            },
            {
                "stage": "Sleep Specialist Reviewed",
                "prerequisites": ["sleep_test_uploaded", "soap_note_received"],
                "next_step": "Route to dental team for treatment planning",
                "description": "SOAP note received from specialist; OSA diagnosis confirmed."
            },
            {
                "stage": "CBCT Uploaded",
                "prerequisites": ["cbct_uploaded"],
                "next_step": "Review airway and structural imaging before appliance planning",
                "description": "CBCT imaging file has been received."
            },
            {
                "stage": "Treatment Plan Ready",
                "prerequisites": ["soap_note_received", "cbct_uploaded", "treatment_plan_ready"],
                "next_step": "Confirm oral appliance order",
                "description": "Dentist has reviewed all inputs and generated a treatment plan."
            },
            {
                "stage": "Oral Appliance Ordered",
                "prerequisites": ["treatment_plan_ready", "appliance_ordered"],
                "next_step": "Schedule fitting appointment with dentist",
                "description": "Oral appliance has been ordered for the patient."
            },
            {
                "stage": "Device Fitted",
                "prerequisites": ["appliance_delivered"],
                "next_step": "Schedule 30-day follow-up and begin monitoring",
                "description": "Appliance was delivered and fitted at the clinic."
            },
            {
                "stage": "Follow-up Completed",
                "prerequisites": ["device_fitted", "followup_visit_completed"],
                "next_step": "Assess need for titration or adherence coaching",
                "description": "Dentist has completed initial post-fit follow-up consultation."
            }
        ]
        
        # Advanced patient status (patient in "Treatment Plan Ready" stage)
        advanced_patient_status = {
            "patient_id": patient_id,
            "current_stage": "Treatment Plan Ready",
            "completed_prerequisites": ["soap_note_received", "cbct_uploaded", "treatment_plan_ready"],
            "pending_prerequisites": ["appliance_ordered"],
            "completed_stages": [
                "Quiz Completed", 
                "Sleep Test Consent", 
                "Sleep Test Uploaded", 
                "Sleep Specialist Reviewed", 
                "CBCT Uploaded", 
                "Treatment Plan Ready"
            ],
            "next_stage": "Oral Appliance Ordered",
            "last_updated": datetime.now().isoformat(),
            "notes": "All diagnostic materials received. Treatment plan approved by patient. Ready to order appliance.",
            "files_uploaded": {
                "sleep_test": f"patients/{patient_id}/medical/sleep_test_results.pdf",
                "soap_note": f"patients/{patient_id}/medical/soap_note.pdf",
                "cbct": f"patients/{patient_id}/imaging/cbct/scan.dcm",
                "treatment_plan": f"patients/{patient_id}/treatment/treatment_plan.pdf"
            },
            "diagnosis": {
                "osa_severity": "Moderate",
                "ahi_score": 18.5,
                "recommended_appliance": "Mandibular Advancement Device"
            }
        }
        
        # Get patient information (or use hardcoded data if patient not found)
        patient = Patient.query.get(patient_id)
        if not patient:
            # Use hardcoded patient data for testing
            patient_name = f"Test Patient {patient_id}"
        else:
            patient_name = patient.name
        
        # Convert to JSON strings for the function
        manifest_content = json.dumps(hardcoded_manifest, indent=2)
        patient_file_content = json.dumps(advanced_patient_status, indent=2)
        
        # Get status from Bedrock
        result = get_patient_status_from_bedrock(
            patient_id, 
            manifest_content, 
            patient_file_content,
            patient_name
        )
        
        if result["success"]:
            # Extract the Claude response text
            status_analysis = result.get('status_analysis', {})
            claude_response = "No response available"
            if status_analysis:
                content = status_analysis.get('content', [{}])[0]
                claude_response = content.get('text', 'No text available')
            
            return render_template('bedrock_test_result.html',
                                 success=True,
                                 patient_id=patient_id,
                                 patient_name=patient_name,
                                 manifest_data=hardcoded_manifest,
                                 patient_data=advanced_patient_status,
                                 manifest_json=json.dumps(hardcoded_manifest, indent=2),
                                 patient_json=json.dumps(advanced_patient_status, indent=2),
                                 claude_response=claude_response,
                                 timestamp=result.get('timestamp'),
                                 manifest_stages=result.get('manifest_stages', 0),
                                 patient_data_available=result.get('patient_data_available', False),
                                 scenario="Advanced")
        else:
            return render_template('bedrock_test_result.html',
                                 success=False,
                                 error=result.get('message', 'Unknown error'),
                                 patient_id=patient_id,
                                 patient_name=patient_name,
                                 scenario="Advanced")
        
    except Exception as e:
        logger.error(f"Error in advanced browser test: {e}")
        return render_template('bedrock_test_result.html',
                             success=False,
                             error=f"Error: {str(e)}",
                             patient_id=patient_id,
                             scenario="Advanced")


        
        # Convert patient_id to int if it's a string
        try:
            if isinstance(patient_id, str):
                patient_id = int(patient_id)
                logger.info(f"Converted patient_id to int: {patient_id}")
        except ValueError as e:
            logger.error(f"Failed to convert patient_id '{patient_id}' to int: {e}")
            return jsonify({"success": False, "message": f"Invalid patient_id format: {patient_id}"}), 400
        
        # Build manifests
        logger.info("Building patient manifest...")
        from flask_app.routes.main_routes import build_patient_manifest
        patient_manifest, demographics, age = build_patient_manifest(patient_id)
        
        logger.info(f"Patient manifest result: {patient_manifest}")
        logger.info(f"Demographics result: {demographics}")
        logger.info(f"Age result: {age}")
        
        logger.info("Getting definition manifest...")
        definition_manifest = get_manifest_definition()
        logger.info(f"Definition manifest result: {definition_manifest}")
        
        # Extract patient name
        patient_name = demographics.get('name', 'Unknown') if demographics else 'Unknown'
        logger.info(f"Extracted patient name: {patient_name}")
        
        # Build the LLM prompt with context about the patient journey
        logger.info("Building LLM prompt...")
        prompt = f"""
        You are Dr. Briz, an expert sleep medicine AI assistant specializing in OSA treatment workflow management.
        
        PATIENT INFORMATION:
        Name: {patient_name}
        ID: {patient_id}
        
        TREATMENT WORKFLOW STAGES:
        {json.dumps(definition_manifest, indent=2)}
        
        PATIENT CURRENT STATUS:
        {json.dumps(patient_manifest, indent=2)}
        
        USER QUESTION: {user_message}
        
        Please provide a helpful, professional response as Dr. Briz. Consider:
        1. The patient's current stage in the treatment workflow
        2. What steps have been completed and what's next
        3. Any specific recommendations based on their progress
        4. How you can assist with their OSA treatment journey
        
        Keep your response conversational, informative, and actionable for the dental team.
        """
        
        logger.info(f"Final prompt length: {len(prompt)} characters")
        logger.info(f"Prompt preview (first 500 chars): {prompt[:500]}...")
        
        # Query Bedrock Claude
        result = query_bedrock_claude(definition_manifest, patient_manifest, prompt, max_tokens=600, temperature=0.3)
        
        if result["success"]:
            # Extract the Claude response text
            status_analysis = result.get('response', {})
            claude_response = "I'm here to help with your patient's OSA treatment journey."
            
            if status_analysis:
                content = status_analysis.get('content', [{}])[0]
                claude_response = content.get('text', 'I\'m here to help with your patient\'s OSA treatment journey.')
            
            logger.info("=== BEDROCK CHAT ENDPOINT COMPLETED SUCCESSFULLY ===")
            
            return jsonify({
                "success": True,
                "response": claude_response,
                "patient_id": patient_id,
                "patient_name": patient_name
            })
        else:
            # Fallback response if Bedrock fails
            fallback_response = f"Hello! I'm Dr. Briz, your AI assistant. I can see that {patient_name} is currently in the OSA treatment workflow. I can help you with treatment planning, progress tracking, and answering questions about their case. What specific information would you like to know?"
            
            return jsonify({
                "success": True,
                "response": fallback_response,
                "patient_id": patient_id,
                "patient_name": patient_name
            })
        
    except Exception as e:
        logger.error(f"=== BEDROCK CHAT ENDPOINT ERROR ===")
        logger.error(f"Exception type: {type(e).__name__}")
        logger.error(f"Exception message: {str(e)}")
        logger.error(f"Exception traceback: {traceback.format_exc()}")
        return jsonify({
            "success": False, 
            "message": f"Internal server error: {str(e)}",
            "error_type": type(e).__name__
        }), 500

@osaagent.route('/api/definition_manifest', methods=['GET'])
def download_definition_manifest():
    definition_manifest = get_manifest_definition()
    json_str = json.dumps(definition_manifest, indent=2)
    return Response(
        json_str,
        mimetype='application/json',
        headers={"Content-Disposition": "attachment;filename=definition_manifest.json"}
    )

# Removed redundant patient_journey route - using patient_workflow_test in main_routes.py instead

@osaagent.route('/api/stage_files/<int:patient_id>/<stage_key>', methods=['GET'])
@login_required
def get_stage_files(patient_id, stage_key):
    """Get files for a specific stage"""
    try:
        logger.info(f"Getting stage files for patient {patient_id}, stage {stage_key}")
        
        from flask_app.models import File, AdminFile
        
        files = []
        
        # Get files based on stage key
        if stage_key == "quiz_completion":
            logger.info(f"Looking for questionnaire files for patient {patient_id}")
            # Get questionnaire files
            db_files = File.query.filter_by(patient_id=patient_id, subcategory='questionnaire').all()
            logger.info(f"Found {len(db_files)} questionnaire files")
            files = [
                {
                    "id": file.id,
                    "name": file.name,
                    "date": file.upload_date,
                    "description": f"Questionnaire file - {file.file_type}",
                    "file_type": file.file_type,
                    "s3_key": file.s3_key,
                    "download_url": file.s3_key,
                    "is_viewable": False
                }
                for file in db_files
            ]
        elif stage_key == "sleep_test_completed":
            logger.info(f"Looking for sleep test files for patient {patient_id}")
            # Get sleep test files
            db_files = File.query.filter_by(patient_id=patient_id, subcategory='sleep-test').all()
            logger.info(f"Found {len(db_files)} sleep test files")
            files = [
                {
                    "id": file.id,
                    "name": file.name,
                    "date": file.upload_date,
                    "description": f"Sleep test file - {file.file_type}",
                    "file_type": file.file_type,
                    "s3_key": file.s3_key,
                    "download_url": file.s3_key,
                    "is_viewable": False
                }
                for file in db_files
            ]
        elif stage_key == "clinical_data_available":
            logger.info(f"Looking for clinical data files for patient {patient_id}")
            # Get CBCT and intraoral scan files
            cbct_files = File.query.filter_by(patient_id=patient_id, subcategory='cbct').all()
            intraoral_files = File.query.filter_by(patient_id=patient_id, subcategory='intraoral-scan').all()
            cbct_admin_files = AdminFile.query.filter_by(patient_id=patient_id, file_category='cbct observations').all()
            
            logger.info(f"Found {len(cbct_files)} CBCT files, {len(intraoral_files)} intraoral files, {len(cbct_admin_files)} CBCT admin files")
            
            # Add CBCT files
            for file in cbct_files:
                files.append({
                    "id": file.id,
                    "name": file.name,
                    "date": file.upload_date,
                    "description": f"CBCT file - {file.file_type}",
                    "file_type": file.file_type,
                    "s3_key": file.s3_key,
                    "download_url": file.s3_key,
                    "is_viewable": False
                })
            
            # Add intraoral scan files
            for file in intraoral_files:
                files.append({
                    "id": file.id,
                    "name": file.name,
                    "date": file.upload_date,
                    "description": f"Intraoral scan - {file.file_type}",
                    "file_type": file.file_type,
                    "s3_key": file.s3_key,
                    "download_url": file.s3_key,
                    "is_viewable": False
                })
            
            # Add CBCT admin files
            for file in cbct_admin_files:
                files.append({
                    "id": file.id,
                    "name": file.name,
                    "date": file.upload_date,
                    "description": f"CBCT observation report - {file.file_type}",
                    "file_type": file.file_type,
                    "s3_key": file.s3_key,
                    "download_url": file.s3_key,
                    "is_viewable": False
                })
        else:
            logger.warning(f"Unknown stage key: {stage_key}")
        
        logger.info(f"Found {len(files)} total files for patient {patient_id}, stage {stage_key}")
        
        return jsonify({'files': files})
    except Exception as e:
        logger.error(f"Error getting stage files: {e}")
        logger.error(f"Exception type: {type(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

@osaagent.route('/patient_stage/<int:patient_id>/<stage_key>', methods=['POST'])
@login_required
def handle_stage_action(patient_id, stage_key):
    """Handle stage-specific actions from the modal"""
    try:
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({"success": False, "message": "Patient not found"}), 404
        
        # Get the action type from the request
        action_type = request.form.get('form_type') or request.json.get('form_type')
        
        if stage_key == 'osa_screening':
            return handle_quiz_status_update(patient_id, request)
        elif stage_key == 'diagnostic_imaging':
            return handle_file_upload(patient_id, request)
        elif stage_key == 'treatment_planning':
            return handle_treatment_planning(patient_id, request)
        elif stage_key == 'appliance_fitting':
            return handle_appliance_order(patient_id, request)
        elif stage_key in ['initial_consultation', 'follow_up']:
            return handle_consultation_schedule(patient_id, stage_key, request)
        else:
            return jsonify({"success": False, "message": f"Unknown stage: {stage_key}"}), 400
            
    except Exception as e:
        logger.error(f"Error handling stage action: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

def handle_quiz_status_update(patient_id, request):
    """Handle quiz status update"""
    try:
        quiz_status = request.json.get('quiz_status') if request.is_json else request.form.get('quiz_status')
        
        if not quiz_status:
            return jsonify({"success": False, "message": "Quiz status is required"}), 400
        
        # Update patient's quiz status in the database
        # You can add a quiz_status field to the Patient model or create a separate table
        # For now, we'll just return success
        logger.info(f"Updated quiz status for patient {patient_id}: {quiz_status}")
        
        return jsonify({
            "success": True,
            "message": f"Quiz status updated to: {quiz_status}",
            "quiz_status": quiz_status
        })
        
    except Exception as e:
        logger.error(f"Error updating quiz status: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

def handle_file_upload(patient_id, request):
    """Handle file upload for diagnostic imaging"""
    try:
        files = request.files.getlist('files')
        category = request.form.get('category', 'diagnostic')
        notes = request.form.get('notes', '')
        
        if not files:
            return jsonify({"success": False, "message": "No files provided"}), 400
        
        uploaded_files = []
        for file in files:
            if file and file.filename:
                # Save file to database
                filename = secure_filename(file.filename)
                file_data = File(
                    name=filename,
                    patient_id=patient_id,
                    category=category,
                    upload_date=datetime.now(),
                    notes=notes
                )
                db.session.add(file_data)
                uploaded_files.append(filename)
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": f"Successfully uploaded {len(uploaded_files)} files",
            "files": uploaded_files
        })
        
    except Exception as e:
        logger.error(f"Error uploading files: {e}")
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500

def handle_treatment_planning(patient_id, request):
    """Handle treatment planning completion"""
    try:
        completed_date = request.form.get('completed_date')
        completed_time = request.form.get('completed_time')
        treatment_plan = request.form.get('treatment_plan')
        recommendations = request.form.get('recommendations')
        
        if not all([completed_date, completed_time, treatment_plan]):
            return jsonify({"success": False, "message": "Missing required fields"}), 400
        
        # Combine date and time
        completed_datetime = datetime.strptime(f"{completed_date} {completed_time}", "%Y-%m-%d %H:%M")
        
        # Save treatment plan to database
        # You can create a TreatmentPlan model or add fields to Patient
        logger.info(f"Treatment planning completed for patient {patient_id}")
        logger.info(f"Plan: {treatment_plan}")
        logger.info(f"Recommendations: {recommendations}")
        
        return jsonify({
            "success": True,
            "message": "Treatment planning completed successfully",
            "completed_datetime": completed_datetime.isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error completing treatment planning: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

def handle_appliance_order(patient_id, request):
    """Handle appliance order placement"""
    try:
        device_type = request.form.get('device_type')
        device_name = request.form.get('device_name')
        fitting_date = request.form.get('fitting_date')
        notes = request.form.get('notes')
        
        if not all([device_type, device_name, fitting_date]):
            return jsonify({"success": False, "message": "Missing required fields"}), 400
        
        # Save appliance order to database
        # You can create an ApplianceOrder model
        logger.info(f"Appliance order placed for patient {patient_id}")
        logger.info(f"Device: {device_type} - {device_name}")
        logger.info(f"Fitting date: {fitting_date}")
        logger.info(f"Notes: {notes}")
        
        return jsonify({
            "success": True,
            "message": "Appliance order placed successfully",
            "device_type": device_type,
            "device_name": device_name,
            "fitting_date": fitting_date
        })
        
    except Exception as e:
        logger.error(f"Error placing appliance order: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

def handle_consultation_schedule(patient_id, stage_key, request):
    """Handle consultation scheduling"""
    try:
        scheduled_date = request.form.get('scheduled_date')
        scheduled_time = request.form.get('scheduled_time')
        consult_type = request.form.get('consult_type', stage_key)
        notes = request.form.get('notes')
        
        if not all([scheduled_date, scheduled_time]):
            return jsonify({"success": False, "message": "Missing required fields"}), 400
        
        # Combine date and time
        scheduled_datetime = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")
        
        # Save consultation schedule to database
        # You can create a ConsultationSchedule model
        logger.info(f"Consultation scheduled for patient {patient_id}")
        logger.info(f"Type: {consult_type}")
        logger.info(f"Date: {scheduled_datetime}")
        logger.info(f"Notes: {notes}")
        
        return jsonify({
            "success": True,
            "message": "Consultation scheduled successfully",
            "consult_type": consult_type,
            "scheduled_datetime": scheduled_datetime.isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error scheduling consultation: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

# NOTE: This route was removed to eliminate duplication with main_routes.py
# The patient_workflow_test route is now handled by main_routes.py
# This prevents confusion and ensures all functionality is in one place

@osaagent.route('/patient_management_dashboard')
@login_required
def patient_management_dashboard():
    """Patient management dashboard with comprehensive overview - OPTIMIZED VERSION"""
    try:
        
        # Get manifest definition for stages
        manifest = get_manifest_definition()
        
        # Apply the same security logic as patient_list
        logger.info(f"Fetching patients for dashboard with user access control...")
        
        # If the current user is an admin, they can see all patients
        if current_user.role == 'admin':
            patients = Patient.query.filter(Patient.status != 'Archived').order_by(desc(Patient.create_date)).all()
            logger.debug(f'Admin viewing all patients. Total patients found: {len(patients)}')
        
        elif current_user.role in ['Dentist', 'dentist', 'Dentists']:
            # Dentist can only see patients associated with the same clinic(s) as the dentist
            logger.debug(f'Dentist {current_user.name} attempting to view patient list based on clinic associations.')

            # Get the dentist's associated clinic IDs
            dentist_clinic_ids = current_user.get_clinic_ids()
            logger.debug(f'Dentist {current_user.name} is associated with clinics: {dentist_clinic_ids}')
            
            # Debug: Check if dentist has any DSO associations as fallback
            dentist_dso_ids = current_user.get_dso_ids() if hasattr(current_user, 'get_dso_ids') else []
            logger.debug(f'Dentist {current_user.name} (ID: {current_user.id}) is associated with DSOs: {dentist_dso_ids}')

            if dentist_clinic_ids:
                # Show patients from the dentist's associated clinics (handles multi-DSO clinics)
                logger.debug(f'Dentist works at clinics: {dentist_clinic_ids}')
                
                patients = (Patient.query
                            .filter(
                                db.or_(
                                    # Patients directly assigned to dentist's clinics
                                    Patient.clinic_id.in_(dentist_clinic_ids),
                                    # Patients whose dentists work at the same clinics
                                    db.and_(
                                        Patient.clinic_id.is_(None),
                                        Patient.dentist_id.isnot(None),
                                        db.exists().where(
                                            db.and_(
                                                dentist_clinic_association.c.dentist_id == Patient.dentist_id,
                                                dentist_clinic_association.c.clinic_id.in_(dentist_clinic_ids)
                                            )
                                        )
                                    )
                                ),
                                Patient.status != 'Archived'
                            )
                            .order_by(desc(Patient.create_date))
                            .all())
                
                logger.debug(f'Found {len(patients)} patients for dentist {current_user.name} in their associated clinics')
            else:
                # No clinic associations found - try DSO fallback
                logger.warning(f'Dentist {current_user.name} has no clinic associations, trying DSO fallback')
                
                if dentist_dso_ids:
                    # Fallback to DSO-based query
                    logger.debug('Using DSO fallback query')
                    patients = (Patient.query
                                .join(Dentist)
                                .join(Clinic, Patient.clinic_id == Clinic.id, isouter=True)
                                .filter(
                                    db.or_(
                                        Clinic.dso_id.in_(dentist_dso_ids),  # Patients in dentist's DSO clinics
                                        db.and_(Patient.clinic_id.is_(None), Dentist.DSO == getattr(current_user, 'DSO', None))  # Legacy patients
                                    ),
                                    Patient.status != 'Archived'
                                )
                                .order_by(desc(Patient.create_date))
                                .all())
                    logger.debug(f'DSO fallback found {len(patients)} patients')
                else:
                    # No associations at all
                    logger.warning(f'Dentist {current_user.name} has no clinic or DSO associations')
                    patients = []

            if not patients:
                logger.warning(f'No patients found for dentist: {current_user.name}')
            else:
                logger.debug(f'{len(patients)} patients found for dentist: {current_user.name}')
        
        else:
            flash('Unauthorized access', 'error')
            logger.warning(f'Unauthorized access attempt by user {current_user.name} with role {current_user.role}')
            return redirect(url_for('main.index'))
        
        logger.info(f"Found {len(patients)} patients for dashboard")
        
        # OPTIMIZATION 2: Batch load manifest data for all patients at once
        patient_ids = [p.id for p in patients]
        logger.info(f"Loading manifest data for {len(patient_ids)} patients: {patient_ids[:5]}...")
        manifest_data = get_batch_patient_manifest_data(patient_ids)
        logger.info(f"Loaded manifest data for {len(manifest_data)} patients")
        
        # OPTIMIZATION 3: Calculate metrics efficiently
        metrics = calculate_patient_metrics_optimized(patients, manifest, manifest_data)
        
        # OPTIMIZATION 4: Get patient list with pre-loaded manifest data
        patient_list = get_patient_list_with_stages_optimized(patients, manifest, manifest_data)
        
        pie_data = get_dashboard_pie_data(patients, manifest, manifest_data)
        
        # Generate unique stages for the filter dropdown from manifest definition
        unique_stages = [stage['stage_name'] for stage in manifest]
        # Add "Not Started" and "Unknown" for patients without stages
        unique_stages = ['Not Started', 'Unknown'] + unique_stages
        
        logger.info(f"Rendering dashboard with {len(patient_list)} patients, {len(unique_stages)} unique stages")
        
        return render_template('admin/patient_management_dashboard.html',
                             metrics=metrics,
                             patients=patient_list,
                             manifest=manifest,
                             pie_data=pie_data,
                             unique_stages=unique_stages)
        
    except Exception as e:
        logger.error(f"Error in admin patient management dashboard: {str(e)}")
        flash('Error loading patient management dashboard', 'error')
        return redirect(url_for('main.index'))

@osaagent.route('/api/patient-search')
@login_required
def patient_search():
    """API endpoint for patient search"""
    try:
        
        search_term = request.args.get('q', '').strip()
        if not search_term:
            return jsonify({'patients': []})
        
        # Apply the same security logic as patient_list
        if current_user.role == 'admin':
            # Admin can see all patients
            patients = Patient.query.filter(
                db.and_(
                    Patient.status != 'Archived',
                    db.or_(
                        Patient.name.ilike(f'%{search_term}%'),
                        Patient.email.ilike(f'%{search_term}%'),
                        Patient.id == search_term if search_term.isdigit() else False
                    )
                )
            ).order_by(desc(Patient.create_date)).limit(20).all()
        
        elif current_user.role in ['Dentist', 'dentist', 'Dentists']:
            # Dentist can only see patients associated with the same clinic(s) as the dentist
            dentist_clinic_ids = current_user.get_clinic_ids()
            dentist_dso_ids = current_user.get_dso_ids() if hasattr(current_user, 'get_dso_ids') else []
            
            if dentist_clinic_ids:
                patients = Patient.query.filter(
                    db.and_(
                        Patient.status != 'Archived',
                        db.or_(
                            # Patients directly assigned to dentist's clinics
                            Patient.clinic_id.in_(dentist_clinic_ids),
                            # Patients whose dentists work at the same clinics
                            db.and_(
                                Patient.clinic_id.is_(None),
                                Patient.dentist_id.isnot(None),
                                db.exists().where(
                                    db.and_(
                                        dentist_clinic_association.c.dentist_id == Patient.dentist_id,
                                        dentist_clinic_association.c.clinic_id.in_(dentist_clinic_ids)
                                    )
                                )
                            )
                        ),
                        db.or_(
                            Patient.name.ilike(f'%{search_term}%'),
                            Patient.email.ilike(f'%{search_term}%'),
                            Patient.id == search_term if search_term.isdigit() else False
                        )
                    )
                ).order_by(desc(Patient.create_date)).limit(20).all()
            else:
                # No clinic associations found - try DSO fallback
                if dentist_dso_ids:
                    patients = (Patient.query
                                .join(Dentist)
                                .join(Clinic, Patient.clinic_id == Clinic.id, isouter=True)
                                .filter(
                                    db.and_(
                                        Patient.status != 'Archived',
                                        db.or_(
                                            Clinic.dso_id.in_(dentist_dso_ids),
                                            db.and_(Patient.clinic_id.is_(None), Dentist.DSO == getattr(current_user, 'DSO', None))
                                        ),
                                        db.or_(
                                            Patient.name.ilike(f'%{search_term}%'),
                                            Patient.email.ilike(f'%{search_term}%'),
                                            Patient.id == search_term if search_term.isdigit() else False
                                        )
                                    )
                                )
                                .order_by(desc(Patient.create_date))
                                .limit(20)
                                .all())
                else:
                    patients = []
        else:
            return jsonify({'error': 'Unauthorized access'}), 403
        
        # Get manifest for stage information
        try:
            manifest = get_manifest_definition()
        except Exception as e:
            logger.error(f"Error getting manifest: {str(e)}")
            manifest = []
        
        # OPTIMIZATION: Batch load manifest data
        patient_ids = [p.id for p in patients]
        manifest_data = get_batch_patient_manifest_data(patient_ids)
        
        # Format patient data
        patient_data = []
        for patient in patients:
            try:
                patient_manifest = manifest_data.get(patient.id, {})
                latest_stage = get_latest_completed_stage_from_data(patient_manifest, manifest)
                next_stage = get_next_stage_for_patient_from_data(patient_manifest, manifest)
                
                # Calculate age from date of birth
                age = None
                if patient.dob:
                    from datetime import date
                    today = date.today()
                    age = today.year - patient.dob.year - ((today.month, today.day) < (patient.dob.month, patient.dob.day))
                
                # Get DSO and clinic information - prioritize patient's clinic first
                dso_name = None
                clinic_name = None
                
                # First, try to get DSO from patient's direct clinic assignment
                if patient.clinic:
                    clinic_name = patient.clinic.name
                    if patient.clinic.dso_info:
                        dso_name = patient.clinic.dso_info.name
                
                # Fallback: if patient has no clinic but has a dentist, use dentist's primary clinic
                if not dso_name and patient.dentist:
                    primary_clinic = patient.dentist.get_primary_clinic()
                    if primary_clinic:
                        if not clinic_name:
                            clinic_name = primary_clinic.name
                        if primary_clinic.dso_info:
                            dso_name = primary_clinic.dso_info.name
                
                patient_data.append({
                    'id': patient.id,
                    'name': patient.name or '',
                    'email': patient.email or '',
                    'phone': patient.phone or '',
                    'gender': patient.gender,
                    'age': age,
                    'dso_name': dso_name,
                    'clinic_name': clinic_name,
                    'created_at': patient.create_date.strftime('%Y-%m-%d %H:%M') if patient.create_date else '',
                    'latest_stage': latest_stage,
                    'next_stage': next_stage,
                    'workflow_url': url_for('main.patient_workflow_manifest', patient_id=patient.id)
                })
            except Exception as e:
                logger.error(f"Error processing patient {patient.id}: {str(e)}")
                # Add patient with minimal data if there's an error
                patient_data.append({
                    'id': patient.id,
                    'name': patient.name or '',
                    'email': patient.email or '',
                    'phone': patient.phone or '',
                    'gender': patient.gender,
                    'age': None,
                    'dso_name': None,
                    'clinic_name': None,
                    'created_at': patient.create_date.strftime('%Y-%m-%d %H:%M') if patient.create_date else '',
                    'latest_stage': None,
                    'next_stage': None,
                    'workflow_url': url_for('main.patient_workflow_manifest', patient_id=patient.id)
                })
        
        return jsonify({'patients': patient_data})
        
    except Exception as e:
        logger.error(f"Error in patient search: {str(e)}")
        return jsonify({'error': 'Search failed', 'details': str(e)}), 500

def calculate_patient_metrics(patients, manifest):
    """Calculate key metrics for the dashboard"""
    try:
        # Initialize counters
        new_patients = 0
        waiting_sleep_test = 0
        waiting_dental_consult = 0
        waiting_reports = 0
        
        # Define stage keys for categorization
        sleep_test_stages = ['sleep_study_scheduled', 'sleep_test_completed']
        dental_consult_stages = ['dental_sleep_doctor_consult_scheduled', 'met_with_dental_sleep_expert']
        report_stages = ['osa_report_ready', 'dental_approval_osa_report']
        
        for patient in patients:
            # Get patient's current stage from manifest
            patient_manifest = get_patient_manifest_data(patient.id)
            
            if not patient_manifest:
                # New patient (no manifest data)
                new_patients += 1
                continue
            
            # Find the latest completed stage
            latest_completed = None
            for stage in manifest:
                stage_key = stage['key']
                if stage_key in patient_manifest and patient_manifest[stage_key].get('is_completed'):
                    latest_completed = stage_key
            
            if latest_completed:
                if latest_completed in sleep_test_stages:
                    waiting_sleep_test += 1
                elif latest_completed in dental_consult_stages:
                    waiting_dental_consult += 1
                elif latest_completed in report_stages:
                    waiting_reports += 1
        
        return {
            'total_patients': len(patients),
            'new_patients': new_patients,
            'waiting_sleep_test': waiting_sleep_test,
            'waiting_dental_consult': waiting_dental_consult,
            'waiting_reports': waiting_reports
        }
        
    except Exception as e:
        logger.error(f"Error calculating patient metrics: {str(e)}")
        return {
            'total_patients': 0,
            'new_patients': 0,
            'waiting_sleep_test': 0,
            'waiting_dental_consult': 0,
            'waiting_reports': 0
        }

def get_patient_list_with_stages(patients, manifest):
    """Get patient list with stage information (LIFO order)"""
    try:
        patient_list = []
        
        for patient in patients:
            # Get patient's manifest data
            patient_manifest = get_patient_manifest_data(patient.id)
            
            # Get latest completed stage
            latest_stage = get_latest_completed_stage(patient.id, manifest)
            
            # Get next stage
            next_stage = get_next_stage_for_patient(patient.id, manifest)
            
            patient_data = {
                'id': patient.id,
                'name': patient.name,
                'email': patient.email,
                'created_at': patient.create_date,
                'latest_stage': latest_stage,
                'next_stage': next_stage,
                'workflow_url': url_for('main.patient_workflow_manifest', patient_id=patient.id)
            }
            
            patient_list.append(patient_data)
        
        # Sort by created_at descending (LIFO)
        patient_list.sort(key=lambda x: x['created_at'], reverse=True)
        
        return patient_list
        
    except Exception as e:
        logger.error(f"Error getting patient list with stages: {str(e)}")
        return []

def get_patient_manifest_data(patient_id):
    """Get patient manifest data from database"""
    conn = None
    try:
        # Database configuration
        DB_CONFIG = {
            'host': os.getenv('DB_HOST', 'vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com'),
            'user': os.getenv('DB_USERNAME', 'admin'),
            'password': os.getenv('DB_PASSWORD', 'Vizbriz2025!'),
            'database': os.getenv('DB_NAME', 'vizbriz'),
            'port': int(os.getenv('DB_PORT', '3306'))
        }
        
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # Get all manifest entries for this patient
        cursor.execute("""
            SELECT * FROM patient_manifest 
            WHERE patient_id = %s 
            ORDER BY stage_number
        """, (patient_id,))
        manifest_entries = cursor.fetchall()
        
        # Create a dictionary of manifest entries by stage_key
        manifest_dict = {}
        for entry in manifest_entries:
            manifest_dict[entry['stage_key']] = {
                'is_completed': entry.get('is_completed', False),
                'completion_date': entry.get('completion_date'),
                'status_message': entry.get('status_message', ''),
                'stage_data': entry.get('stage_data')
            }
        
        return manifest_dict
        
    except Exception as e:
        logger.error(f"Error getting patient manifest data: {str(e)}")
        return None
    finally:
        if conn:
            conn.close()

def get_latest_completed_stage(patient_id, manifest):
    """Get the latest completed stage for a patient"""
    try:
        patient_manifest = get_patient_manifest_data(patient_id)
        if not patient_manifest:
            return None
        
        # Find the highest stage number that is completed
        latest_completed = None
        for stage in manifest:
            stage_key = stage['key']
            if stage_key in patient_manifest and patient_manifest[stage_key].get('is_completed'):
                latest_completed = stage
        
        return latest_completed
        
    except Exception as e:
        logger.error(f"Error getting latest completed stage: {str(e)}")
        return None

def get_next_stage_for_patient(patient_id, manifest):
    """Get the next stage for a patient"""
    try:
        latest_stage = get_latest_completed_stage(patient_id, manifest)
        if not latest_stage:
            # If no completed stages, next stage is the first one
            return manifest[0] if manifest else None
        
        # Get the next stage from manifest
        current_stage_number = latest_stage.get('stage_number', 0)
        next_stage_number = current_stage_number + 1
        
        for stage in manifest:
            if stage.get('stage_number') == next_stage_number:
                return stage
        
        return None
        
    except Exception as e:
        logger.error(f"Error getting next stage for patient: {str(e)}")
        return None

@osaagent.route('/test-dashboard')
def test_dashboard():
    """Debug route to test patient data"""
    try:
        # Test basic patient query
        all_patients = Patient.query.all()
        logger.info(f"Total patients in database: {len(all_patients)}")
        
        # Test status distribution
        status_counts = {}
        for p in all_patients:
            status = p.status if hasattr(p, 'status') else 'No status'
            status_counts[status] = status_counts.get(status, 0) + 1
        
        # Test filtered query
        if hasattr(Patient, 'status'):
            filtered_patients = Patient.query.filter(Patient.status != 'Archived').all()
            logger.info(f"Patients after status filter: {len(filtered_patients)}")
        else:
            filtered_patients = all_patients
            logger.info("No status field found, using all patients")
        
        # Test with limit
        limited_patients = Patient.query.limit(10).all()
        logger.info(f"Limited patients (10): {len(limited_patients)}")
        
        return jsonify({
            'total_patients': len(all_patients),
            'status_distribution': status_counts,
            'filtered_patients': len(filtered_patients),
            'limited_patients': len(limited_patients),
            'sample_patients': [
                {
                    'id': p.id,
                    'name': p.name,
                    'email': p.email,
                    'status': getattr(p, 'status', 'No status'),
                    'create_date': str(p.create_date) if p.create_date else None
                }
                for p in limited_patients[:5]
            ]
        })
        
    except Exception as e:
        logger.error(f"Error in test dashboard: {e}")
        return jsonify({'error': str(e)}), 500

def get_batch_patient_manifest_data(patient_ids):
    """Get manifest data for multiple patients in a single database query - OPTIMIZED"""
    conn = None
    try:
        if not patient_ids:
            logger.warning("No patient IDs provided to get_batch_patient_manifest_data")
            return {}
        
        logger.info(f"Getting batch manifest data for {len(patient_ids)} patients")
        
        # Database configuration
        DB_CONFIG = {
            'host': os.getenv('DB_HOST', 'vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com'),
            'user': os.getenv('DB_USERNAME', 'admin'),
            'password': os.getenv('DB_PASSWORD', 'Vizbriz2025!'),
            'database': os.getenv('DB_NAME', 'vizbriz'),
            'port': int(os.getenv('DB_PORT', '3306'))
        }
        
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # Get all manifest entries for these patients in one query
        placeholders = ','.join(['%s'] * len(patient_ids))
        cursor.execute(f"""
            SELECT * FROM patient_manifest 
            WHERE patient_id IN ({placeholders})
            ORDER BY patient_id, stage_number
        """, patient_ids)
        manifest_entries = cursor.fetchall()
        logger.info(f"Found {len(manifest_entries)} manifest entries for {len(patient_ids)} patients")
        
        # Group by patient_id
        manifest_dict = {}
        for entry in manifest_entries:
            patient_id = entry['patient_id']
            if patient_id not in manifest_dict:
                manifest_dict[patient_id] = {}
            
            manifest_dict[patient_id][entry['stage_key']] = {
                'is_completed': entry.get('is_completed', False),
                'completion_date': entry.get('completion_date'),
                'status_message': entry.get('status_message', ''),
                'stage_data': entry.get('stage_data')
            }
        
        logger.info(f"Returning manifest data for {len(manifest_dict)} patients")
        return manifest_dict
        
    except Exception as e:
        logger.error(f"Error getting batch patient manifest data: {str(e)}")
        return {}
    finally:
        if conn:
            conn.close()

def calculate_patient_metrics_optimized(patients, manifest, manifest_data):
    """Calculate key metrics efficiently using pre-loaded manifest data"""
    try:
        from datetime import datetime
        # Initialize counters
        total_patients = len(patients)
        new_patients = 0
        new_this_month = 0
        waiting_sleep_test = 0
        waiting_dental_consult = 0
        waiting_reports = 0
        waiting_oral_appliance = 0
        waiting_followup = 0
        treatment_times = []
        now = datetime.now()
        current_month = now.month
        current_year = now.year
        
        # Define stage keys for categorization
        sleep_test_stages = ['sleep_study_scheduled', 'sleep_test_completed']
        dental_consult_stages = ['dental_sleep_doctor_consult_scheduled', 'met_with_dental_sleep_expert']
        report_stages = ['osa_report_ready', 'dental_approval_osa_report']
        oral_appliance_stages = ['oral_appliance_ordered', 'oral_appliance_fitted']
        followup_stages = ['followup_scheduled', 'followup_completed']
        
        for patient in patients:
            patient_manifest = manifest_data.get(patient.id, {})
            # New this month
            if patient.create_date and patient.create_date.month == current_month and patient.create_date.year == current_year:
                new_this_month += 1
            if not patient_manifest:
                # New patient (no manifest data)
                new_patients += 1
                continue
            # Find the latest completed stage and its completion date
            latest_completed = None
            latest_completed_date = None
            for stage in manifest:
                stage_key = stage['key']
                if stage_key in patient_manifest and patient_manifest[stage_key].get('is_completed'):
                    latest_completed = stage_key
                    # Use the latest completion date
                    comp_date = patient_manifest[stage_key].get('completion_date')
                    if comp_date:
                        # Parse date if it's a string
                        if isinstance(comp_date, str):
                            try:
                                comp_date = datetime.fromisoformat(comp_date)
                            except Exception:
                                comp_date = None
                        if comp_date and (not latest_completed_date or comp_date > latest_completed_date):
                            latest_completed_date = comp_date
            # Calculate treatment time if possible
            if patient.create_date and latest_completed_date:
                treatment_time = (latest_completed_date - patient.create_date).days
                if treatment_time >= 0:
                    treatment_times.append(treatment_time)
            if latest_completed:
                if latest_completed in sleep_test_stages:
                    waiting_sleep_test += 1
                elif latest_completed in dental_consult_stages:
                    waiting_dental_consult += 1
                elif latest_completed in report_stages:
                    waiting_reports += 1
                elif latest_completed in oral_appliance_stages:
                    waiting_oral_appliance += 1
                elif latest_completed in followup_stages:
                    waiting_followup += 1
        avg_treatment_time = round(sum(treatment_times) / len(treatment_times), 1) if treatment_times else None
        return {
            'total_patients': total_patients,
            'new_patients': new_patients,
            'new_this_month': new_this_month,
            'avg_treatment_time': avg_treatment_time,
            'waiting_sleep_test': waiting_sleep_test,
            'waiting_dental_consult': waiting_dental_consult,
            'waiting_reports': waiting_reports,
            'waiting_oral_appliance': waiting_oral_appliance,
            'waiting_followup': waiting_followup
        }
    except Exception as e:
        logger.error(f"Error calculating patient metrics: {str(e)}")
        return {
            'total_patients': 0,
            'new_patients': 0,
            'new_this_month': 0,
            'avg_treatment_time': None,
            'waiting_sleep_test': 0,
            'waiting_dental_consult': 0,
            'waiting_reports': 0,
            'waiting_oral_appliance': 0,
            'waiting_followup': 0
        }

def get_patient_list_with_stages_optimized(patients, manifest, manifest_data):
    """Get patient list with stage information using pre-loaded manifest data"""
    try:
        logger.info(f"Processing {len(patients)} patients for dashboard list")
        patient_list = []
        for patient in patients:
            patient_manifest = manifest_data.get(patient.id, {})
            # Get latest completed stage
            latest_stage = get_latest_completed_stage_from_data(patient_manifest, manifest)
            # Get next stage
            next_stage = get_next_stage_for_patient_from_data(patient_manifest, manifest)
            # Calculate age from date of birth
            age = None
            if patient.dob:
                from datetime import date
                today = date.today()
                age = today.year - patient.dob.year - ((today.month, today.day) < (patient.dob.month, patient.dob.day))
            # Get DSO and clinic information - prioritize patient's clinic first
            dso_name = None
            clinic_name = None
            
            # First, try to get DSO from patient's direct clinic assignment
            if patient.clinic:
                clinic_name = patient.clinic.name
                if patient.clinic.dso_info:
                    dso_name = patient.clinic.dso_info.name
            
            # Fallback: if patient has no clinic but has a dentist, use dentist's primary clinic
            if not dso_name and patient.dentist:
                primary_clinic = patient.dentist.get_primary_clinic()
                if primary_clinic:
                    if not clinic_name:
                        clinic_name = primary_clinic.name
                    if primary_clinic.dso_info:
                        dso_name = primary_clinic.dso_info.name
            # Determine current stage for filtering
            if latest_stage:
                current_stage = latest_stage['stage_name'] if 'stage_name' in latest_stage else latest_stage.get('title', 'Unknown')
            else:
                current_stage = 'Not Started'
            patient_data = {
                'id': patient.id,
                'name': patient.name,
                'email': patient.email,
                'phone': patient.phone,
                'gender': patient.gender,
                'age': age,
                'status': getattr(patient, 'status', None),
                'dso_name': dso_name,
                'clinic_name': clinic_name,
                'created_at': patient.create_date,
                'latest_stage': latest_stage,
                'next_stage': next_stage,
                'current_stage': current_stage,
                'workflow_url': url_for('main.patient_workflow_manifest', patient_id=patient.id)
            }
            patient_list.append(patient_data)
            logger.debug(f"Added patient {patient.id}: {patient.name} to dashboard list")
        # Sort by created_at descending (LIFO)
        patient_list.sort(key=lambda x: x['created_at'], reverse=True)
        logger.info(f"Returning {len(patient_list)} patients for dashboard")
        return patient_list
    except Exception as e:
        logger.error(f"Error getting patient list with stages: {str(e)}")
        return []

def get_latest_completed_stage_from_data(patient_manifest, manifest):
    """Get the latest completed stage for a patient from pre-loaded data"""
    try:
        if not patient_manifest:
            return None
        
        # Find the highest stage number that is completed
        latest_completed = None
        for stage in manifest:
            stage_key = stage['key']
            if stage_key in patient_manifest and patient_manifest[stage_key].get('is_completed'):
                latest_completed = stage
        
        return latest_completed
        
    except Exception as e:
        logger.error(f"Error getting latest completed stage: {str(e)}")
        return None

def get_next_stage_for_patient_from_data(patient_manifest, manifest):
    """Get the next stage for a patient from pre-loaded data"""
    try:
        latest_stage = get_latest_completed_stage_from_data(patient_manifest, manifest)
        if not latest_stage:
            # If no completed stages, next stage is the first one
            return manifest[0] if manifest else None
        
        # Get the next stage from manifest
        current_stage_number = latest_stage.get('stage_number', 0)
        next_stage_number = current_stage_number + 1
        
        for stage in manifest:
            if stage.get('stage_number') == next_stage_number:
                return stage
        
        return None
        
    except Exception as e:
        logger.error(f"Error getting next stage for patient: {str(e)}")
        return None

def get_monthly_patient_stats(patients, manifest, manifest_data):
    """Return monthly stats: patients added and completed per month."""
    from collections import defaultdict
    from datetime import datetime
    # Dicts: {YYYY-MM: count}
    added = defaultdict(int)
    completed = defaultdict(int)
    last_stage_key = manifest[-1]['key'] if manifest else None
    for patient in patients:
        # Added
        if patient.create_date:
            month = patient.create_date.strftime('%Y-%m')
            added[month] += 1
        # Completed
        patient_manifest = manifest_data.get(patient.id, {})
        if last_stage_key and last_stage_key in patient_manifest:
            entry = patient_manifest[last_stage_key]
            if entry.get('is_completed') and entry.get('completion_date'):
                comp_date = entry['completion_date']
                if isinstance(comp_date, str):
                    try:
                        comp_date = datetime.fromisoformat(comp_date)
                    except Exception:
                        comp_date = None
                if comp_date:
                    month = comp_date.strftime('%Y-%m')
                    completed[month] += 1
    # Union of all months
    all_months = set(added.keys()) | set(completed.keys())
    months = sorted(all_months)
    return {
        'months': months,
        'patients_added': [added[m] for m in months],
        'patients_completed': [completed[m] for m in months]
    }

def get_dashboard_pie_data(patients, manifest, manifest_data):
    from collections import defaultdict
    from datetime import date
    # Stage distribution
    stage_counts = defaultdict(int)
    # Gender distribution
    gender_counts = defaultdict(int)
    # Age distribution (grouped)
    age_groups = {'0-18': 0, '19-35': 0, '36-50': 0, '51+': 0, 'Unknown': 0}
    for patient in patients:
        # Stage: use latest_stage from manifest data
        patient_manifest = manifest_data.get(patient.id, {})
        latest_stage = None
        for stage in manifest:
            stage_key = stage['key']
            if stage_key in patient_manifest and patient_manifest[stage_key].get('is_completed'):
                latest_stage = stage['title'] if 'title' in stage else stage_key.replace('_', ' ').title()
        if latest_stage:
            stage_counts[latest_stage] += 1
        else:
            stage_counts['Not Started'] += 1
        # Gender
        gender = (patient.gender or 'Unknown').title()
        gender_counts[gender] += 1
        # Age group
        if patient.dob:
            today = date.today()
            age = today.year - patient.dob.year - ((today.month, today.day) < (patient.dob.month, patient.dob.day))
            if age <= 18:
                age_groups['0-18'] += 1
            elif age <= 35:
                age_groups['19-35'] += 1
            elif age <= 50:
                age_groups['36-50'] += 1
            elif age > 50:
                age_groups['51+'] += 1
            else:
                age_groups['Unknown'] += 1
        else:
            age_groups['Unknown'] += 1
    return {
        'stage_distribution': dict(stage_counts),
        'gender_distribution': dict(gender_counts),
        'age_distribution': dict(age_groups)
    }

@osaagent.route('/test', methods=['GET'])
def test_route():
    """Simple test route to verify blueprint is working"""
    return jsonify({'message': 'OSA agent blueprint is working!'})

@osaagent.route('/test_patient_files/<int:patient_id>', methods=['GET'])
def test_patient_files(patient_id):
    """Test route to check what files exist for a patient"""
    try:
        from flask_app.models import File, AdminFile
        
        # Get all files for the patient
        all_files = File.query.filter_by(patient_id=patient_id).all()
        all_admin_files = AdminFile.query.filter_by(patient_id=patient_id).all()
        
        file_info = []
        for file in all_files:
            file_info.append({
                'id': file.id,
                'name': file.name,
                'subcategory': file.subcategory,
                'file_type': file.file_type,
                's3_key': file.s3_key
            })
        
        admin_file_info = []
        for file in all_admin_files:
            admin_file_info.append({
                'id': file.id,
                'name': file.name,
                'file_category': file.file_category,
                'file_type': file.file_type,
                's3_key': file.s3_key
            })
        
        return jsonify({
            'patient_id': patient_id,
            'files_count': len(file_info),
            'admin_files_count': len(admin_file_info),
            'files': file_info,
            'admin_files': admin_file_info
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@osaagent.route('/test_patient_journey_data/<int:patient_id>')
@login_required
def test_patient_journey_data(patient_id):
    """Test route to inspect patient journey data generation"""
    try:
        logger.info(f"=== TEST_PATIENT_JOURNEY_DATA STARTED for patient_id: {patient_id} ===")
        
        # Get patient details
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({'error': 'Patient not found'}), 404
        
        logger.info(f"Found patient: {patient.name} (ID: {patient.id})")
        
        # Use build_patient_manifest to get the base manifest structure
        from flask_app.routes.main_routes import build_patient_manifest
        manifest, demographics, age = build_patient_manifest(patient_id)
        
        if not manifest:
            logger.error("Failed to build patient manifest")
            return jsonify({'error': 'Failed to build patient manifest'}), 500
        
        logger.info(f"Base manifest built with {len(manifest)} stages")
        
        # Get manifest definition for stage metadata
        from flask_app.config.manifest_config import get_manifest_definition
        manifest_definition = get_manifest_definition()
        
        # Create a mapping of stage keys to their metadata
        stage_metadata = {stage['key']: stage for stage in manifest_definition}
        
        # Build stages list with file data
        stages = []
        for stage_data in manifest:
            stage_key = stage_data['key']
            stage_metadata_info = stage_metadata.get(stage_key, {})
            
            # Get completion status and date
            is_completed = stage_data.get('value') == 'yes' or (isinstance(stage_data.get('value'), dict) and stage_data.get('value') is not None)
            completion_date = None
            
            # Try to get completion date from patient_manifest table
            manifest_result = db.session.execute(
                text("SELECT completion_date FROM patient_manifest WHERE patient_id = :pid AND stage_key = :stage_key"),
                {'pid': patient_id, 'stage_key': stage_key}
            ).first()
            
            if manifest_result and manifest_result.completion_date:
                completion_date = manifest_result.completion_date
            
            # Get files for this stage from stage_file_links table
            files = []
            try:
                file_links = db.session.execute(
                    text("SELECT file_id, file_table FROM stage_file_links WHERE patient_id = :pid AND stage_key = :stage_key"),
                    {'pid': patient_id, 'stage_key': stage_key}
                ).fetchall()
                
                logger.info(f"Found {len(file_links)} file links for stage {stage_key}")
                
                for file_link in file_links:
                    file_id = file_link.file_id
                    file_table = file_link.file_table
                    
                    # Get the actual file object based on the table
                    if file_table == 'files':
                        file_obj = File.query.get(file_id)
                    elif file_table == 'adminfiles':
                        file_obj = AdminFile.query.get(file_id)
                    else:
                        logger.warning(f"Unknown file table: {file_table}")
                        continue
                    
                    if file_obj:
                        # Convert upload_date to string for JSON serialization
                        upload_date_str = file_obj.upload_date.isoformat() if file_obj.upload_date else None
                        
                        file_data = {
                            'id': file_obj.id,
                            'name': file_obj.name,
                            's3_key': file_obj.s3_key,
                            'file_type': getattr(file_obj, 'file_type', 'unknown'),
                            'date': upload_date_str,
                            'comment': getattr(file_obj, 'comment', ''),
                            'category': getattr(file_obj, 'category', ''),
                            'subcategory': getattr(file_obj, 'subcategory', '')
                        }
                        files.append(file_data)
                        logger.info(f"Added file: {file_obj.name} (ID: {file_obj.id})")
                    else:
                        logger.warning(f"File object not found for ID: {file_id} in table: {file_table}")
                        
            except Exception as e:
                logger.error(f"Error fetching files for stage {stage_key}: {e}")
                files = []
            
            # Build stage object
            stage = {
                'key': stage_key,
                'name': stage_metadata_info.get('stage_name', stage_key.replace('_', ' ').title()),
                'subtitle': stage_metadata_info.get('description', ''),
                'description': stage_metadata_info.get('description', ''),
                'date': completion_date.isoformat() if completion_date else None,
                'status': 'completed' if is_completed else 'pending',
                'files': files
            }
            
            stages.append(stage)
            logger.info(f"Built stage {stage_key} with {len(files)} files")
        
        # Convert stages to JSON for frontend
        import json
        stages_json = json.dumps(stages, default=str)
        logger.info(f"Stages JSON length: {len(stages_json)}")
        logger.info(f"Stages JSON preview: {stages_json[:200]}...")
        
        logger.info("=== TEST_PATIENT_JOURNEY_DATA COMPLETED SUCCESSFULLY ===")
        
        return jsonify({
            'success': True,
            'patient': {
                'id': patient.id,
                'name': patient.name
            },
            'stages': stages,
            'stages_json': stages_json,
            'manifest': manifest,
            'demographics': demographics
        })
        
    except Exception as e:
        logger.error(f"Error in test_patient_journey_data route: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Error loading patient journey data: {str(e)}'}), 500
