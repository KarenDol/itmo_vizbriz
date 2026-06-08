#!/usr/bin/env python3
"""
Web interface functions for Stage 2 consultation scheduling
Can be integrated into the AI Workflow Assistant
"""

from schedule_initial_consultation import (
    schedule_initial_consultation,
    update_consultation_status,
    get_patient_consultations,
    validate_stage2_completion
)
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import json

def get_stage2_status(patient_id: int) -> Dict[str, Any]:
    """
    Get the current status of Stage 2 for a patient
    Returns data suitable for web interface display
    """
    validation_result = validate_stage2_completion(patient_id)
    
    # Get patient consultations for additional context
    consultations_result = get_patient_consultations(patient_id)
    
    return {
        'stage_key': 'initial_consult_scheduled',
        'stage_name': 'Initial Consult Scheduled',
        'stage_number': 2,
        'is_completed': validation_result['is_completed'],
        'status_message': validation_result['status_message'],
        'completion_date': validation_result.get('completion_date'),
        'consultations': consultations_result.get('consultations', []) if consultations_result['success'] else [],
        'can_complete': not validation_result['is_completed'],  # Can complete if not already completed
        'action_required': 'Schedule consultation with sleep expert' if not validation_result['is_completed'] else 'Consultation already scheduled'
    }

def complete_stage2_action(
    patient_id: int,
    scheduled_datetime: datetime,
    notes: Optional[str] = None,
    admin_user_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Complete Stage 2 action by scheduling the initial consultation
    This function can be called from the web interface
    
    Args:
        patient_id: ID of the patient
        scheduled_datetime: When to schedule the consultation
        notes: Optional notes about the consultation
        admin_user_id: ID of the admin user performing the action
    
    Returns:
        Dict with success status and details
    """
    print(f"=== Completing Stage 2 Action for Patient {patient_id} ===")
    
    # Validate current status first
    current_status = get_stage2_status(patient_id)
    
    if current_status['is_completed']:
        return {
            'success': False,
            'error': 'Stage 2 is already completed',
            'current_status': current_status
        }
    
    # Schedule the consultation
    schedule_result = schedule_initial_consultation(
        patient_id=patient_id,
        scheduled_datetime=scheduled_datetime,
        notes=notes or "Initial consultation with sleep expert scheduled via AI Workflow Assistant",
        status="scheduled"
    )
    
    if schedule_result['success']:
        # Get updated status
        updated_status = get_stage2_status(patient_id)
        
        return {
            'success': True,
            'message': f"Stage 2 completed successfully! Consultation scheduled for {scheduled_datetime.strftime('%B %d, %Y at %I:%M %p')}",
            'consultation_id': schedule_result['consultation_id'],
            'consultation': schedule_result['consultation'],
            'updated_status': updated_status
        }
    else:
        return {
            'success': False,
            'error': schedule_result.get('error', 'Unknown error occurred'),
            'current_status': current_status
        }

def get_stage2_form_data(patient_id: int) -> Dict[str, Any]:
    """
    Get form data for Stage 2 completion
    Returns suggested dates and times for scheduling
    """
    # Generate suggested dates (next 7 days)
    suggested_dates = []
    base_date = datetime.now()
    
    for i in range(1, 8):  # Next 7 days
        date = base_date + timedelta(days=i)
        # Suggest times: 9 AM, 11 AM, 2 PM, 4 PM
        for hour in [9, 11, 14, 16]:
            suggested_time = date.replace(hour=hour, minute=0, second=0, microsecond=0)
            suggested_dates.append({
                'datetime': suggested_time,
                'display': suggested_time.strftime('%B %d, %Y at %I:%M %p'),
                'value': suggested_time.isoformat()
            })
    
    return {
        'patient_id': patient_id,
        'suggested_dates': suggested_dates,
        'default_notes': "Initial consultation with sleep expert to discuss sleep apnea symptoms and treatment options.",
        'consultation_types': [
            {'value': 'sleep_expert', 'label': 'Sleep Expert Consultation'},
            {'value': 'sleep_doctor', 'label': 'Sleep Doctor Consultation'},
            {'value': 'dental_sleep_doctor', 'label': 'Dental Sleep Doctor Consultation'}
        ]
    }

def update_stage2_consultation_status(
    consultation_id: int,
    status: str,
    completed_datetime: Optional[datetime] = None,
    comment: Optional[str] = None
) -> Dict[str, Any]:
    """
    Update the status of a Stage 2 consultation
    Useful for marking consultations as completed or cancelled
    """
    result = update_consultation_status(
        consultation_id=consultation_id,
        status=status,
        completed_datetime=completed_datetime,
        comment=comment
    )
    
    if result['success']:
        return {
            'success': True,
            'message': f"Consultation status updated to {status}",
            'consultation': result['consultation']
        }
    else:
        return {
            'success': False,
            'error': result.get('error', 'Unknown error occurred')
        }

def get_stage2_workflow_data(patient_id: int) -> Dict[str, Any]:
    """
    Get comprehensive workflow data for Stage 2
    This can be used by the AI Workflow Assistant to provide recommendations
    """
    current_status = get_stage2_status(patient_id)
    form_data = get_stage2_form_data(patient_id)
    
    # Determine next actions based on current status
    next_actions = []
    
    if not current_status['is_completed']:
        next_actions.append({
            'action': 'schedule_consultation',
            'title': 'Schedule Initial Consultation',
            'description': 'Schedule the initial consultation with the sleep expert',
            'priority': 'high',
            'button_text': 'Schedule Consultation',
            'icon': 'calendar-plus'
        })
    else:
        # Consultation is scheduled, suggest next steps
        next_actions.append({
            'action': 'view_consultation',
            'title': 'View Consultation Details',
            'description': 'Review the scheduled consultation details',
            'priority': 'medium',
            'button_text': 'View Details',
            'icon': 'eye'
        })
        
        # Check if consultation can be marked as completed
        if current_status['consultations']:
            sleep_expert_consultations = [
                c for c in current_status['consultations'] 
                if c['consult_type'] == 'sleep_expert' and c['status'] == 'scheduled'
            ]
            
            if sleep_expert_consultations:
                next_actions.append({
                    'action': 'complete_consultation',
                    'title': 'Mark Consultation Complete',
                    'description': 'Mark the consultation as completed',
                    'priority': 'high',
                    'button_text': 'Mark Complete',
                    'icon': 'check-circle'
                })
    
    return {
        'current_status': current_status,
        'form_data': form_data,
        'next_actions': next_actions,
        'stage_info': {
            'key': 'initial_consult_scheduled',
            'name': 'Initial Consult Scheduled',
            'number': 2,
            'description': 'Schedule the initial consultation with a sleep expert to discuss the patient\'s sleep apnea symptoms and treatment options.',
            'requirements': [
                'Patient must have completed Stage 1 (Quiz or Questionnaire)',
                'Consultation must be scheduled with a sleep expert',
                'Consultation type must be "sleep_expert"'
            ]
        }
    }

# Example usage for web interface
if __name__ == "__main__":
    test_patient_id = 10318
    
    print("=" * 60)
    print("STAGE 2 WEB INTERFACE TESTING")
    print("=" * 60)
    
    # Get current status
    print("\n1. Getting current Stage 2 status...")
    status = get_stage2_status(test_patient_id)
    print(f"Stage 2 completed: {status['is_completed']}")
    print(f"Status message: {status['status_message']}")
    print(f"Can complete: {status['can_complete']}")
    
    # Get workflow data
    print("\n2. Getting workflow data...")
    workflow_data = get_stage2_workflow_data(test_patient_id)
    print(f"Next actions available: {len(workflow_data['next_actions'])}")
    for action in workflow_data['next_actions']:
        print(f"  - {action['title']}: {action['description']}")
    
    # Get form data
    print("\n3. Getting form data...")
    form_data = get_stage2_form_data(test_patient_id)
    print(f"Suggested dates available: {len(form_data['suggested_dates'])}")
    
    # Test completion (if not already completed)
    if not status['is_completed']:
        print("\n4. Testing Stage 2 completion...")
        # Schedule for tomorrow at 2 PM
        tomorrow = datetime.now() + timedelta(days=1)
        scheduled_time = tomorrow.replace(hour=14, minute=0, second=0, microsecond=0)
        
        completion_result = complete_stage2_action(
            patient_id=test_patient_id,
            scheduled_datetime=scheduled_time,
            notes="Initial consultation with sleep expert to discuss sleep apnea symptoms and treatment options."
        )
        
        if completion_result['success']:
            print(f"✅ Stage 2 completed: {completion_result['message']}")
        else:
            print(f"❌ Failed to complete Stage 2: {completion_result.get('error', 'Unknown error')}")
    
    print("\n" + "=" * 60)
    print("TESTING COMPLETED")
    print("=" * 60) 