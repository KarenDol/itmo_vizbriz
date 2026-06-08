#!/usr/bin/env python3
"""
Improved Case-Card Generator
Processes original files directly with better clinical data extraction
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
        logging.FileHandler('improved_case_card_generator.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ImprovedCaseCardGenerator:
    """Improved case-card generator with better clinical data extraction"""
    
    def __init__(self):
        """Initialize the generator with configuration"""
        self.s3_client = boto3.client('s3')
        self.source_bucket = os.environ.get('SOURCE_BUCKET', 'vizbrizknowledgebase')
        self.research_bucket = os.environ.get('RESEARCH_BUCKET', 'vizbrizknowledgebase')
        self.hmac_secret = os.environ.get('HMAC_SECRET', 'your-secret-key-here')
        
        # Enhanced extraction patterns
        self.patterns = {
            # Sleep metrics - more comprehensive patterns
            'AHI': [
                r'\bAHI\b\s*[:=]?\s*(\d+(?:\.\d+)?)',
                r'Apnea.*Hypopnea.*Index.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'apnea.*hypopnea.*index.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'AHI.*[:=]?\s*(\d+(?:\.\d+)?)',
            ],
            'RDI': [
                r'\bRDI\b\s*[:=]?\s*(\d+(?:\.\d+)?)',
                r'Respiratory.*Disturbance.*Index.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'respiratory.*disturbance.*index.*[:=]?\s*(\d+(?:\.\d+)?)',
            ],
            'ODI': [
                r'\bODI\b\s*[:=]?\s*(\d+(?:\.\d+)?)',
                r'Oxygen.*Desaturation.*Index.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'oxygen.*desaturation.*index.*[:=]?\s*(\d+(?:\.\d+)?)',
            ],
            'O2_nadir': [
                r'(?:O2|Oxygen)\s*(?:Nadir|NADIR|nadir)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%?',
                r'oxygen.*saturation.*low.*[:=]?\s*(\d+(?:\.\d+)?)\s*%?',
                r'SpO2.*low.*[:=]?\s*(\d+(?:\.\d+)?)\s*%?',
                r'lowest.*oxygen.*[:=]?\s*(\d+(?:\.\d+)?)\s*%?',
            ],
            'sleep_efficiency_pct': [
                r'sleep\s+efficiency\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%',
                r'efficiency\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%',
                r'total.*sleep.*time.*[:=]?\s*(\d+(?:\.\d+)?)\s*%',
            ],
            
            # PROs - more comprehensive patterns
            'ESS': [
                r'Epworth.*[:=]?\s*(\d{1,2})',
                r'ESS.*[:=]?\s*(\d{1,2})',
                r'Epworth.*Sleepiness.*Scale.*[:=]?\s*(\d{1,2})',
                r'epworth.*sleepiness.*[:=]?\s*(\d{1,2})',
            ],
            'STOP_Bang': [
                r'STOP[-\s]?Bang.*[:=]?\s*(\d{1,2})',
                r'STOP.*Bang.*[:=]?\s*(\d{1,2})',
                r'stop.*bang.*[:=]?\s*(\d{1,2})',
            ],
            'NOSE': [
                r'NOSE.*[:=]?\s*(\d{1,2})',
                r'Nasal.*Obstruction.*Symptom.*[:=]?\s*(\d{1,2})',
                r'nose.*score.*[:=]?\s*(\d{1,2})',
            ],
            'PSQI': [
                r'PSQI.*[:=]?\s*(\d{1,2})',
                r'Pittsburgh.*Sleep.*Quality.*[:=]?\s*(\d{1,2})',
                r'pittsburgh.*sleep.*quality.*[:=]?\s*(\d{1,2})',
            ],
            
            # Physical exam
            'Mallampati': [
                r'Mallampati.*[:=]?\s*([1-4])',
                r'mallampati.*[:=]?\s*([1-4])',
                r'MP.*[:=]?\s*([1-4])',
            ],
            'Tonsil': [
                r'Tonsil.*[:=]?\s*([0-4])',
                r'tonsil.*[:=]?\s*([0-4])',
                r'Tonsil.*Score.*[:=]?\s*([0-4])',
            ],
            
            # Demographics
            'age': [
                r'\b(\d{1,3})\s*(?:yo|y\.o\.|year[s]?\s*old|yr[s]?\s*old)\b',
                r'\b(?:age|aged?)\s*:?\s*(\d{1,3})\b',
                r'\((\d{1,3})yo\b',
                r'\b(\d{1,3})\s*years?\s*of\s*age\b',
                r'\b(\d{1,3})\s*y\.o\.\b',
                r'\b(\d{1,3})\s*yrs?\s*old\b',
            ],
            'BMI': [
                r'\bBMI\b\s*[:=]?\s*(\d+(?:\.\d+)?)',
                r'Body.*Mass.*Index.*[:=]?\s*(\d+(?:\.\d+)?)',
                r'body.*mass.*index.*[:=]?\s*(\d+(?:\.\d+)?)',
            ],
            'sex': [
                r'\b(?:male|female|M|F)\b',
                r'\b(?:Male|Female)\b',
                r'Gender.*[:=]?\s*(?:male|female|M|F)',
            ],
            
            # Clinical flags
            'cpap_intolerance': [
                r'\b(?:cpap|CPAP)\s*(?:intolerance|intolerant)\b',
                r'\b(?:cpap|CPAP)\s*(?:intolerance|intolerant)\b',
                r'cpap.*intolerance',
                r'cpap.*intolerant',
            ],
            'comorbidities': [
                r'\b(?:obesity|HTN|hypertension|diabetes|DM|COPD|asthma|depression|anxiety|heart.*disease|cardiac|pulmonary|respiratory)\b',
                r'\b(?:obesity|HTN|hypertension|diabetes|DM|COPD|asthma|depression|anxiety|heart.*disease|cardiac|pulmonary|respiratory)\b',
            ]
        }
    
    def extract_text_from_pdf(self, file_path: str) -> str:
        """Extract text from PDF using pdfplumber"""
        try:
            text_chunks = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_chunks.append(page_text)
            return "\n\n".join(text_chunks)
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {e}")
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
    
    def process_local_file(self, file_path: str) -> Dict[str, Any]:
        """Process a local file and extract clinical data"""
        try:
            logger.info(f"Processing local file: {file_path}")
            
            # Extract text from PDF
            text = self.extract_text_from_pdf(file_path)
            if not text:
                logger.error(f"Could not extract text from {file_path}")
                return None
            
            logger.info(f"Extracted {len(text)} characters from PDF")
            
            # Extract clinical data with enhanced patterns
            extracted_data = self.extract_clinical_data_enhanced(text)
            logger.info(f"Extracted {len(extracted_data)} fields: {list(extracted_data.keys())}")
            
            # Create case card
            source_uri = f"file://{file_path}"
            case_card = self.create_case_card(extracted_data, source_uri)
            
            # Validate
            validation_result = self.validate_case_card(case_card)
            case_card['validation'] = validation_result
            
            return case_card
            
        except Exception as e:
            logger.error(f"Error processing {file_path}: {e}")
            return None
    
    def create_case_card(self, extracted_data: Dict[str, Any], source_uri: str) -> Dict[str, Any]:
        """Create case-card JSON structure"""
        source_hint = source_uri.replace('file://', '').replace('/', '_')
        patient_rid = self.generate_patient_rid(source_hint)
        case_id = self.generate_case_id(source_hint)
        
        # Build features object
        features = {}
        for field in ['age', 'sex', 'BMI', 'AHI', 'RDI', 'ODI', 'O2_nadir', 
                      'sleep_efficiency_pct', 'ESS', 'STOP_Bang', 'NOSE', 'PSQI',
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

def main():
    """Main function to process the local PDF"""
    logger.info("Starting Improved Case-Card Generator")
    
    generator = ImprovedCaseCardGenerator()
    
    # Process the local PDF file
    pdf_path = "/home/ec2-user/patient_data/Print HTML Document.pdf"
    
    if not os.path.exists(pdf_path):
        logger.error(f"PDF file not found: {pdf_path}")
        return
    
    # Process the file
    case_card = generator.process_local_file(pdf_path)
    
    if case_card:
        # Save to local file
        output_path = "/home/ec2-user/improved_case_card.json"
        with open(output_path, 'w') as f:
            json.dump(case_card, f, indent=2)
        
        logger.info(f"Case card saved to: {output_path}")
        logger.info(f"Extracted fields: {list(case_card['features'].keys())}")
        
        # Print summary
        features = case_card['features']
        non_null_features = {k: v for k, v in features.items() if v is not None}
        logger.info(f"Non-null features: {non_null_features}")
    else:
        logger.error("Failed to generate case card")

if __name__ == "__main__":
    main()

