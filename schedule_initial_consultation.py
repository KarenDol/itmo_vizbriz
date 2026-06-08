#!/usr/bin/env python3
"""
Function to schedule initial consultation with sleep expert for Stage 2 validation
"""

import mysql.connector
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import json

# Database configuration
DB_CONFIG = {
    'host': 'vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com',
    'user': 'admin',
    'password': 'Vizbriz2025!',
    'database': 'vizbriz',
    'port': 3306
}

def schedule_initial_consultation(
    patient_id: int,
    scheduled_datetime: datetime,
    notes: Optional[str] = None,
    status: str = 'scheduled'
) -> Dict[str, Any]:
    """
    Schedule initial consultation with sleep expert for a patient
    
    Args:
        patient_id: ID of the patient
        scheduled_datetime: When the consultation is scheduled for
        notes: Optional notes about the consultation
        status: Status of the consultation (default: 'scheduled')
    
    Returns:
        Dict with success status and consultation details
    """
    print(f"=== Scheduling Initial Consultation for Patient {patient_id} ===")
    
    conn = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # Check if patient exists
        cursor.execute("SELECT id, name, email FROM patients WHERE id = %s", (patient_id,))
        patient = cursor.fetchone()
        
        if not patient:
            return {
                'success': False,
                'error': f'Patient with ID {patient_id} not found'
            }
        
        print(f"✅ Found patient: {patient['name']} ({patient['email']})")
        
        # Check if consultation already exists for this patient and consult_type
        cursor.execute("""
            SELECT id, scheduled_datetime, status, notes
            FROM patient_consult_schedule 
            WHERE patient_id = %s AND consult_type = 'sleep_expert'
        """, (patient_id,))
        existing_consult = cursor.fetchone()
        
        if existing_consult:
            print(f"⚠️  Consultation already exists for patient {patient_id}")
            return {
                'success': False,
                'error': 'Consultation already scheduled for this patient',
                'existing_consultation': {
                    'id': existing_consult['id'],
                    'scheduled_datetime': existing_consult['scheduled_datetime'],
                    'status': existing_consult['status'],
                    'notes': existing_consult['notes']
                }
            }
        
        # Insert new consultation
        insert_query = """
            INSERT INTO patient_consult_schedule (
                patient_id,
                consult_type,
                scheduled_datetime,
                status,
                notes,
                created_at,
                updated_at
            ) VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
        """
        
        cursor.execute(insert_query, (
            patient_id,
            'sleep_expert',
            scheduled_datetime,
            status,
            notes
        ))
        
        consultation_id = cursor.lastrowid
        conn.commit()
        
        print(f"✅ Consultation scheduled successfully!")
        print(f"   Consultation ID: {consultation_id}")
        print(f"   Scheduled for: {scheduled_datetime.strftime('%B %d, %Y at %I:%M %p')}")
        print(f"   Status: {status}")
        if notes:
            print(f"   Notes: {notes}")
        
        # Get the inserted record
        cursor.execute("""
            SELECT id, patient_id, consult_type, scheduled_datetime, status, notes, created_at
            FROM patient_consult_schedule 
            WHERE id = %s
        """, (consultation_id,))
        consultation = cursor.fetchone()
        
        return {
            'success': True,
            'consultation_id': consultation_id,
            'consultation': {
                'id': consultation['id'],
                'patient_id': consultation['patient_id'],
                'consult_type': consultation['consult_type'],
                'scheduled_datetime': consultation['scheduled_datetime'],
                'status': consultation['status'],
                'notes': consultation['notes'],
                'created_at': consultation['created_at']
            },
            'patient': {
                'id': patient['id'],
                'name': patient['name'],
                'email': patient['email']
            }
        }
        
    except Exception as e:
        print(f"❌ Error scheduling consultation: {e}")
        if conn:
            conn.rollback()
        return {
            'success': False,
            'error': str(e)
        }
    finally:
        if conn:
            cursor.close()
            conn.close()

def update_consultation_status(
    consultation_id: int,
    status: str,
    completed_datetime: Optional[datetime] = None,
    comment: Optional[str] = None
) -> Dict[str, Any]:
    """
    Update consultation status (e.g., mark as completed)
    
    Args:
        consultation_id: ID of the consultation to update
        status: New status ('scheduled', 'completed', 'cancelled', etc.)
        completed_datetime: When the consultation was completed (if applicable)
        comment: Optional comment about the consultation
    
    Returns:
        Dict with success status and updated consultation details
    """
    print(f"=== Updating Consultation Status for ID {consultation_id} ===")
    
    conn = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # Check if consultation exists
        cursor.execute("""
            SELECT id, patient_id, consult_type, scheduled_datetime, status, notes
            FROM patient_consult_schedule 
            WHERE id = %s
        """, (consultation_id,))
        consultation = cursor.fetchone()
        
        if not consultation:
            return {
                'success': False,
                'error': f'Consultation with ID {consultation_id} not found'
            }
        
        # Update consultation
        update_query = """
            UPDATE patient_consult_schedule 
            SET status = %s, updated_at = NOW()
        """
        params = [status]
        
        if completed_datetime:
            update_query += ", completed_datetime = %s"
            params.append(completed_datetime)
        
        if comment:
            update_query += ", comment = %s"
            params.append(comment)
        
        update_query += " WHERE id = %s"
        params.append(consultation_id)
        
        cursor.execute(update_query, params)
        conn.commit()
        
        print(f"✅ Consultation status updated successfully!")
        print(f"   New status: {status}")
        if completed_datetime:
            print(f"   Completed on: {completed_datetime.strftime('%B %d, %Y at %I:%M %p')}")
        if comment:
            print(f"   Comment: {comment}")
        
        # Get the updated record
        cursor.execute("""
            SELECT id, patient_id, consult_type, scheduled_datetime, completed_datetime, 
                   status, notes, comment, updated_at
            FROM patient_consult_schedule 
            WHERE id = %s
        """, (consultation_id,))
        updated_consultation = cursor.fetchone()
        
        return {
            'success': True,
            'consultation': updated_consultation
        }
        
    except Exception as e:
        print(f"❌ Error updating consultation: {e}")
        if conn:
            conn.rollback()
        return {
            'success': False,
            'error': str(e)
        }
    finally:
        if conn:
            cursor.close()
            conn.close()

def get_patient_consultations(patient_id: int) -> Dict[str, Any]:
    """
    Get all consultations for a patient
    
    Args:
        patient_id: ID of the patient
    
    Returns:
        Dict with consultations list
    """
    print(f"=== Getting Consultations for Patient {patient_id} ===")
    
    conn = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT id, patient_id, consult_type, scheduled_datetime, completed_datetime,
                   status, notes, comment, created_at, updated_at
            FROM patient_consult_schedule 
            WHERE patient_id = %s
            ORDER BY scheduled_datetime DESC
        """, (patient_id,))
        
        consultations = cursor.fetchall()
        
        print(f"✅ Found {len(consultations)} consultations for patient {patient_id}")
        
        return {
            'success': True,
            'patient_id': patient_id,
            'consultations': consultations
        }
        
    except Exception as e:
        print(f"❌ Error getting consultations: {e}")
        return {
            'success': False,
            'error': str(e)
        }
    finally:
        if conn:
            cursor.close()
            conn.close()

def validate_stage2_completion(patient_id: int) -> Dict[str, Any]:
    """
    Validate if Stage 2 (Initial Consult Scheduled) is completed
    
    Args:
        patient_id: ID of the patient
    
    Returns:
        Dict with validation results
    """
    print(f"=== Validating Stage 2 for Patient {patient_id} ===")
    
    conn = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT id, scheduled_datetime, notes, status
            FROM patient_consult_schedule 
            WHERE patient_id = %s AND consult_type = 'sleep_expert'
        """, (patient_id,))
        result = cursor.fetchone()
        
        if result and result['id']:
            print(f"✅ Stage 2 completed - Consultation scheduled for {result['scheduled_datetime'].strftime('%B %d, %Y')}")
            return {
                'is_completed': True,
                'completion_date': result['scheduled_datetime'],
                'stage_data': json.dumps({'notes': result['notes']}),
                'status_message': f"Consultation scheduled for {result['scheduled_datetime'].strftime('%B %d, %Y')}",
                'consultation_id': result['id'],
                'status': result['status']
            }
        else:
            print(f"❌ Stage 2 not completed - No consultation scheduled")
            return {
                'is_completed': False,
                'completion_date': None,
                'stage_data': None,
                'status_message': 'No consultation scheduled'
            }
        
    except Exception as e:
        print(f"❌ Error validating Stage 2: {e}")
        return {
            'is_completed': False,
            'error': str(e)
        }
    finally:
        if conn:
            cursor.close()
            conn.close()

# Example usage and testing
if __name__ == "__main__":
    # Test with patient ID 10318
    test_patient_id = 10318
    
    print("=" * 60)
    print("TESTING INITIAL CONSULTATION SCHEDULING")
    print("=" * 60)
    
    # First, check current consultations
    print("\n1. Checking current consultations...")
    consultations_result = get_patient_consultations(test_patient_id)
    if consultations_result['success']:
        print(f"Found {len(consultations_result['consultations'])} consultations")
        for consult in consultations_result['consultations']:
            print(f"  - {consult['consult_type']}: {consult['scheduled_datetime']} ({consult['status']})")
    
    # Validate current Stage 2 status
    print("\n2. Validating current Stage 2 status...")
    validation_result = validate_stage2_completion(test_patient_id)
    print(f"Stage 2 completed: {validation_result['is_completed']}")
    print(f"Status message: {validation_result['status_message']}")
    
    # Schedule a new consultation (if none exists)
    if not validation_result['is_completed']:
        print("\n3. Scheduling new consultation...")
        # Schedule for tomorrow at 2 PM
        tomorrow = datetime.now() + timedelta(days=1)
        scheduled_time = tomorrow.replace(hour=14, minute=0, second=0, microsecond=0)
        
        schedule_result = schedule_initial_consultation(
            patient_id=test_patient_id,
            scheduled_datetime=scheduled_time,
            notes="Initial consultation with sleep expert to discuss sleep apnea symptoms and treatment options.",
            status="scheduled"
        )
        
        if schedule_result['success']:
            print("✅ Consultation scheduled successfully!")
        else:
            print(f"❌ Failed to schedule consultation: {schedule_result.get('error', 'Unknown error')}")
    
    # Validate Stage 2 again
    print("\n4. Validating Stage 2 after scheduling...")
    final_validation = validate_stage2_completion(test_patient_id)
    print(f"Stage 2 completed: {final_validation['is_completed']}")
    print(f"Status message: {final_validation['status_message']}")
    
    print("\n" + "=" * 60)
    print("TESTING COMPLETED")
    print("=" * 60) 