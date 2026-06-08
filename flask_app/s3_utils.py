import boto3
import os
from botocore.config import Config
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def get_s3_client():
    """
    Create and return an S3 client with proper AWS credentials.
    Uses environment variables for configuration.
    """
    # Get credentials from environment 
    aws_access_key_id = os.getenv('AWS_ACCESS_KEY_ID')
    aws_secret_access_key = os.getenv('AWS_SECRET_ACCESS_KEY')
    aws_region = os.getenv('AWS_REGION')
    s3_bucket_name = os.getenv('S3_BUCKET_NAME')
    
    # Print debug info
    print(f"S3 Utils - Loading configuration:")
    print(f"AWS Region: {aws_region}")
    print(f"S3 Bucket: {s3_bucket_name}")
    if aws_access_key_id:
        print(f"AWS Access Key ID: {aws_access_key_id[:4]}...{aws_access_key_id[-4:]}")
    else:
        print("AWS Access Key ID: Not set")
    
    # Create S3 client with credentials
    s3_client = boto3.client(
        's3',
        region_name=aws_region,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        config=Config(signature_version='s3v4')
    )
    
    print(f"S3 client created with AWS region: {aws_region}")
    
    return s3_client 