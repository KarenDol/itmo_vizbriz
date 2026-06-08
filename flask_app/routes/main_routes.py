from flask import Blueprint, request, render_template, redirect, url_for, flash, jsonify, send_file, abort, session
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
from typing import Tuple, Dict, Any, Optional
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
from flask_app.models import Patient, File, Dentist, AdminFile, Claim, Comment, PatientStatus, StatusOption, PatientComment, Clinic, PatientConsultSchedule, PatientDeviceOrder, DentistReportApproval, ConsultationRequest, ObservationStore, PatientCaseEnvelope, PatientStageSummaryCache, DocumentProcessingQueue
from collections import OrderedDict
from datetime import date
from flask_app.config.manifest_config import get_manifest_definition
from flask_app.models import DSO, Clinic, Dentist, Patient, dentist_clinic_association  # add others as needed
import json
import pymysql
import io
import qrcode
import mysql.connector
from flask_app.services.delta_ingest import apply_delta_for_patient

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

main = Blueprint('main', __name__)

# Route group registrations (progressive split of this file)
from flask_app.routes.main.auth_routes import register_auth_routes
from flask_app.routes.main.docs_routes import register_docs_routes
from flask_app.routes.main.admin_routes import register_admin_routes
from flask_app.routes.main.patient_list_routes import register_patient_list_routes
from flask_app.routes.main.dentist_clinic_routes import register_dentist_clinic_routes
from flask_app.routes.main.file_upload_routes import register_file_upload_routes
from flask_app.routes.main.misc_pages_routes import register_misc_pages_routes
from flask_app.routes.main.patient_status_routes import register_patient_status_routes
from flask_app.routes.main.consultation_device_routes import register_consultation_device_routes
from flask_app.routes.main.patient_comments_routes import register_patient_comments_routes
from flask_app.routes.main.patient_management_routes import register_patient_management_routes
from flask_app.routes.main.workflow_manifest_routes import register_workflow_manifest_routes

register_auth_routes(main)
register_docs_routes(main)
register_admin_routes(main)
register_patient_list_routes(main)
register_dentist_clinic_routes(main)
register_file_upload_routes(main)
register_misc_pages_routes(main)
register_patient_status_routes(main)
register_consultation_device_routes(main)
register_patient_comments_routes(main)
register_patient_management_routes(main)
register_workflow_manifest_routes(main)

# ---------------------------------------------------------------------------
# Backwards-compatibility re-exports
# Some modules historically imported helpers like `check_db_connection` from this
# module. Keep these names available while we progressively split the file.
# ---------------------------------------------------------------------------
from flask_app.routes.main.auth_routes import check_db_connection, test_s3_access  # noqa: E402,F401

def _parse_metrics_from_diagnosis(diagnosis: str) -> Dict[str, Any]:
    """Best-effort parse of AHI, SpO2 nadir, ODI, severity from a diagnosis sentence."""
    if not diagnosis:
        return {}
    metrics: Dict[str, Any] = {}
    try:
        ahi_match = re.search(r'AHI\s*[:(]?\s*([\d.]+)', diagnosis, re.IGNORECASE)
        if ahi_match:
            metrics['ahi'] = float(ahi_match.group(1))
        spo2_match = re.search(r'(SpO2|O2)\s*nadir\s*[:(]?\s*([\d.]+)\s*%?', diagnosis, re.IGNORECASE)
        if spo2_match:
            metrics['spo2_nadir'] = float(spo2_match.group(2))
        odi_match = re.search(r'ODI\s*[:(]?\s*([\d.]+)', diagnosis, re.IGNORECASE)
        if odi_match:
            metrics['odi'] = float(odi_match.group(1))
        sev_match = re.search(r'\b(severe|moderate|mild)\b', diagnosis, re.IGNORECASE)
        if sev_match:
            metrics['severity'] = sev_match.group(1).lower()
    except Exception:
        # Best-effort parsing; ignore errors
        pass
    return metrics

def build_view_models_from_llm(llm_json: Dict[str, Any],
                               fallback_packet: Optional[Dict[str, Any]] = None
                               ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Convert LLM response JSON → (clinical_vm, operational_vm) for the template.
    fallback_packet is the input packet if numeric metrics are needed as a fallback.
    """
    clinical_section = (llm_json or {}).get('clinical', {}) or {}
    operational_section = (llm_json or {}).get('operational', {}) or {}

    # Metrics from clinical.diagnosis (free text)
    metrics = _parse_metrics_from_diagnosis(clinical_section.get('diagnosis', '') or '')

    # Prefer structured sleep_study from fallback packet if available
    if fallback_packet:
        ss = (fallback_packet or {}).get('sleep_study', {}) or {}
        if ss.get('AHI') is not None:
            metrics['ahi'] = ss.get('AHI')
        if ss.get('SpO2_nadir') is not None:
            metrics['spo2_nadir'] = ss.get('SpO2_nadir')
        if ss.get('ODI') is not None:
            metrics['odi'] = ss.get('ODI')
        if ss.get('severity'):
            metrics['severity'] = ss.get('severity')

    # Derive phenotype bullets (max 6) from phenotype_summary
    phenotype_summary_text = clinical_section.get('phenotype_summary') or ''
    raw_bullets = [b.strip(' .;') for b in re.split(r'[;,•\n]+', phenotype_summary_text) if b.strip()] if phenotype_summary_text else []
    phenotype_bullets = raw_bullets[:6]

    clinical_vm: Dict[str, Any] = {
        'diagnosis': clinical_section.get('diagnosis'),
        'metrics': {
            'ahi': metrics.get('ahi'),
            'spo2_nadir': metrics.get('spo2_nadir'),
            'odi': metrics.get('odi'),
            'severity': metrics.get('severity'),
            'sleep_efficiency': None,
            'sleep_duration': None,
        },
        'phenotype_highlights': phenotype_bullets,
        'rules_fired': clinical_section.get('rules_fired') or [],
        'next_clinical_action': clinical_section.get('next_clinical_action'),
        'risks_and_monitoring': clinical_section.get('risks_and_monitoring') or [],
        'treatment_recommendations': clinical_section.get('treatment_recommendations') or [],
    }

    operational_vm: Dict[str, Any] = {
        'stage': operational_section.get('stage'),
        'completion_pct': operational_section.get('completion_pct'),
        'next_actions': operational_section.get('next_actions') or [],
        'alerts': operational_section.get('alerts') or [],
        'workflow_status': operational_section.get('workflow_status'),
    }

    return clinical_vm, operational_vm

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
                text("SELECT upload_date FROM adminfiles WHERE patient_id = :pid AND (LOWER(name) LIKE '%.pdf' OR LOWER(name) LIKE '%.dcm') ORDER BY upload_date DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.upload_date if result else None
            
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
                text("SELECT upload_date FROM adminfiles WHERE patient_id = :pid AND (LOWER(name) LIKE '%.pdf' OR LOWER(name) LIKE '%.doc' OR LOWER(name) LIKE '%.docx') ORDER BY upload_date DESC LIMIT 1"),
                {'pid': patient_id}
            ).first()
            return result.upload_date if result else None
            
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





def get_quiz_files_with_presigned_urls(quiz_id, patient_id, quiz_type):
    """Get files for a quiz submission with presigned URLs"""
    try:
        from flask_app.routes.conversion_quiz_agent import generate_presigned_url_for_viewing
        from flask_app.models import ConversionQuiz
        
        # Get the quiz submission to determine the quiz type
        quiz = ConversionQuiz.query.get(quiz_id)
        if not quiz:
            return []
        
        # Get quiz files directly from File table using category and subcategory
        # Try specific subcategory first, then fall back to general questionnaire
        quiz_files = File.query.filter(
            File.patient_id == patient_id,
            File.category == 'medical',
            File.subcategory == 'questionnaire'
        ).all()
        
        # If no files found with 'questionnaire', try other medical files
        if not quiz_files:
            quiz_files = File.query.filter(
                File.patient_id == patient_id,
                File.category == 'medical'
            ).all()
        
        # Generate presigned URLs for each file, filtering by quiz type
        files_with_urls = []
        logger.info(f"Filtering quiz files for quiz_type: {quiz_type}")
        for file in quiz_files:
            # Filter files based on quiz type
            file_name_lower = file.name.lower()
            should_include = False
            
            logger.info(f"Checking file: {file.name} for quiz_type: {quiz_type}")
            
            if quiz_type in ['basic', 'basic_quiz']:
                # For basic quiz, only include files with 'basic' in the name
                if 'basic' in file_name_lower and 'quiz' in file_name_lower:
                    should_include = True
                    logger.info(f"Including basic quiz file: {file.name}")
            elif quiz_type in ['advanced', 'advanced_quiz']:
                # For advanced quiz, only include files with 'advanced' in the name
                if 'advanced' in file_name_lower and 'quiz' in file_name_lower:
                    should_include = True
                    logger.info(f"Including advanced quiz file: {file.name}")
            else:
                # For unknown quiz type, include all files
                should_include = True
                logger.info(f"Including all files for unknown quiz_type '{quiz_type}': {file.name}")
            
            if should_include:
                presigned_url = generate_presigned_url_for_viewing(file.s3_key, inline=True, expires_in=3600)
                if presigned_url:
                    files_with_urls.append({
                        'id': file.id,
                        'name': file.name,
                        'file_type': file.file_type,
                        'upload_date': file.upload_date,
                        'view_url': presigned_url,
                        'category': file.category,
                        'subcategory': file.subcategory
                    })
        
        return files_with_urls
    except Exception as e:
        logger.error(f"Error getting quiz files for quiz {quiz_id}: {str(e)}")
        return []





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
        # Add debugging information
        logger.debug(f"Permission check for dentist {current_user.name} (ID: {current_user.id}) accessing patient {patient.name} (ID: {patient.id})")
        logger.debug(f"Patient clinic_id: {patient.clinic_id}")
        logger.debug(f"Dentist clinic associations: {current_user.get_clinic_ids()}")
        logger.debug(f"Dentist DSO associations: {current_user.get_dso_ids() if hasattr(current_user, 'get_dso_ids') else 'N/A'}")
        
        # Use the new clinic-based access control method
        if not current_user.can_access_patient(patient):
            logger.warning(f"User {current_user.email} does not have permission to view patient {patient_id}")
            logger.warning(f"Patient clinic: {patient.clinic_id}, Dentist clinics: {current_user.get_clinic_ids()}")
            flash('You do not have permission to view this patient.', 'error')
            return redirect(url_for('main.patient_list'))
        else:
            logger.debug(f"Permission granted for dentist {current_user.name} to view patient {patient.name}")

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
    cbct_directories = patient_details.get('cbct_directories', [])
    patient_statuses = patient_details.get('patient_statuses', {})
    status_options = StatusOption.query.all()
    logger.debug(f'Fetched patient details using helper (comments excluded): {patient_details}')
    cbct_directories = patient_details.get('cbct_directories', [])
    logger.debug(f"CBCT Directories from backend: {cbct_directories}")

    # Get base URL from environment variable for dynamic link generation
    base_url = os.environ.get('BASE_URL', 'http://localhost:7000')
    logger.debug(f"Using base URL for patient portal: {base_url}")

    # Fetch DSOs, clinics, and dentists for the form
    from flask_app.models import DSO, Clinic, Dentist, dentist_clinic_association
    dsos = DSO.query.filter_by(status='active').all()
    clinics = Clinic.query.filter_by(status='active').all()
    dentists = Dentist.query.filter_by(status='active').all()
    
    # Organize clinics by DSO for JavaScript
    clinics_by_dso = {}
    for clinic in clinics:
        if clinic.dso_id not in clinics_by_dso:
            clinics_by_dso[clinic.dso_id] = []
        clinics_by_dso[clinic.dso_id].append({
            'id': clinic.id,
            'name': clinic.name
        })
    
    # Organize dentists by clinic for JavaScript
    dentists_by_clinic = {}
    for clinic in clinics:
        dentists_by_clinic[clinic.id] = []
        # Query dentists associated with this clinic
        clinic_dentists = db.session.query(Dentist).join(
            dentist_clinic_association
        ).filter(
            dentist_clinic_association.c.clinic_id == clinic.id,
            Dentist.status == 'active'
        ).all()
        
        for dentist in clinic_dentists:
            dentists_by_clinic[clinic.id].append({
                'id': dentist.id,
                'name': dentist.name
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
        dentists_by_clinic=dentists_by_clinic,
        scheduled_consultations=patient_details.get('scheduled_consultations', [])  # Pass scheduled consultations
    )




@main.route('/update_patient/<int:patient_id>', methods=['POST'])
@login_required
def update_patient(patient_id):
    try:
        # Retrieve form data
        name = request.form['name']
        id_number = request.form.get('id_number', '').strip() or None
        gender = request.form.get('gender', '').strip()  # Add gender field
        email = request.form['email']
        phone = request.form['phone']
        insurer = request.form.get('insurer', '').strip()  # Add insurer field
        policy_id = request.form.get('policy_id', '').strip()  # Add policy_id field
        dob = request.form.get('dob', '').strip()
        clinic_id = request.form.get('clinic_id', '').strip()  # Add clinic_id field
        dentist_id = request.form.get('dentist_id', '').strip()  # Add dentist_id field
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
        patient.id_number = id_number
        patient.gender = gender  # Update gender
        patient.email = email
        patient.phone = phone
        patient.insurer = insurer  # Update insurer
        patient.policy_id = policy_id  # Update policy_id
        
        # Update clinic_id if provided
        if clinic_id:
            try:
                clinic_id_int = int(clinic_id)
                # Verify the dentist has access to this clinic
                if current_user.role == 'admin' or current_user.is_associated_with_clinic(clinic_id_int):
                    patient.clinic_id = clinic_id_int
                else:
                    flash('You do not have permission to assign patients to this clinic.', 'error')
                    return redirect(url_for('main.patient_details', patient_id=patient_id))
            except ValueError:
                patient.clinic_id = None
        elif not clinic_id:
            patient.clinic_id = None
        
        # Update dentist_id if provided and user is admin
        if current_user.role == 'admin' and dentist_id:
            try:
                patient.dentist_id = int(dentist_id)
            except ValueError:
                patient.dentist_id = None
        elif current_user.role == 'admin' and not dentist_id:
            patient.dentist_id = None
        # Non-admin users cannot modify dentist_id

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
        # Fix: model uses `last_update`, not `updated_date`
        patient.last_update = datetime.utcnow()

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


@main.route('/api/patient/<int:patient_id>/current-stage', methods=['GET'])
@login_required
def get_patient_current_stage(patient_id):
    """
    Get the patient's current stage and next stage in the workflow
    Based on the highest completed stage, regardless of gaps in previous stages
    """
    try:
        from flask_app.services.manifest_service import ManifestService
        
        # First sync the manifest to ensure it's up to date
        ManifestService.sync_manifest_from_database(patient_id)
        
        # Get current and next stage
        stage_info = ManifestService.get_patient_current_and_next_stage(patient_id)
        
        if not stage_info:
            return jsonify({
                'success': False,
                'message': 'Unable to determine patient stage'
            }), 404
        
        return jsonify({
            'success': True,
            'patient_id': patient_id,
            'current_stage': {
                'number': stage_info['current_stage_number'],
                'name': stage_info['current_stage_name'],
                'key': stage_info['current_stage_key']
            },
            'next_stage': {
                'number': stage_info['next_stage_number'],
                'name': stage_info['next_stage_name'],
                'key': stage_info['next_stage_key']
            },
            'workflow_progress': {
                'completion_percentage': stage_info['workflow_completion_percentage'],
                'completed_stages': stage_info['completed_stages_count'],
                'total_stages': stage_info['total_stages_count']
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting current stage for patient {patient_id}: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@main.route('/api/patient/<int:patient_id>/override-stage', methods=['POST'])
@login_required
def override_patient_stage(patient_id):
    """Override the patient's current stage manually"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        stage_key = data.get('stage_key')
        stage_number = data.get('stage_number')
        stage_name = data.get('stage_name')
        
        if not all([stage_key, stage_number, stage_name]):
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        # Store the stage override in the database
        from flask_app.models import PatientManifest
        from flask_app import db
        from datetime import datetime
        
        # Create or update the stage override record
        override_record = PatientManifest.query.filter_by(
            patient_id=patient_id,
            stage_key='stage_override'
        ).first()
        
        if override_record:
            override_record.is_completed = True
            override_record.stage_data = f'{{"override_stage_key": "{stage_key}", "override_stage_number": {stage_number}, "override_stage_name": "{stage_name}", "override_date": "{datetime.now().isoformat()}"}}'
            override_record.updated_at = datetime.now()
        else:
            override_record = PatientManifest(
                patient_id=patient_id,
                stage_key='stage_override',
                stage_name='Manual Stage Override',
                stage_number=0,  # Special number for override
                is_completed=True,
                completion_date=datetime.now(),
                stage_data=f'{{"override_stage_key": "{stage_key}", "override_stage_number": {stage_number}, "override_stage_name": "{stage_name}", "override_date": "{datetime.now().isoformat()}"}}',
                status_message=f"Manual override to {stage_name}"
            )
            db.session.add(override_record)
        
        db.session.commit()
        
        logger.info(f"Stage override set for patient {patient_id}: {stage_name} (Stage {stage_number})")
        
        return jsonify({
            'success': True,
            'message': f'Stage successfully overridden to: {stage_name}',
            'override_stage': {
                'stage_key': stage_key,
                'stage_number': stage_number,
                'stage_name': stage_name
            }
        })
        
    except Exception as e:
        logger.error(f"Error overriding stage for patient {patient_id}: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


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

    if request.method == 'POST':
        logger.debug('Processing upload new POST request')
        try:
            # Get clinic_id from form or assign dentist's default clinic
            clinic_id = request.form.get('clinic_id')
            if clinic_id:
                try:
                    clinic_id = int(clinic_id)
                    # Verify the dentist has access to this clinic
                    if not current_user.is_associated_with_clinic(clinic_id):
                        return jsonify({'success': False, 'message': 'You do not have permission to assign patients to this clinic.'}), 403
                except (ValueError, TypeError):
                    clinic_id = None
            
            # If no clinic_id provided, get dentist's first clinic
            if not clinic_id:
                dentist_clinics = current_user.clinics.all()
                if dentist_clinics:
                    clinic_id = dentist_clinics[0].id
                    logger.debug(f"Auto-assigning dentist's default clinic: {clinic_id}")
                else:
                    logger.warning(f"Dentist {current_user.id} has no associated clinics")

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
                'clinic_id': clinic_id,
            }

            logger.debug(f"Patient form data: {patient_data}")

            new_patient = Patient(**patient_data)
            db.session.add(new_patient)
            db.session.commit()
            logger.debug(f'Created new patient with ID: {new_patient.id}')
            
            # Log the assigned clinic and DSO for debugging
            if new_patient.clinic_id:
                clinic = Clinic.query.get(new_patient.clinic_id)
                if clinic:
                    logger.debug(f'Patient {new_patient.id} assigned to clinic: {clinic.name} (ID: {clinic.id})')
                    if clinic.dso_info:
                        logger.debug(f'Patient {new_patient.id} assigned to DSO: {clinic.dso_info.name} (ID: {clinic.dso_info.id})')
                else:
                    logger.warning(f'Patient {new_patient.id} assigned to non-existent clinic ID: {new_patient.clinic_id}')
            else:
                logger.warning(f'Patient {new_patient.id} created without clinic assignment')

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
            {
                "id": file.id, 
                "name": file.name, 
                "file_size": file.file_size or 0,
                "upload_date": file.upload_date.strftime('%Y-%m-%d %H:%M') if file.upload_date else None,
                "comment": file.comment
            }
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
        "file_category": getattr(report, 'file_category', None),
        "upload_date": report.upload_date.strftime('%Y-%m-%d %H:%M') if report.upload_date else None
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


def _extract_clinical_detail(observation_text: str, val) -> str:
    """
    Extract meaningful clinical details from observation text and value.
    Prioritizes detailed clinical information over simple yes/no responses.
    """
    # If the value is already detailed clinical information, use it
    if isinstance(val, str) and len(val) > 10 and not val.lower() in ['yes', 'no', 'present', 'absent', 'true', 'false']:
        return val
    
    # Look for detailed clinical information in the observation text
    observation_lower = observation_text.lower()
    
    # Extract specific clinical patterns
    clinical_patterns = [
        r'grade\s+\d+',  # Grade 1, Grade 2, etc.
        r'class\s+\d+',  # Class 1, Class 2, etc.
        r'\d+\s*mm',     # Measurements in mm
        r'\d+\s*cm',     # Measurements in cm
        r'elongated',    # Elongated structures
        r'hypertrophied', # Hypertrophied structures
        r'retrognathic', # Retrognathic position
        r'prognathic',   # Prognathic position
        r'posterior',    # Posterior position
        r'anterior',     # Anterior position
        r'deviated',     # Deviated structures
        r'enlarged',     # Enlarged structures
        r'thickened',    # Thickened structures
        r'narrowing',    # Narrowing
        r'obstruction',  # Obstruction
        r'collapse',     # Collapse
        r'flattening',   # Flattening
        r'irregularities', # Irregularities
        r'discontinuity', # Discontinuity
        r'morphology',   # Morphology
        r'contour',      # Contour
        r'cortical',     # Cortical
        r'articular',    # Articular
        r'condyle',      # Condyle
        r'uvula',        # Uvula findings
        r'tonsils',      # Tonsil findings
        r'septum',       # Septum findings
        r'turbinates',   # Turbinate findings
        r'epiglottis',   # Epiglottis findings
        r'larynx',       # Laryngeal findings
        r'trachea',      # Tracheal findings
    ]
    
    import re
    for pattern in clinical_patterns:
        matches = re.findall(pattern, observation_lower)
        if matches:
            # Return the first detailed clinical finding found
            return matches[0].title()
    
    # If no detailed clinical information found, return the original value
    return str(val)

def _parse_llm_phenotype_summary(phenotype_summary: str) -> dict:
    """
    Parse the LLM's phenotype_summary string to extract anatomical findings.
    This function looks for specific anatomical terms and extracts them into structured data.
    """
    if not phenotype_summary:
        return {}
    
    import re
    anatomical_findings = {
        'airway_findings': {},
        'tmj_findings': {},
        'bruxism': {},
        'other_findings': []
    }
    
    summary_lower = phenotype_summary.lower()
    
    # Extract TMJ findings
    if 'tmj' in summary_lower:
        tmj_details = []
        if 'pain' in summary_lower:
            tmj_details.append({'finding': 'tmj_pain', 'value': 'Present'})
        if 'clicking' in summary_lower:
            tmj_details.append({'finding': 'clicking', 'value': 'Present'})
        if 'locking' in summary_lower:
            tmj_details.append({'finding': 'locking', 'value': 'Present'})
        if 'dysfunction' in summary_lower or 'issues' in summary_lower:
            tmj_details.append({'finding': 'dysfunction', 'value': 'Present'})
        
        anatomical_findings['tmj_findings'] = {
            'present': True,
            'details': tmj_details if tmj_details else None,
            'source': 'LLM Phenotype Summary'
        }
    
    # Extract airway findings
    airway_findings = {}
    
    # Tongue findings
    if 'tongue' in summary_lower:
        if 'hypertrophic' in summary_lower or 'hypertrophied' in summary_lower:
            grade_match = re.search(r'grade\s*(\d+)', summary_lower)
            grade = grade_match.group(1) if grade_match else '2-3'
            airway_findings['tongue_position'] = {
                'present': True,
                'value': f'Hypertrophic Grade {grade}',
                'source': 'LLM Phenotype Summary'
            }
        elif 'posterior' in summary_lower:
            airway_findings['tongue_position'] = {
                'present': True,
                'value': 'Posterior Position',
                'source': 'LLM Phenotype Summary'
            }
        elif 'retrognathic' in summary_lower:
            airway_findings['tongue_position'] = {
                'present': True,
                'value': 'Retrognathic Position',
                'source': 'LLM Phenotype Summary'
            }
    
    # Soft palate findings
    if 'palate' in summary_lower:
        if 'elongated' in summary_lower:
            airway_findings['soft_palate'] = {
                'present': True,
                'value': 'Elongated Soft Palate',
                'source': 'LLM Phenotype Summary'
            }
        elif 'thickened' in summary_lower:
            airway_findings['soft_palate'] = {
                'present': True,
                'value': 'Thickened Soft Palate',
                'source': 'LLM Phenotype Summary'
            }
    
    # Mallampati findings
    if 'mallampati' in summary_lower:
        class_match = re.search(r'class\s*(\d+)', summary_lower)
        if class_match:
            airway_findings['mallampati_class'] = {
                'present': True,
                'value': f'Mallampati Class {class_match.group(1)}',
                'source': 'LLM Phenotype Summary'
            }
    
    # Wall collapse findings
    if 'collapse' in summary_lower:
        if 'medial' in summary_lower:
            airway_findings['medial_wall_collapse'] = 'Medial Wall Collapse'
        elif 'lateral' in summary_lower:
            airway_findings['lateral_wall_collapse'] = 'Lateral Wall Collapse'
    
    # Velopharyngeal findings
    if 'velopharyngeal' in summary_lower:
        airway_findings['velopharyngeal_obstruction'] = {
            'present': True,
            'value': 'Velopharyngeal Obstruction',
            'source': 'LLM Phenotype Summary'
        }
    
    # Nasal findings
    if 'nasal' in summary_lower:
        if 'obstruction' in summary_lower:
            airway_findings['nasal_obstruction'] = {
                'present': True,
                'value': 'Nasal Obstruction',
                'source': 'LLM Phenotype Summary'
            }
        elif 'septum' in summary_lower:
            airway_findings['nasal_septum'] = {
                'present': True,
                'value': 'Nasal Septum Involvement',
                'source': 'LLM Phenotype Summary'
            }
    
    # Add airway findings if any found
    if airway_findings:
        anatomical_findings['airway_findings'] = airway_findings
    
    # Extract other findings
    other_findings = []
    if 'retropalatal' in summary_lower:
        other_findings.append({'finding': 'retropalatal_obstruction', 'value': 'Present'})
    if 'retropositioned' in summary_lower:
        other_findings.append({'finding': 'retropositioned_structures', 'value': 'Present'})
    if 'narrowing' in summary_lower:
        other_findings.append({'finding': 'airway_narrowing', 'value': 'Present'})
    
    if other_findings:
        anatomical_findings['other_findings'] = other_findings
    
    return anatomical_findings

def _build_phenotype_from_observations(obs: dict) -> dict:
    """
    Build comprehensive phenotype from clinical observations with canonical schema
    """
    phenotype = {
        # Canonical Sleep Study Schema (Single Source of Truth)
        'sleep_study': {
            'type': None,                    # "HST" | "PSG"
            'date': None,
            'AHI': None,                     # <-- Always numeric (float)
            'SpO2_nadir': None,              # integer (percent)
            'ODI': None,                     # optional
            'severity': 'unknown',           # derived (see _ahi_to_severity)
            'source_doc_id': None
        },
        # Legacy fields for backward compatibility
        'osa_assessment': {'AHI': None, 'severity': 'unknown', 'policy_category': 'unknown'},
        'sleep_study_data': {'SpO2_nadir': None, 'hypoxia_severity': 'unknown'},
        'clinical_findings': {},
        'treatment_history': {'cpap_intolerance': False, 'cpap_intolerance_evidence': ''},
        'risk_factors': {'smoking': None, 'alcohol': None, 'medications': []},
        'comorbidities': {'BMI': None},
        'anatomical_findings': {'tmj_findings': {'present': False, 'pain_vas': None, 'clicking': False, 'locking': False}},
        'symptom_assessment': {'snoring': None, 'daytime_sleepiness': None, 'ISI_score': None, 'fatigue': None},
        'treatment_preferences': {},
        'raw_observations': [],
        'data_quality': [],
        'feature_schema_version': 2
    }
    
    def _extract_sleep_study_data(obs: dict) -> dict:
        """Extract sleep study data from observations and populate timeline structure"""
        sleep_data = {}
        timeline_data = {
            'reports': [],
            'sleep_studies': []
        }
        
        for source, items in (obs or {}).items():
            for item in (items or []):
                observation_text = str(item.get('observation', '')).lower()
                value = item.get('value')
                date = item.get('date')
                file_name = item.get('file_name', '')
                episode_id = item.get('episode_id', '')
                
                # Debug logging for sleep study observations
                if any(keyword in observation_text for keyword in ['ahi', 'spo2', 'odi', 'efficiency', 'sleep']):
                    logger.debug(f"Processing sleep study observation: {observation_text} = {value}")
                
                # Enhanced AHI extraction - handle multiple formats
                if 'ahi' in observation_text and 'central' not in observation_text:
                    try:
                        if isinstance(value, (int, float)):
                            sleep_data['AHI'] = float(value)
                        else:
                            import re
                            # Try to extract from the observation text itself
                            ahi_patterns = [
                                r'ahi[:\s]*(\d+(?:\.\d+)?)',
                                r'(\d+(?:\.\d+)?)\s*(?:events?/hour?|/hour?|per hour?)',
                                r'apnea[-\s]?hypopnea[-\s]?index[:\s]*(\d+(?:\.\d+)?)',
                                r'(\d+(?:\.\d+)?)\s*(?:events?|episodes?)'
                            ]
                            
                            # First try the observation text
                            for pattern in ahi_patterns:
                                match = re.search(pattern, observation_text, flags=re.IGNORECASE)
                                if match:
                                    sleep_data['AHI'] = float(match.group(1))
                                    break
                            
                            # If not found in observation, try the value
                            if 'AHI' not in sleep_data and value:
                                value_str = str(value)
                                for pattern in ahi_patterns:
                                    match = re.search(pattern, value_str, flags=re.IGNORECASE)
                                    if match:
                                        sleep_data['AHI'] = float(match.group(1))
                                        break
                    except (ValueError, TypeError):
                        pass
                
                # Enhanced SpO2 nadir extraction
                elif any(keyword in observation_text for keyword in ['spo2', 'oxygen', 'desaturation', 'o2']):
                    if 'nadir' in observation_text or 'lowest' in observation_text:
                        try:
                            if isinstance(value, (int, float)):
                                sleep_data['SpO2_nadir'] = int(value)
                            else:
                                import re
                                # Try to extract from observation text
                                spo2_patterns = [
                                    r'o2[:\s]*nadir[:\s]*(\d+)',
                                    r'spo2[:\s]*nadir[:\s]*(\d+)',
                                    r'oxygen[:\s]*nadir[:\s]*(\d+)',
                                    r'(\d+)%?\s*(?:nadir|lowest)',
                                    r'(\d+)%?\s*(?:oxygen|spo2)'
                                ]
                                
                                for pattern in spo2_patterns:
                                    match = re.search(pattern, observation_text, flags=re.IGNORECASE)
                                    if match:
                                        sleep_data['SpO2_nadir'] = int(match.group(1))
                                        break
                                
                                # If not found in observation, try the value
                                if 'SpO2_nadir' not in sleep_data and value:
                                    value_str = str(value)
                                    for pattern in spo2_patterns:
                                        match = re.search(pattern, value_str, flags=re.IGNORECASE)
                                        if match:
                                            sleep_data['SpO2_nadir'] = int(match.group(1))
                                            break
                        except (ValueError, TypeError):
                            pass
                
                # ODI extraction
                elif 'odi' in observation_text:
                    try:
                        if isinstance(value, (int, float)):
                            sleep_data['ODI'] = float(value)
                        else:
                            import re
                            # Enhanced ODI patterns
                            odi_patterns = [
                                r'odi[:\s]*(\d+(?:\.\d+)?)',
                                r'oxygen[:\s]*desaturation[:\s]*index[:\s]*(\d+(?:\.\d+)?)',
                                r'(\d+(?:\.\d+)?)\s*(?:desaturation|odi)',
                                r'oxygen\s*desaturation\s*index[:\s]*(\d+(?:\.\d+)?)',
                                r'(\d+(?:\.\d+)?)\s*oxygen\s*desaturation'
                            ]
                            
                            # Try observation text first
                            for pattern in odi_patterns:
                                match = re.search(pattern, observation_text, flags=re.IGNORECASE)
                                if match:
                                    sleep_data['ODI'] = float(match.group(1))
                                    break
                            
                            # If not found in observation, try the value
                            if 'ODI' not in sleep_data:
                                value_str = str(value)
                                for pattern in odi_patterns:
                                    match = re.search(pattern, value_str, flags=re.IGNORECASE)
                                    if match:
                                        sleep_data['ODI'] = float(match.group(1))
                                        break
                    except (ValueError, TypeError):
                        pass
                
                # Sleep efficiency extraction
                elif 'sleep efficiency' in observation_text or 'efficiency' in observation_text:
                    try:
                        if isinstance(value, (int, float)):
                            sleep_data['sleep_efficiency'] = float(value)
                        else:
                            import re
                            # Enhanced sleep efficiency patterns
                            efficiency_patterns = [
                                r'(\d+(?:\.\d+)?)%',  # 85%
                                r'sleep\s+efficiency[:\s]*(\d+(?:\.\d+)?)',
                                r'efficiency[:\s]*(\d+(?:\.\d+)?)',
                                r'(\d+(?:\.\d+)?)\s*(?:efficiency|%)'
                            ]
                            
                            # Try observation text first
                            for pattern in efficiency_patterns:
                                match = re.search(pattern, observation_text, flags=re.IGNORECASE)
                                if match:
                                    sleep_data['sleep_efficiency'] = float(match.group(1))
                                    break
                            
                            # If not found in observation, try the value
                            if 'sleep_efficiency' not in sleep_data:
                                value_str = str(value)
                                for pattern in efficiency_patterns:
                                    match = re.search(pattern, value_str, flags=re.IGNORECASE)
                                    if match:
                                        sleep_data['sleep_efficiency'] = float(match.group(1))
                                        break
                    except (ValueError, TypeError):
                        pass
                
                # Sleep duration extraction
                elif 'sleep duration' in observation_text or 'duration' in observation_text:
                    try:
                        sleep_data['sleep_duration'] = str(value)
                    except (ValueError, TypeError):
                        pass
                
                # Snoring extraction from sleep study
                elif 'snoring' in observation_text:
                    try:
                        if isinstance(value, (int, float)):
                            sleep_data['snoring_db'] = float(value)
                        else:
                            import re
                            # Try to extract snoring values
                            snoring_patterns = [
                                r'(\d+(?:\.\d+)?)\s*(?:db|decibel)',
                                r'snoring[:\s]*(\d+(?:\.\d+)?)',
                                r'(\d+(?:\.\d+)?)\s*snoring'
                            ]
                            
                            for pattern in snoring_patterns:
                                match = re.search(pattern, observation_text, flags=re.IGNORECASE)
                                if match:
                                    sleep_data['snoring_db'] = float(match.group(1))
                                    break
                            
                            # If no numeric value found, store the text
                            if 'snoring_db' not in sleep_data:
                                sleep_data['snoring_status'] = str(value)
                    except (ValueError, TypeError):
                        pass
                
                # Sleep study type
                elif 'sleep study' in observation_text or 'polysomnography' in observation_text:
                    if 'home' in observation_text:
                        sleep_data['type'] = 'HST'
                    elif 'lab' in observation_text or 'in-lab' in observation_text:
                        sleep_data['type'] = 'PSG'
        
        return sleep_data
    
    def _ahi_to_severity(ahi: float) -> str:
        """Convert AHI to severity using AASM cutoffs"""
        if ahi is None:
            return "unknown"
        if ahi < 5:
            return "normal"
        if ahi < 15:
            return "mild"
        if ahi < 30:
            return "moderate"
        return "severe"
    
    def _determine_policy_eligibility(phenotype: dict) -> dict:
        """Determine policy eligibility based on phenotype data"""
        eligibility = {
            'osa_confirmed': False,
            'treatment_eligible': False,
            'oral_appliance_candidate': False,
            'requires_specialist_referral': False,
            'risk_level': 'low',
            'recommended_pathway': 'standard'
        }
        
        # Check OSA confirmation
        if phenotype.get('osa_assessment', {}).get('AHI'):
            ahi = phenotype['osa_assessment']['AHI']
            if isinstance(ahi, (int, float)) and ahi >= 5:
                eligibility['osa_confirmed'] = True
            elif isinstance(ahi, str) and any(word in ahi.lower() for word in ['present', 'positive', 'abnormal']):
                eligibility['osa_confirmed'] = True
        
        # Check treatment eligibility
        if eligibility['osa_confirmed']:
            eligibility['treatment_eligible'] = True
            
            # Check for CPAP intolerance (makes oral appliance more likely)
            if phenotype.get('treatment_history', {}).get('cpap_intolerance'):
                eligibility['oral_appliance_candidate'] = True
                eligibility['recommended_pathway'] = 'oral_appliance_first'
            
            # Check severity for specialist referral
            severity = phenotype.get('osa_assessment', {}).get('severity')
            if severity in ['severe']:
                eligibility['requires_specialist_referral'] = True
                eligibility['risk_level'] = 'high'
            elif severity in ['moderate']:
                eligibility['risk_level'] = 'medium'
        
        # Check anatomical contraindications
        if phenotype.get('anatomical_findings', {}).get('tmj_findings', {}).get('present'):
            tmj_pain = phenotype['anatomical_findings']['tmj_findings'].get('pain_vas', 0)
            if isinstance(tmj_pain, (int, float)) and tmj_pain > 6:
                eligibility['oral_appliance_candidate'] = False
                eligibility['recommended_pathway'] = 'specialist_consultation'
        
        return eligibility
    
    # Extract sleep study data with robust parsing
    sleep_study_data = _extract_sleep_study_data(obs)
    phenotype['sleep_study'].update(sleep_study_data)
    
    # Additional parsing for observations in "Key: Value" format
    def _parse_key_value_observations(obs: dict) -> dict:
        """Parse observations that are in 'Key: Value' format"""
        additional_data = {}
        
        for source, items in (obs or {}).items():
            for item in (items or []):
                observation_text = str(item.get('observation', ''))
                value = item.get('value')
                
                # Handle "O2 Nadir: 77%" format
                if ':' in observation_text:
                    import re
                    # Extract key-value pairs from observation text
                    key_value_match = re.match(r'^([^:]+):\s*(.+)$', observation_text.strip())
                    if key_value_match:
                        key = key_value_match.group(1).strip().lower()
                        val = key_value_match.group(2).strip()
                        
                        # Parse specific sleep study values
                        if 'o2 nadir' in key or 'spo2 nadir' in key:
                            try:
                                # Extract number from "77%" or "77"
                                num_match = re.search(r'(\d+)', val)
                                if num_match:
                                    additional_data['SpO2_nadir'] = int(num_match.group(1))
                            except:
                                pass
                        
                        elif 'ahi' in key:
                            try:
                                # Extract number from "59.4 (Severe OSA)"
                                num_match = re.search(r'(\d+(?:\.\d+)?)', val)
                                if num_match:
                                    additional_data['AHI'] = float(num_match.group(1))
                            except:
                                pass
                        
                        elif 'odi' in key:
                            try:
                                # Extract number from "30.1 (401 oxygen desaturation events)"
                                num_match = re.search(r'(\d+(?:\.\d+)?)', val)
                                if num_match:
                                    additional_data['ODI'] = float(num_match.group(1))
                            except:
                                pass
                        
                        elif 'sleep efficiency' in key:
                            try:
                                # Extract percentage from "93.5%"
                                num_match = re.search(r'(\d+(?:\.\d+)?)%', val)
                                if num_match:
                                    additional_data['sleep_efficiency'] = float(num_match.group(1))
                            except:
                                pass
                        
                        elif 'sleep duration' in key:
                            additional_data['sleep_duration'] = val
        
        return additional_data
    # Apply additional parsing
    additional_sleep_data = _parse_key_value_observations(obs)
    phenotype['sleep_study'].update(additional_sleep_data)
    
    # Derive severity centrally using AASM cutoffs
    try:
        ahi_value = phenotype['sleep_study'].get('AHI')
        if isinstance(ahi_value, dict):
            logger.warning(f"AHI value is a dictionary instead of number: {ahi_value}")
            ahi_value = None
        phenotype['sleep_study']['severity'] = _ahi_to_severity(ahi_value)
    except Exception as e:
        logger.error(f"Error deriving severity from AHI: {e}")
        phenotype['sleep_study']['severity'] = 'unknown'
    
    # Build legacy fields for backward compatibility
    try:
        for source, items in (obs or {}).items():
            for item in (items or []):
                name = (item.get('observation') or '').lower()
                val = item.get('value')
                source_name = source
                
                # Debug logging for key observations
                if any(keyword in name for keyword in ['ahi', 'spo2', 'odi', 'bmi', 'snoring', 'efficiency']):
                    logger.debug(f"Processing observation: {name} = {val} (source: {source_name})")
                
                # Store raw observation for reference
                phenotype['raw_observations'].append({
                    'observation': item.get('observation', ''),
                    'value': val,
                    'source': source_name,
                    'evidence': item.get('evidence', ''),
                    'confidence': item.get('confidence', 0)
                })
                
                # Legacy OSA Assessment (for backward compatibility)
                if 'ahi' in name and 'central' not in name:
                    try:
                        val_str = str(val).strip()
                        
                        # First, try to parse as a direct number
                        if isinstance(val, (int, float)):
                            ahi_value = float(val)
                            phenotype['osa_assessment']['AHI'] = ahi_value
                        else:
                            # Try regex pattern for text values
                            import re
                            numbers = re.findall(r'\d+(?:\.\d+)?', val_str)
                            
                            if numbers:
                                ahi_value = float(numbers[0])
                                phenotype['osa_assessment']['AHI'] = ahi_value
                            else:
                                # Check for text indicators
                                if any(word in val_str.lower() for word in ['yes', 'positive', 'present', 'abnormal']):
                                    phenotype['osa_assessment']['AHI'] = 'Present (value not specified)'
                                    phenotype['osa_assessment']['severity'] = 'unknown'
                                else:
                                    phenotype['osa_assessment']['AHI'] = val_str
                                    phenotype['osa_assessment']['severity'] = 'unknown'
                        
                        # Set severity based on AHI value (only if we have a numeric value)
                        if isinstance(phenotype['osa_assessment']['AHI'], (int, float)):
                            ahi_value = phenotype['osa_assessment']['AHI']
                            if ahi_value < 5:
                                phenotype['osa_assessment']['severity'] = 'normal'
                                phenotype['osa_assessment']['policy_category'] = 'no_osa'
                            elif ahi_value < 15:
                                phenotype['osa_assessment']['severity'] = 'mild'
                                phenotype['osa_assessment']['policy_category'] = 'mild_osa'
                            elif ahi_value < 30:
                                phenotype['osa_assessment']['severity'] = 'moderate'
                                phenotype['osa_assessment']['policy_category'] = 'moderate_osa'
                            else:
                                phenotype['osa_assessment']['severity'] = 'severe'
                                phenotype['osa_assessment']['policy_category'] = 'severe_osa'
                                
                    except Exception as e:
                        logger.error(f"Error parsing AHI value '{val}': {e}")
                        phenotype['osa_assessment']['AHI'] = val
                        phenotype['osa_assessment']['severity'] = 'unknown'
                
                # 2. SLEEP STUDY DATA (Policy Pathway: Diagnostic Evidence)
                elif any(keyword in name for keyword in ['spo2', 'oxygen', 'desaturation']):
                    if 'nadir' in name or 'lowest' in name:
                        try:
                            spo2_value = float(str(val).replace('%','').split()[0])
                            phenotype['sleep_study_data']['SpO2_nadir'] = spo2_value
                            
                            # Map to policy hypoxia categories
                            if spo2_value < 88:
                                phenotype['sleep_study_data']['hypoxia_severity'] = 'severe'
                            elif spo2_value < 92:
                                phenotype['sleep_study_data']['hypoxia_severity'] = 'moderate'
                            elif spo2_value < 95:
                                phenotype['sleep_study_data']['hypoxia_severity'] = 'mild'
                            else:
                                phenotype['sleep_study_data']['hypoxia_severity'] = 'normal'
                        except Exception:
                            phenotype['sleep_study_data']['SpO2_nadir'] = val
                    else:
                        phenotype['sleep_study_data'][f"oxygen_{name.replace(' ', '_')}"] = val
                
                elif 'rera' in name and 'index' in name:
                    try:
                        phenotype['sleep_study_data']['RERA_index'] = float(str(val).split()[0])
                    except:
                        phenotype['sleep_study_data']['RERA_index'] = val
                
                elif 'airflow' in name and 'limitation' in name:
                    try:
                        phenotype['sleep_study_data']['airflow_limitation_pct'] = float(str(val).replace('%','').split()[0])
                    except:
                        phenotype['sleep_study_data']['airflow_limitation_pct'] = val
                
                # 3. TREATMENT HISTORY (Policy Pathway: Treatment Eligibility)
                elif 'cpap' in name and ('intoler' in name or 'refuse' in name or 'fail' in name):
                    phenotype['treatment_history']['cpap_intolerance'] = True if str(val).strip().lower() in ['true','yes','y','1'] else bool(val)
                    phenotype['treatment_history']['cpap_intolerance_evidence'] = item.get('evidence', '')
                
                elif 'oral_appliance' in name or 'mandibular' in name:
                    phenotype['treatment_history']['oral_appliance_experience'] = val
                
                elif 'surgery' in name and ('sleep' in name or 'osa' in name):
                    phenotype['treatment_history']['surgical_history'] = val
                
                # 4. ANATOMICAL FINDINGS (Policy Pathway: Treatment Selection)
                elif 'tmj' in name:
                    phenotype['anatomical_findings']['tmj_findings'] = phenotype['anatomical_findings'].get('tmj_findings', {})
                    phenotype['anatomical_findings']['tmj_findings']['present'] = True
                    
                    # Store the actual TMJ finding details
                    if 'details' not in phenotype['anatomical_findings']['tmj_findings']:
                        phenotype['anatomical_findings']['tmj_findings']['details'] = []
                    
                    # Add the specific finding
                    finding_detail = {
                        'finding': name,
                        'value': val,
                        'source': source_name
                    }
                    phenotype['anatomical_findings']['tmj_findings']['details'].append(finding_detail)
                    
                    if 'pain' in name or 'vas' in name:
                        try:
                            pain_value = float(str(val).split()[0])
                            phenotype['anatomical_findings']['tmj_findings']['pain_vas'] = pain_value
                        except:
                            pass
                    elif 'click' in name:
                        phenotype['anatomical_findings']['tmj_findings']['clicking'] = True if str(val).strip().lower() in ['true','yes','y','1'] else bool(val)
                    elif 'lock' in name:
                        phenotype['anatomical_findings']['tmj_findings']['locking'] = True if str(val).strip().lower() in ['true','yes','y','1'] else bool(val)
                
                elif 'nasal' in name and ('obstruction' in name or 'valve' in name):
                    phenotype['anatomical_findings']['nasal_obstruction'] = {
                        'present': True,
                        'source': 'cbct' if 'cbct' in source.lower() else 'clinical',
                        'value': val
                    }
                
                elif 'primary' in name and 'narrow' in name and 'site' in name:
                    phenotype['anatomical_findings']['primary_narrowing_site'] = str(val)
                
                # Enhanced airway findings extraction - look in observation text and value
                elif any(keyword in observation_text.lower() for keyword in ['velopharyngeal', 'velopharynx', 'pharyngeal']):
                    if 'airway_findings' not in phenotype['anatomical_findings']:
                        phenotype['anatomical_findings']['airway_findings'] = {}
                    # Extract detailed clinical information from observation text
                    clinical_detail = _extract_clinical_detail(observation_text, val)
                    phenotype['anatomical_findings']['airway_findings']['velopharyngeal_obstruction'] = {
                        'present': True,
                        'value': clinical_detail,
                        'source': source_name
                    }
                
                elif any(keyword in observation_text.lower() for keyword in ['tongue', 'lingual']) and any(pos_keyword in observation_text.lower() for pos_keyword in ['position', 'posterior', 'anterior', 'base', 'hypertrophied']):
                    if 'airway_findings' not in phenotype['anatomical_findings']:
                        phenotype['anatomical_findings']['airway_findings'] = {}
                    # Extract detailed clinical information from observation text
                    clinical_detail = _extract_clinical_detail(observation_text, val)
                    phenotype['anatomical_findings']['airway_findings']['tongue_position'] = clinical_detail
                
                elif any(keyword in observation_text.lower() for keyword in ['soft palate', 'palate', 'uvula']) and any(detail_keyword in observation_text.lower() for detail_keyword in ['elongated', 'long', 'thickened', 'enlarged']):
                    if 'airway_findings' not in phenotype['anatomical_findings']:
                        phenotype['anatomical_findings']['airway_findings'] = {}
                    # Extract detailed clinical information from observation text
                    clinical_detail = _extract_clinical_detail(observation_text, val)
                    phenotype['anatomical_findings']['airway_findings']['soft_palate'] = clinical_detail
                
                elif 'airway' in name and ('obstruction' in name or 'narrowing' in name):
                    if 'airway_findings' not in phenotype['anatomical_findings']:
                        phenotype['anatomical_findings']['airway_findings'] = {}
                    phenotype['anatomical_findings']['airway_findings']['primary_obstruction_level'] = str(val)
                
                elif 'bruxism' in name or 'grinding' in name:
                    phenotype['anatomical_findings']['bruxism'] = {
                        'present': True,
                        'value': val,
                        'source': source_name
                    }
                
                # Additional comprehensive anatomical findings - look in observation text
                elif any(keyword in observation_text.lower() for keyword in ['retrognathia', 'micrognathia', 'mandible', 'mandibular']):
                    if 'airway_findings' not in phenotype['anatomical_findings']:
                        phenotype['anatomical_findings']['airway_findings'] = {}
                    clinical_detail = _extract_clinical_detail(observation_text, val)
                    phenotype['anatomical_findings']['airway_findings']['mandibular_position'] = clinical_detail
                
                elif any(keyword in observation_text.lower() for keyword in ['maxilla', 'maxillary']):
                    if 'airway_findings' not in phenotype['anatomical_findings']:
                        phenotype['anatomical_findings']['airway_findings'] = {}
                    clinical_detail = _extract_clinical_detail(observation_text, val)
                    phenotype['anatomical_findings']['airway_findings']['maxillary_position'] = clinical_detail
                
                elif any(keyword in observation_text.lower() for keyword in ['uvula', 'uvular']):
                    if 'airway_findings' not in phenotype['anatomical_findings']:
                        phenotype['anatomical_findings']['airway_findings'] = {}
                    clinical_detail = _extract_clinical_detail(observation_text, val)
                    phenotype['anatomical_findings']['airway_findings']['uvula_findings'] = clinical_detail
                
                elif any(keyword in observation_text.lower() for keyword in ['tonsils', 'tonsillar']):
                    if 'airway_findings' not in phenotype['anatomical_findings']:
                        phenotype['anatomical_findings']['airway_findings'] = {}
                    clinical_detail = _extract_clinical_detail(observation_text, val)
                    phenotype['anatomical_findings']['airway_findings']['tonsillar_findings'] = clinical_detail
                
                elif any(keyword in observation_text.lower() for keyword in ['adenoids', 'adenoidal']):
                    if 'airway_findings' not in phenotype['anatomical_findings']:
                        phenotype['anatomical_findings']['airway_findings'] = {}
                    clinical_detail = _extract_clinical_detail(observation_text, val)
                    phenotype['anatomical_findings']['airway_findings']['adenoidal_findings'] = clinical_detail
                
                elif any(keyword in observation_text.lower() for keyword in ['septum', 'septal', 'deviated']):
                    if 'airway_findings' not in phenotype['anatomical_findings']:
                        phenotype['anatomical_findings']['airway_findings'] = {}
                    clinical_detail = _extract_clinical_detail(observation_text, val)
                    phenotype['anatomical_findings']['airway_findings']['nasal_septum'] = clinical_detail
                
                elif any(keyword in observation_text.lower() for keyword in ['turbinate', 'turbinates']):
                    if 'airway_findings' not in phenotype['anatomical_findings']:
                        phenotype['anatomical_findings']['airway_findings'] = {}
                    clinical_detail = _extract_clinical_detail(observation_text, val)
                    phenotype['anatomical_findings']['airway_findings']['turbinate_findings'] = clinical_detail
                
                elif any(keyword in name for keyword in ['hyoid', 'hyoid bone']):
                    if 'airway_findings' not in phenotype['anatomical_findings']:
                        phenotype['anatomical_findings']['airway_findings'] = {}
                    phenotype['anatomical_findings']['airway_findings']['hyoid_position'] = str(val)
                
                elif any(keyword in name for keyword in ['epiglottis', 'epiglottic']):
                    if 'airway_findings' not in phenotype['anatomical_findings']:
                        phenotype['anatomical_findings']['airway_findings'] = {}
                    phenotype['anatomical_findings']['airway_findings']['epiglottic_findings'] = str(val)
                
                elif any(keyword in name for keyword in ['larynx', 'laryngeal']):
                    if 'airway_findings' not in phenotype['anatomical_findings']:
                        phenotype['anatomical_findings']['airway_findings'] = {}
                    phenotype['anatomical_findings']['airway_findings']['laryngeal_findings'] = str(val)
                
                elif any(keyword in name for keyword in ['trachea', 'tracheal']):
                    if 'airway_findings' not in phenotype['anatomical_findings']:
                        phenotype['anatomical_findings']['airway_findings'] = {}
                    phenotype['anatomical_findings']['airway_findings']['tracheal_findings'] = str(val)
                
                # 5. SYMPTOM ASSESSMENT (Policy Pathway: Clinical Indications)
                elif 'snoring' in name:
                    # Enhanced snoring extraction
                    if isinstance(val, (int, float)):
                        phenotype['symptom_assessment']['snoring'] = str(val)
                    else:
                        # Try to extract meaningful snoring information
                        val_str = str(val).lower()
                        if any(word in val_str for word in ['yes', 'present', 'positive', 'true', '1']):
                            phenotype['symptom_assessment']['snoring'] = 'Present'
                        elif any(word in val_str for word in ['no', 'absent', 'negative', 'false', '0']):
                            phenotype['symptom_assessment']['snoring'] = 'Absent'
                        else:
                            phenotype['symptom_assessment']['snoring'] = val
                
                elif 'daytime' in name and 'sleepiness' in name:
                    phenotype['symptom_assessment']['daytime_sleepiness'] = val
                
                elif 'insomnia' in name or 'isi' in name:
                    try:
                        phenotype['symptom_assessment']['ISI_score'] = float(str(val).split()[0])
                    except:
                        phenotype['symptom_assessment']['ISI_score'] = val
                
                elif 'fatigue' in name:
                    phenotype['symptom_assessment']['fatigue'] = val
                
                # 6. COMORBIDITIES (Policy Pathway: Risk Assessment)
                elif any(keyword in name for keyword in ['diabetes', 'hypertension', 'cardiac', 'heart']):
                    phenotype['comorbidities'][name.replace(' ', '_')] = val
                
                elif 'bmi' in name or 'weight' in name:
                    try:
                        # Enhanced BMI extraction - handle multiple formats
                        if isinstance(val, (int, float)):
                            phenotype['comorbidities']['BMI'] = float(val)
                        else:
                            import re
                            # Try to extract BMI from various formats
                            bmi_patterns = [
                                r'bmi[:\s]*(\d+(?:\.\d+)?)',
                                r'(\d+(?:\.\d+)?)\s*(?:bmi|kg/m2|kg/m²)',
                                r'body\s*mass\s*index[:\s]*(\d+(?:\.\d+)?)',
                                r'(\d+(?:\.\d+)?)\s*(?:body\s*mass\s*index)'
                            ]
                            
                            # First try the observation text
                            for pattern in bmi_patterns:
                                match = re.search(pattern, observation_text, flags=re.IGNORECASE)
                                if match:
                                    phenotype['comorbidities']['BMI'] = float(match.group(1))
                                    break
                            
                            # If not found in observation, try the value
                            if 'BMI' not in phenotype['comorbidities'] or phenotype['comorbidities']['BMI'] is None:
                                value_str = str(val)
                                for pattern in bmi_patterns:
                                    match = re.search(pattern, value_str, flags=re.IGNORECASE)
                                    if match:
                                        phenotype['comorbidities']['BMI'] = float(match.group(1))
                                        break
                            
                            # If still not found, try to extract any number from the value
                            if 'BMI' not in phenotype['comorbidities'] or phenotype['comorbidities']['BMI'] is None:
                                numbers = re.findall(r'\d+(?:\.\d+)?', str(val))
                                if numbers:
                                    # Take the first number that looks like a BMI (between 15-60)
                                    for num in numbers:
                                        bmi_val = float(num)
                                        if 15 <= bmi_val <= 60:
                                            phenotype['comorbidities']['BMI'] = bmi_val
                                            break
                    except Exception as e:
                        logger.warning(f"Error parsing BMI value '{val}': {e}")
                        # Don't store non-numeric BMI values
                        if isinstance(val, (int, float)):
                            phenotype['comorbidities']['BMI'] = float(val)
                
                # 7. RISK FACTORS (Policy Pathway: Treatment Safety)
                elif 'smoking' in name:
                    phenotype['risk_factors']['smoking'] = val
                
                elif 'alcohol' in name:
                    phenotype['risk_factors']['alcohol'] = val
                
                elif 'medication' in name:
                    phenotype['risk_factors']['medications'] = phenotype['risk_factors'].get('medications', [])
                    phenotype['risk_factors']['medications'].append(val)
                
                # 8. TREATMENT PREFERENCES (Policy Pathway: Patient-Centered Care)
                elif 'preference' in name or 'choice' in name:
                    phenotype['treatment_preferences'][name.replace(' ', '_')] = val
                
                # 9. CLINICAL FINDINGS (Policy Pathway: Comprehensive Assessment)
                else:
                    # Map to clinical findings category
                    phenotype['clinical_findings'][name.replace(' ', '_')] = {
                        'value': val,
                        'source': source_name,
                        'evidence': item.get('evidence', ''),
                        'confidence': item.get('confidence', 0)
                    }
                    
                    # Also try to map to anatomical findings if it seems like an anatomical observation
                    if any(keyword in name for keyword in ['position', 'size', 'shape', 'length', 'width', 'thickness', 'obstruction', 'narrowing', 'enlarged', 'reduced', 'abnormal', 'normal']):
                        if 'anatomical_findings' not in phenotype:
                            phenotype['anatomical_findings'] = {}
                        if 'other_findings' not in phenotype['anatomical_findings']:
                            phenotype['anatomical_findings']['other_findings'] = []
                        phenotype['anatomical_findings']['other_findings'].append({
                            'finding': name,
                            'value': val,
                            'source': source_name
                        })
                    
    except Exception as e:
        logger.error(f"Error building phenotype: {e}")
    
    # Add metadata
    phenotype['total_observations'] = len(phenotype['raw_observations'])
    phenotype['data_sources'] = list(set([obs['source'] for obs in phenotype['raw_observations']]))
    
    # Determine policy eligibility based on phenotype
    phenotype['policy_eligibility'] = _determine_policy_eligibility(phenotype)
    
    return phenotype


def build_enhanced_patient_packet(patient_id, phenotype=None, stage_manifest=None, completed_stages=0, progress_percentage=0, eligible_actions=None):
    """
    Build enhanced patient packet with comprehensive schema including operational data
    """
    from datetime import datetime
    try:
        logger.info(f"build_enhanced_patient_packet: Starting for patient {patient_id}")
        logger.info(f"build_enhanced_patient_packet: Parameters - phenotype={phenotype is not None}, stage_manifest_len={len(stage_manifest) if stage_manifest else 0}, completed_stages={completed_stages}")
        
        # Get patient details
        logger.info(f"build_enhanced_patient_packet: Fetching patient details for {patient_id}")
        try:
            patient_details = fetch_patient_details(patient_id)
            logger.info(f"build_enhanced_patient_packet: fetch_patient_details returned: {type(patient_details)}")
            if patient_details:
                logger.info(f"build_enhanced_patient_packet: patient_details keys: {list(patient_details.keys()) if isinstance(patient_details, dict) else 'Not a dict'}")
            
            patient = patient_details.get('patient') if patient_details else None
            
            if not patient:
                logger.error(f"Patient {patient_id} not found in fetch_patient_details")
                return None
        except Exception as e:
            logger.error(f"Error in fetch_patient_details for patient {patient_id}: {e}")
            return None
        
        logger.info(f"build_enhanced_patient_packet: Found patient {patient.id} - {patient.name}")
        
        # Calculate age if DOB is available
        age = None
        if hasattr(patient, 'dob') and patient.dob:
            try:
                # Convert patient.dob to datetime.date if it's a datetime
                if isinstance(patient.dob, datetime):
                    dob_date = patient.dob.date()
                else:
                    dob_date = patient.dob
                age = (datetime.now().date() - dob_date).days // 365
            except Exception as e:
                logger.warning(f"Error calculating age for patient {patient.id}: {e}")
                age = None
        
        # Build enhanced packet
        enhanced_packet = {
            "patient": {
                "id": str(patient.id),
                "sex": patient.gender or "unknown",
                "age": age,
                "demographics": {
                    "name": patient.name or "Unknown",
                    "date_of_birth": patient.dob.strftime('%Y-%m-%d') if patient.dob else None,
                    "address": patient.address or "",
                    "phone": patient.phone or "",
                    "email": patient.email or ""
                }
            },
            "policy_context": {
                "policy_version": "osa_policy_v2",
                "base_policy": "osa_policy_base_v2"
            },
            "sleep_study": {
                "type": phenotype.get('sleep_study', {}).get('type', 'unknown') if phenotype else 'unknown',
                "date": phenotype.get('sleep_study', {}).get('date') if phenotype else None,
                "AHI": phenotype.get('sleep_study', {}).get('AHI') if phenotype else None,
                "SpO2_nadir": phenotype.get('sleep_study', {}).get('SpO2_nadir') if phenotype else None,
                "ODI": phenotype.get('sleep_study', {}).get('ODI') if phenotype else None,
                "RERA_index": phenotype.get('sleep_study', {}).get('RERA_index') if phenotype else None,
                "sleep_efficiency": phenotype.get('sleep_study', {}).get('sleep_efficiency') if phenotype else None,
                "sleep_duration_hours": phenotype.get('sleep_study', {}).get('sleep_duration_hours') if phenotype else None,
                "snoring_avg_db": phenotype.get('sleep_study', {}).get('snoring_avg_db') if phenotype else None,
                "snoring_max_db": phenotype.get('sleep_study', {}).get('snoring_max_db') if phenotype else None,
                "severity": phenotype.get('sleep_study', {}).get('severity', 'unknown') if phenotype else 'unknown'
            },
            "phenotype_highlights": {
                "applies_to": {
                    "patient_id": str(patient.id),
                    "phenotype_summary": {
                        "anatomical_findings": {
                            "nasal_obstruction": {
                                "present": phenotype.get('anatomical_findings', {}).get('nasal_obstruction', {}).get('present', False) if phenotype else False,
                                "source": phenotype.get('anatomical_findings', {}).get('nasal_obstruction', {}).get('source') if phenotype else None,
                                "value": phenotype.get('anatomical_findings', {}).get('nasal_obstruction', {}).get('value') if phenotype else None
                            },
                            "tmj_findings": {
                                "present": phenotype.get('anatomical_findings', {}).get('tmj_findings', {}).get('present', False) if phenotype else False,
                                "left_condylar_head": phenotype.get('anatomical_findings', {}).get('tmj_findings', {}).get('left_condylar_head') if phenotype else None,
                                "right_condylar_head": phenotype.get('anatomical_findings', {}).get('tmj_findings', {}).get('right_condylar_head') if phenotype else None,
                                "jaw_pain_clicking": phenotype.get('anatomical_findings', {}).get('tmj_findings', {}).get('jaw_pain_clicking') if phenotype else None
                            },
                            "dental_findings": {
                                "overjet": phenotype.get('anatomical_findings', {}).get('dental_findings', {}).get('overjet') if phenotype else None,
                                "overbite": phenotype.get('anatomical_findings', {}).get('dental_findings', {}).get('overbite') if phenotype else None,
                                "palate_type": phenotype.get('anatomical_findings', {}).get('dental_findings', {}).get('palate_type') if phenotype else None,
                                "arch_width": phenotype.get('anatomical_findings', {}).get('dental_findings', {}).get('arch_width') if phenotype else None
                            },
                            "airway_findings": {
                                "tongue_position": phenotype.get('anatomical_findings', {}).get('airway_findings', {}).get('tongue_position') if phenotype else None,
                                "soft_palate": phenotype.get('anatomical_findings', {}).get('airway_findings', {}).get('soft_palate') if phenotype else None,
                                "uvula": phenotype.get('anatomical_findings', {}).get('airway_findings', {}).get('uvula') if phenotype else None,
                                "primary_obstruction_level": phenotype.get('anatomical_findings', {}).get('airway_findings', {}).get('primary_obstruction_level') if phenotype else None
                            }
                        },
                        "clinical_findings": phenotype.get('clinical_findings', {}) if phenotype else {},
                        "comorbidities": {
                            "hypertension": phenotype.get('comorbidities', {}).get('hypertension', False) if phenotype else False,
                            "diabetes": phenotype.get('comorbidities', {}).get('diabetes', False) if phenotype else False,
                            "acid_reflux": phenotype.get('comorbidities', {}).get('acid_reflux', False) if phenotype else False,
                            "tmj_disorder": phenotype.get('comorbidities', {}).get('tmj_disorder', False) if phenotype else False,
                            "bruxism": phenotype.get('comorbidities', {}).get('bruxism', False) if phenotype else False,
                            "allergies": phenotype.get('comorbidities', {}).get('allergies', []) if phenotype else [],
                            "BMI": phenotype.get('comorbidities', {}).get('BMI') if phenotype else None
                        },
                        "symptom_assessment": {
                            "daytime_sleepiness": phenotype.get('symptom_assessment', {}).get('daytime_sleepiness', False) if phenotype else False,
                            "fatigue": phenotype.get('symptom_assessment', {}).get('fatigue', False) if phenotype else False,
                            "snoring": phenotype.get('symptom_assessment', {}).get('snoring', False) if phenotype else False,
                            "witnessed_apneas": phenotype.get('symptom_assessment', {}).get('witnessed_apneas', False) if phenotype else False,
                            "morning_headaches": phenotype.get('symptom_assessment', {}).get('morning_headaches', False) if phenotype else False,
                            "dry_mouth": phenotype.get('symptom_assessment', {}).get('dry_mouth', False) if phenotype else False,
                            "nocturia": phenotype.get('symptom_assessment', {}).get('nocturia', False) if phenotype else False,
                            "difficulty_concentrating": phenotype.get('symptom_assessment', {}).get('difficulty_concentrating', False) if phenotype else False
                        },
                        "treatment_history": {
                            "cpap_experience": phenotype.get('treatment_history', {}).get('cpap_experience', False) if phenotype else False,
                            "cpap_intolerance": phenotype.get('treatment_history', {}).get('cpap_intolerance', False) if phenotype else False,
                            "cpap_intolerance_evidence": phenotype.get('treatment_history', {}).get('cpap_intolerance_evidence') if phenotype else None,
                            "oral_appliance_experience": phenotype.get('treatment_history', {}).get('oral_appliance_experience', False) if phenotype else False,
                            "previous_sleep_surgery": phenotype.get('treatment_history', {}).get('previous_sleep_surgery', False) if phenotype else False
                        },
                        "lifestyle_factors": {
                            "smoking_status": phenotype.get('lifestyle_factors', {}).get('smoking_status') if phenotype else None,
                            "alcohol_use": phenotype.get('lifestyle_factors', {}).get('alcohol_use') if phenotype else None,
                            "exercise_frequency": phenotype.get('lifestyle_factors', {}).get('exercise_frequency') if phenotype else None,
                            "sleep_position": phenotype.get('lifestyle_factors', {}).get('sleep_position') if phenotype else None
                        },
                        "treatment_goals": phenotype.get('treatment_goals', []) if phenotype else [],
                        "policy_eligibility": {
                            "osa_confirmed": phenotype.get('policy_eligibility', {}).get('osa_confirmed', False) if phenotype else False,
                            "oral_appliance_candidate": phenotype.get('policy_eligibility', {}).get('oral_appliance_candidate', False) if phenotype else False,
                            "treatment_eligible": phenotype.get('policy_eligibility', {}).get('treatment_eligible', False) if phenotype else False,
                            "requires_specialist_referral": phenotype.get('policy_eligibility', {}).get('requires_specialist_referral', False) if phenotype else False,
                            "risk_level": phenotype.get('policy_eligibility', {}).get('risk_level') if phenotype else None,
                            "recommended_pathway": phenotype.get('policy_eligibility', {}).get('recommended_pathway') if phenotype else None
                        }
                    }
                }
            },
            "ai_phenotype_summary": {
                "primary_pathway": phenotype.get('policy_eligibility', {}).get('recommended_pathway') if phenotype else None,
                "key_anatomical_findings": {
                    "tmj_dysfunction": phenotype.get('anatomical_findings', {}).get('tmj_findings', {}).get('present', False) if phenotype else False,
                    "nasal_obstruction": phenotype.get('anatomical_findings', {}).get('nasal_obstruction', {}).get('present', False) if phenotype else False,
                    "primary_narrowing_site": phenotype.get('anatomical_findings', {}).get('airway_findings', {}).get('primary_obstruction_level') if phenotype else None
                },
                "comorbidities": {
                    "hypertension": phenotype.get('comorbidities', {}).get('hypertension', False) if phenotype else False,
                    "diabetes": phenotype.get('comorbidities', {}).get('diabetes', False) if phenotype else False,
                    "acid_reflux": phenotype.get('comorbidities', {}).get('acid_reflux', False) if phenotype else False,
                    "tmj_disorder": phenotype.get('comorbidities', {}).get('tmj_disorder', False) if phenotype else False,
                    "bruxism": phenotype.get('comorbidities', {}).get('bruxism', False) if phenotype else False,
                    "allergies": phenotype.get('comorbidities', {}).get('allergies', []) if phenotype else []
                },
                "sleep_study_data": {
                    "AHI": phenotype.get('sleep_study', {}).get('AHI') if phenotype else None,
                    "severity": phenotype.get('sleep_study', {}).get('severity') if phenotype else None,
                    "SpO2_nadir": phenotype.get('sleep_study', {}).get('SpO2_nadir') if phenotype else None
                }
            },
            "policy_features": {
                "workflow_state": {
                    "current_stage": stage_manifest[completed_stages]['stage_name'] if stage_manifest and completed_stages < len(stage_manifest) else 'Completed',
                    "completed_stages": [stage_manifest[i]['stage_name'] for i in range(completed_stages)] if stage_manifest and completed_stages <= len(stage_manifest) else [],
                    "pending_actions": [{"action": a.get('label', 'Unknown action'), "due_date": None, "priority": "normal"} for a in (eligible_actions or [])[:3]]
                },
                "clinical_flags": {
                    "contraindications": phenotype.get('contraindications', []) if phenotype else [],
                    "risk_factors": phenotype.get('risk_factors', []) if phenotype else [],
                    "special_considerations": phenotype.get('special_considerations', []) if phenotype else []
                }
            },
            "stage_context": {
                "stage": stage_manifest[completed_stages]['stage_name'] if stage_manifest and completed_stages < len(stage_manifest) else 'Completed',
                "completion_pct": progress_percentage,
                "stage_details": {
                    "started_date": None,
                    "expected_completion": None,
                    "blocking_factors": [],
                    "requirements_met": [],
                    "requirements_pending": []
                }
            },
            "operational_data": {
                "workflow_progress": {
                    "current_stage": stage_manifest[completed_stages]['stage_name'] if stage_manifest and completed_stages < len(stage_manifest) else 'Completed',
                    "completion_pct": progress_percentage,
                    "total_stages": len(stage_manifest) if stage_manifest else 0,
                    "current_stage_index": completed_stages
                },
                "pending_actions": [
                    {
                        "action": a.get('label', 'Unknown action'),
                        "due_in_days": 0,
                        "priority": "normal",
                        "blocking": True
                    } for a in (eligible_actions or [])[:3]
                ],
                "device_tracking": {
                    "last_device_event": "delivery_2025-01-15",  # This would come from actual device history
                    "device_status": "delivered",
                    "delivery_date": None,
                    "fitting_date": None
                },
                "alerts": [],
                "consultations": [
                    {
                        "consult_type": consultation.get('consult_type', ''),
                        "scheduled_datetime": consultation.get('scheduled_datetime', ''),
                        "status": consultation.get('status', ''),
                        "doctor_name": consultation.get('doctor_name', '')
                    } for consultation in patient_details.get('scheduled_consultations', [])
                ]
            },
            "clinical_data": {
                "vitals": {
                    "blood_pressure_systolic": None,
                    "blood_pressure_diastolic": None,
                    "heart_rate": None,
                    "weight_kg": None,
                    "height_cm": None,
                    "BMI": phenotype.get('comorbidities', {}).get('BMI') if phenotype else None
                },
                "questionnaire_scores": {
                    "ESS_score": None,
                    "ISI_score": None,
                    "LQ_score": None
                },
                "imaging_data": {
                    "cbct_available": len(patient_details.get('uploaded_files', {}).get('cbct', [])) > 0,
                    "cbct_findings": [],
                    "intraoral_scans_available": len(patient_details.get('uploaded_files', {}).get('intraoral_scan', [])) > 0,
                    "clinical_photos_available": len(patient_details.get('uploaded_files', {}).get('clinical_pictures', [])) > 0
                }
            },
            "protocols": {
                "Lamberg_Protocol": {
                    "version": "1.0",
                    "treatment_targets": ["AHI reduction", "symptom improvement"],
                    "decision_flow": [],
                    "eligibility_rules_additions": {}
                },
                "sOSA_Protocol": {
                    "version": "1.0",
                    "follow_up_protocol": {
                        "timelines": [
                            {"timepoint": "1 month", "activity": "Follow-up consultation"},
                            {"timepoint": "3 months", "activity": "Sleep study repeat"},
                            {"timepoint": "6 months", "activity": "Long-term assessment"}
                        ]
                    }
                },
                "Vizbriz_Workflow": {
                    "version": "2.0",
                    "steps": [stage.get('stage_name', 'Unknown stage') for stage in (stage_manifest or [])]
                }
            },
            "meta": {
                "schema_version": 2,
                "packet_hash": "",  # Will be computed
                "generated_at": datetime.now().isoformat(),
                "data_sources": ["Patient Records", "Clinical Assessments", "Sleep Studies"]
            }
        }
        
        # Add alerts based on phenotype
        if phenotype and phenotype.get('anatomical_findings', {}).get('tmj_findings', {}).get('present'):
            enhanced_packet["operational_data"]["alerts"].append("tmj_caution")
        if phenotype and phenotype.get('comorbidities', {}).get('allergies') and 'nickel' in phenotype.get('comorbidities', {}).get('allergies', []):
            enhanced_packet["operational_data"]["alerts"].append("nickel_constraint")
        
        logger.info(f"Enhanced packet built successfully for patient {patient_id}")
        logger.info(f"Enhanced packet keys: {list(enhanced_packet.keys())}")
        return enhanced_packet
        
    except Exception as e:
        logger.error(f"Error building enhanced packet for patient {patient_id}: {e}")
        return None
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
        
        # Always use current user's DSO for quiz links
        patient_dso_id = None
        if hasattr(current_user, 'get_dso_ids'):
            dso_ids = current_user.get_dso_ids()
            if dso_ids:
                patient_dso_id = dso_ids[0]
        if not patient_dso_id:
            patient_dso_id = None  # No default fallback
        
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
                "quiz_link": f"/quiz?dso_id={patient_dso_id}",
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
                            'input_options': action_config.get('input_options', {}),
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
        from flask_app.routes.conversion_quiz_agent import ConversionQuiz as ConversionQuizAgent
        # Get the quiz submission with DSO access control
        query = db.session.query(
            ConversionQuiz,
            Patient,
            Clinic
        ).join(
            Patient, ConversionQuizAgent.user_id == Patient.id
        ).outerjoin(
            Clinic, ConversionQuizAgent.clinic_id == Clinic.id
        ).filter(
            ConversionQuizAgent.id == quiz_id,
            ConversionQuizAgent.user_id == patient_id
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
        from flask_app.routes.conversion_quiz_agent import ConversionQuiz as ConversionQuizAgent
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
            Patient, ConversionQuizAgent.user_id == Patient.id
        ).outerjoin(
            Clinic, ConversionQuizAgent.clinic_id == Clinic.id
        ).filter(
            ConversionQuizAgent.id == quiz_id,
            ConversionQuizAgent.user_id == patient_id
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
        
        # Get CANONICAL patient data (clean, structured data)
        print("Loading canonical patient data...")
        
        try:
            from flask_app.services.cache_service import CacheService
            canonical_data = CacheService.cached_canonical_data(patient_id)
            
            if not canonical_data:
                print("No canonical data found, falling back to basic patient info")
                # Fallback to basic patient data
                patient = Patient.query.get(patient_id)
                if not patient:
                    return jsonify({"success": False, "message": "Patient not found"}), 400
                
                canonical_data = {
                    "patient": {
                        "id": patient.id,
                        "name": patient.name,
                        "email": patient.email,
                        "demographics": {
                            "name": patient.name,
                            "email": patient.email,
                            "age": getattr(patient, 'age', None),
                            "gender": getattr(patient, 'gender', None)
                        }
                    },
                    "clinical_data": {},
                    "workflow_status": "No canonical data available"
                }
            
            print(f"✅ Canonical data loaded: {len(str(canonical_data))} characters")
            
        except Exception as e:
            print(f"❌ Error loading canonical data: {e}")
            return jsonify({"success": False, "message": f"Failed to load patient data: {str(e)}"}), 400
        
        # Extract patient name from canonical data
        patient_name = canonical_data.get('patient', {}).get('demographics', {}).get('name', 'Unknown')
        print(f"Patient name: {patient_name}")
        
        # Get operational summary (workflow status, priorities, recommendations)
        operational_summary = None
        try:
            from flask_app.config.stage_summary_manifest import get_stage_summary_manifest
            from flask_app.services.stage_summary_service import (
                evaluate_stage_completion,
                get_cached_ai_summary,
                generate_overall_workflow_summary
            )
            
            manifest_entries = get_stage_summary_manifest()
            
            # Evaluate all stages to get current status
            all_stages_status = {}
            for entry in manifest_entries:
                stage_key = entry.get("key")
                completion_result = evaluate_stage_completion(patient_id, entry, all_stages_status)
                all_stages_status[stage_key] = completion_result
            
            # Try to get cached summary first
            cached_data = get_cached_ai_summary(patient_id, all_stages_status)
            
            if cached_data and cached_data.get("overall_summary"):
                operational_summary = cached_data.get("overall_summary")
                print(f"✅ Using cached operational summary")
            else:
                # Generate new summary if cache is not available
                print("Generating new operational summary...")
                operational_summary = generate_overall_workflow_summary(
                    patient_id,
                    manifest_entries,
                    all_stages_status
                )
                if operational_summary:
                    print(f"✅ Generated new operational summary")
                else:
                    print("⚠️ Could not generate operational summary")
        except Exception as e:
            print(f"⚠️ Error loading operational summary: {e}")
            import logging
            logger.warning(f"Error loading operational summary for Dr. Briz: {e}")
        
        # Skip document observations to keep canonical data clean
        print("Using clean canonical data without document observations")
        
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

You have access to:
- Patient manifests and clinical observations via S3 URLs
- Operational status and workflow summary (including critical path analysis and AI-generated recommendations)
- Use this information to provide informed, prioritized recommendations that align with the current workflow stage and priorities."""
        else:
            # Regular chat prompt
            system_prompt = f"""You are Dr. Briz, an expert sleep medicine AI assistant specializing in Obstructive Sleep Apnea (OSA) treatment and dental sleep therapy. You have extensive knowledge in:

AUDIENCE AND VOICE:
- You are ALWAYS speaking to healthcare providers/caretakers about their patients.
- NEVER address the patient directly with "you" or "your".
- Refer to the patient strictly in the third person (e.g., "the patient", "this patient", "he/she/they").
- Your responses are for medical professionals to understand treatment options for their patients.

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

COMPREHENSIVE PATIENT DATA ACCESS:
- You now have access to the SAME comprehensive patient data as the workflow prompt
- This includes detailed phenotype data, clinical findings, sleep study results, and workflow status
- You can analyze anatomical findings, comorbidities, treatment history, and current stage
- You have access to document observations from clinical reports and medical documents
- You have access to OPERATIONAL STATUS which includes workflow progress, critical path analysis, and AI-generated recommendations
- Use this comprehensive data to provide highly personalized and informed responses

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
 - When referring to the patient, always use third person and avoid second-person pronouns (no \"you/your\").

You provide evidence-based, professional guidance while being warm and supportive. You can answer questions about OSA treatment beyond just the patient's current workflow stage, drawing on your comprehensive medical knowledge and the detailed patient data provided. When you make recommendations based on clinical data, present them as your own professional assessment, not as references to external sources."""

        # Build operational status section if available
        operational_status_section = ""
        if operational_summary:
            try:
                if isinstance(operational_summary, dict) and operational_summary.get("structured"):
                    structured = operational_summary.get("structured", {})
                    operational_status_section = f"""
========== OPERATIONAL STATUS & WORKFLOW SUMMARY ==========
This section contains AI-generated workflow analysis, priorities, and recommendations for this patient.

OVERALL SUMMARY:
{json.dumps(structured.get("overall_summary", {}), indent=2)}

CRITICAL PATH ANALYSIS:
{json.dumps(structured.get("critical_path_analysis", {}), indent=2)}

RECOMMENDATIONS:
{json.dumps(structured.get("recommendations", []), indent=2)}

Generated: {operational_summary.get("generated_at", "Unknown")}
"""
                elif isinstance(operational_summary, str):
                    operational_status_section = f"""
========== OPERATIONAL STATUS & WORKFLOW SUMMARY ==========
{operational_summary}
"""
            except Exception as e:
                print(f"⚠️ Error formatting operational status: {e}")
        
        user_prompt = f"""
========== PATIENT CLINICAL DATA (PRIMARY SOURCE) ==========
PATIENT INFORMATION:
Name: {patient_name}
ID: {patient_id}

CANONICAL PATIENT DATA (Clean, structured clinical data):
{json.dumps(canonical_data, indent=2)}
{operational_status_section}
========== USER QUESTION ==========
{user_message}

========== INSTRUCTIONS ==========
CRITICAL: You MUST use the PATIENT'S CLINICAL DATA provided above as your PRIMARY source. Analyze the canonical patient data first, then provide your response.

Please analyze the canonical patient data above and provide a concise, direct response as Dr. Briz (2-4 sentences maximum). Focus on:
1. Direct answer to the specific question USING THE PATIENT'S DATA ABOVE
2. Practical, actionable information based on THIS PATIENT'S clinical findings
3. Relevant medical insights based on the patient's specific clinical findings and data shown above
4. Next steps if applicable

IMPORTANT: 
- When making recommendations based on clinical data, present them as YOUR professional assessment. Be confident and authoritative. Use phrases like \"I recommend\", \"Based on my analysis\", \"I suggest\", \"My recommendation is\". 
- You MUST reference the patient's specific clinical data from the CANONICAL PATIENT DATA section above. If the data shows specific findings (AHI, anatomical features, etc.), use them in your response.
- If the patient data is minimal or missing, state that clearly but still provide general guidance based on what IS available.

Consider the patient's:
- Clinical findings and observations (from canonical data above)
- Sleep study results (AHI, severity, SpO2 nadir) - check the sleep_study section
- Anatomical findings and comorbidities - check the diagnosis/anatomy sections
- Treatment history and current status - check the treatment/workflow sections
- Document observations from clinical reports - check observations sections
- Operational status and workflow priorities (from OPERATIONAL STATUS section above) - use this to understand current workflow stage, blocking factors, and recommended next actions

Keep it brief, professional, and helpful without lengthy disclaimers.
"""
        
        print(f"System prompt length: {len(system_prompt)} characters")
        print(f"User prompt length: {len(user_prompt)} characters")
        
        # Import and use Bedrock integration with automatic knowledge base integration
        try:
            from flask_app.routes.osaagent_routes import query_bedrock_claude_enhanced
            logger.info("Calling Bedrock with automatic Knowledge Base integration...")
            
            # Create messages for Bedrock
            # IMPORTANT: Conversation must start with a "user" message. Embed system guidance inline.
            # The knowledge base will be automatically queried and integrated by query_bedrock_claude_enhanced
            combined_content = f"{system_prompt}\n\n{user_prompt}"
            
            # Log to verify clinical data is included
            logger.info(f"Dr. Briz message includes clinical data: {len(canonical_data)} chars of canonical data")
            logger.info(f"Full message length: {len(combined_content)} chars (includes system prompt + patient data + question)")
            
            bedrock_messages = [
                {
                    "role": "user",
                    "content": combined_content
                }
            ]
            
            # Call Bedrock with automatic knowledge base integration enabled
            # Knowledge base will query based on the user_message and enhance the prompt automatically
            # NOTE: Clinical data is preserved - KB context is appended, not replacing patient data
            logger.info(f"Calling Bedrock with knowledge base enabled for patient {patient_id}")
            result = query_bedrock_claude_enhanced(
                bedrock_messages,
                max_tokens=800,
                temperature=0.3,
                patient_id=patient_id,
                endpoint='bedrock_chat',
                use_knowledge_base=True,
                knowledge_base_query=user_message  # Use the original user message for KB query
            )
            print(f"Bedrock result: {result}")
            print(f"Result type: {type(result)}")
            print(f"Result keys: {result.keys() if isinstance(result, dict) else 'Not a dict'}")
            
            # Initialize citations variable
            knowledge_base_citations = []
            
            if result["success"]:
                # For Claude 3.5 Sonnet, response is a string
                claude_response = result.get('response', "I'm here to help with your patient's OSA treatment journey.")
                knowledge_base_citations = result.get('knowledge_base_citations', [])
                print(f"✅ Bedrock success! Response: {claude_response[:200]}...")
                if knowledge_base_citations:
                    print(f"✅ Knowledge base citations available: {len(knowledge_base_citations)} citations")
                    logger.info(f"✅ Dr. Briz response includes knowledge base citations: {len(knowledge_base_citations)}")
                else:
                    logger.warning("⚠️ Dr. Briz response does NOT include knowledge base citations - KB may not have been queried successfully")
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
        
        # Import config to check ENVIRONMENT
        from flask_app.config import Config
        
        # Check if we're in development mode
        is_development = Config.ENVIRONMENT.lower() in ('development', 'dev', 'local')
        
        response_data = {
            "success": True,
            "response": claude_response,
            "patient_id": patient_id,
            "patient_name": patient_name,
            "debug_mode": is_development
        }
        # Include knowledge base citations ONLY in development mode
        if is_development and knowledge_base_citations:
            response_data["knowledge_base_citations"] = knowledge_base_citations
            logger.info(f"Including {len(knowledge_base_citations)} citations in Dr. Briz response (DEVELOPMENT MODE)")
        print(f"Full response data: {response_data}")
        
        return jsonify(response_data)
        
    except Exception as e:
        print(f"=== BEDROCK CHAT ENDPOINT ERROR ===")
        print(f"Exception: {str(e)}")
        return jsonify({
            "success": False, 
            "message": f"Internal server error: {str(e)}"
        }), 500

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
        dso_id = None
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
        dso_id = None
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
        
        logger.info(f"load_document_observations: Found {len(observations)} raw observations for patient {patient_id}")
        if observations:
            logger.info(f"load_document_observations: Sample observation: {observations[0]}")
        
        # Organize observations by source type
        organized_observations = {}
        
        # Map technical source types to user-friendly names
        source_type_mapping = {
            'sleep_test': 'Sleep Study Results',
            'questionnaire': 'Patient Questionnaires',
            'intraoral_scan': 'Intraoral Scans',
            'medical_background': 'Medical History',
            'consent_form': 'Consent Forms',
            'insurance_document': 'Insurance Documents',
            'payment_document': 'Payment Documents',
            'cbct_report': 'CBCT Reports',
            'patient_report': 'Patient Reports',
            'sleep_study': 'Sleep Studies',
            'consultation_notes': 'Consultation Notes',
            'treatment_plan': 'Treatment Plans',
            'follow_up_notes': 'Follow-up Notes',
            'prescription': 'Prescriptions',
            'lab_results': 'Lab Results',
            'imaging_report': 'Imaging Reports',
            'medical_history': 'Medical History',
            'surgical_notes': 'Surgical Notes',
            'discharge_summary': 'Discharge Summaries',
            'general_medical': 'General Medical Documents'
        }
        
        for obs in observations:
            source_type = obs['source_type']
            # Use user-friendly name if available, otherwise use original
            display_name = source_type_mapping.get(source_type, source_type.replace('_', ' ').title())
            
            if display_name not in organized_observations:
                organized_observations[display_name] = []
            
            # Parse the JSON observations
            try:
                obs_data = json.loads(obs['extracted_observations']) if obs['extracted_observations'] else {}
                
                # Clean up observation title - remove redundant prefixes
                observation = obs_data.get('observation', 'Unknown')
                redundant_prefixes = [
                    'Observation: ', 'Finding: ', 'Clinical Finding: ', 'Medical Finding: ',
                    'Diagnosis: ', 'Assessment: ', 'Result: ', 'Note: ', 'Comment: ',
                    'Clinical Observation: ', 'Medical Observation: '
                ]
                
                for prefix in redundant_prefixes:
                    if observation.lower().startswith(prefix.lower()):
                        observation = observation[len(prefix):]
                        break
                
                organized_observations[display_name].append({
                    'observation': observation,
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
                organized_observations[display_name].append({
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

@main.route('/api/patient/<int:patient_id>/observations', methods=['GET'])
@login_required
def api_get_patient_observations(patient_id):
    """Return document-based clinical observations for a patient."""
    try:
        data = load_document_observations(patient_id)
        return jsonify({"success": True, "observations": data})
    except Exception as e:
        logger.error(f"Error returning observations for patient {patient_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    
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
        
        # Get all stages status to check for skipped stages
        from flask_app.routes.routes_stage_summary import get_all_stages_status
        all_stages_status = get_all_stages_status(patient_id)
        
        # Get skipped stages list for validation
        skipped_stages = [stage_key for stage_key, status_info in all_stages_status.items() 
                         if status_info.get('status') == 'skipped']
        
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
        - CRITICAL: Do NOT suggest any actions for stages that are marked as SKIPPED. Skipped stages should never appear as next steps.
        - Skipped stages for this patient: {', '.join(skipped_stages) if skipped_stages else 'None'}
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
- CRITICAL: Do NOT suggest any actions for stages that are marked as SKIPPED. Skipped stages should never appear as next steps in recommendations.
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
                            # Filter out recommendations for skipped stages
                            filtered_recommendations = []
                            for rec in ai_recommendations:
                                rec_stage_key = rec.get('stage_key') or rec.get('action_key', '').split('_')[0] if rec.get('action_key') else None
                                # Check if this recommendation references a skipped stage
                                if rec_stage_key and rec_stage_key in skipped_stages:
                                    logger.warning(f"Filtered out recommendation for skipped stage: {rec_stage_key}")
                                    continue
                                filtered_recommendations.append(rec)
                            recommendations.extend(filtered_recommendations)
                            logger.info(f"Successfully parsed {len(filtered_recommendations)} recommendations from Bedrock (filtered {len(ai_recommendations) - len(filtered_recommendations)} skipped stage recommendations)")
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
                                # Filter out recommendations for skipped stages
                                filtered_recommendations = [rec for rec in ai_recommendations 
                                                           if not (rec.get('stage_key') or (rec.get('action_key', '').split('_')[0] if rec.get('action_key') else None)) in skipped_stages]
                                recommendations.extend(filtered_recommendations)
                                logger.info(f"Successfully parsed {len(filtered_recommendations)} recommendations from Bedrock using regex (filtered {len(ai_recommendations) - len(filtered_recommendations)} skipped stage recommendations)")
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
                                    # Filter out recommendations for skipped stages
                                    filtered_recommendations = [rec for rec in ai_recommendations 
                                                               if not (rec.get('stage_key') or (rec.get('action_key', '').split('_')[0] if rec.get('action_key') else None)) in skipped_stages]
                                    recommendations.extend(filtered_recommendations)
                                    logger.info(f"Successfully parsed {len(filtered_recommendations)} recommendations from Bedrock using fixed JSON (filtered {len(ai_recommendations) - len(filtered_recommendations)} skipped stage recommendations)")
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
                                            # Filter out recommendations for skipped stages
                                            filtered_recommendations = [rec for rec in ai_recommendations 
                                                                       if not (rec.get('stage_key') or (rec.get('action_key', '').split('_')[0] if rec.get('action_key') else None)) in skipped_stages]
                                            recommendations.extend(filtered_recommendations)
                                            logger.info(f"Successfully parsed {len(filtered_recommendations)} recommendations from Bedrock using object extraction (filtered {len(ai_recommendations) - len(filtered_recommendations)} skipped stage recommendations)")
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
                                    # Filter out recommendations for skipped stages
                                    filtered_recommendations = [rec for rec in ai_recommendations 
                                                               if not (rec.get('stage_key') or (rec.get('action_key', '').split('_')[0] if rec.get('action_key') else None)) in skipped_stages]
                                    recommendations.extend(filtered_recommendations)
                                    logger.info(f"Successfully extracted {len(filtered_recommendations)} recommendations from partial response (filtered {len(ai_recommendations) - len(filtered_recommendations)} skipped stage recommendations)")
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
        result = query_bedrock_claude_enhanced(messages, max_tokens=100, temperature=0.1, patient_id=patient_id)
        
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
                result = query_bedrock_claude_enhanced(messages, max_tokens=3000, temperature=0.1, patient_id=patient_id)
                
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
        progress_percentage = round((completed_stages / total_stages * 100)) if total_stages > 0 else 0
        
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
# NEW API ENDPOINTS FOR PROGRESSIVE LOADING (No impact on existing routes)
@main.route('/api/patient/<int:patient_id>/execution-manifest', methods=['GET'])
@login_required
def get_execution_manifest_api(patient_id):
    """API endpoint for loading execution manifest asynchronously."""
    try:
        # Try cache first
        from flask_app.services.cache_service import CacheService
        manifest_data = CacheService.cached_execution_manifest(patient_id)
        
        if manifest_data:
            return jsonify(manifest_data)
        
        # Fallback to direct call if cache fails
        from flask_app.routes.cursor_routes import get_execution_manifest
        execution_manifest_response = get_execution_manifest(patient_id)
        
        # Handle Flask Response objects
        if hasattr(execution_manifest_response, 'get_json'):
            manifest_data = execution_manifest_response.get_json()
        else:
            manifest_data = execution_manifest_response
        
        if not manifest_data or 'error' in manifest_data:
            return jsonify({'error': 'Failed to load execution manifest'}), 500
        
        return jsonify(manifest_data)
        
    except Exception as e:
        logger.error(f"Error in get_execution_manifest_api for patient {patient_id}: {e}")
        return jsonify({'error': str(e)}), 500

@main.route('/api/patient/<int:patient_id>/canonical-data', methods=['GET'])
@login_required
def get_canonical_data_api(patient_id):
    """API endpoint for loading canonical data asynchronously."""
    try:
        from flask_app.services.cache_service import CacheService
        canonical_data = CacheService.cached_canonical_data(patient_id)
        
        return jsonify({'canonical_data': canonical_data})
        
    except Exception as e:
        logger.error(f"Error in get_canonical_data_api for patient {patient_id}: {e}")
        return jsonify({'error': str(e)}), 500


@main.route('/api/patient/<int:patient_id>/basic-manifest', methods=['GET'])
@login_required
def get_basic_manifest_api(patient_id):
    """API endpoint for loading basic manifest data for fast initial page load."""
    try:
        from flask_app.services.performance_service import PerformanceService
        basic_data = PerformanceService.get_basic_manifest_data(patient_id)
        
        if not basic_data:
            return jsonify({'error': 'Patient not found'}), 404
        
        return jsonify(basic_data)
        
    except Exception as e:
        logger.error(f"Error in get_basic_manifest_api for patient {patient_id}: {e}")
        return jsonify({'error': str(e)}), 500

# EXISTING ROUTE (Maintained exactly as before)
@main.route('/api/debug/next_stage/<int:patient_id>', methods=['GET'])
@login_required
def debug_next_stage(patient_id):
    """Debug endpoint to check next stage calculation"""
    try:
        from flask_app.services.manifest_service import ManifestService
        result = ManifestService.get_patient_current_and_next_stage(patient_id)
        return jsonify({
            'current_stage': {
                'key': result.get('current_stage_key'),
                'name': result.get('current_stage_name'),
                'number': result.get('current_stage_number')
            },
            'next_stage': {
                'key': result.get('next_stage_key'),
                'name': result.get('next_stage_name'),
                'number': result.get('next_stage_number')
            } if result else None
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@main.route('/api/patient/<int:patient_id>/llm-data', methods=['GET'])
@login_required
def get_patient_llm_data(patient_id):
    """Load LLM data asynchronously for progressive loading with caching"""
    logger.info(f"API: ===== LLM DATA ENDPOINT CALLED FOR PATIENT {patient_id} =====")
    try:
        # Check for force_refresh parameter
        force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'
        logger.info(f"API: Loading LLM data for patient {patient_id}, force_refresh: {force_refresh}")
        
        # Initialize variables - NO FALLBACK VALUES
        clinical_summary = None
        operational_summary = None
        ai_guidance = None
        clinical_vm = None
        operational_vm = None
        
        # Get patient data
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({'success': False, 'error': 'Patient not found'})
        
        # Check cache first (unless force_refresh is requested)
        from flask_app.services.cache_service import CacheService
        cached_llm_data = CacheService.cached_llm_data(patient_id, force_refresh=force_refresh)
        
        if cached_llm_data is not None:
            logger.info(f"API: Returning cached LLM data for patient {patient_id}")
            return jsonify({
                'success': True,
                'clinical_summary': cached_llm_data.get('clinical_summary'),
                'operational_summary': cached_llm_data.get('operational_summary'),
                'ai_guidance': cached_llm_data.get('ai_guidance'),
                'clinical_vm': cached_llm_data.get('clinical_vm'),
                'operational_vm': cached_llm_data.get('operational_vm'),
                'last_refreshed': cached_llm_data.get('last_refreshed'),
                'debug_info': {
                    'cached': True,
                    'llm_call_success': True,
                    'llm_response_length': 0,
                    'packet_built': True
                }
            })
        
        logger.info(f"API: Cache miss - generating new LLM data for patient {patient_id}")
        
        # Get execution manifest
        execution_manifest = CacheService.cached_execution_manifest(patient_id)
        
        if not execution_manifest:
            from flask_app.routes.cursor_routes import get_execution_manifest
            execution_manifest_response = get_execution_manifest(patient_id)
            
            if hasattr(execution_manifest_response, 'get_json'):
                execution_manifest = execution_manifest_response.get_json()
            else:
                execution_manifest = execution_manifest_response
        
        if not execution_manifest or 'error' in execution_manifest:
            return jsonify({'success': False, 'error': 'Failed to load execution manifest'})
        
        # Get canonical data
        canonical_data = CacheService.cached_canonical_data(patient_id)
        if canonical_data is None:
            try:
                from flask_app.models import PatientCaseEnvelope
                canonical_envelope = PatientCaseEnvelope.query.filter_by(
                    patient_id=patient_id, 
                    report_id='canonical'
                ).first()
                
                if canonical_envelope and canonical_envelope.case_json:
                    if isinstance(canonical_envelope.case_json, str):
                        canonical_data = json.loads(canonical_envelope.case_json)
                    else:
                        canonical_data = canonical_envelope.case_json
            except Exception as e:
                logger.error(f"Error loading canonical data: {e}")
                canonical_data = {}
        
        # Build the packet for LLM using the same comprehensive logic as the main route
        try:
            logger.info(f"API: Starting packet build for patient {patient_id}")
            # Get stage manifest from execution manifest if available
            stage_manifest = []
            completed_stages = 0
            if execution_manifest:
                stage_manifest = execution_manifest.get('stage_manifest') or execution_manifest.get('stages') or []
                # Handle both {value: "yes"} and {is_completed: true}
                completed_stages = sum(
                    1 for stage in stage_manifest
                    if (isinstance(stage, dict) and (
                        stage.get('is_completed', False) is True or str(stage.get('value', '')).lower() in ('yes', 'true', '1')
                    ))
                )
                logger.info(f"API: Found {len(stage_manifest)} stages, {completed_stages} completed")
            else:
                logger.info(f"API: No execution manifest found for patient {patient_id}")
            
            # Use ONLY canonical data - no duplicated raw data
            logger.info(f"API: Building packet with ONLY canonical data for patient {patient_id}")
            
            def _safe_num(v):
                try:
                    if v is None:
                        return None
                    if isinstance(v, bool):
                        return None
                    return float(v)
                except Exception:
                    return None

            def _pick_metric(canonical_root, candidates, tolerance=0.1):
                """
                Deterministically pick a single numeric metric from canonical_root using candidate paths.
                Returns (value, source_path, conflicts:list[str]).
                """
                found = []
                for path in candidates:
                    cur = canonical_root
                    ok = True
                    for part in path.split('.'):
                        if isinstance(cur, dict) and part in cur:
                            cur = cur[part]
                        else:
                            ok = False
                            break
                    if ok:
                        val = _safe_num(cur)
                        if val is not None:
                            found.append((path, val))

                if not found:
                    return None, None, []

                chosen_path, chosen_val = found[0]
                conflicts = []
                for other_path, other_val in found[1:]:
                    try:
                        if abs(other_val - chosen_val) > tolerance:
                            conflicts.append(f"{chosen_path}={chosen_val} vs {other_path}={other_val}")
                    except Exception:
                        continue
                return chosen_val, chosen_path, conflicts

            if canonical_data:
                packet = {
                    "canonical_clinical_data": canonical_data,
                    # Deterministic snapshot to prevent LLM guessing and fragile timeline indexing.
                    "derived_clinical_snapshot": None,
                    # Include operational state only when we can derive it from the execution manifest.
                    "derived_operational_state": None,
                    "meta": {
                        "schema_version": 2,
                        "packet_hash": "",
                        "generated_at": datetime.now().isoformat(),
                        "data_sources": ["Canonical Patient File"]
                    }
                }

                # Build derived snapshot using ONLY canonical_clinical_data.{respiratory_indices,oxygenation,sleep_timing_architecture}
                canonical_root = canonical_data if isinstance(canonical_data, dict) else {}
                demo = canonical_root.get('demographics', {}) if isinstance(canonical_root.get('demographics', {}), dict) else {}
                ri = canonical_root.get('respiratory_indices', {}) if isinstance(canonical_root.get('respiratory_indices', {}), dict) else {}
                ox = canonical_root.get('oxygenation', {}) if isinstance(canonical_root.get('oxygenation', {}), dict) else {}
                sta = canonical_root.get('sleep_timing_architecture', {}) if isinstance(canonical_root.get('sleep_timing_architecture', {}), dict) else {}

                conflicts = []

                ahi, ahi_src, ahi_conf = _pick_metric(canonical_root, ['respiratory_indices.ahi_overall', 'respiratory_indices.ahi'])
                conflicts.extend(ahi_conf)
                rdi, rdi_src, rdi_conf = _pick_metric(canonical_root, ['respiratory_indices.rdi'])
                conflicts.extend(rdi_conf)
                odi, odi_src, odi_conf = _pick_metric(canonical_root, ['respiratory_indices.odi3', 'respiratory_indices.odi4', 'respiratory_indices.odi'])
                conflicts.extend(odi_conf)
                spo2_nadir, spo2_src, spo2_conf = _pick_metric(canonical_root, ['oxygenation.spo2_nadir_pct'], tolerance=0.5)
                conflicts.extend(spo2_conf)
                sleep_eff, sleep_eff_src, sleep_eff_conf = _pick_metric(canonical_root, ['sleep_timing_architecture.sleep_efficiency_pct'], tolerance=0.5)
                conflicts.extend(sleep_eff_conf)
                t90, t90_src, t90_conf = _pick_metric(canonical_root, ['oxygenation.t90_pct'], tolerance=0.5)
                conflicts.extend(t90_conf)

                sources = {}
                if ahi_src: sources['ahi'] = ahi_src
                if rdi_src: sources['rdi'] = rdi_src
                if odi_src: sources['odi'] = odi_src
                if spo2_src: sources['spo2_nadir'] = spo2_src
                if sleep_eff_src: sources['sleep_efficiency'] = sleep_eff_src
                if t90_src: sources['t90'] = t90_src

                packet["derived_clinical_snapshot"] = {
                    "patient_id": canonical_root.get('patient_id', patient_id),
                    "sex": demo.get('sex'),
                    "age_years": demo.get('age_years'),
                    "bmi": demo.get('bmi'),
                    "data_as_of_date": canonical_root.get('as_of'),
                    "current_sleep_metrics": {
                        "ahi": ahi,
                        "rdi": rdi,
                        "odi": odi,
                        "spo2_nadir": spo2_nadir,
                        "sleep_efficiency": sleep_eff,
                        "t90": t90
                    },
                    "sources": sources,
                    "conflicts": conflicts
                }

                # Build derived operational state ONLY if execution_manifest provides concrete values
                if execution_manifest and isinstance(execution_manifest, dict):
                    current_stage = execution_manifest.get('current_stage')
                    completion_pct = execution_manifest.get('progress_percentage')
                    eligible_actions = execution_manifest.get('eligible_actions') if isinstance(execution_manifest.get('eligible_actions'), list) else []

                    # Only include if at least one of stage/completion is present
                    if current_stage is not None or completion_pct is not None:
                        next_actions = []
                        for action in eligible_actions[:3]:
                            if isinstance(action, dict):
                                label = action.get('label') or action.get('action') or action.get('name') or 'Action'
                            else:
                                label = str(action)
                            next_actions.append({"action": str(label), "due": "Not provided", "priority": "normal", "blocking": False})

                        packet["derived_operational_state"] = {
                            "stage": current_stage if current_stage is not None else "Not provided",
                            "completion_pct": completion_pct if isinstance(completion_pct, (int, float)) else 0,
                            "next_actions": next_actions,
                            "alerts": []
                        }
                logger.info(f"API: Successfully built canonical-only packet for patient {patient_id}")
            else:
                # Fallback if no canonical data available
                packet = {
                    "canonical_clinical_data": {
                        "demographics": {
                            "sex": patient.gender,
                            "age_years": (datetime.now().date() - patient.dob).days // 365 if patient.dob else None
                        },
                        "sleep_study": {},
                        "observations": {},
                        "treatment_considerations": {}
                    },
                    "meta": {
                        "schema_version": 2,
                        "packet_hash": "",
                        "generated_at": datetime.now().isoformat(),
                        "data_sources": ["Fallback - No Canonical Data"]
                    }
                }
                logger.warning(f"API: No canonical data found for patient {patient_id}, using fallback")
        except Exception as e:
            logger.error(f"API: Error building comprehensive packet for patient {patient_id}: {e}")
            import traceback
            logger.error(f"API: Traceback: {traceback.format_exc()}")
            # Fallback to canonical-only packet
            packet = {
                "canonical_clinical_data": canonical_data if canonical_data else {
                    "demographics": {
                        "sex": patient.gender,
                        "age_years": (datetime.now().date() - patient.dob).days // 365 if patient.dob else None
                    },
                    "sleep_study": {},
                    "observations": {},
                    "treatment_considerations": {}
                },
                "meta": {
                    "schema_version": 2,
                    "packet_hash": "",
                    "generated_at": datetime.now().isoformat(),
                    "data_sources": ["Fallback - Error in Packet Building"]
                }
            }
            logger.info(f"API: Using fallback simple packet for patient {patient_id}")
        
        # Load prompt template
        import os
        try:
            template_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'enhanced_prompt_template.txt')
            with open(template_path, 'r') as f:
                template = f.read()
        except Exception as e:
            logger.error(f"Failed to load enhanced prompt template: {e}")
            template = "SYSTEM\nYou are the Vizbriz OSA agent. Use only the provided JSON packet.\nProduce two sections in one response: (A) Clinical and (B) Operational.\n\nUSER\n## PATIENT EXECUTION PACKET (Enhanced JSON)\n<<<PACKET_JSON>>>\n\n## TASK\nReturn a single JSON object matching the schema in \"OUTPUT_SCHEMA\"."
        
        # Render the prompt
        from flask_app.config.vizbriz_prompt_helper import render_single_prompt
        prompt = render_single_prompt(packet, template)
        
        # Make LLM call with rate limiting protection
        from flask_app.config.bedrock_config import query_bedrock_claude_enhanced, get_bedrock_config
        import time
        
        config = get_bedrock_config("clinical_analysis")
        
        # Add delay to avoid rate limiting (5 requests per minute = 12 seconds between calls)
        time.sleep(2)  # Small delay to be safe
        
        messages = [{"role": "user", "content": prompt}]
        logger.info(f"Making LLM call for patient {patient_id}...")
        
        try:
            logger.info(f"API: Making LLM call for patient {patient_id} with config: {config}")
            response = query_bedrock_claude_enhanced(
                messages, 
                max_tokens=config["max_tokens"],
                temperature=config["temperature"], 
                top_p=config["top_p"],
                patient_id=patient_id
            )
            logger.info(f"API: LLM call completed for patient {patient_id}, success: {response.get('success', False)}")
            if response.get('success'):
                logger.info(f"API: LLM response length: {len(response.get('response', ''))}")
            else:
                logger.error(f"API: LLM call failed: {response}")
        except Exception as e:
            logger.error(f"API: LLM call failed for patient {patient_id}: {e}")
            import traceback
            logger.error(f"API: LLM call traceback: {traceback.format_exc()}")
            response = {"success": False, "error": str(e)}
        
        # If LLM call failed, try to return last known good value
        if not response.get('success'):
            logger.error(f"API: LLM call failed for patient {patient_id}: {response}")
            
            # Try to get last known good value from cache
            last_known_data = CacheService.cached_llm_data(patient_id, force_refresh=False)
            if last_known_data:
                logger.info(f"API: Returning last known LLM data for patient {patient_id} (LLM call failed)")
                return jsonify({
                    'success': True,
                    'clinical_summary': last_known_data.get('clinical_summary'),
                    'operational_summary': last_known_data.get('operational_summary'),
                    'ai_guidance': last_known_data.get('ai_guidance'),
                    'clinical_vm': last_known_data.get('clinical_vm'),
                    'operational_vm': last_known_data.get('operational_vm'),
                    'last_refreshed': last_known_data.get('last_refreshed'),
                    'debug_info': {
                        'cached': True,
                        'llm_call_success': False,
                        'using_last_known': True,
                        'llm_response_length': 0,
                        'packet_built': True
                    }
                })
            
            # No last known value available
            return jsonify({
                'success': False, 
                'error': f"LLM processing failed: {response.get('error', 'Unknown error')}"
            })
        
        # Initialize variables for successful response
        clinical_summary = None
        operational_summary = None
        ai_guidance = None
        clinical_vm = None
        operational_vm = None
        
        logger.info(f"API: Processing successful LLM response for patient {patient_id}")
        
        try:
            from flask_app.config.vizbriz_prompt_helper import parse_llm_json, basic_validate_response
            response_text = response.get('response', '')
            
            # Log the LLM response to see what we're getting
            logger.info(f"API: LLM response for patient {patient_id}: {len(response_text)} chars")
            logger.info(f"API: LLM response content: {response_text}")
            
            # Try to extract JSON from response
            if '```json' in response_text:
                start = response_text.find('```json') + 7
                end = response_text.find('```', start)
                if end > start:
                    inner_text = response_text[start:end].strip()
                    parsed_response = parse_llm_json(inner_text)
                else:
                    parsed_response = parse_llm_json(response_text)
            else:
                parsed_response = parse_llm_json(response_text)
            
            # Check if we have a valid parsed response
            if not parsed_response:
                logger.error(f"API: Failed to parse LLM response for patient {patient_id}")
                # Try to return last known good value
                last_known_data = CacheService.cached_llm_data(patient_id, force_refresh=False)
                if last_known_data:
                    logger.info(f"API: Returning last known LLM data for patient {patient_id} (parsing failed)")
                    return jsonify({
                        'success': True,
                        'clinical_summary': last_known_data.get('clinical_summary'),
                        'operational_summary': last_known_data.get('operational_summary'),
                        'ai_guidance': last_known_data.get('ai_guidance'),
                        'clinical_vm': last_known_data.get('clinical_vm'),
                        'operational_vm': last_known_data.get('operational_vm'),
                        'last_refreshed': last_known_data.get('last_refreshed'),
                        'debug_info': {
                            'cached': True,
                            'llm_call_success': False,
                            'using_last_known': True,
                            'llm_response_length': 0,
                            'packet_built': True
                        }
                    })
                return jsonify({
                    'success': False, 
                    'error': 'Failed to parse LLM response'
                })
            
            # Try validation, but don't fail if it returns None
            validation_result = basic_validate_response(parsed_response)
            logger.info(f"API: Validation result: {validation_result}")
            
            # Accept the response if it has the required structure, even if validation is None.
            # NOTE: operational may be null when derived_operational_state is absent per prompt guardrails.
            if not (validation_result is True or 
                   (parsed_response.get('clinical') and ('operational' in parsed_response))):
                logger.error(f"API: LLM response validation failed for patient {patient_id}")
                logger.error(f"API: Parsed response: {parsed_response}")
                # Try to return last known good value
                last_known_data = CacheService.cached_llm_data(patient_id, force_refresh=False)
                if last_known_data:
                    logger.info(f"API: Returning last known LLM data for patient {patient_id} (validation failed)")
                    return jsonify({
                        'success': True,
                        'clinical_summary': last_known_data.get('clinical_summary'),
                        'operational_summary': last_known_data.get('operational_summary'),
                        'ai_guidance': last_known_data.get('ai_guidance'),
                        'clinical_vm': last_known_data.get('clinical_vm'),
                        'operational_vm': last_known_data.get('operational_vm'),
                        'last_refreshed': last_known_data.get('last_refreshed'),
                        'debug_info': {
                            'cached': True,
                            'llm_call_success': False,
                            'using_last_known': True,
                            'llm_response_length': 0,
                            'packet_built': True
                        }
                    })
                return jsonify({
                    'success': False, 
                    'error': 'LLM response validation failed'
                })
            
            # Build view models from successful LLM response
            from flask_app.routes.main_routes import build_view_models_from_llm
            clinical_vm, operational_vm = build_view_models_from_llm(parsed_response, fallback_packet=packet)
            
            # Map the actual LLM response fields to our expected fields
            clinical_data = parsed_response.get('clinical') or {}
            operational_data = parsed_response.get('operational') or {}
            
            # Use diagnosis as clinical summary, or fall back to summary if it exists
            clinical_summary = clinical_data.get('diagnosis', clinical_data.get('summary', ''))
            if not clinical_summary:
                logger.error(f"API: No clinical summary found in LLM response for patient {patient_id}")
                # Try to return last known good value
                last_known_data = CacheService.cached_llm_data(patient_id, force_refresh=False)
                if last_known_data:
                    logger.info(f"API: Returning last known LLM data for patient {patient_id} (no clinical summary)")
                    return jsonify({
                        'success': True,
                        'clinical_summary': last_known_data.get('clinical_summary'),
                        'operational_summary': last_known_data.get('operational_summary'),
                        'ai_guidance': last_known_data.get('ai_guidance'),
                        'clinical_vm': last_known_data.get('clinical_vm'),
                        'operational_vm': last_known_data.get('operational_vm'),
                        'last_refreshed': last_known_data.get('last_refreshed'),
                        'debug_info': {
                            'cached': True,
                            'llm_call_success': False,
                            'using_last_known': True,
                            'llm_response_length': 0,
                            'packet_built': True
                        }
                    })
                return jsonify({
                    'success': False, 
                    'error': 'No clinical summary in LLM response'
                })
            
            # Use workflow_status as operational summary, or fall back to summary if it exists
            if parsed_response.get('operational') is None:
                operational_summary = "Operational data not provided in packet (operational_data_missing)."
            else:
                operational_summary = operational_data.get('workflow_status', operational_data.get('summary', ''))
                if not operational_summary:
                    # Create a summary from available operational data (only from LLM output, not guessed here)
                    stage = operational_data.get('stage', 'Not provided')
                    completion = operational_data.get('completion_pct', 0)
                    alerts = operational_data.get('alerts', [])
                    operational_summary = f"Stage: {stage}, {completion}% complete. Alerts: {', '.join(alerts[:2]) if alerts else 'None'}"
            
            # Use next_clinical_action as AI guidance, or fall back to ai_guidance if it exists
            ai_guidance = clinical_data.get('next_clinical_action', operational_data.get('ai_guidance', ''))
            if not ai_guidance:
                ai_guidance = clinical_summary  # Fallback to clinical summary
            
            logger.info(f"API: Successfully extracted LLM data for patient {patient_id}")
            logger.info(f"API: Clinical summary: '{clinical_summary}'")
            logger.info(f"API: Operational summary: '{operational_summary}'")
            logger.info(f"API: AI guidance: '{ai_guidance}'")
            
        except Exception as e:
            logger.error(f"API: LLM parsing error for patient {patient_id}: {e}")
            import traceback
            logger.error(f"API: LLM parsing traceback: {traceback.format_exc()}")
            
            # Try to return last known good value if parsing fails
            last_known_data = CacheService.cached_llm_data(patient_id, force_refresh=False)
            if last_known_data:
                logger.info(f"API: Returning last known LLM data for patient {patient_id} (parsing failed)")
                return jsonify({
                    'success': True,
                    'clinical_summary': last_known_data.get('clinical_summary'),
                    'operational_summary': last_known_data.get('operational_summary'),
                    'ai_guidance': last_known_data.get('ai_guidance'),
                    'clinical_vm': last_known_data.get('clinical_vm'),
                    'operational_vm': last_known_data.get('operational_vm'),
                    'debug_info': {
                        'cached': True,
                        'llm_call_success': False,
                        'using_last_known': True,
                        'llm_response_length': 0,
                        'packet_built': True
                    }
                })
            
            return jsonify({
                'success': False, 
                'error': f'LLM response parsing failed: {str(e)}'
            })
        
        # Log what we're returning to debug the issue
        logger.info(f"API: Returning LLM data for patient {patient_id}: clinical='{clinical_summary[:50]}...', operational='{operational_summary[:50]}...'")
        logger.info(f"API: Full response - clinical_summary: '{clinical_summary}', operational_summary: '{operational_summary}', ai_guidance: '{ai_guidance}'")
        logger.info(f"API: LLM call success: {response.get('success', False)}")
        logger.info(f"API: LLM response length: {len(response.get('response', ''))}")
        
        # Prepare response data with timestamp
        last_refreshed = datetime.now().isoformat()
        
        llm_response_data = {
            'clinical_summary': clinical_summary,
            'operational_summary': operational_summary,
            'ai_guidance': ai_guidance,
            'clinical_vm': clinical_vm,
            'operational_vm': operational_vm,
            'last_refreshed': last_refreshed
        }
        
        # Cache the LLM data for future requests (5 minutes timeout, same as execution manifest)
        CacheService.set_llm_data(patient_id, llm_response_data, timeout=300)
        logger.info(f"API: Cached LLM data for patient {patient_id}")
        
        return jsonify({
            'success': True,
            'clinical_summary': clinical_summary,
            'operational_summary': operational_summary,
            'ai_guidance': ai_guidance,
            'clinical_vm': clinical_vm,
            'operational_vm': operational_vm,
            'last_refreshed': last_refreshed,
            'debug_info': {
                'cached': False,
                'llm_call_success': response.get('success', False),
                'llm_response_length': len(response.get('response', '')),
                'packet_built': packet is not None
            }
        })
        
    except Exception as e:
        logger.error(f"Error loading LLM data for patient {patient_id}: {e}")
        return jsonify({'success': False, 'error': str(e)})


## LLM prompt/response review endpoint removed per request (UI no longer exposes it)


@main.route('/api/patient/<int:patient_id>/validate_manifest', methods=['POST'])
@login_required
def validate_patient_manifest_api(patient_id):
    """Validate and update patient manifest with auto-completion logic"""
    try:
        # Get patient and check permissions
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({'success': False, 'message': 'Patient not found'}), 404
        
        if not current_user.can_access_patient(patient):
            logger.warning(f"Unauthorized manifest validation attempt: patient {patient_id} by user {current_user.id}")
            return jsonify({'success': False, 'message': 'Unauthorized - You do not have permission to access this patient'}), 403
        
        # Import and run the manifest validator
        from flask_app.services.manifest_validator import ManifestValidatorService
        
        logger.info(f"Running manifest validation for patient {patient_id}")
        validation_results = ManifestValidatorService.validate_and_update_patient_stages(patient_id)
        
        if validation_results is None:
            return jsonify({'success': False, 'message': 'Failed to validate manifest'}), 500
        
        # Count completed stages
        completed_count = sum(1 for result in validation_results.values() if result.get('is_completed', False))
        total_stages = len(validation_results)
        
        logger.info(f"Manifest validation completed for patient {patient_id}: {completed_count}/{total_stages} stages completed")
        
        return jsonify({
            'success': True,
            'message': f'Manifest validated successfully: {completed_count}/{total_stages} stages completed',
            'completed_count': completed_count,
            'total_stages': total_stages,
            'validation_results': validation_results
        })
        
    except Exception as e:
        logger.error(f"Error validating manifest for patient {patient_id}: {e}")
        return jsonify({'success': False, 'message': f'Error validating manifest: {str(e)}'}), 500

@main.route('/api/bedrock/logs', methods=['GET'])
@login_required
def get_bedrock_logs():
    """Get Bedrock prompt and response logs for testing and reviewing"""
    try:
        from flask_app.config.bedrock_config import BedrockPromptLogger
        
        # Get query parameters
        hours = request.args.get('hours', 24, type=int)
        session_id = request.args.get('session_id')
        
        logger_instance = BedrockPromptLogger()
        
        if session_id:
            # Get specific session
            files = logger_instance.get_session_files(session_id)
            if files:
                return jsonify({
                    'success': True,
                    'session_id': session_id,
                    'data': files
                })
            else:
                return jsonify({
                    'success': False,
                    'message': f'Session {session_id} not found'
                }), 404
        else:
            # Get recent sessions
            sessions = logger_instance.list_recent_sessions(hours=hours)
            
            # Get performance analysis for each session
            session_analytics = {}
            for session_id in sessions.keys():
                analysis = logger_instance.analyze_session_performance(session_id)
                if analysis:
                    session_analytics[session_id] = analysis
            
            return jsonify({
                'success': True,
                'hours': hours,
                'sessions': sessions,
                'analytics': session_analytics
            })
            
    except Exception as e:
        logger.error(f"Error retrieving Bedrock logs: {e}")
        return jsonify({
            'success': False,
            'message': f'Error retrieving logs: {str(e)}'
        }), 500

@main.route('/api/bedrock/logs/<session_id>/performance', methods=['GET'])
@login_required
def get_session_performance(session_id):
    """Get performance analysis for a specific session"""
    try:
        from flask_app.config.bedrock_config import BedrockPromptLogger
        
        logger_instance = BedrockPromptLogger()
        analysis = logger_instance.analyze_session_performance(session_id)
        
        if analysis:
            return jsonify({
                'success': True,
                'session_id': session_id,
                'analysis': analysis
            })
        else:
            return jsonify({
                'success': False,
                'message': f'Session {session_id} not found or no data available'
            }), 404
            
    except Exception as e:
        logger.error(f"Error analyzing session performance: {e}")
        return jsonify({
            'success': False,
            'message': f'Error analyzing performance: {str(e)}'
        }), 500

@main.route('/api/ingest-delta', methods=['POST'])
@login_required
def api_ingest_delta():
    """Apply a delta update to a patient's canonical case JSON"""
    try:
        payload = request.get_json(silent=True) or {}
        patient_id = payload.get('patient_id')
        delta = payload.get('delta_json')
        if not patient_id or not isinstance(delta, dict):
            return jsonify(success=False, message='patient_id and delta_json required'), 400
        result = apply_delta_for_patient(int(patient_id), delta)
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"ingest-delta failed: {e}")
        return jsonify(success=False, message=str(e)), 500

@main.route('/api/backfill-canonical', methods=['POST'])
@login_required
def api_backfill_canonical():
    """Backfill canonical envelopes from complete per-report envelopes"""
    try:
        payload = request.get_json(silent=True) or {}
        patient_id = payload.get('patient_id')
        
        if patient_id:
            # Backfill single patient
            from flask_app.services.delta_ingest import backfill_canonical_from_complete_envelopes
            result = backfill_canonical_from_complete_envelopes(int(patient_id))
        else:
            # Backfill all patients
            from flask_app.services.delta_ingest import backfill_all_canonicals
            result = backfill_all_canonicals()
        
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"backfill-canonical failed: {e}")
        return jsonify(success=False, message=str(e)), 500

@main.route('/api/regenerate-canonical/<int:patient_id>', methods=['POST'])
@login_required
def api_regenerate_canonical(patient_id):
    """Regenerate canonical schema for a patient from observations (includes risk assessment from quiz)"""
    try:
        current_app.logger.info(f"Regenerating canonical schema for patient {patient_id}")
        
        from flask_app.config.document_observation_extractor_phase2 import create_minimal_canonical_json_for_patient
        result = create_minimal_canonical_json_for_patient(patient_id)
        
        if result.get('success'):
            current_app.logger.info(f"Successfully regenerated canonical for patient {patient_id}")
            return jsonify({
                'success': True,
                'message': f'Canonical schema regenerated for patient {patient_id}',
                'patient_id': patient_id
            })
        else:
            current_app.logger.error(f"Failed to regenerate canonical for patient {patient_id}: {result.get('message')}")
            return jsonify({
                'success': False,
                'message': result.get('message', 'Unknown error'),
                'patient_id': patient_id
            }), 500
    except Exception as e:
        current_app.logger.error(f"regenerate-canonical failed for patient {patient_id}: {e}")
        import traceback
        current_app.logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'message': str(e),
            'patient_id': patient_id
        }), 500