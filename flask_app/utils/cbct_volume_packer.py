"""
CBCT Volume Packer

Converts a DICOM series into a single volume file (NRRD format) for efficient
streaming and MPR rendering with Cornerstone3D.

Output location: patients/{patient_id}/imaging/cbct_volumes/{folder_name}.nrrd

The NRRD format is chosen because:
- Single file containing both data and metadata
- Supports compression (gzip)
- Standard format supported by many medical imaging tools
- Can be streamed with HTTP Range Requests
"""

import os
import json
import logging
import tempfile
import threading
from typing import Optional, Tuple, Dict, Any
from io import BytesIO

import boto3
import numpy as np
from botocore.config import Config

logger = logging.getLogger(__name__)

# Track ongoing conversions
_conversion_status: Dict[Tuple[int, str], Dict[str, Any]] = {}
_conversion_lock = threading.Lock()


def get_s3_client():
    """Get configured S3 client."""
    region = os.environ.get('AWS_REGION', 'us-east-1')
    return boto3.client('s3', region_name=region, config=Config(signature_version='s3v4'))


def get_bucket_name():
    """Get S3 bucket name."""
    bucket = os.environ.get('S3_BUCKET_NAME')
    if not bucket:
        try:
            from flask import current_app
            bucket = current_app.config.get('S3_BUCKET_NAME')
        except:
            pass
    return bucket or 'vizbrizpatients'


def get_volume_s3_key(patient_id: int, folder_name: str) -> str:
    """Get S3 key for the packed volume file.
    
    Stored under: patients/{patient_id}/imaging/cbct/{folder_name}/volume/volume.nrrd
    """
    return f"patients/{patient_id}/imaging/cbct/{folder_name}/volume/volume.nrrd"


def get_volume_manifest_s3_key(patient_id: int, folder_name: str) -> str:
    """Get S3 key for the volume manifest JSON.
    
    Stored under: patients/{patient_id}/imaging/cbct/{folder_name}/volume/manifest.json
    """
    return f"patients/{patient_id}/imaging/cbct/{folder_name}/volume/manifest.json"


def volume_exists(patient_id: int, folder_name: str) -> bool:
    """Check if packed volume already exists."""
    s3_client = get_s3_client()
    bucket = get_bucket_name()
    volume_key = get_volume_s3_key(patient_id, folder_name)
    
    try:
        s3_client.head_object(Bucket=bucket, Key=volume_key)
        return True
    except:
        return False


def get_volume_url(patient_id: int, folder_name: str, expires_in: int = 3600) -> Optional[str]:
    """Get presigned URL for the packed volume file."""
    if not volume_exists(patient_id, folder_name):
        return None
    
    s3_client = get_s3_client()
    bucket = get_bucket_name()
    volume_key = get_volume_s3_key(patient_id, folder_name)
    
    try:
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': volume_key},
            ExpiresIn=expires_in
        )
        return url
    except Exception as e:
        logger.error(f"Error generating presigned URL for volume: {e}")
        return None


def get_volume_manifest(patient_id: int, folder_name: str) -> Optional[Dict]:
    """Get the volume manifest with metadata."""
    s3_client = get_s3_client()
    bucket = get_bucket_name()
    manifest_key = get_volume_manifest_s3_key(patient_id, folder_name)
    
    try:
        response = s3_client.get_object(Bucket=bucket, Key=manifest_key)
        manifest = json.loads(response['Body'].read().decode('utf-8'))
        
        # Add presigned URL for the volume
        manifest['volumeUrl'] = get_volume_url(patient_id, folder_name)
        
        return manifest
    except Exception as e:
        logger.warning(f"Could not get volume manifest: {e}")
        return None


def get_conversion_status(patient_id: int, folder_name: str) -> Dict[str, Any]:
    """Get status of ongoing or completed conversion."""
    status_key = (patient_id, folder_name)
    
    with _conversion_lock:
        if status_key in _conversion_status:
            return _conversion_status[status_key].copy()
    
    # Check if volume exists
    if volume_exists(patient_id, folder_name):
        manifest = get_volume_manifest(patient_id, folder_name)
        return {
            'status': 'complete',
            'progress': 100,
            'message': 'Volume ready',
            'manifest': manifest
        }
    
    return {
        'status': 'not_started',
        'progress': 0,
        'message': 'Volume not generated yet'
    }


def _update_status(patient_id: int, folder_name: str, status: str, progress: int, message: str, **kwargs):
    """Update conversion status."""
    status_key = (patient_id, folder_name)
    with _conversion_lock:
        _conversion_status[status_key] = {
            'status': status,
            'progress': progress,
            'message': message,
            **kwargs
        }


def pack_dicom_to_volume(patient_id: int, folder_name: str) -> Tuple[bool, str]:
    """
    Convert DICOM series to a single NRRD volume file.
    
    This function:
    1. Lists all DICOM files in the S3 folder
    2. Downloads and parses each DICOM file
    3. Sorts slices by position
    4. Stacks into 3D numpy array
    5. Saves as NRRD with metadata
    6. Uploads to S3
    
    Returns:
        Tuple of (success, message)
    """
    try:
        import pydicom
    except ImportError:
        return False, "pydicom not installed. Run: pip install pydicom"
    
    s3_client = get_s3_client()
    bucket = get_bucket_name()
    
    _update_status(patient_id, folder_name, 'running', 5, 'Listing DICOM files...')
    
    # List all files in the CBCT folder
    prefix = f"patients/{patient_id}/imaging/cbct/{folder_name}/"
    
    dicom_keys = []
    paginator = s3_client.get_paginator('list_objects_v2')
    
    try:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            if 'Contents' in page:
                for obj in page['Contents']:
                    key = obj['Key']
                    filename = os.path.basename(key)
                    # Include files with .dcm extension or no extension (common in CBCT)
                    if key.endswith('/'):
                        continue
                    if filename.lower().endswith(('.dcm', '.dicom')) or '.' not in filename:
                        dicom_keys.append(key)
    except Exception as e:
        _update_status(patient_id, folder_name, 'error', 0, f'Error listing files: {e}')
        return False, f"Error listing DICOM files: {e}"
    
    if not dicom_keys:
        _update_status(patient_id, folder_name, 'error', 0, 'No DICOM files found')
        return False, "No DICOM files found in folder"
    
    logger.info(f"Found {len(dicom_keys)} DICOM files to process")
    _update_status(patient_id, folder_name, 'running', 10, f'Found {len(dicom_keys)} DICOM files. Downloading...')
    
    # Download and parse all DICOM files
    slices_data = []
    total_files = len(dicom_keys)
    
    for i, key in enumerate(dicom_keys):
        try:
            response = s3_client.get_object(Bucket=bucket, Key=key)
            dicom_bytes = response['Body'].read()
            
            # Parse DICOM
            ds = pydicom.dcmread(BytesIO(dicom_bytes))
            
            # Get slice position for sorting
            if hasattr(ds, 'ImagePositionPatient'):
                position = float(ds.ImagePositionPatient[2])  # Z position
            elif hasattr(ds, 'SliceLocation'):
                position = float(ds.SliceLocation)
            elif hasattr(ds, 'InstanceNumber'):
                position = float(ds.InstanceNumber)
            else:
                position = i  # Fallback to index
            
            # Get pixel data
            pixel_array = ds.pixel_array
            
            # Apply rescale if present
            slope = getattr(ds, 'RescaleSlope', 1)
            intercept = getattr(ds, 'RescaleIntercept', 0)
            if slope != 1 or intercept != 0:
                pixel_array = pixel_array * slope + intercept
            
            slices_data.append({
                'position': position,
                'pixels': pixel_array,
                'ds': ds
            })
            
            if (i + 1) % 50 == 0 or i == total_files - 1:
                progress = 10 + int((i + 1) / total_files * 50)
                _update_status(patient_id, folder_name, 'running', progress, 
                             f'Downloaded {i + 1}/{total_files} files...')
                
        except Exception as e:
            logger.warning(f"Error processing {key}: {e}")
            continue
    
    if len(slices_data) < 2:
        _update_status(patient_id, folder_name, 'error', 0, 'Not enough valid DICOM slices')
        return False, "Not enough valid DICOM slices found"
    
    logger.info(f"Successfully parsed {len(slices_data)} DICOM slices")
    _update_status(patient_id, folder_name, 'running', 65, 'Sorting and stacking slices...')
    
    # Sort by position
    slices_data.sort(key=lambda x: x['position'])
    
    # Get metadata from first slice
    first_ds = slices_data[0]['ds']
    
    rows = int(getattr(first_ds, 'Rows', slices_data[0]['pixels'].shape[0]))
    cols = int(getattr(first_ds, 'Columns', slices_data[0]['pixels'].shape[1]))
    
    # Get pixel spacing
    pixel_spacing = getattr(first_ds, 'PixelSpacing', [1.0, 1.0])
    row_spacing = float(pixel_spacing[0])
    col_spacing = float(pixel_spacing[1])
    
    # Calculate slice spacing from positions
    if len(slices_data) > 1:
        slice_spacing = abs(slices_data[1]['position'] - slices_data[0]['position'])
        if slice_spacing == 0:
            slice_spacing = float(getattr(first_ds, 'SliceThickness', 1.0))
    else:
        slice_spacing = float(getattr(first_ds, 'SliceThickness', 1.0))
    
    # Get origin
    if hasattr(first_ds, 'ImagePositionPatient'):
        origin = [float(x) for x in first_ds.ImagePositionPatient]
    else:
        origin = [0.0, 0.0, 0.0]
    
    # Stack into 3D volume
    num_slices = len(slices_data)
    
    # Determine data type
    first_pixels = slices_data[0]['pixels']
    if first_pixels.dtype in [np.int8, np.int16, np.int32]:
        dtype = np.int16
    elif first_pixels.dtype in [np.uint8, np.uint16, np.uint32]:
        dtype = np.int16  # Convert to signed for HU values
    else:
        dtype = np.float32
    
    logger.info(f"Creating volume: {cols}x{rows}x{num_slices}, dtype={dtype}")
    
    try:
        volume = np.zeros((num_slices, rows, cols), dtype=dtype)
        
        for i, slice_data in enumerate(slices_data):
            pixels = slice_data['pixels']
            # Ensure correct shape
            if pixels.shape == (rows, cols):
                volume[i] = pixels.astype(dtype)
            else:
                logger.warning(f"Slice {i} has unexpected shape {pixels.shape}, expected ({rows}, {cols})")
                # Try to resize/crop
                min_rows = min(pixels.shape[0], rows)
                min_cols = min(pixels.shape[1], cols)
                volume[i, :min_rows, :min_cols] = pixels[:min_rows, :min_cols].astype(dtype)
                
    except MemoryError:
        _update_status(patient_id, folder_name, 'error', 0, 'Not enough memory to create volume')
        return False, "Not enough memory to create volume"
    
    _update_status(patient_id, folder_name, 'running', 75, 'Creating NRRD file...')
    
    # Create NRRD file
    temp_dir = tempfile.mkdtemp()
    nrrd_path = os.path.join(temp_dir, f"{folder_name}.nrrd")
    
    try:
        # Try to use pynrrd if available
        try:
            import nrrd
            
            # NRRD header
            header = {
                'type': 'int16' if dtype == np.int16 else 'float',
                'dimension': 3,
                'space': 'left-posterior-superior',
                'sizes': [cols, rows, num_slices],
                'space directions': [
                    [col_spacing, 0, 0],
                    [0, row_spacing, 0],
                    [0, 0, slice_spacing]
                ],
                'space origin': origin,
                'encoding': 'gzip',
                'endian': 'little'
            }
            
            # Transpose volume for NRRD (X, Y, Z) ordering
            volume_nrrd = np.transpose(volume, (2, 1, 0))
            
            nrrd.write(nrrd_path, volume_nrrd, header)
            
        except ImportError:
            # Fallback: Write as raw binary with JSON metadata
            logger.warning("pynrrd not installed, using raw binary format")
            nrrd_path = os.path.join(temp_dir, f"{folder_name}.raw")
            volume.tofile(nrrd_path)
        
        file_size = os.path.getsize(nrrd_path)
        logger.info(f"Created volume file: {file_size / (1024*1024):.1f} MB")
        
        _update_status(patient_id, folder_name, 'running', 85, f'Uploading volume ({file_size // (1024*1024)} MB)...')
        
        # Upload to S3
        volume_key = get_volume_s3_key(patient_id, folder_name)
        
        with open(nrrd_path, 'rb') as f:
            s3_client.upload_fileobj(
                f,
                bucket,
                volume_key,
                ExtraArgs={'ContentType': 'application/octet-stream'}
            )
        
        logger.info(f"Uploaded volume to {volume_key}")
        
        # Create and upload manifest
        manifest = {
            'patientId': patient_id,
            'folderName': folder_name,
            'dimensions': [cols, rows, num_slices],
            'spacing': [col_spacing, row_spacing, slice_spacing],
            'origin': origin,
            'dataType': str(dtype),
            'sizeBytes': file_size,
            'numSlices': num_slices,
            'format': 'nrrd' if nrrd_path.endswith('.nrrd') else 'raw'
        }
        
        manifest_key = get_volume_manifest_s3_key(patient_id, folder_name)
        s3_client.put_object(
            Bucket=bucket,
            Key=manifest_key,
            Body=json.dumps(manifest, indent=2),
            ContentType='application/json'
        )
        
        logger.info(f"Uploaded manifest to {manifest_key}")
        
        _update_status(patient_id, folder_name, 'complete', 100, 'Volume ready', manifest=manifest)
        
        return True, volume_key
        
    except Exception as e:
        logger.error(f"Error creating/uploading volume: {e}")
        import traceback
        logger.error(traceback.format_exc())
        _update_status(patient_id, folder_name, 'error', 0, f'Error: {e}')
        return False, str(e)
        
    finally:
        # Cleanup temp files
        try:
            if os.path.exists(nrrd_path):
                os.remove(nrrd_path)
            os.rmdir(temp_dir)
        except:
            pass


def trigger_volume_packing_background(patient_id: int, folder_name: str, app=None):
    """
    Trigger volume packing in a background thread.
    """
    def _run_packing():
        try:
            if app:
                with app.app_context():
                    pack_dicom_to_volume(patient_id, folder_name)
            else:
                pack_dicom_to_volume(patient_id, folder_name)
        except Exception as e:
            logger.error(f"Background volume packing error: {e}")
            _update_status(patient_id, folder_name, 'error', 0, f'Error: {e}')
    
    if app is None:
        try:
            from flask import current_app
            app = current_app._get_current_object()
        except:
            pass
    
    # Check if already running
    status = get_conversion_status(patient_id, folder_name)
    if status['status'] == 'running':
        return False, "Conversion already in progress"
    
    _update_status(patient_id, folder_name, 'running', 0, 'Starting conversion...')
    
    thread = threading.Thread(target=_run_packing, daemon=True)
    thread.start()
    
    logger.info(f"Started background volume packing for patient {patient_id}, folder {folder_name}")
    return True, "Conversion started"

