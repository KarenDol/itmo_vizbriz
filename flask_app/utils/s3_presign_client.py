"""
S3 client for generating long-lived presigned URLs.

When S3_PRESIGN_ACCESS_KEY_ID and S3_PRESIGN_SECRET_ACCESS_KEY are set,
uses those IAM user credentials for presigning. This avoids the EC2 instance
role credential expiry (~6 hours) that causes share links to fail early.

Without these env vars, falls back to default credentials (instance role).
"""
import os
import logging
import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)


def get_s3_client_for_presigning(region: str = None):
    """
    Return an S3 client for generating presigned URLs.
    Uses long-lived IAM user credentials when S3_PRESIGN_* env vars are set.
    """
    region = region or os.environ.get('AWS_REGION', 'us-west-2')
    access_key = os.environ.get('S3_PRESIGN_ACCESS_KEY_ID')
    secret_key = os.environ.get('S3_PRESIGN_SECRET_ACCESS_KEY')

    if access_key and secret_key:
        logger.info("Using S3 presign IAM user (vizbriz-s3-presign) for long-lived share links")
        return boto3.client(
            's3',
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version='s3v4')
        )

    # Fallback to default credentials (instance role)
    return boto3.client('s3', region_name=region, config=Config(signature_version='s3v4'))
