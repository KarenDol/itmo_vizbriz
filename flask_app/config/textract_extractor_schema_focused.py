# textract_extractor_schema_focused.py
# Schema-focused extractor for Patient Case JSON v1

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import json
import re
import logging
from io import BytesIO
from datetime import datetime

# Set up logging
logger = logging.getLogger(__name__)

try:
    import boto3
except Exception as e:
    boto3 = None

try:
    from PyPDF2 import PdfReader
    from pdf2image import convert_from_bytes
    from PIL import Image
    import pytesseract
    import pdfplumber
except Exception as e:
    print(f"Warning: Some text extraction libraries not available: {e}")

try:
    import camelot
    CAMELOT_AVAILABLE = True
except ImportError:
    CAMELOT_AVAILABLE = False
    print("Warning: camelot not available")

try:
    import tabula
    TABULA_AVAILABLE = True
except ImportError:
    TABULA_AVAILABLE = False
    print("Warning: tabula not available")

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    print("Warning: pandas not available")

# Schema fields to extract
SCHEMA_FIELDS = {
    # sleep_study section
    "study_type": ["home", "inlab", "HSAT", "PSG"],
    "sleep_duration_h": "number",
    "sleep_efficiency_pct": "number", 
    "ahi": "number",
    "odi": "number", 
    "rdi": "number",
    "oai": "number",
    "cai": "number",
    "hi": "number",
    "desaturation_events": "integer",
    "o2_nadir_pct": "number",
    "o2_mean_pct": "number",
    "time_below_90_pct_min": "number",
    "time_below_88_pct_min": "number",
    "supine_ahi": "number",
    "non_supine_ahi": "number", 
    "rem_ahi": "number",
    "nrem_ahi": "number",
    "severity": ["none", "mild", "moderate", "severe"],
    
    # snoring sub-object
    "snore_avg_db": "number",
    "snore_max_db": "number",
    
    # heart_rate sub-object  
    "heart_rate_mean": "number",
    "heart_rate_min": "number",
    "heart_rate_max": "number",
    
    # demographics section
    "sex": ["M", "F", "X"],
    "age_years": "number",
    "height_cm": "number",
    "weight_kg": "number", 
    "bmi": "number",
    
    # observations.anatomy_imaging section
    "primary_obstruction_site": "string",
    "soft_palate_uvula": "string",
    "tongue_base": "string",
    "bite_jaw": "string",
    "hyoid": "string",
    "nose_sinus": "string",
    "tmj": "string",
    
    # patient_self_report.scales section
    "ESS": "integer",
    "STOP_Bang": "integer",
    "NOSE": "integer",
    "PSQI": "integer",
    
    # device_design section
    "mandibular_advancement_mm": "number",
    "vertical_opening_mm": "number",
    "anterior_window": ["small", "medium", "large"]
}

# Patterns for extraction
SCHEMA_PATTERNS = {
    "ahi": [r"AHI[:\s]*(\d+\.?\d*)", r"Apnea-Hypopnea Index[:\s]*(\d+\.?\d*)"],
    "rdi": [r"RDI[:\s]*(\d+\.?\d*)", r"Respiratory Disturbance Index[:\s]*(\d+\.?\d*)"],
    "odi": [r"ODI[:\s]*(\d+\.?\d*)(?:\s|$)", r"Oxygen Desaturation Index[:\s]*(\d+\.?\d*)(?:\s|$)", r"ODI\s+(\d+\.?\d*)(?:\s|$)", r"ODI\s*=\s*(\d+\.?\d*)(?:\s|$)", r"ODI\s*(\d+\.?\d*)(?:\s|$)"],
    "hi": [r"HI[:\s]*(\d+\.?\d*)", r"Hypopnea Index[:\s]*(\d+\.?\d*)"],
    "supine_ahi": [r"Supine.*?AHI[:\s]*(\d+\.?\d*)", r"Supine.*?(\d+\.?\d*)"],
    "rem_ahi": [r"REM.*?AHI[:\s]*(\d+\.?\d*)", r"REM.*?(\d+\.?\d*)"],
    "o2_nadir_pct": [r"Minimum[:\s]*(\d+)", r"Min.*?(\d+)", r"O2.*?Nadir[:\s]*(\d+)"],
    "o2_mean_pct": [r"Mean[:\s]*(\d+)(?:\s*Minimum|\s*$)", r"Mean SpO2[:\s]*(\d+)"],
    "desaturation_events": [r"Desaturation.*?(\d+)", r"Events.*?(\d+)"],
    "snore_avg_db": [r"Mean[:\s]*(\d+)\s*dB", r"Average.*?(\d+)\s*dB"],
    "snore_max_db": [r"Maximum[:\s]*(\d+)\s*dB", r"Max.*?(\d+)\s*dB"],
    "sleep_duration_h": [r"Total Study Time[:\s]*(\d+)\s*hrs?[,\s]*(\d+)\s*min", r"(\d+)\s*hrs?[,\s]*(\d+)\s*min"],
    "age_years": [r"Age[:\s]*(\d+)", r"גיל[:\s]*(\d+)", r"Age[:\s]*(\d+)", r"גיל[:\s]*(\d+)"],
    "height_cm": [r"Height[:\s]*(\d+)", r"גובה[:\s]*(\d+)", r"Height[:\s]*(\d+)\s*cm"],
    "weight_kg": [r"Weight[:\s]*(\d+)", r"משקל[:\s]*(\d+)", r"Weight[:\s]*(\d+)\s*kg"],
    "bmi": [r"BMI[:\s]*(\d+\.?\d*)", r"BMI.*?(\d+\.?\d*)", r"BMI[:\s]*(\d+\.?\d*)"],
    "sex": [r"Gender[:\s]*(Male|Female)", r"מין[:\s]*(זכר|נקבה)", r"Sex[:\s]*(M|F|Male|Female)"],
    "ESS": [r"ESS[:\s]*(\d+)", r"Epworth[:\s]*(\d+)"],
    "primary_obstruction_site": [r"Primary.*?obstruction[:\s]*([^.\n]+)", r"Obstruction.*?site[:\s]*([^.\n]+)", r"Site.*?obstruction[:\s]*([^.\n]+)", r"Primary.*?site[:\s]*([^.\n]+)"],
    "soft_palate_uvula": [r"Soft.*?palate[:\s]*([^.\n]+)", r"Uvula[:\s]*([^.\n]+)"],
    "tongue_base": [r"Tongue.*?base[:\s]*([^.\n]+)", r"Base.*?tongue[:\s]*([^.\n]+)"],
    "bite_jaw": [r"Bite[:\s]*([^.\n]+)", r"Jaw[:\s]*([^.\n]+)"],
    "hyoid": [r"Hyoid[:\s]*([^.\n]+)", r"Hyoid.*?position[:\s]*([^.\n]+)"],
    "nose_sinus": [r"Nose[:\s]*([^.\n]+)", r"Sinus[:\s]*([^.\n]+)"],
    "tmj": [r"TMJ[:\s]*([^.\n]+)", r"Temporomandibular[:\s]*([^.\n]+)"],
    "mandibular_advancement_mm": [r"Mandibular.*?advancement[:\s]*(\d+\.?\d*)", r"Advancement[:\s]*(\d+\.?\d*)\s*mm"],
    "vertical_opening_mm": [r"Vertical.*?opening[:\s]*(\d+\.?\d*)", r"Opening[:\s]*(\d+\.?\d*)\s*mm"],
    "anterior_window": [r"Anterior.*?window[:\s]*(small|medium|large)", r"Window.*?size[:\s]*(small|medium|large)"]
}

@dataclass
class Candidate:
    field: str
    value: Any
    source: str
    confidence: float
    page: Optional[int] = None
    key_text: Optional[str] = None
    raw: Optional[str] = None

def _extract_with_pdfplumber(file_content: bytes) -> str:
    """Extract text using PDFPlumber - most reliable for text extraction."""
    try:
        with pdfplumber.open(BytesIO(file_content)) as pdf:
            full_text = ""
            
            for page in pdf.pages:
                # Try table extraction first
                tables = page.extract_tables()
                if tables:
                    for i, table in enumerate(tables):
                        # Convert to DataFrame for consistent processing
                        import pandas as pd
                        df = pd.DataFrame(table[1:], columns=table[0] if table[0] else [f"Col_{j}" for j in range(len(table[1]) if table[1:] else 0)])
                        table_text = _convert_dataframe_to_text(df, f"PDFPlumber_Table_{i+1}")
                        full_text += table_text + "\n"
                
                # Extract regular text
                page_text = page.extract_text()
                if page_text:
                    full_text += page_text + "\n"
            
            return full_text
    except Exception as e:
        raise Exception(f"PDFPlumber extraction failed: {str(e)}")

def _extract_with_pypdf2(file_content: bytes) -> str:
    """Extract text using PyPDF2."""
    try:
        pdf_reader = PdfReader(BytesIO(file_content))
        full_text = ""
        
        for page in pdf_reader.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"
        
        return full_text
    except Exception as e:
        raise Exception(f"PyPDF2 extraction failed: {str(e)}")

def _extract_with_camelot(file_content: bytes) -> str:
    """Extract text using Camelot for table detection."""
    try:
        # Save to temporary file for camelot
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
            tmp_file.write(file_content)
            tmp_path = tmp_file.name
        
        full_text = ""
        
        try:
            # Try lattice flavor first (better for structured tables)
            print("🔄 Camelot: Trying lattice flavor...")
            tables = camelot.read_pdf(tmp_path, pages='all', flavor='lattice')
            
            for i, table in enumerate(tables):
                df = table.df
                if not df.empty:
                    # Convert table to structured text format
                    table_text = _convert_dataframe_to_text(df, f"Table_{i+1}")
                    full_text += table_text + "\n"
                    print(f"✅ Camelot lattice: Found table {i+1} with {len(df)} rows")
            
            if full_text.strip():
                return full_text
                
        except Exception as e:
            print(f"❌ Camelot lattice failed: {str(e)}")
        
        try:
            # Try stream flavor (better for text-based tables)
            print("🔄 Camelot: Trying stream flavor...")
            tables = camelot.read_pdf(tmp_path, pages='all', flavor='stream')
            
            for i, table in enumerate(tables):
                df = table.df
                if not df.empty:
                    # Convert table to structured text format
                    table_text = _convert_dataframe_to_text(df, f"Table_{i+1}")
                    full_text += table_text + "\n"
                    print(f"✅ Camelot stream: Found table {i+1} with {len(df)} rows")
            
            return full_text
            
        except Exception as e:
            print(f"❌ Camelot stream failed: {str(e)}")
            raise
            
        finally:
            os.unlink(tmp_path)
            
    except Exception as e:
        raise Exception(f"Camelot extraction failed: {str(e)}")

def _extract_with_tabula(file_content: bytes) -> str:
    """Extract text using Tabula."""
    try:
        # Save to temporary file for tabula
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
            tmp_file.write(file_content)
            tmp_path = tmp_file.name
        
        try:
            print("🔄 Tabula: Extracting tables...")
            tables = tabula.read_pdf(tmp_path, pages='all', multiple_tables=True)
            full_text = ""
            
            for i, df in enumerate(tables):
                if not df.empty:
                    # Convert table to structured text format
                    table_text = _convert_dataframe_to_text(df, f"Tabula_Table_{i+1}")
                    full_text += table_text + "\n"
                    print(f"✅ Tabula: Found table {i+1} with {len(df)} rows")
            
            return full_text
            
        finally:
            os.unlink(tmp_path)
            
    except Exception as e:
        raise Exception(f"Tabula extraction failed: {str(e)}")

def _extract_with_basic_fallback(file_content: bytes) -> str:
    """Basic fallback using OCR and image processing."""
    try:
        # Try OCR
        images = convert_from_bytes(file_content)
        text = ""
        for image in images:
            img_byte_arr = BytesIO()
            image.save(img_byte_arr, format='PNG')
            img_byte_arr = img_byte_arr.getvalue()
            text += pytesseract.image_to_string(Image.open(BytesIO(img_byte_arr))) + "\n"
        
        if text.strip():
            return text
        
        # Try as direct image
        text = pytesseract.image_to_string(Image.open(BytesIO(file_content)))
        return text
        
    except Exception as e:
        raise Exception(f"Basic fallback extraction failed: {str(e)}")

def _convert_table_to_text(table: List[List]) -> str:
    """Convert table data to searchable text."""
    text = ""
    for row in table:
        if row:
            row_text = " ".join([str(cell) if cell else "" for cell in row])
            text += row_text + "\n"
    return text

def _convert_dataframe_to_text(df, table_name: str = "Table") -> str:
    """Convert pandas DataFrame to searchable text with medical data patterns."""
    try:
        import pandas as pd
        
        text = f"\n=== {table_name} ===\n"
        
        # Get column headers
        headers = df.columns.tolist()
        text += f"Headers: {' | '.join([str(h) for h in headers])}\n"
        
        # Convert each row to key-value pairs for better pattern matching
        for idx, row in df.iterrows():
            row_text = ""
            
            # Try to identify key-value patterns in the row
            for col_idx, (col_name, value) in enumerate(zip(headers, row)):
                if pd.notna(value) and str(value).strip():
                    # Clean the value
                    clean_value = str(value).strip()
                    
                    # Create multiple text patterns for better extraction
                    # Pattern 1: "Column: Value"
                    row_text += f"{col_name}: {clean_value} "
                    
                    # Pattern 2: "Value" (for numeric values that might be standalone)
                    if clean_value.replace('.', '').replace('-', '').isdigit():
                        row_text += f"{clean_value} "
                    
                    # Pattern 3: Common medical abbreviations
                    if col_name and any(abbr in str(col_name).upper() for abbr in ['AHI', 'ODI', 'RDI', 'BMI', 'ESS', 'O2', 'SPO2']):
                        row_text += f"{col_name.upper()}: {clean_value} "
            
            if row_text.strip():
                text += row_text.strip() + "\n"
        
        return text
        
    except Exception as e:
        # Fallback to simple conversion
        return df.to_string()

def extract_text_from_file_enhanced(file_content: bytes) -> str:
    """Enhanced text extraction using osaagent_routes.py approach."""
    logger.debug("=== Starting enhanced text extraction process ===")
    try:
        # First try to read as PDF
        try:
            logger.debug("Attempting PDF extraction with PdfReader...")
            pdf_reader = PdfReader(BytesIO(file_content))
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
            
            if text.strip():
                logger.debug("Successfully extracted text using PdfReader")
                return text

            logger.debug("No text found with PdfReader, trying pdfplumber...")
            with pdfplumber.open(BytesIO(file_content)) as pdf:
                text = ""
                for page in pdf.pages:
                    # Try table extraction first
                    tables = page.extract_tables()
                    if tables:
                        for table in tables:
                            table_text = _convert_table_to_text(table)
                            text += table_text + "\n"
                    
                    # Extract regular text
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                
                if text.strip():
                    logger.debug("Successfully extracted text using pdfplumber")
                    return text

            logger.debug("No text found with pdfplumber, attempting OCR...")
            images = convert_from_bytes(file_content)
            text = ""
            for image in images:
                img_byte_arr = BytesIO()
                image.save(img_byte_arr, format='PNG')
                img_byte_arr = img_byte_arr.getvalue()
                text += pytesseract.image_to_string(Image.open(BytesIO(img_byte_arr))) + "\n"
            logger.debug("Successfully extracted text using OCR")
            return text

        except Exception as pdf_error:
            logger.warning(f"PDF extraction failed: {str(pdf_error)}, trying image OCR")
            
            # If PDF fails, try processing as image
            text = pytesseract.image_to_string(Image.open(BytesIO(file_content)))
            logger.debug("Successfully extracted text from image")
            return text

    except Exception as e:
        logger.error(f"Enhanced text extraction failed: {str(e)}")
        raise Exception(f"Failed to extract text from file: {str(e)}")

def extract_observations_with_ai(extracted_text: str, report_id: Optional[str] = None) -> List[Candidate]:
    """Extract additional observations using AI (OpenAI/Claude)."""
    try:
        # Skip AI extraction if flask_app is not available (standalone mode)
        try:
            from flask_app.routes.osaagent_routes import extract_observations_from_text
        except ImportError:
            print("⚠️  AI extraction skipped - flask_app not available in standalone mode")
            return []
        
        # Extract using datasource_id 1 (sleep test)
        result = extract_observations_from_text(extracted_text, 1)
        
        if not result.get('success'):
            return []
        
        # Convert AI observations to Candidate objects
        candidates = []
        observations = result.get('data', {}).get('datasource_observations', [])
        
        for obs in observations:
            observation_name = obs.get('observation', '').lower()
            value = obs.get('value', '')
            
            # Map AI observations to schema fields
            schema_field = map_ai_observation_to_schema(observation_name)
            if schema_field:
                candidates.append(Candidate(
                    field=schema_field,
                    value=value,
                    source="AI",
                    confidence=obs.get('confidence', 75.0),
                    page=1,
                    key_text=observation_name,
                    raw=value
                ))
                print(f"🤖 AI found schema field {schema_field}: {value}")
        
        return candidates
        
    except Exception as e:
        print(f"❌ AI extraction failed: {str(e)}")
        return []

def map_ai_observation_to_schema(observation_name: str) -> Optional[str]:
    """Map AI observation names to schema field names."""
    mapping = {
        'ahi': 'ahi',
        'apnea hypopnea index': 'ahi',
        'rdi': 'rdi',
        'respiratory disturbance index': 'rdi',
        'odi': 'odi',
        'oxygen desaturation index': 'odi',
        'o2 nadir': 'o2_nadir_pct',
        'minimum oxygen': 'o2_nadir_pct',
        'spo2 nadir': 'o2_nadir_pct',
        'mean oxygen': 'o2_mean_pct',
        'average oxygen': 'o2_mean_pct',
        'desaturation events': 'desaturation_events',
        'age': 'age_years',
        'bmi': 'bmi',
        'body mass index': 'bmi',
        'gender': 'sex',
        'sex': 'sex',
        'weight': 'weight_kg',
        'height': 'height_cm',
        'ess': 'ESS',
        'epworth': 'ESS',
        'epworth sleepiness scale': 'ESS',
        'snoring': 'snore_avg_db',
        'snore average': 'snore_avg_db',
        'snore max': 'snore_max_db',
        'heart rate': 'heart_rate_mean',
        'heart rate mean': 'heart_rate_mean',
        'heart rate min': 'heart_rate_min',
        'heart rate max': 'heart_rate_max',
        'sleep duration': 'sleep_duration_h',
        'total sleep time': 'sleep_duration_h',
        'rem ahi': 'rem_ahi',
        'supine ahi': 'supine_ahi',
        'non supine ahi': 'non_supine_ahi',
        'nrem ahi': 'nrem_ahi',
        'hi': 'hi',
        'hypopnea index': 'hi',
        'oai': 'oai',
        'obstructive apnea index': 'oai',
        'cai': 'cai',
        'central apnea index': 'cai',
        'time below 90': 'time_below_90_pct_min',
        'time below 88': 'time_below_88_pct_min',
        'primary obstruction': 'primary_obstruction_site',
        'soft palate': 'soft_palate_uvula',
        'uvula': 'soft_palate_uvula',
        'tongue base': 'tongue_base',
        'bite': 'bite_jaw',
        'jaw': 'bite_jaw',
        'hyoid': 'hyoid',
        'nose': 'nose_sinus',
        'sinus': 'nose_sinus',
        'tmj': 'tmj',
        'mandibular advancement': 'mandibular_advancement_mm',
        'vertical opening': 'vertical_opening_mm',
        'anterior window': 'anterior_window'
    }
    
    # Try exact match first
    if observation_name in mapping:
        return mapping[observation_name]
    
    # Try partial matches
    for key, value in mapping.items():
        if key in observation_name or observation_name in key:
            return value
    
    return None

def extract_text_from_file_fallback(file_content: bytes) -> str:
    """Legacy function - now uses the enhanced approach."""
    return extract_text_from_file_enhanced(file_content)

def _search_text_for_schema_patterns(text_content: str) -> List[Candidate]:
    """Search extracted text for schema-defined patterns."""
    cands: List[Candidate] = []
    
    for field, patterns in SCHEMA_PATTERNS.items():
        for pattern in patterns:
            matches = re.finditer(pattern, text_content, re.IGNORECASE | re.MULTILINE)
            
            for match in matches:
                if field == "sleep_duration_h":
                    if len(match.groups()) >= 2:
                        hours = int(match.group(1))
                        minutes = int(match.group(2))
                        value = hours + (minutes / 60.0)
                    else:
                        continue
                elif field == "sex":
                    raw_value = match.group(1).lower()
                    if raw_value in ["male", "m", "זכר"]:
                        value = "M"
                    elif raw_value in ["female", "f", "נקבה"]:
                        value = "F"
                    else:
                        value = "X"
                else:
                    value = match.group(1)
                
                cands.append(Candidate(
                    field=field,
                    value=value,
                    source="TEXT",
                    confidence=85.0,
                    page=1,
                    key_text=field,
                    raw=value
                ))
                print(f"🔍 Found schema field {field}: {value}")
                break
    
    return cands

def extract_with_textract(pdf_bytes: bytes, report_id: Optional[str] = None, source_uri: Optional[str] = None) -> Dict[str, Any]:
    """Analyze a single PDF via Textract and return schema-compliant JSON."""
    if boto3 is None:
        raise RuntimeError("boto3 is not available")

    try:
        client = boto3.client("textract", region_name="us-east-1")
        
        schema_queries = []
        for field in SCHEMA_FIELDS.keys():
            if field in ["ahi", "rdi", "odi", "hi"]:
                schema_queries.append({
                    "Text": f"{field.upper()}",
                    "Alias": field
                })
        
        resp = client.analyze_document(
            Document={"Bytes": pdf_bytes},
            FeatureTypes=["TABLES", "FORMS", "QUERIES"],
            QueriesConfig={"Queries": schema_queries},
        )
        
        cands: List[Candidate] = []
        cands += _collect_query_candidates(resp)
        cands += _collect_kv_candidates(resp)
        cands += _collect_table_candidates(resp)
        
        best = _reconcile(cands)
        frag = _to_schema_compliant_fragment(best, report_id=report_id, source_uri=source_uri)
        return frag
        
    except Exception as e:
        if "UnsupportedDocumentException" in str(e):
            return extract_with_fallback_text_extraction(pdf_bytes, report_id, source_uri)
        else:
            return extract_with_fallback_text_extraction(pdf_bytes, report_id, source_uri)

def extract_with_fallback_text_extraction(pdf_bytes: bytes, report_id: Optional[str] = None, source_uri: Optional[str] = None) -> Dict[str, Any]:
    """Enhanced fallback extraction using osaagent_routes.py methods + schema patterns."""
    try:
        # Use the robust extraction method from osaagent_routes.py
        extracted_text = extract_text_from_file_enhanced(pdf_bytes)
        
        if not extracted_text or len(extracted_text.strip()) < 50:
            print("❌ No meaningful text extracted")
            return _to_schema_compliant_fragment({}, report_id=report_id, source_uri=source_uri)
        
        # Save extracted text for debugging
        with open(f"extracted_text_schema_{report_id or 'debug'}_enhanced.txt", "w", encoding="utf-8") as f:
            f.write(extracted_text)
        
        # Extract using schema patterns
        schema_candidates = _search_text_for_schema_patterns(extracted_text)
        
        # Also try AI-powered extraction for additional fields
        ai_candidates = extract_observations_with_ai(extracted_text, report_id)
        
        # Combine both approaches
        all_candidates = schema_candidates + ai_candidates
        best = _reconcile(all_candidates)
        
        frag = _to_schema_compliant_fragment(best, report_id=report_id, source_uri=source_uri)
        
        # Add extraction metadata
        if "extraction_meta" not in frag:
            frag["extraction_meta"] = {}
        frag["extraction_meta"]["fallback_method"] = "enhanced_extraction"
        frag["extraction_meta"]["schema_fields_found"] = len(schema_candidates)
        frag["extraction_meta"]["ai_fields_found"] = len(ai_candidates)
        frag["extraction_meta"]["total_fields_found"] = len(all_candidates)
        
        return frag
        
    except Exception as e:
        print(f"❌ Enhanced fallback extraction failed: {str(e)}")
        return _to_schema_compliant_fragment({}, report_id=report_id, source_uri=source_uri)

def extract_file(path: str, **kwargs) -> Dict[str, Any]:
    """Convenience: open file path and call extract_with_textract."""
    with open(path, "rb") as f:
        data = f.read()
    return extract_with_textract(data, **kwargs)

def _blocks_index(blocks: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {b["Id"]: b for b in blocks}

def _get_text_for_block(block_id: str, idx: Dict[str, Dict[str, Any]]) -> str:
    b = idx[block_id]
    txt = []
    for rel in b.get("Relationships", []):
        if rel["Type"] == "CHILD":
            for cid in rel.get("Ids", []):
                c = idx[cid]
                if c["BlockType"] == "WORD":
                    txt.append(c.get("Text", ""))
    return " ".join(txt).strip()

def _collect_query_candidates(resp: Dict[str, Any]) -> List[Candidate]:
    cands: List[Candidate] = []
    for b in resp.get("Blocks", []):
        if b["BlockType"] == "QUERY_RESULT":
            alias = b.get("Query", {}).get("Alias")
            val = b.get("Text", "")
            conf = float(b.get("Confidence", 0.0) or 0.0)
            if alias and val and alias in SCHEMA_FIELDS:
                cands.append(Candidate(field=alias, value=val, source="QUERY", confidence=conf, page=b.get("Page"), raw=val))
    return cands

def _collect_kv_candidates(resp: Dict[str, Any]) -> List[Candidate]:
    cands: List[Candidate] = []
    blocks = resp.get("Blocks", [])
    idx = _blocks_index(blocks)
    keys = [b for b in blocks if b["BlockType"] == "KEY_VALUE_SET" and "KEY" in b.get("EntityTypes", [])]
    values = {b["Id"]: b for b in blocks if b["BlockType"] == "KEY_VALUE_SET" and "VALUE" in b.get("EntityTypes", [])}

    for k in keys:
        ktext = _get_text_for_block(k["Id"], idx)
        if not ktext:
            continue
        v_id = None
        for rel in k.get("Relationships", []):
            if rel["Type"] == "VALUE":
                ids = rel.get("Ids", [])
                if ids:
                    v_id = ids[0]
        if not v_id or v_id not in values:
            continue
        v = values[v_id]
        vtext = _get_text_for_block(v["Id"], idx)
        if not vtext:
            continue

        k_norm = ktext.lower().strip().replace(":", "")
        for field in SCHEMA_FIELDS.keys():
            if field.lower() in k_norm:
                conf = (float(k.get("Confidence", 0.0) or 0.0) + float(v.get("Confidence", 0.0) or 0.0)) / 2.0
                cands.append(Candidate(field=field, value=vtext, source="FORM", confidence=conf, page=k.get("Page"), key_text=ktext, raw=vtext))
                break
    return cands

def _collect_table_candidates(resp: Dict[str, Any]) -> List[Candidate]:
    cands: List[Candidate] = []
    blocks = resp.get("Blocks", [])
    idx = _blocks_index(blocks)
    tables = [b for b in blocks if b["BlockType"] == "TABLE"]

    def cell_text(c):
        return _get_text_for_block(c["Id"], idx)

    for t in tables:
        page = t.get("Page")
        cells = []
        for rel in t.get("Relationships", []):
            if rel["Type"] == "CHILD":
                for cid in rel.get("Ids", []):
                    cb = idx[cid]
                    if cb["BlockType"] == "CELL":
                        cells.append(cb)
        
        by_row: Dict[int, List[Dict[str, Any]]] = {}
        for cb in cells:
            by_row.setdefault(cb["RowIndex"], []).append(cb)
        for r_i, row_cells in by_row.items():
            row_cells.sort(key=lambda c: c["ColumnIndex"])
            if len(row_cells) < 2:
                continue
            left, right = row_cells[0], row_cells[1]
            ktext, vtext = cell_text(left), cell_text(right)
            if not ktext or not vtext:
                continue
            k_norm = ktext.lower().strip().replace(":", "")
            for field in SCHEMA_FIELDS.keys():
                if field.lower() in k_norm:
                    conf = (float(left.get("Confidence", 0.0) or 0.0) + float(right.get("Confidence", 0.0) or 0.0)) / 2.0
                    cands.append(Candidate(field=field, value=vtext, source="TABLE", confidence=conf, page=page, row=r_i, col=2, key_text=ktext, raw=vtext))
                    break
    return cands

def _to_float(s: str) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None

def _normalize_value(field: str, raw: str) -> Optional[Any]:
    if raw is None:
        return None
    txt = str(raw).strip()

    if field in ["sleep_duration_h", "sleep_efficiency_pct", "ahi", "odi", "rdi", "oai", "cai", "hi", 
                 "o2_nadir_pct", "o2_mean_pct", "time_below_90_pct_min", "time_below_88_pct_min",
                 "supine_ahi", "non_supine_ahi", "rem_ahi", "nrem_ahi", "snore_avg_db", "snore_max_db",
                 "heart_rate_mean", "heart_rate_min", "heart_rate_max", "age_years", "height_cm", 
                 "weight_kg", "bmi", "mandibular_advancement_mm", "vertical_opening_mm"]:
        return _to_float(txt)
    
    elif field == "desaturation_events":
        num = _to_float(txt)
        return int(num) if num is not None else None
    
    elif field in ["sex", "severity", "anterior_window"]:
        return txt
    
    return txt or None

def _reconcile(cands: List[Candidate]) -> Dict[str, Candidate]:
    by_field: Dict[str, List[Candidate]] = {}
    for c in cands:
        norm = _normalize_value(c.field, c.value)
        if norm is None:
            continue
        by_field.setdefault(c.field, []).append(Candidate(
            field=c.field, value=norm, source=c.source, confidence=c.confidence,
            page=c.page, key_text=c.key_text, raw=str(c.value)
        ))

    best: Dict[str, Candidate] = {}
    for field, cs in by_field.items():
        cs.sort(key=lambda x: x.confidence, reverse=True)
        best[field] = cs[0]
    return best

def _prune_empty(obj):
    if isinstance(obj, dict):
        out = {k: _prune_empty(v) for k, v in obj.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(obj, list):
        out = [_prune_empty(v) for v in obj]
        out = [v for v in out if v not in (None, "", [], {})]
        return out
    return obj

def _to_schema_compliant_fragment(best: Dict[str, Candidate], report_id: Optional[str], source_uri: Optional[str]) -> Dict[str, Any]:
    """Create schema-compliant output based on Patient Case JSON — v1.json"""
    sleep_study: Dict[str, Any] = {}
    demographics: Dict[str, Any] = {}
    observations: Dict[str, Any] = {}
    snoring: Dict[str, Any] = {}
    heart_rate: Dict[str, Any] = {}
    device_design: Dict[str, Any] = {}
    
    # Map extracted fields to schema structure
    for field, candidate in best.items():
        if field.startswith("snore_"):
            if field == "snore_avg_db":
                snoring["avg_db"] = candidate.value
            elif field == "snore_max_db":
                snoring["max_db"] = candidate.value
        elif field.startswith("heart_rate_"):
            if field == "heart_rate_mean":
                heart_rate["mean_bpm"] = candidate.value
            elif field == "heart_rate_min":
                heart_rate["min_bpm"] = candidate.value
            elif field == "heart_rate_max":
                heart_rate["max_bpm"] = candidate.value
        elif field in ["sex", "age_years", "height_cm", "weight_kg", "bmi"]:
            demographics[field] = candidate.value
        elif field in ["primary_obstruction_site", "soft_palate_uvula", "tongue_base", "bite_jaw", "hyoid", "nose_sinus", "tmj"]:
            if "anatomy_imaging" not in observations:
                observations["anatomy_imaging"] = {}
            observations["anatomy_imaging"][field] = candidate.value
        elif field in ["mandibular_advancement_mm", "vertical_opening_mm", "anterior_window"]:
            device_design[field] = candidate.value
        else:
            sleep_study[field] = candidate.value
    
    # Add sub-objects if they have data
    if snoring:
        sleep_study["snoring"] = snoring
    if heart_rate:
        sleep_study["heart_rate"] = heart_rate
    
    # Build provenance
    provenance: List[Dict[str, Any]] = []
    for field, c in best.items():
        if field.startswith("snore_"):
            path = f"sleep_study.snoring.{field.replace('snore_', '')}"
        elif field.startswith("heart_rate_"):
            path = f"sleep_study.heart_rate.{field.replace('heart_rate_', '')}"
        elif field in ["sex", "age_years", "height_cm", "weight_kg", "bmi"]:
            path = f"demographics.{field}"
        elif field in ["primary_obstruction_site", "soft_palate_uvula", "tongue_base", "bite_jaw", "hyoid", "nose_sinus", "tmj"]:
            path = f"observations.anatomy_imaging.{field}"
        elif field in ["mandibular_advancement_mm", "vertical_opening_mm", "anterior_window"]:
            path = f"device_design.{field}"
        else:
            path = f"sleep_study.{field}"
        
        provenance.append({
            "path": path,
            "report_id": report_id or "",
            "source_uri": source_uri or "",
            "note": f"{c.source} p{c.page}" if c.page else c.source,
            "confidence": round(c.confidence, 3),
            "key_text": c.key_text,
            "raw": c.raw,
        })

    # Schema-compliant output
    out = {
        "schema_version": "1.0",
        "document_type": "per_report",
        "patient_id": "",
        "as_of": datetime.now().isoformat(),
        "report_meta": {
            "report_id": report_id or "",
            "source_report_type": "sleep_study",
            "source_uri": source_uri or "",
            "created_at": datetime.now().isoformat(),
            "author_role": "AI-extractor",
        },
        "demographics": demographics,
        "sleep_study": sleep_study,
        "provenance": provenance,
    }
    
    # Add observations if it has data
    if observations:
        out["observations"] = observations
    
    # Add device_design if it has data
    if device_design:
        out["device_design"] = device_design
    
    return _prune_empty(out)

def extract_for_observations_db(file_path: str, patient_id: str = None, report_id: str = None) -> Dict[str, Any]:
    """Extract from a single file for insertion into observations database."""
    try:
        if not report_id:
            report_id = f"report_{hash(file_path) % 10000}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        result = extract_file(file_path, report_id=report_id, source_uri=file_path)
        
        if patient_id:
            result['patient_id'] = patient_id
        
        result['extraction_meta'] = {
            'extracted_at': datetime.now().isoformat(),
            'file_path': file_path,
            'file_size_bytes': len(open(file_path, 'rb').read()),
            'extractor_version': 'schema_focused_v1'
        }
        
        return result
        
    except Exception as e:
        return {
            "schema_version": "1.0",
            "document_type": "per_report",
            "patient_id": patient_id or "",
            "as_of": datetime.now().isoformat(),
            "report_meta": {
                "report_id": report_id or "unknown",
                "source_report_type": "sleep_study",
                "source_uri": file_path,
                "created_at": datetime.now().isoformat(),
                "author_role": "AI-extractor",
            },
            "demographics": {},
            "sleep_study": {},
            "provenance": [],
            "extraction_meta": {
                'extracted_at': datetime.now().isoformat(),
                'file_path': file_path,
                'error': str(e),
                'extractor_version': 'schema_focused_v1'
            }
        }

def create_canonical_from_reports(reports: List[Dict[str, Any]], patient_id: str = None) -> Dict[str, Any]:
    """Create canonical JSON from multiple per-report JSONs."""
    if not reports:
        return {
            "schema_version": "1.0",
            "document_type": "canonical",
            "patient_id": patient_id or "",
            "as_of": datetime.now().isoformat(),
            "canonical_meta": {"version": 1, "report_refs": []},
            "demographics": {},
            "sleep_study": {},
            "provenance": [],
            "validation": {"errors": [], "warnings": []},
            "confidence": {"sleep_study": 0.0, "observations": 0.0, "device_design": 0.0}
        }
    
    canonical = {
        "schema_version": "1.0",
        "document_type": "canonical",
        "patient_id": patient_id or "",
        "as_of": datetime.now().isoformat(),
        "canonical_meta": {"version": 1, "report_refs": []},
        "demographics": {},
        "sleep_study": {},
        "provenance": [],
        "validation": {"errors": [], "warnings": []},
        "confidence": {"sleep_study": 0.0, "observations": 0.0, "device_design": 0.0}
    }
    
    field_candidates = {}
    
    for report in reports:
        report_id = report.get('report_meta', {}).get('report_id', 'unknown')
        source_uri = report.get('report_meta', {}).get('source_uri', '')
        
        canonical['canonical_meta']['report_refs'].append({
            "report_id": report_id,
            "source_uri": source_uri,
            "ingested_at": datetime.now().isoformat()
        })
        
        # Process all fields from report
        for section in ['sleep_study', 'demographics', 'observations']:
            section_data = report.get(section, {})
            for field, value in section_data.items():
                if field not in field_candidates:
                    field_candidates[field] = []
                
                field_candidates[field].append({
                    'value': value,
                    'report_id': report_id,
                    'source_uri': source_uri,
                    'confidence': 85.0,
                    'provenance': None
                })
    
    # Resolve conflicts and select best values
    for field, candidates in field_candidates.items():
        if not candidates:
            continue
        
        candidates.sort(key=lambda x: x['confidence'], reverse=True)
        best_candidate = candidates[0]
        
        # Determine field path and add to appropriate section
        if field in ['sex', 'age_years', 'height_cm', 'weight_kg', 'bmi']:
            canonical['demographics'][field] = best_candidate['value']
            field_path = f"demographics.{field}"
        elif field in ['primary_obstruction_site', 'soft_palate_uvula', 'tongue_base', 'bite_jaw', 'hyoid', 'nose_sinus', 'tmj']:
            if 'observations' not in canonical:
                canonical['observations'] = {}
            if 'anatomy_imaging' not in canonical['observations']:
                canonical['observations']['anatomy_imaging'] = {}
            canonical['observations']['anatomy_imaging'][field] = best_candidate['value']
            field_path = f"observations.anatomy_imaging.{field}"
        else:
            canonical['sleep_study'][field] = best_candidate['value']
            field_path = f"sleep_study.{field}"
        
        # Add provenance
        canonical['provenance'].append({
            "path": field_path,
            "report_id": best_candidate['report_id'],
            "source_uri": best_candidate['source_uri'],
            "note": f"Selected from {len(candidates)} candidates (confidence: {best_candidate['confidence']})",
            "confidence": best_candidate['confidence']
        })
        
        # Add warnings for conflicts
        if len(candidates) > 1:
            conflicting_values = [c['value'] for c in candidates if c['value'] != best_candidate['value']]
            if conflicting_values:
                canonical['validation']['warnings'].append(
                    f"Field {field_path}: Multiple values found ({best_candidate['value']} vs {conflicting_values}). Selected highest confidence."
                )
    
    # Calculate overall confidence
    if canonical['provenance']:
        avg_confidence = sum(p.get('confidence', 0) for p in canonical['provenance']) / len(canonical['provenance'])
        canonical['confidence']['sleep_study'] = round(avg_confidence / 100, 3)
    
    return canonical
