from flask import Blueprint, request, render_template, redirect, url_for, flash, jsonify, send_file
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from flask_app import db
from flask_app.models import Patient, File, Dentist, AdminFile, Claim, Comment, PatientStatus, StatusOption, PatientComment, Clinic
import boto3
from botocore.config import Config
import os
import logging
import traceback
from sqlalchemy.exc import SQLAlchemyError  # Fixed import for SQLAlchemyError
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
import re
from datetime import datetime
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash
from reportlab.pdfgen import canvas  # Add this line if it's not present
from reportlab.lib.pagesizes import letter
from pdfrw import PdfReader, PdfWriter, PageMerge
import base64 
from PIL import Image
from datetime import datetime, timedelta
import time
from flask_app.s3_utils import get_s3_client
import secrets
from flask import render_template, request, redirect, url_for, flash
from flask_app.models import Patient, PatientConsultSchedule, PatientDeviceOrder, DentistReportApproval, AdminFile
from datetime import datetime
from sqlalchemy import or_, and_
from flask_app.models import Patient, File, Dentist, AdminFile, Claim, Comment, PatientStatus, StatusOption, PatientComment, Clinic, PatientConsultSchedule, PatientDeviceOrder, DentistReportApproval, ConsultationRequest
from collections import OrderedDict
from datetime import date
from flask_app.config.manifest_config import get_manifest_definition
from flask_app.models import DSO, Clinic, Dentist, Patient  # add others as needed
import json
import pymysql
import io
import qrcode
import mysql.connector

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

main = Blueprint('main', __name__)

def get_stage_completion_date(patient_id, stage_key):
    """Get the actual completion date for a specific stage from the database"""
    try:
        from datetime import datetime
        
        if stage_key == "quiz_completion":
            # Get quiz completion date
            result = db.session.execute(
                text("SELECT created_at FROM conversion_quiz WHERE user_id = :pid ORDER BY created_at DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.created_at if result else None
            
        elif stage_key == "initial_consult_scheduled":
            # Get consultation scheduling date
            result = db.session.execute(
                text("SELECT created_at FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = 'sleep_expert' ORDER BY created_at DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.created_at if result else None
            
        elif stage_key == "met_with_sleep_expert":
            # Get consultation completion date
            result = db.session.execute(
                text("SELECT completed_datetime FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = 'sleep_expert' AND status = 'completed' ORDER BY completed_datetime DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.completed_datetime if result else None
            
        elif stage_key == "sleep_doctor_consult_scheduled":
            # Get consultation scheduling date
            result = db.session.execute(
                text("SELECT created_at FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = 'sleep_doctor' ORDER BY created_at DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.created_at if result else None
            
        elif stage_key == "sleep_test_completed":
            # Get file upload date (approximate completion date)
            result = db.session.execute(
                text("SELECT created_at FROM adminfiles WHERE patient_id = :pid AND (LOWER(name) LIKE '%.pdf' OR LOWER(name) LIKE '%.dcm') ORDER BY created_at DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.created_at if result else None
            
        elif stage_key == "sleep_doctor_followup_completed":
            # Get consultation completion date
            result = db.session.execute(
                text("SELECT completed_datetime FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = 'sleep_doctor' AND status = 'completed' ORDER BY completed_datetime DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.completed_datetime if result else None
            
        elif stage_key == "dental_sleep_doctor_consult_scheduled":
            # Get consultation scheduling date
            result = db.session.execute(
                text("SELECT created_at FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = 'dental_sleep_doctor' ORDER BY created_at DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.created_at if result else None
            
        elif stage_key == "hipaa_consent_signed":
            # Get file upload date (approximate completion date)
            result = db.session.execute(
                text("SELECT created_at FROM files WHERE patient_id = :pid AND category = 'billing' AND (LOWER(name) LIKE '%hipaa%' OR LOWER(name) LIKE '%consent%') ORDER BY created_at DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.created_at if result else None
            
        elif stage_key == "met_with_dental_sleep_expert":
            # Get consultation completion date
            result = db.session.execute(
                text("SELECT completed_datetime FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = 'dental_sleep_doctor' AND status = 'completed' ORDER BY completed_datetime DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.completed_datetime if result else None
            
        elif stage_key == "clinical_data_available":
            # Get file upload date (approximate completion date)
            result = db.session.execute(
                text("SELECT created_at FROM files WHERE patient_id = :pid AND ((category = 'cbct' AND LOWER(name) LIKE '%.dcm') OR (category = 'intra_oral_scan' AND LOWER(name) LIKE '%.stl') OR (category = 'clinical_images')) ORDER BY created_at DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.created_at if result else None
            
        elif stage_key == "osa_report_available":
            # Get file upload date (approximate completion date)
            result = db.session.execute(
                text("SELECT created_at FROM adminfiles WHERE patient_id = :pid AND (LOWER(name) LIKE '%.pdf' OR LOWER(name) LIKE '%.doc' OR LOWER(name) LIKE '%.docx') ORDER BY created_at DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.created_at if result else None
            
        elif stage_key == "appliance_ordered":
            # Get order date
            result = db.session.execute(
                text("SELECT order_date FROM patient_device_order WHERE patient_id = :pid ORDER BY order_date DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.order_date if result else None
            
        elif stage_key == "appliance_delivery":
            # Get delivery date
            result = db.session.execute(
                text("SELECT arrival_date FROM patient_device_order WHERE patient_id = :pid AND arrival_date IS NOT NULL ORDER BY arrival_date DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.arrival_date if result else None
            
        elif stage_key == "appliance_delivery_and_fitting":
            # Get fitting date
            result = db.session.execute(
                text("SELECT fitting_date FROM patient_device_order WHERE patient_id = :pid AND fitting_date IS NOT NULL ORDER BY fitting_date DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.fitting_date if result else None
            
        elif stage_key == "followup_meeting":
            # Get follow-up completion date
            result = db.session.execute(
                text("SELECT completed_datetime FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = 'follow_up_meeting' AND status = 'completed' ORDER BY completed_datetime DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.completed_datetime if result else None
            
        else:
            return None
            
    except Exception as e:
        logger.error(f"Error getting completion date for stage {stage_key}: {e}")
        return None

logger.debug(f"S3_BUCKET_NAME: {os.getenv('S3_BUCKET_NAME')}")

# Replace any direct s3_client creation with the utility function
# For example, replace lines like:
# s3_client = boto3.client('s3', region_name='us-east-2', config=Config(signature_version='s3v4'))
# with:
s3_client = get_s3_client()

# Function to test access to S3 by listing bucket contents
def test_s3_access():
    bucket_name = os.getenv('S3_BUCKET_NAME')  # Replace with your actual bucket name
    try:
        response = s3_client.list_objects_v2(Bucket=bucket_name, MaxKeys=5)
        if 'Contents' in response:
            logger.info(f"Successfully accessed S3. Files in bucket '{bucket_name}': {[file['Key'] for file in response['Contents']]}")
        else:
            logger.info(f"Bucket '{bucket_name}' is empty or does not have accessible files.")
    except Exception as e:
        logger.error(f"Failed to access S3 bucket '{bucket_name}': {str(e)}")

def check_db_connection():
    try:
        result = db.session.execute(text("SELECT COUNT(*) FROM dentists"))
        count = result.scalar()
        logger.info(f"Successfully connected to the database. Number of dentists: {count}")
        return True
    except Exception as e:
        logger.error(f"Error connecting to the database: {str(e)}")
        return False


@main.route('/')
@main.route('/home')
@login_required
def index():
    logger.debug('Accessing index page')
    test_s3_access()  # Test S3 access on homepage load
    return redirect(url_for('main.upload_new'))

@main.route('/login', methods=['GET', 'POST'])
def login():
    logger.debug('Accessing login page')
    
    if not check_db_connection():
        flash('Unable to connect to the database. Please try again later.')
        return render_template('login.html')
    
    if current_user.is_authenticated:
        logger.debug('User is already authenticated, redirecting to upload page')
        return redirect(url_for('main.upload_new'))
    
    if request.method == 'POST':
        logger.debug('Processing login POST request')
        email = request.form.get('email')
        password = request.form.get('password')
        
        logger.debug(f"Login attempt for email: {email}")
        logger.debug(f"Next parameter: {request.args.get('next')}")
        
        dentist = Dentist.query.filter_by(email=email).first()
        
        if dentist:
            logger.debug(f"User found: {dentist.name}")
            logger.debug(f"Password entered: {password}")
            logger.debug(f"Hashed password in DB: {dentist.password}")
            
            # Simple password check using werkzeug
            password_match = check_password_hash(dentist.password, password)
            logger.debug(f"Password match result: {password_match}")
        else:
            logger.debug(f"Query result for email {email}: Not found")

        # Check if the dentist exists and the password matches
        if dentist and password_match:
            logger.debug('Login successful')
            login_user(dentist)
            next_page = request.args.get('next')
            logger.debug(f"Redirecting to next page: {next_page}")
            if next_page:
                return redirect(next_page)
            logger.debug("No next page, redirecting to upload_new")
            return redirect(url_for('main.upload_new'))
        else:
            logger.warning('Login failed')
            flash('Please check your login details and try again.')
    
    return render_template('login.html')


@main.route('/logout')
@login_required
def logout():
    logger.debug('Logging out user')
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('main.login'))





# Define your `upload` route
@main.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    logger.debug('Accessing upload page.')

    if request.method == 'POST':
        logger.debug('Processing upload POST request')

        # Get form fields
        patient_name = request.form.get('patient_name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        dob = request.form.get('dob')
        gender = request.form.get('gender')
        insurer = request.form.get('insurer')
        policy_id = request.form.get('policy_id')
        address = request.form.get('address')
        logger.debug('before OSA fields ')
        # OSA-related fields
        snoring = request.form.get('snoring')
        snoring_other = request.form.get('snoring_other') if snoring == 'other' else None

        daytime_sleepiness = request.form.get('daytime_sleepiness')
        daytime_sleepiness_other = request.form.get('daytime_sleepiness_other') if daytime_sleepiness == 'other' else None

        sleep_study = request.form.get('sleep_study')
        sleep_study_date = request.form.get('sleep_study_date') if sleep_study == 'yes' else None

        cpap_intolerant = request.form.get('cpap_intolerant')
        cpap_intolerant_other = request.form.get('cpap_intolerant_other') if cpap_intolerant == 'other' else None
        logger.debug('before zip file section')

        # Expecting a single zip file per section now
        billing_zip = request.files.get('billing')  # Expecting a single zip file
        logger.debug('before zi[ file section')
        clinical_zip = request.files.get('clinical')  # Expecting a single zip file
        cbct_zip = request.files.get('cbct')  # Expecting a single zip file
        intraoral_zip = request.files.get('intraoral')  # Expecting a single zip file
        sleep_test_zip = request.files.get('sleep')  # Expecting a single zip file
        questionnaire_zip = request.files.get('questionnaire')  # Expecting a single zip file
        medical_background_zip = request.files.get('medical')  # Expecting a single zip file
        logger.debug('before zip file section')
        try:
            # Parse DOB field into a datetime object (if provided)
            parsed_dob = None
            if dob:
                try:
                    parsed_dob = datetime.strptime(dob, '%Y-%m-%d')
                except ValueError:
                    logger.error(f"Invalid date format for DOB: {dob}")
                    return jsonify({'success': False, 'message': 'Invalid date format for DOB. Please use YYYY-MM-DD.'}), 400

            # Parse sleep study date field into a datetime object (if provided)
            logger.debug('parsed_sleep_stud')
            parsed_sleep_study_date = None
            if sleep_study_date:
                try:
                    parsed_sleep_study_date = datetime.strptime(sleep_study_date, '%Y-%m-%d')
                except ValueError:
                    logger.error(f"Invalid date format for sleep study date: {sleep_study_date}")
                    return jsonify({'success': False, 'message': 'Invalid date format for sleep study date. Please use YYYY-MM-DD.'}), 400

            # Get clinic_id from form or fall back to dentist's default clinic
            clinic_id = request.form.get('clinic_id')
            if clinic_id:
                try:
                    clinic_id = int(clinic_id)
                    # Verify the clinic is accessible to this dentist
                    if current_user.role != 'admin':
                        dso_ids = current_user.get_dso_ids()
                        if dso_ids:
                            clinic = Clinic.query.filter(
                                Clinic.id == clinic_id,
                                Clinic.dso_id.in_(dso_ids),
                                Clinic.status == 'active'
                            ).first()
                            if not clinic:
                                logger.warning(f'Dentist {current_user.name} attempted to assign patient to unauthorized clinic {clinic_id}')
                                return jsonify({'success': False, 'message': 'Unauthorized clinic selection'}), 403
                        else:
                            logger.warning(f'Dentist {current_user.name} has no DSO associations')
                            return jsonify({'success': False, 'message': 'No DSO associations found'}), 403
                    logger.debug(f'Patient assigned to selected clinic_id {clinic_id}')
                except ValueError:
                    logger.error(f'Invalid clinic_id format: {clinic_id}')
                    return jsonify({'success': False, 'message': 'Invalid clinic selection'}), 400
            else:
                # Fall back to dentist's default clinic (first clinic in their DSOs)
                clinic_id = None
                dso_ids = current_user.get_dso_ids()
                if dso_ids:
                    clinic = Clinic.query.filter(Clinic.dso_id.in_(dso_ids)).first()
                    clinic_id = clinic.id if clinic else None
                    logger.debug(f'Assigned default clinic_id {clinic_id} to patient based on dentist DSO associations')
                else:
                    logger.debug('No DSO associations found for dentist, clinic_id will be NULL (legacy mode)')

            # Create new patient in the database
            new_patient = Patient(
                name=patient_name,
                email=email,
                phone=phone,
                dob=parsed_dob,
                gender=gender,
                insurer=insurer,
                policy_id=policy_id,
                address=address,
                dentist_id=current_user.id,
                clinic_id=clinic_id,  # Assign clinic based on dentist's DSO
                snoring=snoring,
                snoring_other=snoring_other,
                daytime_sleepiness=daytime_sleepiness,
                daytime_sleepiness_other=daytime_sleepiness_other,
                sleep_study=sleep_study,
                sleep_study_date=parsed_sleep_study_date,
                cpap_intolerant=cpap_intolerant,
                cpap_intolerant_other=cpap_intolerant_other,
                create_date=datetime.now(),  # Set current date as created date
                last_update=datetime.now(),   # Set current date as last updated date
                upload_token=secrets.token_urlsafe(32)  # Generate a 32-byte URL-safe token
            )
                
            db.session.add(new_patient)
            db.session.flush()  # Flush to get patient ID before commit
            logger.debug(f'Created new patient with ID: {new_patient.id}')
            
            # Ensure file exists before trying to process it
            if billing_zip:
                upload_and_save_files(billing_zip, 'billing', 'billing', new_patient, 'billing')
            
            if clinical_zip:
                upload_and_save_files(clinical_zip, 'imaging/clinical_pictures', 'imaging', new_patient, 'clinical_pictures')

            if cbct_zip:
                upload_and_save_files(cbct_zip, 'imaging/cbct', 'imaging', new_patient, 'cbct')

            if intraoral_zip:
                upload_and_save_files(intraoral_zip, 'imaging/intraoral_scan', 'imaging', new_patient, 'intraoral_scan')

            if sleep_test_zip:
                upload_and_save_files(sleep_test_zip, 'medical/sleep_test', 'medical', new_patient, 'sleep_test')

            if questionnaire_zip:
                upload_and_save_files(questionnaire_zip, 'medical/questionnaire', 'medical', new_patient, 'questionnaire')

            if medical_background_zip:
                upload_and_save_files(medical_background_zip, 'medical/medical_background', 'medical', new_patient, 'medical_background')

            # Commit all changes to the database
            db.session.commit()
            logger.debug('All changes committed to the database successfully')

            # Return success response
            return jsonify({'success': True, 'patient_id': new_patient.id})

        except Exception as e:
            db.session.rollback()  # Rollback on error
            logger.error(f'Error during upload: {str(e)}')
            return jsonify({'success': False, 'message': f'Error uploading data: {str(e)}'}), 500

    return render_template('upload_form.html')

@main.route('/api/dentist/clinics', methods=['GET'])
@login_required
def get_dentist_clinics():
    """
    Get clinics available to the current dentist based on their DSO associations
    """
    try:
        if current_user.role == 'admin':
            # Admin can see all clinics
            clinics = Clinic.query.filter_by(status='active').all()
        else:
            # Get clinics from dentist's DSO associations
            dso_ids = current_user.get_dso_ids()
            if dso_ids:
                clinics = Clinic.query.filter(
                    Clinic.dso_id.in_(dso_ids),
                    Clinic.status == 'active'
                ).all()
            else:
                clinics = []
        
        clinic_data = []
        for clinic in clinics:
            clinic_data.append({
                'id': clinic.id,
                'name': clinic.name,
                'dso_id': clinic.dso_id
            })
        
        return jsonify({
            'success': True,
            'clinics': clinic_data,
            'count': len(clinic_data)
        })
        
    except Exception as e:
        logger.error(f'Error getting dentist clinics: {str(e)}')
        return jsonify({
            'success': False,
            'message': f'Error retrieving clinics: {str(e)}'
        }), 500

@main.route('/patient-list')
@login_required
def patient_list():
    logger.debug('Accessing patient list page')

    # If the current user is an admin, they can see all patients
    if current_user.role == 'admin':
        patients = Patient.query.filter(Patient.status != 'Archived').order_by(Patient.create_date.desc()).all()
        logger.debug(f'Admin viewing all patients. Total patients found: {len(patients)}')
    
    elif current_user.role in ['Dentist', 'dentist', 'Dentists']:
        # Dentist can only see patients treated by dentists in their same DSO
        logger.debug(f'Dentist {current_user.name} with DSO: {getattr(current_user, "DSO", "None")} attempting to view patient list.')

        # Try new DSO system first, then fall back to legacy
        if hasattr(current_user, 'dsos') and current_user.dsos.count() > 0:
            # NEW SYSTEM: Use DSO associations
            logger.debug('Using new DSO association system')
            dso_ids = current_user.get_dso_ids()
            patients = (Patient.query
                        .join(Dentist)
                        .join(Clinic, Patient.clinic_id == Clinic.id, isouter=True)
                        .filter(
                            db.or_(
                                Clinic.dso_id.in_(dso_ids),  # New system patients
                                db.and_(Patient.clinic_id.is_(None), Dentist.DSO == getattr(current_user, 'DSO', None))  # Legacy patients
                            ),
                            Patient.status != 'Archived'
                        )
                        .order_by(Patient.create_date.desc())
                        .all())
        elif hasattr(current_user, 'DSO') and current_user.DSO:
            # LEGACY SYSTEM: Use DSO string
            logger.debug('Using legacy DSO string system')
            patients = (Patient.query
                        .join(Dentist)
                        .filter(
                            Dentist.DSO == current_user.DSO,
                            Patient.status != 'Archived'
                        )
                        .order_by(Patient.create_date.desc())
                        .all())
        else:
            # No DSO association found
            logger.warning(f'Dentist {current_user.name} has no DSO associations')
            patients = []

        # Log the DSO of the current user and compare it with patients' dentists
        logger.debug(f'Number of patients found: {len(patients)}')
        for patient in patients[:5]:  # Log first 5 for debugging
            dentist_dso = getattr(patient.dentist, 'DSO', 'None') if patient.dentist else 'None'
            clinic_dso = patient.clinic.dso_id if patient.clinic else 'None'
            logger.debug(f"Patient: {patient.name}, Dentist DSO: {dentist_dso}, Clinic DSO: {clinic_dso}")

        if not patients:
            logger.warning(f'No patients found for dentist: {current_user.name}')
        else:
            logger.debug(f'{len(patients)} patients found for dentist: {current_user.name}')
    
    else:
        flash('Unauthorized access', 'error')
        logger.warning(f'Unauthorized access attempt by user {current_user.name} with role {current_user.role}')
        return redirect(url_for('main.index'))

    return render_template('patient_list.html', patients=patients)

@main.route('/patient/<int:patient_id>', methods=['GET', 'POST'])
@login_required
def patient_details(patient_id):
    logger.debug(f'Accessing patient details page for patient ID: {patient_id}')
    
    # Fetch patient from the database
    patient = Patient.query.get_or_404(patient_id)
    logger.debug(f'Patient details fetched: {patient.last_update}, Payment Method: {patient.payment_method}')

    # Check if the current user is an admin
    is_admin = current_user.role == 'admin'
    
    # Ensure the user has permission to view the patient
    if not is_admin:
        # Use the new DSO access control method
        if not current_user.can_access_patient(patient):
            logger.warning(f"User {current_user.email} does not have permission to view patient {patient_id}")
            flash('You do not have permission to view this patient.', 'error')
            return redirect(url_for('main.patient_list'))

    if request.method == 'POST':
        logger.debug(f'POST request received to update patient ID: {patient_id}')
        
        # Fetch form data for payment method
        payment_method = request.form.get('payment_method', 'N/A').strip()
        logger.debug(f'Received payment method for patient ID {patient_id}: {payment_method}')
        
        valid_payment_methods = ['N/A', 'In Network', 'Out of Network', 'Private']
        
        # Validate and update payment method
        if payment_method in valid_payment_methods:
            patient.payment_method = payment_method
            logger.debug(f'Updating payment method for patient ID {patient_id} to: {payment_method}')
        else:
            logger.warning(f'Invalid payment method value received: {payment_method}')
            flash('Invalid payment method value.', 'error')
            return redirect(url_for('main.patient_details', patient_id=patient_id))
        
        # Commit changes to the database
        try:
            db.session.commit()
            logger.info(f'Successfully updated patient ID {patient_id} with payment method: {payment_method}')
            flash('Patient details updated successfully.', 'success')
        except Exception as e:
            logger.error(f'Error updating patient ID {patient_id}: {e}')
            db.session.rollback()
            flash('An error occurred while updating patient details.', 'error')

        return redirect(url_for('main.patient_details', patient_id=patient_id))

    # Log that no patient comments are being saved or processed
    logger.debug(f'No patient comments are being processed for patient ID {patient_id}.')
    
    # Fetch patient details using the helper function
    patient_details = fetch_patient_details(patient_id)
    uploaded_files = patient_details.get('uploaded_files', {})
    uploaded_files_one_dcm_file = patient_details.get('uploaded_files_one_dcm_file', {})
    cbct_directories = patient_details.get('cbct_directories', []),
    patient_statuses = patient_details.get('patient_statuses', {})
    status_options = StatusOption.query.all()
    logger.debug(f'Fetched patient details using helper (comments excluded): {patient_details}')
    cbct_directories = patient_details.get('cbct_directories', [])
    logger.debug(f"CBCT Directories from backend: {cbct_directories}")

    # Get base URL from environment variable for dynamic link generation
    base_url = os.environ.get('BASE_URL', 'http://localhost:7000')
    logger.debug(f"Using base URL for patient portal: {base_url}")

    # Fetch DSOs and clinics for the form
    from flask_app.models import DSO, Clinic
    dsos = DSO.query.filter_by(status='active').all()
    clinics = Clinic.query.filter_by(status='active').all()
    
    # Organize clinics by DSO for JavaScript
    clinics_by_dso = {}
    for clinic in clinics:
        if clinic.dso_id not in clinics_by_dso:
            clinics_by_dso[clinic.dso_id] = []
        clinics_by_dso[clinic.dso_id].append({
            'id': clinic.id,
            'name': clinic.name
        })

    return render_template(
        'patient_details.html',
        patient=patient,
        cbct_directories=cbct_directories,
        uploaded_files=uploaded_files,
        uploaded_files_one_dcm_file=uploaded_files_one_dcm_file,
        patient_statuses=patient_statuses,
        status_options=status_options,
        all_status_types={option.status_type for option in status_options},
        is_admin=is_admin,
        base_url=base_url,  # Pass the base_url to the template
        dsos=dsos,
        clinics_by_dso=clinics_by_dso,
        scheduled_consultations=patient_details.get('scheduled_consultations', [])  # Pass scheduled consultations
    )




@main.route('/update_patient/<int:patient_id>', methods=['POST'])
@login_required
def update_patient(patient_id):
    try:
        # Retrieve form data
        name = request.form['name']
        email = request.form['email']
        phone = request.form['phone']
        insurer = request.form.get('insurer', '').strip()  # Add insurer field
        policy_id = request.form.get('policy_id', '').strip()  # Add policy_id field
        dob = request.form.get('dob', '').strip()
        clinic_id = request.form.get('clinic_id', '').strip()  # Add clinic_id field
        snoring = request.form['snoring']
        daytime_sleepiness = request.form['daytime_sleepiness']
        sleep_study = request.form['sleep_study']
        sleep_study_date = request.form.get('sleep_study_date')
        sleep_study_doctor = request.form.get('sleep_study_doctor', '').strip()  # Add sleep_study_doctor field
        cpap_intolerant = request.form['cpap_intolerant']
        cpap_intolerant_other = request.form.get('cpap_intolerant_other', '')
        status = request.form['status']
        payment_method = request.form.get('payment_method', 'N/A').strip()

        # Fetch the patient from the database
        patient = Patient.query.get_or_404(patient_id)

        # Update patient details
        patient.name = name
        patient.email = email
        patient.phone = phone
        patient.insurer = insurer  # Update insurer
        patient.policy_id = policy_id  # Update policy_id
        
        # Update clinic_id if provided and user is admin
        if current_user.role == 'admin' and clinic_id:
            try:
                patient.clinic_id = int(clinic_id)
            except ValueError:
                patient.clinic_id = None
        elif current_user.role == 'admin' and not clinic_id:
            patient.clinic_id = None
        # Non-admin users cannot modify clinic_id

        # Handle date of birth conversion safely
        if dob:
            try:
                patient.dob = datetime.strptime(dob, '%Y-%m-%d')
            except ValueError:
                patient.dob = None
        else:
            patient.dob = None

        # Update additional fields
        patient.status = status
        patient.payment_method = payment_method  # Update payment_method explicitly
        patient.snoring = snoring
        patient.daytime_sleepiness = daytime_sleepiness
        patient.sleep_study = sleep_study
        patient.sleep_study_date = datetime.strptime(sleep_study_date, '%Y-%m-%d') if sleep_study == 'yes' and sleep_study_date else None
        patient.sleep_study_doctor = sleep_study_doctor if sleep_study == 'yes' else None  # Update doctor if sleep study was done
        patient.cpap_intolerant = cpap_intolerant
        patient.cpap_intolerant_other = cpap_intolerant_other
        patient.updated_date = datetime.utcnow()

        # Commit changes to the database
        db.session.commit()

        flash('Patient information updated successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating patient information: {str(e)}', 'error')
    
    # Redirect back to patient details
    return redirect(url_for('main.patient_details', patient_id=patient_id))




# Define a helper function to calculate percentage change
def calculate_percentage_change(current_value, previous_value):
    return round(((current_value - previous_value) / previous_value) * 100, 2)


@main.route('/billing')
@login_required
def billing():
    logger.debug('Accessing billing page')
    return render_template('coming-soon.html')


@main.route('/notifications')
@login_required
def notifications():
    logger.debug('Accessing notifications page')
    return render_template('coming-soon.html')

@main.route('/health')
def health_check():
    return jsonify({"status": "healthy"}), 200


@main.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        # Retrieve form data
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        # Validate current password
        if not check_password_hash(current_user.password, current_password):
            flash("Current password is incorrect.", "error")
            return redirect(url_for('main.change_password'))

        # Validate new password meets HIPAA requirements
        if not is_hipaa_compliant(new_password):
            flash("New password must be at least 8 characters long, contain uppercase, lowercase, a number, and a special character.", "error")
            return redirect(url_for('main.change_password'))

        # Check if new password matches confirmation
        if new_password != confirm_password:
            flash("New password and confirmation do not match.", "error")
            return redirect(url_for('main.change_password'))

        # Update password
        current_user.password = generate_password_hash(new_password)
        db.session.commit()
        flash("Password changed successfully!", "success")
        return redirect(url_for('main.index'))

    # Render the change password template
    return render_template('change_password.html')


# Helper function to ensure HIPAA-compliant password
def is_hipaa_compliant(password):
    """Check if the password meets HIPAA requirements."""
    return (len(password) >= 8 and
            re.search(r"[A-Z]", password) and
            re.search(r"[a-z]", password) and
            re.search(r"[0-9]", password) and
            re.search(r"[@$!%*?&]", password))


@main.route('/create_claim', methods=['GET', 'POST'])
@login_required
def create_claim():
    logger.debug('Accessing create claim page')

    if request.method == 'POST':
        # Get form data
        patient_id = request.form.get('patient_id')
        dentist_id = request.form.get('dentist_id')
        insurer = request.form.get('insurer')
        treatment_recommendations = request.form.get('treatment_recommendations')
        other_treatment = request.form.get('other_treatment') if treatment_recommendations == 'Other' else None
        claim_amount = request.form.get('claim_amount')
        deductible = request.form.get('deductible')
        status = request.form.get('status')
        diagnosis = request.form.get('diagnosis')
        comment_text = request.form.get('comments')
        created_date = datetime.utcnow()
        last_update = datetime.utcnow()

        logger.debug(f"Received POST data: "
                     f"patient_id={patient_id}, dentist_id={dentist_id}, insurer={insurer}, "
                     f"treatment_recommendations={treatment_recommendations}, other_treatment={other_treatment}, "
                     f"claim_amount={claim_amount}, deductible={deductible}, status={status}, "
                     f"diagnosis={diagnosis}, comment_text={comment_text}")

        # Validate that patient_id and dentist_id are provided
        if not patient_id or not dentist_id:
            flash("Patient and Dentist fields are required and must be selected from the autocomplete suggestions.", 'red')
            logger.error("Patient ID or Dentist ID is missing; cannot proceed with claim creation.")
            return redirect(url_for('main.create_claim'))

        try:
            # Create and save the new claim in the database
            new_claim = Claim(
                patient_id=patient_id,
                dentist_id=dentist_id,
                insurer=insurer,
                treatment_recommendations=other_treatment if other_treatment else treatment_recommendations,
                claim_amount=claim_amount,
                deductible=deductible,
                status=status,
                diagnosis=diagnosis,
                created_date=created_date,
                last_update=last_update
            )
            db.session.add(new_claim)
            db.session.flush()  # Flush to get new_claim.id before committing
            logger.debug(f"Created claim with ID {new_claim.id}")

            # Save the comment to the Comment table, associated with the claim
            if comment_text:
                new_comment = Comment(
                    claim_id=new_claim.id,
                    content=comment_text,
                    created_date=datetime.utcnow()
                )
                db.session.add(new_comment)
                logger.debug(f"Added comment for claim ID {new_claim.id}")

            # Handle file uploads directly to S3
            uploaded_files = request.files.getlist('claim_files[]')
            logger.debug(f"Number of files uploaded: {len(uploaded_files)}")

            for file in uploaded_files:
                if file:
                    filename = secure_filename(file.filename)
                    s3_key = f'claims/{new_claim.id}/{filename}'

                    # Read the file to determine its size
                    file_stream = file.read()
                    file_size = len(file_stream)
                    file.seek(0)  # Reset file pointer for S3 upload
                    logger.debug(f"Attempting to upload file '{filename}' of size {file_size} bytes to S3 at '{s3_key}'")

                    try:
                        # Upload the file to S3
                        s3_client.upload_fileobj(file, os.getenv('S3_BUCKET_NAME'), s3_key)
                        logger.debug(f"Uploaded {filename} to S3 at {s3_key}")

                        # Save file info in the database
                        new_file = File(
                            name=filename,
                            patient_id=patient_id,
                            upload_date=datetime.utcnow(),
                            file_type=file.mimetype,
                            file_size=file_size,
                            s3_key=s3_key,
                            category='Claim',
                            subcategory='Claim Documents'
                        )
                        db.session.add(new_file)
                        logger.debug(f"File '{filename}' added to DB with claim ID {new_claim.id}")
                    except Exception as e:
                        logger.error(f"Failed to upload file {filename} to S3: {str(e)}")
                        flash(f"Error uploading file {filename}: {str(e)}", 'red')

            # Commit the claim, comment, and file records to the database
            db.session.commit()
            flash('Claim created successfully!', 'green')
            logger.debug("Claim and associated records committed successfully")
            return redirect(url_for('dashboard.dashboard_view'))

        except Exception as e:
            db.session.rollback()
            logger.error(f'Error creating claim: {str(e)}')
            flash(f'Error creating claim: {str(e)}', 'red')
            return redirect(url_for('dashboard.dashboard_view'))

    # For GET request, render the create claim form
    patients = Patient.query.filter(Patient.status != 'Archived').all()  # Fetch patients for selection (excluding archived)
    dentists = Dentist.query.all()  # Fetch dentists for selection
    return render_template('create_claim.html', patients=patients, dentists=dentists)


@main.route('/get_patients_for_autocomplete_forms', methods=['GET'])
def get_patients_for_autocomplete_forms():
    """
    Fetch patients for autocomplete with server-side filtering.
    """
    try:
        # Get the query parameter
        query = request.args.get('query', '').strip()
        if not query:
            # Return empty list if no query is provided
            return jsonify({'patients': []})

        # Filter patients whose name matches the query and are not archived
        patients = Patient.query.filter(
            Patient.name.ilike(f"%{query}%"),
            Patient.status != 'Archived'  # Exclude archived patients
        ).limit(10).all()

        # Prepare the response
        patient_data = [{
            'id': patient.id,
            'name': patient.name,
            'mobile': patient.phone,
            'email': patient.email,
            'address': patient.address
        } for patient in patients]

        return jsonify({'patients': patient_data}), 200

    except Exception as e:
        # Log error for debugging
        logger.error(f"Error in get_patients_for_autocomplete: {e}")
        return jsonify({'error': 'An error occurred while fetching patient data.'}), 500

@main.route('/get_patients_for_autocomplete', methods=['GET'])
@login_required
def get_patients_for_autocomplete():
    """
    Get patients for autocomplete with search filtering
    """
    try:
        query = request.args.get('query', '').strip()
        if len(query) < 2:
            return jsonify({'patients': []})
        
        # Fetch patients based on user role, excluding archived patients and filtering by query
        if current_user.role == 'admin':
            # Admin can see all patients except archived ones, filtered by query
            patients = Patient.query.filter(
                Patient.status != 'Archived',
                Patient.name.ilike(f'%{query}%')
            ).limit(10).all()
        else:
            # Non-admin users should see patients in their DSO only, excluding archived ones, filtered by query
            patients = Patient.query.join(Dentist).filter(
                Dentist.DSO == current_user.DSO,
                Patient.status != 'Archived',
                Patient.name.ilike(f'%{query}%')
            ).limit(10).all()

        # Prepare data for autocomplete
        patient_data = [{
            'id': patient.id,
            'name': patient.name,
            'mobile': patient.phone,
            'insurer': getattr(patient, 'insurer', ''),
            'policy_id': getattr(patient, 'policy_id', ''),
            'dentist_name': patient.dentist.name if patient.dentist else '',
            'dentist_id': patient.dentist.id if patient.dentist else '',
            'email': getattr(patient, 'email', ''),
            'address': getattr(patient, 'address', ''),
            'status': getattr(patient, 'status', ''),
            'create_date': getattr(patient, 'create_date', '').strftime('%Y-%m-%d') if getattr(patient, 'create_date', '') else ''
        } for patient in patients]

        return jsonify({'patients': patient_data})
        
    except Exception as e:
        logger.error(f"Error fetching patients for autocomplete: {e}")
        return jsonify({'error': 'Failed to fetch patients'}), 500

@main.route('/upload_new', methods=['GET', 'POST'])
@login_required
def upload_new():
    if request.method == 'POST':
        logger.debug('Processing upload new POST request')
        try:
            patient_data = {
                'name': request.form.get('patient_name'),
                'email': request.form.get('email'),
                'phone': request.form.get('phone'),
                'dob': datetime.strptime(request.form.get('dob'), '%Y-%m-%d') if request.form.get('dob') else None,
                'gender': request.form.get('gender'),
                'insurer': request.form.get('insurer'),
                'policy_id': request.form.get('policy_id'),
                'address': request.form.get('address'),
                'snoring': request.form.get('snoring'),
                'snoring_other': request.form.get('snoring_other') if request.form.get('snoring') == 'other' else None,
                'daytime_sleepiness': request.form.get('daytime_sleepiness'),
                'daytime_sleepiness_other': request.form.get('daytime_sleepiness_other') if request.form.get('daytime_sleepiness') == 'other' else None,
                'sleep_study': request.form.get('sleep_study'),
                'sleep_study_date': datetime.strptime(request.form.get('sleep_study_date'), '%Y-%m-%d') if request.form.get('sleep_study_date') else None,
                'sleep_study_doctor': request.form.get('sleep_study_doctor') if request.form.get('sleep_study') == 'yes' else None,
                'cpap_intolerant': request.form.get('cpap_intolerant'),
                'cpap_intolerant_other': request.form.get('cpap_intolerant_other') if request.form.get('cpap_intolerant') == 'other' else None,
                'create_date': datetime.now(),
                'last_update': datetime.now(),
                'payment_method': request.form.get('payment_method'),
                'status': request.form.get('status') or 'new',
                'dentist_id': int(request.form.get('dentist_id')) if current_user.role == 'admin' and request.form.get('dentist_id') else current_user.id,
                'clinic_id': int(request.form.get('clinic_id')) if request.form.get('clinic_id') else None,
            }

            logger.debug(f"Patient form data: {patient_data}")

            new_patient = Patient(**patient_data)
            db.session.add(new_patient)
            db.session.commit()
            logger.debug(f'Created new patient with ID: {new_patient.id}')

            return jsonify({'success': True, 'patient_id': new_patient.id})

        except Exception as e:
            db.session.rollback()
            logger.error(f'Error during patient creation: {str(e)}')
            return jsonify({'success': False, 'message': f'Error uploading data: {str(e)}'}), 500

    # GET request - prepare the form with dropdowns
    dsos = DSO.query.all()
    clinics = Clinic.query.all()

    # Organize clinics by DSO
    clinics_by_dso = {}
    for clinic in clinics:
        clinics_by_dso.setdefault(clinic.dso_id, []).append(clinic)

    # Organize dentists by clinic
    from flask_app.models import dentist_clinic_association, Dentist
    dentists_by_clinic = {}
    dentist_clinic_rows = db.session.query(
        dentist_clinic_association.c.dentist_id,
        dentist_clinic_association.c.clinic_id
    ).all()

    dentist_ids = {row.dentist_id for row in dentist_clinic_rows}
    dentist_lookup = {d.id: d for d in Dentist.query.filter(Dentist.id.in_(dentist_ids)).all()}

    for row in dentist_clinic_rows:
        dentists_by_clinic.setdefault(row.clinic_id, []).append({
            'id': row.dentist_id,
            'name': dentist_lookup[row.dentist_id].name
        })

    return render_template(
        'upload_form_new.html',
        dsos=dsos,
        clinics_by_dso=clinics_by_dso,
        dentists_by_clinic=dentists_by_clinic,
        is_admin=current_user.role == 'admin'
    )


from flask import jsonify, request, redirect, url_for, flash
from flask_login import login_required, current_user
from datetime import datetime
from werkzeug.utils import secure_filename
import logging
import boto3


@main.route('/store_file_metadata', methods=['POST'])
@login_required
def store_file_metadata():
    """
    Stores file metadata in the database after upload.
    """
    try:
        logger.debug("=== START store_file_metadata ===")
        data = request.json
        logger.debug(f"Received metadata: {data}")
        
        patient_id = data.get('patient_id')
        s3_key = data.get('s3_key')
        
        # Validate required fields
        if not patient_id:
            logger.error(f"Missing 'patient_id' in request: {data}")
            return jsonify({'success': False, 'message': "Missing 'patient_id' in request"}), 400
        if not s3_key:
            logger.error(f"Missing 's3_key' in request: {data}")
            return jsonify({'success': False, 'message': "Missing 's3_key' in request"}), 400
            
        # Handle patient_id as string - convert to int if it's a string
        if isinstance(patient_id, str):
            try:
                patient_id = int(patient_id)
                logger.debug(f"Converted patient_id from string to int: {patient_id}")
            except ValueError:
                logger.error(f"Invalid patient_id format (not convertible to int): {patient_id}")
                return jsonify({'success': False, 'message': f"Invalid patient_id format: {patient_id}"}), 400
            
        filename = s3_key.split("/")[-1]
        file_size = data.get('file_size', 0)
        file_type = data.get('file_type', 'application/octet-stream')
        category = data.get('category')  # Ensure category is provided
        subcategory = data.get('subcategory')  # Ensure subcategory is provided

        logger.debug(f"Extracted data - patient_id: {patient_id}, s3_key: {s3_key}")
        logger.debug(f"Extracted data - filename: {filename}, file_size: {file_size}, file_type: {file_type}")
        logger.debug(f"Extracted data - category: {category}, subcategory: {subcategory}")

        # Validate category and subcategory
        if not category:
            logger.error(f"Missing 'category' for file metadata: {filename}")
            return jsonify({'success': False, 'message': "Missing 'category' for file metadata"}), 400
        if not subcategory:
            logger.error(f"Missing 'subcategory' for file metadata: {filename}")
            return jsonify({'success': False, 'message': "Missing 'subcategory' for file metadata"}), 400

        # Check if patient exists
        patient = Patient.query.get(patient_id)
        if not patient:
            logger.error(f"Patient with ID {patient_id} not found.")
            return jsonify({'success': False, 'message': f"Patient with ID {patient_id} not found"}), 404

        logger.debug(f"Patient found: {patient.name} (ID: {patient.id})")
        logger.debug(f"Creating file entry for {filename} with s3_key: {s3_key}")
        
        # Check if S3 key already exists in the database to avoid duplicates
        existing_file = File.query.filter_by(s3_key=s3_key).first()
        if existing_file:
            logger.warning(f"File with s3_key {s3_key} already exists in database with ID {existing_file.id}")
            return jsonify({
                'success': True,
                'message': 'File metadata already exists.',
                'file_id': existing_file.id,
                'already_exists': True
            })
        
        # Verify S3 key exists in bucket (but don't fail if check doesn't work)
        try:
            bucket_name = os.getenv('S3_BUCKET_NAME')
            logger.debug(f"Checking if S3 key exists in bucket: {bucket_name}/{s3_key}")
            s3_client.head_object(Bucket=bucket_name, Key=s3_key)
            logger.debug(f"S3 object exists: {s3_key}")
        except Exception as s3_error:
            logger.warning(f"Unable to verify S3 object exists (will proceed anyway): {str(s3_error)}")
        
        # Create a new file entry in the database
        new_file = File(
            name=filename,
            patient_id=patient_id,
            s3_key=s3_key,
            upload_date=datetime.utcnow(),
            file_size=file_size,
            file_type=file_type,
            category=category,
            subcategory=subcategory
        )
        
        logger.debug(f"File object created, about to add to database session")
        db.session.add(new_file)
        logger.debug(f"File added to session, about to commit")
        db.session.commit()
        logger.debug(f"Database commit successful")
        
        # Get the ID of the newly created file entry
        file_id = new_file.id
        
        logger.info(f"File metadata stored successfully for file: {filename}, ID: {file_id}")
        logger.debug("=== END store_file_metadata ===")
        return jsonify({
            'success': True, 
            'message': 'File metadata stored successfully.',
            'file_id': file_id
        })

    except SQLAlchemyError as db_error:
        db.session.rollback()
        error_message = str(db_error)
        logger.error(f"Database error storing file metadata: {error_message}")
        logger.error(f"Data that caused the error: {data if 'data' in locals() else 'data not available'}")
        
        # Check for common database errors
        if 'IntegrityError' in error_message:
            if 'foreign key constraint' in error_message.lower():
                logger.error("Foreign key constraint violation - patient ID may not exist")
            elif 'unique constraint' in error_message.lower():
                logger.error("Unique constraint violation - duplicate file entry")
        
        return jsonify({'success': False, 'message': f"Database error: {error_message}"}), 500

    except Exception as e:
        db.session.rollback()
        error_message = str(e)
        logger.error(f"Error storing file metadata: {error_message}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Stack trace:", exc_info=True)
        logger.error(f"Request data: {request.json}")
        return jsonify({'success': False, 'message': f"Error storing file metadata: {error_message}"}), 500

@main.route('/store_admin_file_metadata', methods=['POST'])
@login_required
def store_admin_file_metadata():
    """
    Stores admin file metadata in the database after upload.
    """
    try:
        data = request.json
        patient_id = data.get('patient_id')
        s3_key = data.get('s3_key')
        filename = s3_key.split("/")[-1]
        file_size = data.get('file_size', 0)
        file_type = data.get('file_type', 'application/octet-stream')
        # New fields
        is_public = data.get('is_public', False)
        file_category = data.get('file_category')

        # Validate required fields
        if not patient_id or not s3_key:
            logger.error(f"Missing 'patient_id' or 's3_key' for file metadata: {filename}")
            return jsonify({'success': False, 'message': "Missing 'patient_id' or 's3_key' for file metadata"}), 400

        # Check if patient exists
        patient = Patient.query.get(patient_id)
        if not patient:
            logger.error(f"Patient with ID {patient_id} not found.")
            return jsonify({'success': False, 'message': f"Patient with ID {patient_id} not found"}), 404

        # Create a new AdminFile entry in the database
        new_admin_file = AdminFile(
            name=filename,
            patient_id=patient_id,
            s3_key=s3_key,
            upload_date=datetime.utcnow(),
            file_size=file_size,
            file_type=file_type,
            is_public=bool(is_public),
            file_category=file_category
        )
        db.session.add(new_admin_file)
        db.session.commit()

        logger.info(f"Admin file metadata stored successfully for file: {filename}")
        return jsonify({'success': True, 'message': 'Admin file metadata stored successfully.'})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error storing admin file metadata: {e}")
        return jsonify({'success': False, 'message': f"Error storing admin file metadata: {str(e)}"}), 500

@main.route('/delete_admin_file/<int:file_id>', methods=['POST'])
@login_required
def delete_admin_file(file_id):
    logger.info(f"Delete admin file request received for file ID: {file_id}")
    logger.info(f"User: {current_user.email}, Role: {getattr(current_user, 'role', 'unknown')}")
    
    try:
        # Only allow admins
        if not hasattr(current_user, 'role') or current_user.role != 'admin':
            logger.warning(f"Permission denied for user {current_user.email}")
            return jsonify({'success': False, 'message': 'Permission denied'}), 403
            
        admin_file = AdminFile.query.get(file_id)
        if not admin_file:
            logger.warning(f"Admin file with ID {file_id} not found")
            return jsonify({'success': False, 'message': 'File not found'}), 404
            
        logger.info(f"Found admin file: {admin_file.name}, S3 key: {admin_file.s3_key}")
        
        # Delete from S3
        try:
            s3_client = get_s3_client()
            s3_client.delete_object(Bucket=os.getenv('S3_BUCKET_NAME'), Key=admin_file.s3_key)
            logger.info(f"Deleted admin file '{admin_file.name}' from S3")
        except Exception as s3_error:
            logger.warning(f"Could not delete file from S3: {s3_error}")
            
        # Delete from database
        db.session.delete(admin_file)
        db.session.commit()
        
        logger.info(f"Successfully deleted admin file ID {file_id}")
        return jsonify({'success': True, 'message': 'File deleted successfully'})
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting admin file {file_id}: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@main.route('/file/<int:file_id>/update_comment', methods=['POST'])
@login_required
def update_file_comment(file_id):
    try:
        # Fetch the file and associated patient and dentist
        file = File.query.get_or_404(file_id)
        patient = Patient.query.get_or_404(file.patient_id)
        dentist = Dentist.query.get_or_404(patient.dentist_id)

        # Check user permissions
        if current_user.role != 'admin' and dentist.DSO != current_user.DSO:
            return jsonify({'success': False, 'message': 'Permission denied'}), 403

        # Retrieve the comment from the JSON request body
        data = request.get_json()
        new_comment = data.get('comment', '').strip()

        # Validate the comment
        if not new_comment:
            return jsonify({'success': False, 'message': 'Comment cannot be empty.'}), 400

        # Update the file's comment
        file.comment = new_comment
        db.session.commit()

        return jsonify({'success': True, 'message': 'Comment updated successfully.'})

    except SQLAlchemyError as db_error:
        db.session.rollback()
        logger.error(f"Database error updating comment for file {file_id}: {str(db_error)}")
        return jsonify({'success': False, 'message': 'Database error occurred.'}), 500

    except Exception as e:
        logger.error(f"Error updating comment for file {file_id}: {str(e)}")
        return jsonify({'success': False, 'message': 'An unexpected error occurred.'}), 500
    try:
        file = File.query.get_or_404(file_id)
        patient = Patient.query.get_or_404(file.patient_id)
        dentist = Dentist.query.get_or_404(patient.dentist_id)

        # Updated permission check
        if current_user.role != 'admin' and dentist.DSO != current_user.DSO:
            return jsonify({'success': False, 'message': 'Permission denied'}), 403

        new_comment = request.form.get('comment')
        if new_comment is None:
            return jsonify({'success': False, 'message': 'Comment cannot be empty'}), 400

        file.comment = new_comment
        db.session.commit()
        return jsonify({'success': True, 'message': 'Comment updated successfully'})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating comment for file {file_id}: {str(e)}")
        return jsonify({'success': False, 'message': 'An error occurred'}), 500


@main.route('/file/<int:file_id>/update_mapping', methods=['POST'])
@login_required
def update_file_mapping(file_id):
    try:
        # Fetch the file by ID
        file = File.query.get_or_404(file_id)

        # Check if the user has permission to update the mapping
        patient = Patient.query.get_or_404(file.patient_id)
        if current_user.id != patient.dentist_id and not current_user.is_admin:
            return jsonify({'success': False, 'message': 'You do not have permission to update this file.'}), 403

        # Get the new mapping from the form data
        new_mapping = request.form.get('mapping')
        if not new_mapping:
            return jsonify({'success': False, 'message': 'Mapping selection cannot be empty.'}), 400

        # Update the mapping field in the File model
        file.mapping = new_mapping

        # Commit the changes to the database
        db.session.commit()
        return jsonify({'success': True, 'message': 'Mapping updated successfully.'}), 200

    except Exception as e:
        # Log the exception for debugging (optional)
        print(f"Error updating mapping: {e}")
        return jsonify({'success': False, 'message': 'An error occurred while updating the mapping.'}), 500

@main.route('/patient/<int:patient_id>/status/update', methods=['POST'])
@login_required
def update_patient_status(patient_id):
    data = request.get_json()

    # Log the incoming data for debugging
    app.logger.debug("Received data for update_patient_status endpoint:")
    app.logger.debug(data)

    try:
        # Extracting and processing information from the request
        status_id = int(data.get('status_id', -1))  # Ensure status_id is an integer
        status_type = data.get('status_type')  # Extract status_type from request data
        status_value = data.get('status_value')
        comment = data.get('comment', '').strip()
        mapping = data.get('mapping', '').strip()  # Extract mapping from request data

        app.logger.debug(f"Parsed data - status_id: {status_id} (type: {type(status_id)}), "
                         f"status_type: {status_type}, status_value: {status_value}, "
                         f"comment: {comment}, mapping: {mapping}")

        if status_id == -1:
            # Ensure that status_type is not None for new entries
            if not status_type:
                app.logger.error("status_type is null for new status entry.")
                return jsonify({'success': False, 'message': 'Status type cannot be null for new entries.'}), 400

            # Create a new PatientStatus entry if status_id is -1
            new_status = PatientStatus(
                patient_id=patient_id,
                status_type=status_type,  # Insert status_type
                status_value=status_value,
                comment=comment,
                mapping=mapping,  # Insert mapping
                updated_at=datetime.utcnow()
            )
            db.session.add(new_status)
            app.logger.debug(f"New status created: {new_status}")
        else:
            # Fetch the existing patient status record
            status = PatientStatus.query.filter_by(id=status_id, patient_id=patient_id).first()
            if not status:
                app.logger.error(f"Status not found for status_id: {status_id}, patient_id: {patient_id}")
                return jsonify({'success': False, 'message': 'Status not found.'}), 404

            # Update the existing status
            status.status_value = status_value
            status.comment = comment
            status.mapping = mapping  # Update mapping field
            status.updated_at = datetime.utcnow()
            app.logger.debug(f"Status updated: {status}")

        # Commit changes to the database
        db.session.commit()
        app.logger.info("Status update committed to the database successfully.")

        return jsonify({'success': True, 'message': 'Status updated successfully'})

    except ValueError as ve:
        app.logger.error(f"Invalid value for status_id: {data.get('status_id')}")
        return jsonify({'success': False, 'message': 'Invalid status ID format.'}), 400

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error updating status: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500


@main.route('/patient_status_list', methods=['GET'])
@login_required
def patient_status_list():
    """
    Endpoint to fetch and render the patient status list page with all necessary patient details,
    including dynamic status headers.
    Implements DSO-based access control - dentists can only see patients from their associated DSOs.
    """
    logger.debug("Accessing the patient status list endpoint.")
    
    # Fetch distinct status types for headers
    try:
        status_headers = [
            status.status_type 
            for status in PatientStatus.query.with_entities(PatientStatus.status_type).distinct()
        ]
        logger.debug(f"Fetched {len(status_headers)} distinct status headers: {status_headers}")
    except Exception as e:
        logger.error(f"Error fetching status headers: {e}")
        status_headers = []

    # Base query for all patients with proper access control
    try:
        # If the current user is an admin, they can see all patients
        if current_user.role == 'admin':
            patients = Patient.query.filter(Patient.status != 'Archived').order_by(Patient.create_date.desc()).all()
            logger.debug(f'Admin viewing all patients. Total patients found: {len(patients)}')
        
        elif current_user.role in ['Dentist', 'dentist', 'Dentists']:
            # Dentist can only see patients treated by dentists in their same DSO
            logger.debug(f'Dentist {current_user.name} with DSO: {getattr(current_user, "DSO", "None")} attempting to view patient status list.')

            # Try new DSO system first, then fall back to legacy
            if hasattr(current_user, 'dsos') and current_user.dsos.count() > 0:
                # NEW SYSTEM: Use DSO associations
                logger.debug('Using new DSO association system')
                dso_ids = current_user.get_dso_ids()
                patients = (Patient.query
                            .join(Dentist)
                            .join(Clinic, Patient.clinic_id == Clinic.id, isouter=True)
                            .filter(
                                db.or_(
                                    Clinic.dso_id.in_(dso_ids),  # New system patients
                                    db.and_(Patient.clinic_id.is_(None), Dentist.DSO == getattr(current_user, 'DSO', None))  # Legacy patients
                                ),
                                Patient.status != 'Archived'
                            )
                            .order_by(Patient.create_date.desc())
                            .all())
            elif hasattr(current_user, 'DSO') and current_user.DSO:
                # LEGACY SYSTEM: Use DSO string
                logger.debug('Using legacy DSO string system')
                patients = (Patient.query
                            .join(Dentist)
                            .filter(
                                Dentist.DSO == current_user.DSO,
                                Patient.status != 'Archived'
                            )
                            .order_by(Patient.create_date.desc())
                            .all())
            else:
                # No DSO association found
                logger.warning(f'Dentist {current_user.name} has no DSO associations')
                patients = []

            # Log the DSO of the current user and compare it with patients' dentists
            logger.debug(f'Number of patients found: {len(patients)}')
            for patient in patients[:5]:  # Log first 5 for debugging
                dentist_dso = getattr(patient.dentist, 'DSO', 'None') if patient.dentist else 'None'
                clinic_dso = patient.clinic.dso_id if patient.clinic else 'None'
                logger.debug(f"Patient: {patient.name}, Dentist DSO: {dentist_dso}, Clinic DSO: {clinic_dso}")

            if not patients:
                logger.warning(f'No patients found for dentist: {current_user.name}')
            else:
                logger.debug(f'{len(patients)} patients found for dentist: {current_user.name}')
        
        else:
            flash('Unauthorized access', 'error')
            logger.warning(f'Unauthorized access attempt by user {current_user.name} with role {current_user.role}')
            return redirect(url_for('main.index'))

        logger.debug(f"Fetched {len(patients)} patients from the database.")
    except Exception as e:
        logger.error(f"Error fetching patients: {e}")
        patients = []

    # Collect patient data
    patient_data = []
    for patient in patients:
        try:
            # Directly use the `status` column from the Patient model
            patient_status = patient.status if patient.status else "N/A"
            logger.debug(f"Patient ID {patient.id} status: {patient_status}")

            # Fetch other patient details
            patient_details = fetch_patient_details(patient.id)
            logger.debug(f"Fetched details for patient ID {patient.id}")

            patient_data.append({
                "id": patient.id,
                "name": patient.name,
                "status": patient_status,  # Use directly from Patient model
                "phone": patient.phone,
                "payment_method": patient.payment_method,
                "last_update": patient.last_update.strftime('%Y-%m-%d %H:%M:%S') if patient.last_update else "N/A",
                "comments": patient_details['comments'],
                "statuses": {
                    status.status_type: status.status_value 
                    for status in patient_details['patient_statuses'].values()
                },
                "uploaded_files": patient_details['uploaded_files'],
                "uploaded_files_one_dcm_file": patient_details['uploaded_files_one_dcm_file']
            })
        except Exception as e:
            logger.error(f"Error processing patient ID {patient.id}: {e}")

    if not patient_data:
        logger.warning("No patient data to render. Check database or query logic.")

    # Render the template with full patient data and dynamic headers
    try:
        response = render_template(
            "patient_status_list.html",
            patients=patient_data,
            status_headers=status_headers,  # Pass the dynamic headers to the template
        )
        logger.debug("Template rendered successfully.")
        return response
    except Exception as e:
        logger.error(f"Error rendering template: {e}")
        return "Error rendering the page.", 500

def fetch_patient_details(patient_id):
    """
    Fetch detailed information for a specific patient, including files, statuses, status options, and comments.
    """
    # Fetch patient from the database
    patient = Patient.query.get_or_404(patient_id)

    # Initialize uploaded files dictionaries
    uploaded_files = {}
    uploaded_files_one_dcm_file = {}

    # Categories and subcategories for files
    categories = {
        'billing': {'category': 'billing'},
        'clinical_pictures': {'subcategory': 'clinical-pictures'},
        'cbct': {'subcategory': 'cbct'},
        'intraoral_scan': {'subcategory': 'intraoral-scan'},
        'sleep_test': {'subcategory': 'sleep-test'},
        'questionnaire': {'subcategory': 'questionnaire'},
        'medical_background': {'subcategory': 'medical-background'},
        # Reports will now be fetched from AdminFiles
    }

    # Iterate over each category
    for category, filter_kwargs in categories.items():
        # Fetch all files for the category
        all_files = File.query.filter_by(patient_id=patient.id, **filter_kwargs).all()

        # Separate non-`.dcm` and the first `.dcm` file
        non_dcm_files = [file for file in all_files if not file.name.endswith('dcm')]
        dcm_file = next((file for file in all_files if file.name.endswith('dcm')), None)

        # Serialize non-DCM files
        uploaded_files[category] = [
            {"id": file.id, "name": file.name, "file_size": file.file_size}
            for file in non_dcm_files
        ]

        # Serialize the first `.dcm` file
        if dcm_file:
            unique_name = f"many_dcm_{category}.dcm"
            dcm_file.name = unique_name  # Update the name in memory
            uploaded_files_one_dcm_file[category] = [{
                "id": dcm_file.id,
                "name": dcm_file.name,
                "file_size": dcm_file.file_size
            }]

            # -- NEW LOGIC FOR COUNTING DIRECTORIES UNDER CBCT --
    # Assume you have access to patient_id in this scope
    cbct_prefix = f"patients/{patient_id}/imaging/cbct/"

    # Prepare list to hold directory names
    cbct_directories = []
    bucket_name = os.getenv('S3_BUCKET_NAME')  # Replace with your actual bucket name
    # Use Delimiter='/' to get "CommonPrefixes" for directories
    response = s3_client.list_objects_v2(
        Bucket=bucket_name,
        Prefix=cbct_prefix,
        Delimiter='/'
    )

    # Check if there are any directories under the cbct prefix
    if 'CommonPrefixes' in response:
        for cp in response['CommonPrefixes']:
            # Each 'Prefix' in CommonPrefixes includes the full path, e.g. "patients/123/imaging/cbct/folder/"
            dir_name = cp['Prefix'].replace(cbct_prefix, '').rstrip('/')
            if dir_name:  # Ensure we don't add empty strings
                cbct_directories.append(dir_name)


    # Fetch reports from AdminFiles
    admin_reports = AdminFile.query.filter_by(patient_id=patient.id).all()
    uploaded_files['reports'] = [
    {
        "id": report.id,
        "name": report.name,
        "file_size": report.file_size,
        "category": "reports",  # Add category for the Reports section
        "is_public": getattr(report, 'is_public', False),
        "file_category": getattr(report, 'file_category', None)
    }
        for report in admin_reports
    ]

    # Fetch all status options
    status_options = StatusOption.query.all()

    # Fetch existing patient statuses, including the mapping
    patient_statuses = {status.status_type: status for status in PatientStatus.query.filter_by(patient_id=patient_id).all()}

    # Fetch patient comments
    comments = [
        {
            "content": comment.content,
            "created_date": comment.created_date.strftime('%Y-%m-%d %H:%M:%S')
        }
        for comment in PatientComment.query.filter_by(patient_id=patient.id).all()
    ]

    # Fetch scheduled consultations
    from flask_app.models import PatientConsultSchedule
    consultations = PatientConsultSchedule.query.filter_by(patient_id=patient.id).all()
    scheduled_consultations = [
        {
            "id": consultation.id,
            "consult_type": consultation.consult_type,
            "scheduled_datetime": consultation.scheduled_datetime.strftime('%Y-%m-%d %H:%M:%S') if consultation.scheduled_datetime else None,
            "status": consultation.status,
            "doctor_name": consultation.doctor_name,
            "notes": consultation.notes,
            "completed_datetime": consultation.completed_datetime.strftime('%Y-%m-%d %H:%M:%S') if consultation.completed_datetime else None,
            "comment": consultation.comment
        }
        for consultation in consultations
    ]

    # Create a list of all possible status types
    all_status_types = {option.status_type for option in status_options}

    # Return collected patient details
    return {
        "patient": patient,
        "uploaded_files": uploaded_files,
        "uploaded_files_one_dcm_file": uploaded_files_one_dcm_file,
        "cbct_directories": cbct_directories,
        "patient_statuses": patient_statuses,
        "status_options": status_options,
        "all_status_types": all_status_types,
        "comments": comments,  # Include comments in the returned details
        "scheduled_consultations": scheduled_consultations  # Include scheduled consultations
    }

@main.route('/api/patients/<int:patient_id>/comments', methods=['GET', 'POST'])
@login_required
def patient_comments(patient_id):
    """
    Endpoint to handle fetching and saving comments for a patient.
    """
    app.logger.debug(f"Received request for comments for patient ID: {patient_id}")
    
    # Ensure the patient exists
    patient = Patient.query.get_or_404(patient_id)
    app.logger.debug(f"Patient retrieved: ID={patient.id}, Name={patient.name}")

    dentist_id = current_user.id
    app.logger.debug(f"Current user (dentist): ID={dentist_id}")

    if request.method == 'GET':
        try:
            app.logger.debug("Fetching comments for patient.")
            comments = PatientComment.query.filter_by(patient_id=patient_id).order_by(PatientComment.created_date.desc()).all()
            comments_data = [
                {
                    'id': comment.id,
                    'content': comment.content,
                    'created_date': comment.created_date.strftime('%Y-%m-%d %H:%M:%S'),
                    'dentist': comment.dentist.name
                }
                for comment in comments
            ]
            app.logger.debug(f"Fetched {len(comments)} comments for patient ID {patient_id}")
            return jsonify({'success': True, 'comments': comments_data})
        except Exception as e:
            app.logger.error(f"Error fetching comments for patient ID {patient_id}: {str(e)}")
            return jsonify({'success': False, 'message': f'Error fetching comments: {str(e)}'}), 500

    elif request.method == 'POST':
        app.logger.debug("Processing POST request to add a comment.")
        try:
            data = request.get_json()
            app.logger.debug(f"Received data: {data}")
            
            if not data:
                app.logger.warning("No data received in POST request.")
                return jsonify({'success': False, 'message': 'No data provided'}), 400

            content = data.get('content', '').strip()
            if not content:
                app.logger.warning("Empty content provided for the comment.")
                return jsonify({'success': False, 'message': 'Comment cannot be empty'}), 400

            # Save the new comment
            new_comment = PatientComment(
                patient_id=patient_id,
                content=content,
                created_date=datetime.utcnow(),
                dentist_id=dentist_id
            )
            app.logger.debug(f"New comment to be added: {new_comment}")
            
            db.session.add(new_comment)
            db.session.commit()
            app.logger.info(f"Comment successfully added for patient ID {patient_id} by dentist ID {dentist_id}")
            return jsonify({'success': True, 'message': 'Comment added successfully'})
        except Exception as e:
            app.logger.error(f"Error saving comment for patient ID {patient_id}: {str(e)}")
            db.session.rollback()
            return jsonify({'success': False, 'message': f'Error saving comment: {str(e)}'}), 500


@main.route('/get_status_types', methods=['GET'])
def get_status_types():
    """
    Fetch distinct status types from the patient_status table for the client.
    """
    app.logger.debug(f"Received request for upodate field  for patient ID: {patient_id}")
    try:
        # Fetch distinct status_type from the patient_status table
        status_types = [status.status_type for status in PatientStatus.query.distinct(PatientStatus.status_type).all()]

        # Return the status types as JSON
        return jsonify({'status_types': status_types}), 200

    except Exception as e:
        # Log the error and return a 500 response
        logger.error(f"Error fetching status types: {e}")
        return jsonify({'error': 'An error occurred while fetching status types.'}), 500

@main.route('/api/patient/<int:patient_id>/update_field', methods=['POST'])
@login_required
def update_patient_field(patient_id):
    """
    Endpoint to update a specific field of a patient based on a key-value pair.
  @  """
    try:
        # Parse JSON data from the request
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400

        # Extract the key and value
        key = data.get('key')
        value = data.get('value')
        
        if not key or value is None:
            return jsonify({'success': False, 'message': 'Both key and value are required'}), 400

        # Fetch the patient record
        patient = Patient.query.get_or_404(patient_id)

        # Check if the user has permission to update this patient
        if current_user.role != 'admin' and patient.dentist_id != current_user.id:
            return jsonify({'success': False, 'message': 'Permission denied'}), 403

        # Update the corresponding field dynamically
        if hasattr(patient, key):
            setattr(patient, key, value)
            patient.last_update = datetime.utcnow()  # Update the timestamp
        else:
            return jsonify({'success': False, 'message': f'Invalid field: {key}'}), 400

        # Commit the changes to the database
        db.session.commit()
        return jsonify({'success': True, 'message': 'Patient updated successfully'}), 200

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error updating patient field: {str(e)}")
        return jsonify({'success': False, 'message': f'Error updating patient: {str(e)}'}), 500

@main.route('/archive_patient/<int:patient_id>', methods=['POST'])
def archive_patient(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    patient.status = "Archived"
    db.session.commit()
    # After archiving, redirect back to the list (which won't show archived patients)
    return redirect(url_for('main.patient_list'))

@main.route('/support')
def support():
    return render_template('support.html')



@main.route('/test_metadata_store', methods=['GET'])
@login_required
def test_metadata_store():
    """
    Test route for metadata storage.
    """
    try:
        logger.debug("=== START test_metadata_store ===")
        
        # Hardcoded test data for patient ID 10314
        test_data = {
            "patient_id": "10314",
            "s3_key": "patients/10314/billing/test_file.txt",
            "file_size": 1024,
            "file_type": "text/plain",
            "category": "billing",
            "subcategory": "billing"
        }
        
        logger.debug(f"Test metadata: {test_data}")
        
        patient_id = test_data.get('patient_id')
        s3_key = test_data.get('s3_key')
        filename = s3_key.split("/")[-1]
        file_size = test_data.get('file_size', 0)
        file_type = test_data.get('file_type', 'application/octet-stream')
        category = test_data.get('category')
        subcategory = test_data.get('subcategory')

        logger.debug(f"Extracted data - patient_id: {patient_id}, s3_key: {s3_key}")
        logger.debug(f"Extracted data - filename: {filename}, file_size: {file_size}, file_type: {file_type}")
        logger.debug(f"Extracted data - category: {category}, subcategory: {subcategory}")

        # Check if patient exists
        patient = Patient.query.get(patient_id)
        if not patient:
            logger.error(f"Patient with ID {patient_id} not found.")
            return jsonify({'success': False, 'message': f"Patient with ID {patient_id} not found"}), 404

        logger.debug(f"Patient found: {patient.name} (ID: {patient.id})")
        
        # Create a new file entry in the database
        new_file = File(
            name=filename,
            patient_id=patient_id,
            s3_key=s3_key,
            upload_date=datetime.utcnow(),
            file_size=file_size,
            file_type=file_type,
            category=category,
            subcategory=subcategory
        )
        
        logger.debug(f"File object created, about to add to database session")
        db.session.add(new_file)
        logger.debug(f"File added to session, about to commit")
        db.session.commit()
        logger.debug(f"Database commit successful")
        
        # Get the ID of the newly created file entry
        file_id = new_file.id
        
        logger.info(f"Test file metadata stored successfully for file: {filename}, ID: {file_id}")
        logger.debug("=== END test_metadata_store ===")
        
        return jsonify({
            'success': True, 
            'message': 'Test file metadata stored successfully.',
            'file_id': file_id
        })

    except SQLAlchemyError as db_error:
        db.session.rollback()
        error_message = str(db_error)
        logger.error(f"Database error storing test file metadata: {error_message}")
        return jsonify({'success': False, 'message': f"Database error: {error_message}"}), 500

    except Exception as e:
        db.session.rollback()
        error_message = str(e)
        logger.error(f"Error in test_metadata_store: {error_message}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Stack trace:", exc_info=True)
        return jsonify({'success': False, 'message': f"Error in test_metadata_store: {error_message}"}), 500

# ==============================================================================
# TRACKING ROUTES MOVED TO DEDICATED BLUEPRINT
# ==============================================================================
# All tracking functionality has been moved to flask_app/routes/tracking_routes.py
# This provides better organization and separation of concerns.
# 
# Available tracking endpoints:
# - POST /api/tracking/track-page-view
# - POST /api/tracking/track-cta-click  
# - GET  /api/tracking/track-email-click
# - GET  /api/tracking/engagement-stats (login required)
# - GET  /api/tracking/conversion-funnel (login required)
# - GET  /api/tracking/test

# --- Patient Manifest Builder ---
def build_patient_manifest(patient_id):
    """Build patient manifest with comprehensive patient details for LLM analysis"""
    logger.info(f"=== BUILD_PATIENT_MANIFEST STARTED for patient_id: {patient_id} ===")
    
    try:
        # Get full patient details including consultations
        patient_details = fetch_patient_details(patient_id)
        
        # Get actual patient data
        patient = patient_details.get('patient')
        if not patient:
            logger.error(f"Patient {patient_id} not found")
            return None, None, None
        
        # Build comprehensive demographics
        demographics = {
            'id': patient.id,
            'name': patient.name,
            'email': patient.email,
            'phone': patient.phone,
            'gender': getattr(patient, 'gender', 'Unknown'),
            'last_visit': patient.last_update.strftime('%Y-%m-%d') if patient.last_update else None,
            'osa_risk_score': getattr(patient, 'osa_risk_score', None),
            'payment_method': getattr(patient, 'payment_method', 'N/A')
        }
        
        # Include uploaded files by category
        uploaded_files = patient_details.get('uploaded_files', {})
        demographics['files'] = {
            category: len(files) for category, files in uploaded_files.items()
        }
        
        # Include scheduled consultations
        scheduled_consultations = patient_details.get('scheduled_consultations', [])
        demographics['consultations'] = scheduled_consultations
        
        # Include patient statuses
        patient_statuses = patient_details.get('patient_statuses', {})
        demographics['statuses'] = {
            status_type: status.status for status_type, status in patient_statuses.items()
        }
        
        # Include comments
        comments = patient_details.get('comments', [])
        demographics['comments'] = comments
        
        # Build manifest stages (simplified for now, but includes all patient data)
        manifest = [
            {
                "stage_number": 1,
                "stage_name": "Patient Information",
                "key": "patient_info",
                "value": "completed",
                "data": demographics
            }
        ]
        
        age = None
        if hasattr(patient, 'date_of_birth') and patient.date_of_birth:
            from datetime import datetime
            age = (datetime.now() - patient.date_of_birth).days // 365
        
        logger.info(f"Comprehensive manifest built successfully with patient details")
        logger.info(f"Patient: {patient.name}, Consultations: {len(scheduled_consultations)}")
        logger.info("=== BUILD_PATIENT_MANIFEST COMPLETED SUCCESSFULLY ===")
        return manifest, demographics, age
        
    except Exception as e:
        logger.error(f"Error building patient manifest: {e}")
        return None, None, None



# CANONICAL ROUTE: This is the main patient_workflow_test route
# The duplicate route in osaagent_routes.py has been removed to prevent confusion
@main.route('/patient_workflow_test/<int:patient_id>', methods=['GET'])
@login_required
def patient_workflow_test(patient_id):
    """Display the modern patient journey interface with timeline and chatbot"""
    try:
        # Get manifest type from query parameter
        manifest_type = request.args.get('manifest', 'normal')
        
        # Get patient information
        patient = Patient.query.get(patient_id)
        if not patient:
            flash('Patient not found', 'error')
            return redirect(url_for('main.patient_list'))
        
        # Get doctor information (you can customize this based on your user system)
        doctor_name = current_user.name if hasattr(current_user, 'name') else "Sosa"
        
        # Build patient manifest
        patient_manifest, demographics, age = build_patient_manifest(patient_id)
        
        # We'll generate file links directly in the route
        enhanced_stages = []
        
        # Debug the patient manifest
        logger.info(f"=== PATIENT MANIFEST DEBUG ===")
        logger.info(f"Patient manifest type: {type(patient_manifest)}")
        logger.info(f"Patient manifest length: {len(patient_manifest) if patient_manifest else None}")
        logger.info(f"Patient manifest content: {patient_manifest}")
        logger.info(f"=== END PATIENT MANIFEST DEBUG ===")
        
        # Get the definition manifest to use the exact stages from your system
        definition_manifest = get_manifest_definition()
        
        # Import the new functions for prerequisites and next steps
        from flask_app.config.manifest_config import get_prerequisites_for_stage, get_next_step_for_stage
        
        # Debug the definition manifest
        logger.info(f"=== DEFINITION MANIFEST DEBUG ===")
        logger.info(f"Definition manifest type: {type(definition_manifest)}")
        logger.info(f"Definition manifest length: {len(definition_manifest) if definition_manifest else None}")
        logger.info(f"Definition manifest content: {definition_manifest}")
        logger.info(f"=== END DEFINITION MANIFEST DEBUG ===")
        
        # Get patients DSO information
        patient_dso_id = None
        if patient.clinic_id:
            from flask_app.models import Clinic
            clinic = Clinic.query.get(patient.clinic_id)
            if clinic:
                patient_dso_id = clinic.dso_id
        
        # Create stages based on the actual manifest definition - only show completed stages
        logger.info(f"Processing {len(definition_manifest)} definition manifest stages")
        logger.info(f"Definition manifest stages: {[s['key'] for s in definition_manifest]}")
        logger.info(f"Patient manifest stages: {[s.get('key') for s in patient_manifest] if patient_manifest else 'None'}")
        
        # Set up S3 client for file URL generation
        try:
            s3_client = boto3.client('s3', region_name='us-west-2')
            bucket = os.getenv('S3_BUCKET_NAME', 'vizbrizpatients')
        except Exception as e:
            logger.error(f"Error setting up S3 client: {e}")
            s3_client = None
            bucket = None
        
        def is_viewable_file(file_type, filename=None):
            """Check if a file type can be viewed inline in a browser"""
            viewable_extensions = {
                'pdf', 'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'tiff', 'tif',  # Images and PDFs
                'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',  # Office documents
                'txt', 'csv', 'html', 'htm', 'xml', 'json',  # Text files
                'dcm', 'dicom'  # Medical imaging files
            }
            
            # Common MIME type mappings
            mime_type_mappings = {
                'application/pdf': 'pdf',
                'image/jpeg': 'jpg',
                'image/jpg': 'jpg', 
                'image/png': 'png',
                'image/gif': 'gif',
                'image/bmp': 'bmp',
                'image/webp': 'webp',
                'image/tiff': 'tiff',
                'image/tif': 'tif',
                'text/plain': 'txt',
                'text/csv': 'csv',
                'text/html': 'html',
                'text/xml': 'xml',
                'application/json': 'json',
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
                'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx',
                'application/msword': 'doc',
                'application/vnd.ms-excel': 'xls',
                'application/vnd.ms-powerpoint': 'ppt'
            }
            
            if not file_type:
                logger.info(f"File type is None or empty")
                # If we have a filename, try to extract extension from it
                if filename:
                    logger.info(f"Trying to extract extension from filename: {filename}")
                    filename_lower = filename.lower()
                    if filename_lower.endswith('.pdf'):
                        logger.info(f"Filename ends with .pdf - marking as viewable")
                        return True
                    elif any(filename_lower.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif']):
                        logger.info(f"Filename ends with image extension - marking as viewable")
                        return True
                    elif any(filename_lower.endswith(ext) for ext in ['.txt', '.csv', '.html', '.htm', '.xml', '.json']):
                        logger.info(f"Filename ends with text extension - marking as viewable")
                        return True
                return False
            
            # Handle different file type formats
            file_type_lower = file_type.lower().strip()
            logger.info(f"Checking if file type '{file_type}' (lowered: '{file_type_lower}') is viewable")
            
            # If it's already just an extension (e.g., "pdf", "jpg")
            if file_type_lower in viewable_extensions:
                logger.info(f"File type '{file_type}' is directly viewable")
                return True
            
            # Special case for PDF files - check if the filename ends with .pdf
            if 'pdf' in file_type_lower:
                logger.info(f"File type '{file_type}' contains 'pdf' - marking as viewable")
                return True
            
            # Special case for common image formats
            if any(img_type in file_type_lower for img_type in ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'tiff', 'tif']):
                logger.info(f"File type '{file_type}' contains image format - marking as viewable")
                return True
            
            # Special case for text formats
            if any(text_type in file_type_lower for text_type in ['txt', 'csv', 'html', 'htm', 'xml', 'json']):
                logger.info(f"File type '{file_type}' contains text format - marking as viewable")
                return True
            
            # If it has a dot, extract extension (e.g., "document.pdf" -> "pdf")
            if '.' in file_type_lower:
                extension = file_type_lower.split('.')[-1]
                is_viewable = extension in viewable_extensions
                logger.info(f"File type '{file_type}' -> extension '{extension}' -> viewable: {is_viewable}")
                return is_viewable
            
            # If it's a MIME type (e.g., "application/pdf")
            if '/' in file_type_lower:
                # Check if it's a known MIME type
                if file_type_lower in mime_type_mappings:
                    mapped_extension = mime_type_mappings[file_type_lower]
                    is_viewable = mapped_extension in viewable_extensions
                    logger.info(f"File type '{file_type}' -> MIME mapped to '{mapped_extension}' -> viewable: {is_viewable}")
                    return is_viewable
                else:
                    # Try to extract the subtype
                    mime_subtype = file_type_lower.split('/')[-1]
                    is_viewable = mime_subtype in viewable_extensions
                    logger.info(f"File type '{file_type}' -> MIME subtype '{mime_subtype}' -> viewable: {is_viewable}")
                    return is_viewable
            
            # If it's something else, try to match the whole string
            is_viewable = file_type_lower in viewable_extensions
            logger.info(f"File type '{file_type}' -> viewable: {is_viewable}")
            return is_viewable
        
        def generate_file_url(s3_key, file_type):
            """Generate S3 presigned URL for file access"""
            if not s3_key or not s3_client:
                return None
            
            try:
                is_viewable = is_viewable_file(file_type)
                mode = 'view' if is_viewable else 'download'
                
                params = {'Bucket': bucket, 'Key': s3_key}
                if mode == 'view':
                    params['ResponseContentDisposition'] = 'inline'
                
                url = s3_client.generate_presigned_url('get_object', Params=params, ExpiresIn=3600)
                return url
            except Exception as e:
                logger.error(f"Error generating URL for {s3_key}: {e}")
                return None
        
        def get_files_for_stage_simple(patient_id, stage_key):
            """Get files for a stage by directly querying files and adminfiles tables"""
            try:
                files = []
                
                # Map stage keys to file queries
                if stage_key == "quiz_completion":
                    # Get questionnaire files
                    results = db.session.execute(text("""
                        SELECT id, name, upload_date, file_type, s3_key, category, subcategory
                        FROM files 
                        WHERE patient_id = :patient_id AND category = 'medical' AND subcategory = 'questionnaire'
                        ORDER BY upload_date DESC
                    """), {'patient_id': patient_id}).fetchall()
                    
                    for result in results:
                        download_url = generate_file_url(result.s3_key, result.file_type)
                        is_viewable = is_viewable_file(result.file_type, result.name)
                        logger.info(f"Quiz file: {result.name} (type: '{result.file_type}') -> viewable: {is_viewable}")
                        logger.info(f"  S3 Key: {result.s3_key}")
                        logger.info(f"  Download URL: {download_url}")
                        
                        files.append({
                            'id': result.id,
                            'type': 'file',
                            'name': result.name,
                            'date': result.upload_date,
                            'description': 'Patient Questionnaire',
                            'file_type': result.file_type,
                            's3_key': result.s3_key,
                            'download_url': download_url,
                            'is_viewable': is_viewable
                        })
                
                elif stage_key == "sleep_test_completed":
                    # Get sleep test files
                    results = db.session.execute(text("""
                        SELECT id, name, upload_date, file_type, s3_key, category, subcategory
                        FROM files 
                        WHERE patient_id = :patient_id AND LOWER(subcategory) = LOWER('sleep-test')
                        ORDER BY upload_date DESC
                    """), {'patient_id': patient_id}).fetchall()
                    
                    for result in results:
                        download_url = generate_file_url(result.s3_key, result.file_type)
                        is_viewable = is_viewable_file(result.file_type, result.name)
                        logger.info(f"Sleep test file: {result.name} (type: '{result.file_type}') -> viewable: {is_viewable}")
                        logger.info(f"  S3 Key: {result.s3_key}")
                        logger.info(f"  Download URL: {download_url}")
                        
                        files.append({
                            'id': result.id,
                            'type': 'file',
                            'name': result.name,
                            'date': result.upload_date,
                            'description': f"Sleep test file - {result.file_type}",
                            'file_type': result.file_type,
                            's3_key': result.s3_key,
                            'download_url': download_url,
                            'is_viewable': is_viewable
                        })
                
                elif stage_key == "cbct_observation_report_uploaded":
                    # Get CBCT observation files
                    results = db.session.execute(text("""
                        SELECT id, name, upload_date, file_type, s3_key, file_category
                        FROM adminfiles 
                        WHERE patient_id = :patient_id AND LOWER(file_category) LIKE LOWER('%cbct observations%')
                        ORDER BY upload_date DESC
                    """), {'patient_id': patient_id}).fetchall()
                    
                    for result in results:
                        download_url = generate_file_url(result.s3_key, result.file_type)
                        is_viewable = is_viewable_file(result.file_type, result.name)
                        logger.info(f"CBCT file: {result.name} (type: '{result.file_type}') -> viewable: {is_viewable}")
                        logger.info(f"  S3 Key: {result.s3_key}")
                        logger.info(f"  Download URL: {download_url}")
                        
                        files.append({
                            'id': result.id,
                            'type': 'adminfile',
                            'name': result.name,
                            'date': result.upload_date,
                            'description': f"CBCT observation report - {result.file_type}",
                            'file_type': result.file_type,
                            's3_key': result.s3_key,
                            'download_url': download_url,
                            'is_viewable': is_viewable
                        })
                
                elif stage_key == "intraoral_scan_uploaded":
                    # Get intraoral scan files
                    results = db.session.execute(text("""
                        SELECT id, name, upload_date, file_type, s3_key, category, subcategory
                        FROM files 
                        WHERE patient_id = :patient_id AND LOWER(subcategory) = LOWER('intraoral-scan')
                        ORDER BY upload_date DESC
                    """), {'patient_id': patient_id}).fetchall()
                    
                    for result in results:
                        download_url = generate_file_url(result.s3_key, result.file_type)
                        is_viewable = is_viewable_file(result.file_type, result.name)
                        logger.info(f"Intraoral scan file: {result.name} (type: {result.file_type}) -> viewable: {is_viewable}")
                        
                        files.append({
                            'id': result.id,
                            'type': 'file',
                            'name': result.name,
                            'date': result.upload_date,
                            'description': f"Intraoral scan - {result.file_type}",
                            'file_type': result.file_type,
                            's3_key': result.s3_key,
                            'download_url': download_url,
                            'is_viewable': is_viewable
                        })
                
                elif stage_key == "hipaa_consent_signed":
                    # Get HIPAA consent files
                    results = db.session.execute(text("""
                        SELECT id, name, upload_date, file_type, s3_key, category, subcategory
                        FROM files 
                        WHERE patient_id = :patient_id AND LOWER(subcategory) = LOWER('billing') AND (
                            LOWER(name) LIKE '%hipaa%' OR 
                            LOWER(name) LIKE '%consent%' OR
                            LOWER(name) LIKE '%authorization%'
                        )
                        ORDER BY upload_date DESC
                    """), {'patient_id': patient_id}).fetchall()
                    
                    for result in results:
                        download_url = generate_file_url(result.s3_key, result.file_type)
                        is_viewable = is_viewable_file(result.file_type, result.name)
                        logger.info(f"HIPAA file: {result.name} (type: {result.file_type}) -> viewable: {is_viewable}")
                        
                        files.append({
                            'id': result.id,
                            'type': 'file',
                            'name': result.name,
                            'date': result.upload_date,
                            'description': f"HIPAA consent form - {result.file_type}",
                            'file_type': result.file_type,
                            's3_key': result.s3_key,
                            'download_url': download_url,
                            'is_viewable': is_viewable
                        })
                
                elif stage_key == "osa_report_ready":
                    # Get OSA report files
                    results = db.session.execute(text("""
                        SELECT id, name, upload_date, file_type, s3_key, file_category
                        FROM adminfiles 
                        WHERE patient_id = :patient_id AND LOWER(file_category) LIKE LOWER('%patient report%') AND is_public = 1
                        ORDER BY upload_date DESC
                    """), {'patient_id': patient_id}).fetchall()
                    
                    for result in results:
                        download_url = generate_file_url(result.s3_key, result.file_type)
                        is_viewable = is_viewable_file(result.file_type, result.name)
                        logger.info(f"OSA report file: {result.name} (type: {result.file_type}) -> viewable: {is_viewable}")
                        
                        files.append({
                            'id': result.id,
                            'type': 'adminfile',
                            'name': result.name,
                            'date': result.upload_date,
                            'description': f"OSA patient report - {result.file_type}",
                            'file_type': result.file_type,
                            's3_key': result.s3_key,
                            'download_url': download_url,
                            'is_viewable': is_viewable
                        })
                
                elif stage_key == "follow_up_sleep_test_after_delivery":
                    # Get follow-up sleep test files
                    results = db.session.execute(text("""
                        SELECT id, name, upload_date, file_type, s3_key, category, subcategory
                        FROM files 
                        WHERE patient_id = :patient_id AND LOWER(subcategory) = LOWER('sleep-test')
                        ORDER BY upload_date DESC
                    """), {'patient_id': patient_id}).fetchall()
                    
                    for result in results:
                        download_url = generate_file_url(result.s3_key, result.file_type)
                        is_viewable = is_viewable_file(result.file_type, result.name)
                        logger.info(f"Follow-up sleep test file: {result.name} (type: {result.file_type}) -> viewable: {is_viewable}")
                        
                        files.append({
                            'type': 'file',
                            'name': result.name,
                            'date': result.upload_date,
                            'description': f"Follow-up sleep test - {result.file_type}",
                            'file_type': result.file_type,
                            's3_key': result.s3_key,
                            'download_url': download_url,
                            'is_viewable': is_viewable
                        })
                
                logger.info(f"Found {len(files)} files for stage {stage_key}")
                return files
                
            except Exception as e:
                logger.error(f"Error getting files for stage {stage_key}: {e}")
                return []
        
        stages = []
        for stage_def in definition_manifest:
            stage_key = stage_def['key']
            stage_name = stage_def['stage_name']
            logger.info(f"Processing stage: {stage_key} - {stage_name}")
            # Find the corresponding patient manifest stage
            patient_stage = next((s for s in patient_manifest if s.get('key') == stage_key), None)
            logger.info(f"Found patient stage for {stage_key}: {patient_stage}")
            
            # Include all stages, not just completed ones
            if patient_stage:
                if patient_stage.get('value') == 'yes':
                    status = "completed"
                    # Get actual completion date from the database
                    completion_date = get_stage_completion_date(patient_id, stage_key)
                elif isinstance(patient_stage.get('value'), dict) and patient_stage.get('value'):
                    status = "completed"
                    # Get actual completion date from the database
                    completion_date = get_stage_completion_date(patient_id, stage_key)
                else:
                    status = "pending"
                    completion_date = None
            else:
                status = "pending"
                completion_date = None
            
            # Get files for this stage - simple lookup using stage_file_links table
            files = get_files_for_stage_simple(patient_id, stage_key)
            logger.info(f"Found {len(files)} files for stage {stage_key}")
            
            # Create descriptive content based on the stage - use manifest as source of truth
            stage_descriptions = {
                "quiz_completion": {
                    "subtitle": "Sleep apnea screening questionnaire completed",
                    "description": "Patient has completed the initial OSA screening questionnaire to assess sleep apnea risk factors and symptoms.",
                    "quiz_data": patient_stage.get('value') if patient_stage else None
                },
                "initial_consult_scheduled": {
                    "subtitle": "First consultation with sleep expert scheduled",
                    "description": "Initial consultation has been scheduled with a sleep medicine expert to discuss symptoms and treatment options."
                },
                "met_with_sleep_expert": {
                    "subtitle": "Completed consultation with sleep expert",
                    "description": "Patient has completed the initial consultation with the sleep medicine expert. Symptoms reviewed and preliminary assessment completed."
                },
                "sleep_doctor_consult_scheduled": {
                    "subtitle": "Sleep specialist consultation scheduled",
                    "description": "Consultation with sleep specialist (ENT or pulmonologist) has been scheduled for comprehensive sleep evaluation."
                },
                "sleep_test_completed": {
                    "subtitle": "Sleep study or home sleep test completed",
                    "description": "Patient has completed either an in-lab sleep study or home sleep test to diagnose OSA severity."
                },
                "sleep_doctor_followup_completed": {
                    "subtitle": "Sleep specialist follow-up completed",
                    "description": "Follow-up consultation with sleep specialist completed. OSA diagnosis confirmed and treatment recommendations provided."
                },
                "dental_sleep_doctor_consult_scheduled": {
                    "subtitle": "Dental sleep specialist consultation scheduled",
                    "description": "Consultation with dental sleep specialist scheduled to discuss oral appliance therapy options."
                },
                "hipaa_consent_signed": {
                    "subtitle": "HIPAA consent and treatment authorization signed",
                    "description": "Patient has signed HIPAA consent forms and treatment authorization for oral appliance therapy."
                },
                "met_with_dental_sleep_expert": {
                    "subtitle": "Completed consultation with dental sleep expert",
                    "description": "Patient has completed consultation with dental sleep specialist. Treatment plan discussed and oral appliance therapy recommended."
                },
                "clinical_data_available": {
                    "subtitle": "CBCT scans and clinical imaging completed",
                    "description": "Cone beam CT scans and clinical imaging have been completed for airway analysis and treatment planning."
                },
                "osa_report_available": {
                    "subtitle": "OSA diagnosis report and treatment plan available",
                    "description": "Comprehensive OSA diagnosis report and treatment plan have been completed and are available for review."
                },
                "appliance_ordered": {
                    "subtitle": "Oral appliance ordered from laboratory",
                    "description": "Custom oral appliance has been ordered from the dental laboratory based on treatment plan specifications."
                },
                "appliance_delivery": {
                    "subtitle": "Oral appliance delivered to clinic",
                    "description": "Custom oral appliance has been delivered to the dental clinic and is ready for fitting."
                },
                "appliance_delivery_and_fitting": {
                    "subtitle": "Oral appliance fitted and adjusted",
                    "description": "Oral appliance has been fitted to the patient and initial adjustments completed for optimal comfort and effectiveness."
                },
                "followup_meeting": {
                    "subtitle": "Post-treatment follow-up completed",
                    "description": "Follow-up appointment completed to assess treatment effectiveness and make any necessary adjustments."
                }
            }
            
            # Get stage description or use default
            stage_info = stage_descriptions.get(stage_key, {
                "subtitle": f"{stage_name} stage",
                "description": f"Patient has reached the {stage_name} stage in their OSA treatment journey."
            })
            
            # Get next step and prerequisites from manifest configuration
            next_step = get_next_step_for_stage(stage_key)
            prerequisites = get_prerequisites_for_stage(stage_key)
            
            # Debug next step calculation
            logger.info(f"Stage {stage_key}: stage_number={stage_def['stage_number']}, next_step={next_step}")
            
            # Use actual completion date from database
            if completion_date:
                stage_date = completion_date.strftime("%B %d, %Y")
            else:
                # Fallback to calculated date if no actual date found
                from datetime import datetime, timedelta
                base_date = datetime(2023, 1, 15)
                fallback_date = base_date + timedelta(days=(stage_def['stage_number'] - 1) * 7)
                stage_date = fallback_date.strftime("%B %d, %Y")
            
            stages.append({
                "key": stage_key,
                "name": stage_name,
                "date": stage_date,
                "status": status,
                "subtitle": stage_info["subtitle"],
                "description": stage_info["description"],
                "next_step": next_step,
                "prerequisites": prerequisites,
                "quiz_data": stage_info.get("quiz_data"),
                "quiz_link": f"/quiz?dso_id={patient_dso_id}" if patient_dso_id else "/quiz",
                "files": files
            })
            
            # Special debug for quiz_completion stage
            if stage_key == 'quiz_completion':
                logger.info(f"=== QUIZ COMPLETION DEBUG ===")
                logger.info(f"Stage key: {stage_key}")
                logger.info(f"Stage number: {stage_def['stage_number']}")
                logger.info(f"Next step calculated: {next_step}")
                logger.info(f"Status: {status}")
                logger.info(f"Patient stage value: {patient_stage.get('value') if patient_stage else 'None'}")
                logger.info(f"=== END QUIZ COMPLETION DEBUG ===")
        
        # Calculate progress using enhanced stages data
        if enhanced_stages:
            completed_stages = len([s for s in enhanced_stages if s.get('is_completed', False)])
            total_stages = len(enhanced_stages)
            progress_percentage = round((completed_stages / total_stages) * 100)
        else:
            # Fallback to original calculation
            completed_stages = len([s for s in stages if s['status'] == 'completed'])
            total_stages = len(stages)
            progress_percentage = round((completed_stages / total_stages) * 100)
        
        # Find current stage - for display purposes, show the first stage that needs attention
        # This could be the first pending stage, or if all are completed, the last stage
        current_stage = None
        
        # First, try to find the first pending stage
        for stage in stages:
            if stage['status'] == 'pending':
                current_stage = stage
                break
        
        # If no pending stages found, use the last stage
        if not current_stage:
            current_stage = stages[-1]
        
        # For debugging: let's also show what the next step should be for each stage
        logger.info(f"Patient manifest: {patient_manifest}")
        logger.info(f"Current stage selected: {current_stage['key']} - {current_stage.get('stage_name', 'Unknown')}")
        logger.info(f"Current stage next_step: {current_stage.get('next_step', 'Unknown')}")
        logger.info(f"All stages and their next_steps:")
        for stage in stages:
            logger.info(f"  {stage['key']}: {stage.get('stage_name', 'Unknown')} -> {stage.get('next_step', 'Unknown')} (status: {stage.get('status', 'Unknown')})")
        
        # Inside patient_workflow_test, after building definition_manifest and patient_manifest ...
        s3_client = get_s3_client()  # Uses us-west-2 from env
        bucket = os.getenv('S3_BUCKET_NAME')

        # Define S3 keys
        manifest_config_key = f'patients/manifest/manifest_config.json'
        patient_manifest_key = f'patients/manifest/patient_{patient_id}_manifest.json'
        clinical_manifest_key = f'patients/manifest/patient_{patient_id}_clinical_manifest.json'

        # Upload manifest config
        s3_client.put_object(
            Bucket=bucket,
            Key=manifest_config_key,
            Body=json.dumps(definition_manifest),
            ContentType='application/json'
        )
        # Upload patient manifest
        s3_client.put_object(
            Bucket=bucket,
            Key=patient_manifest_key,
            Body=json.dumps(patient_manifest),
            ContentType='application/json'
        )
        
        # Load and upload clinical manifest (document observations)
        document_observations = load_document_observations(patient_id)
        logger.info(f"Loaded {sum(len(obs) for obs in document_observations.values())} document observations")
        
        # Generate AI workflow recommendations
        # Pass the stage key instead of the full stage object
        current_stage_key = current_stage['key'] if current_stage else None
        ai_recommendations = get_ai_workflow_recommendations(patient_id, patient_manifest, definition_manifest, current_stage_key)
        
        # Get action manifest data from S3
        try:
            from flask_app.config.action_manifest import get_action_manifest_from_s3, get_actions_for_stage
            action_manifest_data = get_action_manifest_from_s3()
            
            # Organize actions by stage for template
            action_manifest_by_stage = {}
            if action_manifest_data and 'actions' in action_manifest_data:
                for action_key, action_config in action_manifest_data['actions'].items():
                    for stage in action_config.get('stages', []):
                        if stage not in action_manifest_by_stage:
                            action_manifest_by_stage[stage] = []
                        action_manifest_by_stage[stage].append({
                            'action_key': action_key,
                            'description': action_config['description'],
                            'category': action_config['category'],
                            'ai_guidance': action_config.get('ai_guidance', ''),
                            'parameters': action_config.get('parameters', []),
                            'validation_rule': action_config.get('validation_rule', ''),
                            'method': action_config.get('method', ''),
                            'endpoint': action_config.get('endpoint', ''),
                            'default_message': action_config.get('default_message', '')
                        })
            
            # Add action manifest data to AI recommendations context
            if action_manifest_data and 'actions' in action_manifest_data:
                # Get available actions for current stage
                available_actions = get_actions_for_stage(current_stage_key) if current_stage_key else {}
                
                # Add action manifest context to AI recommendations
                if available_actions:
                    # Create a suggested action based on available actions
                    first_action_key = list(available_actions.keys())[0]
                    first_action = available_actions[first_action_key]
                    
                    # Add a suggested action to the first recommendation if it exists
                    if ai_recommendations:
                        ai_recommendations[0]['suggested_action'] = {
                            'action_key': first_action_key,
                            'reasoning': f"Based on current stage '{current_stage_key}', the most appropriate action is {first_action_key}",
                            'parameters': first_action.get('parameters', [])
                        }
                        ai_recommendations[0]['available_actions'] = list(available_actions.keys())
        except Exception as e:
            logger.error(f"Error loading action manifest: {e}")
            action_manifest_by_stage = {}
        
        # Upload clinical manifest
        s3_client.put_object(
            Bucket=bucket,
            Key=clinical_manifest_key,
            Body=json.dumps(document_observations),
            ContentType='application/json'
        )
        
        # Generate pre-signed URLs for all manifests
        manifest_config_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': manifest_config_key},
            ExpiresIn=3600
        )
        patient_manifest_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': patient_manifest_key},
            ExpiresIn=3600
        )
        clinical_manifest_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': clinical_manifest_key},
            ExpiresIn=3600
        )
        
        return render_template('patient_journey.html',
                             patient=patient,
                             doctor_name=doctor_name,
                             stages=stages,
                             current_stage=current_stage,
                             completed_stages=completed_stages,
                             total_stages=total_stages,
                             progress_percentage=progress_percentage,
                             patient_manifest=patient_manifest,
                             demographics=demographics,
                             age=age,
                             patient_dso_id=patient_dso_id,
                             definition_manifest=definition_manifest,
                             enhanced_stages=enhanced_stages,
                             manifest_config_url=manifest_config_url,
                             patient_manifest_url=patient_manifest_url,
                             clinical_manifest_url=clinical_manifest_url,
                             manifest_type=manifest_type,
                             document_observations=document_observations,
                             ai_recommendations=ai_recommendations,
                             action_manifest_by_stage=action_manifest_by_stage
        )
                             
    except Exception as e:
        logger.error(f"Error in patient workflow test: {e}")
        flash(f'Error loading patient journey: {str(e)}', 'error')
        return redirect(url_for('main.patient_list'))

@main.route('/patient_quiz_details/<int:patient_id>/<int:quiz_id>')
@login_required
def patient_quiz_details(patient_id, quiz_id):
    """Show the quiz form with patient's answers filled out, using basic quiz structure if applicable"""
    try:
        from flask_app.models import Clinic
        from flask_app.routes.conversion_quiz_agent import ConversionQuiz
        # Get the quiz submission with DSO access control
        query = db.session.query(
            ConversionQuiz,
            Patient,
            Clinic
        ).join(
            Patient, ConversionQuiz.user_id == Patient.id
        ).outerjoin(
            Clinic, ConversionQuiz.clinic_id == Clinic.id
        ).filter(
            ConversionQuiz.id == quiz_id,
            ConversionQuiz.user_id == patient_id
        )
        # Apply DSO-based access control
        if current_user.role != 'admin':
            user_dso_ids = current_user.get_dso_ids()
            if user_dso_ids:
                query = query.filter(Clinic.dso_id.in_(user_dso_ids))
            else:
                query = query.filter(False)
        submission_data = query.first_or_404()
        quiz_answers = json.loads(submission_data[0].quiz_input)
        # If it's a basic quiz, order and label the answers accordingly
        if submission_data[0].quiz_type == 'basic_quiz':
            basic_quiz_questions = [
                ('full_name', 'Full Name'),
                ('patient_email', 'Email Address'),
                ('phone', 'Phone Number'),
                ('dob', 'Date of Birth'),
                ('gender', 'Gender'),
                ('snoring', 'Do you snore loudly at night?'),
                ('tiredness', 'Do you often wake up feeling tired or unrested?'),
                ('observed_apnea', 'Has anyone observed you stop breathing while you sleep — or have you ever woken up gasping for air or choking?'),
                ('daytime_sleepiness', 'Have you ever unintentionally fallen asleep during the day or in the afternoon?'),
                ('driving_fatigue', 'Do you have trouble staying awake while driving or watching TV or other activities requiring attention?'),
                ('bruxism', 'Do you grind your teeth at night, have signs of worn teeth, or has anyone told you that you suffer from bruxism?'),
                ('weight', 'Do you consider yourself overweight or have you been told your BMI is above normal (BMI ≥ 25)?'),
                ('diagnosed', 'Have you ever been diagnosed with sleep apnea?'),
                ('using_treatment', 'Are you currently using treatment for sleep apnea?'),
                ('treatment_details', 'Treatment Details'),
            ]
            ordered_answers = [(label, quiz_answers.get(key, '')) for key, label in basic_quiz_questions]
        else:
            # fallback: show all answers as-is
            ordered_answers = [(k.replace('_', ' ').title(), v) for k, v in quiz_answers.items()]
        return render_template('patient_quiz_details.html', 
                             submission=submission_data, 
                             quiz_answers=ordered_answers,
                             patient_id=patient_id)
    except Exception as e:
        logger.error(f"Error loading quiz details: {e}")
        flash(f'Error loading quiz details: {str(e)}', 'error')
        return redirect(url_for('main.patient_workflow_test', patient_id=patient_id))

@main.route('/patient_quiz_pdf/<int:patient_id>/<int:quiz_id>')
@login_required
def patient_quiz_pdf(patient_id, quiz_id):
    """Generate PDF of the quiz submission, using basic quiz structure if applicable"""
    try:
        from flask_app.models import Clinic
        from flask_app.routes.conversion_quiz_agent import ConversionQuiz
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors
        import io
        # Get the quiz submission with DSO access control
        query = db.session.query(
            ConversionQuiz,
            Patient,
            Clinic
        ).join(
            Patient, ConversionQuiz.user_id == Patient.id
        ).outerjoin(
            Clinic, ConversionQuiz.clinic_id == Clinic.id
        ).filter(
            ConversionQuiz.id == quiz_id,
            ConversionQuiz.user_id == patient_id
        )
        # Apply DSO-based access control
        if current_user.role != 'admin':
            user_dso_ids = current_user.get_dso_ids()
            if user_dso_ids:
                query = query.filter(Clinic.dso_id.in_(user_dso_ids))
            else:
                query = query.filter(False)
        submission_data = query.first_or_404()
        quiz_answers = json.loads(submission_data[0].quiz_input)
        # If it's a basic quiz, order and label the answers accordingly
        if submission_data[0].quiz_type == 'basic_quiz':
            basic_quiz_questions = [
                ('full_name', 'Full Name'),
                ('patient_email', 'Email Address'),
                ('phone', 'Phone Number'),
                ('dob', 'Date of Birth'),
                ('gender', 'Gender'),
                ('snoring', 'Do you snore loudly at night?'),
                ('tiredness', 'Do you often wake up feeling tired or unrested?'),
                ('observed_apnea', 'Has anyone observed you stop breathing while you sleep — or have you ever woken up gasping for air or choking?'),
                ('daytime_sleepiness', 'Have you ever unintentionally fallen asleep during the day or in the afternoon?'),
                ('driving_fatigue', 'Do you have trouble staying awake while driving or watching TV or other activities requiring attention?'),
                ('bruxism', 'Do you grind your teeth at night, have signs of worn teeth, or has anyone told you that you suffer from bruxism?'),
                ('weight', 'Do you consider yourself overweight or have you been told your BMI is above normal (BMI ≥ 25)?'),
                ('diagnosed', 'Have you ever been diagnosed with sleep apnea?'),
                ('using_treatment', 'Are you currently using treatment for sleep apnea?'),
                ('treatment_details', 'Treatment Details'),
            ]
            answers_data = [['Question', 'Answer']]
            for key, label in basic_quiz_questions:
                answers_data.append([label, str(quiz_answers.get(key, ''))])
        else:
            answers_data = [['Question', 'Answer']]
            for question, answer in quiz_answers.items():
                question_text = question.replace('_', ' ').title()
                answers_data.append([question_text, str(answer)])
        # ... existing PDF generation code ...
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=16,
            spaceAfter=30,
            alignment=1
        )
        content = []
        content.append(Paragraph("OSA Screening Quiz Results", title_style))
        content.append(Spacer(1, 20))
        content.append(Paragraph("Patient Information", styles['Heading2']))
        patient_info = [
            ['Name:', submission_data[1].name if submission_data[1] else 'N/A'],
            ['Email:', submission_data[0].patient_email],
            ['Quiz Type:', submission_data[0].quiz_type.replace('_', ' ').title()],
            ['Submitted:', submission_data[0].created_at.strftime('%Y-%m-%d %H:%M:%S')],
            ['Clinic:', submission_data[2].name if submission_data[2] else 'N/A']
        ]
        patient_table = Table(patient_info)
        patient_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.grey),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        content.append(patient_table)
        content.append(Spacer(1, 20))
        content.append(Paragraph("Quiz Answers", styles['Heading2']))
        answers_table = Table(answers_data)
        answers_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('WORDWRAP', (0, 0), (-1, -1), True)
        ]))
        content.append(answers_table)
        doc.build(content)
        buffer.seek(0)
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=False,  # Open in browser, not download
            download_name=f'osa_quiz_{patient_id}_{quiz_id}_{datetime.now().strftime("%Y%m%d")}.pdf'
        )
    except Exception as e:
        logger.error(f"Error generating quiz PDF: {e}")
        flash(f'Error generating PDF: {str(e)}', 'error')
        return redirect(url_for('main.patient_workflow_test', patient_id=patient_id))

def format_bedrock_error_for_user(error_message):
    """
    Format Bedrock error messages into user-friendly, shortened versions.
    
    Args:
        error_message (str): The raw error message from Bedrock
        
    Returns:
        str: User-friendly error message
    """
    error_lower = error_message.lower()
    
    # Common Bedrock error patterns and their user-friendly messages
    if "throttlingexception" in error_lower or "too many requests" in error_lower:
        return "⚠️ Dr. Briz is currently busy. Please try again in a moment."
    
    elif "validationexception" in error_lower or "invalid" in error_lower:
        return "⚠️ There was an issue with the request format. Please try rephrasing your question."
    
    elif "accessdeniedexception" in error_lower or "access denied" in error_lower:
        return "⚠️ Dr. Briz is temporarily unavailable due to access issues. Please try again later."
    
    elif "modelnotfoundexception" in error_lower or "model not found" in error_lower:
        return "⚠️ Dr. Briz's AI model is temporarily unavailable. Please try again later."
    
    elif "timeout" in error_lower or "timed out" in error_lower:
        return "⚠️ Dr. Briz took too long to respond. Please try again."
    
    elif "network" in error_lower or "connection" in error_lower:
        return "⚠️ Network connection issue. Please check your internet and try again."
    
    elif "credentials" in error_lower or "authentication" in error_lower:
        return "⚠️ Dr. Briz is temporarily unavailable due to authentication issues."
    
    elif "quota" in error_lower or "limit" in error_lower:
        return "⚠️ Dr. Briz has reached the conversation limit. Please try again later."
    
    elif "internal" in error_lower or "server" in error_lower:
        return "⚠️ Dr. Briz encountered an internal error. Please try again."
    
    else:
        # For unknown errors, provide a generic but helpful message
        return "⚠️ Dr. Briz is temporarily unavailable. Please try again in a moment."

@main.route('/api/bedrock_chat', methods=['POST'])
def bedrock_chat():
    """Chat endpoint for Dr. Briz with comprehensive logging"""
    print("=== BEDROCK CHAT ENDPOINT STARTED ===")
    print(f"Request method: {request.method}")
    print(f"Request URL: {request.url}")
    print(f"Request path: {request.path}")
    print(f"Request headers: {dict(request.headers)}")
    
    try:
        # Parse JSON data
        data = request.get_json()
        print(f"Raw request data: {data}")
        
        if not data:
            print("No JSON data received in request")
            return jsonify({"success": False, "message": "No JSON data received"}), 400
        
        # Extract patient_id, user_message, and workflow_mode
        patient_id = data.get('patient_id')
        user_message = data.get('message')
        workflow_mode = data.get('workflow_mode', False)
        
        print(f"Extracted patient_id: {patient_id}")
        print(f"Extracted user_message: {user_message}")
        print(f"Workflow mode: {workflow_mode}")
        
        # Validate required fields
        if patient_id is None:
            return jsonify({"success": False, "message": "patient_id is required"}), 400
        
        if user_message is None:
            return jsonify({"success": False, "message": "message is required"}), 400
        
        # Build patient manifest and context
        print("Building patient manifest...")
        patient_manifest, demographics, age = build_patient_manifest(patient_id)
        definition_manifest = get_manifest_definition()
        
        # Extract patient name
        patient_name = demographics.get('name', 'Unknown') if demographics else 'Unknown'
        print(f"Patient name: {patient_name}")
        
        # Generate S3 URLs for manifests instead of sending full content
        print("Generating S3 URLs for manifests...")
        s3_client = get_s3_client()
        bucket = os.getenv('S3_BUCKET_NAME')
        
        # Define S3 keys
        manifest_config_key = f'patients/manifest/manifest_config.json'
        patient_manifest_key = f'patients/manifest/patient_{patient_id}_manifest.json'
        clinical_manifest_key = f'patients/manifest/patient_{patient_id}_clinical_manifest.json'
        
        # Upload manifests to S3
        s3_client.put_object(
            Bucket=bucket,
            Key=manifest_config_key,
            Body=json.dumps(definition_manifest),
            ContentType='application/json'
        )
        s3_client.put_object(
            Bucket=bucket,
            Key=patient_manifest_key,
            Body=json.dumps(patient_manifest),
            ContentType='application/json'
        )
        
        # Load and upload clinical manifest
        document_observations = load_document_observations(patient_id)
        print(f"Found {sum(len(obs) for obs in document_observations.values())} document observations")
        
        s3_client.put_object(
            Bucket=bucket,
            Key=clinical_manifest_key,
            Body=json.dumps(document_observations),
            ContentType='application/json'
        )
        
        # Generate pre-signed URLs
        manifest_config_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': manifest_config_key},
            ExpiresIn=3600
        )
        patient_manifest_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': patient_manifest_key},
            ExpiresIn=3600
        )
        clinical_manifest_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': clinical_manifest_key},
            ExpiresIn=3600
        )
        
        # Build the enhanced LLM prompt with comprehensive medical knowledge
        if workflow_mode:
            # Special prompt for workflow recommendations
            system_prompt = f"""You are Dr. Briz, an expert sleep medicine AI assistant specializing in Obstructive Sleep Apnea (OSA) treatment and dental sleep therapy. 

You are being asked to provide specific, actionable workflow recommendations for a patient. Your response should be a valid JSON array of recommendation objects.

IMPORTANT: Return ONLY valid JSON, no additional text or explanations. The response must be parseable as a JSON array.

Each recommendation object should have this exact structure:
{{
    "type": "recommendation_type",
    "title": "Clear action title", 
    "description": "Detailed description of what needs to be done",
    "action": "specific_action_name",
    "priority": "high|medium|low",
    "icon": "material_icon_name"
}}

Available action types: schedule_consultation, validate_consultation, order_appliance, track_delivery, schedule_delivery, complete_stage, prepare_next_stage

Available icons: schedule, check_circle, shopping_cart, local_shipping, assignment, arrow_forward, error

You have access to patient manifests and clinical observations via S3 URLs. Use this information to provide informed recommendations."""
        else:
            # Regular chat prompt
            system_prompt = f"""You are Dr. Briz, an expert sleep medicine AI assistant specializing in Obstructive Sleep Apnea (OSA) treatment and dental sleep therapy. You have extensive knowledge in:

MEDICAL EXPERTISE:
- Sleep medicine and sleep disorders
- OSA diagnosis, severity assessment, and treatment options
- Dental sleep therapy and oral appliance therapy
- Sleep study interpretation and AHI scoring
- CPAP therapy and alternatives
- Sleep hygiene and lifestyle modifications
- Medical device regulations and insurance considerations

TREATMENT WORKFLOW KNOWLEDGE:
- OSA screening and risk assessment
- Sleep test types (home sleep tests vs. in-lab polysomnography)
- Consultation scheduling and patient education
- Treatment planning and device selection
- Follow-up protocols and titration
- Compliance monitoring and outcome assessment
- Referral coordination between dental and medical providers

CLINICAL GUIDELINES:
- AASM (American Academy of Sleep Medicine) guidelines
- ADA (American Dental Association) sleep medicine standards
- Insurance coverage requirements for OSA treatment
- HIPAA compliance and patient privacy
- Medical device safety and efficacy standards

PATIENT CARE APPROACH:
- Patient education and counseling
- Treatment adherence strategies
- Side effect management and troubleshooting
- Long-term follow-up and maintenance
- Emergency protocols and when to refer to specialists

DOCUMENT ANALYSIS CAPABILITIES:
- You have access to clinical observations extracted from patient documents via S3 URLs
- These observations include findings from sleep tests, medical reports, and other clinical documents
- Use these observations to provide more informed and personalized responses
- Consider both workflow status and clinical findings when making recommendations

READ THE MANIFEST DATA FROM THE PROVIDED S3 URLs before responding

RECOMMENDATION STYLE:
- When making recommendations based on clinical data, present them as YOUR professional assessment
- Be confident and authoritative in your recommendations
- Don't reference external sources or say \"someone else recommended\" - make it sound like YOUR recommendation
- Use phrases like \"I recommend\", \"Based on my analysis\", \"I suggest\", \"My recommendation is\"
- When discussing specific devices or treatments found in documents, present them as YOUR professional choice
- Be decisive and avoid hedging language like \"likely\" or \"should be confirmed\"

RESPONSE STYLE:
- Keep responses concise and direct (2-4 sentences maximum)
- Focus on practical, actionable information
- Be warm and professional but avoid lengthy medical disclaimers
- Provide specific, relevant answers without unnecessary warnings
- Use bullet points for multiple items when helpful
- Avoid repetitive phrases like \"However, I must emphasize\" or \"preliminary recommendations\"
- Be confident and authoritative in your tone

You provide evidence-based, professional guidance while being warm and supportive. You can answer questions about OSA treatment beyond just the patient's current workflow stage, drawing on your comprehensive medical knowledge and document-based clinical observations. When you make recommendations based on clinical data, present them as your own professional assessment, not as references to external sources."""

        user_prompt = f"""
PATIENT INFORMATION:
Name: {patient_name}
ID: {patient_id}

MANIFEST DATA (Available via S3 URLs):
- Treatment Workflow Stages: {manifest_config_url}
- Patient Current Status: {patient_manifest_url}
- Clinical Observations: {clinical_manifest_url}

USER QUESTION: {user_message}

Please read the manifest data from the provided S3 URLs and provide a concise, direct response as Dr. Briz (2-4 sentences maximum). Focus on:
1. Direct answer to the specific question
2. Practical, actionable information
3. Relevant medical insights
4. Next steps if applicable

IMPORTANT: When making recommendations based on clinical data, present them as YOUR professional assessment. Be confident and authoritative. Use phrases like \"I recommend\", \"Based on my analysis\", \"I suggest\", \"My recommendation is\". Don't reference external sources or say \"someone else recommended\" - make it sound like YOUR recommendation.

Consider both the treatment workflow status and any clinical observations from patient documents when providing your response.

Keep it brief, professional, and helpful without lengthy disclaimers.
"""
        
        print(f"System prompt length: {len(system_prompt)} characters")
        print(f"User prompt length: {len(user_prompt)} characters")
        
        # Import and use Bedrock integration
        try:
            from flask_app.routes.osaagent_routes import query_bedrock_claude_enhanced
            print("Calling Bedrock Claude...")
            
            # Create enhanced messages for Bedrock
            # Bedrock uses "user" and "assistant" roles, not "system"
            bedrock_messages = [
                {
                    "role": "assistant",
                    "content": system_prompt
                },
                {
                    "role": "user", 
                    "content": user_prompt
                }
            ]
            
            # Call Bedrock with enhanced context
            print("=== CALLING BEDROCK ENHANCED ===")
            print(f"Messages being sent: {json.dumps(bedrock_messages, indent=2)}")
            
            result = query_bedrock_claude_enhanced(
                bedrock_messages,
                max_tokens=800,
                temperature=0.3
            )
            print(f"Bedrock result: {result}")
            print(f"Result type: {type(result)}")
            print(f"Result keys: {result.keys() if isinstance(result, dict) else 'Not a dict'}")
            
            if result["success"]:
                # For Claude 3.5 Sonnet, response is a string
                claude_response = result.get('response', "I'm here to help with your patient's OSA treatment journey.")
                print(f"✅ Bedrock success! Response: {claude_response[:200]}...")
            else:
                # Enhanced error handling with user-friendly messages
                error_message = result.get('message', 'Unknown error')
                print(f"❌ Bedrock failed: {error_message}")
                
                # Format error message for user display
                user_friendly_error = format_bedrock_error_for_user(error_message)
                
                # Return error response to user
                return jsonify({
                    "success": False,
                    "response": user_friendly_error,
                    "patient_id": patient_id,
                    "patient_name": patient_name,
                    "error_type": "bedrock_error"
                })
                
        except Exception as e:
            print(f"❌ Exception calling Bedrock: {str(e)}")
            print(f"Exception type: {type(e).__name__}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
            
            # Format exception error for user display
            user_friendly_error = format_bedrock_error_for_user(str(e))
            
            # Return error response to user
            return jsonify({
                "success": False,
                "response": user_friendly_error,
                "patient_id": patient_id,
                "patient_name": patient_name,
                "error_type": "bedrock_exception"
            })
        
        print("=== BEDROCK CHAT ENDPOINT COMPLETED SUCCESSFULLY ===")
        print(f"Returning response: {claude_response}")
        
        response_data = {
            "success": True,
            "response": claude_response,
            "patient_id": patient_id,
            "patient_name": patient_name
        }
        print(f"Full response data: {response_data}")
        
        return jsonify(response_data)
        
    except Exception as e:
        print(f"=== BEDROCK CHAT ENDPOINT ERROR ===")
        print(f"Exception: {str(e)}")
        return jsonify({
            "success": False, 
            "message": f"Internal server error: {str(e)}"
        }), 500

@main.route('/patient_stage/<int:patient_id>/consultation_schedule', methods=['POST'])
@login_required
def schedule_consultation(patient_id):
    """Handle consultation scheduling from AI Workflow"""
    try:
        data = request.get_json()
        consult_type = data.get('consult_type')
        scheduled_date = data.get('scheduled_date')
        scheduled_time = data.get('scheduled_time')
        notes = data.get('notes', '')
        
        if not all([consult_type, scheduled_date, scheduled_time]):
            return jsonify({"success": False, "message": "Missing required fields"}), 400
        
        # Combine date and time
        scheduled_datetime = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")
        
        # Create or update consultation schedule
        existing_schedule = PatientConsultSchedule.query.filter_by(
            patient_id=patient_id,
            consult_type=consult_type
        ).first()
        
        if existing_schedule:
            existing_schedule.scheduled_datetime = scheduled_datetime
            existing_schedule.notes = notes
            existing_schedule.updated_at = datetime.utcnow()
        else:
            new_schedule = PatientConsultSchedule(
                patient_id=patient_id,
                consult_type=consult_type,
                scheduled_datetime=scheduled_datetime,
                notes=notes,
                status='scheduled'
            )
            db.session.add(new_schedule)
        
        db.session.commit()
        return jsonify({"success": True, "message": "Consultation scheduled successfully"})
        
    except Exception as e:
        logger.error(f"Error scheduling consultation: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@main.route('/patient_stage/<int:patient_id>/consultation_validate', methods=['POST'])
@login_required
def validate_consultation(patient_id):
    """Handle consultation validation from AI Workflow"""
    try:
        data = request.get_json()
        consult_type = data.get('consult_type')
        completed_date = data.get('completed_date')
        comment = data.get('comment', '')
        
        if not all([consult_type, completed_date]):
            return jsonify({"success": False, "message": "Missing required fields"}), 400
        
        # Update consultation schedule
        schedule = PatientConsultSchedule.query.filter_by(
            patient_id=patient_id,
            consult_type=consult_type
        ).first()
        
        if schedule:
            schedule.status = 'completed'
            schedule.completed_datetime = datetime.strptime(completed_date, "%Y-%m-%d")
            schedule.comment = comment
            schedule.updated_at = datetime.utcnow()
            db.session.commit()
            
            return jsonify({"success": True, "message": "Consultation validated successfully"})
        else:
            return jsonify({"success": False, "message": "Consultation schedule not found"}), 404
        
    except Exception as e:
        logger.error(f"Error validating consultation: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@main.route('/patient_stage/<int:patient_id>/order_appliance', methods=['POST'])
@login_required
def order_oral_appliance(patient_id):
    """Handle oral appliance ordering from AI Workflow"""
    try:
        data = request.get_json()
        device_name = data.get('device_name', 'Custom Mandibular Advancement Device')
        notes = data.get('notes', 'Oral appliance ordered based on OSA diagnosis and dental approval.')
        
        from flask_app.models import PatientDeviceOrder
        
        # Check if order already exists
        existing_order = PatientDeviceOrder.query.filter_by(
            patient_id=patient_id,
            device_type='oral_appliance'
        ).first()
        
        if existing_order:
            return jsonify({"success": False, "message": "Oral appliance order already exists"}), 400
        
        # Create new order
        new_order = PatientDeviceOrder(
            patient_id=patient_id,
            device_type='oral_appliance',
            device_name=device_name,
            order_date=datetime.utcnow(),
            status='ordered',
            notes=notes
        )
        
        db.session.add(new_order)
        db.session.commit()
        
        return jsonify({"success": True, "message": "Oral appliance ordered successfully"})
        
    except Exception as e:
        logger.error(f"Error ordering oral appliance: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@main.route('/patient_stage/<int:patient_id>/update_device_status', methods=['POST'])
@login_required
def update_device_status(patient_id):
    """Update device delivery status"""
    try:
        data = request.get_json()
        new_status = data.get('status')  # 'shipped', 'delivered', etc.
        arrival_date = data.get('arrival_date')
        notes = data.get('notes', '')
        
        from flask_app.models import PatientDeviceOrder
        
        # Find existing order
        order = PatientDeviceOrder.query.filter_by(
            patient_id=patient_id,
            device_type='oral_appliance'
        ).first()
        
        if not order:
            return jsonify({"success": False, "message": "No oral appliance order found"}), 404
        
        # Update status
        order.status = new_status
        if arrival_date:
            order.arrival_date = datetime.strptime(arrival_date, "%Y-%m-%d")
        if notes:
            order.notes = notes
        order.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({"success": True, "message": f"Device status updated to {new_status}"})
        
    except Exception as e:
        logger.error(f"Error updating device status: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@main.route('/patient_stage/<int:patient_id>/schedule_appliance_delivery', methods=['POST'])
@login_required
def schedule_appliance_delivery(patient_id):
    """Schedule oral appliance delivery appointment"""
    try:
        data = request.get_json()
        scheduled_date = data.get('scheduled_date')
        scheduled_time = data.get('scheduled_time')
        notes = data.get('notes', '')
        
        if not all([scheduled_date, scheduled_time]):
            return jsonify({"success": False, "message": "Missing required fields"}), 400
        
        # Combine date and time
        scheduled_datetime = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")
        
        # Create or update consultation schedule
        existing_schedule = PatientConsultSchedule.query.filter_by(
            patient_id=patient_id,
            consult_type='oral_appliance_delivery'
        ).first()
        
        if existing_schedule:
            existing_schedule.scheduled_datetime = scheduled_datetime
            existing_schedule.notes = notes
            existing_schedule.updated_at = datetime.utcnow()
        else:
            new_schedule = PatientConsultSchedule(
                patient_id=patient_id,
                consult_type='oral_appliance_delivery',
                scheduled_datetime=scheduled_datetime,
                notes=notes,
                status='scheduled'
            )
            db.session.add(new_schedule)
        
        db.session.commit()
        return jsonify({"success": True, "message": "Appliance delivery scheduled successfully"})
        
    except Exception as e:
        logger.error(f"Error scheduling appliance delivery: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

# Removed redundant generic "Complete Current Stage" action
# Each stage should have specific actions defined in the action manifest
# @main.route('/patient_stage/<int:patient_id>/complete_current', methods=['POST'])
# @login_required
# def complete_current_stage(patient_id):
#     """Complete the current stage for a patient"""
#     try:
#         # Get current stage from patient manifest
#         patient_manifest, _, _ = build_patient_manifest(patient_id)
#         definition_manifest = get_manifest_definition()
#         
#         # Find the current active stage
#         current_stage = None
#         for stage in definition_manifest:
#             stage_key = stage['key']
#             patient_stage = next((s for s in patient_manifest if s.get('key') == stage_key), None)
#             if patient_stage and patient_stage.get('value') != 'yes':
#                 current_stage = stage_key
#                 break
#         
#         if not current_stage:
#             return jsonify({"success": False, "message": "No current stage found"}), 404
#         
#         # Update the stage to completed
#         # This would typically update the patient_manifest table
#         # For now, we'll just return success
#         return jsonify({"success": True, "message": f"Stage {current_stage} completed successfully"})
#         
#     except Exception as e:
#         logger.error(f"Error completing current stage: {e}")
#         return jsonify({"success": False, "message": str(e)}), 500

@main.route('/patient_stage/<int:patient_id>/<stage_key>', methods=['GET', 'POST'])
@login_required
def patient_stage_detail(patient_id, stage_key):
    """Detailed view for a specific treatment stage with interactive forms"""
    try:
        # Get patient information
        patient = Patient.query.get(patient_id)
        if not patient:
            flash('Patient not found', 'error')
            return redirect(url_for('main.patient_list'))
        
        # Get the definition manifest to find stage details
        definition_manifest = get_manifest_definition()
        
        # Find the specific stage
        stage_def = next((s for s in definition_manifest if s['key'] == stage_key), None)
        if not stage_def:
            flash('Stage not found', 'error')
            return redirect(url_for('main.patient_workflow_test', patient_id=patient_id))
        
        # Build patient manifest to get current status
        patient_manifest, demographics, age = build_patient_manifest(patient_id)
        current_stage_data = next((s for s in patient_manifest if s.get('key') == stage_key), None)
        
        # Get stage configuration from centralized manifest
        from flask_app.config.manifest_config import get_stage_config
        stage_config = get_stage_config(stage_key) or {}
        
        # Add patient-specific URL for quiz
        if stage_key == 'quiz_completion' and stage_config.get('form_type') == 'quiz_link':
            stage_config['quiz_url'] = f"/quiz/basic?patient_id={patient_id}"
        
        # Handle form submissions
        if request.method == 'POST':
            form_type = request.form.get('form_type')
            
            if form_type == 'consultation_schedule':
                # Schedule consultation
                consult_type = request.form.get('consult_type')
                scheduled_date = request.form.get('scheduled_date')
                scheduled_time = request.form.get('scheduled_time')
                notes = request.form.get('notes', '')
                
                if scheduled_date and scheduled_time:
                    scheduled_datetime = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")
                    
                    # Create or update consultation schedule
                    existing_schedule = PatientConsultSchedule.query.filter_by(
                        patient_id=patient_id,
                        consult_type=consult_type
                    ).first()
                    
                    if existing_schedule:
                        existing_schedule.scheduled_datetime = scheduled_datetime
                        existing_schedule.notes = notes
                        existing_schedule.updated_at = datetime.utcnow()
                    else:
                        new_schedule = PatientConsultSchedule(
                            patient_id=patient_id,
                            consult_type=consult_type,
                            scheduled_datetime=scheduled_datetime,
                            notes=notes,
                            status='scheduled'
                        )
                        db.session.add(new_schedule)
                    
                    db.session.commit()
                    flash('Consultation scheduled successfully!', 'success')
                    return redirect(url_for('main.patient_stage_detail', patient_id=patient_id, stage_key=stage_key))
                else:
                    flash('Please provide both date and time', 'error')
            
            elif form_type == 'consultation_complete':
                # Mark consultation as completed
                consult_type = request.form.get('consult_type')
                completed_date = request.form.get('completed_date')
                completed_time = request.form.get('completed_time')
                comment = request.form.get('comment', '')
                
                if completed_date and completed_time:
                    completed_datetime = datetime.strptime(f"{completed_date} {completed_time}", "%Y-%m-%d %H:%M")
                    
                    # Update consultation schedule
                    schedule = PatientConsultSchedule.query.filter_by(
                        patient_id=patient_id,
                        consult_type=consult_type
                    ).first()
                    
                    if schedule:
                        schedule.status = 'completed'
                        schedule.completed_datetime = completed_datetime
                        schedule.comment = comment
                        schedule.updated_at = datetime.utcnow()
                        db.session.commit()
                        flash('Consultation marked as completed!', 'success')
                        return redirect(url_for('main.patient_stage_detail', patient_id=patient_id, stage_key=stage_key))
                    else:
                        flash('No scheduled consultation found', 'error')
                else:
                    flash('Please provide completion date and time', 'error')
            
            elif form_type == 'appliance_order':
                # Order appliance
                device_type = request.form.get('device_type')
                device_name = request.form.get('device_name')
                notes = request.form.get('notes', '')
                
                if device_type and device_name:
                    # Create appliance order
                    from datetime import datetime
                    new_order = {
                        'patient_id': patient_id,
                        'device_type': device_type,
                        'device_name': device_name,
                        'order_date': datetime.utcnow(),
                        'notes': notes,
                        'status': 'ordered'
                    }
                    
                    # Execute SQL to insert order
                    from sqlalchemy import text
                    db.session.execute(text("""
                        INSERT INTO patient_device_order 
                        (patient_id, device_type, device_name, order_date, notes, status)
                        VALUES (:patient_id, :device_type, :device_name, :order_date, :notes, :status)
                    """), new_order)
                    db.session.commit()
                    
                    flash('Appliance ordered successfully!', 'success')
                    return redirect(url_for('main.patient_stage_detail', patient_id=patient_id, stage_key=stage_key))
                else:
                    flash('Please provide device type and name', 'error')
        
        # Get existing consultation schedules for this patient
        consultation_schedules = PatientConsultSchedule.query.filter_by(patient_id=patient_id).all()
        
        # Get existing files for this patient
        patient_files = File.query.filter_by(patient_id=patient_id).all()
        admin_files = AdminFile.query.filter_by(patient_id=patient_id).all()
        
        return render_template('patient_stage_detail.html',
                             patient=patient,
                             stage_def=stage_def,
                             stage_config=stage_config,
                             current_stage_data=current_stage_data,
                             prerequisites_met=prerequisites_met,
                             prerequisites_missing=prerequisites_missing,
                             consultation_schedules=consultation_schedules,
                             patient_files=patient_files,
                             admin_files=admin_files,
                             demographics=demographics)
                             
    except Exception as e:
        logger.error(f"Error in patient stage detail: {e}")
        flash(f'Error loading stage details: {str(e)}', 'error')
        return redirect(url_for('main.patient_workflow_test', patient_id=patient_id))

main_routes = Blueprint('main_routes', __name__)

@main_routes.route('/workflow')
@login_required
def workflow():
    # Get the DSO ID for the currently logged-in dentist
    dso_id = None
    if hasattr(current_user, 'get_dso_ids'):
        dso_ids = current_user.get_dso_ids()
        if dso_ids:
            dso_id = dso_ids[0]
    # Fallback if no DSO found
    if not dso_id:
        dso_id = 1
    return render_template('workflow.html', dso_id=dso_id)

@main_routes.route('/qr_code')
@login_required
def qr_code():
    dso_id = None
    if hasattr(current_user, 'get_dso_ids'):
        dso_ids = current_user.get_dso_ids()
        if dso_ids:
            dso_id = dso_ids[0]
    if not dso_id:
        dso_id = 1
    qr_url = f'https://app.vizbriz.com/wizard/stage1_personal_info?dso_id={dso_id}'
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

def load_document_observations(patient_id):
    """
    Load document-based observations from the observation_store table for a patient.
    These observations were extracted from patient documents using LLM analysis.
    
    Args:
        patient_id (int): Patient ID
        
    Returns:
        dict: Dictionary containing observations organized by source type
    """
    try:
        conn = mysql.connector.connect(
            host='vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com',
            user='admin',
            password='Vizbriz2025!',
            database='vizbriz',
            port=3306
        )
        cursor = conn.cursor(dictionary=True)
        
        # Query for all observations for this patient
        query = """
            SELECT source_type, source_text, extracted_observations, created_at
            FROM observation_store 
            WHERE patient_id = %s 
            ORDER BY created_at DESC
        """
        cursor.execute(query, (patient_id,))
        observations = cursor.fetchall()
        
        # Organize observations by source type
        organized_observations = {}
        
        for obs in observations:
            source_type = obs['source_type']
            if source_type not in organized_observations:
                organized_observations[source_type] = []
            
            # Parse the JSON observations
            try:
                obs_data = json.loads(obs['extracted_observations']) if obs['extracted_observations'] else {}
                organized_observations[source_type].append({
                    'observation': obs_data.get('observation', 'Unknown'),
                    'value': obs_data.get('value', ''),
                    'evidence': obs_data.get('evidence', ''),
                    'confidence': obs_data.get('confidence', 0),
                    'document_name': obs_data.get('document_name', ''),
                    'document_type': obs_data.get('document_type', ''),
                    'extraction_date': obs_data.get('extraction_date', ''),
                    'created_at': obs['created_at'].isoformat() if obs['created_at'] else None
                })
            except json.JSONDecodeError:
                # If JSON parsing fails, create a simple observation
                organized_observations[source_type].append({
                    'observation': 'Document Analysis',
                    'value': 'Extracted',
                    'evidence': obs['source_text'] or 'Document content analysis',
                    'confidence': 100,
                    'document_name': 'Unknown',
                    'document_type': source_type,
                    'extraction_date': obs['created_at'].isoformat() if obs['created_at'] else None,
                    'created_at': obs['created_at'].isoformat() if obs['created_at'] else None
                })
        
        cursor.close()
        conn.close()
        
        return organized_observations
        
    except Exception as e:
        logger.error(f"Error loading document observations for patient {patient_id}: {e}")
        return {}
    
def get_ai_workflow_recommendations(patient_id, patient_manifest, definition_manifest, current_stage):
    """
    Analyze patient's current stage and provide AI-driven recommendations for next actions using Bedrock
    """
    recommendations = []
    
    logger.info(f"=== AI WORKFLOW RECOMMENDATIONS DEBUG ===")
    logger.info(f"Patient ID: {patient_id}")
    logger.info(f"Current stage: {current_stage}")
    logger.info(f"Patient manifest length: {len(patient_manifest) if patient_manifest else 0}")
    logger.info(f"Definition manifest length: {len(definition_manifest) if definition_manifest else 0}")
    
    try:
        # Get current stage info
        current_stage_info = next((stage for stage in definition_manifest if stage['key'] == current_stage), None)
        
        if not current_stage_info:
            logger.warning(f"No stage info found for current_stage: {current_stage}")
            return recommendations
            
        stage_name = current_stage_info.get('stage_name', current_stage)
        stage_key = current_stage_info.get('key')
        
        # Get patient's current status for this stage
        patient_stage_status = next((stage for stage in patient_manifest if stage.get('key') == stage_key), None)
        stage_completed = patient_stage_status and patient_stage_status.get('value') == 'yes'
        
        # Get available actions for this stage from action manifest
        from flask_app.config.action_manifest import get_actions_for_stage
        available_actions = get_actions_for_stage(stage_key) if stage_key else {}
        
        # Create action manifest context for AI
        action_context = ""
        if available_actions:
           action_context = f"\nCURRENT STAGE: {stage_name}\n"
           action_context += "AVAILABLE ACTIONS FOR THIS SPECIFIC STAGE:\n"
           for action_key, action_config in available_actions.items():
               action_context += f"- {action_key}: {action_config.get('description', 'No description')}\n"
           action_context += f"\nCRITICAL: You MUST use ONLY these action_key values for stage '{stage_name}':\n"
           action_context += ", ".join(available_actions.keys())
           action_context += "\n\nDO NOT create new action keys. DO NOT use action keys from other stages."
           action_context += "\n\nEXAMPLE VALID RESPONSE:"
           action_context += f"\n[{{\"type\": \"workflow\", \"title\": \"{list(available_actions.keys())[0].replace('_', ' ').title()}\", \"description\": \"Use the {list(available_actions.keys())[0]} action\", \"action_key\": \"{list(available_actions.keys())[0]}\", \"priority\": \"high\", \"icon\": \"check_circle\"}}]"
        else:
            action_context = f"\nCURRENT STAGE: {stage_name}\n"
            action_context += "NO SPECIFIC ACTIONS AVAILABLE FOR THIS STAGE.\n"
            action_context += "Use logical action names that can be mapped later."
        
        # Use the existing Bedrock chat endpoint to get AI recommendations
        # Create a workflow-specific prompt for the AI
        workflow_prompt = f"""
        You are Dr. Briz, an expert sleep medicine AI assistant. Analyze the patient's current workflow stage and provide specific, actionable recommendations.

        PATIENT CONTEXT:
        - Patient ID: {patient_id}
        - Current Stage: {stage_name} ({stage_key})
        - Stage Completed: {'Yes' if stage_completed else 'No'}
        {action_context}

        TASK:
        Based on the patient's current stage and workflow status, provide 2-4 specific, actionable recommendations for next steps. Each recommendation should include:
        1. A clear title
        2. A detailed description of what needs to be done
        3. The specific action_key from the available actions list above (or a logical action if none available)
        4. Priority level (high, medium, low)
        5. An appropriate icon name

        RESPONSE FORMAT:
        Return your response as a JSON array of recommendation objects with this exact structure:
        [
            {{
                "type": "recommendation_type",
                "title": "Clear action title",
                "description": "Detailed description of what needs to be done",
                "action": "specific_action_name",
                "action_key": "exact_action_key_from_manifest",
                "priority": "high|medium|low",
                "icon": "material_icon_name"
            }}
        ]

        CRITICAL INSTRUCTIONS: 
        - Focus ONLY on the current stage ({stage_name})
        - Use ONLY the action_key values provided for this specific stage
        - Do NOT create new action keys or use action keys from other stages
        - Do NOT use generic action names like "schedule_next_appointment", "update_treatment_plan", "remind_document_upload", or "send_reminder"
        - Do NOT create custom action titles - use ONLY the exact action_key values from the manifest
        - Each recommendation MUST use a different action_key from the available list
        - The action_key MUST match exactly one of the keys listed above
        - Do NOT create generic recommendations like "Document Followup Outcomes", "Prepare for Next Phase", or "Send Reminder"
        - ONLY use actions that are explicitly defined in the action manifest for this specific stage
        - If only one action is available, return only that one action
        - Do NOT suggest generic reminder actions unless they are specifically defined for this stage
        - Only return valid JSON, no additional text or explanations
        """
        
        # Call the Bedrock integration directly instead of making HTTP request
        import json
        
        try:
            # Import the Bedrock integration
            from flask_app.routes.osaagent_routes import query_bedrock_claude_enhanced
            
            # Create messages for Bedrock
            bedrock_messages = [
                {
                    "role": "assistant",
                    "content": """You are Dr. Briz, an expert sleep medicine AI assistant. 

You are being asked to provide specific, actionable workflow recommendations for a patient. Your response should be a valid JSON array of recommendation objects.

IMPORTANT: Return ONLY valid JSON, no additional text or explanations. The response must be parseable as a JSON array.

Each recommendation object should have this exact structure:
{
    "type": "recommendation_type",
    "title": "Clear action title", 
    "description": "Detailed description of what needs to be done",
    "action": "specific_action_name",
    "action_key": "exact_action_key_from_manifest",
    "priority": "high|medium|low",
    "icon": "material_icon_name"
}

CRITICAL INSTRUCTIONS: 
- You are providing recommendations for a SPECIFIC STAGE in the patient workflow
- When available actions are provided in the context, you MUST use ONLY the exact action_key values from that list
- Do NOT create new action_key values that are not in the provided list
- Do NOT use generic action names like "schedule_next_appointment", "update_treatment_plan", "remind_document_upload", or "send_reminder"
- Do NOT use action keys from other stages - only use the ones provided for the current stage
- Do NOT suggest generic reminder actions unless they are specifically defined for this stage
- This ensures the recommendations can be properly executed by the system
- If no specific actions are available, use logical action names that can be mapped later

Available icons: schedule, check_circle, shopping_cart, local_shipping, assignment, arrow_forward, error

You have access to patient manifests and clinical observations via S3 URLs. Use this information to provide informed recommendations."""
                },
                {
                    "role": "user",
                    "content": workflow_prompt
                }
            ]
            
            # Call Bedrock directly
            result = query_bedrock_claude_enhanced(
                bedrock_messages,
                max_tokens=800,
                temperature=0.2
            )
            
            if result.get('success'):
                # Parse the AI response to extract recommendations
                ai_response = result.get('response', '')
                logger.info(f"Bedrock response: {ai_response}")
                
                # Try to extract JSON from the AI response
                try:
                    # Debug: Log the raw response
                    logger.info(f"Raw AI response length: {len(ai_response)}")
                    logger.info(f"Raw AI response: {repr(ai_response)}")
                    
                    # First, try to parse the entire response as JSON
                    try:
                        ai_recommendations = json.loads(ai_response.strip())
                        if isinstance(ai_recommendations, list):
                            recommendations.extend(ai_recommendations)
                            logger.info(f"Successfully parsed {len(ai_recommendations)} recommendations from Bedrock")
                        else:
                            logger.warning(f"Response is not a JSON array, type: {type(ai_recommendations)}")
                            raise ValueError("Response is not a JSON array")
                    except (json.JSONDecodeError, ValueError) as e:
                        logger.warning(f"Direct JSON parsing failed: {e}")
                        # If that fails, try to extract JSON array using regex
                        import re
                        json_match = re.search(r'\[.*\]', ai_response, re.DOTALL)
                        if json_match:
                            matched_json = json_match.group()
                            logger.info(f"Found JSON match: {matched_json}")
                            try:
                                ai_recommendations = json.loads(matched_json)
                                recommendations.extend(ai_recommendations)
                                logger.info(f"Successfully parsed {len(ai_recommendations)} recommendations from Bedrock using regex")
                            except json.JSONDecodeError as regex_error:
                                logger.warning(f"Regex JSON parsing also failed: {regex_error}")
                                # Try to fix truncated JSON by completing the last object
                                try:
                                    # Find the last complete object in the array
                                    fixed_json = re.sub(r',\s*$', '', matched_json)  # Remove trailing comma
                                    fixed_json = re.sub(r'}\s*$', '}]', fixed_json)  # Complete the array
                                    # Also handle incomplete objects at the end
                                    if fixed_json.count('{') > fixed_json.count('}'):
                                        fixed_json = fixed_json.rstrip() + '}]'
                                    ai_recommendations = json.loads(fixed_json)
                                    recommendations.extend(ai_recommendations)
                                    logger.info(f"Successfully parsed {len(ai_recommendations)} recommendations from Bedrock using fixed JSON")
                                except json.JSONDecodeError as fix_error:
                                    logger.warning(f"JSON fixing failed: {fix_error}")
                                    # Try one more approach - extract complete objects only
                                    try:
                                        import re
                                        # Find all complete JSON objects
                                        object_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
                                        objects = re.findall(object_pattern, matched_json)
                                        if objects:
                                            fixed_json = '[' + ','.join(objects) + ']'
                                            ai_recommendations = json.loads(fixed_json)
                                            recommendations.extend(ai_recommendations)
                                            logger.info(f"Successfully parsed {len(ai_recommendations)} recommendations from Bedrock using object extraction")
                                        else:
                                            raise ValueError("No complete objects found")
                                    except Exception as extract_error:
                                        logger.warning(f"Object extraction failed: {extract_error}")
                                        raise ValueError("Could not parse JSON even after fixing")
                        else:
                            logger.warning("No JSON array found in Bedrock response, trying to extract partial recommendations")
                            # Try to extract any complete objects from the response
                            try:
                                # Find all complete JSON objects in the response
                                import re
                                object_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
                                objects = re.findall(object_pattern, ai_response)
                                if objects:
                                    # Create a valid JSON array from complete objects
                                    fixed_json = '[' + ','.join(objects) + ']'
                                    ai_recommendations = json.loads(fixed_json)
                                    recommendations.extend(ai_recommendations)
                                    logger.info(f"Successfully extracted {len(ai_recommendations)} recommendations from partial response")
                                else:
                                    raise ValueError("No complete objects found")
                            except Exception as extract_error:
                                logger.warning(f"Object extraction failed: {extract_error}")
                                # Add AI error message but continue with fallback
                                recommendations.append({
                                    'type': 'ai_error',
                                    'title': '🤖 AI Analysis Unavailable',
                                    'description': 'The AI assistant encountered an issue processing your request. Showing standard recommendations below.',
                                    'action': 'none',
                                    'priority': 'low',
                                    'icon': 'error'
                                })
                                # Fallback to structured recommendations based on stage
                                recommendations.extend(get_fallback_recommendations(stage_key, stage_name, stage_completed, patient_id))
                except json.JSONDecodeError as e:
                    logger.error(f"JSON decode error: {e}")
                    # Add AI error message but continue with fallback
                    recommendations.append({
                        'type': 'ai_error',
                        'title': '🤖 AI Response Error',
                        'description': 'The AI assistant returned an invalid response format. Showing standard recommendations below.',
                        'action': 'none',
                        'priority': 'low',
                        'icon': 'error'
                    })
                    # Fallback to structured recommendations
                    recommendations.extend(get_fallback_recommendations(stage_key, stage_name, stage_completed, patient_id))
            else:
                logger.warning(f"Bedrock call failed: {result.get('message', 'Unknown error')}")
                # Add AI error message but continue with fallback
                recommendations.append({
                    'type': 'ai_error',
                    'title': '🤖 AI Service Unavailable',
                    'description': f'The AI assistant is currently unavailable: {result.get("message", "Unknown error")}. Showing standard recommendations below.',
                    'action': 'none',
                    'priority': 'low',
                    'icon': 'error'
                })
                # Fallback to structured recommendations
                recommendations.extend(get_fallback_recommendations(stage_key, stage_name, stage_completed, patient_id))
                
        except Exception as e:
            logger.error(f"Error calling Bedrock integration: {e}")
            # Add AI error message but continue with fallback
            recommendations.append({
                'type': 'ai_error',
                'title': '🤖 AI Integration Error',
                'description': f'The AI assistant encountered a technical error: {str(e)}. Showing standard recommendations below.',
                'action': 'none',
                'priority': 'low',
                'icon': 'error'
            })
            # Fallback to structured recommendations
            recommendations.extend(get_fallback_recommendations(stage_key, stage_name, stage_completed, patient_id))
        
    except Exception as e:
        logger.error(f"Error generating AI workflow recommendations: {e}")
        recommendations.append({
            'type': 'ai_error',
            'title': '🤖 AI System Error',
            'description': f'Unable to generate AI recommendations due to: {str(e)}. Showing standard recommendations below.',
            'action': 'none',
            'priority': 'low',
            'icon': 'error'
        })
        # Still try to provide fallback recommendations
        try:
            recommendations.extend(get_fallback_recommendations(stage_key, stage_name, stage_completed, patient_id))
        except Exception as fallback_error:
            logger.error(f"Even fallback recommendations failed: {fallback_error}")
            recommendations.append({
                'type': 'error',
                'title': 'System Error',
                'description': 'Unable to generate any recommendations at this time. Please try again later.',
                'action': 'none',
                'priority': 'low',
                'icon': 'error'
            })
    
    logger.info(f"Returning {len(recommendations)} recommendations: {recommendations}")
    logger.info(f"=== END AI WORKFLOW RECOMMENDATIONS DEBUG ===")
    
    return recommendations

def get_fallback_recommendations(stage_key, stage_name, stage_completed, patient_id):
    """Fallback recommendations when Bedrock is not available"""
    recommendations = []
    
    if stage_key == 'initial_consult_scheduled':
        if not stage_completed:
            recommendations.append({
                'type': 'schedule_consultation',
                'title': 'Schedule Specialist Appointment',
                'description': f'Patient is in "{stage_name}" stage. Schedule an appointment with a sleep specialist.',
                'action': 'schedule_consultation',
                'action_key': 'schedule_consultation',
                'consult_type': 'sleep_specialist',
                'priority': 'high',
                'icon': 'schedule'
            })
        else:
            recommendations.append({
                'type': 'validate_consultation',
                'title': 'Validate Specialist Meeting',
                'description': f'Confirm that the sleep specialist consultation has been completed.',
                'action': 'validate_consultation',
                'action_key': 'complete_consultation',
                'consult_type': 'sleep_specialist',
                'priority': 'medium',
                'icon': 'check_circle'
            })
            
    elif stage_key == 'sleep_study_scheduled':
        if not stage_completed:
            recommendations.append({
                'type': 'schedule_consultation',
                'title': 'Schedule Sleep Study',
                'description': f'Patient is in "{stage_name}" stage. Schedule a sleep study appointment.',
                'action': 'schedule_consultation',
                'action_key': 'schedule_sleep_study',
                'consult_type': 'sleep_study',
                'priority': 'high',
                'icon': 'schedule'
            })
        else:
            recommendations.append({
                'type': 'validate_consultation',
                'title': 'Validate Sleep Study',
                'description': f'Confirm that the sleep study has been completed and results are available.',
                'action': 'validate_consultation',
                'action_key': 'complete_sleep_doctor_followup',
                'consult_type': 'sleep_study',
                'priority': 'medium',
                'icon': 'check_circle'
            })
            
    elif stage_key == 'dental_consultation_scheduled':
        if not stage_completed:
            recommendations.append({
                'type': 'schedule_consultation',
                'title': 'Schedule Dental Consultation',
                'description': f'Patient is in "{stage_name}" stage. Schedule consultation with dental expert.',
                'action': 'schedule_consultation',
                'action_key': 'schedule_dental_consultation',
                'consult_type': 'dental_expert',
                'priority': 'high',
                'icon': 'schedule'
            })
        else:
            recommendations.append({
                'type': 'validate_consultation',
                'title': 'Validate Dental Consultation',
                'description': f'Confirm that the dental consultation has been completed.',
                'action': 'validate_consultation',
                'action_key': 'complete_dental_consultation',
                'consult_type': 'dental_expert',
                'priority': 'medium',
                'icon': 'check_circle'
            })
            
    elif stage_key == 'sleep_doctor_followup_completed':
        if not stage_completed:
            recommendations.append({
                'type': 'complete_followup',
                'title': 'Complete Sleep Doctor Followup',
                'description': f'Mark the sleep doctor followup as completed in the system to move the patient to the next stage of their care plan.',
                'action': 'complete_followup',
                'action_key': 'complete_sleep_doctor_followup',
                'priority': 'high',
                'icon': 'check_circle'
            })
        else:
            recommendations.append({
                'type': 'schedule_next',
                'title': 'Schedule Next Appointment',
                'description': f'Arrange the next follow-up appointment with the sleep doctor to ensure continuity of care.',
                'action': 'schedule_next',
                'action_key': 'schedule_sleep_test_review',
                'priority': 'medium',
                'icon': 'schedule'
            })
            
    elif stage_key == 'schedule_sleep_test_review':
        if not stage_completed:
            recommendations.append({
                'type': 'schedule_review',
                'title': 'Schedule Sleep Test Review',
                'description': f'Schedule a consultation with the sleep doctor to review the sleep study results.',
                'action': 'schedule_review',
                'action_key': 'schedule_sleep_test_review',
                'priority': 'high',
                'icon': 'schedule'
            })
        else:
            recommendations.append({
                'type': 'complete_review',
                'title': 'Complete Sleep Test Review',
                'description': f'Mark the sleep test review as completed.',
                'action': 'complete_review',
                'action_key': 'complete_sleep_doctor_followup',
                'priority': 'medium',
                'icon': 'check_circle'
            })
            
    elif stage_key == 'order_oral_appliance':
        # Check if oral appliance order exists
        from flask_app.models import PatientDeviceOrder
        existing_order = PatientDeviceOrder.query.filter_by(
            patient_id=patient_id,
            device_type='oral_appliance'
        ).first()
        
        if not existing_order:
            recommendations.append({
                'type': 'order_appliance',
                'title': 'Order Oral Appliance',
                'description': f'Patient is in "{stage_name}" stage. Place order for oral appliance in patient_device_order table.',
                'action': 'order_appliance',
                'action_key': 'order_oral_appliance',
                'priority': 'high',
                'icon': 'shopping_cart'
            })
        elif existing_order.status == 'ordered':
            recommendations.append({
                'type': 'track_delivery',
                'title': 'Track Appliance Delivery',
                'description': f'Monitor the delivery status of the ordered oral appliance. Current status: {existing_order.status}',
                'action': 'track_delivery',
                'action_key': 'update_device_delivery',
                'priority': 'medium',
                'icon': 'local_shipping'
            })
        elif existing_order.status == 'delivered':
            recommendations.append({
                'type': 'schedule_delivery',
                'title': 'Schedule Appliance Delivery',
                'description': f'Device has been delivered. Schedule the delivery appointment with the patient.',
                'action': 'schedule_delivery',
                'priority': 'high',
                'icon': 'schedule'
            })
    
    # Remove generic "Complete Current Stage" action - each stage should have specific actions
    # if not stage_completed:
    #     recommendations.append({
    #         'type': 'general',
    #         'title': 'Complete Current Stage',
    #         'description': f'Ensure all requirements for "{stage_name}" stage are met.',
    #         'action': 'complete_stage',
    #         'priority': 'high',
    #         'icon': 'assignment'
    #     })
    
    return recommendations
    
    


    
    

@main.route('/patient_workflow_bedrock/<int:patient_id>', methods=['GET'])
@login_required
def patient_workflow_bedrock(patient_id):
    """Generate patient workflow page using template with dynamic content"""
    try:
        # Get patient details
        patient = Patient.query.get(patient_id)
        if not patient:
            flash('Patient not found', 'error')
            return redirect(url_for('main.patient_list'))
        
        # Get execution manifest
        from flask_app.routes.cursor_routes import get_execution_manifest
        execution_manifest_response = get_execution_manifest(patient_id)
        
        # Check if it's a Flask Response object
        if hasattr(execution_manifest_response, 'get_json'):
            # It's a Flask Response, get the JSON data
            execution_manifest = execution_manifest_response.get_json()
        else:
            # It's already a dictionary
            execution_manifest = execution_manifest_response
        
        if not execution_manifest or 'error' in execution_manifest:
            error_msg = execution_manifest.get('error', 'Failed to load execution manifest') if execution_manifest else 'Failed to load execution manifest'
            flash(error_msg, 'error')
            return redirect(url_for('main.patient_list'))
        
        manifest_data = execution_manifest
        
        # Render the template with patient and manifest data
        return render_template('patient_workflow_bedrock.html', 
                             patient=patient, 
                             manifest_data=manifest_data)
        
    except Exception as e:
        logger.error(f"Error in patient_workflow_bedrock: {e}")
        flash(f'Error loading workflow page: {str(e)}', 'error')
        return redirect(url_for('main.patient_workflow_test', patient_id=patient_id))

@main.route('/test_bedrock_simple/<int:patient_id>', methods=['GET'])
@login_required
def test_bedrock_simple(patient_id):
    """Simple test to debug Bedrock function"""
    try:
        from flask_app.routes.osaagent_routes import query_bedrock_claude_enhanced
        
        messages = [
            {
                "role": "user",
                "content": "Hello, this is a test message. Please respond with 'Test successful'."
            }
        ]
        
        logger.info("Testing Bedrock function...")
        result = query_bedrock_claude_enhanced(messages, max_tokens=100, temperature=0.1)
        
        logger.info(f"Result type: {type(result)}")
        logger.info(f"Result: {result}")
        
        if isinstance(result, dict):
            return jsonify({
                'success': True,
                'result_type': str(type(result)),
                'result': result
            })
        else:
            return jsonify({
                'success': False,
                'result_type': str(type(result)),
                'result': str(result)
            })
            
    except Exception as e:
        logger.error(f"Test error: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'error_type': str(type(e))
        })

@main.route('/api/bedrock/generate_workflow_content/<int:patient_id>', methods=['POST'])
@login_required
def generate_workflow_content(patient_id):
    """Generate dynamic workflow content using Bedrock"""
    try:
        data = request.get_json()
        manifest_data = data.get('manifest_data', {})
        
        # Create simplified manifest to reduce token count
        simplified_manifest = create_simplified_manifest_for_ai(manifest_data)
        
        # Prepare the prompt for Bedrock
        prompt = f"""You are an expert UI generator. Generate a complete, professional HTML dashboard using Bootstrap 5 ONLY.

Use the provided manifest data to generate the UI:

patient_info: includes name, email, phone, ID, etc.
stage_manifest: an array of stage objects with stage_name and value
eligible_actions: an array of actions with label, ui_type, endpoint, input_fields, and ai_guidance

⚙️ Required UI Sections:

1. Patient Summary Card - Show patient name, email, phone, and status
2. Progress Section - Visual progress bar with stage badges (green=completed, red=pending)
3. Available Actions - Cards for each action with proper buttons/forms

🎯 HTML/JS Requirements:

- Use Bootstrap 5 only (no Tailwind)
- Include proper grid layout (row, col-md-*)
- Use fetch() for all API calls (POST)
- Add JavaScript functions executeAction() and executeForm()
- Show Bootstrap toast notifications after actions
- Return ONLY valid HTML/JS code

### MANIFEST DATA:
{simplified_manifest}"""

        # Call Bedrock with retry logic
        from flask_app.routes.osaagent_routes import query_bedrock_claude_enhanced
        import time
        
        messages = [
            {
                "role": "user",
                "content": prompt
            }
        ]
        
        # Retry logic for Bedrock throttling
        max_retries = 3
        retry_delay = 2  # seconds
        result = None
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Bedrock content generation attempt {attempt + 1}/{max_retries}")
                result = query_bedrock_claude_enhanced(messages, max_tokens=3000, temperature=0.1)
                
                if result and isinstance(result, dict) and result.get('success'):
                    logger.info("Bedrock content generation successful")
                    break
                else:
                    logger.warning(f"Bedrock attempt {attempt + 1} failed: {result}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay * (attempt + 1))
                        
            except Exception as e:
                logger.error(f"Bedrock attempt {attempt + 1} error: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (attempt + 1))
                else:
                    # Return fallback content
                    return jsonify({
                        'success': False,
                        'error': 'AI service unavailable',
                        'fallback': True
                    })
        
        if not result or not isinstance(result, dict) or not result.get('success'):
            return jsonify({
                'success': False,
                'error': 'Failed to generate content',
                'fallback': True
            })
        
        # Extract the HTML content from Bedrock response
        html_content = result.get('response', '')
        
        # Log what the AI actually generated for debugging
        logger.info(f"AI Generated Content Length: {len(html_content)}")
        logger.info(f"AI Generated Content Preview: {html_content[:500]}...")
        
        return jsonify({
            'success': True,
            'html_content': html_content
        })
        
    except Exception as e:
        logger.error(f"Error in generate_workflow_content: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'fallback': True
        })

def create_fallback_workflow_page(manifest_data, patient):
    """Create a simple fallback HTML page when Bedrock is unavailable"""
    try:
        patient_info = manifest_data.get('patient_info', {})
        stage_manifest = manifest_data.get('stage_manifest', [])
        eligible_actions = manifest_data.get('eligible_actions', [])
        
        html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Patient Workflow - {patient_info.get('name', 'Unknown')}</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50">
    <div class="container mx-auto px-4 py-8">
        <!-- Patient Summary -->
        <div class="bg-white rounded-lg shadow-md p-6 mb-6">
            <h1 class="text-2xl font-bold text-gray-800 mb-4">Patient Workflow</h1>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                    <h2 class="text-lg font-semibold text-gray-700 mb-2">Patient Information</h2>
                    <p><strong>Name:</strong> {patient_info.get('name', 'N/A')}</p>
                    <p><strong>Email:</strong> {patient_info.get('email', 'N/A')}</p>
                    <p><strong>Phone:</strong> {patient_info.get('phone', 'N/A')}</p>
                </div>
                <div>
                    <h2 class="text-lg font-semibold text-gray-700 mb-2">Status</h2>
                    <p><strong>Patient ID:</strong> {patient_info.get('patient_id', 'N/A')}</p>
                    <p><strong>Status:</strong> {patient_info.get('status', 'N/A')}</p>
                </div>
            </div>
        </div>

        <!-- Stage Progress -->
        <div class="bg-white rounded-lg shadow-md p-6 mb-6">
            <h2 class="text-xl font-semibold text-gray-800 mb-4">Stage Progress</h2>
            <div class="space-y-3">
"""
        
        for stage in stage_manifest:
            status = "✅ Completed" if stage.get('value') == 'yes' else "⏳ Pending"
            status_class = "text-green-600" if stage.get('value') == 'yes' else "text-yellow-600"
            html_content += f"""
                <div class="flex items-center justify-between p-3 bg-gray-50 rounded">
                    <div>
                        <span class="font-medium">{stage.get('stage_number', '?')}. {stage.get('stage_name', 'Unknown Stage')}</span>
                    </div>
                    <span class="{status_class} font-medium">{status}</span>
                </div>
"""
        
        html_content += """
            </div>
        </div>

        <!-- Available Actions -->
        <div class="bg-white rounded-lg shadow-md p-6">
            <h2 class="text-xl font-semibold text-gray-800 mb-4">Available Actions</h2>
            <div class="space-y-4">
"""
        
        for action in eligible_actions:
            action_key = action.get('action_key', 'unknown')
            label = action.get('label', 'Unknown Action')
            ui_type = action.get('ui_type', 'button')
            endpoint = action.get('endpoint', '#')
            ai_guidance = action.get('ai_guidance', 'No guidance available')
            
            if ui_type == 'button':
                html_content += f"""
                <div class="p-4 border border-gray-200 rounded-lg">
                    <div class="flex items-center justify-between">
                        <div>
                            <h3 class="font-medium text-gray-800">{label}</h3>
                            <p class="text-sm text-gray-600 mt-1">{ai_guidance}</p>
                        </div>
                        <button onclick="executeAction('{endpoint}', '{action_key}')" 
                                class="bg-blue-500 hover:bg-blue-600 text-white px-4 py-2 rounded">
                            Execute
                        </button>
                    </div>
                </div>
"""
            elif ui_type == 'form':
                input_fields = action.get('input_fields', [])
                html_content += f"""
                <div class="p-4 border border-gray-200 rounded-lg">
                    <h3 class="font-medium text-gray-800 mb-2">{label}</h3>
                    <p class="text-sm text-gray-600 mb-3">{ai_guidance}</p>
                    <form onsubmit="executeForm(event, '{endpoint}', '{action_key}')" class="space-y-3">
"""
                for field in input_fields:
                    html_content += f"""
                        <div>
                            <label class="block text-sm font-medium text-gray-700">{field}</label>
                            <input type="text" name="{field}" required 
                                   class="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2">
                        </div>
"""
                html_content += """
                        <button type="submit" class="bg-green-500 hover:bg-green-600 text-white px-4 py-2 rounded">
                            Submit
                        </button>
                    </form>
                </div>
"""
            elif ui_type == 'upload_link':
                upload_link = action.get('upload_link', '#')
                html_content += f"""
                <div class="p-4 border border-gray-200 rounded-lg">
                    <div class="flex items-center justify-between">
                        <div>
                            <h3 class="font-medium text-gray-800">{label}</h3>
                            <p class="text-sm text-gray-600 mt-1">{ai_guidance}</p>
                        </div>
                        <a href="{upload_link}" target="_blank" 
                           class="bg-purple-500 hover:bg-purple-600 text-white px-4 py-2 rounded">
                            Upload
                        </a>
                    </div>
                </div>
"""
        
        html_content += """
            </div>
        </div>
    </div>

    <script>
        function executeAction(endpoint, actionKey) {
            console.log('Executing action:', actionKey, 'at endpoint:', endpoint);
            fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    action_key: actionKey,
                    patient_id: """ + str(patient.id) + """
                })
            })
            .then(response => response.json())
            .then(data => {
                console.log('Action result:', data);
                alert('Action executed successfully!');
            })
            .catch(error => {
                console.error('Error:', error);
                alert('Error executing action');
            });
        }

        function executeForm(event, endpoint, actionKey) {
            event.preventDefault();
            const formData = new FormData(event.target);
            const data = {};
            for (let [key, value] of formData.entries()) {
                data[key] = value;
            }
            data.action_key = actionKey;
            data.patient_id = """ + str(patient.id) + """;

            console.log('Executing form action:', actionKey, 'at endpoint:', endpoint);
            fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(data)
            })
            .then(response => response.json())
            .then(data => {
                console.log('Form action result:', data);
                alert('Form submitted successfully!');
            })
            .catch(error => {
                console.error('Error:', error);
                alert('Error submitting form');
            });
        }
    </script>
</body>
</html>
"""
        
        return html_content, 200, {'Content-Type': 'text/html'}
        
    except Exception as e:
        logger.error(f"Error creating fallback workflow page: {e}")
        return f"<h1>Error creating workflow page: {str(e)}</h1>", 500, {'Content-Type': 'text/html'}

def create_simplified_manifest_for_ai(manifest_data):
    """Create a simplified manifest for AI processing to reduce token count"""
    simplified = {
        "patient_info": {
            "name": manifest_data.get("patient_info", {}).get("name", ""),
            "email": manifest_data.get("patient_info", {}).get("email", ""),
            "phone": manifest_data.get("patient_info", {}).get("phone", ""),
            "patient_id": manifest_data.get("patient_info", {}).get("patient_id", ""),
            "status": manifest_data.get("patient_info", {}).get("status", "")
        },
        "stage_manifest": [],
        "eligible_actions": []
    }
    
    # Simplify stage manifest - only keep essential fields
    for stage in manifest_data.get("stage_manifest", []):
        simplified["stage_manifest"].append({
            "stage_name": stage.get("stage_name", ""),
            "value": stage.get("value", "no")
        })
    
    # Simplify eligible actions - only keep essential fields
    for action in manifest_data.get("eligible_actions", []):
        simplified["eligible_actions"].append({
            "action_key": action.get("action_key", ""),
            "label": action.get("label", ""),
            "ui_type": action.get("ui_type", ""),
            "endpoint": action.get("endpoint", ""),
            "input_fields": action.get("input_fields", []),
            "ai_guidance": action.get("ai_guidance", "")[:100] + "..." if len(action.get("ai_guidance", "")) > 100 else action.get("ai_guidance", "")
        })
    
    return simplified

@main.route('/patient_workflow_bootstrap/<int:patient_id>', methods=['GET'])
@login_required
def patient_workflow_bootstrap(patient_id):
    """Display a Bootstrap 5-based patient workflow interface with action buttons"""
    try:
        # Get patient information
        patient = Patient.query.get(patient_id)
        if not patient:
            flash('Patient not found', 'error')
            return redirect(url_for('main.patient_list'))
        
        # Get execution manifest data
        from flask_app.routes.cursor_routes import get_execution_manifest
        execution_manifest_response = get_execution_manifest(patient_id)
        
        # Check if it's a Flask Response object
        if hasattr(execution_manifest_response, 'get_json'):
            # It's a Flask Response, get the JSON data
            execution_manifest = execution_manifest_response.get_json()
        else:
            # It's already a dictionary
            execution_manifest = execution_manifest_response
        
        if not execution_manifest or 'error' in execution_manifest:
            error_msg = execution_manifest.get('error', 'Failed to load execution manifest') if execution_manifest else 'Failed to load execution manifest'
            flash(error_msg, 'error')
            return redirect(url_for('main.patient_list'))
        
        manifest_data = execution_manifest
        
        # Calculate progress
        stage_manifest = manifest_data.get('stage_manifest', [])
        completed_stages = sum(1 for stage in stage_manifest if stage.get('value') == 'yes')
        total_stages = len(stage_manifest)
        progress_percentage = (completed_stages / total_stages * 100) if total_stages > 0 else 0
        
        # Get eligible actions
        eligible_actions = manifest_data.get('eligible_actions', [])
        
        return render_template('patient_workflow_bootstrap.html', 
                             patient=patient, 
                             manifest_data=manifest_data,
                             progress_percentage=progress_percentage,
                             completed_stages=completed_stages,
                             total_stages=total_stages,
                             eligible_actions=eligible_actions)
                             
    except Exception as e:
        logger.error(f"Error in patient_workflow_bootstrap: {e}")
        flash(f'Error loading patient workflow: {str(e)}', 'error')
        return redirect(url_for('main.patient_list'))

@main.route('/patient_workflow_manifest/<int:patient_id>', methods=['GET'])
@login_required
def patient_workflow_manifest(patient_id):
    """Display a manifest-aware patient workflow interface with LLM guidance"""
    try:
        # Get patient information
        patient = Patient.query.get(patient_id)
        if not patient:
            flash('Patient not found', 'error')
            return redirect(url_for('main.patient_list'))
        
        # Get execution manifest data
        from flask_app.routes.cursor_routes import get_execution_manifest
        execution_manifest_response = get_execution_manifest(patient_id)
        
        # Check if it's a Flask Response object
        if hasattr(execution_manifest_response, 'get_json'):
            execution_manifest = execution_manifest_response.get_json()
        else:
            execution_manifest = execution_manifest_response
        
        if not execution_manifest or 'error' in execution_manifest:
            error_msg = execution_manifest.get('error', 'Failed to load execution manifest') if execution_manifest else 'Failed to load execution manifest'
            flash(error_msg, 'error')
            return redirect(url_for('main.patient_list'))
        
        manifest_data = execution_manifest
        
        # Calculate progress
        stage_manifest = manifest_data.get('stage_manifest', [])
        completed_stages = sum(1 for stage in stage_manifest if stage.get('value') == 'yes')
        total_stages = len(stage_manifest)
        progress_percentage = (completed_stages / total_stages * 100) if total_stages > 0 else 0
        
        # Get eligible actions
        eligible_actions = manifest_data.get('eligible_actions', [])
        
        # Get current stage and next steps using LLM
        from flask_app.routes.osaagent_routes import query_bedrock_claude_enhanced
        
        # Create LLM prompt for guidance - Focus on next 3 stages only
        current_stage_index = completed_stages
        next_stages = stage_manifest[current_stage_index:current_stage_index + 3] if current_stage_index < len(stage_manifest) else []
        
        llm_prompt = f"""
        Provide brief guidance for the next 3 treatment stages.
        
        Patient: {patient.name} (ID: {patient.id})
        Progress: {completed_stages}/{total_stages} completed ({progress_percentage:.1f}%)
        
        Next 3 stages:
        {chr(10).join([f"{i+1}. {stage['stage_name']}" for i, stage in enumerate(next_stages)])}
        
        Available actions: {[a['label'] for a in eligible_actions]}
        
        Provide 2-3 sentences max. Focus on immediate next action.
        """
        
        messages = [
            {
                "role": "user",
                "content": llm_prompt
            }
        ]
        
        # Get LLM guidance
        llm_response = query_bedrock_claude_enhanced(messages, max_tokens=300, temperature=0.3)
        ai_guidance = llm_response.get('response', 'Unable to generate guidance at this time.') if llm_response.get('success') else 'AI guidance temporarily unavailable.'
        
        return render_template('patient_workflow_manifest.html', 
                             patient=patient, 
                             manifest_data=manifest_data,
                             progress_percentage=progress_percentage,
                             completed_stages=completed_stages,
                             total_stages=total_stages,
                             eligible_actions=eligible_actions,
                             ai_guidance=ai_guidance)
                             
    except Exception as e:
        logger.error(f"Error in patient_workflow_manifest: {e}")
        flash(f'Error loading patient workflow: {str(e)}', 'error')
        return redirect(url_for('main.patient_list'))


    
    