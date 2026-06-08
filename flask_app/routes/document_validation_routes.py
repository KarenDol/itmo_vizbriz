from flask import Blueprint, render_template, request, redirect, url_for, flash

# OpenAI from flask_app.extensions import db
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
import os
import boto3
import pytesseract
from flask import Flask, jsonify, request
from flask import Blueprint 
from pdf2image import convert_from_path
from PIL import Image
from openai import OpenAI



docValid = Blueprint('docValid', __name__)
logger = logging.getLogger(__name__)
region = os.environ.get('AWS_REGION', 'us-west-2')
s3_client = boto3.client('s3', region_name=region, config=Config(signature_version='s3v4'))

UPLOAD_FOLDER = 'patients/test'
ALLOWED_EXTENSIONS = {'pdf', 'jpg', 'jpeg', 'png', 'docx'}

BUCKET_NAME = os.getenv('S3_BUCKET_NAME')
BASE_DIRECTORY = "patients/test"
ALLOWED_FILE_TYPES = {'application/pdf', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'image/jpeg', 'image/png'}


# Helper function to check file type
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Route for the HTML upload page
@docValid.route('/doc_valid', methods=['GET'])
def doc_valid():
    return render_template('doc_valid.html')  # Ensure upload.html exists in your templates folder

# Route to handle file uploads and analysis

@docValid.route('/upload_document_for_validation', methods=['POST'])
def upload_document_for_validation():
    logger.info("Received a request to upload and analyze a document")

    # Step 1: Check if 'file' exists in the request
    if 'file' not in request.files:
        logger.error("No file part in the request")
        return jsonify({'success': False, 'message': 'No file part in the request'}), 400


    file = request.files['file']

    # Step 2: Check if a file is selected
    if file.filename == '':
        logger.error("No file selected for upload")
        return jsonify({'success': False, 'message': 'No selected file'}), 400

    logger.info(f"File received: {file.filename}")


    # Step 3: Validate the file type
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        s3_key = f"{BASE_DIRECTORY}/{filename}"  # S3 key includes the base directory
        logger.info(f"Uploading file to S3: Bucket={BUCKET_NAME}, Key={s3_key}")

        try:
            # Step 4: Upload the file to S3
            s3_client.upload_fileobj(
                file,
                BUCKET_NAME,
                s3_key,
                ExtraArgs={'ContentType': file.mimetype}
            )
            logger.info("File successfully uploaded to S3")     
            # Step 5: Analyze the file (you can use the S3 URL or key for processing)
            file_url = f"https://{BUCKET_NAME}.s3.{s3_client.meta.region_name}.amazonaws.com/{s3_key}"
            logger.info(f"File received: {file_url}")
            analysis_result = analyze_file_from_s3(BUCKET_NAME,s3_key)  # Analyze file using the S3 URL
            logger.info("File analysis completed successfully")

            return jsonify({'success': True, 'analysis': analysis_result})
        except Exception as e:
            logger.error(f"An error occurred while uploading to S3: {str(e)}")
            return jsonify({'success': False, 'message': f"Error uploading file: {str(e)}"}), 500
    else:
        logger.error("Invalid file type")
        return jsonify({'success': False, 'message': 'Invalid file type'}), 400

def analyze_file_from_s3(bucket_name, key):
    """
    Analyze a file from S3 using its bucket name and key.
    """
    logger.info(f"Analyzing file from S3: Bucket={bucket_name}, Key={key}")

    try:
        # Download the file into memory
        response = s3_client.get_object(Bucket=bucket_name, Key=key)
        file_content = response.Body.read()
        file_type = response.ContentType
        logger.info(f"File successfully downloaded. Content-Type: {file_type}")

        # Process the file based on its type
        extracted_text = analyze_file_content(file_content, file_type)
        logger.info("File content successfully analyzed")

        # Validate content using OpenAI
        analysis_result = query_openai(extracted_text)
        return {'success': True, 'analysis': analysis_result}

    except Exception as e:
        logger.error(f"Error during file analysis: {e}")
        return {'success': False, 'error': str(e)}





def analyze_file_content(file_content, file_type):
    try:
        if file_type in ['image/jpeg', 'image/png']:
            image = Image.open(BytesIO(file_content))
            return pytesseract.image_to_string(image)
        elif file_type == 'application/pdf':
            pdf_reader = PdfReader(BytesIO(file_content))
            return ''.join([page.extract_text() for page in pdf_reader.pages if page.extract_text()])
        elif file_type in ['application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document']:
            import docx
            doc = docx.Document(BytesIO(file_content))
            return '\n'.join([para.text for para in doc.paragraphs])
        else:
            raise ValueError("Unsupported file type")
    except Exception as e:
        logger.error(f"Error analyzing file content: {e}")
        return {'error': str(e)}   


        logger.error(f"Error querying OpenAI: {e}")
        return {'error': str(e)}


#######################################################

# Function to analyze the file
def analyze_file(file_path):
    ext = file_path.rsplit('.', 1)[1].lower()
    if ext in {'jpg', 'jpeg', 'png'}:
        extracted_text = analyze_image(file_path)
    elif ext == 'pdf':
        extracted_text = analyze_pdf(file_path)
    elif ext == 'docx':
        extracted_text = analyze_docx(file_path)
    else:
        return {'error': 'Unsupported file type'}

    # Pass extracted text to OpenAI for further analysis
    return query_openai(extracted_text)


# Analyze image from in-memory bytes
def analyze_image_from_bytes(image_bytes):
    try:
        image = Image.open(BytesIO(image_bytes))
        text = pytesseract.image_to_string(image)
        logger.info("Text extracted from image")
        return text
    except Exception as e:
        logger.error(f"Error analyzing image: {e}")
        return {'error': str(e)}

# Analyze PDF from in-memory bytes
def analyze_pdf_from_bytes(pdf_bytes):
    try:
        from PyPDF2 import PdfReader
        pdf_reader = PdfReader(BytesIO(pdf_bytes))
        text = ''.join([page.extract_text() for page in pdf_reader.pages if page.extract_text()])
        logger.info("Text extracted from PDF")
        return text
    except Exception as e:
        logger.error(f"Error analyzing PDF: {e}")
        return {'error': str(e)}


# Helper function to extract bucket name and key from an S3 URL
def extract_bucket_and_key(file_url):
    """
    Parse S3 URL to extract bucket name and object key.
    """
    try:
        url_parts = file_url.replace("s3://", "").split("/", 1)
        return url_parts[0], url_parts[1]
    except Exception as e:
        logger.error(f"Error extracting bucket and key from URL: {file_url}")
        raise ValueError("Invalid S3 URL")


