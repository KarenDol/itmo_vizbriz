from flask import Blueprint, request, render_template, redirect, url_for, flash, jsonify, send_file
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from .. import db
from ..models import db, Patient, File, Dentist, AdminFile, Claim, Comment, PatientStatus, StatusOption, PatientComment
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


# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

main = Blueprint('main', __name__)
logger.debug(f"S3_BUCKET_NAME: {os.getenv('S3_BUCKET_NAME')}")

# Configure S3 client without hardcoded credentials
region = os.environ.get('AWS_REGION', 'us-west-2')
s3_client = boto3.client('s3', region_name=region, config=Config(signature_version='s3v4'))

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
        result = db.session.execute(text("SELECT COUNT(*) FROM Dentists"))
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
    return patient_list()