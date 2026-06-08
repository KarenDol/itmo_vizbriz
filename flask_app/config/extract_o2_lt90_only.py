#!/usr/bin/env python3
"""
Standalone LLM-only extractor for percent time SpO2 < 90.
- Reads all patient documents (files + adminfiles)
- Normalizes text, then uses multiple LLM prompts to extract the metric
- Stores observations with metric_key=time_below_90_pct (non-destructive)
- Refreshes minimal canonical JSON

Usage:
  python -m flask_app.config.extract_o2_lt90_only --patient-id 46351
"""
from __future__ import annotations
import argparse
import re
import mysql.connector
from typing import Optional

# Try relative imports first, fall back to absolute imports
try:
    from document_observation_extractor_phase2 import DB_CONFIG, extract_document_content
    from bedrock_config import query_bedrock_claude_enhanced as bedrock_query_enhanced
except ImportError:
    # Fall back to absolute imports if running from vizbriz root
    from flask_app.config.document_observation_extractor_phase2 import DB_CONFIG, extract_document_content
    from flask_app.config.bedrock_config import query_bedrock_claude_enhanced as bedrock_query_enhanced


def normalize_text(text: str) -> str:
    try:
        subs = str.maketrans({'₀':'0','₁':'1','₂':'2','₃':'3','₄':'4','₅':'5','₆':'6','₇':'7','₈':'8','₉':'9'})
        t = text.translate(subs)
        t = t.replace('％','%').replace('\u200b','').replace('\u00a0',' ')
        t = t.replace('O₂','O2').replace('SpO₂','SpO2')
        return t
    except Exception:
        return text


def ask_llm_direct(document_text: str) -> Optional[float]:
    """Ask the LLM directly for the percent of sleep with SpO2 < 90."""
    combined_prompt = (
        "You extract a single numerical metric from a sleep study. "
        "Return ONLY the number (no text), representing the percent of sleep time with oxygen saturation below 90.\n\n"
        "Question: What percentage of time did this patient spend with oxygen saturation below 90%?\n\n"
        f"Document: {document_text[:8000]}"
    )
    messages = [{"role": "user", "content": combined_prompt}]
    resp = bedrock_query_enhanced(messages, max_tokens=64, temperature=0.0, top_p=0.9)
    if isinstance(resp, dict) and resp.get("success"):
        raw = (resp.get("response") or "").strip()
        m = re.search(r"(\d+(?:\.\d+)?)\s*%?", raw)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                return None
    return None


def ask_llm_structured(document_text: str) -> Optional[float]:
    """Ask LLM for structured JSON including time_below_90%."""
    combined_prompt = (
        "Extract sleep study metrics as STRICT JSON. "
        "Keys: time_below_90_percent_o2 (number), o2_nadir (number), ahi (number).\n\n"
        "Return ONLY JSON. Example: {\"time_below_90_percent_o2\": 0.5, \"o2_nadir\": 83, \"ahi\": 28.2}.\n\n"
        f"Document: {document_text[:8000]}"
    )
    messages = [{"role": "user", "content": combined_prompt}]
    resp = bedrock_query_enhanced(messages, max_tokens=128, temperature=0.0, top_p=0.9)
    if isinstance(resp, dict) and resp.get("success"):
        raw = (resp.get("response") or "").strip()
        try:
            import json
            data = json.loads(raw)
            val = data.get("time_below_90_percent_o2")
            if val is None:
                return None
            return float(val)
        except Exception:
            return None
    return None


def ask_llm_keyvalue(document_text: str) -> Optional[float]:
    """Ask LLM for key-value pairs; parse the specific key."""
    combined_prompt = (
        "Extract key-value lines from the sleep study text focusing on oxygen metrics.\n\n"
        "Return concise lines like 'Time <90% SpO2: 0.5%' if present.\n\n"
        f"Document: {document_text[:8000]}"
    )
    messages = [{"role": "user", "content": combined_prompt}]
    resp = bedrock_query_enhanced(messages, max_tokens=256, temperature=0.0, top_p=0.9)
    if isinstance(resp, dict) and resp.get("success"):
        raw = (resp.get("response") or "").strip()
        m = re.search(r"(time\s*<\s*90%[^\n\r\d]{0,40})(\d+(?:\.\d+)?)\s*%?", raw, flags=re.IGNORECASE)
        if m:
            try:
                return float(m.group(2))
            except Exception:
                return None
    return None


def store_observation(patient_id: int, name: str, table: str, s3_key: Optional[str], upload_date, value: float, evidence: str = None):
    from flask_app.config.document_observation_extractor_phase2 import store_observations_with_deduplication
    observation = {
        'path': 'sleep_study.time_below_90_pct',
        'value': str(value),
        'source': 'o2-lt90-llm-only',
        'confidence': 85,
        'explanation': 'LLM-only extraction for percent time SpO2 < 90',
        'evidence': evidence,
    }
    store_observations_with_deduplication(patient_id, 'numerical_extraction', [observation], {
        'name': name or 'o2_lt90_doc',
        'file_type': 'text/plain',
        'id': None,
        'source_table': table,
        's3_key': s3_key,
        'upload_date': upload_date,
        'document_date': None,
    })


def run(patient_id: int) -> dict:
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name, s3_key, upload_date FROM files WHERE patient_id=%s", (patient_id,))
    files = cursor.fetchall() or []
    cursor.execute("SELECT id, name, s3_key, upload_date FROM adminfiles WHERE patient_id=%s", (patient_id,))
    adminfiles = cursor.fetchall() or []
    conn.close()

    docs = []
    for r in files:
        docs.append({'table': 'files', **r})
    for r in adminfiles:
        docs.append({'table': 'adminfiles', **r})

    added = 0
    for d in docs:
        name = d.get('name') or ''
        s3_key = d.get('s3_key')
        # Build a minimal document dict for the shared extractor
        # Attempt to infer file_type from extension
        file_type = 'application/pdf' if (name or '').lower().endswith('.pdf') else ''
        doc = {'s3_key': s3_key, 'file_type': file_type, 'name': name}
        text = extract_document_content(doc)
        if not text:
            continue
        text = normalize_text(text)
        val = ask_llm_direct(text)
        if val is None:
            val = ask_llm_structured(text)
        if val is None:
            val = ask_llm_keyvalue(text)
        if val is None:
            continue
        store_observation(patient_id, name, d.get('table'), s3_key, d.get('upload_date'), val, evidence='LLM-only')
        added += 1

    # Rebuild canonical minimal JSON
    from flask_app.config.document_observation_extractor_phase2 import create_minimal_canonical_json_for_patient
    try:
        create_minimal_canonical_json_for_patient(patient_id)
    except Exception:
        pass
    return {'patient_id': patient_id, 'added': added}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--patient-id', type=int, required=True)
    args = parser.parse_args()

    print(f"Running O2 extraction for patient {args.patient_id}")
    result = run(args.patient_id)
    print(f"Extraction completed: {result}")
