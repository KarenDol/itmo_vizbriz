import mysql.connector
from datetime import datetime
import json
from typing import Dict, Any
import unittest

# Local manifest definition (no Flask dependency)
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

# Database configuration
DB_CONFIG = {
    'host': 'vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com',
    'user': 'admin',
    'password': 'Vizbriz2025!',
    'database': 'vizbriz',
    'port': 3306
}

def validate_patient_stages(patient_id: int) -> Dict[str, Any]:
    """
    Validates all 11 stages for a specific patient using a single database connection
    """
    print(f"=== Starting validation for patient {patient_id} ===")
    
    conn = None
    try:
        # Single database connection for all validations
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True, buffered=True)
        
        # Ensure stage_file_links table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS `stage_file_links` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `patient_id` INT NOT NULL,
                `stage_key` VARCHAR(100) NOT NULL,
                `file_id` INT NOT NULL,
                `file_table` VARCHAR(20) NOT NULL CHECK (`file_table` IN ('files', 'adminfiles')),
                `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                
                -- Indexes for performance
                INDEX `idx_patient_stage` (`patient_id`, `stage_key`),
                INDEX `idx_file` (`file_id`, `file_table`),
                
                -- Foreign key constraint
                FOREIGN KEY (`patient_id`) REFERENCES `patients`(`id`) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        conn.commit()
        print("✅ Ensured stage_file_links table exists")
        
        # Clear existing stage_file_links for this patient to avoid duplicates
        cursor.execute("DELETE FROM stage_file_links WHERE patient_id = %s", (patient_id,))
        conn.commit()
        print(f"🧹 Cleared existing stage_file_links for patient {patient_id}")
        
        results = {}
        
        # Stage 1: Quiz Completion
        print("  Validating Stage 1: Quiz Completion...")
        
        # First check for quiz completion in conversion_quiz table
        cursor.execute("""
            SELECT cq.id, cq.created_at, cq.quiz_type, cq.patient_email
            FROM patients p
            LEFT JOIN conversion_quiz cq ON p.id = cq.user_id
            WHERE p.id = %s AND cq.quiz_type = 'basic_quiz'
        """, (patient_id,))
        quiz_result = cursor.fetchone()
        
        # Check for questionnaire files in files table
        cursor.execute("""
            SELECT f.id, f.name, f.upload_date, f.file_type, f.subcategory
            FROM files f
            WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('questionnaire')
        """, (patient_id,))
        quiz_files = cursor.fetchall()
        
        # Determine if stage is completed based on quiz OR questionnaire files
        stage_completed = False
        completion_date = None
        stage_data = {}
        status_message = ""
        
        if quiz_result and quiz_result['id']:
            print(f"    ✅ Quiz completed on {quiz_result['created_at'].strftime('%B %d, %Y')}")
            stage_completed = True
            completion_date = quiz_result['created_at']
            stage_data = {
                'quiz_id': quiz_result['id'],
                'quiz_type': quiz_result['quiz_type'],
                'patient_email': quiz_result['patient_email'],
                'completion_method': 'quiz_record'
            }
            status_message = f"Quiz completed on {quiz_result['created_at'].strftime('%B %d, %Y')}"
        elif quiz_files:
            print(f"    ✅ Found {len(quiz_files)} questionnaire files")
            stage_completed = True
            # Use the earliest file upload date as completion date
            completion_date = min(file['upload_date'] for file in quiz_files)
            stage_data = {
                'file_count': len(quiz_files),
                'files': [{'id': f['id'], 'name': f['name'], 'upload_date': f['upload_date'].isoformat()} for f in quiz_files],
                'completion_method': 'questionnaire_files'
            }
            status_message = f"Questionnaire files uploaded ({len(quiz_files)} files)"
        else:
            print(f"    ❌ No quiz completed and no questionnaire files found")
            stage_completed = False
            status_message = 'No quiz completed and no questionnaire files found'
        
        # Create stage_file_links for questionnaire files if they exist
        if quiz_files:
            print(f"    📁 Linking {len(quiz_files)} questionnaire files to stage")
            for file_record in quiz_files:
                cursor.execute("""
                    INSERT IGNORE INTO stage_file_links (patient_id, stage_key, file_id, file_table)
                    VALUES (%s, %s, %s, 'files')
                """, (patient_id, 'quiz_completion', file_record['id']))
            conn.commit()
        
        results['quiz_completion'] = {
            'is_completed': stage_completed,
            'completion_date': completion_date,
            'stage_data': json.dumps(stage_data) if stage_data else None,
            'status_message': status_message
        }
        
        # Stage 2: Initial Consult Scheduled
        print("  Validating Stage 2: Initial Consult Scheduled...")
        cursor.execute("""
            SELECT pcs.id, pcs.scheduled_datetime, pcs.notes
            FROM patients p
            LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
            WHERE p.id = %s AND LOWER(pcs.consult_type) = LOWER('sleep_expert')
        """, (patient_id,))
        result = cursor.fetchone()
        
        if result and result['id']:
            print(f"    ✅ Consultation scheduled for {result['scheduled_datetime'].strftime('%B %d, %Y')}")
            results['initial_consult_scheduled'] = {
                'is_completed': True,
                'completion_date': result['scheduled_datetime'],
                'stage_data': json.dumps({'notes': result['notes']}),
                'status_message': f"Consultation scheduled for {result['scheduled_datetime'].strftime('%B %d, %Y')}"
            }
        else:
            print(f"    ❌ No consultation scheduled")
            results['initial_consult_scheduled'] = {
                'is_completed': False,
                'completion_date': None,
                'stage_data': None,
                'status_message': 'No consultation scheduled'
            }
        
        # Stage 3: Met with Sleep Expert
        print("  Validating Stage 3: Met with Sleep Expert...")
        cursor.execute("""
            SELECT pcs.id, pcs.completed_datetime, pcs.comment
            FROM patients p
            LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
            WHERE p.id = %s AND LOWER(pcs.consult_type) = LOWER('sleep_expert') AND LOWER(pcs.status) = LOWER('completed')
        """, (patient_id,))
        result = cursor.fetchone()
        
        if result and result['id']:
            print(f"    ✅ Consultation completed on {result['completed_datetime'].strftime('%B %d, %Y')}")
            results['initial_consult_completed'] = {
                'is_completed': True,
                'completion_date': result['completed_datetime'],
                'stage_data': json.dumps({'comment': result['comment']}),
                'status_message': f"Consultation completed on {result['completed_datetime'].strftime('%B %d, %Y')}"
            }
        else:
            print(f"    ❌ Consultation not completed")
            results['initial_consult_completed'] = {
                'is_completed': False,
                'completion_date': None,
                'stage_data': None,
                'status_message': 'Consultation not completed'
            }
        
        # Stage 4: Sleep Study Scheduled
        print("  Validating Stage 4: Sleep Study Scheduled...")
        cursor.execute("""
            SELECT pcs.id, pcs.scheduled_datetime, pcs.notes
            FROM patients p
            LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
            WHERE p.id = %s AND LOWER(pcs.consult_type) = LOWER('sleep_doctor')
        """, (patient_id,))
        result = cursor.fetchone()
        
        if result and result['id']:
            print(f"    ✅ Sleep study scheduled for {result['scheduled_datetime'].strftime('%B %d, %Y')}")
            results['sleep_study_scheduled'] = {
                'is_completed': True,
                'completion_date': result['scheduled_datetime'],
                'stage_data': json.dumps({'notes': result['notes']}),
                'status_message': f"Sleep study scheduled for {result['scheduled_datetime'].strftime('%B %d, %Y')}"
            }
        else:
            # Check if sleep test files exist - if so, auto-complete this stage
            cursor.execute("""
                SELECT f.id, f.name, f.upload_date, f.file_type
                FROM files f
                WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('sleep-test')
            """, (patient_id,))
            sleep_test_files = cursor.fetchall()
            
            if sleep_test_files:
                print(f"    ✅ Auto-completing: Sleep test files found - must have been scheduled")
                results['sleep_study_scheduled'] = {
                    'is_completed': True,
                    'completion_date': min(file['upload_date'] for file in sleep_test_files),
                    'stage_data': json.dumps({
                        'auto_completed': True,
                        'reason': 'sleep_test_files_uploaded',
                        'file_count': len(sleep_test_files)
                    }),
                    'status_message': f"Auto-completed: Sleep test files uploaded ({len(sleep_test_files)} files) - study must have been scheduled"
                }
            else:
                print(f"    ❌ Sleep study not scheduled")
                results['sleep_study_scheduled'] = {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': 'Sleep study not scheduled'
                }
        
        # Stage 5: Sleep Test Completed
        print("  Validating Stage 5: Sleep Test Completed...")
        cursor.execute("""
            SELECT f.id, f.name, f.upload_date, f.file_type
            FROM files f
            WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('sleep-test')
        """, (patient_id,))
        results_list = cursor.fetchall()
        
        if results_list:
            print(f"    ✅ Sleep test files found: {len(results_list)} files")
            
            # Create stage_file_links for sleep test files
            for file_record in results_list:
                cursor.execute("""
                    INSERT IGNORE INTO stage_file_links (patient_id, stage_key, file_id, file_table)
                    VALUES (%s, %s, %s, 'files')
                """, (patient_id, 'sleep_test_completed', file_record['id']))
            conn.commit()
            
            results['sleep_test_completed'] = {
                'is_completed': True,
                'completion_date': results_list[0]['upload_date'],
                'stage_data': json.dumps([{
                    'file_id': r['id'],
                    'file_name': r['name'],
                    'file_type': r['file_type'],
                    'upload_date': r['upload_date'].isoformat() if r['upload_date'] else None
                } for r in results_list]),
                'status_message': f"Sleep test files uploaded: {len(results_list)} files"
            }
        else:
            print(f"    ❌ No sleep test files found")
            results['sleep_test_completed'] = {
                'is_completed': False,
                'completion_date': None,
                'stage_data': None,
                'status_message': 'No sleep test files found'
            }
        
        # Stage 6: Schedule Sleep Test Review
        print("  Validating Stage 6: Schedule Sleep Test Review...")
        cursor.execute("""
            SELECT pcs.id, pcs.scheduled_datetime, pcs.notes
            FROM patients p
            LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
            WHERE p.id = %s AND LOWER(pcs.consult_type) = LOWER('sleep_doctor') AND LOWER(pcs.status) = LOWER('scheduled')
        """, (patient_id,))
        result = cursor.fetchone()
        
        if result and result['id']:
            print(f"    ✅ Sleep test review scheduled for {result['scheduled_datetime'].strftime('%B %d, %Y')}")
            results['schedule_sleep_test_review'] = {
                'is_completed': True,
                'completion_date': result['scheduled_datetime'],
                'stage_data': json.dumps({'notes': result['notes']}),
                'status_message': f"Sleep test review scheduled for {result['scheduled_datetime'].strftime('%B %d, %Y')}"
            }
        else:
            # Check if sleep test files exist - if so, auto-complete this stage
            cursor.execute("""
                SELECT f.id, f.name, f.upload_date, f.file_type
                FROM files f
                WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('sleep-test')
            """, (patient_id,))
            sleep_test_files = cursor.fetchall()
            
            if sleep_test_files:
                print(f"    ✅ Auto-completing: Sleep test files found - review must have been scheduled")
                results['schedule_sleep_test_review'] = {
                    'is_completed': True,
                    'completion_date': min(file['upload_date'] for file in sleep_test_files),
                    'stage_data': json.dumps({
                        'auto_completed': True,
                        'reason': 'sleep_test_files_uploaded',
                        'file_count': len(sleep_test_files)
                    }),
                    'status_message': f"Auto-completed: Sleep test files uploaded ({len(sleep_test_files)} files) - review must have been scheduled"
                }
            else:
                print(f"    ❌ Sleep test review not scheduled")
                results['schedule_sleep_test_review'] = {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': 'Sleep test review not scheduled'
                }
        
        # Stage 7: Sleep Doctor Followup Completed
        print("  Validating Stage 7: Sleep Doctor Followup Completed...")
        cursor.execute("""
            SELECT pcs.id, pcs.completed_datetime, pcs.comment
            FROM patients p
            LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
            WHERE p.id = %s AND LOWER(pcs.consult_type) = LOWER('ep_doctor') AND LOWER(pcs.status) = LOWER('completed')
        """, (patient_id,))
        result = cursor.fetchone()
        
        if result and result['id']:
            print(f"    ✅ Sleep doctor followup completed on {result['completed_datetime'].strftime('%B %d, %Y')}")
            results['sleep_doctor_followup_completed'] = {
                'is_completed': True,
                'completion_date': result['completed_datetime'],
                'stage_data': json.dumps({'comment': result['comment']}),
                'status_message': f"Sleep doctor followup completed on {result['completed_datetime'].strftime('%B %d, %Y')}"
            }
        else:
            # Check if sleep test files exist - if so, auto-complete this stage
            cursor.execute("""
                SELECT f.id, f.name, f.upload_date, f.file_type
                FROM files f
                WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('sleep-test')
            """, (patient_id,))
            sleep_test_files = cursor.fetchall()
            
            if sleep_test_files:
                print(f"    ✅ Auto-completing: Sleep test files found - followup must have been completed")
                results['sleep_doctor_followup_completed'] = {
                    'is_completed': True,
                    'completion_date': min(file['upload_date'] for file in sleep_test_files),
                    'stage_data': json.dumps({
                        'auto_completed': True,
                        'reason': 'sleep_test_files_uploaded',
                        'file_count': len(sleep_test_files)
                    }),
                    'status_message': f"Auto-completed: Sleep test files uploaded ({len(sleep_test_files)} files) - followup must have been completed"
                }
            else:
                print(f"    ❌ Sleep doctor followup not completed")
                results['sleep_doctor_followup_completed'] = {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': 'Sleep doctor followup not completed'
                }
        
        # Stage 8: Dental Sleep Doctor Consult Scheduled
        print("  Validating Stage 8: Dental Sleep Doctor Consult Scheduled...")
        cursor.execute("""
            SELECT pcs.id, pcs.scheduled_datetime, pcs.notes
            FROM patients p
            LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
            WHERE p.id = %s 
              AND LOWER(pcs.consult_type) IN (LOWER('dental_sleep_doctor'), LOWER('dental_sleep_doctor_consult'))
        """, (patient_id,))
        result = cursor.fetchone()
        
        if result and result['id']:
            print(f"    ✅ Dental sleep doctor consult scheduled for {result['scheduled_datetime'].strftime('%B %d, %Y')}")
            results['dental_sleep_doctor_consult_scheduled'] = {
                'is_completed': True,
                'completion_date': result['scheduled_datetime'],
                'stage_data': json.dumps({'notes': result['notes']}),
                'status_message': f"Dental sleep doctor consult scheduled for {result['scheduled_datetime'].strftime('%B %d, %Y')}"
            }
        else:
            # Check if intraoral scan files exist - if so, auto-complete this stage
            cursor.execute("""
                SELECT f.id, f.name, f.upload_date, f.file_type
                FROM files f
                WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('intraoral-scan')
            """, (patient_id,))
            intraoral_files = cursor.fetchall()
            
            # Check if CBCT observation files exist - if so, auto-complete this stage
            cursor.execute("""
                SELECT af.id, af.name, af.upload_date, af.file_type
                FROM adminfiles af
                WHERE af.patient_id = %s AND LOWER(af.file_category) = LOWER('cbct observations')
            """, (patient_id,))
            cbct_files = cursor.fetchall()
            
            if intraoral_files or cbct_files:
                auto_completion_reason = []
                completion_date = None
                
                if intraoral_files:
                    auto_completion_reason.append(f"intraoral scans ({len(intraoral_files)} files)")
                    if not completion_date:
                        completion_date = min(file['upload_date'] for file in intraoral_files)
                
                if cbct_files:
                    auto_completion_reason.append(f"CBCT observations ({len(cbct_files)} files)")
                    if not completion_date:
                        completion_date = min(file['upload_date'] for file in cbct_files)
                
                print(f"    ✅ Auto-completing: {', '.join(auto_completion_reason)} found - consult must have been scheduled")
                results['dental_sleep_doctor_consult_scheduled'] = {
                    'is_completed': True,
                    'completion_date': completion_date,
                    'stage_data': json.dumps({
                        'auto_completed': True,
                        'reason': 'dental_files_uploaded',
                        'intraoral_file_count': len(intraoral_files),
                        'cbct_file_count': len(cbct_files)
                    }),
                    'status_message': f"Auto-completed: {', '.join(auto_completion_reason)} uploaded - consult must have been scheduled"
                }
            else:
                print(f"    ❌ Dental sleep doctor consult not scheduled")
                results['dental_sleep_doctor_consult_scheduled'] = {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': 'Dental sleep doctor consult not scheduled'
                }
        
        # Stage 9: CBCT Observation Report Uploaded
        print("  Validating Stage 9: CBCT Observation Report Uploaded...")
        cursor.execute("""
            SELECT af.id, af.name, af.upload_date, af.file_type
            FROM adminfiles af
            WHERE af.patient_id = %s AND LOWER(af.file_category) = LOWER('cbct observations')
        """, (patient_id,))
        results_list = cursor.fetchall()
        
        if results_list:
            print(f"    ✅ CBCT observation files found: {len(results_list)} files")
            
            # Create stage_file_links for CBCT observation files
            for file_record in results_list:
                cursor.execute("""
                    INSERT IGNORE INTO stage_file_links (patient_id, stage_key, file_id, file_table)
                    VALUES (%s, %s, %s, 'adminfiles')
                """, (patient_id, 'cbct_observation_report_uploaded', file_record['id']))
            conn.commit()
            
            results['cbct_observation_report_uploaded'] = {
                'is_completed': True,
                'completion_date': results_list[0]['upload_date'],
                'stage_data': json.dumps([{
                    'file_id': r['id'],
                    'file_name': r['name'],
                    'file_type': r['file_type'],
                    'upload_date': r['upload_date'].isoformat() if r['upload_date'] else None
                } for r in results_list]),
                'status_message': f"CBCT observation files uploaded: {len(results_list)} files"
            }
        else:
            print(f"    ❌ No CBCT observation files found")
            results['cbct_observation_report_uploaded'] = {
                'is_completed': False,
                'completion_date': None,
                'stage_data': None,
                'status_message': 'No CBCT observation files found'
            }
        
        # Stage 10: Intraoral Scan Uploaded
        print("  Validating Stage 10: Intraoral Scan Uploaded...")
        cursor.execute("""
            SELECT f.id, f.name, f.upload_date, f.file_type
            FROM files f
            WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('intraoral-scan')
        """, (patient_id,))
        results_list = cursor.fetchall()
        
        if results_list:
            print(f"    ✅ Intraoral scan files found: {len(results_list)} files")
            
            # Create stage_file_links for intraoral scan files
            for file_record in results_list:
                cursor.execute("""
                    INSERT IGNORE INTO stage_file_links (patient_id, stage_key, file_id, file_table)
                    VALUES (%s, %s, %s, 'files')
                """, (patient_id, 'intraoral_scan_uploaded', file_record['id']))
            conn.commit()
            
            results['intraoral_scan_uploaded'] = {
                'is_completed': True,
                'completion_date': results_list[0]['upload_date'],
                'stage_data': json.dumps([{
                    'file_id': r['id'],
                    'file_name': r['name'],
                    'file_type': r['file_type'],
                    'upload_date': r['upload_date'].isoformat() if r['upload_date'] else None
                } for r in results_list]),
                'status_message': f"Intraoral scan files uploaded: {len(results_list)} files"
            }
        else:
            print(f"    ❌ No intraoral scan files found")
            results['intraoral_scan_uploaded'] = {
                'is_completed': False,
                'completion_date': None,
                'stage_data': None,
                'status_message': 'No intraoral scan files found'
            }
        
        # Stage 11: HIPAA Consent Signed
        print("  Validating Stage 11: HIPAA Consent Signed...")
        cursor.execute("""
            SELECT f.id, f.name, f.upload_date, f.file_type
            FROM files f
            WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('billing') AND (
                LOWER(f.name) LIKE '%hipaa%' OR 
                LOWER(f.name) LIKE '%consent%' OR
                LOWER(f.name) LIKE '%authorization%'
            )
        """, (patient_id,))
        results_list = cursor.fetchall()
        
        if results_list:
            print(f"    ✅ HIPAA consent files found: {len(results_list)} files")
            
            # Create stage_file_links for HIPAA consent files
            for file_record in results_list:
                cursor.execute("""
                    INSERT IGNORE INTO stage_file_links (patient_id, stage_key, file_id, file_table)
                    VALUES (%s, %s, %s, 'files')
                """, (patient_id, 'hipaa_consent_signed', file_record['id']))
            conn.commit()
            
            results['hipaa_consent_signed'] = {
                'is_completed': True,
                'completion_date': results_list[0]['upload_date'],
                'stage_data': json.dumps([{
                    'file_id': r['id'],
                    'file_name': r['name'],
                    'file_type': r['file_type'],
                    'upload_date': r['upload_date'].isoformat() if r['upload_date'] else None
                } for r in results_list]),
                'status_message': f"HIPAA consent files uploaded: {len(results_list)} files"
            }
        else:
            print(f"    ❌ No HIPAA consent files found")
            results['hipaa_consent_signed'] = {
                'is_completed': False,
                'completion_date': None,
                'stage_data': None,
                'status_message': 'HIPAA consent forms not found - please upload files with "HIPAA" or "consent" in filename under billing category'
            }
        
        # Stage 12: Patient Completes Consult with Dental Sleep Expert
        print("  Validating Stage 12: Patient Completes Consult with Dental Sleep Expert...")
        cursor.execute("""
            SELECT pcs.id, pcs.completed_datetime, pcs.comment
            FROM patient_consult_schedule pcs
            WHERE pcs.patient_id = %s 
              AND LOWER(pcs.consult_type) IN (LOWER('dental_sleep_doctor'), LOWER('dental_sleep_doctor_consult'))
              AND LOWER(pcs.status) = LOWER('completed')
        """, (patient_id,))
        result = cursor.fetchone()
        
        if result and result['id']:
            print(f"    ✅ Dental sleep expert consultation completed on {result['completed_datetime'].strftime('%B %d, %Y')}")
            results['met_with_dental_sleep_expert'] = {
                'is_completed': True,
                'completion_date': result['completed_datetime'],
                'stage_data': json.dumps({'comment': result['comment']}),
                'status_message': f"Dental sleep expert consultation completed on {result['completed_datetime'].strftime('%B %d, %Y')}"
            }
        else:
            # Check if intraoral scan files exist - if so, auto-complete this stage
            cursor.execute("""
                SELECT f.id, f.name, f.upload_date, f.file_type
                FROM files f
                WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('intraoral-scan')
            """, (patient_id,))
            intraoral_files = cursor.fetchall()
            
            # Check if OSA report files exist - if so, auto-complete this stage
            cursor.execute("""
                SELECT af.id, af.name, af.upload_date, af.file_type
                FROM adminfiles af
                WHERE af.patient_id = %s AND LOWER(af.file_category) LIKE LOWER('%patient report%') AND af.is_public = 1
            """, (patient_id,))
            osa_report_files = cursor.fetchall()
            
            if intraoral_files or osa_report_files:
                auto_completion_reason = []
                completion_date = None
                
                if intraoral_files:
                    auto_completion_reason.append(f"intraoral scans ({len(intraoral_files)} files)")
                    if not completion_date:
                        completion_date = min(file['upload_date'] for file in intraoral_files)
                
                if osa_report_files:
                    auto_completion_reason.append(f"OSA reports ({len(osa_report_files)} files)")
                    if not completion_date:
                        completion_date = min(file['upload_date'] for file in osa_report_files)
                
                print(f"    ✅ Auto-completing: {', '.join(auto_completion_reason)} found - consult must have been completed")
                results['met_with_dental_sleep_expert'] = {
                    'is_completed': True,
                    'completion_date': completion_date,
                    'stage_data': json.dumps({
                        'auto_completed': True,
                        'reason': 'dental_files_uploaded',
                        'intraoral_file_count': len(intraoral_files),
                        'osa_report_file_count': len(osa_report_files)
                    }),
                    'status_message': f"Auto-completed: {', '.join(auto_completion_reason)} uploaded - consult must have been completed"
                }
            else:
                print(f"    ❌ Dental sleep expert consultation not completed")
                results['met_with_dental_sleep_expert'] = {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': 'Dental sleep expert consultation not completed'
                }
        
        # Stage 13: Patient OSA Report Ready
        print("  Validating Stage 13: Patient OSA Report Ready...")
        cursor.execute("""
            SELECT af.id, af.name, af.upload_date, af.file_type
            FROM adminfiles af
            WHERE af.patient_id = %s AND LOWER(af.file_category) LIKE LOWER('%patient report%') AND af.is_public = 1
        """, (patient_id,))
        results_list = cursor.fetchall()
        
        if results_list:
            print(f"    ✅ OSA report files found: {len(results_list)} files")
            
            # Create stage_file_links for OSA report files
            for file_record in results_list:
                cursor.execute("""
                    INSERT IGNORE INTO stage_file_links (patient_id, stage_key, file_id, file_table)
                    VALUES (%s, %s, %s, 'adminfiles')
                """, (patient_id, 'osa_report_ready', file_record['id']))
            conn.commit()
            
            results['osa_report_ready'] = {
                'is_completed': True,
                'completion_date': results_list[0]['upload_date'],
                'stage_data': json.dumps([{
                    'file_id': r['id'],
                    'file_name': r['name'],
                    'file_type': r['file_type'],
                    'upload_date': r['upload_date'].isoformat() if r['upload_date'] else None
                } for r in results_list]),
                'status_message': f"OSA report files uploaded: {len(results_list)} files"
            }
        else:
            print(f"    ❌ No OSA report files found")
            results['osa_report_ready'] = {
                'is_completed': False,
                'completion_date': None,
                'stage_data': None,
                'status_message': 'No OSA report files found - please upload files with "patient report" in category'
            }
        
        # Stage 14: Dental Approval for OSA Report
        print("  Validating Stage 14: Dental Approval for OSA Report...")
        
        # First, get the OSA report file ID from Stage 13
        osa_report_file_id = None
        if results.get('osa_report_ready', {}).get('is_completed'):
            try:
                stage_13_data = json.loads(results['osa_report_ready']['stage_data'])
                if isinstance(stage_13_data, list) and len(stage_13_data) > 0:
                    osa_report_file_id = stage_13_data[0]['file_id']
                    print(f"    Found OSA report file ID: {osa_report_file_id}")
            except (json.JSONDecodeError, KeyError, IndexError):
                print(f"    Could not extract OSA report file ID from Stage 13 data")
        
        if osa_report_file_id:
            cursor.execute("""
                SELECT dra.id, dra.patient_id, dra.report_id, dra.approval_status, dra.dentist_id, dra.approval_timestamp, dra.notes
                FROM dentist_report_approval dra
                WHERE dra.patient_id = %s AND dra.report_id = %s AND dra.approval_status = 'approved'
            """, (patient_id, osa_report_file_id))
            result = cursor.fetchone()
            
            if result and result['id']:
                print(f"    ✅ Dental approval found for report {osa_report_file_id} - approved on {result['approval_timestamp'].strftime('%B %d, %Y')}")
                results['dental_approval_osa_report'] = {
                    'is_completed': True,
                    'completion_date': result['approval_timestamp'],
                    'stage_data': json.dumps({
                        'approval_id': result['id'],
                        'report_id': result['report_id'],
                        'dentist_id': result['dentist_id'],
                        'notes': result['notes']
                    }),
                    'status_message': f"Dental approval completed on {result['approval_timestamp'].strftime('%B %d, %Y')} for report {osa_report_file_id}"
                }
            else:
                print(f"    ❌ No dental approval found for report {osa_report_file_id}")
                results['dental_approval_osa_report'] = {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': f'No dental approval found for OSA report {osa_report_file_id}'
                }
        else:
            print(f"    ❌ No OSA report found in Stage 13 - cannot check dental approval")
            results['dental_approval_osa_report'] = {
                'is_completed': False,
                'completion_date': None,
                'stage_data': None,
                'status_message': 'No OSA report found in Stage 13 - cannot check dental approval'
            }
        
        # Stage 15: Order Oral Appliance
        print("  Validating Stage 15: Order Oral Appliance...")
        cursor.execute("""
            SELECT pdo.id, pdo.device_type, pdo.device_name, pdo.order_date, pdo.status, pdo.notes
            FROM patient_device_order pdo
            WHERE pdo.patient_id = %s AND LOWER(pdo.device_type) = LOWER('oral_appliance')
        """, (patient_id,))
        result = cursor.fetchone()
        
        if result and result['id']:
            print(f"    ✅ Oral appliance ordered on {result['order_date'].strftime('%B %d, %Y')} - Status: {result['status']}")
            results['order_oral_appliance'] = {
                'is_completed': True,
                'completion_date': result['order_date'],
                'stage_data': json.dumps({
                    'order_id': result['id'],
                    'device_type': result['device_type'],
                    'device_name': result['device_name'],
                    'status': result['status'],
                    'notes': result['notes']
                }),
                'status_message': f"Oral appliance ordered on {result['order_date'].strftime('%B %d, %Y')} - Status: {result['status']}"
            }
        else:
            print(f"    ❌ No oral appliance order found")
            results['order_oral_appliance'] = {
                'is_completed': False,
                'completion_date': None,
                'stage_data': None,
                'status_message': 'No oral appliance order found'
            }
        
        # Stage 16: Device Delivered to Dental Office
        print("  Validating Stage 16: Device Delivered to Dental Office...")
        cursor.execute("""
            SELECT pdo.id, pdo.device_type, pdo.device_name, pdo.order_date, pdo.arrival_date, pdo.status, pdo.notes
            FROM patient_device_order pdo
            WHERE pdo.patient_id = %s AND LOWER(pdo.device_type) = LOWER('oral_appliance') AND pdo.status = 'delivered'
        """, (patient_id,))
        result = cursor.fetchone()
        
        if result and result['id']:
            print(f"    ✅ Device delivered on {result['arrival_date'].strftime('%B %d, %Y')} - Status: {result['status']}")
            results['device_delivered'] = {
                'is_completed': True,
                'completion_date': result['arrival_date'],
                'stage_data': json.dumps({
                    'order_id': result['id'],
                    'device_type': result['device_type'],
                    'device_name': result['device_name'],
                    'order_date': result['order_date'].isoformat() if result['order_date'] else None,
                    'arrival_date': result['arrival_date'].isoformat() if result['arrival_date'] else None,
                    'status': result['status'],
                    'notes': result['notes']
                }),
                'status_message': f"Device delivered to dental office on {result['arrival_date'].strftime('%B %d, %Y')}"
            }
        else:
            print(f"    ❌ Device not delivered yet")
            results['device_delivered'] = {
                'is_completed': False,
                'completion_date': None,
                'stage_data': None,
                'status_message': 'Device not delivered to dental office yet'
            }
        
        # Stage 17: Schedule Oral Appliance Delivery
        print("  Validating Stage 17: Schedule Oral Appliance Delivery...")
        cursor.execute("""
            SELECT pcs.id, pcs.scheduled_datetime, pcs.notes, pcs.status
            FROM patient_consult_schedule pcs
            WHERE pcs.patient_id = %s 
              AND LOWER(pcs.consult_type) IN (LOWER('oral_appliance_delivery'), LOWER('appliance_delivery'))
        """, (patient_id,))
        result = cursor.fetchone()
        
        if result and result['id']:
            print(f"    ✅ Oral appliance delivery scheduled for {result['scheduled_datetime'].strftime('%B %d, %Y')} - Status: {result['status']}")
            results['schedule_oral_appliance_delivery'] = {
                'is_completed': True,
                'completion_date': result['scheduled_datetime'],
                'stage_data': json.dumps({
                    'consult_id': result['id'],
                    'scheduled_datetime': result['scheduled_datetime'].isoformat() if result['scheduled_datetime'] else None,
                    'status': result['status'],
                    'notes': result['notes']
                }),
                'status_message': f"Oral appliance delivery scheduled for {result['scheduled_datetime'].strftime('%B %d, %Y')}"
            }
        else:
            print(f"    ❌ Oral appliance delivery not scheduled")
            results['schedule_oral_appliance_delivery'] = {
                'is_completed': False,
                'completion_date': None,
                'stage_data': None,
                'status_message': 'Oral appliance delivery not scheduled - please schedule delivery appointment'
            }
        
        # Stage 18: Oral Appliance Delivery Completed
        print("  Validating Stage 18: Oral Appliance Delivery Completed...")
        cursor.execute("""
            SELECT pcs.id, pcs.scheduled_datetime, pcs.completed_datetime, pcs.comment, pcs.status
            FROM patient_consult_schedule pcs
            WHERE pcs.patient_id = %s 
              AND LOWER(pcs.consult_type) IN (LOWER('oral_appliance_delivery'), LOWER('appliance_delivery'))
              AND LOWER(pcs.status) = LOWER('completed')
        """, (patient_id,))
        result = cursor.fetchone()
        
        if result and result['id']:
            print(f"    ✅ Oral appliance delivery completed on {result['completed_datetime'].strftime('%B %d, %Y')}")
            results['oral_appliance_delivery_completed'] = {
                'is_completed': True,
                'completion_date': result['completed_datetime'],
                'stage_data': json.dumps({
                    'consult_id': result['id'],
                    'scheduled_datetime': result['scheduled_datetime'].isoformat() if result['scheduled_datetime'] else None,
                    'completed_datetime': result['completed_datetime'].isoformat() if result['completed_datetime'] else None,
                    'status': result['status'],
                    'comment': result['comment']
                }),
                'status_message': f"Oral appliance delivery completed on {result['completed_datetime'].strftime('%B %d, %Y')}"
            }
        else:
            print(f"    ❌ Oral appliance delivery not completed")
            results['oral_appliance_delivery_completed'] = {
                'is_completed': False,
                'completion_date': None,
                'stage_data': None,
                'status_message': 'Oral appliance delivery not completed - please mark appointment as completed'
            }
        
        # Stage 19: Follow Up Sleep Test After Delivery
        print("  Validating Stage 19: Follow Up Sleep Test After Delivery...")
        
        # First, get the delivery date from Stage 18
        delivery_date = None
        if results.get('oral_appliance_delivery_completed', {}).get('is_completed'):
            delivery_date = results['oral_appliance_delivery_completed']['completion_date']
            print(f"    Found delivery date: {delivery_date.strftime('%B %d, %Y')}")
        
        if delivery_date:
            cursor.execute("""
                SELECT f.id, f.name, f.upload_date, f.file_type
                FROM files f
                WHERE f.patient_id = %s AND LOWER(f.subcategory) = LOWER('sleep-test') AND f.upload_date > %s
                ORDER BY f.upload_date DESC
                LIMIT 1
            """, (patient_id, delivery_date))
            result = cursor.fetchone()
            
            if result and result['id']:
                print(f"    ✅ Follow-up sleep test found - uploaded on {result['upload_date'].strftime('%B %d, %Y')} (after delivery)")
                
                # Create stage_file_links for follow-up sleep test files
                cursor.execute("""
                    INSERT IGNORE INTO stage_file_links (patient_id, stage_key, file_id, file_table)
                    VALUES (%s, %s, %s, 'files')
                """, (patient_id, 'follow_up_sleep_test_after_delivery', result['id']))
                conn.commit()
                
                results['follow_up_sleep_test_after_delivery'] = {
                    'is_completed': True,
                    'completion_date': result['upload_date'],
                    'stage_data': json.dumps({
                        'file_id': result['id'],
                        'file_name': result['name'],
                        'file_type': result['file_type'],
                        'upload_date': result['upload_date'].isoformat() if result['upload_date'] else None,
                        'delivery_date': delivery_date.isoformat() if delivery_date else None
                    }),
                    'status_message': f"Follow-up sleep test uploaded on {result['upload_date'].strftime('%B %d, %Y')} (after device delivery)"
                }
            else:
                print(f"    ❌ No follow-up sleep test found after delivery date")
                results['follow_up_sleep_test_after_delivery'] = {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': f'No follow-up sleep test found after delivery date {delivery_date.strftime("%B %d, %Y")}'
                }
        else:
            print(f"    ❌ No delivery date found from Stage 18 - cannot check follow-up sleep test")
            results['follow_up_sleep_test_after_delivery'] = {
                'is_completed': False,
                'completion_date': None,
                'stage_data': None,
                'status_message': 'No delivery date found from Stage 18 - cannot check follow-up sleep test'
            }
        
        print(f"=== Validation completed for patient {patient_id} ===")
        
        # Commit all changes including stage_file_links
        conn.commit()
        print("✅ All changes committed to database")
        
        return results
        
    except Exception as e:
        print(f"Error validating stages: {e}")
        return None
    finally:
        if conn:
            cursor.close()
            conn.close()

def update_manifest_from_validation(patient_id: int, validation_results: Dict[str, Any]) -> bool:
    """
    Update the patient_manifest table with validation results
    """
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        # Use local manifest definition
        manifest_def = MANIFEST_DEFINITION
        
        for stage_key, result in validation_results.items():
            # Find the stage definition
            stage_def = None
            for stage in manifest_def:
                if stage['key'] == stage_key:
                    stage_def = stage
                    break
            
            if not stage_def:
                print(f"Warning: No manifest definition found for stage {stage_key}")
                continue
            
            # Check if entry exists
            cursor.execute("""
                SELECT id FROM patient_manifest 
                WHERE patient_id = %s AND stage_key = %s
            """, (patient_id, stage_key))
            
            existing = cursor.fetchone()
            
            if existing:
                # Update existing entry
                cursor.execute("""
                    UPDATE patient_manifest 
                    SET is_completed = %s, completion_date = %s, 
                        stage_data = %s, status_message = %s,
                        updated_at = NOW()
                    WHERE patient_id = %s AND stage_key = %s
                """, (
                    result['is_completed'],
                    result['completion_date'],
                    result['stage_data'],
                    result['status_message'],
                    patient_id,
                    stage_key
                ))
            else:
                # Insert new entry
                cursor.execute("""
                    INSERT INTO patient_manifest 
                    (patient_id, stage_key, stage_number, stage_name, 
                     is_completed, completion_date, stage_data, status_message, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (
                    patient_id,
                    stage_key,
                    stage_def['stage_number'],
                    stage_def['stage_name'],
                    result['is_completed'],
                    result['completion_date'],
                    result['stage_data'],
                    result['status_message']
                ))
        
        conn.commit()
        print(f"Manifest updated successfully for patient {patient_id}")
        return True
        
    except Exception as e:
        print(f"Error updating manifest: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

class TestManifestValidation(unittest.TestCase):
    """Test cases for manifest validation functions"""
    
    def setUp(self):
        """Set up test database connection"""
        self.test_patient_id = 15927
    
    def test_validate_quiz_completion(self):
        """Test quiz completion validation"""
        results = validate_patient_stages(self.test_patient_id)
        self.assertIsNotNone(results)
        self.assertIn('quiz_completion', results)
        
        quiz_result = results['quiz_completion']
        self.assertIsInstance(quiz_result['is_completed'], bool)
        self.assertIsInstance(quiz_result['status_message'], str)
    
    def test_validate_initial_consult_scheduled(self):
        """Test initial consult scheduled validation"""
        results = validate_patient_stages(self.test_patient_id)
        self.assertIsNotNone(results)
        self.assertIn('initial_consult_scheduled', results)
        
        consult_result = results['initial_consult_scheduled']
        self.assertIsInstance(consult_result['is_completed'], bool)
        self.assertIsInstance(consult_result['status_message'], str)
    
    def test_validate_met_with_sleep_expert(self):
        """Test met with sleep expert validation"""
        results = validate_patient_stages(self.test_patient_id)
        self.assertIsNotNone(results)
        self.assertIn('initial_consult_completed', results)
        
        expert_result = results['initial_consult_completed']
        self.assertIsInstance(expert_result['is_completed'], bool)
        self.assertIsInstance(expert_result['status_message'], str)
    
    def test_validate_sleep_test_completed(self):
        """Test sleep test completed validation"""
        results = validate_patient_stages(self.test_patient_id)
        self.assertIsNotNone(results)
        self.assertIn('sleep_test_completed', results)
        
        test_result = results['sleep_test_completed']
        self.assertIsInstance(test_result['is_completed'], bool)
        self.assertIsInstance(test_result['status_message'], str)
    
    def test_validate_dental_sleep_doctor_consult_scheduled(self):
        """Test dental sleep doctor consult scheduled validation"""
        results = validate_patient_stages(self.test_patient_id)
        self.assertIsNotNone(results)
        self.assertIn('dental_sleep_doctor_consult_scheduled', results)
        consult_result = results['dental_sleep_doctor_consult_scheduled']
        self.assertIsInstance(consult_result['is_completed'], bool)
        self.assertIsInstance(consult_result['status_message'], str)
    
    def test_validate_cbct_observation_report_uploaded(self):
        """Test CBCT observation report uploaded validation"""
        results = validate_patient_stages(self.test_patient_id)
        self.assertIsNotNone(results)
        self.assertIn('cbct_observation_report_uploaded', results)
        
        cbct_result = results['cbct_observation_report_uploaded']
        self.assertIsInstance(cbct_result['is_completed'], bool)
        self.assertIsInstance(cbct_result['status_message'], str)
    
    def test_validate_intraoral_scan_uploaded(self):
        """Test intraoral scan uploaded validation"""
        results = validate_patient_stages(self.test_patient_id)
        self.assertIsNotNone(results)
        self.assertIn('intraoral_scan_uploaded', results)
        
        intraoral_result = results['intraoral_scan_uploaded']
        self.assertIsInstance(intraoral_result['is_completed'], bool)
        self.assertIsInstance(intraoral_result['status_message'], str)
    
    def test_validate_hipaa_consent_signed(self):
        """Test HIPAA consent signed validation"""
        results = validate_patient_stages(self.test_patient_id)
        self.assertIsNotNone(results)
        self.assertIn('hipaa_consent_signed', results)
        
        hipaa_result = results['hipaa_consent_signed']
        self.assertIsInstance(hipaa_result['is_completed'], bool)
        self.assertIsInstance(hipaa_result['status_message'], str)
    
    def test_all_stages_present(self):
        """Test that all 11 stages are present in results"""
        results = validate_patient_stages(self.test_patient_id)
        self.assertIsNotNone(results)
        
        expected_stages = [
            'quiz_completion',
            'initial_consult_scheduled',
            'initial_consult_completed',
            'sleep_study_scheduled',
            'sleep_test_completed',
            'schedule_sleep_test_review',
            'sleep_doctor_followup_completed',
            'dental_sleep_doctor_consult_scheduled',
            'cbct_observation_report_uploaded',
            'intraoral_scan_uploaded',
            'hipaa_consent_signed',
            'met_with_dental_sleep_expert',
            'osa_report_ready',
            'dental_approval_osa_report'
        ]
        
        for stage in expected_stages:
            self.assertIn(stage, results, f"Stage {stage} not found in results")
        
        self.assertEqual(len(results), 14, f"Expected 14 stages, got {len(results)}")
    
    def test_update_manifest_from_validation(self):
        """Test updating manifest from validation results"""
        results = validate_patient_stages(self.test_patient_id)
        self.assertIsNotNone(results)
        
        success = update_manifest_from_validation(self.test_patient_id, results)
        self.assertIsInstance(success, bool)

def run_validation_tests():
    """Run all validation tests"""
    print("Running manifest validation tests...")
    unittest.main(argv=[''], exit=False, verbosity=2)

# Example usage
if __name__ == "__main__":
    patient_id = 10299
        
    
    print("=" * 60)
    print(f"RUNNING MANIFEST VALIDATION FOR PATIENT {patient_id}")
    print("=" * 60)
    
    # Validate stages (ONCE ONLY)
    results = validate_patient_stages(patient_id)
    
    if results:
        print("\n=== FINAL VALIDATION RESULTS ===")
        completed_count = 0
        for stage_key, result in results.items():
            status = "✅ COMPLETED" if result['is_completed'] else "❌ NOT COMPLETED"
            print(f"{stage_key}: {status}")
            print(f"  Message: {result['status_message']}")
            if result['completion_date']:
                print(f"  Date: {result['completion_date']}")
                completed_count += 1
        print(f"\nSUMMARY: {completed_count}/19 stages completed")
        print("=== END RESULTS ===")
        
        # Update manifest table
        print("\n" + "=" * 60)
        print("UPDATING PATIENT_MANIFEST TABLE")
        print("=" * 60)
        
        success = update_manifest_from_validation(patient_id, results)
        
        if success:
            print("✅ Manifest table updated successfully!")
        else:
            print("❌ Failed to update manifest table")
    else:
        print("❌ Validation failed - no results returned") 