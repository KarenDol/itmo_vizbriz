import os
import json
import boto3
import requests
from requests.auth import HTTPBasicAuth
import logging
from datetime import datetime, timezone, timedelta
import tempfile
import pydicom
import sys

# Configure logging
log_dir = os.path.dirname(os.path.abspath(__file__))
log_file_path = os.path.join(log_dir, 's3_orthanc_sync.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("s3_orthanc_sync")

# Configuration
S3_BUCKET_NAME = "sharkbiitpatientdataai"
S3_BASE_PREFIX = "patients/10318/" # Generalized to allow deep traversal

ORTHANC_URL = "http://3.132.113.74:8042"
ORTHANC_USERNAME = "vizbriz"
ORTHANC_PASSWORD = "Vizbriz2025!"

SYNC_INTERVAL = 600  # 10 minutes
LAST_RUN_FILE = os.path.join(log_dir, 'last_sync_time.json')

def check_aws_credentials():
    try:
        session = boto3.Session()
        credentials = session.get_credentials()
        if credentials is None:
            logger.error("AWS credentials not found. Please configure your AWS credentials.")
            logger.info("You can set them using environment variables AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY")
            logger.info("Or create a credentials file at ~/.aws/credentials")
            return False
        return True
    except Exception as e:
        logger.error(f"Error checking AWS credentials: {str(e)}")
        return False

def check_orthanc_connection():
    try:
        response = requests.get(
            f'{ORTHANC_URL}/system',
            auth=HTTPBasicAuth(ORTHANC_USERNAME, ORTHANC_PASSWORD),
            timeout=5
        )
        if response.status_code == 200:
            logger.info(f"Successfully connected to Orthanc: {ORTHANC_URL}")
            logger.info(f"Orthanc version: {response.json().get('Version', 'unknown')}")
            return True
        else:
            logger.error(f"Failed to connect to Orthanc: Status {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        logger.error(f"Could not connect to Orthanc at {ORTHANC_URL}")
        logger.info("Please make sure Orthanc is running and accessible at the configured URL")
        return False
    except Exception as e:
        logger.error(f"Error connecting to Orthanc: {str(e)}")
        return False

def load_last_run_time():
    if os.path.exists(LAST_RUN_FILE):
        try:
            with open(LAST_RUN_FILE, 'r') as f:
                data = json.load(f)
                return datetime.fromisoformat(data['last_run'])
        except Exception as e:
            logger.error(f"Error loading last run time: {str(e)}")
    return datetime.now(timezone.utc) - timedelta(days=365)

def save_last_run_time():
    try:
        with open(LAST_RUN_FILE, 'w') as f:
            json.dump({'last_run': datetime.now(timezone.utc).isoformat()}, f)
    except Exception as e:
        logger.error(f"Error saving last run time: {str(e)}")

def is_dicom_file(key):
    valid_extensions = ['.dcm', '.dicom', '.ima']
    key_lower = key.lower()
    if any(key_lower.endswith(ext) for ext in valid_extensions):
        return True
    filename = os.path.basename(key)
    if '.' not in filename:
        return True
    return False

def is_compressed_dicom(file_path):
    try:
        ds = pydicom.dcmread(file_path, stop_before_pixels=True)
        tsuid = str(ds.file_meta.TransferSyntaxUID)
        # Common MicroDICOM transfer syntaxes
        microdicom_uids = [
            "1.2.840.10008.1.2.4.91",  # JPEG 2000 Lossless
            "1.2.840.10008.1.2.4.80",  # JPEG-LS Lossless
            "1.2.840.10008.1.2.4.70",  # JPEG Lossless
            "1.2.840.10008.1.2.5",     # RLE Lossless
            "1.2.840.10008.1.2.4.50",  # JPEG Baseline
            "1.2.840.10008.1.2.4.51",  # JPEG Extended
            "1.2.840.10008.1.2.4.57",  # JPEG Lossless
            "1.2.840.10008.1.2.4.90"   # JPEG 2000
        ]
        is_compressed = tsuid in microdicom_uids
        logger.info(f"File transfer syntax: {tsuid}")
        logger.info(f"Is MicroDICOM compressed format: {is_compressed}")
        return is_compressed
    except Exception as e:
        logger.error(f"Could not read DICOM metadata for compression check: {str(e)}")
        return False

def convert_dicom_to_uncompressed(input_path, output_path):
    try:
        logger.info("Starting DICOM conversion...")
        # Read only the metadata first to check the transfer syntax
        ds = pydicom.dcmread(input_path, stop_before_pixels=True)
        original_syntax = str(ds.file_meta.TransferSyntaxUID)
        logger.info(f"Original transfer syntax: {original_syntax}")
        
        # For large files, use chunked reading
        if os.path.getsize(input_path) > 100 * 1024 * 1024:  # 100MB
            logger.info("Large file detected, using chunked conversion")
            ds = pydicom.dcmread(input_path, force=True)
        else:
            ds = pydicom.dcmread(input_path)
        
        # Force uncompressed transfer syntax
        ds.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
        logger.info("Converting to Explicit VR Little Endian format")
        
        # Save with specific transfer syntax
        ds.save_as(output_path, write_like_original=False)
        logger.info("Conversion completed successfully")
        return True
    except Exception as e:
        logger.error(f"Error converting DICOM file: {str(e)}")
        return False

def stream_to_orthanc(s3, bucket, key):
    try:
        logger.info(f"Processing file: {key}")
        
        # Get file size first
        response = s3.head_object(Bucket=bucket, Key=key)
        file_size = response['ContentLength']
        logger.info(f"File size: {file_size / (1024*1024):.2f} MB")
        
        # For very large files, use streaming
        if file_size > 100 * 1024 * 1024:  # 100MB
            logger.info("Large file detected, using streaming mode")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".dcm") as temp_file:
                temp_path = temp_file.name
                # Stream the file in chunks
                with s3.get_object(Bucket=bucket, Key=key)['Body'] as stream:
                    for chunk in iter(lambda: stream.read(8192), b''):
                        temp_file.write(chunk)
                temp_file.flush()
                
                # Process the file
                if is_compressed_dicom(temp_path):
                    logger.info("File is compressed, converting...")
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".dcm") as converted_file:
                        converted_path = converted_file.name
                        if not convert_dicom_to_uncompressed(temp_path, converted_path):
                            logger.error(f"Failed to convert {key}")
                            return False
                        upload_path = converted_path
                else:
                    logger.info("File is already uncompressed")
                    upload_path = temp_path
        else:
            # For smaller files, use the original method
            response = s3.get_object(Bucket=bucket, Key=key)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".dcm") as original_file:
                original_path = original_file.name
                original_file.write(response['Body'].read())

            if is_compressed_dicom(original_path):
                logger.info("File is compressed, converting...")
                with tempfile.NamedTemporaryFile(delete=False, suffix=".dcm") as converted_file:
                    converted_path = converted_file.name
                    if not convert_dicom_to_uncompressed(original_path, converted_path):
                        logger.error(f"Failed to convert {key}")
                        return False
                    upload_path = converted_path
            else:
                logger.info("File is already uncompressed")
                upload_path = original_path

        # Stream the file to Orthanc in chunks
        logger.info("Uploading to Orthanc...")
        with open(upload_path, 'rb') as f:
            orthanc_response = requests.post(
                f'{ORTHANC_URL}/instances',
                data=f,
                auth=HTTPBasicAuth(ORTHANC_USERNAME, ORTHANC_PASSWORD),
                headers={'Content-Type': 'application/dicom'},
                stream=True  # Enable streaming for the upload
            )

        if orthanc_response.status_code == 200:
            logger.info(f"Successfully uploaded to Orthanc: {key}")
            return True
        else:
            logger.error(f"Failed to upload to Orthanc: {key}. Status: {orthanc_response.status_code}, Error: {orthanc_response.text}")
            return False

    except Exception as e:
        logger.error(f"Error handling {key}: {str(e)}")
        return False
    finally:
        for path in ['original_path', 'converted_path', 'temp_path']:
            if path in locals() and os.path.exists(locals()[path]):
                os.remove(locals()[path])

def process_directory(s3, bucket, prefix, last_run_time):
    try:
        logger.info(f"Processing directory: {prefix}")
        files_processed = 0
        total_files_checked = 0

        paginator = s3.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket, Prefix=prefix)

        for page in page_iterator:
            if 'Contents' not in page:
                logger.info(f"No contents found in {prefix}")
                continue
            
            logger.info(f"Found {len(page['Contents'])} objects in current page")
            
            for obj in page['Contents']:
                key = obj['Key']
                total_files_checked += 1
                
                logger.info(f"Checking file: {key}")
                
                if key.endswith('/'):
                    logger.info(f"Skipping directory: {key}")
                    continue
                
                if is_dicom_file(key):
                    logger.info(f"Found DICOM file: {key}")
                    if stream_to_orthanc(s3, bucket, key):
                        files_processed += 1
                        logger.info(f"Successfully processed: {key}")
                    else:
                        logger.error(f"Failed to process: {key}")
                else:
                    logger.warning(f"Not a DICOM file: {key}")

        logger.info(f"Processed {files_processed} DICOM files out of {total_files_checked} total files")
        return files_processed

    except Exception as e:
        logger.error(f"Error processing directory {prefix}: {str(e)}")
        return 0

def process_new_dicom_files():
    try:
        s3 = boto3.client('s3')
        last_run_time = load_last_run_time()
        logger.info(f"Looking for files modified after {last_run_time.isoformat()}")
        total_files_processed = process_directory(s3, S3_BUCKET_NAME, S3_BASE_PREFIX, last_run_time)

        if total_files_processed == 0:
            logger.warning("No DICOM files were found to process. This might indicate an issue with:")
            logger.warning(f"1. The base prefix path (currently: {S3_BASE_PREFIX})")
            logger.warning(f"2. The last run time (currently: {last_run_time.isoformat()})")
            logger.warning("3. The DICOM file detection logic")
            logger.warning("4. AWS credentials or permissions")
        else:
            logger.info(f"Sync complete. Processed a total of {total_files_processed} DICOM files")

        save_last_run_time()

    except Exception as e:
        logger.error(f"Error in process_new_dicom_files: {str(e)}")

def main():
    logging.getLogger().setLevel(logging.INFO)
    logger.info("Starting S3 to Orthanc synchronization one-time run")
    logger.info(f"Searching for DICOM files in bucket: {S3_BUCKET_NAME}")
    logger.info(f"Starting from directory: {S3_BASE_PREFIX}")

    if not check_aws_credentials():
        sys.exit(1)
    if not check_orthanc_connection():
        sys.exit(1)

    try:
        process_new_dicom_files()
    except Exception as e:
        logger.error(f"Error in main process: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
