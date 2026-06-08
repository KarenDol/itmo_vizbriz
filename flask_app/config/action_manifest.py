"""
Action Manifest Configuration
Maps available actions to API endpoints and parameters for dynamic LLM decision making.
Based on manifest_validation_complete_20260728.py validation rules.

MANIFEST_VERSION: 2025-09-15-QUIZ-EMAIL-DEBUG-v1.2.3
LAST_UPDATED: Updated send_quiz_link parameters to patient_email + message
"""

import json
import boto3
import os
from datetime import datetime

# Manifest version for debugging
MANIFEST_VERSION = "2025-09-15-QUIZ-EMAIL-DEBUG-v1.2.3"
SEND_QUIZ_PARAMS_EXPECTED = ["patient_id", "patient_email", "message"]

def get_manifest_version():
    """Get the current manifest version for debugging"""
    return MANIFEST_VERSION

ACTION_MANIFEST = {
    # Stage 1: Quiz Completion
    # Validation: Check conversion_quiz table OR files with subcategory='questionnaire'  
    "send_quiz_link": {
        "description": "Send patient a dynamic link to complete the basic quiz",
        "method": "POST",
        "endpoint": "/api/send_quiz",
        "parameters": [
            "patient_id",
            "patient_email",
            "message"
        ],
        "stages": ["quiz_completion"],
        "category": "assessment",
        "patient_journey_stage": "onboarding",
        "validation_rule": "Check conversion_quiz table OR files with subcategory='questionnaire'",
        "validation_query": """
            SELECT cq.id, cq.created_at, cq.quiz_type, cq.patient_email
            FROM patients p
            LEFT JOIN conversion_quiz cq ON p.id = cq.user_id
            WHERE p.id = %s AND cq.quiz_type = 'basic_quiz'
        """,
        "default_message": "Please complete your sleep questionnaire using the link below. This will help us understand your sleep patterns and provide personalized treatment recommendations.",
        "ai_guidance": "Use this action when the patient has not completed their initial assessment quiz. The system will generate a unique quiz link and QR code, then send it via email/SMS to the patient. This is the first step in the patient journey and must be completed before proceeding to consultation scheduling.",
        "ui_enhancement": {
            "purpose": "Complete initial patient assessment to determine sleep apnea risk",
            "trigger": "Patient has not completed basic sleep questionnaire",
            "outcome": "Patient receives personalized quiz link and QR code for assessment",
            "icon": "quiz",
            "button_text": "Send Quiz Link",
            "priority": "high"
        }
    },
    
    "mark_quiz_completed": {
        "description": "Mark quiz as completed (manual override)",
        "method": "POST",
        "endpoint": "/api/mark_quiz_completed",
        "parameters": [
            "patient_id",
            "completion_date",
            "notes"
        ],
        "stages": ["quiz_completion"],
        "category": "completion",
        "patient_journey_stage": "onboarding",
        "validation_rule": "Manually mark quiz completion stage as completed",
        "ai_guidance": "Use this action to manually mark the quiz as completed when the patient has completed it but the system hasn't detected it automatically.",
        "ui_enhancement": {
            "purpose": "Manually override quiz completion status",
            "trigger": "Patient completed quiz outside system or automatic detection failed",
            "outcome": "Quiz completion stage marked as complete, workflow advances",
            "icon": "check_circle",
            "button_text": "Mark Quiz Completed",
            "priority": "medium"
        }
    },
    
    # Stage 2: Initial Consult Scheduled
    # Validation: Check patient_consult_schedule with consult_type='sleep_expert'
    "schedule_consultation": {
        "description": "Schedule initial consultation with sleep expert",
        "method": "POST",
        "endpoint": "/api/schedule_consultation",
        "parameters": [
            "patient_id",
            "scheduled_date",
            "scheduled_time",
            "doctor_name",
            "notes"
        ],
        "stages": ["initial_consult_scheduled"],
        "category": "scheduling",
        "patient_journey_stage": "onboarding",
        "validation_rule": "Check patient_consult_schedule with consult_type='sleep_expert'",
        "validation_query": """
            SELECT pcs.id, pcs.scheduled_datetime, pcs.notes
            FROM patients p
            LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
            WHERE p.id = %s AND LOWER(pcs.consult_type) = LOWER('sleep_expert')
        """,
        "ai_guidance": "Use this action when the patient has completed their quiz and is ready for their first consultation. Schedule a consultation with a sleep expert to discuss their sleep concerns and determine if a sleep study is needed. This is the second step in the patient journey.",
        "ui_enhancement": {
            "purpose": "Schedule initial consultation to evaluate sleep concerns and determine need for sleep study",
            "trigger": "Patient has completed quiz and is ready for first consultation",
            "outcome": "Patient receives scheduled appointment with sleep expert",
            "icon": "schedule",
            "button_text": "Schedule Consultation",
            "priority": "high"
        }
    },
    
    # Stage 3: Met with Sleep Expert
    # Validation: Check patient_consult_schedule with consult_type='sleep_expert' AND status='completed'
    "complete_consultation": {
        "description": "Mark initial consultation as completed",
        "method": "POST",
        "endpoint": "/api/complete_consultation",
        "parameters": [
            "patient_id",
            "completion_date",
            "completion_time",
            "doctor_name",
            "notes"
        ],
        "stages": ["initial_consult_completed"],
        "category": "scheduling",
        "patient_journey_stage": "onboarding",
        "validation_rule": "Check patient_consult_schedule with consult_type='sleep_expert' AND status='completed'",
        "validation_query": """
            SELECT pcs.id, pcs.completed_datetime, pcs.comment
            FROM patients p
            LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
            WHERE p.id = %s AND LOWER(pcs.consult_type) = LOWER('sleep_expert') AND LOWER(pcs.status) = LOWER('completed')
        """,
        "ai_guidance": "Use this action when the initial consultation with the sleep expert has been completed. This marks the consultation as finished and allows the patient to proceed to the next stage in their treatment journey."
    },
    
    # Stage 4: Sleep Study Scheduled
    # Validation: Check patient_consult_schedule with consult_type='sleep_doctor'
    "schedule_sleep_study": {
        "description": "Schedule sleep study with sleep doctor",
        "method": "POST",
        "endpoint": "/api/schedule_sleep_study",
        "parameters": [
            "patient_id",
            "scheduled_date",
            "scheduled_time",
            "facility_name",
            "notes"
        ],
        "stages": ["sleep_study_scheduled"],
        "category": "scheduling",
        "patient_journey_stage": "data_collection",
        "validation_rule": "Check patient_consult_schedule with consult_type='sleep_doctor'",
        "validation_query": """
            SELECT pcs.id, pcs.scheduled_datetime, pcs.notes
            FROM patients p
            LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
            WHERE p.id = %s AND LOWER(pcs.consult_type) = LOWER('sleep_doctor')
        """,
        "ai_guidance": "Use this action when the sleep expert has determined that a sleep study is needed to diagnose the patient's sleep apnea. Schedule the sleep study with a sleep doctor to gather diagnostic data."
    },
    
    # Stage 5: Sleep Test Completed (File Upload)
    # Validation: Check files table with subcategory='sleep-test'
    "request_sleep_test_files": {
        "description": "Upload required sleep test files",
        "method": "GET",
        "endpoint": "/patient_details/{patient_id}",
        "parameters": [
            "patient_id"
        ],
        "stages": ["sleep_test_completed"],
        "category": "file_upload",
        "patient_journey_stage": "data_collection",
        "validation_rule": "Check files table with subcategory='sleep-test'",
        "validation_query": """
            SELECT f.id, f.name, f.upload_date, f.file_type
            FROM files f
            WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('sleep-test')
        """,
        "ai_guidance": "Use this action when the patient has completed their sleep study and needs to upload the test results. This will redirect to the patient details page where files can be uploaded."
    },
    
    # Stage 6: Schedule Sleep Test Review
    # Validation: Check patient_consult_schedule with consult_type='sleep_doctor' AND status='scheduled'
    "schedule_sleep_test_review": {
        "description": "Schedule sleep test review with sleep doctor",
        "method": "POST",
        "endpoint": "/api/schedule_sleep_test_review",
        "parameters": [
            "patient_id",
            "scheduled_date",
            "scheduled_time",
            "doctor_name",
            "notes"
        ],
        "stages": ["schedule_sleep_test_review"],
        "category": "scheduling",
        "patient_journey_stage": "data_collection",
        "validation_rule": "Check patient_consult_schedule with consult_type='sleep_doctor' AND status='scheduled'",
        "validation_query": """
            SELECT pcs.id, pcs.scheduled_datetime, pcs.notes
            FROM patients p
            LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
            WHERE p.id = %s AND LOWER(pcs.consult_type) = LOWER('sleep_doctor') AND LOWER(pcs.status) = LOWER('scheduled')
        """,
        "ai_guidance": "Use this action when sleep test files have been uploaded and the sleep doctor needs to review the results. Schedule a consultation to discuss the sleep study findings and determine the next steps."
    },
    
    # Stage 7: Sleep Doctor Followup Completed
    # Validation: Check patient_consult_schedule with consult_type='ep_doctor' AND status='completed'
    "complete_sleep_doctor_followup": {
        "description": "Mark sleep doctor followup as completed",
        "method": "POST",
        "endpoint": "/api/complete_sleep_doctor_followup",
        "parameters": [
            "patient_id",
            "completion_date",
            "completion_time",
            "doctor_name",
            "notes"
        ],
        "stages": ["sleep_doctor_followup_completed"],
        "category": "scheduling",
        "patient_journey_stage": "data_collection",
        "validation_rule": "Check patient_consult_schedule with consult_type='ep_doctor' AND status='completed'",
        "validation_query": """
            SELECT pcs.id, pcs.completed_datetime, pcs.comment
            FROM patients p
            LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
            WHERE p.id = %s AND LOWER(pcs.consult_type) = LOWER('ep_doctor') AND LOWER(pcs.status) = LOWER('completed')
        """,
        "ai_guidance": "Use this action when the sleep doctor has completed their followup consultation and reviewed the sleep study results. This marks the completion of the sleep medicine evaluation phase."
    },
    
    # Stage 8: Dental Sleep Doctor Consult Scheduled
    # Validation: Check patient_consult_schedule with consult_type='dental_sleep_doctor'
    "schedule_dental_consultation": {
        "description": "Schedule consultation with dental sleep specialist",
        "method": "POST",
        "endpoint": "/api/schedule_dental_consultation",
        "parameters": [
            "patient_id",
            "scheduled_date",
            "scheduled_time",
            "doctor_name",
            "notes"
        ],
        "stages": ["dental_sleep_doctor_consult_scheduled"],
        "category": "scheduling",
        "patient_journey_stage": "planning",
        "validation_rule": "Check patient_consult_schedule with consult_type='dental_sleep_doctor'",
        "validation_query": """
            SELECT pcs.id, pcs.scheduled_datetime, pcs.notes
            FROM patients p
            LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
            WHERE p.id = %s AND LOWER(pcs.consult_type) = LOWER('dental_sleep_doctor')
        """,
        "ai_guidance": "Use this action when the sleep study has been completed and the patient is ready for dental sleep medicine consultation. Schedule with a dental sleep specialist to discuss oral appliance treatment options."
    },
    
    # Stage 9: CBCT Observation Report Uploaded (File Upload)
    # Validation: Check adminfiles table with file_category='cbct observations'
    "request_cbct_files": {
        "description": "Request CBCT observation report from Vizbriz (includes patient ID)",
        "method": "POST",
        "endpoint": "/api/send_cbct_request_email",
        "parameters": [
            "patient_id",
            "request_date",
            "message"
        ],
        "default_message": "Please upload required report for patient ID {patient_id}.",
        "stages": ["cbct_observation_report_uploaded"],
        "category": "communication",
        "patient_journey_stage": "planning",
        "validation_rule": "Check adminfiles table with file_category='cbct observations'",
        "validation_query": """
            SELECT af.id, af.name, af.upload_date, af.file_type
            FROM adminfiles af
            WHERE af.patient_id = %s AND LOWER(af.file_category) = LOWER('cbct observations')
        """,
        "ai_guidance": "Use this action when the patient needs CBCT (Cone Beam Computed Tomography) observation reports. This will send an email to info@vizbridge.com requesting Vizbriz to upload the relevant CBCT report for the patient. The email automatically includes the patient ID."
    },
    
    # Stage 10: Intraoral Scan Uploaded (File Upload)
    # Validation: Check files table with subcategory='intraoral-scan'
    "request_intraoral_scan": {
        "description": "Upload required intraoral scan files",
        "method": "GET",
        "endpoint": "/patient_details/{patient_id}",
        "parameters": [
            "patient_id"
        ],
        "stages": ["intraoral_scan_uploaded"],
        "category": "file_upload",
        "patient_journey_stage": "planning",
        "validation_rule": "Check files table with subcategory='intraoral-scan'",
        "validation_query": """
            SELECT f.id, f.name, f.upload_date, f.file_type
            FROM files f
            WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('intraoral-scan')
        """,
        "ai_guidance": "Use this action when the patient needs to provide intraoral scan files. This will redirect to the patient details page where files can be uploaded."
    },
    
    # Stage 11: HIPAA Consent Signed (File Upload)
    # Validation: Check files table with subcategory='billing' AND name contains 'hipaa' or 'consent'
    "request_hipaa_consent": {
        "description": "Send email to patient requesting HIPAA consent forms with upload link",
        "method": "POST",
        "endpoint": "/api/send_hipaa_consent_email",
        "parameters": [
            "patient_id",
            "patient_email",
            "request_date",
            "message"
        ],
        "stages": ["hipaa_consent_signed"],
        "category": "email_request",
        "patient_journey_stage": "planning",
        "validation_rule": "Check files table with subcategory='billing' AND name contains 'hipaa' or 'consent'",
        "validation_query": """
            SELECT f.id, f.name, f.upload_date, f.file_type
            FROM files f
            WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('billing') AND (
                LOWER(f.name) LIKE '%hipaa%' OR 
                LOWER(f.name) LIKE '%consent%' OR
                LOWER(f.name) LIKE '%authorization%'
            )
        """,
        "ai_guidance": "PRIORITY ACTION: Use this action FIRST when the patient needs to provide HIPAA consent forms for the first time. This sends the initial email with upload link. Only use 'remind_document_upload' if the patient has already been sent the initial request but hasn't uploaded yet.",
        "default_message": "You have been requested by the clinic to complete the patient onboarding process. Please use the following link to complete the process before your next appointment."
    },
    
    # Stage 12: Patient Completes Consult with Dental Sleep Expert
    # Validation: Check patient_consult_schedule with consult_type='dental_sleep_doctor' AND status='completed'
    "complete_dental_consultation": {
        "description": "Mark dental sleep expert consultation as completed",
        "method": "POST",
        "endpoint": "/api/complete_dental_consultation",
        "parameters": [
            "patient_id",
            "completion_date",
            "completion_time",
            "doctor_name",
            "notes"
        ],
        "stages": ["met_with_dental_sleep_expert"],
        "category": "scheduling",
        "patient_journey_stage": "planning",
        "validation_rule": "Check patient_consult_schedule with consult_type IN ('dental_sleep_doctor', 'dental_sleep_doctor_consult') AND status='completed' (requires prior scheduling)",
        "validation_query": """
            SELECT pcs.id, pcs.completed_datetime, pcs.comment
            FROM patient_consult_schedule pcs
            WHERE pcs.patient_id = %s AND LOWER(pcs.consult_type) IN (LOWER('dental_sleep_doctor'), LOWER('dental_sleep_doctor_consult')) AND LOWER(pcs.status) = LOWER('completed')
        """,
        "ai_guidance": "Use this action when a previously scheduled dental sleep expert consultation has been completed. This action updates an existing scheduled consultation to completed status. IMPORTANT: A consultation must be scheduled first using the 'schedule_dental_consultation' action before this completion action can be used. This marks the completion of the dental evaluation and allows the patient to proceed with oral appliance treatment."
    },
    
    # Stage 13: Patient OSA Report Ready (Admin Reminder)
    # Validation: Check adminfiles table with file_category='patient report' AND is_public=1
    "request_osa_report": {
        "description": "Request OSA report from Vizbriz (includes patient ID)",
        "method": "POST",
        "endpoint": "/api/send_osa_report_request_email",
        "parameters": [
            "patient_id",
            "request_date",
            "message"
        ],
        "stages": ["osa_report_ready"],
        "category": "communication",
        "patient_journey_stage": "planning",
        "validation_rule": "Check adminfiles table with file_category='patient report' AND is_public=1",
        "validation_query": """
            SELECT af.id, af.name, af.upload_date, af.file_type
            FROM adminfiles af
            WHERE af.patient_id = %s AND LOWER(af.file_category) LIKE LOWER('%patient report%') AND af.is_public = 1
        """,
        "ai_guidance": "Send an admin reminder to info@vizbriz.com to upload the patient's OSA report. This action does not complete the stage; the stage is completed only after the OSA report file is uploaded and marked public."
    },
    
    # Stage 14: Dental Approval for OSA Report
    # Validation: Check dentist_report_approval table with report_id and approval_status='approved'
    "approve_osa_report": {
        "description": "Approve OSA report for patient",
        "method": "POST",
        "endpoint": "/api/approve_osa_report",
        "parameters": [
            "patient_id",
            "report_id",
            "approval_date",
            "approver_name",
            "notes"
        ],
        "stages": ["dental_approval_osa_report"],
        "category": "approval",
        "patient_journey_stage": "planning",
        "validation_rule": "Check dentist_report_approval table with report_id and approval_status='approved'",
        "validation_query": """
            SELECT dra.id, dra.patient_id, dra.report_id, dra.approval_status, dra.dentist_id, dra.approval_timestamp, dra.notes
            FROM dentist_report_approval dra
            WHERE dra.patient_id = %s AND dra.report_id = %s AND dra.approval_status = 'approved'
        """,
        "ai_guidance": "Use this action when an OSA report has been uploaded and needs dental approval. The dentist should review the OSA report and approve it before proceeding with oral appliance ordering. This is a critical step that ensures the treatment plan is based on approved diagnostic data."
    },
    
    # Stage 15: Order Oral Appliance
    # Validation: Check patient_device_order table with device_type='oral_appliance'
    "order_oral_appliance": {
        "description": "Place order for oral appliance",
        "method": "POST",
        "endpoint": "/api/order_oral_appliance",
        "parameters": [
            "patient_id",
            "device_type",
            "device_brand",
            "order_date",
            "lab_name",
            "notes"
        ],
        "input_options": {
            "device_type": [
                "Herbst",
                "Dorsal fins",
                "Other"
            ],
            "device_brand": [
                "Dynaflex",
                "Emerald",
                "Orthoapnea",
                "Oasys",
                "Pantera X3",
                "Respire",
                "Other"
            ]
        },
        "stages": ["order_oral_appliance"],
        "category": "ordering",
        "patient_journey_stage": "order_device",
        "validation_rule": "Check patient_device_order table with device_type='oral_appliance'",
        "validation_query": """
            SELECT pdo.id, pdo.device_type, pdo.device_name, pdo.order_date, pdo.status, pdo.notes
            FROM patient_device_order pdo
            WHERE pdo.patient_id = %s AND LOWER(pdo.device_type) = LOWER('oral_appliance')
        """,
        "ai_guidance": "Use this action when the OSA report has been approved and all required documents (CBCT, intraoral scan, HIPAA consent) are uploaded. This orders the custom oral appliance for the patient. The appliance will be manufactured and delivered to the dental office."
    },
    
    # Stage 16: Device Delivered to Dental Office
    # Validation: Check patient_device_order table with device_type='oral_appliance' AND status='delivered'
    "update_device_delivery": {
        "description": "Update device delivery status",
        "method": "POST",
        "endpoint": "/api/update_device_delivery",
        "parameters": [
            "patient_id",
            "arrival_date",
            "delivery_notes"
        ],
        "stages": ["device_delivered"],
        "category": "tracking",
        "patient_journey_stage": "order_device",
        "validation_rule": "Check patient_device_order table with device_type='oral_appliance' AND status='delivered'",
        "validation_query": """
            SELECT pdo.id, pdo.device_type, pdo.device_name, pdo.order_date, pdo.arrival_date, pdo.status, pdo.notes
            FROM patient_device_order pdo
            WHERE pdo.patient_id = %s AND LOWER(pdo.device_type) = LOWER('oral_appliance') AND pdo.status = 'delivered'
        """,
        "ai_guidance": "Use this action when the oral appliance has been delivered to the dental office. Update the delivery status to track the device arrival and prepare for patient fitting."
    },
    
    # Stage 17: Schedule Oral Appliance Delivery
    # Validation: Check patient_consult_schedule with consult_type='oral_appliance_delivery'
    "schedule_appliance_delivery": {
        "description": "Schedule oral appliance delivery appointment",
        "method": "POST",
        "endpoint": "/api/schedule_appliance_delivery",
        "parameters": [
            "patient_id",
            "scheduled_date",
            "scheduled_time",
            "notes"
        ],
        "stages": ["schedule_oral_appliance_delivery"],
        "category": "scheduling",
        "patient_journey_stage": "order_device",
        "validation_rule": "Check patient_consult_schedule with consult_type='oral_appliance_delivery'",
        "validation_query": """
            SELECT pcs.id, pcs.scheduled_datetime, pcs.notes, pcs.status
            FROM patient_consult_schedule pcs
            WHERE pcs.patient_id = %s AND LOWER(pcs.consult_type) = LOWER('oral_appliance_delivery')
        """,
        "ai_guidance": "Use this action when the oral appliance has been delivered and is ready for patient fitting. Schedule the delivery appointment to fit the custom oral appliance to the patient."
    },
    
    # Stage 18: Oral Appliance Delivery Completed
    # Validation: Check patient_consult_schedule with consult_type='oral_appliance_delivery' AND status='completed'
    "complete_appliance_delivery": {
        "description": "Mark appliance delivery as completed",
        "method": "POST",
        "endpoint": "/api/complete_appliance_delivery",
        "parameters": [
            "patient_id",
            "completion_date",
            "completion_time",
            "notes"
        ],
        "stages": ["oral_appliance_delivery_completed"],
        "category": "scheduling",
        "patient_journey_stage": "order_device",
        "validation_rule": "Check patient_consult_schedule with consult_type='oral_appliance_delivery' AND status='completed'",
        "validation_query": """
            SELECT pcs.id, pcs.scheduled_datetime, pcs.completed_datetime, pcs.comment, pcs.status
            FROM patient_consult_schedule pcs
            WHERE pcs.patient_id = %s AND LOWER(pcs.consult_type) = LOWER('oral_appliance_delivery') AND LOWER(pcs.status) = LOWER('completed')
        """,
        "ai_guidance": "Use this action when the oral appliance delivery appointment has been completed. The patient has received their custom oral appliance and instructions for use."
    },
    
    # Stage 19: Follow Up Sleep Test After Delivery (File Upload)
    # Validation: Check files table with subcategory='sleep-test' AND upload_date > delivery_date
    "request_followup_sleep_test": {
        "description": "Request follow-up sleep test after appliance delivery",
        "method": "POST",
        "endpoint": "/api/request_documents",
        "parameters": [
            "patient_id",
            "document_types",
            "message"
        ],
        "stages": ["follow_up_sleep_test_after_delivery"],
        "category": "communication",
        "patient_journey_stage": "followup",
        "validation_rule": "Check files table with subcategory='sleep-test' AND upload_date > delivery_date",
        "validation_query": """
            SELECT f.id, f.name, f.upload_date, f.file_type
            FROM files f
            WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('sleep-test') AND f.upload_date > %s
            ORDER BY f.upload_date DESC
            LIMIT 1
        """,
        "ai_guidance": "Use this action when the patient needs to complete a follow-up sleep test after receiving their oral appliance. This test evaluates the effectiveness of the treatment and ensures the appliance is working properly."
    },
    
    # Reminder actions removed for simplicity - they are communication actions, not workflow stages

    "share_patient_files": {
        "description": "Share selected patient files via email (short links)",
        "method": "POST",
        "endpoint": "/api/share_patient_files",
        "parameters": [
            "patient_id",
            "file_ids",
            "recipient_emails",
            "custom_message"
        ],
        "stages": [
            "sleep_test_completed",
            "cbct_observation_report_uploaded",
            "intraoral_scan_uploaded",
            "hipaa_consent_signed",
            "osa_report_ready",
            "follow_up_sleep_test_after_delivery"
        ],
        "category": "communication",
        "validation_rule": "Generate short links and email them to recipients",
        "ai_guidance": "Use this action to share any combination of patient files with recipients. The system will generate short share links that redirect to time-limited downloads."
    },

    "view_patient_files": {
        "description": "View and manage patient files (preview or share)",
        "method": "GET",
        "endpoint": "/api/patient/{patient_id}/files",
        "parameters": [
            "patient_id"
        ],
        "stages": [
            "sleep_test_completed",
            "cbct_observation_report_uploaded",
            "intraoral_scan_uploaded",
            "hipaa_consent_signed",
            "osa_report_ready",
            "follow_up_sleep_test_after_delivery"
        ],
        "category": "files",
        "validation_rule": "List patient files with category and provide view/share controls",
        "ai_guidance": "Use this to view all patient files (grouped by category). You can preview or select files to share."
    }
}

def get_actions_for_stage(stage_key: str) -> dict:
    """
    Get all available actions for a specific stage.
    
    Args:
        stage_key (str): The stage key to get actions for
        
    Returns:
        dict: Dictionary of actions available for the stage
    """
    stage_actions = {}
    
    for action_key, action_config in ACTION_MANIFEST.items():
        # Check if action is available for this specific stage (no more generic "*" actions)
        if stage_key in action_config.get("stages", []):
            stage_actions[action_key] = action_config
    
    return stage_actions

def get_action_by_key(action_key: str) -> dict:
    """
    Get a specific action configuration by key.
    
    Args:
        action_key (str): The action key to retrieve
        
    Returns:
        dict: Action configuration or None if not found
    """
    return ACTION_MANIFEST.get(action_key)

def get_all_actions() -> dict:
    """
    Get all available actions.
    
    Returns:
        dict: All action configurations
    """
    return ACTION_MANIFEST.copy()

def validate_action_parameters(action_key: str, parameters: dict) -> tuple[bool, str]:
    """
    Validate that all required parameters are provided for an action.
    
    Args:
        action_key (str): The action key to validate
        parameters (dict): The parameters to validate
        
    Returns:
        tuple: (is_valid, error_message)
    """
    action_config = get_action_by_key(action_key)
    if not action_config:
        return False, f"Action '{action_key}' not found"
    
    required_params = action_config.get("parameters", [])
    missing_params = []
    
    for param in required_params:
        if param not in parameters:
            missing_params.append(param)
    
    if missing_params:
        return False, f"Missing required parameters: {', '.join(missing_params)}"
    
    return True, ""

def format_action_for_llm(stage_key: str) -> str:
    """
    Format available actions for LLM consumption.
    
    Args:
        stage_key (str): The current stage key
        
    Returns:
        str: Formatted string of available actions
    """
    stage_actions = get_actions_for_stage(stage_key)
    
    if not stage_actions:
        return "No actions available for this stage."
    
    formatted_actions = []
    for action_key, action_config in stage_actions.items():
        params_str = ", ".join(action_config["parameters"])
        validation_rule = action_config.get("validation_rule", "No specific validation rule")
        formatted_actions.append(
            f"- {action_key}: {action_config['description']} "
            f"(Parameters: {params_str}) "
            f"(Validation: {validation_rule})"
        )
    
    return "\n".join(formatted_actions)

def get_stage_validation_rule(stage_key: str) -> str:
    """
    Get the validation rule for a specific stage.
    
    Args:
        stage_key (str): The stage key
        
    Returns:
        str: Validation rule description
    """
    stage_actions = get_actions_for_stage(stage_key)
    
    # Find the primary action for this stage (not general actions)
    for action_key, action_config in stage_actions.items():
        if stage_key in action_config.get("stages", []):
            return action_config.get("validation_rule", "No validation rule defined")
    
    return "No validation rule defined for this stage"

def get_validation_query_for_action(action_key: str) -> str:
    """
    Get the validation query for a specific action.
    
    Args:
        action_key (str): The action key
        
    Returns:
        str: Validation query or None if not found
    """
    action_config = get_action_by_key(action_key)
    if action_config:
        return action_config.get("validation_query")
    return None

def get_ai_guidance_for_action(action_key: str) -> str:
    """
    Get the AI guidance for a specific action.
    
    Args:
        action_key (str): The action key to get AI guidance for
        
    Returns:
        str: AI guidance or empty string if not found
    """
    action_config = get_action_by_key(action_key)
    if action_config:
        return action_config.get("ai_guidance", "")
    return ""

def get_ai_guidance_for_stage(stage_key: str) -> dict:
    """
    Get AI guidance for all actions available in a specific stage.
    
    Args:
        stage_key (str): The stage key to get guidance for
        
    Returns:
        dict: Dictionary of action keys and their AI guidance
    """
    stage_actions = get_actions_for_stage(stage_key)
    guidance = {}
    
    for action_key, action_config in stage_actions.items():
        if 'ai_guidance' in action_config:
            guidance[action_key] = action_config['ai_guidance']
    
    return guidance 

def generate_action_manifest_json():
    """
    Generate a JSON representation of the action manifest for S3 storage.
    
    Returns:
        str: JSON string of the action manifest
    """
    manifest_data = {
        "generated_at": datetime.utcnow().isoformat(),
        "version": "1.0",
        "total_actions": len(ACTION_MANIFEST),
        "actions": ACTION_MANIFEST,
        "stage_summary": {}
    }
    
    # Generate stage summary
    for action_key, action_config in ACTION_MANIFEST.items():
        for stage in action_config.get("stages", []):
            if stage not in manifest_data["stage_summary"]:
                manifest_data["stage_summary"][stage] = []
            manifest_data["stage_summary"][stage].append({
                "action_key": action_key,
                "description": action_config["description"],
                "category": action_config["category"],
                "ai_guidance": action_config.get("ai_guidance", "")
            })
    
    return json.dumps(manifest_data, indent=2)

def upload_action_manifest_to_s3():
    """
    Upload the action manifest to S3 for static serving.
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Generate JSON
        manifest_json = generate_action_manifest_json()
        
        # Setup S3 client
        s3_client = boto3.client('s3', region_name='us-west-2')
        bucket_name = os.getenv('S3_BUCKET_NAME', 'vizbrizpatients')
        
        # Upload to S3
        s3_key = 'action_manifest/action_manifest.json'
        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=manifest_json,
            ContentType='application/json',
            CacheControl='max-age=3600'  # Cache for 1 hour
        )
        
        print(f"✅ Action manifest uploaded to S3: s3://{bucket_name}/{s3_key}")
        return True
        
    except Exception as e:
        print(f"❌ Error uploading action manifest to S3: {e}")
        return False

def get_action_manifest_from_s3():
    """
    Fetch the action manifest from S3.
    
    Returns:
        dict: Action manifest data or None if not found
    """
    try:
        # Setup S3 client
        s3_client = boto3.client('s3', region_name='us-west-2')
        bucket_name = os.getenv('S3_BUCKET_NAME', 'vizbrizpatients')
        s3_key = 'action_manifest/action_manifest.json'
        
        # Get object from S3
        response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
        manifest_data = json.loads(response['Body'].read().decode('utf-8'))
        
        return manifest_data
        
    except Exception as e:
        print(f"❌ Error fetching action manifest from S3: {e}")
        return None

def get_action_manifest_url():
    """
    Get the S3 URL for the action manifest.
    
    Returns:
        str: S3 URL for the action manifest
    """
    bucket_name = os.getenv('S3_BUCKET_NAME', 'vizbrizpatients')
    return f"https://{bucket_name}.s3.us-west-2.amazonaws.com/patients/manifest/action_manifest.json"

def upload_action_manifest_to_s3():
    """
    Upload the action manifest to S3 for static serving.
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Generate JSON
        manifest_json = generate_action_manifest_json()
        
        # Setup S3 client
        s3_client = boto3.client('s3', region_name='us-west-2')
        bucket_name = os.getenv('S3_BUCKET_NAME', 'vizbrizpatients')
        
        # Upload to S3 - same location as manifest_config
        s3_key = 'patients/manifest/action_manifest.json'
        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=manifest_json,
            ContentType='application/json',
            CacheControl='max-age=3600'  # Cache for 1 hour
        )
        
        print(f"✅ Action manifest uploaded to S3: s3://{bucket_name}/{s3_key}")
        return True
        
    except Exception as e:
        print(f"❌ Error uploading action manifest to S3: {e}")
        return False

def get_action_manifest_from_s3():
    """
    Fetch the action manifest from S3.
    
    Returns:
        dict: Action manifest data or None if not found
    """
    try:
        # Setup S3 client
        s3_client = boto3.client('s3', region_name='us-west-2')
        bucket_name = os.getenv('S3_BUCKET_NAME', 'vizbrizpatients')
        s3_key = 'patients/manifest/action_manifest.json'
        
        # Get object from S3
        response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
        manifest_data = json.loads(response['Body'].read().decode('utf-8'))
        
        return manifest_data
        
    except Exception as e:
        print(f"❌ Error fetching action manifest from S3: {e}")
        return None 