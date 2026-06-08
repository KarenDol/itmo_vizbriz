"""
Improved Treatment Manifest Configuration - Grouped Stages
This version groups related activities into logical stages for better UX and workflow management.
"""

TREATMENT_MANIFEST_DEFINITION_V2 = [
    {
        "stage_number": 1, 
        "stage_name": "Initial Assessment", 
        "key": "initial_assessment", 
        "prerequisites": [], 
        "next_step": "sleep_study_phase",
        "description": "Complete patient questionnaire and initial consultation",
        "activities": [
            {"key": "quiz_completion", "name": "Complete Sleep Questionnaire", "required": True},
            {"key": "initial_consult_scheduled", "name": "Schedule Initial Consultation", "required": True},
            {"key": "initial_consult_completed", "name": "Complete Initial Consultation", "required": True}
        ]
    },
    {
        "stage_number": 2, 
        "stage_name": "Sleep Study Phase", 
        "key": "sleep_study_phase", 
        "prerequisites": ["initial_assessment"], 
        "next_step": "diagnosis_and_planning",
        "description": "Complete sleep study and get diagnosis",
        "activities": [
            {"key": "sleep_study_scheduled", "name": "Schedule Sleep Study Consultation", "required": True},
            {"key": "sleep_test_completed", "name": "Complete Sleep Test", "required": True},
            {"key": "schedule_sleep_test_review", "name": "Schedule Test Review", "required": True},
            {"key": "sleep_doctor_followup_completed", "name": "Complete Sleep Doctor Follow-up", "required": True}
        ]
    },
    {
        "stage_number": 3, 
        "stage_name": "Diagnosis and Planning", 
        "key": "diagnosis_and_planning", 
        "prerequisites": ["sleep_study_phase"], 
        "next_step": "clinical_data_collection",
        "description": "Get OSA diagnosis and treatment plan",
        "activities": [
            {"key": "dental_sleep_doctor_consult_scheduled", "name": "Schedule Dental Sleep Consultation", "required": True},
            {"key": "met_with_dental_sleep_expert", "name": "Complete Dental Sleep Consultation", "required": True},
            {"key": "osa_report_ready", "name": "OSA Report Generated", "required": True},
            {"key": "dental_approval_osa_report", "name": "Dental Approval of Treatment Plan", "required": True}
        ]
    },
    {
        "stage_number": 4, 
        "stage_name": "Clinical Data Collection", 
        "key": "clinical_data_collection", 
        "prerequisites": ["diagnosis_and_planning"], 
        "next_step": "treatment_preparation",
        "description": "Collect all required clinical data and consent",
        "activities": [
            {"key": "cbct_observation_report_uploaded", "name": "CBCT Scan Uploaded", "required": True},
            {"key": "intraoral_scan_uploaded", "name": "Intraoral Scan Uploaded", "required": True},
            {"key": "hipaa_consent_signed", "name": "HIPAA Consent Signed", "required": True}
        ]
    },
    {
        "stage_number": 5, 
        "stage_name": "Treatment Preparation", 
        "key": "treatment_preparation", 
        "prerequisites": ["clinical_data_collection"], 
        "next_step": "device_delivery",
        "description": "Order and prepare oral appliance",
        "activities": [
            {"key": "order_oral_appliance", "name": "Order Oral Appliance", "required": True},
            {"key": "device_delivered", "name": "Device Delivered to Clinic", "required": True}
        ]
    },
    {
        "stage_number": 6, 
        "stage_name": "Device Delivery", 
        "key": "device_delivery", 
        "prerequisites": ["treatment_preparation"], 
        "next_step": "treatment_completion",
        "description": "Deliver and fit oral appliance",
        "activities": [
            {"key": "schedule_oral_appliance_delivery", "name": "Schedule Device Delivery", "required": True},
            {"key": "oral_appliance_delivery_completed", "name": "Complete Device Fitting", "required": True}
        ]
    },
    {
        "stage_number": 7, 
        "stage_name": "Treatment Completion", 
        "key": "treatment_completion", 
        "prerequisites": ["device_delivery"], 
        "next_step": "treatment_complete",
        "description": "Follow-up and treatment validation",
        "activities": [
            {"key": "follow_up_sleep_test_after_delivery", "name": "Follow-up Sleep Test", "required": True}
        ]
    }
]

def get_manifest_definition_v2():
    """Get the improved grouped manifest definition."""
    return TREATMENT_MANIFEST_DEFINITION_V2.copy()

def get_stage_by_key_v2(stage_key):
    """Get a specific stage definition by its key."""
    for stage in TREATMENT_MANIFEST_DEFINITION_V2:
        if stage.get('key') == stage_key:
            return stage
    return None

def get_activities_for_stage(stage_key):
    """Get all activities for a specific stage."""
    stage = get_stage_by_key_v2(stage_key)
    if stage:
        return stage.get('activities', [])
    return []

def is_stage_complete(patient_id, stage_key):
    """Check if all required activities for a stage are complete."""
    from flask_app.services.manifest_service import ManifestService
    
    activities = get_activities_for_stage(stage_key)
    if not activities:
        return False
    
    for activity in activities:
        if activity.get('required', True):
            is_completed, _, _, _ = ManifestService._get_stage_data_from_db(patient_id, activity['key'])
            if not is_completed:
                return False
    
    return True

def get_stage_completion_status(patient_id, stage_key):
    """Get detailed completion status for a stage including all activities."""
    from flask_app.services.manifest_service import ManifestService
    
    activities = get_activities_for_stage(stage_key)
    if not activities:
        return {"complete": False, "activities": [], "completion_percentage": 0}
    
    completed_activities = 0
    total_required = 0
    activity_status = []
    
    for activity in activities:
        is_required = activity.get('required', True)
        if is_required:
            total_required += 1
            
        is_completed, completion_date, stage_data, status_message = ManifestService._get_stage_data_from_db(patient_id, activity['key'])
        
        if is_completed and is_required:
            completed_activities += 1
        
        activity_status.append({
            'key': activity['key'],
            'name': activity['name'],
            'required': is_required,
            'completed': is_completed,
            'completion_date': completion_date,
            'status_message': status_message
        })
    
    completion_percentage = (completed_activities / total_required * 100) if total_required > 0 else 0
    
    return {
        "complete": completed_activities == total_required,
        "activities": activity_status,
        "completion_percentage": completion_percentage,
        "completed_activities": completed_activities,
        "total_required": total_required
    }
