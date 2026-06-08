#!/usr/bin/env python3
"""
Unified Document Extraction System
Combines Schema-Focused Extraction + LLM Extraction with Production Database Framework

This script:
1. Fetches documents from the database (not local files)
2. Deletes old observation store entries for each document
3. Extracts observations using both schema patterns and LLM
4. Stores new observations in observation_store table
5. Deletes old canonical JSON and creates new ones
6. Stores canonical JSON in patient_case_envelope table

KEY FEATURES:
- Uses production database framework from document_observation_extractor_phase2.py
- Combines regex-based schema extraction with LLM-powered extraction
- Handles both files and adminfiles tables
- Creates comprehensive canonical JSON from all observations
- Supports batch processing for efficiency
"""

import os
import sys
import logging
import json
import boto3
import requests
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any
import mysql.connector
from dotenv import load_dotenv
import PyPDF2
import io
from PIL import Image
import pytesseract
import fitz  # PyMuPDF for better PDF handling
import shutil
import gc
import argparse
import time
from pathlib import Path

# Add the project root to Python path for imports
sys.path.append('/home/ec2-user/vizbriz')

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('unified_extractor.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Import production framework
from vizbriz.flask_app.config.document_observation_extractor_phase2 import (
    DB_CONFIG, S3_BUCKET_NAME, AWS_REGION,
    get_s3_client, extract_document_content,
    discover_patient_documents, delete_existing_observations,
    store_observations_with_deduplication, create_minimal_canonical_json_for_patient,
    extract_demographics_from_text, _prune_empty
)

# Import schema-focused extraction
from vizbriz.flask_app.config.textract_extractor_schema_focused import (
    extract_for_observations_db, _search_text_for_schema_patterns,
    _reconcile, _to_schema_compliant_fragment, SCHEMA_FIELDS, SCHEMA_PATTERNS
)

# Initialize S3 client
s3_client = get_s3_client()

# LLM throttling configuration
BEDROCK_RPM = 5  # 5 requests per minute
MIN_BEDROCK_INTERVAL_SECONDS = 12  # 60 seconds / 5 RPM = 12 seconds between calls
_LAST_BEDROCK_CALL_AT = 0.0

def throttle_bedrock() -> None:
    """Sleep as needed to ensure Bedrock calls are spaced to respect RPM limits."""
    global _LAST_BEDROCK_CALL_AT
    now = time.time()
    if _LAST_BEDROCK_CALL_AT > 0:
        elapsed = now - _LAST_BEDROCK_CALL_AT
        if elapsed < MIN_BEDROCK_INTERVAL_SECONDS:
            wait_s = MIN_BEDROCK_INTERVAL_SECONDS - elapsed
            logger.info(f"Throttling Bedrock call for {wait_s:.1f}s to respect rate limits ({BEDROCK_RPM}/min)")
            time.sleep(wait_s)
    _LAST_BEDROCK_CALL_AT = time.time()

def extract_with_schema_patterns(document_content: str, document_name: str) -> List[Dict]:
    """
    Extract observations using schema-focused regex patterns.
    
    Args:
        document_content (str): Extracted text content
        document_name (str): Name of the document
        
    Returns:
        List[Dict]: List of observations with schema paths
    """
    try:
        if not document_content or len(document_content.strip()) < 50:
            logger.warning(f"Insufficient content for schema extraction: {document_name}")
            return []
        
        # Use schema-focused extraction
        candidates = _search_text_for_schema_patterns(document_content)
        
        if not candidates:
            logger.info(f"No schema patterns found in {document_name}")
            return []
        
        # Reconcile candidates to get best values
        best_candidates = _reconcile(candidates)
        
        # Convert to observations with correct schema paths
        observations = []
        for field, candidate in best_candidates.items():
            # Map fields to correct schema sections according to Patient Case JSON v1
            schema_path = map_field_to_schema_path(field)
            
            obs = {
                'path': schema_path,
                'value': str(candidate.value),
                'observation': f"{field.replace('_', ' ').title()}: {candidate.value}",
                'score': 1,
                'explanation': f'Extracted using schema patterns from {document_name}',
                'evidence': f'Found {field} using regex patterns',
                'confidence': candidate.confidence,
                'source': 'schema-extraction',
                'page': candidate.page,
                'key_text': candidate.key_text,
                'raw': candidate.raw
            }
            observations.append(obs)
        
        logger.info(f"Schema extraction found {len(observations)} observations in {document_name}")
        return observations
        
    except Exception as e:
        logger.error(f"Error in schema extraction for {document_name}: {e}")
        return []

def map_field_to_schema_path(field: str) -> str:
    """
    Map extracted field names to correct schema paths according to Patient Case JSON v1.
    
    Args:
        field (str): Field name from regex extraction
        
    Returns:
        str: Correct schema path
    """
    # Demographics section - only basic patient info
    demographics_fields = {
        'sex', 'age_years', 'height_cm', 'weight_kg', 'bmi'
    }
    
    # Sleep study section - all sleep-related metrics
    sleep_study_fields = {
        'study_type', 'sleep_duration_h', 'sleep_efficiency_pct',
        'ahi', 'odi', 'rdi', 'oai', 'cai', 'hi', 'desaturation_events',
        'o2_nadir_pct', 'o2_mean_pct', 'time_below_90_pct_min', 'time_below_88_pct_min',
        'supine_ahi', 'non_supine_ahi', 'rem_ahi', 'nrem_ahi',
        'snore_avg_db', 'snore_max_db', 'ESS'
    }
    
    # Anatomical findings section - physical examination findings
    anatomical_fields = {
        'primary_obstruction_site', 'soft_palate_uvula', 'tongue_base',
        'bite_jaw', 'hyoid', 'nose_sinus', 'tmj'
    }
    
    # Device design section - appliance specifications
    device_design_fields = {
        'mandibular_advancement_mm', 'vertical_opening_mm', 'anterior_window'
    }
    
    # Patient self-report section - questionnaire responses
    self_report_fields = {
        'tmj_flags_clicking', 'tmj_flags_pain', 'tmj_flags_side',
        'symptoms_daytime_sleepiness', 'symptoms_non_restorative_sleep',
        'symptoms_witnessed_apneas', 'symptoms_nocturia', 'symptoms_morning_headache',
        'symptoms_dry_mouth', 'symptoms_bruxism', 'symptoms_reflux', 'symptoms_insomnia_features'
    }
    
    # Positional metrics section
    positional_fields = {
        'positional_phenotype'
    }
    
    # Map to correct schema paths
    if field in demographics_fields:
        return f'demographics.{field}'
    elif field in sleep_study_fields:
        # Handle nested sleep study fields
        if field in ['snore_avg_db', 'snore_max_db']:
            snore_field = 'avg_db' if field == 'snore_avg_db' else 'max_db'
            return f'sleep_study.snoring.{snore_field}'
        elif field == 'ESS':
            return f'patient_self_report.scales.ESS'
        else:
            return f'sleep_study.{field}'
    elif field in anatomical_fields:
        return f'observations.anatomy_imaging.{field}'
    elif field in device_design_fields:
        return f'device_design.{field}'
    elif field in self_report_fields:
        # Handle nested self-report fields
        if field.startswith('tmj_flags_'):
            tmj_field = field.replace('tmj_flags_', '')
            return f'observations.tmj_flags.{tmj_field}'
        elif field.startswith('symptoms_'):
            symptom_field = field.replace('symptoms_', '')
            return f'patient_self_report.symptoms.{symptom_field}'
        else:
            return f'patient_self_report.{field}'
    elif field in positional_fields:
        return f'positional_metrics.{field}'
    else:
        # Default to observations.summary for unrecognized fields
        return f'observations.summary'

def extract_with_llm(document_content: str, document_name: str, document_type: str) -> List[Dict]:
    """
    Extract observations using LLM (AWS Bedrock) with rate limiting and retry logic.
    
    Args:
        document_content (str): Extracted text content
        document_name (str): Name of the document
        document_type (str): Type of document
        
    Returns:
        List[Dict]: List of observations from LLM
    """
    try:
        if not document_content or len(document_content.strip()) < 50:
            logger.warning(f"Insufficient content for LLM extraction: {document_name}")
            return []
        
        # Throttle Bedrock calls to respect rate limits
        throttle_bedrock()
        
        # Use the LLM extraction from production framework with retry logic
        from vizbriz.flask_app.config.document_observation_extractor_phase2 import extract_observations_with_llm
        
        max_retries = 3
        retry_delay = 15  # 15 seconds between retries
        
        for attempt in range(max_retries):
            try:
                llm_response = extract_observations_with_llm(document_content, document_type, document_name)
                
                # Check if response is an error message
                if llm_response and llm_response.startswith("Error extracting observations:"):
                    logger.warning(f"LLM returned error on attempt {attempt + 1}: {llm_response}")
                    if attempt < max_retries - 1:
                        logger.info(f"Retrying LLM extraction for {document_name} in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                        continue
                    else:
                        logger.error(f"LLM extraction failed after {max_retries} attempts for {document_name}")
                        return []
                
                if not llm_response or llm_response == "No content available for analysis":
                    logger.info(f"No LLM observations found in {document_name}")
                    return []
                
                # Parse LLM response into observations
                observations = parse_llm_observations(llm_response, document_name)
                
                logger.info(f"LLM extraction found {len(observations)} observations in {document_name}")
                return observations
                
            except Exception as e:
                logger.error(f"LLM extraction attempt {attempt + 1} failed for {document_name}: {e}")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying LLM extraction for {document_name} in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logger.error(f"LLM extraction failed after {max_retries} attempts for {document_name}")
                    return []
        
    except Exception as e:
        logger.error(f"Error in LLM extraction for {document_name}: {e}")
        return []

def parse_llm_observations(llm_response: str, document_name: str) -> List[Dict]:
    """
    Parse LLM response into structured observations with proper schema mapping.
    
    Args:
        llm_response (str): Raw LLM response text
        document_name (str): Name of the document
        
    Returns:
        List[Dict]: List of structured observations
    """
    observations = []
    
    try:
        # Detect document type to determine appropriate schema mapping
        document_type = detect_document_type(document_name, llm_response)
        
        # Split response into lines and process each line
        lines = llm_response.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Skip headers and section titles
            if any(skip_word in line.lower() for skip_word in [
                'diagnosis:', 'sleep apnea indices:', 'oxygen saturation:', 
                'sleep architecture:', 'sleep latency:', 'oxygen desaturation events:',
                'time spent at low oxygen saturations:', 'pulse rate during sleep:',
                'sleep fragmentation:', 'these observations indicate', '==='
            ]):
                continue
            
            # Extract numbered items (e.g., "1. Diagnosis: Severe OSA")
            if re.match(r'^\d+\.', line):
                observation_text = re.sub(r'^\d+\.\s*', '', line)
                if observation_text:
                    schema_path = map_llm_observation_to_schema(observation_text, document_type)
                    obs = {
                        'path': schema_path,
                        'value': observation_text,
                        'observation': observation_text,
                        'score': 1,
                        'explanation': f'Extracted from LLM analysis of {document_name}',
                        'evidence': observation_text,
                        'confidence': 85,
                        'source': 'llm-extraction'
                    }
                    observations.append(obs)
            
            # Extract bullet points or dash items
            elif line.startswith('-') or line.startswith('•') or line.startswith('*'):
                observation_text = line[1:].strip()
                if observation_text:
                    schema_path = map_llm_observation_to_schema(observation_text, document_type)
                    obs = {
                        'path': schema_path,
                        'value': observation_text,
                        'observation': observation_text,
                        'score': 1,
                        'explanation': f'Extracted from LLM analysis of {document_name}',
                        'evidence': observation_text,
                        'confidence': 85,
                        'source': 'llm-extraction'
                    }
                    observations.append(obs)
            
            # Extract key-value pairs (e.g., "Mean: 93%")
            elif ':' in line and not line.startswith('Diagnosis:') and not line.startswith('Sleep'):
                parts = line.split(':', 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip()
                    if key and value:
                        schema_path = map_llm_observation_to_schema(f"{key}: {value}", document_type)
                        obs = {
                            'path': schema_path,
                            'value': f"{key}: {value}",
                            'observation': f"{key}: {value}",
                            'score': 1,
                            'explanation': f'Extracted from LLM analysis of {document_name}',
                            'evidence': f'{key}: {value}',
                            'confidence': 85,
                            'source': 'llm-extraction'
                        }
                        observations.append(obs)
        
        # If no structured observations found, check if it's an error response
        if not observations:
            if llm_response and llm_response.startswith("Error extracting observations:"):
                logger.warning(f"LLM returned error response for {document_name}: {llm_response}")
                return []  # Return empty list instead of creating error observation
            else:
                # Create one general observation for successful but empty analysis
                schema_path = map_llm_observation_to_schema('Document Analysis Complete', document_type)
                observations.append({
                    'path': schema_path,
                    'value': 'Document Analysis Complete',
                    'observation': 'Document was analyzed for clinical observations',
                    'score': 1,
                    'explanation': f'LLM analysis completed for {document_name}',
                    'evidence': llm_response[:200] + '...' if len(llm_response) > 200 else llm_response,
                    'confidence': 85,
                    'source': 'llm-extraction'
                })
        
        return observations
        
    except Exception as e:
        logger.error(f"Error parsing LLM observations for {document_name}: {e}")
        return []

def detect_document_type(document_name: str, content: str) -> str:
    """
    Detect document type based on filename and content to determine schema mapping.
    
    Args:
        document_name (str): Name of the document
        content (str): Document content
        
    Returns:
        str: Document type ('questionnaire', 'sleep_study', 'medical_report', 'general')
    """
    document_name_lower = document_name.lower()
    content_lower = content.lower()
    
    # Questionnaire detection
    if any(keyword in document_name_lower for keyword in ['questionnaire', 'form', 'survey', 'שאלון']):
        return 'questionnaire'
    
    # Sleep study detection
    if any(keyword in document_name_lower for keyword in ['sleep', 'psg', 'hsat', 'polysomnography', 'home sleep']):
        return 'sleep_study'
    
    # Medical report detection
    if any(keyword in document_name_lower for keyword in ['report', 'assessment', 'evaluation', 'consultation']):
        return 'medical_report'
    
    # Content-based detection
    if any(keyword in content_lower for keyword in ['questionnaire', 'survey', 'form', 'patient reported']):
        return 'questionnaire'
    elif any(keyword in content_lower for keyword in ['sleep study', 'polysomnography', 'ahi', 'odi', 'sleep efficiency']):
        return 'sleep_study'
    elif any(keyword in content_lower for keyword in ['diagnosis', 'assessment', 'findings', 'examination']):
        return 'medical_report'
    
    return 'general'

def map_llm_observation_to_schema(observation_text: str, document_type: str) -> str:
    """
    Map LLM observation text to appropriate schema path based on document type and content.
    
    Args:
        observation_text (str): Observation text from LLM
        document_type (str): Type of document
        
    Returns:
        str: Appropriate schema path
    """
    observation_lower = observation_text.lower()
    
    # Questionnaire-specific mappings
    if document_type == 'questionnaire':
        # Map common questionnaire responses to patient_self_report sections
        if any(symptom in observation_lower for symptom in ['tired', 'sleepy', 'fatigue', 'exhausted']):
            return 'patient_self_report.symptoms.daytime_sleepiness'
        elif any(symptom in observation_lower for symptom in ['mouth breathing', 'breathing through mouth']):
            return 'patient_self_report.symptoms.dry_mouth'
        elif any(symptom in observation_lower for symptom in ['tmj', 'jaw', 'clicking', 'pain']):
            return 'observations.tmj_flags.clicking'
        elif any(symptom in observation_lower for symptom in ['bruxism', 'teeth grinding']):
            return 'patient_self_report.symptoms.bruxism'
        elif any(symptom in observation_lower for symptom in ['reflux', 'heartburn', 'acid']):
            return 'patient_self_report.symptoms.reflux'
        elif any(symptom in observation_lower for symptom in ['insomnia', 'difficulty falling asleep']):
            return 'patient_self_report.symptoms.insomnia_features'
        elif any(symptom in observation_lower for symptom in ['nocturia', 'frequent urination']):
            return 'patient_self_report.symptoms.nocturia'
        elif any(symptom in observation_lower for symptom in ['headache', 'morning headache']):
            return 'patient_self_report.symptoms.morning_headache'
        elif any(symptom in observation_lower for symptom in ['apnea', 'stopping breathing']):
            return 'patient_self_report.symptoms.witnessed_apneas'
        elif any(symptom in observation_lower for symptom in ['non-restorative', 'unrefreshing']):
            return 'patient_self_report.symptoms.non_restorative_sleep'
        else:
            # Default for questionnaire responses
            return 'patient_self_report.primary_complaint'
    
    # Sleep study-specific mappings
    elif document_type == 'sleep_study':
        if any(metric in observation_lower for metric in ['ahi', 'apnea hypopnea index']):
            return 'sleep_study.ahi'
        elif any(metric in observation_lower for metric in ['odi', 'oxygen desaturation index']):
            return 'sleep_study.odi'
        elif any(metric in observation_lower for metric in ['sleep efficiency']):
            return 'sleep_study.sleep_efficiency_pct'
        elif any(metric in observation_lower for metric in ['oxygen', 'o2', 'saturation']):
            return 'sleep_study.o2_mean_pct'
        else:
            return 'sleep_study.study_type'
    
    # Medical report-specific mappings
    elif document_type == 'medical_report':
        if any(anatomy in observation_lower for anatomy in ['palate', 'uvula', 'soft palate']):
            return 'observations.anatomy_imaging.soft_palate_uvula'
        elif any(anatomy in observation_lower for anatomy in ['tongue', 'tongue base']):
            return 'observations.anatomy_imaging.tongue_base'
        elif any(anatomy in observation_lower for anatomy in ['bite', 'jaw', 'mandible']):
            return 'observations.anatomy_imaging.bite_jaw'
        elif any(anatomy in observation_lower for anatomy in ['hyoid']):
            return 'observations.anatomy_imaging.hyoid'
        elif any(anatomy in observation_lower for anatomy in ['nose', 'sinus', 'nasal']):
            return 'observations.anatomy_imaging.nose_sinus'
        else:
            return 'observations.summary'
    
    # Default mapping
    else:
        return 'observations.summary'

def extract_demographics_observations(document_content: str, document_name: str) -> List[Dict]:
    """
    Extract demographics observations from document content.
    
    Args:
        document_content (str): Raw document content
        document_name (str): Name of the document
        
    Returns:
        List[Dict]: List of demographics observations with schema paths
    """
    demographics_obs = []
    
    if not document_content:
        return demographics_obs
    
    # Extract demographics using the production framework
    demographics = extract_demographics_from_text([document_content])
    
    # Create structured observations for each found demographic
    for field, value in demographics.items():
        if value is not None:
            obs = {
                'path': f'demographics.{field}',
                'value': str(value),
                'observation': f"{field.replace('_', ' ').title()}: {value}",
                'score': 1,
                'explanation': f'Extracted from document: {document_name}',
                'evidence': f'Found {field.replace("_", " ")} in document content',
                'confidence': 100,
                'source': 'demographics-extraction'
            }
            demographics_obs.append(obs)
    
    return demographics_obs

def process_document_unified(document: Dict, skip_llm: bool = False) -> Dict[str, Any]:
    """
    Process a single document using both schema and LLM extraction.
    
    Args:
        document (Dict): Document metadata from database
        skip_llm (bool): Whether to skip LLM extraction
        
    Returns:
        Dict: Processing results
    """
    document_name = document.get('name', 'unknown')
    patient_id = document.get('patient_id')
    source_type = document.get('source_type', 'unknown')
    
    logger.info(f"Processing document: {document_name} for patient {patient_id}")
    
    try:
        # Extract document content using production framework
        content = extract_document_content(document)
        
        if not content:
            logger.warning(f"No content extracted from {document_name}")
            return {
                'success': False,
                'document_name': document_name,
                'error': 'No content extracted',
                'observations_count': 0
            }
        
        # Delete existing observations for this document
        delete_existing_observations(patient_id, source_type, document)
        logger.info(f"Deleted existing observations for {document_name}")
        
        all_observations = []
        
        # 1. Extract using schema patterns
        schema_observations = extract_with_schema_patterns(content, document_name)
        all_observations.extend(schema_observations)
        logger.info(f"Schema extraction: {len(schema_observations)} observations")
        
        # 2. Extract using LLM (if not skipped)
        llm_observations = []
        if not skip_llm:
            llm_observations = extract_with_llm(content, document_name, source_type)
            # Filter out error messages from LLM observations
            valid_llm_observations = []
            for obs in llm_observations:
                if isinstance(obs, dict) and 'value' in obs:
                    # Skip observations that are error messages
                    if not obs['value'].startswith('Error extracting observations:'):
                        valid_llm_observations.append(obs)
                    else:
                        logger.warning(f"Skipping LLM error observation: {obs['value']}")
                else:
                    valid_llm_observations.append(obs)
            
            all_observations.extend(valid_llm_observations)
            logger.info(f"LLM extraction: {len(valid_llm_observations)} valid observations (filtered from {len(llm_observations)} total)")
        else:
            logger.info("Skipping LLM extraction due to --skip-llm flag")
        
        # 3. Extract demographics
        demographics_observations = extract_demographics_observations(content, document_name)
        all_observations.extend(demographics_observations)
        logger.info(f"Demographics extraction: {len(demographics_observations)} observations")
        
        # Store all observations
        if all_observations:
            success = store_observations_with_deduplication(patient_id, source_type, all_observations, document)
            if success:
                logger.info(f"Successfully stored {len(all_observations)} observations for {document_name}")
                return {
                    'success': True,
                    'document_name': document_name,
                    'observations_count': len(all_observations),
                    'schema_count': len(schema_observations),
                    'llm_count': len(llm_observations),
                    'demographics_count': len(demographics_observations)
                }
            else:
                logger.error(f"Failed to store observations for {document_name}")
                return {
                    'success': False,
                    'document_name': document_name,
                    'error': 'Failed to store observations',
                    'observations_count': 0
                }
        else:
            logger.warning(f"No observations extracted from {document_name}")
            return {
                'success': False,
                'document_name': document_name,
                'error': 'No observations extracted',
                'observations_count': 0
            }
            
    except Exception as e:
        logger.error(f"Error processing document {document_name}: {e}")
        return {
            'success': False,
            'document_name': document_name,
            'error': str(e),
            'observations_count': 0
        }

def process_patient_unified(patient_id: int, max_documents: int = None, skip_llm: bool = False) -> Dict[str, Any]:
    """
    Process all documents for a patient using unified extraction with LLM rate limiting.
    
    Args:
        patient_id (int): Patient ID to process
        max_documents (int): Maximum number of documents to process (for testing)
        skip_llm (bool): Whether to skip LLM extraction
        
    Returns:
        Dict: Processing statistics
    """
    logger.info(f"Starting unified processing for patient {patient_id}")
    if not skip_llm:
        logger.info(f"LLM rate limit: {BEDROCK_RPM} calls per minute (1 call every {MIN_BEDROCK_INTERVAL_SECONDS} seconds)")
    else:
        logger.info("LLM extraction disabled (--skip-llm flag)")
    
    # Discover documents using production framework
    documents = discover_patient_documents(patient_id)
    
    if not documents:
        logger.info(f"No documents found for patient {patient_id}")
        return {
            'patient_id': patient_id,
            'total_documents': 0,
            'processed_documents': 0,
            'successful_extractions': 0,
            'failed_extractions': 0,
            'total_observations': 0,
            'canonical_created': False
        }
    
    # Limit documents for testing if specified
    if max_documents:
        documents = documents[:max_documents]
        logger.info(f"Limited to {max_documents} documents for testing")
    
    # Process each document
    successful = 0
    failed = 0
    total_observations = 0
    results = []
    llm_calls_made = 0
    
    for i, doc in enumerate(documents, 1):
        logger.info(f"Processing document {i}/{len(documents)}: {doc['name']}")
        
        result = process_document_unified(doc, skip_llm=skip_llm)
        results.append(result)
        
        if result['success']:
            successful += 1
            total_observations += result['observations_count']
            if result.get('llm_count', 0) > 0:
                llm_calls_made += 1
        else:
            failed += 1
        
        logger.info(f"Document {doc['name']}: {result['observations_count']} observations (LLM calls: {llm_calls_made})")
        
        # Add delay between documents to respect rate limits (only if LLM is enabled)
        if not skip_llm and i < len(documents):
            delay = max(1, MIN_BEDROCK_INTERVAL_SECONDS // 2)  # Half the rate limit interval
            logger.info(f"Waiting {delay}s before next document to respect rate limits...")
            time.sleep(delay)
    
    # Create canonical JSON from all observations
    canonical_created = False
    if successful > 0:
        try:
            canonical_result = create_minimal_canonical_json_for_patient(patient_id)
            if canonical_result.get('success'):
                canonical_created = True
                logger.info(f"Successfully created canonical JSON for patient {patient_id}")
            else:
                logger.warning(f"Failed to create canonical JSON for patient {patient_id}: {canonical_result.get('message')}")
        except Exception as e:
            logger.error(f"Error creating canonical JSON for patient {patient_id}: {e}")
    
    stats = {
        'patient_id': patient_id,
        'total_documents': len(documents),
        'processed_documents': len(documents),
        'successful_extractions': successful,
        'failed_extractions': failed,
        'total_observations': total_observations,
        'llm_calls_made': llm_calls_made,
        'canonical_created': canonical_created,
        'results': results
    }
    
    logger.info(f"Completed unified processing for patient {patient_id}: {stats}")
    return stats

def process_all_patients_unified(limit_patients: Optional[int] = None, skip_llm: bool = False) -> Dict[str, Any]:
    """
    Process all patients using unified extraction.
    
    Args:
        limit_patients (Optional[int]): Limit number of patients to process
        skip_llm (bool): Whether to skip LLM extraction
        
    Returns:
        Dict: Processing summary
    """
    from vizbriz.flask_app.config.document_observation_extractor_phase2 import get_all_patient_ids
    
    pids = get_all_patient_ids(limit=limit_patients)
    total = len(pids)
    
    logger.info(f"Starting unified processing for {total} patients")
    if skip_llm:
        logger.info("LLM extraction disabled for all patients")
    
    results = []
    total_successful = 0
    total_failed = 0
    total_observations = 0
    total_canonical_created = 0
    
    for i, pid in enumerate(pids, 1):
        logger.info(f"Processing patient {i}/{total}: {pid}")
        
        try:
            stats = process_patient_unified(pid, max_documents=None, skip_llm=skip_llm)
            results.append({'patient_id': pid, 'stats': stats})
            
            if stats.get('successful_extractions', 0) > 0:
                total_successful += 1
            if stats.get('failed_extractions', 0) > 0:
                total_failed += 1
            
            total_observations += stats.get('total_observations', 0)
            
            if stats.get('canonical_created', False):
                total_canonical_created += 1
                
        except Exception as e:
            logger.error(f"Error processing patient {pid}: {e}")
            results.append({'patient_id': pid, 'error': str(e)})
    
    summary = {
        'total_patients': total,
        'successful_patients': total_successful,
        'failed_patients': total_failed,
        'total_observations': total_observations,
        'total_canonical_created': total_canonical_created,
        'results': results
    }
    
    logger.info(f"Completed processing all patients: {summary}")
    return summary

def compare_extraction_methods(patient_id: int) -> Dict[str, Any]:
    """
    Compare schema vs LLM extraction methods for a single patient.
    
    Args:
        patient_id (int): Patient ID to compare
        
    Returns:
        Dict: Comparison results
    """
    logger.info(f"Comparing extraction methods for patient {patient_id}")
    
    # Discover documents
    documents = discover_patient_documents(patient_id)
    
    if not documents:
        logger.info(f"No documents found for patient {patient_id}")
        return {
            'patient_id': patient_id,
            'total_documents': 0,
            'comparison_results': []
        }
    
    comparison_results = []
    
    for doc in documents:
        document_name = doc.get('name', 'unknown')
        logger.info(f"Comparing extraction methods for: {document_name}")
        
        # Extract content
        content = extract_document_content(doc)
        
        if not content:
            comparison_results.append({
                'document_name': document_name,
                'schema_observations': 0,
                'llm_observations': 0,
                'overlap': 0,
                'schema_only': 0,
                'llm_only': 0,
                'error': 'No content extracted'
            })
            continue
        
        # Extract using schema patterns
        schema_observations = extract_with_schema_patterns(content, document_name)
        
        # Extract using LLM
        llm_observations = extract_with_llm(content, document_name, doc.get('source_type', 'unknown'))
        
        # Compare results
        schema_values = set(obs['value'] for obs in schema_observations)
        llm_values = set(obs['value'] for obs in llm_observations)
        
        overlap = len(schema_values & llm_values)
        schema_only = len(schema_values - llm_values)
        llm_only = len(llm_values - schema_values)
        
        comparison_results.append({
            'document_name': document_name,
            'schema_observations': len(schema_observations),
            'llm_observations': len(llm_observations),
            'overlap': overlap,
            'schema_only': schema_only,
            'llm_only': llm_only,
            'schema_examples': list(schema_values)[:3],  # First 3 examples
            'llm_examples': list(llm_values)[:3]  # First 3 examples
        })
    
    # Calculate totals
    total_schema = sum(r['schema_observations'] for r in comparison_results)
    total_llm = sum(r['llm_observations'] for r in comparison_results)
    total_overlap = sum(r['overlap'] for r in comparison_results)
    total_schema_only = sum(r['schema_only'] for r in comparison_results)
    total_llm_only = sum(r['llm_only'] for r in comparison_results)
    
    summary = {
        'patient_id': patient_id,
        'total_documents': len(documents),
        'total_schema_observations': total_schema,
        'total_llm_observations': total_llm,
        'total_overlap': total_overlap,
        'total_schema_only': total_schema_only,
        'total_llm_only': total_llm_only,
        'comparison_results': comparison_results
    }
    
    logger.info(f"Comparison complete for patient {patient_id}: {summary}")
    return summary

def main():
    """Main function with command line interface."""
    parser = argparse.ArgumentParser(description="Unified Document Extraction System")
    parser.add_argument('--mode', choices=['patient', 'all_patients', 'compare', 'test'], default='test')
    parser.add_argument('--patient-id', type=int, default=None, help='Process a single patient ID')
    parser.add_argument('--limit-patients', type=int, default=5, help='Limit number of patients when processing all')
    parser.add_argument('--max-documents', type=int, default=None, help='Maximum documents per patient (for testing)')
    parser.add_argument('--skip-llm', action='store_true', help='Skip LLM extraction to avoid rate limits')
    
    args = parser.parse_args()
    
    if args.mode == 'test':
        # Test with a single patient, limited documents
        test_patient_id = 71100  # Use the test patient from production code
        logger.info(f"Testing unified extraction with patient {test_patient_id}")
        result = process_patient_unified(test_patient_id, max_documents=2, skip_llm=args.skip_llm)
        print(json.dumps(result, indent=2, default=str))
        
    elif args.mode == 'patient' and args.patient_id:
        logger.info(f"Processing single patient {args.patient_id}")
        result = process_patient_unified(args.patient_id, max_documents=args.max_documents, skip_llm=args.skip_llm)
        print(json.dumps(result, indent=2, default=str))
        
    elif args.mode == 'all_patients':
        logger.info(f"Processing all patients (limit: {args.limit_patients})")
        result = process_all_patients_unified(limit_patients=args.limit_patients, skip_llm=args.skip_llm)
        print(json.dumps(result, indent=2, default=str))
        
    elif args.mode == 'compare' and args.patient_id:
        logger.info(f"Comparing extraction methods for patient {args.patient_id}")
        result = compare_extraction_methods(args.patient_id)
        print(json.dumps(result, indent=2, default=str))
        
    else:
        print("Invalid mode or missing patient ID. Use --help for usage information.")

if __name__ == "__main__":
    main()
