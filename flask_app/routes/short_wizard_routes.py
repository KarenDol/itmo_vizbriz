from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from flask_app.models import db, Patient, File, Dentist, AdminFile, Claim, Comment, PatientStatus, StatusOption, PatientComment
from flask_app.extensions import db
import logging
import boto3 
from botocore.config import Config
import os
import logging
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text, func
from datetime import datetime, timedelta
from io import BytesIO
import zipfile
from flask import current_app, request, url_for, redirect, flash
from werkzeug.security import check_password_hash
from flask import request, jsonify, render_template, current_app as app
from datetime import datetime
from werkzeug.utils import secure_filename
import logging
import os
import tempfile
import base64
import re
from datetime import datetime
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash
from flask_login import login_required, current_user
import tempfile
import secrets
import io
import qrcode
from flask import send_file
from flask_app.models import Clinic, DSO, File


# Create the Blueprint for short wizard routes
short_wizard = Blueprint('short_wizard', __name__)
logger = logging.getLogger(__name__)

# Use same S3 configuration as original wizard
def get_s3_client():
    """Get S3 client with same configuration as original wizard"""
    return boto3.client(
        's3',
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        region_name=os.getenv('AWS_REGION', 'us-west-2')
    )


@short_wizard.route('/short_wizard/clear_session', methods=['POST'])
def clear_session():
    """Clear session data for debugging"""
    logger.info(f'Session before clear: {dict(session)}')
    session.clear()
    logger.info(f'Session after clear: {dict(session)}')
    return jsonify({'status': 'success', 'message': 'Session cleared'})


@short_wizard.route('/short_wizard/test_redirect')
def test_redirect():
    """Test redirect functionality"""
    return redirect(url_for('short_wizard.stage2_sleep_info'))


@short_wizard.route('/short_wizard/stage1_personal_info', methods=['GET', 'POST'])
def stage1_personal_info():
    logger.info('Entered short_wizard stage1_personal_info route')
    logger.info(f'Current session data: {dict(session)}')
    
    # Accept clinic_id and dentist_id from GET, POST, or session
    clinic_id = request.args.get('clinic_id') or request.form.get('clinic_id') or session.get('clinic_id')
    dentist_id = request.args.get('dentist_id') or request.form.get('dentist_id') or session.get('dentist_id')
    
    if clinic_id:
        session['clinic_id'] = clinic_id
    if dentist_id:
        session['dentist_id'] = dentist_id
        clinic = Clinic.query.get(clinic_id)
        clinic_dict = {
            'id': clinic.id,
            'name': clinic.name,
            'address': clinic.address,
            'phone': clinic.telephone,
            'email': clinic.email
        } if clinic else None
    else:
        clinic = None
        clinic_dict = None
    
    if request.method == 'POST':
        logger.info('POST request received for stage1_personal_info')
        logger.info(f'Form data: {dict(request.form)}')
        logger.info(f'Request method: {request.method}')
        logger.info(f'Request URL: {request.url}')
        try:
            # Store personal information in session
            personal_info = {
                'first_name': request.form.get('first_name'),
                'last_name': request.form.get('last_name'),
                'email': request.form.get('email'),
                'phone': request.form.get('phone'),
                'date_of_birth': request.form.get('date_of_birth'),
                'address': request.form.get('address', ''),
                'city': request.form.get('city', ''),
                'state': request.form.get('state', ''),
                'zip_code': request.form.get('zip_code', ''),
                'country': request.form.get('country', ''),
                'clinic_id': clinic_id
            }
            logger.info(f'Personal info collected: {personal_info}')
            
            # Get clinic_id from request or use default
            clinic_id = request.form.get('clinic_id')
            if clinic_id:
                try:
                    clinic_id = int(clinic_id)
                    # Verify the clinic exists
                    selected_clinic = Clinic.query.get(clinic_id)
                    if not selected_clinic:
                        logger.error(f"Invalid clinic_id: {clinic_id}")
                        return render_template('short_wizard/stage1_personal_info.html', 
                                             personal_info=session.get('personal_info', {}), 
                                             clinic=clinic, 
                                             clinic_dict=clinic_dict,
                                             error_message="Invalid clinic selection.")
                    logger.info(f"Using provided clinic_id: {clinic_id}")
                except ValueError:
                    logger.error(f"Invalid clinic_id format: {clinic_id}")
                    return render_template('short_wizard/stage1_personal_info.html', 
                                         personal_info=session.get('personal_info', {}), 
                                         clinic=clinic, 
                                         clinic_dict=clinic_dict,
                                         error_message="Invalid clinic selection.")
            else:
                # No clinic_id provided, use default logic
                clinic_id = None
                logger.info("No clinic_id provided, will use default assignment")
            
            # Smart dentist and clinic assignment
            # Use provided dentist_id from URL parameter if available, otherwise assign
            dentist_id = session.get('dentist_id')
            assigned_clinic_id = clinic_id if clinic_id else None
            
            if not clinic_id:
                # No clinic provided, try to get from DSO or use first available
                from flask_app.models import DSO
                dso = DSO.query.first()  # Get first DSO as fallback
                if dso:
                    clinics = Clinic.query.filter_by(dso_id=dso.id, status='active').all()
                    if clinics:
                        assigned_clinic_id = clinics[0].id
                        logger.info(f"Assigned default clinic {assigned_clinic_id} from DSO {dso.id}")
                    else:
                        logger.warning("No active clinics found for DSO")
                else:
                    logger.warning("No DSO found in database")
            
            # Only assign dentist if not provided via URL parameter
            if not dentist_id:
                # Default to dentist_id=7 if no dentist_id provided
                dentist_id = 7
                logger.info(f"Using default dentist_id: {dentist_id}")
            else:
                logger.info(f"Using provided dentist_id from URL parameter: {dentist_id}")
            
            # Get dentist and associated clinic based on dentist_id
            from flask_app.models import Dentist
            dentist = Dentist.query.get(dentist_id)
            if dentist:
                # Priority: 1) URL clinic_id, 2) Primary clinic, 3) Any clinic
                if clinic_id:
                    # Use provided clinic_id (highest priority)
                    assigned_clinic_id = clinic_id
                    logger.info(f"Using provided clinic_id {assigned_clinic_id}")
                else:
                    # Check for primary clinic first
                    primary_clinic_id = dentist.get_primary_clinic_id()
                    if primary_clinic_id:
                        assigned_clinic_id = primary_clinic_id
                        logger.info(f"Using primary clinic {assigned_clinic_id} for dentist {dentist_id}")
                    else:
                        # Use any clinic from dentist's associations
                        dentist_clinics = dentist.clinics.all()
                        if dentist_clinics:
                            assigned_clinic_id = dentist_clinics[0].id
                            logger.info(f"Using clinic {assigned_clinic_id} for dentist {dentist_id} (no primary set)")
                        else:
                            logger.error(f"No clinic found for dentist {dentist_id}")
                            return render_template('short_wizard/stage1_personal_info.html', 
                                                 personal_info=session.get('personal_info', {}), 
                                                 clinic=clinic, 
                                                 clinic_dict=clinic_dict,
                                                 error_message="System error: No clinic available. Please contact support.")
            else:
                logger.error(f"Dentist {dentist_id} not found in database")
                return render_template('short_wizard/stage1_personal_info.html', 
                                     personal_info=session.get('personal_info', {}), 
                                     clinic=clinic, 
                                     clinic_dict=clinic_dict,
                                     error_message="System error: Dentist not found. Please contact support.")
            
            # Store clinic_id in session for document generation
            session['clinic_id'] = assigned_clinic_id
            logger.info(f'Session updated with clinic_id: {assigned_clinic_id}')
            
            # Combine address fields into a single address string
            address_parts = []
            if personal_info['address']:
                address_parts.append(personal_info['address'])
            if personal_info['city']:
                address_parts.append(personal_info['city'])
            if personal_info['state']:
                address_parts.append(personal_info['state'])
            if personal_info['zip_code']:
                address_parts.append(personal_info['zip_code'])
            if personal_info['country']:
                address_parts.append(personal_info['country'])
            
            combined_address = ', '.join(address_parts) if address_parts else None
            
            # Check if email already exists - if so, update existing patient instead of creating new one
            existing_patient = Patient.query.filter_by(email=personal_info['email']).first()
            if existing_patient:
                logger.info(f"Patient with email {personal_info['email']} already exists, updating existing patient")
                # Update existing patient details
                existing_patient.name = f"{personal_info['first_name']} {personal_info['last_name']}"
                existing_patient.phone = personal_info['phone']
                existing_patient.dob = datetime.strptime(personal_info['date_of_birth'], '%Y-%m-%d').date() if personal_info['date_of_birth'] else existing_patient.dob
                existing_patient.address = combined_address
                
                # Update dentist and clinic associations if provided
                # (Keep existing associations if not provided in this form)
                if dentist_id:
                    existing_patient.dentist_id = dentist_id
                if assigned_clinic_id:
                    existing_patient.clinic_id = assigned_clinic_id
                
                # Set patient_id in session for subsequent stages
                patient_id = existing_patient.id
                session['patient_id'] = patient_id
                logger.info(f'Updated existing patient with ID: {patient_id}')
                
                # Commit the updates
                db.session.commit()
                logger.info('Database commit successful for patient update')
                
                logger.info(f'Redirecting to stage2_sleep_info for existing patient. Patient ID: {patient_id}')
                return redirect(url_for('short_wizard.stage2_sleep_info'))
            
            # Create new patient record (only if email doesn't exist)
            
            new_patient = Patient(
                name=f"{personal_info['first_name']} {personal_info['last_name']}",
                email=personal_info['email'],
                phone=personal_info['phone'],
                dob=datetime.strptime(personal_info['date_of_birth'], '%Y-%m-%d').date() if personal_info['date_of_birth'] else None,
                # Use original address field with combined data
                address=combined_address,
                dentist_id=dentist_id,
                clinic_id=assigned_clinic_id,  # This will automatically associate with DSO through clinic
                create_date=datetime.utcnow()
            )
            
            logger.info(f'About to create patient with dentist_id: {dentist_id}, clinic_id: {assigned_clinic_id}')
            logger.info(f'Patient data: name={new_patient.name}, email={new_patient.email}, phone={new_patient.phone}')
            logger.info(f'Combined address: {combined_address}')
            
            try:
                db.session.add(new_patient)
                db.session.flush()  # Get the patient ID
                patient_id = new_patient.id
                session['patient_id'] = patient_id
                logger.info(f'Patient created with ID: {patient_id}')
            except Exception as db_error:
                logger.error(f'Database error during patient creation: {str(db_error)}')
                logger.error(f'Error type: {type(db_error).__name__}')
                raise db_error
            
            db.session.commit()
            logger.info('Database commit successful')
            
            logger.info(f'Redirecting to stage2_sleep_info. Patient ID: {patient_id}')
            return redirect(url_for('short_wizard.stage2_sleep_info'))
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error in short_wizard stage1_personal_info: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            logger.error(f"Exception details: {str(e)}")
            return render_template('short_wizard/stage1_personal_info.html', 
                                 personal_info=session.get('personal_info', {}), 
                                 clinic=clinic, 
                                 clinic_dict=clinic_dict,
                                 error_message=str(e))
    
    # Pass session data to prepopulate the form if available
    return render_template('short_wizard/stage1_personal_info.html', 
                         personal_info=session.get('personal_info', {}), 
                         clinic=clinic, 
                         clinic_dict=clinic_dict)


@short_wizard.route('/short_wizard/stage2_sleep_info', methods=['GET', 'POST'])
def stage2_sleep_info():
    # Accept dentist_id from GET parameters
    dentist_id = request.args.get('dentist_id')
    if dentist_id:
        session['dentist_id'] = dentist_id
    
    if request.method == 'POST':
        try:
            # Retrieve the patient ID from the session
            patient_id = session.get('patient_id')
            if not patient_id:
                return render_template('short_wizard/stage2_sleep_info.html', 
                                     error_message='Please complete personal information first.')
            
            # Store sleep information in session
            sleep_info = {
                'has_sleep_test': request.form.get('has_sleep_test'),
                'sleep_test_date': request.form.get('sleep_test_date'),
                'sleep_test_physician': request.form.get('sleep_test_physician')
            }
            
            session['sleep_info'] = sleep_info
            
            # Store sleep information in session (Patient model doesn't have these fields yet)
            # TODO: Add sleep test fields to Patient model if needed
            logger.info(f"Sleep test information stored in session: {sleep_info}")
            
            # Handle file uploads for patients with sleep test
            logger.info(f"Sleep test status: {sleep_info.get('has_sleep_test')}")
            if sleep_info.get('has_sleep_test') == 'yes':
                logger.info("Processing file uploads for sleep test...")
                
                # Upload sleep test result to medical/sleep_test
                sleep_test_result = request.files.get('sleep_test_result')
                logger.info(f"Sleep test file received: {sleep_test_result}")
                logger.info(f"Sleep test file filename: {sleep_test_result.filename if sleep_test_result else 'None'}")
                logger.info(f"Sleep test file content length: {len(sleep_test_result.read()) if sleep_test_result else 'None'}")
                if sleep_test_result:
                    sleep_test_result.seek(0)  # Reset file pointer
                if sleep_test_result and sleep_test_result.filename:
                    logger.info(f"Uploading file: {sleep_test_result.filename}")
                    try:
                        # Get file extension and create secure filename
                        original_filename = secure_filename(sleep_test_result.filename)
                        file_ext = os.path.splitext(original_filename)[1]
                        if not file_ext:
                            raise ValueError("No file extension found for sleep test result.")
                        
                        # Generate unique filename
                        filename = f"sleep_test_result_{patient_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{file_ext}"
                        
                        # Upload to S3
                        bucket_name = os.environ.get('S3_BUCKET_NAME', 'vizbrizpatients')
                        s3_key = f"patients/{patient_id}/medical/sleep-test/{filename}"
                        
                        sleep_test_result.seek(0)
                        s3_client = get_s3_client()
                        s3_client.upload_fileobj(
                            sleep_test_result,
                            bucket_name,
                            s3_key,
                            ExtraArgs={'ContentType': sleep_test_result.mimetype}
                        )
                        
                        # Save file record to database
                        file_record = File(
                            patient_id=patient_id,
                            s3_key=s3_key,
                            file_type=sleep_test_result.mimetype,
                            name=filename,
                            category='medical',
                            subcategory='sleep-test',
                            upload_date=datetime.utcnow()
                        )
                        db.session.add(file_record)
                        db.session.commit()
                        logger.info(f"Successfully uploaded sleep test result for patient {patient_id}")
                        
                    except Exception as upload_error:
                        logger.error(f"Error uploading sleep test file: {str(upload_error)}")
                        logger.error(f"Upload error type: {type(upload_error).__name__}")
                        import traceback
                        logger.error(f"Upload error traceback: {traceback.format_exc()}")
                        # Continue with the process even if file upload fails
                else:
                    logger.info("No sleep test file provided or file is empty")
            else:
                logger.info("No sleep test indicated, skipping file uploads")
            
            # Add sleep test information as a comment in PatientComment table (regardless of file upload)
            if sleep_info.get('has_sleep_test') == 'yes':
                try:
                    from flask_app.models import PatientComment, Dentist
                    
                    # Create sleep test comment with details
                    sleep_test_comment = f"Sleep Test Information:\n"
                    if sleep_info.get('sleep_test_date'):
                        sleep_test_comment += f"Date: {sleep_info['sleep_test_date']}\n"
                    if sleep_info.get('sleep_test_physician'):
                        sleep_test_comment += f"Ordering Physician: {sleep_info['sleep_test_physician']}\n"
                    
                    # Add file upload status
                    if sleep_test_result and sleep_test_result.filename:
                        sleep_test_comment += "Sleep Test Result: Uploaded"
                    else:
                        sleep_test_comment += "Sleep Test Result: Not uploaded"
                    
                    # Get a dentist for the comment (required field)
                    dentist = Dentist.query.first()
                    if not dentist:
                        logger.error("No dentist found for comment creation")
                        return render_template('short_wizard/stage2_sleep_info.html', 
                                             error_message="System error: No dentist available.")
                    
                    # Create the comment record with only mandatory fields
                    comment = PatientComment(
                        patient_id=patient_id,
                        dentist_id=dentist.id,
                        comment_type='conversion',
                        content=sleep_test_comment
                    )
                    db.session.add(comment)
                    db.session.commit()
                    
                    logger.info(f"Added sleep test comment for patient {patient_id}")
                    
                except Exception as comment_error:
                    logger.error(f"Error adding sleep test comment: {str(comment_error)}")
                    # Continue with the process even if comment creation fails
            
            return redirect(url_for('short_wizard.stage3_cpap_intolerance'))
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error in short_wizard stage2_sleep_info: {str(e)}")
            return render_template('short_wizard/stage2_sleep_info.html', 
                                 error_message=str(e))
    
    return render_template('short_wizard/stage2_sleep_info.html')


@short_wizard.route('/short_wizard/stage3_cpap_intolerance', methods=['GET', 'POST'])
def stage3_cpap_intolerance():
    # Accept dentist_id from GET parameters
    dentist_id = request.args.get('dentist_id')
    if dentist_id:
        session['dentist_id'] = dentist_id
    
    if request.method == 'POST':
        try:
            # Retrieve patient ID from session
            patient_id = session.get('patient_id')
            if not patient_id:
                return render_template('short_wizard/stage3_cpap_intolerance.html', 
                                     error_message='Please complete previous steps first.')
            
            # Store CPAP intolerance information in session
            cpap_info = {
                'issues': request.form.getlist('issues[]'),
                'other_reasons_cpap': request.form.get('other_reasons_cpap'),
                'no_attempt': request.form.getlist('no_attempt[]'),
                'other_reasons_oral_appliance': request.form.get('other_reasons_oral_appliance')
            }
            
            session['cpap_info'] = cpap_info
            
            # Process form screenshot (same as original wizard)
            form_screenshot_base64 = request.form.get('form_screenshot')
            if form_screenshot_base64:
                logger.info("CPAP intolerance form screenshot received, processing...")
                
                # Remove the 'data:image/png;base64,' prefix
                form_screenshot_base64 = form_screenshot_base64.split(',')[1]
                form_screenshot_data = base64.b64decode(form_screenshot_base64)
                
                # Define the file path to save the screenshot locally
                screenshot_filename = f"cpap_intolerance_form_{patient_id}.png"
                screenshot_path = os.path.join(tempfile.gettempdir(), screenshot_filename)
                
                # Save the image to a temporary file
                with open(screenshot_path, 'wb') as img_file:
                    img_file.write(form_screenshot_data)
                logger.info(f"Screenshot saved locally at {screenshot_path}")
                
                # Upload the image to S3 (same as original wizard)
                bucket_name = os.getenv('S3_BUCKET_NAME')
                if bucket_name:
                    s3_key = f"patients/{patient_id}/billing/billing/{screenshot_filename}"
                    with open(screenshot_path, 'rb') as img_file:
                        s3_client = get_s3_client()
                        s3_client.upload_fileobj(
                            img_file,
                            bucket_name,
                            s3_key,
                            ExtraArgs={'ContentType': 'image/png'}
                        )
                    logger.info(f"Screenshot uploaded to S3 at {s3_key}")
                    
                    # Save metadata to the database (same as original wizard)
                    new_file = File(
                        name=screenshot_filename,
                        patient_id=patient_id,
                        file_type='image/png',
                        file_size=os.path.getsize(screenshot_path),
                        s3_key=s3_key,
                        category='billing',
                        subcategory='billing'
                    )
                    db.session.add(new_file)
                    db.session.commit()
                else:
                    logger.error("S3 bucket name not found in environment variables")
            else:
                logger.warning("No form screenshot received in the request")
            
            return redirect(url_for('short_wizard.stage4_informed_consent'))
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error in short_wizard stage3_cpap_intolerance: {str(e)}")
            return render_template('short_wizard/stage3_cpap_intolerance.html', 
                                 error_message=str(e))
    
    return render_template('short_wizard/stage3_cpap_intolerance.html')


@short_wizard.route('/short_wizard/stage4_informed_consent', methods=['GET', 'POST'])
def stage4_informed_consent():
    # Accept dentist_id from GET parameters
    dentist_id = request.args.get('dentist_id')
    if dentist_id:
        session['dentist_id'] = dentist_id
    
    if request.method == 'POST':
        try:
            # Retrieve patient ID from session
            patient_id = session.get('patient_id')
            if not patient_id:
                return render_template('short_wizard/stage4_informed_consent.html', 
                                     error_message='Please complete previous steps first.')
            
            # Store informed consent information
            consent_info = {
                'doctor_name': request.form.get('doctor_name'),
                'consent_date': request.form.get('consent_date'),
                'signature': request.form.get('signature'),
                'consent_agreed': request.form.get('consent_agreed')
            }
            
            session['consent_info'] = consent_info
            
            # Process form screenshot (same as original wizard)
            form_screenshot_base64 = request.form.get('form_screenshot')
            if form_screenshot_base64:
                logger.info("Informed consent form screenshot received, processing...")
                
                # Remove the 'data:image/png;base64,' prefix
                form_screenshot_base64 = form_screenshot_base64.split(',')[1]
                form_screenshot_data = base64.b64decode(form_screenshot_base64)
                
                # Define the file path to save the screenshot locally
                screenshot_filename = f"informed_consent_form_{patient_id}.png"
                screenshot_path = os.path.join(tempfile.gettempdir(), screenshot_filename)
                
                # Save the image to a temporary file
                with open(screenshot_path, 'wb') as img_file:
                    img_file.write(form_screenshot_data)
                logger.info(f"Screenshot saved locally at {screenshot_path}")
                
                # Upload the image to S3 (same as original wizard)
                bucket_name = os.getenv('S3_BUCKET_NAME')
                if bucket_name:
                    s3_key = f"patients/{patient_id}/billing/billing/{screenshot_filename}"
                    with open(screenshot_path, 'rb') as img_file:
                        s3_client = get_s3_client()
                        s3_client.upload_fileobj(
                            img_file,
                            bucket_name,
                            s3_key,
                            ExtraArgs={'ContentType': 'image/png'}
                        )
                    logger.info(f"Screenshot uploaded to S3 at {s3_key}")
                    
                    # Save metadata to the database (same as original wizard)
                    new_file = File(
                        name=screenshot_filename,
                        patient_id=patient_id,
                        file_type='image/png',
                        file_size=os.path.getsize(screenshot_path),
                        s3_key=s3_key,
                        category='billing',
                        subcategory='billing'
                    )
                    db.session.add(new_file)
                    db.session.commit()
                else:
                    logger.error("S3 bucket name not found in environment variables")
            else:
                logger.warning("No form screenshot received in the request")
            
            return redirect(url_for('short_wizard.stage5_hipaa_authorization'))
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error in short_wizard stage4_informed_consent: {str(e)}")
            return render_template('short_wizard/stage4_informed_consent.html', 
                                 error_message=str(e))
    
    return render_template('short_wizard/stage4_informed_consent.html')


@short_wizard.route('/short_wizard/stage5_hipaa_authorization', methods=['GET', 'POST'])
def stage5_hipaa_authorization():
    # Accept dentist_id from GET parameters
    dentist_id = request.args.get('dentist_id')
    if dentist_id:
        session['dentist_id'] = dentist_id
    
    clinic_id = session.get('clinic_id')
    clinic = Clinic.query.get(clinic_id) if clinic_id else None
    clinic_dict = None
    
    if clinic:
        clinic_dict = {
            'id': clinic.id,
            'name': clinic.name,
            'address': clinic.address,
            'phone': clinic.telephone,
            'email': clinic.email
        }
    
    if request.method == 'POST':
        try:
            # Retrieve patient ID from session
            patient_id = session.get('patient_id')
            if not patient_id:
                return render_template('short_wizard/stage5_hipaa_authorization.html', 
                                     clinic=clinic,
                                     clinic_dict=clinic_dict,
                                     error_message='Please complete previous steps first.')
            
            # Store HIPAA authorization information
            hipaa_info = {
                'hipaa_agreed': request.form.get('hipaa_agreed'),
                'hipaa_date': request.form.get('hipaa_date'),
                'signature': request.form.get('signature')
            }
            
            session['hipaa_info'] = hipaa_info
            
            # Process form screenshot (same as original wizard)
            form_screenshot_base64 = request.form.get('form_screenshot')
            if form_screenshot_base64:
                logger.info("HIPAA authorization form screenshot received, processing...")
                
                # Remove the 'data:image/png;base64,' prefix
                form_screenshot_base64 = form_screenshot_base64.split(',')[1]
                form_screenshot_data = base64.b64decode(form_screenshot_base64)
                
                # Define the file path to save the screenshot locally
                screenshot_filename = f"hipaa_authorization_form_{patient_id}.png"
                screenshot_path = os.path.join(tempfile.gettempdir(), screenshot_filename)
                
                # Save the image to a temporary file
                with open(screenshot_path, 'wb') as img_file:
                    img_file.write(form_screenshot_data)
                logger.info(f"Screenshot saved locally at {screenshot_path}")
                
                # Upload the image to S3 (same as original wizard)
                bucket_name = os.getenv('S3_BUCKET_NAME')
                if bucket_name:
                    s3_key = f"patients/{patient_id}/billing/billing/{screenshot_filename}"
                    with open(screenshot_path, 'rb') as img_file:
                        s3_client = get_s3_client()
                        s3_client.upload_fileobj(
                            img_file,
                            bucket_name,
                            s3_key,
                            ExtraArgs={'ContentType': 'image/png'}
                        )
                    logger.info(f"Screenshot uploaded to S3 at {s3_key}")
                    
                    # Save metadata to the database (same as original wizard)
                    new_file = File(
                        name=screenshot_filename,
                        patient_id=patient_id,
                        file_type='image/png',
                        file_size=os.path.getsize(screenshot_path),
                        s3_key=s3_key,
                        category='billing',
                        subcategory='billing'
                    )
                    db.session.add(new_file)
                    db.session.commit()
                else:
                    logger.error("S3 bucket name not found in environment variables")
            else:
                logger.warning("No form screenshot received in the request")
            
            return redirect(url_for('short_wizard.end_wizard'))
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error in short_wizard stage5_hipaa_authorization: {str(e)}")
            return render_template('short_wizard/stage5_hipaa_authorization.html', 
                                clinic=clinic,
                                clinic_dict=clinic_dict,
                                error_message=str(e))
    
    return render_template('short_wizard/stage5_hipaa_authorization.html', 
                         clinic=clinic, 
                         clinic_dict=clinic_dict)


@short_wizard.route('/short_wizard/end_wizard')
def end_wizard():
    # Fetch patient and clinic for dynamic branding
    patient_id = session.get('patient_id')
    patient = Patient.query.get(patient_id) if patient_id else None
    clinic_id = session.get('clinic_id')
    clinic = Clinic.query.get(clinic_id) if clinic_id else None
    
    # Clear session data
    session.pop('personal_info', None)
    session.pop('sleep_info', None)
    session.pop('cpap_info', None)
    session.pop('consent_info', None)
    session.pop('hipaa_info', None)
    session.pop('patient_id', None)
    session.pop('clinic_id', None)
    
    return render_template('short_wizard/end_wizard.html', 
                         patient=patient, 
                         clinic=clinic,
                         message="Thank you for completing the short patient onboarding process!")