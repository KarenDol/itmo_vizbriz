"""
Manifest Validator Service - Integrated version for patient workflow
This service provides auto-completion logic for patient manifest stages
based on uploaded files and database records.
"""

from flask_app import db
from flask_app.models import PatientManifest, Patient, PatientConsultSchedule, PatientDeviceOrder, File, AdminFile, DentistReportApproval
from sqlalchemy import text
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)

class ManifestValidatorService:
    """Service for validating and auto-completing patient manifest stages"""
    
    @staticmethod
    def filter_already_sent_email_actions(patient_id, eligible_actions):
        """Filter out email actions that have already been sent to avoid duplicates"""
        try:
            # Define mapping of action_key to email_type
            email_action_types = {
                'send_quiz_link': 'quiz_link_stage1',
                'remind_quiz_completion': 'reminder_quiz_completion',
                'remind_consultation_scheduling': 'reminder_consultation_scheduling', 
                'remind_sleep_study': 'reminder_sleep_study',
                'remind_document_upload': 'reminder_document_upload',
                'remind_appliance_delivery': 'reminder_appliance_delivery',
                'remind_followup_test': 'reminder_followup_test',
                'request_hipaa_consent': 'hipaa_consent_request',
                'request_cbct_files': 'cbct_request',
                'request_osa_report': 'osa_report_request'
            }
            
            # Get all sent emails for this patient
            from flask_app import db
            from sqlalchemy import text
            
            sent_emails = db.session.execute(
                text("""
                    SELECT DISTINCT el.email_type, el.sent_at
                    FROM email_logs el
                    WHERE el.patient_id = :patient_id
                    ORDER BY el.sent_at DESC
                """),
                {'patient_id': patient_id}
            ).fetchall()
            
            sent_email_types = {email.email_type for email in sent_emails}
            
            # Filter out actions that have already been sent
            filtered_actions = []
            for action in eligible_actions:
                action_key = action.get('action_key', '')
                expected_email_type = email_action_types.get(action_key)
                
                # Keep the action if it's not an email action or hasn't been sent
                if not expected_email_type or expected_email_type not in sent_email_types:
                    filtered_actions.append(action)
                else:
                    import logging
                    logging.info(f"Filtered out already sent email action: {action_key} (email_type: {expected_email_type})")
            
            return filtered_actions
            
        except Exception as e:
            import logging
            logging.error(f"Error filtering email actions for patient {patient_id}: {e}")
            return eligible_actions  # Return original list if filtering fails
    
    # Manifest definition - matches the original validator
    MANIFEST_DEFINITION = [
        # Core workflow stages
        {"stage_number": 1, "stage_name": "Quiz Completion", "key": "quiz_completion"},
        {"stage_number": 1.1, "stage_name": "Quiz Link Sent", "key": "quiz_link_sent", "group": "quiz"},
        {"stage_number": 1.2, "stage_name": "Quiz Reminder Sent", "key": "quiz_reminder_sent", "group": "quiz"},
        
        {"stage_number": 2, "stage_name": "Initial Consult Scheduled", "key": "initial_consult_scheduled"},
        {"stage_number": 2.1, "stage_name": "Consultation Reminder Sent", "key": "consultation_reminder_sent", "group": "consultation"},
        
        {"stage_number": 3, "stage_name": "Met with Sleep Expert", "key": "initial_consult_completed"},
        {"stage_number": 4, "stage_name": "Sleep Study Scheduled", "key": "sleep_study_scheduled"},
        {"stage_number": 4.1, "stage_name": "Sleep Study Reminder Sent", "key": "sleep_study_reminder_sent", "group": "sleep_study"},
        
        {"stage_number": 5, "stage_name": "Sleep Test Completed", "key": "sleep_test_completed"},
        {"stage_number": 6, "stage_name": "Schedule Sleep Test Review", "key": "schedule_sleep_test_review"},
        {"stage_number": 7, "stage_name": "Sleep Doctor Followup Completed", "key": "sleep_doctor_followup_completed"},
        {"stage_number": 8, "stage_name": "Dental Sleep Doctor Consult Scheduled", "key": "dental_sleep_doctor_consult_scheduled"},
        
        {"stage_number": 9, "stage_name": "CBCT Observation Report Uploaded", "key": "cbct_observation_report_uploaded"},
        {"stage_number": 9.1, "stage_name": "CBCT Request Sent", "key": "cbct_request_sent", "group": "cbct"},
        
        {"stage_number": 10, "stage_name": "IntraOral Scan Uploaded", "key": "intraoral_scan_uploaded"},
        {"stage_number": 10.1, "stage_name": "Document Upload Reminder Sent", "key": "document_upload_reminder_sent", "group": "documents"},
        
        {"stage_number": 11, "stage_name": "HIPAA Consent Signed", "key": "hipaa_consent_signed"},
        {"stage_number": 11.1, "stage_name": "HIPAA Consent Request Sent", "key": "hipaa_consent_request_sent", "group": "hipaa"},
        
        {"stage_number": 12, "stage_name": "Patient Completes Consult with Dental Sleep Expert", "key": "met_with_dental_sleep_expert"},
        
        {"stage_number": 13, "stage_name": "Patient OSA Report Ready", "key": "osa_report_ready"},
        {"stage_number": 13.1, "stage_name": "OSA Report Request Sent", "key": "osa_report_request_sent", "group": "osa_report"},
        
        {"stage_number": 14, "stage_name": "Dental Approval for OSA Report", "key": "dental_approval_osa_report"},
        {"stage_number": 15, "stage_name": "Order Oral Appliance", "key": "order_oral_appliance"},
        {"stage_number": 16, "stage_name": "Device Delivered to Dental Office", "key": "device_delivered"},
        
        {"stage_number": 17, "stage_name": "Schedule Oral Appliance Delivery", "key": "schedule_oral_appliance_delivery"},
        {"stage_number": 18, "stage_name": "Oral Appliance Delivery Completed", "key": "oral_appliance_delivery_completed"},
        {"stage_number": 17.1, "stage_name": "Appliance Delivery Reminder Sent", "key": "appliance_delivery_reminder_sent", "group": "appliance"},
        
        {"stage_number": 19, "stage_name": "Follow Up Sleep Test After Delivery", "key": "follow_up_sleep_test_after_delivery"},
        {"stage_number": 19.1, "stage_name": "Follow-up Test Reminder Sent", "key": "followup_test_reminder_sent", "group": "followup"}
    ]
    
    @staticmethod
    def validate_and_update_patient_stages(patient_id):
        """
        Validate all stages for a patient and update manifest with auto-completion logic
        Returns dict with validation results
        """
        print(f"🔍 DEBUG: ManifestValidatorService.validate_and_update_patient_stages() CALLED for patient {patient_id}")
        logger.info(f"🔍 DEBUG: ManifestValidatorService.validate_and_update_patient_stages() CALLED for patient {patient_id}")
        try:
            logger.info(f"Starting manifest validation for patient {patient_id}")
            
            # Clear existing stage_file_links for this patient to avoid duplicates
            db.session.execute(
                text("DELETE FROM stage_file_links WHERE patient_id = :patient_id"),
                {'patient_id': patient_id}
            )
            db.session.commit()
            
            # First, check for key file groups that auto-complete multiple stages
            file_groups = ManifestValidatorService._check_file_groups(patient_id)
            logger.info(f"File groups found: {file_groups}")
            
            # Debug: Log sleep test files specifically
            sleep_test_files = file_groups.get('sleep_test_files', [])
            logger.info(f"SLEEP TEST FILES FOUND: {len(sleep_test_files)} files")
            print(f"DEBUG: SLEEP TEST FILES FOUND: {len(sleep_test_files)} files")
            for file in sleep_test_files:
                logger.info(f"  - {file.name} (uploaded: {file.upload_date})")
                print(f"DEBUG: Sleep test file: {file.name} (uploaded: {file.upload_date})")
            
            results = {}
            
            # Validate each stage with auto-completion logic
            print("DEBUG: Starting stage validation...")
            
            # Validate each stage individually to catch None returns
            quiz_result = ManifestValidatorService._validate_quiz_completion(patient_id)
            print(f"DEBUG: Quiz completion result: {quiz_result}")
            if quiz_result:
                results.update(quiz_result)
            
            # Validate email/reminder stages
            quiz_link_result = ManifestValidatorService._validate_email_stage(patient_id, 'quiz_link_sent', 'quiz_link_stage1')
            if quiz_link_result:
                results.update(quiz_link_result)
                
            quiz_reminder_result = ManifestValidatorService._validate_email_stage(patient_id, 'quiz_reminder_sent', 'reminder_quiz_completion')  
            if quiz_reminder_result:
                results.update(quiz_reminder_result)
            
            initial_consult_scheduled_result = ManifestValidatorService._validate_initial_consult_scheduled(patient_id, file_groups)
            print(f"DEBUG: Initial consult scheduled result: {initial_consult_scheduled_result}")
            if initial_consult_scheduled_result:
                results.update(initial_consult_scheduled_result)
            
            initial_consult_completed_result = ManifestValidatorService._validate_initial_consult_completed(patient_id, file_groups)
            print(f"DEBUG: Initial consult completed result: {initial_consult_completed_result}")
            if initial_consult_completed_result:
                results.update(initial_consult_completed_result)
            
            sleep_study_scheduled_result = ManifestValidatorService._validate_sleep_study_scheduled(patient_id, file_groups)
            print(f"DEBUG: Sleep study scheduled result: {sleep_study_scheduled_result}")
            if sleep_study_scheduled_result:
                results.update(sleep_study_scheduled_result)
            
            sleep_test_completed_result = ManifestValidatorService._validate_sleep_test_completed(patient_id, file_groups)
            print(f"DEBUG: Sleep test completed result: {sleep_test_completed_result}")
            if sleep_test_completed_result:
                results.update(sleep_test_completed_result)
            
            schedule_sleep_test_review_result = ManifestValidatorService._validate_schedule_sleep_test_review(patient_id, file_groups)
            print(f"DEBUG: Schedule sleep test review result: {schedule_sleep_test_review_result}")
            if schedule_sleep_test_review_result:
                results.update(schedule_sleep_test_review_result)
            
            sleep_doctor_followup_result = ManifestValidatorService._validate_sleep_doctor_followup_completed(patient_id, file_groups)
            print(f"DEBUG: Sleep doctor followup result: {sleep_doctor_followup_result}")
            if sleep_doctor_followup_result:
                results.update(sleep_doctor_followup_result)
            
            print(f"DEBUG: Stage validation completed. Results: {list(results.keys())}")
            
            # Continue with remaining validation methods
            dental_consult_result = ManifestValidatorService._validate_dental_sleep_doctor_consult_scheduled(patient_id, file_groups)
            if dental_consult_result:
                results.update(dental_consult_result)
            
            cbct_result = ManifestValidatorService._validate_cbct_observation_report_uploaded(patient_id, file_groups)
            if cbct_result:
                results.update(cbct_result)
            
            intraoral_result = ManifestValidatorService._validate_intraoral_scan_uploaded(patient_id, file_groups)
            if intraoral_result:
                results.update(intraoral_result)
            
            hipaa_result = ManifestValidatorService._validate_hipaa_consent_signed(patient_id, file_groups)
            if hipaa_result:
                results.update(hipaa_result)
            
            dental_expert_result = ManifestValidatorService._validate_met_with_dental_sleep_expert(patient_id, file_groups)
            if dental_expert_result:
                results.update(dental_expert_result)
            
            osa_report_result = ManifestValidatorService._validate_osa_report_ready(patient_id, file_groups)
            if osa_report_result:
                results.update(osa_report_result)
            
            order_appliance_result = ManifestValidatorService._validate_order_oral_appliance(patient_id)
            if order_appliance_result:
                results.update(order_appliance_result)
            
            device_delivered_result = ManifestValidatorService._validate_device_delivered(patient_id)
            if device_delivered_result:
                results.update(device_delivered_result)
            
            schedule_delivery_result = ManifestValidatorService._validate_schedule_oral_appliance_delivery(patient_id)
            if schedule_delivery_result:
                results.update(schedule_delivery_result)
            
            delivery_completed_result = ManifestValidatorService._validate_oral_appliance_delivery_completed(patient_id)
            if delivery_completed_result:
                results.update(delivery_completed_result)
            
            followup_test_result = ManifestValidatorService._validate_follow_up_sleep_test_after_delivery(patient_id, results)
            if followup_test_result:
                results.update(followup_test_result)
            
            # Validate additional email/reminder stages
            cbct_request_result = ManifestValidatorService._validate_email_stage(patient_id, 'cbct_request_sent', 'cbct_request')
            if cbct_request_result:
                results.update(cbct_request_result)
                
            hipaa_request_result = ManifestValidatorService._validate_email_stage(patient_id, 'hipaa_consent_request_sent', 'hipaa_consent_request')
            if hipaa_request_result:
                results.update(hipaa_request_result)
                
            osa_request_result = ManifestValidatorService._validate_email_stage(patient_id, 'osa_report_request_sent', 'osa_report_request')
            if osa_request_result:
                results.update(osa_request_result)
                
            # Additional reminder stages
            consultation_reminder_result = ManifestValidatorService._validate_email_stage(patient_id, 'consultation_reminder_sent', 'reminder_consultation_scheduling')
            if consultation_reminder_result:
                results.update(consultation_reminder_result)
                
            sleep_study_reminder_result = ManifestValidatorService._validate_email_stage(patient_id, 'sleep_study_reminder_sent', 'reminder_sleep_study')
            if sleep_study_reminder_result:
                results.update(sleep_study_reminder_result)
                
            document_reminder_result = ManifestValidatorService._validate_email_stage(patient_id, 'document_upload_reminder_sent', 'reminder_document_upload')
            if document_reminder_result:
                results.update(document_reminder_result)
                
            appliance_reminder_result = ManifestValidatorService._validate_email_stage(patient_id, 'appliance_delivery_reminder_sent', 'reminder_appliance_delivery')
            if appliance_reminder_result:
                results.update(appliance_reminder_result)
                
            followup_reminder_result = ManifestValidatorService._validate_email_stage(patient_id, 'followup_test_reminder_sent', 'reminder_followup_test')
            if followup_reminder_result:
                results.update(followup_reminder_result)
            
            # Update manifest table with results
            print(f"DEBUG: About to update manifest table with {len(results)} results")
            logger.info(f"DEBUG: About to update manifest table with {len(results)} results")
            update_success = ManifestValidatorService._update_manifest_from_validation(patient_id, results)
            print(f"DEBUG: Manifest table update successful: {update_success}")
            logger.info(f"DEBUG: Manifest table update successful: {update_success}")
            
            logger.info(f"Manifest validation completed for patient {patient_id}")
            return results
            
        except Exception as e:
            logger.error(f"Error validating stages for patient {patient_id}: {e}")
            db.session.rollback()
            return None
    
    @staticmethod
    def _check_file_groups(patient_id):
        """
        Check for key file groups that auto-complete multiple stages
        Returns dict with file group information
        """
        try:
            file_groups = {
                'sleep_test_files': [],
                'questionnaire_files': [],
                'intraoral_files': [],
                'cbct_files': [],
                'osa_report_files': []
            }
            
            # Check for sleep test files (auto-completes stages 2, 3, 4, 5, 6, 7)
            sleep_test_files = db.session.execute(
                text("""
                    SELECT f.id, f.name, f.upload_date, f.file_type, f.category, f.subcategory
                    FROM files f
                    WHERE f.patient_id = :patient_id AND LOWER(f.subcategory) = LOWER('sleep-test')
                """),
                {'patient_id': patient_id}
            ).fetchall()
            file_groups['sleep_test_files'] = sleep_test_files
            
            # Check for questionnaire files (auto-completes stage 1)
            questionnaire_files = db.session.execute(
                text("""
                    SELECT f.id, f.name, f.upload_date, f.file_type, f.category, f.subcategory
                    FROM files f
                    WHERE f.patient_id = :patient_id AND LOWER(f.subcategory) = LOWER('questionnaire')
                """),
                {'patient_id': patient_id}
            ).fetchall()
            file_groups['questionnaire_files'] = questionnaire_files
            
            # Check for intraoral scan files (auto-completes stages 8, 12)
            intraoral_files = db.session.execute(
                text("""
                    SELECT f.id, f.name, f.upload_date, f.file_type, f.category, f.subcategory
                    FROM files f
                    WHERE f.patient_id = :patient_id AND LOWER(f.subcategory) = LOWER('intraoral-scan')
                """),
                {'patient_id': patient_id}
            ).fetchall()
            file_groups['intraoral_files'] = intraoral_files
            
            # Check for CBCT observation files (auto-completes stages 8, 9)
            cbct_files = db.session.execute(
                text("""
                    SELECT af.id, af.name, af.upload_date, af.file_type, af.file_category
                    FROM adminfiles af
                    WHERE af.patient_id = :patient_id AND LOWER(af.file_category) = LOWER('cbct observations')
                """),
                {'patient_id': patient_id}
            ).fetchall()
            file_groups['cbct_files'] = cbct_files
            
            # Check for OSA report files (auto-completes stages 12, 13)
            osa_report_files = db.session.execute(
                text("""
                    SELECT af.id, af.name, af.upload_date, af.file_type, af.file_category
                    FROM adminfiles af
                    WHERE af.patient_id = :patient_id AND LOWER(af.file_category) = LOWER('patient report')
                """),
                {'patient_id': patient_id}
            ).fetchall()
            file_groups['osa_report_files'] = osa_report_files
            
            return file_groups
            
        except Exception as e:
            logger.error(f"Error checking file groups for patient {patient_id}: {e}")
            return {
                'sleep_test_files': [],
                'questionnaire_files': [],
                'intraoral_files': [],
                'cbct_files': [],
                'osa_report_files': []
            }
    
    @staticmethod
    def _validate_email_stage(patient_id, stage_key, email_type):
        """Generic validation for email/reminder stages based on email_logs table"""
        try:
            from flask_app import db
            from sqlalchemy import text
            
            # Check if email has been sent
            email_result = db.session.execute(
                text("""
                    SELECT el.id, el.sent_at, el.email_type, el.recipient_email, el.subject
                    FROM email_logs el
                    WHERE el.patient_id = :patient_id AND el.email_type = :email_type
                    ORDER BY el.sent_at DESC
                    LIMIT 1
                """),
                {'patient_id': patient_id, 'email_type': email_type}
            ).first()
            
            if email_result:
                stage_data = {
                    'email_id': email_result.id,
                    'sent_date': email_result.sent_at.isoformat(),
                    'recipient_email': email_result.recipient_email,
                    'email_type': email_result.email_type,
                    'subject': email_result.subject
                }
                
                return {
                    stage_key: {
                        'is_completed': True,
                        'completion_date': email_result.sent_at,
                        'stage_data': stage_data,
                        'status_message': f"Email sent on {email_result.sent_at.strftime('%B %d, %Y')} to {email_result.recipient_email}"
                    }
                }
            else:
                return {
                    stage_key: {
                        'is_completed': False,
                        'completion_date': None,
                        'stage_data': None,
                        'status_message': f"Email not sent"
                    }
                }
                
        except Exception as e:
            import logging
            logging.error(f"Error validating email stage {stage_key} for patient {patient_id}: {e}")
            return None

    @staticmethod
    def _validate_quiz_completion(patient_id):
        """Validate Stage 1: Quiz Completion with auto-completion logic"""
        try:
            # Check for quiz completion in conversion_quiz table
            quiz_result = db.session.execute(
                text("""
                    SELECT cq.id, cq.created_at, cq.quiz_type, cq.patient_email
                    FROM patients p
                    LEFT JOIN conversion_quiz cq ON p.id = cq.user_id
                    WHERE p.id = :patient_id AND cq.quiz_type = 'basic_quiz'
                """),
                {'patient_id': patient_id}
            ).first()
            
            # Check for questionnaire files
            quiz_files = db.session.execute(
                text("""
                    SELECT f.id, f.name, f.upload_date, f.file_type, f.subcategory
                    FROM files f
                    WHERE f.patient_id = :patient_id AND LOWER(f.subcategory) = LOWER('questionnaire')
                """),
                {'patient_id': patient_id}
            ).fetchall()
            
            # Check if quiz link has been sent (to avoid duplicate sends)
            quiz_link_sent = db.session.execute(
                text("""
                    SELECT el.id, el.sent_at, el.email_type, el.recipient_email
                    FROM email_logs el
                    WHERE el.patient_id = :patient_id AND el.email_type = 'quiz_link_stage1'
                    ORDER BY el.sent_at DESC
                    LIMIT 1
                """),
                {'patient_id': patient_id}
            ).first()
            
            stage_completed = False
            completion_date = None
            stage_data = {}
            status_message = ""
            
            if quiz_result and quiz_result.id:
                stage_completed = True
                completion_date = quiz_result.created_at
                stage_data = {
                    'quiz_id': quiz_result.id,
                    'quiz_type': quiz_result.quiz_type,
                    'patient_email': quiz_result.patient_email,
                    'completion_method': 'quiz_record'
                }
                status_message = f"Quiz completed on {quiz_result.created_at.strftime('%B %d, %Y')}"
            elif quiz_files:
                stage_completed = True
                completion_date = min(file.upload_date for file in quiz_files)
                stage_data = {
                    'file_count': len(quiz_files),
                    'files': [{'id': f.id, 'name': f.name, 'upload_date': f.upload_date.isoformat()} for f in quiz_files],
                    'completion_method': 'questionnaire_files'
                }
                status_message = f"Questionnaire files uploaded ({len(quiz_files)} files)"
            elif quiz_link_sent:
                stage_completed = False  # Link sent but not completed yet
                stage_data = {
                    'quiz_link_sent': True,
                    'sent_date': quiz_link_sent.sent_at.isoformat(),
                    'recipient_email': quiz_link_sent.recipient_email,
                    'status': 'awaiting_completion'
                }
                status_message = f"Quiz link sent on {quiz_link_sent.sent_at.strftime('%B %d, %Y')} - awaiting patient completion"
            else:
                stage_completed = False
                status_message = 'No quiz completed and no questionnaire files found'
            
            # Create stage_file_links for questionnaire files
            if quiz_files:
                for file_record in quiz_files:
                    db.session.execute(
                        text("""
                            INSERT IGNORE INTO stage_file_links (patient_id, stage_key, file_id, file_table)
                            VALUES (:patient_id, :stage_key, :file_id, 'files')
                        """),
                        {'patient_id': patient_id, 'stage_key': 'quiz_completion', 'file_id': file_record.id}
                    )
                db.session.commit()
            
            return {
                'quiz_completion': {
                    'is_completed': stage_completed,
                    'completion_date': completion_date,
                    'stage_data': json.dumps(stage_data) if stage_data else None,
                    'status_message': status_message
                }
            }
            
        except Exception as e:
            logger.error(f"Error validating quiz completion for patient {patient_id}: {e}")
            return {'quiz_completion': {'is_completed': False, 'completion_date': None, 'stage_data': None, 'status_message': f'Error: {str(e)}'}}
    
    @staticmethod
    def _validate_sleep_study_scheduled(patient_id, file_groups=None):
        """Validate Stage 4: Sleep Study Scheduled with auto-completion logic"""
        try:
            print(f"DEBUG: Validating sleep_study_scheduled for patient {patient_id}")
            # Check for scheduled sleep study
            result = db.session.execute(
                text("""
                    SELECT pcs.id, pcs.scheduled_datetime, pcs.notes
                    FROM patients p
                    LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
                    WHERE p.id = :patient_id AND LOWER(pcs.consult_type) = LOWER('sleep_doctor')
                """),
                {'patient_id': patient_id}
            ).first()
            
            if result and result.id:
                return {
                    'sleep_study_scheduled': {
                        'is_completed': True,
                        'completion_date': result.scheduled_datetime,
                        'stage_data': json.dumps({'notes': result.notes}),
                        'status_message': f"Sleep study scheduled for {result.scheduled_datetime.strftime('%B %d, %Y')}"
                    }
                }
            else:
                # Auto-completion: Use file groups if available, otherwise check directly
                if file_groups and file_groups.get('sleep_test_files'):
                    sleep_test_files = file_groups['sleep_test_files']
                    completion_date = min(file.upload_date for file in sleep_test_files)
                    print(f"DEBUG: Auto-completing sleep_study_scheduled with {len(sleep_test_files)} files")
                    return {
                        'sleep_study_scheduled': {
                            'is_completed': True,
                            'completion_date': completion_date,
                            'stage_data': json.dumps({
                                'auto_completed': True,
                                'reason': 'sleep_test_files_uploaded',
                                'file_count': len(sleep_test_files)
                            }),
                            'status_message': f"Auto-completed: Sleep test files uploaded ({len(sleep_test_files)} files) - study must have been scheduled"
                        }
                    }
                else:
                    return {
                        'sleep_study_scheduled': {
                            'is_completed': False,
                            'completion_date': None,
                            'stage_data': None,
                            'status_message': 'Sleep study not scheduled'
                        }
                    }
                    
        except Exception as e:
            logger.error(f"Error validating sleep study scheduled for patient {patient_id}: {e}")
            return {'sleep_study_scheduled': {'is_completed': False, 'completion_date': None, 'stage_data': None, 'status_message': f'Error: {str(e)}'}}
    
    @staticmethod
    def _validate_schedule_sleep_test_review(patient_id, file_groups=None):
        """Validate Stage 6: Schedule Sleep Test Review with auto-completion logic"""
        try:
            # Check for scheduled sleep test review
            result = db.session.execute(
                text("""
                    SELECT pcs.id, pcs.scheduled_datetime, pcs.notes
                    FROM patients p
                    LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
                    WHERE p.id = :patient_id AND LOWER(pcs.consult_type) = LOWER('sleep_doctor') AND LOWER(pcs.status) = LOWER('scheduled')
                """),
                {'patient_id': patient_id}
            ).first()
            
            if result and result.id:
                return {
                    'schedule_sleep_test_review': {
                        'is_completed': True,
                        'completion_date': result.scheduled_datetime,
                        'stage_data': json.dumps({'notes': result.notes}),
                        'status_message': f"Sleep test review scheduled for {result.scheduled_datetime.strftime('%B %d, %Y')}"
                    }
                }
            else:
                # Auto-completion: Use file groups if available, otherwise check directly
                if file_groups and file_groups.get('sleep_test_files'):
                    sleep_test_files = file_groups['sleep_test_files']
                    completion_date = min(file.upload_date for file in sleep_test_files)
                    return {
                        'schedule_sleep_test_review': {
                            'is_completed': True,
                            'completion_date': completion_date,
                            'stage_data': json.dumps({
                                'auto_completed': True,
                                'reason': 'sleep_test_files_uploaded',
                                'file_count': len(sleep_test_files)
                            }),
                            'status_message': f"Auto-completed: Sleep test files uploaded ({len(sleep_test_files)} files) - review must have been scheduled"
                        }
                    }
                else:
                    return {
                        'schedule_sleep_test_review': {
                            'is_completed': False,
                            'completion_date': None,
                            'stage_data': None,
                            'status_message': 'Sleep test review not scheduled'
                        }
                    }
                    
        except Exception as e:
            logger.error(f"Error validating schedule sleep test review for patient {patient_id}: {e}")
            return {'schedule_sleep_test_review': {'is_completed': False, 'completion_date': None, 'stage_data': None, 'status_message': f'Error: {str(e)}'}}
    
    @staticmethod
    def _validate_dental_sleep_doctor_consult_scheduled(patient_id, file_groups=None):
        """Validate Stage 8: Dental Sleep Doctor Consult Scheduled with auto-completion logic"""
        try:
            # Check for scheduled dental consult
            result = db.session.execute(
                text("""
                    SELECT pcs.id, pcs.scheduled_datetime, pcs.notes
                    FROM patients p
                    LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
                    WHERE p.id = :patient_id 
                      AND LOWER(pcs.consult_type) IN (LOWER('dental_sleep_doctor'), LOWER('dental_sleep_doctor_consult'))
                """),
                {'patient_id': patient_id}
            ).first()
            
            if result and result.id:
                return {
                    'dental_sleep_doctor_consult_scheduled': {
                        'is_completed': True,
                        'completion_date': result.scheduled_datetime,
                        'stage_data': json.dumps({'notes': result.notes}),
                        'status_message': f"Dental sleep doctor consult scheduled for {result.scheduled_datetime.strftime('%B %d, %Y')}"
                    }
                }
            else:
                # Auto-completion: First check for OSA reports (highest priority)
                if file_groups and file_groups.get('osa_report_files'):
                    osa_report_files = file_groups['osa_report_files']
                    completion_date = min(file.upload_date for file in osa_report_files)
                    return {
                        'dental_sleep_doctor_consult_scheduled': {
                            'is_completed': True,
                            'completion_date': completion_date,
                            'stage_data': json.dumps({
                                'auto_completed': True,
                                'reason': 'osa_report_uploaded',
                                'file_count': len(osa_report_files)
                            }),
                            'status_message': f"Auto-completed: OSA report uploaded ({len(osa_report_files)} files) - consult must have been scheduled"
                        }
                    }
                
                # Auto-completion: Check for intraoral scans or CBCT observations
                intraoral_files = db.session.execute(
                    text("""
                        SELECT f.id, f.name, f.upload_date, f.file_type
                        FROM files f
                        WHERE f.patient_id = :patient_id AND LOWER(f.subcategory) = LOWER('intraoral-scan')
                    """),
                    {'patient_id': patient_id}
                ).fetchall()
                
                cbct_files = db.session.execute(
                    text("""
                        SELECT af.id, af.name, af.upload_date, af.file_type
                        FROM adminfiles af
                        WHERE af.patient_id = :patient_id AND LOWER(af.file_category) = LOWER('cbct observations')
                    """),
                    {'patient_id': patient_id}
                ).fetchall()
                
                if intraoral_files or cbct_files:
                    auto_completion_reason = []
                    completion_date = None
                    
                    if intraoral_files:
                        auto_completion_reason.append(f"intraoral scans ({len(intraoral_files)} files)")
                        if not completion_date:
                            completion_date = min(file.upload_date for file in intraoral_files)
                    
                    if cbct_files:
                        auto_completion_reason.append(f"CBCT observations ({len(cbct_files)} files)")
                        if not completion_date:
                            completion_date = min(file.upload_date for file in cbct_files)
                    
                    return {
                        'dental_sleep_doctor_consult_scheduled': {
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
                    }
                else:
                    return {
                        'dental_sleep_doctor_consult_scheduled': {
                            'is_completed': False,
                            'completion_date': None,
                            'stage_data': None,
                            'status_message': 'Dental sleep doctor consult not scheduled'
                        }
                    }
                    
        except Exception as e:
            logger.error(f"Error validating dental sleep doctor consult scheduled for patient {patient_id}: {e}")
            return {'dental_sleep_doctor_consult_scheduled': {'is_completed': False, 'completion_date': None, 'stage_data': None, 'status_message': f'Error: {str(e)}'}}
    
    @staticmethod
    def _validate_met_with_dental_sleep_expert(patient_id, file_groups=None):
        """Validate Stage 12: Patient Completes Consult with Dental Sleep Expert with auto-completion logic"""
        try:
            # Check for completed dental consult
            result = db.session.execute(
                text("""
                    SELECT pcs.id, pcs.completed_datetime, pcs.comment
                    FROM patient_consult_schedule pcs
                    WHERE pcs.patient_id = :patient_id 
                      AND LOWER(pcs.consult_type) IN (LOWER('dental_sleep_doctor'), LOWER('dental_sleep_doctor_consult'))
                      AND LOWER(pcs.status) = LOWER('completed')
                """),
                {'patient_id': patient_id}
            ).first()
            
            if result and result.id:
                return {
                    'met_with_dental_sleep_expert': {
                        'is_completed': True,
                        'completion_date': result.completed_datetime,
                        'stage_data': json.dumps({'comment': result.comment}),
                        'status_message': f"Dental sleep expert consultation completed on {result.completed_datetime.strftime('%B %d, %Y')}"
                    }
                }
            else:
                # Auto-completion: Check for intraoral scans or OSA reports
                intraoral_files = db.session.execute(
                    text("""
                        SELECT f.id, f.name, f.upload_date, f.file_type
                        FROM files f
                        WHERE f.patient_id = :patient_id AND LOWER(f.subcategory) = LOWER('intraoral-scan')
                    """),
                    {'patient_id': patient_id}
                ).fetchall()
                
                osa_report_files = db.session.execute(
                    text("""
                        SELECT af.id, af.name, af.upload_date, af.file_type
                        FROM adminfiles af
                        WHERE af.patient_id = :patient_id AND LOWER(af.file_category) LIKE LOWER('%patient report%') AND af.is_public = 1
                    """),
                    {'patient_id': patient_id}
                ).fetchall()
                
                if intraoral_files or osa_report_files:
                    auto_completion_reason = []
                    completion_date = None
                    
                    if intraoral_files:
                        auto_completion_reason.append(f"intraoral scans ({len(intraoral_files)} files)")
                        if not completion_date:
                            completion_date = min(file.upload_date for file in intraoral_files)
                    
                    if osa_report_files:
                        auto_completion_reason.append(f"OSA reports ({len(osa_report_files)} files)")
                        if not completion_date:
                            completion_date = min(file.upload_date for file in osa_report_files)
                    
                    return {
                        'met_with_dental_sleep_expert': {
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
                    }
                else:
                    return {
                        'met_with_dental_sleep_expert': {
                            'is_completed': False,
                            'completion_date': None,
                            'stage_data': None,
                            'status_message': 'Dental sleep expert consultation not completed'
                        }
                    }
                    
        except Exception as e:
            logger.error(f"Error validating met with dental sleep expert for patient {patient_id}: {e}")
            return {'met_with_dental_sleep_expert': {'is_completed': False, 'completion_date': None, 'stage_data': None, 'status_message': f'Error: {str(e)}'}}
    
    # Additional validation methods would go here...
    # For brevity, I'm including the key auto-completion methods
    
    @staticmethod
    def _validate_initial_consult_scheduled(patient_id, file_groups=None):
        """Validate Stage 2: Initial Consult Scheduled with auto-completion logic"""
        try:
            # Check for scheduled consultation
            # Accept both 'sleep_doctor' and legacy 'sleep_expert' for backward compatibility
            result = db.session.execute(
                text("""
                    SELECT pcs.id, pcs.scheduled_datetime, pcs.notes
                    FROM patients p
                    LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
                    WHERE p.id = :patient_id 
                      AND LOWER(pcs.consult_type) IN (LOWER('sleep_doctor'), LOWER('sleep_expert'))
                """),
                {'patient_id': patient_id}
            ).first()
            
            if result and result.id:
                return {
                    'initial_consult_scheduled': {
                        'is_completed': True,
                        'completion_date': result.scheduled_datetime,
                        'stage_data': json.dumps({'notes': result.notes}),
                        'status_message': f"Consultation scheduled for {result.scheduled_datetime.strftime('%B %d, %Y')}"
                    }
                }
            else:
                # Auto-completion: Use file groups if available, otherwise check directly
                if file_groups and file_groups.get('sleep_test_files'):
                    sleep_test_files = file_groups['sleep_test_files']
                    completion_date = min(file.upload_date for file in sleep_test_files)
                    return {
                        'initial_consult_scheduled': {
                            'is_completed': True,
                            'completion_date': completion_date,
                            'stage_data': json.dumps({
                                'auto_completed': True,
                                'reason': 'sleep_test_files_uploaded',
                                'file_count': len(sleep_test_files)
                            }),
                            'status_message': f"Auto-completed: Sleep test files uploaded ({len(sleep_test_files)} files) - consultation must have been scheduled"
                        }
                    }
                else:
                    return {
                        'initial_consult_scheduled': {
                            'is_completed': False,
                            'completion_date': None,
                            'stage_data': None,
                            'status_message': 'No consultation scheduled'
                        }
                    }
                    
        except Exception as e:
            logger.error(f"Error validating initial consult scheduled for patient {patient_id}: {e}")
            return {'initial_consult_scheduled': {'is_completed': False, 'completion_date': None, 'stage_data': None, 'status_message': f'Error: {str(e)}'}}
    
    @staticmethod
    def _validate_initial_consult_completed(patient_id, file_groups=None):
        """Validate Stage 3: Met with Sleep Expert with auto-completion logic"""
        try:
            # Check for completed consultation
            # Accept both 'sleep_doctor' and legacy 'sleep_expert' for backward compatibility
            result = db.session.execute(
                text("""
                    SELECT pcs.id, pcs.completed_datetime, pcs.comment
                    FROM patients p
                    LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
                    WHERE p.id = :patient_id 
                      AND LOWER(pcs.consult_type) IN (LOWER('sleep_doctor'), LOWER('sleep_expert'))
                      AND LOWER(pcs.status) = LOWER('completed')
                """),
                {'patient_id': patient_id}
            ).first()
            
            if result and result.id:
                return {
                    'initial_consult_completed': {
                        'is_completed': True,
                        'completion_date': result.completed_datetime,
                        'stage_data': json.dumps({'comment': result.comment}),
                        'status_message': f"Consultation completed on {result.completed_datetime.strftime('%B %d, %Y')}"
                    }
                }
            else:
                # Auto-completion: Use file groups if available, otherwise check directly
                if file_groups and file_groups.get('sleep_test_files'):
                    sleep_test_files = file_groups['sleep_test_files']
                    completion_date = min(file.upload_date for file in sleep_test_files)
                    return {
                        'initial_consult_completed': {
                            'is_completed': True,
                            'completion_date': completion_date,
                            'stage_data': json.dumps({
                                'auto_completed': True,
                                'reason': 'sleep_test_files_uploaded',
                                'file_count': len(sleep_test_files)
                            }),
                            'status_message': f"Auto-completed: Sleep test files uploaded ({len(sleep_test_files)} files) - consultation must have been completed"
                        }
                    }
                else:
                    return {
                        'initial_consult_completed': {
                            'is_completed': False,
                            'completion_date': None,
                            'stage_data': None,
                            'status_message': 'Consultation not completed'
                        }
                    }
                    
        except Exception as e:
            logger.error(f"Error validating initial consult completed for patient {patient_id}: {e}")
            return {'initial_consult_completed': {'is_completed': False, 'completion_date': None, 'stage_data': None, 'status_message': f'Error: {str(e)}'}}
    
    @staticmethod
    def _validate_sleep_test_completed(patient_id, file_groups=None):
        """Validate Stage 5: Sleep Test Completed with auto-completion logic"""
        try:
            # Use file groups if available, otherwise check directly
            if file_groups and file_groups.get('sleep_test_files'):
                sleep_test_files = file_groups['sleep_test_files']
                if sleep_test_files:
                    result = sleep_test_files[0]  # Get the first file
                else:
                    result = None
            else:
                # Check for sleep test files directly
                result = db.session.execute(
                    text("""
                        SELECT id, upload_date, name, category, subcategory FROM files 
                        WHERE patient_id = :patient_id AND category = 'medical' AND subcategory = 'sleep-test' 
                        ORDER BY upload_date DESC LIMIT 1
                    """),
                    {'patient_id': patient_id}
                ).first()
            
            if result:
                # Create stage_file_links for sleep test files
                db.session.execute(
                    text("""
                        INSERT IGNORE INTO stage_file_links (patient_id, stage_key, file_id, file_table)
                        VALUES (:patient_id, :stage_key, :file_id, 'files')
                    """),
                    {'patient_id': patient_id, 'stage_key': 'sleep_test_completed', 'file_id': result.id}
                )
                db.session.commit()
                
                return {
                    'sleep_test_completed': {
                        'is_completed': True,
                        'completion_date': result.upload_date,
                        'stage_data': json.dumps({
                            'file_id': result.id,
                            'filename': result.name,
                            'file_category': result.category,
                            'subcategory': result.subcategory
                        }),
                        'status_message': f"Sleep test uploaded on {result.upload_date.strftime('%B %d, %Y')}"
                    }
                }
            else:
                return {
                    'sleep_test_completed': {
                        'is_completed': False,
                        'completion_date': None,
                        'stage_data': None,
                        'status_message': 'No sleep test uploaded'
                    }
                }
                    
        except Exception as e:
            logger.error(f"Error validating sleep test completed for patient {patient_id}: {e}")
            return {'sleep_test_completed': {'is_completed': False, 'completion_date': None, 'stage_data': None, 'status_message': f'Error: {str(e)}'}}
    
    @staticmethod
    def _validate_sleep_doctor_followup_completed(patient_id, file_groups=None):
        """Validate Stage 7: Sleep Doctor Followup Completed with auto-completion logic"""
        try:
            # Check for completed sleep doctor followup
            # Accept both 'sleep_doctor' and legacy 'ep_doctor' for backward compatibility
            result = db.session.execute(
                text("""
                    SELECT pcs.id, pcs.completed_datetime, pcs.comment
                    FROM patients p
                    LEFT JOIN patient_consult_schedule pcs ON p.id = pcs.patient_id 
                    WHERE p.id = :patient_id 
                      AND LOWER(pcs.consult_type) IN (LOWER('sleep_doctor'), LOWER('ep_doctor'))
                      AND LOWER(pcs.status) = LOWER('completed')
                """),
                {'patient_id': patient_id}
            ).first()
            
            if result and result.id:
                return {
                    'sleep_doctor_followup_completed': {
                        'is_completed': True,
                        'completion_date': result.completed_datetime,
                        'stage_data': json.dumps({'comment': result.comment}),
                        'status_message': f"Sleep doctor followup completed on {result.completed_datetime.strftime('%B %d, %Y')}"
                    }
                }
            else:
                # Auto-completion: Use file groups if available, otherwise check directly
                if file_groups and file_groups.get('sleep_test_files'):
                    sleep_test_files = file_groups['sleep_test_files']
                    completion_date = min(file.upload_date for file in sleep_test_files)
                    return {
                        'sleep_doctor_followup_completed': {
                            'is_completed': True,
                            'completion_date': completion_date,
                            'stage_data': json.dumps({
                                'auto_completed': True,
                                'reason': 'sleep_test_files_uploaded',
                                'file_count': len(sleep_test_files)
                            }),
                            'status_message': f"Auto-completed: Sleep test files uploaded ({len(sleep_test_files)} files) - followup must have been completed"
                        }
                    }
                else:
                    return {
                        'sleep_doctor_followup_completed': {
                            'is_completed': False,
                            'completion_date': None,
                            'stage_data': None,
                            'status_message': 'Sleep doctor followup not completed'
                        }
                    }
                    
        except Exception as e:
            logger.error(f"Error validating sleep doctor followup completed for patient {patient_id}: {e}")
            return {'sleep_doctor_followup_completed': {'is_completed': False, 'completion_date': None, 'stage_data': None, 'status_message': f'Error: {str(e)}'}}
    
    @staticmethod
    def _validate_cbct_observation_report_uploaded(patient_id, file_groups=None):
        """Validate Stage 9: CBCT Observation Report Uploaded with auto-completion logic"""
        try:
            # First check for actual CBCT files
            result = db.session.execute(
                text("""
                    SELECT af.id, af.upload_date, af.name, af.file_category FROM adminfiles af 
                    WHERE af.patient_id = :patient_id AND (
                        LOWER(af.file_category) = LOWER('cbct observations') OR
                        LOWER(af.file_category) LIKE LOWER('%cbct%') OR
                        LOWER(af.file_category) LIKE LOWER('%level 2%')
                    )
                    ORDER BY af.upload_date DESC LIMIT 1
                """),
                {'patient_id': patient_id}
            ).first()
            
            if result:
                return {
                    'cbct_observation_report_uploaded': {
                        'is_completed': True,
                        'completion_date': result.upload_date,
                        'stage_data': json.dumps({
                            'file_name': result.name,
                            'file_category': result.file_category
                        }),
                        'status_message': f"CBCT observation report uploaded - {result.name}"
                    }
                }
            else:
                # No auto-completion - CBCT files must be actually uploaded
                return {
                    'cbct_observation_report_uploaded': {
                        'is_completed': False,
                        'completion_date': None,
                        'stage_data': None,
                        'status_message': 'CBCT observation report not uploaded'
                    }
                }
        except Exception as e:
            logger.error(f"Error validating CBCT observation report for patient {patient_id}: {e}")
            return {
                'cbct_observation_report_uploaded': {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': f'Error: {str(e)}'
                }
            }
    
    @staticmethod
    def _validate_intraoral_scan_uploaded(patient_id, file_groups=None):
        """Validate Stage 10: Intraoral Scan Uploaded with auto-completion logic"""
        try:
            # First check for actual intraoral scan files
            result = db.session.execute(
                text("""
                    SELECT f.id, f.upload_date, f.name, f.subcategory FROM files f 
                    WHERE f.patient_id = :patient_id AND LOWER(f.subcategory) = LOWER('intraoral-scan') 
                    ORDER BY f.upload_date DESC LIMIT 1
                """),
                {'patient_id': patient_id}
            ).first()
            
            if result:
                return {
                    'intraoral_scan_uploaded': {
                        'is_completed': True,
                        'completion_date': result.upload_date,
                        'stage_data': json.dumps({
                            'file_name': result.name,
                            'subcategory': result.subcategory
                        }),
                        'status_message': f"Intraoral scan uploaded - {result.name}"
                    }
                }
            else:
                # No auto-completion - intraoral scan files must be actually uploaded
                return {
                    'intraoral_scan_uploaded': {
                        'is_completed': False,
                        'completion_date': None,
                        'stage_data': None,
                        'status_message': 'Intraoral scan not uploaded'
                    }
                }
        except Exception as e:
            logger.error(f"Error validating intraoral scan for patient {patient_id}: {e}")
            return {
                'intraoral_scan_uploaded': {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': f'Error: {str(e)}'
                }
            }
    
    @staticmethod
    def _validate_hipaa_consent_signed(patient_id, file_groups=None):
        """Validate Stage 11: HIPAA Consent Signed with auto-completion logic"""
        try:
            # First check for actual HIPAA consent files
            result = db.session.execute(
                text("""
                    SELECT f.id, f.upload_date, f.name, f.subcategory FROM files f 
                    WHERE f.patient_id = :patient_id AND (
                        LOWER(f.subcategory) LIKE LOWER('%hipaa%') OR
                        LOWER(f.name) LIKE LOWER('%hipaa%') OR
                        LOWER(f.subcategory) = LOWER('billing')
                    )
                    ORDER BY f.upload_date DESC LIMIT 1
                """),
                {'patient_id': patient_id}
            ).first()
            
            if result:
                return {
                    'hipaa_consent_signed': {
                        'is_completed': True,
                        'completion_date': result.upload_date,
                        'stage_data': json.dumps({
                            'file_name': result.name,
                            'subcategory': result.subcategory
                        }),
                        'status_message': f"HIPAA consent signed - {result.name}"
                    }
                }
            else:
                # No auto-completion - HIPAA consent files must be actually uploaded
                return {
                    'hipaa_consent_signed': {
                        'is_completed': False,
                        'completion_date': None,
                        'stage_data': None,
                        'status_message': 'HIPAA consent not signed'
                    }
                }
        except Exception as e:
            logger.error(f"Error validating HIPAA consent for patient {patient_id}: {e}")
            return {
                'hipaa_consent_signed': {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': f'Error: {str(e)}'
                }
            }
    
    @staticmethod
    def _validate_osa_report_ready(patient_id, file_groups=None):
        """Validate Stage 13: Patient OSA Report Ready with auto-completion logic"""
        try:
            # Auto-completion: Use file groups if available, otherwise check directly
            if file_groups and file_groups.get('osa_report_files'):
                osa_report_files = file_groups['osa_report_files']
                completion_date = min(file.upload_date for file in osa_report_files)
                return {
                    'osa_report_ready': {
                        'is_completed': True,
                        'completion_date': completion_date,
                        'stage_data': json.dumps({
                            'auto_completed': True,
                            'reason': 'osa_report_uploaded',
                            'file_count': len(osa_report_files)
                        }),
                        'status_message': f"OSA report ready - {len(osa_report_files)} files uploaded"
                    }
                }
            else:
                # Check for OSA report files directly
                result = db.session.execute(
                    text("""
                        SELECT af.id, af.upload_date, af.name, af.file_category FROM adminfiles af 
                        WHERE af.patient_id = :patient_id AND (
                            LOWER(af.file_category) = LOWER('patient report') OR
                            LOWER(af.file_category) LIKE LOWER('%level%') OR
                            LOWER(af.file_category) LIKE LOWER('%osa%') OR
                            LOWER(af.file_category) LIKE LOWER('%report%') OR
                            LOWER(af.name) LIKE LOWER('%osa%') OR
                            LOWER(af.name) LIKE LOWER('%level%') OR
                            LOWER(af.name) LIKE LOWER('%report%')
                        )
                        ORDER BY af.upload_date DESC LIMIT 1
                    """),
                    {'patient_id': patient_id}
                ).first()
                
                if result:
                    return {
                        'osa_report_ready': {
                            'is_completed': True,
                            'completion_date': result.upload_date,
                            'stage_data': json.dumps({
                                'file_name': result.name,
                                'file_category': result.file_category
                            }),
                            'status_message': f"OSA report ready - {result.name} uploaded"
                        }
                    }
                else:
                    return {
                        'osa_report_ready': {
                            'is_completed': False,
                            'completion_date': None,
                            'stage_data': None,
                            'status_message': 'OSA report not ready'
                        }
                    }
        except Exception as e:
            logger.error(f"Error validating OSA report for patient {patient_id}: {e}")
            return {
                'osa_report_ready': {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': f'Error: {str(e)}'
                }
            }
    
    
    @staticmethod
    def _validate_order_oral_appliance(patient_id):
        """Validate Stage 15: Order Oral Appliance"""
        try:
            # Check for oral appliance orders in patient_device_order table
            result = db.session.execute(
                text("""
                    SELECT pdo.id, pdo.device_type, pdo.device_name, pdo.order_date, pdo.status, pdo.notes 
                    FROM patient_device_order pdo 
                    WHERE pdo.patient_id = :patient_id 
                    ORDER BY pdo.order_date DESC LIMIT 1
                """),
                {'patient_id': patient_id}
            ).first()
            
            if result:
                return {
                    'order_oral_appliance': {
                        'is_completed': True,
                        'completion_date': result.order_date,
                        'stage_data': json.dumps({
                            'device_type': result.device_type,
                            'device_name': result.device_name,
                            'status': result.status,
                            'notes': result.notes
                        }),
                        'status_message': f"Oral appliance ordered on {result.order_date.strftime('%B %d, %Y')} - Status: {result.status}"
                    }
                }
            else:
                return {
                    'order_oral_appliance': {
                        'is_completed': False,
                        'completion_date': None,
                        'stage_data': None,
                        'status_message': 'No oral appliance order found'
                    }
                }
        except Exception as e:
            logger.error(f"Error validating oral appliance order for patient {patient_id}: {e}")
            return {
                'order_oral_appliance': {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': f'Error: {str(e)}'
                }
            }
    
    @staticmethod
    def _validate_device_delivered(patient_id):
        """Validate Stage 16: Device Delivered to Dental Office"""
        try:
            # Check for delivered devices in patient_device_order table
            result = db.session.execute(
                text("""
                    SELECT pdo.id, pdo.device_type, pdo.device_name, pdo.order_date, 
                           pdo.status, pdo.arrival_date, pdo.notes 
                    FROM patient_device_order pdo 
                    WHERE pdo.patient_id = :patient_id 
                    AND (pdo.status = 'delivered' OR pdo.arrival_date IS NOT NULL)
                    ORDER BY pdo.arrival_date DESC, pdo.order_date DESC LIMIT 1
                """),
                {'patient_id': patient_id}
            ).first()
            
            if result:
                delivery_date = result.arrival_date or result.order_date
                return {
                    'device_delivered': {
                        'is_completed': True,
                        'completion_date': delivery_date,
                        'stage_data': json.dumps({
                            'device_type': result.device_type,
                            'device_name': result.device_name,
                            'status': result.status,
                            'delivery_date': delivery_date.isoformat() if delivery_date else None,
                            'notes': result.notes
                        }),
                        'status_message': f"Device delivered on {delivery_date.strftime('%B %d, %Y') if delivery_date else 'unknown date'} - Status: {result.status}"
                    }
                }
            else:
                return {
                    'device_delivered': {
                        'is_completed': False,
                        'completion_date': None,
                        'stage_data': None,
                        'status_message': 'Device not delivered'
                    }
                }
        except Exception as e:
            logger.error(f"Error validating device delivery for patient {patient_id}: {e}")
            return {
                'device_delivered': {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': f'Error: {str(e)}'
                }
            }
    
    @staticmethod
    def _validate_schedule_oral_appliance_delivery(patient_id):
        """Validate Stage 17: Schedule Oral Appliance Delivery"""
        try:
            # Check if oral appliance delivery appointment was actually scheduled
            result = db.session.execute(
                text("""
                    SELECT pcs.id, pcs.scheduled_datetime, pcs.notes, pcs.status
                    FROM patient_consult_schedule pcs
                    WHERE pcs.patient_id = :patient_id 
                    AND LOWER(pcs.consult_type) = LOWER('oral_appliance_delivery')
                    ORDER BY pcs.scheduled_datetime DESC LIMIT 1
                """),
                {'patient_id': patient_id}
            ).first()
            
            if result:
                return {
                    'schedule_oral_appliance_delivery': {
                        'is_completed': True,
                        'completion_date': result.scheduled_datetime,
                        'stage_data': json.dumps({
                            'appointment_id': result.id,
                            'scheduled_datetime': result.scheduled_datetime.isoformat(),
                            'status': result.status,
                            'notes': result.notes
                        }),
                        'status_message': f"Oral appliance delivery scheduled for {result.scheduled_datetime.strftime('%B %d, %Y at %I:%M %p')}"
                    }
                }
            else:
                return {
                    'schedule_oral_appliance_delivery': {
                        'is_completed': False,
                        'completion_date': None,
                        'stage_data': None,
                        'status_message': 'Oral appliance delivery not scheduled'
                    }
                }
        except Exception as e:
            logger.error(f"Error validating schedule oral appliance delivery for patient {patient_id}: {e}")
            return {
                'schedule_oral_appliance_delivery': {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': f'Error: {str(e)}'
                }
            }
    
    @staticmethod
    def _validate_oral_appliance_delivery_completed(patient_id):
        """Validate Stage 18: Oral Appliance Delivery Completed"""
        try:
            # Check if oral appliance delivery appointment was completed
            result = db.session.execute(
                text("""
                    SELECT pcs.id, pcs.scheduled_datetime, pcs.notes, pcs.status, pcs.completed_at
                    FROM patient_consult_schedule pcs
                    WHERE pcs.patient_id = :patient_id 
                    AND LOWER(pcs.consult_type) = LOWER('oral_appliance_delivery')
                    AND pcs.status = 'completed'
                    ORDER BY pcs.completed_at DESC LIMIT 1
                """),
                {'patient_id': patient_id}
            ).first()
            
            if result:
                completion_date = result.completed_at or result.scheduled_datetime
                return {
                    'oral_appliance_delivery_completed': {
                        'is_completed': True,
                        'completion_date': completion_date,
                        'stage_data': json.dumps({
                            'appointment_id': result.id,
                            'scheduled_datetime': result.scheduled_datetime.isoformat(),
                            'completed_at': result.completed_at.isoformat() if result.completed_at else None,
                            'status': result.status,
                            'notes': result.notes
                        }),
                        'status_message': f"Oral appliance delivery completed on {completion_date.strftime('%B %d, %Y at %I:%M %p')}"
                    }
                }
            else:
                return {
                    'oral_appliance_delivery_completed': {
                        'is_completed': False,
                        'completion_date': None,
                        'stage_data': None,
                        'status_message': 'Oral appliance delivery not completed'
                    }
                }
        except Exception as e:
            logger.error(f"Error validating oral appliance delivery for patient {patient_id}: {e}")
            return {
                'oral_appliance_delivery_completed': {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': f'Error: {str(e)}'
                }
            }
    
    @staticmethod
    def _validate_follow_up_sleep_test_after_delivery(patient_id, results):
        """Validate Stage 19: Follow Up Sleep Test After Delivery"""
        try:
            return {
                'follow_up_sleep_test_after_delivery': {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': 'Follow-up sleep test not completed'
                }
            }
        except Exception as e:
            logger.error(f"Error validating follow-up sleep test for patient {patient_id}: {e}")
            return {
                'follow_up_sleep_test_after_delivery': {
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': f'Error: {str(e)}'
                }
            }
    
    @staticmethod
    def _update_manifest_from_validation(patient_id, validation_results):
        """Update the patient_manifest table with validation results"""
        from flask_app.models import PatientManifest
        from datetime import datetime
        
        try:
            print(f"DEBUG: Updating manifest for patient {patient_id} with {len(validation_results)} results")
            for stage_key, result in validation_results.items():
                print(f"DEBUG: Processing stage {stage_key} -> completed: {result.get('is_completed', False)}")
                # Find the stage definition
                stage_def = None
                for stage in ManifestValidatorService.MANIFEST_DEFINITION:
                    if stage['key'] == stage_key:
                        stage_def = stage
                        break
                
                if not stage_def:
                    logger.warning(f"No manifest definition found for stage {stage_key}")
                    print(f"DEBUG: WARNING - No manifest definition found for stage {stage_key}")
                    continue
                
                # Check if entry exists
                existing = PatientManifest.query.filter_by(
                    patient_id=patient_id,
                    stage_key=stage_key
                ).first()
                
                if existing:
                    # Update existing entry
                    print(f"DEBUG: Updating existing entry for {stage_key}")
                    existing.is_completed = result['is_completed']
                    existing.completion_date = result['completion_date']
                    existing.stage_data = result['stage_data']
                    existing.status_message = result['status_message']
                    existing.updated_at = datetime.utcnow()
                else:
                    # Insert new entry
                    print(f"DEBUG: Creating new entry for {stage_key}")
                    manifest_entry = PatientManifest(
                        patient_id=patient_id,
                        stage_key=stage_key,
                        stage_number=stage_def['stage_number'],
                        stage_name=stage_def['stage_name'],
                        is_completed=result['is_completed'],
                        completion_date=result['completion_date'],
                        stage_data=result['stage_data'],
                        status_message=result['status_message']
                    )
                    db.session.add(manifest_entry)
            
            db.session.commit()
            print(f"DEBUG: Database commit successful for patient {patient_id}")
            
            # Verify the database was actually updated
            updated_entries = PatientManifest.query.filter_by(patient_id=patient_id).all()
            print(f"DEBUG: After update, found {len(updated_entries)} entries in PatientManifest table")
            for entry in updated_entries:
                if entry.is_completed:
                    print(f"DEBUG: COMPLETED STAGE: {entry.stage_key} = {entry.is_completed} (updated: {entry.updated_at})")
            
            logger.info(f"Manifest updated successfully for patient {patient_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating manifest for patient {patient_id}: {e}")
            db.session.rollback()
            return False
