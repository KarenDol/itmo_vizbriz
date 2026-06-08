"""
Simple Enhanced Patient Workflow
Reads from patient_manifest table and fetches S3 URLs for files.
"""

import mysql.connector
from datetime import datetime
import json
from typing import Dict, Any, List
import boto3
from botocore.exceptions import ClientError
import os

# Database configuration
DB_CONFIG = {
    'host': 'vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com',
    'user': 'admin',
    'password': 'Vizbriz2025!',
    'database': 'vizbriz',
    'port': 3306
}

# S3 configuration
S3_BUCKET = os.environ.get('S3_BUCKET_NAME', 'vizbrizpatients')
S3_REGION = os.environ.get('AWS_REGION', 'us-west-2')

# All 19 stages definition
STAGE_DEFINITIONS = [
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

def generate_s3_presigned_url(s3_key: str, expiration: int = 3600, mode: str = 'download') -> str:
    """Generate a presigned URL for S3 file access
    
    Args:
        s3_key: The S3 key of the file
        expiration: URL expiration time in seconds
        mode: 'download' for attachment, 'view' for inline display
    """
    try:
        s3_client = boto3.client('s3', region_name=S3_REGION)
        
        # Define parameters based on mode
        params = {
            'Bucket': S3_BUCKET, 
            'Key': s3_key
        }
        
        # For view mode, add ResponseContentDisposition to display inline
        if mode == 'view':
            params['ResponseContentDisposition'] = 'inline'
        
        url = s3_client.generate_presigned_url(
            'get_object',
            Params=params,
            ExpiresIn=expiration
        )
        return url
    except ClientError as e:
        print(f"Error generating presigned URL for {s3_key}: {e}")
        return None

def is_viewable_file(file_type: str) -> bool:
    """Check if a file type can be viewed inline in a browser"""
    viewable_extensions = {
        'pdf', 'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'tiff', 'tif',  # Images and PDFs
        'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',  # Office documents
        'txt', 'csv', 'html', 'htm', 'xml', 'json',  # Text files
        'dcm', 'dicom',  # Medical imaging files (some browsers can view these)
        'edf', 'rec', 'hyp', 'xml', 'txt', 'csv'  # Sleep study file formats
    }
    
    if not file_type:
        return False
    
    # Extract extension from file type or filename
    extension = file_type.lower().split('.')[-1] if '.' in file_type else file_type.lower()
    return extension in viewable_extensions

def get_files_for_stage(patient_id: int, stage_key: str, cursor) -> List[Dict]:
    """Get files and S3 URLs for a specific stage"""
    files = []
    
    try:
        if stage_key == "quiz_completion":
            # First, check if there are questionnaire files in the files table
            cursor.execute("""
                SELECT f.id, f.name, f.upload_date, f.file_type, f.s3_key, f.category, f.subcategory
                FROM files f
                WHERE f.patient_id = %s AND f.category = 'medical' AND f.subcategory = 'questionnaire'
                ORDER BY f.upload_date DESC
                LIMIT 1
            """, (patient_id,))
            questionnaire_result = cursor.fetchone()
            
            if questionnaire_result:
                # If we have a questionnaire file, use it and determine if it's viewable
                is_viewable = is_viewable_file(questionnaire_result['file_type'])
                mode = 'view' if is_viewable else 'download'
                file_url = generate_s3_presigned_url(questionnaire_result['s3_key'], mode=mode) if questionnaire_result['s3_key'] else None
                
                files.append({
                    'type': 'file',
                    'name': questionnaire_result['name'],
                    'date': questionnaire_result['upload_date'],
                    'description': 'Patient Questionnaire',
                    'file_type': questionnaire_result['file_type'],
                    's3_key': questionnaire_result['s3_key'],
                    'download_url': file_url,
                    'is_viewable': is_viewable
                })
            else:
                # Fallback to quiz data if no questionnaire file exists
                cursor.execute("""
                    SELECT cq.id, cq.created_at, cq.quiz_type, cq.patient_email, cq.quiz_input
                    FROM conversion_quiz cq
                    WHERE cq.user_id = %s AND cq.quiz_type = 'basic_quiz'
                """, (patient_id,))
                result = cursor.fetchone()
                if result:
                    files.append({
                        'type': 'quiz',
                        'name': f"Quiz Results - {result['quiz_type']}",
                        'date': result['created_at'],
                        'description': 'Patient sleep apnea screening questionnaire',
                        'data': result['quiz_input'],
                        'download_url': None,
                        'is_viewable': False
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
                is_viewable = is_viewable_file(result['file_type'])
                mode = 'view' if is_viewable else 'download'
                file_url = generate_s3_presigned_url(result['s3_key'], mode=mode) if result['s3_key'] else None
                files.append({
                    'type': 'file',
                    'name': result['name'],
                    'date': result['upload_date'],
                    'description': f"Sleep test file - {result['file_type']}",
                    'file_type': result['file_type'],
                    's3_key': result['s3_key'],
                    'download_url': file_url,
                    'is_viewable': is_viewable
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
                is_viewable = is_viewable_file(result['file_type'])
                mode = 'view' if is_viewable else 'download'
                file_url = generate_s3_presigned_url(result['s3_key'], mode=mode) if result['s3_key'] else None
                files.append({
                    'type': 'adminfile',
                    'name': result['name'],
                    'date': result['upload_date'],
                    'description': f"CBCT observation report - {result['file_type']}",
                    'file_type': result['file_type'],
                    's3_key': result['s3_key'],
                    'download_url': file_url,
                    'is_viewable': is_viewable
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
                is_viewable = is_viewable_file(result['file_type'])
                mode = 'view' if is_viewable else 'download'
                file_url = generate_s3_presigned_url(result['s3_key'], mode=mode) if result['s3_key'] else None
                files.append({
                    'type': 'file',
                    'name': result['name'],
                    'date': result['upload_date'],
                    'description': f"Intraoral scan - {result['file_type']}",
                    'file_type': result['file_type'],
                    's3_key': result['s3_key'],
                    'download_url': file_url,
                    'is_viewable': is_viewable
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
                is_viewable = is_viewable_file(result['file_type'])
                mode = 'view' if is_viewable else 'download'
                file_url = generate_s3_presigned_url(result['s3_key'], mode=mode) if result['s3_key'] else None
                files.append({
                    'type': 'file',
                    'name': result['name'],
                    'date': result['upload_date'],
                    'description': f"HIPAA consent form - {result['file_type']}",
                    'file_type': result['file_type'],
                    's3_key': result['s3_key'],
                    'download_url': file_url,
                    'is_viewable': is_viewable
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
                is_viewable = is_viewable_file(result['file_type'])
                mode = 'view' if is_viewable else 'download'
                file_url = generate_s3_presigned_url(result['s3_key'], mode=mode) if result['s3_key'] else None
                files.append({
                    'type': 'adminfile',
                    'name': result['name'],
                    'date': result['upload_date'],
                    'description': f"OSA patient report - {result['file_type']}",
                    'file_type': result['file_type'],
                    's3_key': result['s3_key'],
                    'download_url': file_url,
                    'is_viewable': is_viewable
                })
        
        elif stage_key == "follow_up_sleep_test_after_delivery":
            # Get follow-up sleep test files
            cursor.execute("""
                SELECT f.id, f.name, f.upload_date, f.file_type, f.s3_key, f.category, f.subcategory
                FROM files f
                WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('sleep-test')
                ORDER BY f.upload_date DESC
            """, (patient_id,))
            results = cursor.fetchall()
            for result in results:
                is_viewable = is_viewable_file(result['file_type'])
                mode = 'view' if is_viewable else 'download'
                file_url = generate_s3_presigned_url(result['s3_key'], mode=mode) if result['s3_key'] else None
                files.append({
                    'type': 'file',
                    'name': result['name'],
                    'date': result['upload_date'],
                    'description': f"Follow-up sleep test - {result['file_type']}",
                    'file_type': result['file_type'],
                    's3_key': result['s3_key'],
                    'download_url': file_url,
                    'is_viewable': is_viewable
                })
    
    except Exception as e:
        print(f"Error getting files for stage {stage_key}: {e}")
    
    return files

def get_enhanced_patient_workflow(patient_id: int) -> Dict[str, Any]:
    """Get patient workflow from patient_manifest table with file links"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # Get patient information
        cursor.execute("SELECT * FROM patients WHERE id = %s", (patient_id,))
        patient = cursor.fetchone()
        
        if not patient:
            return {'error': 'Patient not found'}
        
        # Get all manifest entries for this patient
        cursor.execute("""
            SELECT * FROM patient_manifest 
            WHERE patient_id = %s 
            ORDER BY stage_number
        """, (patient_id,))
        manifest_entries = cursor.fetchall()
        
        # Create a dictionary of manifest entries by stage_key
        manifest_dict = {entry['stage_key']: entry for entry in manifest_entries}
        
        # Build enhanced stages
        enhanced_stages = []
        for stage_def in STAGE_DEFINITIONS:
            stage_key = stage_def['key']
            stage_name = stage_def['stage_name']
            stage_number = stage_def['stage_number']
            
            # Get manifest entry for this stage
            manifest_entry = manifest_dict.get(stage_key)
            
            # Get files for this stage
            files = get_files_for_stage(patient_id, stage_key, cursor)
            
            # Parse stage_data if it exists
            stage_data = None
            if manifest_entry and manifest_entry.get('stage_data'):
                try:
                    stage_data = json.loads(manifest_entry['stage_data'])
                except json.JSONDecodeError:
                    stage_data = manifest_entry['stage_data']
            
            enhanced_stages.append({
                'stage_number': stage_number,
                'stage_name': stage_name,
                'stage_key': stage_key,
                'is_completed': manifest_entry.get('is_completed', False) if manifest_entry else False,
                'completion_date': manifest_entry.get('completion_date') if manifest_entry else None,
                'status_message': manifest_entry.get('status_message', 'Stage not started') if manifest_entry else 'Stage not started',
                'files': files,
                'stage_data': stage_data
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
            
            print() 