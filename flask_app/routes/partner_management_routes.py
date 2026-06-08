from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_app.extensions import db
from flask_login import login_required, current_user
from ..models import db, Patient, File, Dentist, AdminFile, Claim, Comment, PatientStatus, StatusOption, PatientComment
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

ssssss

# Blueprint for filemgmt routes
partersMgmt = Blueprint('partersMgmt', __name__)
logger = logging.getLogger(__name__)

region = os.environ.get('AWS_REGION', 'us-west-2')
s3_client = boto3.client('s3', region_name=region, config=Config(signature_version='s3v4'))
ses_client = boto3.client('ses', region_name=region)



@partersMgmt.route('/partner_upload/<token>', methods=['GET', 'POST'])
def partner_upload(token):
    """
    Endpoint for partners to upload patient files without login.
    """
    try:
        # Fetch patient using the token
        patient = Patient.query.filter_by(upload_token=token).first()
        if not patient:
            logger.warning(f"Invalid or expired token used: {token}")
            return render_template('error.html', message="Invalid or expired link"), 404

        if request.method == 'POST':
            # Get uploaded files
            uploaded_files = request.files.getlist('files')
            if not uploaded_files:
                flash("No files uploaded. Please select files to upload.", "error")
                return render_template('lab_upload.html', patient=patient)

            # Process each file
            for file in uploaded_files:
                if not file.filename:
                    continue  # Skip empty file inputs

                if not file.filename.endswith(('.zip', '.dcm', '.jpg', '.jpeg', '.png')):
                    flash(f"Unsupported file type: {file.filename}", "error")
                    continue

                # Secure filename
                filename = secure_filename(file.filename)
                s3_key = f"patients/{patient.id}/partner_uploads/{filename}"

                # Upload file to S3
                try:
                    s3_client.upload_fileobj(
                        file,
                        os.getenv('S3_BUCKET_NAME'),
                        s3_key,
                        ExtraArgs={'ContentType': file.mimetype}
                    )
                    logger.info(f"File {filename} uploaded successfully to S3.")

                    # Save file details in the database
                    new_file = File(
                        name=filename,
                        patient_id=patient.id,
                        file_type=file.mimetype,
                        file_size=file.content_length,
                        s3_key=s3_key,
                        category='partner_upload'
                    )
                    db.session.add(new_file)

                except Exception as e:
                    logger.error(f"Error uploading file {filename} to S3: {str(e)}")
                    flash(f"Failed to upload file: {filename}.", "error")
                    continue

            # Commit the transaction if all files were processed
            db.session.commit()
            flash("Files uploaded successfully!", "success")
            return render_template('lab_upload_success.html', patient=patient)

        # Render the upload page
        return render_template('lab_upload.html', patient=patient)

    except Exception as e:
        logger.error(f"Error handling partner upload: {str(e)}")
        return render_template('error.html', message="An error occurred while processing the request."), 500

