"""
Enhanced Patient Workflow with Unified Manifest Validation
This combines the comprehensive manifest validation with file links and detailed stage information.
"""

import mysql.connector
from datetime import datetime
import json
from typing import Dict, Any, List
import boto3
from botocore.exceptions import ClientError

# Database configuration
DB_CONFIG = {
    'host': 'vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com',
    'user': 'admin',
    'password': 'Vizbriz2025!',
    'database': 'vizbriz',
    'port': 3306
}

# S3 configuration
S3_BUCKET = 'vizbriz-files'
S3_REGION = 'us-east-2'

# Enhanced manifest definition with all 19 stages
ENHANCED_MANIFEST_DEFINITION = [
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

def generate_s3_presigned_url(s3_key: str, expiration: int = 3600) -> str:
    """Generate a presigned URL for S3 file access"""
    try:
        s3_client = boto3.client('s3', region_name=S3_REGION)
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET, 'Key': s3_key},
            ExpiresIn=expiration
        )
        return url
    except ClientError as e:
        print(f"Error generating presigned URL for {s3_key}: {e}")
        return None

def get_file_links_for_stage(patient_id: int, stage_key: str, cursor) -> List[Dict]:
    """Get file links and information for a specific stage"""
    file_links = []
    
    try:
        if stage_key == "quiz_completion":
            # Get quiz files
            cursor.execute("""
                SELECT cq.id, cq.created_at, cq.quiz_type, cq.patient_email, cq.quiz_input
                FROM conversion_quiz cq
                WHERE cq.user_id = %s AND cq.quiz_type = 'basic_quiz'
            """, (patient_id,))
            result = cursor.fetchone()
            if result:
                file_links.append({
                    'type': 'quiz',
                    'name': f"Quiz Results - {result['quiz_type']}",
                    'date': result['created_at'],
                    'description': 'Patient sleep apnea screening questionnaire',
                    'data': result['quiz_input'],
                    'download_url': None
                })
        
        elif stage_key == "sleep_test_completed":
            # Get sleep test files
            cursor.execute("""
                SELECT f.id, f.name, f.upload_date, f.file_type, f.s3_key, f.category, f.subcategory
                FROM files f
                WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('sleep-test')
                ORDER BY f.upload_date DESC
            """, (patient_id,))
            results = cursor.fetchall()
            for result in results:
                download_url = generate_s3_presigned_url(result['s3_key']) if result['s3_key'] else None
                file_links.append({
                    'type': 'file',
                    'name': result['name'],
                    'date': result['upload_date'],
                    'description': f"Sleep test file - {result['file_type']}",
                    'file_type': result['file_type'],
                    'category': result['category'],
                    'subcategory': result['subcategory'],
                    'download_url': download_url
                })
        
        elif stage_key == "cbct_observation_report_uploaded":
            # Get CBCT observation files
            cursor.execute("""
                SELECT af.id, af.name, af.upload_date, af.file_type, af.s3_key, af.file_category
                FROM adminfiles af
                WHERE af.patient_id = %s AND LOWER(af.file_category) = LOWER('cbct observations')
                ORDER BY af.upload_date DESC
            """, (patient_id,))
            results = cursor.fetchall()
            for result in results:
                download_url = generate_s3_presigned_url(result['s3_key']) if result['s3_key'] else None
                file_links.append({
                    'type': 'adminfile',
                    'name': result['name'],
                    'date': result['upload_date'],
                    'description': f"CBCT observation report - {result['file_type']}",
                    'file_type': result['file_type'],
                    'category': result['file_category'],
                    'download_url': download_url
                })
        
        elif stage_key == "intraoral_scan_uploaded":
            # Get intraoral scan files
            cursor.execute("""
                SELECT f.id, f.name, f.upload_date, f.file_type, f.s3_key, f.category, f.subcategory
                FROM files f
                WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('intraoral-scan')
                ORDER BY f.upload_date DESC
            """, (patient_id,))
            results = cursor.fetchall()
            for result in results:
                download_url = generate_s3_presigned_url(result['s3_key']) if result['s3_key'] else None
                file_links.append({
                    'type': 'file',
                    'name': result['name'],
                    'date': result['upload_date'],
                    'description': f"Intraoral scan - {result['file_type']}",
                    'file_type': result['file_type'],
                    'category': result['category'],
                    'subcategory': result['subcategory'],
                    'download_url': download_url
                })
        
        elif stage_key == "hipaa_consent_signed":
            # Get HIPAA consent files
            cursor.execute("""
                SELECT f.id, f.name, f.upload_date, f.file_type, f.s3_key, f.category, f.subcategory
                FROM files f
                WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('billing') AND (
                    LOWER(f.name) LIKE '%hipaa%' OR 
                    LOWER(f.name) LIKE '%consent%' OR
                    LOWER(f.name) LIKE '%authorization%'
                )
                ORDER BY f.upload_date DESC
            """, (patient_id,))
            results = cursor.fetchall()
            for result in results:
                download_url = generate_s3_presigned_url(result['s3_key']) if result['s3_key'] else None
                file_links.append({
                    'type': 'file',
                    'name': result['name'],
                    'date': result['upload_date'],
                    'description': f"HIPAA consent form - {result['file_type']}",
                    'file_type': result['file_type'],
                    'category': result['category'],
                    'subcategory': result['subcategory'],
                    'download_url': download_url
                })
        
        elif stage_key == "osa_report_ready":
            # Get OSA report files
            cursor.execute("""
                SELECT af.id, af.name, af.upload_date, af.file_type, af.s3_key, af.file_category
                FROM adminfiles af
                WHERE af.patient_id = %s AND LOWER(af.file_category) LIKE LOWER('%patient report%') AND af.is_public = 1
                ORDER BY af.upload_date DESC
            """, (patient_id,))
            results = cursor.fetchall()
            for result in results:
                download_url = generate_s3_presigned_url(result['s3_key']) if result['s3_key'] else None
                file_links.append({
                    'type': 'adminfile',
                    'name': result['name'],
                    'date': result['upload_date'],
                    'description': f"OSA patient report - {result['file_type']}",
                    'file_type': result['file_type'],
                    'category': result['file_category'],
                    'download_url': download_url
                })
        
        elif stage_key == "order_oral_appliance":
            # Get device order information
            cursor.execute("""
                SELECT pdo.id, pdo.device_type, pdo.device_name, pdo.order_date, pdo.status, pdo.notes
                FROM patient_device_order pdo
                WHERE pdo.patient_id = %s AND LOWER(pdo.device_type) = LOWER('oral_appliance')
                ORDER BY pdo.order_date DESC
            """, (patient_id,))
            results = cursor.fetchall()
            for result in results:
                file_links.append({
                    'type': 'order',
                    'name': f"Device Order - {result['device_name']}",
                    'date': result['order_date'],
                    'description': f"Oral appliance order - Status: {result['status']}",
                    'device_type': result['device_type'],
                    'device_name': result['device_name'],
                    'status': result['status'],
                    'notes': result['notes'],
                    'download_url': None
                })
        
        elif stage_key == "follow_up_sleep_test_after_delivery":
            # Get follow-up sleep test files (after delivery date)
            cursor.execute("""
                SELECT f.id, f.name, f.upload_date, f.file_type, f.s3_key, f.category, f.subcategory
                FROM files f
                WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('sleep-test')
                ORDER BY f.upload_date DESC
            """, (patient_id,))
            results = cursor.fetchall()
            for result in results:
                download_url = generate_s3_presigned_url(result['s3_key']) if result['s3_key'] else None
                file_links.append({
                    'type': 'file',
                    'name': result['name'],
                    'date': result['upload_date'],
                    'description': f"Follow-up sleep test - {result['file_type']}",
                    'file_type': result['file_type'],
                    'category': result['category'],
                    'subcategory': result['subcategory'],
                    'download_url': download_url
                })
    
    except Exception as e:
        print(f"Error getting file links for stage {stage_key}: {e}")
    
    return file_links

def get_consultation_info_for_stage(patient_id: int, stage_key: str, cursor) -> Dict:
    """Get consultation information for stages that involve appointments"""
    try:
        if stage_key in ["initial_consult_scheduled", "initial_consult_completed"]:
            consult_type = "sleep_expert"
        elif stage_key == "sleep_study_scheduled":
            consult_type = "sleep_doctor"
        elif stage_key == "schedule_sleep_test_review":
            consult_type = "sleep_doctor"
        elif stage_key == "sleep_doctor_followup_completed":
            consult_type = "ep_doctor"
        elif stage_key == "dental_sleep_doctor_consult_scheduled":
            consult_type = "dental_sleep_doctor"
        elif stage_key == "met_with_dental_sleep_expert":
            consult_type = "dental_sleep_doctor"
        elif stage_key in ["schedule_oral_appliance_delivery", "oral_appliance_delivery_completed"]:
            consult_type = "oral_appliance_delivery"
        else:
            return None
        
        cursor.execute("""
            SELECT pcs.id, pcs.scheduled_datetime, pcs.completed_datetime, pcs.status, pcs.notes, pcs.comment
            FROM patient_consult_schedule pcs
            WHERE pcs.patient_id = %s AND LOWER(pcs.consult_type) = LOWER(%s)
            ORDER BY pcs.scheduled_datetime DESC
            LIMIT 1
        """, (patient_id, consult_type))
        
        result = cursor.fetchone()
        if result:
            return {
                'consult_id': result['id'],
                'scheduled_datetime': result['scheduled_datetime'],
                'completed_datetime': result['completed_datetime'],
                'status': result['status'],
                'notes': result['notes'],
                'comment': result['comment']
            }
    
    except Exception as e:
        print(f"Error getting consultation info for stage {stage_key}: {e}")
    
    return None

def get_enhanced_stage_info(patient_id: int, stage_key: str, stage_data: Dict, cursor) -> Dict:
    """Get enhanced information for a stage including files and consultation details"""
    enhanced_info = {
        'stage_key': stage_key,
        'is_completed': stage_data.get('is_completed', False),
        'completion_date': stage_data.get('completion_date'),
        'status_message': stage_data.get('status_message', ''),
        'files': get_file_links_for_stage(patient_id, stage_key, cursor),
        'consultation': get_consultation_info_for_stage(patient_id, stage_key, cursor),
        'stage_data': stage_data.get('stage_data')
    }
    
    return enhanced_info

def get_enhanced_patient_workflow(patient_id: int) -> Dict[str, Any]:
    """Get comprehensive patient workflow with all stage information and file links"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # Get patient information
        cursor.execute("SELECT * FROM patients WHERE id = %s", (patient_id,))
        patient = cursor.fetchone()
        
        if not patient:
            return {'error': 'Patient not found'}
        
        # Get all stage validations
        from manifest_validator_clean import validate_patient_stages
        stage_results = validate_patient_stages(patient_id)
        
        if not stage_results:
            return {'error': 'Failed to validate patient stages'}
        
        # Build enhanced stage information
        enhanced_stages = []
        for stage_def in ENHANCED_MANIFEST_DEFINITION:
            stage_key = stage_def['key']
            stage_name = stage_def['stage_name']
            stage_number = stage_def['stage_number']
            
            stage_data = stage_results.get(stage_key, {})
            enhanced_info = get_enhanced_stage_info(patient_id, stage_key, stage_data, cursor)
            
            enhanced_stages.append({
                'stage_number': stage_number,
                'stage_name': stage_name,
                'stage_key': stage_key,
                'is_completed': enhanced_info['is_completed'],
                'completion_date': enhanced_info['completion_date'],
                'status_message': enhanced_info['status_message'],
                'files': enhanced_info['files'],
                'consultation': enhanced_info['consultation'],
                'stage_data': enhanced_info['stage_data']
            })
        
        # Calculate progress
        completed_stages = len([s for s in enhanced_stages if s['is_completed']])
        total_stages = len(enhanced_stages)
        progress_percentage = round((completed_stages / total_stages) * 100) if total_stages > 0 else 0
        
        return {
            'patient': patient,
            'stages': enhanced_stages,
            'completed_stages': completed_stages,
            'total_stages': total_stages,
            'progress_percentage': progress_percentage,
            'current_stage': next((s for s in enhanced_stages if not s['is_completed']), enhanced_stages[-1] if enhanced_stages else None)
        }
        
    except Exception as e:
        print(f"Error in enhanced patient workflow: {e}")
        return {'error': str(e)}
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    # Test with patient 15927
    result = get_enhanced_patient_workflow(15927)
    
    if 'error' in result:
        print(f"Error: {result['error']}")
    else:
        print(f"=== Enhanced Patient Workflow for Patient {result['patient']['id']} ===")
        print(f"Patient: {result['patient']['name']}")
        print(f"Progress: {result['completed_stages']}/{result['total_stages']} stages completed ({result['progress_percentage']}%)")
        print()
        
        for stage in result['stages']:
            status_icon = "✅" if stage['is_completed'] else "⏳"
            print(f"{status_icon} Stage {stage['stage_number']}: {stage['stage_name']}")
            print(f"   Status: {stage['status_message']}")
            
            if stage['files']:
                print(f"   Files ({len(stage['files'])}):")
                for file in stage['files']:
                    file_icon = "📄" if file['download_url'] else "📋"
                    print(f"     {file_icon} {file['name']} ({file['date'].strftime('%B %d, %Y')})")
                    if file['download_url']:
                        print(f"       Download: {file['download_url']}")
            
            if stage['consultation']:
                print(f"   Consultation:")
                print(f"     Status: {stage['consultation']['status']}")
                if stage['consultation']['scheduled_datetime']:
                    print(f"     Scheduled: {stage['consultation']['scheduled_datetime'].strftime('%B %d, %Y')}")
                if stage['consultation']['completed_datetime']:
                    print(f"     Completed: {stage['consultation']['completed_datetime'].strftime('%B %d, %Y')}")
            
            print() 