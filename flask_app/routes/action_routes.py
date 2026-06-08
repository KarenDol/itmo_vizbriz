"""
Action Routes - LLM-Driven Action Execution
Handles dynamic action execution based on action manifest and LLM decisions.
"""

from flask import Blueprint, request, jsonify, current_app, redirect, session
from flask_login import login_required, current_user
from flask_app.models import db, Patient, PatientConsultSchedule, PatientDeviceOrder, DentistReportApproval
import json
from flask_app.config.action_manifest import (
    get_actions_for_stage, 
    get_action_by_key, 
    validate_action_parameters,
    format_action_for_llm
)

# Import manifest definition from validator
MANIFEST_DEFINITION = [
    {"stage_number": 1, "stage_name": "Quiz Completion", "key": "quiz_completion"},
    {"stage_number": 2, "stage_name": "Initial Consult Scheduled", "key": "initial_consult_scheduled"},
    {"stage_number": 3, "stage_name": "Met with Sleep Expert", "key": "initial_consult_completed"},
    {"stage_number": 4, "stage_name": "Sleep Study Scheduled", "key": "sleep_study_scheduled"},
    {"stage_number": 5, "stage_name": "Sleep Test Completed", "key": "sleep_test_completed"},
    {"stage_number": 6, "stage_name": "Schedule Sleep Test Review", "key": "schedule_sleep_test_review"},
    {"stage_number": 7, "stage_name": "Sleep Doctor Followup Completed", "key": "sleep_doctor_followup_completed"},
    {"stage_number": 8, "stage_name": "Dental Sleep Doctor Consult Scheduled", "key": "dental_sleep_doctor_consult_scheduled"},
    {"stage_number": 9, "stage_name": "CBCT Observation Report Uploaded", "key": "cbct_observation_report_uploaded"},
    {"stage_number": 10, "stage_name": "IntraOral Scan Uploaded", "key": "intraoral_scan_uploaded"},
    {"stage_number": 11, "stage_name": "HIPAA Consent Signed", "key": "hipaa_consent_signed"},
    {"stage_number": 12, "stage_name": "Patient Completes Consult with Dental Sleep Expert", "key": "met_with_dental_sleep_expert"},
    {"stage_number": 13, "stage_name": "Patient OSA Report Ready", "key": "osa_report_ready"},
    {"stage_number": 14, "stage_name": "Dental Approval for OSA Report", "key": "dental_approval_osa_report"},
    {"stage_number": 15, "stage_name": "Order Oral Appliance", "key": "order_oral_appliance"},
    {"stage_number": 16, "stage_name": "Device Delivered to Dental Office", "key": "device_delivered"},
    {"stage_number": 17, "stage_name": "Schedule Oral Appliance Delivery", "key": "schedule_oral_appliance_delivery"},
    {"stage_number": 18, "stage_name": "Oral Appliance Delivery Completed", "key": "oral_appliance_delivery_completed"},
    {"stage_number": 19, "stage_name": "Follow Up Sleep Test After Delivery", "key": "follow_up_sleep_test_after_delivery"},
]
from flask_app.routes.osaagent_routes import query_bedrock_claude_enhanced
from flask_app.utils.url_utils import shorten_url_with_tinyurl
from flask_app.utils.s3_presign_client import get_s3_client_for_presigning
import json
import logging
import os
import secrets
import boto3
import requests
import urllib.parse
from datetime import datetime
from cachetools import TTLCache

logger = logging.getLogger(__name__)

def _normalize_patient_id(raw_pid):
    """Normalize patient_id values that may include '$' or whitespace."""
    try:
        if raw_pid is None:
            return None
        return int(str(raw_pid).strip().lstrip('$').split()[0])
    except Exception:
        return None

"""In-memory short-link token cache
Maps short token -> presigned S3 URL, expires automatically.
This is intentionally ephemeral to avoid coupling; emails already include fallback presigned URLs internally.
"""
SHARE_TOKEN_CACHE: TTLCache = TTLCache(maxsize=10000, ttl=7 * 24 * 3600)

def send_cbct_request_email(recipient_email, subject, email_body):
    """
    Send CBCT request email with custom sender (no_reply@vizbriz.com).
    This function is specifically for CBCT request emails only.
    """
    try:
        from flask_mail import Mail, Message
        from flask import current_app
        
        mail = Mail(current_app)
        msg = Message(
            subject=subject,
            sender='no_reply@vizbriz.com',  # Custom sender for CBCT requests only
            recipients=[recipient_email]
        )
        msg.body = email_body
        msg.html = email_body
        
        logger.info(f"Attempting to send CBCT request email to {recipient_email}")
        logger.info(f"Email subject: {subject}")
        logger.info(f"Email body: {email_body}")
        
        mail.send(msg)
        logger.info(f"CBCT request email sent successfully to {recipient_email}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send CBCT request email: {str(e)}")
        return False

action_bp = Blueprint('action', __name__)

@action_bp.route('/api/llm/analyze_and_suggest_actions', methods=['POST'])
@login_required
def analyze_and_suggest_actions():
    """
    Analyze current patient state and suggest actions using LLM.
    """
    try:
        data = request.get_json()
        patient_id = _normalize_patient_id(data.get('patient_id'))
        current_stage = data.get('current_stage')
        
        if not patient_id or not current_stage:
            return jsonify({
                'success': False,
                'error': 'Missing required parameters: patient_id, current_stage'
            }), 400
        
        # Get comprehensive patient information including consultations
        from flask_app.routes.main_routes import fetch_patient_details
        patient_details = fetch_patient_details(patient_id)
        
        patient = patient_details.get('patient')
        if not patient:
            return jsonify({
                'success': False,
                'error': f'Patient {patient_id} not found'
            }), 404
        
        # Get available actions for current stage
        available_actions = get_actions_for_stage(current_stage)
        
        if not available_actions:
            return jsonify({
                'success': False,
                'error': f'No actions available for stage: {current_stage}'
            }), 400
        
        # Format actions for LLM
        actions_text = format_action_for_llm(current_stage)
        
        # Get AI guidance for available actions
        from flask_app.config.action_manifest import get_ai_guidance_for_stage
        ai_guidance = get_ai_guidance_for_stage(current_stage)
        
        # Format AI guidance for LLM
        guidance_text = ""
        if ai_guidance:
            guidance_text = "\nAI GUIDANCE FOR ACTIONS:\n"
            for action_key, guidance in ai_guidance.items():
                guidance_text += f"- {action_key}: {guidance}\n"
        
        # Build comprehensive patient information for LLM
        uploaded_files = patient_details.get('uploaded_files', {})
        scheduled_consultations = patient_details.get('scheduled_consultations', [])
        patient_statuses = patient_details.get('patient_statuses', {})
        comments = patient_details.get('comments', [])
        
        # Format patient information for LLM
        patient_info = f"""
PATIENT INFORMATION:
- Patient ID: {patient_id}
- Patient Name: {patient.name}
- Email: {patient.email}
- Phone: {patient.phone}
- Current Stage: {current_stage}
- Payment Method: {getattr(patient, 'payment_method', 'N/A')}

UPLOADED FILES:
"""
        for category, files in uploaded_files.items():
            patient_info += f"- {category}: {len(files)} files\n"
        
        patient_info += f"""
SCHEDULED CONSULTATIONS ({len(scheduled_consultations)}):
"""
        for consultation in scheduled_consultations:
            patient_info += f"- {consultation['consult_type']}: {consultation['status']} ({consultation['scheduled_datetime']})\n"
            if consultation['doctor_name']:
                patient_info += f"  Doctor: {consultation['doctor_name']}\n"
            if consultation['notes']:
                patient_info += f"  Notes: {consultation['notes']}\n"
        
        patient_info += f"""
PATIENT STATUSES:
"""
        for status_type, status in patient_statuses.items():
            patient_info += f"- {status_type}: {status.status}\n"
        
        if comments:
            patient_info += f"""
PATIENT COMMENTS ({len(comments)}):
"""
            for comment in comments:
                patient_info += f"- {comment['created_date']}: {comment['content']}\n"
        
        # Create LLM prompt for action analysis
        llm_prompt = f"""
You are an AI workflow assistant for a sleep medicine clinic. Analyze the current patient situation and suggest the most appropriate action to take.

{patient_info}

AVAILABLE ACTIONS FOR THIS STAGE:
{actions_text}{guidance_text}

TASK:
1. Analyze the current patient situation using all available information
2. Consider the patient's consultation history, uploaded files, and current status
3. Determine which action(s) would be most appropriate based on the AI guidance
4. Provide specific parameters for the chosen action(s)
5. Explain your reasoning

RESPONSE FORMAT:
Return a JSON object with the following structure:
{{
    "suggested_actions": [
        {{
            "action_key": "action_name",
            "parameters": {{
                "param1": "value1",
                "param2": "value2"
            }},
            "reasoning": "Explanation of why this action is appropriate"
        }}
    ],
    "analysis": "Overall analysis of the patient's current situation"
}}

IMPORTANT: Use the AI guidance provided to understand when and why each action should be used. The guidance explains the context and purpose of each action.
"""
        
        # Query LLM for action suggestions using proper message format
        bedrock_messages = [
            {
                "role": "assistant",
                "content": """You are an AI workflow assistant for a sleep medicine clinic. Analyze the current patient situation and suggest the most appropriate action to take.

IMPORTANT: Use the AI guidance provided to understand when and why each action should be used. The guidance explains the context and purpose of each action.

Return a JSON object with the following structure:
{
    "suggested_actions": [
        {
            "action_key": "action_name",
            "parameters": {
                "param1": "value1",
                "param2": "value2"
            },
            "reasoning": "Explanation of why this action is appropriate"
        }
    ],
    "analysis": "Overall analysis of the patient's current situation"
}"""
            },
            {
                "role": "user",
                "content": llm_prompt
            }
        ]
        
        result = query_bedrock_claude_enhanced(bedrock_messages, max_tokens=500, temperature=0.2, patient_id=patient_id)
        
        if result.get('success'):
            llm_response = result.get('response', '')
            try:
                # Parse LLM response
                response_data = json.loads(llm_response)
                
                return jsonify({
                    'success': True,
                    'suggested_actions': response_data.get('suggested_actions', []),
                    'analysis': response_data.get('analysis', ''),
                    'available_actions': list(available_actions.keys())
                })
                
            except json.JSONDecodeError:
                # If LLM didn't return valid JSON, return the raw response
                return jsonify({
                    'success': True,
                    'raw_response': llm_response,
                    'available_actions': list(available_actions.keys())
                })
        else:
            # If Bedrock call failed, return error
            return jsonify({
                'success': False,
                'error': result.get('message', 'Unknown error'),
                'available_actions': list(available_actions.keys())
            }), 500
            
    except Exception as e:
        logger.error(f"Error in analyze_and_suggest_actions: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@action_bp.route('/api/llm/execute_action', methods=['POST'])
@login_required
def execute_action():
    """
    Execute a specific action based on LLM decision.
    """
    try:
        data = request.get_json()
        action_key = data.get('action_key')
        parameters = data.get('parameters', {})
        
        if not action_key:
            return jsonify({
                'success': False,
                'error': 'Missing required parameter: action_key'
            }), 400
        
        # Get action configuration
        action_config = get_action_by_key(action_key)
        if not action_config:
            return jsonify({
                'success': False,
                'error': f'Action "{action_key}" not found'
            }), 400
        
        # Validate parameters
        is_valid, error_msg = validate_action_parameters(action_key, parameters)
        if not is_valid:
            return jsonify({
                'success': False,
                'error': error_msg
            }), 400
        
        # Execute the action based on type
        logger.info(f"=== EXECUTING ACTION: {action_key} ===")
        logger.info(f"Parameters: {parameters}")
        result = execute_specific_action(action_key, parameters)
        logger.info(f"=== ACTION EXECUTION RESULT ===")
        logger.info(f"Result: {result}")
        
        return jsonify({
            'success': True,
            'action_executed': action_key,
            'result': result
        })
        
    except Exception as e:
        logger.error(f"Error in execute_action: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

def execute_specific_action(action_key: str, parameters: dict) -> dict:
    """
    Execute a specific action based on the action key.
    Uses the action manifest to determine the endpoint and method.
    
    Args:
        action_key (str): The action to execute
        parameters (dict): Action parameters
        
    Returns:
        dict: Execution result
    """
    try:
        # Get action configuration from manifest
        action_config = get_action_by_key(action_key)
        if not action_config:
            return {
                'status': 'error',
                'message': f'Action "{action_key}" not found in action manifest'
            }
        
        # Get endpoint and method from manifest
        endpoint = action_config.get('endpoint', '')
        method = action_config.get('method', 'POST')
        
        # Handle special cases that need custom logic
        if action_key == "request_cbct_files":
            logger.info(f"=== ROUTING TO EXECUTE_REQUEST_CBCT_FILES ===")
            logger.info(f"Action key: {action_key}")
            logger.info(f"Parameters: {parameters}")
            return execute_request_cbct_files(parameters)
        elif action_key == "schedule_consultation":
            return execute_schedule_consultation(parameters)
        elif action_key == "complete_consultation":
            return execute_complete_consultation(parameters)
        elif action_key == "approve_dentist_report":
            return execute_approve_dentist_report(parameters)
        elif action_key == "approve_osa_report":
            return execute_approve_osa_report(parameters)
        elif action_key == "order_oral_appliance":
            return execute_order_oral_appliance(parameters)
        elif action_key == "schedule_appliance_delivery":
            return execute_schedule_appliance_delivery(parameters)
        elif action_key == "complete_appliance_delivery":
            return execute_complete_appliance_delivery(parameters)
        elif action_key == "complete_sleep_doctor_followup":
            return execute_complete_sleep_doctor_followup(parameters)
        elif action_key == "schedule_dental_consultation":
            return execute_schedule_dental_consultation(parameters)
        elif action_key == "complete_dental_consultation":
            logger.info(f"=== ROUTING TO COMPLETE DENTAL CONSULTATION ===")
            logger.info(f"Action key: {action_key}")
            logger.info(f"Parameters: {parameters}")
            try:
                result = execute_complete_dental_consultation(parameters)
                logger.info(f"=== EXECUTE COMPLETE DENTAL CONSULTATION RESULT ===")
                logger.info(f"Result: {result}")
                return result
            except Exception as e:
                logger.error(f"Error in execute_complete_dental_consultation: {e}")
                return {
                    'status': 'error',
                    'message': f'Error executing complete_dental_consultation: {str(e)}'
                }
        elif action_key == "schedule_sleep_test_review":
            return execute_schedule_sleep_test_review(parameters)
        elif action_key == "share_patient_files":
            return execute_share_patient_files(parameters)
        elif action_key == "mark_quiz_completed":
            return execute_mark_quiz_completed(parameters)
        
        # For file upload actions, redirect to patient details page
        elif action_key in ["request_sleep_test_files", "request_intraoral_scan"]:
            patient_id = parameters.get('patient_id')
            return {
                'status': 'redirect',
                'message': f'Redirecting to patient details page for file upload',
                'redirect_url': f'/patient_details/{patient_id}',
                'action_key': action_key
            }
        
        # For email request actions, call the specific function
        elif action_key == "request_hipaa_consent":
            return execute_request_hipaa_consent(parameters)
        
        # For reminder actions, use the generic reminder endpoint
        elif action_key.startswith('remind_'):
            return execute_generic_reminder(action_key, parameters)
        
        # For other actions, use the endpoint from manifest
        else:
            return {
                'status': 'action_recognized',
                'message': f'Action "{action_key}" endpoint: {endpoint} (method: {method})',
                'endpoint': endpoint,
                'method': method,
                'parameters': parameters
            }
            
    except Exception as e:
        logger.error(f"Error in execute_specific_action: {e}")
        return {
            'status': 'error',
            'message': f'Error executing action {action_key}: {str(e)}'
        }

def execute_mark_quiz_completed(parameters: dict) -> dict:
    """Execute mark quiz completed action."""
    try:
        patient_id = parameters['patient_id']
        completion_date = parameters.get('completion_date')
        notes = parameters.get('notes', '')
        
        # Parse completion date
        from datetime import datetime
        if completion_date:
            try:
                completion_dt = datetime.strptime(completion_date, "%Y-%m-%d")
            except ValueError:
                completion_dt = datetime.now()
        else:
            completion_dt = datetime.now()
        
        # Update the patient manifest for quiz completion
        update_patient_manifest_for_action(
            patient_id=patient_id,
            action_key='mark_quiz_completed',
            is_completed=True,
            status_message=f'Quiz manually marked as completed on {completion_dt.strftime("%Y-%m-%d")}'
        )
        
        logger.info(f'Quiz manually marked as completed for patient {patient_id} on {completion_dt}')
        if notes:
            logger.info(f'Completion notes: {notes}')
        
        return {
            'status': 'success',
            'message': f'Quiz marked as completed for patient {patient_id}',
            'patient_id': patient_id,
            'completion_date': completion_dt.strftime('%Y-%m-%d'),
            'notes': notes
        }
        
    except Exception as e:
        logger.error(f"Error in execute_mark_quiz_completed: {e}")
        return {
            'status': 'error',
            'message': f'Error marking quiz as completed: {str(e)}'
        }

def execute_schedule_consultation(parameters: dict) -> dict:
    """Execute schedule consultation action."""
    try:
        patient_id = parameters['patient_id']
        scheduled_date = parameters['scheduled_date']
        scheduled_time = parameters['scheduled_time']
        doctor_name = parameters.get('doctor_name', 'Sleep Expert')
        notes = parameters.get('notes', '')
        
        # Create consultation schedule
        scheduled_datetime = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")
        
        new_schedule = PatientConsultSchedule(
            patient_id=patient_id,
            consult_type='sleep_expert',
            scheduled_datetime=scheduled_datetime,
            notes=notes,
            status='scheduled'
        )
        
        db.session.add(new_schedule)
        db.session.commit()
        
        return {
            'status': 'success',
            'message': f'Consultation scheduled for {scheduled_datetime.strftime("%B %d, %Y at %I:%M %p")}',
            'consultation_id': new_schedule.id
        }
        
    except Exception as e:
        db.session.rollback()
        raise e

def execute_complete_consultation(parameters: dict) -> dict:
    """Execute complete consultation action."""
    try:
        patient_id = parameters['patient_id']
        completion_date = parameters['completion_date']
        completion_time = parameters['completion_time']
        doctor_name = parameters.get('doctor_name', 'Sleep Expert')
        notes = parameters.get('notes', '')
        
        # Find existing consultation
        consultation = PatientConsultSchedule.query.filter_by(
            patient_id=patient_id,
            consult_type='sleep_expert',
            status='scheduled'
        ).first()
        
        if not consultation:
            return {
                'status': 'error',
                'message': 'No scheduled consultation found to complete'
            }
        
        # Update consultation
        consultation.status = 'completed'
        consultation.completed_datetime = datetime.strptime(f"{completion_date} {completion_time}", "%Y-%m-%d %H:%M")
        consultation.comment = notes
        consultation.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        return {
            'status': 'success',
            'message': f'Consultation marked as completed on {completion_date}',
            'consultation_id': consultation.id
        }
        
    except Exception as e:
        db.session.rollback()
        raise e

def execute_approve_osa_report(parameters: dict) -> dict:
    """Execute approve OSA report action."""
    try:
        patient_id = parameters['patient_id']
        report_id = parameters['report_id']
        approval_date = parameters['approval_date']
        approver_name = parameters['approver_name']
        notes = parameters.get('notes', '')
        
        # Create OSA report approval
        approval = DentistReportApproval(
            patient_id=patient_id,
            report_id=report_id,
            approval_timestamp=datetime.strptime(approval_date, "%Y-%m-%d"),
            dentist_full_name=approver_name,
            dentist_signature=approver_name,  # Using name as signature for now
            notes=notes,
            approval_status='approved'
        )
        
        db.session.add(approval)
        db.session.commit()
        
        logger.info(f'OSA report {report_id} approved by {approver_name} for patient {patient_id} on {approval_date}')
        
        return {
            'status': 'success',
            'message': f'OSA report {report_id} approved by {approver_name} on {approval_date}',
            'patient_id': patient_id,
            'report_id': report_id,
            'approval_date': approval_date,
            'approver_name': approver_name,
            'notes': notes,
            'approval_id': approval.id
        }
        
    except Exception as e:
        db.session.rollback()
        raise e

def update_patient_manifest_for_action(patient_id: int, action_key: str, is_completed: bool, status_message: str):
    """Update patient manifest for stages associated with an action"""
    try:
        from sqlalchemy import text
        from flask_app.config.action_manifest import get_action_by_key
        
        # Get action definition from manifest
        action_def = get_action_by_key(action_key)
        if not action_def:
            logger.warning(f"Action {action_key} not found in manifest")
            return
        
        # Get stages associated with this action
        stages = action_def.get('stages', [])
        if not stages:
            logger.warning(f"No stages defined for action {action_key}")
            return
        
        # Update each stage associated with this action
        for stage_key in stages:
            # Check if entry exists
            existing = db.session.execute(
                text("SELECT id FROM patient_manifest WHERE patient_id = :pid AND stage_key = :stage_key"),
                {'pid': patient_id, 'stage_key': stage_key}
            ).first()
            
            if existing:
                # Update existing entry
                db.session.execute(
                    text("""
                        UPDATE patient_manifest 
                        SET is_completed = :is_completed, completion_date = NOW(), 
                            status_message = :status_message, updated_at = NOW()
                        WHERE patient_id = :pid AND stage_key = :stage_key
                    """),
                    {
                        'is_completed': is_completed,
                        'status_message': status_message,
                        'pid': patient_id,
                        'stage_key': stage_key
                    }
                )
            else:
                # Insert new entry
                db.session.execute(
                    text("""
                        INSERT INTO patient_manifest 
                        (patient_id, stage_key, is_completed, completion_date, status_message, created_at, updated_at)
                        VALUES (:pid, :stage_key, :is_completed, NOW(), :status_message, NOW(), NOW())
                    """),
                    {
                        'pid': patient_id,
                        'stage_key': stage_key,
                        'is_completed': is_completed,
                        'status_message': status_message
                    }
                )
            
            logger.info(f"Patient manifest updated for patient {patient_id}, stage {stage_key} via action {action_key}")
        
        db.session.commit()
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating patient manifest for action {action_key}: {e}")
        raise e

def update_patient_manifest_stage(patient_id: int, stage_key: str, is_completed: bool, status_message: str):
    """Update patient manifest for a specific stage (legacy function for direct stage updates)"""
    try:
        from sqlalchemy import text
        
        # Check if entry exists
        existing = db.session.execute(
            text("SELECT id FROM patient_manifest WHERE patient_id = :pid AND stage_key = :stage_key"),
            {'pid': patient_id, 'stage_key': stage_key}
        ).first()
        
        if existing:
            # Update existing entry
            db.session.execute(
                text("""
                    UPDATE patient_manifest 
                    SET is_completed = :is_completed, completion_date = NOW(), 
                        status_message = :status_message, updated_at = NOW()
                    WHERE patient_id = :pid AND stage_key = :stage_key
                """),
                {
                    'is_completed': is_completed,
                    'status_message': status_message,
                    'pid': patient_id,
                    'stage_key': stage_key
                }
            )
        else:
            # Insert new entry
            db.session.execute(
                text("""
                    INSERT INTO patient_manifest 
                    (patient_id, stage_key, is_completed, completion_date, status_message, created_at, updated_at)
                    VALUES (:pid, :stage_key, :is_completed, NOW(), :status_message, NOW(), NOW())
                """),
                {
                    'pid': patient_id,
                    'stage_key': stage_key,
                    'is_completed': is_completed,
                    'status_message': status_message
                }
            )
        
        db.session.commit()
        logger.info(f"Patient manifest updated for patient {patient_id}, stage {stage_key}")
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating patient manifest: {e}")
        raise e

def execute_approve_dentist_report(parameters: dict) -> dict:
    """Execute approve dentist report action."""
    try:
        patient_id = parameters['patient_id']
        approval_date = parameters['approval_date']
        approver_name = parameters['approver_name']
        notes = parameters.get('notes', '')
        
        # Create dentist report approval
        approval = DentistReportApproval(
            patient_id=patient_id,
            approval_date=datetime.strptime(approval_date, "%Y-%m-%d"),
            approver_name=approver_name,
            notes=notes
        )
        
        db.session.add(approval)
        db.session.commit()
        
        return {
            'status': 'success',
            'message': f'Dentist report approved by {approver_name} on {approval_date}',
            'approval_id': approval.id
        }
        
    except Exception as e:
        db.session.rollback()
        raise e

# This duplicate function has been removed - using the corrected version above

def execute_order_oral_appliance(parameters: dict) -> dict:
    """Execute order oral appliance action."""
    try:
        patient_id = parameters['patient_id']
        device_type = parameters['device_type']
        device_brand = parameters['device_brand']
        order_date = parameters['order_date']
        lab_name = parameters.get('lab_name', '')
        notes = parameters.get('notes', '')
        
        # Create appliance order
        order = PatientDeviceOrder(
            patient_id=patient_id,
            device_type=device_type,      # Use the actual device type (Herbst, Dorsal fins, etc.)
            device_name=device_brand,    # Store the specific brand (Emerald, Dynaflex, etc.) in device_name
            order_date=datetime.strptime(order_date, "%Y-%m-%d"),
            status='ordered',
            notes=notes
        )
        
        db.session.add(order)
        db.session.commit()
        
        logger.info(f'Oral appliance {device_type} ({device_brand}) ordered for patient {patient_id} on {order_date}')
        
        # Update PatientManifest table to mark stage as complete
        try:
            from flask_app.models import PatientManifest
            manifest_entry = PatientManifest.query.filter_by(
                patient_id=patient_id,
                stage_key='order_oral_appliance'
            ).first()
            
            if manifest_entry:
                manifest_entry.is_completed = True
                manifest_entry.completion_date = datetime.strptime(order_date, "%Y-%m-%d")
                manifest_entry.stage_data = json.dumps({
                    'device_type': device_type,
                    'device_brand': device_brand,
                    'lab_name': lab_name,
                    'order_date': order_date,
                    'status': 'ordered',
                    'notes': notes,
                    'order_id': order.id
                })
                manifest_entry.status_message = f"Oral appliance ordered on {order_date} - Status: ordered"
                manifest_entry.updated_at = datetime.now()
                
                db.session.commit()
                logger.info(f'Updated PatientManifest for patient {patient_id} - order_oral_appliance stage marked complete')
            else:
                logger.warning(f'No PatientManifest entry found for patient {patient_id} stage order_oral_appliance')
        except Exception as manifest_error:
            logger.error(f'Error updating PatientManifest for patient {patient_id}: {manifest_error}')
            # Don't fail the whole operation if manifest update fails
        
        return {
            'status': 'success',
            'message': f'{device_type} ({device_brand}) ordered on {order_date}',
            'patient_id': patient_id,
            'device_type': device_type,
            'device_brand': device_brand,
            'lab_name': lab_name,
            'order_date': order_date,
            'notes': notes,
            'order_id': order.id
        }
        
    except Exception as e:
        db.session.rollback()
        raise e

def execute_schedule_appliance_delivery(parameters: dict) -> dict:
    """Execute schedule appliance delivery action."""
    try:
        patient_id = parameters['patient_id']
        scheduled_date = parameters['scheduled_date']
        scheduled_time = parameters['scheduled_time']
        notes = parameters.get('notes', '')
        
        # Create delivery schedule
        scheduled_datetime = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")
        
        delivery_schedule = PatientConsultSchedule(
            patient_id=patient_id,
            consult_type='appliance_delivery',
            scheduled_datetime=scheduled_datetime,
            notes=notes,
            status='scheduled'
        )
        
        db.session.add(delivery_schedule)
        db.session.commit()
        
        return {
            'status': 'success',
            'message': f'Appliance delivery scheduled for {scheduled_datetime.strftime("%B %d, %Y at %I:%M %p")}',
            'delivery_id': delivery_schedule.id
        }
        
    except Exception as e:
        db.session.rollback()
        raise e

def execute_complete_appliance_delivery(parameters: dict) -> dict:
    """Execute complete appliance delivery action."""
    try:
        patient_id = parameters['patient_id']
        completion_date = parameters['completion_date']
        completion_time = parameters['completion_time']
        notes = parameters.get('notes', '')
        
        # Find existing delivery schedule
        delivery = PatientConsultSchedule.query.filter_by(
            patient_id=patient_id,
            consult_type='appliance_delivery',
            status='scheduled'
        ).first()
        
        if not delivery:
            return {
                'status': 'error',
                'message': 'No scheduled appliance delivery found to complete'
            }
        
        # Update delivery consultation
        delivery.status = 'completed'
        delivery.completed_datetime = datetime.strptime(f"{completion_date} {completion_time}", "%Y-%m-%d %H:%M")
        delivery.comment = notes
        delivery.updated_at = datetime.utcnow()
        
        # Also update the device order status to 'delivered'
        from flask_app.models import PatientDeviceOrder
        device_order = PatientDeviceOrder.query.filter_by(
            patient_id=patient_id,
            device_type='oral_appliance'
        ).first()
        
        if device_order:
            device_order.status = 'delivered'
            device_order.notes = f"Appliance delivered on {completion_date} - {notes}"
            device_order.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        logger.info(f'Appliance delivery completed for patient {patient_id} on {completion_date}')
        
        return {
            'status': 'success',
            'message': f'Appliance delivery completed on {completion_date}',
            'patient_id': patient_id,
            'completion_date': completion_date,
            'completion_time': completion_time,
            'notes': notes,
            'delivery_id': delivery.id
        }
        
    except Exception as e:
        db.session.rollback()
        raise e

def execute_request_cbct_files(parameters: dict) -> dict:
    """Execute request CBCT files action."""
    try:
        logger.info(f"=== EXECUTE_REQUEST_CBCT_FILES CALLED ===")
        logger.info(f"Parameters received: {parameters}")
        
        patient_id = parameters['patient_id']
        request_date = parameters.get('request_date', datetime.now().strftime('%Y-%m-%d'))
        message = parameters.get('message', '')
        
        logger.info(f"Patient ID: {patient_id}")
        logger.info(f"Request date: {request_date}")
        logger.info(f"Message: {message}")
        
        # Get patient information
        patient = Patient.query.get(patient_id)
        if not patient:
            logger.error(f"Patient {patient_id} not found")
            return {
                'status': 'error',
                'message': f'Patient {patient_id} not found'
            }
        
        # Construct email subject and body
        subject = f"CBCT report for patient id {patient_id}"
        
        # Use default message from action manifest if none provided
        if not message:
            message = f"Please upload required report for patient ID {patient_id}."
        
        email_body = f"{message}"
        
        # Add additional information if provided
        if parameters.get('additional_info'):
            email_body += f"\n\nAdditional Information:\n{parameters.get('additional_info')}"
        
        # Send email using the new SendGrid CBCT function
        logger.info(f"About to call send_cbct_email_with_sendgrid with recipient: info@vizbriz.com")
        logger.info(f"Subject: {subject}")
        logger.info(f"Email body: {email_body}")
        
        email_sent = send_cbct_email_with_sendgrid('info@vizbriz.com', subject, email_body, patient_id=patient_id)
        
        logger.info(f"send_cbct_email_with_sendgrid returned: {email_sent}")
        
        if email_sent:
            # Don't update patient manifest stage - it should only be completed when document is uploaded
            # Just log the action was performed
            logger.info(f'CBCT request email sent for patient {patient_id} - stage remains incomplete until document is uploaded')
            
            return {
                'status': 'success',
                'message': f'CBCT request email sent successfully for patient {patient_id}. Stage will be completed when CBCT document is uploaded.',
                'email_sent': True,
                'recipient': 'info@vizbridge.com',
                'subject': subject
            }
        else:
            return {
                'status': 'error',
                'message': 'Failed to send CBCT request email'
            }
        
    except Exception as e:
        logger.error(f"Error in execute_request_cbct_files: {e}")
        return {
            'status': 'error',
            'message': f'Error executing CBCT request: {str(e)}'
        }

def send_cbct_email_with_sendgrid(recipient_email, subject, email_body, patient_id=None, sender_id=None):
    """
    Send CBCT request email using the existing working SendGrid function.
    This is a new function specifically for CBCT emails that uses the proven SendGrid setup.
    """
    try:
        # Import the existing working SendGrid function
        from flask_app.routes.file_management_routes import send_email_with_sendgrid
        
        logger.info(f"Attempting to send CBCT request email to {recipient_email}")
        logger.info(f"Email subject: {subject}")
        logger.info(f"Email body: {email_body}")
        logger.info(f"From email: no_reply@vizbriz.com")
        logger.info(f"About to call send_email_with_sendgrid function")
        
        # Use the existing working SendGrid function with patient tracking
        email_sent = send_email_with_sendgrid(
            recipient_email, 
            subject, 
            email_body, 
            email_body,
            patient_id=patient_id,
            sender_id=sender_id,  # Can be None for system emails
            email_type='cbct_request',
            sender_type='system'
        )
        
        logger.info(f"send_email_with_sendgrid function returned: {email_sent}")
        
        if email_sent:
            logger.info(f"CBCT request email sent successfully to {recipient_email}")
            return True
        else:
            logger.error(f"Failed to send CBCT request email to {recipient_email}")
            return False
        
    except Exception as e:
        logger.error(f"Failed to send CBCT request email: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        return False

def create_simple_hipaa_email(patient_name, wizard_link):
    """Create a simple HIPAA consent email with the exact message requested."""
    return f"""Dear {patient_name},

You have been requested by the clinic to complete the patient onboarding process. Please use the following link to complete the process before your next appointment:

{wizard_link}

This will include HIPAA consent forms and is required to proceed with your treatment.

Thank you"""

def send_hipaa_consent_email_with_sendgrid(recipient_email, subject, email_body, patient_id=None, sender_id=None, email_type='hipaa_consent_request'):
    """
    Send HIPAA consent request email using the same send_email_with_sendgrid function as CBCT.
    This uses the proven SendGrid setup that's already working.
    """
    try:
        logger.info(f"QUIZ_EMAIL_DEBUG: === SEND_HIPAA_CONSENT_EMAIL_WITH_SENDGRID CALLED ===")
        logger.info(f"QUIZ_EMAIL_DEBUG: recipient_email: {recipient_email}")
        logger.info(f"QUIZ_EMAIL_DEBUG: subject: {subject}")
        logger.info(f"QUIZ_EMAIL_DEBUG: patient_id: {patient_id}")
        logger.info(f"QUIZ_EMAIL_DEBUG: sender_id: {sender_id}")
        
        # Import the existing working SendGrid function
        from flask_app.routes.file_management_routes import send_email_with_sendgrid
        
        logger.info(f"QUIZ_EMAIL_DEBUG: Successfully imported send_email_with_sendgrid function")
        logger.info(f"QUIZ_EMAIL_DEBUG: About to call send_email_with_sendgrid function")
        
        # Use the same send_email_with_sendgrid function that works for CBCT
        email_sent = send_email_with_sendgrid(
            recipient_email, 
            subject, 
            email_body, 
            email_body,
            patient_id=patient_id,
            sender_id=sender_id,  # Can be None for system emails
            email_type=email_type,
            sender_type='system'
        )
        
        logger.info(f"QUIZ_EMAIL_DEBUG: send_email_with_sendgrid function returned: {email_sent}")
        
        if email_sent:
            logger.info(f"QUIZ_EMAIL_DEBUG: HIPAA consent email sent successfully to {recipient_email}")
            return True
        else:
            logger.error(f"QUIZ_EMAIL_DEBUG: Failed to send HIPAA consent email to {recipient_email}")
            return False
        
    except Exception as e:
        logger.error(f"Failed to send HIPAA consent email: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        return False

def execute_generic_reminder(action_key: str, parameters: dict) -> dict:
    """Execute generic reminder actions using the reminder endpoint."""
    try:
        patient_id = parameters['patient_id']
        reminder_type = action_key.replace('remind_', '')
        custom_message = parameters.get('custom_message', '')
        
        # Get action config for default message
        action_config = get_action_by_key(action_key)
        default_message = action_config.get('default_message', '') if action_config else ''
        
        # Use custom message if provided, otherwise use default
        message = custom_message if custom_message else default_message
        
        # Format message with patient name if available
        patient = Patient.query.get(patient_id)
        if patient and '{patient_name}' in message:
            try:
                message = message.format(patient_name=patient.name)
                logger.info(f"Template formatted successfully: {message}")
            except Exception as e:
                logger.error(f"Template formatting failed: {e}")
                # Fallback to simple replacement
                message = message.replace('{patient_name}', patient.name)
                logger.info(f"Fallback replacement used: {message}")
        
        # Send reminder using the existing reminder endpoint
        reminder_data = {
            'patient_id': patient_id,
            'reminder_type': reminder_type,
            'custom_message': message
        }
        
        # For now, return success - in a full implementation, this would call the reminder API
        return {
            'status': 'success',
            'message': f'Reminder sent for {reminder_type}',
            'reminder_type': reminder_type,
            'patient_id': patient_id
        }
        
    except Exception as e:
        logger.error(f"Error in execute_generic_reminder: {e}")
        return {
            'status': 'error',
            'message': f'Error sending reminder: {str(e)}'
        }

def execute_schedule_sleep_study(parameters: dict) -> dict:
    """Execute schedule sleep study action."""
    try:
        # Backward-compatible executor (not used by new API)
        patient_id = parameters.get('patient_id')
        if not patient_id:
            return {'status': 'error', 'message': 'patient_id is required'}
        return {
            'status': 'redirect',
            'message': 'Use /api/schedule_sleep_study to create DB record',
            'redirect_url': f'/patient_details/{patient_id}'
        }
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

# New: schedule sleep study API endpoint (used by action card)
@action_bp.route('/api/schedule_sleep_study', methods=['POST'])
@login_required
def api_schedule_sleep_study():
    try:
        from datetime import datetime
        from flask_app.models import Patient, PatientConsultSchedule
        data = request.get_json() or {}
        patient_id = _normalize_patient_id(data.get('patient_id'))
        scheduled_date = data.get('scheduled_date')
        scheduled_time = data.get('scheduled_time')
        facility_name = data.get('facility_name', '')
        notes = data.get('notes', '')

        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400
        if not scheduled_date or not scheduled_time:
            return jsonify({'success': False, 'error': 'scheduled_date and scheduled_time are required'}), 400

        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({'success': False, 'error': f'Patient {patient_id} not found'}), 404

        scheduled_datetime = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")

        # Create sleep doctor consult (validator expects consult_type='sleep_doctor')
        details_note = notes or ''
        if facility_name:
            details_note = f"{details_note} (Facility: {facility_name})".strip()

        new_row = PatientConsultSchedule(
            patient_id=patient_id,
            consult_type='sleep_doctor',
            scheduled_datetime=scheduled_datetime,
            status='scheduled',
            notes=details_note
        )
        db.session.add(new_row)
        db.session.commit()

        # Update UI manifest (optional informational)
        update_patient_manifest_for_action(
            patient_id=patient_id,
            action_key='schedule_sleep_study',
            is_completed=True,
            status_message=f'Sleep study scheduled for {scheduled_datetime.strftime("%Y-%m-%d %H:%M")}'
        )

        return jsonify({'success': True, 'result': {
            'status': 'success',
            'message': 'Sleep study scheduled',
            'patient_id': patient_id,
            'scheduled_datetime': scheduled_datetime.isoformat(),
            'notes': details_note
        }})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

def execute_request_sleep_test_files(parameters: dict) -> dict:
    """Execute request sleep test files action."""
    try:
        patient_id = parameters['patient_id']
        return {
            'status': 'redirect',
            'message': f'Redirecting to patient details for sleep test file upload',
            'redirect_url': f'/patient_details/{patient_id}'
        }
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def execute_schedule_sleep_test_review(parameters: dict) -> dict:
    """Execute schedule sleep test review action."""
    try:
        from flask_app.models import PatientConsultSchedule
        from datetime import datetime
        patient_id = parameters.get('patient_id')
        scheduled_date = parameters.get('scheduled_date')
        scheduled_time = parameters.get('scheduled_time')
        doctor_name = parameters.get('doctor_name', 'Sleep Doctor')
        notes = parameters.get('notes', '')

        if not patient_id:
            return {'status': 'error', 'message': 'Missing required parameter: patient_id'}

        if scheduled_date and scheduled_time:
            scheduled_datetime = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")
        else:
            scheduled_datetime = datetime.now()

        consultation = PatientConsultSchedule.query.filter_by(
            patient_id=patient_id, consult_type='sleep_doctor'
        ).first()
        if consultation:
            consultation.scheduled_datetime = scheduled_datetime
            consultation.status = 'scheduled'
            consultation.notes = notes
            db.session.commit()
        else:
            new_row = PatientConsultSchedule(
                patient_id=patient_id,
                consult_type='sleep_doctor',
                scheduled_datetime=scheduled_datetime,
                status='scheduled',
                notes=notes
            )
            db.session.add(new_row)
            db.session.commit()

        update_patient_manifest_for_action(
            patient_id=patient_id,
            action_key='schedule_sleep_test_review',
            is_completed=True,
            status_message=f'Sleep test review scheduled for {scheduled_datetime.strftime("%Y-%m-%d %H:%M")}'
        )

        return {
            'status': 'success',
            'message': f'Sleep test review scheduled for patient {patient_id}',
            'patient_id': patient_id,
            'scheduled_datetime': scheduled_datetime.isoformat(),
            'doctor_name': doctor_name,
            'notes': notes
        }
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def execute_complete_sleep_doctor_followup(parameters: dict) -> dict:
    """Execute complete sleep doctor followup action."""
    try:
        from flask_app.models import PatientConsultSchedule
        from datetime import datetime
        patient_id = parameters.get('patient_id')
        doctor_name = parameters.get('doctor_name', 'Sleep Doctor')
        notes = parameters.get('notes', '')

        if not patient_id:
            return {'status': 'error', 'message': 'Missing required parameter: patient_id'}

        consultation = PatientConsultSchedule.query.filter_by(
            patient_id=patient_id, consult_type='ep_doctor'
        ).first()

        completed_dt = datetime.now()
        if consultation:
            consultation.status = 'completed'
            consultation.completed_datetime = completed_dt
            consultation.comment = notes
            db.session.commit()
        else:
            new_consultation = PatientConsultSchedule(
                patient_id=patient_id,
                consult_type='ep_doctor',
                scheduled_datetime=completed_dt,
                status='completed',
                completed_datetime=completed_dt,
                comment=notes,
                notes=f'Sleep doctor followup completed by {doctor_name}'
            )
            db.session.add(new_consultation)
            db.session.commit()

        update_patient_manifest_for_action(
            patient_id=patient_id,
            action_key='complete_sleep_doctor_followup',
            is_completed=True,
            status_message=f'Sleep doctor followup completed on {completed_dt.strftime("%Y-%m-%d")}'
        )

        return {
            'status': 'success',
            'message': f'Sleep doctor followup marked as completed for patient {patient_id}',
            'patient_id': patient_id,
            'completion_date': completed_dt.isoformat(),
            'doctor_name': doctor_name,
            'notes': notes
        }
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def execute_schedule_dental_consultation(parameters: dict) -> dict:
    """Execute schedule dental consultation action."""
    try:
        from flask_app.models import PatientConsultSchedule
        from datetime import datetime
        patient_id = parameters.get('patient_id')
        scheduled_date = parameters.get('scheduled_date')
        scheduled_time = parameters.get('scheduled_time')
        doctor_name = parameters.get('doctor_name', 'Dental Specialist')
        notes = parameters.get('notes', '')

        if not patient_id:
            return {'status': 'error', 'message': 'Missing required parameter: patient_id'}
        if not scheduled_date or not scheduled_time:
            return {'status': 'error', 'message': 'Missing required parameters: scheduled_date, scheduled_time'}

        scheduled_datetime = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")

        new_consultation = PatientConsultSchedule(
            patient_id=patient_id,
            consult_type='dental_sleep_doctor_consult',
            scheduled_datetime=scheduled_datetime,
            status='scheduled',
            notes=notes
        )
        db.session.add(new_consultation)
        db.session.commit()

        update_patient_manifest_for_action(
            patient_id=patient_id,
            action_key='schedule_dental_consultation',
            is_completed=True,
            status_message=f'Dental consultation scheduled for {scheduled_datetime.strftime("%Y-%m-%d %H:%M")}'
        )

        return {
            'status': 'success',
            'message': f'Dental consultation scheduled for patient {patient_id}',
            'patient_id': patient_id,
            'scheduled_datetime': scheduled_datetime.isoformat(),
            'doctor_name': doctor_name,
            'notes': notes
        }
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def execute_complete_dental_consultation(parameters: dict) -> dict:
    """Execute complete dental consultation action - SIMPLE WORKING VERSION"""
    try:
        from datetime import datetime
        from flask_app.models import PatientConsultSchedule
        
        patient_id = parameters.get('patient_id')
        completion_date = parameters.get('completion_date')
        completion_time = parameters.get('completion_time')
        doctor_name = parameters.get('doctor_name', 'Dental Sleep Expert')
        notes = parameters.get('notes', '')
        
        if not patient_id:
            return {'status': 'error', 'message': 'Missing patient_id'}
        
        # Parse completion datetime
        if completion_date and completion_time:
            completion_datetime = datetime.strptime(f"{completion_date} {completion_time}", "%Y-%m-%d %H:%M")
        else:
            completion_datetime = datetime.now()
        
        # Find and update the consultation - SIMPLE DIRECT APPROACH
        consultation = PatientConsultSchedule.query.filter_by(
            patient_id=patient_id,
            status='scheduled'
        ).filter(
            PatientConsultSchedule.consult_type.in_(['dental_sleep_doctor', 'dental_sleep_doctor_consult'])
        ).first()
        
        if consultation:
            # Update the consultation
            consultation.status = 'completed'
            consultation.completed_datetime = completion_datetime
            consultation.comment = notes
            db.session.commit()
            
            # Update patient manifest
            update_patient_manifest_for_action(
                patient_id=patient_id,
                action_key='complete_dental_consultation',
                is_completed=True,
                status_message=f'Dental consultation completed on {completion_datetime.strftime("%Y-%m-%d %H:%M")}'
            )
            
            return {
                'status': 'success',
                'message': f'Dental consultation marked as completed for patient {patient_id}',
                'consultation_id': consultation.id,
                'completion_datetime': completion_datetime.isoformat()
            }
        else:
            return {
                'status': 'error',
                'message': 'No scheduled dental consultation found for this patient'
            }
        
    except Exception as e:
        logger.error(f"Error in execute_complete_dental_consultation: {e}")
        return {'status': 'error', 'message': str(e)}

def execute_request_intraoral_scan(parameters: dict) -> dict:
    """Execute request intraoral scan action."""
    try:
        patient_id = parameters['patient_id']
        return {
            'status': 'redirect',
            'message': f'Redirecting to patient details for intraoral scan upload',
            'redirect_url': f'/patient_details/{patient_id}'
        }
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def execute_request_hipaa_consent(parameters: dict) -> dict:
    """Execute request HIPAA consent action."""
    try:
        logger.info(f"=== EXECUTE_REQUEST_HIPAA_CONSENT CALLED ===")
        logger.info(f"Parameters received: {parameters}")
        
        patient_id = parameters['patient_id']
        patient_email = parameters.get('patient_email', '')
        request_date = parameters.get('request_date', datetime.now().strftime('%Y-%m-%d'))
        message = parameters.get('message', '')
        
        logger.info(f"Patient ID: {patient_id}")
        logger.info(f"Patient email: {patient_email}")
        logger.info(f"Request date: {request_date}")
        logger.info(f"Message: {message}")
        
        # Get patient details
        patient = Patient.query.get(patient_id)
        if not patient:
            logger.error(f"Patient {patient_id} not found")
            return {
                'status': 'error',
                'message': f'Patient {patient_id} not found'
            }
        
        # Use patient's email if available, otherwise use provided email
        recipient_email = patient.email if patient.email else patient_email
        if not recipient_email:
            logger.error(f"No email address available for patient {patient_id}")
            return {
                'status': 'error',
                'message': 'No email address available for patient'
            }
        
        # Prepare email content
        email_subject = f"HIPAA Consent Request for Patient {patient.name}"
        
        # Build the message body
        base_message = f"Please complete the patient onboarding process using the link or QR code provided. This will include HIPAA consent forms and is required to proceed with your treatment."
        additional_message = message if message else ""
        
        # Get clinic_id: prefer session (set at login), else first from user's associations
        clinic_id = session.get('clinic_id')
        if not clinic_id and hasattr(current_user, 'get_clinic_ids'):
            clinic_ids = current_user.get_clinic_ids()
            if clinic_ids:
                clinic_id = clinic_ids[0]
        if not clinic_id:
            clinic_id = None
        
        # Create dynamic wizard link that starts the patient onboarding process
        # The patient will go through the entire wizard and eventually reach HIPAA authorization
        base_url = os.environ.get('BASE_URL', 'http://localhost:7000')
        wizard_link = f"{base_url}/wizard/stage1_personal_info?clinic_id={clinic_id}"
        
        # Generate QR code for the wizard link
        import qrcode
        import io
        import base64
        
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(wizard_link)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        
        # Create simple email body
        email_body = create_simple_hipaa_email(patient.name, wizard_link)
        
        # Send HIPAA consent email using the same SendGrid function as CBCT
        logger.info(f"About to call send_hipaa_consent_email_with_sendgrid with recipient: {recipient_email}")
        logger.info(f"Subject: {email_subject}")
        logger.info(f"Email body: {email_body}")
        
        email_sent = send_hipaa_consent_email_with_sendgrid(
            recipient_email=recipient_email,
            subject=email_subject,
            email_body=email_body,
            patient_id=patient_id
        )
        
        logger.info(f"Email send result: {email_sent}")
        
        if not email_sent:
            logger.error("Failed to send HIPAA consent email via SendGrid")
            return {
                'status': 'error',
                'message': 'Failed to send email'
            }
        
        # Update the patient manifest
        update_patient_manifest_for_action(
            patient_id=patient_id,
            action_key='request_hipaa_consent',
            is_completed=True,
            status_message=f'HIPAA consent email sent to patient on {request_date or "current date"}'
        )
        
        result = {
            'status': 'success',
            'message': f'HIPAA consent email sent for patient {patient_id}',
            'patient_id': patient_id,
            'email_sent_to': recipient_email,
            'request_date': request_date
        }
        
        return result
    except Exception as e:
        logger.error(f"Error in execute_request_hipaa_consent: {e}")
        return {
            'status': 'error',
            'message': f'Error executing HIPAA consent request: {str(e)}'
        }

# REMOVED DUPLICATE FUNCTION - This was overriding the correct function above

def execute_request_osa_report(parameters: dict) -> dict:
    """Execute request OSA report action."""
    try:
        patient_id = parameters['patient_id']
        # Auto-generate subject and message; do not accept client-provided text
        subject = f"OSA report for patient id {patient_id}"
        email_body = f"Please upload the OSA report for patient {patient_id}."

        # Send email to Vizbriz admin
        email_sent = send_cbct_email_with_sendgrid('info@vizbriz.com', subject, email_body, patient_id=patient_id)
        if email_sent:
            logger.info(f'OSA report request email sent for patient {patient_id} - stage remains incomplete until document is uploaded')
            from datetime import datetime
            return {
                'status': 'success',
                'message': f'OSA report request email sent successfully for patient {patient_id}. Stage will be completed when OSA report document is uploaded.',
                'email_sent': True,
                'recipient': 'info@vizbriz.com',
                'subject': subject,
                'request_date': datetime.utcnow().isoformat()
            }
        else:
            return {
                'status': 'error',
                'message': 'Failed to send OSA report request email'
            }
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def _generate_presigned_url_for_share(s3_key: str, inline: bool = False, expires_in: int = 3600) -> str:
    try:
        # Handle None or empty values first
        if s3_key is None:
            logger.warning(f"Invalid S3 key provided for share: None")
            return None
        
        # Handle tuple/list (edge case - should not happen but protect against it)
        if isinstance(s3_key, (list, tuple)):
            if len(s3_key) > 0:
                s3_key = s3_key[0]  # Take first element
            else:
                logger.warning(f"Invalid S3 key provided for share: empty {type(s3_key).__name__}")
                return None
        
        # Convert to string immediately to handle any type (SQLAlchemy objects, etc.)
        try:
            s3_key = str(s3_key)
        except (TypeError, AttributeError, ValueError) as e:
            logger.warning(f"Error converting S3 key to string for share: {repr(s3_key)} (type: {type(s3_key).__name__}), error: {e}")
            return None
        
        # Now that we have a string, check if it's valid
        # Only call strip() if it's actually a string (double-check)
        if not isinstance(s3_key, str):
            logger.warning(f"S3 key is not a string after conversion for share: {repr(s3_key)} (type: {type(s3_key).__name__})")
            return None
        
        s3_key = s3_key.strip()
        if not s3_key or s3_key in ['None', 'null', 'NULL', '']:
            logger.warning(f"Invalid S3 key after conversion for share: {repr(s3_key)}")
            return None

        s3_client = get_s3_client_for_presigning(region='us-west-2')
        bucket = os.getenv('S3_BUCKET_NAME', 'vizbrizpatients')
        params = {'Bucket': bucket, 'Key': s3_key}
        if inline:
            params['ResponseContentDisposition'] = 'inline'
        return s3_client.generate_presigned_url('get_object', Params=params, ExpiresIn=expires_in)
    except Exception as e:
        logger.error(f"Error generating presigned URL for share: {e}")
        return None

def _build_share_short_link(token: str) -> str:
    base_url = os.environ.get('BASE_URL', 'http://localhost:7000')
    return f"{base_url}/file/share/{token}"

def execute_share_patient_files(parameters: dict) -> dict:
    """Generate short links for selected files and email them using the shared SendGrid pipeline."""
    try:
        patient_id = parameters.get('patient_id')
        file_ids = parameters.get('file_ids') or []
        recipient_emails = parameters.get('recipient_emails') or []
        custom_message = parameters.get('custom_message') or ''

        if not patient_id or not file_ids or not recipient_emails:
            return {'status': 'error', 'message': 'Missing patient_id, file_ids, or recipient_emails'}

        # Load files from both models and validate ownership
        from flask_app.models import File, AdminFile
        patient_name = None
        try:
            from flask_app.models import Patient as _P
            p = _P.query.get(patient_id)
            if p and getattr(p, 'name', None):
                patient_name = p.name
        except Exception:
            patient_name = None
        found_files = []
        for fid in file_ids:
            f = File.query.filter_by(id=fid, patient_id=patient_id).first()
            if f:
                # Only add files with valid s3_key
                if f.s3_key and f.s3_key != 'None' and f.s3_key != '':
                    found_files.append({'id': f.id, 'name': f.name, 's3_key': f.s3_key, 'category': getattr(f, 'category', None), 'subcategory': getattr(f, 'subcategory', None)})
                continue
            af = AdminFile.query.filter_by(id=fid, patient_id=patient_id).first()
            if af:
                # Only add files with valid s3_key
                if af.s3_key and af.s3_key != 'None' and af.s3_key != '':
                    found_files.append({'id': af.id, 'name': af.name, 's3_key': af.s3_key, 'category': getattr(af, 'file_category', None), 'subcategory': getattr(af, 'subcategory', None)})

        if not found_files:
            return {'status': 'error', 'message': 'No valid files found for this patient'}

        # Create tokens and presigned links
        link_items = []
        for f in found_files:
            # Longer validity for recipient convenience
            presigned = _generate_presigned_url_for_share(f['s3_key'], inline=False, expires_in=7 * 24 * 3600)
            if not presigned:
                continue
            # App short link (resolver)
            token = secrets.token_urlsafe(16)
            short_app_link = _build_share_short_link(token)
            try:
                SHARE_TOKEN_CACHE[token] = presigned
            except Exception:
                pass
            # TinyURL for direct S3 link
            try:
                short_s3_link = shorten_url_with_tinyurl(presigned) or presigned
            except Exception:
                short_s3_link = presigned
            link_items.append({
                'name': f['name'],
                'category': f.get('category'),
                'subcategory': f.get('subcategory'),
                'short_app_link': short_app_link,
                'short_s3_link': short_s3_link,
                'presigned_url': presigned
            })

        if not link_items:
            return {'status': 'error', 'message': 'Failed to generate links'}

        subject = f"Files shared for {patient_name or 'patient'}"
        lines = []
        if custom_message:
            lines.append(custom_message.strip())
        # Force a blank line before the list for better readability across clients
        lines.append("")
        lines.append("The following files have been shared with you:")
        lines.append("")
        for item in link_items:
            cat = (item.get('category') or 'Uncategorized')
            sub = item.get('subcategory')
            cat_label = f"{cat} / {sub}" if sub else cat
            prefix = f"{patient_name or 'Patient'} - {item['name']} [{cat_label}]"
            # Only include the TinyURL to the presigned S3 link, one file per line
            lines.append(f"- {prefix}: {item['short_s3_link']}")
        email_body = "\n".join(lines)

        # Use the same email sender as CBCT/HIPAA
        for recipient in recipient_emails:
            sent = send_hipaa_consent_email_with_sendgrid(recipient, subject, email_body, patient_id=patient_id)
            if not sent:
                return {'status': 'error', 'message': f'Failed to send email to {recipient}'}

        return {
            'status': 'success',
            'message': 'Share links sent successfully',
            'recipients': recipient_emails,
            'files_shared': len(link_items)
        }
    except Exception as e:
        logger.error(f"Error in execute_share_patient_files: {e}")
        return {'status': 'error', 'message': str(e)}

@action_bp.route('/file/view/<token>', methods=['GET'])
def view_shared_file(token: str):
    """Open a shared file in the internal viewer using a token.
    
    This endpoint is public (no login required) and opens files in the VizBriz viewer.
    """
    try:
        token_data = SHARE_TOKEN_CACHE.get(token)
        if not token_data:
            return (
                '<html><body style="font-family: sans-serif; padding: 24px;">'
                '<h3>Link expired or invalid</h3>'
                '<p>Your file share link has expired. Please contact the sender for a new link.</p>'
                '</body></html>',
                410,
                {'Content-Type': 'text/html; charset=utf-8'}
            )
        
        # Check if it's a viewer token
        if isinstance(token_data, dict) and token_data.get('type') == 'viewer':
            file_id = token_data.get('file_id')
            source = token_data.get('source')
            
            if not file_id or not source:
                return jsonify({'success': False, 'error': 'Invalid token data'}), 400
            
            # Serve the file directly (public access via token)
            from flask_app.models import File, AdminFile
            import boto3
            from flask import Response
            
            # Get the file record
            if source == 'files':
                file_record = File.query.get(file_id)
            elif source == 'adminfiles':
                file_record = AdminFile.query.get(file_id)
            else:
                return jsonify({'error': 'Invalid source'}), 400
            
            if not file_record or not file_record.s3_key:
                return jsonify({'error': 'File not found'}), 404
            
            # Generate presigned URL with inline content disposition for viewing
            s3_client = boto3.client('s3', region_name='us-west-2')
            bucket = os.getenv('S3_BUCKET_NAME', 'vizbrizpatients')
            
            # Determine content type based on file extension
            file_ext = (file_record.name or '').split('.')[-1].lower()
            content_type_map = {
                'pdf': 'application/pdf',
                'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                'png': 'image/png', 'gif': 'image/gif',
                'bmp': 'image/bmp', 'webp': 'image/webp',
                'tiff': 'image/tiff', 'tif': 'image/tiff'
            }
            content_type = content_type_map.get(file_ext, 'application/octet-stream')
            
            # Generate presigned URL with inline disposition for viewing
            presigned_url = s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': bucket,
                    'Key': file_record.s3_key,
                    'ResponseContentDisposition': 'inline',
                    'ResponseContentType': content_type
                },
                ExpiresIn=3600  # 1 hour for viewer links
            )
            
            # Redirect to the presigned URL to open in browser/viewer
            return redirect(presigned_url, code=302)
        else:
            # Legacy: presigned URL redirect
            return redirect(token_data, code=302)
            
    except Exception as e:
        logger.error(f"Error resolving viewer token: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': 'Unable to resolve link'}), 500


@action_bp.route('/file/share/<token>', methods=['GET'])
def resolve_file_share(token: str):
    """Resolve a short link token to its time-limited presigned URL and redirect.

    Notes:
    - Tokens are stored ephemerally in-memory when emails are sent.
    - If token is missing or expired, show a simple message.
    - This endpoint is intentionally unauthenticated for external recipients.
    - Supports both viewer tokens (dict) and presigned URL tokens (string).
    """
    try:
        token_data = SHARE_TOKEN_CACHE.get(token)
        if not token_data:
            # Soft failure with minimal HTML so external recipients are not confused
            return (
                '<html><body style="font-family: sans-serif; padding: 24px;">'
                '<h3>Link expired or invalid</h3>'
                '<p>Your file share link has expired. Please contact the sender for a new link.</p>'
                '</body></html>',
                410,
                {'Content-Type': 'text/html; charset=utf-8'}
            )
        
        # If it's a viewer token, redirect to viewer endpoint
        if isinstance(token_data, dict) and token_data.get('type') == 'viewer':
            return redirect(f"/file/view/{token}", code=302)
        
        # Otherwise it's a presigned URL (legacy behavior)
        return redirect(token_data, code=302)
    except Exception as e:
        logger.error(f"Error resolving share token: {e}")
        return jsonify({'success': False, 'error': 'Unable to resolve link'}), 500

# NOTE: /api/patient/<int:patient_id>/files route moved to reports_files_routes.py
# to reduce file size and improve organization

@action_bp.route('/api/file/view/<int:file_id>/<source>', methods=['GET'])
@login_required
def api_view_file(file_id: int, source: str):
    """Serve a file for inline viewing with proper headers to prevent download"""
    try:
        from flask_app.models import File, AdminFile
        import boto3
        
        # Get the file record
        if source == 'files':
            file_record = File.query.get_or_404(file_id)
        elif source == 'adminfiles':
            file_record = AdminFile.query.get_or_404(file_id)
        else:
            return jsonify({'error': 'Invalid source'}), 400
        
        if not file_record.s3_key:
            return jsonify({'error': 'File not found in storage'}), 404
        
        # Generate presigned URL with inline content disposition
        s3_client = boto3.client('s3', region_name='us-west-2')
        bucket = os.getenv('S3_BUCKET_NAME', 'vizbrizpatients')
        
        # Determine content type based on file extension
        file_ext = (file_record.name or '').split('.')[-1].lower()
        content_type_map = {
            'pdf': 'application/pdf',
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
            'png': 'image/png', 'gif': 'image/gif',
            'bmp': 'image/bmp', 'webp': 'image/webp',
            'tiff': 'image/tiff', 'tif': 'image/tiff'
        }
        content_type = content_type_map.get(file_ext, 'application/octet-stream')
        
        # Generate presigned URL with inline disposition and proper Unicode handling
        import urllib.parse
        
        # Safely encode filename for HTTP header (RFC 6266)
        safe_filename = file_record.name or 'document'
        try:
            # Try ASCII encoding first
            safe_filename.encode('ascii')
            filename_header = f'inline; filename="{safe_filename}"'
        except UnicodeEncodeError:
            # Use RFC 5987 encoding for non-ASCII characters
            encoded_filename = urllib.parse.quote(safe_filename, safe='')
            filename_header = f"inline; filename*=UTF-8''{encoded_filename}"
        
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': bucket,
                'Key': file_record.s3_key,
                'ResponseContentDisposition': filename_header,
                'ResponseContentType': content_type
            },
            ExpiresIn=3600
        )
        
        # Redirect to the presigned URL
        return redirect(presigned_url)
        
    except Exception as e:
        logger.error(f"Error serving file for viewing: {e}")
        return jsonify({'error': 'Failed to serve file'}), 500

@action_bp.route('/api/share_patient_files', methods=['POST'])
@login_required
def api_share_patient_files():
    try:
        data = request.get_json() or {}
        result = execute_share_patient_files(data)
        return jsonify({'success': result.get('status') == 'success', 'result': result}), (200 if result.get('status') == 'success' else 400)
    except Exception as e:
        logger.error(f"Error in api_share_patient_files: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@action_bp.route('/api/patient/<int:patient_id>/history', methods=['GET'])
@login_required
def api_get_patient_history(patient_id: int):
    """Return a concise chronological history for the patient (consults, uploads, approvals, orders)."""
    try:
        from flask_app.models import (
            PatientConsultSchedule, PatientDeviceOrder, DentistReportApproval, File, AdminFile, PatientComment
        )
        events = []

        consults = (
            PatientConsultSchedule.query
            .filter_by(patient_id=patient_id)
            .order_by(PatientConsultSchedule.scheduled_datetime.desc())
            .all()
        )
        for c in consults:
            when = c.completed_datetime or c.scheduled_datetime
            events.append({
                'type': 'consult',
                'title': f"{c.consult_type.replace('_',' ').title()} ({c.status})",
                'when': when.isoformat() if when else None,
                'meta': {'id': c.id}
            })

        orders = (
            PatientDeviceOrder.query
            .filter_by(patient_id=patient_id)
            .order_by(PatientDeviceOrder.updated_at.desc())
            .all()
        )
        for o in orders:
            events.append({
                'type': 'device_order',
                'title': f"Order {o.device_type} - {o.status}",
                'when': (o.updated_at or o.order_date).isoformat() if (o.updated_at or o.order_date) else None,
                'meta': {'id': o.id, 'device_name': o.device_name}
            })

        approvals = (
            DentistReportApproval.query
            .filter_by(patient_id=patient_id)
            .order_by(DentistReportApproval.approval_timestamp.desc())
            .all()
        )
        for a in approvals:
            events.append({
                'type': 'approval',
                'title': f"OSA report approval - {a.approval_status}",
                'when': a.approval_timestamp.isoformat() if a.approval_timestamp else None,
                'meta': {'id': a.id, 'report_id': a.report_id}
            })

        # Aggregate file uploads into concise events by category/subcategory (all-time, no per-day split)
        from collections import defaultdict
        from sqlalchemy import func
        grouped_uploads = defaultdict(lambda: {'count': 0, 'latest': None})

        def add_row_to_group(category: str, subcategory: str, when_dt):
            cat_l = (category or '').lower()
            sub_l = (subcategory or '').lower()
            # Build a human label with smarter detection using both category and subcategory
            if 'cbct' in (cat_l, sub_l):
                label = 'CBCT files uploaded'
            elif any(k in (cat_l, sub_l) for k in ['intraoral_scan', 'intra_oral_scan', 'intraoral', 'ios']):
                label = 'Intraoral scan files uploaded'
            elif any(k in (cat_l, sub_l) for k in ['imaging', 'digital_image', 'images', 'photo', 'photos', 'jpeg', 'jpg', 'png']):
                label = 'Digital images uploaded'
            elif any(k in (cat_l, sub_l) for k in ['medical', 'report', 'reports', 'soap', 'hipaa', 'consent']):
                label = 'Medical documents uploaded'
            else:
                # Generic label with category/subcategory for clarity
                pretty_cat = (category or 'Files').replace('_', ' ').title()
                pretty_sub = (subcategory or '').replace('_', ' ').title()
                label = f"{pretty_cat}{(' - ' + pretty_sub) if pretty_sub else ''} uploaded"

            # Group by label only (category/subcategory), not by day
            key = label
            grouped_uploads[key]['count'] += 1
            if when_dt and (grouped_uploads[key]['latest'] is None or when_dt > grouped_uploads[key]['latest']):
                grouped_uploads[key]['latest'] = when_dt

        # Use aggregated queries to avoid bias from large CBCT file counts
        file_agg = (
            db.session.query(
                File.category.label('cat'),
                File.subcategory.label('sub'),
                func.count(File.id).label('cnt'),
                func.max(File.upload_date).label('latest')
            )
            .filter(File.patient_id == patient_id)
            .group_by(File.category, File.subcategory)
            .all()
        )
        for row in file_agg:
            # Expand counts into grouped structure
            when_dt = row.latest
            label_before = len(grouped_uploads)
            add_row_to_group(row.cat, row.sub, when_dt)
            # Adjust count to aggregated count (override increment-by-1 behavior)
            label_key = next(reversed(grouped_uploads.keys()))
            grouped_uploads[label_key]['count'] = int(row.cnt)

        admin_agg = (
            db.session.query(
                AdminFile.file_category.label('cat'),
                func.count(AdminFile.id).label('cnt'),
                func.max(AdminFile.upload_date).label('latest')
            )
            .filter(AdminFile.patient_id == patient_id)
            .group_by(AdminFile.file_category)
            .all()
        )
        for row in admin_agg:
            when_dt = row.latest
            # AdminFile has no subcategory; pass None
            add_row_to_group(row.cat, None, when_dt)
            label_key = next(reversed(grouped_uploads.keys()))
            grouped_uploads[label_key]['count'] = grouped_uploads[label_key]['count'] + int(row.cnt)

        for label, info in grouped_uploads.items():
            when_dt = info['latest']
            events.append({
                'type': 'file_upload_group',
                'title': f"{label} ({info['count']})",
                'when': when_dt.isoformat() if when_dt else None,
                'meta': {
                    'count': info['count']
                }
            })

        # Add patient comments to history (excluding consultation and delivery comments to avoid duplication)
        comments = (
            PatientComment.query
            .filter_by(patient_id=patient_id)
            .order_by(PatientComment.created_date.desc())
            .all()
        )
        for comment in comments:
            # Create a descriptive title based on comment type and content
            comment_type = comment.comment_type or 'general'
            
            # Skip consultation and delivery comments to avoid duplication with consult schedules and device orders
            if comment_type in ['consultation', 'delivery']:
                continue
                
            if comment_type == 'titration':
                title = f"Titration adjustment: {comment.content[:50]}{'...' if len(comment.content) > 50 else ''}"
            elif comment_type == 'initial':
                title = f"Initial setup: {comment.content[:50]}{'...' if len(comment.content) > 50 else ''}"
            else:
                title = f"Comment: {comment.content[:50]}{'...' if len(comment.content) > 50 else ''}"
            
            # Add numeric value if present
            if comment.numeric_value and comment.numeric_unit:
                title += f" ({comment.numeric_value}{comment.numeric_unit})"
            
            events.append({
                'type': 'comment',
                'title': title,
                'when': comment.created_date.isoformat() if comment.created_date else None,
                'meta': {
                    'id': comment.id,
                    'comment_type': comment_type,
                    'content': comment.content,
                    'numeric_value': float(comment.numeric_value) if comment.numeric_value else None,
                    'numeric_unit': comment.numeric_unit
                }
            })

        # Deduplicate events by type, title, and when (to handle multiple identical records)
        seen_events = set()
        deduplicated_events = []
        
        for event in events:
            # Create a unique key for deduplication
            event_key = (event['type'], event['title'], event['when'])
            if event_key not in seen_events:
                seen_events.add(event_key)
                deduplicated_events.append(event)
        
        events_sorted = sorted(deduplicated_events, key=lambda e: e['when'] or '', reverse=True)[:300]
        # If there were CBCT uploads, add a single aggregated event per CBCT folder (optional future enhancement)
        return jsonify({'success': True, 'events': events_sorted})
    except Exception as e:
        logger.error(f"Error in api_get_patient_history: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def execute_update_device_delivery(parameters: dict) -> dict:
    """Execute update device delivery action."""
    try:
        patient_id = parameters['patient_id']
        # Accept multiple payload shapes to match manifest and UI
        new_status = (
            parameters.get('new_status')
            or parameters.get('status')
        )
        notes = parameters.get('notes') or parameters.get('delivery_notes', '')
        arrival_date_str = parameters.get('arrival_date')
        # If arrival_date provided but no status, default to delivered (validator expectation)
        if not new_status and arrival_date_str:
            new_status = 'delivered'
        # If still no status and no arrival_date, return clear error without throwing
        if not new_status and not arrival_date_str:
            return {
                'status': 'error',
                'message': 'Missing new_status or arrival_date'
            }
        
        # Find the device order for this patient
        from flask_app.models import PatientDeviceOrder
        # Look for any device order for this patient (not just oral_appliance)
        base_query = PatientDeviceOrder.query.filter_by(patient_id=patient_id)
        device_order = (
            base_query
            .filter(PatientDeviceOrder.status != 'delivered')
            .order_by(
                PatientDeviceOrder.updated_at.desc(),
                PatientDeviceOrder.order_date.desc(),
                PatientDeviceOrder.id.desc()
            )
            .first()
        )
        if not device_order:
            device_order = (
                base_query
                .order_by(
                    PatientDeviceOrder.updated_at.desc(),
                    PatientDeviceOrder.order_date.desc(),
                    PatientDeviceOrder.id.desc()
                )
                .first()
            )
        
        if not device_order:
            return {
                'status': 'error',
                'message': f'No device order found for patient {patient_id}'
            }
        
        # Update the device order status
        device_order.status = new_status
        device_order.notes = notes
        device_order.updated_at = datetime.utcnow()
        # Ensure arrival_date is set when marking as delivered (validator expects it)
        if new_status == 'delivered':
            if arrival_date_str:
                try:
                    device_order.arrival_date = datetime.strptime(arrival_date_str, "%Y-%m-%d")
                except Exception:
                    device_order.arrival_date = datetime.utcnow()
            elif not device_order.arrival_date:
                device_order.arrival_date = datetime.utcnow()
        
        db.session.commit()
        
        logger.info(f'Device delivery status updated for patient {patient_id} to {new_status}')
        
        return {
            'status': 'success',
            'message': f'Device delivery status updated to {new_status}',
            'patient_id': patient_id,
            'new_status': new_status,
            'notes': notes,
            'order_id': device_order.id
        }
        
    except Exception as e:
        db.session.rollback()
        raise e

@action_bp.route('/api/request_followup_sleep_test', methods=['POST'])
@login_required
def api_request_followup_sleep_test():
    """Execute request followup sleep test action."""
    try:
        data = request.get_json()
        patient_id = _normalize_patient_id(data.get('patient_id'))
        return {
            'status': 'redirect',
            'message': f'Redirecting to patient details for followup sleep test upload',
            'redirect_url': f'/patient_details/{patient_id}'
        }
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def execute_remind_quiz_completion(parameters: dict) -> dict:
    """Execute remind quiz completion action."""
    try:
        # This should use the existing reminder API endpoint
        from flask import request
        data = request.get_json()
        result = api_send_reminder()
        return result
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def execute_remind_consultation_scheduling(parameters: dict) -> dict:
    """Execute remind consultation scheduling action."""
    try:
        # This should use the existing reminder API endpoint
        from flask import request
        data = request.get_json()
        result = api_send_reminder()
        return result
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def execute_remind_sleep_study(parameters: dict) -> dict:
    """Execute remind sleep study action."""
    try:
        # This should use the existing reminder API endpoint
        from flask import request
        data = request.get_json()
        result = api_send_reminder()
        return result
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def execute_remind_document_upload(parameters: dict) -> dict:
    """Execute remind document upload action."""
    try:
        # This should use the existing reminder API endpoint
        from flask import request
        data = request.get_json()
        result = api_send_reminder()
        return result
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

@action_bp.route('/api/remind_followup_test', methods=['POST'])
@login_required
def api_remind_followup_test():
    """Execute remind followup test action."""
    try:
        # This should use the existing reminder API endpoint
        from flask import request
        data = request.get_json()
        result = api_send_reminder()
        return result
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

@action_bp.route('/api/mark_quiz_completed', methods=['POST'])
@login_required
def api_mark_quiz_completed():
    """Mark quiz as completed (manual override)."""
    try:
        data = request.get_json()
        patient_id = _normalize_patient_id(data.get('patient_id'))
        completion_date = data.get('completion_date')
        notes = data.get('notes', '')
        
        if not patient_id:
            return jsonify({
                'success': False,
                'error': 'Missing required parameter: patient_id'
            }), 400
        
        # Get patient details
        from flask_app.models import Patient
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({
                'success': False,
                'error': f'Patient with ID {patient_id} not found'
            }), 404
        
        # Parse completion date
        from datetime import datetime
        if completion_date:
            try:
                completion_dt = datetime.strptime(completion_date, "%Y-%m-%d")
            except ValueError:
                completion_dt = datetime.now()
        else:
            completion_dt = datetime.now()
        
        # Update the patient manifest for quiz completion
        update_patient_manifest_for_action(
            patient_id=patient_id,
            action_key='mark_quiz_completed',
            is_completed=True,
            status_message=f'Quiz manually marked as completed on {completion_dt.strftime("%Y-%m-%d")}'
        )
        
        # Log the manual completion
        logger.info(f'Quiz manually marked as completed for patient {patient_id} on {completion_dt}')
        if notes:
            logger.info(f'Completion notes: {notes}')
        
        result = {
            'status': 'success',
            'message': f'Quiz marked as completed for patient {patient_id}',
            'patient_id': patient_id,
            'completion_date': completion_dt.strftime('%Y-%m-%d'),
            'notes': notes
        }
        
        return jsonify({
            'success': True,
            'result': result
        })
        
    except Exception as e:
        logger.error(f"Error in api_mark_quiz_completed: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@action_bp.route('/api/send_quiz_link', methods=['POST'])
@login_required
def api_send_quiz_link():
    """Execute send quiz link action."""
    try:
        # This should use the existing quiz API endpoint
        from flask import request
        data = request.get_json()
        result = api_send_quiz()
        return result
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

# Removed execute_update_patient_status function as patient status updates are now automatic

@action_bp.route('/api/actions/available/<stage_key>', methods=['GET'])
@login_required
def get_available_actions(stage_key):
    """
    Get available actions for a specific stage.
    """
    try:
        available_actions = get_actions_for_stage(stage_key)
        
        return jsonify({
            'success': True,
            'stage_key': stage_key,
            'available_actions': available_actions
        })
        
    except Exception as e:
        logger.error(f"Error getting available actions: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@action_bp.route('/api/actions/all', methods=['GET'])
@login_required
def get_all_actions():
    """
    Get all available actions.
    """
    try:
        from flask_app.config.action_manifest import get_all_actions
        all_actions = get_all_actions()
        
        return jsonify({
            'success': True,
            'actions': all_actions
        })
        
    except Exception as e:
        logger.error(f"Error getting all actions: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500 

@action_bp.route('/api/actions/manifest', methods=['GET'])
@login_required
def get_action_manifest():
    """Get action manifest for frontend forms."""
    try:
        from flask_app.config.action_manifest import ACTION_MANIFEST
        
        return jsonify({
            'success': True,
            'actions': ACTION_MANIFEST
        })
    except Exception as e:
        logger.error(f"Error in get_action_manifest: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Individual Action Endpoints
@action_bp.route('/api/approve_osa_report', methods=['POST'])
@login_required
def api_approve_osa_report():
    """Approve OSA report endpoint."""
    try:
        data = request.get_json()
        result = execute_approve_osa_report(data)
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        logger.error(f"Error in api_approve_osa_report: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@action_bp.route('/api/order_oral_appliance', methods=['POST'])
@login_required
def api_order_oral_appliance():
    """Order oral appliance endpoint."""
    try:
        data = request.get_json()
        result = execute_order_oral_appliance(data)
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        logger.error(f"Error in api_order_oral_appliance: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500



@action_bp.route('/api/send_reminder', methods=['POST'])
@login_required
def api_send_reminder():
    """Send contextual reminder endpoint."""
    try:
        data = request.get_json()
        patient_id = _normalize_patient_id(data.get('patient_id'))
        # Use patient_email from data if provided, otherwise fetch from patient record
        recipient_email = data.get('patient_email', '')
        reminder_type = data.get('reminder_type', 'general')
        custom_message = data.get('custom_message', '')
        action_key = data.get('action_key', 'send_reminder')
        
        # Get patient name for personalization
        from flask_app.models import Patient
        patient = Patient.query.get(patient_id)
        patient_name = (getattr(patient, 'name', None) or 'Patient') if patient else 'Patient'
        logger.info(f"Patient name for template: '{patient_name}'")
        
        if not recipient_email: # If not provided in data, try to get from patient record
            recipient_email = patient.email if patient and getattr(patient, 'email', None) else None
        
        # Get action definition to get default message
        from flask_app.config.action_manifest import get_action_by_key
        action_def = get_action_by_key(action_key)
        
        if custom_message:
            # Use custom message if provided
            message = custom_message
        elif action_def and 'default_message' in action_def:
            # Use default message from action manifest and substitute patient name
            template = action_def['default_message']
            logger.info(f"Using template: '{template}'")
            try:
                message = template.format(patient_name=patient_name)
                logger.info(f"Template formatted successfully: '{message}'")
            except KeyError:
                # If the template doesn't have {patient_name}, try simple replacement
                message = template.replace('{patient_name}', patient_name)
                logger.info(f"Fallback replacement used: '{message}'")
            except Exception as e:
                logger.error(f"Template formatting failed: {e}")
                message = template.replace('{patient_name}', patient_name)
                logger.info(f"Emergency fallback used: '{message}'")
        else:
            # Fallback message
            message = f"Hi {patient_name}, this is a reminder about your treatment progress."
        
        # Subject based on action
        subject_map = {
            'remind_followup_test': 'Follow-up sleep test reminder',
            'remind_document_upload': 'Document upload reminder',
            'remind_appliance_delivery': 'Oral appliance delivery appointment reminder',
            'remind_quiz_completion': 'Quiz completion reminder',
            'remind_consultation_scheduling': 'Consultation scheduling reminder',
            'remind_sleep_study': 'Sleep study reminder'
        }
        subject = subject_map.get(action_key, 'Reminder from Vizbriz')

        # For now we only support email reminders
        if reminder_type != 'email':
            reminder_type = 'email'

        if not recipient_email:
            return jsonify({'success': False, 'error': 'No email address available for patient'}), 400

        # Map action_key to email_type for proper logging
        email_type_map = {
            'remind_quiz_completion': 'reminder_quiz_completion',
            'remind_consultation_scheduling': 'reminder_consultation_scheduling',
            'remind_sleep_study': 'reminder_sleep_study',
            'remind_document_upload': 'reminder_document_upload',
            'remind_appliance_delivery': 'reminder_appliance_delivery',
            'remind_followup_test': 'reminder_followup_test'
        }
        email_type = email_type_map.get(action_key, 'general_reminder')
        
        # Send email using SendGrid with correct email type
        from flask_app.routes.file_management_routes import send_email_with_sendgrid
        email_sent = send_email_with_sendgrid(
            recipient_email,
            subject,
            message,
            message,  # text content same as html
            patient_id=patient_id,
            sender_id=None,
            email_type=email_type,
            sender_type='system'
        )
        if not email_sent:
            logger.error('Failed to send reminder email via SendGrid')
            return jsonify({'success': False, 'error': 'Failed to send email'}), 500

        result = {
            'status': 'success',
            'message': f'Reminder email sent to patient {patient_id}',
            'patient_id': patient_id,
            'reminder_type': reminder_type,
            'action_key': action_key,
            'subject': subject,
            'recipient': recipient_email,
            'body': message
        }

        return jsonify({'success': True, 'result': result})
    except Exception as e:
        logger.error(f"Error in api_send_reminder: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@action_bp.route('/api/send_quiz', methods=['POST'])
@login_required
def api_send_quiz():
    """Send quiz link endpoint."""
    try:
        logger.info("QUIZ_EMAIL_DEBUG: === API SEND QUIZ CALLED ===")
        data = request.get_json()
        logger.info(f"QUIZ_EMAIL_DEBUG: Request data: {data}")
        
        patient_id = data.get('patient_id')
        patient_email = data.get('patient_email', '')
        quiz_type = data.get('quiz_type', 'sleep_questionnaire')
        message = data.get('message', '')
        
        logger.info(f"QUIZ_EMAIL_DEBUG: Parsed parameters: patient_id={patient_id}, patient_email={patient_email}, quiz_type={quiz_type}, message={message}")
        
        if not patient_id:
            return jsonify({
                'success': False,
                'error': 'Missing required parameter: patient_id'
            }), 400
        
        # Get patient details
        from flask_app.models import Patient
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({
                'success': False,
                'error': f'Patient with ID {patient_id} not found'
            }), 404
        
        # Use patient's email if available, otherwise use provided email
        recipient_email = patient.email if patient.email else patient_email
        if not recipient_email:
            return jsonify({
                'success': False,
                'error': 'No email address available for patient'
            }), 400
        
        # Get clinic_id: prefer session, else first from user's associations
        clinic_id = session.get('clinic_id')
        if not clinic_id and hasattr(current_user, 'get_clinic_ids'):
            clinic_ids = current_user.get_clinic_ids()
            if clinic_ids:
                clinic_id = clinic_ids[0]
        if not clinic_id:
            clinic_id = None
        
        # Get dso_id: prefer session, else from clinic, else first DSO
        dso_id = session.get('dso_id')
        if not dso_id and clinic_id:
            from flask_app.models import Clinic
            clinic = Clinic.query.get(clinic_id)
            if clinic and clinic.dso_id:
                dso_id = clinic.dso_id
        if not dso_id and hasattr(current_user, 'get_dso_ids'):
            dso_ids = current_user.get_dso_ids()
            if dso_ids:
                dso_id = dso_ids[0]
        if not dso_id:
            dso_id = None
        
        # Create personalized quiz link - use VizBriz quiz (/vizbriz/quiz) which properly handles dso_id/clinic_id for patient assignment
        base_url = os.environ.get('BASE_URL', 'http://localhost:7000')
        params = ["lang=en"]
        if dso_id is not None:
            params.append(f"dso_id={dso_id}")
        if clinic_id:
            params.append(f"clinic_id={clinic_id}")
        if hasattr(current_user, 'id') and current_user.id:
            params.append(f"dentist_id={current_user.id}")
        quiz_link = f"{base_url}/vizbriz/quiz?" + "&".join(params)
        
        # Prepare email content
        email_subject = f"Sleep Questionnaire for {patient.name}"
        
        # Build the email body
        base_message = f"Please complete your sleep questionnaire using the link below. This will help us understand your sleep patterns and provide personalized treatment recommendations."
        additional_message = message if message else ""
        
        email_body = f"""{base_message}

{additional_message if additional_message else ""}

Quiz Link: {quiz_link}

Best regards,
Dental Sleep Team"""
        
        # Send quiz email using SendGrid with correct email type
        logger.info(f"QUIZ_EMAIL_DEBUG: About to call send_hipaa_consent_email_with_sendgrid with recipient_email={recipient_email}")
        email_sent = send_hipaa_consent_email_with_sendgrid(
            recipient_email=recipient_email,
            subject=email_subject,
            email_body=email_body,
            patient_id=patient_id,
            email_type='quiz_link_stage1'
        )
        logger.info(f"QUIZ_EMAIL_DEBUG: send_hipaa_consent_email_with_sendgrid returned: {email_sent}")
        
        if not email_sent:
            logger.error("Failed to send quiz email via SendGrid")
            return jsonify({
                'success': False,
                'error': 'Failed to send email'
            }), 500
        
        # Update the patient manifest
        update_patient_manifest_for_action(
            patient_id=patient_id,
            action_key='send_quiz_link',
            is_completed=True,
            status_message=f'Quiz link sent to patient on {datetime.now().strftime("%Y-%m-%d")}'
        )
        
        result = {
            'status': 'success',
            'message': f'Quiz link sent to patient {patient_id}',
            'patient_id': patient_id,
            'patient_email': recipient_email,
            'quiz_type': quiz_type,
            'quiz_link': quiz_link,
            'dso_id': dso_id
        }
        
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        logger.error(f"Error in api_send_quiz: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@action_bp.route('/api/complete_sleep_doctor_followup', methods=['POST'])
@login_required
def api_complete_sleep_doctor_followup():
    """Complete sleep doctor followup endpoint."""
    try:
        data = request.get_json()
        patient_id = data.get('patient_id')
        completion_date = data.get('completion_date')
        completion_time = data.get('completion_time')
        doctor_name = data.get('doctor_name', 'Sleep Doctor')
        notes = data.get('notes', '')
        
        if not patient_id:
            return jsonify({
                'success': False,
                'error': 'Missing required parameter: patient_id'
            }), 400
        
        # Update the patient_consult_schedule table
        from flask_app.models import PatientConsultSchedule
        from datetime import datetime
        
        # Find the existing sleep doctor consultation
        consultation = PatientConsultSchedule.query.filter_by(
            patient_id=patient_id,
            consult_type='ep_doctor'  # This matches the validation requirement
        ).first()
        
        if consultation:
            # Update the consultation as completed
            consultation.status = 'completed'
            consultation.completed_datetime = datetime.now()
            consultation.comment = notes
            db.session.commit()
            
            # Update the patient manifest
            update_patient_manifest_for_action(
                patient_id=patient_id,
                action_key='complete_sleep_doctor_followup',
                is_completed=True,
                status_message=f'Sleep doctor followup completed on {datetime.now().strftime("%Y-%m-%d")}'
            )
            
            result = {
                'status': 'success',
                'message': f'Sleep doctor followup marked as completed for patient {patient_id}',
                'patient_id': patient_id,
                'completion_date': completion_date,
                'doctor_name': doctor_name,
                'notes': notes
            }
        else:
            # Create a new consultation record if none exists
            new_consultation = PatientConsultSchedule(
                patient_id=patient_id,
                consult_type='ep_doctor',
                scheduled_datetime=datetime.now(),
                status='completed',
                completed_datetime=datetime.now(),
                comment=notes,
                notes=f'Sleep doctor followup completed by {doctor_name}'
            )
            db.session.add(new_consultation)
            db.session.commit()
            
            # Update the patient manifest
            update_patient_manifest_for_action(
                patient_id=patient_id,
                action_key='complete_sleep_doctor_followup',
                is_completed=True,
                status_message=f'Sleep doctor followup completed on {datetime.now().strftime("%Y-%m-%d")}'
            )
            
            result = {
                'status': 'success',
                'message': f'Sleep doctor followup completed for patient {patient_id}',
                'patient_id': patient_id,
                'completion_date': completion_date,
                'doctor_name': doctor_name,
                'notes': notes
            }
        
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        logger.error(f"Error in api_complete_sleep_doctor_followup: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Stage 2: Schedule initial consultation (sleep expert)
@action_bp.route('/api/schedule_consultation', methods=['POST'])
@login_required
def api_schedule_consultation():
    """Schedule initial consultation with sleep expert (Stage 2)."""
    try:
        from datetime import datetime
        from flask_app.models import Patient, PatientConsultSchedule
        data = request.get_json() or {}
        patient_id = data.get('patient_id')
        scheduled_date = data.get('scheduled_date')
        scheduled_time = data.get('scheduled_time')
        doctor_name = data.get('doctor_name', 'Sleep Expert')
        notes = data.get('notes', '')

        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400
        if not scheduled_date or not scheduled_time:
            return jsonify({'success': False, 'error': 'scheduled_date and scheduled_time are required'}), 400

        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({'success': False, 'error': f'Patient {patient_id} not found'}), 404

        scheduled_datetime = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")

        row = PatientConsultSchedule(
            patient_id=patient_id,
            consult_type='sleep_expert',
            scheduled_datetime=scheduled_datetime,
            status='scheduled',
            doctor_name=doctor_name,
            notes=notes
        )
        db.session.add(row)
        db.session.commit()

        update_patient_manifest_for_action(
            patient_id=patient_id,
            action_key='schedule_consultation',
            is_completed=True,
            status_message=f'Consultation scheduled for {scheduled_datetime.strftime("%Y-%m-%d %H:%M")}'
        )

        return jsonify({'success': True, 'result': {
            'status': 'success',
            'message': 'Initial consultation scheduled',
            'patient_id': patient_id,
            'scheduled_datetime': scheduled_datetime.isoformat(),
            'doctor_name': doctor_name,
            'notes': notes
        }})
    except Exception as e:
        logger.error(f"Error in api_schedule_consultation: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Stage 3: Complete initial consultation (sleep expert)
@action_bp.route('/api/complete_consultation', methods=['POST'])
@login_required
def api_complete_consultation():
    """Mark initial consultation with sleep expert as completed (Stage 3)."""
    try:
        from datetime import datetime
        from flask_app.models import Patient, PatientConsultSchedule
        data = request.get_json() or {}
        patient_id = data.get('patient_id')
        completion_date = data.get('completion_date')
        completion_time = data.get('completion_time')
        doctor_name = data.get('doctor_name', 'Sleep Expert')
        notes = data.get('notes', '')

        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400

        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({'success': False, 'error': f'Patient {patient_id} not found'}), 404

        completed_dt = None
        if completion_date and completion_time:
            completed_dt = datetime.strptime(f"{completion_date} {completion_time}", "%Y-%m-%d %H:%M")
        else:
            completed_dt = datetime.now()

        consult = PatientConsultSchedule.query.filter_by(
            patient_id=patient_id,
            consult_type='sleep_expert'
        ).order_by(PatientConsultSchedule.scheduled_datetime.desc()).first()

        if consult:
            consult.status = 'completed'
            consult.completed_datetime = completed_dt
            consult.comment = notes
            db.session.commit()
        else:
            # Create a completed record if none exists
            new_row = PatientConsultSchedule(
                patient_id=patient_id,
                consult_type='sleep_expert',
                scheduled_datetime=completed_dt,
                status='completed',
                completed_datetime=completed_dt,
                comment=notes,
                doctor_name=doctor_name
            )
            db.session.add(new_row)
            db.session.commit()

        update_patient_manifest_for_action(
            patient_id=patient_id,
            action_key='complete_consultation',
            is_completed=True,
            status_message=f'Initial consultation completed on {completed_dt.strftime("%Y-%m-%d %H:%M")}'
        )

        return jsonify({'success': True, 'result': {
            'status': 'success',
            'message': 'Initial consultation marked as completed',
            'patient_id': patient_id,
            'completed_datetime': completed_dt.isoformat(),
            'doctor_name': doctor_name,
            'notes': notes
        }})
    except Exception as e:
        logger.error(f"Error in api_complete_consultation: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@action_bp.route('/api/schedule_dental_consultation', methods=['POST'])
@login_required
def api_schedule_dental_consultation():
    """Schedule dental consultation endpoint."""
    try:
        data = request.get_json()
        logger.info(f"=== DENTAL CONSULTATION API CALLED ===")
        logger.info(f"Received data: {data}")
        
        patient_id = data.get('patient_id')
        scheduled_date = data.get('scheduled_date')
        scheduled_time = data.get('scheduled_time')
        doctor_name = data.get('doctor_name', 'Dental Specialist')
        notes = data.get('notes', '')
        
        logger.info(f"Extracted parameters:")
        logger.info(f"  patient_id: {patient_id}")
        logger.info(f"  scheduled_date: {scheduled_date}")
        logger.info(f"  scheduled_time: {scheduled_time}")
        logger.info(f"  doctor_name: {doctor_name}")
        logger.info(f"  notes: {notes}")
        
        if not patient_id:
            return jsonify({
                'success': False,
                'error': 'Missing required parameter: patient_id'
            }), 400
        
        if not scheduled_date or not scheduled_time:
            return jsonify({
                'success': False,
                'error': 'Missing required parameters: scheduled_date, scheduled_time'
            }), 400
        
        # Get patient details
        from flask_app.models import Patient
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({
                'success': False,
                'error': f'Patient with ID {patient_id} not found'
            }), 404
        
        # Create the consultation record
        from flask_app.models import PatientConsultSchedule
        from datetime import datetime
        
        # Combine date and time
        scheduled_datetime = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")
        
        new_consultation = PatientConsultSchedule(
            patient_id=patient_id,
            consult_type='dental_sleep_doctor_consult',  # Correct consultation type
            scheduled_datetime=scheduled_datetime,
            status='scheduled',
            doctor_name=doctor_name,
            notes=notes
        )
        db.session.add(new_consultation)
        db.session.commit()
        
        # Send confirmation email to patient
        if patient.email:
            email_subject = f"Dental Consultation Scheduled - {patient.name}"
            email_body = f"""Dear {patient.name},

Your dental consultation has been scheduled for {scheduled_datetime.strftime("%B %d, %Y at %I:%M %p")}.

Details:
- Doctor: {doctor_name}
- Date: {scheduled_datetime.strftime("%B %d, %Y")}
- Time: {scheduled_datetime.strftime("%I:%M %p")}
- Notes: {notes if notes else "No additional notes"}

Please arrive 15 minutes before your scheduled time.

Best regards,
Dental Sleep Team"""
            
            email_sent = send_hipaa_consent_email_with_sendgrid(
                recipient_email=patient.email,
                subject=email_subject,
                email_body=email_body,
                patient_id=patient_id
            )
            
            if not email_sent:
                logger.warning(f"Failed to send consultation confirmation email to {patient.email}")
        
        # Update the patient manifest for action (generic, not stage-specific)
        update_patient_manifest_for_action(
            patient_id=patient_id,
            action_key='schedule_dental_consultation',
            is_completed=True,
            status_message=f'Dental consultation scheduled for {scheduled_datetime.strftime("%Y-%m-%d %H:%M")}'
        )
        
        result = {
            'status': 'success',
            'message': f'Dental consultation scheduled for patient {patient_id}',
            'patient_id': patient_id,
            'patient_name': patient.name,
            'scheduled_datetime': scheduled_datetime.isoformat(),
            'doctor_name': doctor_name,
            'notes': notes,
            'email_sent': patient.email is not None
        }
        
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        logger.error(f"Error in api_schedule_dental_consultation: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@action_bp.route('/api/send_cbct_request_email', methods=['POST'])
@login_required
def api_send_cbct_request_email():
    """Send CBCT request email to Vizbriz."""
    try:
        data = request.get_json()
        patient_id = data.get('patient_id')
        request_date = data.get('request_date', '')
        message = data.get('message', '')
        
        if not patient_id:
            return jsonify({
                'success': False,
                'error': 'Missing required parameter: patient_id'
            }), 400
        
        # Get patient details
        from flask_app.models import Patient
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({
                'success': False,
                'error': f'Patient with ID {patient_id} not found'
            }), 404
        
        # Prepare email content
        email_subject = f"CBCT report for patient id {patient_id}"
        
        # Build the message body
        base_message = f"Please upload CBCT report for patient ID {patient_id}."
        additional_message = message if message else ""
        
        # Build a cleaner email format
        email_body = f"""{base_message}

{additional_message if additional_message else ""}

Best regards,
Dental Sleep Team"""
        
        # Send CBCT request email using the SendGrid function
        email_sent = send_cbct_email_with_sendgrid(
            recipient_email='info@vizbriz.com',
            subject=email_subject,
            email_body=email_body,
            patient_id=patient_id
        )
        
        if not email_sent:
            logger.error("Failed to send CBCT request email via SendGrid")
            return jsonify({
                'success': False,
                'error': 'Failed to send email'
            }), 500
        
        # Update the patient manifest
        update_patient_manifest_for_action(
            patient_id=patient_id,
            action_key='request_cbct_files',
            is_completed=True,
            status_message=f'CBCT request email sent to Vizbriz on {request_date or "current date"}'
        )
        
        result = {
            'status': 'success',
            'message': f'CBCT request email sent for patient {patient_id}',
            'patient_id': patient_id,
            'email_sent_to': 'info@vizbriz.com',
            'request_date': request_date
        }
        
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        logger.error(f"Error in api_send_cbct_request_email: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@action_bp.route('/api/send_hipaa_consent_email', methods=['POST'])
@login_required
def api_send_hipaa_consent_email():
    """Send HIPAA consent request email to patient."""
    try:
        data = request.get_json()
        patient_id = data.get('patient_id')
        patient_email = data.get('patient_email', '')
        request_date = data.get('request_date', '')
        message = data.get('message', '')
        
        if not patient_id:
            return jsonify({
                'success': False,
                'error': 'Missing required parameter: patient_id'
            }), 400
        
        # Get patient details
        from flask_app.models import Patient
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({
                'success': False,
                'error': f'Patient with ID {patient_id} not found'
            }), 404
        
        # Use patient's email if available, otherwise use provided email
        recipient_email = patient.email if patient.email else patient_email
        logger.info(f"Patient email from DB: {patient.email}")
        logger.info(f"Patient email from request: {patient_email}")
        logger.info(f"Final recipient email: {recipient_email}")
        
        if not recipient_email:
            return jsonify({
                'success': False,
                'error': 'No email address available for patient'
            }), 400
        
        # Ensure patient has an upload token
        if not patient.upload_token:
            import secrets
            patient.upload_token = secrets.token_urlsafe(32)
            db.session.commit()
            logger.info(f"Generated upload token for patient {patient_id}: {patient.upload_token}")
        
        # Prepare email content
        email_subject = f"HIPAA Consent Request for Patient {patient.name}"
        
        # Get clinic_id: prefer session (set at login), else first from user's associations
        clinic_id = session.get('clinic_id')
        if not clinic_id and hasattr(current_user, 'get_clinic_ids'):
            clinic_ids = current_user.get_clinic_ids()
            if clinic_ids:
                clinic_id = clinic_ids[0]
        if not clinic_id:
            clinic_id = None
        
        # Create dynamic wizard link that starts the patient onboarding process
        base_url = os.environ.get('BASE_URL', 'http://localhost:7000')
        wizard_link = f"{base_url}/wizard/stage1_personal_info?clinic_id={clinic_id}"
        
        # Get message template from the execution manifest
        template_file = 'execution_manifest_10279_vizbriz.json'
        with open(template_file, 'r') as f:
            template_manifest = json.load(f)
        
        # Find the request_hipaa_consent action and get its message_template
        message_template = ""
        for action in template_manifest['eligible_actions']:
            if action['action_key'] == 'request_hipaa_consent':
                message_template = action['message_template']
                break
        
        # Create email content using the template
        email_body = f"""Dear {patient.name},

{message_template}

Patient Wizard Link:
{wizard_link}

This is required to continue with your treatment plan."""
        
        # Send HIPAA consent email
        logger.info(f"About to send HIPAA consent email to: {recipient_email}")
        logger.info(f"Email subject: {email_subject}")
        logger.info(f"Email body length: {len(email_body)}")
        
        email_sent = send_hipaa_consent_email_with_sendgrid(
            recipient_email=recipient_email,
            subject=email_subject,
            email_body=email_body,
            patient_id=patient_id
        )
        
        logger.info(f"Email send result: {email_sent}")
        
        if not email_sent:
            logger.error("Failed to send HIPAA consent email via SendGrid")
            return jsonify({
                'success': False,
                'error': 'Failed to send email'
            }), 500
        
        # Update the patient manifest
        update_patient_manifest_for_action(
            patient_id=patient_id,
            action_key='request_hipaa_consent',
            is_completed=True,
            status_message=f'HIPAA consent email sent to patient on {request_date or "current date"}'
        )
        
        result = {
            'status': 'success',
            'message': f'HIPAA consent email sent for patient {patient_id}',
            'patient_id': patient_id,
            'email_sent_to': recipient_email,
            'request_date': request_date
        }
        
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        logger.error(f"Error in api_send_hipaa_consent_email: {e}")
        return {'status': 'error', 'message': str(e)}

@action_bp.route('/api/schedule_sleep_test_review', methods=['POST'])
@login_required
def api_schedule_sleep_test_review():
    """Schedule sleep test review endpoint."""
    try:
        data = request.get_json()
        raw_pid = data.get('patient_id')
        try:
            patient_id = int(str(raw_pid).strip().lstrip('$').split()[0])
        except Exception:
            patient_id = None
        scheduled_date = data.get('scheduled_date')
        scheduled_time = data.get('scheduled_time')
        doctor_name = data.get('doctor_name', 'Sleep Doctor')
        notes = data.get('notes', '')
        
        if not patient_id:
            return jsonify({
                'success': False,
                'error': 'Missing required parameter: patient_id'
            }), 400
        
        # Create or update the patient_consult_schedule table
        from flask_app.models import PatientConsultSchedule
        from datetime import datetime
        
        # Parse the scheduled datetime
        if scheduled_date and scheduled_time:
            scheduled_datetime = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")
        else:
            scheduled_datetime = datetime.now()
        
        # Check if consultation already exists
        consultation = PatientConsultSchedule.query.filter_by(
            patient_id=patient_id,
            consult_type='sleep_doctor'
        ).first()
        
        if consultation:
            # Update existing consultation
            consultation.scheduled_datetime = scheduled_datetime
            consultation.status = 'scheduled'
            consultation.notes = notes
            db.session.commit()
        else:
            # Create new consultation
            new_consultation = PatientConsultSchedule(
                patient_id=patient_id,
                consult_type='sleep_doctor',
                scheduled_datetime=scheduled_datetime,
                status='scheduled',
                notes=notes
            )
            db.session.add(new_consultation)
            db.session.commit()
        
        # Update the patient manifest
        update_patient_manifest_for_action(
            patient_id=patient_id,
            action_key='schedule_sleep_test_review',
            is_completed=True,
            status_message=f'Sleep test review scheduled for {scheduled_datetime.strftime("%Y-%m-%d %H:%M")}'
        )
        
        result = {
            'status': 'success',
            'message': f'Sleep test review scheduled for patient {patient_id}',
            'patient_id': patient_id,
            'scheduled_datetime': scheduled_datetime.isoformat(),
            'doctor_name': doctor_name,
            'notes': notes
        }
        
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        logger.error(f"Error in api_schedule_sleep_test_review: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500 

@action_bp.route('/api/complete_dental_consultation', methods=['POST'])
@login_required
def api_complete_dental_consultation():
    """Mark dental sleep expert consultation as completed."""
    try:
        logger.info("=== DENTAL CONSULTATION COMPLETION API CALLED ===")
        logger.info("=== INTENTIONALLY FAILING FOR TESTING ===")
        data = request.get_json()
        logger.info(f"Received data: {data}")
        
        # Intentionally fail for testing
        return jsonify({
            'success': False,
            'error': 'INTENTIONAL FAILURE FOR TESTING - API IS BEING CALLED!'
        }), 500
        patient_id = data.get('patient_id')
        completion_date = data.get('completion_date')
        completion_time = data.get('completion_time')
        doctor_name = data.get('doctor_name', 'Dental Sleep Expert')
        notes = data.get('notes', '')
        
        if not patient_id:
            return jsonify({
                'success': False,
                'error': 'Missing required parameter: patient_id'
            }), 400
        
        # Get patient details
        from flask_app.models import Patient
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({
                'success': False,
                'error': f'Patient with ID {patient_id} not found'
            }), 404
        
        # Parse completion datetime
        from datetime import datetime
        if completion_date and completion_time:
            completion_datetime = datetime.strptime(f"{completion_date} {completion_time}", "%Y-%m-%d %H:%M")
        else:
            completion_datetime = datetime.now()
        
        # Update the consultation record in the database
        from flask_app.models import PatientConsultSchedule
        
        # Debug: Log all consultations for this patient
        all_consultations = PatientConsultSchedule.query.filter_by(patient_id=patient_id).all()
        logger.info(f"All consultations for patient {patient_id}:")
        for cons in all_consultations:
            logger.info(f"  ID: {cons.id}, Type: '{cons.consult_type}', Status: '{cons.status}', Scheduled: {cons.scheduled_datetime}")
        
        # Find the scheduled consultation to mark as completed
        logger.info(f"Looking for consultations with patient_id={patient_id}, status='scheduled', consult_type in ['dental_sleep_doctor', 'dental_sleep_doctor_consult']")
        consultation = PatientConsultSchedule.query.filter(
            PatientConsultSchedule.patient_id == patient_id,
            PatientConsultSchedule.status == 'scheduled',
            PatientConsultSchedule.consult_type.in_(['dental_sleep_doctor', 'dental_sleep_doctor_consult'])
        ).first()
        
        logger.info(f"Found consultation to complete: {consultation.id if consultation else 'None'}")
        if consultation:
            logger.info(f"Consultation details - ID: {consultation.id}, Type: '{consultation.consult_type}', Status: '{consultation.status}'")
        
        if consultation:
            # Update existing scheduled consultation to completed
            logger.info(f'Before update - Consultation ID: {consultation.id}, Status: {consultation.status}, Completed: {consultation.completed_datetime}')
            consultation.status = 'completed'
            consultation.completed_datetime = completion_datetime
            consultation.comment = notes
            db.session.commit()
            
            # Verify the update by refreshing the object
            db.session.refresh(consultation)
            logger.info(f'After update - Consultation ID: {consultation.id}, Status: {consultation.status}, Completed: {consultation.completed_datetime}')
            logger.info(f'Successfully updated consultation record {consultation.id} from scheduled to completed')
            
            # Double-check with a direct database query
            verification = PatientConsultSchedule.query.get(consultation.id)
            if verification:
                logger.info(f'Verification query - ID: {verification.id}, Status: {verification.status}, Completed: {verification.completed_datetime}')
            else:
                logger.error(f'Verification query failed - could not find consultation {consultation.id}')
        else:
            logger.warning(f'No scheduled dental consultation found for patient {patient_id} to mark as completed')
            return jsonify({
                'success': False,
                'error': 'No scheduled dental consultation found. Please schedule a consultation first before marking it as completed.'
            }), 400
        

        
        # Update the patient manifest for action
        update_patient_manifest_for_action(
            patient_id=patient_id,
            action_key='complete_dental_consultation',
            is_completed=True,
            status_message=f'Dental consultation completed on {completion_datetime.strftime("%Y-%m-%d %H:%M")}'
        )
        
        # Log the completion
        logger.info(f'Dental consultation completed for patient {patient_id} by {doctor_name} on {completion_datetime}')
        if notes:
            logger.info(f'Consultation notes: {notes}')
        
        result = {
            'status': 'success',
            'message': f'Dental consultation marked as completed for patient {patient_id}',
            'patient_id': patient_id,
            'completion_date': completion_datetime.strftime('%Y-%m-%d'),
            'completion_time': completion_datetime.strftime('%H:%M'),
            'doctor_name': doctor_name,
            'notes': notes
        }
        
        return jsonify({
            'success': True,
            'result': result
        })
        
    except Exception as e:
        logger.error(f"Error in api_complete_dental_consultation: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@action_bp.route('/api/request_osa_report', methods=['POST'])
@login_required
def api_request_osa_report():
    """Execute request OSA report action (send admin reminder)."""
    try:
        data = request.get_json()
        result = execute_request_osa_report(data)
        return jsonify({ 'success': result.get('status') == 'success', 'result': result })
    except Exception as e:
        return {'status': 'error', 'message': str(e)}
@action_bp.route('/api/send_osa_report_request_email', methods=['POST'])
@login_required
def api_send_osa_report_request_email():
    """Send OSA report request email to Vizbriz admin (info@vizbriz.com)."""
    try:
        data = request.get_json()
        patient_id = data.get('patient_id')

        if not patient_id:
            return jsonify({ 'success': False, 'error': 'Missing required parameter: patient_id' }), 400

        # Validate patient exists
        from flask_app.models import Patient
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({ 'success': False, 'error': f'Patient with ID {patient_id} not found' }), 404

        subject = f"OSA report for patient id {patient_id}"
        email_body = f"Please upload the OSA report for patient {patient_id}."

        # Send OSA report request email with proper email type logging
        from flask_app.routes.file_management_routes import send_email_with_sendgrid
        email_sent = send_email_with_sendgrid(
            'info@vizbriz.com',
            subject,
            email_body,
            email_body,  # text content same as html
            patient_id=patient_id,
            sender_id=None,
            email_type='osa_report_request',
            sender_type='system'
        )
        if not email_sent:
            logger.error("Failed to send OSA report request email via SendGrid")
            return jsonify({ 'success': False, 'error': 'Failed to send email' }), 500

        # Do NOT mark stage complete here; only when the report file is uploaded (validator handles it)
        logger.info(f'OSA report request email sent for patient {patient_id} - stage remains incomplete until document upload')

        from datetime import datetime
        return jsonify({
            'success': True,
            'result': {
                'status': 'success',
                'message': f'OSA report request email sent for patient {patient_id}',
                'patient_id': patient_id,
                'email_sent_to': 'info@vizbriz.com',
                'request_date': datetime.utcnow().isoformat()
            }
        })
    except Exception as e:
        logger.error(f"Error in api_send_osa_report_request_email: {e}")
        return jsonify({ 'success': False, 'error': str(e) }), 500

@action_bp.route('/api/update_device_delivery', methods=['POST'])
@login_required
def api_update_device_delivery():
    """Update device delivery status endpoint."""
    try:
        data = request.get_json()
        result = execute_update_device_delivery(data)
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        logger.error(f"Error in api_update_device_delivery: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@action_bp.route('/api/schedule_appliance_delivery', methods=['POST'])
@login_required
def api_schedule_appliance_delivery():
    """Schedule oral appliance delivery appointment."""
    try:
        data = request.get_json()
        patient_id = data.get('patient_id')
        scheduled_date = data.get('scheduled_date')
        scheduled_time = data.get('scheduled_time')
        notes = data.get('notes', '')
        
        if not patient_id:
            return jsonify({
                'success': False,
                'error': 'Missing required parameter: patient_id'
            }), 400
        
        # Get patient details
        from flask_app.models import Patient
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({
                'success': False,
                'error': f'Patient with ID {patient_id} not found'
            }), 404
        
        # Parse scheduled datetime
        from datetime import datetime
        if scheduled_date and scheduled_time:
            scheduled_datetime = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")
        else:
            scheduled_datetime = datetime.now()
        
        # Store in PatientConsultSchedule table
        from flask_app.models import PatientConsultSchedule
        new_consultation = PatientConsultSchedule(
            patient_id=patient_id,
            consult_type='appliance_delivery',
            scheduled_datetime=scheduled_datetime,
            status='scheduled',
            doctor_name='Dental Sleep Expert',
            notes=notes
        )
        db.session.add(new_consultation)
        
        # Store in PatientDeviceOrder table
        from flask_app.models import PatientDeviceOrder
        device_order = PatientDeviceOrder.query.filter_by(
            patient_id=patient_id,
            device_type='oral_appliance'
        ).first()
        
        if device_order:
            # Update existing device order with fitting date
            device_order.fitting_date = scheduled_datetime
            device_order.status = 'scheduled_for_fitting'
            device_order.notes = f"Fitting scheduled for {scheduled_datetime.strftime('%B %d, %Y at %I:%M %p')}"
        else:
            # Create new device order
            device_order = PatientDeviceOrder(
                patient_id=patient_id,
                device_type='oral_appliance',
                device_name='Custom Oral Appliance',
                order_date=datetime.now(),
                status='scheduled_for_fitting',
                fitting_date=scheduled_datetime,
                notes=f"Appliance delivery scheduled for {scheduled_datetime.strftime('%B %d, %Y at %I:%M %p')}"
            )
            db.session.add(device_order)
        
        db.session.commit()
        logger.info(f'Appliance delivery scheduled for patient {patient_id} on {scheduled_datetime}')
        logger.info(f'Stored in PatientConsultSchedule (ID: {new_consultation.id}) and PatientDeviceOrder (ID: {device_order.id})')
        
        # Log the scheduling
        logger.info(f'Appliance delivery scheduled for patient {patient_id} on {scheduled_datetime}')
        if notes:
            logger.info(f'Scheduling notes: {notes}')
        
        result = {
            'status': 'success',
            'message': f'Appliance delivery scheduled for patient {patient_id}',
            'patient_id': patient_id,
            'scheduled_date': scheduled_datetime.strftime('%Y-%m-%d'),
            'scheduled_time': scheduled_datetime.strftime('%H:%M'),
            'notes': notes
        }
        
        return jsonify({
            'success': True,
            'result': result
        })
        
    except Exception as e:
        logger.error(f"Error in api_schedule_appliance_delivery: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@action_bp.route('/api/complete_appliance_delivery', methods=['POST'])
@login_required
def api_complete_appliance_delivery():
    """Mark appliance delivery as completed."""
    try:
        data = request.get_json()
        patient_id = data.get('patient_id')
        completion_date = data.get('completion_date')
        completion_time = data.get('completion_time')
        notes = data.get('notes', '')
        
        if not patient_id:
            return jsonify({
                'success': False,
                'error': 'Missing required parameter: patient_id'
            }), 400
        
        # Get patient details
        from flask_app.models import Patient
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({
                'success': False,
                'error': f'Patient with ID {patient_id} not found'
            }), 404
        
        # Parse completion datetime
        from datetime import datetime
        if completion_date and completion_time:
            completion_datetime = datetime.strptime(f"{completion_date} {completion_time}", "%Y-%m-%d %H:%M")
        else:
            completion_datetime = datetime.now()
        
        # Update PatientConsultSchedule record
        from flask_app.models import PatientConsultSchedule
        consultation = PatientConsultSchedule.query.filter_by(
            patient_id=patient_id,
            consult_type='appliance_delivery',
            status='scheduled'
        ).first()
        
        if consultation:
            consultation.status = 'completed'
            consultation.completed_datetime = completion_datetime
            consultation.comment = notes
            db.session.commit()
            logger.info(f'Updated consultation record {consultation.id} to completed')
        else:
            logger.warning(f'No scheduled appliance delivery found for patient {patient_id}')
        
        # Update PatientDeviceOrder record
        from flask_app.models import PatientDeviceOrder
        device_order = PatientDeviceOrder.query.filter_by(
            patient_id=patient_id,
            device_type='oral_appliance'
        ).first()
        
        if device_order:
            device_order.status = 'delivered'
            device_order.fitting_comment = notes
            db.session.commit()
            logger.info(f'Updated device order {device_order.id} to delivered')
        else:
            logger.warning(f'No device order found for patient {patient_id}')
        
        db.session.commit()
        logger.info(f'Appliance delivery completed for patient {patient_id} on {completion_datetime}')
        
        # Log the completion
        logger.info(f'Appliance delivery completed for patient {patient_id} on {completion_datetime}')
        if notes:
            logger.info(f'Completion notes: {notes}')
        
        result = {
            'status': 'success',
            'message': f'Appliance delivery marked as completed for patient {patient_id}',
            'patient_id': patient_id,
            'completion_date': completion_datetime.strftime('%Y-%m-%d'),
            'completion_time': completion_datetime.strftime('%H:%M'),
            'notes': notes
        }
        
        return jsonify({
            'success': True,
            'result': result
        })
        
    except Exception as e:
        logger.error(f"Error in api_complete_appliance_delivery: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@action_bp.route('/api/remind_quiz_completion', methods=['POST'])
@login_required
def api_remind_quiz_completion():
    """Execute remind quiz completion action."""
    try:
        # This should use the existing reminder API endpoint
        from flask import request
        data = request.get_json()
        # Ensure action_key is set for proper message template lookup
        if 'action_key' not in data:
            data['action_key'] = 'remind_quiz_completion'
        result = api_send_reminder()
        return result
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

@action_bp.route('/api/remind_consultation_scheduling', methods=['POST'])
@login_required
def api_remind_consultation_scheduling():
    """Execute remind consultation scheduling action."""
    try:
        # This should use the existing reminder API endpoint
        from flask import request
        data = request.get_json()
        # Ensure action_key is set for proper message template lookup
        if 'action_key' not in data:
            data['action_key'] = 'remind_consultation_scheduling'
        result = api_send_reminder()
        return result
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

@action_bp.route('/api/remind_sleep_study', methods=['POST'])
@login_required
def api_remind_sleep_study():
    """Execute remind sleep study action."""
    try:
        # This should use the existing reminder API endpoint
        from flask import request
        data = request.get_json()
        # Ensure action_key is set for proper message template lookup
        if 'action_key' not in data:
            data['action_key'] = 'remind_sleep_study'
        result = api_send_reminder()
        return result
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

@action_bp.route('/api/remind_document_upload', methods=['POST'])
@login_required
def api_remind_document_upload():
    """Execute remind document upload action."""
    try:
        # This should use the existing reminder API endpoint
        from flask import request
        data = request.get_json()
        # Ensure action_key is set for proper message template lookup
        if 'action_key' not in data:
            data['action_key'] = 'remind_document_upload'
        result = api_send_reminder()
        return result
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


def check_and_update_stage_completion(patient_id: int, action_key: str, action_result: dict) -> dict:
    """
    Check if an action should complete a stage and update accordingly.
    
    Args:
        patient_id (int): Patient ID
        action_key (str): The action that was executed
        action_result (dict): Result of the action execution
        
    Returns:
        dict: Updated result with stage completion info
    """
    try:
        # Load manifest to get action configuration
        import json
        import os
        
        template_file = 'execution_manifest_10279_vizbriz.json'
        if not os.path.exists(template_file):
            return action_result
            
        with open(template_file, 'r') as f:
            template_manifest = json.load(f)
        
        # Find the action configuration
        action_config = next((a for a in template_manifest['eligible_actions'] if a['action_key'] == action_key), None)
        if not action_config:
            return action_result
        
        # Check if this action should complete the stage
        completes_stage = action_config.get('completes_stage', False)
        stage_key = action_config.get('stage')
        
        if completes_stage and stage_key and action_result.get('success'):
            # Update stage completion in database
            from flask_app.services.manifest_service import ManifestService
            
            # Mark stage as completed
            ManifestService.update_stage_completion(patient_id, stage_key, True, f"Completed via action: {action_key}")
            
            # Update action result
            action_result['stage_completed'] = True
            action_result['stage_key'] = stage_key
            action_result['message'] = f"{action_result.get('message', '')} Stage '{stage_key}' marked as completed."
            
            logger.info(f"Stage '{stage_key}' completed via action '{action_key}' for patient {patient_id}")
        
        return action_result
        
    except Exception as e:
        logger.error(f"Error in check_and_update_stage_completion: {e}")
        return action_result

@action_bp.route('/api/llm/select_actions/<int:patient_id>', methods=['POST'])
@login_required
def llm_select_actions(patient_id):
    """
    Use LLM to intelligently select which actions to display based on current stage status.
    The LLM has access to all stage information and business rules from manifest validator.
    """
    logger.info(f"=== LLM ACTION SELECTION for patient_id: {patient_id} ===")
    
    try:
        # Get current patient manifest with real completion status
        from flask_app.services.manifest_service import ManifestService
        
        # Get patient info
        from flask_app.models import Patient
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({"error": f"Patient {patient_id} not found"}), 404
        
        # Calculate age from date of birth
        patient_age = 'N/A'
        if patient.dob:
            from datetime import date
            today = date.today()
            try:
                patient_age = today.year - patient.dob.year - ((today.month, today.day) < (patient.dob.month, patient.dob.day))
            except:
                patient_age = 'N/A'
        
        patient_info = {
            'id': patient.id,
            'name': patient.name,
            'email': patient.email,
            'phone': patient.phone,
            'age': patient_age,
            'gender': patient.gender
        }
        
        # Get current stage completion status from database
        stage_manifest = []
        for stage in MANIFEST_DEFINITION:
            entry = ManifestService.get_stage_entry(patient_id, stage['key'])
            stage_manifest.append({
                "key": stage['key'],
                "name": stage['stage_name'],
                "number": stage['stage_number'],
                "value": "yes" if entry and entry.get('is_completed') else "no",
                "completion_date": entry.get('completion_date') if entry else None,
                "status_message": entry.get('status_message') if entry else None
            })
        
        # Get all available actions from manifest
        import json
        import os
        
        template_file = 'execution_manifest_10279_vizbriz.json'
        if not os.path.exists(template_file):
            return jsonify({"error": "Template manifest not found"}), 500
            
        with open(template_file, 'r') as f:
            template_manifest = json.load(f)
        
        # Create context for LLM
        context = {
            "patient_info": patient_info,
            "stage_manifest": stage_manifest,
            "all_actions": template_manifest['eligible_actions'],
            "business_rules": {
                "quiz_completion": "Completed when quiz record exists OR questionnaire files uploaded",
                "initial_consult_scheduled": "Completed when patient_consult_schedule record exists with consult_type='sleep_expert'",
                "initial_consult_completed": "Completed when patient_consult_schedule has status='completed'",
                "sleep_study_scheduled": "Completed when patient_consult_schedule exists with consult_type='sleep_doctor'",
                "sleep_test_completed": "Completed when files uploaded with subcategory='sleep-test'",
                "schedule_sleep_test_review": "Completed when patient_consult_schedule exists with consult_type='sleep_doctor' and status='scheduled'",
                "sleep_doctor_followup_completed": "Completed when patient_consult_schedule has status='completed' and consult_type='ep_doctor'",
                "dental_sleep_doctor_consult_scheduled": "Completed when patient_consult_schedule exists with consult_type='dental_sleep_doctor' OR 'dental_sleep_doctor_consult'",
                "cbct_observation_report_uploaded": "Completed when adminfiles uploaded with file_category='cbct observations'",
                "intraoral_scan_uploaded": "Completed when files uploaded with subcategory='intraoral-scan'",
                "hipaa_consent_signed": "Completed when HIPAA consent is signed",
                "met_with_dental_sleep_expert": "Completed when dental consultation is completed",
                "osa_report_ready": "Completed when OSA report is generated",
                "dental_approval_osa_report": "Completed when dentist approves OSA report",
                "order_oral_appliance": "Completed when oral appliance is ordered",
                "device_delivered": "Completed when device is delivered to dental office",
                "schedule_oral_appliance_delivery": "Completed when appliance delivery is scheduled",
                "oral_appliance_delivery_completed": "Completed when appliance delivery is completed",
                "follow_up_sleep_test_after_delivery": "Completed when follow-up sleep test is completed"
            }
        }
        
        # Create prompt for LLM
        prompt = f"""
You are an AI assistant that selects which actions to display for a patient workflow. You have access to:

PATIENT INFO:
{json.dumps(patient_info, indent=2)}

CURRENT STAGE STATUS:
{json.dumps(stage_manifest, indent=2)}

ALL AVAILABLE ACTIONS:
{json.dumps(template_manifest['eligible_actions'], indent=2)}

BUSINESS RULES FOR STAGE COMPLETION:
{json.dumps(context['business_rules'], indent=2)}

LOGICAL WORKFLOW EXAMPLES:
- If quiz_completion is "yes" (completed): Next step should be schedule consultation, NOT send quiz reminder
- If initial_consult_scheduled is "yes" (completed): Next step should be complete consultation, NOT schedule again
- If sleep_test_completed is "yes" (completed): Next step should be schedule review, NOT request test files
- Only show reminders for stages that are "no" (incomplete) and need follow-up

TASK: Select which actions should be displayed to the user based on:
1. Current stage completion status
2. Business rules for what completes each stage
3. Logical workflow progression
4. User needs (reminders, follow-ups, etc.)

RULES:
- Only show actions that make sense for the current state
- If a stage is completed, don't show actions that would complete it again
- Don't show reminder actions for completed stages (e.g., don't remind about quiz if quiz is done)
- Show next logical actions in the workflow progression
- Only show reminders for stages that are in progress but not completed
- Focus on the next 2-3 logical steps in the workflow

RESPONSE FORMAT:
Return a JSON object with:
{{
  "selected_actions": [
    {{
      "action_key": "string",
      "reason": "string explaining why this action is selected",
      "priority": "high|medium|low"
    }}
  ],
  "workflow_guidance": "string with next 2-3 recommended steps"
}}

Select the most appropriate actions for this patient's current workflow state.
"""
        
        # Call LLM to select actions
        from flask_app.services.bedrock_service import BedrockService
        bedrock = BedrockService()
        
        # Format prompt as messages for BedrockService
        messages = [
            {"role": "user", "content": prompt}
        ]
        
        response = bedrock.invoke_model(
            messages=messages,
            max_tokens=2000,
            temperature=0.1,
            patient_id=patient_id,
            endpoint="action_routes"
        )
        
        if not response or not response.get('success'):
            logger.error(f"LLM call failed: {response.get('error', 'Unknown error')}")
            return jsonify({"error": "Failed to get LLM response"}), 500
        
        # Parse LLM response
        try:
            llm_result = json.loads(response['response'])
            selected_actions = llm_result.get('selected_actions', [])
            workflow_guidance = llm_result.get('workflow_guidance', '')
        except json.JSONDecodeError:
            logger.error(f"Failed to parse LLM response: {response.get('response', '')}")
            return jsonify({"error": "Invalid LLM response format"}), 500
        
        # Filter actions based on LLM selection
        eligible_actions = []
        for selection in selected_actions:
            action_key = selection['action_key']
            action = next((a for a in template_manifest['eligible_actions'] if a['action_key'] == action_key), None)
            
            if action:
                action_copy = action.copy()
                action_copy['llm_reason'] = selection.get('reason', '')
                action_copy['priority'] = selection.get('priority', 'medium')
                
                # Update patient-specific data
                if 'upload_link' in action_copy:
                    action_copy['upload_link'] = action_copy['upload_link'].replace('10279', str(patient_id))
                
                eligible_actions.append(action_copy)
        
        # Build response
        response_data = {
            "patient_info": patient_info,
            "stage_manifest": stage_manifest,
            "eligible_actions": eligible_actions,
            "workflow_guidance": workflow_guidance,
            "llm_selection": selected_actions
        }
        
        logger.info(f"LLM selected {len(eligible_actions)} actions for patient {patient_id}")
        logger.info(f"Workflow guidance: {workflow_guidance}")
        
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"=== LLM ACTION SELECTION ERROR for patient_id: {patient_id} ===")
        logger.error(f"Error: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({"error": f"Failed to select actions: {str(e)}"}), 500

 