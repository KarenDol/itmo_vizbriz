from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from flask_app.models import db, Patient, File, Dentist, AdminFile, Claim, Comment, PatientStatus, StatusOption, PatientComment
from flask_app.extensions import db
import logging
import boto3 
from botocore.config import Config
import os
import logging
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
from flask_login import login_required, current_user
import tempfile
import secrets
import io
import traceback
import qrcode
from flask import send_file
from flask_app.models import Clinic, DSO, Lab, LabReference, dentist_clinic_association


# Create the Blueprint for wizard routes
wizard = Blueprint('wizard', __name__)
logger = logging.getLogger(__name__)
region = os.environ.get('AWS_REGION', 'us-west-2')
s3_client = boto3.client('s3', region_name=region, config=Config(signature_version='s3v4'))

SCAN_DOCUMENT_TYPES = [
    {'value': 'sleep_study', 'label': 'Sleep Study'},
    {'value': 'sleep_test', 'label': 'Sleep Test'},
    {'value': 'medical_background', 'label': 'Medical Background'},
    {'value': 'billing', 'label': 'Billing'},
    {'value': 'questionnaire', 'label': 'Questionnaire'},
    {'value': 'other', 'label': 'Other Document'}
]

SCAN_STORAGE_CONFIG = {
    'sleep_study': {'category': 'medical', 'subcategory': 'sleep-test', 'folder': 'medical/sleep-test'},
    'sleep_test': {'category': 'medical', 'subcategory': 'sleep-test', 'folder': 'medical/sleep-test'},
    'medical_background': {'category': 'medical', 'subcategory': 'medical-background', 'folder': 'medical/medical-background'},
    'billing': {'category': 'billing', 'subcategory': 'billing', 'folder': 'billing/billing'},
    'questionnaire': {'category': 'medical', 'subcategory': 'questionnaire', 'folder': 'medical/questionnaire'},
    'other': {'category': 'medical', 'subcategory': 'medical-background', 'folder': 'medical/medical-background'}
}


def _resolve_scan_storage(document_type: str):
    key = (document_type or 'other').strip().lower().replace('-', '_')
    return SCAN_STORAGE_CONFIG.get(key, SCAN_STORAGE_CONFIG['other'])


def _sanitized_document_name(document_name: str, fallback: str) -> str:
    safe = secure_filename(document_name or '')
    if not safe:
        safe = fallback
    if not safe.lower().endswith('.pdf'):
        safe = f"{safe}.pdf"
    return safe


def _images_to_pdf(files):
    pil_images = []
    for storage in files:
        if not storage or not storage.filename:
            continue
        storage.stream.seek(0)
        try:
            image = Image.open(storage.stream)
            image.load()
            if image.mode != 'RGB':
                image = image.convert('RGB')
            pil_images.append(image)
        except Exception as exc:
            logger.warning(f"Skipping image '{getattr(storage, 'filename', 'unknown')}': {exc}")
        finally:
            storage.stream.seek(0)
    if not pil_images:
        return None
    buffer = BytesIO()
    first, *rest = pil_images
    try:
        first.save(buffer, format='PDF', save_all=bool(rest), append_images=rest)
        buffer.seek(0)
        return buffer
    finally:
        for image in pil_images:
            image.close()


@wizard.route('/scan_upload_files', methods=['GET'])
@login_required
def scan_upload_files():
    dso_name = getattr(current_user, 'DSO', 'Your DSO')
    return render_template('scan_upload.html', dso_name=dso_name, document_types=SCAN_DOCUMENT_TYPES)


@wizard.route('/scan_upload_files/submit', methods=['POST'])
@login_required
def scan_upload_files_submit():
    try:
        patient_id = request.form.get('patient_id', type=int)
        if not patient_id:
            return jsonify({'success': False, 'message': 'Patient selection is required.'}), 400

        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({'success': False, 'message': 'Patient not found.'}), 404

        if current_user.role != 'admin' and patient.dentist_id not in {None, current_user.id}:
            return jsonify({'success': False, 'message': 'Unauthorized patient selection.'}), 403

        storage_config = _resolve_scan_storage(request.form.get('document_type', 'other'))
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        document_name = _sanitized_document_name(
            request.form.get('document_name'),
            f"{storage_config['subcategory']}_{timestamp}"
        )

        image_files = request.files.getlist('scan_pages[]') or []
        pdf_buffer = _images_to_pdf(image_files)

        if not pdf_buffer:
            return jsonify({'success': False, 'message': 'Please capture or upload at least one image before submitting.'}), 400

        pdf_bytes = pdf_buffer.getvalue()
        file_size = len(pdf_bytes)
        pdf_buffer.seek(0)

        bucket = os.getenv('S3_BUCKET_NAME')
        s3_key = f"patients/{patient_id}/{storage_config['folder']}/{document_name}"
        s3_client.upload_fileobj(pdf_buffer, bucket, s3_key, ExtraArgs={'ContentType': 'application/pdf'})
        pdf_buffer.close()

        new_file = File(
            name=document_name,
            patient_id=patient_id,
            file_type='application/pdf',
            file_size=file_size,
            s3_key=s3_key,
            category=storage_config['category'],
            subcategory=storage_config['subcategory']
        )
        db.session.add(new_file)
        patient.last_update = datetime.utcnow()
        db.session.commit()

        return jsonify({'success': True, 'message': 'Document uploaded successfully.', 'file_id': new_file.id})
    except Exception as exc:
        logger.error(f"Scan upload failed: {exc}")
        logger.error(traceback.format_exc())
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Failed to upload scanned document.'}), 500


@wizard.route('/scan_upload', methods=['GET'])
@login_required
def scan_upload():
    dso_name = getattr(current_user, 'DSO', 'Your DSO')
    return render_template('scan_upload.html', dso_name=dso_name)


@wizard.route('/wizard/stage1_personal_info', methods=['GET', 'POST'])
def stage1_personal_info():
    logger.info('Entered stage1_personal_info route')
    
    # Accept clinic_id from GET, POST, or session
    clinic_id = request.args.get('clinic_id') or request.form.get('clinic_id') or session.get('clinic_id')
    if clinic_id:
        session['clinic_id'] = clinic_id
    from flask_app.models import Clinic
    clinic = Clinic.query.get(clinic_id) if clinic_id else None
    clinic_dict = None
    if clinic:
        clinic_dict = {
            'id': clinic.id,
            'name': clinic.name,
            'email': clinic.email,
        }

    if request.method == 'POST':
        try:
            logger.info(f"POST data received: {request.form}")
            # Collect form data
            first_name = request.form.get('first_name')
            middle_name = request.form.get('middle_name')
            last_name = request.form.get('last_name')
            email = request.form.get('email')
            phone = request.form.get('phone')
            dob = request.form.get('dob')
            gender = request.form.get('gender')
            address = request.form.get('address')
            doctor_name = request.form.get('doctor_name')

            # Save form data to the session for future use
            session['personal_info'] = {
                'first_name': first_name,
                'middle_name': middle_name,
                'last_name': last_name,
                'email': email,
                'phone': phone,
                'dob': dob,
                'gender': gender,
                'address': address,
                'doctor_name': doctor_name
            }

            # Parse the date of birth safely
            dob_parsed = datetime.strptime(dob, '%Y-%m-%d') if dob else None

            # Get dentist_id based on clinic_id from session
            dentist_id = None
            if clinic_id:
                # Find a dentist associated with this clinic using the many-to-many relationship
                from flask_app.models import Dentist
                dentist = Dentist.query.join(Dentist.clinics).filter_by(id=clinic_id).first()
                if dentist:
                    dentist_id = dentist.id
                    logger.info(f"Found dentist ID {dentist_id} for clinic {clinic_id}")
                else:
                    # If no dentist found for this clinic, try to find any dentist
                    dentist = Dentist.query.first()
                    if dentist:
                        dentist_id = dentist.id
                        logger.info(f"Using fallback dentist ID {dentist_id}")
            
            # If still no dentist_id, leave as None
            if not dentist_id:
                dentist_id = None  # No default dentist ID
                logger.info(f"No dentist ID found, leaving as None")

            # Check if a patient with this email already exists
            existing_patient = Patient.query.filter_by(email=email).first()

            if existing_patient:
                logger.info(f"Existing patient found with email {email}, updating patient ID: {existing_patient.id}")
                # Update existing patient info
                existing_patient.name = f"{first_name} {middle_name} {last_name}".strip()
                existing_patient.phone = phone
                existing_patient.dob = dob_parsed
                existing_patient.gender = gender
                existing_patient.address = address
                existing_patient.last_update = datetime.now()
                if dentist_id:
                    existing_patient.dentist_id = dentist_id
                if clinic_id:
                    existing_patient.clinic_id = clinic_id
                # Optionally update upload_token if you want to allow new uploads
                existing_patient.upload_token = secrets.token_urlsafe(32)
                db.session.add(existing_patient)
                db.session.flush()
                session['patient_id'] = existing_patient.id
                upload_token = existing_patient.upload_token
                logger.info(f"Updated existing patient with ID: {existing_patient.id}")
                base_path = f"patients/{existing_patient.id}/billing/billing"
                flash('An account with this email already exists. Your information has been updated.', 'info')
            else:
                logger.info(f"No existing patient found with email {email}, creating new patient.")
                # Create a new Patient record with upload_token
                upload_token = secrets.token_urlsafe(32)
                new_patient = Patient(
                    name=f"{first_name} {middle_name} {last_name}".strip(),
                    dentist_id=dentist_id,
                    email=email,
                    phone=phone,
                    dob=dob_parsed,
                    gender=gender,
                    address=address,
                    create_date=datetime.now(),
                    last_update=datetime.now(),
                    status='New',
                    upload_token=upload_token,
                    clinic_id=clinic_id
                )
                db.session.add(new_patient)
                db.session.flush()
                session['patient_id'] = new_patient.id
                logger.info(f"Created new patient with ID: {new_patient.id} and upload token: {upload_token}")
                base_path = f"patients/{new_patient.id}/billing/billing"
                flash('New account created successfully!', 'success')

            # Bucket name for uploads
            bucket_name = os.getenv('S3_BUCKET_NAME')

            # Handle file uploads with specific filenames
            driver_license_front = request.files.get('driver_license_front')
            driver_license_back = request.files.get('driver_license_back')
            insurance_front = request.files.get('insurance_front')
            insurance_back = request.files.get('insurance_back')

            # Helper function to upload files
            def upload_file(file_obj, file_description, patient_id):
                if file_obj:
                    # Get the file extension
                    original_filename = secure_filename(file_obj.filename)
                    file_ext = os.path.splitext(original_filename)[1]
                    if not file_ext:
                        raise ValueError(f"No file extension found for {file_description}.")

                    # Use the file_description as the base filename
                    base_filename = secure_filename(file_description)
                    filename = f"{base_filename}{file_ext}"

                    # Calculate file size
                    file_size = len(file_obj.read())
                    file_obj.seek(0)  # Reset file pointer after reading

                    # Generate S3 key
                    s3_key = f"patients/{patient_id}/billing/billing/{filename}"

                    # Upload to S3
                    s3_client.upload_fileobj(
                        file_obj,
                        bucket_name,
                        s3_key,
                        ExtraArgs={'ContentType': file_obj.mimetype}
                    )

                    logger.debug(f"Uploaded {file_description} file to S3 at {s3_key}")

                    # Save file info to database
                    new_file = File(
                        name=filename,
                        patient_id=patient_id,
                        file_type=file_obj.mimetype,
                        file_size=file_size,
                        s3_key=s3_key,
                        category='billing',
                        subcategory='billing'
                    )
                    db.session.add(new_file)

            patient_id = session['patient_id']
            upload_file(driver_license_front, 'driver_license_front', patient_id)
            upload_file(driver_license_back, 'driver_license_back', patient_id)
            upload_file(insurance_front, 'insurance_front', patient_id)
            upload_file(insurance_back, 'insurance_back', patient_id)

            # Commit the transaction to save the patient and files in the database
            db.session.commit()

            flash('Personal information submitted successfully!', 'success')
            return redirect(url_for('wizard.stage2_epworth_survey'))

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error in stage1_personal_info: {str(e)}")
            flash(f"An error occurred: {str(e)}", 'danger')
            return render_template('wizard/stage1_personal_info.html', personal_info=session.get('personal_info', {}), clinic=clinic, clinic_dict=clinic_dict)

    # Pass session data to prepopulate the form if available
    return render_template('wizard/stage1_personal_info.html', personal_info=session.get('personal_info', {}), clinic=clinic, clinic_dict=clinic_dict)





from fpdf import FPDF

@wizard.route('/wizard/stage2_epworth_survey', methods=['GET', 'POST'])
def stage2_epworth_survey():
    if request.method == 'POST':
        try:
            # Retrieve patient ID from the session
            patient_id = session.get('patient_id')
            if not patient_id:
                raise ValueError("Patient ID not found in session")

            # Retrieve the patient record from the database
            patient = Patient.query.get(patient_id)
            if not patient:
                raise ValueError("Patient not found")

            # Get the current date
            current_date = datetime.now().strftime("%Y-%m-%d")

            # Collect form data for all situations
            form_data = {
                'Sitting and reading': request.form.get('situation_1'),
                'Watching TV': request.form.get('situation_2'),
                'Sitting inactive in a public place (e.g., meeting or theater)': request.form.get('situation_3'),
                'Passenger in a car for an hour without a break': request.form.get('situation_4'),
                'Lying down to rest in the afternoon': request.form.get('situation_5'),
                'Sitting and talking to someone': request.form.get('situation_6'),
                'Sitting quietly after a lunch without alcohol': request.form.get('situation_7'),
                'In a car, while stopped for a few minutes in traffic': request.form.get('situation_8')
            }

            # Mapping for full answer text
            answer_mapping = {
                '0': '0 = would never doze or sleep',
                '1': '1 = slight chance of dozing or sleeping',
                '2': '2 = moderate chance of dozing or sleeping',
                '3': '3 = high chance of dozing or sleeping'
            }

            # Calculate total score
            total_score = sum(int(value) for value in form_data.values() if value)

            # Interpretation based on the total score
            if total_score <= 7:
                interpretation = "It is unlikely that you are abnormally sleepy."
            elif total_score <= 9:
                interpretation = "You have an average amount of daytime sleepiness."
            elif total_score <= 15:
                interpretation = "You may be excessively sleepy depending on the situation. You may want to consider seeking medical attention."
            else:
                interpretation = "You are excessively sleepy and should consider seeking medical attention."

            # Generate a PDF file from the form data
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Arial", size=12)

            # Add patient name and date at the top
            pdf.cell(0, 10, txt=f"Patient Name: {patient.name}", ln=True)
            pdf.cell(0, 10, txt=f"Date: {current_date}", ln=True)
            pdf.ln(10)  # Add space

            pdf.cell(200, 10, txt="Epworth Sleepiness Scale", ln=True, align='C')
            pdf.ln(10)  # Add space

            # Add questions and answers
            for question, value in form_data.items():
                full_answer = answer_mapping.get(value, 'N/A')
                pdf.cell(0, 10, txt=f"{question}: {full_answer}", ln=True)

            pdf.ln(10)  # Add space before total score
            pdf.cell(0, 10, txt=f"Total Score: {total_score}", ln=True)
            pdf.cell(0, 10, txt=f"Interpretation: {interpretation}", ln=True)

            # Save the PDF to a temporary file
            pdf_file_name = f"epworth_survey_patient_{patient_id}.pdf"
            temp_fd, temp_path = tempfile.mkstemp(suffix='.pdf')
            try:
                with os.fdopen(temp_fd, 'wb') as f:
                    pdf_content = pdf.output(dest='S')  # Get PDF content as string
                    f.write(pdf_content.encode('latin-1'))  # Encode to bytes and write to file
                logger.debug(f"PDF saved to {temp_path}")

                # Upload the PDF to S3
                bucket_name = os.getenv('S3_BUCKET_NAME')
                s3_key = f"patients/{patient_id}/billing/billing/{pdf_file_name}"

                with open(temp_path, 'rb') as pdf_file:
                    s3_client.upload_fileobj(
                        pdf_file,
                        bucket_name,
                        s3_key,
                        ExtraArgs={'ContentType': 'application/pdf'}
                    )
                    logger.debug(f"Uploaded PDF file to S3 at {s3_key}")

                # Save PDF metadata to the database
                new_file = File(
                    name=pdf_file_name,
                    patient_id=patient_id,
                    file_type='application/pdf',
                    file_size=os.path.getsize(temp_path),
                    s3_key=s3_key,
                    category='billing',
                    subcategory='billing'
                )
                db.session.add(new_file)
                db.session.commit()

                flash('Epworth Sleepiness Scale submitted and saved successfully!', 'success')
                return redirect(url_for('wizard.stage3_sleep_info'))

            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)  # Clean up temp file

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error in stage2_epworth_survey: {str(e)}")
            flash(f"An error occurred: {str(e)}", 'danger')
            return render_template('wizard/stage2_epworth_survey.html')

    return render_template('wizard/stage2_epworth_survey.html')


from datetime import datetime, timedelta

from flask import (
    render_template, session, request, redirect, url_for, flash
)


@wizard.route('/wizard/stage3_sleep_info', methods=['GET', 'POST']) 
def stage3_sleep_info():
    if request.method == 'POST':
        try:
            # Retrieve the patient ID from the session
            patient_id = session.get('patient_id')
            if not patient_id:
                raise ValueError("Patient ID not found in session.")

            # Collect form data
            sleep_test = request.form.get('sleep_test')
            sleep_test_date = request.form.get('sleep_test_date') if sleep_test == 'Yes' else None
            doctor_name = request.form.get('doctor_name') if sleep_test == 'Yes' else None

            # Determine the sleep test status and recency
            is_recent_test = False
            sleep_test_date_obj = None
            sleep_test_status = "no_test"  # Default to no test

            if sleep_test == 'Yes':
                if sleep_test_date:
                    sleep_test_date_obj = datetime.strptime(sleep_test_date, '%Y-%m-%d')
                    one_year_ago = datetime.now() - timedelta(days=365)
                    is_recent_test = sleep_test_date_obj > one_year_ago
                    sleep_test_status = "recent_test" if is_recent_test else "old_test"

            # Update session with sleep test information
            session['sleep_info'] = {
                'sleep_test': sleep_test,
                'sleep_test_date': sleep_test_date,
                'is_recent_test': is_recent_test,
                'status': sleep_test_status
            }
            logger.debug(f"Session sleep_info updated: {session['sleep_info']}")

            # Retrieve the patient record from the database
            patient = Patient.query.get(patient_id)
            if not patient:
                raise ValueError("Patient not found in the database.")

            # Update the database with patient's sleep information
            patient.sleep_study = sleep_test
            patient.sleep_study_date = sleep_test_date_obj
            patient.sleep_study_doctor = doctor_name
            patient.last_update = datetime.now()
            db.session.add(patient)

            # Helper function to upload files (move to outer scope if needed)
            def upload_file(file_obj, file_description, patient_id, category='billing', subcategory='billing'):
                if file_obj:
                    original_filename = secure_filename(file_obj.filename)
                    file_ext = os.path.splitext(original_filename)[1]
                    if not file_ext:
                        raise ValueError(f"No file extension found for {file_description}.")
                    base_filename = secure_filename(file_description)
                    filename = f"{base_filename}{file_ext}"
                    file_size = len(file_obj.read())
                    file_obj.seek(0)
                    if category == 'billing':
                        s3_key = f"patients/{patient_id}/billing/billing/{filename}"
                    else:
                        s3_key = f"patients/{patient_id}/medical/sleep_test/{filename}"
                    s3_client.upload_fileobj(
                        file_obj,
                        bucket_name,
                        s3_key,
                        ExtraArgs={'ContentType': file_obj.mimetype}
                    )
                    logger.debug(f"Uploaded {file_description} file to S3 at {s3_key}")
                    new_file = File(
                        name=filename,
                        patient_id=patient_id,
                        file_type=file_obj.mimetype,
                        file_size=file_size,
                        s3_key=s3_key,
                        category=category,
                        subcategory=subcategory
                    )
                    db.session.add(new_file)

            # Handle file uploads only for patients with a sleep test
            if sleep_test == 'Yes':
                bucket_name = os.getenv('S3_BUCKET_NAME')
                # Upload sleep_test_result to medical/sleep_test
                sleep_test_result = request.files.get('sleep_test_result')
                upload_file(sleep_test_result, 'sleep_test_result', patient_id, category='medical', subcategory='sleep-test')
                # Upload md_reference and oral_appliance_prescription to billing/billing
                for file_key in ['md_reference', 'oral_appliance_prescription']:
                    file_obj = request.files.get(file_key)
                    upload_file(file_obj, file_key, patient_id, category='billing', subcategory='billing')

            # Commit database changes
            db.session.commit()

            flash('Sleep information submitted and saved successfully!', 'success')
            return redirect(url_for('wizard.stage4_cpap_intolerance'))

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error in stage3_sleep_info: {str(e)}")
            flash(f"An error occurred: {str(e)}", 'danger')
            return render_template('wizard/stage3_sleep_info.html')

    return render_template('wizard/stage3_sleep_info.html')




@wizard.route('/wizard/stage4_cpap_intolerance', methods=['GET', 'POST'])
def stage4_cpap_intolerance():
    if request.method == 'POST':
        try:
            # Hardcoded user ID for testing (replace with actual user session data)
            patient_id = session.get('patient_id')

            # Retrieve the form screenshot from the request
            form_screenshot_base64 = request.form.get('form_screenshot')
            if form_screenshot_base64:
                logger.info("Form screenshot received, processing...")

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

                # Upload the image to S3
                bucket_name = os.getenv('S3_BUCKET_NAME')
                if bucket_name:
                    s3_key = f"patients/{patient_id}/billing/billing/{screenshot_filename}"
                    with open(screenshot_path, 'rb') as img_file:
                        s3_client.upload_fileobj(
                            img_file,
                            bucket_name,
                            s3_key,
                            ExtraArgs={'ContentType': 'image/png'}
                        )
                    logger.info(f"Screenshot uploaded to S3 at {s3_key}")

                    # Save metadata to the database
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

            flash('CPAP Intolerance form submitted and saved successfully!', 'success')
            return redirect(url_for('wizard.stage5_informed_consent'))

        except Exception as e:
            # Rollback database changes if applicable and log the error
            db.session.rollback()
            logger.error(f"Error in stage4_cpap_intolerance: {str(e)}")
            flash(f"An error occurred: {str(e)}", 'danger')
            return render_template('wizard/stage4_cpap_intolerance.html')

    return render_template('wizard/stage4_cpap_intolerance.html')


@wizard.route('/wizard/stage5_informed_consent', methods=['GET', 'POST'])
def stage5_informed_consent():
    if request.method == 'POST':
        try:
            # Hardcoded user ID for testing (replace with actual user session data)
           # patient_id = session.get('patient_id')
            patient_id = session.get('patient_id')
            # Retrieve the form screenshot from the request
            form_screenshot_base64 = request.form.get('form_screenshot')
            if form_screenshot_base64:
                logger.info("Form screenshot received, processing...")
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

                # Upload the image to S3
                bucket_name = os.getenv('S3_BUCKET_NAME')
                if bucket_name:
                    s3_key = f"patients/{patient_id}/billing/billing/{screenshot_filename}"
                    with open(screenshot_path, 'rb') as img_file:
                        s3_client.upload_fileobj(
                            img_file,
                            bucket_name,
                            s3_key,
                            ExtraArgs={'ContentType': 'image/png'}
                        )
                    logger.info(f"Screenshot uploaded to S3 at {s3_key}")

                    # Save metadata to the database
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

            flash('Informed Consent form submitted and saved successfully!', 'success')
            return redirect(url_for('wizard.stage6_hipaa_authorization'))  # Replace with the next step as appropriate

        except Exception as e:
            # Rollback database changes if applicable and log the error
            db.session.rollback()
            logger.error(f"Error in stage5_informed_consent: {str(e)}")
            flash(f"An error occurred: {str(e)}", 'danger')
            return render_template('wizard/stage5_informed_consent.html')

    return render_template('wizard/stage5_informed_consent.html')


@wizard.route('/wizard/stage6_hipaa_authorization', methods=['GET', 'POST'])
def stage6_hipaa_authorization():
    clinic_id = session.get('clinic_id')
    from flask_app.models import Clinic
    clinic = Clinic.query.get(clinic_id) if clinic_id else None
    clinic_dict = None
    if clinic:
        clinic_dict = {
            'id': clinic.id,
            'name': clinic.name,
            'email': clinic.email,
        }
    if request.method == 'POST':
        try:
            patient_id = session.get('patient_id')
            form_screenshot_base64 = request.form.get('form_screenshot')
            if form_screenshot_base64:
                logger.info("Form screenshot received, processing...")
                form_screenshot_base64 = form_screenshot_base64.split(',')[1]
                form_screenshot_data = base64.b64decode(form_screenshot_base64)
                screenshot_filename = f"hipaa_authorization_form_{patient_id}.png"
                screenshot_path = os.path.join(tempfile.gettempdir(), screenshot_filename)
                with open(screenshot_path, 'wb') as img_file:
                    img_file.write(form_screenshot_data)
                logger.info(f"Screenshot saved locally at {screenshot_path}")
                bucket_name = os.getenv('S3_BUCKET_NAME')
                if bucket_name:
                    s3_key = f"patients/{patient_id}/billing/billing/{screenshot_filename}"
                    with open(screenshot_path, 'rb') as img_file:
                        s3_client.upload_fileobj(
                            img_file,
                            bucket_name,
                            s3_key,
                            ExtraArgs={'ContentType': 'image/png'}
                        )
                    logger.info(f"Screenshot uploaded to S3 at {s3_key}")
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
            flash('HIPAA authorization form submitted and saved successfully!', 'success')
            return redirect(url_for('wizard.end_wizard'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error in hipaa_authorization: {str(e)}")
            flash(f"An error occurred: {str(e)}", 'danger')
            return render_template('wizard/stage6_hipaa_authorization.html', clinic=clinic, clinic_dict=clinic_dict)
    return render_template('wizard/stage6_hipaa_authorization.html', clinic=clinic, clinic_dict=clinic_dict)

@wizard.route('/wizard/end_wizard')
def end_wizard():
    # Log session sleep_info for debugging
    sleep_info = session.get('sleep_info', {})
    logger.debug(f"Session sleep_info at end_wizard: {sleep_info}")

    # Fetch patient and clinic for dynamic branding
    patient_id = session.get('patient_id')
    patient = None
    clinic = None
    if patient_id:
        from flask_app.models import Patient, Clinic
        patient = Patient.query.get(patient_id)
        if patient and patient.clinic_id:
            clinic = Clinic.query.get(patient.clinic_id)
    # Fallback: if no clinic, use a default or None
    if not clinic:
        clinic = None

    # Extract sleep test data
    sleep_test = sleep_info.get('sleep_test')
    sleep_test_date = sleep_info.get('sleep_test_date')
    is_recent_test = sleep_info.get('is_recent_test')

    logger.debug(f"Evaluating sleep_test: {sleep_test}, sleep_test_date: {sleep_test_date}, is_recent_test: {is_recent_test}")

    if sleep_test == 'Yes' and is_recent_test:
        logger.info("User has a recent sleep test. Proceeding to completion message.")
        return render_template('wizard/end_wizard.html', message="Your sleep test is recent. Thank you for completing the onboarding.", clinic=clinic)
    elif sleep_test == 'Yes' and not is_recent_test:
        logger.info("User's sleep test is older than a year. Informing about new test requirement.")
        return render_template(
            'wizard/end_wizard.html',
            message="Your sleep test is over one year old. Please complete a new sleep test to continue.",
            clinic=clinic
        )
    elif sleep_test == 'No' or not sleep_test_date:
        logger.info("User has not completed a sleep test. Requesting completion.")
        return render_template(
            'wizard/end_wizard.html',
            message="It appears you have not had a sleep test. Please schedule a test to proceed.",
            clinic=clinic
        )
    else:
        logger.warning("Unable to determine sleep test status. Debugging required.")
        return render_template(
            'wizard/end_wizard.html',
            message="Unable to determine your sleep test status. Please contact support.",
            clinic=clinic
        )

@wizard.route('/direct_file_upload', methods=['GET', 'POST'])
@login_required
def direct_file_upload():
    """
    Render the upload template and handle file uploads for both new and existing users.
    """
    if request.method == 'GET':
        try:
            # Get the DSO name from the current user (default to "Your DSO" if not present)
            dso_name = getattr(current_user, 'DSO', 'Your DSO')

            # Render the HTML template and pass the DSO name
            return render_template('direct_file_upload.html', dso_name=dso_name)

        except Exception as e:
            logger.error(f"Error loading upload page: {e}")
            return render_template('error.html', message="An error occurred while loading the upload page."), 500

    elif request.method == 'POST':
        try:
            # Check if patient_id is provided (update existing user)
            patient_id = request.form.get('patient_id')
            if not patient_id:
                # Create a new patient (new user use case)
                name = request.form.get('name')
                email = request.form.get('email')
                phone = request.form.get('phone')
                if not name or not email:
                    return jsonify({'error': 'Name and email are required for new user creation.'}), 400

                # Debug: print current user info
                logger.info(f"Current user: {current_user}")
                logger.info(f"Current user id: {getattr(current_user, 'id', None)}")

                new_patient = Patient(
                    name=name,
                    email=email,
                    phone=phone,
                    dentist_id=current_user.id,  # <-- This should not be None!
                    create_date=datetime.now(),
                    last_update=datetime.now(),
                    status='New'
                )
                db.session.add(new_patient)
                db.session.flush()  # Get the new patient ID
                patient_id = new_patient.id
                logger.info(f"Created new patient with ID: {patient_id}")

            # Validate if any file is uploaded
            if not request.files:
                return jsonify({'error': 'No files provided for upload'}), 400

            bucket_name = os.getenv('S3_BUCKET_NAME')
            uploaded_files_info = []

            # Process each file grouped by status type
            file_groups = list(request.files.lists())
            logger.info(f"Processing upload request with {len(file_groups)} file groups")
            for key, files in file_groups:
                status = key.rstrip('[]')  # Extract status type from input name
                logger.info(f"Processing files for status: {status}, count: {len(files)}")

                # Determine base path based on status type
                if 'sleep' in status.lower():
                    base_path = f"patients/{patient_id}/medical/sleep-test"
                    category = 'medical'
                    subcategory = 'sleep-test'
                elif 'clinical_pictures' in status.lower():
                    base_path = f"patients/{patient_id}/imaging/clinical-pictures"
                    category = 'imaging'
                    subcategory = 'clinical-pictures'
                else:
                    base_path = f"patients/{patient_id}/billing/billing"
                    category = 'billing'
                    subcategory = 'billing'

                file_count = 1  # To handle multiple files for the same status type
                for file in files:
                    if not file or not file.filename.strip():
                        continue

                    # Get the original file extension
                    original_filename = secure_filename(file.filename)
                    file_ext = os.path.splitext(original_filename)[1]
                    if not file_ext:
                        file_ext = '.jpg'  # Default extension for images
                    
                    # Create filename with proper extension
                    filename = f"{status}_{file_count}{file_ext}"
                    file_count += 1

                    s3_key = f"{base_path}/{filename}"

                    # Upload the file to S3
                    file_size = len(file.read())
                    file.seek(0)  # Reset file pointer after reading

                    s3_client.upload_fileobj(
                        file,
                        bucket_name,
                        s3_key,
                        ExtraArgs={'ContentType': file.mimetype}
                    )
                    logger.debug(f"Uploaded {filename} to S3 at {s3_key}")

                    # Save file information in the database
                    new_file = File(
                        name=filename,
                        patient_id=patient_id,
                        file_type=file.mimetype,
                        file_size=file_size,
                        s3_key=s3_key,
                        category=category,
                        subcategory=subcategory
                    )
                    db.session.add(new_file)
                    logger.info(f"Added file to database: {filename}, category: {category}, subcategory: {subcategory}")

                    uploaded_files_info.append({
                        'filename': filename,
                        's3_key': s3_key,
                        'status': status,
                        'category': category,
                        'subcategory': subcategory
                    })

            # Commit database changes
            db.session.commit()

            return jsonify({'message': 'Files uploaded successfully!', 'patient_id': patient_id, 'uploaded_files': uploaded_files_info}), 200

        except Exception as e:
            logger.error(f"Error during file upload: {str(e)}")
            logger.error(f"Error type: {type(e).__name__}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            db.session.rollback()
            return jsonify({'error': f'An unexpected error occurred during file upload: {str(e)}'}), 500

@wizard.route('/wizard/options', methods=['GET', 'POST'])
def wizard_options():
    is_admin = current_user.is_authenticated and getattr(current_user, "role", None) == "admin"

    # Context patient: URL on GET; on POST, form wins (sending to selected patient)
    context_patient_id = request.args.get("patient_id", type=int)
    if request.method == "POST":
        form_pid = request.form.get("selected_patient_id", type=int)
        if form_pid:
            context_patient_id = form_pid

    clinic = None
    from flask_app.models import Clinic  # local import kept for compatibility with this block

    # 1) Admin: use clinic tied to the patient when we have a patient id
    if is_admin and context_patient_id:
        pat = Patient.query.get(context_patient_id)
        if pat and pat.clinic_id:
            c = Clinic.query.get(pat.clinic_id)
            if c and getattr(c, "status", None) == "active":
                clinic = c
                session["clinic_id"] = c.id

    # 2) Admin: explicit ?clinic_id= / form clinic_id
    if not clinic and is_admin:
        explicit_cid = request.args.get("clinic_id", type=int)
        if request.method == "POST":
            form_cid = request.form.get("clinic_id", type=int)
            if form_cid:
                explicit_cid = form_cid
        if explicit_cid:
            c = Clinic.query.get(explicit_cid)
            if c and getattr(c, "status", None) == "active":
                clinic = c
                session["clinic_id"] = c.id

    # 3) Session + dentist associations (non-admins: enforce association list)
    if not clinic:
        clinic_id = session.get("clinic_id")
        if (
            not is_admin
            and clinic_id
            and hasattr(current_user, "get_clinic_ids")
            and current_user.get_clinic_ids()
            and clinic_id not in current_user.get_clinic_ids()
        ):
            clinic_id = None
        if not clinic_id and hasattr(current_user, "get_clinic_ids"):
            assoc_ids = current_user.get_clinic_ids()
            if assoc_ids:
                clinic_id = assoc_ids[0]
        if clinic_id:
            c = Clinic.query.get(clinic_id)
            if c and (not is_admin or getattr(c, "status", None) == "active"):
                clinic = c

    # 4) Admin: first active clinic as last resort
    if not clinic and is_admin:
        c = Clinic.query.filter(Clinic.status == "active").order_by(Clinic.id).first()
        if c:
            clinic = c
            session["clinic_id"] = c.id

    clinic_dict = None
    if clinic:
        clinic_dict = {
            "id": clinic.id,
            "name": clinic.name,
            "email": clinic.email,
        }

    # Handle case where no clinic is found
    if not clinic:
        active_clinics_err = (
            Clinic.query.filter(Clinic.status == "active").order_by(Clinic.name).all()
        )
        if is_admin:
            if not active_clinics_err:
                error_message = "No active clinics in the system. Add a clinic before using Patient Forms."
            elif context_patient_id:
                pat = Patient.query.get(context_patient_id)
                if pat and not pat.clinic_id:
                    error_message = (
                        "This patient has no clinic on file. Set their clinic in the patient record, or open "
                        "this page with ?clinic_id=<id> to pick a practice for links and email."
                    )
                else:
                    error_message = (
                        "Could not use that patient’s clinic. Try ?clinic_id= with an active clinic, or another patient."
                    )
            else:
                error_message = (
                    "No clinic is available. Select a patient who has a clinic, add ?clinic_id= to the URL, or create an active clinic."
                )
        elif hasattr(current_user, "get_clinic_ids") and current_user.get_clinic_ids():
            error_message = f"Clinic not found. Available clinic ids: {current_user.get_clinic_ids()}"
        else:
            error_message = (
                "No clinic associations found for this user. Please contact an administrator to associate you with a clinic."
            )
        base_url = os.getenv("BASE_URL") or (request.host_url.rstrip("/") if request else "https://app.vizbriz.com")
        return render_template(
            "wizard/wizard_options.html",
            clinic=None,
            clinic_dict=None,
            error_message=error_message,
            base_url=base_url,
            is_admin=is_admin,
            context_patient_id=context_patient_id,
            active_clinics=active_clinics_err,
        )
    
    # Handle POST request for sending email
    if request.method == 'POST':
        send_type = request.form.get('send_type')
        patient_email = request.form.get('patient_email')
        
        if patient_email:
            try:
                # Generate the appropriate link based on send_type
                if send_type == 'quiz':
                    quiz_base = os.getenv('BASE_URL') or request.host_url.rstrip('/')
                    quiz_language = (request.form.get('quiz_language') or 'en').strip().lower()
                    if quiz_language == 'he':
                        dso_id = getattr(clinic, 'dso_id', None)
                        if isinstance(dso_id, int) and dso_id > 0:
                            quiz_link = f"{quiz_base}/vizbriz/quiz_hebrew?dso_id={dso_id}&clinic_id={clinic.id}"
                            if current_user.is_authenticated and getattr(current_user, 'id', None):
                                quiz_link += f"&dentist_id={current_user.id}"
                        else:
                            quiz_link = f"{quiz_base}/vizbriz/quiz_hebrew"
                        email_subject = "שאלון הערכת שינה"
                        email_body = f"""שלום,

אנא מלא/י את השאלון בקישור:
{quiz_link}

תודה,
{clinic.name}"""
                        email_html = f"""<html><body dir="rtl">
<p>שלום,</p>
<p>אנא מלא/י את השאלון בקישור:</p>
<p><a href="{quiz_link}">{quiz_link}</a></p>
<p>תודה,<br>{clinic.name}</p>
</body></html>"""
                    else:
                        # Quiz link (English)
                        dso_id = getattr(clinic, 'dso_id', None)
                        if isinstance(dso_id, int) and dso_id > 0:
                            quiz_link = f"{quiz_base}/vizbriz/quiz?lang=en&dso_id={dso_id}&clinic_id={clinic.id}"
                            if current_user.is_authenticated and getattr(current_user, 'id', None):
                                quiz_link += f"&dentist_id={current_user.id}"
                        else:
                            quiz_link = f"{quiz_base}/vizbriz/quiz?lang=en"
                        email_subject = "Sleep Apnea Assessment Quiz"
                        email_body = f"""Hello,

Please complete the Sleep Apnea Assessment Quiz:
{quiz_link}

{clinic.name}"""
                        email_html = f"""<html><body>
<p>Hello,</p>
<p>Please complete the Sleep Apnea Assessment Quiz:</p>
<p><a href="{quiz_link}">{quiz_link}</a></p>
<p>{clinic.name}</p>
</body></html>"""
                elif send_type == 'followup':
                    followup_language = (request.form.get('followup_language') or 'en').strip().lower()
                    base_url = os.getenv('BASE_URL') or (request.host_url.rstrip('/') if request else 'https://app.vizbriz.com')
                    dso_id = getattr(clinic, 'dso_id', None)
                    followup_params = []
                    if followup_language != 'he':
                        followup_params.append('lang=en')
                    if isinstance(dso_id, int) and dso_id > 0:
                        followup_params.append(f'dso_id={dso_id}')
                        followup_params.append(f'clinic_id={clinic.id}')
                    if current_user.is_authenticated and getattr(current_user, 'id', None):
                        followup_params.append(f'dentist_id={current_user.id}')
                    followup_path = '/vizbriz/followup_hebrew' if followup_language == 'he' else '/vizbriz/followup'
                    followup_link = f"{base_url}{followup_path}" + (('?' + '&'.join(followup_params)) if followup_params else '')
                    followup_link = _append_query_to_url(
                        followup_link,
                        _followup_prefill_query_dict(
                            patient_email=patient_email,
                            patient_id=request.form.get('selected_patient_id'),
                        ),
                    )
                    if followup_language == 'he':
                        email_subject = "שאלון מעקב ראשון לאחר קבלת התקן אוראלי"
                        email_body = f"""שלום,

ההתקן האוראלי שלך לטיפול בדום נשימה בשינה נמסר לך בשבועות האחרונים. כחלק מהפרוטוקול הטיפולי המומלץ, אנו יוצרים קשר כדי לבדוק את התקדמותך.

אנא מלא/י את השאלון בקישור:
{followup_link}

תודה,
{clinic.name}"""
                        email_html = f"""<html><body dir="rtl">
<p>שלום,</p>
<p>ההתקן האוראלי שלך לטיפול בדום נשימה בשינה נמסר לך בשבועות האחרונים. כחלק מהפרוטוקול הטיפולי המומלץ, אנו יוצרים קשר כדי לבדוק את התקדמותך.</p>
<p>אנא מלא/י את השאלון בקישור:</p>
<p><a href="{followup_link}">{followup_link}</a></p>
<p>תודה,<br>{clinic.name}</p>
</body></html>"""
                    else:
                        email_subject = "1st Follow-up Questionnaire - Sleep Apnea Treatment"
                        email_body = f"""
Hello,

Thank you for being a valued patient. We would like to check on your progress with the sleep apnea treatment.

Please click the following link to complete the follow-up questionnaire:
{followup_link}

This questionnaire will help us track your treatment progress and ensure you're getting the best care.

Best regards,
{clinic.name} Team
                    """
                        email_html = f"""
<html>
<body>
<h2>1st Follow-up Questionnaire</h2>
<p>Hello,</p>
<p>Thank you for being a valued patient. We would like to check on your progress with the sleep apnea treatment.</p>
<p>Please click the following link to complete the follow-up questionnaire:</p>
<p><a href="{followup_link}" style="background-color: #ff9800; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Complete Questionnaire</a></p>
<p>Or copy and paste this link: {followup_link}</p>
<p>This questionnaire will help us track your treatment progress and ensure you're getting the best care.</p>
<p>Best regards,<br>{clinic.name} Team</p>
</body>
</html>
                    """
                elif send_type == 'short_wizard':
                    # Short wizard link with dentist_id
                    short_wizard_link = url_for('short_wizard.stage1_personal_info', dentist_id=current_user.id, _external=True)
                    email_subject = "Patient Onboarding - Sleep Apnea Assessment"
                    email_body = f"""
Hello,

You have been invited to complete the Patient Onboarding process for Sleep Apnea Assessment.

This includes:
- Personal Information
- Sleep Test Information  
- CPAP Intolerance Assessment
- HIPAA Authorization
- Informed Consent

Please click the following link to get started:
{short_wizard_link}

This link is personalized for your assessment and should not be shared with others.

Best regards,
{clinic.name} Team
                    """
                    email_html = f"""
<html>
<body>
<h2>Patient Onboarding - Sleep Apnea Assessment</h2>
<p>Hello,</p>
<p>You have been invited to complete the Patient Onboarding process for Sleep Apnea Assessment.</p>
<p>This includes:</p>
<ul>
<li>Personal Information</li>
<li>Sleep Test Information</li>
<li>CPAP Intolerance Assessment</li>
<li>HIPAA Authorization</li>
<li>Informed Consent</li>
</ul>
<p>Please click the following link to get started:</p>
<p><a href="{short_wizard_link}" style="background-color: #9b59b6; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Start Onboarding</a></p>
<p>Or copy and paste this link: {short_wizard_link}</p>
<p>This link is personalized for your assessment and should not be shared with others.</p>
<p>Best regards,<br>{clinic.name} Team</p>
</body>
</html>
                    """
                else:
                    # Wizard link
                    wizard_link = url_for('wizard.stage1_personal_info', clinic_id=clinic.id, _external=True)
                    email_subject = "Sleep Apnea Patient Wizard"
                    email_body = f"""
Hello,

You have been invited to complete the Sleep Apnea Patient Wizard.

Please click the following link to get started:
{wizard_link}

This wizard will help us gather important information about your sleep health.

Best regards,
{clinic.name} Team
                    """
                    email_html = f"""
<html>
<body>
<h2>Sleep Apnea Patient Wizard</h2>
<p>Hello,</p>
<p>You have been invited to complete the Sleep Apnea Patient Wizard.</p>
<p>Please click the following link to get started:</p>
<p><a href="{wizard_link}" style="background-color: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Start Wizard</a></p>
<p>Or copy and paste this link: {wizard_link}</p>
<p>This wizard will help us gather important information about your sleep health.</p>
<p>Best regards,<br>{clinic.name} Team</p>
</body>
</html>
                    """
                
                # Send email
                from flask_mail import Mail, Message
                from flask import current_app
                
                mail = Mail(current_app)
                msg = Message(
                    subject=email_subject,
                    sender=current_app.config.get('MAIL_DEFAULT_SENDER', 'info@vizbriz.com'),
                    recipients=[patient_email]
                )
                msg.body = email_body
                msg.html = email_html
                mail.send(msg)
                
                if send_type == 'quiz':
                    flash('Quiz link sent successfully!', 'success')
                elif send_type == 'followup':
                    flash('Follow-up questionnaire link sent successfully!', 'success')
                elif send_type == 'short_wizard':
                    flash('Short wizard link sent successfully!', 'success')
                else:
                    flash('Wizard link sent successfully!', 'success')
            except Exception as e:
                logger.error(f"Error sending email: {str(e)}")
                flash('Error sending email. Please try again.', 'danger')
        else:
            flash('Please enter a valid email address.', 'danger')
    
    # Get dso_id the same way as osa_guidelines
    dso_id = None
    if hasattr(current_user, 'get_dso_ids'):
        dso_ids = current_user.get_dso_ids()
        if dso_ids:
            dso_id = dso_ids[0]
    if not dso_id:
        dso_id = clinic.dso_id if clinic else None
    if not dso_id:
        dso_id = None

    # Base URL for quiz links - use env or current request host (dev/staging)
    base_url = os.getenv('BASE_URL') or (request.host_url.rstrip('/') if request else 'https://app.vizbriz.com')
    
    active_clinics = []
    if is_admin:
        active_clinics = (
            Clinic.query.filter(Clinic.status == "active").order_by(Clinic.name).all()
        )

    return render_template(
        "wizard/wizard_options.html",
        clinic=clinic,
        clinic_dict=clinic_dict,
        error_message=None,
        dso_id=dso_id,
        base_url=base_url,
        is_admin=is_admin,
        context_patient_id=context_patient_id,
        active_clinics=active_clinics,
    )

@wizard.route('/wizard/get_barcode', methods=['GET'])
def get_barcode():
    # Generate a unique barcode for the patient
    # You can use any barcode generation library here
    # For now, we'll use a placeholder image
    barcode_url = url_for('static', filename='images/patient_barcode.png')
    return jsonify({'barcode_url': barcode_url})


from flask_login import login_required, current_user
from flask import render_template, Blueprint

@wizard.route('/osa_guidelines', methods=['GET', 'POST'])
@login_required
def osa_guidelines():
    dso_id = None
    if hasattr(current_user, 'get_dso_ids'):
        dso_ids = current_user.get_dso_ids()
        if dso_ids:
            dso_id = dso_ids[0]
    if not dso_id:
        dso_id = None
    
    # Handle POST request for sending email
    if request.method == 'POST':
        patient_email = request.form.get('patient_email')
        link_type = request.form.get('link_type')
        
        if patient_email and link_type:
            try:
                # Generate the appropriate link based on link_type
                if link_type == 'quiz':
                    import os
                    base_url = os.getenv('BASE_URL', 'http://localhost:7000')
                    link_url = f"{base_url}/quiz?dso_id={dso_id}"
                    email_subject = "Sleep Apnea Assessment Quiz"
                    email_body = f"""
Hello,

You have been invited to complete the Sleep Apnea Assessment Quiz.

Please click the following link to get started:
{link_url}

This quiz will help us assess your sleep health.

Best regards,
Vizbriz Team
                    """
                    email_html = f"""
<html>
<body>
<h2>Sleep Apnea Assessment Quiz</h2>
<p>Hello,</p>
<p>You have been invited to complete the Sleep Apnea Assessment Quiz.</p>
<p>Please click the following link to get started:</p>
<p><a href="{link_url}" style="background-color: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Start Quiz</a></p>
<p>Or copy and paste this link: {link_url}</p>
<p>This quiz will help us assess your sleep health.</p>
<p>Best regards,<br>Vizbriz Team</p>
</body>
</html>
                    """
                elif link_type == 'medicare':
                    link_url = "https://portal.isleepemr.com/booking/create-appointment/?booking=6809ea85e24b0b0ae4bdce75"
                    email_subject = "Schedule an appointment with a sleep specialist - Insurance Package"
                    email_body = f"""
Hello,

We strongly recommend that you book a home sleep test to confirm the diagnosis and find the treatment approach that's right for you.

Click here to schedule an appointment with a sleep specialist that includes pre and post test consultations, shipment of home sleep test to patient, home sleep test interpretation, prescription and letter of medical necessity for treatment.

{link_url}

Best regards,
Vizbriz Team
                    """
                    email_html = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
<h2>Schedule an appointment with a sleep specialist</h2>
<p>Hello,</p>
<p>We <strong>strongly recommend</strong> that you book a home sleep test to confirm the diagnosis and find the treatment approach that's right for you.</p>
<p>Click here to schedule an appointment with a sleep specialist that includes pre and post test consultations, shipment of home sleep test to patient, home sleep test interpretation, prescription and letter of medical necessity for treatment:</p>
<div style="margin: 25px 0; text-align: center;">
    <a href="{link_url}" style="background-color: #007bff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block; font-weight: bold; font-size: 16px;">Click here to schedule an appointment</a>
</div>
<p>Or copy and paste this link: {link_url}</p>
<p>Best regards,<br>Vizbriz Team</p>
</body>
</html>
                    """
                elif link_type == 'full_package':
                    link_url = "https://portal.isleepemr.com/booking/create-appointment/?booking=67d8f750532ff1ecaaf98700"
                    email_subject = "Schedule an appointment with a sleep specialist - Full Package"
                    email_body = f"""
Hello,

We strongly recommend that you book a home sleep test to confirm the diagnosis and find the treatment approach that's right for you.

Click here to schedule an appointment with a sleep specialist that includes pre and post test consultations, shipment of home sleep test to patient, home sleep test interpretation, prescription and letter of medical necessity for treatment.

{link_url}

Best regards,
Vizbriz Team
                    """
                    email_html = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
<h2>Schedule an appointment with a sleep specialist</h2>
<p>Hello,</p>
<p>We <strong>strongly recommend</strong> that you book a home sleep test to confirm the diagnosis and find the treatment approach that's right for you.</p>
<p>Click here to schedule an appointment with a sleep specialist that includes pre and post test consultations, shipment of home sleep test to patient, home sleep test interpretation, prescription and letter of medical necessity for treatment:</p>
<div style="margin: 25px 0; text-align: center;">
    <a href="{link_url}" style="background-color: #007bff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block; font-weight: bold; font-size: 16px;">Click here to schedule an appointment</a>
</div>
<p>Or copy and paste this link: {link_url}</p>
<p>Best regards,<br>Vizbriz Team</p>
</body>
</html>
                    """
                else:
                    flash('Invalid link type.', 'danger')
                    return render_template('osa_guidelines.html', dso_id=dso_id)
                
                # Send email
                from flask_mail import Mail, Message
                from flask import current_app
                
                mail = Mail(current_app)
                msg = Message(
                    subject=email_subject,
                    sender=current_app.config.get('MAIL_DEFAULT_SENDER', 'info@vizbriz.com'),
                    recipients=[patient_email]
                )
                msg.body = email_body
                msg.html = email_html
                mail.send(msg)
                
                flash('Email sent successfully!', 'success')
            except Exception as e:
                logger.error(f"Error sending email: {str(e)}")
                flash('Error sending email. Please try again.', 'danger')
        else:
            flash('Please enter a valid email address.', 'danger')
    
    return render_template('osa_guidelines.html', dso_id=dso_id)

@wizard.route('/osa_guidelines_qr_code')
@login_required
def osa_guidelines_qr_code():
    dso_id = None
    if hasattr(current_user, 'get_dso_ids'):
        dso_ids = current_user.get_dso_ids()
        if dso_ids:
            dso_id = dso_ids[0]
    if not dso_id:
        dso_id = None
    import os
    base_url = os.getenv('BASE_URL', 'http://localhost:7000')
    qr_url = f'{base_url}/quiz?dso_id={dso_id}'
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

@wizard.route('/medicare_qr_code')
def medicare_qr_code():
    """Generate QR code for Medicare Package with fixed URL"""
    medicare_url = "https://portal.isleepemr.com/booking/create-appointment/?booking=6809ea85e24b0b0ae4bdce75"
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(medicare_url)
    qr.make(fit=True)
    
    # Create QR code image
    qr_img = qr.make_image(fill_color="black", back_color="white")
    
    # Convert to PIL Image for logo addition
    qr_img = qr_img.convert('RGBA')
    
    # Get image dimensions
    width, height = qr_img.size
    
    # Calculate center position for logo
    center_x = width // 2
    center_y = height // 2
    
    # Create a drawing object
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(qr_img)
    
    # Create a white background for the logo area
    logo_size = 40
    logo_x = center_x - logo_size // 2
    logo_y = center_y - logo_size // 2
    
    # Draw white rectangle for logo background
    draw.rectangle([logo_x, logo_y, logo_x + logo_size, logo_y + logo_size], 
                   fill='white', outline='black', width=2)
    
    # Try to use a font, fallback to default if not available
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except:
        font = ImageFont.load_default()
    
    # Calculate text position to center it
    text_bbox = draw.textbbox((0, 0), "Y•", font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    
    text_x = center_x - text_width // 2
    text_y = center_y - text_height // 2
    
    # Draw the logo text
    draw.text((text_x, text_y), "Y•", fill='black', font=font)
    
    # Convert to bytes for Flask response
    buf = io.BytesIO()
    qr_img.save(buf, 'PNG')
    buf.seek(0)
    
    return send_file(buf, mimetype='image/png')

@wizard.route('/get_patients_for_select')
@login_required
def get_patients_for_select():
    """
    Get patients for select dropdown - uses shared get_accessible_patients.
    Guarantees identical list to patient_list page.
    """
    try:
        from flask_app.helpers.patient_access_helpers import get_accessible_patients

        include_archived = False
        if current_user.role == 'admin':
            include_archived = request.args.get('include_archived', 'false').lower() == 'true'

        patients = get_accessible_patients(include_archived=include_archived)
        
        patient_list = []
        for patient in patients:
            patient_list.append({
                'id': patient.id,
                'name': patient.name,
                'email': getattr(patient, 'email', ''),
                'phone': getattr(patient, 'phone', ''),
                'status': getattr(patient, 'status', ''),
                'clinic_id': getattr(patient, 'clinic_id', None),
                'create_date': getattr(patient, 'create_date', '').strftime('%Y-%m-%d') if getattr(patient, 'create_date', '') else ''
            })
        return jsonify({'patients': patient_list})
    except Exception as e:
        logger.error(f"Error fetching patients for select: {e}")
        return jsonify({'error': 'Failed to fetch patients'}), 500



@wizard.route('/full_package_qr_code')
def full_package_qr_code():
    """Generate QR code for Full Package with fixed URL"""
    full_package_url = "https://portal.isleepemr.com/booking/create-appointment/?booking=67d8f750532ff1ecaaf98700"
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(full_package_url)
    qr.make(fit=True)
    
    # Create QR code image
    qr_img = qr.make_image(fill_color="black", back_color="white")
    
    # Convert to PIL Image for logo addition
    qr_img = qr_img.convert('RGBA')
    
    # Get image dimensions
    width, height = qr_img.size
    
    # Calculate center position for logo
    center_x = width // 2
    center_y = height // 2
    
    # Create a drawing object
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(qr_img)
    
    # Create a white background for the logo area
    logo_size = 40
    logo_x = center_x - logo_size // 2
    logo_y = center_y - logo_size // 2
    
    # Draw white rectangle for logo background
    draw.rectangle([logo_x, logo_y, logo_x + logo_size, logo_y + logo_size], 
                   fill='white', outline='black', width=2)
    
    # Try to use a font, fallback to default if not available
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except:
        font = ImageFont.load_default()
    
    # Calculate text position to center it
    text_bbox = draw.textbbox((0, 0), "Y•", font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    
    text_x = center_x - text_width // 2
    text_y = center_y - text_height // 2
    
    # Draw the logo text
    draw.text((text_x, text_y), "Y•", fill='black', font=font)
    
    # Convert to bytes for Flask response
    buf = io.BytesIO()
    qr_img.save(buf, 'PNG')
    buf.seek(0)
    
    return send_file(buf, mimetype='image/png')

@wizard.route('/wizard/generate_qr')
def generate_qr():
    import qrcode
    import io
    from flask import send_file, request, url_for
    clinic_id = request.args.get('clinic_id')
    # Generate the patient wizard link with the clinic_id
    wizard_url = url_for('wizard.stage1_personal_info', clinic_id=clinic_id, _external=True)
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(wizard_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@wizard.route('/wizard/quiz_qr_code')
def quiz_qr_code():
    # Try to get DSO or clinic context
    dso_id = None
    clinic_id = request.args.get('clinic_id') or session.get('clinic_id')
    if clinic_id:
        from flask_app.models import Clinic
        clinic = Clinic.query.get(clinic_id)
        if clinic and hasattr(clinic, 'dso_id'):
            dso_id = clinic.dso_id
    if not dso_id:
        # Fallback: try to get from current_user
        if hasattr(current_user, 'get_dso_ids'):
            dso_ids = current_user.get_dso_ids()
            if dso_ids:
                dso_id = dso_ids[0]
    if not dso_id:
        dso_id = None  # Default/fallback
    dentist_id = request.args.get('dentist_id') or (current_user.id if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated and getattr(current_user, 'id', None) else None)
    if dentist_id is not None and str(dentist_id) == '0':
        dentist_id = None
    base_url = os.getenv('BASE_URL') or (request.host_url.rstrip('/') if request else 'https://app.vizbriz.com')
    params = []
    if dso_id:
        params.append(f"dso_id={dso_id}")
    if clinic_id and str(clinic_id) != '0':
        params.append(f"clinic_id={clinic_id}")
    if dentist_id:
        params.append(f"dentist_id={dentist_id}")
    if params:
        quiz_url = f"{base_url}/vizbriz/quiz?lang=en&" + "&".join(params)
    else:
        quiz_url = f"{base_url}/vizbriz/quiz?lang=en"
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(quiz_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')


@wizard.route('/wizard/quiz_qr_code_hebrew')
def quiz_qr_code_hebrew():
    """
    Generate QR code for the Hebrew VizBriz assessment quiz.
    When clinic_id is provided, includes dso_id in URL so patients register under the correct DSO (same as English).
    """
    dso_id = None
    clinic_id = request.args.get('clinic_id') or session.get('clinic_id')
    if clinic_id and str(clinic_id) != '0':
        from flask_app.models import Clinic
        clinic = Clinic.query.get(clinic_id)
        if clinic and hasattr(clinic, 'dso_id'):
            dso_id = clinic.dso_id
    if not dso_id and hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
        if hasattr(current_user, 'get_dso_ids'):
            dso_ids = current_user.get_dso_ids()
            if dso_ids:
                dso_id = dso_ids[0]
    dentist_id = request.args.get('dentist_id') or (current_user.id if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated and getattr(current_user, 'id', None) else None)
    if dentist_id is not None and str(dentist_id) == '0':
        dentist_id = None
    base_url = os.getenv('BASE_URL') or (request.host_url.rstrip('/') if request else 'https://app.vizbriz.com')
    params = []
    if dso_id:
        params.append(f"dso_id={dso_id}")
    if clinic_id and str(clinic_id) != '0':
        params.append(f"clinic_id={clinic_id}")
    if dentist_id:
        params.append(f"dentist_id={dentist_id}")
    if params:
        quiz_url = f"{base_url}/vizbriz/quiz_hebrew?" + "&".join(params)
    else:
        quiz_url = f"{base_url}/vizbriz/quiz_hebrew"
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(quiz_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@wizard.route('/wizard/short_wizard_qr_code')
@login_required
def short_wizard_qr_code():
    """Generate QR code for Short Patient Onboarding (CPAP Intolerance, HIPAA Authorization, Informed Consent)"""
    dentist_id = current_user.id
    short_wizard_url = url_for('short_wizard.stage1_personal_info', dentist_id=dentist_id, _external=True)
    
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(short_wizard_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')


@wizard.route('/wizard/sleep_labs')
@login_required
def sleep_labs():
    """Sleep Labs page with Full Package, Insurance Package, and lab referral (imaging)."""
    labs = Lab.query.order_by(Lab.name).all()
    return render_template('wizard/sleep_labs.html', labs=labs)


@wizard.route('/wizard/sleep_labs/send_referral', methods=['POST'])
@login_required
def send_lab_referral():
    """Send Hebrew lab referral email to lab, dentist, info@vizbriz.com, and patient."""
    from flask_app.services.lab_reference_service import build_hebrew_referral_html
    from flask_mail import Mail, Message

    data = request.get_json() or {}
    patient_id = data.get('patient_id')
    lab_id = data.get('lab_id')
    image_types = data.get('image_types') or []

    if not patient_id:
        return jsonify({'success': False, 'message': 'Patient is required.'}), 400
    if not lab_id:
        return jsonify({'success': False, 'message': 'Lab is required.'}), 400
    if not image_types:
        return jsonify({'success': False, 'message': 'Select at least one scan type.'}), 400

    patient = Patient.query.get(patient_id)
    if not patient:
        return jsonify({'success': False, 'message': 'Patient not found.'}), 404
    if not getattr(patient, 'email', None) or not (patient.email or '').strip():
        return jsonify({'success': False, 'message': 'Patient must have an email address.'}), 400

    lab = Lab.query.get(lab_id)
    if not lab:
        return jsonify({'success': False, 'message': 'Lab not found.'}), 404

    # Labs may list multiple addresses in `email` (comma- or semicolon-separated)
    lab_to_emails = [
        p.strip()
        for p in re.split(r"[,;]+", (lab.email or "").strip())
        if p.strip()
    ]
    if not lab_to_emails:
        return jsonify({'success': False, 'message': 'Lab has no email address.'}), 400

    dentist = current_user
    if not getattr(dentist, 'email', None):
        return jsonify({'success': False, 'message': 'Dentist email not found.'}), 400

    html_body = build_hebrew_referral_html(
        patient_name=patient.name,
        patient_phone=getattr(patient, 'phone', '') or '',
        patient_email=(patient.email or '').strip(),
        patient_id_number=getattr(patient, 'id_number', '') or '',
        dentist_name=dentist.name,
        dentist_email=dentist.email,
        image_types_list=image_types,
        lab_name=lab.name,
    )

    subject_he = 'VIZBRIZ – הפניה למכון רנטגן (דום נשימה בשינה)'
    recipients = lab_to_emails + [
        dentist.email,
        current_app.config.get('VIZBRIZ_INFO_EMAIL', 'info@vizbriz.com'),
        (patient.email or '').strip(),
    ]
    recipients = [r for r in recipients if r]

    try:
        mail = Mail(current_app)
        for to in recipients:
            msg = Message(
                subject=subject_he,
                sender=current_app.config.get('MAIL_DEFAULT_SENDER', 'info@vizbriz.com'),
                recipients=[to],
            )
            msg.body = subject_he
            msg.html = html_body
            mail.send(msg)
    except Exception as e:
        logger.exception('Send lab referral email failed: %s', e)
        return jsonify({'success': False, 'message': 'Failed to send email.'}), 500

    try:
        ref = LabReference(
            patient_id=patient.id,
            dentist_id=dentist.id,
            lab_id=lab.id,
            image_types=','.join(str(s) for s in image_types),
        )
        db.session.add(ref)
        db.session.commit()
    except Exception as e:
        logger.exception('Lab reference log failed: %s', e)

    return jsonify({'success': True, 'message': 'Referral sent.'})


def _followup_prefill_query_dict(patient_email=None, patient_id=None, patient_name=None):
    """Build query params to pre-fill contact fields on the follow-up form."""
    q = {}
    pid = patient_id
    if pid:
        try:
            pid = int(pid)
            p = Patient.query.get(pid)
            if p:
                q['patient_id'] = str(pid)
                patient_email = (patient_email or '').strip() or (getattr(p, 'email', None) or '').strip()
                patient_name = (patient_name or '').strip() or (getattr(p, 'name', None) or '').strip()
        except (TypeError, ValueError):
            pass
    if patient_email:
        q['email'] = patient_email.strip()
    if patient_name:
        q['name'] = patient_name.strip()
    return q


def _append_query_to_url(url, extra_params):
    from urllib.parse import urlencode

    if not extra_params:
        return url
    sep = '&' if '?' in url else '?'
    return url + sep + urlencode(extra_params)


def _build_followup_quiz_url(language='en', patient_email=None, patient_id=None, patient_name=None):
    """Build in-system 1st follow-up questionnaire URL (same query params as assessment quiz)."""
    dso_id = None
    clinic_id = request.args.get('clinic_id') or session.get('clinic_id')
    if clinic_id and str(clinic_id) != '0':
        from flask_app.models import Clinic
        clinic = Clinic.query.get(clinic_id)
        if clinic and hasattr(clinic, 'dso_id'):
            dso_id = clinic.dso_id
    if not dso_id and hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
        if hasattr(current_user, 'get_dso_ids'):
            dso_ids = current_user.get_dso_ids()
            if dso_ids:
                dso_id = dso_ids[0]
    dentist_id = request.args.get('dentist_id') or (
        current_user.id
        if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated and getattr(current_user, 'id', None)
        else None
    )
    if dentist_id is not None and str(dentist_id) == '0':
        dentist_id = None
    base_url = os.getenv('BASE_URL') or (request.host_url.rstrip('/') if request else 'https://app.vizbriz.com')
    path = '/vizbriz/followup_hebrew' if language == 'he' else '/vizbriz/followup'
    params = []
    if language != 'he':
        params.append('lang=en')
    if dso_id:
        params.append(f'dso_id={dso_id}')
    if clinic_id and str(clinic_id) != '0':
        params.append(f'clinic_id={clinic_id}')
    if dentist_id:
        params.append(f'dentist_id={dentist_id}')
    url = f'{base_url}{path}'
    if params:
        url += '?' + '&'.join(params)
    prefill = _followup_prefill_query_dict(
        patient_email=patient_email,
        patient_id=patient_id,
        patient_name=patient_name,
    )
    return _append_query_to_url(url, prefill)


@wizard.route('/wizard/followup_qr_code')
def followup_qr_code():
    """Generate QR code for the 1st follow-up questionnaire (English, in-system)."""
    return _generate_followup_qr_image(_build_followup_quiz_url(
        'en',
        patient_email=request.args.get('email'),
        patient_id=request.args.get('patient_id'),
        patient_name=request.args.get('name'),
    ))


@wizard.route('/wizard/followup_qr_code_hebrew')
def followup_qr_code_hebrew():
    """Generate QR code for the 1st follow-up questionnaire (Hebrew, in-system)."""
    return _generate_followup_qr_image(_build_followup_quiz_url(
        'he',
        patient_email=request.args.get('email'),
        patient_id=request.args.get('patient_id'),
        patient_name=request.args.get('name'),
    ))


def _generate_followup_qr_image(url):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')



