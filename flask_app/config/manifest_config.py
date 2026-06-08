"""
Centralized Treatment Manifest Configuration
This file contains the single source of truth for all treatment workflow stages.
All other parts of the application should import from this file to ensure consistency.
"""

TREATMENT_MANIFEST_DEFINITION = [
    {"stage_number": 1, "stage_name": "Quiz Completion", "key": "quiz_completion", "prerequisites": [], "next_step": "initial_consult_scheduled"},
    {"stage_number": 2, "stage_name": "Initial Consult Scheduled", "key": "initial_consult_scheduled", "prerequisites": ["quiz_completion"], "next_step": "initial_consult_completed"},
    {"stage_number": 3, "stage_name": "Initial Consult Completed", "key": "initial_consult_completed", "prerequisites": ["initial_consult_scheduled"], "next_step": "sleep_study_scheduled"},
    {"stage_number": 4, "stage_name": "Sleep Study Scheduled", "key": "sleep_study_scheduled", "prerequisites": ["initial_consult_completed"], "next_step": "sleep_test_completed"},
    {"stage_number": 5, "stage_name": "Sleep Test Completed", "key": "sleep_test_completed", "prerequisites": ["sleep_study_scheduled"], "next_step": "schedule_sleep_test_review"},
    {"stage_number": 6, "stage_name": "Schedule Sleep Test Review", "key": "schedule_sleep_test_review", "prerequisites": ["sleep_test_completed"], "next_step": "sleep_doctor_followup_completed"},
    {"stage_number": 7, "stage_name": "Sleep Doctor Followup Completed", "key": "sleep_doctor_followup_completed", "prerequisites": ["schedule_sleep_test_review"], "next_step": "dental_sleep_doctor_consult_scheduled"},
    {"stage_number": 8, "stage_name": "Dental Sleep Doctor Consult Scheduled", "key": "dental_sleep_doctor_consult_scheduled", "prerequisites": ["sleep_doctor_followup_completed"], "next_step": "cbct_observation_report_uploaded"},
    {"stage_number": 9, "stage_name": "CBCT Observation Report Uploaded", "key": "cbct_observation_report_uploaded", "prerequisites": ["dental_sleep_doctor_consult_scheduled"], "next_step": "intraoral_scan_uploaded"},
    {"stage_number": 10, "stage_name": "Intraoral Scan Uploaded", "key": "intraoral_scan_uploaded", "prerequisites": ["cbct_observation_report_uploaded"], "next_step": "hipaa_consent_signed"},
    {"stage_number": 11, "stage_name": "HIPAA Consent Signed", "key": "hipaa_consent_signed", "prerequisites": ["intraoral_scan_uploaded"], "next_step": "met_with_dental_sleep_expert"},
    {"stage_number": 12, "stage_name": "Met with Dental Sleep Expert", "key": "met_with_dental_sleep_expert", "prerequisites": ["hipaa_consent_signed"], "next_step": "osa_report_ready"},
    {"stage_number": 13, "stage_name": "OSA Report Ready", "key": "osa_report_ready", "prerequisites": ["met_with_dental_sleep_expert"], "next_step": "order_oral_appliance"},
    {"stage_number": 14, "stage_name": "Order Oral Appliance", "key": "order_oral_appliance", "prerequisites": ["osa_report_ready"], "next_step": "device_delivered"},
    {"stage_number": 15, "stage_name": "Device Delivered to Dental Office", "key": "device_delivered", "prerequisites": ["order_oral_appliance"], "next_step": "schedule_oral_appliance_delivery"},
    {"stage_number": 16, "stage_name": "Schedule Oral Appliance Delivery", "key": "schedule_oral_appliance_delivery", "prerequisites": ["device_delivered"], "next_step": "oral_appliance_delivery_completed"},
    {"stage_number": 17, "stage_name": "Oral Appliance Delivery Completed", "key": "oral_appliance_delivery_completed", "prerequisites": ["schedule_oral_appliance_delivery"], "next_step": "follow_up_sleep_test_after_delivery"},
    {"stage_number": 18, "stage_name": "Follow Up Sleep Test After Delivery", "key": "follow_up_sleep_test_after_delivery", "prerequisites": ["oral_appliance_delivery_completed"], "next_step": "treatment_complete"},
]

def get_manifest_definition():
    """Get the centralized manifest definition."""
    return TREATMENT_MANIFEST_DEFINITION.copy()

def get_stage_by_key(stage_key):
    """Get a specific stage definition by its key."""
    for stage in TREATMENT_MANIFEST_DEFINITION:
        if stage.get('key') == stage_key:
            return stage
    return None

def get_stage_by_number(stage_number):
    """Get a specific stage definition by its number."""
    for stage in TREATMENT_MANIFEST_DEFINITION:
        if stage.get('stage_number') == stage_number:
            return stage
    return None

def get_next_stage(current_stage_key):
    """Get the next stage in the workflow."""
    current_stage = get_stage_by_key(current_stage_key)
    if not current_stage:
        return None
    current_number = current_stage.get('stage_number', 0)
    return get_stage_by_number(current_number + 1)

def get_previous_stage(current_stage_key):
    """Get the previous stage in the workflow."""
    current_stage = get_stage_by_key(current_stage_key)
    if not current_stage:
        return None
    current_number = current_stage.get('stage_number', 0)
    return get_stage_by_number(current_number - 1)

def get_stage_keys():
    """Get all stage keys in order."""
    return [stage.get('key') for stage in TREATMENT_MANIFEST_DEFINITION]

def get_stage_names():
    """Get all stage names in order."""
    return [stage.get('stage_name') for stage in TREATMENT_MANIFEST_DEFINITION]

def get_next_step_for_stage(stage_key):
    """Get the next step name for a given stage key.
    This is the centralized function that should be used everywhere.
    
    Args:
        stage_key (str): The key of the current stage
        
    Returns:
        str: The name of the next step, or "Treatment Complete" if it's the last stage
    """
    stage = get_stage_by_key(stage_key)
    if stage and 'next_step' in stage:
        return stage['next_step']
    else:
        return "Treatment Complete"

def get_prerequisites_for_stage(stage_key):
    """Get the prerequisites for a given stage key.
    
    Args:
        stage_key (str): The key of the current stage
        
    Returns:
        list: List of prerequisite stage names, or empty list if no prerequisites
    """
    stage = get_stage_by_key(stage_key)
    if stage and 'prerequisites' in stage:
        return stage['prerequisites']
    else:
        return []

def get_stage_config(stage_key):
    """Get the complete configuration for a stage including title, description, 
    prerequisites, requirements, and next step.
    
    Args:
        stage_key (str): The key of the stage
        
    Returns:
        dict: Complete stage configuration
    """
    stage_def = get_stage_by_key(stage_key)
    if not stage_def:
        return None
    
    # Define stage-specific information and forms
    stage_configs = {
        "quiz_completion": {
            "title": "Quiz Completion",
            "description": "Patient has completed the basic or advanced sleep quiz.",
            "prerequisites": ["quiz_filled"],
            "requirements": [
                "Patient must complete and submit the sleep quiz",
                "Quiz results must be stored in database"
            ],
            "form_type": "quiz_link",
            "quiz_url": "/quiz/basic?patient_id={patient_id}"
        },
        "initial_consult_scheduled": {
            "title": "Initial Consult Scheduled",
            "description": "Schedule initial consultation with sleep expert.",
            "prerequisites": ["quiz_completion"],
            "requirements": [
                "Consultation must be scheduled with sleep expert",
                "Date and time must be confirmed"
            ],
            "form_type": "consultation_schedule",
            "consult_type": "sleep_expert"
        },
        "met_with_sleep_expert": {
            "title": "Met with Sleep Expert",
            "description": "Complete initial consultation with sleep expert.",
            "prerequisites": ["initial_consult_scheduled"],
            "requirements": [
                "Consultation must be completed",
                "Notes and recommendations recorded"
            ],
            "form_type": "consultation_complete",
            "consult_type": "sleep_expert"
        },
        "sleep_study_scheduled": {
            "title": "Sleep Study Scheduled",
            "description": "Schedule consultation with sleep doctor (ENT) to order sleep study.",
            "prerequisites": ["met_with_sleep_expert"],
            "requirements": [
                "Consultation must be scheduled with sleep doctor (ENT)",
                "Date and time must be confirmed"
            ],
            "form_type": "consultation_schedule",
            "consult_type": "sleep_doctor"
        },
        "sleep_test_completed": {
            "title": "Sleep Test Completed",
            "description": "Complete sleep study or home sleep test.",
            "prerequisites": ["sleep_study_scheduled"],
            "requirements": [
                "Sleep test must be completed",
                "Results must be uploaded to system"
            ],
            "form_type": "load",
            "file_category": "sleep_test"
        },
        "schedule_sleep_test_review": {
            "title": "Schedule Sleep Test Review",
            "description": "Schedule follow-up consultation with sleep specialist to review test results.",
            "prerequisites": ["sleep_test_completed"],
            "requirements": [
                "Consultation must be scheduled with sleep specialist (EP doctor)",
                "Date and time must be confirmed"
            ],
            "form_type": "consultation_schedule",
            "consult_type": "ep_doctor"
        },
        "sleep_doctor_followup_completed": {
            "title": "Sleep Doctor Followup Completed",
            "description": "Complete follow-up consultation with sleep specialist.",
            "prerequisites": ["schedule_sleep_test_review"],
            "requirements": [
                "Follow-up consultation must be completed",
                "OSA diagnosis confirmed and documented"
            ],
            "form_type": "consultation_complete",
            "consult_type": "ep_doctor"
        },
        "dental_sleep_doctor_consult_scheduled": {
            "title": "Dental Sleep Doctor Consult Scheduled",
            "description": "Schedule consultation with dental sleep specialist.",
            "prerequisites": ["sleep_doctor_followup_completed"],
            "requirements": [
                "Consultation must be scheduled with dental sleep specialist",
                "Date and time must be confirmed"
            ],
            "form_type": "consultation_schedule",
            "consult_type": "dental_sleep_doctor"
        },
        "cbct_observation_report_uploaded": {
            "title": "CBCT Observation Report Uploaded",
            "description": "CBCT observation report has been uploaded and is available for review.",
            "prerequisites": ["dental_sleep_doctor_consult_scheduled"],
            "requirements": [
                "CBCT observation report must be uploaded as public file",
                "File must be categorized as 'CBCT Observations'"
            ],
            "form_type": "admin_file_upload",
            "file_category": "CBCT Observations"
        },
        "intraoral_scan_uploaded": {
            "title": "IntraOral Scan Uploaded",
            "description": "Intraoral scan files (STL format) have been uploaded and are available for review.",
            "prerequisites": ["cbct_observation_report_uploaded"],
            "requirements": [
                "Intraoral scan files must be uploaded",
                "Files must be in STL format",
                "Files must be categorized as 'intraoral-scan'"
            ],
            "form_type": "load",
            "file_category": "intraoral-scan"
        },
        "hipaa_consent_signed": {
            "title": "HIPAA Consent Signed",
            "description": "Patient must sign HIPAA consent and treatment authorization forms.",
            "prerequisites": ["intraoral_scan_uploaded"],
            "requirements": [
                "HIPAA consent form must be signed",
                "Treatment authorization must be completed",
                "Files must be in 'billing' subcategory",
                "File content must contain 'HIPAA' or 'consent' terms"
            ],
            "form_type": "load",
            "file_category": "billing",
            "content_validation": True
        },
        "met_with_dental_sleep_expert": {
            "title": "Met with Dental Sleep Expert",
            "description": "Complete consultation with dental sleep specialist.",
            "prerequisites": ["hipaa_consent_signed"],
            "requirements": [
                "Consultation must be completed",
                "Treatment plan discussed and approved"
            ],
            "form_type": "consultation_complete",
            "consult_type": "dental_sleep_doctor"
        },
        "clinical_data_available": {
            "title": "Clinical Data Available",
            "description": "Scans and clinical imaging completed.",
            "prerequisites": ["met_with_dental_sleep_expert"],
            "requirements": [
                "CBCT scan must be completed",
                "Intraoral scans must be completed",
                "Clinical images must be uploaded"
            ],
            "form_type": "load",
            "file_category": "cbct"
        },
        "osa_report_available": {
            "title": "OSA Report Available",
            "description": "OSA diagnosis report and treatment plan available.",
            "prerequisites": ["clinical_data_available"],
            "requirements": [
                "OSA diagnosis report must be completed",
                "Treatment plan must be finalized"
            ],
            "form_type": "load",
            "file_category": "reports"
        },
        "appliance_ordered": {
            "title": "Appliance Ordered",
            "description": "Custom oral appliance ordered from laboratory.",
            "prerequisites": ["osa_report_available"],
            "requirements": [
                "Appliance type must be selected",
                "Order must be placed with laboratory"
            ],
            "form_type": "appliance_order"
        },
        "appliance_delivery": {
            "title": "Appliance Delivery",
            "description": "Appliance delivered to clinic.",
            "prerequisites": ["appliance_ordered"],
            "requirements": [
                "Appliance must be delivered to clinic",
                "Delivery date must be recorded"
            ],
            "form_type": "appliance_delivery"
        },
        "appliance_delivery_and_fitting": {
            "title": "Appliance Delivery and Fitting",
            "description": "Appliance fitted and adjusted.",
            "prerequisites": ["appliance_delivery"],
            "requirements": [
                "Appliance must be fitted to patient",
                "Initial adjustments must be completed"
            ],
            "form_type": "appliance_fitting"
        },
        "followup_meeting": {
            "title": "Followup Meeting",
            "description": "Treatment follow-up completed.",
            "prerequisites": ["appliance_delivery_and_fitting"],
            "requirements": [
                "Follow-up appointment must be completed",
                "Treatment effectiveness must be assessed"
            ],
            "form_type": "consultation_complete",
            "consult_type": "follow_up_meeting"
        }
    }
    
    config = stage_configs.get(stage_key, {})
    if config:
        # Add the next step from the centralized manifest
        config["next_stage"] = get_next_step_for_stage(stage_key)
        config["next_stage_key"] = get_next_stage(stage_key).get('key') if get_next_stage(stage_key) else None
    
    return config 