
from flask import Blueprint, request, render_template, redirect, url_for, flash, jsonify, send_file
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from .. import db
from ..models import db, Patient, File, Dentist, AdminFile, Claim, Comment, PatientStatus, StatusOption, PatientComment, Clinic
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
from flask_app.extensions import db
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
        clinics_by_dso=clinics_by_dso
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
        "comments": comments  # Include comments in the returned details
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
    """Build patient manifest with comprehensive logging"""
    logger.info(f"=== BUILD_PATIENT_MANIFEST STARTED for patient_id: {patient_id} ===")
    
    try:
        # Demographics
        logger.info(f"Querying patient with ID: {patient_id}")
        patient = Patient.query.get(patient_id)
        
        if not patient:
            logger.error(f"Patient with ID {patient_id} not found in database")
            return None, None, None
        
        logger.info(f"Found patient: {patient.name} (ID: {patient.id})")
        
        demographics = {
            'id': patient.id,
            'name': patient.name,
            'gender': getattr(patient, 'gender', None),
            'last_visit': None,  # Populate if you track this
            'osa_risk_score': None  # Populate if you track this
        }
        
        logger.info(f"Demographics built: {demographics}")
        
        # Calculate age (not part of manifest)
        age = None
        if getattr(patient, 'dob', None):
            today = date.today()
            dob = patient.dob
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            logger.info(f"Calculated age: {age}")
        else:
            logger.info("No date of birth available for age calculation")

        # Use centralized manifest definition
        definition_manifest = get_manifest_definition()
        logger.info(f"Using centralized manifest definition with {len(definition_manifest)} stages")

        manifest = []
        for stage in definition_manifest:
            key = stage['key']
            stage_number = stage['stage_number']
            stage_name = stage['stage_name']
            
            # Get stage information from patient_manifest table
            manifest_result = db.session.execute(
                text("""
                    SELECT is_completed, completion_date, stage_data, status_message
                    FROM patient_manifest 
                    WHERE patient_id = :pid AND stage_key = :stage_key
                """),
                {'pid': patient_id, 'stage_key': key}
            ).first()
            comment = None
            if manifest_result:
                # Stage exists in manifest table
                if manifest_result.is_completed:
                    value = 'yes'
                else:
                    value = 'no'
                # Add comment if available
                if manifest_result.status_message:
                    comment = manifest_result.status_message
            else:
                # Stage doesn't exist in manifest table yet - check if it should be completed
                if key == "quiz_completion":
                    result = db.session.execute(
                        text("SELECT id, created_at, quiz_type, patient_email FROM conversion_quiz WHERE user_id = :pid ORDER BY created_at DESC LIMIT 1"),
                        {'pid': patient_id}
                    ).first()
                    if result and hasattr(result, 'id') and result.id is not None:
                        value = {
                            'quiz_id': result.id,
                            'created_at': result.created_at.isoformat() if result.created_at else None,
                            'quiz_type': result.quiz_type,
                            'patient_email': result.patient_email
                        }
                    else:
                        value = None
                elif key == "initial_consult_scheduled":
                    result = db.session.execute(
                        text("SELECT 1 FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = 'sleep_expert' LIMIT 1"),
                        {'pid': patient_id}
                    ).first()
                    value = 'yes' if result else 'no'
                elif key == "met_with_sleep_expert":
                    result = db.session.execute(
                        text("SELECT 1 FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = 'sleep_expert' AND status = 'completed' LIMIT 1"),
                        {'pid': patient_id}
                    ).first()
                    value = 'yes' if result else 'no'
                elif key == "sleep_doctor_consult_scheduled":
                    result = db.session.execute(
                        text("SELECT 1 FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = 'sleep_doctor' LIMIT 1"),
                        {'pid': patient_id}
                    ).first()
                    value = 'yes' if result else 'no'
                elif key == "sleep_test_completed":
                    result = db.session.execute(
                        text("SELECT 1 FROM adminfiles WHERE patient_id = :pid AND (LOWER(name) LIKE '%.pdf' OR LOWER(name) LIKE '%.dcm') LIMIT 1"),
                        {'pid': patient_id}
                    ).first()
                    value = 'yes' if result else 'no'
                elif key == "sleep_doctor_followup_completed":
                    result = db.session.execute(
                        text("SELECT 1 FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = 'sleep_doctor' AND status = 'completed' LIMIT 1"),
                        {'pid': patient_id}
                    ).first()
                    value = 'yes' if result else 'no'
                elif key == "dental_sleep_doctor_consult_scheduled":
                    result = db.session.execute(
                        text("SELECT 1 FROM patient_consult_schedule WHERE patient_id = :pid AND consult_type = 'dental_sleep_doctor' LIMIT 1"),
                        {'pid': patient_id}
                    ).first()
                    value = 'yes' if result else 'no'
                else:
                    # For stages that should be validated by the validator, default to 'no'
                    value = 'no'
            stage_entry = {
                "stage_number": stage_number,
                "stage_name": stage_name,
                "key": key,
                "value": value
            }
            if comment:
                stage_entry["comment"] = comment
            manifest.append(stage_entry)
        logger.info(f"Manifest built successfully with {len(manifest)} stages")
        logger.info(f"Final manifest: {manifest}")
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
        logger.info(f"Current stage selected: {current_stage['key']} - {current_stage['name']}")
        logger.info(f"Current stage next_step: {current_stage['next_step']}")
        logger.info(f"All stages and their next_steps:")
        for stage in stages:
            logger.info(f"  {stage['key']}: {stage['name']} -> {stage['next_step']} (status: {stage['status']})")
        
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
                             document_observations=document_observations
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
        
        # Extract patient_id and user_message
        patient_id = data.get('patient_id')
        user_message = data.get('message')
        
        print(f"Extracted patient_id: {patient_id}")
        print(f"Extracted user_message: {user_message}")
        
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
- READ THE MANIFEST DATA FROM THE PROVIDED S3 URLs before responding

RECOMMENDATION STYLE:
- When making recommendations based on clinical data, present them as YOUR professional assessment
- Be confident and authoritative in your recommendations
- Don't reference external sources or say "someone else recommended" - make it sound like YOUR recommendation
- Use phrases like "I recommend", "Based on my analysis", "I suggest", "My recommendation is"
- When discussing specific devices or treatments found in documents, present them as YOUR professional choice
- Be decisive and avoid hedging language like "likely" or "should be confirmed"

RESPONSE STYLE:
- Keep responses concise and direct (2-4 sentences maximum)
- Focus on practical, actionable information
- Be warm and professional but avoid lengthy medical disclaimers
- Provide specific, relevant answers without unnecessary warnings
- Use bullet points for multiple items when helpful
- Avoid repetitive phrases like "However, I must emphasize" or "preliminary recommendations"
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

IMPORTANT: When making recommendations based on clinical data, present them as YOUR professional assessment. Be confident and authoritative. Use phrases like "I recommend", "Based on my analysis", "I suggest", "My recommendation is". Don't reference external sources or say "someone else recommended" - make it sound like YOUR recommendation.

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
                max_tokens=300,
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
