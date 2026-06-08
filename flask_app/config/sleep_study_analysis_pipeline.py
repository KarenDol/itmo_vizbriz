#!/usr/bin/env python3
"""
Sleep study analysis pipeline (v1)

Sends the raw sleep-study file to OpenAI (ChatGPT API): Responses API with PDF ``input_file``
for PDFs, and Chat Completions vision for PNG/JPEG. Parses structured JSON and persists to
observation_store (metric_key paths, source_kind sleep_study).

Environment:
  SLEEP_STUDY_OPENAI_API_KEY — preferred API key for this pipeline
  OPENAI_API_KEY — fallback if the dedicated key is unset
  SLEEP_STUDY_OPENAI_MODEL — default ``gpt-4o`` (vision-capable; PDF via Responses API)
  SLEEP_STUDY_OPENAI_TIMEOUT — HTTP timeout seconds (default 300)
  SLEEP_STUDY_OPENAI_MAX_RETRIES — SDK HTTP retries (default 1; lower = less tail latency on stalls)
  SLEEP_STUDY_OPENAI_PDF_INLINE — if ``true``, send PDF as base64 ``file_data`` (legacy); default uses
    Files API upload + ``file_id`` (smaller request, usually faster than huge data URIs)

Reuses: DB_CONFIG, store_observations_with_deduplication, optional PDF chunking for very large files.
Does not run local PDF text extraction (no extract_document_content).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# OpenAI single-request file limit is 50 MB; stay slightly under. Chunk larger PDFs for reliability.
SLEEP_OPENAI_MAX_INLINE_BYTES = int(
    os.getenv("SLEEP_STUDY_OPENAI_MAX_INLINE_BYTES", str(48 * 1024 * 1024))
)
SLEEP_OPENAI_CHUNK_BYTES = int(
    os.getenv("SLEEP_STUDY_OPENAI_CHUNK_BYTES", str(10 * 1024 * 1024))
)

# Lazy imports from phase2 to avoid circular imports at module load
def _phase2():
    from flask_app.config import document_observation_extractor_phase2 as p2

    return p2


def _is_sleep_study_row(row: Dict[str, Any]) -> bool:
    """True if this adminfiles or files row should run the sleep LLM pipeline."""
    name_raw = row.get("name") or ""
    name = name_raw.lower()
    if name.startswith("level_3_l1_sleepai_") and name.endswith(".pdf"):
        return False
    cat = (row.get("file_category") or row.get("category") or "").strip().lower()
    if "level 3" in cat and "l1" in cat and "sleep" in cat:
        return False
    if cat in ("sleep_test", "sleep_study"):
        return True
    name = (row.get("name") or "").lower()
    s3 = (row.get("s3_key") or "").lower()
    ft = (row.get("file_type") or "").lower()

    if "questionnaire" in s3 or "questionnaire" in cat:
        return False
    if "sleep" in cat or "sleep" in s3:
        return True
    if "sleep" in name or "psg" in name or "hsat" in name or "apnea" in name:
        return True
    if "pdf" in ft or name.endswith(".pdf"):
        if any(k in name for k in ("sleep study", "sleep_study", "sleep-study", "polysomn", "titrat")):
            return True
    return False


def _parse_study_date_iso(study_date: Any) -> Optional[datetime]:
    if not study_date:
        return None
    s = str(study_date).strip()
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            pass
    if re.match(r"^\d{1,2}/\d{1,2}/\d{4}", s):
        for fmt in ("%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(s[:10], fmt)
            except ValueError:
                continue
    try:
        from dateutil import parser as date_parser

        return date_parser.parse(s, fuzzy=True)
    except Exception:
        return None


def _get_openai_api_key() -> Optional[str]:
    k = (os.getenv("SLEEP_STUDY_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    return k or None


def _get_openai_model() -> str:
    return (os.getenv("SLEEP_STUDY_OPENAI_MODEL") or "gpt-4o").strip()


def _get_openai_timeout_seconds() -> float:
    try:
        return float(os.getenv("SLEEP_STUDY_OPENAI_TIMEOUT", "300"))
    except ValueError:
        return 300.0


def _get_openai_max_retries() -> int:
    try:
        return max(0, int(os.getenv("SLEEP_STUDY_OPENAI_MAX_RETRIES", "1")))
    except ValueError:
        return 1


def _sleep_pdf_use_inline_base64() -> bool:
    v = (os.getenv("SLEEP_STUDY_OPENAI_PDF_INLINE") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _sanitize_safe_filename(name: str) -> str:
    base = os.path.splitext(os.path.basename(name or "sleep_study"))[0]
    base = re.sub(r"[^A-Za-z0-9\-\(\)\[\]\s]", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    return (base or "SleepStudy")[:180]


def _pdf_filename_for_openai(display_name: str) -> str:
    base = _sanitize_safe_filename(display_name)
    if not base.lower().endswith(".pdf"):
        return f"{base}.pdf"
    return base[:200]


def _download_object_bytes(s3_key: str) -> Optional[bytes]:
    try:
        import boto3

        bucket = os.getenv("S3_BUCKET_NAME", "vizbrizpatients")
        s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-west-2"))
        obj = s3.get_object(Bucket=bucket, Key=s3_key)
        body = obj["Body"].read()
        logger.info("sleep_study_analysis_pipeline: downloaded s3://%s/%s (%s bytes)", bucket, s3_key, len(body))
        return body
    except Exception as e:
        logger.error("sleep_study_analysis_pipeline: S3 download failed for %s: %s", s3_key, e)
        return None


def _infer_doc_format(s3_key: str, file_type: str, name: str) -> str:
    ft = (file_type or "").lower()
    nk = (name or "").lower()
    sk = (s3_key or "").lower()
    for ext, fmt in (
        (".pdf", "pdf"),
        (".png", "png"),
        (".jpg", "jpeg"),
        (".jpeg", "jpeg"),
    ):
        if sk.endswith(ext) or nk.endswith(ext):
            return fmt
    if "pdf" in ft:
        return "pdf"
    if "png" in ft:
        return "png"
    if "jpeg" in ft or "jpg" in ft:
        return "jpeg"
    return "pdf"


def _merge_sleep_analysis_dict(left: Optional[Dict[str, Any]], right: Dict[str, Any]) -> Dict[str, Any]:
    """Merge partial analyses (e.g. PDF chunks): prefer non-null; numeric leaves take max; lists concat."""
    if not left:
        return dict(right)
    out: Dict[str, Any] = json.loads(json.dumps(left))
    for k, rv in right.items():
        if rv is None:
            continue
        if k not in out or out[k] is None:
            out[k] = rv
            continue
        lv = out[k]
        if isinstance(lv, dict) and isinstance(rv, dict):
            out[k] = _merge_sleep_analysis_dict(lv, rv)
        elif isinstance(lv, list) and isinstance(rv, list):
            merged_list: List[Any] = []
            seen_keys: set = set()
            for x in lv + rv:
                if x is None:
                    continue
                dedupe_key = json.dumps(x, sort_keys=True) if isinstance(x, dict) else str(x)
                if dedupe_key not in seen_keys:
                    seen_keys.add(dedupe_key)
                    merged_list.append(x)
            out[k] = merged_list
        elif isinstance(lv, (int, float)) and isinstance(rv, (int, float)):
            out[k] = max(float(lv), float(rv))
        elif isinstance(lv, str) and isinstance(rv, str):
            if rv.strip() and rv.strip() not in lv:
                out[k] = f"{lv} | {rv}"[:12000]
        else:
            out[k] = rv
    return out


def _sleep_analysis_system_prompt() -> str:
    return """You are a clinical sleep-medicine information extraction engine.
The patient's sleep study / polysomnography / home sleep test is attached as a document or image.
Read the attachment (tables, plots, and narrative) and return ONE JSON object.

Rules:
- Numbers only where explicitly supported by the report (use null if absent).
- Do not guess clinical values not stated in the attachment.
- study_info.study_date: ISO YYYY-MM-DD if you can parse a study / recording date, else null.
- diagnosis.severity: one of mild|moderate|severe|normal|null based on report wording and AHI if present.
- clinical_insights: 3-8 short English bullet strings highlighting clinically relevant findings for a dentist/sleep coordinator (not patient-facing marketing).
- report_content: clinician-facing narrative for a Level 3 integrated report (see structure below). Use ONLY values stated in the attachment for metrics; write observations and interpretation in formal clinical English.

report_content rules:
- study_header: one line, e.g. "Home Sleep Study – Home Sleep Test (February 23, 2026)" using study_info.study_type and a long-form date when available.
- key_metrics_table: 6-10 rows of the most clinically important metrics from the report (Parameter + Value). Prefer items such as AHI, respiratory event counts/types, oxygen nadir, mean SpO2, time below 90%, desaturation event count, total sleep or recording time, supine sleep %, snoring %, RDI/ODI when present. Values must include units as in the source report.
- observations: 5-8 bullet strings. Each is one complete clinical observation sentence (not a label). Comment on significance (e.g. mild vs significant OSA, symptom–index mismatch if noted in report).
- interpretation_paragraphs: 3-5 short paragraphs synthesizing findings, limitations of the study type if relevant, and clinical relevance for treatment planning. Do not invent questionnaire symptoms unless they appear in the sleep report.

Return JSON with exactly this structure (all keys present; use null for unknown):
{
  "patient_demographics": {"age": null, "sex": null, "bmi": null, "height_cm": null, "weight_kg": null},
  "sleep_metrics": {
    "ahi": null, "rdi": null, "odi": null, "supine_ahi": null, "rem_ahi": null, "nrem_ahi": null,
    "cai": null, "mixed_index": null
  },
  "oxygen_metrics": {
    "o2_nadir_pct": null, "time_below_90_pct": null, "time_below_88_pct": null, "mean_spo2_pct": null
  },
  "sleep_architecture": {
    "total_sleep_time_min": null, "sleep_efficiency_pct": null, "rem_pct": null, "nrem_pct": null,
    "latency_rem_min": null, "latency_persistent_sleep_min": null
  },
  "arousal_and_movement": {"arousal_index": null, "plm_index": null, "limb_movement_index": null},
  "snoring_and_hr": {"snore_pct_of_sleep": null, "heart_rate_mean": null, "heart_rate_min": null, "heart_rate_max": null},
  "diagnosis": {"severity": null, "primary_diagnosis": null, "impression": null},
  "study_info": {"study_date": null, "study_type": null, "device_lab": null, "technician_notes": null},
  "clinical_insights": [],
  "report_content": {
    "study_header": null,
    "key_metrics_table": [{"parameter": null, "value": null}],
    "observations": [],
    "interpretation_paragraphs": []
  }
}"""


def _parse_json_output(raw: str) -> Dict[str, Any]:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```\s*$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("sleep_study_analysis_pipeline: JSON parse failed: %s", e)
        return {"_error": "json_parse", "_raw": raw[:8000]}
    if isinstance(data, dict) and not isinstance(data.get("clinical_insights"), list):
        data["clinical_insights"] = []
    if isinstance(data, dict):
        rc = data.get("report_content")
        if not isinstance(rc, dict):
            data["report_content"] = {
                "study_header": None,
                "key_metrics_table": [],
                "observations": [],
                "interpretation_paragraphs": [],
            }
    return data if isinstance(data, dict) else {"_error": "json_not_object", "_raw": raw[:2000]}


def _openai_response_output_text(resp: Any) -> str:
    t = getattr(resp, "output_text", None)
    if t and str(t).strip():
        return str(t).strip()
    out = getattr(resp, "output", None) or []
    buf: List[str] = []
    for block in out:
        if isinstance(block, dict):
            for part in block.get("content") or []:
                if isinstance(part, dict) and part.get("text"):
                    buf.append(str(part.get("text") or ""))
    return "".join(buf).strip()


def _call_openai_sleep_analysis_pdf(content: bytes, display_name: str) -> Dict[str, Any]:
    from openai import OpenAI

    api_key = _get_openai_api_key()
    if not api_key:
        return {"_error": "missing_openai_api_key", "_raw": "Set SLEEP_STUDY_OPENAI_API_KEY or OPENAI_API_KEY"}

    model = _get_openai_model()
    timeout = _get_openai_timeout_seconds()
    max_retries = _get_openai_max_retries()
    client = OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)

    fname = _pdf_filename_for_openai(display_name)
    inline_pdf = _sleep_pdf_use_inline_base64()
    uploaded = None

    system_text = _sleep_analysis_system_prompt()
    user_text = (
        f"Source filename: {display_name}\n"
        "Analyze the attached sleep study PDF and respond with ONLY the JSON object "
        "described in your instructions (no markdown, no commentary)."
    )

    try:
        if inline_pdf:
            b64 = base64.standard_b64encode(content).decode("ascii")
            data_uri = f"data:application/pdf;base64,{b64}"
            file_part: Dict[str, Any] = {
                "type": "input_file",
                "filename": fname,
                "file_data": data_uri,
            }
            logger.info(
                "sleep_study_analysis_pipeline: PDF path=inline_base64 bytes=%s fname=%s",
                len(content),
                fname,
            )
        else:
            t_up = time.monotonic()
            uploaded = client.files.create(
                file=(fname, content),
                purpose="user_data",
                timeout=timeout,
            )
            logger.info(
                "sleep_study_analysis_pipeline: OpenAI files.create id=%s bytes=%s in %.2fs",
                getattr(uploaded, "id", None),
                len(content),
                time.monotonic() - t_up,
            )
            # Responses API: file_id and filename are mutually exclusive for input_file.
            file_part = {
                "type": "input_file",
                "file_id": uploaded.id,
            }

        t_resp = time.monotonic()
        response = client.responses.create(
            model=model,
            instructions=system_text,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": user_text},
                        file_part,
                    ],
                }
            ],
            max_output_tokens=4096,
            temperature=0.05,
        )
        logger.info(
            "sleep_study_analysis_pipeline: responses.create finished in %.2fs (model=%s)",
            time.monotonic() - t_resp,
            model,
        )
        raw_out = _openai_response_output_text(response)
    except Exception as e:
        logger.exception("sleep_study_analysis_pipeline: OpenAI Responses call failed")
        return {"_error": "openai_exception", "_raw": str(e)}
    finally:
        if uploaded is not None and getattr(uploaded, "id", None):
            try:
                client.files.delete(uploaded.id)
            except Exception as del_err:
                logger.warning("sleep_study_analysis_pipeline: files.delete failed: %s", del_err)

    return _parse_json_output(raw_out)


def _call_openai_sleep_analysis_image(content: bytes, display_name: str, doc_format: str) -> Dict[str, Any]:
    from openai import OpenAI

    api_key = _get_openai_api_key()
    if not api_key:
        return {"_error": "missing_openai_api_key", "_raw": "Set SLEEP_STUDY_OPENAI_API_KEY or OPENAI_API_KEY"}

    model = _get_openai_model()
    timeout = _get_openai_timeout_seconds()
    max_retries = _get_openai_max_retries()
    client = OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)

    fmt = (doc_format or "png").lower()
    mime = "image/png" if fmt == "png" else "image/jpeg"
    b64 = base64.standard_b64encode(content).decode("ascii")
    url = f"data:{mime};base64,{b64}"

    system_text = _sleep_analysis_system_prompt()
    user_text = (
        f"Source filename: {display_name}\n"
        "Analyze the attached sleep study image and respond with ONLY the JSON object "
        "described in the system message (no markdown, no commentary)."
    )

    try:
        completion = client.chat.completions.create(
            model=model,
            temperature=0.05,
            max_tokens=4096,
            messages=[
                {"role": "system", "content": system_text},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": url}},
                    ],
                },
            ],
        )
        msg = completion.choices[0].message
        raw_out = (msg.content or "").strip() if isinstance(msg.content, str) else ""
        if not raw_out and isinstance(msg.content, list):
            parts: List[str] = []
            for block in msg.content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
            raw_out = "".join(parts).strip()
    except Exception as e:
        logger.exception("sleep_study_analysis_pipeline: OpenAI vision call failed")
        return {"_error": "openai_exception", "_raw": str(e)}

    return _parse_json_output(raw_out)


def _call_openai_sleep_analysis_raw_document(
    content: bytes,
    display_name: str,
    doc_format: str,
) -> Dict[str, Any]:
    fmt = (doc_format or "pdf").lower()
    if fmt == "pdf":
        return _call_openai_sleep_analysis_pdf(content, display_name)
    if fmt in ("png", "jpeg"):
        return _call_openai_sleep_analysis_image(content, display_name, fmt)
    return {"_error": f"unsupported_format:{fmt}", "_raw": ""}


def _analyze_sleep_file_bytes(
    file_bytes: bytes,
    display_name: str,
    doc_format: str,
) -> Dict[str, Any]:
    """
    Run OpenAI on raw bytes. Chunk very large PDFs for size and latency.
    """
    if not file_bytes:
        return {"_error": "empty_bytes"}

    if not _get_openai_api_key():
        return {"_error": "missing_openai_api_key", "_raw": "Set SLEEP_STUDY_OPENAI_API_KEY or OPENAI_API_KEY"}

    fmt = (doc_format or "pdf").lower()
    if fmt != "pdf":
        if len(file_bytes) > SLEEP_OPENAI_MAX_INLINE_BYTES:
            return {"_error": "document_too_large", "_raw": f"{len(file_bytes)} bytes"}
        return _call_openai_sleep_analysis_raw_document(file_bytes, display_name, fmt)

    if len(file_bytes) <= SLEEP_OPENAI_MAX_INLINE_BYTES:
        return _call_openai_sleep_analysis_raw_document(file_bytes, display_name, "pdf")

    p2 = _phase2()
    chunks: List[Tuple[bytes, int, int]] = p2._chunk_pdf_by_pages(file_bytes, SLEEP_OPENAI_CHUNK_BYTES)
    if not chunks:
        return {"_error": "chunk_failed", "_raw": f"{len(file_bytes)} bytes"}

    merged: Optional[Dict[str, Any]] = None
    for idx, (chunk_bytes, start_p, end_p) in enumerate(chunks):
        chunk_label = f"{display_name} (pages {start_p + 1}-{end_p + 1})"
        part = _call_openai_sleep_analysis_raw_document(chunk_bytes, chunk_label, "pdf")
        if part.get("_error"):
            logger.warning(
                "sleep_study_analysis_pipeline: chunk %s/%s failed: %s",
                idx + 1,
                len(chunks),
                part.get("_error"),
            )
            continue
        merged = _merge_sleep_analysis_dict(merged, part)

    if not merged:
        return {"_error": "all_chunks_failed", "_raw": ""}
    return merged


def _flatten_analysis_to_observations(
    analysis: Dict[str, Any],
    evidence_prefix: str,
) -> List[Dict[str, Any]]:
    """
    Map nested analysis dict to observation_store rows (path + value) compatible with phase2 canonical.
    """
    obs: List[Dict[str, Any]] = []

    def add_num(path: str, val: Any, note: str):
        if val is None:
            return
        try:
            float(str(val).replace(",", "."))
        except (TypeError, ValueError):
            return
        obs.append(
            {
                "path": path,
                "value": val,
                "observation": note,
                "confidence": 90,
                "explanation": "sleep_study_analysis_pipeline v1",
                "evidence": (evidence_prefix + note)[:2000],
                "source": "sleep_study_analysis_pipeline",
            }
        )

    sm = analysis.get("sleep_metrics") or {}
    ox = analysis.get("oxygen_metrics") or {}
    arch = analysis.get("sleep_architecture") or {}
    am = analysis.get("arousal_and_movement") or {}
    shr = analysis.get("snoring_and_hr") or {}
    demo = analysis.get("patient_demographics") or {}

    # Primary respiratory indices (canonical normalization expects these prefixes)
    add_num("respiratory_indices.ahi_overall", sm.get("ahi"), "AHI overall")
    add_num("respiratory_indices.rdi", sm.get("rdi"), "RDI")
    add_num("respiratory_indices.odi", sm.get("odi"), "ODI")
    add_num("respiratory_indices.supine_ahi", sm.get("supine_ahi"), "Supine AHI")
    add_num("respiratory_indices.rem_ahi", sm.get("rem_ahi"), "REM AHI")
    add_num("respiratory_indices.nrem_ahi", sm.get("nrem_ahi"), "NREM AHI")
    add_num("respiratory_indices.cai", sm.get("cai"), "Central AHI")

    add_num("oxygenation.spo2_nadir_pct", ox.get("o2_nadir_pct"), "SpO2 nadir")
    add_num("respiratory_indices.time_below_90_pct", ox.get("time_below_90_pct"), "Time below 90%")
    add_num("respiratory_indices.time_below_88_pct_min", ox.get("time_below_88_pct"), "Time below 88%")
    add_num("oxygenation.spo2_mean_pct", ox.get("mean_spo2_pct"), "Mean SpO2")

    if arch.get("total_sleep_time_min") is not None:
        try:
            tstm = float(arch.get("total_sleep_time_min"))
            hours = round(tstm / 60.0, 2)
            add_num("sleep_timing_architecture.sleep_duration_h", hours, "Total sleep time (hours, from minutes)")
        except (TypeError, ValueError):
            pass

    add_num("sleep_timing_architecture.sleep_efficiency_pct", arch.get("sleep_efficiency_pct"), "Sleep efficiency")
    add_num("sleep_timing_architecture.rem_latency_min", arch.get("latency_rem_min"), "REM latency")
    add_num("sleep_timing_architecture.sleep_onset_latency_min", arch.get("latency_persistent_sleep_min"), "Sleep onset latency")

    add_num("sleep_study.arousal_index", am.get("arousal_index"), "Arousal index")
    add_num("sleep_study.plm_index", am.get("plm_index"), "PLM index")
    add_num("sleep_study.limb_movement_index", am.get("limb_movement_index"), "Limb movement index")

    add_num("sleep_study.snoring_pct", shr.get("snore_pct_of_sleep"), "Snoring % of sleep")
    add_num("sleep_study.heart_rate.mean_bpm", shr.get("heart_rate_mean"), "Mean HR")
    add_num("sleep_study.heart_rate.min_bpm", shr.get("heart_rate_min"), "Min HR")
    add_num("sleep_study.heart_rate.max_bpm", shr.get("heart_rate_max"), "Max HR")

    add_num("sleep_study.demographics.age_years", demo.get("age"), "Age from report header")
    add_num("sleep_study.demographics.bmi", demo.get("bmi"), "BMI from report")

    # Full JSON snapshot for L3 / audits (numeric placeholder; full JSON in explanation + evidence)
    snap = json.dumps(analysis, ensure_ascii=False)
    obs.append(
        {
            "path": "sleep_study.pipeline_snapshot_v1",
            "value": 1,
            "observation": "Full structured LLM extraction JSON",
            "confidence": 100,
            "explanation": snap[:12000],
            "evidence": snap[:8000],
            "source": "sleep_study_analysis_pipeline",
        }
    )

    return obs


def _already_has_pipeline_snapshot(patient_id: int, s3_key: str) -> bool:
    p2 = _phase2()
    conn = None
    try:
        import mysql.connector

        conn = mysql.connector.connect(**p2.DB_CONFIG)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM observation_store
            WHERE patient_id = %s AND s3_key = %s AND metric_key = %s
            """,
            (patient_id, s3_key, "sleep_study.pipeline_snapshot_v1"),
        )
        row = cur.fetchone()
        return bool(row and row[0] and int(row[0]) > 0)
    except Exception as e:
        logger.warning("pipeline snapshot check failed: %s", e)
        return False
    finally:
        if conn:
            conn.close()


def _list_sleep_like_documents(patient_id: int) -> List[Dict[str, Any]]:
    """
    Sleep-like PDFs from both adminfiles and files (patient uploads often use files.category=sleep_test).
    Each row includes source_table 'adminfiles' | 'files' and file_category for heuristics.
    """
    p2 = _phase2()
    conn = None
    out: List[Dict[str, Any]] = []
    try:
        import mysql.connector

        conn = mysql.connector.connect(**p2.DB_CONFIG)
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, name, patient_id, file_type, s3_key, upload_date, file_category, analyzed
            FROM adminfiles
            WHERE patient_id = %s
            ORDER BY upload_date DESC
            """,
            (patient_id,),
        )
        for r in cur.fetchall() or []:
            if _is_sleep_study_row(r):
                r["source_table"] = "adminfiles"
                out.append(r)

        cur.execute(
            """
            SELECT id, name, patient_id, file_type, s3_key, upload_date,
                   category AS file_category, subcategory, analyzed
            FROM files
            WHERE patient_id = %s AND category != 'imaging'
            ORDER BY upload_date DESC
            """,
            (patient_id,),
        )
        for r in cur.fetchall() or []:
            if _is_sleep_study_row(r):
                r["source_table"] = "files"
                out.append(r)

        out.sort(key=lambda x: x.get("upload_date") or datetime.min, reverse=True)
        return out
    finally:
        if conn:
            conn.close()


def run_sleep_study_pipeline_for_document_row(
    patient_id: int,
    file_row: Dict[str, Any],
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """Run LLM extraction + DB persist for one adminfiles or files row."""
    p2 = _phase2()
    s3_key = file_row.get("s3_key") or ""
    name = file_row.get("name") or "unknown"
    source_table = (file_row.get("source_table") or "adminfiles").strip().lower()
    if source_table not in ("adminfiles", "files"):
        source_table = "adminfiles"

    def _log_row(ret: Dict[str, Any]) -> Dict[str, Any]:
        return ret

    if not s3_key:
        return _log_row({"success": False, "error": "missing_s3_key", "file": name})

    if not force and _already_has_pipeline_snapshot(patient_id, s3_key):
        return _log_row({"success": True, "skipped": True, "reason": "snapshot_exists", "file": name})

    doc = {
        "s3_key": s3_key,
        "file_type": file_row.get("file_type") or "application/pdf",
        "name": name,
    }
    raw_bytes = _download_object_bytes(s3_key)
    if not raw_bytes:
        return _log_row({"success": False, "error": "s3_download_failed", "file": name})

    doc_format = _infer_doc_format(s3_key, doc["file_type"], name)
    analysis = _analyze_sleep_file_bytes(raw_bytes, name, doc_format)
    if analysis.get("_error"):
        return _log_row(
            {"success": False, "error": analysis.get("_error"), "file": name, "raw": analysis.get("_raw")}
        )

    study_info = analysis.get("study_info") or {}
    doc_date = _parse_study_date_iso(study_info.get("study_date"))

    document_info = {
        "name": name,
        "file_type": doc["file_type"],
        "s3_key": s3_key,
        "id": file_row.get("id"),
        "source_table": source_table,
        "upload_date": file_row.get("upload_date"),
        "document_date": doc_date,
    }

    obs = _flatten_analysis_to_observations(analysis, evidence_prefix=f"{name}: ")
    if len(obs) <= 1:
        return _log_row({"success": False, "error": "no_metrics_extracted", "file": name})

    ok = p2.store_observations_with_deduplication(patient_id, "sleep_test", obs, document_info)
    return _log_row(
        {
            "success": bool(ok),
            "file": name,
            "metrics_written": max(0, len(obs) - 1),
            "study_date": study_info.get("study_date"),
        }
    )


def run_sleep_study_pipeline_for_admin_file(
    patient_id: int,
    admin_row: Dict[str, Any],
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """Backward-compatible name; delegates to run_sleep_study_pipeline_for_document_row."""
    if not admin_row.get("source_table"):
        admin_row = dict(admin_row)
        admin_row["source_table"] = "adminfiles"
    return run_sleep_study_pipeline_for_document_row(patient_id, admin_row, force=force)


def run_sleep_study_pipeline_for_patient(
    patient_id: int,
    *,
    force: bool = False,
    admin_file_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Process all sleep-like admin PDFs for a patient (or one id).

    Returns aggregate stats. Can run standalone (UI / queue) or after a legacy
    phase2 pass; the UI path uses :mod:`flask_app.services.direct_sleep_extraction` only.
    """
    files = _list_sleep_like_documents(patient_id)
    if admin_file_id is not None:
        files = [r for r in files if r.get("id") == admin_file_id]

    results: List[Dict[str, Any]] = []
    for row in files:
        try:
            results.append(run_sleep_study_pipeline_for_admin_file(patient_id, row, force=force))
        except Exception as e:
            logger.exception("sleep pipeline failed for %s: %s", row.get("name"), e)
            results.append({"success": False, "file": row.get("name"), "error": str(e)})

    processed = sum(1 for r in results if r.get("success") and not r.get("skipped"))
    skipped = sum(1 for r in results if r.get("skipped"))
    failed = sum(1 for r in results if not r.get("success"))

    return {
        "patient_id": patient_id,
        "files_considered": len(files),
        "processed": processed,
        "skipped": skipped,
        "failed": failed,
        "details": results,
    }
