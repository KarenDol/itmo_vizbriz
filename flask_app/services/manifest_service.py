from flask_app import db
from flask_app.models import PatientManifest, Patient, PatientConsultSchedule, PatientDeviceOrder, File, AdminFile
from flask_app.config.manifest_config import get_manifest_definition
from flask_app.config.manifest_config_v2 import get_manifest_definition_v2, get_stage_completion_status
from sqlalchemy import text
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class ManifestService:
    """Service for managing patient manifests"""
    
    @staticmethod
    def initialize_patient_manifest(patient_id):
        """Initialize manifest entries for a new patient"""
        try:
            definition_manifest = get_manifest_definition()
            
            for stage in definition_manifest:
                # Check if manifest entry already exists
                existing = PatientManifest.query.filter_by(
                    patient_id=patient_id,
                    stage_key=stage['key']                ).first()
                
                if not existing:
                    manifest_entry = PatientManifest(
                        patient_id=patient_id,
                        stage_key=stage['key'],
                        stage_number=stage['stage_number'],
                        stage_name=stage['stage_name'],
                        is_completed=False,
                        completion_date=None,
                        stage_data=None,
                        status_message="Not started"
                    )
                    db.session.add(manifest_entry)
            
            db.session.commit()
            logger.info(f"Initialized manifest for patient {patient_id}")
            return True          
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error initializing manifest for patient {patient_id}: {e}")
            return False
    
    @staticmethod
    def update_stage_completion(patient_id, stage_key, is_completed=True, completion_date=None, stage_data=None, status_message=None):
        """Update a specific stage's completion status"""
        try:
            manifest_entry = PatientManifest.query.filter_by(
                patient_id=patient_id,
                stage_key=stage_key
            ).first()
            
            if manifest_entry:
                manifest_entry.is_completed = is_completed
                manifest_entry.completion_date = completion_date
                manifest_entry.stage_data = stage_data
                if status_message:
                    manifest_entry.status_message = status_message
                manifest_entry.updated_at = datetime.utcnow()
                
                db.session.commit()
                logger.info(f"Updated stage {stage_key} for patient {patient_id}")
                return True
            else:
                logger.warning(f"Manifest entry not found for patient {patient_id}, stage {stage_key}")
                return False
                
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating stage {stage_key} for patient {patient_id}: {e}")
            return False
    
    @staticmethod
    def sync_manifest_from_database(patient_id):
        """Sync manifest with actual database state using intelligent completion detection"""
        try:
            definition_manifest = get_manifest_definition()
            updated_stages = []
            
            for stage in definition_manifest:
                stage_key = stage['key']
                manifest_entry = PatientManifest.query.filter_by(
                    patient_id=patient_id,
                    stage_key=stage_key
                ).first()
                
                if not manifest_entry:
                    # Create missing entry
                    manifest_entry = PatientManifest(
                        patient_id=patient_id,
                        stage_key=stage_key,
                        stage_number=stage['stage_number'],
                        stage_name=stage['stage_name'],
                        is_completed=False,
                        completion_date=None,
                        stage_data=None,
                        status_message="Not started"
                    )
                    db.session.add(manifest_entry)
                
                # Get smart completion status from database
                is_completed, completion_date, stage_data, status_message = ManifestService._get_stage_data_from_db(patient_id, stage_key)
                
                # Only update if there's a change to avoid unnecessary database writes
                if (manifest_entry.is_completed != is_completed or 
                    manifest_entry.status_message != status_message or
                    manifest_entry.stage_data != stage_data):
                    
                    manifest_entry.is_completed = is_completed
                    manifest_entry.completion_date = completion_date
                    manifest_entry.stage_data = stage_data
                    manifest_entry.status_message = status_message
                    manifest_entry.updated_at = datetime.utcnow()
                    
                    updated_stages.append({
                        'stage_key': stage_key,
                        'stage_name': stage['stage_name'],
                        'is_completed': is_completed,
                        'status_message': status_message
                    })
            
            db.session.commit()
            
            if updated_stages:
                logger.info(f"Synced manifest for patient {patient_id} - Updated {len(updated_stages)} stages:")
                for stage in updated_stages:
                    status = "✅ COMPLETED" if stage['is_completed'] else "⏳ PENDING"
                    logger.info(f"  {stage['stage_name']}: {status} - {stage['status_message']}")
            else:
                logger.info(f"Synced manifest for patient {patient_id} - No changes needed")
            
            return True          
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error syncing manifest for patient {patient_id}: {e}")
            return False
    
    @staticmethod
    def auto_complete_stages_for_patient(patient_id):
        """Automatically complete stages based on existing data - useful for new patients or data imports"""
        try:
            logger.info(f"Auto-completing stages for patient {patient_id}")
            updated_count = 0
            
            # First ensure manifest exists
            ManifestService.initialize_patient_manifest(patient_id)
            
            # Then sync with smart detection
            if ManifestService.sync_manifest_from_database(patient_id):
                # Count completed stages
                completed_stages = PatientManifest.query.filter_by(
                    patient_id=patient_id,
                    is_completed=True
                ).count()
                
                total_stages = PatientManifest.query.filter_by(patient_id=patient_id).count()
                
                logger.info(f"Auto-completion complete for patient {patient_id}: {completed_stages}/{total_stages} stages completed")
                return True, completed_stages, total_stages
            else:
                return False, 0, 0
                
        except Exception as e:
            logger.error(f"Error auto-completing stages for patient {patient_id}: {e}")
            return False, 0, 0
    
    @staticmethod
    def get_grouped_manifest_for_patient(patient_id):
        """Get manifest using the new grouped stage structure"""
        try:
            grouped_manifest = get_manifest_definition_v2()
            patient_manifest = []
            
            for stage in grouped_manifest:
                stage_key = stage['key']
                status = get_stage_completion_status(patient_id, stage_key)
                
                patient_manifest.append({
                    'stage_number': stage['stage_number'],
                    'stage_name': stage['stage_name'],
                    'key': stage_key,
                    'description': stage['description'],
                    'is_completed': status['complete'],
                    'completion_percentage': status['completion_percentage'],
                    'completed_activities': status['completed_activities'],
                    'total_required': status['total_required'],
                    'activities': status['activities'],
                    'prerequisites': stage['prerequisites'],
                    'next_step': stage['next_step']
                })
            
            return patient_manifest
            
        except Exception as e:
            logger.error(f"Error getting grouped manifest for patient {patient_id}: {e}")
            return []
    
    @staticmethod
    def sync_grouped_manifest_from_database(patient_id):
        """Sync manifest using grouped structure with smart completion detection"""
        try:
            logger.info(f"Syncing grouped manifest for patient {patient_id}")
            grouped_manifest = get_manifest_definition_v2()
            updated_stages = []
            
            for stage in grouped_manifest:
                stage_key = stage['key']
                status = get_stage_completion_status(patient_id, stage_key)
                
                # Check if we need to update the manifest entry
                manifest_entry = PatientManifest.query.filter_by(
                    patient_id=patient_id,
                    stage_key=stage_key
                ).first()
                
                if not manifest_entry:
                    # Create new entry for grouped stage
                    manifest_entry = PatientManifest(
                        patient_id=patient_id,
                        stage_key=stage_key,
                        stage_number=stage['stage_number'],
                        stage_name=stage['stage_name'],
                        is_completed=status['complete'],
                        completion_date=None,  # Will be set based on last activity completion
                        stage_data={
                            'completion_percentage': status['completion_percentage'],
                            'completed_activities': status['completed_activities'],
                            'total_required': status['total_required'],
                            'activities': status['activities']
                        },
                        status_message=f"{status['completed_activities']}/{status['total_required']} activities completed"
                    )
                    db.session.add(manifest_entry)
                    updated_stages.append({
                        'stage_key': stage_key,
                        'stage_name': stage['stage_name'],
                        'is_completed': status['complete'],
                        'status_message': manifest_entry.status_message
                    })
                else:
                    # Update existing entry
                    old_completed = manifest_entry.is_completed
                    old_status = manifest_entry.status_message
                    
                    manifest_entry.is_completed = status['complete']
                    manifest_entry.stage_data = {
                        'completion_percentage': status['completion_percentage'],
                        'completed_activities': status['completed_activities'],
                        'total_required': status['total_required'],
                        'activities': status['activities']
                    }
                    manifest_entry.status_message = f"{status['completed_activities']}/{status['total_required']} activities completed"
                    manifest_entry.updated_at = datetime.utcnow()
                    
                    if old_completed != status['complete'] or old_status != manifest_entry.status_message:
                        updated_stages.append({
                            'stage_key': stage_key,
                            'stage_name': stage['stage_name'],
                            'is_completed': status['complete'],
                            'status_message': manifest_entry.status_message
                        })
            
            db.session.commit()
            
            if updated_stages:
                logger.info(f"Synced grouped manifest for patient {patient_id} - Updated {len(updated_stages)} stages:")
                for stage in updated_stages:
                    status = "✅ COMPLETED" if stage['is_completed'] else "⏳ IN PROGRESS"
                    logger.info(f"  {stage['stage_name']}: {status} - {stage['status_message']}")
            else:
                logger.info(f"Synced grouped manifest for patient {patient_id} - No changes needed")
            
            return True
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error syncing grouped manifest for patient {patient_id}: {e}")
            return False
    
    @staticmethod
    def _get_stage_data_from_db(patient_id, stage_key):
        """Get stage data from database tables with intelligent completion detection"""
        try:
            logger.info(f"Getting stage data for patient {patient_id}, stage {stage_key}")
            # 1. QUIZ COMPLETION - Check for uploaded questionnaire files
            if stage_key == "quiz_completion":
                # Check for questionnaire files with category='medical' and subcategory='questionnaire'
                result = db.session.execute(
                    text("SELECT id, upload_date, name, category, subcategory FROM files WHERE patient_id = :pid AND category = 'medical' AND subcategory = 'questionnaire' ORDER BY upload_date DESC LIMIT 1"),
                    {'pid': patient_id}
                ).first()
                
                if result:
                    return True, result.upload_date, {
                        'file_id': result.id,
                        'filename': result.name,
                        'file_category': result.category,
                        'subcategory': result.subcategory
                    }, f"Questionnaire uploaded on {result.upload_date.strftime('%B %d, %Y')}"
                
                return False, None, None, "No questionnaire uploaded"
            
            # 2. INITIAL CONSULT SCHEDULED - Check for scheduled consultations OR sleep test uploads
            elif stage_key == "initial_consult_scheduled":
                # First check for actual consultation records
                result = db.session.execute(
                    text("SELECT scheduled_datetime, notes FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = 'sleep_expert' ORDER BY scheduled_datetime DESC LIMIT 1"),
                    {'pid': patient_id}
                ).first()
                if result:
                    return True, result.scheduled_datetime, {
                        'notes': result.notes
                    }, f"Consultation scheduled for {result.scheduled_datetime.strftime('%B %d, %Y')}"
                
                # If no consultation record, check if sleep tests are uploaded (indicates consultation happened)
                sleep_test_result = db.session.execute(
                    text("SELECT upload_date FROM files WHERE patient_id = :pid AND category = 'medical' AND subcategory = 'sleep-test' ORDER BY upload_date DESC LIMIT 1"),
                    {'pid': patient_id}
                ).first()
                if sleep_test_result:
                    return True, sleep_test_result.upload_date, {
                        'evidence': 'sleep_test_uploaded'
                    }, f"Consultation implied by sleep test upload on {sleep_test_result.upload_date.strftime('%B %d, %Y')}"
                
                return False, None, None, "No consultation scheduled"
            
            # 3. INITIAL CONSULT COMPLETED - Check for completed consultations OR sleep test uploads
            elif stage_key == "initial_consult_completed":
                # First check for actual completed consultation records
                result = db.session.execute(
                    text("SELECT completed_datetime, comment FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = 'sleep_expert' AND status = 'completed' ORDER BY completed_datetime DESC LIMIT 1"),
                    {'pid': patient_id}
                ).first()
                if result:
                    return True, result.completed_datetime, {
                        'comment': result.comment
                    }, f"Consultation completed on {result.completed_datetime.strftime('%B %d, %Y')}"
                
                # If no completed consultation record, check if sleep tests are uploaded (indicates consultation was completed)
                sleep_test_result = db.session.execute(
                    text("SELECT upload_date FROM files WHERE patient_id = :pid AND category = 'medical' AND subcategory = 'sleep-test' ORDER BY upload_date DESC LIMIT 1"),
                    {'pid': patient_id}
                ).first()
                if sleep_test_result:
                    return True, sleep_test_result.upload_date, {
                        'evidence': 'sleep_test_uploaded'
                    }, f"Consultation completed (implied by sleep test upload on {sleep_test_result.upload_date.strftime('%B %d, %Y')})"
                
                return False, None, None, "Consultation not completed"
            
            # 4. SLEEP STUDY SCHEDULED - Check for sleep doctor consultations
            elif stage_key == "sleep_study_scheduled":
                result = db.session.execute(
                    text("SELECT scheduled_datetime, notes FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = 'sleep_doctor' ORDER BY scheduled_datetime DESC LIMIT 1"),
                    {'pid': patient_id}
                ).first()
                if result:
                    return True, result.scheduled_datetime, {
                        'notes': result.notes
                    }, f"Sleep study consultation scheduled for {result.scheduled_datetime.strftime('%B %d, %Y')}"
                else:
                    return False, None, None, "Sleep study consultation not scheduled"
            
            # 5. SLEEP TEST COMPLETED - Check for uploaded sleep test files
            elif stage_key == "sleep_test_completed":
                result = db.session.execute(
                    text("SELECT id, upload_date, name, category, subcategory FROM files WHERE patient_id = :pid AND category = 'medical' AND subcategory = 'sleep-test' ORDER BY upload_date DESC LIMIT 1"),
                    {'pid': patient_id}
                ).first()
                if result:
                    return True, result.upload_date, {
                        'file_id': result.id,
                        'filename': result.name,
                        'file_category': result.category,
                        'subcategory': result.subcategory
                    }, f"Sleep test uploaded on {result.upload_date.strftime('%B %d, %Y')}"
                else:
                    return False, None, None, "No sleep test uploaded"
            
            # 6. CBCT OBSERVATION REPORT UPLOADED - Check for CBCT files
            elif stage_key == "cbct_observation_report_uploaded":
                result = db.session.execute(
                    text("SELECT id, upload_date, name FROM adminfiles WHERE patient_id = :pid AND file_category = 'CBCT Observations' ORDER BY upload_date DESC LIMIT 1"),
                    {'pid': patient_id}
                ).first()
                if result:
                    return True, result.upload_date, {
                        'file_id': result.id,
                        'filename': result.name
                    }, f"CBCT report uploaded on {result.upload_date.strftime('%B %d, %Y')}"
                else:
                    return False, None, None, "No CBCT report uploaded"
            
            # 7. INTRAORAL SCAN UPLOADED - Check for intraoral scan files
            elif stage_key == "intraoral_scan_uploaded":
                result = db.session.execute(
                    text("SELECT id, upload_date, name FROM files WHERE patient_id = :pid AND category = 'intraoral-scan' ORDER BY upload_date DESC LIMIT 1"),
                    {'pid': patient_id}
                ).first()
                if result:
                    return True, result.upload_date, {
                        'file_id': result.id,
                        'filename': result.name
                    }, f"Intraoral scan uploaded on {result.upload_date.strftime('%B %d, %Y')}"
                else:
                    return False, None, None, "No intraoral scan uploaded"
            
            # 8. HIPAA CONSENT SIGNED - Check for consent files
            elif stage_key == "hipaa_consent_signed":
                result = db.session.execute(
                    text("SELECT id, upload_date, name FROM files WHERE patient_id = :pid AND category = 'billing' AND (name LIKE '%HIPAA%' OR name LIKE '%consent%' OR name LIKE '%authorization%') ORDER BY upload_date DESC LIMIT 1"),
                    {'pid': patient_id}
                ).first()
                if result:
                    return True, result.upload_date, {
                        'file_id': result.id,
                        'filename': result.name
                    }, f"HIPAA consent uploaded on {result.upload_date.strftime('%B %d, %Y')}"
                else:
                    return False, None, None, "No HIPAA consent uploaded"
            
            # 9. OSA REPORT READY - Check for OSA report files
            elif stage_key == "osa_report_ready":
                result = db.session.execute(
                    text("SELECT id, upload_date, name, category, subcategory FROM files WHERE patient_id = :pid AND category = 'medical' AND subcategory = 'reports' ORDER BY upload_date DESC LIMIT 1"),
                    {'pid': patient_id}
                ).first()
                if result:
                    return True, result.upload_date, {
                        'file_id': result.id,
                        'filename': result.name,
                        'file_category': result.category,
                        'subcategory': result.subcategory
                    }, f"OSA report uploaded on {result.upload_date.strftime('%B %d, %Y')}"
                else:
                    return False, None, None, "No OSA report uploaded"
            
            # 10. ORDER ORAL APPLIANCE - Check for device orders
            elif stage_key == "order_oral_appliance":
                result = db.session.execute(
                    text("SELECT id, created_at, device_type, status FROM patient_device_order WHERE patient_id = :pid ORDER BY created_at DESC LIMIT 1"),
                    {'pid': patient_id}
                ).first()
                if result:
                    return True, result.created_at, {
                        'order_id': result.id,
                        'device_type': result.device_type,
                        'status': result.status
                    }, f"Device ordered on {result.created_at.strftime('%B %d, %Y')}"
                else:
                    return False, None, None, "No device ordered"
            
            # 11. DEVICE DELIVERED - Check for device delivery status
            elif stage_key == "device_delivered":
                result = db.session.execute(
                    text("SELECT id, created_at, device_type, status FROM patient_device_order WHERE patient_id = :pid AND status IN ('delivered', 'ready_for_delivery') ORDER BY created_at DESC LIMIT 1"),
                    {'pid': patient_id}
                ).first()
                if result:
                    return True, result.created_at, {
                        'order_id': result.id,
                        'device_type': result.device_type,
                        'status': result.status
                    }, f"Device delivered on {result.created_at.strftime('%B %d, %Y')}"
                else:
                    return False, None, None, "Device not delivered"
            
            # 12. CONSULTATION-BASED STAGES - Check for various consultation types
            elif stage_key in ["schedule_sleep_test_review", "sleep_doctor_followup_completed", 
                              "dental_sleep_doctor_consult_scheduled", "met_with_dental_sleep_expert"]:
                consult_type_map = {
                    "schedule_sleep_test_review": "ep_doctor",
                    "sleep_doctor_followup_completed": "ep_doctor", 
                    "dental_sleep_doctor_consult_scheduled": "dental_sleep_doctor",
                    "met_with_dental_sleep_expert": "dental_sleep_doctor"
                }
                
                consult_type = consult_type_map.get(stage_key)
                if consult_type:
                    if "scheduled" in stage_key:
                        # Check for scheduled consultations
                        result = db.session.execute(
                            text("SELECT scheduled_datetime, notes FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = :consult_type ORDER BY scheduled_datetime DESC LIMIT 1"),
                            {'pid': patient_id, 'consult_type': consult_type}
                        ).first()
                        if result:
                            return True, result.scheduled_datetime, {
                                'notes': result.notes
                            }, f"Consultation scheduled for {result.scheduled_datetime.strftime('%B %d, %Y')}"
                    else:
                        # Check for completed consultations
                        result = db.session.execute(
                            text("SELECT completed_datetime, comment FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = :consult_type AND status = 'completed' ORDER BY completed_datetime DESC LIMIT 1"),
                            {'pid': patient_id, 'consult_type': consult_type}
                        ).first()
                        if result:
                            return True, result.completed_datetime, {
                                'comment': result.comment
                            }, f"Consultation completed on {result.completed_datetime.strftime('%B %d, %Y')}"
                
                return False, None, None, f"No {consult_type} consultation found"
            
            # 13. DELIVERY AND FITTING STAGES
            elif stage_key in ["schedule_oral_appliance_delivery", "oral_appliance_delivery_completed"]:
                result = db.session.execute(
                    text("SELECT id, created_at, status FROM patient_device_order WHERE patient_id = :pid AND status IN ('delivered', 'fitted', 'completed') ORDER BY created_at DESC LIMIT 1"),
                    {'pid': patient_id}
                ).first()
                if result:
                    return True, result.created_at, {
                        'order_id': result.id,
                        'status': result.status
                    }, f"Device {result.status} on {result.created_at.strftime('%B %d, %Y')}"
                else:
                    return False, None, None, "Device not delivered or fitted"
            
            # 14. FOLLOW-UP STAGES
            elif stage_key == "follow_up_sleep_test_after_delivery":
                # Check for follow-up sleep test files
                result = db.session.execute(
                    text("SELECT id, upload_date, name FROM files WHERE patient_id = :pid AND category = 'medical' AND subcategory = 'sleep-test' AND upload_date > (SELECT created_at FROM patient_device_order WHERE patient_id = :pid AND status = 'delivered' ORDER BY created_at DESC LIMIT 1) ORDER BY upload_date DESC LIMIT 1"),
                    {'pid': patient_id}
                ).first()
                if result:
                    return True, result.upload_date, {
                        'file_id': result.id,
                        'filename': result.name
                    }, f"Follow-up sleep test uploaded on {result.upload_date.strftime('%B %d, %Y')}"
                else:
                    return False, None, None, "No follow-up sleep test uploaded"
            
            else:
                return False, None, None, "Unknown stage"
                
        except Exception as e:
            logger.error(f"Error getting stage data for {stage_key}: {e}")
            import traceback
            traceback.print_exc()
            return False, None, None, "Error retrieving data"
    
    @staticmethod
    def get_patient_manifest(patient_id):
        """Get complete manifest for a patient"""
        try:
            manifest_entries = PatientManifest.query.filter_by(
                patient_id=patient_id
            ).order_by(PatientManifest.stage_number).all()
            
            manifest = []
            for entry in manifest_entries:
                # Handle corrupted stage_data that might be a tuple
                stage_data = entry.stage_data
                if isinstance(stage_data, tuple):
                    logger.warning(f"Patient {patient_id}: stage_data is a tuple for stage {entry.stage_key}, converting to dict")
                    stage_data = {}
                
                manifest.append({
                    'stage_number': entry.stage_number,
                    'stage_name': entry.stage_name,
                    'key': entry.stage_key,
                    'is_completed': entry.is_completed,
                    'completion_date': entry.completion_date.isoformat() if entry.completion_date else None,
                    'stage_data': stage_data,
                    'status_message': entry.status_message
                })
            
            return manifest
            
        except Exception as e:
            logger.error(f"Error getting manifest for patient {patient_id}: {e}")
            return []
    
    @staticmethod
    def create_template_manifest():
        """Create a template manifest structure that can be used as a base"""
        from flask_app.config.manifest_config import get_manifest_definition
        from flask_app.config.action_manifest import ACTION_MANIFEST
        
        # Get the manifest definition
        manifest_definition = get_manifest_definition()
        
        # Create template stage manifest
        stage_manifest = []
        for stage in manifest_definition:
            stage_manifest.append({
                "stage_number": stage['stage_number'],
                "stage_name": stage['stage_name'],
                "key": stage['key'],
                "value": "no"  # Default to not completed
            })
        
        # Create template eligible actions
        eligible_actions = []
        for stage in stage_manifest:
            # For template, show actions for all stages (since we don't know completion status)
            for action_key, action_data in ACTION_MANIFEST.items():
                if stage['key'] in action_data.get('stages', []):
                    action_obj = {
                        "action_key": action_key,
                        "stage": stage['key'],
                        "label": action_data['description'],
                        "ui_type": "form" if 'form' in action_data['description'].lower() else "upload_link" if 'upload' in action_data['description'].lower() else "button",
                        "endpoint": action_data['endpoint'],
                        "method": action_data['method'],
                        "input_fields": action_data.get('parameters', []),
                        "message_template": action_data.get('default_message', ''),
                        "ai_guidance": action_data['ai_guidance']
                    }
                    
                    if action_obj["ui_type"] == "upload_link":
                        action_obj["upload_link"] = f"https://app.vizbriz.com/upload/{{patient_id}}/{stage['key']}"
                    elif action_obj["ui_type"] == "form":
                        action_obj["form_url"] = f"/form/{action_key}/{{patient_id}}"
                    
                    eligible_actions.append(action_obj)
        
        return {
            "patient_info": {
                "patient_id": "{patient_id}",
                "name": "{patient_name}",
                "email": "{patient_email}",
                "phone": "{patient_phone}",
                "status": "{patient_status}"
            },
            "stage_manifest": stage_manifest,
            "eligible_actions": eligible_actions
        }
    
    @staticmethod
    def generate_manifest_from_template(patient_id, patient_data, completion_status=None):
        """Generate a dynamic manifest from template using real patient data"""
        try:
            # Get template manifest
            template = ManifestService.create_template_manifest()
            
            # Replace patient info with real data
            template['patient_info']['patient_id'] = patient_id
            template['patient_info']['name'] = patient_data.get('name', 'Unknown Patient')
            template['patient_info']['email'] = patient_data.get('email', '')
            template['patient_info']['phone'] = patient_data.get('phone', '')
            template['patient_info']['status'] = patient_data.get('status', 'Active')
            
            # Update completion status if provided
            if completion_status:
                for stage in template['stage_manifest']:
                    stage_key = stage['key']
                    if stage_key in completion_status:
                        stage['value'] = completion_status[stage_key]
            
            # Update upload links and form URLs with real patient ID
            for action in template['eligible_actions']:
                if 'upload_link' in action:
                    action['upload_link'] = action['upload_link'].replace('{patient_id}', str(patient_id))
                if 'form_url' in action:
                    action['form_url'] = action['form_url'].replace('{patient_id}', str(patient_id))
            
            return template
            
        except Exception as e:
            logger.error(f"Error generating manifest from template for patient {patient_id}: {e}")
            return None
    
    @staticmethod
    def get_patient_current_and_next_stage(patient_id):
        """
        Determine the patient's actual current stage and next stage
        based on the highest completed stage in the manifest
        
        Returns:
            dict: {
                'current_stage_number': decimal,
                'current_stage_name': str,
                'current_stage_key': str,
                'next_stage_number': decimal,
                'next_stage_name': str,
                'next_stage_key': str,
                'workflow_completion_percentage': float,
                'completed_stages_count': int,
                'total_stages_count': int
            }
        """
        try:
            from flask_app.config.manifest_config import get_manifest_definition
            
            # Check for manual stage override first
            override_entry = PatientManifest.query.filter_by(
                patient_id=patient_id,
                stage_key='stage_override'
            ).first()
            
            if override_entry and override_entry.is_completed:
                # Parse the override data
                import json
                try:
                    override_data = json.loads(override_entry.stage_data) if isinstance(override_entry.stage_data, str) else override_entry.stage_data
                    override_stage_key = override_data.get('override_stage_key')
                    override_stage_number = override_data.get('override_stage_number')
                    override_stage_name = override_data.get('override_stage_name')
                    
                    if override_stage_key and override_stage_number and override_stage_name:
                        logger.info(f"Using manual stage override for patient {patient_id}: {override_stage_name} (Stage {override_stage_number})")
                        
                        # Get the full manifest definition to find the next stage
                        manifest_definition = get_manifest_definition()
                        
                        # Find the next stage after the override stage, skipping any skipped stages
                        next_stage = None
                        sorted_stages = sorted(manifest_definition, key=lambda x: x['stage_number'])
                        
                        # Get all stages status to check for skipped stages
                        from flask_app.services.stage_summary_service import evaluate_stage_completion
                        from flask_app.config.stage_summary_manifest import get_stage_summary_manifest
                        
                        all_stages_status = {}
                        try:
                            # Get the stage summary manifest to evaluate stages
                            manifest_entries = get_stage_summary_manifest()
                            # First pass: evaluate all stages
                            for entry in manifest_entries:
                                stage_key = entry.get("key")
                                if stage_key:
                                    completion_result = evaluate_stage_completion(patient_id, entry, all_stages_status)
                                    all_stages_status[stage_key] = completion_result
                            
                            # Second pass: re-evaluate stages with skip_if conditions now that we have all statuses
                            for entry in manifest_entries:
                                stage_key = entry.get("key")
                                if stage_key and entry.get("skip_if"):
                                    completion_result = evaluate_stage_completion(patient_id, entry, all_stages_status)
                                    all_stages_status[stage_key] = completion_result
                        except Exception as e:
                            logger.warning(f"Could not evaluate stage statuses for skipped stage check (override path): {e}")
                            all_stages_status = {}
                        
                        for stage in sorted_stages:
                            if stage['stage_number'] > override_stage_number:
                                # Check if this stage is skipped
                                stage_key = stage['key']
                                stage_status_info = all_stages_status.get(stage_key, {})
                                stage_status = stage_status_info.get('status', 'pending')
                                
                                # Skip over skipped stages
                                if stage_status == 'skipped':
                                    logger.info(f"Skipping stage {stage_key} ({stage['stage_name']}) - marked as skipped (override path)")
                                    continue
                                
                                # Found the next non-skipped stage
                                next_stage = stage
                                break
                        
                        # Calculate workflow completion percentage
                        total_stages = len(manifest_definition)
                        completed_stages = PatientManifest.query.filter_by(
                            patient_id=patient_id,
                            is_completed=True
                        ).count()
                        completion_percentage = (completed_stages / total_stages) * 100 if total_stages > 0 else 0
                        
                        return {
                            'current_stage_number': float(override_stage_number),
                            'current_stage_name': override_stage_name,
                            'current_stage_key': override_stage_key,
                            'next_stage_number': float(next_stage['stage_number']) if next_stage else None,
                            'next_stage_name': next_stage['stage_name'] if next_stage else 'Workflow Complete',
                            'next_stage_key': next_stage['key'] if next_stage else None,
                            'workflow_completion_percentage': round(completion_percentage, 1),
                            'completed_stages_count': completed_stages,
                            'total_stages_count': total_stages
                        }
                except Exception as e:
                    logger.error(f"Error parsing stage override data for patient {patient_id}: {e}")
                    # Fall through to normal logic
            
            # Get all manifest entries for patient, ordered by stage number descending
            manifest_entries = PatientManifest.query.filter_by(
                patient_id=patient_id
            ).order_by(PatientManifest.stage_number.desc()).all()
            
            if not manifest_entries:
                logger.warning(f"No manifest entries found for patient {patient_id}")
                return None
            
            # Get the full manifest definition to find stages
            manifest_definition = get_manifest_definition()
            
            # Find the highest completed stage first
            highest_completed_stage = None
            for entry in manifest_entries:
                if entry.is_completed and entry.completion_date is not None:
                    if not highest_completed_stage or entry.stage_number > highest_completed_stage.stage_number:
                        highest_completed_stage = entry
            
            # Get all stages status to check for skipped stages BEFORE finding current stage
            from flask_app.services.stage_summary_service import evaluate_stage_completion
            from flask_app.config.stage_summary_manifest import get_stage_summary_manifest
            
            all_stages_status = {}
            try:
                # Get the stage summary manifest to evaluate stages
                stage_summary_entries = get_stage_summary_manifest()
                logger.info(f"Evaluating {len(stage_summary_entries)} stages from stage summary manifest for skipped stage detection")
                
                # First pass: evaluate all stages
                for entry in stage_summary_entries:
                    stage_key = entry.get("key")
                    if stage_key:
                        completion_result = evaluate_stage_completion(patient_id, entry, all_stages_status)
                        all_stages_status[stage_key] = completion_result
                        if completion_result.get('status') == 'skipped':
                            logger.info(f"Stage {stage_key} evaluated as SKIPPED: {completion_result.get('skip_reason', 'No reason')}")
                
                # Second pass: re-evaluate stages with skip_if conditions now that we have all statuses
                for entry in stage_summary_entries:
                    stage_key = entry.get("key")
                    if stage_key and entry.get("skip_if"):
                        completion_result = evaluate_stage_completion(patient_id, entry, all_stages_status)
                        all_stages_status[stage_key] = completion_result
                        if completion_result.get('status') == 'skipped':
                            logger.info(f"Stage {stage_key} re-evaluated as SKIPPED: {completion_result.get('skip_reason', 'No reason')}")
                
                logger.info(f"Stage status summary: {len([k for k, v in all_stages_status.items() if v.get('status') == 'skipped'])} skipped stages detected")
            except Exception as e:
                logger.error(f"Could not evaluate stage statuses for skipped stage check: {e}", exc_info=True)
                all_stages_status = {}
            
            # Find the FIRST incomplete, non-skipped stage that the patient can actually work on
            # This will be used as the "next stage" to show what needs to be done next
            # Skip email request stages - they are not actual workflow stages
            next_stage_to_do = None
            sorted_stages = sorted(manifest_definition, key=lambda x: x['stage_number'])
            
            for stage in sorted_stages:
                # Skip email request stages (they are communication actions, not workflow stages)
                stage_key = stage['key']
                if any(keyword in stage_key.lower() for keyword in ['request_sent', 'reminder_sent', 'link_sent']):
                    continue
                
                # Check if this stage is skipped according to stage summary evaluation
                stage_status_info = all_stages_status.get(stage_key, {})
                if stage_status_info.get('status') == 'skipped':
                    logger.info(f"Skipping stage {stage_key} ({stage['stage_name']}) - marked as skipped in stage summary evaluation")
                    continue
                
                # Find the manifest entry for this stage
                stage_entry = next((entry for entry in manifest_entries if entry.stage_key == stage_key), None)
                
                # Check if stage is completed - if so, skip it
                if stage_entry and stage_entry.is_completed:
                    logger.info(f"Skipping stage {stage_key} ({stage['stage_name']}) - already completed")
                    continue
                
                # Check stage status from evaluation - skip if completed
                if stage_status_info.get('status') == 'completed':
                    logger.info(f"Skipping stage {stage_key} ({stage['stage_name']}) - marked as completed in evaluation")
                    continue
                
                if not stage_entry or not stage_entry.is_completed:
                    # For file upload stages, check if the patient actually has the required files
                    if stage_key == 'cbct_observation_report_uploaded':
                        # Check if CBCT files exist in adminfiles
                        from sqlalchemy import text
                        cbct_files = db.session.execute(text("""
                            SELECT COUNT(*) as count FROM adminfiles 
                            WHERE patient_id = :patient_id AND (
                                LOWER(file_category) = LOWER('cbct observations') OR
                                LOWER(file_category) LIKE LOWER('%cbct%') OR
                                LOWER(file_category) LIKE LOWER('%level 2%')
                            )
                        """), {'patient_id': patient_id}).first()
                        
                        if cbct_files.count == 0:
                            # No CBCT files uploaded yet, skip this stage
                            continue
                    
                    elif stage_key == 'intraoral_scan_uploaded':
                        # Check if intraoral scan files exist
                        from sqlalchemy import text
                        scan_files = db.session.execute(text("""
                            SELECT COUNT(*) as count FROM files 
                            WHERE patient_id = :patient_id AND LOWER(subcategory) = LOWER('intraoral-scan')
                        """), {'patient_id': patient_id}).first()
                        
                        if scan_files.count == 0:
                            # No intraoral scan files uploaded yet, skip this stage
                            continue
                    
                    elif stage_key == 'hipaa_consent_signed':
                        # Check if HIPAA consent files exist
                        from sqlalchemy import text
                        hipaa_files = db.session.execute(text("""
                            SELECT COUNT(*) as count FROM files 
                            WHERE patient_id = :patient_id AND (
                                LOWER(subcategory) LIKE LOWER('%hipaa%') OR
                                LOWER(name) LIKE LOWER('%hipaa%') OR
                                LOWER(subcategory) = LOWER('billing')
                            )
                        """), {'patient_id': patient_id}).first()
                        
                        if hipaa_files.count == 0:
                            # No HIPAA consent files uploaded yet, skip this stage
                            continue
                    
                    # This stage is incomplete, not skipped, and the patient can work on it
                    # This is the next stage that needs to be done
                    next_stage_to_do = stage
                    break
            
            # For simplicity: next_stage is the first thing that needs to be done
            # We'll use the highest completed stage as current_stage for tracking purposes
            # but next_stage is what actually needs to be done next
            current_stage = next_stage_to_do  # Use the next stage as current for compatibility
            next_stage = next_stage_to_do     # Next stage is the first pending stage
            
            if not next_stage:
                # All stages completed
                return {
                    'current_stage_number': None,
                    'current_stage_name': 'Workflow Complete',
                    'current_stage_key': None,
                    'next_stage_number': None,
                    'next_stage_name': 'Workflow Complete',
                    'next_stage_key': None,
                    'workflow_completion_percentage': 100.0,
                    'completed_stages_count': len(manifest_entries),
                    'total_stages_count': len(manifest_definition)
                }
            
            logger.info(f"✅ Next stage to do: {next_stage['stage_name']} ({next_stage['key']})")
            
            # Calculate workflow completion percentage
            total_stages = len(manifest_definition)
            completed_stages = sum(1 for entry in manifest_entries if entry.is_completed)
            completion_percentage = (completed_stages / total_stages) * 100 if total_stages > 0 else 0
            
            result = {
                'current_stage_number': float(current_stage['stage_number']),
                'current_stage_name': current_stage['stage_name'],
                'current_stage_key': current_stage['key'],
                'next_stage_number': float(next_stage['stage_number']) if next_stage else None,
                'next_stage_name': next_stage['stage_name'] if next_stage else 'Workflow Complete',
                'next_stage_key': next_stage['key'] if next_stage else None,
                'workflow_completion_percentage': round(completion_percentage, 1),
                'completed_stages_count': completed_stages,
                'total_stages_count': total_stages
            }
            
            logger.info(f"Patient {patient_id} - Current: Stage {result['current_stage_number']} ({result['current_stage_name']}) -> Next: {result['next_stage_name']}")
            
            # Log detailed info about skipped stages
            if result.get('next_stage_key'):
                logger.info(f"✅ Next stage selected: {result['next_stage_key']} ({result['next_stage_name']})")
            else:
                logger.warning(f"⚠️ No next stage found - all subsequent stages may be skipped or completed")
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting current/next stage for patient {patient_id}: {e}", exc_info=True)
            # Return a default structure instead of None to prevent null values in execution manifest
            return {
                'current_stage_number': None,
                'current_stage_name': 'Error determining stage',
                'current_stage_key': None,
                'next_stage_number': None,
                'next_stage_name': 'Error determining stage',
                'next_stage_key': None,
                'workflow_completion_percentage': 0,
                'completed_stages_count': 0,
                'total_stages_count': 0
            }