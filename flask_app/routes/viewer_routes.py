from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify  
from flask_app.extensions import db
from flask_login import login_required, current_user
from ..models import db, Patient, File, Dentist, AdminFile, Claim, Comment, StatusOption, PatientComment
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func
from datetime import datetime, timedelta
import logging
import boto3 
from botocore.config import Config
import os
from werkzeug.utils import secure_filename
import zipfile
from io import BytesIO
import mimetypes
import trimesh
import numpy as np
from flask import send_file
import openai
import requests
from PyPDF2 import PdfReader
from ..models import Observations  # Ensure this imports your Observation model
import json
from PIL import Image
import pytesseract
import pdfplumber
from pdf2image import convert_from_bytes  # Using pdf2image instead of PyMuPDF
from flask_app.models import DataSources
from anthropic import Anthropic
from enum import Enum
import traceback





# Blueprint for dashboard routes
viewer = Blueprint('viewer', __name__)
logger = logging.getLogger(__name__)
region = os.environ.get('AWS_REGION', 'us-west-2')
s3_client = boto3.client('s3', region_name=region, config=Config(signature_version='s3v4'))

class AIProvider:
    CLAUDE = "claude"
    OPENAI = "openai"

# Set default AI provider to OpenAI
CURRENT_AI_PROVIDER = AIProvider.OPENAI

# Global settings
CLAUDE_API_KEY = os.getenv('ANTHROPIC_API_KEY') or os.getenv('LEVEL4_ANTHROPIC_API_KEY')

@viewer.route('/viewer/<int:patient_id>', methods=['GET'])
@login_required
def viewer_page(patient_id):
    """
    Render the HTML viewer page with files for a specific patient.
    Fetches STL files from BOTH files and adminfiles tables.
    """
    try:
        files_list = []
        bucket_name = os.getenv('S3_BUCKET_NAME')
        
        # 1. Fetch STL files from the 'files' table
        from ..models import File, AdminFile
        
        # Supported 3D file extensions (STL, GLB, PLY for colored models)
        supported_extensions = ('.stl', '.glb', '.ply')
        
        patient_files = File.query.filter_by(patient_id=patient_id).all()
        for f in patient_files:
            if f.name and f.name.lower().endswith(supported_extensions):
                try:
                    presigned_url = s3_client.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': bucket_name, 'Key': f.s3_key},
                        ExpiresIn=7200  # URL valid for 2 hours
                    )
                    file_ext = f.name.lower().split('.')[-1]
                    files_list.append({
                        'file_name': f.name,
                        'url': presigned_url,
                        'source': 'files',
                        'category': f.category or 'Uncategorized',
                        'subcategory': f.subcategory or '',
                        'file_type': file_ext  # stl, glb, or ply
                    })
                    logger.debug(f"Added {file_ext.upper()} from files table: {f.name}")
                except Exception as e:
                    logger.error(f"Error generating presigned URL for file {f.name}: {str(e)}")
                    continue
        
        # 2. Fetch 3D files from the 'adminfiles' table
        admin_files = AdminFile.query.filter_by(patient_id=patient_id).all()
        for af in admin_files:
            if af.name and af.name.lower().endswith(supported_extensions):
                try:
                    presigned_url = s3_client.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': bucket_name, 'Key': af.s3_key},
                        ExpiresIn=7200  # URL valid for 2 hours
                    )
                    file_ext = af.name.lower().split('.')[-1]
                    files_list.append({
                        'file_name': af.name,
                        'url': presigned_url,
                        'source': 'adminfiles',
                        'category': af.file_category or 'Admin Files',
                        'file_type': file_ext  # stl, glb, or ply
                    })
                    logger.debug(f"Added {file_ext.upper()} from adminfiles table: {af.name}")
                except Exception as e:
                    logger.error(f"Error generating presigned URL for admin file {af.name}: {str(e)}")
                    continue
        
        logger.info(f"Successfully fetched {len(files_list)} STL files for patient {patient_id} from database tables.")

        # Render the viewer page with the files for the given patient
        return render_template('viewer.html', patient_id=patient_id, files=files_list)

    except Exception as e:
        logger.error(f"Error fetching files for patient {patient_id}: {str(e)}")
        flash(f"Error fetching files: {str(e)}", 'error')
        return redirect (url_for('main.patient_list'))


@viewer.route('/viewer/files/<int:patient_id>', methods=['GET'])
@login_required
def fetch_viewer_files(patient_id):
    """
    Fetch a list of STL (.stl) and GLB (.glb) files available for viewing.
    Fetches from BOTH files and adminfiles tables for the given patient.
    """
    try:
        files_list = []
        bucket_name = os.getenv('S3_BUCKET_NAME')
        
        # Supported 3D file extensions (STL, GLB, PLY for colored models)
        supported_extensions = ('.stl', '.glb', '.ply')
        
        # 1. Fetch 3D files from the 'files' table
        from ..models import File, AdminFile
        
        patient_files = File.query.filter_by(patient_id=patient_id).all()
        for f in patient_files:
            if f.name and f.name.lower().endswith(supported_extensions):
                try:
                    presigned_url = s3_client.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': bucket_name, 'Key': f.s3_key},
                        ExpiresIn=7200  # URL valid for 2 hours
                    )
                    file_ext = f.name.lower().split('.')[-1]
                    files_list.append({
                        'file_name': f.name,
                        'url': presigned_url,
                        'source': 'files',
                        'category': f.category or 'Uncategorized',
                        'subcategory': f.subcategory or '',
                        'file_type': file_ext
                    })
                    logger.debug(f"Added {file_ext.upper()} from files table: {f.name}")
                except Exception as e:
                    logger.error(f"Error generating presigned URL for file {f.name}: {str(e)}")
                    continue
        
        # 2. Fetch 3D files from the 'adminfiles' table
        admin_files = AdminFile.query.filter_by(patient_id=patient_id).all()
        for af in admin_files:
            if af.name and af.name.lower().endswith(supported_extensions):
                try:
                    presigned_url = s3_client.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': bucket_name, 'Key': af.s3_key},
                        ExpiresIn=7200  # URL valid for 2 hours
                    )
                    file_ext = af.name.lower().split('.')[-1]
                    files_list.append({
                        'file_name': af.name,
                        'url': presigned_url,
                        'source': 'adminfiles',
                        'category': af.file_category or 'Admin Files',
                        'file_type': file_ext
                    })
                    logger.debug(f"Added {file_ext.upper()} from adminfiles table: {af.name}")
                except Exception as e:
                    logger.error(f"Error generating presigned URL for admin file {af.name}: {str(e)}")
                    continue

        if not files_list:
            logger.warning(f"No 3D files found for patient {patient_id}")
            return jsonify({'success': False, 'message': 'No 3D model files (STL/GLB/PLY) found.'}), 404

        logger.info(f"Successfully fetched {len(files_list)} 3D files for patient {patient_id}.")
        return jsonify({'success': True, 'files': files_list})

    except Exception as e:
        logger.error(f"Error fetching files: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500


@viewer.route('/glb_to_stl')
def glb_to_stl():
    return render_template('glb_to_stl.html')  # Ensure the file is named `upload.html` and located in the `templates` directory.s

@viewer.route('/convert_glb_to_stl', methods=['POST'])
def convert_glb_to_stl():
    # Define local paths for testing
    CURRENT_DIR = os.getcwd()

    UPLOAD_FOLDER = os.path.join(CURRENT_DIR, 'uploads')
    OUTPUT_FOLDER = os.path.join(CURRENT_DIR, 'outputs')

    logging.info(f"Current working directory: {CURRENT_DIR}")  # Log the current directory
    logging.info(f"Current working directory: {UPLOAD_FOLDER}")  # Log the current directory
    logging.info(f"Current working directory: {OUTPUT_FOLDER}")  # Log the current directory

    # Ensure folders exist
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    # Check if the request has a file
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']

    # Ensure the file is a GLB
    if file.filename.split('.')[-1].lower() != 'glb':
        return jsonify({"error": "Invalid file format. Only GLB files are supported."}), 400

    try:
        # Save the uploaded file to the uploads directory
        glb_path = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(glb_path)

        # Load the GLB file using trimesh
        glb_mesh = trimesh.load(glb_path)

        # Generate STL path
        stl_filename = os.path.splitext(file.filename)[0] + ".stl"
        stl_path = os.path.join(OUTPUT_FOLDER, stl_filename)

        # Export to STL
        glb_mesh.export(stl_path)

        return send_file(stl_path, as_attachment=True)

    except Exception as e:
        return jsonify({"error": str(e)}), 500



@viewer.route('/viewer_imaging/<int:patient_id>', methods=['GET'])
@login_required
def viewer_imaging(patient_id):
    """
    Render the HTML viewer page with files for a specific patient.
    Automatically fetch and pass STL files, images, and DICOM files for the given patient_id.
    """
    try:
        # Define the S3 path for imaging files
        s3_prefix = f"patients/{patient_id}/imaging/"
        logger.debug(f"Fetching files from S3 with prefix: {s3_prefix}")

        # Prepare dictionary to group files by type
        files_dict = {
            'images': [],
            'stl': [],
            'dicom': [],
        }

        # Use S3 paginator to list objects under the prefix
        paginator = s3_client.get_paginator('list_objects_v2')
        operation_parameters = {
            'Bucket': os.getenv('S3_BUCKET_NAME'),
            'Prefix': s3_prefix,
        }
        page_iterator = paginator.paginate(**operation_parameters)

        for page in page_iterator:
            if 'Contents' in page:
                for obj in page['Contents']:
                    file_key = obj['Key']
                    file_name = os.path.basename(file_key)

                    try:
                        # Generate pre-signed URL for the file
                        presigned_url = s3_client.generate_presigned_url(
                            'get_object',
                            Params={'Bucket': os.getenv('S3_BUCKET_NAME'), 'Key': file_key},
                            ExpiresIn=7200  # URL valid for 2 hour
                        )
                    except Exception as e:
                        logger.error(f"Error generating pre-signed URL for {file_name}: {str(e)}")
                        continue

                    # Filter and categorize files by extension
                    if file_name.lower().endswith(('png', 'jpg', 'jpeg', 'img')):
                        files_dict['images'].append({'file_name': file_name, 'url': presigned_url})
                    elif file_name.lower().endswith('.dcm'):
                        files_dict['dicom'].append({'file_name': file_name, 'url': presigned_url})
                    elif file_name.lower().endswith('.stl'):
                        files_dict['stl'].append({'file_name': file_name, 'url': presigned_url})

        # Log and handle the case when no files are found
        if not any(files_dict.values()):
            logger.warning(f"No relevant files found under S3 prefix: {s3_prefix}")
        else:
            logger.info(f"Successfully fetched files for patient {patient_id}: {files_dict}")

        # Render the viewer page with grouped files
        return render_template('viewer_images.html', patient_id=patient_id, files=files_dict)

    except Exception as e:
        logger.error(f"Error fetching files for patient {patient_id}: {str(e)}")
        flash(f"Error fetching files: {str(e)}", 'error')
        return redirect(url_for('main.patient_list'))


def build_s3_tree(keys):
    """
    Build a nested dictionary representing the folder tree from a list of S3 keys.
    Example:
        keys = ['folder1/file1.txt', 'folder1/folder2/file2.txt']
        returns:
        {
            'folder1': {
                'file1.txt': {},
                'folder2': {
                    'file2.txt': {}
                }
            }
        }
    """
    tree = {}
    for key in keys:
        # Split the key by '/' to get individual parts
        parts = key.split('/')
        current_level = tree
        for part in parts:
            if part == "":  # Skip empty parts (can happen if key ends with '/')
                continue
            if part not in current_level:
                current_level[part] = {}
            current_level = current_level[part]
    return tree





@viewer.route('/get_s3_tree', methods=['GET'])
@login_required
def get_s3_tree():
    """
    Fetch the folder structure of the S3 bucket starting from the "patients/" base folder.
    For each folder, count the number of DICOM (.dcm), STL (.stl), and image files (.jpg, .jpeg, .png)
    and return this data as a JSON object.
    """
    try:
        bucket = os.getenv('S3_BUCKET_NAME')
        base_prefix = "patients/"
        logger.debug(f"Fetching folders from bucket: {bucket} using base prefix: '{base_prefix}'")

        def get_folders(prefix):
            """
            Recursively fetch folders (common prefixes) under the specified prefix.
            For each folder, count the number of DICOM, STL, and image files (directly under that prefix).
            Returns a dictionary with the following structure:
            
              {
                  "counts": { "dicom": <int>, "stl": <int>, "images": <int> },
                  "children": {
                      "subfolder1": { ... },
                      "subfolder2": { ... },
                      ...
                  }
              }
            """
            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter='/')
            folder_tree = {}
            counts = {"dicom": 0, "stl": 0, "images": 0}

            for page in pages:
                # Count the files immediately under this prefix.
                for obj in page.get('Contents', []):
                    key = obj.get('Key')
                    # Skip if the key represents a folder (ends with a slash)
                    if key.endswith('/'):
                        continue
                    lower_key = key.lower()
                    if lower_key.endswith('.dcm'):
                        counts["dicom"] += 1
                    elif lower_key.endswith('.stl'):
                        counts["stl"] += 1
                    elif lower_key.endswith(('.jpg', '.jpeg', '.png')):
                        counts["images"] += 1

                # Process subfolders (common prefixes)
                for cp in page.get('CommonPrefixes', []):
                    sub_prefix = cp.get('Prefix')
                    folder_name = sub_prefix[len(prefix):].rstrip('/')
                    child_tree = get_folders(sub_prefix)
                    folder_tree[folder_name] = child_tree

            return {"counts": counts, "children": folder_tree}

        tree = get_folders(base_prefix)
        logger.info("Successfully built S3 folder tree structure with file counts.")
        return jsonify(success=True, tree=tree)
    except Exception as e:
        logger.error(f"Error fetching S3 bucket tree structure: {str(e)}")
        return jsonify(success=False, message=str(e)), 500



@viewer.route('/load_tree_strcture')
def load_tree_strcture():
    return render_template('viewer_s3_tree.html')  # Ensure the file is named `upload.html` and located in the `templates` directory.s


@viewer.route('/s3_bucket_tree', methods=['GET'])
@login_required
def s3_bucket_tree():
    """
    Fetch the folder structure of the S3 bucket starting from the "patients/" base folder.
    For each folder, count the number of DICOM (.dcm), STL (.stl), and image files (.jpg, .jpeg, .png)
    and return this data as part of the tree structure.
    """
    try:
        bucket = os.getenv('S3_BUCKET_NAME')
        # Set the base prefix to "patients/" so that the tree is built starting from that folder.
        base_prefix = "patients/"
        logger.debug(f"Fetching folders from bucket: {bucket} using base prefix: '{base_prefix}'")

        def get_folders(prefix):
            """
            Recursively fetch folders (common prefixes) under the specified prefix.
            For each folder, count the number of DICOM, STL, and image files (directly under that prefix).
            Returns a dictionary with the following structure:
            
              {
                  "counts": { "dicom": <int>, "stl": <int>, "images": <int> },
                  "children": {
                      "subfolder1": { ... },
                      "subfolder2": { ... },
                      ...
                  }
              }
            """
            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter='/')
            folder_tree = {}
            counts = {"dicom": 0, "stl": 0, "images": 0}

            for page in pages:
                # Count the files immediately under this prefix.
                for obj in page.get('Contents', []):
                    key = obj.get('Key')
                    # Skip if the key represents a folder (ends with a slash)
                    if key.endswith('/'):
                        continue
                    lower_key = key.lower()
                    if lower_key.endswith('.dcm'):
                        counts["dicom"] += 1
                    elif lower_key.endswith('.stl'):
                        counts["stl"] += 1
                    elif lower_key.endswith(('.jpg', '.jpeg', '.png')):
                        counts["images"] += 1

                # Process subfolders (common prefixes)
                for cp in page.get('CommonPrefixes', []):
                    sub_prefix = cp.get('Prefix')
                    # Remove the current prefix and trailing slash to obtain the folder name.
                    folder_name = sub_prefix[len(prefix):].rstrip('/')
                    # Recursively build the tree for the subfolder.
                    child_tree = get_folders(sub_prefix)
                    folder_tree[folder_name] = child_tree

            return {"counts": counts, "children": folder_tree}

        # Build the tree starting from the "patients/" base prefix.
        tree = get_folders(base_prefix)
        logger.info("Successfully built S3 folder tree structure with file counts.")
        return render_template('viewer_s3_tree.html', tree=tree)

    except Exception as e:
        logger.error(f"Error fetching S3 bucket tree structure: {str(e)}")
        flash(f"Error fetching bucket structure: {str(e)}", 'error')
        return redirect(url_for('main.patient_list'))


@viewer.route('/load_images/<path:folder>', methods=['GET'])
@login_required
def load_images(folder):
    """
    Given a folder path in the S3 bucket, fetch all the image files (e.g., .jpg, .jpeg, .png)
    in that folder and return their pre-signed URLs as JSON.
    The folder path is expected to be the relative path/prefix in the S3 bucket.
    """
    try:
        bucket = os.getenv('S3_BUCKET_NAME')
        if not bucket:
            raise ValueError("S3_BUCKET_NAME environment variable not set.")

        # Ensure the prefix ends with a slash if it's not empty
        prefix = folder if folder.endswith('/') else f"{folder}/"
        logger.debug(f"Fetching image files from S3 with prefix: {prefix}")

        # Use S3 paginator to list objects under the prefix
        paginator = s3_client.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket, Prefix=prefix)

        image_files = []
        for page in page_iterator:
            if 'Contents' in page:
                for obj in page['Contents']:
                    file_key = obj['Key']
                    # Only include files that end with .jpg, .jpeg, or .png (case-insensitive)
                    if file_key.lower().endswith(('.jpg', '.jpeg', '.png')):
                        try:
                            # Generate pre-signed URL for each image file
                            presigned_url = s3_client.generate_presigned_url(
                                'get_object',
                                Params={'Bucket': bucket, 'Key': file_key},
                                ExpiresIn=7200  # URL valid for 2 hours
                            )
                            image_files.append({
                                'file_name': os.path.basename(file_key),
                                'url': presigned_url
                            })
                            logger.debug(f"Added image file: {file_key}")
                        except Exception as e:
                            logger.error(f"Error generating pre-signed URL for {file_key}: {str(e)}")
                            continue

        if not image_files:
            logger.info(f"No image files found under prefix '{prefix}'")
            return jsonify(success=False, message="No image files found in the selected folder."), 404

        logger.info(f"Successfully fetched {len(image_files)} image files from folder '{folder}'")
        return jsonify(success=True, image_files=image_files, folder=folder), 200

    except Exception as e:
        logger.error(f"Error loading image files from folder {folder}: {str(e)}")
        return jsonify(success=False, message=f"Error loading image files: {str(e)}"), 500




@viewer.route('/load_stl/<path:folder>', methods=['GET'])
@login_required
def load_stl(folder):
    """
    Given a folder path in the S3 bucket, fetch all the STL (.stl) and GLB (.glb) files
    in that folder and return their pre-signed URLs as JSON.
    The folder path is expected to be the relative path/prefix in the S3 bucket.
    """
    try:
        bucket = os.getenv('S3_BUCKET_NAME')
        if not bucket:
            raise ValueError("S3_BUCKET_NAME environment variable not set.")

        # Ensure the prefix ends with a slash if it's not empty
        prefix = folder if folder.endswith('/') else f"{folder}/"
        logger.debug(f"Fetching STL files from S3 with prefix: {prefix}")

        # Use S3 paginator to list objects under the prefix
        paginator = s3_client.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket, Prefix=prefix)

        stl_files = []
        for page in page_iterator:
            if 'Contents' in page:
                for obj in page['Contents']:
                    file_key = obj['Key']
                    # Only include files that end with .stl or .glb (case-insensitive)
                    if file_key.lower().endswith(('.stl', '.glb')):
                        try:
                            # Generate a pre-signed URL for the file
                            presigned_url = s3_client.generate_presigned_url(
                                'get_object',
                                Params={'Bucket': bucket, 'Key': file_key},
                                ExpiresIn=7200  # URL valid for 2 hours
                            )
                            stl_files.append({
                                'file_name': os.path.basename(file_key),
                                'url': presigned_url
                            })
                            logger.debug(f"Added STL file: {file_key}")
                        except Exception as e:
                            logger.error(f"Error generating pre-signed URL for {file_key}: {str(e)}")
                            continue

        if not stl_files:
            logger.info(f"No STL/GLB files found under prefix '{prefix}'")
            return jsonify(success=False, message="No STL/GLB files found in the selected folder."), 404

        logger.info(f"Successfully fetched {len(stl_files)} STL/GLB files from folder '{folder}'")
        return jsonify(success=True, stl_files=stl_files, folder=folder), 200

    except Exception as e:
        logger.error(f"Error loading STL files from folder {folder}: {str(e)}")
        return jsonify(success=False, message=f"Error loading STL files: {str(e)}"), 500



@viewer.route('/load_dicom/<path:folder>', methods=['GET'])
@login_required
def load_dicom(folder):
    """
    Given a folder path in the S3 bucket, fetch all the DICOM (.dcm) files in that folder
    and return their pre-signed URLs as JSON.
    The folder path is expected to be the relative path/prefix in the S3 bucket.
    """
    try:
        bucket = os.getenv('S3_BUCKET_NAME')
        if not bucket:
            raise ValueError("S3_BUCKET_NAME environment variable not set.")

        # Ensure the prefix ends with a slash if it's not empty
        prefix = folder if folder.endswith('/') else f"{folder}/"
        logger.debug(f"Fetching DICOM files from S3 with prefix: {prefix}")

        # Use a paginator in case there are many objects
        paginator = s3_client.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket, Prefix=prefix)

        dicom_files = []
        for page in page_iterator:
            contents = page.get('Contents', [])
            for obj in contents:
                file_key = obj['Key']
                # Only include files that end with .dcm (case-insensitive)
                if file_key.lower().endswith('.dcm'):
                    try:
                        presigned_url = s3_client.generate_presigned_url(
                            'get_object',
                            Params={'Bucket': bucket, 'Key': file_key},
                            ExpiresIn=7200  # URL valid for 2 hour.
                        )
                        dicom_files.append({
                            'file_name': os.path.basename(file_key),
                            'url': presigned_url
                        })
                        logger.debug(f"Added DICOM file: {file_key}")
                    except Exception as e:
                        logger.error(f"Error generating pre-signed URL for {file_key}: {str(e)}")
                        continue

        if not dicom_files:
            logger.info(f"No DICOM files found under prefix '{prefix}'")
            return jsonify(success=False,
                           message="No DICOM files found in the selected folder."), 404

        logger.info(f"Successfully fetched {len(dicom_files)} DICOM files from folder '{folder}'")
        return jsonify(success=True, dicom_files=dicom_files, folder=folder), 200

    except Exception as e:
        logger.error(f"Error loading DICOM files from folder {folder}: {str(e)}")
        return jsonify(success=False, message=f"Error loading DICOM files: {str(e)}"), 500



@viewer.route("/folder_selection/<viewer_type>/<path:selected_folder>", methods=["GET"])
@login_required
def folder_selection(viewer_type, selected_folder):
    """
    Renders the appropriate viewer template based on the viewer type.
    - If viewer_type is "dicom", it renders load_dicom.html.
    - If viewer_type is "image", it renders load_images.html.
    - If viewer_type is "stl", it renders load_stl.html.
    
    The selected_folder is passed to the template.
    """
    logger.info(f"Viewer selection: {viewer_type}, Folder: {selected_folder}")

    if viewer_type.lower() == "dicom":
        return render_template("load_dicom.html", folder=selected_folder)
    elif viewer_type.lower() == "image":
        return render_template("load_images.html", folder=selected_folder)
    elif viewer_type.lower() == "stl":
        return render_template("load_stl.html", folder=selected_folder)
    else:
        logger.error(f"Invalid viewer type specified: {viewer_type}")
        flash("Invalid viewer type specified", "error")
        return redirect(url_for('main.patient_list'))


from flask import render_template
from flask_app.routes.viewer_routes import viewer
from flask_app.models import DataSources, Observations

@viewer.route('/create_report_wizard')
def create_report_wizard():
    # Query the database for data sources and observations
    data_sources_q = DataSources.query.all()
    observations_q = Observations.query.all()
    
    # Convert SQLAlchemy objects into lists of dictionaries for the template
    data_sources = [{"id": ds.DataSourceID, "name": ds.Name} for ds in data_sources_q]
    observations = [{"id": obs.ObservationID, "text": obs.Text, "data_source": obs.DataSourceID} for obs in observations_q]
    
    # Hardcoded HL7-like list for previous conditions (could later also be stored in DB)
    conditions_list = [
        "hypertension", "asthma", "diabetes", "hyperlipidemia", "obesity", "COPD", "sleep apnea"
    ]

    patient = {
        'id': 123,
        'name': 'John Doe',
        'dob': '1980-01-01',
        'gender': 'Male',
        'previous_conditions': ""
    }

    return render_template('create_report_wizard.html', 
                           patient=patient, 
                           data_sources=data_sources, 
                           observations=observations,
                           conditions_list=conditions_list)



from flask import jsonify, request, current_app
from openai import OpenAI

# Instantiate the client with your API key

@viewer.route('/generate_report', methods=['POST'])
def generate_report():
    current_app.logger.info("Received request on /generate_report endpoint.")
    prompt = request.json.get('prompt', '')
    current_app.logger.debug(f"Prompt received: {prompt}")
    
    # Get the API key from the environment
    api_key = os.getenv("OPENAI_API_KEY")
    current_app.logger.debug(f"OPENAI_API_KEY from env: {api_key}")
    
    # Instantiate the client with the API key
    client = OpenAI(api_key=api_key)
    current_app.logger.debug(f"Client API key: {client.api_key}")
    
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an expert sleep dentistry doctor generating detailed sleep treatment recommendations."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        report = response.choices[0].message.content
        current_app.logger.info("Report successfully generated by OpenAI API.")
        return jsonify({'report': report})
    except Exception as e:
        current_app.logger.error("Error generating report: %s", e, exc_info=True)
        return jsonify({'error': str(e)}), 500
    
# Route: /upload_testing_file
@viewer.route('/upload_testing_file', methods=['POST'])
@login_required
def upload_testing_file():
    current_app.logger.info("upload_testing_file route called")
    if 'file' not in request.files:
        current_app.logger.error("No file provided in request")
        return jsonify({'success': False, 'message': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        current_app.logger.error("No file selected")
        return jsonify({'success': False, 'message': 'No file selected'}), 400

    filename = secure_filename(file.filename)
    s3_key = f"testing/{filename}"
    current_app.logger.info(f"Uploading file: {filename} to S3 key: {s3_key}")

    try:
        s3_client.upload_fileobj(
            file,
            os.getenv('S3_BUCKET_NAME'),
            s3_key,
            ExtraArgs={'ContentType': file.mimetype}
        )
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': os.getenv('S3_BUCKET_NAME'), 'Key': s3_key},
            ExpiresIn=7200
        )
        current_app.logger.info(f"File uploaded successfully. Presigned URL: {presigned_url}")
        return jsonify({'success': True, 'url': presigned_url})
    except Exception as e:
        current_app.logger.error(f"Error in upload_testing_file: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


@viewer.route('/analyze_file', methods=['POST'])
@login_required
def analyze_file():
    try:
        logger.info("Starting file analysis")
        
        # Ensure we're getting JSON data
        if not request.is_json:
            return jsonify({
                'success': False,
                'error': 'Content-Type must be application/json'
            }), 400
            
        data = request.get_json()
        logger.debug(f"Received request data: {data}")
        
        if not data or 'file_url' not in data or 'datasource_id' not in data:
            logger.error("Missing required parameters in request")
            return jsonify({
                'success': False,
                'error': 'Missing required parameters: file_url and datasource_id'
            }), 400

        file_url = data['file_url']
        datasource_id = data['datasource_id']
        
        logger.info(f"Processing file: {file_url} for datasource: {datasource_id}")
        
        # Download the file
        try:
            response = requests.get(file_url)
            response.raise_for_status()
            file_content = response.content
            logger.info(f"Successfully downloaded file, size: {len(file_content)} bytes")
        except requests.exceptions.RequestException as e:
            logger.error(f"Error downloading file: {str(e)}")
            return jsonify({
                'success': False,
                'error': f'Error downloading file: {str(e)}'
            }), 500

        # Extract text from the file
        try:
            text = extract_text_from_file(file_content)
            if not text or not text.strip():
                return jsonify({
                    'success': False,
                    'error': 'No text could be extracted from the file'
                }), 400
            logger.info(f"Successfully extracted text, length: {len(text)} characters")
        except Exception as e:
            logger.error(f"Error extracting text: {str(e)}")
            return jsonify({
                'success': False,
                'error': f'Error extracting text: {str(e)}'
            }), 500

        # Analyze the text
        try:
            result = extract_observations_from_text(text, datasource_id)
            if not result or not isinstance(result, dict):
                return jsonify({
                    'success': False,
                    'error': 'Failed to analyze text: Invalid response format'
                }), 500
                
            logger.info(f"Successfully analyzed text")
            return jsonify(result)
        except Exception as e:
            logger.error(f"Error analyzing text: {str(e)}")
            return jsonify({
                'success': False,
                'error': f'Error analyzing text: {str(e)}'
            }), 500

    except Exception as e:
        logger.error(f"Unexpected error in analyze_file: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Unexpected error: {str(e)}'
        }), 500

def extract_text_from_file(file_content):
    """Extract text from PDF or image file content."""
    current_app.logger.debug("=== Starting text extraction process ===")
    try:
        # First try to read as PDF
        try:
            current_app.logger.debug("Attempting PDF extraction with PdfReader...")
            pdf_reader = PdfReader(BytesIO(file_content))
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
            
            if text.strip():
                current_app.logger.debug("Successfully extracted text using PdfReader")
                return text

            current_app.logger.debug("No text found with PdfReader, trying pdfplumber...")
            with pdfplumber.open(BytesIO(file_content)) as pdf:
                text = ""
                for page in pdf.pages:
                    text += page.extract_text() or "" + "\n"
                if text.strip():
                    current_app.logger.debug("Successfully extracted text using pdfplumber")
                    return text

            current_app.logger.debug("No text found with pdfplumber, attempting OCR...")
            images = convert_from_bytes(file_content)
            text = ""
            for image in images:
                img_byte_arr = BytesIO()
                image.save(img_byte_arr, format='PNG')
                img_byte_arr = img_byte_arr.getvalue()
                text += pytesseract.image_to_string(Image.open(BytesIO(img_byte_arr)), lang="eng+heb") + "\n"
            current_app.logger.debug("Successfully extracted text using OCR")
            return text

        except Exception as pdf_error:
            current_app.logger.warning(f"PDF extraction failed: {str(pdf_error)}, trying image OCR")
            
            # If PDF fails, try processing as image
            text = pytesseract.image_to_string(Image.open(BytesIO(file_content)), lang="eng+heb")
            current_app.logger.debug("Successfully extracted text from image")
            return text

    except Exception as e:
        current_app.logger.error(f"Text extraction failed: {str(e)}")
        raise Exception(f"Failed to extract text from file: {str(e)}")

def extract_observations_from_text(extracted_text: str, datasource_id: int) -> dict:
    """Extract observations from text using the current AI provider."""
    if CURRENT_AI_PROVIDER == AIProvider.CLAUDE:
        return extract_observations_claude(extracted_text, datasource_id)
    else:
        return extract_observations_openai(extracted_text, datasource_id)

def get_extraction_prompt(datasource_id: int) -> tuple[str, bool]:
    """Get the appropriate extraction prompt for the given datasource."""
    try:
        # Query the database for datasource information
        datasource = DataSources.query.get(datasource_id)
        if not datasource:
            logger.error(f"Datasource with ID {datasource_id} not found")
            return "Datasource not found", False
            
        # Check if this is a supported datasource type - using more flexible string matching
        datasource_name = datasource.Name.lower().replace('-', '').replace(' ', '')
        if not ('sleep' in datasource_name or 'questionnaire' in datasource_name):
            logger.error(f"Unsupported datasource type: {datasource.Name}. Only sleep tests and questionnaires are currently supported.")
            return "This type of datasource is not currently supported for analysis", False
            
        # Query available observations for this datasource
        available_observations = Observations.query.filter_by(DataSourceID=datasource_id).all()
        if not available_observations:
            logger.error(f"No observations found for datasource {datasource.Name} (ID: {datasource_id})")
            return "No observations configured for this datasource", False
        
        observation_texts = [obs.Text for obs in available_observations]
        
        base_prompt = """Analyze the following document for specific medical observations.

For each observation found:
1. Extract the exact observation
2. Include any specific values and units
3. Note the evidence text that supports this observation
4. Assess confidence level (0-100)

Format your response as JSON with this structure:
{
    "observations": [
        {
            "observation": "exact observation text",
            "value": "numerical value if any",
            "unit": "unit of measurement if any",
            "evidence": "supporting text from document",
            "confidence": confidence_score
        }
    ]
}"""

        # Add datasource-specific instructions
        if 'sleep' in datasource_name:
            specific_instructions = f"""
Focus ONLY on finding observations that match these specific criteria:
{', '.join(observation_texts)}

Pay special attention to:
- Sleep study metrics (AHI, RDI, oxygen saturation levels)
- Sleep event frequencies and durations
- Only include observations that directly match the criteria listed above"""

        else:  # questionnaire
            specific_instructions = f"""
Focus ONLY on finding responses that relate to these specific observations:
{', '.join(observation_texts)}

Pay special attention to:
- Questions and answers that directly relate to the listed observations
- Numerical scores or ratings
- Yes/no responses
- Scale-based answers (e.g., 1-5, never-always)
- Only include responses that match or directly relate to the criteria listed above"""

        final_prompt = base_prompt + "\n" + specific_instructions
        
        logger.info(f"Successfully generated prompt for datasource {datasource.Name} with {len(observation_texts)} observations")
        return final_prompt, True
        
    except Exception as e:
        logger.error(f"Error getting extraction prompt: {str(e)}", exc_info=True)
        return f"Error generating prompt: {str(e)}", False

def get_general_observations_prompt() -> str:
    """Get the prompt for extracting general medical observations."""
    return """Analyze the following document for general medical observations.

For each observation found:
1. Extract the exact observation
2. Include any specific values and units
3. Note the evidence text that supports this observation
4. Assess confidence level (0-100)

Format your response as JSON with this structure:
{
    "observations": [
        {
            "observation": "exact observation text",
            "value": "numerical value if any",
            "unit": "unit of measurement if any",
            "evidence": "supporting text from document",
            "confidence": confidence_score
        }
    ]
}

Focus on:
- Clinically relevant observations
- Medical conditions and symptoms
- Vital signs and measurements
- Only include observations that have clear supporting evidence
- Exclude observations specific to sleep studies or questionnaires"""

def extract_observations_openai(extracted_text: str, datasource_id: int) -> dict:
    """Extract observations using OpenAI's API with improved error handling and logging."""
    try:
        if not openai.api_key:
            logger.error("OpenAI API key is not set")
            return {"success": False, "error": "API key not configured"}

        # Get datasource-specific prompt
        datasource_prompt, prompt_supported = get_extraction_prompt(datasource_id)
        if not prompt_supported:
            logger.error(f"Extraction prompt not supported for datasource {datasource_id}")
            return {"success": False, "error": "Unsupported datasource"}

        logger.debug(f"Extracting observations for datasource {datasource_id}")
        
        # Make datasource-specific observations call
        try:
            datasource_response = openai.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": datasource_prompt},
                    {"role": "user", "content": extracted_text}
                ],
                temperature=0.1
            )
            datasource_observations = json.loads(datasource_response.choices[0].message.content)
        except openai.RateLimitError:
            logger.warning("Rate limit hit for datasource-specific analysis")
            datasource_observations = {"observations": []}
        except Exception as e:
            logger.error(f"Error in datasource analysis: {str(e)}")
            datasource_observations = {"observations": []}

        # Make general observations call
        try:
            general_response = openai.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": get_general_observations_prompt()},
                    {"role": "user", "content": extracted_text}
                ],
                temperature=0.1
            )
            general_observations = json.loads(general_response.choices[0].message.content)
        except openai.RateLimitError:
            logger.warning("Rate limit hit for general analysis")
            general_observations = {"observations": []}
        except Exception as e:
            logger.error(f"Error in general analysis: {str(e)}")
            general_observations = {"observations": []}

        # Return standardized response
        return standardize_ai_response(
            document_text=extracted_text,
            response_data={
                "datasource_observations": datasource_observations.get("observations", []),
                "general_observations": general_observations.get("observations", [])
            },
            provider="openai"
        )

    except Exception as e:
        logger.error(f"Error in extract_observations_openai: {str(e)}\n{traceback.format_exc()}")
        return {"success": False, "error": "Failed to process document"}

def standardize_ai_response(document_text: str, response_data: dict, provider: str) -> dict:
    """
    Standardize the AI response format across different providers.
    
    Args:
        document_text (str): The original text that was analyzed
        response_data (dict): The raw response data from the AI provider
        provider (str): The AI provider used ('claude' or 'openai')
    
    Returns:
        dict: A standardized response format
    """
    try:
        # Initialize the standardized response
        standardized_response = {
            "success": True,
            "data": {
                "document_text": document_text,
                "datasource_observations": [],
                "general_observations": [],
                "general_info": {
                    "extraction_status": "success",
                    "processing_completed": datetime.utcnow().isoformat(),
                    "provider": provider,
                    "error": None
                }
            }
        }
        
        # Handle error cases
        if "error" in response_data:
            standardized_response["success"] = False
            standardized_response["data"]["general_info"]["extraction_status"] = "error"
            standardized_response["data"]["general_info"]["error"] = response_data["error"]
            return standardized_response
            
        # Process datasource-specific observations
        if "datasource_observations" in response_data:
            standardized_response["data"]["datasource_observations"] = response_data["datasource_observations"]
            
        # Process general observations
        if "general_observations" in response_data:
            standardized_response["data"]["general_observations"] = response_data["general_observations"]
        elif "extracted_observations" in response_data:
            # Handle OpenAI format where all observations are in extracted_observations
            standardized_response["data"]["general_observations"] = response_data["extracted_observations"]
            
        # Add confidence summary for both types of observations
        standardized_response["data"]["general_info"]["datasource_confidence"] = summarize_confidence(
            standardized_response["data"]["datasource_observations"]
        )
        standardized_response["data"]["general_info"]["general_confidence"] = summarize_confidence(
            standardized_response["data"]["general_observations"]
        )
        
        return standardized_response
        
    except Exception as e:
        logger.error(f"Error in standardize_ai_response: {str(e)}")
        return {
            "success": False,
            "data": {
                "document_text": document_text,
                "datasource_observations": [],
                "general_observations": [],
                "general_info": {
                    "extraction_status": "error",
                    "processing_completed": datetime.utcnow().isoformat(),
                    "provider": provider,
                    "error": f"Error standardizing response: {str(e)}"
                }
            }
        }

def summarize_confidence(observations: list) -> dict:
    """
    Summarize confidence levels of observations.
    """
    if not observations:
        return {
            "average_confidence": 0,
            "high_confidence_count": 0,
            "medium_confidence_count": 0,
            "low_confidence_count": 0,
            "total_observations": 0
        }
    
    # Calculate confidence metrics
    confidence_values = [obs.get("confidence", 0) for obs in observations if obs is not None]
    avg_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0
    
    # Count by confidence level
    high_count = sum(1 for c in confidence_values if c >= 80)
    medium_count = sum(1 for c in confidence_values if 60 <= c < 80)
    low_count = sum(1 for c in confidence_values if c < 60)
    
    return {
        "average_confidence": avg_confidence,
        "high_confidence_count": high_count,
        "medium_confidence_count": medium_count,
        "low_confidence_count": low_count,
        "total_observations": len(confidence_values)
    }

def test_extraction_prompt():
    """Test the extraction prompts to ensure they're properly formatted"""
    test_text = """
    Patient Name: John Doe
    Age: 45
    Blood Pressure: 120/80
    Chief Complaint: Headache and fatigue
    """
    
    try:
        # Test general observations prompt
        general_prompt = get_general_observations_prompt()
        current_app.logger.debug("Testing general observations prompt...")
        current_app.logger.debug(f"Prompt:\n{general_prompt}")
        
        # Test with a known datasource type
        test_prompt, is_supported = get_extraction_prompt("Sleep Study Report")
        current_app.logger.debug("Testing sleep study prompt...")
        current_app.logger.debug(f"Prompt:\n{test_prompt}")
        current_app.logger.debug(f"Is supported: {is_supported}")
        
    except Exception as e:
        current_app.logger.error(f"Error testing prompts: {str(e)}")

def analyze_all_patient_medical_files(patient_id):
    s3_prefix = f"patients/{patient_id}/medical/"
    files_list = []
    paginator = s3_client.get_paginator('list_objects_v2')
    operation_parameters = {
        'Bucket': os.getenv('S3_BUCKET_NAME'),
        'Prefix': s3_prefix
    }
    page_iterator = paginator.paginate(**operation_parameters)
    for page in page_iterator:
        if 'Contents' in page:
            for obj in page['Contents']:
                file_key = obj['Key']
                file_name = os.path.basename(file_key)
                # Filter for relevant medical files (customize as needed)
                if file_name.lower().endswith(('.pdf', '.jpg', '.jpeg', '.png')):
                    files_list.append(file_key)

    all_observations = []
    for file_key in files_list:
        obj = s3_client.get_object(Bucket=os.getenv('S3_BUCKET_NAME'), Key=file_key)
        file_content = obj['Body'].read()
        try:
            text = extract_text_from_file(file_content)
        except Exception as e:
            logger.error(f"Failed to extract text from {file_key}: {e}")
            continue

        # Use your robust extraction logic
        datasource_id = 1  # Or determine dynamically if needed
        result = extract_observations_from_text(text, datasource_id)
        if result and result.get('success'):
            all_observations.extend(result['data'].get('datasource_observations', []))
            all_observations.extend(result['data'].get('general_observations', []))

    # Build a prompt for OpenAI
    obs_text = "\n".join(
        [f"- {obs.get('observation', '')}: {obs.get('value', '')} {obs.get('unit', '')} (evidence: {obs.get('evidence', '')})"
         for obs in all_observations]
    )
    ai_prompt = (
        "You are an expert sleep medicine doctor. "
        "Given the following patient observations from medical records, "
        "provide a concise diagnosis and summary of the patient's OSA status. "
        "List any key findings and suggest next diagnostic steps if needed.\n\n"
        f"Patient Observations:\n{obs_text}"
    )

    ai_response = openai.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": ai_prompt}
        ],
        temperature=0.3
    )
    diagnosis = ai_response.choices[0].message.content

    return {
        "observations": all_observations,
        "diagnosis": diagnosis,
        "openai_prompt": ai_prompt
    }

