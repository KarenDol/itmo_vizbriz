# textract_extractor_pdfplumber.py
# Drop-in helper to extract sleep-study markers from PDFs using AWS Textract
# and map them to your patient_case_v1 schema (sparse output + provenance).

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# ---- If boto3 is not installed in your environment, install it in your image/env.
try:
    import boto3
except Exception as e:
    boto3 = None

# ---- PDFPlumber for better text extraction
try:
    import pdfplumber
except Exception as e:
    pdfplumber = None

# =========================
# Config: Queries & Synonyms
# =========================
QUERY_SET: Dict[str, str] = {
    # canonical_field : Textract query text (tune as needed)
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

# Labels we might see in tables / forms → canonical fields
SYNONYMS: Dict[str, List[str]] = {
    "ahi": ["AHI", "Apnea-Hypopnea Index", "Apnea Hypopnea Index"],
    "rdi": ["RDI", "Respiratory Disturbance Index"],
    "odi": ["ODI", "Oxygen Desaturation Index"],
    "oai": ["OAI"],
    "cai": ["CAI"],
    "hi":  ["HI", "Hypopnea Index"],

    "sleep_duration_h": ["Total Sleep Time", "TST"],
    "sleep_efficiency_pct": ["Sleep Efficiency", "SE"],

    "o2_nadir_pct": ["Min SpO2", "Nadir SpO2", "Minimum SaO2", "Min SaO2"],
    "o2_mean_pct": ["Mean SpO2", "Average SaO2", "Mean SaO2"],
    "time_below_90_pct_min": ["Time <90%", "Time below 90%"],
    "time_below_88_pct_min": ["Time <88%", "Time below 88%"],

    "rem_ahi": ["REM AHI"],
    "nrem_ahi": ["NREM AHI"],
    "supine_ahi": ["Supine AHI"],
    "non_supine_ahi": ["Non-supine AHI", "Non supine AHI"],

    "snore_avg_db": ["Average snore (dB)", "Avg Snore dB"],
    "snore_max_db": ["Max snore (dB)", "Maximum Snore dB"],
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

    client = boto3.client("textract", region_name="us-east-1")
    
    # Try analyze_document first (for native PDFs)
    try:
        queries = [{"Text": qtext, "Alias": field} for field, qtext in QUERY_SET.items()]
        resp = client.analyze_document(
            Document={"Bytes": pdf_bytes},
            FeatureTypes=["TABLES", "FORMS", "QUERIES"],
            QueriesConfig={"Queries": queries},
        )
        print("✅ Used analyze_document with queries")
    except Exception as e:
        if "UnsupportedDocumentException" in str(e):
            print("⚠️  Document format not supported by Textract, using PDFPlumber fallback")
            # Fallback to PDFPlumber for unsupported formats
            return extract_with_pdfplumber_fallback(pdf_bytes, report_id, source_uri)
        else:
            raise e

    # 2) Collect candidates from queries, forms (KV), and tables
    cands: List[Candidate] = []
    cands += _collect_query_candidates(resp)
    cands += _collect_kv_candidates(resp)
    cands += _collect_table_candidates(resp)
    print(f"✅ Collected {len(cands)} candidates from analyze_document")

    # 3) Normalize & reconcile
    best = _reconcile(cands)

    # 4) Map to schema fragment + prune
    frag = _to_patient_case_fragment(best, report_id=report_id, source_uri=source_uri)
    return frag


def extract_file(path: str, **kwargs) -> Dict[str, Any]:
    """Convenience: open file path and call extract_with_textract."""
    with open(path, "rb") as f:
        data = f.read()
    return extract_with_textract(data, **kwargs)

def extract_with_pdfplumber_fallback(
    pdf_bytes: bytes,
    report_id: Optional[str] = None,
    source_uri: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fallback extraction using PDFPlumber when Textract doesn't support the document format.
    """
    if pdfplumber is None:
        print("PDFPlumber not available, returning empty result")
        return _to_patient_case_fragment({}, report_id=report_id, source_uri=source_uri)
    
    try:
        # Open PDF with PDFPlumber - need to use BytesIO for bytes
        import io
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text_content = ""
            table_data = []
            
            print(f"PDFPlumber: PDF has {len(pdf.pages)} pages")
            
            for page_num, page in enumerate(pdf.pages):
                print(f"Processing page {page_num + 1}")
                
                # Extract text
                page_text = page.extract_text()
                if page_text:
                    text_content += page_text + "\n"
                    print(f"  Extracted {len(page_text)} chars of text")
                
                # Extract tables
                tables = page.extract_tables()
                if tables:
                    print(f"  Found {len(tables)} tables")
                    for table_num, table in enumerate(tables):
                        table_data.append({
                            'page': page_num + 1,
                            'table': table_num + 1,
                            'data': table
                        })
                        print(f"    Table {table_num + 1}: {len(table)} rows")
            
            # If no text extracted, try Textract OCR
            if not text_content.strip():
                print("No text extracted by PDFPlumber, trying Textract OCR...")
                return extract_with_textract_ocr(pdf_bytes, report_id, source_uri)
            
            # Clean up the text
            text_content = text_content.strip()
            
            print(f"PDFPlumber: Extracted {len(text_content)} chars total")
            print(f"First 500 chars: {text_content[:500]}")
            
            # Save extracted text to file for debugging
            with open(f"extracted_text_{report_id or 'debug'}.txt", "w", encoding="utf-8") as f:
                f.write(text_content)
            print(f"Saved extracted text to extracted_text_{report_id or 'debug'}.txt")
            
            # Now search for patterns in the extracted text and tables
            cands = _search_text_for_patterns(text_content)
            cands += _search_tables_for_patterns(table_data)
            
            print(f"Found {len(cands)} candidates from PDFPlumber")
            
            # Normalize & reconcile
            best = _reconcile(cands)
            
            # Map to schema fragment + prune
            frag = _to_patient_case_fragment(best, report_id=report_id, source_uri=source_uri)
            return frag
        
    except Exception as e:
        print(f"PDFPlumber extraction failed: {e}")
        # Try Textract OCR as last resort
        print("Trying Textract OCR as fallback...")
        return extract_with_textract_ocr(pdf_bytes, report_id, source_uri)

def extract_with_textract_ocr(
    pdf_bytes: bytes,
    report_id: Optional[str] = None,
    source_uri: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Extract text using Textract OCR for scanned documents.
    """
    if boto3 is None:
        print("boto3 not available, returning empty result")
        return _to_patient_case_fragment({}, report_id=report_id, source_uri=source_uri)
    
    try:
        client = boto3.client("textract", region_name="us-east-1")
        
        print("Using Textract OCR (detect_document_text)...")
        resp = client.detect_document_text(
            Document={"Bytes": pdf_bytes}
        )
        
        # Extract text from OCR response
        text_lines = []
        for item in resp.get('Blocks', []):
            if item.get('BlockType') == 'LINE':
                text_lines.append(item.get('Text', ''))
        
        text_content = '\n'.join(text_lines)
        
        print(f"Textract OCR: Extracted {len(text_content)} chars")
        print(f"First 500 chars: {text_content[:500]}")
        
        # Save extracted text to file for debugging
        with open(f"extracted_text_ocr_{report_id or 'debug'}.txt", "w", encoding="utf-8") as f:
            f.write(text_content)
        print(f"Saved OCR text to extracted_text_ocr_{report_id or 'debug'}.txt")
        
        # Search for patterns in the OCR text
        cands = _search_text_for_patterns(text_content)
        
        print(f"Found {len(cands)} candidates from Textract OCR")
        
        # Normalize & reconcile
        best = _reconcile(cands)
        
        # Map to schema fragment + prune
        frag = _to_patient_case_fragment(best, report_id=report_id, source_uri=source_uri)
        return frag
        
    except Exception as e:
        print(f"Textract OCR failed: {e}")
        # Return empty result
        return _to_patient_case_fragment({}, report_id=report_id, source_uri=source_uri)

def _search_text_for_patterns(text_content: str) -> List[Candidate]:
    """Search extracted text for sleep study patterns."""
    cands: List[Candidate] = []
    
    # Search for patterns in the text
    for field, labels in SYNONYMS.items():
        for label in labels:
            # Look for the label followed by numbers
            import re
            # More flexible patterns
            patterns = [
                rf'{re.escape(label)}[:\s]*([0-9]+\.?[0-9]*)',  # Label: 42.5
                rf'{re.escape(label)}\s*=\s*([0-9]+\.?[0-9]*)',  # Label = 42.5
                rf'{re.escape(label)}\s+([0-9]+\.?[0-9]*)',      # Label 42.5
                rf'([0-9]+\.?[0-9]*)\s*{re.escape(label)}',      # 42.5 Label
            ]
            
            for pattern in patterns:
                matches = re.finditer(pattern, text_content, re.IGNORECASE)
                
                for match in matches:
                    value = match.group(1)
                    cands.append(Candidate(
                        field=field,
                        value=value,
                        source="TEXT",
                        confidence=85.0,  # PDFPlumber confidence
                        page=1,
                        key_text=label,
                        raw=value
                    ))
                    print(f"Found {field}: {value} (from text)")
                    break  # Only take first match per field
    
    return cands

def _search_tables_for_patterns(table_data: List[Dict]) -> List[Candidate]:
    """Search tables for sleep study patterns."""
    cands: List[Candidate] = []
    
    for table_info in table_data:
        table = table_info['data']
        page = table_info['page']
        
        # Search each row for key-value pairs
        for row_num, row in enumerate(table):
            if len(row) >= 2:  # Need at least 2 columns for key-value
                for col_num in range(len(row) - 1):
                    key_cell = str(row[col_num]).strip()
                    value_cell = str(row[col_num + 1]).strip()
                    
                    if key_cell and value_cell:
                        # Check if key matches any of our labels
                        for field, labels in SYNONYMS.items():
                            for label in labels:
                                if label.lower() in key_cell.lower():
                                    # Try to extract number from value
                                    import re
                                    number_match = re.search(r'([0-9]+\.?[0-9]*)', value_cell)
                                    if number_match:
                                        value = number_match.group(1)
                                        cands.append(Candidate(
                                            field=field,
                                            value=value,
                                            source="TABLE",
                                            confidence=90.0,  # Table confidence
                                            page=page,
                                            row=row_num,
                                            col=col_num,
                                            key_text=key_cell,
                                            raw=value_cell
                                        ))
                                        print(f"Found {field}: {value} (from table)")
                                        break
    
    return cands

# =========================
# Textract parsing helpers
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
# Normalize & reconcile
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

    if field in ("sleep_efficiency_pct", "o2_nadir_pct", "o2_mean_pct"):
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
# Schema mapping & pruning
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

    def put(k: str):
        if k in best:
            sleep_study[k] = best[k].value

    for k in (
        "ahi","rdi","odi","oai","cai","hi",
        "sleep_duration_h","sleep_efficiency_pct",
        "o2_nadir_pct","o2_mean_pct","time_below_90_pct_min","time_below_88_pct_min",
        "desaturation_events","rem_ahi","nrem_ahi","supine_ahi","non_supine_ahi",
    ):
        put(k)

    if "snore_avg_db" in best: snoring["avg_db"] = best["snore_avg_db"].value
    if "snore_max_db" in best: snoring["max_db"] = best["snore_max_db"].value
    if snoring:
        sleep_study["snoring"] = snoring

    provenance: List[Dict[str, Any]] = []
    for field, c in best.items():
        path = f"sleep_study.{field}"
        if field.startswith("snore_"):
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
