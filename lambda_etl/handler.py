"""
Lambda ETL Handler for Case-Card Generation
Processes redacted S3 files and creates structured JSON case-cards
Uses MySQL for idempotency tracking instead of DynamoDB
"""

import json
import logging
import os
import time
from typing import Dict, Any, Optional
import boto3
from botocore.exceptions import ClientError

# Import our modules
from mysql_client import get_mysql_client
from extractors.regex_extractor import RegexExtractor
from extractors.llm_fallback import LLMFallbackExtractor
from mappers.case_card_mapper import CaseCardMapper
from mappers.pseudonymizer import Pseudonymizer
from validators.schema_validator import SchemaValidator
from storage.s3_client import S3Client
from utils.logger import setup_logger
from utils.config import Config

# Set up logging
logger = setup_logger(__name__)

# Initialize AWS clients
s3_client = boto3.client('s3')
secrets_client = boto3.client('secretsmanager')

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler for S3 event processing
    
    Args:
        event: S3 event containing bucket and key information
        context: Lambda context
        
    Returns:
        dict: Processing result
    """
    start_time = time.time()
    processing_duration_ms = 0
    record_id = None
    
    try:
        # Parse S3 event
        bucket, key = parse_s3_event(event)
        logger.info(f"Processing S3 object: s3://{bucket}/{key}")
        
        # Get object metadata
        etag = get_s3_object_etag(bucket, key)
        logger.info(f"Object ETag: {etag}")
        
        # Check idempotency using MySQL
        mysql_client = get_mysql_client()
        
        if mysql_client.is_already_processed(bucket, key, etag):
            logger.info(f"Object already processed, skipping: s3://{bucket}/{key}")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Object already processed',
                    'bucket': bucket,
                    'key': key,
                    'etag': etag
                })
        }
        
        # Mark as processing started
        record_id = mysql_client.mark_processing_started(bucket, key, etag)
        
        # Download and process the file
        file_content = download_s3_file(bucket, key)
        logger.info(f"Downloaded file content ({len(file_content)} bytes)")
        
        # Extract clinical data using regex patterns
        extractor = RegexExtractor()
        extracted_data = extractor.extract(file_content)
        logger.info(f"Extracted {len(extracted_data)} fields using regex")
        
        # Optional LLM fallback for missing fields
        if Config.ENABLE_LLM_FALLBACK and should_use_llm_fallback(extracted_data):
            logger.info("Using LLM fallback for missing fields")
            llm_extractor = LLMFallbackExtractor()
            llm_data = llm_extractor.extract(file_content, extracted_data)
            extracted_data.update(llm_data)
        
        # Generate pseudonymous IDs
        pseudonymizer = Pseudonymizer()
        source_hint = f"{bucket}/{key}"
        patient_rid = pseudonymizer.generate_patient_rid(source_hint)
        case_id = pseudonymizer.generate_case_id(source_hint, "psg")
        
        logger.info(f"Generated IDs - Patient RID: {patient_rid}, Case ID: {case_id}")
        
        # Map extracted data to case-card JSON structure
        mapper = CaseCardMapper()
        case_card = mapper.map_to_case_card(
            extracted_data=extracted_data,
            case_id=case_id,
            patient_rid=patient_rid,
            source_uri=f"s3://{bucket}/{key}"
        )
        
        # Validate against JSON schema
        validator = SchemaValidator()
        validation_result = validator.validate(case_card)
        
        # Log validation errors/warnings to MySQL
        if validation_result.get('errors'):
            for error in validation_result['errors']:
                mysql_client.log_validation_error(
                    record_id, 'SCHEMA', 'ERROR', error
                )
        
        if validation_result.get('warnings'):
            for warning in validation_result['warnings']:
                mysql_client.log_validation_error(
                    record_id, 'SCHEMA', 'WARNING', warning
                )
        
        # Add validation results to case card
        case_card['validation'] = validation_result
        
        # Calculate extraction coverage
        extraction_coverage = calculate_extraction_coverage(case_card)
        
        # Upload case-card to research bucket
        s3_storage = S3Client()
        output_key = f"precedent_cases/{case_id}.json"
        s3_storage.upload_case_card(case_card, output_key, patient_rid)
        
        # Calculate processing duration
        processing_duration_ms = int((time.time() - start_time) * 1000)
        
        # Mark as completed in MySQL
        mysql_client.mark_processing_completed(
            record_id=record_id,
            case_id=case_id,
            patient_rid=patient_rid,
            extraction_coverage=extraction_coverage,
            processing_duration_ms=processing_duration_ms
        )
        
        # Update extraction pattern statistics
        update_extraction_stats(extracted_data)
        
        logger.info(f"Successfully processed: s3://{bucket}/{key} -> {output_key}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Processing completed successfully',
                'bucket': bucket,
                'key': key,
                'case_id': case_id,
                'patient_rid': patient_rid,
                'output_key': output_key,
                'extraction_coverage': extraction_coverage,
                'processing_duration_ms': processing_duration_ms
            })
        }
        
    except Exception as e:
        logger.error(f"Error processing S3 object: {str(e)}", exc_info=True)
        
        # Mark as failed in MySQL
        if record_id:
            mysql_client = get_mysql_client()
            mysql_client.mark_processing_failed(record_id, str(e))
        
        # Return error response
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': 'Processing failed',
                'error': str(e),
                'bucket': bucket if 'bucket' in locals() else 'unknown',
                'key': key if 'key' in locals() else 'unknown'
            })
        }

def parse_s3_event(event: Dict[str, Any]) -> tuple[str, str]:
    """Parse S3 event to extract bucket and key"""
    try:
        # Handle S3 event structure
        if 'Records' in event:
            record = event['Records'][0]
            bucket = record['s3']['bucket']['name']
            key = record['s3']['object']['key']
        else:
            # Direct invocation with bucket/key in event
            bucket = event['bucket']
            key = event['key']
        
        return bucket, key
    except Exception as e:
        logger.error(f"Error parsing S3 event: {e}")
        raise

def get_s3_object_etag(bucket: str, key: str) -> str:
    """Get S3 object ETag for idempotency tracking"""
    try:
        response = s3_client.head_object(Bucket=bucket, Key=key)
        return response['ETag'].strip('"')
    except ClientError as e:
        logger.error(f"Error getting S3 object ETag: {e}")
        raise

def download_s3_file(bucket: str, key: str) -> str:
    """Download S3 file content"""
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        return response['Body'].read().decode('utf-8')
    except ClientError as e:
        logger.error(f"Error downloading S3 file: {e}")
        raise

def should_use_llm_fallback(extracted_data: Dict[str, Any]) -> bool:
    """Determine if LLM fallback should be used based on extraction coverage"""
    required_fields = ['AHI', 'RDI', 'age', 'sex']
    extracted_required = sum(1 for field in required_fields if field in extracted_data and extracted_data[field] is not None)
    coverage = extracted_required / len(required_fields)
    
    # Use LLM fallback if coverage is less than 50%
    return coverage < 0.5

def calculate_extraction_coverage(case_card: Dict[str, Any]) -> float:
    """Calculate the fraction of fields successfully extracted"""
    features = case_card.get('features', {})
    total_fields = len(features)
    extracted_fields = sum(1 for value in features.values() if value is not None)
    
    return extracted_fields / total_fields if total_fields > 0 else 0.0

def update_extraction_stats(extracted_data: Dict[str, Any]):
    """Update extraction pattern statistics in MySQL"""
    try:
        mysql_client = get_mysql_client()
        
        # Update stats for each extracted field
        for field, value in extracted_data.items():
            if value is not None:
                mysql_client.update_extraction_pattern_stats(field, success=True)
            else:
                mysql_client.update_extraction_pattern_stats(field, success=False)
                
    except Exception as e:
        logger.error(f"Error updating extraction stats: {e}")

# Cleanup function for Lambda
def cleanup():
    """Cleanup resources"""
    try:
        mysql_client = get_mysql_client()
        mysql_client.close()
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
