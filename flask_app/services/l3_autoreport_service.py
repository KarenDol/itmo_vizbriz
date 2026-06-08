"""
L3 autoreport: clinical observation builder, OpenAI conclusion, PDF merge (L2 + L3 + new section).
"""

from __future__ import annotations

import io
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask_app.config.l3_autoreport_observations import (
    CANONICAL_TO_AUTOREPORT,
    L3_AUTOREPORT_OBSERVATIONS,
)
from flask_app.extensions import db
from flask_app.models import AdminFile, File, Patient

logger = logging.getLogger(__name__)

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    from PyPDF2 import PdfReader, PdfWriter  # type: ignore


def _get_s3_client():
    import boto3

    return boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION", "us-west-2"),
    )


def _presigned_url(s3_key: str, expires_in: int = 3600) -> Optional[str]:
    if not s3_key:
        return None
    try:
        bucket = os.getenv("S3_BUCKET_NAME")
        return _get_s3_client().generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": s3_key,
                "ResponseContentDisposition": "inline",
            },
            ExpiresIn=expires_in,
        )
    except Exception as e:
        logger.warning("presigned_url failed for %s: %s", s3_key, e)
        return None


def _download_s3_bytes(s3_key: str) -> Optional[bytes]:
    if not s3_key:
        return None
    try:
        bucket = os.getenv("S3_BUCKET_NAME")
        obj = _get_s3_client().get_object(Bucket=bucket, Key=s3_key)
        return obj["Body"].read()
    except Exception as e:
        logger.warning("S3 download failed for %s: %s", s3_key, e)
        return None


def _admin_file_to_dict(row: AdminFile) -> Dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "s3_key": row.s3_key,
        "upload_date": row.upload_date.isoformat() if row.upload_date else None,
        "file_category": row.file_category,
        "view_url": _presigned_url(row.s3_key),
    }


def _is_pdf_admin_file(row: AdminFile) -> bool:
    ft = (row.file_type or "").lower()
    name = (row.name or "").lower()
    if ft and "pdf" in ft:
        return True
    return name.endswith(".pdf")


def _is_autoreport_file(row: AdminFile) -> bool:
    cat = (row.file_category or "").lower()
    name = (row.name or "").lower()
    sk = (row.s3_key or "").lower()
    return (
        "autoreport" in cat
        or "autoreport" in name
        or "l3_autoreport" in cat
        or "/temp/" in sk
    )


def _matches_l2_report(row: AdminFile) -> bool:
    """Explicit Level-2 OSA assessment PDFs only (avoid false positives)."""
    if _is_autoreport_file(row) or not _is_pdf_admin_file(row):
        return False
    cat = (row.file_category or "").lower()
    name = (row.name or "").lower()
    if "level 2" in cat or cat.startswith("level2"):
        return True
    if "osa_data_assessment_report_l2" in name or "_l2_" in name:
        return True
    if name.startswith("osa_data_assessment_report_l2"):
        return True
    return False


def _matches_l3_report(row: AdminFile) -> bool:
    """Level-3 integrated report only — never L2, never prior L3 autoreport merges."""
    if not _is_pdf_admin_file(row) or _is_autoreport_file(row):
        return False
    if _matches_l2_report(row):
        return False
    cat = (row.file_category or "").lower()
    name = (row.name or "").lower()
    sk = (row.s3_key or "").lower()
    if "autoreport" in name or "l3_autoreport" in cat:
        return False
    if "osa_data_assessment" in name and "_l2_" in name:
        return False
    if "level_3_report" in name or name.startswith("level_3"):
        return True
    if cat == "level3_report" or cat.endswith("level3_report"):
        return True
    if "integrated sleep" in name or "therapeutic pathway" in name:
        return True
    if ("level 3" in cat or cat.startswith("level3")) and "level 2" not in cat:
        if "screening" in cat and "questionnaire" in cat:
            return False
        return True
    # Temp preview from Level-3 editor only
    if "/reports/level3/temp/" in sk and "level_3" in name:
        return True
    return False


def get_latest_l2_report(patient_id: int) -> Optional[Dict[str, Any]]:
    rows = (
        AdminFile.query.filter_by(patient_id=patient_id)
        .order_by(AdminFile.upload_date.desc())
        .all()
    )
    for row in rows:
        if _matches_l2_report(row):
            logger.info("L3 autoreport: found L2 %s (%s)", row.name, row.file_category)
            return _admin_file_to_dict(row)
    return None


def get_latest_l3_report(patient_id: int) -> Optional[Dict[str, Any]]:
    rows = (
        AdminFile.query.filter_by(patient_id=patient_id)
        .order_by(AdminFile.upload_date.desc())
        .all()
    )
    for row in rows:
        if _matches_l3_report(row):
            logger.info("L3 autoreport: found L3 %s (%s)", row.name, row.file_category)
            return _admin_file_to_dict(row)
    return None


def get_clinical_pictures(patient_id: int) -> List[Dict[str, Any]]:
    files = (
        File.query.filter_by(patient_id=patient_id, category="imaging")
        .filter(
            File.subcategory.in_(["clinical-pictures", "clinical_pictures"])
        )
        .order_by(File.upload_date.desc())
        .all()
    )
    out = []
    for f in files:
        ext = (f.name or "").rsplit(".", 1)[-1].lower()
        if ext not in ("jpg", "jpeg", "png", "gif", "webp", "bmp"):
            continue
        out.append(
            {
                "id": f.id,
                "name": f.name,
                "view_url": _presigned_url(f.s3_key),
            }
        )
    return out


def prefill_observations_from_canonical(canonical_json: dict) -> Dict[str, str]:
    """Seed observation text areas from canonical anatomy_imaging when available."""
    prefill: Dict[str, str] = {o["key"]: "" for o in L3_AUTOREPORT_OBSERVATIONS}
    if not canonical_json:
        return prefill

    anatomy = canonical_json.get("anatomy_imaging") or {}
    if not anatomy and isinstance(canonical_json.get("observations"), dict):
        anatomy = canonical_json["observations"].get("anatomy_imaging") or {}

    for canon_key, auto_key in CANONICAL_TO_AUTOREPORT.items():
        val = anatomy.get(canon_key)
        if val is None:
            continue
        if isinstance(val, list):
            text = "; ".join(str(x).strip() for x in val if x)
        else:
            text = str(val).strip()
        if text and text.lower() not in ("none", "null", "n/a"):
            prefill[auto_key] = text
    return prefill


def generate_conclusion_openai(
    patient_id: int,
    observations: Dict[str, str],
    patient_context: Optional[dict] = None,
) -> Tuple[str, Optional[str]]:
    """
    Generate clinical conclusion paragraph via OpenAI.
    Returns (conclusion_text, error_message).
    """
    try:
        import openai
    except ImportError:
        return "", "openai package not installed"

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LEVEL4_OPENAI_API_KEY")
    if not api_key:
        return "", "OPENAI_API_KEY not configured"

    rows = []
    for obs in L3_AUTOREPORT_OBSERVATIONS:
        key = obs["key"]
        detail = (observations.get(key) or "").strip()
        if detail:
            rows.append({"observation": obs["label"], "details": detail})

    if not rows:
        return "", "Enter at least one observation before generating a conclusion."

    system = (
        "You are a clinical writing assistant for dental sleep medicine CBCT imaging reports. "
        "Write a single cohesive Conclusion paragraph (4–8 sentences) that synthesizes the "
        "provided imaging observations. Use professional clinical English. Do not invent "
        "findings not supported by the observations. Do not use bullet points or markdown. "
        "Mention anatomical contributors to pharyngeal airway narrowing where relevant. "
        "End with a neutral statement that findings support anatomy-guided therapeutic "
        "pathway discussion (not a treatment prescription)."
    )
    user_payload = {
        "patient_context": patient_context or {},
        "observations_table": rows,
    }
    user = (
        "Generate the Conclusion section for an L3 integrated sleep data report based on "
        "these CBCT-based observations:\n\n"
        f"{json.dumps(user_payload, indent=2, ensure_ascii=False)}"
    )

    try:
        openai.api_key = api_key
        completion = openai.chat.completions.create(
            model=os.getenv("L3_AUTOREPORT_OPENAI_MODEL", os.getenv("LEVEL4_OPENAI_MODEL", "gpt-4o")),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.25,
            max_tokens=900,
        )
        text = (completion.choices[0].message.content or "").strip()
        if not text:
            return "", "OpenAI returned an empty conclusion"
        return text, None
    except Exception as e:
        logger.error("L3 autoreport OpenAI conclusion failed: %s", e, exc_info=True)
        return "", str(e)


# PDF row labels aligned with Level 4 radiographic findings table layout
_PDF_OBSERVATION_LABELS = {
    "obstruction_sites": "Obstruction Sites",
    "narrowest_airway": "Narrowest Airway Point",
    "soft_palate": "Soft Palate & Uvula",
    "palatal_soft_tissue": "Palatal Soft Tissue (Hard Palate Region)",
    "tongue_position": "Tongue Position",
    "bite_jaw": "Bite & Jaw Structure",
    "dental_findings": "Dental Findings",
    "hyoid_bone": "Hyoid Bone",
    "epiglottis": "Epiglottis",
    "tmj": "Temporomandibular Joints (TMJ)",
    "nasal_sinus": "Nasal & Sinus Findings",
    "cervical_spine_alignment": "Cervical Spine Alignment",
}


def _append_pdf_bytes(writer: PdfWriter, pdf_bytes: Optional[bytes]) -> int:
    if not pdf_bytes:
        return 0
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        n = len(reader.pages)
        for page in reader.pages:
            writer.add_page(page)
        return n
    except Exception as e:
        logger.warning("Skipping PDF part in merge: %s", e)
        return 0


def _generate_observations_pdf(
    patient_id: int,
    patient_name: str,
    observations: Dict[str, str],
    conclusion: str,
) -> bytes:
    """Borderless Observation | Details layout + narrative Conclusion (no grid table)."""
    from flask_app.routes.level3_report_routes import _fix_text_for_pdf, _name_to_initials
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=0.85 * inch,
        bottomMargin=0.85 * inch,
        leftMargin=0.85 * inch,
        rightMargin=0.85 * inch,
    )
    styles = getSampleStyleSheet()
    header_style = ParagraphStyle(
        "ObsHeader",
        parent=styles["Normal"],
        fontSize=11,
        fontName="Helvetica-Bold",
        leading=14,
        textColor=colors.black,
    )
    label_style = ParagraphStyle(
        "ObsLabel",
        parent=styles["Normal"],
        fontSize=10,
        fontName="Helvetica-Bold",
        leading=13,
        textColor=colors.black,
    )
    detail_style = ParagraphStyle(
        "ObsDetail",
        parent=styles["Normal"],
        fontSize=10,
        fontName="Helvetica",
        leading=14,
        textColor=colors.black,
    )
    body_style = ParagraphStyle(
        "ObsBody",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        textColor=colors.black,
    )

    elements = []
    elements.append(
        Paragraph(
            f"<b>Patient:</b> {_name_to_initials(patient_name)} (ID: {patient_id}) &nbsp;&nbsp; "
            f"<b>Date:</b> {datetime.utcnow().strftime('%B %d, %Y')}",
            body_style,
        )
    )
    elements.append(Spacer(1, 16))

    rows = [
        [
            Paragraph("<b>Observation</b>", header_style),
            Paragraph("<b>Details</b>", header_style),
        ]
    ]
    for obs in L3_AUTOREPORT_OBSERVATIONS:
        detail = (observations.get(obs["key"]) or "").strip()
        if not detail:
            continue
        label = _PDF_OBSERVATION_LABELS.get(obs["key"], obs["label"])
        rows.append(
            [
                Paragraph(_fix_text_for_pdf(label), label_style),
                Paragraph(_fix_text_for_pdf(detail), detail_style),
            ]
        )

    if len(rows) == 1:
        rows.append(
            [
                Paragraph("—", label_style),
                Paragraph("No observations documented.", detail_style),
            ]
        )

    # Two-column layout without borders (matches sample report)
    layout = Table(rows, colWidths=[2.15 * inch, 4.35 * inch], hAlign="LEFT")
    layout.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (0, -1), 12),
                ("RIGHTPADDING", (1, 0), (1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    elements.append(layout)
    elements.append(Spacer(1, 20))

    conclusion_text = (conclusion or "").strip() or "Conclusion not provided."
    elements.append(Paragraph("<b>Conclusion:</b>", label_style))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(_fix_text_for_pdf(conclusion_text), body_style))

    doc.build(elements)
    return buffer.getvalue()


def merge_pdfs(pdf_parts: List[bytes]) -> bytes:
    """Append PDF pages in order (no divider / disclaimer pages)."""
    writer = PdfWriter()
    for part in pdf_parts:
        _append_pdf_bytes(writer, part)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def build_l3_autoreport_pdf(
    patient_id: int,
    observations: Dict[str, str],
    conclusion: str,
    *,
    include_l2: bool = True,
    include_l3: bool = True,
) -> Tuple[bytes, Dict[str, Any]]:
    """
    Merge latest L2 PDF + latest L3 PDF + new observations/conclusion PDF.
    Returns (pdf_bytes, metadata).
    """
    patient = Patient.query.get(patient_id)
    if not patient:
        raise ValueError("Patient not found")

    meta: Dict[str, Any] = {
        "l2_included": False,
        "l3_included": False,
        "l2_name": None,
        "l3_name": None,
    }
    parts: List[bytes] = []
    seen_s3_keys: set = set()

    def _add_pdf_part(s3_key: Optional[str], label: str) -> bool:
        if not s3_key or s3_key in seen_s3_keys:
            if s3_key and s3_key in seen_s3_keys:
                logger.info("L3 autoreport: skip duplicate source %s (%s)", label, s3_key)
            return False
        data = _download_s3_bytes(s3_key)
        if not data:
            return False
        seen_s3_keys.add(s3_key)
        parts.append(data)
        logger.info("L3 autoreport: attached %s (%s)", label, s3_key)
        return True

    if include_l2:
        l2 = get_latest_l2_report(patient_id)
        if l2 and _add_pdf_part(l2.get("s3_key"), "L2"):
            meta["l2_included"] = True
            meta["l2_name"] = l2.get("name")

    if include_l3:
        l3 = get_latest_l3_report(patient_id)
        l3_key = l3.get("s3_key") if l3 else None
        if l3_key and l3_key not in seen_s3_keys and _add_pdf_part(l3_key, "L3"):
            meta["l3_included"] = True
            meta["l3_name"] = l3.get("name")

    parts.append(
        _generate_observations_pdf(
            patient_id, patient.name or "Patient", observations, conclusion
        )
    )

    merged = merge_pdfs(parts)
    logger.info(
        "L3 autoreport merge patient=%s: l2=%s l3=%s parts=%s",
        patient_id,
        meta["l2_included"],
        meta["l3_included"],
        len(parts),
    )
    return merged, meta


def store_l3_autoreport(
    patient_id: int,
    pdf_bytes: bytes,
    *,
    uploaded_by_id: Optional[int] = None,
) -> AdminFile:
    """Upload merged L3 autoreport to S3 and register in adminfiles."""
    from flask_app.routes.level3_report_routes import _upload_to_s3

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"L3_Autoreport_Patient_{patient_id}_{timestamp}.pdf"
    s3_key = f"patients/{patient_id}/reports/level3/{filename}"
    _upload_to_s3(pdf_bytes, s3_key)

    admin_file = AdminFile(
        patient_id=patient_id,
        name=filename,
        s3_key=s3_key,
        file_type="application/pdf",
        file_size=len(pdf_bytes),
        upload_date=datetime.utcnow(),
        file_category="level3_autoreport",
    )
    db.session.add(admin_file)
    db.session.commit()
    return admin_file
