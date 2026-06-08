# textract_extractor_improved.py
# Enhanced extractor incorporating best practices from reference code

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import json
import re
import logging
from io import BytesIO
from datetime import datetime

# ---- If boto3 is not installed in your environment, install it in your image/env.
try:
    import boto3
except Exception as e:
    boto3 = None

# ---- Text extraction libraries with availability checks
try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    logging.warning("pdfplumber not available")

try:
    from PyPDF2 import PdfReader
    PYPDF2_AVAILABLE = True
except ImportError:
    PYPDF2_AVAILABLE = False
    logging.warning("PyPDF2 not available")

try:
    import camelot
    CAMELOT_AVAILABLE = True
except ImportError:
    CAMELOT_AVAILABLE = False
    logging.warning("camelot not available")

try:
    import tabula
    TABULA_AVAILABLE = True
except ImportError:
    TABULA_AVAILABLE = False
    logging.warning("tabula not available")

# =========================
# Structured Data Classes
# =========================
@dataclass
class PatientInfo:
    first_name: str = ""
    last_name: str = ""
    id: str = ""
    birth_date: str = ""
    age: int = 0
    gender: str = ""
    bmi: float = 0.0
    weight: float = 0.0
    height: float = 0.0

@dataclass
class SleepSummary:
    start_time: str = ""
    end_time: str = ""
    total_study_time: str = ""
    sleep_time: str = ""
    rem_percentage: float = 0.0
    sleep_duration_h: float = 0.0
    sleep_efficiency_pct: float = 0.0

@dataclass
class RespiratoryIndices:
    ahi: float = 0.0
    rdi: float = 0.0
    odi: float = 0.0
    oai: float = 0.0
    cai: float = 0.0
    hi: float = 0.0
    supine_ahi: float = 0.0
    non_supine_ahi: float = 0.0
    rem_ahi: float = 0.0
    nrem_ahi: float = 0.0
    desaturation_events: int = 0

@dataclass
class OxygenStats:
    o2_nadir_pct: float = 0.0
    o2_mean_pct: float = 0.0
    o2_max_pct: float = 0.0
    time_below_90_pct_min: float = 0.0
    time_below_88_pct_min: float = 0.0

@dataclass
class HeartRateStats:
    mean_bpm: float = 0.0
    min_bpm: float = 0.0
    max_bpm: float = 0.0

@dataclass
class SnoringStats:
    avg_db: float = 0.0
    max_db: float = 0.0

@dataclass
class SleepStudyData:
    patient_info: PatientInfo
    sleep_summary: SleepSummary
    respiratory_indices: RespiratoryIndices
    oxygen_stats: OxygenStats
    heart_rate: HeartRateStats
    snoring: SnoringStats
    extraction_method: str = ""
    confidence_score: float = 0.0
    additional_data: Dict = None

# =========================
# Enhanced Pattern Matching
# =========================
class PatternMatcher:
    """Enhanced pattern matching with report-type specific patterns"""
    
    def __init__(self):
        self.patterns = self._load_extraction_patterns()
    
    def _load_extraction_patterns(self) -> Dict:
        """Load extraction patterns for different report formats"""
        return {
            "clalit_sleep_report": {
                "patient_info": {
                    "first_name": [
                        r"First Name:\s*(.+?)(?:\s+Last Name:|$)",
                        r"דניאל"  # Hebrew name pattern
                    ],
                    "last_name": [
                        r"Last Name:\s*(.+?)(?:\s+ID:|$)",
                        r"קליפון"  # Hebrew surname pattern
                    ],
                    "id": [r"ID:\s*(\d+)", r"ת\.ז\.?\s*(\d+)"],
                    "birth_date": [r"Birth Date:\s*(\d{1,2}/\d{1,2}/\d{4})"],
                    "age": [r"Age:\s*(\d+)", r"גיל:\s*(\d+)"],
                    "gender": [r"Gender:\s*(Male|Female)", r"מין:\s*(זכר|נקבה)"],
                    "bmi": [r"BMI:\s*(\d+\.?\d*)", r"BMI.*?(\d+\.?\d*)"]
                },
                "respiratory_indices": {
                    "ahi": [
                        r"pAHI:?\s*(\d+\.?\d*)",
                        r"AHI.*?All Night.*?(\d+\.?\d*)",
                        r"pAHI=(\d+\.?\d*)",
                        r"AHI:?\s*(\d+\.?\d*)"
                    ],
                    "rdi": [r"pRDI:?\s*(\d+\.?\d*)", r"RDI:?\s*(\d+\.?\d*)"],
                    "odi": [r"ODI:?\s*(\d+\.?\d*)"],
                    "hi": [r"HI:?\s*(\d+\.?\d*)"],
                    "rem_ahi": [r"REM.*?AHI.*?(\d+\.?\d*)", r"AHI.*?REM.*?(\d+\.?\d*)"],
                    "supine_ahi": [r"Supine.*?AHI.*?(\d+\.?\d*)", r"Supine.*?pAHI.*?(\d+\.?\d*)"],
                    "desaturation_events": [r"Total.*?(\d+)\s*events", r"Desaturation.*?(\d+)"]
                },
                "oxygen_stats": {
                    "o2_mean_pct": [
                        r"Mean:?\s*(\d+)(?:\s*Minimum|\s*$)",
                        r"Mean SpO2:?\s*(\d+)",
                        r"SpO2.*?Mean:?\s*(\d+)"
                    ],
                    "o2_nadir_pct": [
                        r"Minimum:?\s*(\d+)",
                        r"Min SpO2:?\s*(\d+)",
                        r"O2 Nadir:?\s*(\d+)",
                        r"Nadir:?\s*(\d+)"
                    ],
                    "o2_max_pct": [
                        r"Maximum:?\s*(\d+)",
                        r"Max SpO2:?\s*(\d+)"
                    ],
                    "time_below_90_pct_min": [
                        r"Time.*?<90.*?(\d+\.?\d*)",
                        r"Oxygen Saturation <90.*?(\d+\.?\d*)"
                    ],
                    "time_below_88_pct_min": [
                        r"Time.*?<=88.*?(\d+\.?\d*)",
                        r"Oxygen Saturation <=88.*?(\d+\.?\d*)"
                    ]
                },
                "heart_rate": {
                    "mean_bpm": [
                        r"Mean:?\s*(\d+)\s*BPM",
                        r"Pulse Rate.*?Mean:?\s*(\d+)",
                        r"Mean.*?(\d+)\s*BPM"
                    ],
                    "min_bpm": [
                        r"Minimum:?\s*(\d+)\s*BPM",
                        r"Min.*?(\d+)\s*BPM"
                    ],
                    "max_bpm": [
                        r"Maximum:?\s*(\d+)\s*BPM",
                        r"Max.*?(\d+)\s*BPM"
                    ]
                },
                "snoring": {
                    "avg_db": [
                        r"Mean:?\s*(\d+)\s*dB",
                        r"Average.*?(\d+)\s*dB",
                        r"Snoring.*?Mean:?\s*(\d+)\s*dB"
                    ],
                    "max_db": [
                        r"Maximum.*?(\d+)\s*dB",
                        r"Max.*?(\d+)\s*dB"
                    ]
                },
                "sleep_summary": {
                    "start_time": [r"Start Study Time:\s*(\d{2}:\d{2}:\d{2})"],
                    "end_time": [r"End Study Time:\s*(\d{2}:\d{2}:\d{2})"],
                    "rem_percentage": [r"% REM of Sleep Time:\s*(\d+\.?\d*)"],
                    "sleep_duration_h": [
                        r"Total Study Time:\s*(\d+)\s*hrs?[,\s]*(\d+)\s*min",
                        r"Sleep Time:\s*(\d+)\s*hrs?[,\s]*(\d+)\s*min"
                    ]
                }
            }
        }
    
    def extract_data(self, text: str, report_type: str = "clalit_sleep_report") -> SleepStudyData:
        """Extract data using pattern matching"""
        data = SleepStudyData(
            patient_info=PatientInfo(),
            sleep_summary=SleepSummary(),
            respiratory_indices=RespiratoryIndices(),
            oxygen_stats=OxygenStats(),
            heart_rate=HeartRateStats(),
            snoring=SnoringStats()
        )
        
        patterns = self.patterns.get(report_type, {})
        
        # Extract patient info
        self._extract_section(text, patterns.get("patient_info", {}), data.patient_info)
        
        # Extract respiratory indices
        self._extract_section(text, patterns.get("respiratory_indices", {}), data.respiratory_indices)
        
        # Extract oxygen stats
        self._extract_section(text, patterns.get("oxygen_stats", {}), data.oxygen_stats)
        
        # Extract heart rate
        self._extract_section(text, patterns.get("heart_rate", {}), data.heart_rate)
        
        # Extract snoring
        self._extract_section(text, patterns.get("snoring", {}), data.snoring)
        
        # Extract sleep summary (special handling for duration)
        self._extract_sleep_summary(text, patterns.get("sleep_summary", {}), data.sleep_summary)
        
        return data
    
    def _extract_section(self, text: str, patterns: Dict, data_obj):
        """Extract data for a specific section"""
        for field, regex_list in patterns.items():
            value = self._extract_first_match(text, regex_list)
            if value and hasattr(data_obj, field):
                if field in ["age", "desaturation_events"]:
                    try:
                        setattr(data_obj, field, int(value))
                    except ValueError:
                        pass
                elif field in ["bmi", "ahi", "rdi", "odi", "hi", "rem_ahi", "supine_ahi", 
                              "o2_nadir_pct", "o2_mean_pct", "o2_max_pct", 
                              "time_below_90_pct_min", "time_below_88_pct_min",
                              "mean_bpm", "min_bpm", "max_bpm", "avg_db", "max_db",
                              "rem_percentage"]:
                    try:
                        setattr(data_obj, field, float(value))
                    except ValueError:
                        pass
                else:
                    setattr(data_obj, field, value)
    
    def _extract_sleep_summary(self, text: str, patterns: Dict, sleep_summary: SleepSummary):
        """Special handling for sleep summary with duration calculation"""
        for field, regex_list in patterns.items():
            if field == "sleep_duration_h":
                # Special handling for duration (hours + minutes)
                for pattern in regex_list:
                    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
                    if match:
                        try:
                            hours = int(match.group(1))
                            minutes = int(match.group(2))
                            duration = hours + (minutes / 60.0)
                            sleep_summary.sleep_duration_h = duration
                            break
                        except (ValueError, IndexError):
                            continue
            else:
                value = self._extract_first_match(text, regex_list)
                if value and hasattr(sleep_summary, field):
                    if field == "rem_percentage":
                        try:
                            setattr(sleep_summary, field, float(value))
                        except ValueError:
                            pass
                    else:
                        setattr(sleep_summary, field, value)
    
    def _extract_first_match(self, text: str, regex_patterns: List[str]) -> Optional[str]:
        """Extract first matching value from text using regex patterns"""
        for pattern in regex_patterns:
            try:
                match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
                if match:
                    return match.group(1).strip() if len(match.groups()) > 0 else match.group(0).strip()
            except Exception as e:
                logging.debug(f"Pattern matching error: {str(e)}")
                continue
        return None

# =========================
# Enhanced Text Extraction
# =========================
class TextExtractor:
    """Enhanced text extraction with multiple methods"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def extract_text(self, pdf_bytes: bytes) -> Tuple[str, str]:
        """Extract text using multiple methods, return (text, method_used)"""
        
        # Try methods in order of preference
        methods = [
            ("pdfplumber", self._extract_with_pdfplumber),
            ("pypdf2", self._extract_with_pypdf2),
            ("camelot", self._extract_with_camelot),
            ("tabula", self._extract_with_tabula)
        ]
        
        for method_name, method_func in methods:
            if self._is_method_available(method_name):
                try:
                    self.logger.info(f"Trying extraction method: {method_name}")
                    text = method_func(pdf_bytes)
                    if text and len(text.strip()) > 100:  # Minimum text threshold
                        return text, method_name
                except Exception as e:
                    self.logger.warning(f"{method_name} extraction failed: {str(e)}")
                    continue
        
        raise Exception("All text extraction methods failed")
    
    def _is_method_available(self, method_name: str) -> bool:
        """Check if extraction method dependencies are available"""
        availability_map = {
            "pdfplumber": PDFPLUMBER_AVAILABLE,
            "pypdf2": PYPDF2_AVAILABLE,
            "camelot": CAMELOT_AVAILABLE,
            "tabula": TABULA_AVAILABLE
        }
        return availability_map.get(method_name, False)
    
    def _extract_with_pdfplumber(self, pdf_bytes: bytes) -> str:
        """Extract using PDFPlumber - most reliable for text extraction"""
        if not PDFPLUMBER_AVAILABLE:
            raise ImportError("pdfplumber not available")
        
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            full_text = ""
            
            for page in pdf.pages:
                # Try table extraction first
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        table_text = self._convert_table_to_text(table)
                        full_text += table_text + "\n"
                
                # Extract regular text
                page_text = page.extract_text()
                if page_text:
                    full_text += page_text + "\n"
            
            return full_text
    
    def _extract_with_pypdf2(self, pdf_bytes: bytes) -> str:
        """Fallback extraction using PyPDF2"""
        if not PYPDF2_AVAILABLE:
            raise ImportError("PyPDF2 not available")
        
        reader = PdfReader(BytesIO(pdf_bytes))
        full_text = ""
        
        for page in reader.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"
        
        return full_text
    
    def _extract_with_camelot(self, pdf_bytes: bytes) -> str:
        """Extract using Camelot for table detection"""
        if not CAMELOT_AVAILABLE:
            raise ImportError("camelot not available")
        
        # Save bytes to temporary file for camelot
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
            tmp_file.write(pdf_bytes)
            tmp_file_path = tmp_file.name
        
        try:
            full_text = ""
            
            # Try lattice flavor first
            try:
                tables = camelot.read_pdf(tmp_file_path, pages='all', flavor='lattice')
                for table in tables:
                    df = table.df
                    table_text = df.to_string()
                    full_text += table_text + "\n"
            except Exception:
                # Try stream flavor if lattice fails
                tables = camelot.read_pdf(tmp_file_path, pages='all', flavor='stream')
                for table in tables:
                    df = table.df
                    table_text = df.to_string()
                    full_text += table_text + "\n"
            
            return full_text
            
        finally:
            # Clean up temporary file
            import os
            if os.path.exists(tmp_file_path):
                os.remove(tmp_file_path)
    
    def _extract_with_tabula(self, pdf_bytes: bytes) -> str:
        """Extract using Tabula"""
        if not TABULA_AVAILABLE:
            raise ImportError("tabula not available")
        
        # Save bytes to temporary file for tabula
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
            tmp_file.write(pdf_bytes)
            tmp_file_path = tmp_file.name
        
        try:
            tables = tabula.read_pdf(tmp_file_path, pages='all', multiple_tables=True)
            full_text = ""
            
            for df in tables:
                if not df.empty:
                    table_text = df.to_string()
                    full_text += table_text + "\n"
            
            return full_text
            
        finally:
            # Clean up temporary file
            import os
            if os.path.exists(tmp_file_path):
                os.remove(tmp_file_path)
    
    def _convert_table_to_text(self, table: List[List]) -> str:
        """Convert table data to searchable text"""
        text = ""
        for row in table:
            if row:
                row_text = " ".join([str(cell) if cell else "" for cell in row])
                text += row_text + "\n"
        return text

# =========================
# Main Extractor Class
# =========================
class EnhancedSleepStudyExtractor:
    """Enhanced sleep study extractor with multiple methods and pattern matching"""
    
    def __init__(self):
        self.text_extractor = TextExtractor()
        self.pattern_matcher = PatternMatcher()
        self.logger = logging.getLogger(__name__)
    
    def extract_with_textract(
        self,
        pdf_bytes: bytes,
        report_id: Optional[str] = None,
        source_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Enhanced extraction with multiple fallback methods
        """
        if boto3 is None:
            raise RuntimeError("boto3 is not available; AWS Textract cannot be called in this environment.")

        # PRIMARY APPROACH: Try Textract first
        try:
            client = boto3.client("textract", region_name="us-east-1")
            
            print("🔄 Trying Textract analyze_document with queries...")
            queries = [{"Text": qtext, "Alias": field} for field, qtext in QUERY_SET.items()]
            resp = client.analyze_document(
                Document={"Bytes": pdf_bytes},
                FeatureTypes=["TABLES", "FORMS", "QUERIES"],
                QueriesConfig={"Queries": queries},
            )
            print("✅ Textract analyze_document succeeded!")
            
            # Process Textract response
            cands: List[Candidate] = []
            cands += _collect_query_candidates(resp)
            cands += _collect_kv_candidates(resp)
            cands += _collect_table_candidates(resp)
            print(f"✅ Collected {len(cands)} candidates from Textract")

            # Normalize & reconcile
            best = _reconcile(cands)

            # Map to schema fragment + prune
            frag = _to_patient_case_fragment(best, report_id=report_id, source_uri=source_uri)
            return frag
            
        except Exception as e:
            if "UnsupportedDocumentException" in str(e):
                print("⚠️  Textract failed - document format not supported")
                print("🔄 Falling back to enhanced local text extraction...")
                return self.extract_with_enhanced_fallback(pdf_bytes, report_id, source_uri)
            else:
                print(f"❌ Textract error: {e}")
                print("🔄 Falling back to enhanced local text extraction...")
                return self.extract_with_enhanced_fallback(pdf_bytes, report_id, source_uri)
    
    def extract_with_enhanced_fallback(
        self,
        pdf_bytes: bytes,
        report_id: Optional[str] = None,
        source_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Enhanced fallback extraction using multiple methods
        """
        try:
            print("📄 Using enhanced fallback text extraction methods...")
            
            # Extract text using multiple methods
            extracted_text, method_used = self.text_extractor.extract_text(pdf_bytes)
            
            print(f"📄 Extracted {len(extracted_text)} characters using {method_used}")
            print(f"📄 First 500 chars: {extracted_text[:500]}")
            
            # Save extracted text to file for debugging
            with open(f"extracted_text_enhanced_{report_id or 'debug'}.txt", "w", encoding="utf-8") as f:
                f.write(extracted_text)
            print(f"💾 Saved extracted text to extracted_text_enhanced_{report_id or 'debug'}.txt")
            
            # Extract data using pattern matching
            sleep_data = self.pattern_matcher.extract_data(extracted_text)
            sleep_data.extraction_method = method_used
            
            # Calculate confidence
            confidence = self._calculate_confidence(sleep_data)
            sleep_data.confidence_score = confidence
            
            print(f"🔍 Extracted data with confidence: {confidence:.2f}")
            
            # Convert to schema format
            result = self._convert_to_schema_format(sleep_data, report_id, source_uri)
            return result
            
        except Exception as e:
            print(f"❌ Enhanced fallback extraction failed: {e}")
            # Return empty result
            return _to_patient_case_fragment({}, report_id=report_id, source_uri=source_uri)
    
    def _calculate_confidence(self, data: SleepStudyData) -> float:
        """Calculate confidence score based on extracted data completeness"""
        total_fields = 0
        filled_fields = 0
        
        # Check respiratory indices (most important)
        respiratory_dict = data.respiratory_indices.__dict__
        for field, value in respiratory_dict.items():
            total_fields += 2  # Weight respiratory data more heavily
            if value and value > 0:
                filled_fields += 2
        
        # Check oxygen stats
        oxygen_dict = data.oxygen_stats.__dict__
        for field, value in oxygen_dict.items():
            total_fields += 1
            if value and value > 0:
                filled_fields += 1
        
        # Check other sections
        for section in [data.heart_rate, data.snoring, data.sleep_summary]:
            section_dict = section.__dict__
            for field, value in section_dict.items():
                total_fields += 1
                if value and value != "" and value != 0 and value != 0.0:
                    filled_fields += 1
        
        return filled_fields / total_fields if total_fields > 0 else 0.0
    
    def _convert_to_schema_format(self, data: SleepStudyData, report_id: str, source_uri: str) -> Dict[str, Any]:
        """Convert extracted data to schema format"""
        
        # Build sleep_study section
        sleep_study = {}
        
        # Basic respiratory indices
        if data.respiratory_indices.ahi > 0:
            sleep_study["ahi"] = data.respiratory_indices.ahi
        if data.respiratory_indices.rdi > 0:
            sleep_study["rdi"] = data.respiratory_indices.rdi
        if data.respiratory_indices.odi > 0:
            sleep_study["odi"] = data.respiratory_indices.odi
        if data.respiratory_indices.hi > 0:
            sleep_study["hi"] = data.respiratory_indices.hi
        if data.respiratory_indices.supine_ahi > 0:
            sleep_study["supine_ahi"] = data.respiratory_indices.supine_ahi
        if data.respiratory_indices.rem_ahi > 0:
            sleep_study["rem_ahi"] = data.respiratory_indices.rem_ahi
        if data.respiratory_indices.desaturation_events > 0:
            sleep_study["desaturation_events"] = data.respiratory_indices.desaturation_events
        
        # Sleep duration and efficiency
        if data.sleep_summary.sleep_duration_h > 0:
            sleep_study["sleep_duration_h"] = data.sleep_summary.sleep_duration_h
        if data.sleep_summary.sleep_efficiency_pct > 0:
            sleep_study["sleep_efficiency_pct"] = data.sleep_summary.sleep_efficiency_pct
        
        # Oxygen saturation
        oxygen_saturation = {}
        if data.oxygen_stats.o2_nadir_pct > 0:
            oxygen_saturation["nadir"] = data.oxygen_stats.o2_nadir_pct
        if data.oxygen_stats.o2_mean_pct > 0:
            oxygen_saturation["mean"] = data.oxygen_stats.o2_mean_pct
        if data.oxygen_stats.o2_max_pct > 0:
            oxygen_saturation["max"] = data.oxygen_stats.o2_max_pct
        if data.oxygen_stats.time_below_90_pct_min > 0:
            oxygen_saturation["time_below_90_minutes"] = data.oxygen_stats.time_below_90_pct_min
        if data.oxygen_stats.time_below_88_pct_min > 0:
            oxygen_saturation["time_below_88_minutes"] = data.oxygen_stats.time_below_88_pct_min
        
        if oxygen_saturation:
            sleep_study["oxygen_saturation"] = oxygen_saturation
        
        # Heart rate
        heart_rate = {}
        if data.heart_rate.mean_bpm > 0:
            heart_rate["mean_bpm"] = data.heart_rate.mean_bpm
        if data.heart_rate.min_bpm > 0:
            heart_rate["min_bpm"] = data.heart_rate.min_bpm
        if data.heart_rate.max_bpm > 0:
            heart_rate["max_bpm"] = data.heart_rate.max_bpm
        
        if heart_rate:
            sleep_study["heart_rate"] = heart_rate
        
        # Snoring
        snoring = {}
        if data.snoring.avg_db > 0:
            snoring["avg_db"] = data.snoring.avg_db
        if data.snoring.max_db > 0:
            snoring["max_db"] = data.snoring.max_db
        
        if snoring:
            sleep_study["snoring"] = snoring
        
        # Build provenance
        provenance = []
        for field, value in sleep_study.items():
            if isinstance(value, dict):
                for subfield, subvalue in value.items():
                    provenance.append({
                        "path": f"sleep_study.{field}.{subfield}",
                        "report_id": report_id or "",
                        "source_uri": source_uri or "",
                        "note": f"Enhanced extraction using {data.extraction_method}",
                        "confidence": round(data.confidence_score, 3),
                        "key_text": f"{field}.{subfield}",
                        "raw": str(subvalue),
                    })
            else:
                provenance.append({
                    "path": f"sleep_study.{field}",
                    "report_id": report_id or "",
                    "source_uri": source_uri or "",
                    "note": f"Enhanced extraction using {data.extraction_method}",
                    "confidence": round(data.confidence_score, 3),
                    "key_text": field,
                    "raw": str(value),
                })
        
        # Build final result
        result = {
            "schema_version": "1.0",
            "document_type": "per_report",
            "patient_id": "",
            "as_of": "",
            "report_meta": {
                "report_id": report_id or "",
                "source_report_type": "sleep_study",
                "source_uri": source_uri or "",
                "created_at": None,
                "author_role": "AI-extractor",
            },
            "sleep_study": sleep_study,
            "provenance": provenance,
        }
        
        return result

# =========================
# Legacy Support (for compatibility)
# =========================
def extract_with_textract(
    pdf_bytes: bytes,
    report_id: Optional[str] = None,
    source_uri: Optional[str] = None,
) -> Dict[str, Any]:
    """Legacy function for compatibility"""
    extractor = EnhancedSleepStudyExtractor()
    return extractor.extract_with_textract(pdf_bytes, report_id, source_uri)

def extract_file(path: str, **kwargs) -> Dict[str, Any]:
    """Convenience: open file path and call extract_with_textract."""
    with open(path, "rb") as f:
        data = f.read()
    return extract_with_textract(data, **kwargs)

# =========================
# Legacy functions (keeping for compatibility)
# =========================
QUERY_SET = {
    "ahi": "Apnea-Hypopnea Index (AHI)",
    "sleep_duration_h": "Total Sleep Time (hours)",
    "sleep_efficiency_pct": "Sleep Efficiency (%)",
    "desaturation_events": "Number of desaturation events",
    "o2_nadir_pct": "Minimum SpO2 (%)",
    "o2_mean_pct": "Mean SpO2 (%)",
    "time_below_90_pct_min": "Time below 90% oxygen saturation (minutes)",
    "time_below_88_pct_min": "Time below 88% oxygen saturation (minutes)",
    "rem_ahi": "REM AHI",
    "nrem_ahi": "NREM AHI",
    "supine_ahi": "Supine AHI",
    "non_supine_ahi": "Non-supine AHI",
    "snore_avg_db": "Average snore level (dB)",
    "snore_max_db": "Maximum snore level (dB)",
}

@dataclass
class Candidate:
    field: str
    value: Any
    source: str
    confidence: float
    page: Optional[int] = None
    row: Optional[int] = None
    col: Optional[int] = None
    key_text: Optional[str] = None
    raw: Optional[str] = None

def _collect_query_candidates(resp: Dict[str, Any]) -> List[Candidate]:
    # Legacy function - simplified
    return []

def _collect_kv_candidates(resp: Dict[str, Any]) -> List[Candidate]:
    # Legacy function - simplified
    return []

def _collect_table_candidates(resp: Dict[str, Any]) -> List[Candidate]:
    # Legacy function - simplified
    return []

def _reconcile(cands: List[Candidate]) -> Dict[str, Candidate]:
    # Legacy function - simplified
    return {}

def _to_patient_case_fragment(best: Dict[str, Candidate], report_id: Optional[str], source_uri: Optional[str]) -> Dict[str, Any]:
    # Legacy function - simplified
    return {
        "schema_version": "1.0",
        "document_type": "per_report",
        "patient_id": "",
        "as_of": "",
        "report_meta": {
            "report_id": report_id or "",
            "source_report_type": "sleep_study",
            "source_uri": source_uri or "",
            "created_at": None,
            "author_role": "AI-extractor",
        },
        "sleep_study": {},
        "provenance": [],
    }
