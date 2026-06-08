#!/usr/bin/env python3
"""
Direct Vision Extractor - PDF → Images → Bedrock Claude Vision
=============================================================

This script processes PDFs by converting them to images and sending directly
to Bedrock Claude's vision model for temporal sleep data extraction.

No text extraction - the LLM sees the actual document formatting and tables.

Usage:
    python direct_vision_extractor.py --patient-id 25793
"""

import argparse
import logging
import sys
import json
import base64
import os
import tempfile
from datetime import datetime
from pathlib import Path
import re

# PDF to image conversion
try:
    from pdf2image import convert_from_bytes
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False
    logging.warning("pdf2image not available - install with: pip install pdf2image")

# Bedrock and database imports
try:
    from document_observation_extractor_phase2 import (
        DB_CONFIG,
        discover_patient_documents,
        get_s3_client,
        extract_document_content
    )
    from bedrock_config import query_bedrock_claude_enhanced as bedrock_query_enhanced
except ImportError:
    from flask_app.config.document_observation_extractor_phase2 import (
        DB_CONFIG,
        discover_patient_documents,
        get_s3_client,
        extract_document_content
    )
    from flask_app.config.bedrock_config import query_bedrock_claude_enhanced as bedrock_query_enhanced

import mysql.connector
from mysql.connector import Error
import boto3
import requests
from flask_app.services.bedrock_service import BedrockService


def setup_logging():
    """Setup logging for the script"""
    # Create logs directory if it doesn't exist
    import os
    os.makedirs('logs', exist_ok=True)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/direct_vision_extractor.log'),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(__name__)


def download_pdf_from_s3(s3_key):
    """
    Download PDF content from S3 using the same approach as action_routes.py
    
    Args:
        s3_key (str): S3 key of the PDF
        
    Returns:
        bytes: PDF content
    """
    try:
        import boto3
        import os
        
        # Use the same S3 setup as action_routes.py
        s3_client = boto3.client('s3', region_name='us-west-2')
        bucket = os.getenv('S3_BUCKET_NAME', 'vizbrizpatients')
        
        # Generate presigned URL (same approach as action_routes.py)
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': bucket,
                'Key': s3_key,
                'ResponseContentType': 'application/pdf'
            },
            ExpiresIn=3600
        )
        
        # Download using requests (same as existing system)
        import requests
        response = requests.get(presigned_url, timeout=30)
        response.raise_for_status()
        pdf_content = response.content
        
        logging.info(f"Downloaded PDF from S3: {s3_key} ({len(pdf_content)} bytes) using bucket: {bucket}")
        return pdf_content
        
    except Exception as e:
        logging.error(f"Error downloading PDF from S3 {s3_key}: {str(e)}")
        return None


def convert_pdf_to_images(pdf_content, max_pages=10):
    """
    Convert PDF content to images
    
    Args:
        pdf_content (bytes): PDF content
        max_pages (int): Maximum pages to process
        
    Returns:
        list: List of PIL Image objects
    """
    if not PDF2IMAGE_AVAILABLE:
        raise ImportError("pdf2image not available. Install with: pip install pdf2image")
    
    try:
        # Convert PDF to images (300 DPI for good quality)
        images = convert_from_bytes(
            pdf_content,
            dpi=300,
            first_page=1,
            last_page=max_pages,
            fmt='PNG'
        )
        
        logging.info(f"Converted PDF to {len(images)} images")
        return images
        
    except Exception as e:
        logging.error(f"Error converting PDF to images: {str(e)}")
        return []


def image_to_base64(image):
    """
    Convert PIL image to base64 string
    
    Args:
        image: PIL Image object
        
    Returns:
        str: Base64 encoded image
    """
    try:
        import io
        
        # Convert to PNG bytes
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='PNG')
        img_byte_arr = img_byte_arr.getvalue()
        
        # Encode to base64
        base64_str = base64.b64encode(img_byte_arr).decode('utf-8')
        return base64_str
        
    except Exception as e:
        logging.error(f"Error converting image to base64: {str(e)}")
        return None


def extract_comprehensive_observations_from_pdf_vision(
    s3_key: str,
    document_name: str,
    *,
    model_id=None,  # Will use BedrockService default if not provided
    region="us-west-2",
):
    """
    Extract comprehensive medical observations from PDF using direct vision processing (no text extraction).
    Returns: (extracted_observations_dict, success_bool, error_message)
    """
    try:
        # Use BedrockService for model ID if not explicitly provided
        if model_id is None:
            bedrock_service = BedrockService()
            model_id = bedrock_service.MODELS[bedrock_service.DEFAULT_MODEL]
        
        bedrock = boto3.client("bedrock-runtime", region_name=region)

        # Download PDF content from S3 (Bedrock only accepts bytes, not S3 URIs)
        pdf_content = download_pdf_from_s3(s3_key)
        if not pdf_content:
            return {}, False, f"Failed to download PDF from S3: {s3_key}"
        
        # Sanitize document name for Bedrock (strict validation rules)
        def sanitize_bedrock_doc_name(name: str) -> str:
            import os
            import re
            # keep only base name, drop extension
            base = os.path.splitext(os.path.basename(name))[0]
            # replace anything not allowed with a space
            base = re.sub(r'[^A-Za-z0-9\-\(\)\[\]\s]', ' ', base)
            # collapse multiple spaces and trim
            base = re.sub(r'\s+', ' ', base).strip()
            # fallback
            return base or "Document"
        
        safe_name = sanitize_bedrock_doc_name(document_name or s3_key)
        
        document_block = {
            "document": {
                "format": "pdf",
                "name": safe_name,
                "source": {"bytes": pdf_content},
            }
        }
        logging.info(f"Sanitized filename: '{document_name}' → '{safe_name}'")
        logging.info(f"Sending PDF bytes to Bedrock for {safe_name} ({len(pdf_content)} bytes)")

        # Comprehensive medical extraction prompt with full Patient Case JSON v1 schema
        system = [{
            "text": (
                "You extract structured MEDICAL INFORMATION from a clinical sleep study document (PDF/DOCX) following the Patient Case JSON v1 schema.\n"
                "Return ONLY a single STRICT JSON object (no prose). If a value is absent or unclear, use null. Do not guess.\n\n"
                "## OUTPUT SCHEMA (Patient Case JSON v1)\n"
                "{\n"
                "  \"meta\": {\n"
                "    \"patient_name\": \"string|null\",\n"
                "    \"dob\": \"YYYY-MM-DD|null\",\n"
                "    \"mrn\": \"string|null\",\n"
                "    \"sex\": \"M|F|Other|null\",\n"
                "    \"age_years\": number|null,\n"
                "    \"height_cm\": number|null,\n"
                "    \"weight_kg\": number|null,\n"
                "    \"bmi\": number|null,\n"
                "    \"facility\": \"string|null\",\n"
                "    \"ordering_provider\": \"string|null\",\n"
                "    \"report_author\": \"string|null\",\n"
                "    \"date_of_study\": \"YYYY-MM-DD|null\",\n"
                "    \"report_date\": \"YYYY-MM-DD|null\",\n"
                "    \"study_type\": \"HSAT|PSG|Type I|Type II|Type III|Type IV|null\",\n"
                "    \"scoring_hypopnea_rule\": \"AASM_3pct|AASM_4pct|unknown|null\",\n"
                "    \"methodology_notes\": \"string|null\",\n"
                "    \"data_quality\": {\n"
                "      \"overall_quality\": \"good|fair|poor|null\",\n"
                "      \"data_loss_pct\": number|null,\n"
                "      \"comments\": \"string|null\"\n"
                "    }\n"
                "  },\n\n"
                "  \"indications_symptoms\": {\n"
                "    \"primary_indication\": \"string|null\",\n"
                "    \"epworth_score\": number|null,\n"
                "    \"snoring_reported\": true|false|null,\n"
                "    \"witnessed_apneas\": true|false|null,\n"
                "    \"daytime_sleepiness\": true|false|null,\n"
                "    \"insomnia\": true|false|null\n"
                "  },\n\n"
                "  \"comorbidities\": [\n"
                "    {\"condition\": \"string\", \"present\": true|false, \"evidence\": \"string|null\"}\n"
                "  ],\n\n"
                "  \"medications\": [\n"
                "    {\"name\": \"string\", \"dose\": \"string|null\", \"timing\": \"string|null\"}\n"
                "  ],\n\n"
                "  \"prior_therapy\": {\n"
                "    \"cpap\": {\"used\": true|false|null, \"settings\": \"string|null\", \"mask_type\": \"string|null\"},\n"
                "    \"apap\": {\"used\": true|false|null, \"settings\": \"string|null\"},\n"
                "    \"bilevel\": {\"used\": true|false|null, \"settings\": \"string|null\"},\n"
                "    \"oral_appliance\": {\"used\": true|false|null, \"type\": \"string|null\"}\n"
                "  },\n\n"
                "  \"device_adherence_if_applicable\": {\n"
                "    \"nights_ge_4h_pct\": number|null,\n"
                "    \"avg_use_hours\": number|null,\n"
                "    \"residual_ahi\": number|null,\n"
                "    \"median_pressure_cmH2O\": number|null,\n"
                "    \"p95_pressure_cmH2O\": number|null,\n"
                "    \"median_leak_lpm\": number|null\n"
                "  },\n\n"
                "  \"sleep_timing_architecture\": {\n"
                "    \"trt_min\": number|null,\n"
                "    \"tst_min\": number|null,\n"
                "    \"sleep_efficiency_pct\": number|null,\n"
                "    \"sleep_latency_min\": number|null,\n"
                "    \"rem_latency_min\": number|null,\n"
                "    \"wakeup_after_sleep_onset_min\": number|null,\n"
                "    \"stages_pct\": {\"n1\": number|null, \"n2\": number|null, \"n3\": number|null, \"rem\": number|null}\n"
                "  },\n\n"
                "  \"respiratory_indices\": {\n"
                "    \"ahi_overall\": number|null,\n"
                "    \"rdi_overall\": number|null,\n"
                "    \"odi3\": number|null,\n"
                "    \"odi4\": number|null,\n"
                "    \"oai\": number|null,\n"
                "    \"cai\": number|null,\n"
                "    \"mai\": number|null,\n"
                "    \"hi\": number|null,\n"
                "    \"ahi_rem\": number|null,\n"
                "    \"ahi_nrem\": number|null,\n"
                "    \"ahi_supine\": number|null,\n"
                "    \"ahi_non_supine\": number|null\n"
                "  },\n\n"
                "  \"event_counts\": {\n"
                "    \"apnea_total\": number|null,\n"
                "    \"apnea_obstructive\": number|null,\n"
                "    \"apnea_central\": number|null,\n"
                "    \"apnea_mixed\": number|null,\n"
                "    \"hypopnea_total\": number|null\n"
                "  },\n\n"
                "  \"oxygenation\": {\n"
                "    \"spo2_nadir_pct\": number|null,\n"
                "    \"spo2_mean_pct\": number|null,\n"
                "    \"t90_pct\": number|null,\n"
                "    \"t88_pct\": number|null,\n"
                "    \"t85_pct\": number|null,\n"
                "    \"t80_pct\": number|null,\n"
                "    \"t70_pct\": number|null,\n"
                "    \"time_below_90_min\": number|null\n"
                "  },\n\n"
                "  \"snoring\": {\n"
                "    \"snore_index\": number|null,\n"
                "    \"snore_time_pct\": number|null\n"
                "  },\n\n"
                "  \"arousals_movements\": {\n"
                "    \"arousal_index\": number|null,\n"
                "    \"rera_index\": number|null,\n"
                "    \"plmi\": number|null,\n"
                "    \"plm_arousal_index\": number|null\n"
                "  },\n\n"
                "  \"position_stats\": {\n"
                "    \"supine_pct_of_sleep\": number|null,\n"
                "    \"left_pct_of_sleep\": number|null,\n"
                "    \"right_pct_of_sleep\": number|null,\n"
                "    \"prone_pct_of_sleep\": number|null\n"
                "  },\n\n"
                "  \"cardiac\": {\n"
                "    \"avg_hr_bpm\": number|null,\n"
                "    \"min_hr_bpm\": number|null,\n"
                "    \"max_hr_bpm\": number|null,\n"
                "    \"arrhythmia_notes\": \"string|null\"\n"
                "  },\n\n"
                "  \"titration_if_present\": {\n"
                "    \"modality\": \"CPAP|APAP|Bilevel|OA|None|null\",\n"
                "    \"settings\": \"string|null\",\n"
                "    \"recommendation\": \"string|null\"\n"
                "  },\n\n"
                "  \"impression_assessment\": {\n"
                "    \"diagnoses\": [\"string\", \"...\"],\n"
                "    \"ahi_severity_label\": \"mild|moderate|severe|null\",\n"
                "    \"free_text_impression\": \"string|null\",\n"
                "    \"plan_recommendations\": \"string|null\",\n"
                "    \"follow_up_interval\": \"string|null\"\n"
                "  },\n\n"
                "  \"temporal_series\": [\n"
                "    {\n"
                "      \"label\": \"Baseline|Follow-up #1|…|string\",\n"
                "      \"date\": \"YYYY-MM-DD|null\",\n"
                "      \"study_type\": \"baseline|follow_up|unknown\",\n"
                "      \"ahi\": number|null,\n"
                "      \"rdi\": number|null,\n"
                "      \"odi3\": number|null,\n"
                "      \"odi4\": number|null,\n"
                "      \"o2_nadir_pct\": number|null,\n"
                "      \"time_below_90_pct\": number|null,\n"
                "      \"tst_min\": number|null,\n"
                "      \"sleep_efficiency_pct\": number|null,\n"
                "      \"rem_ahi\": number|null,\n"
                "      \"supine_ahi\": number|null\n"
                "    }\n"
                "  ],\n\n"
                "  \"evidence\": {\n"
                "    \"ahi_overall\": \"string|null\",\n"
                "    \"ahi_supine\": \"string|null\",\n"
                "    \"t90_pct\": \"string|null\",\n"
                "    \"odi3\": \"string|null\",\n"
                "    \"odi4\": \"string|null\",\n"
                "    \"spo2_nadir_pct\": \"string|null\",\n"
                "    \"scoring_hypopnea_rule\": \"string|null\"\n"
                "  }\n"
                "}\n\n"
                "## EXTRACTION RULES\n"
                "- Return numbers only (no units or % symbols). Use null if absent.\n"
                "- Search BOTH narrative text and tables throughout the document.\n"
                "- Extract ALL available information from each category.\n"
                "- For comorbidities: Look for conditions like hypertension, diabetes, heart disease, etc.\n"
                "- For medications: Extract drug names, dosages, and timing if mentioned.\n"
                "- For prior therapy: Look for CPAP, APAP, oral appliance history.\n"
                "- For symptoms: Extract Epworth scores, snoring reports, witnessed apneas, etc.\n"
                "- For sleep architecture: Extract sleep stages, latency, efficiency metrics.\n"
                "- For respiratory indices: Extract all AHI variants (overall, REM, NREM, supine, non-supine).\n"
                "- For oxygenation: Extract all O2 saturation metrics and time below thresholds.\n"
                "- For cardiac: Extract heart rate metrics and arrhythmia notes.\n"
                "- For impression: Extract diagnoses, severity labels, and recommendations.\n"
                "- For temporal series: Create entries for baseline vs follow-up studies if present.\n"
                "- Keep evidence snippets short (≤120 chars) and human-readable.\n"
                "- Return ONLY the single JSON object. No extra text."
            )
        }]
        
        # Few-shot examples
        shot_a_input = (
            "Oxygen Saturation <90   <=88  <85  <80  <70\n"
            "Duration (minutes): 3.1  0.0  0.0  0.0  0.0\n"
            "Sleep %:             0.7  0.0  0.0  0.0  0.0\n"
            "Body Position Statistics … Supine … pAHI 42.5 …"
        )
        shot_a_output = {
            "oxygenation": {"t90_pct": 0.7, "time_below_90_min": 3.1},
            "respiratory_indices": {"ahi_supine": 42.5},
            "evidence": {
                "t90_pct": "Sleep %: 0.7 under <90",
                "ahi_supine": "Supine … pAHI 42.5"
            }
        }

        shot_b_input = (
            "Supine AHI 42.5 (62% of sleep time). Less than 90% O2 0.5%.\n"
            "AHI overall 28.2. ODI (3%) 16.1. SpO2 nadir 83%."
        )
        shot_b_output = {
            "respiratory_indices": {"ahi_overall": 28.2, "ahi_supine": 42.5, "odi3": 16.1},
            "oxygenation": {"spo2_nadir_pct": 83, "t90_pct": 0.5},
            "evidence": {
                "ahi_overall": "AHI overall 28.2",
                "ahi_supine": "Supine AHI 42.5",
                "t90_pct": "Less than 90% O2 0.5%",
                "spo2_nadir_pct": "SpO2 nadir 83%"
            }
        }

        # Build messages with few-shot examples
        messages = [
            # FEW-SHOT A
            {"role": "user", "content": [{"text": "Extract per the schema from this slice:\n" + shot_a_input}]},
            {"role": "assistant", "content": [{"text": json.dumps(shot_a_output, ensure_ascii=False)}]},
            # FEW-SHOT B
            {"role": "user", "content": [{"text": "Extract per the schema from this slice:\n" + shot_b_input}]},
            {"role": "assistant", "content": [{"text": json.dumps(shot_b_output, ensure_ascii=False)}]},
            # REAL REQUEST + DOCUMENT
            {"role": "user", "content": [
                {"text": "Now extract ALL fields per the schema from this full document. Return ONLY the single JSON object."},
                document_block,
            ]}
        ]

        resp = bedrock.converse(
            modelId=model_id,
            system=system,
            messages=messages,
            inferenceConfig={"temperature": 0.0, "maxTokens": 4000},
        )
        raw = resp["output"]["message"]["content"][0]["text"]
        
        # Debug: Log the raw response
        logging.info(f"📝 Raw LLM response for {document_name}: {raw[:500]}...")  # First 500 chars
        logging.info(f"📏 Full response length: {len(raw)} characters")
        
        if not raw or not raw.strip():
            logging.error(f"❌ Empty response from LLM for {document_name}")
            return {}, False, f"Empty response from LLM for {document_name}"

        # robust JSON parse (handles code fences/stray text)
        def _parse_json_only(raw_text: str):
            txt = raw_text.strip()
            # remove ```json fences if present
            txt = re.sub(r'^```(?:json)?\s*', '', txt)
            txt = re.sub(r'\s*```$', '', txt)
            # try to capture the first JSON block
            m = re.search(r'(\{.*\}|\[.*\])', txt, flags=re.DOTALL)
            if m:
                txt = m.group(1)
            return json.loads(txt)

        try:
            logging.info(f"🔍 Parsing JSON response for {document_name}...")
            extracted_data = _parse_json_only(raw)
            logging.info(f"✅ Successfully parsed JSON for {document_name}")
            logging.info(f"📊 Extracted data structure: {list(extracted_data.keys()) if isinstance(extracted_data, dict) else 'Not a dict'}")
            return extracted_data, True, None
        except json.JSONDecodeError as e:
            logging.error(f"❌ JSON decode error for {document_name}: {e}")
            logging.error(f"📝 Raw response (first 1000 chars): {raw[:1000]}")
            return {}, False, f"Failed to parse JSON response: {e}"

    except Exception as e:
        msg = f"Error in comprehensive PDF vision extraction for {document_name}: {e}"
        logging.error(msg)
        return {}, False, msg


def store_comprehensive_observations_in_observation_store(patient_id, extracted_observations, document_name):
    """
    Store comprehensive medical observations in observation_store table (like the previous system)
    
    Args:
        patient_id (int): Patient ID
        extracted_observations (dict): Comprehensive observations from LLM
        document_name (str): Name of the source document
        
    Returns:
        tuple: (success_boolean, inserted_count, error_message)
    """
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        cursor = connection.cursor()
        
        inserted_count = 0
        
        # Check if we already have data for this document to prevent duplicates
        logging.info(f"🔍 Checking for existing observations for {document_name}...")
        cursor.execute('''
            SELECT COUNT(*) FROM observation_store 
            WHERE patient_id = %s AND file_name = %s AND source_type = 'comprehensive_llm_extraction'
        ''', (patient_id, document_name))
        
        existing_count = cursor.fetchone()[0]
        if existing_count > 0:
            logging.warning(f"⚠️ Document {document_name} already has {existing_count} observations. Skipping to prevent duplicates.")
            return True, 0, f"Document already processed ({existing_count} existing observations)"
        
        logging.info(f"✅ No existing observations found for {document_name}, proceeding with storage...")
        
        # First, store the complete JSON response in extracted_observations column
        import json
        json_response = json.dumps(extracted_observations, ensure_ascii=False, indent=2)
        
        cursor.execute('''
            INSERT INTO observation_store 
            (patient_id, file_name, source_type, extracted_observations, provider, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
        ''', (
            patient_id, document_name, 'comprehensive_llm_extraction', 
            json_response, 'bedrock_llm'
        ))
        inserted_count += 1
        
        logging.info(f"✅ Stored complete JSON response in extracted_observations column for {document_name} ({len(json_response)} characters)")
        
        # Store meta information
        if 'meta' in extracted_observations:
            meta_data = extracted_observations['meta']
            for metric_key, metric_value in meta_data.items():
                if metric_value is not None:
                    source_kind = 'demographics'
                    if metric_key in ['facility', 'ordering_provider', 'report_author']:
                        source_kind = 'provider_info'
                    elif metric_key in ['study_type', 'scoring_hypopnea_rule', 'methodology_notes']:
                        source_kind = 'sleep_study'
                    
                    metric_value_decimal = None
                    if isinstance(metric_value, (int, float)):
                        metric_value_decimal = float(metric_value)
                    elif isinstance(metric_value, bool):
                        metric_value_decimal = 1.0 if metric_value else 0.0
                    
                    cursor.execute('''
                        INSERT INTO observation_store 
                        (patient_id, file_name, source_type, metric_key, metric_value_decimal, 
                         source_kind, provider, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ''', (
                        patient_id, document_name, 'comprehensive_llm_extraction',
                        metric_key, metric_value_decimal, source_kind, 'bedrock_llm'
                    ))
                    inserted_count += 1
        
        # Store respiratory indices (sleep study metrics)
        if 'respiratory_indices' in extracted_observations:
            resp_data = extracted_observations['respiratory_indices']
            for metric_key, metric_value in resp_data.items():
                if metric_value is not None:
                    source_kind = 'sleep_study'
                    
                    metric_value_decimal = None
                    if isinstance(metric_value, (int, float)):
                        metric_value_decimal = float(metric_value)
                    elif isinstance(metric_value, bool):
                        metric_value_decimal = 1.0 if metric_value else 0.0
                    
                    cursor.execute('''
                        INSERT INTO observation_store 
                        (patient_id, file_name, source_type, metric_key, metric_value_decimal, 
                         source_kind, provider, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ''', (
                        patient_id, document_name, 'comprehensive_llm_extraction',
                        metric_key, metric_value_decimal, source_kind, 'bedrock_llm'
                    ))
                    inserted_count += 1
        
        # Store oxygenation data
        if 'oxygenation' in extracted_observations:
            oxy_data = extracted_observations['oxygenation']
            for metric_key, metric_value in oxy_data.items():
                if metric_value is not None:
                    source_kind = 'sleep_study'
                    
                    metric_value_decimal = None
                    if isinstance(metric_value, (int, float)):
                        metric_value_decimal = float(metric_value)
                    elif isinstance(metric_value, bool):
                        metric_value_decimal = 1.0 if metric_value else 0.0
                    
                    cursor.execute('''
                        INSERT INTO observation_store 
                        (patient_id, file_name, source_type, metric_key, metric_value_decimal, 
                         source_kind, provider, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ''', (
                        patient_id, document_name, 'comprehensive_llm_extraction',
                        metric_key, metric_value_decimal, source_kind, 'bedrock_llm'
                    ))
                    inserted_count += 1
        
        # Store sleep timing architecture
        if 'sleep_timing_architecture' in extracted_observations:
            sleep_data = extracted_observations['sleep_timing_architecture']
            for metric_key, metric_value in sleep_data.items():
                if metric_value is not None:
                    source_kind = 'sleep_study'
                    
                    metric_value_decimal = None
                    if isinstance(metric_value, (int, float)):
                        metric_value_decimal = float(metric_value)
                    elif isinstance(metric_value, bool):
                        metric_value_decimal = 1.0 if metric_value else 0.0
                    
                    cursor.execute('''
                        INSERT INTO observation_store 
                        (patient_id, file_name, source_type, metric_key, metric_value_decimal, 
                         source_kind, provider, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ''', (
                        patient_id, document_name, 'comprehensive_llm_extraction',
                        metric_key, metric_value_decimal, source_kind, 'bedrock_llm'
                    ))
                    inserted_count += 1
        
        # Store cardiac data
        if 'cardiac' in extracted_observations:
            cardiac_data = extracted_observations['cardiac']
            for metric_key, metric_value in cardiac_data.items():
                if metric_value is not None and metric_key != 'arrhythmia_notes':
                    source_kind = 'vital_signs'
                    
                    metric_value_decimal = None
                    if isinstance(metric_value, (int, float)):
                        metric_value_decimal = float(metric_value)
                    elif isinstance(metric_value, bool):
                        metric_value_decimal = 1.0 if metric_value else 0.0
                    
                    cursor.execute('''
                        INSERT INTO observation_store 
                        (patient_id, file_name, source_type, metric_key, metric_value_decimal, 
                         source_kind, provider, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ''', (
                        patient_id, document_name, 'comprehensive_llm_extraction',
                        metric_key, metric_value_decimal, source_kind, 'bedrock_llm'
                    ))
                    inserted_count += 1
        
        # Store vital signs
        if 'vital_signs' in extracted_observations:
            vital_data = extracted_observations['vital_signs']
            for metric_key, metric_value in vital_data.items():
                if metric_value is not None:
                    source_kind = 'vital_signs'
                    
                    metric_value_decimal = None
                    if isinstance(metric_value, (int, float)):
                        metric_value_decimal = float(metric_value)
                    
                    insert_query = """
                    INSERT INTO observation_store 
                    (patient_id, file_name, source_type, metric_key, metric_value_decimal, 
                     source_kind, study_type, observed_at, provider, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    """
                    
                    cursor.execute(insert_query, (
                        patient_id, document_name, 'comprehensive_llm_extraction',
                        metric_key, metric_value_decimal, source_kind, 'unknown',
                        'Unknown', 'bedrock_llm'
                    ))
                    inserted_count += 1
        
        # Store lab values
        if 'lab_values' in extracted_observations:
            lab_data = extracted_observations['lab_values']
            for metric_key, metric_value in lab_data.items():
                if metric_value is not None:
                    source_kind = 'lab_results'
                    
                    metric_value_decimal = None
                    if isinstance(metric_value, (int, float)):
                        metric_value_decimal = float(metric_value)
                    
                    insert_query = """
                    INSERT INTO observation_store 
                    (patient_id, file_name, source_type, metric_key, metric_value_decimal, 
                     source_kind, study_type, observed_at, provider, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    """
                    
                    cursor.execute(insert_query, (
                        patient_id, document_name, 'comprehensive_llm_extraction',
                        metric_key, metric_value_decimal, source_kind, 'unknown',
                        'Unknown', 'bedrock_llm'
                    ))
                    inserted_count += 1
        
        # Store demographics
        if 'demographics' in extracted_observations:
            demo_data = extracted_observations['demographics']
            for metric_key, metric_value in demo_data.items():
                if metric_value is not None:
                    source_kind = 'demographics'
                    
                    metric_value_decimal = None
                    if isinstance(metric_value, (int, float)):
                        metric_value_decimal = float(metric_value)
                    
                    insert_query = """
                    INSERT INTO observation_store 
                    (patient_id, file_name, source_type, metric_key, metric_value_decimal, 
                     source_kind, study_type, observed_at, provider, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    """
                    
                    cursor.execute(insert_query, (
                        patient_id, document_name, 'comprehensive_llm_extraction',
                        metric_key, metric_value_decimal, source_kind, 'unknown',
                        'Unknown', 'bedrock_llm'
                    ))
                    inserted_count += 1
        
        # Store symptoms
        if 'symptoms' in extracted_observations:
            symptom_data = extracted_observations['symptoms']
            for metric_key, metric_value in symptom_data.items():
                if metric_value is not None:
                    source_kind = 'symptoms'
                    
                    # Store boolean symptoms as 1/0
                    metric_value_decimal = 1.0 if metric_value is True else 0.0 if metric_value is False else None
                    
                    insert_query = """
                    INSERT INTO observation_store 
                    (patient_id, file_name, source_type, metric_key, metric_value_decimal, 
                     source_kind, study_type, observed_at, provider, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    """
                    
                    cursor.execute(insert_query, (
                        patient_id, document_name, 'comprehensive_llm_extraction',
                        metric_key, metric_value_decimal, source_kind, 'unknown',
                        'Unknown', 'bedrock_llm'
                    ))
                    inserted_count += 1
        
        # Store temporal series data (multiple time points)
        if 'temporal_series' in extracted_observations and isinstance(extracted_observations['temporal_series'], list):
            for time_point in extracted_observations['temporal_series']:
                study_date = time_point.get('date', 'Unknown')
                study_type = time_point.get('study_type', 'unknown')
                label = time_point.get('label', 'Unknown')
                
                for metric_key, metric_value in time_point.items():
                    if metric_key not in ['date', 'study_type', 'label'] and metric_value is not None:
                        source_kind = 'sleep_study'
                        
                        metric_value_decimal = None
                        if isinstance(metric_value, (int, float)):
                            metric_value_decimal = float(metric_value)
                        elif isinstance(metric_value, bool):
                            metric_value_decimal = 1.0 if metric_value else 0.0
                        
                        cursor.execute('''
                            INSERT INTO observation_store 
                            (patient_id, file_name, source_type, metric_key, metric_value_decimal, 
                             source_kind, study_type, observed_at, provider, created_at, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                        ''', (
                            patient_id, document_name, 'comprehensive_llm_extraction',
                            metric_key, metric_value_decimal, source_kind, study_type,
                            study_date, 'bedrock_llm'
                        ))
                        inserted_count += 1
        
        # Store any additional metrics that don't fit the above categories
        # This handles metrics that the LLM extracted but don't fit the predefined schema
        for category, data in extracted_observations.items():
            if category not in ['meta', 'respiratory_indices', 'oxygenation', 'sleep_timing_architecture', 'cardiac', 'vital_signs', 'lab_values', 'demographics', 'symptoms', 'treatment', 'temporal_series', 'indications_symptoms', 'comorbidities', 'medications', 'prior_therapy', 'device_adherence_if_applicable', 'event_counts', 'snoring', 'arousals_movements', 'position_stats', 'titration_if_present', 'impression_assessment', 'evidence']:
                if isinstance(data, dict):
                    for metric_key, metric_value in data.items():
                        if metric_value is not None:
                            # Determine source_kind based on metric_key patterns
                            source_kind = 'general_observation'
                            if any(keyword in metric_key.lower() for keyword in ['heart', 'blood', 'pressure', 'pulse', 'temp', 'respiratory']):
                                source_kind = 'vital_signs'
                            elif any(keyword in metric_key.lower() for keyword in ['glucose', 'cholesterol', 'hemoglobin', 'lab', 'test']):
                                source_kind = 'lab_results'
                            elif any(keyword in metric_key.lower() for keyword in ['ahi', 'odi', 'rdi', 'sleep', 'rem', 'apnea']):
                                source_kind = 'sleep_study'
                            elif any(keyword in metric_key.lower() for keyword in ['pain', 'fatigue', 'snoring', 'breathing']):
                                source_kind = 'symptoms'
                            
                            metric_value_decimal = None
                            if isinstance(metric_value, (int, float)):
                                metric_value_decimal = float(metric_value)
                            elif isinstance(metric_value, bool):
                                metric_value_decimal = 1.0 if metric_value else 0.0
                            
                            cursor.execute('''
                                INSERT INTO observation_store 
                                (patient_id, file_name, source_type, metric_key, metric_value_decimal, 
                                 source_kind, provider, created_at, updated_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                            ''', (
                                patient_id, document_name, 'comprehensive_llm_extraction',
                                metric_key, metric_value_decimal, source_kind, 'bedrock_llm'
                            ))
                            inserted_count += 1
        
        connection.commit()
        logging.info(f"✅ Successfully stored {inserted_count} comprehensive observations for patient {patient_id}")
        logging.info(f"📊 Storage breakdown:")
        logging.info(f"   - Complete JSON response: 1 record")
        logging.info(f"   - Individual metrics: {inserted_count - 1} records")
        return True, inserted_count, None
        
    except Exception as e:
        error_msg = f"Database error storing comprehensive observations: {str(e)}"
        logging.error(error_msg)
        return False, 0, error_msg
    finally:
        if 'connection' in locals():
            connection.close()


def store_temporal_data_in_observation_store(patient_id, time_points, document_name):
    """
    Store extracted temporal data in observation_store table
    
    Args:
        patient_id (int): Patient ID
        time_points (list): Array of time point data from vision LLM
        document_name (str): Name of the source document
        
    Returns:
        tuple: (success_boolean, inserted_count, error_message)
    """
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        cursor = connection.cursor()
        
        extraction_date = datetime.now()
        inserted_count = 0
        
        for time_point in time_points:
            study_date = time_point.get('date', 'Unknown')
            study_type = time_point.get('study_type', 'unknown')
            
            # Extract all metrics except date and study_type
            for metric_key, metric_value in time_point.items():
                if metric_key not in ['date', 'study_type'] and metric_value is not None:
                    
                    # Determine source_kind based on metric type
                    source_kind = 'sleep_study'  # default
                    if any(keyword in metric_key.lower() for keyword in ['blood_pressure', 'heart_rate', 'weight', 'height', 'bmi']):
                        source_kind = 'vital_signs'
                    elif any(keyword in metric_key.lower() for keyword in ['glucose', 'cholesterol', 'hemoglobin', 'lab']):
                        source_kind = 'lab_results'
                    elif any(keyword in metric_key.lower() for keyword in ['medication', 'dosage', 'drug']):
                        source_kind = 'medication'
                    elif any(keyword in metric_key.lower() for keyword in ['symptom', 'complaint', 'pain']):
                        source_kind = 'symptoms'
                    elif any(keyword in metric_key.lower() for keyword in ['ahi', 'odi', 'o2', 'sleep', 'rem', 'supine']):
                        source_kind = 'sleep_study'
                    else:
                        source_kind = 'general_observation'
                    
                    # Try to convert to decimal for numerical values, store as text for others
                    metric_value_decimal = None
                    if isinstance(metric_value, (int, float)):
                        metric_value_decimal = float(metric_value)
                    elif isinstance(metric_value, str) and metric_value.replace('.', '').replace('-', '').isdigit():
                        try:
                            metric_value_decimal = float(metric_value)
                        except ValueError:
                            pass
                    
                    # Insert into observation_store using the same pattern as existing system
                    insert_query = """
                    INSERT INTO observation_store 
                    (patient_id, file_name, source_type, metric_key, metric_value_decimal, 
                     source_kind, study_type, observed_at, provider, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    """
                    
                    cursor.execute(insert_query, (
                        patient_id,
                        document_name,
                        'vision_extraction',
                        metric_key,
                        metric_value_decimal,
                        source_kind,
                        study_type,
                        study_date,
                        'bedrock_vision'
                    ))
                    
                    inserted_count += 1
                    logging.info(f"Stored {metric_key}={metric_value} ({source_kind}) for {study_date} ({study_type})")
        
        connection.commit()
        logging.info(f"Successfully stored {inserted_count} observations for patient {patient_id}")
        
        return True, inserted_count, None
        
    except Error as e:
        error_msg = f"Database error storing vision data: {str(e)}"
        logging.error(error_msg)
        return False, 0, error_msg
        
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()


def cleanup_patient_data(patient_id, canonical_only=False):
    """
    Delete existing observations and canonical schema for patient
    
    Args:
        patient_id (int): Patient ID
        canonical_only (bool): If True, only delete canonical data, preserve observations
        
    Returns:
        tuple: (success_boolean, error_message)
    """
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        cursor = connection.cursor()
        
        if canonical_only:
            logging.info(f"🧹 Cleaning up canonical data only for patient {patient_id}")
        else:
            logging.info(f"🧹 Cleaning up all data for patient {patient_id}")
        
        observations_deleted = 0
        canonical_deleted = 0
        
        # Delete from observation_store (only if not canonical_only)
        if not canonical_only:
            delete_observations_query = "DELETE FROM observation_store WHERE patient_id = %s"
            cursor.execute(delete_observations_query, (patient_id,))
            observations_deleted = cursor.rowcount
        
        # Delete canonical schema from patient_case_envelope
        delete_canonical_query = "DELETE FROM patient_case_envelope WHERE patient_id = %s"
        cursor.execute(delete_canonical_query, (patient_id,))
        canonical_deleted = cursor.rowcount
        
        connection.commit()
        
        if canonical_only:
            logging.info(f"✅ Canonical cleanup complete for patient {patient_id}:")
            logging.info(f"   - Deleted {canonical_deleted} case envelopes")
            logging.info(f"   - Preserved all observations in observation_store")
        else:
            logging.info(f"✅ Full cleanup complete for patient {patient_id}:")
            logging.info(f"   - Deleted {observations_deleted} observations")
            logging.info(f"   - Deleted {canonical_deleted} case envelopes")
        
        return True, None
        
    except Error as e:
        error_msg = f"Database error during cleanup for patient {patient_id}: {str(e)}"
        logging.error(error_msg)
        return False, error_msg
        
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()


def process_document_with_vision(patient_id, document):
    """
    Process a single document using vision extraction
    
    Args:
        patient_id (int): Patient ID
        document (dict): Document metadata
        
    Returns:
        dict: Processing results
    """
    doc_name = document.get('name', 'Unknown')
    doc_s3_key = document.get('s3_key', '')
    doc_id = document.get('id')
    
    logging.info(f"🔍 Processing document with vision: {doc_name}")
    logging.info(f"   📁 S3 Key: {doc_s3_key}")
    logging.info(f"   🆔 Document ID: {doc_id}")
    
    # Only process PDFs for now
    if not doc_name.lower().endswith('.pdf'):
        logging.warning(f"⚠️ Skipping non-PDF file: {doc_name}")
        return {
            'success': False,
            'filename': doc_name,
            'error': 'Only PDF files supported for vision extraction',
            'stage': 'file_type_check'
        }
    
    try:
        # Step 1: Extract comprehensive observations using direct PDF vision processing
        logging.info(f"🤖 Step 1: Extracting observations from {doc_name} using Bedrock vision...")
        extracted_observations, llm_success, llm_error = extract_comprehensive_observations_from_pdf_vision(
            doc_s3_key, doc_name
        )
        if not llm_success:
            logging.error(f"❌ LLM extraction failed for {doc_name}: {llm_error}")
            return {
                'success': False,
                'filename': doc_name,
                'error': llm_error,
                'stage': 'vision_llm_extraction'
            }
        
        logging.info(f"✅ LLM extraction successful for {doc_name}")
        logging.info(f"   📊 Extracted {len(extracted_observations)} top-level categories")
        
        # Log what was extracted
        for category, data in extracted_observations.items():
            if isinstance(data, dict):
                logging.info(f"   📋 {category}: {len(data)} fields")
            elif isinstance(data, list):
                logging.info(f"   📋 {category}: {len(data)} items")
            else:
                logging.info(f"   📋 {category}: {data}")
        
        # Step 2: Store comprehensive observations in observation_store
        logging.info(f"💾 Step 2: Storing observations in database for {doc_name}...")
        store_success, inserted_count, store_error = store_comprehensive_observations_in_observation_store(
            patient_id, extracted_observations, doc_name
        )
        if not store_success:
            logging.error(f"❌ Database storage failed for {doc_name}: {store_error}")
            return {
                'success': False,
                'filename': doc_name,
                'error': store_error,
                'stage': 'database_storage'
            }
        
        logging.info(f"✅ Database storage successful for {doc_name} - {inserted_count} observations stored")
        
        return {
            'success': True,
            'filename': doc_name,
            'document_id': doc_id,
            'observations_stored': inserted_count,
            'method': 'comprehensive_vision_llm'
        }
        
    except Exception as e:
        logging.error(f"❌ Unexpected error processing {doc_name}: {str(e)}")
        import traceback
        logging.error(f"Full traceback: {traceback.format_exc()}")
        return {
            'success': False,
            'filename': doc_name,
            'error': str(e),
            'stage': 'processing'
        }


def process_patient_with_vision(patient_id):
    """
    Process all PDF documents for a patient using vision extraction
    
    Args:
        patient_id (int): Patient ID
        
    Returns:
        dict: Processing summary
    """
    try:
        logging.info(f"🚀 Processing patient {patient_id} with vision extraction")
        
        # Get patient documents
        logging.info(f"📋 Discovering documents for patient {patient_id}...")
        patient_documents = discover_patient_documents(patient_id)
        
        if not patient_documents:
            logging.warning(f"⚠️ No documents found for patient {patient_id}")
            return {
                'success': False,
                'error': f"No documents found for patient {patient_id}",
                'total_files': 0
            }
        
        logging.info(f"📄 Found {len(patient_documents)} total documents for patient {patient_id}")
        
        # Filter for PDF documents only
        pdf_documents = [doc for doc in patient_documents if doc.get('name', '').lower().endswith('.pdf')]
        
        logging.info(f"📄 Found {len(pdf_documents)} PDF documents for patient {patient_id}")
        
        # Log each document found
        for i, doc in enumerate(pdf_documents, 1):
            logging.info(f"   {i}. {doc.get('name', 'Unknown')} (ID: {doc.get('id', 'N/A')})")
        
        results = {
            'total_files': len(pdf_documents),
            'successful': 0,
            'failed': 0,
            'total_observations': 0,
            'file_results': [],
            'patient_id': patient_id
        }
        
        # Process each PDF document
        logging.info(f"🔄 Starting to process {len(pdf_documents)} PDF documents...")
        for i, doc in enumerate(pdf_documents, 1):
            logging.info(f"📄 Processing document {i}/{len(pdf_documents)}: {doc.get('name', 'Unknown')}")
            result = process_document_with_vision(patient_id, doc)
            results['file_results'].append(result)
            
            if result['success']:
                results['successful'] += 1
                results['total_observations'] += result['observations_stored']
                logging.info(f"✅ Successfully processed {result['filename']} - {result['observations_stored']} observations stored")
            else:
                results['failed'] += 1
                logging.error(f"❌ Failed to process {result['filename']}: {result['error']} (stage: {result.get('stage', 'unknown')})")
        
        logging.info(f"🎉 Document processing complete! {results['successful']} successful, {results['failed']} failed")
        results['success'] = True
        return results
        
    except Exception as e:
        error_msg = f"Error processing patient {patient_id} with vision: {str(e)}"
        logging.error(error_msg)
        import traceback
        logging.error(f"Full traceback: {traceback.format_exc()}")
        return {
            'success': False,
            'error': error_msg,
            'patient_id': patient_id
        }


def generate_canonical_manifest_from_observation_store(patient_id):
    """
    Generate canonical manifest from observation store data using the existing script
    
    Args:
        patient_id (int): Patient ID
        
    Returns:
        tuple: (success_boolean, canonical_data, error_message)
    """
    try:
        import subprocess
        import os
        
        logging.info(f"🔄 Generating canonical manifest for patient {patient_id} using existing script")
        
        # Get the script directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(script_dir, 'document_observation_extractor_phase2.py')
        
        # Run the existing script with regenerate_canonical mode
        cmd = [
            'python', script_path,
            '--mode', 'regenerate_canonical',
            '--patient-id', str(patient_id)
        ]
        
        logging.info(f"Running command: {' '.join(cmd)}")
        
        # Execute the script
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=script_dir
        )
        
        if result.returncode == 0:
            logging.info(f"✅ Successfully generated canonical manifest for patient {patient_id}")
            logging.info(f"Script output: {result.stdout}")
            return True, None, None
        else:
            error_msg = f"Script failed with return code {result.returncode}: {result.stderr}"
            logging.error(f"❌ Failed to generate canonical manifest: {error_msg}")
            return False, None, error_msg
            
    except Exception as e:
        error_msg = f"Error running canonical generation script: {str(e)}"
        logging.error(error_msg)
        return False, None, error_msg


def main():
    """Main script execution"""
    parser = argparse.ArgumentParser(description='Direct Vision Extractor - PDF → Images → Bedrock Vision')
    parser.add_argument('--patient-id', type=int, required=True, help='Patient ID')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    parser.add_argument('--skip-cleanup', action='store_true', help='Skip deleting existing data')
    parser.add_argument('--generate-canonical', action='store_true', help='Generate canonical manifest from observation store')
    
    args = parser.parse_args()
    
    # Setup logging
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    logger = setup_logging()
    
    logger.info(f"🚀 Starting Direct PDF Extraction for Patient {args.patient_id}")
    logger.info(f"📄 Method: S3 PDF URI → Bedrock Claude Converse API")
    logger.info(f"✨ LLM processes PDF directly - no conversion needed!")
    logger.info(f"🎯 Perfect for comparison tables and temporal data!")
    
    try:
        # Step 1: Cleanup existing data
        if args.generate_canonical:
            # When generating canonical, only clean canonical data, preserve observations
            cleanup_success, cleanup_error = cleanup_patient_data(args.patient_id, canonical_only=True)
            if not cleanup_success:
                logger.error(f"❌ Canonical cleanup failed: {cleanup_error}")
                sys.exit(1)
        elif not args.skip_cleanup:
            # Full cleanup for normal processing
            cleanup_success, cleanup_error = cleanup_patient_data(args.patient_id, canonical_only=False)
            if not cleanup_success:
                logger.error(f"❌ Cleanup failed: {cleanup_error}")
                sys.exit(1)
        else:
            logger.info(f"⏭️ Skipping cleanup as requested")
        
        # Step 2: Process patient documents with vision (unless only generating canonical)
        if not args.generate_canonical:
            results = process_patient_with_vision(args.patient_id)
            
            if not results['success']:
                logger.error(f"❌ FAILED to process patient {args.patient_id}: {results['error']}")
                sys.exit(1)
            
            logger.info(f"🎉 DIRECT PDF EXTRACTION COMPLETE!")
            logger.info(f"   Patient ID: {results['patient_id']}")
            logger.info(f"   PDF documents processed: {results['total_files']}")
            logger.info(f"   Successful: {results['successful']}")
            logger.info(f"   Failed: {results['failed']}")
            logger.info(f"   Total observations stored: {results['total_observations']}")
            
            # Show individual document results
            logger.info(f"📋 Document Processing Details:")
            for result in results['file_results']:
                if result['success']:
                    logger.info(f"   ✅ {result['filename']}: {result['observations_stored']} observations")
                else:
                    logger.error(f"   ❌ {result['filename']}: {result['error']} (stage: {result.get('stage', 'unknown')})")
            
            # Final success message
            if results['successful'] > 0:
                logger.info(f"🎉 SUCCESS: Patient {args.patient_id} processed with DIRECT PDF EXTRACTION!")
                logger.info(f"📊 {results['total_observations']} temporal observations extracted from native PDF processing")
                logger.info(f"💡 LLM processed actual PDF documents - perfect table parsing!")
            else:
                logger.warning(f"⚠️  No documents were successfully processed for patient {args.patient_id}")
        
        # Step 3: Generate canonical manifest from observation store
        logger.info(f"📋 Generating canonical manifest from observation store...")
        canonical_success, canonical_data, canonical_error = generate_canonical_manifest_from_observation_store(args.patient_id)
        
        if canonical_success:
            logger.info(f"✅ Canonical manifest generated successfully!")
            logger.info(f"   Used existing script to regenerate canonical from observation store")
            logger.info(f"🔗 Next: Check patient workflow at /patient_workflow_manifest/{args.patient_id}")
        else:
            logger.error(f"❌ Failed to generate canonical manifest: {canonical_error}")
            sys.exit(1)
    
    except KeyboardInterrupt:
        logger.info("Script interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == '__main__':
    main()
