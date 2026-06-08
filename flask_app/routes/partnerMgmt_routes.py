from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, send_file
from flask_app.extensions import db
from flask_login import login_required, current_user
from ..models import db, Patient, File, Dentist, AdminFile, Claim, Comment, PatientStatus, StatusOption, PatientComment, DentistCourseParticipation
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func
from datetime import datetime, timedelta
import logging
import boto3 
from botocore.config import Config
from botocore.exceptions import ClientError
import os
from werkzeug.utils import secure_filename
import time
import urllib.parse
import re
import zipfile
from io import BytesIO
import mimetypes
from flask_mail import Mail, Message
from flask import Flask, request, jsonify
import qrcode
import io

# Flask-Mail is used for email sending (same as working wizard implementation)

# Blueprint for filemgmt routes
partnerMgmt = Blueprint('partnerMgmt', __name__)
logger = logging.getLogger(__name__)

region = os.environ.get('AWS_REGION', 'us-west-2')
s3_client = boto3.client('s3', region_name=region, config=Config(signature_version='s3v4'))
# ses_client = boto3.client('ses', region_name=region)

# Flask-Mail configuration is handled in __init__.py

@partnerMgmt.route('/partner_upload/<token>', methods=['GET', 'POST'])
def partner_upload(token):
    """
    Endpoint for partners to upload patient files without login.
    """
    try:
        # Fetch patient details using the token
        patient = Patient.query.filter_by(upload_token=token).first()

        if not patient:
            # Token is invalid or expired
            logger.warning(f"Invalid or expired token: {token}")
            return render_template(
                'partner_upload.html',
                error_message="Invalid or expired link. Please contact the dentist for a valid link.",
                patient=None
            )

        # Render the upload page with patient info
        return render_template(
            'partner_upload.html',
            patient={
                'id': patient.id,
                'name': patient.name,
                'email': patient.email,
                'telephone': patient.phone,
            },
            error_message=None
        )

    except Exception as e:
        # Log unexpected exceptions
        logger.error(f"Unexpected error in partner upload endpoint: {e}")
        return render_template(
            'partner_upload.html',
            error_message="An unexpected error occurred. Please try again later.",
            patient=None
        )

@partnerMgmt.route('/send_partner_email', methods=['POST'])
def send_email():
    try:
        # Log the incoming request
        logger.info("Received request to send email.")
        data = request.json
        logger.info(f"Request JSON: {data}")

        recipient = data.get('to')
        subject = data.get('subject')
        message_body = data.get('message')

        # Validate inputs
        if not recipient or not subject or not message_body:
            logger.error("Validation failed: Missing required fields.")
            return jsonify({'error': 'Missing required fields: to, subject, or message'}), 400

        # Log the email parameters
        logger.info(f"Recipient: {recipient}")
        logger.info(f"Subject: {subject}")
        logger.info(f"Message Body: {message_body}")

        try:
            # Send email using Flask-Mail (same as working wizard implementation)
            from flask_mail import Mail, Message
            from flask import current_app
            
            mail = Mail(current_app)
            msg = Message(
                subject=subject,
                sender=current_app.config.get('MAIL_DEFAULT_SENDER', 'info@vizbriz.com'),
                recipients=[recipient]
            )
            msg.body = message_body
            msg.html = f"<pre>{message_body}</pre>"  # HTML version for email clients
            
            mail.send(msg)
            
            logger.info(f"Email sent successfully to {recipient} via Flask-Mail")
            return jsonify({'success': 'Email sent successfully'}), 200
                
        except Exception as email_error:
            logger.error(f"Flask-Mail error: {str(email_error)}")
            return jsonify({'error': f'Email error: {str(email_error)}'}), 500

    except Exception as e:
        logger.exception("Unexpected error occurred:")
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500

@partnerMgmt.route('/qr/<code>')
def qr_redirect(code):
    """
    Dynamic QR code redirect: /qr/<code>
    Looks up the code and redirects to the mapped URL.
    """
    qr_code_map = {    
    "osa_patient_eng": "https://app.vizbriz.com/vizbriz/quiz?lang=en&dso_id=28",  # Removed hardcoded dso_id=28
    "osa_patient_ukr": "https://forms.gle/Jv58hUGDnjjpg4bs8",
    # Hebrew patient assessment (updated to new VizBriz Hebrew quiz)
    "osa_patient_isr": "https://app.vizbriz.com/vizbriz/quiz_hebrew",
    "patient_wizard": "https://app.vizbriz.com/wizard/stage1_personal_info" ,
    "direct_upload": "https://app.vizbriz.com/direct_file_upload",
    "1st followup questionaire": "https://app.vizbriz.com/vizbriz/followup?lang=en",
    "1st followup questionaire hebrew": "https://app.vizbriz.com/vizbriz/followup_hebrew",
    "dental_course": "/dental_course_registration",
    "dental_course_qr": "/dental_course_qr"}
    url = qr_code_map.get(code)
    if url:
        return redirect(url)
    else:
        abort(404)

@partnerMgmt.route('/dental_course_qr_image')
def dental_course_qr_image():
    """
    Generate QR code image for Dental Sleep Medicine Course registration
    """
    # Get DSO parameter, default to "Rologo Dental"
    dso_name = request.args.get('dso', 'Rologo Dental')
    # Force session parameter to "session_2" regardless of URL
    session_id = 'session_2'
    #comment for git
    # Get the full URL for the registration form with DSO and session parameters
    registration_url = url_for('partnerMgmt.dental_course_registration', dso=dso_name, session=session_id, _external=True)
    
    # Create QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(registration_url)
    qr.make(fit=True)
    
    # Generate image
    img = qr.make_image(fill_color="#1e293b", back_color="white")
    
    # Save to BytesIO buffer
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    
    return send_file(buf, mimetype='image/png')

@partnerMgmt.route('/dental_course_qr')
def dental_course_qr():
    """
    Display QR code page for Dental Sleep Medicine Course registration
    """
    # Get DSO parameter, default to "Rologo Dental"
    dso_name = request.args.get('dso', 'Rologo Dental')
    # Force session parameter to "session_2" regardless of URL
    session_id = 'session_2'
    
    # Get the full URL for the registration form with DSO and session parameters
    registration_url = url_for('partnerMgmt.dental_course_registration', dso=dso_name, session=session_id, _external=True)
    
    return render_template('dental_course_qr.html', 
                         registration_url=registration_url,
                         dso_name=dso_name,
                         session_id=session_id)

@partnerMgmt.route('/dental_course_registration', methods=['GET', 'POST'])
def dental_course_registration():
    """
    Dental Sleep Medicine Course registration form for dentists
    """
    # Get DSO name from query parameter, default to "Rologo Dental"
    dso_name = request.args.get('dso', 'Rologo Dental')
    # Force session_id to 'session_2' regardless of URL
    session_id = 'session_2'
    
    if request.method == 'GET':
        # Show the registration form
        return render_template('dental_course_registration.html', dso_name=dso_name, session_id=session_id)
    
    elif request.method == 'POST':
        try:
            # Get form data
            doctor_name = request.form.get('doctor_name', '').strip()
            clinic_name = request.form.get('clinic_name', '').strip()
            email = request.form.get('email', '').strip()
            phone_number = request.form.get('phone_number', '').strip()
            role = request.form.get('role', '').strip()
            
            # Get DSO name from query parameter or form, default to "Rologo Dental"
            dso_name = request.args.get('dso') or request.form.get('dso_name', 'Rologo Dental')
            
            # Force session_id to 'session_2' regardless of URL or form
            session_id = 'session_2'
            
            # Validate required fields
            if not all([doctor_name, clinic_name, email, phone_number, role]):
                flash('All fields are required. Please fill in all information.', 'error')
                return render_template('dental_course_registration.html', dso_name=dso_name, session_id=session_id)
            
            # Basic email validation
            import re
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, email):
                flash('Please enter a valid email address.', 'error')
                return render_template('dental_course_registration.html', dso_name=dso_name, session_id=session_id)
            
            # Check if email already registered for this specific session
            existing_registration = DentistCourseParticipation.query.filter_by(
                email=email, 
                session_id=session_id
            ).first()
            
            if existing_registration:
                flash('This email is already registered. If you need to update your information, please contact us.', 'warning')
                return render_template('dental_course_registration.html', dso_name=dso_name, session_id=session_id)
            
            # Create new registration
            new_registration = DentistCourseParticipation(
                doctor_name=doctor_name,
                dso_name=dso_name,
                clinic_name=clinic_name,
                email=email,
                phone_number=phone_number,
                role=role,
                session_name='Dental Sleep Medicine Course',
                session_id=session_id
            )
            
            # Save to database
            db.session.add(new_registration)
            db.session.commit()
            
            logger.info(f"New course registration: {doctor_name} ({email}) from {dso_name}")
            
            # Success message and redirect
            flash(f'Thank you, Dr. {doctor_name}! Your registration for the Dental Sleep Medicine Course has been successfully submitted.', 'success')
            return render_template('dental_course_registration.html', success=True, dso_name=dso_name, session_id=session_id)
            
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error during course registration: {e}")
            flash('A database error occurred. Please try again or contact support.', 'error')
            return render_template('dental_course_registration.html', dso_name=dso_name, session_id=session_id)
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Unexpected error during course registration: {e}")
            flash('An unexpected error occurred. Please try again or contact support.', 'error')
            return render_template('dental_course_registration.html', dso_name=dso_name, session_id=session_id)

@partnerMgmt.route('/dental_course_registration_hebrew', methods=['GET', 'POST'])
def dental_course_registration_hebrew():
    """
    Dental Sleep Medicine Course registration form for dentists (Hebrew version)
    """
    # Get DSO name from query parameter, default to empty string
    dso_name = request.args.get('dso', '')
    # Force session_id to 'session_2' regardless of URL
    session_id = 'session_2'
    
    if request.method == 'GET':
        # Show the registration form
        return render_template('dental_course_registration_hebrew.html', dso_name=dso_name, session_id=session_id)
    
    elif request.method == 'POST':
        try:
            # Get form data
            doctor_name = request.form.get('doctor_name', '').strip()
            clinic_name = request.form.get('clinic_name', '').strip()
            email = request.form.get('email', '').strip()
            phone_number = request.form.get('phone_number', '').strip()
            role = request.form.get('role', '').strip()
            
            # Get DSO name from query parameter or form, default to empty string
            dso_name = request.args.get('dso') or request.form.get('dso_name', '')
            
            # Force session_id to 'session_2' regardless of URL or form
            session_id = 'session_2'
            
            # Validate required fields
            if not all([doctor_name, clinic_name, email, phone_number, role]):
                flash('כל השדות נדרשים. אנא מלא את כל הפרטים.', 'error')
                return render_template('dental_course_registration_hebrew.html', dso_name=dso_name, session_id=session_id)
            
            # Basic email validation
            import re
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, email):
                flash('אנא הזן כתובת אימייל תקינה.', 'error')
                return render_template('dental_course_registration_hebrew.html', dso_name=dso_name, session_id=session_id)
            
            # Check if email already registered for this specific session
            existing_registration = DentistCourseParticipation.query.filter_by(
                email=email, 
                session_id=session_id
            ).first()
            
            if existing_registration:
                flash('כתובת האימייל הזו כבר רשומה. אם אתה צריך לעדכן את הפרטים שלך, אנא צור קשר.', 'warning')
                return render_template('dental_course_registration_hebrew.html', dso_name=dso_name, session_id=session_id)
            
            # Create new registration
            new_registration = DentistCourseParticipation(
                doctor_name=doctor_name,
                dso_name=dso_name,
                clinic_name=clinic_name,
                email=email,
                phone_number=phone_number,
                role=role,
                session_name='Dental Sleep Medicine Course',
                session_id=session_id
            )
            
            # Save to database
            db.session.add(new_registration)
            db.session.commit()
            
            logger.info(f"New course registration (Hebrew): {doctor_name} ({email}) from {dso_name}")
            
            # Success message and redirect
            flash(f'תודה, ד"ר {doctor_name}! הרישום שלך לקורס רפואת שינה דנטלית נשלח בהצלחה.', 'success')
            return render_template('dental_course_registration_hebrew.html', success=True, dso_name=dso_name, session_id=session_id)
            
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error during course registration (Hebrew): {e}")
            flash('אירעה שגיאת מסד נתונים. אנא נסה שוב או צור קשר עם התמיכה.', 'error')
            return render_template('dental_course_registration_hebrew.html', dso_name=dso_name, session_id=session_id)
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Unexpected error during course registration (Hebrew): {e}")
            flash('אירעה שגיאה בלתי צפויה. אנא נסה שוב או צור קשר עם התמיכה.', 'error')
            return render_template('dental_course_registration_hebrew.html', dso_name=dso_name, session_id=session_id)