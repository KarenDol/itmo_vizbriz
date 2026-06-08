# textract_extractor_enhanced.py
# Enhanced version with better pattern matching for comprehensive sleep study data

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import json
import re
from io import BytesIO

# ---- If boto3 is not installed in your environment, install it in your image/env.
try:
    import boto3
except Exception as e:
    boto3 = None

# ---- Text extraction fallback methods (from osaagent_routes.py)
try:
    from PyPDF2 import PdfReader
    from pdf2image import convert_from_bytes
    from PIL import Image
    import pytesseract
    import pdfplumber
except Exception as e:
    print(f"Warning: Some text extraction libraries not available: {e}")

# =========================
# Enhanced Config: Queries & Synonyms
# =========================
QUERY_SET: Dict[str, str] = {
    # canonical_field : Textract query text (tune as needed)
    "ahi": "Apnea-Hypopnea Index (AHI)",
    "sleep_duration_h": "Total Sleep Time (hours)",
    "sleep_efficiency_pct": "Sleep Efficiency (%)",
    "desaturation_events": "Number of desaturation events",
    "o2_nadir_pct": "Minimum SpO2 (%)",
    "o2_mean_pct": "Mean SpO2 (%)",
    "o2_max_pct": "Maximum SpO2 (%)",
    "time_below_90_pct_min": "Time below 90% oxygen saturation (minutes)",
    "time_below_88_pct_min": "Time below 88% oxygen saturation (minutes)",
    "rem_ahi": "REM AHI",
    "nrem_ahi": "NREM AHI",
    "supine_ahi": "Supine AHI",
    "non_supine_ahi": "Non-supine AHI",
    "snore_avg_db": "Average snore level (dB)",
    "snore_max_db": "Maximum snore level (dB)",
    "heart_rate_mean": "Mean heart rate (BPM)",
    "heart_rate_min": "Minimum heart rate (BPM)",
    "heart_rate_max": "Maximum heart rate (BPM)",
    "rem_sleep_pct": "REM sleep percentage",
    "snoring_pct": "Snoring percentage of sleep time",
}

# Enhanced labels we might see in tables / forms → canonical fields
SYNONYMS: Dict[str, List[str]] = {
    "ahi": ["AHI", "Apnea-Hypopnea Index", "Apnea Hypopnea Index", "pAHI"],
    "rdi": ["RDI", "Respiratory Disturbance Index", "pRDI"],
    "odi": ["ODI", "Oxygen Desaturation Index"],
    "oai": ["OAI"],
    "cai": ["CAI"],
    "hi":  ["HI", "Hypopnea Index"],

    "sleep_duration_h": ["Total Sleep Time", "TST", "Sleep Time", "Total Study Time"],
    "sleep_efficiency_pct": ["Sleep Efficiency", "SE"],

    "o2_nadir_pct": ["Min SpO2", "Nadir SpO2", "Minimum SaO2", "Min SaO2", "O2 Nadir", "Minimum:", "Minimum SpO2"],
    "o2_mean_pct": ["Mean SpO2", "Average SaO2", "Mean SaO2", "Mean:", "Mean SpO2"],
    "o2_max_pct": ["Max SpO2", "Maximum SaO2", "Maximum:", "Maximum SpO2"],
    "time_below_90_pct_min": ["Time <90%", "Time below 90%", "Oxygen Saturation <90"],
    "time_below_88_pct_min": ["Time <88%", "Time below 88%", "Oxygen Saturation <=88"],

    "rem_ahi": ["REM AHI"],
    "nrem_ahi": ["NREM AHI"],
    "supine_ahi": ["Supine AHI"],
    "non_supine_ahi": ["Non-supine AHI", "Non supine AHI"],

    "snore_avg_db": ["Average snore (dB)", "Avg Snore dB", "Snore dB"],
    "snore_max_db": ["Max snore (dB)", "Maximum Snore dB"],
    
    "heart_rate_mean": ["Mean heart rate", "Mean pulse rate", "Mean BPM", "Pulse Rate Mean"],
    "heart_rate_min": ["Min heart rate", "Min pulse rate", "Min BPM", "Pulse Rate Minimum"],
    "heart_rate_max": ["Max heart rate", "Max pulse rate", "Max BPM", "Pulse Rate Maximum"],
    
    "rem_sleep_pct": ["REM sleep percentage", "REM of Sleep Time", "% REM"],
    "snoring_pct": ["Snoring percentage", "Snoring %", "Snoring:"],
}

SOURCE_PRIORITY = {"TABLE": 3, "QUERY": 2, "FORM": 2, "CELL": 2, "TEXT": 1}

# =========================
# Internal data structure
# =========================
@dataclass
class Candidate:
    field: str
    value: Any
    source: str           # TABLE|FORM|QUERY|CELL|TEXT
    confidence: float
    page: Optional[int] = None
    row: Optional[int] = None
    col: Optional[int] = None
    key_text: Optional[str] = None
    raw: Optional[str] = None

# =========================
# Text Extraction Fallback (from osaagent_routes.py)
# =========================
def extract_text_from_file_fallback(file_content: bytes) -> str:
    """Extract text from PDF or image file content - FALLBACK METHOD."""
    print("=== Starting fallback text extraction process ===")
    try:
        # First try to read as PDF
        try:
            print("Attempting PDF extraction with PdfReader...")
            pdf_reader = PdfReader(BytesIO(file_content))
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
            
            if text.strip():
                print("Successfully extracted text using PdfReader")
                return text

            print("No text found with PdfReader, trying pdfplumber...")
            with pdfplumber.open(BytesIO(file_content)) as pdf:
                text = ""
                for page in pdf.pages:
                    text += page.extract_text() or "" + "\n"
                if text.strip():
                    print("Successfully extracted text using pdfplumber")
                    return text

            print("No text found with pdfplumber, attempting OCR...")
            images = convert_from_bytes(file_content)
            text = ""
            for image in images:
                img_byte_arr = BytesIO()
                image.save(img_byte_arr, format='PNG')
                img_byte_arr = img_byte_arr.getvalue()
                text += pytesseract.image_to_string(Image.open(BytesIO(img_byte_arr))) + "\n"
            print("Successfully extracted text using OCR")
            return text

        except Exception as pdf_error:
            print(f"PDF extraction failed: {str(pdf_error)}, trying image OCR")
            
            # If PDF fails, try processing as image
            text = pytesseract.image_to_string(Image.open(BytesIO(file_content)))
            print("Successfully extracted text from image")
            return text

    except Exception as e:
        print(f"Text extraction failed: {str(e)}")
        raise Exception(f"Failed to extract text from file: {str(e)}")

# =========================
# Enhanced Pattern Matching
# =========================
def _search_text_for_patterns(text_content: str) -> List[Candidate]:
    """Enhanced search for sleep study patterns with better coverage."""
    cands: List[Candidate] = []
    
    # Search for patterns in the text
    for field, labels in SYNONYMS.items():
        for label in labels:
            # Enhanced patterns for better matching
            patterns = [
                rf'{re.escape(label)}[:\s]*([0-9]+\.?[0-9]*)',  # Label: 42.5
                rf'{re.escape(label)}\s*=\s*([0-9]+\.?[0-9]*)',  # Label = 42.5
                rf'{re.escape(label)}\s+([0-9]+\.?[0-9]*)',      # Label 42.5
                rf'([0-9]+\.?[0-9]*)\s*{re.escape(label)}',      # 42.5 Label
                rf'{re.escape(label)}[:\s]*([0-9]+\.?[0-9]*)\s*%',  # Label: 42.5%
                rf'{re.escape(label)}[:\s]*([0-9]+\.?[0-9]*)\s*dB',  # Label: 42.5dB
                rf'{re.escape(label)}[:\s]*([0-9]+\.?[0-9]*)\s*BPM',  # Label: 42.5BPM
            ]
            
            for pattern in patterns:
                matches = re.finditer(pattern, text_content, re.IGNORECASE)
                
                for match in matches:
                    value = match.group(1)
                    cands.append(Candidate(
                        field=field,
                        value=value,
                        source="TEXT",
                        confidence=80.0,  # Fallback confidence
                        page=1,
                        key_text=label,
                        raw=value
                    ))
                    print(f"🔍 Found {field}: {value} (from fallback text)")
                    break  # Only take first match per field
    
    # Special patterns for oxygen saturation data
    o2_patterns = [
        (r'Mean[:\s]*([0-9]+\.?[0-9]*)\s*%', 'o2_mean_pct'),
        (r'Minimum[:\s]*([0-9]+\.?[0-9]*)\s*%', 'o2_nadir_pct'),
        (r'Maximum[:\s]*([0-9]+\.?[0-9]*)\s*%', 'o2_max_pct'),
        (r'O2\s*Nadir[:\s]*([0-9]+\.?[0-9]*)\s*%', 'o2_nadir_pct'),
        (r'SpO2[:\s]*([0-9]+\.?[0-9]*)\s*%', 'o2_mean_pct'),
    ]
    
    for pattern, field in o2_patterns:
        matches = re.finditer(pattern, text_content, re.IGNORECASE)
        for match in matches:
            value = match.group(1)
            cands.append(Candidate(
                field=field,
                value=value,
                source="TEXT",
                confidence=85.0,
                page=1,
                key_text="Oxygen Saturation",
                raw=value
            ))
            print(f"🔍 Found {field}: {value} (oxygen saturation)")
    
    # Special patterns for heart rate data
    hr_patterns = [
        (r'Mean[:\s]*([0-9]+\.?[0-9]*)\s*BPM', 'heart_rate_mean'),
        (r'Minimum[:\s]*([0-9]+\.?[0-9]*)\s*BPM', 'heart_rate_min'),
        (r'Maximum[:\s]*([0-9]+\.?[0-9]*)\s*BPM', 'heart_rate_max'),
        (r'Pulse Rate[:\s]*([0-9]+\.?[0-9]*)\s*BPM', 'heart_rate_mean'),
    ]
    
    for pattern, field in hr_patterns:
        matches = re.finditer(pattern, text_content, re.IGNORECASE)
        for match in matches:
            value = match.group(1)
            cands.append(Candidate(
                field=field,
                value=value,
                source="TEXT",
                confidence=85.0,
                page=1,
                key_text="Heart Rate",
                raw=value
            ))
            print(f"🔍 Found {field}: {value} (heart rate)")
    
    # Special patterns for snoring data
    snore_patterns = [
        (r'Snoring[:\s]*([0-9]+\.?[0-9]*)\s*%', 'snoring_pct'),
        (r'([0-9]+\.?[0-9]*)\s*dB.*snor', 'snore_avg_db'),
        (r'snor.*([0-9]+\.?[0-9]*)\s*dB', 'snore_avg_db'),
    ]
    
    for pattern, field in snore_patterns:
        matches = re.finditer(pattern, text_content, re.IGNORECASE)
        for match in matches:
            value = match.group(1)
            cands.append(Candidate(
                field=field,
                value=value,
                source="TEXT",
                confidence=80.0,
                page=1,
                key_text="Snoring",
                raw=value
            ))
            print(f"🔍 Found {field}: {value} (snoring)")
    
    # Special patterns for sleep duration
    sleep_patterns = [
        (r'([0-9]+)\s*hrs?[,\s]*([0-9]+)\s*min', 'sleep_duration_h'),
        (r'Total Study Time[:\s]*([0-9]+)\s*hrs?[,\s]*([0-9]+)\s*min', 'sleep_duration_h'),
    ]
    
    for pattern, field in sleep_patterns:
        matches = re.finditer(pattern, text_content, re.IGNORECASE)
        for match in matches:
            if field == 'sleep_duration_h':
                hours = int(match.group(1))
                minutes = int(match.group(2))
                value = hours + (minutes / 60.0)
            else:
                value = match.group(1)
            
            cands.append(Candidate(
                field=field,
                value=value,
                source="TEXT",
                confidence=85.0,
                page=1,
                key_text="Sleep Duration",
                raw=f"{match.group(1)}hrs {match.group(2)}min"
            ))
            print(f"🔍 Found {field}: {value} (sleep duration)")
    
    return cands

# =========================
# Public API
# =========================
def extract_with_textract(
    pdf_bytes: bytes,
    report_id: Optional[str] = None,
    source_uri: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Analyze a single PDF (bytes) via Textract and return a sparse per_report JSON
    mapped to patient_case_v1 (sleep_study.*, provenance[]).
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
            print("🔄 Falling back to local text extraction...")
            return extract_with_fallback_text_extraction(pdf_bytes, report_id, source_uri)
        else:
            print(f"❌ Textract error: {e}")
            print("🔄 Falling back to local text extraction...")
            return extract_with_fallback_text_extraction(pdf_bytes, report_id, source_uri)

def extract_with_fallback_text_extraction(
    pdf_bytes: bytes,
    report_id: Optional[str] = None,
    source_uri: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fallback extraction using local text extraction methods when Textract fails.
    """
    try:
        print("📄 Using fallback text extraction methods...")
        
        # Extract text using the working method from osaagent_routes.py
        extracted_text = extract_text_from_file_fallback(pdf_bytes)
        
        print(f"📄 Extracted {len(extracted_text)} characters using fallback methods")
        print(f"📄 First 500 chars: {extracted_text[:500]}")
        
        # Save extracted text to file for debugging
        with open(f"extracted_text_fallback_{report_id or 'debug'}.txt", "w", encoding="utf-8") as f:
            f.write(extracted_text)
        print(f"💾 Saved extracted text to extracted_text_fallback_{report_id or 'debug'}.txt")
        
        # Search for patterns in the extracted text
        cands = _search_text_for_patterns(extracted_text)
        
        print(f"🔍 Found {len(cands)} candidates from fallback text extraction")
        
        # Normalize & reconcile
        best = _reconcile(cands)
        
        # Map to schema fragment + prune
        frag = _to_patient_case_fragment(best, report_id=report_id, source_uri=source_uri)
        return frag
        
    except Exception as e:
        print(f"❌ Fallback text extraction failed: {e}")
        # Return empty result
        return _to_patient_case_fragment({}, report_id=report_id, source_uri=source_uri)

def extract_file(path: str, **kwargs) -> Dict[str, Any]:
    """Convenience: open file path and call extract_with_textract."""
    with open(path, "rb") as f:
        data = f.read()
    return extract_with_textract(data, **kwargs)

# =========================
# Textract parsing helpers (same as before)
# =========================
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
                elif c["BlockType"] == "SELECTION_ELEMENT" and c.get("SelectionStatus") == "SELECTED":
                    txt.append("☑")
    return " ".join(txt).strip()

def _collect_query_candidates(resp: Dict[str, Any]) -> List[Candidate]:
    cands: List[Candidate] = []
    for b in resp.get("Blocks", []):
        if b["BlockType"] == "QUERY_RESULT":
            alias = b.get("Query", {}).get("Alias")
            val = b.get("Text", "")
            conf = float(b.get("Confidence", 0.0) or 0.0)
            if alias and val:
                cands.append(Candidate(field=alias, value=val, source="QUERY",
                                       confidence=conf, page=b.get("Page"), raw=val))
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
        # map label to canonical field
        for field, labels in SYNONYMS.items():
            if any(lbl.lower() in k_norm for lbl in labels):
                conf = (float(k.get("Confidence", 0.0) or 0.0) + float(v.get("Confidence", 0.0) or 0.0)) / 2.0
                cands.append(Candidate(field=field, value=vtext, source="FORM",
                                       confidence=conf, page=k.get("Page"), key_text=ktext, raw=vtext))
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
        # gather cells
        cells = []
        for rel in t.get("Relationships", []):
            if rel["Type"] == "CHILD":
                for cid in rel.get("Ids", []):
                    cb = idx[cid]
                    if cb["BlockType"] == "CELL":
                        cells.append(cb)
        # naive key:value per row scan (2 leftmost cells)
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
            for field, labels in SYNONYMS.items():
                if any(lbl.lower() in k_norm for lbl in labels):
                    conf = (float(left.get("Confidence", 0.0) or 0.0) + float(right.get("Confidence", 0.0) or 0.0)) / 2.0
                    cands.append(Candidate(field=field, value=vtext, source="TABLE",
                                           confidence=conf, page=page, row=r_i, col=2,
                                           key_text=ktext, raw=vtext))
                    break
    return cands

# =========================
# Normalize & reconcile (same as before)
# =========================
def _to_float(s: str) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None

def _normalize_value(field: str, raw: str) -> Optional[Any]:
    if raw is None:
        return None
    txt = str(raw).strip()

    # strip trailing "%" as number when present
    if txt.endswith("%"):
        try:
            return max(0.0, min(100.0, float(txt[:-1].strip())))
        except Exception:
            pass

    if field == "sleep_duration_h":
        low = txt.lower()
        if "min" in low:
            num = _to_float(low.replace("min", ""))
            return round(num / 60.0, 3) if num is not None else None
        num = _to_float(txt)
        if num is None:
            return None
        if num > 14:  # assume minutes if absurd for hours
            return round(num / 60.0, 3)
        return num

    if field in ("sleep_efficiency_pct", "o2_nadir_pct", "o2_mean_pct", "o2_max_pct", "rem_sleep_pct", "snoring_pct"):
        num = _to_float(txt)
        if num is None:
            return None
        if 0.0 <= num <= 1.0:  # convert fractions to percentages
            num *= 100.0
        return max(0.0, min(100.0, num))

    if field in ("desaturation_events",):
        num = _to_float(txt)
        return int(num) if num is not None else None

    if field in (
        "ahi","rdi","odi","oai","cai","hi",
        "rem_ahi","nrem_ahi","supine_ahi","non_supine_ahi",
        "time_below_90_pct_min","time_below_88_pct_min",
        "snore_avg_db","snore_max_db",
        "heart_rate_mean","heart_rate_min","heart_rate_max",
    ):
        return _to_float(txt)

    # default (string)
    return txt or None

def _reconcile(cands: List[Candidate]) -> Dict[str, Candidate]:
    by_field: Dict[str, List[Candidate]] = {}
    for c in cands:
        norm = _normalize_value(c.field, c.value)
        if norm is None:
            continue
        by_field.setdefault(c.field, []).append(Candidate(
            field=c.field, value=norm, source=c.source, confidence=c.confidence,
            page=c.page, row=c.row, col=c.col, key_text=c.key_text, raw=str(c.value)
        ))

    best: Dict[str, Candidate] = {}
    for field, cs in by_field.items():
        cs.sort(key=lambda x: (SOURCE_PRIORITY.get(x.source, 0), x.confidence), reverse=True)
        best[field] = cs[0]
    return best

# =========================
# Schema mapping & pruning (same as before)
# =========================
def _prune_empty(obj):
    if isinstance(obj, dict):
        out = {k: _prune_empty(v) for k, v in obj.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(obj, list):
        out = [_prune_empty(v) for v in obj]
        out = [v for v in out if v not in (None, "", [], {})]
        return out
    return obj

def _to_patient_case_fragment(best: Dict[str, Candidate],
                              report_id: Optional[str],
                              source_uri: Optional[str]) -> Dict[str, Any]:
    sleep_study: Dict[str, Any] = {}
    snoring: Dict[str, Any] = {}
    heart_rate: Dict[str, Any] = {}
    oxygen_saturation: Dict[str, Any] = {}

    def put(k: str):
        if k in best:
            sleep_study[k] = best[k].value

    # Basic sleep study metrics
    for k in (
        "ahi","rdi","odi","oai","cai","hi",
        "sleep_duration_h","sleep_efficiency_pct",
        "rem_ahi","nrem_ahi","supine_ahi","non_supine_ahi",
        "rem_sleep_pct","snoring_pct",
    ):
        put(k)

    # Oxygen saturation data
    for k in ("o2_nadir_pct", "o2_mean_pct", "o2_max_pct", "time_below_90_pct_min", "time_below_88_pct_min"):
        if k in best:
            oxygen_saturation[k.replace("o2_", "").replace("_pct", "").replace("_min", "_minutes")] = best[k].value
    
    if oxygen_saturation:
        sleep_study["oxygen_saturation"] = oxygen_saturation

    # Heart rate data
    for k in ("heart_rate_mean", "heart_rate_min", "heart_rate_max"):
        if k in best:
            heart_rate[k.replace("heart_rate_", "")] = best[k].value
    
    if heart_rate:
        sleep_study["heart_rate"] = heart_rate

    # Snoring data
    if "snore_avg_db" in best: snoring["avg_db"] = best["snore_avg_db"].value
    if "snore_max_db" in best: snoring["max_db"] = best["snore_max_db"].value
    if snoring:
        sleep_study["snoring"] = snoring

    provenance: List[Dict[str, Any]] = []
    for field, c in best.items():
        path = f"sleep_study.{field}"
        if field.startswith("o2_"):
            path = f"sleep_study.oxygen_saturation.{field.replace('o2_', '').replace('_pct', '').replace('_min', '_minutes')}"
        elif field.startswith("heart_rate_"):
            path = f"sleep_study.heart_rate.{field.replace('heart_rate_', '')}"
        elif field.startswith("snore_"):
            path = "sleep_study.snoring." + ("avg_db" if field == "snore_avg_db" else "max_db")
        
        provenance.append({
            "path": path,
            "report_id": report_id or "",
            "source_uri": source_uri or "",
            "note": f"{c.source} p{c.page}" if c.page else c.source,
            "confidence": round(c.confidence, 3),
            "key_text": c.key_text,
            "raw": c.raw,
        })

    out = {
        "schema_version": "1.0",
        "document_type": "per_report",
        "patient_id": "",      # fill upstream if known
        "as_of": "",           # fill upstream if known
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
    return _prune_empty(out)
