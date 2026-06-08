"""
Auto-generate Level 3 PDF after sleep-study AI analysis.

Flow:
1. Build sleep-study-only PDF pages from OpenAI pipeline snapshot (metrics, observations, interpretation).
2. Prepend the latest Level 2 OSA Data Assessment PDF (questionnaire narrative — no duplicate L1 work).
3. Store merged PDF as one AdminFile per sleep study upload.

L2 is sourced from adminfiles (category ``Level 2 - OSA Data Assessment``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

# Stored on auto-generated L3 PDFs; manifest groups all categories containing "level 3".
L3_L1_SLEEP_AI_CATEGORY = "Level 3 - L1 + Sleep AI"
L2_OSA_ASSESSMENT_CATEGORY = "Level 2 - OSA Data Assessment"

logger = logging.getLogger(__name__)

_L1_SYMPTOM_PLACEHOLDERS = frozenset(
    {
        "A detailed symptom summary will appear here based on your responses.",
        "סיכום התסמינים יופיע כאן לאחר עיבוד התשובות.",
        "טקסט לדוגמה לתסמינים שדווחו.",
        "Placeholder narrative summary of reported symptoms.",
    }
)


def _fmt_tst_minutes(minutes: Any) -> str:
    try:
        m = float(minutes)
    except (TypeError, ValueError):
        return ""
    if m <= 0:
        return ""
    h = int(m // 60)
    r = int(round(m % 60))
    if h and r:
        return f"{h}h {r}min"
    if h:
        return f"{h}h"
    return f"{r} min"


def _as_pct_str(val: Any, suffix: str = "%") -> str:
    if val is None:
        return ""
    try:
        v = float(str(val).replace(",", "."))
    except (TypeError, ValueError):
        return str(val).strip()
    if v == int(v):
        return f"{int(v)}{suffix}"
    return f"{v:g}{suffix}"


def _evt_hr(val: Any) -> str:
    if val is None:
        return "Not provided"
    try:
        return f"{float(str(val).replace(',', '.')):g} events/hr"
    except (TypeError, ValueError):
        return str(val).strip()


def _strip_html(val: Any) -> str:
    if not val or not isinstance(val, str):
        return ""
    t = re.sub(r"<[^>]+>", " ", val)
    t = re.sub(r"\s+", " ", t).replace("&nbsp;", " ").strip()
    return t


def _format_study_date_long(study_date: Any) -> str:
    if not study_date:
        return ""
    s = str(study_date).strip()
    if not s:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%B %d, %Y")
        except ValueError:
            continue
    return s


def _default_study_header(analysis: Dict[str, Any]) -> str:
    study_info = analysis.get("study_info") or {}
    study_type = (study_info.get("study_type") or "Home Sleep Study").strip()
    long_date = _format_study_date_long(study_info.get("study_date"))
    if long_date:
        return f"{study_type} – {long_date}"
    return study_type


def _normalize_metrics_table(rows: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        param = str(row.get("parameter") or row.get("name") or "").strip()
        val = str(row.get("value") or "").strip()
        if param and val and val.lower() not in ("null", "none", "not provided"):
            out.append({"parameter": param, "value": val})
    return out[:12]


def _build_default_key_metrics_table(analysis: Dict[str, Any]) -> List[Dict[str, str]]:
    """Fallback metrics table from structured extraction when report_content is absent."""
    sm = analysis.get("sleep_metrics") or {}
    ox = analysis.get("oxygen_metrics") or {}
    arch = analysis.get("sleep_architecture") or {}
    shr = analysis.get("snoring_and_hr") or {}
    rows: List[Dict[str, str]] = []

    def add(param: str, val: Any, fmt=None):
        if val is None or str(val).strip() == "":
            return
        rows.append({"parameter": param, "value": fmt(val) if fmt else str(val).strip()})

    add("AHI", sm.get("ahi"), _evt_hr)
    add("RDI", sm.get("rdi"), _evt_hr)
    add("ODI", sm.get("odi"), _evt_hr)
    add("Oxygen Nadir", ox.get("o2_nadir_pct"), lambda v: _as_pct_str(v))
    add("Mean SpO₂", ox.get("mean_spo2_pct"), lambda v: _as_pct_str(v))
    add("Time <90% SpO₂", ox.get("time_below_90_pct"), lambda v: _as_pct_str(v))
    tst = _fmt_tst_minutes(arch.get("total_sleep_time_min"))
    if tst:
        rows.append({"parameter": "Total Sleep Time", "value": tst})
    add("Sleep Efficiency", arch.get("sleep_efficiency_pct"), lambda v: _as_pct_str(v))
    add("Snoring", shr.get("snore_pct_of_sleep"), lambda v: _as_pct_str(v))
    return rows


def _report_content_from_analysis(analysis: Dict[str, Any]) -> Dict[str, Any]:
    rc = analysis.get("report_content")
    return rc if isinstance(rc, dict) else {}


def generate_level3_sleep_narrative_with_bedrock(
    patient_id: int,
    analysis: Dict[str, Any],
    questionnaire_context: Optional[Dict[str, Any]] = None,
    *,
    sleep_study_only: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Generate study header, key metrics table, observations, and interpretation (Bedrock fallback).
    Default ``sleep_study_only=True``: use only sleep extraction JSON (L2 PDF carries questionnaire).
    """
    try:
        from flask_app.config.bedrock_config import query_bedrock_claude_enhanced
    except Exception as e:
        logger.warning("Bedrock import failed for L3 sleep narrative: %s", e)
        return None

    safe_analysis = {k: v for k, v in (analysis or {}).items() if k != "_raw"}
    qctx = questionnaire_context or {}
    if sleep_study_only or not qctx:
        prompt = f"""You are a clinical sleep-medicine assistant drafting the sleep-study portion of a Level 3 report.

Use ONLY facts in SLEEP_STUDY_JSON. Do not invent test results. Patient questionnaire / OSA screening narrative is in a separate Level 2 PDF — do not duplicate it.

Write formal clinical English for a treating dentist / sleep coordinator audience.

Return JSON ONLY:
{{
  "study_header": "Home Sleep Study – <study type> (<Month D, YYYY> if known)",
  "key_metrics_table": [{{"parameter": "AHI", "value": "4 events/hr"}}],
  "observations": ["One complete observation sentence per bullet (5-8 bullets)."],
  "interpretation_paragraphs": ["3-5 short paragraphs on sleep findings only."]
}}

SLEEP_STUDY_JSON:
{json.dumps(safe_analysis, ensure_ascii=False, default=str)[:14000]}
"""
    else:
        prompt = f"""You are a clinical sleep-medicine assistant drafting sections for a Level 3 integrated sleep report.

Use ONLY facts in SLEEP_STUDY_JSON and QUESTIONNAIRE_CONTEXT.

Return JSON ONLY with keys: study_header, key_metrics_table, observations, interpretation_paragraphs.

SLEEP_STUDY_JSON:
{json.dumps(safe_analysis, ensure_ascii=False, default=str)[:14000]}

QUESTIONNAIRE_CONTEXT:
{json.dumps(qctx, ensure_ascii=False, default=str)[:8000]}
"""

    result = query_bedrock_claude_enhanced(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2500,
        temperature=0.15,
        patient_id=patient_id,
        endpoint="level3_sleep_narrative",
        use_knowledge_base=False,
    )
    if not result or not result.get("success"):
        return None
    text = (result.get("response") or "").strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _apply_sleep_report_content(
    report_data: Dict[str, Any],
    analysis: Dict[str, Any],
    patient_id: int,
    questionnaire_context: Optional[Dict[str, Any]] = None,
    *,
    sleep_study_only: bool = True,
) -> None:
    """Populate sleep study narrative fields from extraction and/or Bedrock (sleep test only by default)."""
    rc = _report_content_from_analysis(analysis)
    study_header = (rc.get("study_header") or "").strip() or _default_study_header(analysis)
    report_data["sleep_study_intro"] = study_header

    metrics = _normalize_metrics_table(rc.get("key_metrics_table"))
    if not metrics:
        metrics = _build_default_key_metrics_table(analysis)
    report_data["sleep_key_metrics_table"] = metrics

    observations: List[str] = []
    if isinstance(rc.get("observations"), list):
        observations = [str(x).strip() for x in rc["observations"] if str(x).strip()]
    elif isinstance(analysis.get("clinical_insights"), list):
        observations = [str(x).strip() for x in analysis["clinical_insights"] if str(x).strip()]

    interpretation_parts: List[str] = []
    if isinstance(rc.get("interpretation_paragraphs"), list):
        interpretation_parts = [str(x).strip() for x in rc["interpretation_paragraphs"] if str(x).strip()]
    diag = analysis.get("diagnosis") or {}
    impr = diag.get("impression") or diag.get("primary_diagnosis")
    if not interpretation_parts and isinstance(impr, str) and impr.strip():
        interpretation_parts = [impr.strip()]

    need_bedrock = (os.getenv("LEVEL3_SLEEP_NARRATIVE_BEDROCK", "1").strip().lower() not in ("0", "false", "no", "off")) and (
        len(observations) < 3 or len(interpretation_parts) < 1
    )
    if need_bedrock:
        try:
            llm_rc = generate_level3_sleep_narrative_with_bedrock(
                patient_id,
                analysis,
                questionnaire_context,
                sleep_study_only=sleep_study_only,
            )
            if isinstance(llm_rc, dict):
                if llm_rc.get("study_header"):
                    report_data["sleep_study_intro"] = str(llm_rc["study_header"]).strip()
                llm_metrics = _normalize_metrics_table(llm_rc.get("key_metrics_table"))
                if llm_metrics:
                    report_data["sleep_key_metrics_table"] = llm_metrics
                if len(observations) < 3 and isinstance(llm_rc.get("observations"), list):
                    observations = [str(x).strip() for x in llm_rc["observations"] if str(x).strip()]
                if not interpretation_parts and isinstance(llm_rc.get("interpretation_paragraphs"), list):
                    interpretation_parts = [
                        str(x).strip() for x in llm_rc["interpretation_paragraphs"] if str(x).strip()
                    ]
        except Exception as e:
            logger.warning("L3 sleep narrative Bedrock failed: %s", e)

    report_data["sleep_observations"] = observations
    report_data["sleep_interpretation"] = "\n\n".join(interpretation_parts)
    report_data["sleep_brief_summary"] = "\n\n".join(observations[:8]) if observations else ""


def _analysis_dict_from_snapshot_blob(blob: Any) -> Optional[Dict[str, Any]]:
    """Parse structured sleep JSON from one observation_store.extracted_observations blob."""
    if not blob:
        return None
    try:
        data = json.loads(blob) if isinstance(blob, str) else blob
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("sleep_metrics") or data.get("clinical_insights") or data.get("diagnosis"):
        return data
    expl = data.get("explanation")
    if isinstance(expl, str) and expl.strip().startswith("{"):
        try:
            inner = json.loads(expl)
        except json.JSONDecodeError:
            return None
        return inner if isinstance(inner, dict) else None
    return None


def fetch_sleep_pipeline_snapshot_row_for_source_file(
    patient_id: int,
    *,
    file_name: Optional[str] = None,
    s3_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Snapshot row for a specific sleep upload (matches observation_store.file_name / s3_key).

    Used after direct sleep so L3 reflects the file that was just processed, not an older
    study with a more recent *clinical* observed_at.
    """
    if not file_name and not s3_key:
        return None
    from sqlalchemy import text

    from flask_app import db

    conditions = [
        "patient_id = :pid",
        "metric_key = 'sleep_study.pipeline_snapshot_v1'",
    ]
    params: Dict[str, Any] = {"pid": patient_id}
    if file_name:
        conditions.append("file_name = :fn")
        params["fn"] = file_name
    if s3_key:
        conditions.append("s3_key = :sk")
        params["sk"] = s3_key

    try:
        row = db.session.execute(
            text(
                f"""
                SELECT extracted_observations, file_name, observed_at, s3_key, episode_id
                FROM observation_store
                WHERE {' AND '.join(conditions)}
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ),
            params,
        ).fetchone()
    except Exception as e:
        logger.warning("fetch_sleep_pipeline_snapshot_row_for_source_file: %s", e)
        return None

    if not row:
        return None

    raw_obs = row[0]
    analysis = _analysis_dict_from_snapshot_blob(raw_obs)
    if not analysis:
        return None

    return {
        "analysis": analysis,
        "file_name": row[1],
        "s3_key": row[3],
        "episode_id": row[4],
        "observed_at": row[2],
    }


def fetch_latest_sleep_pipeline_snapshot_row(patient_id: int) -> Optional[Dict[str, Any]]:
    """
    Newest pipeline snapshot row for the patient plus provenance for deduping auto L3.

    Rows are ordered by **ingestion time** (``created_at``), not clinical ``observed_at``,
    so a newly analyzed 2024 study is not shadowed by an older row whose study_date is 2026.

    Returns dict: analysis, episode_id, s3_key, file_name (analysis may be None if blob malformed).
    """
    from sqlalchemy import text

    from flask_app import db

    try:
        row = db.session.execute(
            text(
                """
                SELECT extracted_observations, file_name, observed_at, s3_key, episode_id
                FROM observation_store
                WHERE patient_id = :pid
                  AND metric_key = 'sleep_study.pipeline_snapshot_v1'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"pid": patient_id},
        ).fetchone()
    except Exception as e:
        logger.warning("fetch_latest_sleep_pipeline_snapshot_row: %s", e)
        return None

    if not row:
        return None

    raw_obs = row[0]
    file_name = row[1]
    observed_at = row[2]
    s3_key = row[3]
    episode_id = row[4]

    analysis = _analysis_dict_from_snapshot_blob(raw_obs)
    if not analysis:
        return None

    return {
        "analysis": analysis,
        "file_name": file_name,
        "s3_key": s3_key,
        "episode_id": episode_id,
        "observed_at": observed_at,
    }


def _stable_sleep_study_slug(
    patient_id: int,
    episode_id: Optional[str],
    s3_key: Optional[str],
    file_name: Optional[str],
) -> str:
    """Short filesystem-safe token: one auto L3 file per distinct sleep study / upload."""
    eid = (episode_id or "").strip()
    if eid and len(eid) >= 8:
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", eid)[:40]
    raw = f"{patient_id}\0{s3_key or ''}\0{file_name or ''}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def fetch_latest_sleep_pipeline_analysis(patient_id: int) -> Optional[Dict[str, Any]]:
    """Return parsed OpenAI sleep JSON from the newest observation_store pipeline snapshot row."""
    snap = fetch_latest_sleep_pipeline_snapshot_row(patient_id)
    return snap["analysis"] if snap else None


def _latest_vizbriz_quiz_for_patient(patient):
    from flask_app.models import VizBrizQuiz

    q = (
        VizBrizQuiz.query.filter_by(user_id=patient.id)
        .order_by(VizBrizQuiz.created_at.desc())
        .first()
    )
    if not q and getattr(patient, "email", None):
        q = (
            VizBrizQuiz.query.filter_by(patient_email=patient.email)
            .order_by(VizBrizQuiz.created_at.desc())
            .first()
        )
    return q


def _apply_l1_quiz_to_report_data(
    patient,
    patient_id: int,
    patient_name: str,
    report_data: Dict[str, Any],
) -> None:
    """
    Fill clinical_background / complaints / goals / demographics from the latest VizBriz quiz.

    Uses the same sources as L2 OSA Data Assessment automation:
    - Demographics: ``extract_level1_demographics_from_vizbriz_quiz`` (raw_answers + enhanced qa_lookup).
    - Narrative backfill: ``_fallback_l2_narrative`` when L1 template fields are still placeholders.
    """
    try:
        from flask_app.helpers.level1_report_hebrew import (
            build_level1_context_from_vizbriz_quiz,
            extract_level1_demographics_from_vizbriz_quiz,
        )
        from flask_app.helpers.vizbriz_quiz_helpers import _fallback_l2_narrative
    except Exception as e:
        logger.warning("L1 / L2 narrative import failed: %s", e)
        report_data["clinical_background"] = (
            f"Patient {patient_name} (ID {patient_id}). Level 1 questionnaire context unavailable ({e}). "
            "Sleep study section reflects AI extraction from the uploaded sleep test."
        )
        return

    quiz = _latest_vizbriz_quiz_for_patient(patient)
    if not quiz:
        report_data["clinical_background"] = (
            f"Patient {patient_name} (ID {patient_id}). No VizBriz Level 1 questionnaire found for this patient. "
            "Patient overview fields below are limited to sleep-test AI extraction and canonical demographics where present."
        )
        return

    # Demographics: align with L2 / manifest merge (handles DEMO_* in enhanced_answers).
    demo = extract_level1_demographics_from_vizbriz_quiz(quiz)
    if demo:
        if demo.get("sex"):
            report_data["gender"] = str(demo["sex"]).strip()
        if demo.get("age_years") is not None and str(demo.get("age_years")).strip():
            report_data["age"] = str(demo["age_years"]).strip()
        if demo.get("bmi") is not None and str(demo.get("bmi")).strip():
            report_data["bmi"] = str(demo["bmi"]).strip()
        if demo.get("height_cm") is not None:
            report_data["height"] = str(demo["height_cm"]).strip()
        if demo.get("weight_kg") is not None:
            report_data["weight"] = str(demo["weight_kg"]).strip()

    try:
        payload = json.loads(quiz.quiz_input or "{}")
    except Exception:
        payload = {}
    raw = payload.get("raw_answers") or {}
    enhanced = payload.get("enhanced_answers") or {}
    summary = dict(payload.get("evaluation_summary") or {})
    if quiz.total_score is not None and summary.get("total_score") is None:
        summary["total_score"] = quiz.total_score
    if quiz.risk_band and not summary.get("risk_band"):
        summary["risk_band"] = quiz.risk_band
    if quiz.red_flags is not None and summary.get("red_flags") is None:
        summary["red_flags"] = quiz.red_flags

    l1: Dict[str, Any] = {}
    try:
        l1 = build_level1_context_from_vizbriz_quiz(quiz) or {}
    except Exception as e:
        logger.warning("build_level1_context_from_vizbriz_quiz failed: %s", e)

    fb = _fallback_l2_narrative(raw, enhanced, summary, level1_context=l1)

    qdate = quiz.created_at.strftime("%Y-%m-%d") if getattr(quiz, "created_at", None) else "unknown date"
    risk = (l1.get("risk_level") or "").strip()
    alert = (l1.get("alert_text") or "").strip()
    stxt = (l1.get("symptoms_text") or "").strip()
    if stxt in _L1_SYMPTOM_PLACEHOLDERS:
        stxt = ""

    intro_lines = [
        f"VizBriz Level 1 screening questionnaire (submitted {qdate}, quiz id {getattr(quiz, 'id', '—')}).",
    ]
    if risk and risk != "—":
        intro_lines.append(f"Screening risk band: {risk}.")
    if alert:
        intro_lines.append(alert)
    if stxt:
        intro_lines.append(stxt)
    cb_fb = (fb.get("clinical_background") or "").strip()
    if cb_fb:
        intro_lines.append(cb_fb)

    report_data["clinical_background"] = "\n\n".join(intro_lines).strip()

    symptoms_was_placeholder = (l1.get("symptoms_text") or "").strip() in _L1_SYMPTOM_PLACEHOLDERS
    complaints = stxt if stxt else (alert if alert else "")
    if symptoms_was_placeholder or not (complaints or "").strip():
        pp = (fb.get("patient_presentation") or "").strip()
        if pp:
            complaints = pp
    report_data["complaints"] = (
        complaints if complaints else "Not reported based on Level 1 questionnaire."
    )

    rec_html = l1.get("recommendations_list") or ""
    goals_plain = _strip_html(rec_html)
    if not goals_plain:
        goals_plain = (
            _strip_html(l1.get("rec_1"))
            + (" " + _strip_html(l1.get("rec_2")) if l1.get("rec_2") else "")
        ).strip()
    if (
        not goals_plain
        or goals_plain.lower() == "not reported based on level 1 questionnaire."
    ):
        gf = (fb.get("patient_goals") or "").strip()
        if gf:
            goals_plain = gf
    report_data["goals"] = goals_plain[:4000] if goals_plain else "Not reported based on Level 1 questionnaire."


def build_level3_report_data_from_sleep_only(
    analysis: Dict[str, Any],
    canonical_json: Optional[Dict[str, Any]] = None,
    patient_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Sleep-study-only report payload (merged at PDF time with latest L2 OSA assessment)."""
    demo = (canonical_json or {}).get("demographics") or {}
    patient_block = (canonical_json or {}).get("patient") or {}
    study_demo = analysis.get("patient_demographics") or {}

    def _demo_val(*keys):
        for src in (study_demo, patient_block, demo):
            for k in keys:
                v = src.get(k) if isinstance(src, dict) else None
                if v is not None and str(v).strip():
                    return str(v).strip()
        return ""

    sm = analysis.get("sleep_metrics") or {}
    ox = analysis.get("oxygen_metrics") or {}
    arch = analysis.get("sleep_architecture") or {}
    shr = analysis.get("snoring_and_hr") or {}

    tst = _fmt_tst_minutes(arch.get("total_sleep_time_min"))
    if not tst and arch.get("total_sleep_time_min") is not None:
        tst = str(arch.get("total_sleep_time_min"))

    report_data: Dict[str, Any] = {
        "report_purpose": (
            "This section contains AI analysis of the uploaded sleep test only. "
            "Patient screening, symptoms, and OSA data assessment are in the preceding "
            "Level 2 OSA Data Assessment report (merged into this document)."
        ),
        "sleep_study_intro": _default_study_header(analysis),
        "sleep_key_metrics_table": [],
        "sleep_observations": [],
        "sleep_interpretation": "",
        "sleep_brief_summary": "",
        "gender": _demo_val("sex", "gender") or "See Level 2 report",
        "age": _demo_val("age", "age_years") or "See Level 2 report",
        "bmi": _demo_val("bmi") or "See Level 2 report",
        "height": _demo_val("height_cm") or "",
        "weight": _demo_val("weight_kg") or "",
        "ahi": _evt_hr(sm.get("ahi")),
        "rdi": _evt_hr(sm.get("rdi")),
        "rem_ahi": _evt_hr(sm.get("rem_ahi")),
        "rem_rdi": "Not provided",
        "supine_ahi": _evt_hr(sm.get("supine_ahi")),
        "non_supine_ahi": "Not provided",
        "odi": _evt_hr(sm.get("odi")),
        "rem_odi": "Not provided",
        "snoring_pct": _as_pct_str(shr.get("snore_pct_of_sleep")) or "Not provided",
        "o2_nadir": _as_pct_str(ox.get("o2_nadir_pct")) or "Not provided",
        "mean_spo2": _as_pct_str(ox.get("mean_spo2_pct")) or "Not provided",
        "time_below_90": _as_pct_str(ox.get("time_below_90_pct")) or "Not provided",
        "sleep_efficiency": _as_pct_str(arch.get("sleep_efficiency_pct")) or "Not provided",
        "total_sleep_time": tst or "Not provided",
        "clinical_background": "",
        "complaints": "",
        "goals": "",
        "obstruction_sites": "Not provided",
        "soft_palate": "Not provided",
        "tongue_position": "Not provided",
        "bite_jaw": "Not provided",
        "hyoid_bone": "Not provided",
        "nasal_sinus": "Not provided",
        "include_cbct_section": False,
        "craniofacial_dental_context": "",
        "cbct_measurements": [],
        "integrated_therapeutic_pathway": "",
        "images": [],
    }

    if patient_id:
        _apply_sleep_report_content(
            report_data,
            analysis,
            patient_id,
            questionnaire_context=None,
            sleep_study_only=True,
        )
    return report_data


def build_level3_report_data_from_sleep_l1_and_canonical(
    patient,
    patient_id: int,
    analysis: Dict[str, Any],
    canonical_json: Dict[str, Any],
    patient_name: str,
) -> Dict[str, Any]:
    """Backward-compatible alias: auto L3 uses sleep-only data (L2 merged as PDF)."""
    return build_level3_report_data_from_sleep_only(
        analysis,
        canonical_json=canonical_json,
        patient_id=patient_id,
    )


def fetch_latest_l2_osa_assessment_admin_file(patient_id: int):
    """Most recent L2 OSA Data Assessment AdminFile for this patient."""
    from flask_app.models import AdminFile

    return (
        AdminFile.query.filter_by(patient_id=patient_id)
        .filter(AdminFile.file_category == L2_OSA_ASSESSMENT_CATEGORY)
        .order_by(AdminFile.upload_date.desc(), AdminFile.id.desc())
        .first()
    )


def download_adminfile_pdf_bytes(admin_file) -> Optional[bytes]:
    """Load PDF bytes from S3 for an AdminFile row."""
    if not admin_file or not getattr(admin_file, "s3_key", None):
        return None
    try:
        from flask_app.s3_utils import get_s3_client

        bucket = os.getenv("S3_BUCKET_NAME")
        if not bucket:
            return None
        client = get_s3_client()
        resp = client.get_object(Bucket=bucket, Key=admin_file.s3_key)
        return resp["Body"].read()
    except Exception as e:
        logger.warning("download_adminfile_pdf_bytes failed for %s: %s", getattr(admin_file, "s3_key", None), e)
        return None


def merge_pdf_documents_in_order(parts: List[bytes]) -> bytes:
    """Concatenate PDF byte strings in order (e.g. L2 then sleep-study L3)."""
    import io

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for blob in parts:
        if not blob:
            continue
        reader = PdfReader(io.BytesIO(blob))
        for page in reader.pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def generate_and_store_level3_integrated_after_sleep(
    patient_id: int,
    *,
    snapshot_file_name: Optional[str] = None,
    snapshot_s3_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build Level 3 PDF: latest L2 OSA assessment + sleep-study-only addendum; store as AdminFile.

    Sleep narrative comes only from the pipeline snapshot. Questionnaire content is not
    regenerated — it is taken from the most recent ``Level 2 - OSA Data Assessment`` PDF.

    Controlled by env LEVEL3_AUTO_AFTER_SLEEP (default: 1). Set to 0 to disable.
    """
    if (os.getenv("LEVEL3_AUTO_AFTER_SLEEP") or "1").strip().lower() in ("0", "false", "no", "off"):
        return {"skipped": True, "reason": "LEVEL3_AUTO_AFTER_SLEEP disabled"}

    from flask_app import db
    from flask_app.models import AdminFile, Patient

    from flask_app.routes.level3_report_routes import (
        _load_canonical_data,
        _upload_to_s3,
        generate_level3_pdf,
    )

    snap = fetch_sleep_pipeline_snapshot_row_for_source_file(
        patient_id,
        file_name=(snapshot_file_name or "").strip() or None,
        s3_key=(snapshot_s3_key or "").strip() or None,
    )
    if not snap:
        snap = fetch_latest_sleep_pipeline_snapshot_row(patient_id)
    if not snap or not snap.get("analysis"):
        return {"skipped": True, "reason": "no_pipeline_snapshot"}

    analysis = snap["analysis"]
    study_slug = _stable_sleep_study_slug(
        patient_id,
        snap.get("episode_id"),
        snap.get("s3_key"),
        snap.get("file_name"),
    )
    filename = f"Level_3_L1_SleepAI_{patient_id}_{study_slug}.pdf"

    patient = Patient.query.get(patient_id)
    if not patient:
        return {"skipped": True, "reason": "patient_not_found"}

    existing = (
        AdminFile.query.filter_by(patient_id=patient_id, name=filename)
        .filter(AdminFile.file_category == L3_L1_SLEEP_AI_CATEGORY)
        .first()
    )
    if existing:
        logger.info(
            "Auto Level 3 skipped (already exists for this sleep study) patient=%s file_id=%s name=%s",
            patient_id,
            existing.id,
            filename,
        )
        return {
            "skipped": True,
            "reason": "l3_already_exists_for_this_sleep_study",
            "admin_file_id": existing.id,
            "filename": filename,
        }

    canonical_json = _load_canonical_data(patient_id)
    report_data = build_level3_report_data_from_sleep_only(
        analysis,
        canonical_json=canonical_json,
        patient_id=patient_id,
    )

    sleep_pdf = generate_level3_pdf(
        report_data,
        patient_id,
        patient.name or "Patient",
        sleep_study_only=True,
    )

    merge_parts: List[bytes] = []
    l2_admin = fetch_latest_l2_osa_assessment_admin_file(patient_id)
    l2_merged = False
    if l2_admin:
        l2_bytes = download_adminfile_pdf_bytes(l2_admin)
        if l2_bytes:
            merge_parts.append(l2_bytes)
            l2_merged = True
        else:
            logger.warning(
                "Auto Level 3: could not download L2 PDF patient=%s admin_file_id=%s",
                patient_id,
                getattr(l2_admin, "id", None),
            )
    else:
        logger.warning(
            "Auto Level 3: no L2 OSA assessment found; storing sleep addendum only patient=%s",
            patient_id,
        )

    merge_parts.append(sleep_pdf)
    pdf_bytes = merge_pdf_documents_in_order(merge_parts) if len(merge_parts) > 1 else sleep_pdf

    s3_key = f"patients/{patient_id}/reports/{filename}"
    _upload_to_s3(pdf_bytes, s3_key)

    admin_file = AdminFile(
        patient_id=patient_id,
        name=filename,
        s3_key=s3_key,
        file_type="application/pdf",
        file_size=len(pdf_bytes),
        upload_date=datetime.utcnow(),
        file_category=L3_L1_SLEEP_AI_CATEGORY,
        is_public=False,
        analyzed=False,
    )

    db.session.add(admin_file)
    db.session.commit()

    logger.info(
        "Auto Level 3 (L2 + sleep AI) stored patient=%s file=%s id=%s l2_merged=%s",
        patient_id,
        s3_key,
        getattr(admin_file, "id", None),
        l2_merged,
    )
    return {
        "success": True,
        "admin_file_id": getattr(admin_file, "id", None),
        "s3_key": s3_key,
        "filename": filename,
        "l2_merged": l2_merged,
        "l2_admin_file_id": getattr(l2_admin, "id", None) if l2_admin else None,
    }
