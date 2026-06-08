"""
Cursor API Routes for LLM-Driven Patient Journey Management
Provides execution manifest and action endpoints for Cursor integration.
"""

from flask import Blueprint, jsonify, request, current_app
from flask_login import login_required
from datetime import datetime
import os

cursor_bp = Blueprint('cursor', __name__)

@cursor_bp.route('/cursor_test', methods=['GET'])
def cursor_test():
    """Test endpoint to verify cursor blueprint is working."""
    return jsonify({"status": "cursor blueprint working"})

@cursor_bp.route('/execution_manifest/<int:patient_id>', methods=['GET'])
def get_execution_manifest(patient_id):
    """
    Generate execution manifest for LLM-driven patient journey management.
    Uses template manifest and populates with real patient data.
    
    Returns:
        JSON with patient_info, stage_manifest, and eligible_actions
    """
    print(f"🌐 DEBUG: get_execution_manifest() CALLED for patient {patient_id}")
    import logging
    import json
    import os
    logger = logging.getLogger(__name__)
    
    logger.info(f"=== EXECUTION_MANIFEST STARTED for patient_id: {patient_id} ===")
    
    try:
        # Load template manifest
        template_file = 'execution_manifest_10279_vizbriz.json'
        logger.info(f"Loading template manifest from: {template_file}")
        
        if not os.path.exists(template_file):
            logger.error(f"Template file not found: {template_file}")
            return jsonify({"error": "Template manifest not found"}), 500
        
        with open(template_file, 'r') as f:
            template_manifest = json.load(f)
        
        logger.info(f"Loaded template manifest with {len(template_manifest.get('stage_manifest', []))} stages")
        
        # Debug: Check the first few eligible_actions
        eligible_actions_template = template_manifest.get('eligible_actions', [])
        logger.info(f"Template has {len(eligible_actions_template)} eligible actions")
        for i, action in enumerate(eligible_actions_template[:3]):
            logger.info(f"Action {i}: type={type(action)}, keys={list(action.keys()) if isinstance(action, dict) else 'Not a dict'}")
        
        # Get patient info from database
        from flask_app.models import Patient
        from flask_app.services.manifest_service import ManifestService
        
        # Get comprehensive patient data including consultations
        from flask_app.routes.main_routes import fetch_patient_details
        patient_details = fetch_patient_details(patient_id)
        
        patient = patient_details.get('patient')
        if not patient:
            logger.error(f"Patient {patient_id} not found")
            return jsonify({"error": f"Patient {patient_id} not found"}), 404
        
        # Get comprehensive patient information
        uploaded_files = patient_details.get('uploaded_files', {})
        scheduled_consultations = patient_details.get('scheduled_consultations', [])
        patient_statuses = patient_details.get('patient_statuses', {})
        comments = patient_details.get('comments', [])
        
        # Build comprehensive patient info
        patient_info = {
            "patient_id": patient.id,
            "name": patient.name,
            "email": patient.email,
            "phone": patient.phone,
            "date_of_birth": patient.dob.isoformat() if patient.dob else None,
            "preferred_language": "en",
            "status": patient.status,
            "payment_method": getattr(patient, 'payment_method', 'N/A'),
            "gender": getattr(patient, 'gender', 'Unknown'),
            "last_visit": patient.last_update.strftime('%Y-%m-%d') if patient.last_update else None,
            "osa_risk_score": getattr(patient, 'osa_risk_score', None),
            "uploaded_files": {
                category: len(files) for category, files in uploaded_files.items()
            },
            "scheduled_consultations": scheduled_consultations,
            "patient_statuses": {
                status_type: status.status_value for status_type, status in patient_statuses.items()
            },
            "comments": comments
        }
        
        logger.info(f"Found patient: {patient_info['name']} (ID: {patient_id})")
        
        # Get patient's current manifest from database
        manifest_entries = ManifestService.get_patient_manifest(patient_id)
        
        # If no manifest entries exist, initialize them
        if not manifest_entries:
            logger.info(f"Initializing manifest for patient {patient_id}")
            ManifestService.initialize_patient_manifest(patient_id)
            manifest_entries = ManifestService.get_patient_manifest(patient_id)
        
        # AUTO-UPDATE MANIFEST: Validate and update patient manifest stages
        print(f"DEBUG: Auto-updating manifest for patient {patient_id} via get_execution_manifest")
        logger.info(f"DEBUG: Auto-updating manifest for patient {patient_id} via get_execution_manifest")
        try:
            from flask_app.services.manifest_validator import ManifestValidatorService
            validation_results = ManifestValidatorService.validate_and_update_patient_stages(patient_id)
            if validation_results:
                completed_count = sum(1 for result in validation_results.values() if result.get('is_completed', False))
                logger.info(f"Manifest auto-validation completed: {completed_count}/{len(validation_results)} stages completed")
                print(f"DEBUG: Manifest auto-validation completed: {completed_count}/{len(validation_results)} stages completed")
            else:
                logger.warning(f"Manifest validation returned None for patient {patient_id}")
                print(f"DEBUG: Manifest validation returned None for patient {patient_id}")
        except Exception as e:
            logger.error(f"Error in manifest auto-validation for patient {patient_id}: {e}")
            print(f"DEBUG: Error in manifest auto-validation: {e}")
            import traceback
            traceback.print_exc()
        
        # Skip sync_manifest_from_database since ManifestValidatorService already updated everything
        logger.info(f"Skipping sync_manifest_from_database since ManifestValidatorService already updated the manifest for patient {patient_id}")
        
        # Get updated manifest entries directly from PatientManifest table
        from flask_app.models import PatientManifest
        manifest_db_entries = PatientManifest.query.filter_by(patient_id=patient_id).all()
        manifest_entries = []
        for entry in manifest_db_entries:
            manifest_entries.append({
                'stage_key': entry.stage_key,
                'stage_number': entry.stage_number,
                'stage_name': entry.stage_name,
                'is_completed': entry.is_completed,
                'completion_date': entry.completion_date,
                'stage_data': entry.stage_data,
                'status_message': entry.status_message,
                'updated_at': entry.updated_at
            })
        print(f"DEBUG: Read {len(manifest_entries)} entries directly from PatientManifest table")
        
        # Debug: Check what entries we have
        print(f"DEBUG: manifest_entries type: {type(manifest_entries)}")
        if manifest_entries:
            print(f"DEBUG: Sample manifest entry keys: {list(manifest_entries[0].keys()) if manifest_entries else 'No entries'}")
            sleep_entries = [entry for entry in manifest_entries if 'sleep' in entry.get('stage_key', '').lower()]
            for entry in sleep_entries:
                print(f"DEBUG: Sleep entry: {entry.get('stage_key')} = completed:{entry.get('is_completed')}")
        else:
            print("DEBUG: No manifest entries found!")
        
        # Update stage manifest with database entries (after ManifestValidatorService has updated them)
        stage_manifest = []
        print(f"DEBUG: Building stage manifest from {len(manifest_entries)} database entries")
        logger.info(f"DEBUG: Building stage manifest from {len(manifest_entries)} database entries")
        for stage in template_manifest['stage_manifest']:
            try:
                # Use database entries from PatientManifest table (updated by ManifestValidatorService)
                manifest_entry = next((entry for entry in manifest_entries if entry.get('stage_key') == stage['key']), None)
                print(f"DEBUG: Stage {stage['key']} - Found in PatientManifest: {manifest_entry is not None}")
                
                if manifest_entry:
                    is_completed = manifest_entry.get('is_completed', False)
                    completion_date = manifest_entry.get('completion_date')
                    stage_data = manifest_entry.get('stage_data')
                    status_message = manifest_entry.get('status_message', 'No status message')
                else:
                    # Fallback to smart detection if no database entry found
                    result = ManifestService._get_stage_data_from_db(patient_id, stage['key'])
                    
                    # Ensure we got a proper tuple
                    if isinstance(result, tuple) and len(result) == 4:
                        is_completed, completion_date, stage_data, status_message = result
                    else:
                        logger.error(f"Unexpected result format for stage {stage['key']}: {result}")
                        is_completed, completion_date, stage_data, status_message = False, None, None, "Error retrieving data"
                
                # Convert boolean to string for consistency with template
                stage_value = "yes" if is_completed else "no"
            except Exception as e:
                logger.error(f"Error processing stage {stage['key']}: {e}")
                is_completed, completion_date, stage_data, status_message = False, None, None, "Error retrieving data"
                stage_value = "no"

            stage_manifest.append({
                "stage_number": stage['stage_number'],
                "stage_name": stage['stage_name'],
                "key": stage['key'],
                "value": stage_value,
                "completion_date": completion_date.isoformat() if completion_date else None,
                "stage_data": stage_data,
                "status_message": status_message
            })
        
        logger.info(f"Updated stage manifest with smart completion detection")
        logger.info(f"Stage manifest summary: {len(stage_manifest)} stages")
        for stage in stage_manifest[:5]:  # Log first 5 stages for debugging
            logger.info(f"  Stage {stage['stage_number']}: {stage['stage_name']} = {stage['value']} ({stage.get('status_message', 'No message')})")
        
        # Special focus on quiz completion
        quiz_stage = next((s for s in stage_manifest if s['key'] == 'quiz_completion'), None)
        if quiz_stage:
            logger.info(f"QUIZ STAGE DEBUG: {quiz_stage['stage_name']} = {quiz_stage['value']} - {quiz_stage.get('status_message', 'No message')}")
            if quiz_stage['value'] == 'yes':
                logger.info(f"✅ QUIZ DETECTED AS COMPLETED - Should not show quiz actions")
            else:
                logger.warning(f"❌ QUIZ NOT DETECTED AS COMPLETED - Will show quiz actions")
        
        # Additional debugging for stage 0 issue
        if stage_manifest:
            first_stage = stage_manifest[0]
            logger.info(f"FIRST STAGE DEBUG: stage_number={first_stage.get('stage_number')}, stage_name='{first_stage.get('stage_name')}', key='{first_stage.get('key')}', value='{first_stage.get('value')}'")
            
            # Check if any stage has stage_number 0
            stage_0_found = any(stage.get('stage_number') == 0 for stage in stage_manifest)
            if stage_0_found:
                logger.warning(f"WARNING: Found stage with stage_number=0 in manifest!")
                for stage in stage_manifest:
                    if stage.get('stage_number') == 0:
                        logger.warning(f"  Stage 0: {stage}")
            else:
                logger.info(f"CONFIRMED: No stage with stage_number=0 found in manifest")
        
        # Get current and next stage using the new logic (highest completed, not first incomplete)
        stage_info = ManifestService.get_patient_current_and_next_stage(patient_id)
        logger.info(f"Current stage info type: {type(stage_info)}, value: {stage_info}")
        
        # Validate stage_info is a dict
        if stage_info and not isinstance(stage_info, dict):
            logger.error(f"stage_info is not a dict! Type: {type(stage_info)}, converting to None")
            stage_info = None
        
        # Filter eligible actions based on current and next stage
        # Only show actions relevant to current/next stage, not all incomplete stages
        eligible_actions = []
        
        # Get current and next stage keys with proper validation
        current_stage_key = None
        next_stage_key = None
        
        if stage_info and isinstance(stage_info, dict):
            current_stage_key = stage_info.get('current_stage_key')
            next_stage_key = stage_info.get('next_stage_key')
            logger.info(f"Filtering actions for current_stage: {current_stage_key}, next_stage: {next_stage_key}")
        else:
            logger.warning(f"stage_info is not valid (type: {type(stage_info)}), will show all incomplete stage actions")
            # Fallback: show all incomplete actions if we can't determine current/next stage
        
        for action in template_manifest['eligible_actions']:
            # Debug: Check if action is a tuple or dict
            if not isinstance(action, dict):
                logger.error(f"Action is not a dict: {type(action)} - {action}")
                continue
                
            # Find the corresponding stage
            stage = next((s for s in stage_manifest if s['key'] == action['stage']), None)
            
            if not stage:
                logger.warning(f"Action '{action.get('action_key', 'unknown')}' references stage '{action['stage']}' which is not in stage_manifest")
                continue
                
            # Check if action should be available
            stage_completed = stage['value'] == 'yes'
            available_after_completion = action.get('available_after_completion', False)
            action_stage_key = action.get('stage')
            
            # IMPROVED LOGIC: Only show actions for current stage and next stage
            # This prevents showing actions from previous stages that are already completed
            is_current_stage = (current_stage_key and action_stage_key == current_stage_key)
            is_next_stage = (next_stage_key and action_stage_key == next_stage_key)
            
            is_relevant_stage = (
                (is_current_stage and not stage_completed) or  # Current stage if not completed
                (is_next_stage) or  # Next stage (regardless of completion status)
                available_after_completion  # Or actions marked as available after completion
            )
            
            # Debug logging for sleep-related actions
            if 'sleep' in action.get('action_key', '').lower() or 'sleep' in action.get('stage', '').lower():
                logger.info(f"ACTION FILTER: {action.get('action_key', 'unknown')}")
                logger.info(f"  -> stage: '{action['stage']}'")
                logger.info(f"  -> current_stage: {current_stage_key}")
                logger.info(f"  -> next_stage: {next_stage_key}")
                logger.info(f"  -> is_current_stage: {is_current_stage}")
                logger.info(f"  -> is_next_stage: {is_next_stage}")
                logger.info(f"  -> stage_completed: {stage_completed}")
                logger.info(f"  -> available_after_completion: {available_after_completion}")
                logger.info(f"  -> is_relevant_stage: {is_relevant_stage}")
                logger.info(f"  -> WILL BE INCLUDED: {is_relevant_stage and (not stage_completed or available_after_completion)}")
            
            # Include action if:
            # 1. Action is relevant AND stage is not completed, OR
            # 2. Action is marked as available_after_completion
            if is_relevant_stage and (not stage_completed or available_after_completion):
                # Update action with patient-specific data
                action_copy = action.copy()
                if 'upload_link' in action_copy:
                    action_copy['upload_link'] = action_copy['upload_link'].replace('10279', str(patient_id))
                eligible_actions.append(action_copy)
        
        logger.info(f"Filtered to {len(eligible_actions)} eligible actions for current/next stage (was showing all incomplete stages)")
        
        # INTELLIGENT ACTION FILTERING: Remove actions that don't make sense given uploaded files
        intelligent_actions = []
        sleep_test_count = patient_info.get('uploaded_files', {}).get('sleep_test', 0)
        questionnaire_count = patient_info.get('uploaded_files', {}).get('questionnaire', 0)
        reports_count = patient_info.get('uploaded_files', {}).get('reports', 0)
        
        for action in eligible_actions:
            action_key = action.get('action_key', '')
            should_include = True
            exclusion_reason = None
            
            # If sleep tests are uploaded, remove sleep-related scheduling actions
            if sleep_test_count > 0:
                if action_key in ['schedule_sleep_study', 'remind_sleep_study', 'request_sleep_test_files']:
                    should_include = False
                    exclusion_reason = f"Sleep tests already uploaded ({sleep_test_count} files)"
                elif action_key in ['schedule_sleep_test_review', 'complete_sleep_doctor_followup']:
                    should_include = False
                    exclusion_reason = f"Sleep tests uploaded - these steps are implied complete"
            
            # If questionnaire is uploaded, remove quiz-related actions
            if questionnaire_count > 0:
                if 'quiz' in action_key.lower() or 'questionnaire' in action_key.lower():
                    should_include = False
                    exclusion_reason = f"Questionnaire already uploaded ({questionnaire_count} files)"
            
            # If reports are uploaded, remove report request actions
            if reports_count > 0:
                if action_key in ['request_osa_report']:
                    should_include = False
                    exclusion_reason = f"Reports already uploaded ({reports_count} files)"
            
            if should_include:
                intelligent_actions.append(action)
            else:
                logger.info(f"🤖 INTELLIGENT FILTER: Excluded '{action_key}' - {exclusion_reason}")
        
        logger.info(f"🤖 INTELLIGENT FILTERING: {len(eligible_actions)} -> {len(intelligent_actions)} actions (removed {len(eligible_actions) - len(intelligent_actions)} irrelevant actions)")
        eligible_actions = intelligent_actions
        
        # Debug: Log sleep test related stages
        sleep_stages = [s for s in stage_manifest if 'sleep' in s.get('key', '').lower()]
        for stage in sleep_stages:
            logger.info(f"SLEEP STAGE: {stage['key']} -> value: {stage['value']} -> completed: {stage['value'] == 'yes'}")
        
        # Debug: Check specific sleep_study_scheduled stage for action filtering
        sleep_study_stage = next((s for s in stage_manifest if s['key'] == 'sleep_study_scheduled'), None)
        if sleep_study_stage:
            logger.info(f"🎯 SLEEP_STUDY_SCHEDULED DEBUG: value='{sleep_study_stage['value']}', completed={sleep_study_stage['value'] == 'yes'}")
            print(f"🎯 SLEEP_STUDY_SCHEDULED DEBUG: value='{sleep_study_stage['value']}', completed={sleep_study_stage['value'] == 'yes'}")
        
        # Log which quiz-related actions are being included/excluded
        quiz_actions = [a for a in eligible_actions if 'quiz' in a.get('action_key', '').lower()]
        if quiz_actions:
            logger.warning(f"❌ QUIZ ACTIONS STILL SHOWING: {[a['action_key'] for a in quiz_actions]}")
        else:
            logger.info(f"✅ NO QUIZ ACTIONS SHOWING - Quiz stage properly detected as complete")
        
        # Ensure all eligible_actions are dictionaries, not tuples
        cleaned_eligible_actions = []
        for i, action in enumerate(eligible_actions):
            if isinstance(action, dict):
                cleaned_eligible_actions.append(action)
            else:
                logger.warning(f"Patient {patient_id}: eligible_actions[{i}] is not a dict: {type(action)}, value: {action}")
                # Convert tuple to dict if possible, otherwise skip
                if isinstance(action, tuple):
                    try:
                        # Try to convert tuple to dict based on expected structure
                        action_dict = {
                            'action_key': action[0] if len(action) > 0 else 'unknown',
                            'label': action[1] if len(action) > 1 else 'Unknown Action',
                            'ui_type': action[2] if len(action) > 2 else 'button',
                            'endpoint': action[3] if len(action) > 3 else '',
                            'input_fields': action[4] if len(action) > 4 else []
                        }
                        cleaned_eligible_actions.append(action_dict)
                        logger.info(f"Patient {patient_id}: Converted tuple to dict: {action_dict}")
                    except Exception as e:
                        logger.error(f"Patient {patient_id}: Failed to convert tuple to dict: {e}")
                else:
                    logger.error(f"Patient {patient_id}: Skipping non-dict, non-tuple action: {type(action)}")
        
        eligible_actions = cleaned_eligible_actions
        logger.info(f"After cleaning: {len(eligible_actions)} eligible actions")
        
        # Filter out email actions that have already been sent
        from flask_app.services.manifest_validator import ManifestValidatorService
        eligible_actions = ManifestValidatorService.filter_already_sent_email_actions(patient_id, eligible_actions)
        logger.info(f"After email filtering: {len(eligible_actions)} eligible actions")
        
        # stage_info already defined above
        
        # Build complete response with timestamp for validation
        current_time = datetime.now().isoformat()
        response_data = {
            "manifest_created_at": current_time,
            "manifest_version": "1.0",
            "patient_info": patient_info,
            "stage_manifest": stage_manifest,
            "eligible_actions": eligible_actions,
            "current_stage": {
                "stage_number": stage_info.get('current_stage_number') if stage_info else None,
                "stage_name": stage_info.get('current_stage_name') if stage_info else 'Unknown',
                "stage_key": stage_info.get('current_stage_key') if stage_info else None
            } if stage_info else None,
            "next_stage": {
                "stage_number": stage_info.get('next_stage_number') if stage_info else None,
                "stage_name": stage_info.get('next_stage_name') if stage_info else 'Unknown',
                "stage_key": stage_info.get('next_stage_key') if stage_info else None
            } if stage_info else None,
            "workflow_progress": {
                "completion_percentage": stage_info.get('workflow_completion_percentage') if stage_info else 0,
                "completed_stages": stage_info.get('completed_stages_count') if stage_info else 0,
                "total_stages": stage_info.get('total_stages_count') if stage_info else 0
            } if stage_info else None
        }
        
        logger.info(f"MANIFEST TIMESTAMP: {current_time}")
        
        logger.info(f"=== EXECUTION_MANIFEST SUCCESS for patient_id: {patient_id} ===")
        logger.info(f"  - patient_info keys: {list(patient_info.keys())}")
        logger.info(f"  - stage_manifest count: {len(stage_manifest)}")
        logger.info(f"  - eligible_actions count: {len(eligible_actions)}")
        
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"=== EXECUTION_MANIFEST ERROR for patient_id: {patient_id} ===")
        logger.error(f"Error generating execution manifest: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({"error": f"Failed to generate execution manifest: {str(e)}"}), 500

# Atomic Action APIs - Each is self-sufficient and uses manifest data

@cursor_bp.route('/api/send_hipaa_consent_email', methods=['POST'])
@login_required
def send_hipaa_consent_email():
    """Send HIPAA consent request email to patient"""
    logger.info("=== HIPAA CONSENT EMAIL API STARTED ===")
    
    try:
        logger.info("Parsing request data...")
        data = request.get_json()
        logger.info(f"Request data received: {data}")
        
        # Validate required fields
        required_fields = ['patient_id', 'patient_email', 'request_date', 'message']
        logger.info(f"Validating required fields: {required_fields}")
        
        for field in required_fields:
            if field not in data:
                logger.error(f"Missing required field: {field}")
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        logger.info("All required fields present")
        
        patient_id = data['patient_id']
        patient_email = data['patient_email']
        request_date = data['request_date']
        message = data['message']
        
        logger.info(f"Extracted data - patient_id: {patient_id}, email: {patient_email}, date: {request_date}")
        
        # Get patient info
        logger.info("Fetching patient from database...")
        from flask_app.models import Patient
        patient = Patient.query.get(patient_id)
        if not patient:
            logger.error(f"Patient {patient_id} not found in database")
            return jsonify({"error": f"Patient {patient_id} not found"}), 404
        
        logger.info(f"Patient found: {patient.name} (ID: {patient_id})")
        
        # Get clinic_id from current_user
        logger.info("Getting clinic_id from current_user...")
        clinic_id = None
        if hasattr(current_user, 'get_primary_clinic_id'):
            clinic_id = current_user.get_primary_clinic_id()
            logger.info(f"Got clinic_id from get_primary_clinic_id(): {clinic_id}")
        elif hasattr(current_user, 'clinics') and current_user.clinics.first():
            clinic_id = current_user.clinics.first().id
            logger.info(f"Got clinic_id from clinics.first(): {clinic_id}")
        
        if not clinic_id:
            logger.error("No clinic found for current user")
            return jsonify({"error": "No clinic found for current user"}), 400
        
        logger.info(f"Using clinic_id: {clinic_id}")
        
        # Generate patient wizard link
        import os
        base_url = os.getenv('BASE_URL', 'http://localhost:7000')
        wizard_link = f"{base_url}/wizard/stage1_personal_info?clinic_id={clinic_id}"
        logger.info(f"Generated wizard link: {wizard_link}")
        
        # Generate upload link
        upload_link = f"{base_url}/upload/{patient_id}/hipaa_consent_signed"
        logger.info(f"Generated upload link: {upload_link}")
        
        # Create email content using message_template from manifest
        email_subject = "HIPAA Consent Forms Required"
        
        # Get message template from the execution manifest
        template_file = 'execution_manifest_10279_vizbriz.json'
        with open(template_file, 'r') as f:
            template_manifest = json.load(f)
        
        # Find the request_hipaa_consent action and get its message_template
        message_template = ""
        for action in template_manifest['eligible_actions']:
            if action['action_key'] == 'request_hipaa_consent':
                message_template = action['message_template']
                break
        
        # Create email body using the template
        email_body = f"""Dear {patient.name},

{message_template}

Patient Wizard Link:
{wizard_link}

This is required to continue with your treatment plan."""
        
        logger.info(f"Email subject: {email_subject}")
        logger.info(f"Email body length: {len(email_body)} characters")
        
        # Send actual email using Flask-Mail
        logger.info("Attempting to send email via Flask-Mail...")
        from flask_mail import Message
        from flask_app import mail
        
        try:
            logger.info("Creating Message object...")
            msg = Message(
                subject=email_subject,
                recipients=[patient_email],
                body=email_body,
                sender=current_app.config.get('MAIL_DEFAULT_SENDER', 'info@vizbriz.com')
            )
            logger.info(f"Message created - From: {msg.sender}, To: {msg.recipients}, Subject: {msg.subject}")
            
            logger.info("Sending email via mail.send()...")
            mail.send(msg)
            
            logger.info(f"HIPAA consent email sent successfully to {patient_email}")
            logger.info(f"Email subject: {email_subject}")
            logger.info(f"Wizard link: {wizard_link}")
            logger.info(f"Upload link: {upload_link}")
            
        except Exception as email_error:
            logger.error(f"Failed to send email: {str(email_error)}")
            logger.error(f"Email error type: {type(email_error).__name__}")
            import traceback
            logger.error(f"Email error traceback: {traceback.format_exc()}")
            return jsonify({"error": f"Failed to send email: {str(email_error)}"}), 500
        
        # Update patient manifest to track this action
        logger.info("Updating patient manifest...")
        from flask_app.services.manifest_service import ManifestService
        try:
            ManifestService.update_stage_completion(
                patient_id=patient_id,
                stage_key='hipaa_consent_signed',
                is_completed=False,
                status_message=f"HIPAA consent email sent on {request_date}"
            )
            logger.info("Patient manifest updated successfully")
        except Exception as manifest_error:
            logger.error(f"Failed to update manifest: {str(manifest_error)}")
            # Don't fail the whole request if manifest update fails
        
        logger.info("=== HIPAA CONSENT EMAIL API SUCCESS ===")
        return jsonify({
            "status": "success",
            "message": "HIPAA consent email sent successfully",
            "patient_email": patient_email,
            "wizard_link": wizard_link,
            "upload_link": upload_link,
            "clinic_id": clinic_id
        })
        
    except Exception as e:
        logger.error("=== HIPAA CONSENT EMAIL API ERROR ===")
        logger.error(f"Error in send_hipaa_consent_email: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        return jsonify({"error": f"Failed to send HIPAA consent email: {str(e)}"}), 500

@cursor_bp.route('/cursor_api/actions/request_cbct_files', methods=['POST'])
@login_required
def request_cbct_files():
    """Execute CBCT request - atomic and self-sufficient."""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['patient_id', 'request_date', 'message']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        # Send email using existing function
        from flask_app.routes.action_routes import execute_request_cbct_files
        
        result = execute_request_cbct_files(data)
        
        if result.get('status') == 'success':
            return jsonify({"status": "sent", "message": result.get('message')})
        else:
            return jsonify({"error": result.get('message')}), 400
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cursor_bp.route('/cursor_api/actions/send_quiz_link', methods=['POST'])
@login_required
def send_quiz_link():
    """Execute send quiz action - atomic and self-sufficient."""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['patient_id', 'quiz_type']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        # Send quiz using existing function
        from flask_app.routes.action_routes import execute_send_quiz_link
        
        result = execute_send_quiz_link(data)
        
        if result.get('status') == 'success':
            return jsonify({"status": "sent", "message": result.get('message')})
        else:
            return jsonify({"error": result.get('message')}), 400
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cursor_bp.route('/cursor_api/actions/schedule_consultation', methods=['POST'])
@login_required
def schedule_consultation():
    """Execute schedule consultation action - atomic and self-sufficient."""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['patient_id', 'scheduled_date', 'scheduled_time', 'doctor_name', 'notes']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        # Schedule using existing function
        from flask_app.routes.action_routes import execute_schedule_consultation
        
        result = execute_schedule_consultation(data)
        
        if result.get('status') == 'success':
            return jsonify({"status": "scheduled", "message": result.get('message')})
        else:
            return jsonify({"error": result.get('message')}), 400
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cursor_bp.route('/cursor_api/actions/complete_consultation', methods=['POST'])
@login_required
def complete_consultation():
    """Execute complete consultation action - atomic and self-sufficient."""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['patient_id', 'completion_date', 'completion_time', 'doctor_name', 'notes']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        # Complete using existing function
        from flask_app.routes.action_routes import execute_complete_consultation
        
        result = execute_complete_consultation(data)
        
        if result.get('status') == 'success':
            return jsonify({"status": "completed", "message": result.get('message')})
        else:
            return jsonify({"error": result.get('message')}), 400
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500 

 