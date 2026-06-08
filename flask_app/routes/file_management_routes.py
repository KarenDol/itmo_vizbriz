from flask import Blueprint, render_template, request, Response, redirect, url_for, flash, current_app
from flask_app.extensions import db
from flask_login import login_required, current_user
from ..models import db, Patient, File, Dentist, AdminFile, Claim, Comment, PatientStatus, StatusOption, PatientComment, Clinic
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
import logging
import boto3 
from botocore.config import Config
from botocore.exceptions import ClientError
import os
from pathlib import Path
from werkzeug.utils import secure_filename
import time
import urllib.parse
import re
import requests
import rarfile
# Configure rarfile to use 7z (more widely available than unrar)
rarfile.UNRAR_TOOL = '7z'
rarfile.ALT_TOOL = '7za'
from flask import send_file, jsonify, after_this_request
import tempfile
import shutil
import io
import pydicom
import base64
# Flask-Mail is used for email sending (same as working wizard implementation)
from requests.auth import HTTPBasicAuth
from pydicom.uid import generate_uid
import subprocess
import json
from threading import Lock, Thread
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps

# DICOM file extensions including MicroDICOM (.microsdcm) and common variants
DICOM_EXTENSIONS = ('.dcm', '.dicom', '.dcom', '.microsdcm')


def is_dicom_file(key_or_path):
    """Check if file key/path has a DICOM extension."""
    lower = (key_or_path or '').lower()
    return any(lower.endswith(ext) for ext in DICOM_EXTENSIONS)


def convert_dicom_to_uncompressed(input_path, output_path):
    """
    Convert a compressed DICOM file to uncompressed format using gdcmconv.
    Returns True if successful, False otherwise.
    """
    try:
        # Check if gdcmconv is available
        try:
            subprocess.run(['gdcmconv', '--version'], capture_output=True, check=True)
        except (subprocess.SubprocessError, FileNotFoundError):
            logger.error("gdcmconv not found. Please install GDCM tools.")
            return False

        # Convert the file
        result = subprocess.run([
            'gdcmconv',
            '--raw',  # Use raw (uncompressed) transfer syntax
            input_path,
            output_path
        ], capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"Conversion failed: {result.stderr}")
            return False

        # Verify the output file exists and is readable
        if not os.path.exists(output_path):
            logger.error("Output file was not created")
            return False

        try:
            ds = pydicom.dcmread(output_path, stop_before_pixels=True)
            if str(ds.file_meta.TransferSyntaxUID) != '1.2.840.10008.1.2.1':  # Uncompressed
                logger.error("Output file is not uncompressed")
                return False
        except Exception as e:
            logger.error(f"Error verifying output file: {str(e)}")
            return False

        return True

    except Exception as e:
        logger.error(f"Error in convert_dicom_to_uncompressed: {str(e)}")
        return False


def analyze_dicom_for_multiframe(ds, file_path):
    """
    Analyze a DICOM dataset to determine if it's multi-frame.
    Uses Weasis-style checks: PerFrameFunctionalGroupsSequence, NumberOfFrames, pixel array.
    """
    analysis = {
        'is_multiframe': False,
        'reason': '',
        'number_of_frames': None,
        'frame_dim': 0,
        'modality': getattr(ds, 'Modality', 'Unknown'),
        'manufacturer': getattr(ds, 'Manufacturer', 'Unknown'),
        'model': getattr(ds, 'ManufacturerModelName', 'Unknown'),
        'file_size_mb': round(os.path.getsize(file_path) / (1024 * 1024), 2),
        'indicators': []
    }
    
    # Check 0: PerFrameFunctionalGroupsSequence (Enhanced Multi-Frame - Weasis/standard check)
    if hasattr(ds, 'PerFrameFunctionalGroupsSequence') and len(ds.PerFrameFunctionalGroupsSequence) > 1:
        n = len(ds.PerFrameFunctionalGroupsSequence)
        analysis['number_of_frames'] = n
        analysis['indicators'].append(f"PerFrameFunctionalGroupsSequence: {n} frames (Enhanced Multi-Frame)")
        analysis['is_multiframe'] = True
        analysis['reason'] = f"Enhanced Multi-Frame with {n} frames (PerFrameFunctionalGroupsSequence)"
        return analysis
    elif hasattr(ds, 'PerFrameFunctionalGroupsSequence') and len(ds.PerFrameFunctionalGroupsSequence) == 1:
        analysis['indicators'].append("PerFrameFunctionalGroupsSequence: 1 frame only")
    
    # Check 1: NumberOfFrames attribute
    if hasattr(ds, 'NumberOfFrames'):
        analysis['number_of_frames'] = ds.NumberOfFrames
        analysis['indicators'].append(f"NumberOfFrames: {ds.NumberOfFrames}")
        if ds.NumberOfFrames > 1:
            analysis['is_multiframe'] = True
            analysis['reason'] = f"Confirmed multi-frame with {ds.NumberOfFrames} frames"
            return analysis
    else:
        analysis['indicators'].append("No NumberOfFrames attribute")
    
    # Check 2: Pixel Array Shape (authoritative - overrides NumberOfFrames if mismatch)
    try:
        pixel_array = ds.pixel_array
        analysis['indicators'].append(f"Pixel Array Shape: {pixel_array.shape}")
        if pixel_array.ndim == 3:
            n0, n1, n2 = pixel_array.shape
            if n0 > 1:
                analysis['frame_dim'] = 0
                analysis['number_of_frames'] = n0
                analysis['indicators'].append(f"Pixel array has {n0} frames (dim 0)")
                analysis['is_multiframe'] = True
                analysis['reason'] = f"Multi-frame from pixel array shape: {pixel_array.shape}"
                return analysis
            if n2 > 1:
                analysis['frame_dim'] = 2
                analysis['number_of_frames'] = n2
                analysis['indicators'].append(f"Pixel array has {n2} frames (dim 2)")
                analysis['is_multiframe'] = True
                analysis['reason'] = f"Multi-frame from pixel array shape: {pixel_array.shape}"
                return analysis
        analysis['indicators'].append("Pixel array is single frame")
    except Exception as e:
        analysis['indicators'].append(f"Could not load pixel array: {str(e)}")
    
    # Check 3: File size (heuristic)
    if analysis['file_size_mb'] > 10:
        analysis['indicators'].append(f"Large file size ({analysis['file_size_mb']} MB) - typical for multi-frame")
    else:
        analysis['indicators'].append(f"File size: {analysis['file_size_mb']} MB")
    
    # Check 4: Manufacturer / software hints (Micro-DICOM viewer exports, etc.)
    manufacturer = analysis['manufacturer'].lower()
    if 'microdicom' in manufacturer or 'microdicom' in getattr(ds, 'SoftwareVersions', '') or 'microdicom' in getattr(ds, 'ManufacturerModelName', '').lower():
        analysis['indicators'].append("MicroDICOM detected - likely multi-frame")
        analysis['is_multiframe'] = True
        analysis['reason'] = "MicroDICOM export detected - likely multi-frame"
        return analysis
    if 'icordicon' in manufacturer or 'cordicon' in manufacturer:
        analysis['indicators'].append("iCordicon device detected - likely multi-frame")
        analysis['is_multiframe'] = True
        analysis['reason'] = "iCordicon device detected - likely multi-frame"
        return analysis
    elif 'carestream' in manufacturer:
        analysis['indicators'].append("Carestream device detected - may be multi-frame")
    elif 'sirona' in manufacturer:
        analysis['indicators'].append("Sirona device detected - may be multi-frame")
    
    # Check 5: Modality hints
    modality = analysis['modality']
    if modality in ['CT', 'CBCT', 'XA']:
        analysis['indicators'].append(f"{modality} modality - commonly multi-frame")
    elif modality == 'CR':
        analysis['indicators'].append(f"{modality} modality - usually single frame")
    
    # If we get here, it's not clearly multi-frame
    if not analysis['is_multiframe']:
        analysis['reason'] = "No clear indicators of multi-frame DICOM found"
    
    return analysis


def get_per_frame_geometry(ds, frame_index, num_frames):
    """
    Extract per-frame geometry (ImagePositionPatient, ImageOrientationPatient, PixelSpacing)
    for MPR compatibility when splitting multi-frame DICOM.

    Handles both Enhanced Multi-Frame (PerFrameFunctionalGroupsSequence) and legacy multi-frame.
    Returns dict with ipp, iop, pixel_spacing, slice_thickness.
    """
    import numpy as np
    result = {'ipp': None, 'iop': None, 'pixel_spacing': None, 'slice_thickness': None}

    # Try Enhanced Multi-Frame: PerFrameFunctionalGroupsSequence
    if hasattr(ds, 'PerFrameFunctionalGroupsSequence') and frame_index < len(ds.PerFrameFunctionalGroupsSequence):
        try:
            frame_group = ds.PerFrameFunctionalGroupsSequence[frame_index]
            # PlanePositionSequence (0020,9113) -> ImagePositionPatient
            if hasattr(frame_group, 'PlanePositionSequence') and len(frame_group.PlanePositionSequence) > 0:
                result['ipp'] = [float(x) for x in frame_group.PlanePositionSequence[0].ImagePositionPatient]
            # PlaneOrientationSequence (0020,9116) -> ImageOrientationPatient
            if hasattr(frame_group, 'PlaneOrientationSequence') and len(frame_group.PlaneOrientationSequence) > 0:
                result['iop'] = [float(x) for x in frame_group.PlaneOrientationSequence[0].ImageOrientationPatient]
            # PixelMeasuresSequence (0028,9110) -> PixelSpacing, SliceThickness
            if hasattr(frame_group, 'PixelMeasuresSequence') and len(frame_group.PixelMeasuresSequence) > 0:
                pm = frame_group.PixelMeasuresSequence[0]
                if hasattr(pm, 'PixelSpacing'):
                    result['pixel_spacing'] = [float(x) for x in pm.PixelSpacing]
                if hasattr(pm, 'SliceThickness'):
                    result['slice_thickness'] = float(pm.SliceThickness)
        except Exception as e:
            logger.warning(f"Could not extract Enhanced per-frame geometry for frame {frame_index}: {e}")

    # Fallback: SharedFunctionalGroupsSequence (shared across frames)
    if result['iop'] is None and hasattr(ds, 'SharedFunctionalGroupsSequence') and len(ds.SharedFunctionalGroupsSequence) > 0:
        try:
            shared = ds.SharedFunctionalGroupsSequence[0]
            if hasattr(shared, 'PixelMeasuresSequence') and len(shared.PixelMeasuresSequence) > 0:
                pm = shared.PixelMeasuresSequence[0]
                if hasattr(pm, 'PixelSpacing'):
                    result['pixel_spacing'] = [float(x) for x in pm.PixelSpacing]
                if hasattr(pm, 'SliceThickness'):
                    result['slice_thickness'] = float(pm.SliceThickness)
            if hasattr(shared, 'PlaneOrientationSequence') and len(shared.PlaneOrientationSequence) > 0:
                result['iop'] = [float(x) for x in shared.PlaneOrientationSequence[0].ImageOrientationPatient]
        except Exception as e:
            logger.warning(f"Could not extract Shared geometry: {e}")

    # Use top-level attributes as fallback
    if result['pixel_spacing'] is None and hasattr(ds, 'PixelSpacing'):
        result['pixel_spacing'] = [float(x) for x in ds.PixelSpacing]
    if result['iop'] is None and hasattr(ds, 'ImageOrientationPatient'):
        result['iop'] = [float(x) for x in ds.ImageOrientationPatient]
    if result['slice_thickness'] is None and hasattr(ds, 'SliceThickness'):
        result['slice_thickness'] = float(ds.SliceThickness)

    # Compute ImagePositionPatient if not from Enhanced: use first frame IPP + slice_normal * index * spacing
    if result['ipp'] is None and result['iop'] is not None:
        if hasattr(ds, 'ImagePositionPatient'):
            base_ipp = np.array([float(x) for x in ds.ImagePositionPatient[:3]])
        else:
            base_ipp = np.array([0.0, 0.0, 0.0])
        row = np.array(result['iop'][0:3])
        col = np.array(result['iop'][3:6])
        slice_normal = np.cross(row, col)
        norm = np.linalg.norm(slice_normal)
        if norm > 1e-10:
            slice_normal = slice_normal / norm
        spacing = result['slice_thickness'] or 1.0
        result['ipp'] = (base_ipp + slice_normal * frame_index * spacing).tolist()

    return result


def _get_frame_pixels(vol, frame_idx, frame_dim):
    """Extract 2D frame from 3D volume. frame_dim: 0=(Z,Y,X), 2=(Y,X,Z)."""
    if frame_dim == 0:
        return vol[frame_idx, :, :]
    return vol[:, :, frame_idx]


def process_dicom_for_upload(file_bytes, s3_key, patient_id=None):
    """
    Process a DICOM file at upload time: if multi-frame, split into single-frame slices.
    Returns list of dicts: [{'s3_key': str, 'content': bytes, 'filename': str, 'file_size': int}, ...]
    Used so the rest of the system receives single-frame DICOM and MPR works.
    """
    filename = s3_key.split('/')[-1]
    base_name = os.path.splitext(filename)[0]
    parent = s3_key.rsplit('/', 1)[0] + '/' if '/' in s3_key else ''
    result = []
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.dcm') as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            ds = pydicom.dcmread(tmp_path)
            analysis = analyze_dicom_for_multiframe(ds, tmp_path)
            if not analysis['is_multiframe']:
                result.append({'s3_key': s3_key, 'content': file_bytes, 'filename': filename, 'file_size': len(file_bytes)})
                return result
            vol = ds.pixel_array
            if vol.ndim != 3:
                logger.warning(f"Unexpected pixel shape {vol.shape}, storing as-is")
                result.append({'s3_key': s3_key, 'content': file_bytes, 'filename': filename, 'file_size': len(file_bytes)})
                return result
            frame_dim = analysis.get('frame_dim', 0)
            n_frames = vol.shape[frame_dim]
            splits_folder = f"{parent}{base_name}_splits/"
            logger.info(f"Multi-frame DICOM at upload: splitting {s3_key} into {n_frames} slices in {splits_folder}")
            for i in range(n_frames):
                frame_pixels = _get_frame_pixels(vol, i, frame_dim)
                geometry = get_per_frame_geometry(ds, i, n_frames)
                new_ds = _prepare_single_frame_from_multiframe(ds, i, frame_pixels, geometry, patient_id=patient_id)
                with tempfile.NamedTemporaryFile(delete=False, suffix='.dcm') as out:
                    new_ds.save_as(out.name)
                    with open(out.name, 'rb') as f:
                        content = f.read()
                    try:
                        os.remove(out.name)
                    except Exception:
                        pass
                slice_name = f"slice_{i+1:03}.dcm"
                slice_key = f"{splits_folder}{slice_name}"
                result.append({'s3_key': slice_key, 'content': content, 'filename': slice_name, 'file_size': len(content)})
            logger.info(f"Split complete: {n_frames} slices created")
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Could not process DICOM for split (will upload as-is): {e}")
        result.append({'s3_key': s3_key, 'content': file_bytes, 'filename': filename, 'file_size': len(file_bytes)})
    return result


def _prepare_single_frame_from_multiframe(ds, frame_index, frame_pixels, geometry, patient_id=None):
    """
    Create a legacy single-frame DICOM dataset from a multi-frame frame.
    Sets ImagePositionPatient, ImageOrientationPatient, PixelSpacing for MPR compatibility.
    """
    new_ds = ds.copy()
    new_ds.PixelData = frame_pixels.tobytes()
    new_ds.NumberOfFrames = 1
    new_ds.InstanceNumber = frame_index + 1
    new_ds.SOPInstanceUID = generate_uid()

    # Remove Enhanced Multi-Frame sequences (output must be legacy single-frame for MPR)
    for seq_name in ('PerFrameFunctionalGroupsSequence', 'SharedFunctionalGroupsSequence'):
        if seq_name in new_ds:
            del new_ds[seq_name]

    # Set geometry for MPR
    if geometry.get('ipp') is not None:
        new_ds.ImagePositionPatient = geometry['ipp']
    if geometry.get('iop') is not None:
        new_ds.ImageOrientationPatient = geometry['iop']
    if geometry.get('pixel_spacing') is not None:
        new_ds.PixelSpacing = geometry['pixel_spacing']

    # Ensure uncompressed transfer syntax for MPR compatibility
    if hasattr(new_ds, 'file_meta') and new_ds.file_meta.TransferSyntaxUID != pydicom.uid.ExplicitVRLittleEndian:
        new_ds.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian

    if patient_id:
        new_ds = ensure_minimal_dicom_compliance(new_ds, patient_id)
    return new_ds


def process_multiframe_file(file_path, patient_id, s3_client, bucket_name, orthanc_url, orthanc_username, orthanc_password, original_file_key):
    """
    Process a multi-frame DICOM file by splitting it into individual files and uploading to Orthanc
    
    Args:
        file_path: Path to the multi-frame DICOM file
        patient_id: Patient ID
        s3_client: S3 client
        bucket_name: S3 bucket name
        orthanc_url: Orthanc server URL
        orthanc_username: Orthanc username
        orthanc_password: Orthanc password
        original_file_key: Original S3 key for logging
        
    Returns:
        dict: Results with successful uploads and errors
    """
    successful_uploads = 0
    errors = []
    
    try:
        logger.info(f"   🔧 Starting multi-frame processing for: {original_file_key}")
        
        # Load the DICOM file
        ds = pydicom.dcmread(file_path)
        
        # Get frames
        try:
            frames = ds.pixel_array
            logger.info(f"   📊 Extracted {frames.shape[0]} frames from multi-frame DICOM")
        except Exception as e:
            logger.error(f"   ❌ Failed to extract pixel array: {str(e)}")
            errors.append({'file': original_file_key, 'error': f'Failed to extract pixel data: {str(e)}'})
            return {'successful': 0, 'errors': errors}
        
        # Process each frame
        for i in range(frames.shape[0]):
            try:
                logger.info(f"   🔧 Processing frame {i+1}/{frames.shape[0]}...")
                geometry = get_per_frame_geometry(ds, i, frames.shape[0])
                new_ds = _prepare_single_frame_from_multiframe(
                    ds, i, frames[i], geometry, patient_id=patient_id
                )
                
                # Save to temporary file
                with tempfile.NamedTemporaryFile(delete=False, suffix=".dcm") as temp_file:
                    temp_output_path = temp_file.name
                    new_ds.save_as(temp_output_path)
                
                # Read file content
                with open(temp_output_path, 'rb') as f:
                    file_content = f.read()
                
                if not file_content:
                    raise ValueError(f"Generated frame {i+1} is empty")
                
                # Upload to Orthanc
                logger.info(f"   🚀 Uploading frame {i+1} to Orthanc...")
                
                response = requests.post(
                    f"{orthanc_url}/instances",
                    data=file_content,
                    auth=HTTPBasicAuth(orthanc_username, orthanc_password),
                    headers={
                        'Content-Type': 'application/dicom',
                        'Accept': 'application/json'
                    },
                    timeout=120
                )
                
                if response.status_code in [200, 201, 202]:
                    logger.info(f"   ✅ Successfully uploaded frame {i+1} to Orthanc (HTTP {response.status_code})")
                    successful_uploads += 1
                else:
                    logger.error(f"   ❌ Failed to upload frame {i+1} (HTTP {response.status_code})")
                    errors.append({
                        'file': f"{original_file_key}_frame_{i+1}", 
                        'error': f'Orthanc upload failed (HTTP {response.status_code}): {response.text}'
                    })
                
                # Cleanup temporary file
                try:
                    os.remove(temp_output_path)
                except Exception as cleanup_err:
                    logger.warning(f"   ⚠️ Could not delete temp frame file: {cleanup_err}")
                    
            except Exception as frame_err:
                logger.error(f"   ❌ Error processing frame {i+1}: {frame_err}")
                errors.append({
                    'file': f"{original_file_key}_frame_{i+1}", 
                    'error': str(frame_err)
                })
                continue
        
        logger.info(f"   🎉 Multi-frame processing complete: {successful_uploads}/{frames.shape[0]} frames uploaded successfully")
        
    except Exception as e:
        logger.error(f"   ❌ Error in multi-frame processing: {str(e)}")
        errors.append({'file': original_file_key, 'error': f'Multi-frame processing failed: {str(e)}'})
    
    return {
        'successful': successful_uploads,
        'errors': errors
    }



# Blueprint for filemgmt routes
filemgmt = Blueprint('filemgmt', __name__)
logger = logging.getLogger(__name__)
region = os.environ.get('AWS_REGION', 'us-west-2')

# Debug logging for environment variables
logger.debug("=== S3 Environment Variables ===")
logger.debug(f"AWS_ACCESS_KEY_ID: {os.environ.get('AWS_ACCESS_KEY_ID')}")
logger.debug(f"AWS_SECRET_ACCESS_KEY: {'*' * 10 if os.environ.get('AWS_SECRET_ACCESS_KEY') else 'Not set'}")
logger.debug(f"AWS_REGION: {os.environ.get('AWS_REGION')}")
logger.debug(f"S3_BUCKET_NAME: {os.environ.get('S3_BUCKET_NAME')}")
logger.debug("===============================")

s3_client = boto3.client('s3', region_name=region, config=Config(signature_version='s3v4'))
ses_client = boto3.client('ses', region_name=region)

# Log the credentials provider chain
try:
    credentials = s3_client._credentials
    logger.debug(f"S3 Client using credentials from: {credentials.method}")
except Exception as e:
    logger.debug(f"Could not determine credentials method: {str(e)}")

@filemgmt.before_request
def log_request_info():
    logger.debug(f"Request method: {request.method}")
    if request.method == 'OPTIONS':
        logger.debug("CORS preflight request received")
        logger.debug(f"CORS headers: Origin: {request.headers.get('Origin')}, Access-Control-Request-Method: {request.headers.get('Access-Control-Request-Method')}")

def is_non_ascii(string):
    """Helper function to check if a string contains non-ASCII characters."""
    return not all(ord(char) < 128 for char in string)


import zipfile
from io import BytesIO
import mimetypes

def upload_and_save_files(zip_file, s3_base_path, category, patient, subcategory=None):
    logger.debug(f'Processing zip file for category: {category}, subcategory: {subcategory}')

    # Log the file details (name, type, size) before proceeding
    logger.debug(f'File received: {zip_file.filename}, Content-Type: {zip_file.content_type}, Size: {zip_file.content_length} bytes')

    if zip_file.filename.endswith('.zip'):
        logger.debug(f'Received zip file: {zip_file.filename}, starting to unzip and process.')

        # Read the uploaded zip file
        zip_buffer = BytesIO(zip_file.read())

        try:
            # Open the zip file
            with zipfile.ZipFile(zip_buffer, 'r') as zip_file_obj:
                # Iterate over each file in the zip archive
                for zip_info in zip_file_obj.infolist():
                    if not zip_info.is_dir():  # Only process files, skip directories
                        extracted_file = zip_file_obj.open(zip_info)
                        file_bytes = extracted_file.read()
                        
                        # Split the path into parts and sanitize each part
                        zip_path_parts = zip_info.filename.split('/')
                        safe_parts = [secure_filename(part) for part in zip_path_parts]
                        extracted_filename = '/'.join(safe_parts)
                        
                        # Determine MIME type
                        mime_type, _ = mimetypes.guess_type(extracted_filename)
                        mime_type = mime_type or 'application/octet-stream'  # Default fallback

                        logger.debug(f'Processing extracted file: {extracted_filename}, Size: {zip_info.file_size} bytes, MIME type: {mime_type}')

                        # Create S3 key path for the extracted file, preserving directory structure
                        s3_key = f"patients/{patient.id}/{s3_base_path}/{extracted_filename}"
                        logger.debug(f'Uploading extracted file: {extracted_filename} to S3 at {s3_key}')

                        # For CBCT DICOM: split multi-frame at upload so MPR works
                        files_to_upload = []
                        if subcategory == 'cbct' and is_dicom_file(extracted_filename):
                            for item in process_dicom_for_upload(file_bytes, s3_key, patient_id=str(patient.id)):
                                files_to_upload.append((item['s3_key'], item['content'], item['filename'], item['file_size'], 'application/dicom'))
                        else:
                            files_to_upload = [(s3_key, file_bytes, extracted_filename, len(file_bytes), mime_type)]

                        try:
                            for up_key, up_content, up_name, up_size, up_mime in files_to_upload:
                                s3_client.put_object(
                                    Bucket=os.getenv('S3_BUCKET_NAME'),
                                    Key=up_key,
                                    Body=up_content,
                                    ContentType=up_mime
                                )
                                logger.debug(f'Successfully uploaded {up_name} to S3 at {up_key}')
                                new_file = File(
                                    name=up_name,
                                    patient_id=patient.id,
                                    file_type=up_mime,
                                    file_size=up_size,
                                    s3_key=up_key,
                                    category=category,
                                    subcategory=subcategory
                                )
                                db.session.add(new_file)
                                logger.debug(f'File {up_name} added to the database for patient {patient.id}')
                        except Exception as e:
                            logger.error(f'Failed to upload {extracted_filename} to S3: {str(e)}')
                            db.session.rollback()
                            raise Exception(f'S3 Upload failed for {extracted_filename}')

            # Commit the transaction after all files have been processed successfully
            db.session.commit()
            logger.debug(f'All extracted files from {zip_file.filename} processed and saved successfully.')

        except zipfile.BadZipFile:
            logger.error(f'Uploaded file {zip_file.filename} is not a valid zip file.')
            raise Exception(f'Uploaded file {zip_file.filename} is not a valid zip file.')

    else:
        logger.error(f"File {zip_file.filename} is not a zip file. All uploaded files must be zip archives.")
        raise Exception(f"File {zip_file.filename} is not a zip file. All uploaded files must be zip archives.")

@filemgmt.route('/upload_patient_billing/<int:patient_id>', methods=['POST'])
@login_required
def upload_patient_billing(patient_id):
    logger.debug(f"Accessing billing upload for patient ID: {patient_id}")

    try:
        # Get the patient
        patient = Patient.query.get_or_404(patient_id)

        # Ensure the user has permission to upload files for the patient
        if current_user.role != 'admin' and patient.dentist_id != current_user.id:
            logger.warning(f"User {current_user.email} does not have permission to upload billing files for patient {patient_id}")
            return jsonify({'success': False, 'message': 'Unauthorized access'}), 403

        # Get the uploaded zip file (expecting a file named 'billing_zip')
        billing_zip_file = request.files.get('billing_zip')

        # Ensure a zip file is uploaded and it's valid
        if not billing_zip_file or not billing_zip_file.filename.endswith('.zip'):
            logger.error("No valid zip file uploaded")
            return jsonify({'success': False, 'message': 'No valid zip file uploaded'}), 400

        logger.debug(f"Billing zip file received: {billing_zip_file.filename}")

        # Directly pass the uploaded zip file to the existing function
        # Assuming 'upload_and_save_files' can handle zip files directly
        upload_and_save_files(billing_zip_file, 'billing', 'billing', patient, 'billing')

        logger.info(f"Billing files uploaded successfully for patient {patient_id}")
        return jsonify({'success': True, 'message': 'Billing files uploaded successfully.'})

    except Exception as e:
        logger.error(f"Error uploading billing files for patient {patient_id}: {str(e)}")
        return jsonify({'success': False, 'message': f'Error uploading billing files: {str(e)}'}), 500


from botocore.exceptions import BotoCoreError, ClientError
import traceback
from flask import jsonify, send_file, redirect, url_for, flash
from flask_login import login_required, current_user
import logging
import zipfile
from io import BytesIO

logger = logging.getLogger(__name__)

@filemgmt.route('/patient/<int:patient_id>/download_all', methods=['GET'])
@login_required
def download_all_patient_files(patient_id):
    logger.debug(f"Preparing to download all files for patient ID: {patient_id}")
    patient = Patient.query.get_or_404(patient_id)

    if current_user.role != 'admin' and patient.dentist_id != current_user.id:
        return jsonify({'success': False, 'message': 'Unauthorized access'}), 403

    zip_buffer = BytesIO()
    presigned_urls = []

    try:
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_STORED) as zip_file:
            regular_files = File.query.filter_by(patient_id=patient.id).all()

            if not regular_files:
                logger.warning(f"No files found for patient ID: {patient_id}")
                return jsonify({'success': False, 'message': 'No files found for this patient.'}), 404

            for file in regular_files:
                try:
                    file_data = BytesIO()
                    s3_key_modified = file.s3_key.replace(" ", "_")
                    logger.debug(f"Modified S3 key for file {file.name}: {s3_key_modified}")

                    # Download the file from S3
                    s3_client.download_fileobj(os.getenv('S3_BUCKET_NAME'), s3_key_modified, file_data)
                    file_data.seek(0)

                    file_size_mb = len(file_data.getvalue()) / (1024 * 1024)
                    logger.debug(f"File size for {file.name}: {file_size_mb:.2f} MB")

                    if file_size_mb > 50:
                        logger.debug(f"Generating presigned URL for file {file.name} due to size")
                        presign_client = get_s3_client_for_presigning()
                        presigned_url = presign_client.generate_presigned_url(
                            'get_object',
                            Params={'Bucket': os.getenv('S3_BUCKET_NAME'), 'Key': s3_key_modified},
                            ExpiresIn=3600 * 24 * 7
                        )
                        presigned_urls.append({'name': file.name, 'url': presigned_url})
                    else:
                        zip_file.writestr(f"regular/{file.name}", file_data.read(), compress_type=zipfile.ZIP_DEFLATED)

                except (BotoCoreError, ClientError) as e:
                    logger.error(f"Boto3 error downloading file {file.name}: {str(e)}")
                    continue
                except Exception as e:
                    logger.error(f"General error downloading file {file.name}: {str(e)}")
                    logger.error(traceback.format_exc())
                    continue

        zip_buffer.seek(0)

        if presigned_urls:
            logger.debug("Creating presigned URL for the ZIP file with smaller files.")
            zip_file_name = f"patient_{patient_id}_files.zip"
            
            # Upload the ZIP to S3
            s3_client.put_object(
                Bucket=os.getenv('S3_BUCKET_NAME'),
                Key=f"temporary_zips/{zip_file_name}",
                Body=zip_buffer.getvalue()
            )

            presign_client = get_s3_client_for_presigning()
            zip_presigned_url = presign_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': os.getenv('S3_BUCKET_NAME'), 'Key': f"temporary_zips/{zip_file_name}"},
                ExpiresIn=3600 * 24 * 7
            )

            return jsonify({
                'success': True,
                'patient_id': patient_id,
                'zip_file_url': zip_presigned_url,
                'large_files': presigned_urls
            })

        logger.debug("Returning ZIP file for download.")
        return send_file(
            zip_buffer,
            as_attachment=True,
            download_name=f"patient_{patient_id}_files.zip",
            mimetype='application/zip'
        )

    except Exception as e:
        logger.error(f"Error creating ZIP file for patient ID {patient_id}: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'message': 'Error downloading files'}), 500

# Upload patient report
@filemgmt.route('/patient/<int:patient_id>/upload_report', methods=['POST'])
@login_required
def upload_patient_report(patient_id):
    """
    Upload multiple report files for a specific patient.
    """
    patient = Patient.query.get_or_404(patient_id)

    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized access'}), 403

    uploaded_files = request.files.getlist('report_files')
    if not uploaded_files:
        return jsonify({'success': False, 'message': 'No files provided'}), 400

    try:
        uploaded_file_details = []  # To collect details of all uploaded files

        for report_file in uploaded_files:
            if not report_file.filename:
                continue  # Skip empty files

            filename = secure_filename(report_file.filename)
            s3_key = f"patients/{patient_id}/reports/{filename}"

            # Get file size
            report_file.seek(0, os.SEEK_END)
            file_size = report_file.tell()
            report_file.seek(0)  # Reset pointer to the start

            # Upload the report to the S3 bucket
            s3_client.upload_fileobj(
                report_file,
                os.getenv('S3_BUCKET_NAME'),
                s3_key,
                ExtraArgs={'ContentType': report_file.mimetype}
            )
            logger.debug(f"Uploaded report {filename} to S3 with MIME type {report_file.mimetype}")

            # Save file information to the database
            new_file = AdminFile(
                name=filename,
                patient_id=patient.id,
                file_type=report_file.mimetype,  # Use MIME type for accuracy
                file_size=file_size,
                s3_key=s3_key,
                is_public=True  # All admin uploads should be visible to everyone
            )
            db.session.add(new_file)

            # Collect file details for the response
            uploaded_file_details.append({'name': filename, 'size': file_size, 'type': report_file.mimetype})

        db.session.commit()  # Commit to save all files in the database
        logger.debug(f"All files uploaded and saved to the database for patient {patient_id}")

        return jsonify({'success': True, 'message': 'Files uploaded successfully', 'files': uploaded_file_details})

    except Exception as e:
        # Rollback if there's an error
        db.session.rollback()
        logger.error(f"Error uploading reports: {str(e)}")
        return jsonify({'success': False, 'message': f"Error uploading reports: {str(e)}"}), 500

    patient = Patient.query.get_or_404(patient_id)

    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized access'}), 403

    report_file = request.files.get('report_file')
    if not report_file:
        return jsonify({'success': False, 'message': 'No file provided'}), 400

    try:
        filename = secure_filename(report_file.filename)
        s3_key = f"patients/{patient_id}/reports/{filename}"

        # Get file size
        report_file.seek(0, os.SEEK_END)
        file_size = report_file.tell()
        report_file.seek(0)  # Reset pointer to the start

        # Upload the report to the S3 bucket directly from the file-like object with content type
        s3_client.upload_fileobj(report_file, os.getenv('S3_BUCKET_NAME'), s3_key, ExtraArgs={'ContentType': report_file.mimetype})
        logger.debug(f"Uploaded report {filename} to S3 with MIME type {report_file.mimetype}")

        # Save file information to the database
        new_file = AdminFile(
            name=filename,
            patient_id=patient.id,
            file_type=report_file.mimetype,  # Use MIME type for more accuracy
            file_size=file_size,
            s3_key=s3_key,
            is_public=True  # All admin uploads should be visible to everyone
        )
        db.session.add(new_file)
        db.session.commit()  # Commit to save in the database

        logger.debug(f"Report {filename} saved to the database.")
        return jsonify({'success': True, 'message': 'Report uploaded successfully'})

    except Exception as e:
        # Rollback if there's an error
        db.session.rollback()
        logger.error(f"Error uploading report: {str(e)}")
        return jsonify({'success': False, 'message': f"Error uploading report: {str(e)}"}), 500

    patient = Patient.query.get_or_404(patient_id)

    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized access'}), 403

    report_file = request.files.get('report_file')
    if not report_file:
        return jsonify({'success': False, 'message': 'No file provided'}), 400

    try:
        filename = secure_filename(report_file.filename)
        s3_key = f"patients/{patient_id}/reports/{filename}"

        # Get file size without reading the entire file into memory
        file_size = report_file.content_length

        # Upload the report to the S3 bucket directly from the file-like object
        s3_client.upload_fileobj(report_file, os.getenv('S3_BUCKET_NAME'), s3_key)
        logger.debug(f"Uploaded report {filename} to S3")

        # Save file information to the database
        new_file = AdminFile(
            name=filename,
            patient_id=patient.id,
            file_type=report_file.content_type,  # Save the file's MIME type
            file_size=file_size,  # Use the size from content_length
            s3_key=s3_key,  # Store the S3 key
            is_public=True  # All admin uploads should be visible to everyone
        )
        db.session.add(new_file)
        db.session.commit()  # Commit to save in the database

        logger.debug(f"Report {filename} saved to the database.")
        return jsonify({'success': True, 'message': 'Report uploaded successfully'})

    except Exception as e:
        # Rollback if there's an error
        db.session.rollback()
        logger.error(f"Error uploading report: {str(e)}")
        return jsonify({'success': False, 'message': f"Error uploading report: {str(e)}"}), 500

@filemgmt.route('/download_file/<int:file_id>', methods=['GET'])
@login_required
def download_file(file_id):
    logger.debug(f"Received request to download file with ID: {file_id}")
    file = File.query.get(file_id)

    if not file:
        logger.debug(f"File with ID {file_id} not found in the 'File' table, checking 'AdminFile' table.")
        # Optionally, check in another table like 'AdminFile'
        file = AdminFile.query.get(file_id)
        if not file:
            logger.error(f"File with ID {file_id} not found in the 'AdminFile' table either.")
            return jsonify({'success': False, 'message': 'File not found'}), 404
        else:
            logger.debug(f"File with ID {file_id} found in the 'AdminFile' table.")

    try:
        logger.debug(f"Processing file: {file.name}, S3 Key: {file.s3_key}")

        # Check if the current user is an admin
        is_admin = current_user.role == 'admin'
        logger.debug(f"Current user role: {'admin' if is_admin else 'non-admin'}")

        # Get the patient associated with the file
        patient = Patient.query.get(file.patient_id)
        if not patient:
            logger.error(f"Patient with ID {file.patient_id} not found.")
            return jsonify({'success': False, 'message': 'Associated patient not found'}), 404

        dentist = Dentist.query.get(patient.dentist_id)
        if not dentist:
            logger.error(f"Dentist with ID {patient.dentist_id} not found.")
            return jsonify({'success': False, 'message': 'Associated dentist not found'}), 404

        logger.debug(f"Patient: {patient.name}, Dentist: {dentist.name}, Dentist's DSO: {dentist.DSO}")

        # Ensure the user has permission to access this patient's files
        if not current_user.can_access_patient(patient):
            logger.warning(f"Unauthorized access attempt by user {current_user.email} for file {file_id}")
            return jsonify({'success': False, 'message': 'Unauthorized access'}), 403

        # Set content type with a fallback
        content_type = file.file_type or 'application/octet-stream'
        logger.debug(f"Determined content type: {content_type}")
        s3_key_modified = file.s3_key.replace(" ", "_")


        # Ensure correct MIME type for PDFs if needed
        if file.file_type == 'application/pdf' and 'pdf' not in content_type:
            content_type = 'application/pdf'
            logger.debug("Adjusted content type to 'application/pdf' for PDF file.")
       

        # Generate a pre-signed URL with the correct Content-Type and inline Content-Disposition
        presign_client = get_s3_client_for_presigning()
        presigned_url = presign_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': os.getenv('S3_BUCKET_NAME'),
                'Key': s3_key_modified,
                'ResponseContentDisposition': 'inline',
                'ResponseContentType': content_type
            },
            ExpiresIn=3600 * 24 * 7 # URL expires in 1 week
        )
        
        logger.debug(f"Generated pre-signed URL for file {file.name}: {presigned_url}")
        return redirect(presigned_url)
    except Exception as e:
        logger.error(f"Error generating pre-signed URL for file {file.name}: {str(e)}")
        return jsonify({'success': False, 'message': 'Error generating file URL'}), 500

@filemgmt.route('/file/<int:file_id>/delete', methods=['POST'])
@login_required
def delete_file(file_id):
    try:
        # Attempt to find the file in the 'File' table
        file = File.query.get(file_id)
        file_table = 'File'

        # If not found in 'File', check in 'AdminFile'
        if not file:
            file = AdminFile.query.get_or_404(file_id)
            file_table = 'AdminFile'

        # Fetch associated patient and dentist
        patient = Patient.query.get_or_404(file.patient_id)
        dentist = Dentist.query.get_or_404(patient.dentist_id)  # Get the dentist associated with the patient

        # Updated permission check using the proper access control method
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'message': 'Permission denied'}), 403

        # Remove file from S3
        s3 = boto3.client('s3')
        try:
            s3.delete_object(Bucket=os.getenv('S3_BUCKET_NAME'), Key=file.s3_key)
            logger.info(f"File '{file.s3_key}' successfully deleted from S3.")
        except s3.exceptions.NoSuchKey:
            logger.warning(f"File '{file.s3_key}' not found on S3. Proceeding with database deletion.")
        except Exception as s3_error:
            logger.error(f"Error deleting file '{file.s3_key}' from S3: {str(s3_error)}")

        # Remove file from the appropriate table
        db.session.delete(file)
        db.session.commit()
        logger.info(f"File ID {file_id} successfully deleted from {file_table} table.")

        return jsonify({'success': True, 'message': 'File deleted successfully'})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting file ID {file_id}: {str(e)}")
        return jsonify({'success': False, 'message': 'An error occurred'}), 500


@filemgmt.route('/api/cbct_folder/delete', methods=['POST'])
@login_required
def delete_cbct_folder():
    """
    Delete a CBCT folder and all S3 files within it.
    Expects JSON payload with patient_id and folder_name.
    """
    try:
        data = request.get_json()
        patient_id = data.get('patient_id')
        folder_name = data.get('folder_name')
        
        if not patient_id or not folder_name:
            return jsonify({
                'success': False, 
                'message': 'Both patient_id and folder_name are required'
            }), 400
        
        # Fetch patient and verify access
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({'success': False, 'message': 'Patient not found'}), 404
        
        # Check user permission
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'message': 'Permission denied'}), 403
        
        # Build the S3 prefix for this CBCT folder
        prefix = f"patients/{patient_id}/imaging/cbct/{folder_name}/"
        logger.info(f"Deleting CBCT folder with prefix: {prefix}")
        
        # Initialize S3 client
        s3_client = boto3.client('s3')
        bucket_name = os.getenv('S3_BUCKET_NAME')
        
        # List all objects under the prefix
        paginator = s3_client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix)
        
        # Collect all objects to delete
        objects_to_delete = []
        for page in pages:
            if 'Contents' in page:
                for obj in page['Contents']:
                    objects_to_delete.append({'Key': obj['Key']})
        
        if not objects_to_delete:
            return jsonify({
                'success': False, 
                'message': f'No files found in CBCT folder "{folder_name}"'
            }), 404
        
        # Delete objects in batches (S3 allows max 1000 objects per delete request)
        deleted_count = 0
        batch_size = 1000
        
        for i in range(0, len(objects_to_delete), batch_size):
            batch = objects_to_delete[i:i + batch_size]
            try:
                response = s3_client.delete_objects(
                    Bucket=bucket_name,
                    Delete={'Objects': batch, 'Quiet': True}
                )
                
                # Check for errors in the response
                if 'Errors' in response and response['Errors']:
                    for error in response['Errors']:
                        logger.error(f"Error deleting S3 object {error['Key']}: {error['Message']}")
                else:
                    deleted_count += len(batch)
                    
            except ClientError as e:
                logger.error(f"S3 ClientError during batch delete: {str(e)}")
                return jsonify({
                    'success': False, 
                    'message': f'Error deleting files from S3: {str(e)}'
                }), 500
        
        logger.info(f"Successfully deleted {deleted_count} files from CBCT folder '{folder_name}' for patient {patient_id}")
        
        return jsonify({
            'success': True, 
            'message': f'CBCT folder "{folder_name}" deleted successfully ({deleted_count} files removed)',
            'deleted_count': deleted_count
        })
        
    except Exception as e:
        logger.error(f"Error deleting CBCT folder: {str(e)}")
        return jsonify({
            'success': False, 
            'message': f'An error occurred while deleting the CBCT folder: {str(e)}'
        }), 500


@filemgmt.route('/confirm_upload', methods=['POST'])
@login_required
def confirm_upload():
    """
    Confirms that a file has been uploaded via a presigned URL.
    """
    try:
        data = request.json
        patient_id = data['patient_id']
        s3_key = data['s3_key']

        # Update the database or perform any necessary actions to confirm the upload
        logger.info(f"Upload confirmed for file: {s3_key}")
        return jsonify({'success': True, 'message': 'Upload confirmed.'})

    except Exception as e:
        logger.error(f"Error confirming upload: {e}")
        return jsonify({'success': False, 'message': f"Error confirming upload: {str(e)}"}), 500
        
@filemgmt.route('/generate_client_presigned_url', methods=['POST'])
@login_required
def generate_client_presigned_url():
    """
    Generates a presigned URL for client-side uploads to S3.
    """
    try:
        logger.debug(f"Starting generate_client_presigned_url with headers: {dict(request.headers)}")
        data = request.get_json()
        logger.debug(f"Request data: {data}")
        filename = data.get('filename')  # The full relative path provided by the client
        section = data.get('section')
        patient_id = data.get('patient_id')
        category = data.get('category')  # Get the category from the request

        if not filename or not section or not patient_id or not category:
            logger.error("Missing required parameters for presigned URL generation.")
            return jsonify({'success': False, 'message': 'Missing required parameters'}), 400

        def secure_filename_custom(filename, patient_id=None):
            """
            A custom version of secure_filename that:
            - Allows Hebrew letters (א-ת).
            - Allows typical ASCII letters, digits, underscore, hyphen, and dot.
            - Splits out the extension, so you never lose ".pdf", ".txt", etc.
            - Falls back to "untitled_<patient_id>" if the base name becomes empty.
            """
            # Split the base name and extension
            base, ext = os.path.splitext(filename)  # e.g. "ד״ר" and ".pdf"
            
            # Replace anything not in [A-Za-z0-9, underscore, hyphen, dot, Hebrew range, etc.] with '_'
            # Note: We allow typical Hebrew punctuation here: ״ (U+05F4), ׳ (U+05F3), ־ (U+05BE) 
            # If you need more, add them to the regex.
            pattern = r'[^\w\-.א-ת״׳־]'
            
            sanitized_base = re.sub(pattern, '_', base)
            
            # If everything got replaced or removed, fall back
            if not sanitized_base or all(ch == '_' for ch in sanitized_base):
                if patient_id:
                    sanitized_base = f"untitled_{patient_id}"
                else:
                    sanitized_base = "untitled"

            # Reattach the extension
            sanitized_filename = sanitized_base + ext
            
            return sanitized_filename

        def sanitize_relative_path(relative_path, patient_id=None):
            """
            Safely sanitize the relative path while preserving the directory structure.
            """
            parts = relative_path.split('/')  # Split into directories and filename
            
            sanitized_parts = []
            for part in parts:
                sanitized_part = secure_filename_custom(part)  # your custom logic
                if not sanitized_part and patient_id:
                    # If the filename is empty *after* sanitization (e.g. all Hebrew stripped out)
                    # then use a fallback name that includes the patient ID
                    sanitized_part = f"untitled_{patient_id}"
                sanitized_parts.append(sanitized_part)
            
            sanitized_path = '/'.join(sanitized_parts)
            return sanitized_path

        # Sanitize the provided filename while preserving directory structure
        sanitized_filename = sanitize_relative_path(filename, patient_id)
        logger.debug(f"Original filename: {filename}")
        logger.debug(f"Sanitized filename with preserved structure: {sanitized_filename}")

        # Construct the S3 key
        s3_key = f"patients/{patient_id}/{category}/{section}/{sanitized_filename}"
        logger.debug(f"Generating presigned URL for client-side upload of file: {s3_key}")

        # Generate presigned POST URL
        presigned_url = s3_client.generate_presigned_post(
            Bucket=os.getenv('S3_BUCKET_NAME'),
            Key=s3_key,
            Fields={"acl": "private"},
            Conditions=[
                {"acl": "private"},
                ["content-length-range", 0, 1073741824]  # Limit to 1GB
            ],
            ExpiresIn=3600 * 24 * 7 # URL valid for 1 week 
        )

        logger.debug(f"Generated presigned URL: {presigned_url['url']}")
        logger.debug(f"Fields for presigned request: {presigned_url['fields']}")
        
        return jsonify({
            'success': True,
            'url': presigned_url['url'],
            'fields': presigned_url['fields'],
            's3_key': s3_key,
            'original_filename': filename,  # Include original filename for reference
            'sanitized_filename': sanitized_filename  # Include sanitized filename for reference
        })

    except Exception as e:
        logger.error(f"Error generating client presigned URL: {e}")
        logger.exception("Detailed error trace:")
        return jsonify({'success': False, 'message': f"Error generating presigned URL: {str(e)}"}), 500

@filemgmt.route('/download_files', methods=['POST'])
@login_required
def download_files():
    """
    Handles both files & folders for download:
      - If 1 item and it's a single file, download file directly.
      - Otherwise, zip everything (multiple items OR single folder).
    Expected request JSON structure:
    {
      "items": [
        {
          "type": "file",
          "fileId": 123,
          "patientId": "10296",
          "category": "intraoral_scan"
        },
        {
          "type": "folder",
          "folderName": "Farnsworth",
          "patientId": "10296",
          "category": "cbct"
        },
        ...
      ]
    }
    """
    logger.debug("Received request to download multiple files/folders.")

    try:
        data = request.get_json()
        if not data:
            logger.error("No JSON body provided.")
            return jsonify({'success': False, 'message': 'No input data'}), 400

        items = data.get('items', [])
        logger.debug(f"Received items for download: {items}")

        if not items:
            logger.error("No items provided for download.")
            return jsonify({'success': False, 'message': 'No items selected'}), 400

        # ========== Step 1: Expand all items into a list of (filename, s3_key) ==========
        # We'll gather them in a list, because even a single folder might contain multiple S3 objects.
        s3_objects = []  # will hold tuples of (filename, s3_key)

        for item in items:
            item_type = item.get('type')
            if item_type == 'file':
                # Lookup this file in the DB (File or AdminFile)
                file_id = item.get('fileId')
                if not file_id:
                    logger.warning("Missing fileId for a 'file' type item.")
                    continue

                # Search in both File and AdminFile
                db_file = File.query.get(file_id)
                if not db_file:
                    db_file = AdminFile.query.get(file_id)

                if not db_file:
                    logger.warning(f"File with ID {file_id} not found in DB.")
                    continue

                # We have a valid file from DB
                s3_objects.append((db_file.name, db_file.s3_key))

            elif item_type == 'folder':
                # We'll assume 'folderName' + a known S3 prefix, e.g. patients/<pid>/imaging/cbct/<folderName>/
                folder_name = item.get('folderName')
                if not folder_name:
                    logger.warning("Missing folderName for a 'folder' type item.")
                    continue

                # Retrieve 'patientId' from the item
                patient_id = item.get('patientId')
                if not patient_id:
                    logger.error("No patientId provided for folder item.")
                    return jsonify({'success': False, 'message': 'patientId is required for folder items.'}), 400

                # Check for pre-zipped file first (for faster downloads of large CBCT folders)
                try:
                    from flask_app.utils.cbct_prezip_manager import get_prezip_url
                    logger.info(f"Checking for pre-zip: patient_id={patient_id}, folder_name={folder_name}")
                    prezip_url = get_prezip_url(patient_id, folder_name)
                    
                    if prezip_url:
                        # Pre-zipped file exists! Return JSON with redirect URL
                        # (fetch doesn't handle HTTP redirects well for file downloads)
                        logger.info(f"Using pre-zipped file for folder {folder_name}, returning redirect URL")
                        return jsonify({
                            'success': True,
                            'redirect_url': prezip_url,
                            'filename': f"{folder_name}.zip"
                        })
                    else:
                        logger.info(f"No pre-zip found for folder {folder_name}, will create on-the-fly")
                            
                except Exception as prezip_check_error:
                    logger.warning(f"Error checking for pre-zip, continuing with on-the-fly zip: {prezip_check_error}")

                # Fall back to on-the-fly zip if no pre-zipped file available
                # Build the prefix
                prefix = f"patients/{patient_id}/imaging/cbct/{folder_name}/"
                logger.debug(f"Listing objects in folder prefix: {prefix}")

                # List all objects under that prefix
                bucket_name = os.getenv('S3_BUCKET_NAME')
                paginator = s3_client.get_paginator('list_objects_v2')
                pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix)

                for page in pages:
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            # Example: key = "patients/10296/imaging/cbct/Farnsworth/file1.dcm"
                            key = obj['Key']
                            # We can produce a local filename e.g. "Farnsworth/file1.dcm"
                            # by removing the "patients/10296/imaging/cbct/" portion
                            local_name = key.replace(f"patients/{patient_id}/imaging/cbct/", "")
                            s3_objects.append((local_name, key))

            else:
                logger.warning(f"Unknown item type: {item_type}")

        # ========== Step 2: Decide single-file vs. zip logic ==========

        # If there's exactly 1 object total, and we have no 'folder' items, we can do direct file download.
        # BUT since you asked "if the selected item is a folder, we always zip," let's check that logic.
        single_requested_item = (len(items) == 1)  # only 1 item in "items"
        single_s3_object = (len(s3_objects) == 1) # after expansions, we have 1 S3 object

        # Condition: If 1 item AND it's type == "file", do single file direct download
        # Otherwise, zip (multiple items or a folder).
        if single_requested_item and items[0].get('type') == 'file' and single_s3_object:
            # Direct download approach
            filename, s3_key = s3_objects[0]
            logger.debug(f"Direct download for single file: {filename}, Key: {s3_key}")
            try:
                s3_response = s3_client.get_object(
                    Bucket=os.getenv('S3_BUCKET_NAME'),
                    Key=s3_key
                )
                file_content = s3_response['Body'].read()

                content_type = s3_response.get('ContentType') or 'application/octet-stream'
                response = Response(file_content, mimetype=content_type)
                # Properly encode filename for Content-Disposition header to handle non-ASCII characters
                import urllib.parse
                # Check if filename contains non-ASCII characters
                try:
                    filename.encode('ascii')
                    # ASCII-only filename - use simple format
                    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
                except UnicodeEncodeError:
                    # Non-ASCII characters - use RFC 5987 format
                    encoded_filename = urllib.parse.quote(filename, safe='')
                    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"; filename*=UTF-8\'\'{encoded_filename}'
                return response

            except Exception as e:
                logger.error(f"Error fetching single file from S3: {e}")
                return jsonify({'success': False, 'message': 'Error fetching file'}), 500

        # ========== Step 3: Otherwise, create a ZIP of all s3_objects ==========
        if not s3_objects:
            logger.debug("No valid S3 objects to download.")
            return jsonify({'success': False, 'message': 'No S3 objects found'}), 404

        zip_filename = f"files_{int(time.time())}.zip"
        # Create disk-backed temporary ZIP to avoid memory spikes
        temp_zip = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
        temp_zip_path = temp_zip.name
        temp_zip.close()

        try:
            with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for (local_filename, s3_key) in s3_objects:
                    try:
                        s3_response = s3_client.get_object(
                            Bucket=os.getenv('S3_BUCKET_NAME'),
                            Key=s3_key
                        )
                        body = s3_response['Body']
                        # Stream into the zip entry to avoid buffering whole file
                        with zf.open(local_filename, 'w') as dest:
                            # Use 8MB chunks
                            while True:
                                chunk = body.read(8 * 1024 * 1024)
                                if not chunk:
                                    break
                                dest.write(chunk)
                    except Exception as e:
                        logger.error(f"Error fetching file {s3_key}: {str(e)}")

            logger.debug(f"Generated ZIP file {zip_filename} at {temp_zip_path} containing {len(s3_objects)} objects.")

            @after_this_request
            def cleanup_temp_zip(response):
                try:
                    os.remove(temp_zip_path)
                except Exception as cleanup_err:
                    logger.warning(f"Failed to remove temp zip {temp_zip_path}: {cleanup_err}")
                return response

            return send_file(
                temp_zip_path,
                mimetype='application/zip',
                as_attachment=True,
                download_name=zip_filename
            )
        except Exception as e:
            # Ensure cleanup on error
            try:
                os.remove(temp_zip_path)
            except Exception:
                pass
            raise

    except Exception as e:
        logger.error(f"Error in download_files: {str(e)}")
        return jsonify({'success': False, 'message': 'Error downloading files'}), 500





@filemgmt.route('/api/share_patient_items', methods=['POST'])
@login_required
def api_share_patient_items():
    """Create shareable links for selected files and/or CBCT folders and email lines.
    Does not send emails; returns lines for the caller to use.
    """
    try:
        data = request.get_json() or {}
        patient_id = data.get('patient_id')
        recipients = data.get('recipients') or []
        items = data.get('items') or []
        custom_message = data.get('custom_message', '').strip()
        if not patient_id or not items:
            return jsonify({'success': False, 'error': 'patient_id and items are required'}), 400

        patient = Patient.query.get_or_404(patient_id)
        
        # Get dentist and clinic information
        dentist = None
        clinic = None
        if patient.dentist_id:
            dentist = Dentist.query.get(patient.dentist_id)
            if dentist:
                # Get clinic - priority: patient.clinic_id > dentist primary clinic
                if patient.clinic_id:
                    clinic = Clinic.query.get(patient.clinic_id)
                elif hasattr(dentist, 'get_primary_clinic'):
                    clinic = dentist.get_primary_clinic()
                else:
                    # Fallback: get first clinic associated with dentist
                    dentist_clinics = dentist.clinics.all() if hasattr(dentist, 'clinics') else []
                    if dentist_clinics:
                        clinic = dentist_clinics[0]
        lines = []
        bucket = os.getenv('S3_BUCKET_NAME')

        # Expand items to concrete S3 keys and detect CBCT folders
        objects_to_share = []  # (label, local_name, s3_key, file_id, source)
        has_cbct_folder = False
        for item in items:
            if item.get('type') == 'file':
                file_id = item.get('fileId')
                db_file = File.query.get(file_id)
                source = 'files'
                if not db_file:
                    db_file = AdminFile.query.get(file_id)
                    source = 'adminfiles'
                if not db_file:
                    continue
                label = f"{db_file.name} [{getattr(db_file, 'category', None) or getattr(db_file, 'file_category', '')} / {getattr(db_file, 'subcategory', '')}]"
                objects_to_share.append((label, db_file.name, db_file.s3_key, file_id, source))
            elif item.get('type') == 'folder' and (item.get('category') or '').lower() == 'cbct':
                has_cbct_folder = True
                folder = item.get('folderName')
                
                # Check for pre-zipped file first
                from flask_app.utils.cbct_prezip_manager import get_prezip_url
                prezip_url = get_prezip_url(patient_id, folder, expires_in=3600 * 24 * 7)  # 7 days
                
                if prezip_url:
                    # Use pre-zipped file - add as special marker
                    label = f"CBCT Scan {folder} [imaging / cbct]"
                    objects_to_share.append((label, f"{folder}.zip", None, None, 'prezip', prezip_url))
                else:
                    # No pre-zip available, list individual files
                    prefix = f"patients/{patient_id}/imaging/cbct/{folder}/"
                    paginator = s3_client.get_paginator('list_objects_v2')
                    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                        for obj in page.get('Contents', []):
                            key = obj['Key']
                            local_name = key.replace(f"patients/{patient_id}/imaging/cbct/", "")
                            label = f"CBCT Scan {folder} [imaging / cbct]"
                            objects_to_share.append((label, local_name, key))

        total_files = len(objects_to_share)
        
        # Check if we have pre-zipped CBCT folders (they have 6 elements with 'prezip' source)
        prezip_items = [obj for obj in objects_to_share if len(obj) >= 6 and obj[4] == 'prezip']
        regular_items = [obj for obj in objects_to_share if len(obj) < 6 or obj[4] != 'prezip']
        
        should_zip = (has_cbct_folder and not prezip_items) or len(regular_items) > 3

        presigned_zip_url = None
        short_zip_url = None

        # Handle pre-zipped CBCT folders - just use the presigned URL directly
        for prezip_obj in prezip_items:
            label, _local, _key, _fid, _source, prezip_url = prezip_obj
            try:
                short_url = shorten_url_with_tinyurl(prezip_url) or prezip_url
            except Exception:
                short_url = prezip_url
            # Extract filename from label (remove category brackets)
            file_name = label.split(' [')[0] if ' [' in label else label
            lines.append(f"{file_name}: {short_url}")
            logger.info(f"Using pre-zipped file for sharing: {file_name}")

        if should_zip and regular_items:
            # Build disk-backed ZIP synchronously for non-prezip items
            temp_zip = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
            temp_zip_path = temp_zip.name
            temp_zip.close()
            zip_name = f"share_{patient_id}_{int(time.time())}.zip"
            try:
                with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for obj in regular_items:
                        # Handle both 3-tuple (CBCT folders) and 5-tuple (files) formats
                        if len(obj) >= 3:
                            _label, local_name, s3_key = obj[0], obj[1], obj[2]
                        else:
                            continue
                        if not s3_key:
                            continue
                        try:
                            s3_resp = s3_client.get_object(Bucket=bucket, Key=s3_key)
                            with zf.open(local_name, 'w') as dest:
                                while True:
                                    chunk = s3_resp['Body'].read(8 * 1024 * 1024)
                                    if not chunk:
                                        break
                                    dest.write(chunk)
                        except Exception as e:
                            logger.error(f"Error adding to zip {s3_key}: {e}")

                # Upload to S3 and shorten
                tmp_key = f"temporary_zips/{zip_name}"
                s3_client.upload_file(temp_zip_path, bucket, tmp_key)
                presign_client = get_s3_client_for_presigning()
                presigned_zip_url = presign_client.generate_presigned_url(
                    'get_object', Params={'Bucket': bucket, 'Key': tmp_key}, ExpiresIn=3600 * 24 * 7
                )
                try:
                    short_zip_url = shorten_url_with_tinyurl(presigned_zip_url) or presigned_zip_url
                except Exception:
                    short_zip_url = presigned_zip_url
            finally:
                try:
                    os.remove(temp_zip_path)
                except Exception:
                    pass

            # Build individual file links (for downloading separately) AND ZIP option
            seen_labels = set()
            file_count = 0
            presign_client = get_s3_client_for_presigning()
            for obj in regular_items:
                label = obj[0]
                if label in seen_labels:
                    continue
                seen_labels.add(label)
                file_count += 1
                # Extract filename from label (remove category brackets)
                file_name = label.split(' [')[0] if ' [' in label else label
                
                # Generate individual presigned URL for this file
                if len(obj) >= 5:
                    # File format: (label, file_name, s3_key, file_id, source)
                    s3_key = obj[2]
                elif len(obj) >= 3:
                    # CBCT format: (label, local_name, s3_key)
                    s3_key = obj[2]
                else:
                    continue
                
                if s3_key:
                    try:
                        url = presign_client.generate_presigned_url(
                            'get_object', Params={'Bucket': bucket, 'Key': s3_key}, ExpiresIn=3600 * 24 * 7
                        )
                        individual_url = shorten_url_with_tinyurl(url) or url
                        lines.append(f"{file_name}: {individual_url}")
                    except Exception as e:
                        logger.error(f"Error generating individual link for {s3_key}: {e}")
                        # Fallback to ZIP if individual link fails
                        lines.append(f"{file_name}: {short_zip_url}")
            
            # Add combined ZIP option at the end
            if file_count > 0:
                lines.append("")
                lines.append(f"Combined ZIP ({file_count} files): {short_zip_url}")
        elif regular_items:
            # <= 3 non-CBCT files: individual links - generate viewer links instead of direct S3 links
            logger.info(f"Non-ZIP path: generating individual links for {len(regular_items)} objects")
            presign_client = get_s3_client_for_presigning()
            for obj in regular_items:
                label, _local, s3_key, file_id, source = obj if len(obj) >= 5 else (obj[0], obj[1], obj[2], None, 'files')
                logger.info(f"Processing object: label={label}, file_id={file_id}, source={source}, s3_key={s3_key}")
                try:
                    url = presign_client.generate_presigned_url(
                        'get_object', Params={'Bucket': bucket, 'Key': s3_key}, ExpiresIn=3600 * 24 * 7
                    )
                    short_url = shorten_url_with_tinyurl(url) or url
                    
                    # Extract filename from label (remove category brackets)
                    file_name = label.split(' [')[0] if ' [' in label else label
                    lines.append(f"{file_name}: {short_url}")
                    logger.info(f"Successfully generated link for {file_name}: {short_url[:50]}...")
                except Exception as e:
                    logger.error(f"Error generating per-file link for {s3_key}: {e}", exc_info=True)
            
            logger.info(f"Finished generating links. Total lines generated: {len(lines)}")

        # Optionally send emails if recipients were provided by the caller
        emails_sent = []
        emails_failed = []
        if recipients and lines:  # Only send if we have links
            try:
                # Get patient initials and ID for privacy
                def get_patient_initials(patient):
                    """Extract initials from patient name."""
                    if not patient.name:
                        return "N/A"
                    name_parts = patient.name.strip().split()
                    if len(name_parts) >= 2:
                        # First letter of first name + first letter of last name
                        return f"{name_parts[0][0].upper()}{name_parts[-1][0].upper()}"
                    elif len(name_parts) == 1:
                        # Only one name part, use first two letters
                        return name_parts[0][:2].upper() if len(name_parts[0]) >= 2 else name_parts[0][0].upper()
                    return "N/A"
                
                patient_initials = get_patient_initials(patient)
                patient_identifier = f"{patient_initials} (ID: {patient.id})"
                
                subject = f"Files shared for {patient_identifier}"

                # Build an email body with custom message, dentist/clinic info, and file links
                email_body_parts = []
                
                # Replace patient name with patient identifier in custom_message if present
                if custom_message and patient.name:
                    # Replace full patient name with patient identifier
                    custom_message = custom_message.replace(patient.name, patient_identifier)
                    # Also replace Jinja2 template variable if somehow present
                    custom_message = custom_message.replace('{{ patient.name }}', patient_identifier).replace('{{patient.name}}', patient_identifier)
                
                # Check if custom_message already contains Contact Information and Shared items
                custom_has_contact_info = custom_message and "Contact Information:" in custom_message
                custom_has_shared_items = custom_message and "Shared items:" in custom_message
                
                if custom_message and custom_has_contact_info and custom_has_shared_items:
                    # Custom message already has the structure - replace placeholders with actual links
                    message_lines = custom_message.split('\n')
                    final_message_lines = []
                    in_shared_items = False
                    link_index = 0
                    
                    for line in message_lines:
                        if "Shared items:" in line:
                            in_shared_items = True
                            final_message_lines.append(line)
                        elif in_shared_items and "[Link will appear here]" in line:
                            # Replace placeholder with actual link
                            if link_index < len(lines):
                                final_message_lines.append(lines[link_index])
                                link_index += 1
                            # Skip the placeholder line
                        elif in_shared_items and ':' in line and 'http' not in line.lower() and '[Link will appear here]' in line:
                            # File name line with placeholder - replace with actual link
                            if link_index < len(lines):
                                final_message_lines.append(lines[link_index])
                                link_index += 1
                            # Skip the placeholder line
                        else:
                            final_message_lines.append(line)
                    
                    # Add any remaining links
                    while link_index < len(lines):
                        final_message_lines.append(lines[link_index])
                        link_index += 1
                    
                    email_body_parts.extend(final_message_lines)
                else:
                    # Build message from scratch
                    if custom_message:
                        email_body_parts.append(custom_message)
                        email_body_parts.append("")
                    
                    # Add dentist and clinic contact information
                    contact_info_parts = []
                    if dentist:
                        contact_info_parts.append(f"Dr. {dentist.name}")
                        if dentist.email:
                            contact_info_parts.append(f"Email: {dentist.email}")
                    
                    if clinic:
                        if clinic.name:
                            contact_info_parts.append(f"Clinic: {clinic.name}")
                        if clinic.telephone:
                            contact_info_parts.append(f"Phone: {clinic.telephone}")
                        if clinic.email:
                            contact_info_parts.append(f"Clinic Email: {clinic.email}")
                    
                    if contact_info_parts:
                        email_body_parts.append("Contact Information:")
                        email_body_parts.extend(contact_info_parts)
                        email_body_parts.append("")
                    
                    # Add file links
                    if lines:
                        email_body_parts.append("Shared items:")
                        email_body_parts.extend(lines)
                
                email_body_text = "\n".join(email_body_parts) if email_body_parts else f"{patient_identifier} – Files shared"
                
                # Build HTML email with logo using helper function
                email_body_html = build_email_with_logo(
                    email_body_text,
                    title="Files Shared",
                    subtitle=f"Files have been shared for <strong>{patient_identifier}</strong>"
                )

                for recipient in recipients:
                    try:
                        sent = send_email_with_sendgrid(
                            recipient,
                            subject,
                            email_body_html,
                            email_body_text,
                            patient_id=patient_id,
                            email_type='notification'
                        )
                        if sent:
                            emails_sent.append(recipient)
                        else:
                            emails_failed.append(recipient)
                    except Exception as send_err:
                        logger.error(f"Error sending share email to {recipient}: {send_err}")
                        emails_failed.append(recipient)
            except Exception as e2:
                logger.error(f"Unexpected error preparing share emails: {e2}")
        
        # Return normally with results
        return jsonify({
            'success': True,
            'processing': False,
            'lines': lines,
            'zip_url': short_zip_url or None,
            'emails_sent': emails_sent,
            'emails_failed': emails_failed
        })
    except Exception as e:
        logger.error(f"Error in api_share_patient_items: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@filemgmt.route('/api/presign-status', methods=['GET'])
@login_required
def presign_status():
    """Diagnostic: verify S3 presign IAM user is configured (for 7-day share links).
    Hit this on live server to confirm deployment has env vars."""
    ak = os.environ.get('S3_PRESIGN_ACCESS_KEY_ID')
    sk = os.environ.get('S3_PRESIGN_SECRET_ACCESS_KEY')
    env_path = Path(__file__).resolve().parents[2] / "env" / "app.env"
    return jsonify({
        'presign_configured': bool(ak and sk),
        'access_key_set': bool(ak),
        'secret_key_set': bool(sk),
        'env_file_exists': env_path.exists(),
        'message': 'Using IAM user for 7-day links' if (ak and sk) else 'Using instance role (links may expire in ~6h)'
    })


@filemgmt.route('/api/patient/<int:patient_id>/share-info', methods=['GET'])
@login_required
def get_patient_share_info(patient_id):
    """Get dentist and clinic information for share email preview."""
    try:
        patient = Patient.query.get_or_404(patient_id)
        
        # Helper function to get patient initials
        def get_patient_initials(patient):
            """Extract initials from patient name."""
            if not patient.name:
                return "N/A"
            name_parts = patient.name.strip().split()
            if len(name_parts) >= 2:
                # First letter of first name + first letter of last name
                return f"{name_parts[0][0].upper()}{name_parts[-1][0].upper()}"
            elif len(name_parts) == 1:
                # Only one name part, use first two letters
                return name_parts[0][:2].upper() if len(name_parts[0]) >= 2 else name_parts[0][0].upper()
            return "N/A"
        
        patient_initials = get_patient_initials(patient)
        patient_identifier = f"{patient_initials} (ID: {patient.id})"
        
        # Get dentist and clinic information
        dentist = None
        clinic = None
        if patient.dentist_id:
            dentist = Dentist.query.get(patient.dentist_id)
            if dentist:
                # Get clinic - priority: patient.clinic_id > dentist primary clinic
                if patient.clinic_id:
                    clinic = Clinic.query.get(patient.clinic_id)
                elif hasattr(dentist, 'get_primary_clinic'):
                    clinic = dentist.get_primary_clinic()
                else:
                    # Fallback: get first clinic associated with dentist
                    dentist_clinics = dentist.clinics.all() if hasattr(dentist, 'clinics') else []
                    if dentist_clinics:
                        clinic = dentist_clinics[0]
        
        return jsonify({
            'success': True,
            'patient_identifier': patient_identifier,
            'dentist': {
                'name': dentist.name if dentist else None,
                'email': dentist.email if dentist else None
            },
            'clinic': {
                'name': clinic.name if clinic else None,
                'phone': clinic.telephone if clinic else None,
                'email': clinic.email if clinic else None
            }
        })
    except Exception as e:
        logger.error(f"Error in get_patient_share_info: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@filemgmt.route('/api/share_progress/<progress_key>', methods=['GET'])
@login_required
def get_share_progress(progress_key):
    """Get current progress for ZIP creation and results when complete"""
    try:
        if not hasattr(current_app, 'zip_progress_cache'):
            return jsonify({'percent': 0, 'message': 'Starting...', 'complete': False})
        
        progress = current_app.zip_progress_cache.get(progress_key, {'percent': 100, 'message': 'Complete', 'complete': True})
        is_complete = progress.get('percent', 0) >= 100
        
        # If complete, include results
        result_data = {}
        if is_complete and hasattr(current_app, 'zip_result_cache'):
            result = current_app.zip_result_cache.get(progress_key, {})
            if result.get('status') == 'complete':
                result_data = {
                    'zip_url': result.get('short_zip_url'),
                    'lines': result.get('lines', [])
                }
                # Send emails if recipients were provided (we'll need to store recipients in cache)
                # For now, emails will be sent when frontend gets complete status
            elif result.get('status') == 'error':
                result_data = {'error': result.get('error')}
            
            # Clean up after returning results
            def cleanup():
                import time
                time.sleep(10)  # Wait 10 seconds for frontend to get results
                if hasattr(current_app, 'zip_progress_cache') and progress_key in current_app.zip_progress_cache:
                    del current_app.zip_progress_cache[progress_key]
                if hasattr(current_app, 'zip_result_cache') and progress_key in current_app.zip_result_cache:
                    del current_app.zip_result_cache[progress_key]
            Thread(target=cleanup, daemon=True).start()
        
        response = {
            'percent': progress.get('percent', 0),
            'message': progress.get('message', 'Processing...'),
            'complete': is_complete
        }
        response.update(result_data)
        return jsonify(response)
    except Exception as e:
        logger.error(f"Error getting share progress: {e}")
        return jsonify({'percent': 0, 'message': 'Unknown', 'complete': False})


@filemgmt.route('/download_admin_file/<int:file_id>', methods=['GET'])
@login_required
def download_admin_file(file_id):
    logger.debug(f"Received request to download admin file with ID: {file_id}")
    file = AdminFile.query.get_or_404(file_id)  # Querying the AdminFile table

    try:
        logger.debug(f"Processing admin file: {file.name}, S3 Key: {file.s3_key}")

        # Check if the current user is an admin
        is_admin = current_user.role == 'admin'
        logger.debug(f"Current user role: {'admin' if is_admin else 'non-admin'}")

        # Get the patient associated with the file
        patient = Patient.query.get(file.patient_id)
        if not patient:
            logger.error(f"Patient with ID {file.patient_id} not found.")
            return jsonify({'success': False, 'message': 'Associated patient not found'}), 404

        dentist = Dentist.query.get(patient.dentist_id)
        if not dentist:
            logger.error(f"Dentist with ID {patient.dentist_id} not found.")
            return jsonify({'success': False, 'message': 'Associated dentist not found'}), 404

        logger.debug(f"Patient: {patient.name}, Dentist: {dentist.name}, Dentist's DSO: {dentist.DSO}")

        # Ensure the user has permission to access this patient's files
        if not current_user.can_access_patient(patient):
            logger.warning(f"Unauthorized access attempt by user {current_user.email} for admin file {file_id}")
            return jsonify({'success': False, 'message': 'Unauthorized access'}), 403

        # Set content type with a fallback
        content_type = file.file_type or 'application/octet-stream'
        logger.debug(f"Determined content type: {content_type}")

        # Ensure correct MIME type for PDFs if needed
        if file.file_type == 'application/pdf' and 'pdf' not in content_type:
            content_type = 'application/pdf'
            logger.debug("Adjusted content type to 'application/pdf' for PDF file.")

        # Generate a pre-signed URL with the correct Content-Type and inline Content-Disposition
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': os.getenv('S3_BUCKET_NAME'),
                'Key': file.s3_key,
                'ResponseContentDisposition': 'inline',
                'ResponseContentType': content_type
            },
            ExpiresIn=3600 * 24 * 7  # URL expires in 1 week
        )

        logger.debug(f"Generated pre-signed URL for admin file {file.name}: {presigned_url}")
        return redirect(presigned_url)
    except Exception as e:
        logger.error(f"Error generating pre-signed URL for admin file {file.name}: {str(e)}")
        return jsonify({'success': False, 'message': 'Error generating file URL'}), 500

@filemgmt.route('/download_billing_files/<int:patient_id>', methods=['GET'])
@login_required
def download_billing_files(patient_id):
    logger.info(f"Preparing to download all billing files for patient ID: {patient_id}")
    patient = Patient.query.get_or_404(patient_id)

    # Fetch the billing files from the database
    billing_files = File.query.filter_by(patient_id=patient_id, category='billing').all()

    # Check if files exist
    if not billing_files:
        logger.warning(f"No billing files found for patient ID: {patient_id}")
        return jsonify({'success': False, 'message': 'No billing files found.'}), 404

    # Create a temporary in-memory zip file
    zip_buffer = BytesIO()

    try:
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for file in billing_files:
                # Download each file from S3 and add to the ZIP
                file_data = BytesIO()
                logger.debug(f"Downloading file: {file.name} from S3 with key: {file.s3_key}")
                
                # Download the file from S3
                s3_client.download_fileobj(os.getenv('S3_BUCKET_NAME'), file.s3_key, file_data)
                file_data.seek(0)  # Reset buffer position to the start
                
                # Write the file into the ZIP under the appropriate name
                zip_file.writestr(file.name, file_data.read())

        # Finalize the ZIP
        zip_buffer.seek(0)
        return send_file(zip_buffer, as_attachment=True, download_name=f'billing_files_patient_{patient_id}.zip', mimetype='application/zip')
    
    except Exception as e:
        logger.error(f"Error creating ZIP for patient {patient_id}: {str(e)}")
        return jsonify({'success': False, 'message': 'Error downloading billing files.'}), 500


@filemgmt.route('/remove_file/<int:file_id>', methods=['DELETE'])
@login_required
def remove_file(file_id):
    try:
        # Attempt to fetch the file from the File table
        file = File.query.get(file_id)

        if not file:
            # If not found in File, check in AdminFile
            file = AdminFile.query.get_or_404(file_id)

        # Check permissions
        if current_user.role != 'admin' and getattr(file, 'patient_id', None) != current_user.id:
            return jsonify({'success': False, 'message': 'Unauthorized access'}), 403

        # Delete the file from S3
        try:
            s3_client.delete_object(Bucket=os.getenv('S3_BUCKET_NAME'), Key=file.s3_key)
            logger.debug(f"Deleted file '{file.name}' from S3.")
        except Exception as e:
            logger.error(f"Failed to delete file '{file.name}' from S3: {str(e)}")

        # Delete the file from the database
        db.session.delete(file)
        db.session.commit()
        logger.debug(f"Deleted file '{file.name}' from the database.")
        return jsonify({'success': True, 'message': 'File removed successfully.'})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting file: {str(e)}")
        return jsonify({'success': False, 'message': 'Error deleting file.'}), 500



def generate_presigned_url(file_key, expiration=3600):
    """Generate a pre-signed URL for uploading large files directly to S3."""
    try:
        # Validate and ensure file_key is a string
        if not file_key or file_key == 'None' or file_key == '':
            logger.warning(f"Invalid file key provided: {file_key}")
            return None
        
        # Ensure file_key is a string (convert if needed)
        if not isinstance(file_key, str):
            file_key = str(file_key)
            
        return s3_client.generate_presigned_url(
            'put_object',
            Params={'Bucket': os.getenv('S3_BUCKET_NAME'), 'Key': file_key},
            ExpiresIn=expiration
        )
    except Exception as e:
        logger.error(f'Error generating pre-signed URL for {file_key}: {str(e)}')
        raise



@filemgmt.route('/extract_rar_files', methods=['POST'])
@login_required
def extract_rar_files():
    """
    Extracts a .rar file on the server and returns file list for direct upload.
    This is more efficient than RAR->ZIP->extract for large files.
    Files are extracted to temp directory, then uploaded directly to S3.
    """
    temp_rar_path = None
    extract_dir = None
    
    try:
        rar_file = request.files.get('rar_file')
        patient_id = request.form.get('patient_id', type=int)
        folder_name = request.form.get('folder_name', '')

        if not rar_file or not rar_file.filename.lower().endswith('.rar'):
            logger.error("No valid .rar file uploaded.")
            return jsonify({'success': False, 'message': 'Please upload a valid .rar file.'}), 400

        rar_filename = secure_filename(rar_file.filename)
        logger.info(f"Processing .rar file: {rar_filename} for patient {patient_id}")

        # Save RAR to temp file
        temp_rar = tempfile.NamedTemporaryFile(delete=False, suffix='.rar')
        temp_rar_path = temp_rar.name
        temp_rar.close()
        
        logger.info("Saving RAR file to disk...")
        rar_file.seek(0)
        rar_file.save(temp_rar_path)
        
        # Ensure file is fully written
        fd = os.open(temp_rar_path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        
        file_size = os.path.getsize(temp_rar_path)
        logger.info(f"Saved RAR to temp file: {temp_rar_path} (size: {file_size:,} bytes)")
        
        if file_size == 0:
            if temp_rar_path and os.path.exists(temp_rar_path):
                os.remove(temp_rar_path)
            return jsonify({'success': False, 'message': 'The uploaded RAR file appears to be empty.'}), 400

        # Initialize extraction tool preference
        use_unrar = False

        # Verify required extraction tools exist (avoid confusing [Errno 2] and hanging UI)
        seven_zip = shutil.which('7z') or shutil.which('7za')
        unrar_bin = shutil.which('unrar')
        if not seven_zip and not unrar_bin:
            logger.error("RAR extraction tools not found: neither '7z'/'7za' nor 'unrar' is installed")
            if temp_rar_path and os.path.exists(temp_rar_path):
                os.remove(temp_rar_path)
            return jsonify({
                'success': False,
                'message': "Server is missing RAR extraction tools. Please install '7z' (p7zip) or 'unrar' on the server."
            }), 500

        # Verify RAR header and file integrity
        with open(temp_rar_path, 'rb') as f:
            header = f.read(7)
            if not header.startswith(b'Rar!'):
                if temp_rar_path and os.path.exists(temp_rar_path):
                    os.remove(temp_rar_path)
                return jsonify({
                    'success': False, 
                    'message': 'The file does not appear to be a valid RAR archive.'
                }), 400
            
            # Check RAR version (RAR3 vs RAR5)
            f.seek(0)
            header_bytes = f.read(10)
            if header_bytes[6:7] == b'\x00':
                rar_version = "RAR3"
            elif header_bytes[6:7] == b'\x01':
                rar_version = "RAR5"
            else:
                rar_version = "Unknown"
            logger.info(f"Detected RAR format: {rar_version}")
            
            # Verify file is readable by testing with 7z list command
            if seven_zip:
                test_result = subprocess.run(
                    [seven_zip, 'l', temp_rar_path],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
            else:
                # Force fallback to unrar when 7z isn't installed
                test_result = subprocess.CompletedProcess(args=['7z', 'l', temp_rar_path], returncode=1, stdout='', stderr='7z not installed')
            
            if test_result.returncode != 0:
                logger.warning(f"7z test failed: {test_result.stderr}")
                # Try unrar-free as fallback
                logger.info("Trying unrar-free as fallback...")
                if not unrar_bin:
                    # Don't attempt to run a missing binary; return a clear, actionable error.
                    if temp_rar_path and os.path.exists(temp_rar_path):
                        os.remove(temp_rar_path)
                    return jsonify({
                        'success': False,
                        'message': (
                            "This RAR archive could not be opened with 7z/7za on the server. "
                            "Install 'unrar' to support this RAR format (often RAR5), or upload an extracted folder / .zip instead."
                        )
                    }), 400

                unrar_test = subprocess.run(
                    [unrar_bin, 't', temp_rar_path],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if unrar_test.returncode != 0:
                    logger.error(f"Both 7z and unrar failed. 7z: {test_result.stderr}, unrar: {unrar_test.stderr}")
                    if temp_rar_path and os.path.exists(temp_rar_path):
                        os.remove(temp_rar_path)
                    return jsonify({
                        'success': False,
                        'message': f'The RAR file cannot be opened. It may be corrupted or incomplete. Error: {test_result.stderr[:200]}'
                    }), 400
                else:
                    logger.info("unrar-free can read the file, will use it for extraction")
                    use_unrar = True
            else:
                logger.info("7z can read the file")
                use_unrar = False

        # Check available disk space before extraction
        # Estimate: RAR file size + 3x for extracted files (compression ratio)
        estimated_extracted_size = file_size * 3
        required_space = file_size + estimated_extracted_size
        
        try:
            statvfs = os.statvfs(os.path.dirname(temp_rar_path))
            available_space = statvfs.f_bavail * statvfs.f_frsize
            logger.info(f"Disk space check - Required: {required_space:,} bytes, Available: {available_space:,} bytes")
            
            if available_space < required_space:
                logger.error(f"Insufficient disk space: need {required_space:,} bytes, have {available_space:,} bytes")
                if temp_rar_path and os.path.exists(temp_rar_path):
                    os.remove(temp_rar_path)
                return jsonify({
                    'success': False,
                    'message': f'Insufficient disk space. Required: {required_space / (1024**3):.1f} GB, Available: {available_space / (1024**3):.1f} GB'
                }), 507  # 507 Insufficient Storage
        except Exception as e:
            logger.warning(f"Could not check disk space: {e}")

        # Extract RAR to temp directory
        extract_dir = tempfile.mkdtemp()
        logger.info(f"Extracting RAR to: {extract_dir} (estimated size: {estimated_extracted_size:,} bytes)")
        
        try:
            # Use unrar if 7z couldn't read it, otherwise use 7z
            if use_unrar:
                if not unrar_bin:
                    return jsonify({
                        'success': False,
                        'message': "Server missing 'unrar' but RAR requires it. Please install 'unrar' or upload an extracted folder / .zip."
                    }), 500
                logger.info("Using unrar-free for extraction...")
                extract_result = subprocess.run(
                    [unrar_bin, 'x', '-y', temp_rar_path, extract_dir + '/'],
                    capture_output=True,
                    text=True,
                    timeout=3600
                )
            else:
                logger.info("Using 7z for extraction...")
                extract_result = subprocess.run(
                    [seven_zip or '7z', 'x', '-y', '-o' + extract_dir, temp_rar_path],
                    capture_output=True,
                    text=True,
                    timeout=3600
                )
            
            if extract_result.returncode != 0:
                logger.error(f"Extraction failed ({'unrar' if use_unrar else '7z'}): {extract_result.stderr}")
                # Try the other tool as fallback
                if not use_unrar:
                    logger.info("7z failed, trying unrar-free as fallback...")
                    if not unrar_bin:
                        return jsonify({
                            'success': False,
                            'message': (
                                "7z failed to extract this RAR and 'unrar' is not installed on the server. "
                                "Install 'unrar' or upload an extracted folder / .zip."
                            )
                        }), 500
                    fallback_result = subprocess.run(
                        [unrar_bin, 'x', '-y', temp_rar_path, extract_dir + '/'],
                        capture_output=True,
                        text=True,
                        timeout=3600
                    )
                    if fallback_result.returncode != 0:
                        return jsonify({
                            'success': False,
                            'message': f'Failed to extract RAR file with both tools. 7z: {extract_result.stderr[:200]}, unrar: {fallback_result.stderr[:200]}'
                        }), 500
                    logger.info("unrar-free fallback succeeded")
                else:
                    return jsonify({
                        'success': False,
                        'message': f'Failed to extract RAR file: {extract_result.stderr[:200]}'
                    }), 500
            
            # Get list of extracted files and calculate actual size
            extracted_files = []
            total_extracted_size = 0
            for root, dirs, files in os.walk(extract_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, extract_dir)
                    file_size = os.path.getsize(file_path)
                    total_extracted_size += file_size
                    extracted_files.append({
                        'path': rel_path,
                        'size': file_size,
                        'full_path': file_path
                    })
            
            logger.info(f"Extracted {len(extracted_files)} files from RAR (total size: {total_extracted_size:,} bytes)")
            
            # Upload files directly to S3
            s3_client = boto3.client('s3')
            bucket = os.getenv('S3_BUCKET_NAME')
            uploaded_count = 0
            
            # Determine S3 prefix based on folder_name or use RAR filename
            if folder_name:
                base_folder = folder_name
            else:
                base_folder = rar_filename.rsplit('.', 1)[0]
            
            s3_prefix = f"patients/{patient_id}/imaging/cbct/{base_folder}/"
            
            total_files = len(extracted_files)
            db_records_created = 0
            
            for idx, file_info in enumerate(extracted_files, 1):
                try:
                    s3_key = s3_prefix + file_info['path'].replace('\\', '/')
                    file_name = os.path.basename(file_info['path'])
                    
                    # Log progress every 50 files or at milestones
                    if idx % 50 == 0 or idx == total_files or idx <= 3:
                        logger.info(f"Uploading file {idx}/{total_files}: {file_info['path']} ({file_info['size']:,} bytes)")
                    
                    # Determine MIME type based on file extension
                    file_ext = file_name.lower().split('.')[-1] if '.' in file_name else ''
                    if file_ext in [e.lstrip('.') for e in DICOM_EXTENSIONS]:
                        mime_type = 'application/dicom'
                    elif file_ext in ['jpg', 'jpeg']:
                        mime_type = 'image/jpeg'
                    elif file_ext == 'png':
                        mime_type = 'image/png'
                    else:
                        mime_type = 'application/octet-stream'
                    
                    # For CBCT DICOM: split multi-frame at upload so MPR works
                    files_to_upload = []
                    if is_dicom_file(file_name):
                        with open(file_info['full_path'], 'rb') as f:
                            file_bytes = f.read()
                        for item in process_dicom_for_upload(file_bytes, s3_key, patient_id=str(patient_id)):
                            files_to_upload.append((item['s3_key'], item['content'], item['filename'], item['file_size']))
                    else:
                        with open(file_info['full_path'], 'rb') as f:
                            files_to_upload = [(s3_key, f.read(), file_name, file_info['size'])]
                    
                    for up_key, up_content, up_name, up_size in files_to_upload:
                        s3_client.put_object(
                            Bucket=bucket,
                            Key=up_key,
                            Body=up_content,
                            ContentType='application/dicom' if is_dicom_file(up_name) else mime_type,
                            ServerSideEncryption='AES256'
                        )
                        uploaded_count += 1
                        try:
                            new_file = File(
                                name=up_name,
                                patient_id=patient_id,
                                file_type='application/dicom' if is_dicom_file(up_name) else mime_type,
                                file_size=up_size,
                                s3_key=up_key,
                                category='imaging',
                                subcategory='cbct'
                            )
                            db.session.add(new_file)
                            db_records_created += 1
                            if db_records_created % 100 == 0:
                                db.session.commit()
                                logger.info(f"Committed {db_records_created} database records")
                                db.session.expunge_all()
                        except Exception as db_err:
                            logger.warning(f"Error creating DB record for {up_name}: {db_err}")
                        
                except Exception as e:
                    logger.error(f"Error uploading {file_info['path']}: {e}")
            
            # Final commit for remaining records
            try:
                db.session.commit()
                logger.info(f"Created {db_records_created} database records for extracted files")
                db.session.expunge_all()
            except Exception as commit_err:
                logger.error(f"Error committing database records: {commit_err}")
                db.session.rollback()
            
            logger.info(f"Successfully extracted and uploaded {uploaded_count}/{len(extracted_files)} files, {db_records_created} database records created")
            
            # Trigger pre-zip in the background.
            # Why: building the pre-zip can take a long time for large CBCT folders (many files),
            # and blocking this request makes the UI appear stuck at ~95%.
            prezip_created = False
            prezip_scheduled = False
            try:
                from flask_app.utils.cbct_prezip_manager import prezip_exists, trigger_prezip_background_for_folder
                if prezip_exists(patient_id, base_folder):
                    prezip_created = True
                else:
                    trigger_prezip_background_for_folder(
                        patient_id,
                        base_folder,
                        app=current_app._get_current_object()
                    )
                    prezip_scheduled = True
                    logger.info(f"Pre-zip scheduled in background for folder '{base_folder}' patient {patient_id}")
            except Exception as e:
                logger.warning(f"Failed to schedule pre-zip: {e}")
            
            return jsonify({
                'success': True,
                'message': (
                    f'Successfully extracted and uploaded {uploaded_count} files from RAR archive.' +
                    (' Pre-zip ready for fast downloads!' if prezip_created else '') +
                    (' Pre-zip is being generated in the background for fast downloads.' if (not prezip_created and prezip_scheduled) else '')
                ),
                'uploaded_count': uploaded_count,
                'total_files': len(extracted_files),
                'db_records_created': db_records_created,
                'folder_name': base_folder,
                'prezip_created': prezip_created,
                'prezip_scheduled': prezip_scheduled
            })
            
        finally:
            # Always clean up temp files, even if there's an error
            logger.info("Cleaning up temporary files...")
            if extract_dir and os.path.exists(extract_dir):
                try:
                    # Calculate size before deletion for logging
                    total_size = sum(
                        os.path.getsize(os.path.join(root, file))
                        for root, dirs, files in os.walk(extract_dir)
                        for file in files
                    )
                    shutil.rmtree(extract_dir, ignore_errors=True)
                    logger.info(f"Deleted extraction directory ({total_size:,} bytes freed)")
                except Exception as e:
                    logger.warning(f"Error deleting extraction directory: {e}")
            
            if temp_rar_path and os.path.exists(temp_rar_path):
                try:
                    rar_size = os.path.getsize(temp_rar_path)
                    os.remove(temp_rar_path)
                    logger.info(f"Deleted RAR temp file ({rar_size:,} bytes freed)")
                except Exception as e:
                    logger.warning(f"Error deleting RAR temp file: {e}")
        
    except subprocess.TimeoutExpired:
        logger.error("RAR extraction timed out")
        if extract_dir and os.path.exists(extract_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)
        return jsonify({
            'success': False,
            'message': 'RAR extraction timed out. The file may be too large.'
        }), 500
    except Exception as e:
        # Log full traceback to diagnose live-only failures (S3/DB/OOM/etc.)
        logger.exception("Error extracting RAR")
        if extract_dir and os.path.exists(extract_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)
        if temp_rar_path and os.path.exists(temp_rar_path):
            os.remove(temp_rar_path)
        return jsonify({
            'success': False,
            'message': f'Error processing RAR file: {str(e)}'
        }), 500


@filemgmt.route('/convert_rar_to_zip', methods=['POST'])
@login_required
def convert_rar_to_zip():
    """
    Converts a .rar file to a .zip file and returns the resulting .zip file to the client.
    Uses disk-based temp files to handle large CBCT archives without memory issues.
    Requires unrar, unar, 7z, or bsdtar to be installed on the system.
    """
    temp_rar_path = None
    temp_zip_path = None
    extract_dir = None
    
    try:
        rar_file = request.files.get('rar_file')

        if not rar_file or not rar_file.filename.lower().endswith('.rar'):
            logger.error("No valid .rar file uploaded.")
            return jsonify({'success': False, 'message': 'Please upload a valid .rar file.'}), 400

        # Ensure secure filename
        rar_filename = secure_filename(rar_file.filename)
        logger.info(f"Processing .rar file: {rar_filename}")

        # Save RAR to temp file (handles large files better than memory)
        temp_rar = tempfile.NamedTemporaryFile(delete=False, suffix='.rar')
        temp_rar_path = temp_rar.name
        temp_rar.close()
        
        # Save file using Flask's save method (handles large files properly)
        logger.info("Saving RAR file to disk...")
        rar_file.seek(0)  # Reset file pointer to beginning
        rar_file.save(temp_rar_path)
        
        # Ensure file is fully written to disk before reading
        fd = os.open(temp_rar_path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        
        # Verify file was saved correctly
        file_size = os.path.getsize(temp_rar_path)
        logger.info(f"Saved RAR to temp file: {temp_rar_path} (size: {file_size:,} bytes)")
        
        if file_size == 0:
            logger.error("RAR file is empty after upload")
            return jsonify({'success': False, 'message': 'The uploaded RAR file appears to be empty.'}), 400
        
        # Additional check: verify file is readable
        if not os.access(temp_rar_path, os.R_OK):
            logger.error(f"RAR file is not readable: {temp_rar_path}")
            return jsonify({'success': False, 'message': 'Error saving RAR file. Please try again.'}), 500

        # Verify RAR file header
        logger.info("Verifying RAR file header...")
        with open(temp_rar_path, 'rb') as f:
            header = f.read(7)
            if not header.startswith(b'Rar!'):
                logger.error(f"Invalid RAR header: {header.hex()}")
                return jsonify({
                    'success': False, 
                    'message': 'The file does not appear to be a valid RAR archive. Please ensure the file was fully uploaded.'
                }), 400
        logger.info("RAR file header verified")

        # For large files (2GB+), we need to extract to disk temporarily
        # But we'll minimize disk usage by extracting directly to ZIP using 7z
        # This is more efficient than extracting then zipping separately
        temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        temp_zip_path = temp_zip.name
        temp_zip.close()
        
        zip_filename = rar_filename.rsplit('.', 1)[0] + '.zip'
        
        logger.info(f"Converting RAR to ZIP using 7z (input: {file_size:,} bytes)...")
        logger.info("Note: For large files, this requires temporary disk space (~2x file size)")
        
        try:
            # Use 7z to extract RAR contents and add them directly to ZIP
            # This is more efficient than extract-then-zip for large files
            # 7z x -so input.rar | 7z a -si output.zip
            # Actually, simpler: extract to temp dir, then zip it (7z handles large files better)
            extract_dir = tempfile.mkdtemp()
            logger.info(f"Extracting RAR to temp directory: {extract_dir}")
            
            # Extract RAR using 7z
            extract_result = subprocess.run(
                ['7z', 'x', '-y', '-o' + extract_dir, temp_rar_path],
                capture_output=True,
                text=True,
                timeout=3600
            )
            
            if extract_result.returncode != 0:
                logger.error(f"7z extraction failed: {extract_result.stderr}")
                shutil.rmtree(extract_dir, ignore_errors=True)
                return jsonify({
                    'success': False,
                    'message': f'Failed to extract RAR file: {extract_result.stderr[:200]}'
                }), 500
            
            logger.info("RAR extraction complete, creating ZIP...")
            
            # Create ZIP from extracted files using 7z (handles large files efficiently)
            zip_result = subprocess.run(
                ['7z', 'a', '-tzip', '-mm=Deflate', '-mx=1', '-y', temp_zip_path, extract_dir + '/*'],
                capture_output=True,
                text=True,
                timeout=3600
            )
            
            # Clean up extraction directory immediately to free disk space
            shutil.rmtree(extract_dir, ignore_errors=True)
            logger.info("Cleaned up extraction directory")
            
            if zip_result.returncode != 0:
                logger.error(f"7z ZIP creation failed: {zip_result.stderr}")
                return jsonify({
                    'success': False,
                    'message': f'Failed to create ZIP file: {zip_result.stderr[:200]}'
                }), 500
            
            zip_size = os.path.getsize(temp_zip_path)
            logger.info(f"Successfully converted {rar_filename} to {zip_filename} (ZIP size: {zip_size:,} bytes)")
            
        except subprocess.TimeoutExpired:
            logger.error("7z operation timed out (file too large or complex)")
            if 'extract_dir' in locals():
                shutil.rmtree(extract_dir, ignore_errors=True)
            return jsonify({
                'success': False,
                'message': 'RAR file conversion timed out. The file may be too large. Please try converting to ZIP format first.'
            }), 500
        except FileNotFoundError:
            logger.error("7z command not found")
            return jsonify({
                'success': False,
                'message': 'RAR extraction tool not available. Please contact support.'
            }), 500
        except Exception as e:
            logger.error(f"Error during RAR conversion: {str(e)}")
            if 'extract_dir' in locals():
                shutil.rmtree(extract_dir, ignore_errors=True)
            return jsonify({
                'success': False,
                'message': f'Error converting RAR file: {str(e)}'
            }), 500

        # Clean up temp files after response is sent
        @after_this_request
        def cleanup(response):
            try:
                if temp_rar_path and os.path.exists(temp_rar_path):
                    os.remove(temp_rar_path)
                if temp_zip_path and os.path.exists(temp_zip_path):
                    os.remove(temp_zip_path)
                if extract_dir and os.path.exists(extract_dir):
                    shutil.rmtree(extract_dir, ignore_errors=True)
                logger.debug("Cleaned up temp files after RAR conversion")
            except Exception as cleanup_err:
                logger.warning(f"Error during cleanup: {cleanup_err}")
            return response

        return send_file(
            temp_zip_path,
            as_attachment=True,
            download_name=zip_filename,
            mimetype='application/zip'
        )

    except rarfile.RarCannotExec as e:
        logger.error(f"RAR extraction tool not found: {str(e)}")
        return jsonify({
            'success': False, 
            'message': 'RAR extraction is not available. Please convert the file to ZIP format before uploading.'
        }), 500
    except rarfile.Error as e:
        logger.error(f"Error processing .rar file: {str(e)}")
        return jsonify({'success': False, 'message': f"Error processing .rar file: {str(e)}"}), 500
    except Exception as e:
        logger.error(f"Unexpected error while converting .rar to .zip: {str(e)}")
        return jsonify({'success': False, 'message': 'An error occurred during conversion.'}), 500
    finally:
        # Cleanup on error (success cleanup is handled by after_this_request)
        try:
            if temp_rar_path and os.path.exists(temp_rar_path):
                os.remove(temp_rar_path)
            if extract_dir and os.path.exists(extract_dir):
                shutil.rmtree(extract_dir, ignore_errors=True)
        except Exception:
            pass


def build_email_with_logo(email_body_text, title="Files Shared", subtitle=""):
    """
    Build HTML email body with VizBriz logo embedded.
    
    Args:
        email_body_text: Plain text content for the email
        title: Email title/heading
        subtitle: Optional subtitle text
    
    Returns:
        HTML formatted email body with embedded logo
    """
    # Embed VizBriz logo as base64 for email
    logo_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        'flask_static', 'images', 'logos', 'vizbrizz_logo color without grad.png'
    )
    
    logo_data_uri = ''
    if os.path.exists(logo_path):
        try:
            with open(logo_path, 'rb') as logo_file:
                logo_data = logo_file.read()
                logo_base64 = base64.b64encode(logo_data).decode('utf-8')
                logo_data_uri = f"data:image/png;base64,{logo_base64}"
        except Exception as e:
            logger.error(f"Failed to encode logo for email: {str(e)}")
            # Fallback to absolute URL
            base_url = os.getenv('BASE_URL') or current_app.config.get('BASE_URL') or ''
            if base_url:
                logo_data_uri = f"{base_url.rstrip('/')}/flask_static/images/logos/vizbrizz_logo%20color%20without%20grad.png"
            else:
                logo_data_uri = '/flask_static/images/logos/vizbrizz_logo%20color%20without%20grad.png'
    else:
        logger.warning(f"Logo file not found at {logo_path}, using URL fallback")
        base_url = os.getenv('BASE_URL') or current_app.config.get('BASE_URL') or ''
        if base_url:
            logo_data_uri = f"{base_url.rstrip('/')}/flask_static/images/logos/vizbrizz_logo%20color%20without%20grad.png"
        else:
            logo_data_uri = '/flask_static/images/logos/vizbrizz_logo%20color%20without%20grad.png'
    
    # Build HTML email with logo and professional styling
    # Replace newlines with HTML breaks (do this before f-string to avoid backslash in expression)
    formatted_body = email_body_text.replace('\n', '<br>').replace(chr(10), '<br>')
    subtitle_html = f"<p style='margin:0 0 18px 0; color:#6b7280;'>{subtitle}</p>" if subtitle else ""
    
    email_body_html = f"""
    <div style='font-family:Segoe UI,Arial,sans-serif; color:#2c3e50;'>
      <div style='text-align:center; margin-bottom:30px; padding:20px 0;'>
        <img src='{logo_data_uri}' alt='VizBriz Logo' style='height:120px; max-width:400px; object-fit:contain; display:block; margin:0 auto;'>
      </div>
      <h2 style='margin:0 0 8px 0; color:#2c3e50;'>{title}</h2>
      {subtitle_html}
      <div style='margin:0 0 20px 0; padding:16px; background:#f8f9fa; border-radius:8px; border-left:4px solid #3498db; font-family:monospace; font-size:14px; line-height:1.6;'>
        {formatted_body}
      </div>
      <p style='margin:20px 0 0 0; color:#6b7280; font-size:14px;'>Links will expire in 7 days.</p>
    </div>
    """
    return email_body_html


def send_email_with_sendgrid(recipient_email, subject, html_content, text_content=None, 
                           patient_id=None, sender_id=None, email_type=None, sender_type='system', 
                           skip_db_logging=False):
    """
    Send email via SendGrid and optionally log it to the database.
    
    Args:
        recipient_email (str): Email address of the recipient
        subject (str): Email subject
        html_content (str): HTML content of the email
        text_content (str, optional): Plain text content of the email
        patient_id (int, optional): ID of the patient this email is related to
        sender_id (int, optional): ID of the dentist/admin sending the email
        email_type (str, optional): Type of email (hipaa_consent, osa_report, follow_up, notification, etc.)
        sender_type (str): Type of sender (dentist, admin, system)
        skip_db_logging (bool): If True, skip database logging (useful for file sharing notifications)
    """
    try:
        # Debug logging
        logger.info(f"send_email_with_sendgrid called with recipient_email: {recipient_email}, subject: {subject}")
        
        # Send email using Flask-Mail (same as working wizard implementation)
        from flask_mail import Mail, Message
        from flask import current_app
        
        mail = Mail(current_app)
        sender_email = current_app.config.get('MAIL_DEFAULT_SENDER', 'info@vizbriz.com')
        
        msg = Message(
            subject=subject,
            sender=sender_email,
            recipients=[recipient_email]
        )
        msg.body = text_content or html_content
        msg.html = html_content
        
        # Send the email
        mail.send(msg)
        
        logger.info(f"Email sent to {recipient_email} via Flask-Mail")
        
        # Log the email to the database (skip for file sharing notifications)
        if not skip_db_logging:
            try:
                from flask_app.models import EmailLog
                from flask_app.extensions import db
                
                # Only log if patient_id is provided (required field)
                if patient_id is not None:
                    # Create email log entry
                    email_log = EmailLog(
                        patient_id=patient_id,
                        sender_id=sender_id,  # Can be None for system emails
                        sender_type=sender_type,
                        sender_email=sender_email,
                        recipient_email=recipient_email,
                        subject=subject,
                        message_content=html_content,
                        email_type=email_type or 'notification',
                        status='sent'
                    )
                    
                    db.session.add(email_log)
                    db.session.commit()
                    
                    logger.info(f"Email logged to database with ID: {email_log.id}")
                else:
                    logger.warning(f"Skipping email log - patient_id is None for email to {recipient_email}")
                
            except Exception as log_error:
                logger.error(f"Failed to log email to database: {str(log_error)}")
                # Rollback the session if there was an error
                try:
                    db.session.rollback()
                except Exception:
                    pass
                # Don't fail the email sending if logging fails
        
        return True
            
    except Exception as e:
        logger.error(f"Failed to send email via Flask-Mail: {str(e)}")
        
        # Try to log the failed email (skip for file sharing)
        if not skip_db_logging:
            try:
                from flask_app.models import EmailLog
                from flask_app.extensions import db
                
                # Only log if patient_id is provided
                if patient_id is not None:
                    email_log = EmailLog(
                        patient_id=patient_id,
                        sender_id=sender_id,  # Can be None for system emails
                        sender_type=sender_type,
                        sender_email=current_app.config.get('MAIL_DEFAULT_SENDER', 'info@vizbriz.com'),
                        recipient_email=recipient_email,
                        subject=subject,
                        message_content=html_content,
                        email_type=email_type or 'notification',
                        status='failed'
                    )
                    
                    db.session.add(email_log)
                    db.session.commit()
                    
                    logger.info(f"Failed email logged to database with ID: {email_log.id}")
                    
            except Exception as log_error:
                logger.error(f"Failed to log failed email to database: {str(log_error)}")
        
        return False


# Import the centralized URL shortening utility
from ..utils.url_utils import shorten_url_with_tinyurl
from ..utils.s3_presign_client import get_s3_client_for_presigning


@filemgmt.route('/generate_presigned_links', methods=['POST'])
def generate_presigned_links():
    """
    Generate presigned download links for a list of files or folders.
    - If folder is a CBCT folder, list objects directly from S3 in 
      patients/<patientId>/imaging/cbct/<folderName> 
    - If more than 5 total objects, create ZIP files (split into 50MB chunks for CBCT).
    - Otherwise, create individual presigned links.
    """
    try:
        # Ensure we're getting JSON data
        if not request.is_json:
            return jsonify({
                'success': False,
                'message': 'Invalid request format. Expected JSON data.',
                'error_type': 'invalid_format'
            }), 400

        data = request.get_json()

        # 1) Retrieve items from request (instead of just file_ids)
        items = data.get('items', [])
        recipient_email = data.get('recipient_email')
        user_message = data.get('message', '')

        if not items or not recipient_email:
            return jsonify({
                'success': False,
                'message': 'Missing required parameters. Please provide both items and recipient email.',
                'error_type': 'missing_parameters'
            }), 400

        # We'll accumulate a list of S3 keys for everything to share
        all_s3_keys = []
        all_db_files = []
        
        # Extract patient_id from items (should be the same for all items)
        # Also try to get from request data as fallback
        patient_id = None
        if items and len(items) > 0:
            patient_id = items[0].get('patientId')
        
        # If not found in items, try request data
        if not patient_id:
            patient_id = data.get('patient_id')

        # 2) Process items
        for item in items:
            is_folder = item.get('isFolder', False)
            category = (item.get('category') or '').lower()
            item_patient_id = item.get('patientId')
            
            # Use item_patient_id if we still don't have patient_id
            if not patient_id and item_patient_id:
                patient_id = item_patient_id
            folder_name = item.get('folderName')
            file_id = item.get('fileId')

            if is_folder and category == 'cbct':
                # Check for pre-zipped file first
                from flask_app.utils.cbct_prezip_manager import get_prezip_url
                prezip_url = get_prezip_url(item_patient_id, folder_name, expires_in=3600 * 24 * 7)  # 7 days
                
                if prezip_url:
                    # Pre-zip exists! Share this directly instead of creating new zips
                    logger.info(f"Using pre-zipped file for sharing CBCT folder: {folder_name}")
                    
                    # Shorten the URL
                    try:
                        short_url = shorten_url_with_tinyurl(prezip_url) or prezip_url
                    except Exception:
                        short_url = prezip_url
                    
                    # Get patient info for email
                    patient = Patient.query.get(item_patient_id)
                    patient_name = patient.name if patient else f"Patient {item_patient_id}"
                    
                    # Build and send email with the pre-zip link
                    email_body = f"Please find link(s) below to the VizBriz Quiz Respondent files.\n\n"
                    email_body += f"CBCT Scan ({folder_name}): {short_url}\n"
                    if user_message:
                        email_body += f"\nMessage from sender: {user_message}\n"
                    
                    email_body_html = build_email_with_logo(email_body, title="CBCT Files Shared")
                    email_sent = send_email_with_sendgrid(
                        recipient_email, 
                        "CBCT Files shared from VizBriz", 
                        email_body_html, 
                        email_body, 
                        patient_id=item_patient_id, 
                        email_type='notification'
                    )
                    
                    if email_sent:
                        return jsonify({
                            'success': True,
                            'message': 'CBCT files shared successfully.'
                        })
                    else:
                        return jsonify({
                            'success': False,
                            'message': 'Error sending email. Please try again.',
                            'error_type': 'email_error'
                        }), 500
                
                # No pre-zip, fall back to listing files
                prefix = f"patients/{item_patient_id}/imaging/cbct/{folder_name}/"
                logger.info(f"No pre-zip found, listing CBCT folder in S3: prefix={prefix}")
                
                try:
                    response = s3_client.list_objects_v2(
                        Bucket=os.getenv('S3_BUCKET_NAME'),
                        Prefix=prefix
                    )
                    contents = response.get('Contents', [])
                    for obj in contents:
                        key = obj['Key']
                        if not key.endswith('/'):
                            all_s3_keys.append(key)
                except Exception as e:
                    logger.error(f"Error listing S3 objects for prefix {prefix}: {str(e)}")
                    return jsonify({
                        'success': False,
                        'message': f'Error accessing folder {folder_name}. Please try again.',
                        'error_type': 's3_access_error'
                    }), 500
            elif file_id:
                try:
                    file_obj = File.query.get(file_id)
                    if file_obj:
                        all_s3_keys.append(file_obj.s3_key)
                        all_db_files.append(file_obj)
                        # Extract patient_id from file if we still don't have it
                        if not patient_id and hasattr(file_obj, 'patient_id') and file_obj.patient_id:
                            patient_id = file_obj.patient_id
                except Exception as e:
                    logger.error(f"Error retrieving file {file_id}: {str(e)}")
                    return jsonify({
                        'success': False,
                        'message': 'Error accessing file. Please try again.',
                        'error_type': 'file_access_error'
                    }), 500

        # Validate patient_id is present after processing items
        if not patient_id:
            return jsonify({
                'success': False,
                'message': 'Missing patient_id. Please provide patient_id in request or items.',
                'error_type': 'missing_patient_id'
            }), 400

        # Remove duplicates
        all_s3_keys = list(set(all_s3_keys))
        logger.info(f"Total S3 objects to share: {len(all_s3_keys)}")

        if not all_s3_keys:
            return jsonify({
                'success': False,
                'message': 'No valid files found to share.',
                'error_type': 'no_files'
            }), 404

        # For 5 or fewer files, generate individual links
        if len(all_s3_keys) <= 5:
            presigned_links = []
            presign_client = get_s3_client_for_presigning()
            for key in all_s3_keys:
                try:
                    presigned_url = presign_client.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': os.getenv('S3_BUCKET_NAME'), 'Key': key},
                        ExpiresIn=168 * 3600
                    )
                    short_url = shorten_url_with_tinyurl(presigned_url) or presigned_url
                    filename = key.split('/')[-1] or "file"
                    presigned_links.append({'file_name': filename, 'url': short_url})
                except Exception as e:
                    logger.error(f"Error generating presigned URL for {key}: {str(e)}")
                    continue

            if not presigned_links:
                return jsonify({
                    'success': False,
                    'message': 'Error generating download links. Please try again.',
                    'error_type': 'link_generation_error'
                }), 500

            try:
                email_body = f"{user_message}\n\n" if user_message else "Here are your downloadable links:\n\n"
                for link in presigned_links:
                    email_body += f"\n{link['file_name']}: {link['url']}\n"

                # Check if email sending was successful
                email_body_html = build_email_with_logo(email_body, title="Files Shared")
                email_sent = send_email_with_sendgrid(recipient_email, "Files shared from VizBriz", email_body_html, email_body, patient_id=patient_id, email_type='notification')
                if email_sent:
                    return jsonify({
                        'success': True,
                        'message': 'Individual links generated and email sent successfully',
                        'links': presigned_links
                    })
                else:
                    logger.error("Email sending failed - send_email_with_sendgrid returned False")
                    return jsonify({
                        'success': False,
                        'message': 'Error sending email. Please try again.',
                        'error_type': 'email_error'
                    }), 500
            except Exception as e:
                logger.error(f"Error sending email: {str(e)}")
                return jsonify({
                    'success': False,
                    'message': 'Error sending email. Please try again.',
                    'error_type': 'email_error'
                }), 500

        # Check if we have CBCT files or more than 5 files
        has_cbct_files = any('cbct' in key.lower() for key in all_s3_keys)
        
        if has_cbct_files:
            # For CBCT files, always split into multiple ZIPs (regardless of count)
            return create_cbct_zip_files(all_s3_keys, recipient_email, user_message, patient_id=patient_id)
        elif len(all_s3_keys) > 3:
            # For non-CBCT files with more than 3 files, create a single ZIP
            return create_single_zip_file(all_s3_keys, recipient_email, user_message, patient_id=patient_id)
        else:
            # For 3 or fewer non-CBCT files, this should not reach here
            # as the individual links logic above should handle it
            return jsonify({
                'success': False,
                'message': 'Unexpected file count. Please try again.',
                'error_type': 'unexpected_count'
            }), 500

    except Exception as e:
        logger.error(f"Error in generate_presigned_links: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Server error: {str(e)}',
            'error_type': 'unexpected_error'
        }), 500

def create_cbct_zip_files(all_s3_keys, recipient_email, user_message, patient_id=None):
    """Create multiple ZIP files for CBCT files, splitting into 100MB chunks"""
    try:
        total_files = len(all_s3_keys)
        processed_files = 0
        
        # Calculate file sizes and group files into 100MB chunks
        file_groups = []
        current_group = []
        current_group_size = 0
        max_group_size = 100 * 1024 * 1024  # 100MB
        
        # First pass: collect all file sizes
        file_sizes = {}
        total_size = 0
        for i, key in enumerate(all_s3_keys):
            try:
                head = s3_client.head_object(Bucket=os.getenv('S3_BUCKET_NAME'), Key=key)
                file_size = head.get('ContentLength', 0)
                file_sizes[key] = file_size
                total_size += file_size
                processed_files += 1
                
                logger.info(f"Progress: {processed_files}/{total_files} files analyzed ({processed_files/total_files*100:.1f}%)")
            except Exception as e:
                logger.error(f"Error getting size for {key}: {str(e)}")
                file_sizes[key] = 0
        
        logger.info(f"Total CBCT files: {len(all_s3_keys)}, Total size: {total_size / (1024*1024):.1f}MB")
        
        # Second pass: distribute files more intelligently
        # Sort files by size (largest first) to avoid having tiny files in separate parts
        sorted_files = sorted(all_s3_keys, key=lambda k: file_sizes.get(k, 0), reverse=True)
        
        for key in sorted_files:
            file_size = file_sizes.get(key, 0)
            
            # If adding this file would exceed 100MB and we already have files in current group
            if current_group_size + file_size > max_group_size and current_group:
                # Start a new group
                file_groups.append(current_group)
                current_group = [key]
                current_group_size = file_size
            else:
                # Add to current group
                current_group.append(key)
                current_group_size += file_size
        
        # Add the last group if it has files
        if current_group:
            file_groups.append(current_group)
        
        # Filter out groups that are too small (less than 10MB) and merge them
        final_groups = []
        small_group = []
        small_group_size = 0
        
        for group in file_groups:
            group_size = sum(file_sizes.get(key, 0) for key in group)
            
            if group_size < 10 * 1024 * 1024:  # Less than 10MB
                # Add to small group for later merging
                small_group.extend(group)
                small_group_size += group_size
            else:
                # This is a substantial group, keep it
                if small_group:
                    # Merge small group with this substantial group
                    final_groups.append(small_group + group)
                    small_group = []
                    small_group_size = 0
                else:
                    final_groups.append(group)
        
        # Handle any remaining small group
        if small_group:
            if final_groups:
                # Merge with the last group
                final_groups[-1].extend(small_group)
            else:
                # If no groups exist, create one with the small files
                final_groups.append(small_group)
        
        logger.info(f"Split {len(all_s3_keys)} files into {len(final_groups)} groups")
        for i, group in enumerate(final_groups, 1):
            group_size = sum(file_sizes.get(key, 0) for key in group)
            logger.info(f"  Group {i}: {len(group)} files, {group_size / (1024*1024):.1f}MB")
        
        # Create ZIP files for each group
        zip_urls = []
        total_zip_groups = len(final_groups)
        total_files_to_zip = sum(len(group) for group in final_groups)
        files_zipped = 0
        
        for i, file_group in enumerate(final_groups, 1):
            if not file_group:  # Skip empty groups
                continue
                
            logger.info(f"Creating ZIP part {i}/{total_zip_groups} with {len(file_group)} files")
            zip_filename = f"patients/temp/cbct_shared_{int(time.time())}_part_{i}.zip"
            
            # Create ZIP in memory
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zip_file:
                for j, key in enumerate(file_group, 1):
                    try:
                        # Download file from S3
                        response = s3_client.get_object(Bucket=os.getenv('S3_BUCKET_NAME'), Key=key)
                        file_content = response['Body'].read()
                        
                        # Add to ZIP with relative path
                        filename = key.split('/')[-1] or "file"
                        zip_file.writestr(filename, file_content)
                        
                        files_zipped += 1
                        logger.info(f"ZIP {i}/{total_zip_groups}: File {j}/{len(file_group)} processed. Total progress: {files_zipped}/{total_files_to_zip} files zipped ({files_zipped/total_files_to_zip*100:.1f}%)")
                        
                    except Exception as e:
                        logger.error(f"Error processing {key}: {str(e)}")
                        continue
            
            # Upload ZIP to S3
            zip_buffer.seek(0)
            try:
                s3_client.upload_fileobj(
                    zip_buffer,
                    os.getenv('S3_BUCKET_NAME'),
                    zip_filename
                )

                presign_client = get_s3_client_for_presigning()
                presigned_url = presign_client.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': os.getenv('S3_BUCKET_NAME'), 'Key': zip_filename},
                    ExpiresIn=168 * 3600  # 7 days
                )
                short_url = shorten_url_with_tinyurl(presigned_url) or presigned_url
                zip_urls.append(short_url)
                
            except Exception as e:
                logger.error(f"Error uploading ZIP {zip_filename}: {str(e)}")
                return jsonify({
                    'success': False,
                    'message': 'Error creating ZIP files. Please try again.',
                    'error_type': 'zip_creation_error'
                }), 500

        # Send email with all ZIP links
        email_body = f"{user_message}\n\n" if user_message else "Here are your CBCT files:\n\n"
        email_body += f"Your CBCT files have been split into {len(zip_urls)} parts for easier download:\n\n"
        
        for i, url in enumerate(zip_urls, 1):
            email_body += f"Part {i}: {url}\n"
        
        email_body += f"\nTotal ZIP files: {len(zip_urls)}"
        email_body += f"\nOriginal files: {len(all_s3_keys)}"
        email_body += f"\nTotal size: {total_size / (1024*1024):.1f} MB"
        email_body += f"\n\nInstructions: Download all parts and extract each ZIP file to the same folder to combine all files."

        # Send email
        email_body_html = build_email_with_logo(email_body, title="CBCT Files Shared")
        email_sent = send_email_with_sendgrid(recipient_email, "CBCT Files shared from VizBriz", email_body_html, email_body, patient_id=patient_id, email_type='notification')
        if email_sent:
            return jsonify({
                'success': True,
                'message': f'CBCT files split into {len(zip_urls)} parts and email sent successfully',
                'zip_count': len(zip_urls),
                'total_files': len(all_s3_keys),
                'total_size_mb': total_size / (1024*1024),
                'progress_info': {
                    'total_files_analyzed': total_files,
                    'total_files_zipped': total_files_to_zip,
                    'files_zipped': files_zipped,
                    'zip_groups_created': total_zip_groups
                }
            })
        else:
            logger.error("Email sending failed")
            return jsonify({
                'success': False,
                'message': 'Error sending email. Please try again.',
                'error_type': 'email_error'
            }), 500

    except Exception as e:
        logger.error(f"Error in create_cbct_zip_files: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error creating CBCT ZIP files: {str(e)}',
            'error_type': 'zip_creation_error'
        }), 500



def create_single_zip_file(all_s3_keys, recipient_email, user_message, patient_id=None):
    """Create a single ZIP file for regular files"""
    try:
        zip_filename = f"patients/temp/files_shared_{int(time.time())}.zip"
        
        # Create ZIP in memory
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zip_file:
            for key in all_s3_keys:
                try:
                    # Download file from S3
                    response = s3_client.get_object(Bucket=os.getenv('S3_BUCKET_NAME'), Key=key)
                    file_content = response['Body'].read()
                    
                    # Add to ZIP with relative path
                    filename = key.split('/')[-1] or "file"
                    zip_file.writestr(filename, file_content)
                    
                except Exception as e:
                    logger.error(f"Error processing {key}: {str(e)}")
                    continue

        # Upload ZIP to S3
        zip_buffer.seek(0)
        try:
            s3_client.upload_fileobj(
                zip_buffer,
                os.getenv('S3_BUCKET_NAME'),
                zip_filename
            )

            presign_client = get_s3_client_for_presigning()
            presigned_url = presign_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': os.getenv('S3_BUCKET_NAME'), 'Key': zip_filename},
                ExpiresIn=168 * 3600  # 7 days
            )
            short_url = shorten_url_with_tinyurl(presigned_url) or presigned_url

            email_body = f"{user_message}\n\n" if user_message else "Here is your downloadable link:\n\n"
            email_body += f"Download ZIP: {short_url}"

            # Send email
            email_body_html = build_email_with_logo(email_body, title="Files Shared")
            email_sent = send_email_with_sendgrid(recipient_email, "Files shared from VizBriz", email_body_html, email_body, patient_id=patient_id, email_type='notification')
            if email_sent:
                return jsonify({
                    'success': True,
                    'message': 'ZIP file created and email sent successfully',
                    'url': short_url
                })
            else:
                logger.error("Email sending failed")
                return jsonify({
                    'success': False,
                    'message': 'Error sending email. Please try again.',
                    'error_type': 'email_error'
                }), 500
                
        except Exception as e:
            logger.error(f"Error uploading ZIP to S3: {str(e)}")
            return jsonify({
                'success': False,
                'message': 'Error uploading ZIP file. Please try again.',
                'error_type': 's3_upload_error'
            }), 500

    except Exception as e:
        logger.error(f"Error in create_single_zip_file: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error creating ZIP file: {str(e)}',
            'error_type': 'zip_creation_error'
        }), 500

@filemgmt.route('/partner_upload_presigned_url', methods=['POST'])
def partner_upload_presigned_url():
    """
    Generates a presigned URL specifically for the partner upload page.
    This endpoint does not require authentication.
    """
    try:
        logger.debug(f"Starting partner_upload_presigned_url with headers: {dict(request.headers)}")
        data = request.get_json()
        logger.debug(f"Partner upload request data: {data}")
        
        filename = data.get('filename')
        section = data.get('section')
        patient_id = data.get('patient_id')
        category = data.get('category')
        
        if not all([filename, section, patient_id, category]):
            logger.error("Missing required parameters for partner upload presigned URL generation.")
            return jsonify({'success': False, 'message': 'Missing required parameters'}), 400
        
        # Sanitize filename
        def secure_filename_custom(filename):
            # Allow Hebrew characters, alphanumeric, _, -, ., and / (for paths)
            filename = re.sub(r'[^\w\-.א-ת/]', '_', filename)
            return filename
            
        sanitized_filename = secure_filename_custom(filename)
        
        # Construct the S3 key
        s3_key = f"patients/{patient_id}/{category}/{section}/{sanitized_filename}"
        logger.debug(f"Generating presigned URL for partner upload page: {s3_key}")
        
        # Generate presigned POST URL
        presigned_url = s3_client.generate_presigned_post(
            Bucket=os.getenv('S3_BUCKET_NAME'),
            Key=s3_key,
            Fields={"acl": "private"},
            Conditions=[
                {"acl": "private"},
                ["content-length-range", 0, 1073741824]  # Limit to 1GB
            ],
            ExpiresIn=3600 * 24 * 7  # URL valid for 1 week
        )
        
        logger.debug(f"Generated partner upload presigned URL: {presigned_url['url']}")
        logger.debug(f"Fields for partner presigned request: {presigned_url['fields']}")
        
        return jsonify({
            'success': True,
            'url': presigned_url['url'],
            'fields': presigned_url['fields'],
            's3_key': s3_key
        })
        
    except Exception as e:
        logger.error(f"Error generating partner upload presigned URL: {e}")
        logger.exception("Detailed error trace:")
        return jsonify({'success': False, 'message': f"Error generating presigned URL: {str(e)}"}), 500

def anonymize_dataset(dataset):
    """Anonymize a DICOM dataset by removing patient information."""
    # Remove patient identifying information
    dataset.PatientName = "Anonymous"
    dataset.PatientID = "ID" + datetime.now().strftime("%Y%m%d%H%M%S")
    
    # Clear other identifying fields
    if "PatientBirthDate" in dataset:
        dataset.PatientBirthDate = ""
    if "PatientAddress" in dataset:
        dataset.PatientAddress = ""
    
    # Remove private tags
    dataset.remove_private_tags()
    
    # Handle other optional fields
    for tag in ["OtherPatientIDs", "OtherPatientNames"]:
        if tag in dataset:
            delattr(dataset, tag)
    
    return dataset


@filemgmt.route('/anonymize_page')
def anonymize_page():
    """Serve the main page with the upload form."""
    return render_template('anonymize_page.html')



@filemgmt.route('/anonymize', methods=['POST'])
def anonymize_dicom_zip():
    logging.debug("Received request to anonymize DICOM zip")
    if 'file' not in request.files:
        logging.error("No file part in the request")
        return "No file part", 400
    
    file = request.files['file']
    if file.filename == '':
        logging.error("No file selected")
        return "No selected file", 400

    # Create temporary directories
    temp_dir = tempfile.mkdtemp()
    anon_dir = tempfile.mkdtemp()
    logging.debug(f"Created temp_dir: {temp_dir} and anon_dir: {anon_dir}")
    
    try:
        # Save and extract the uploaded zip file
        zip_path = os.path.join(temp_dir, "original.zip")
        file.save(zip_path)
        logging.debug(f"Saved uploaded zip to {zip_path}")
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        logging.debug(f"Extracted zip file to {temp_dir}")
        
        # Process each DICOM file
        for root, _, files in os.walk(temp_dir):
            for filename in files:
                if is_dicom_file(filename):
                    file_path = os.path.join(root, filename)
                    logging.debug(f"Processing DICOM file: {file_path}")
                    try:
                        ds = pydicom.dcmread(file_path)
                        ds = anonymize_dataset(ds)
                        
                        # Create relative path for saving
                        rel_path = os.path.relpath(file_path, temp_dir)
                        anon_path = os.path.join(anon_dir, rel_path)
                        os.makedirs(os.path.dirname(anon_path), exist_ok=True)
                        ds.save_as(anon_path)
                        logging.debug(f"Saved anonymized DICOM to: {anon_path}")
                    except Exception as e:
                        logging.error(f"Error processing file {file_path}: {e}")
                        continue
        
        # Create a new zip file with anonymized DICOM files
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w') as zf:
            for root, _, files in os.walk(anon_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, anon_dir)
                    zf.write(file_path, arcname=arcname)
                    logging.debug(f"Added {file_path} as {arcname} to zip")
        
        memory_file.seek(0)
        logging.debug("Anonymization complete, sending file")
        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'anonymized_dicom_{datetime.now().strftime("%Y%m%d%H%M%S")}.zip'
        )
    
    finally:
        logging.debug("Cleaning up temporary directories")
        shutil.rmtree(temp_dir, ignore_errors=True)
        shutil.rmtree(anon_dir, ignore_errors=True)


@filemgmt.route('/partner_generate_presigned_url', methods=['POST'])
def partner_generate_presigned_url():
    """
    Generates a presigned URL for partner uploads to S3 without requiring login.
    """
    try:
        logger.debug(f"Starting partner_generate_presigned_url with headers: {dict(request.headers)}")
        data = request.get_json()
        logger.debug(f"Partner request data: {data}")
        
        filename = data.get('filename')
        section = data.get('section')
        patient_id = data.get('patient_id')
        category = data.get('category')
        token = data.get('token')  # Partner should provide this token
        
        if not all([filename, section, patient_id, category]):
            logger.error("Missing required parameters for presigned URL generation.")
            return jsonify({'success': False, 'message': 'Missing required parameters'}), 400
        
        # Sanitize filename
        def secure_filename_custom(filename):
            # Allow Hebrew characters, alphanumeric, _, -, ., and / (for paths)
            filename = re.sub(r'[^\w\-.א-ת/]', '_', filename)
            return filename
            
        sanitized_filename = secure_filename_custom(filename)
        
        # Construct the S3 key
        s3_key = f"patients/{patient_id}/{category}/{section}/{sanitized_filename}"
        logger.debug(f"Generating presigned URL for partner upload of file: {s3_key}")
        
        # Generate presigned POST URL
        presigned_url = s3_client.generate_presigned_post(
            Bucket=os.getenv('S3_BUCKET_NAME'),
            Key=s3_key,
            Fields={"acl": "private"},
            Conditions=[
                {"acl": "private"},
                ["content-length-range", 0, 1073741824]  # Limit to 1GB
            ],
            ExpiresIn=3600 * 24 * 7  # URL valid for 1 week
        )
        
        logger.debug(f"Generated presigned URL: {presigned_url['url']}")
        logger.debug(f"Fields for presigned request: {presigned_url['fields']}")
        
        return jsonify({
            'success': True,
            'url': presigned_url['url'],
            'fields': presigned_url['fields'],
            's3_key': s3_key
        })
        
    except Exception as e:
        logger.error(f"Error generating partner presigned URL: {e}")
        logger.exception("Detailed error trace:")
        return jsonify({'success': False, 'message': f"Error generating presigned URL: {str(e)}"}), 500
        

@filemgmt.route('/partner_upload_confirm', methods=['POST'])
def partner_upload_confirm():
    """
    Stores file metadata in the database after successful S3 upload by a partner.
    """
    try:
        logger.debug(f"Starting partner_upload_confirm with data: {request.json}")
        data = request.get_json()
        
        patient_id = data.get('patient_id')
        s3_key = data.get('s3_key')
        filename = data.get('filename')
        file_size = data.get('file_size')
        file_type = data.get('file_type')
        section = data.get('section')
        
        if not all([patient_id, s3_key, filename]):
            logger.error("Missing required parameters for file metadata storage")
            return jsonify({'success': False, 'message': 'Missing required parameters'}), 400
            
        # Create a new File record in the database
        new_file = File(
            name=filename,
            patient_id=patient_id,
            file_type=file_type or 'application/octet-stream',
            file_size=file_size or 0,
            s3_key=s3_key,
            category='imaging',
            subcategory=section
        )
        
        db.session.add(new_file)
        db.session.commit()
        logger.info(f"Successfully stored metadata for partner uploaded file: {filename} for patient {patient_id}")
        
        return jsonify({'success': True, 'message': 'File metadata saved successfully'})
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error storing file metadata from partner upload: {e}")
        logger.exception("Detailed error trace:")
        return jsonify({'success': False, 'message': f"Error saving file metadata: {str(e)}"}), 500
        

def validate_mpr_requirements(ds):
    """
    Validates if a DICOM dataset meets MPR requirements.
    Returns (bool, str) tuple: (is_valid, error_message)
    """
    try:
        logger.info("Starting MPR validation for DICOM dataset")
        
        # Check for required tags
        required_tags = [
            'InstanceNumber',
            'ImagePositionPatient',
            'ImageOrientationPatient',
            'PixelSpacing',
            'SeriesInstanceUID'
        ]
        
        missing_tags = [tag for tag in required_tags if not hasattr(ds, tag)]
        if missing_tags:
            logger.error(f"MPR validation failed: Missing required tags: {', '.join(missing_tags)}")
            return False, f"Missing required tags: {', '.join(missing_tags)}"

        # Check ImageOrientationPatient is valid (should be 6 values)
        if len(ds.ImageOrientationPatient) != 6:
            logger.error(f"MPR validation failed: Invalid ImageOrientationPatient (should have 6 values, got {len(ds.ImageOrientationPatient)})")
            return False, "Invalid ImageOrientationPatient (should have 6 values)"

        # Check PixelSpacing is valid (should be 2 values)
        if len(ds.PixelSpacing) != 2:
            logger.error(f"MPR validation failed: Invalid PixelSpacing (should have 2 values, got {len(ds.PixelSpacing)})")
            return False, "Invalid PixelSpacing (should have 2 values)"

        # Check ImagePositionPatient is valid (should be 3 values)
        if len(ds.ImagePositionPatient) != 3:
            logger.error(f"MPR validation failed: Invalid ImagePositionPatient (should have 3 values, got {len(ds.ImagePositionPatient)})")
            return False, "Invalid ImagePositionPatient (should have 3 values)"

        # Check transfer syntax is uncompressed
        if ds.file_meta.TransferSyntaxUID != pydicom.uid.ExplicitVRLittleEndian:
            logger.error(f"MPR validation failed: Transfer syntax is not uncompressed (got {ds.file_meta.TransferSyntaxUID})")
            return False, "Transfer syntax is not uncompressed (Explicit VR Little Endian)"

        logger.info("MPR validation passed successfully")
        return True, "Valid for MPR"

    except Exception as e:
        logger.error(f"Error during MPR validation: {str(e)}")
        return False, f"Error validating MPR requirements: {str(e)}"

def process_dicom_series(files):
    """
    Process a series of DICOM files to ensure they are MPR-ready.
    Returns (bool, str, list) tuple: (is_valid, error_message, sorted_files)
    """
    try:
        logger.info(f"Starting DICOM series processing for {len(files)} files")
        
        # Group files by SeriesInstanceUID
        series_dict = {}
        for file in files:
            ds = pydicom.dcmread(file, stop_before_pixels=True)
            if not hasattr(ds, 'SeriesInstanceUID'):
                logger.warning(f"File {file} has no SeriesInstanceUID, skipping")
                continue
            series_uid = ds.SeriesInstanceUID
            if series_uid not in series_dict:
                series_dict[series_uid] = []
            series_dict[series_uid].append(file)
            logger.info(f"Added file to series {series_uid}")

        logger.info(f"Found {len(series_dict)} distinct series")

        # Process each series
        for series_uid, series_files in series_dict.items():
            logger.info(f"Processing series {series_uid} with {len(series_files)} files")
            
            # Sort files by InstanceNumber
            try:
                series_files.sort(key=lambda x: pydicom.dcmread(x, stop_before_pixels=True).InstanceNumber)
                logger.info("Successfully sorted files by InstanceNumber")
            except Exception as e:
                logger.error(f"Failed to sort files by InstanceNumber: {str(e)}")
                return False, "Failed to sort files by InstanceNumber", []

            # Check spacing consistency
            first_ds = pydicom.dcmread(series_files[0], stop_before_pixels=True)
            first_spacing = first_ds.PixelSpacing
            first_orientation = first_ds.ImageOrientationPatient
            logger.info(f"First file spacing: {first_spacing}, orientation: {first_orientation}")

            for file in series_files[1:]:
                ds = pydicom.dcmread(file, stop_before_pixels=True)
                if ds.PixelSpacing != first_spacing:
                    logger.error(f"Inconsistent PixelSpacing in file {file}: {ds.PixelSpacing} vs {first_spacing}")
                    return False, "Inconsistent PixelSpacing across series", []
                if ds.ImageOrientationPatient != first_orientation:
                    logger.error(f"Inconsistent ImageOrientationPatient in file {file}: {ds.ImageOrientationPatient} vs {first_orientation}")
                    return False, "Inconsistent ImageOrientationPatient across series", []

            # Validate each file in the series
            for file in series_files:
                logger.info(f"Validating MPR requirements for file {file}")
                ds = pydicom.dcmread(file, stop_before_pixels=True)
                is_valid, error_msg = validate_mpr_requirements(ds)
                if not is_valid:
                    logger.error(f"File {file} failed MPR validation: {error_msg}")
                    return False, f"File {file} failed MPR validation: {error_msg}", []

        logger.info("All series passed MPR validation")
        return True, "Series is MPR-ready", series_files

    except Exception as e:
        logger.error(f"Error processing DICOM series: {str(e)}")
        return False, f"Error processing DICOM series: {str(e)}", []


@filemgmt.route('/trigger_cbct_page', methods=['GET'])
@login_required
def trigger_cbct_page():
    logger.info(f"🔗 CBCT trigger page accessed by user {current_user.email} (ID: {current_user.id})")
    logger.info(f"📅 Access time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"🌐 User agent: {request.headers.get('User-Agent', 'Unknown')}")
    logger.info(f"📍 Remote address: {request.remote_addr}")
    return render_template('trigger_cbct.html')


@filemgmt.route('/upload_files_to_orthanc', methods=['GET', 'POST'])
@login_required
def upload_files_to_orthanc():
    """
    New interface for uploading files from file system to Orthanc
    """
    if request.method == 'GET':
        logger.info(f"🔗 Orthanc upload page accessed by user {current_user.email} (ID: {current_user.id})")
        logger.info(f"📅 Access time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return render_template('upload_to_orthanc.html')
    
    try:
        logger.info(f"🚀 Orthanc upload request received from user {current_user.email}")
        logger.info(f"📋 Request method: {request.method}")
        logger.info(f"📋 Content type: {request.content_type}")
        logger.info(f"📋 Is JSON: {request.is_json}")
        
        # Get parameters
        patient_id = request.json.get('patient_id') if request.is_json else request.form.get('patient_id')
        file_paths = request.json.get('file_paths', []) if request.is_json else request.form.getlist('file_paths')
        
        logger.info(f"👤 Patient ID: {patient_id}")
        logger.info(f"📁 Number of files to process: {len(file_paths)}")
        logger.info(f"📁 File paths: {file_paths[:5]}{'...' if len(file_paths) > 5 else ''}")
        
        if not patient_id:
            logger.error("❌ Missing patient_id parameter")
            return jsonify({'success': False, 'message': 'Missing patient_id parameter'}), 400
        
        if not file_paths:
            logger.error("❌ No file paths provided")
            return jsonify({'success': False, 'message': 'No file paths provided'}), 400
        
        # Orthanc configuration
        ORTHANC_URL = "http://3.132.113.74:8042"
        ORTHANC_USERNAME = "vizbriz"
        ORTHANC_PASSWORD = "Vizbriz2025!"
        
        logger.info(f"🏥 Orthanc Server: {ORTHANC_URL}")
        logger.info(f"👤 Orthanc Username: {ORTHANC_USERNAME}")
        
        processed_files = 0
        errors = []
        skipped_files = 0
        
        logger.info(f"🚀 Starting processing of {len(file_paths)} files")
        
        for i, file_path in enumerate(file_paths, 1):
            try:
                logger.info(f"📥 Processing file {i}/{len(file_paths)}: {file_path}")
                
                # Check if file exists
                if not os.path.exists(file_path):
                    logger.error(f"   ❌ File does not exist: {file_path}")
                    errors.append({'file': file_path, 'error': 'File does not exist'})
                    continue
                
                # Check if it's a DICOM file
                if not is_dicom_file(file_path):
                    logger.warning(f"   ⚠️ Skipping non-DICOM file: {file_path}")
                    skipped_files += 1
                    continue
                
                # Get file size
                file_size = os.path.getsize(file_path)
                logger.info(f"   📊 File size: {file_size / (1024*1024):.2f} MB")
                
                # Read DICOM metadata
                logger.info(f"   🔍 Reading DICOM metadata...")
                ds = pydicom.dcmread(file_path, stop_before_pixels=True)
                tsuid = str(getattr(ds.file_meta, 'TransferSyntaxUID', ''))
                
                logger.info(f"   📋 DICOM Info: Modality={getattr(ds, 'Modality', 'Unknown')}, "
                          f"Manufacturer={getattr(ds, 'Manufacturer', 'Unknown')}, "
                          f"TransferSyntax={tsuid[:20]}...")
                
                # Check for compression
                compressed_uids = [
                    "1.2.840.10008.1.2.4.91", "1.2.840.10008.1.2.4.80",
                    "1.2.840.10008.1.2.4.70", "1.2.840.10008.1.2.5",
                    "1.2.840.10008.1.2.4.50", "1.2.840.10008.1.2.4.51",
                    "1.2.840.10008.1.2.4.57", "1.2.840.10008.1.2.4.90"
                ]
                
                upload_path = file_path
                if tsuid in compressed_uids:
                    logger.info(f"   🗜️ File is compressed. Decompressing...")
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".dcm") as converted_file:
                        converted_path = converted_file.name
                        if not convert_dicom_to_uncompressed(file_path, converted_path):
                            raise RuntimeError(f"Failed to decompress: {file_path}")
                        upload_path = converted_path
                        logger.info(f"   ✅ Decompression successful")
                else:
                    logger.info(f"   ✅ File is already uncompressed")
                
                # Check if this is a multi-frame DICOM
                logger.info(f"   🔍 Checking for multi-frame DICOM...")
                multiframe_analysis = analyze_dicom_for_multiframe(ds, upload_path)
                
                if multiframe_analysis['is_multiframe']:
                    logger.info(f"   🔧 Multi-frame DICOM detected! Splitting into individual files...")
                    logger.info(f"   📊 Frames to process: {multiframe_analysis['number_of_frames'] or 'Unknown'}")
                    
                    # Create a temporary S3 client for multi-frame processing
                    s3 = boto3.client('s3')
                    bucket_name = current_app.config['S3_BUCKET_NAME']
                    
                    # Split the multi-frame DICOM
                    split_files_processed = process_multiframe_file(
                        upload_path, 
                        patient_id, 
                        s3, 
                        bucket_name, 
                        ORTHANC_URL, 
                        ORTHANC_USERNAME, 
                        ORTHANC_PASSWORD,
                        os.path.basename(file_path)
                    )
                    
                    processed_files += split_files_processed['successful']
                    errors.extend(split_files_processed['errors'])
                    
                    logger.info(f"   ✅ Multi-frame processing complete: {split_files_processed['successful']} files uploaded")
                    
                else:
                    logger.info(f"   ✅ Single-frame DICOM - processing normally")
                    
                    # Load and update metadata
                    logger.info(f"   🔧 Updating DICOM metadata...")
                    ds = pydicom.dcmread(upload_path)
                    ds = ensure_minimal_dicom_compliance(ds, patient_id)
                    ds.save_as(upload_path)
                    logger.info(f"   ✅ Metadata updated for: {os.path.basename(file_path)}")
                    
                    # Read file content
                    with open(upload_path, 'rb') as f:
                        file_content = f.read()
                    
                    if not file_content:
                        raise ValueError(f"{file_path} is empty")
                    
                    logger.info(f"   🚀 Uploading to Orthanc server...")
                    logger.info(f"   📡 POST {ORTHANC_URL}/instances")
                    
                    response = requests.post(
                        f"{ORTHANC_URL}/instances",
                        data=file_content,
                        auth=HTTPBasicAuth(ORTHANC_USERNAME, ORTHANC_PASSWORD),
                        headers={
                            'Content-Type': 'application/dicom',
                            'Accept': 'application/json'
                        },
                        timeout=120
                    )
                    
                    if response.status_code in [200, 201, 202]:
                        logger.info(f"   ✅ Successfully uploaded to Orthanc (HTTP {response.status_code})")
                        if response.status_code == 200:
                            try:
                                response_data = response.json()
                                logger.info(f"   📋 Orthanc response: {response_data}")
                            except:
                                logger.info(f"   📋 Orthanc response: {response.text[:100]}...")
                        processed_files += 1
                    else:
                        logger.error(f"   ❌ Orthanc upload failed (HTTP {response.status_code})")
                        logger.error(f"   📋 Response: {response.text}")
                        raise RuntimeError(f"Upload failed (HTTP {response.status_code}): {response.text}")
                
                # Cleanup temporary converted file if it was created
                if upload_path != file_path and os.path.exists(upload_path):
                    try:
                        os.remove(upload_path)
                        logger.debug(f"   🗑️ Cleaned up temp file: {upload_path}")
                    except Exception as cleanup_err:
                        logger.warning(f"   ⚠️ Could not delete temp file {upload_path}: {cleanup_err}")
                
            except Exception as file_err:
                logger.error(f"   ❌ Error processing {file_path}: {file_err}")
                errors.append({'file': file_path, 'error': str(file_err)})
                logger.error(f"   📋 Error details: {type(file_err).__name__}: {str(file_err)}")
        
        # Final summary
        logger.info(f"🎉 Orthanc upload process completed!")
        logger.info(f"📊 Summary: {processed_files}/{len(file_paths)} files processed successfully")
        logger.info(f"📊 Skipped: {skipped_files} non-DICOM files")
        logger.info(f"📊 Errors: {len(errors)} files failed")
        
        if errors:
            logger.error(f"❌ Errors encountered:")
            for error in errors:
                logger.error(f"   - {error['file']}: {error['error']}")
        
        return jsonify({
            'success': True,
            'message': f'Successfully processed {processed_files} files',
            'processed_files': processed_files,
            'total_files': len(file_paths),
            'skipped_files': skipped_files,
            'errors': errors
        })
        
    except Exception as e:
        logger.error(f"❌ Error in Orthanc upload process: {str(e)}")
        logger.exception("Detailed error trace:")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500


def ensure_minimal_dicom_compliance(ds, patient_id):
    """Ensure required tags are present with default values."""
    if 'PatientName' not in ds or not ds.PatientName:
        ds.PatientName = f"Unknown - {patient_id}"
    else:
        ds.PatientName = f"{ds.PatientName} - {patient_id}"

    if 'PatientID' not in ds:
        ds.PatientID = str(patient_id)
    if 'StudyInstanceUID' not in ds:
        ds.StudyInstanceUID = pydicom.uid.generate_uid()
    if 'SeriesInstanceUID' not in ds:
        ds.SeriesInstanceUID = pydicom.uid.generate_uid()
    if 'SOPInstanceUID' not in ds:
        ds.SOPInstanceUID = pydicom.uid.generate_uid()
    if 'StudyDate' not in ds:
        ds.StudyDate = datetime.now().strftime('%Y%m%d')
    if 'Modality' not in ds:
        ds.Modality = "CBCT"

    return ds


@filemgmt.route('/process_cbct_for_orthanc', methods=['POST'])
@login_required
def process_cbct_for_orthanc():
    """
    Original CBCT processing route for backward compatibility
    """
    try:
        logger.info(f"🚀 CBCT processing request received from user {current_user.email}")
        logger.info(f"📋 Request content type: {request.content_type}")
        logger.info(f"📋 Request is JSON: {request.is_json}")
        
        # Get patient_id from JSON or query param
        patient_id = request.json.get('patient_id') if request.is_json else request.args.get('patient_id')
        logger.info(f"👤 Patient ID from request: {patient_id}")
        
        if not patient_id:
            logger.error("❌ Missing patient_id parameter")
            return jsonify({'success': False, 'message': 'Missing patient_id parameter'}), 400

        global cbct_progress
        with cbct_progress_lock:
            if patient_id in cbct_progress and cbct_progress[patient_id].get('status') in ['in_progress', 'processing']:
                return jsonify({'success': False, 'message': 'A process is already running for this patient.'}), 409
            cbct_progress[patient_id] = {
                'status': 'in_progress',
                'current_file': 0,
                'total_files': 0,
                'processed_files': 0,
                'errors': [],
                'multiframe_files': 0,
                'messages': [],
                'completed': False,
                'stop': False
            }

        prefix = f"patients/{patient_id}/imaging/cbct/"

        # Orthanc configuration
        ORTHANC_URL = "http://3.132.113.74:8042"
        ORTHANC_USERNAME = "vizbriz"
        ORTHANC_PASSWORD = "Vizbriz2025!"

        s3 = boto3.client('s3')
        bucket_name = current_app.config['S3_BUCKET_NAME']

        processed_files = 0
        errors = []

        # Get .dcm files
        logger.info(f"🔍 Searching for DICOM files in S3 prefix: {prefix}")
        cbct_progress[patient_id]['messages'].append('🔍 Searching for DICOM files in S3...')
        result = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
        dicom_keys = [obj['Key'] for obj in result.get('Contents', []) if is_dicom_file(obj['Key'])]

        cbct_progress[patient_id]['total_files'] = len(dicom_keys)
        logger.info(f"📁 Found {len(dicom_keys)} DICOM files for patient {patient_id}")
        cbct_progress[patient_id]['messages'].append(f'📁 Found {len(dicom_keys)} DICOM files for patient {patient_id}')
        
        if not dicom_keys:
            logger.warning(f"No DICOM files found for patient {patient_id} in prefix {prefix}")
            cbct_progress[patient_id]['status'] = 'error'
            cbct_progress[patient_id]['messages'].append(f'No DICOM files found for patient {patient_id}')
            return jsonify({
                'success': False, 
                'message': f'No DICOM files found for patient {patient_id}',
                'details': f'Searched in: {prefix}'
            }), 404

        compressed_uids = [
            "1.2.840.10008.1.2.4.91", "1.2.840.10008.1.2.4.80",
            "1.2.840.10008.1.2.4.70", "1.2.840.10008.1.2.5",
            "1.2.840.10008.1.2.4.50", "1.2.840.10008.1.2.4.51",
            "1.2.840.10008.1.2.4.57", "1.2.840.10008.1.2.4.90"
        ]

        logger.info(f"🚀 Starting processing of {len(dicom_keys)} DICOM files")
        logger.info(f"🏥 Orthanc Server: {ORTHANC_URL}")
        logger.info(f"👤 Orthanc Username: {ORTHANC_USERNAME}")
        cbct_progress[patient_id]['messages'].append(f'🚀 Starting processing of {len(dicom_keys)} DICOM files')
        cbct_progress[patient_id]['messages'].append(f'🏥 Orthanc Server: {ORTHANC_URL}')
        
        for i, dicom_key in enumerate(dicom_keys, 1):
            with cbct_progress_lock:
                if cbct_progress[patient_id].get('stop'):
                    cbct_progress[patient_id]['status'] = 'stopped'
                    cbct_progress[patient_id]['messages'].append('❌ Process was stopped by user.')
                    cbct_progress[patient_id]['completed'] = True
                    return jsonify({'success': False, 'message': 'Process stopped by user.'}), 200
            cbct_progress[patient_id]['current_file'] = i
            cbct_progress[patient_id]['status'] = 'processing'
            temp_path = converted_path = upload_path = None
            try:
                logger.info(f"📥 Processing file {i}/{len(dicom_keys)}: {dicom_key}")
                cbct_progress[patient_id]['messages'].append(f'📥 Processing file {i}/{len(dicom_keys)}: {os.path.basename(dicom_key)}')
                
                file_size_mb = s3.head_object(Bucket=bucket_name, Key=dicom_key)['ContentLength'] / (1024*1024)
                logger.info(f"   File size: {file_size_mb:.2f} MB")
                cbct_progress[patient_id]['messages'].append(f'   📊 File size: {file_size_mb:.2f} MB')

                with tempfile.NamedTemporaryFile(delete=False, suffix=".dcm") as temp_file:
                    temp_path = temp_file.name
                    logger.info(f"   📥 Downloading from S3...")
                    cbct_progress[patient_id]['messages'].append('   📥 Downloading from S3...')
                    response = s3.get_object(Bucket=bucket_name, Key=dicom_key)
                    for chunk in iter(lambda: response['Body'].read(8192), b''):
                        temp_file.write(chunk)

                logger.info(f"   🔍 Reading DICOM metadata...")
                cbct_progress[patient_id]['messages'].append('   🔍 Reading DICOM metadata...')
                ds = pydicom.dcmread(temp_path, stop_before_pixels=True)
                tsuid = str(getattr(ds.file_meta, 'TransferSyntaxUID', ''))
                
                modality = getattr(ds, 'Modality', 'Unknown')
                manufacturer = getattr(ds, 'Manufacturer', 'Unknown')
                logger.info(f"   📋 DICOM Info: Modality={modality}, Manufacturer={manufacturer}")
                cbct_progress[patient_id]['messages'].append(f'   📋 DICOM Info: Modality={modality}, Manufacturer={manufacturer}')

                if tsuid in compressed_uids:
                    logger.info(f"   🗜️ File is compressed. Decompressing...")
                    cbct_progress[patient_id]['messages'].append('   🗜️ File is compressed. Decompressing...')
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".dcm") as converted_file:
                        converted_path = converted_file.name
                        if not convert_dicom_to_uncompressed(temp_path, converted_path):
                            raise RuntimeError(f"Failed to decompress: {dicom_key}")
                        upload_path = converted_path
                        logger.info(f"   ✅ Decompression successful")
                        cbct_progress[patient_id]['messages'].append('   ✅ Decompression successful')
                else:
                    upload_path = temp_path
                    logger.info(f"   ✅ File is already uncompressed")
                    cbct_progress[patient_id]['messages'].append('   ✅ File is already uncompressed')

                # Check if this is a multi-frame DICOM
                logger.info(f"   🔍 Checking for multi-frame DICOM...")
                cbct_progress[patient_id]['messages'].append('   🔍 Checking for multi-frame DICOM...')
                multiframe_analysis = analyze_dicom_for_multiframe(ds, upload_path)
                
                if multiframe_analysis['is_multiframe']:
                    logger.info(f"   🔧 Multi-frame DICOM detected! Splitting into individual files...")
                    cbct_progress[patient_id]['messages'].append('   🔧 Multi-frame DICOM detected! Splitting into individual files...')
                    frames_count = multiframe_analysis['number_of_frames'] or 'Unknown'
                    logger.info(f"   📊 Frames to process: {frames_count}")
                    cbct_progress[patient_id]['messages'].append(f'   📊 Frames to process: {frames_count}')
                    
                    cbct_progress[patient_id]['multiframe_files'] += 1
                    
                    # Split the multi-frame DICOM
                    split_files_processed = process_multiframe_file(
                        upload_path, 
                        patient_id, 
                        s3, 
                        bucket_name, 
                        ORTHANC_URL, 
                        ORTHANC_USERNAME, 
                        ORTHANC_PASSWORD,
                        dicom_key
                    )
                    
                    cbct_progress[patient_id]['processed_files'] += split_files_processed['successful']
                    cbct_progress[patient_id]['errors'].extend(split_files_processed['errors'])
                    
                    successful_count = split_files_processed['successful']
                    logger.info(f"   ✅ Multi-frame processing complete: {successful_count} files uploaded")
                    cbct_progress[patient_id]['messages'].append(f'   ✅ Multi-frame processing complete: {successful_count} files uploaded')
                    
                else:
                    logger.info(f"   ✅ Single-frame DICOM - processing normally")
                    cbct_progress[patient_id]['messages'].append('   ✅ Single-frame DICOM - processing normally')
                    
                    # Load and update metadata
                    logger.info(f"   🔧 Updating DICOM metadata...")
                    cbct_progress[patient_id]['messages'].append('   🔧 Updating DICOM metadata...')
                    ds = pydicom.dcmread(upload_path)
                    ds = ensure_minimal_dicom_compliance(ds, patient_id)
                    ds.save_as(upload_path)
                    logger.info(f"   ✅ Metadata updated for: {dicom_key}")
                    cbct_progress[patient_id]['messages'].append('   ✅ Metadata updated')

                    with open(upload_path, 'rb') as f:
                        file_content = f.read()

                    if not file_content:
                        raise ValueError(f"{dicom_key} is empty")

                    logger.info(f"   🚀 Uploading to Orthanc server...")
                    cbct_progress[patient_id]['messages'].append('   🚀 Uploading to Orthanc server...')
                    logger.info(f"   📡 POST {ORTHANC_URL}/instances")
                    
                    response = requests.post(
                        f"{ORTHANC_URL}/instances",
                        data=file_content,
                        auth=HTTPBasicAuth(ORTHANC_USERNAME, ORTHANC_PASSWORD),
                        headers={
                            'Content-Type': 'application/dicom',
                            'Accept': 'application/json'
                        },
                        timeout=120
                    )

                    if response.status_code in [200, 201, 202]:
                        logger.info(f"   ✅ Successfully uploaded to Orthanc (HTTP {response.status_code})")
                        cbct_progress[patient_id]['messages'].append(f'   ✅ Successfully uploaded to Orthanc (HTTP {response.status_code})')
                        if response.status_code == 200:
                            try:
                                response_data = response.json()
                                logger.info(f"   📋 Orthanc response: {response_data}")
                            except:
                                logger.info(f"   📋 Orthanc response: {response.text[:100]}...")
                        cbct_progress[patient_id]['processed_files'] += 1
                    else:
                        logger.error(f"   ❌ Orthanc upload failed (HTTP {response.status_code})")
                        cbct_progress[patient_id]['messages'].append(f'   ❌ Orthanc upload failed (HTTP {response.status_code})')
                        logger.error(f"   📋 Response: {response.text}")
                        raise RuntimeError(f"Upload failed (HTTP {response.status_code}): {response.text}")

            except Exception as file_err:
                logger.error(f"   ❌ Error processing {dicom_key}: {file_err}")
                cbct_progress[patient_id]['messages'].append(f'   ❌ Error processing {os.path.basename(dicom_key)}: {str(file_err)}')
                cbct_progress[patient_id]['errors'].append({'file': dicom_key, 'error': str(file_err)})
                logger.error(f"   📋 Error details: {type(file_err).__name__}: {str(file_err)}")

            finally:
                # Cleanup temporary files
                for path in [temp_path, converted_path, upload_path]:
                    if path and os.path.exists(path):
                        try:
                            os.remove(path)
                            logger.debug(f"   🗑️ Cleaned up temp file: {path}")
                        except Exception as cleanup_err:
                            logger.warning(f"   ⚠️ Could not delete temp file {path}: {cleanup_err}")

        # Final summary
        logger.info(f"🎉 CBCT upload process completed!")
        cbct_progress[patient_id]['messages'].append('🎉 CBCT upload process completed!')
        processed_count = cbct_progress[patient_id]['processed_files']
        logger.info(f"📊 Summary: {processed_count}/{len(dicom_keys)} files processed successfully")
        cbct_progress[patient_id]['messages'].append(f'📊 Summary: {processed_count}/{len(dicom_keys)} files processed successfully')
        
        error_count = len(cbct_progress[patient_id]['errors'])
        if error_count > 0:
            logger.warning(f"⚠️ {error_count} files had errors")
            cbct_progress[patient_id]['messages'].append(f'⚠️ {error_count} files had errors')
        
        # Mark as completed
        cbct_progress[patient_id]['status'] = 'completed'
        cbct_progress[patient_id]['completed'] = True
        
        return jsonify({
            'success': True,
            'message': f'Successfully processed {processed_count} out of {len(dicom_keys)} DICOM files for patient {patient_id}',
            'processed_files': processed_count,
            'total_files': len(dicom_keys),
            'failed_files': error_count,
            'success_rate': round((processed_count / len(dicom_keys)) * 100, 1) if dicom_keys else 0,
            'errors': cbct_progress[patient_id]['errors'],
            'orthanc_server': ORTHANC_URL,
            'processing_time': f"Processed {len(dicom_keys)} files"
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Unhandled error in process_cbct_for_orthanc: {str(e)}")
        if patient_id in cbct_progress:
            cbct_progress[patient_id]['status'] = 'error'
            cbct_progress[patient_id]['messages'].append(f'❌ Unhandled error: {str(e)}')
        import traceback
        logger.error(f"📋 Full traceback: {traceback.format_exc()}")
        return jsonify({
            'success': False, 
            'message': f'Server error: {str(e)}',
            'error_type': type(e).__name__,
            'details': 'Check server logs for more information'
        }), 500


@filemgmt.route('/split_multiframe_dicom', methods=['POST'])
@login_required
def split_multiframe_dicom():
    """Split multi-frame DICOM files into individual DCM slices"""
    
    try:
        logger.info(f"Multi-frame DICOM split request received from user {current_user.email}")
        
        # Get parameters from request
        patient_id = request.json.get('patient_id')
        source_file = request.json.get('source_file')
        
        logger.info(f"Patient ID: {patient_id}, Source file: {source_file}")
        
        if not patient_id or not source_file:
            return jsonify({'success': False, 'message': 'Missing patient_id or source_file parameter'}), 400
        
        # Initialize S3 client
        s3 = boto3.client('s3')
        bucket_name = current_app.config['S3_BUCKET_NAME']
        
        # Create temporary directory for processing
        with tempfile.TemporaryDirectory() as temp_dir:
            logger.info(f"Created temporary directory: {temp_dir}")
            
            # Download the source multi-frame DICOM file
            input_path = os.path.join(temp_dir, 'input.dcm')
            logger.info(f"Downloading {source_file} from S3...")
            
            try:
                s3.download_file(bucket_name, source_file, input_path)
                logger.info(f"Successfully downloaded {source_file}")
            except Exception as e:
                logger.error(f"Failed to download {source_file}: {str(e)}")
                return jsonify({'success': False, 'message': f'Failed to download source file: {str(e)}'}), 404
            
            # Load the DICOM file
            try:
                ds = pydicom.dcmread(input_path)
                logger.info(f"Successfully loaded DICOM file")
            except Exception as e:
                logger.error(f"Failed to read DICOM file: {str(e)}")
                return jsonify({'success': False, 'message': f'Failed to read DICOM file: {str(e)}'}), 400
            
            # Validate it's a multi-frame image
            multiframe_analysis = analyze_dicom_for_multiframe(ds, input_path)
            logger.info(f"Multi-frame analysis: {multiframe_analysis}")
            
            if not multiframe_analysis['is_multiframe']:
                logger.error(f"Not a multi-frame DICOM: {multiframe_analysis['reason']}")
                return jsonify({
                    'success': False, 
                    'message': f'Not a multi-frame DICOM: {multiframe_analysis["reason"]}',
                    'analysis': multiframe_analysis
                }), 400
            
            # Get frames
            try:
                frames = ds.pixel_array
                logger.info(f"Found {frames.shape[0]} frames")
            except Exception as e:
                logger.error(f"Failed to extract pixel array: {str(e)}")
                return jsonify({'success': False, 'message': f'Failed to extract pixel data: {str(e)}'}), 400
            
            # Create output directory in S3
            output_prefix = f"patients/{patient_id}/imaging/cbct/split_{int(time.time())}/"
            created_files = 0
            
            # Process each frame with per-frame geometry for MPR compatibility
            for i in range(frames.shape[0]):
                try:
                    geometry = get_per_frame_geometry(ds, i, frames.shape[0])
                    new_ds = _prepare_single_frame_from_multiframe(
                        ds, i, frames[i], geometry, patient_id=None
                    )
                    # Save to temporary file
                    temp_output_path = os.path.join(temp_dir, f"slice_{i+1:03}.dcm")
                    new_ds.save_as(temp_output_path)
                    
                    # Upload to S3
                    s3_key = f"{output_prefix}slice_{i+1:03}.dcm"
                    s3.upload_file(temp_output_path, bucket_name, s3_key)
                    
                    created_files += 1
                    logger.info(f"Created and uploaded slice {i+1}")
                    
                except Exception as e:
                    logger.error(f"Failed to process frame {i+1}: {str(e)}")
                    continue
            
            if created_files == 0:
                return jsonify({'success': False, 'message': 'Failed to create any individual DCM files'}), 500
            
            logger.info(f"Successfully created {created_files} individual DCM files")
            
            return jsonify({
                'success': True,
                'message': f'Successfully split multi-frame DICOM into {created_files} individual files',
                'created_files': created_files,
                'output_directory': output_prefix,
                'original_frames': frames.shape[0]
            }), 200
            
    except Exception as e:
        logger.error(f"Unhandled error in split_multiframe_dicom: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': f'Server error: {str(e)}'}), 500

# Global variable to store progress information
cbct_progress = {}

@filemgmt.route('/process_cbct_for_orthanc_stream', methods=['GET'])
@login_required
def process_cbct_for_orthanc_stream():
    """
    CBCT processing route with real-time progress updates using Server-Sent Events
    """
    def generate():
        try:
            # Get patient_id from query parameters
            patient_id = request.args.get('patient_id')
            
            if not patient_id:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Missing patient_id parameter'})}\n\n"
                return

            # Initialize progress tracking
            global cbct_progress
            cbct_progress[patient_id] = {
                'status': 'starting',
                'current_file': 0,
                'total_files': 0,
                'processed_files': 0,
                'errors': [],
                'multiframe_files': 0,
                'messages': [],
                'completed': False
            }

            # Send initial status
            yield f"data: {json.dumps({'type': 'status', 'message': '🚀 Starting CBCT processing and upload...'})}\n\n"
            cbct_progress[patient_id]['messages'].append('🚀 Starting CBCT processing and upload...')
            
            yield f"data: {json.dumps({'type': 'status', 'message': f'👤 Processing patient ID: {patient_id}'})}\n\n"
            cbct_progress[patient_id]['messages'].append(f'👤 Processing patient ID: {patient_id}')

            prefix = f"patients/{patient_id}/imaging/cbct/"

            # Orthanc configuration
            ORTHANC_URL = "http://3.132.113.74:8042"
            ORTHANC_USERNAME = "vizbriz"
            ORTHANC_PASSWORD = "Vizbriz2025!"

            s3 = boto3.client('s3')
            bucket_name = current_app.config['S3_BUCKET_NAME']

            # Get .dcm files
            yield f"data: {json.dumps({'type': 'status', 'message': f'🔍 Searching for DICOM files in S3...'})}\n\n"
            cbct_progress[patient_id]['messages'].append('🔍 Searching for DICOM files in S3...')
            
            result = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
            dicom_keys = [obj['Key'] for obj in result.get('Contents', []) if is_dicom_file(obj['Key'])]

            cbct_progress[patient_id]['total_files'] = len(dicom_keys)
            yield f"data: {json.dumps({'type': 'status', 'message': f'📁 Found {len(dicom_keys)} DICOM files for patient {patient_id}'})}\n\n"
            cbct_progress[patient_id]['messages'].append(f'📁 Found {len(dicom_keys)} DICOM files for patient {patient_id}')
            
            if not dicom_keys:
                yield f"data: {json.dumps({'type': 'error', 'message': f'No DICOM files found for patient {patient_id}'})}\n\n"
                cbct_progress[patient_id]['status'] = 'error'
                cbct_progress[patient_id]['messages'].append(f'No DICOM files found for patient {patient_id}')
                return

            compressed_uids = [
                "1.2.840.10008.1.2.4.91", "1.2.840.10008.1.2.4.80",
                "1.2.840.10008.1.2.4.70", "1.2.840.10008.1.2.5",
                "1.2.840.10008.1.2.4.50", "1.2.840.10008.1.2.4.51",
                "1.2.840.10008.1.2.4.57", "1.2.840.10008.1.2.4.90"
            ]

            yield f"data: {json.dumps({'type': 'status', 'message': f'🚀 Starting processing of {len(dicom_keys)} DICOM files'})}\n\n"
            cbct_progress[patient_id]['messages'].append(f'🚀 Starting processing of {len(dicom_keys)} DICOM files')
            
            yield f"data: {json.dumps({'type': 'status', 'message': f'🏥 Orthanc Server: {ORTHANC_URL}'})}\n\n"
            cbct_progress[patient_id]['messages'].append(f'🏥 Orthanc Server: {ORTHANC_URL}')
            
            for i, dicom_key in enumerate(dicom_keys, 1):
                with cbct_progress_lock:
                    if cbct_progress[patient_id].get('stop'):
                        cbct_progress[patient_id]['status'] = 'stopped'
                        cbct_progress[patient_id]['messages'].append('❌ Process was stopped by user.')
                        cbct_progress[patient_id]['completed'] = True
                        return jsonify({'success': False, 'message': 'Process stopped by user.'}), 200
                temp_path = converted_path = upload_path = None
                try:
                    cbct_progress[patient_id]['current_file'] = i
                    cbct_progress[patient_id]['status'] = 'processing'
                    
                    yield f"data: {json.dumps({'type': 'progress', 'current': i, 'total': len(dicom_keys), 'message': f'📥 Processing file {i}/{len(dicom_keys)}: {os.path.basename(dicom_key)}'})}\n\n"
                    cbct_progress[patient_id]['messages'].append(f'📥 Processing file {i}/{len(dicom_keys)}: {os.path.basename(dicom_key)}')
                    
                    # Get file size
                    file_size_mb = s3.head_object(Bucket=bucket_name, Key=dicom_key)['ContentLength'] / (1024*1024)
                    yield f"data: {json.dumps({'type': 'status', 'message': f'   📊 File size: {file_size_mb:.2f} MB'})}\n\n"
                    cbct_progress[patient_id]['messages'].append(f'   📊 File size: {file_size_mb:.2f} MB')

                    with tempfile.NamedTemporaryFile(delete=False, suffix=".dcm") as temp_file:
                        temp_path = temp_file.name
                        yield f"data: {json.dumps({'type': 'status', 'message': f'   📥 Downloading from S3...'})}\n\n"
                        cbct_progress[patient_id]['messages'].append('   📥 Downloading from S3...')
                        response = s3.get_object(Bucket=bucket_name, Key=dicom_key)
                        for chunk in iter(lambda: response['Body'].read(8192), b''):
                            temp_file.write(chunk)

                    yield f"data: {json.dumps({'type': 'status', 'message': f'   🔍 Reading DICOM metadata...'})}\n\n"
                    cbct_progress[patient_id]['messages'].append('   🔍 Reading DICOM metadata...')
                    ds = pydicom.dcmread(temp_path, stop_before_pixels=True)
                    tsuid = str(getattr(ds.file_meta, 'TransferSyntaxUID', ''))
                    
                    modality = getattr(ds, 'Modality', 'Unknown')
                    manufacturer = getattr(ds, 'Manufacturer', 'Unknown')
                    yield f"data: {json.dumps({'type': 'status', 'message': f'   📋 DICOM Info: Modality={modality}, Manufacturer={manufacturer}'})}\n\n"
                    cbct_progress[patient_id]['messages'].append(f'   📋 DICOM Info: Modality={modality}, Manufacturer={manufacturer}')

                    if tsuid in compressed_uids:
                        yield f"data: {json.dumps({'type': 'status', 'message': f'   🗜️ File is compressed. Decompressing...'})}\n\n"
                        cbct_progress[patient_id]['messages'].append('   🗜️ File is compressed. Decompressing...')
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".dcm") as converted_file:
                            converted_path = converted_file.name
                            if not convert_dicom_to_uncompressed(temp_path, converted_path):
                                raise RuntimeError(f"Failed to decompress: {dicom_key}")
                            upload_path = converted_path
                            yield f"data: {json.dumps({'type': 'status', 'message': f'   ✅ Decompression successful'})}\n\n"
                            cbct_progress[patient_id]['messages'].append('   ✅ Decompression successful')
                    else:
                        upload_path = temp_path
                        yield f"data: {json.dumps({'type': 'status', 'message': f'   ✅ File is already uncompressed'})}\n\n"
                        cbct_progress[patient_id]['messages'].append('   ✅ File is already uncompressed')

                    # Check if this is a multi-frame DICOM
                    yield f"data: {json.dumps({'type': 'status', 'message': f'   🔍 Checking for multi-frame DICOM...'})}\n\n"
                    cbct_progress[patient_id]['messages'].append('   🔍 Checking for multi-frame DICOM...')
                    multiframe_analysis = analyze_dicom_for_multiframe(ds, upload_path)
                    
                    if multiframe_analysis['is_multiframe']:
                        yield f"data: {json.dumps({'type': 'status', 'message': f'   🔧 Multi-frame DICOM detected! Splitting into individual files...'})}\n\n"
                        cbct_progress[patient_id]['messages'].append('   🔧 Multi-frame DICOM detected! Splitting into individual files...')
                        frames_count = multiframe_analysis["number_of_frames"] or "Unknown"
                        yield f"data: {json.dumps({'type': 'status', 'message': f'   📊 Frames to process: {frames_count}'})}\n\n"
                        cbct_progress[patient_id]['messages'].append(f'   📊 Frames to process: {frames_count}')
                        
                        cbct_progress[patient_id]['multiframe_files'] += 1
                        
                        # Split the multi-frame DICOM
                        split_files_processed = process_multiframe_file(
                            upload_path, 
                            patient_id, 
                            s3, 
                            bucket_name, 
                            ORTHANC_URL, 
                            ORTHANC_USERNAME, 
                            ORTHANC_PASSWORD,
                            dicom_key
                        )
                        
                        cbct_progress[patient_id]['processed_files'] += split_files_processed['successful']
                        cbct_progress[patient_id]['errors'].extend(split_files_processed['errors'])
                        
                        successful_count = split_files_processed['successful']
                        yield f"data: {json.dumps({'type': 'status', 'message': f'   ✅ Multi-frame processing complete: {successful_count} files uploaded'})}\n\n"
                        cbct_progress[patient_id]['messages'].append(f'   ✅ Multi-frame processing complete: {successful_count} files uploaded')
                        
                    else:
                        yield f"data: {json.dumps({'type': 'status', 'message': f'   ✅ Single-frame DICOM - processing normally'})}\n\n"
                        cbct_progress[patient_id]['messages'].append('   ✅ Single-frame DICOM - processing normally')
                        
                        # Load and update metadata
                        yield f"data: {json.dumps({'type': 'status', 'message': f'   🔧 Updating DICOM metadata...'})}\n\n"
                        cbct_progress[patient_id]['messages'].append('   🔧 Updating DICOM metadata...')
                        ds = pydicom.dcmread(upload_path)
                        ds = ensure_minimal_dicom_compliance(ds, patient_id)
                        ds.save_as(upload_path)
                        yield f"data: {json.dumps({'type': 'status', 'message': f'   ✅ Metadata updated'})}\n\n"
                        cbct_progress[patient_id]['messages'].append('   ✅ Metadata updated')

                        with open(upload_path, 'rb') as f:
                            file_content = f.read()

                        if not file_content:
                            raise ValueError(f"{dicom_key} is empty")

                        yield f"data: {json.dumps({'type': 'status', 'message': f'   🚀 Uploading to Orthanc server...'})}\n\n"
                        cbct_progress[patient_id]['messages'].append('   🚀 Uploading to Orthanc server...')
                        
                        response = requests.post(
                            f"{ORTHANC_URL}/instances",
                            data=file_content,
                            auth=HTTPBasicAuth(ORTHANC_USERNAME, ORTHANC_PASSWORD),
                            headers={
                                'Content-Type': 'application/dicom',
                                'Accept': 'application/json'
                            },
                            timeout=120
                        )

                        if response.status_code in [200, 201, 202]:
                            yield f"data: {json.dumps({'type': 'status', 'message': f'   ✅ Successfully uploaded to Orthanc (HTTP {response.status_code})'})}\n\n"
                            cbct_progress[patient_id]['messages'].append(f'   ✅ Successfully uploaded to Orthanc (HTTP {response.status_code})')
                            cbct_progress[patient_id]['processed_files'] += 1
                        else:
                            yield f"data: {json.dumps({'type': 'status', 'message': f'   ❌ Orthanc upload failed (HTTP {response.status_code})'})}\n\n"
                            cbct_progress[patient_id]['messages'].append(f'   ❌ Orthanc upload failed (HTTP {response.status_code})')
                            raise RuntimeError(f"Upload failed (HTTP {response.status_code}): {response.text}")

                except Exception as file_err:
                    yield f"data: {json.dumps({'type': 'status', 'message': f'   ❌ Error processing {os.path.basename(dicom_key)}: {str(file_err)}'})}\n\n"
                    cbct_progress[patient_id]['messages'].append(f'   ❌ Error processing {os.path.basename(dicom_key)}: {str(file_err)}')
                    cbct_progress[patient_id]['errors'].append({'file': dicom_key, 'error': str(file_err)})

                finally:
                    # Cleanup temporary files
                    for path in [temp_path, converted_path, upload_path]:
                        if path and os.path.exists(path):
                            try:
                                os.remove(path)
                            except Exception as cleanup_err:
                                pass

            # Final summary
            yield f"data: {json.dumps({'type': 'status', 'message': f'🎉 CBCT upload process completed!'})}\n\n"
            cbct_progress[patient_id]['messages'].append('🎉 CBCT upload process completed!')
            processed_count = cbct_progress[patient_id]['processed_files']
            yield f"data: {json.dumps({'type': 'status', 'message': f'📊 Summary: {processed_count}/{len(dicom_keys)} files processed successfully'})}\n\n"
            cbct_progress[patient_id]['messages'].append(f'📊 Summary: {processed_count}/{len(dicom_keys)} files processed successfully')
            
            error_count = len(cbct_progress[patient_id]['errors'])
            if error_count > 0:
                yield f"data: {json.dumps({'type': 'status', 'message': f'⚠️ {error_count} files had errors'})}\n\n"
                cbct_progress[patient_id]['messages'].append(f'⚠️ {error_count} files had errors')
            
            # Mark as completed
            cbct_progress[patient_id]['status'] = 'completed'
            cbct_progress[patient_id]['completed'] = True
            
            # Send final result
            yield f"data: {json.dumps({'type': 'complete', 'success': True, 'processed_files': processed_count, 'total_files': len(dicom_keys), 'failed_files': error_count, 'multiframe_files': cbct_progress[patient_id]['multiframe_files'], 'errors': cbct_progress[patient_id]['errors']})}\n\n"
            
        except Exception as e:
            if patient_id in cbct_progress:
                cbct_progress[patient_id]['status'] = 'error'
                cbct_progress[patient_id]['messages'].append(f'❌ Unhandled error: {str(e)}')
            yield f"data: {json.dumps({'type': 'error', 'message': f'❌ Unhandled error: {str(e)}'})}\n\n"

    return Response(generate(), mimetype='text/plain')

@filemgmt.route('/cbct_progress/<patient_id>', methods=['GET'])
@login_required
def get_cbct_progress(patient_id):
    """
    Get the current progress of CBCT processing for a patient
    """
    global cbct_progress
    
    if patient_id not in cbct_progress:
        return jsonify({
            'status': 'not_found',
            'message': 'No processing found for this patient'
        }), 404
    
    progress = cbct_progress[patient_id]
    
    return jsonify({
        'status': progress['status'],
        'current_file': progress['current_file'],
        'total_files': progress['total_files'],
        'processed_files': progress['processed_files'],
        'errors': progress['errors'],
        'multiframe_files': progress['multiframe_files'],
        'messages': progress['messages'],
        'completed': progress['completed'],
        'percentage': (progress['current_file'] / progress['total_files'] * 100) if progress['total_files'] > 0 else 0
    })

# Add a stop flag to cbct_progress and a lock to prevent double processing
cbct_progress_lock = Lock()

@filemgmt.route('/stop_cbct_process/<patient_id>', methods=['POST'])
@login_required
def stop_cbct_process(patient_id):
    global cbct_progress
    with cbct_progress_lock:
        if patient_id in cbct_progress:
            cbct_progress[patient_id]['stop'] = True
            cbct_progress[patient_id]['status'] = 'stopped'
            cbct_progress[patient_id]['messages'].append('❌ Process was stopped by user.')
            return jsonify({'success': True, 'message': 'Process stopped.'})
        else:
            return jsonify({'success': False, 'message': 'No process found for this patient.'}), 404



