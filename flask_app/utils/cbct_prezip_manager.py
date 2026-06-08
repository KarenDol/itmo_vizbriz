"""
CBCT Pre-Zip Manager

Simple module to create and manage pre-zipped CBCT folders.
After CBCT files are uploaded, this creates a ZIP at a predictable S3 location
so downloads and shares don't need to zip on-the-fly (avoiding timeouts for large folders).

Pre-zip location: patients/{patient_id}/imaging/cbct_prezip/{folder_name}.zip
"""

import os
import logging
import tempfile
import zipfile
import threading
from typing import Optional, List, Tuple

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)


def get_s3_client():
    """Get configured S3 client."""
    region = os.environ.get('AWS_REGION', 'us-east-1')
    return boto3.client('s3', region_name=region, config=Config(signature_version='s3v4'))


def _get_s3_client_for_presigning():
    """Get S3 client for presigning (uses long-lived credentials when set)."""
    from flask_app.utils.s3_presign_client import get_s3_client_for_presigning
    return get_s3_client_for_presigning()


def get_bucket_name():
    """Get S3 bucket name from environment or Flask config."""
    bucket = os.environ.get('S3_BUCKET_NAME')
    if not bucket:
        try:
            from flask import current_app
            bucket = current_app.config.get('S3_BUCKET_NAME')
        except:
            pass
    if not bucket:
        # Fallback to known bucket name
        bucket = 'vizbrizpatients'
    return bucket


def get_prezip_s3_key(patient_id: int, folder_name: str) -> str:
    """Get the predictable S3 key for a pre-zipped CBCT folder."""
    return f"patients/{patient_id}/imaging/cbct_prezip/{folder_name}.zip"


def prezip_exists(patient_id: int, folder_name: str) -> bool:
    """Check if a pre-zipped file exists for this folder."""
    s3_client = get_s3_client()
    bucket_name = get_bucket_name()
    prezip_key = get_prezip_s3_key(patient_id, folder_name)
    
    try:
        s3_client.head_object(Bucket=bucket_name, Key=prezip_key)
        return True
    except:
        return False


def get_prezip_url(patient_id: int, folder_name: str, expires_in: int = 3600) -> Optional[str]:
    """
    Get a presigned URL for downloading the pre-zipped file.
    Returns None if pre-zip doesn't exist.
    Uses long-lived IAM credentials when set (for 7-day share links).
    """
    s3_client = get_s3_client()
    bucket_name = get_bucket_name()
    prezip_key = get_prezip_s3_key(patient_id, folder_name)

    logger.info(f"Checking for pre-zip: bucket={bucket_name}, key={prezip_key}")

    try:
        # Check if exists first
        s3_client.head_object(Bucket=bucket_name, Key=prezip_key)
        logger.info(f"Pre-zip found: {prezip_key}")

        # Use presigning client for URL generation (long-lived creds when set)
        presign_client = _get_s3_client_for_presigning()
        import urllib.parse
        zip_filename = f"{folder_name}.zip"
        encoded_filename = urllib.parse.quote(zip_filename, safe='')
        url = presign_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': bucket_name,
                'Key': prezip_key,
                'ResponseContentDisposition': f"attachment; filename*=UTF-8''{encoded_filename}"
            },
            ExpiresIn=expires_in
        )
        logger.info(f"Generated presigned URL for pre-zip: {folder_name}")
        return url
    except Exception as e:
        logger.info(f"Pre-zip not found for patient {patient_id}, folder {folder_name}: {e}")
        return None


def create_prezip(patient_id: int, folder_name: str) -> Tuple[bool, str]:
    """
    Create a pre-zipped file for a CBCT folder.
    
    Args:
        patient_id: Patient ID
        folder_name: CBCT folder name
        
    Returns:
        Tuple of (success, message)
    """
    s3_client = get_s3_client()
    bucket_name = get_bucket_name()
    
    if not bucket_name:
        logger.error("S3_BUCKET_NAME environment variable not set!")
        return False, "S3_BUCKET_NAME not set"
    
    # List all files in the CBCT folder
    prefix = f"patients/{patient_id}/imaging/cbct/{folder_name}/"
    prezip_key = get_prezip_s3_key(patient_id, folder_name)
    
    logger.info(f"Creating pre-zip for patient {patient_id}, folder {folder_name}")
    logger.info(f"  Source prefix: {prefix}")
    logger.info(f"  Target key: {prezip_key}")
    
    # Get all objects in the folder
    s3_keys = []
    paginator = s3_client.get_paginator('list_objects_v2')
    
    try:
        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
            if 'Contents' in page:
                for obj in page['Contents']:
                    s3_keys.append(obj['Key'])
    except Exception as e:
        return False, f"Error listing folder contents: {e}"
    
    if not s3_keys:
        return False, f"No files found in folder {folder_name}"
    
    logger.info(f"Found {len(s3_keys)} files to zip")
    
    # Create temporary ZIP file
    temp_dir = tempfile.mkdtemp()
    temp_zip_path = os.path.join(temp_dir, f"{folder_name}.zip")
    
    try:
        with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for i, s3_key in enumerate(s3_keys):
                try:
                    response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
                    file_content = response['Body'].read()
                    
                    # Relative path within the folder
                    relative_path = s3_key.replace(f"patients/{patient_id}/imaging/cbct/", "")
                    zf.writestr(relative_path, file_content)
                    
                    if (i + 1) % 100 == 0:
                        logger.info(f"  Zipped {i + 1}/{len(s3_keys)} files...")
                        
                except Exception as e:
                    logger.warning(f"Error adding {s3_key} to zip: {e}")
                    continue
        
        # Upload to S3
        zip_size = os.path.getsize(temp_zip_path)
        logger.info(f"Uploading pre-zip ({zip_size / (1024*1024):.2f} MB) to {prezip_key}")
        
        with open(temp_zip_path, 'rb') as f:
            s3_client.upload_fileobj(
                f,
                bucket_name,
                prezip_key,
                ExtraArgs={'ContentType': 'application/zip'}
            )
        
        logger.info(f"Pre-zip created successfully: {prezip_key}")
        return True, prezip_key
        
    except Exception as e:
        logger.error(f"Error creating pre-zip: {e}")
        return False, str(e)
        
    finally:
        # Clean up
        try:
            if os.path.exists(temp_zip_path):
                os.remove(temp_zip_path)
            os.rmdir(temp_dir)
        except:
            pass


def trigger_prezip_background(patient_id: int, app=None):
    """
    Trigger pre-zipping of all CBCT folders for a patient in a background thread.
    Called after CBCT files are uploaded.
    """
    def _run_prezip():
        try:
            with app.app_context():
                logger.info(f"[Background] Starting pre-zip for patient {patient_id}")
                
                # Get list of CBCT folders from S3
                s3_client = get_s3_client()
                bucket_name = get_bucket_name()
                prefix = f"patients/{patient_id}/imaging/cbct/"
                
                logger.info(f"[Background] Searching for CBCT files with prefix: {prefix}")
                
                # Find unique folder names by listing all objects and extracting folder names
                # This is more reliable than using CommonPrefixes
                folders = set()
                paginator = s3_client.get_paginator('list_objects_v2')
                
                for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            key = obj['Key']
                            # Extract folder name from key like "patients/123/imaging/cbct/FolderName/..."
                            # Skip the prefix and get the first path component
                            relative_path = key[len(prefix):]
                            if '/' in relative_path:
                                folder_name = relative_path.split('/')[0]
                                if folder_name and folder_name != 'cbct_prezip':
                                    folders.add(folder_name)
                            else:
                                # File directly in cbct/ without subfolder - use filename as folder
                                logger.debug(f"[Background] File without folder: {key}")
                
                if not folders:
                    logger.info(f"[Background] No CBCT folders found for patient {patient_id}")
                    return
                
                logger.info(f"[Background] Found {len(folders)} CBCT folders to pre-zip: {folders}")
                
                for folder_name in folders:
                    # Skip if pre-zip already exists
                    if prezip_exists(patient_id, folder_name):
                        logger.info(f"[Background] Pre-zip already exists for {folder_name}, skipping")
                        continue
                    
                    logger.info(f"[Background] Creating pre-zip for folder: {folder_name}")
                    success, message = create_prezip(patient_id, folder_name)
                    if success:
                        logger.info(f"[Background] Created pre-zip for {folder_name}: {message}")
                    else:
                        logger.error(f"[Background] Failed to create pre-zip for {folder_name}: {message}")
                
                logger.info(f"[Background] Pre-zip completed for patient {patient_id}")
                
        except Exception as e:
            logger.error(f"[Background] Error in pre-zip for patient {patient_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    if app is None:
        from flask import current_app
        app = current_app._get_current_object()
    
    thread = threading.Thread(target=_run_prezip, daemon=True)
    thread.start()
    logger.info(f"Pre-zip background thread started for patient {patient_id}")
    
    return thread


def trigger_prezip_background_for_folder(patient_id: int, folder_name: str, app=None):
    """
    Trigger pre-zipping of a single CBCT folder for a patient in a background thread.

    This is useful when a specific folder was just uploaded (e.g. via RAR extraction)
    and we don't want the request to block while the ZIP is generated.
    """
    def _run_prezip_one():
        try:
            with app.app_context():
                logger.info(f"[Background] Starting pre-zip for patient {patient_id}, folder {folder_name}")

                if prezip_exists(patient_id, folder_name):
                    logger.info(f"[Background] Pre-zip already exists for {folder_name}, skipping")
                    return

                success, message = create_prezip(patient_id, folder_name)
                if success:
                    logger.info(f"[Background] Created pre-zip for {folder_name}: {message}")
                else:
                    logger.error(f"[Background] Failed to create pre-zip for {folder_name}: {message}")
        except Exception as e:
            logger.error(f"[Background] Error in pre-zip for patient {patient_id}, folder {folder_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())

    if app is None:
        from flask import current_app
        app = current_app._get_current_object()

    thread = threading.Thread(target=_run_prezip_one, daemon=True)
    thread.start()
    logger.info(f"Pre-zip background thread started for patient {patient_id}, folder {folder_name}")
    return thread


def delete_prezip(patient_id: int, folder_name: str) -> bool:
    """Delete a pre-zipped file (e.g., when folder is deleted)."""
    s3_client = get_s3_client()
    bucket_name = get_bucket_name()
    prezip_key = get_prezip_s3_key(patient_id, folder_name)
    
    try:
        s3_client.delete_object(Bucket=bucket_name, Key=prezip_key)
        logger.info(f"Deleted pre-zip: {prezip_key}")
        return True
    except Exception as e:
        logger.warning(f"Error deleting pre-zip {prezip_key}: {e}")
        return False
