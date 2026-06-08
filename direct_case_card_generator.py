#!/usr/bin/env python3
"""
Direct Case-Card Generator
Extracts clinical data directly from original files without PHI redaction
Only extracts non-PHI clinical metrics - no free text or PHI is stored
"""

import os
import re
import json
import time
import hashlib
import hmac
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
import boto3
from botocore.exceptions import ClientError
import mysql.connector
from mysql.connector import Error
import pdfplumber
from docx import Document

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('direct_case_card_generator.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class DirectCaseCardGenerator:
    """Direct case-card generator - extracts only clinical metrics, no PHI"""
    
    def __init__(self):
        """Initialize the generator with configuration"""
        self.s3_client = boto3.client('s3')
        self.source_bucket = os.environ.get('SOURCE_BUCKET', 'vizbrizknowledgebase')
        self.research_bucket = os.environ.get('RESEARCH_BUCKET', 'vizbrizknowledgebase')
        self.hmac_secret = os.environ.get('HMAC_SECRET', 'your-secret-key-here')
        
        # Enhanced extraction patterns - ONLY for clinical metrics
        self.patterns = {
            # Sleep metrics - comprehensive patterns
            'AHI': [
                r'\bAHI\b\s*[:=]?\s*(\d+(?:\.\d+)?)',
                r'Apnea.*Hypopnea.*Index.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'apnea.*hypopnea.*index.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'AHI.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'apnea.*hypopnea.*index.*(\d+(?:\.\d+)?)',
            ],
            'RDI': [
                r'\bRDI\b\s*[:=]?\s*(\d+(?:\.\d+)?)',
                r'Respiratory.*Disturbance.*Index.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'respiratory.*disturbance.*index.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'RDI.*[:=]?\s*(\d+(?:\.\d+)?)',
            ],
            'ODI': [
                r'\bODI\b\s*[:=]?\s*(\d+(?:\.\d+)?)',
                r'Oxygen.*Desaturation.*Index.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'oxygen.*desaturation.*index.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'ODI.*[:=]?\s*(\d+(?:\.\d+)?)',
            ],
            'O2_nadir': [
                r'(?:O2|Oxygen)\s*(?:Nadir|NADIR|nadir)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%?',
                r'oxygen.*saturation.*low.*[:=]?\s*(\d+(?:\.\d+)?)\s*%?',
                r'SpO2.*low.*[:=]?\s*(\d+(?:\.\d+)?)\s*%?',
                r'lowest.*oxygen.*[:=]?\s*(\d+(?:\.\d+)?)\s*%?',
                r'O2.*nadir.*[:=]?\s*(\d+(?:\.\d+)?)',
            ],
            'sleep_efficiency_pct': [
                r'sleep\s+efficiency\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%',
                r'efficiency\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%',
                r'total.*sleep.*time.*[:=]?\s*(\d+(?:\.\d+)?)\s*%',
                r'sleep.*efficiency.*(\d+(?:\.\d+)?)\s*%',
            ],
            
            # Positional AHI
            'supine_AHI': [
                r'supine.*AHI.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'AHI.*supine.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'supine.*apnea.*[:=]?\s*(\d+(?:\.\d+)?)',
            ],
            'non_supine_AHI': [
                r'non.*supine.*AHI.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'AHI.*non.*supine.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'lateral.*AHI.*[:=]?\s*(\d+(?:\.\d+)?)',
            ],
            'REM_AHI': [
                r'REM.*AHI.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'AHI.*REM.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'REM.*apnea.*[:=]?\s*(\d+(?:\.\d+)?)',
            ],
            'NREM_AHI': [
                r'NREM.*AHI.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'AHI.*NREM.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'NREM.*apnea.*[:=]?\s*(\d+(?:\.\d+)?)',
            ],
            
            # PROs - comprehensive patterns
            'ESS': [
                r'Epworth.*[:=]?\s*(\d{1,2})',
                r'ESS.*[:=]?\s*(\d{1,2})',
                r'Epworth.*Sleepiness.*Scale.*[:=]?\s*(\d{1,2})',
                r'epworth.*sleepiness.*[:=]?\s*(\d{1,2})',
                r'Epworth.*(\d{1,2})',
            ],
            'STOP_Bang': [
                r'STOP[-\s]?Bang.*[:=]?\s*(\d{1,2})',
                r'STOP.*Bang.*[:=]?\s*(\d{1,2})',
                r'stop.*bang.*[:=]?\s*(\d{1,2})',
                r'STOP.*Bang.*(\d{1,2})',
            ],
            'NOSE': [
                r'NOSE.*[:=]?\s*(\d{1,2})',
                r'Nasal.*Obstruction.*Symptom.*[:=]?\s*(\d{1,2})',
                r'nose.*score.*[:=]?\s*(\d{1,2})',
                r'NOSE.*(\d{1,2})',
            ],
            'PSQI': [
                r'PSQI.*[:=]?\s*(\d{1,2})',
                r'Pittsburgh.*Sleep.*Quality.*[:=]?\s*(\d{1,2})',
                r'pittsburgh.*sleep.*quality.*[:=]?\s*(\d{1,2})',
                r'PSQI.*(\d{1,2})',
            ],
            
            # Physical exam
            'Mallampati': [
                r'Mallampati.*[:=]?\s*([1-4])',
                r'mallampati.*[:=]?\s*([1-4])',
                r'MP.*[:=]?\s*([1-4])',
                r'Mallampati.*([1-4])',
            ],
            'Tonsil': [
                r'Tonsil.*[:=]?\s*([0-4])',
                r'tonsil.*[:=]?\s*([0-4])',
                r'Tonsil.*Score.*[:=]?\s*([0-4])',
                r'Tonsil.*([0-4])',
            ],
            
            # Demographics - only age and sex, no names/addresses
            'age': [
                r'\b(\d{1,3})\s*(?:yo|y\.o\.|year[s]?\s*old|yr[s]?\s*old)\b',
                r'\b(?:age|aged?)\s*:?\s*(\d{1,3})\b',
                r'\((\d{1,3})yo\b',
                r'\b(\d{1,3})\s*years?\s*of\s*age\b',
                r'\b(\d{1,3})\s*y\.o\.\b',
                r'\b(\d{1,3})\s*yrs?\s*old\b',
                r'age.*(\d{1,3})',
            ],
            'BMI': [
                r'\bBMI\b\s*[:=]?\s*(\d+(?:\.\d+)?)',
                r'Body.*Mass.*Index.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'body.*mass.*index.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'BMI.*(\d+(?:\.\d+)?)',
            ],
            'sex': [
                r'\b(?:male|female|M|F)\b',
                r'\b(?:Male|Female)\b',
                r'Gender.*[:=]?\s*(?:male|female|M|F)',
                r'sex.*[:=]?\s*(?:male|female|M|F)',
            ],
            
            # Clinical flags
            'cpap_intolerance': [
                r'\b(?:cpap|CPAP)\s*(?:intolerance|intolerant)\b',
                r'cpap.*intolerance',
                r'cpap.*intolerant',
                r'CPAP.*intolerance',
            ],
            'comorbidities': [
                r'\b(?:obesity|HTN|hypertension|diabetes|DM|COPD|asthma|depression|anxiety|heart.*disease|cardiac|pulmonary|respiratory|sleep.*apnea|OSA)\b',
            ]
        }
    
    def extract_text_from_file(self, file_content: bytes, file_extension: str) -> str:
        """Extract text from different file types"""
        try:
            if file_extension in {".txt", ".md"}:
                return file_content.decode("utf-8", errors="ignore")
            
            elif file_extension == ".docx":
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp_file:
                    tmp_file.write(file_content)
                    tmp_file.flush()
                    
                    doc = Document(tmp_file.name)
                    text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
                    
                    os.unlink(tmp_file.name)
                    return text
            
            elif file_extension == ".pdf":
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
                    tmp_file.write(file_content)
                    tmp_file.flush()
                    
                    text_chunks = []
                    with pdfplumber.open(tmp_file.name) as pdf:
                        for page in pdf.pages:
                            page_text = page.extract_text()
                            if page_text:
                                text_chunks.append(page_text)
                    
                    os.unlink(tmp_file.name)
                    return "\n\n".join(text_chunks)
            
            else:
                return ""
                
        except Exception as e:
            logger.error(f"Error extracting text from {file_extension} file: {e}")
            return ""
    
    def extract_clinical_data_enhanced(self, text: str) -> Dict[str, Any]:
        """Enhanced clinical data extraction with multiple patterns per field"""
        extracted = {}
        
        for field, patterns in self.patterns.items():
            try:
                if field == 'comorbidities':
                    # Handle comorbidities separately
                    comorbidities = []
                    for pattern in patterns:
                        matches = re.findall(pattern, text, re.IGNORECASE)
                        comorbidities.extend([m.lower() for m in matches])
                    if comorbidities:
                        extracted[field] = list(set(comorbidities))
                    continue
                
                # Try each pattern for the field
                for pattern in patterns:
                    matches = re.findall(pattern, text, re.IGNORECASE)
                    if matches:
                        if field == 'age':
                            # Age extraction - get the first reasonable age
                            for match in matches:
                                age_value = int(match[0]) if isinstance(match, tuple) else int(match)
                                if 0 <= age_value <= 120:
                                    extracted[field] = age_value
                                    break
                        elif field == 'sex':
                            # Sex extraction
                            sex_match = re.search(pattern, text, re.IGNORECASE)
                            if sex_match:
                                sex_text = sex_match.group(0).lower()
                                if 'male' in sex_text or 'm' in sex_text:
                                    extracted[field] = 'M'
                                elif 'female' in sex_text or 'f' in sex_text:
                                    extracted[field] = 'F'
                        elif field == 'cpap_intolerance':
                            # Boolean flag
                            extracted[field] = bool(re.search(pattern, text, re.IGNORECASE))
                        else:
                            # Numeric values
                            value = matches[0] if isinstance(matches[0], str) else matches[0][0]
                            try:
                                if field in ['ESS', 'STOP_Bang', 'NOSE', 'PSQI', 'Mallampati', 'Tonsil']:
                                    extracted[field] = int(float(value))
                                else:
                                    extracted[field] = float(value)
                            except (ValueError, TypeError):
                                pass
                        break  # Found a match, move to next field
                        
            except Exception as e:
                logger.warning(f"Error extracting {field}: {e}")
        
        return extracted
    
    def process_s3_file(self, bucket: str, key: str) -> bool:
        """Process a single S3 file directly"""
        try:
            logger.info(f"Processing: s3://{bucket}/{key}")
            
            # Download file
            response = self.s3_client.get_object(Bucket=bucket, Key=key)
            file_content = response['Body'].read()
            
            # Get file extension
            file_extension = os.path.splitext(key)[1].lower()
            
            # Extract text
            text = self.extract_text_from_file(file_content, file_extension)
            if not text:
                logger.warning(f"No text content found in: {key}")
                return False
            
            logger.info(f"Extracted {len(text)} characters from {key}")
            
            # Extract clinical data with enhanced patterns
            extracted_data = self.extract_clinical_data_enhanced(text)
            logger.info(f"Extracted {len(extracted_data)} fields: {list(extracted_data.keys())}")
            
            # Create case card
            source_uri = f"s3://{bucket}/{key}"
            case_card = self.create_case_card(extracted_data, source_uri)
            
            # Validate
            validation_result = self.validate_case_card(case_card)
            case_card['validation'] = validation_result
            
            # Upload to research bucket
            output_key = f"precedent_cases/{case_card['case_id']}.json"
            self.s3_client.put_object(
                Bucket=self.research_bucket,
                Key=output_key,
                Body=json.dumps(case_card, indent=2),
                ContentType='application/json',
                Metadata={
                    'type': 'precedent_case',
                    'patient_rid': case_card['patient_rid'],
                    'case_id': case_card['case_id'],
                    'doc_type': 'report'
                }
            )
            
            logger.info(f"Uploaded case card: s3://{self.research_bucket}/{output_key}")
            return True
            
        except Exception as e:
            logger.error(f"Error processing {bucket}/{key}: {e}")
            return False
    
    def create_case_card(self, extracted_data: Dict[str, Any], source_uri: str) -> Dict[str, Any]:
        """Create case-card JSON structure"""
        source_hint = source_uri.replace('s3://', '').replace('/', '_')
        patient_rid = self.generate_patient_rid(source_hint)
        case_id = self.generate_case_id(source_hint)
        
        # Build features object
        features = {}
        for field in ['age', 'sex', 'BMI', 'AHI', 'RDI', 'ODI', 'O2_nadir', 
                      'sleep_efficiency_pct', 'supine_AHI', 'non_supine_AHI',
                      'REM_AHI', 'NREM_AHI', 'ESS', 'STOP_Bang', 'NOSE', 'PSQI',
                      'Mallampati', 'Tonsil', 'cpap_intolerance']:
            features[field] = extracted_data.get(field)
        
        # Handle comorbidities
        if 'comorbidities' in extracted_data:
            features['comorbidities'] = extracted_data['comorbidities']
        else:
            features['comorbidities'] = []
        
        # Create case card
        case_card = {
            "type": "precedent_case",
            "version": "cc-1",
            "case_id": case_id,
            "patient_rid": patient_rid,
            "study_date": None,
            "features": features,
            "cbct": {
                "scan_id": None,
                "min_csa_mm2": None,
                "airway_vol_ml": None,
                "tongue_vol_ml": None,
                "hyoid_mand_mm": None
            },
            "therapy": None,
            "outcome": None,
            "labels": None,
            "provenance": {
                "source_uri": source_uri,
                "note": f"Generated on {datetime.now().isoformat()}"
            },
            "validation": {
                "errors": [],
                "warnings": []
            }
        }
        
        return case_card
    
    def generate_patient_rid(self, source_hint: str) -> str:
        """Generate pseudonymous patient RID"""
        return "rid-" + hmac.new(
            self.hmac_secret.encode(),
            source_hint.encode(),
            hashlib.sha256
        ).hexdigest()[:15]
    
    def generate_case_id(self, source_hint: str, case_type: str = "psg") -> str:
        """Generate pseudonymous case ID"""
        return "case-" + hmac.new(
            self.hmac_secret.encode(),
            f"{source_hint}:{case_type}".encode(),
            hashlib.sha256
        ).hexdigest()[:15]
    
    def validate_case_card(self, case_card: Dict[str, Any]) -> Dict[str, List[str]]:
        """Basic validation of case card"""
        errors = []
        warnings = []
        
        # Check required fields
        required_fields = ['type', 'version', 'case_id', 'patient_rid', 'features', 'provenance']
        for field in required_fields:
            if field not in case_card:
                errors.append(f"Missing required field: {field}")
        
        # Check features
        features = case_card.get('features', {})
        if not isinstance(features, dict):
            errors.append("Features must be an object")
        
        return {"errors": errors, "warnings": warnings}
    
    def scan_and_process_original_files(self, prefix: str = ""):
        """Scan S3 bucket and process original files directly"""
        logger.info(f"Scanning S3 bucket: {self.source_bucket} with prefix: {prefix}")
        
        processed_count = 0
        error_count = 0
        
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            
            for page in paginator.paginate(Bucket=self.source_bucket, Prefix=prefix):
                if 'Contents' not in page:
                    continue
                
                for obj in page['Contents']:
                    key = obj['Key']
                    
                    # Skip directories
                    if key.endswith('/'):
                        continue
                    
                    # Skip already processed case-cards
                    if key.startswith('precedent_cases/'):
                        continue
                    
                    # Only process supported file types
                    file_extension = os.path.splitext(key)[1].lower()
                    if file_extension not in {'.pdf', '.docx', '.txt', '.md'}:
                        logger.info(f"Skipping unsupported file type: {key}")
                        continue
                    
                    # Process file
                    if self.process_s3_file(self.source_bucket, key):
                        processed_count += 1
                    else:
                        error_count += 1
                    
                    # Small delay to avoid overwhelming the system
                    time.sleep(0.1)
            
            logger.info(f"Processing complete: {processed_count} successful, {error_count} errors")
            
        except Exception as e:
            logger.error(f"Error scanning S3 bucket: {e}")

def main():
    """Main function"""
    logger.info("Starting Direct Case-Card Generator")
    
    generator = DirectCaseCardGenerator()
    
    try:
        # Process original files directly
        generator.scan_and_process_original_files()
        
    except KeyboardInterrupt:
        logger.info("Processing interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        logger.info("Direct Case-Card Generator finished")

if __name__ == "__main__":
    main()

