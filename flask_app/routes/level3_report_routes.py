"""
Level 3 Report Routes Blueprint

Clinician-driven OSA report where ALL content is entered manually by the clinician.
No AI analysis - data is pre-populated from canonical patient JSON but fully editable.
Features autocomplete for structural observations and image upload.
"""

import logging
import os
import io
import json
import boto3
from datetime import datetime
from flask import Blueprint, jsonify, request, render_template, current_app
from flask_login import login_required, current_user
from flask_app.extensions import db
from flask_app.models import Patient, PatientCaseEnvelope, AdminFile

# ReportLab imports for PDF generation
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, Image as RLImage
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import re

logger = logging.getLogger(__name__)

# Default text for "1. Report Purpose" when the user leaves the field blank (easy to change here).
DEFAULT_REPORT_PURPOSE = (
    "This report integrates objective sleep study data with CBCT-based airway analysis and "
    "CBCT-derived cephalometric findings to support anatomy-guided therapeutic pathway "
    "orientation within dental, orthodontic, and craniofacial care. This report is intended as a "
    "clinical decision-support tool and does not constitute a treatment plan or device prescription."
)

# =============================================================================
# HEBREW RTL TEXT HANDLING
# =============================================================================

_HAS_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
_HEBREW_RUN_RE = re.compile(r"[\u0590-\u05FF]+")


def _fix_hebrew_for_pdf(value: str) -> str:
    """
    ReportLab has limited RTL support and often renders Hebrew characters reversed.
    We pre-process strings so they visually render correctly in the generated PDF.

    Preference:
    - If python-bidi is installed, use it (best for mixed LTR/RTL).
    - Otherwise, reverse Hebrew character runs as a pragmatic fallback.
    """
    if not value:
        return value
    
    # Convert to string if not already
    if not isinstance(value, str):
        value = str(value)

    # Best-case: proper bidi algorithm if available (handles mixed Hebrew/English better)
    try:
        from bidi.algorithm import get_display  # type: ignore

        # base_dir='R' helps for predominantly RTL strings that include LTR tokens (e.g., CPAP/OAT, numbers)
        result = get_display(value, base_dir="R")
        logger.debug(f"Hebrew RTL fix applied using bidi: '{value[:30]}...' -> '{result[:30]}...'")
        return result
    except ImportError:
        logger.warning("python-bidi not installed, using fallback Hebrew reversal")
    except Exception as e:
        logger.warning(f"bidi.get_display failed: {e}, using fallback")

    # Fallback: reverse only Hebrew character runs; keep LTR tokens (numbers/CPAP/OAT) intact.
    if not _HAS_HEBREW_RE.search(value):
        return value

    def _rev(match: re.Match) -> str:
        return match.group(0)[::-1]

    return _HEBREW_RUN_RE.sub(_rev, value)


def _fix_text_for_pdf(value) -> str:
    """
    Prepare text for PDF - handles Hebrew RTL and ensures string conversion.
    """
    if value is None:
        return "Not provided"
    if not isinstance(value, str):
        value = str(value)
    if not value.strip():
        return "Not provided"
    # Apply Hebrew fix if text contains Hebrew characters
    if _HAS_HEBREW_RE.search(value):
        return _fix_hebrew_for_pdf(value)
    return value


def _name_to_initials(name: str) -> str:
    """
    Convert a full name to initials (e.g. "John Doe" -> "J.D.", "John Michael Doe" -> "J.M.D.").
    Used in the Level 3 report so only initials appear, not the full name.
    """
    if not name or not isinstance(name, str):
        return "—"
    parts = name.strip().split()
    if not parts:
        return "—"
    initials = ".".join(p[0].upper() for p in parts if p) + ("." if len(parts) > 1 else ".")
    return initials or "—"


def _sleep_key_metrics_table_rows(key_metrics: list) -> list:
    """Build 4-column Parameter/Value table rows from [{parameter, value}, ...]."""
    header = ["Parameter", "Value", "Parameter", "Value"]
    rows = [header]
    items = key_metrics or []
    if not items:
        return rows
    for i in range(0, len(items), 2):
        row = []
        for j in range(2):
            if i + j < len(items):
                item = items[i + j]
                if isinstance(item, dict):
                    row.append(_fix_text_for_pdf(item.get("parameter")))
                    row.append(_fix_text_for_pdf(item.get("value")))
                else:
                    row.extend(["", ""])
            else:
                row.extend(["", ""])
        rows.append(row)
    return rows


def _observations_to_pdf_elements(observations, body_style) -> list:
    """Render observation bullets as separate • paragraphs."""
    if isinstance(observations, str):
        observations = [ln.strip() for ln in observations.splitlines() if ln.strip()]
    if not isinstance(observations, list) or not observations:
        return []
    elements = []
    for obs in observations:
        text = str(obs).strip()
        if not text:
            continue
        if text.startswith("•"):
            text = text[1:].strip()
        elements.append(Paragraph("• " + _fix_text_for_pdf(text), body_style))
    return elements


def _interpretation_to_pdf_elements(interpretation_text: str, body_style) -> list:
    """Render interpretation as one paragraph per block (split on blank lines)."""
    if not interpretation_text or not str(interpretation_text).strip():
        return []
    blocks = [b.strip() for b in re.split(r"\n\s*\n", str(interpretation_text)) if b.strip()]
    if not blocks:
        blocks = [str(interpretation_text).strip()]
    return [Paragraph(_fix_text_for_pdf(b), body_style) for b in blocks]


def _pathway_to_pdf_elements(html_content: str, body_style, bold_style) -> list:
    """
    Render Integrated Therapeutic Pathway as separate bullet paragraphs (no long block).
    If content has multiple lines (bullet lines or <ul><li>), each becomes one • paragraph; otherwise use _html_to_pdf_elements.
    """
    if not html_content or not html_content.strip():
        return [Paragraph("Not provided", body_style)]
    # Preserve <li> as lines: replace </li><li> with newline, then strip tags
    plain = re.sub(r"</li>\s*<li[^>]*>", "\n", html_content, flags=re.I)
    plain = re.sub(r"<br\s*/?>", "\n", plain, flags=re.I)
    plain = re.sub(r"</p>\s*<p[^>]*>", "\n", plain, flags=re.I)
    plain = re.sub(r"<[^>]+>", " ", plain)
    plain = plain.replace("&bull;", "•").replace("&#8226;", "•")
    lines = [ln.strip() for ln in plain.splitlines() if ln.strip()]
    if len(lines) >= 2:
        elements = []
        for ln in lines:
            if ln.startswith("•"):
                ln = ln[1:].strip()
            elements.append(Paragraph("• " + _fix_text_for_pdf(ln), body_style))
        return elements
    return _html_to_pdf_elements(html_content, body_style, bold_style)


def _html_to_pdf_elements(html_content: str, body_style, bold_style) -> list:
    """
    Convert HTML from rich text editor to ReportLab elements.
    Handles <b>, <i>, <ul>, <ol>, <li>, <p>, <br> tags.
    """
    from html.parser import HTMLParser
    
    if not html_content or not html_content.strip():
        return [Paragraph("Not provided", body_style)]
    
    # Clean HTML and convert to plain text with formatting markers
    class RichTextParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.elements = []
            self.current_text = ""
            self.in_list = False
            self.list_type = None  # 'ul' or 'ol'
            self.list_items = []
            self.list_counter = 0
            self.tag_stack = []
        
        def handle_starttag(self, tag, attrs):
            self.tag_stack.append(tag)
            if tag in ('ul', 'ol'):
                # Save any pending text as paragraph
                if self.current_text.strip():
                    self.elements.append(('p', self.current_text.strip()))
                    self.current_text = ""
                self.in_list = True
                self.list_type = tag
                self.list_counter = 0
                self.list_items = []
            elif tag == 'li':
                self.current_text = ""
            elif tag == 'br':
                self.current_text += "\n"
            elif tag == 'p':
                if self.current_text.strip():
                    self.elements.append(('p', self.current_text.strip()))
                    self.current_text = ""
            elif tag == 'b' or tag == 'strong':
                self.current_text += "<b>"
            elif tag == 'i' or tag == 'em':
                self.current_text += "<i>"
        
        def handle_endtag(self, tag):
            if self.tag_stack and self.tag_stack[-1] == tag:
                self.tag_stack.pop()
            
            if tag in ('ul', 'ol'):
                self.in_list = False
                if self.list_items:
                    self.elements.append((tag, self.list_items.copy()))
                self.list_items = []
                self.list_type = None
            elif tag == 'li':
                text = self.current_text.strip()
                if text:
                    self.list_items.append(text)
                self.current_text = ""
            elif tag == 'p':
                if self.current_text.strip():
                    self.elements.append(('p', self.current_text.strip()))
                self.current_text = ""
            elif tag == 'b' or tag == 'strong':
                self.current_text += "</b>"
            elif tag == 'i' or tag == 'em':
                self.current_text += "</i>"
        
        def handle_data(self, data):
            self.current_text += data
        
        def get_elements(self):
            # Handle any remaining text
            if self.current_text.strip():
                self.elements.append(('p', self.current_text.strip()))
            return self.elements
    
    parser = RichTextParser()
    try:
        parser.feed(html_content)
    except Exception as e:
        logger.warning(f"HTML parsing error: {e}")
        # Fall back to plain text
        text = re.sub(r'<[^>]+>', '', html_content)
        return [Paragraph(_fix_text_for_pdf(text), body_style)]
    
    parsed = parser.get_elements()
    
    # Convert to ReportLab elements
    pdf_elements = []
    for elem_type, content in parsed:
        if elem_type == 'p':
            # Apply Hebrew fix to the content
            fixed = _fix_text_for_pdf(content)
            pdf_elements.append(Paragraph(fixed, body_style))
        elif elem_type == 'ul':
            # Unordered list - bullet points
            for item in content:
                fixed = _fix_text_for_pdf(item)
                pdf_elements.append(Paragraph(f"• {fixed}", body_style))
        elif elem_type == 'ol':
            # Ordered list - numbered
            for i, item in enumerate(content, 1):
                fixed = _fix_text_for_pdf(item)
                pdf_elements.append(Paragraph(f"{i}. {fixed}", body_style))
    
    if not pdf_elements:
        pdf_elements.append(Paragraph("Not provided", body_style))
    
    return pdf_elements

# Create blueprint with URL prefix
level3_report_bp = Blueprint('level3_report', __name__, url_prefix='/reports')

# =============================================================================
# AUTOCOMPLETE DATA FOR STRUCTURAL OBSERVATIONS
# =============================================================================

AUTOCOMPLETE_DATA = {
    "obstruction_sites": [
        "Primary restriction at the tongue base",
        "Primary restriction at the velopharynx",
        "Primary restriction at the oropharynx",
        "Velopharyngeal obstruction",
        "Oropharyngeal narrowing",
        "Hypopharyngeal collapse",
        "Multi-level obstruction (velopharynx, oropharynx, tongue base)",
        "Retropalatal obstruction",
        "Retroglossal obstruction",
        "Lateral pharyngeal wall collapse",
        "Epiglottic collapse",
        "No significant obstruction identified"
    ],
    "soft_palate": [
        "Elongated and swollen, partially obstructing the oropharynx",
        "Elongated soft palate with redundant tissue",
        "Elongated uvula",
        "Webbed soft palate",
        "Thick soft palate",
        "Low-hanging soft palate",
        "Swollen uvula",
        "Normal soft palate and uvula",
        "Mildly elongated soft palate",
        "Moderately elongated soft palate with uvular edema"
    ],
    "tongue_position": [
        "Posteriorly positioned tongue base encroaching the airway",
        "Large tongue (macroglossia)",
        "Tongue base hypertrophy",
        "Large, posteriorly positioned tongue base encroaching the airway",
        "Tongue crenations present (scalloped tongue)",
        "Moderate tongue base prolapse",
        "Significant tongue base prolapse",
        "Normal tongue position",
        "Mild posterior tongue positioning"
    ],
    "bite_jaw": [
        "Reduced overjet and overbite, with a retruded mandible",
        "Class II malocclusion",
        "Class III malocclusion", 
        "Retrognathic mandible",
        "Micrognathia",
        "Increased overjet",
        "Deep bite",
        "Open bite",
        "Crossbite",
        "Normal bite and jaw relationship",
        "Mild mandibular retrusion",
        "Lingually tipped posterior teeth"
    ],
    "hyoid_bone": [
        "Positioned significantly inferior to the mandibular plane",
        "Low hyoid position",
        "Inferiorly positioned hyoid",
        "Hyoid bone positioned at the same level as the mandibular plane",
        "Normal hyoid position",
        "Anteriorly positioned hyoid"
    ],
    "nasal_sinus": [
        "Mucosal thickening in the maxillary sinuses and hypertrophic turbinates",
        "Deviated nasal septum",
        "Inferior turbinate hypertrophy",
        "Nasal polyps",
        "Polypoid mucosal thickening in the maxillary sinuses bilaterally",
        "Chronic sinusitis changes",
        "Concha bullosa",
        "Nasal valve collapse",
        "Normal nasal passages",
        "Mild mucosal thickening",
        "Bilateral inferior turbinate hypertrophy"
    ]
}

# =============================================================================
# HEBREW TRANSLATION HELPERS (same as Level 4)
# =============================================================================

import re

def _contains_hebrew(text: str) -> bool:
    """Return True if text contains any Hebrew Unicode characters."""
    if not text or not isinstance(text, str):
        return False
    # Hebrew block: U+0590–U+05FF
    return bool(re.search(r'[\u0590-\u05FF]', text))


def _collect_hebrew_strings(obj, base_path: str = ""):
    """
    Collect (path, value) pairs for string values containing Hebrew characters.
    Only traverses dict/list structures.
    """
    items = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{base_path}.{k}" if base_path else str(k)
            items.extend(_collect_hebrew_strings(v, path))
        return items
    if isinstance(obj, list):
        for idx, v in enumerate(obj):
            path = f"{base_path}[{idx}]"
            items.extend(_collect_hebrew_strings(v, path))
        return items
    if isinstance(obj, str) and _contains_hebrew(obj):
        items.append((base_path, obj))
    return items


def _set_value_by_path(root, path: str, value: str) -> bool:
    """
    Set a value into a nested dict/list using a simple path format.
    Returns True if the path was resolved and set.
    """
    if not path:
        return False

    tokens = []
    buf = ""
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == '.':
            if buf:
                tokens.append(buf)
                buf = ""
            i += 1
            continue
        if ch == '[':
            if buf:
                tokens.append(buf)
                buf = ""
            j = path.find(']', i)
            if j == -1:
                return False
            idx_str = path[i + 1:j]
            try:
                tokens.append(int(idx_str))
            except Exception:
                return False
            i = j + 1
            continue
        buf += ch
        i += 1
    if buf:
        tokens.append(buf)

    ref = root
    for t in tokens[:-1]:
        if isinstance(t, int):
            if not isinstance(ref, list) or t < 0 or t >= len(ref):
                return False
            ref = ref[t]
        else:
            if not isinstance(ref, dict) or t not in ref:
                return False
            ref = ref[t]

    last = tokens[-1]
    if isinstance(last, int):
        if not isinstance(ref, list) or last < 0 or last >= len(ref):
            return False
        ref[last] = value
        return True
    if not isinstance(ref, dict):
        return False
    ref[last] = value
    return True


def _parse_json_from_llm(text: str):
    """Best-effort JSON extraction from an LLM response."""
    if not text:
        return None
    # Try to find JSON object in the response
    text = text.strip()
    # Look for JSON object
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _load_canonical_data(patient_id: int) -> dict:
    """
    Load canonical JSON data for a patient.
    Uses the same cleaning as Level 4 for consistency.
    If no canonical exists, returns an empty structure with patient info.
    """
    envelope = PatientCaseEnvelope.query.filter_by(
        patient_id=patient_id, report_id="canonical"
    ).first()
    
    if not envelope or not envelope.case_json:
        # Return empty structure - clinician can still fill in manually
        logger.warning(f"Patient {patient_id}: No canonical JSON found, returning empty structure")
        return {
            'patient': {},
            'demographics': {},
            'sleep_study': {},
            'anatomy': {},
            'clinical_background': '',
            'complaints': [],
            'goals': []
        }
    
    if isinstance(envelope.case_json, str):
        canonical_json = json.loads(envelope.case_json)
    else:
        canonical_json = envelope.case_json
    
    # Clean canonical for display (same as Level 4)
    try:
        from flask_app.config.document_observation_extractor_phase2 import create_clean_canonical_for_llm
        canonical_json = create_clean_canonical_for_llm(canonical_json, patient_id)
        logger.info(f"Patient {patient_id}: Cleaned canonical for Level-3 report")
    except Exception as e:
        logger.warning(f"Failed to clean canonical for patient {patient_id}: {e}, using original")
    
    return canonical_json


def _get_s3_client():
    """Get S3 client."""
    return boto3.client(
        's3',
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        region_name=os.getenv('AWS_REGION', 'us-west-2')
    )


def _upload_to_s3(file_bytes: bytes, s3_key: str, content_type: str = 'application/pdf') -> str:
    """Upload file to S3 and return the s3_key (not URL)."""
    s3_client = _get_s3_client()
    bucket_name = os.getenv('S3_BUCKET_NAME')
    
    file_obj = io.BytesIO(file_bytes)
    file_obj.seek(0)
    
    s3_client.upload_fileobj(
        file_obj,
        bucket_name,
        s3_key,
        ExtraArgs={'ContentType': content_type}
    )
    
    return s3_key


def _generate_presigned_url(s3_key: str, expires_in: int = 3600) -> str:
    """Generate a presigned URL for accessing an S3 object."""
    if not s3_key:
        return None
    
    s3_client = _get_s3_client()
    bucket_name = os.getenv('S3_BUCKET_NAME')
    
    try:
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': bucket_name,
                'Key': s3_key,
                'ResponseContentDisposition': 'inline'
            },
            ExpiresIn=expires_in
        )
        return url
    except Exception as e:
        logger.error(f"Error generating presigned URL for {s3_key}: {e}")
        return None


# =============================================================================
# LEVEL 3 PATIENT OVERVIEW LLM GENERATION (same pattern as Level 4)
# =============================================================================

LEVEL3_OVERVIEW_SYSTEM_PROMPT = """You are a clinical assistant for a Level 3 report. Your role is to generate the Patient Overview section (Section 2): clinical background, reported symptoms (complaints), and patient goals.

Rules:
- Use ONLY the patient data provided. Do not invent findings.
- Output MUST be valid JSON with exactly these three keys: "clinical_background", "complaints", "goals".
- "clinical_background" MUST be no more than 3–4 sentences. Keep it concise. Other values: 1–3 short sentences each.
- If a section has no data, use a short placeholder like "Not documented" or "To be obtained from patient."
- No markdown, no LaTeX, no extra keys. Only the JSON object."""

LEVEL3_OVERVIEW_USER_PROMPT_TEMPLATE = """Generate the Patient Overview (clinical background, complaints, goals) for this patient.

Available patient data:
{context_json}

Return ONLY a JSON object with exactly these keys (each value a string):
- "clinical_background": Medical history, comorbidities, medications, relevant history. No more than 3–4 sentences.
- "complaints": Reported symptoms and chief complaints.
- "goals": Patient-stated goals (e.g. snoring reduction, sleep quality, TMJ, appliance tolerance).

Output nothing else except the JSON object."""


def _generate_level3_overview_content(patient_id: int, canonical_json: dict) -> dict:
    """
    Call LLM to generate Patient Overview (clinical_background, complaints, goals) from canonical data.
    Returns dict with keys clinical_background, complaints, goals (empty strings on failure).
    """
    context = {
        "patient": canonical_json.get("patient", {}),
        "demographics": canonical_json.get("demographics", {}),
        "clinical_background": canonical_json.get("clinical_background", ""),
        "complaints": canonical_json.get("complaints", []),
        "goals": canonical_json.get("goals", []),
        "sleep_study_summary": canonical_json.get("sleep_study", {}),
        "anatomy": canonical_json.get("anatomy", {}),
    }
    # Flatten lists for display
    if isinstance(context.get("complaints"), list):
        context["complaints"] = ", ".join(str(x) for x in context["complaints"]) if context["complaints"] else ""
    if isinstance(context.get("goals"), list):
        context["goals"] = ", ".join(str(x) for x in context["goals"]) if context["goals"] else ""
    context_str = json.dumps(context, indent=2, default=str)
    user_prompt = LEVEL3_OVERVIEW_USER_PROMPT_TEMPLATE.format(context_json=context_str)

    out = {"clinical_background": "", "complaints": "", "goals": ""}
    try:
        from flask_app.services.bedrock_service import get_bedrock_service
        service = get_bedrock_service()
        if not service or not service.is_available():
            logger.warning("Bedrock service unavailable for Level 3 overview")
            return out
        result = service.invoke_model(
            messages=[
                {"role": "user", "content": f"{LEVEL3_OVERVIEW_SYSTEM_PROMPT}\n\n---\n\n{user_prompt}"},
            ],
            max_tokens=1200,
            temperature=0.2,
            patient_id=patient_id,
            endpoint="level3_generate_overview",
        )
        if result.get("error"):
            logger.warning(f"Level 3 overview LLM error: {result['error']}")
            return out
        text = (result.get("response") or "").strip()
        parsed = _parse_json_from_llm(text)
        if isinstance(parsed, dict):
            out["clinical_background"] = str(parsed.get("clinical_background", "")).strip()
            out["complaints"] = str(parsed.get("complaints", "")).strip()
            out["goals"] = str(parsed.get("goals", "")).strip()
        return out
    except Exception as e:
        logger.error(f"Level 3 overview generation failed: {e}", exc_info=True)
        return out


# =============================================================================
# LEVEL 3 PATHWAY LLM GENERATION (same pattern as Level 4 micro-sections)
# =============================================================================

LEVEL3_PATHWAY_PROMPT_TEMPLATE = """You are a clinical assistant generating ONLY section "5. Integrated Therapeutic Pathway and Next Steps" for a Level 3 Integrated Sleep Data & Therapeutic Pathway report.

Rules:

Use ONLY the patient data provided in {context_json}. Do NOT invent or infer findings.

Output MUST be a bullet list with no more than 5 bullets.

Each bullet MUST:

Start with • (bullet + space)

Contain one short sentence only

Select the most clinically relevant items (primary therapy, secondary pathway if applicable, and clear next steps).

Do NOT include headers, disclaimers, explanations, or meta-commentary.

Do NOT use markdown, numbering, or paragraphs.

Example format:
• Primary recommendation based on AHI and anatomy.
• Additional pathway if relevant.
• Next clinical step or referral.
• Follow-up or monitoring step.
• Final actionable item if needed.

Patient data (entire current report page):
{context_json}

Output ONLY the bullet lines and nothing else."""


def _pathway_response_to_bullets(text: str, max_bullets: int = 5) -> str:
    """
    Enforce bullet format and max 5 bullets: extract lines that look like bullets, keep at most max_bullets.
    """
    if not text or not text.strip():
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    bullets = []
    for ln in lines:
        # Accept lines starting with • or - or numbered (1. 2. etc.)
        if ln.startswith("•") or ln.startswith("-") or re.match(r"^\d+[.)]\s", ln):
            clean = re.sub(r"^[•\-]\s*", "", ln)
            clean = re.sub(r"^\d+[.)]\s*", "", clean)
            if clean:
                bullets.append("• " + clean.strip())
        if len(bullets) >= max_bullets:
            break
    return "\n".join(bullets) if bullets else text.strip()


def _generate_level3_pathway_content(patient_id: int, report_data: dict, canonical_json: dict) -> str:
    """
    Call LLM to generate "5. Integrated Therapeutic Pathway and Next Steps" from patient data.
    Uses full current page data (report_data = form state, including user edits). Output limited to 5 bullets.
    """
    # Build context from current form (report_data) first; fall back to canonical only when form value is empty
    def _form_first(key, canon_path=None):
        val = report_data.get(key)
        if val is not None and str(val).strip():
            return val
        if canon_path is None:
            return canonical_json.get(key, "")
        obj = canonical_json
        for p in canon_path.split("."):
            obj = (obj or {}).get(p) or {}
        return obj

    def _complaints():
        c = report_data.get("complaints")
        if c is not None and str(c).strip():
            return c
        lst = canonical_json.get("complaints", [])
        return ", ".join(str(x) for x in lst) if isinstance(lst, list) and lst else ""

    def _goals():
        g = report_data.get("goals")
        if g is not None and str(g).strip():
            return g
        lst = canonical_json.get("goals", [])
        return ", ".join(str(x) for x in lst) if isinstance(lst, list) and lst else ""

    ss = canonical_json.get("sleep_study") or {}
    context = {
        "note": "All values below reflect the current report page (user-edited + canonical). Use all of it.",
        "patient": canonical_json.get("patient", {}),
        "demographics": canonical_json.get("demographics", {}),
        "report_purpose": _form_first("report_purpose"),
        "clinical_background": _form_first("clinical_background"),
        "complaints": _complaints(),
        "goals": _goals(),
        "sleep_study": {
            "intro": report_data.get("sleep_study_intro") or "",
            "ahi": report_data.get("ahi") or ss.get("ahi"),
            "rdi": report_data.get("rdi") or ss.get("rdi"),
            "rem_ahi": report_data.get("rem_ahi") or ss.get("rem_ahi"),
            "supine_ahi": report_data.get("supine_ahi") or ss.get("supine_ahi"),
            "o2_nadir": report_data.get("o2_nadir") or ss.get("o2_nadir"),
            "sleep_efficiency": report_data.get("sleep_efficiency") or ss.get("sleep_efficiency"),
            "total_sleep_time": report_data.get("total_sleep_time") or ss.get("total_sleep_time_min"),
            "brief_summary": report_data.get("sleep_brief_summary", ""),
        },
        "anatomy_observations": {
            "obstruction_sites": report_data.get("obstruction_sites", ""),
            "soft_palate": report_data.get("soft_palate", ""),
            "tongue_position": report_data.get("tongue_position", ""),
            "bite_jaw": report_data.get("bite_jaw", ""),
            "hyoid_bone": report_data.get("hyoid_bone", ""),
            "nasal_sinus": report_data.get("nasal_sinus", ""),
        },
        "craniofacial_context": report_data.get("craniofacial_dental_context", ""),
        "cbct_measurements": report_data.get("cbct_measurements", []),
    }
    context_str = json.dumps(context, indent=2, default=str)
    prompt = LEVEL3_PATHWAY_PROMPT_TEMPLATE.format(context_json=context_str)

    try:
        from flask_app.services.bedrock_service import get_bedrock_service
        service = get_bedrock_service()
        if not service or not service.is_available():
            logger.warning("Bedrock service unavailable for Level 3 pathway")
            return ""
        result = service.invoke_model(
            messages=[
                {"role": "user", "content": prompt},
            ],
            max_tokens=600,
            temperature=0.2,
            patient_id=patient_id,
            endpoint="level3_generate_pathway",
        )
        if result.get("error"):
            logger.warning(f"Level 3 pathway LLM error: {result['error']}")
            return ""
        text = (result.get("response") or "").strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Enforce max 5 bullets so report never gets one long block
        return _pathway_response_to_bullets(text, max_bullets=5)
    except Exception as e:
        logger.error(f"Level 3 pathway generation failed: {e}", exc_info=True)
        return ""


# =============================================================================
# PDF GENERATION
# =============================================================================

def generate_level3_pdf(
    report_data: dict,
    patient_id: int,
    patient_name: str,
    *,
    sleep_study_only: bool = False,
) -> bytes:
    """
    Generate Level 3 PDF report from the form data.

    When ``sleep_study_only`` is True, render only the sleep-study section (for merging
    with the latest Level 2 OSA Data Assessment PDF).
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=50,
        leftMargin=50,
        topMargin=50,
        bottomMargin=50
    )
    
    # Check if DejaVu fonts are already registered, if not register them
    # DejaVu Sans has full Unicode support including Hebrew
    try:
        # Check if already registered
        pdfmetrics.getFont('DejaVuSans')
        base_font_name = 'DejaVuSans'
        base_font_bold = 'DejaVuSans-Bold'
    except KeyError:
        # Not registered, try to register
        dejavu_registered = False
        dejavu_paths = [
            '/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/dejavu/DejaVuSans.ttf',
        ]
        dejavu_bold_paths = [
            '/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',
        ]
        
        for font_path in dejavu_paths:
            if os.path.exists(font_path):
                try:
                    pdfmetrics.registerFont(TTFont('DejaVuSans', font_path))
                    dejavu_registered = True
                    logger.info(f"Registered DejaVuSans font from: {font_path}")
                    break
                except Exception as e:
                    logger.warning(f"Could not register DejaVuSans: {e}")
        
        for font_path in dejavu_bold_paths:
            if os.path.exists(font_path):
                try:
                    pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', font_path))
                    logger.info(f"Registered DejaVuSans-Bold font from: {font_path}")
                    break
                except Exception as e:
                    logger.warning(f"Could not register DejaVuSans-Bold: {e}")
        
        # Use DejaVu if registered, otherwise fall back to Helvetica
        if dejavu_registered:
            base_font_name = 'DejaVuSans'
            # Check if bold was registered
            try:
                pdfmetrics.getFont('DejaVuSans-Bold')
                base_font_bold = 'DejaVuSans-Bold'
            except KeyError:
                base_font_bold = 'DejaVuSans'  # Use regular as fallback
        else:
            base_font_name = 'Helvetica'
            base_font_bold = 'Helvetica-Bold'
            logger.warning("DejaVu Sans not available, using Helvetica (Hebrew may not render)")
    
    # Styles - Blue color scheme (matching VizBriz logo)
    styles = getSampleStyleSheet()
    
    # Blue color palette
    BLUE_PRIMARY = '#1d4ed8'      # Primary blue
    BLUE_DARK = '#1e40af'         # Darker blue
    BLUE_LIGHT = '#eff6ff'        # Light blue background
    BLUE_ACCENT = '#2563eb'       # Accent blue
    
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        spaceAfter=12,
        textColor=colors.HexColor('#1a1a1a'),
        fontName=base_font_bold
    )
    
    section_style = ParagraphStyle(
        'SectionHeader',
        parent=styles['Heading2'],
        fontSize=12,
        spaceBefore=16,
        spaceAfter=8,
        textColor=colors.HexColor(BLUE_PRIMARY),
        fontName=base_font_bold,
        borderPadding=4,
        backColor=colors.HexColor(BLUE_LIGHT)
    )
    
    # Subsection style - smaller, no background, for nested headers
    subsection_style = ParagraphStyle(
        'SubsectionHeader',
        parent=styles['Heading3'],
        fontSize=11,
        spaceBefore=10,
        spaceAfter=6,
        textColor=colors.HexColor(BLUE_DARK),
        fontName=base_font_bold,
        leftIndent=10
    )
    
    body_style = ParagraphStyle(
        'BodyText',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=6,
        textColor=colors.HexColor('#1a1a1a'),
        fontName=base_font_name,
        leading=14
    )
    
    bold_style = ParagraphStyle(
        'BoldText',
        parent=body_style,
        fontName=base_font_bold
    )
    
    disclaimer_style = ParagraphStyle(
        'Disclaimer',
        parent=body_style,
        fontSize=9,
        textColor=colors.HexColor('#4b5563'),
        fontName=base_font_name,  # Use Hebrew-supporting font for disclaimer too
        leading=12
    )
    cell_style = ParagraphStyle(
        'CellText',
        parent=body_style,
        fontSize=9,
        leading=12
    )
    elements = []
    
    # Logo - centered at top of page, preserve aspect ratio
    logo_path = '/home/ec2-user/vizbriz/flask_app/flask_static/images/logos/vizbriz_logo_clean.png'
    if os.path.exists(logo_path):
        try:
            # Load logo and scale proportionally to preserve aspect ratio
            logo = RLImage(logo_path)
            max_width = 2.0 * inch
            if logo.drawWidth > max_width:
                scale = max_width / logo.drawWidth
                logo.drawWidth = max_width
                logo.drawHeight = logo.drawHeight * scale
            logo.hAlign = 'CENTER'
            elements.append(logo)
            elements.append(Spacer(1, 8))
        except Exception as e:
            logger.warning(f"Could not add logo: {e}")
    
    if sleep_study_only:
        elements.append(Paragraph("VizBriz – Sleep Study Analysis (Level 3 Addendum)", title_style))
    else:
        elements.append(Paragraph("VizBriz – Level 3 Integrated Sleep Data & Therapeutic Pathway Report", title_style))
    elements.append(Paragraph(f"Patient: {_name_to_initials(patient_name)} (ID: {patient_id})", body_style))
    elements.append(Paragraph(f"Date: {datetime.now().strftime('%B %d, %Y')}", body_style))
    elements.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor(BLUE_ACCENT), spaceAfter=12))

    if not sleep_study_only:
        elements.append(Spacer(1, 12))
        elements.append(Paragraph("1. Report Purpose", section_style))
        report_purpose = (report_data.get('report_purpose') or '').strip() or DEFAULT_REPORT_PURPOSE
        elements.append(Paragraph(_fix_text_for_pdf(report_purpose), body_style))

        elements.append(Spacer(1, 12))
        elements.append(Paragraph("2. Patient Overview", section_style))
        sex = _fix_text_for_pdf(report_data.get('gender') or '')
        age = _fix_text_for_pdf(report_data.get('age') or '')
        bmi = _fix_text_for_pdf(report_data.get('bmi') or 'As reported')
        elements.append(Paragraph(f"Sex: {sex} | Age: {age} | BMI: {bmi}", body_style))
        clinical_background = (report_data.get('clinical_background') or '').strip()
        complaints = (report_data.get('complaints') or '').strip() or 'Not reported.'
        goals = (report_data.get('goals') or '').strip() or 'Not reported.'
        if clinical_background:
            elements.append(Paragraph(_fix_text_for_pdf(clinical_background), body_style))
        elements.append(Paragraph("<b>Reported symptoms:</b> " + _fix_text_for_pdf(complaints), body_style))
        elements.append(Paragraph("<b>Patient goals:</b> " + _fix_text_for_pdf(goals), body_style))
    else:
        report_purpose = (report_data.get('report_purpose') or '').strip()
        if report_purpose:
            elements.append(Spacer(1, 12))
            elements.append(Paragraph("Note", section_style))
            elements.append(Paragraph(_fix_text_for_pdf(report_purpose), body_style))

    sleep_section_num = "1" if sleep_study_only else "3"
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"{sleep_section_num}. Sleep Study Data", section_style))
    sleep_intro = (report_data.get('sleep_study_intro') or '').strip() or "Sleep study"
    elements.append(Paragraph("<b>" + _fix_text_for_pdf(sleep_intro) + "</b>", body_style))
    elements.append(Spacer(1, 6))

    key_metrics = report_data.get("sleep_key_metrics_table")
    if isinstance(key_metrics, list) and key_metrics:
        sleep_data = _sleep_key_metrics_table_rows(key_metrics)
    else:
        sleep_data = [
            ['Parameter', 'Value', 'Parameter', 'Value'],
            ['AHI', _fix_text_for_pdf(report_data.get('ahi')), 'RDI', _fix_text_for_pdf(report_data.get('rdi'))],
            ['REM AHI', _fix_text_for_pdf(report_data.get('rem_ahi')), 'Supine AHI', _fix_text_for_pdf(report_data.get('supine_ahi'))],
            ['Minimum SpO₂', _fix_text_for_pdf(report_data.get('o2_nadir')), 'Mean SpO₂', _fix_text_for_pdf(report_data.get('mean_spo2') or 'As reported')],
            ['Time <90%', _fix_text_for_pdf(report_data.get('time_below_90') or 'As reported'), 'ODI', _fix_text_for_pdf(report_data.get('odi'))],
            ['Total Sleep Time', _fix_text_for_pdf(report_data.get('total_sleep_time')), 'Sleep Efficiency', _fix_text_for_pdf(report_data.get('sleep_efficiency'))],
        ]
    sleep_table = Table(sleep_data, colWidths=[110, 145, 110, 145])
    sleep_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), base_font_bold),
        ('FONTNAME', (0, 1), (0, -1), base_font_bold),
        ('FONTNAME', (2, 1), (2, -1), base_font_bold),
        ('FONTNAME', (1, 1), (1, -1), base_font_name),
        ('FONTNAME', (3, 1), (3, -1), base_font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dbeafe')),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.white, colors.HexColor('#f0f9ff')]),
    ]))
    elements.append(sleep_table)

    sleep_observations = report_data.get("sleep_observations")
    obs_elements = _observations_to_pdf_elements(sleep_observations, body_style)
    if not obs_elements:
        sleep_brief = (report_data.get('sleep_brief_summary') or '').strip()
        if sleep_brief:
            obs_elements = _observations_to_pdf_elements(
                [ln.strip() for ln in sleep_brief.split("\n\n") if ln.strip()],
                body_style,
            )
    if obs_elements:
        elements.append(Spacer(1, 10))
        elements.append(Paragraph("Observations", subsection_style))
        elements.extend(obs_elements)

    sleep_interpretation = (report_data.get("sleep_interpretation") or "").strip()
    interp_elements = _interpretation_to_pdf_elements(sleep_interpretation, body_style)
    if interp_elements:
        elements.append(Spacer(1, 10))
        elements.append(Paragraph("Interpretation", subsection_style))
        elements.extend(interp_elements)

    # 4. Key Anatomical Contributors – CBCT-Based Observations (only when section not excluded)
    include_cbct = report_data.get('include_cbct_section', True) and not sleep_study_only
    if include_cbct:
        elements.append(Spacer(1, 12))
        elements.append(Paragraph("4. Key Anatomical Contributors – CBCT-Based Observations", section_style))
        elements.append(Paragraph(
            "<b>Important Note:</b> This section presents observations based on imaging data and does not constitute an official radiological interpretation. Any imaging findings must be reviewed by a certified radiologist or physician before making clinical decisions.",
            disclaimer_style
        ))
        elements.append(Spacer(1, 8))

        # Structural observations as bullet list (reference format)
        def _obs_bullets(val, label_prefix=''):
            parts = [p.strip() for p in str(val or '').split(',') if p.strip()]
            return parts
        obs_bullets = []
        for label, key in [
            ('Obstruction Sites', 'obstruction_sites'),
            ('Soft Palate & Uvula', 'soft_palate'),
            ('Tongue Position', 'tongue_position'),
            ('Bite & Jaw', 'bite_jaw'),
            ('Hyoid', 'hyoid_bone'),
            ('Nasal & Sinus', 'nasal_sinus'),
        ]:
            parts = _obs_bullets(report_data.get(key))
            for p in parts:
                obs_bullets.append(_fix_text_for_pdf(p))
        if obs_bullets:
            bullet_para = '&bull; ' + '<br/>&bull; '.join(obs_bullets)
            elements.append(Paragraph(bullet_para, body_style))
            elements.append(Spacer(1, 8))

        # Clinical Images (if any) - appear before CBCT measurements in section 4
        images = report_data.get('images', [])
        if images:
            s3_client = _get_s3_client()
            bucket_name = os.getenv('S3_BUCKET_NAME')
            
            caption_style = ParagraphStyle(
                'ImageCaption',
                parent=body_style,
                fontSize=7,
                textColor=colors.HexColor('#4b5563'),
                alignment=1,
                spaceBefore=2
            )
            
            image_cells = []
            for img_data in images:
                s3_key = img_data.get('s3_key')
                caption = img_data.get('caption', '')
                
                if s3_key:
                    try:
                        response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
                        image_bytes = response['Body'].read()
                        img_buffer = io.BytesIO(image_bytes)
                        img = RLImage(img_buffer, width=1.6*inch, height=1.6*inch)
                        if caption:
                            fixed_caption = _fix_text_for_pdf(caption)
                            cell_content = [img, Paragraph(f"<i>{fixed_caption}</i>", caption_style)]
                        else:
                            cell_content = [img]
                        image_cells.append(cell_content)
                    except Exception as e:
                        logger.warning(f"Could not add image {s3_key} to PDF: {e}")
            
            if image_cells:
                gallery_rows = []
                for i in range(0, len(image_cells), 3):
                    row = []
                    row.append(image_cells[i])
                    row.append(image_cells[i + 1] if i + 1 < len(image_cells) else '')
                    row.append(image_cells[i + 2] if i + 2 < len(image_cells) else '')
                    gallery_rows.append(row)
                gallery_table = Table(gallery_rows, colWidths=[170, 170, 170])
                gallery_table.setStyle(TableStyle([
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 4),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ]))
                elements.append(gallery_table)
                elements.append(Spacer(1, 12))
        
        # Measurements (bullets) first, then Craniofacial and dental context
        cbct_measurements = report_data.get('cbct_measurements') or []
        cbct_measurements = [m for m in cbct_measurements if (m.get('name') or m.get('value'))]
        if cbct_measurements:
            elements.append(Spacer(1, 8))
            elements.append(Paragraph("Selected craniofacial and dental measurements (CBCT-Derived Cephalometric Data)", subsection_style))
            meas_bullets = []
            for m in cbct_measurements:
                name = (m.get('name') or '').strip()
                value = (m.get('value') or '').strip()
                if name and value:
                    meas_bullets.append(_fix_text_for_pdf(f"{name}: {value}"))
                elif name or value:
                    meas_bullets.append(_fix_text_for_pdf(name or value))
            if meas_bullets:
                bullet_para = '&bull; ' + '<br/>&bull; '.join(meas_bullets)
                elements.append(Paragraph(bullet_para, body_style))
                elements.append(Spacer(1, 8))
        craniofacial_context = (report_data.get('craniofacial_dental_context') or '').strip()
        if craniofacial_context:
            elements.append(Paragraph("Craniofacial and dental context", subsection_style))
            elements.append(Paragraph(_fix_text_for_pdf(craniofacial_context), body_style))

    integrated_pathway = report_data.get('integrated_therapeutic_pathway', '') or report_data.get('treatment_considerations', '') or report_data.get('recommendations', '')
    if not sleep_study_only and (integrated_pathway or '').strip():
        elements.append(Spacer(1, 12))
        elements.append(Paragraph("5. Integrated Therapeutic Pathway and Next Steps", section_style))
        pathway_elements = _pathway_to_pdf_elements(integrated_pathway, body_style, bold_style)
        elements.extend(pathway_elements)

    disclaimer_num = "2" if sleep_study_only else "6"
    elements.append(Spacer(1, 16))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#bfdbfe'), spaceAfter=8))
    elements.append(Paragraph(f"{disclaimer_num}. Disclaimer", section_style))
    disclaimer = report_data.get('disclaimer',
        'This report is intended as a clinical data-integration and pathway-orientation tool. It does not replace clinical judgment, establish a standard of care, or provide definitive treatment recommendations. Final treatment decisions remain the responsibility of the treating clinician.')
    elements.append(Paragraph(_fix_text_for_pdf(disclaimer), disclaimer_style))
    
    # Build PDF
    doc.build(elements)
    pdf_content = buffer.getvalue()
    buffer.close()
    
    return pdf_content


# =============================================================================
# ROUTES
# =============================================================================

@level3_report_bp.route('/level3-report', methods=['GET'])
@login_required
def level3_report_page():
    """Render the Level 3 report page."""
    return render_template('level3_report.html', default_report_purpose=DEFAULT_REPORT_PURPOSE)


@level3_report_bp.route('/api/level3_report/patient_search', methods=['GET'])
@login_required
def level3_patient_search():
    """Search for patients by name."""
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'patients': []})
    
    candidates = (
        Patient.query.filter(Patient.name.ilike(f'%{query}%'))
        .order_by(Patient.name)
        .limit(20)
        .all()
    )
    
    payload = [
        {'id': patient.id, 'name': patient.name}
        for patient in candidates
        if current_user.can_access_patient(patient)
    ]
    return jsonify({'patients': payload})


@level3_report_bp.route('/api/level3_report/patient/<int:patient_id>/data', methods=['GET'])
@login_required
def level3_patient_data(patient_id: int):
    """Load canonical data for a patient (same structure as Level 4)."""
    try:
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({'success': False, 'error': 'Patient not found'}), 404
        
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Check if canonical exists
        envelope = PatientCaseEnvelope.query.filter_by(
            patient_id=patient_id, report_id="canonical"
        ).first()
        has_canonical = envelope is not None and envelope.case_json is not None
        
        canonical_json = _load_canonical_data(patient_id)
        
        # Ensure patient object exists and has name/id (same as Level 4)
        if 'patient' not in canonical_json:
            canonical_json['patient'] = {}
        canonical_json['patient']['name'] = patient.name
        canonical_json['patient']['id'] = patient_id
        
        # Map demographics to patient if available (for compatibility)
        demographics = canonical_json.get('demographics', {})
        if demographics:
            if 'sex' not in canonical_json['patient'] and demographics.get('sex'):
                canonical_json['patient']['sex'] = demographics.get('sex')
            if 'age' not in canonical_json['patient'] and demographics.get('age_years'):
                canonical_json['patient']['age'] = demographics.get('age_years')
            if 'bmi' not in canonical_json['patient'] and demographics.get('bmi'):
                canonical_json['patient']['bmi'] = demographics.get('bmi')
            if 'weight_kg' not in canonical_json['patient'] and demographics.get('weight_kg'):
                canonical_json['patient']['weight_kg'] = demographics.get('weight_kg')
            if 'height_cm' not in canonical_json['patient'] and demographics.get('height_cm'):
                canonical_json['patient']['height_cm'] = demographics.get('height_cm')
        
        return jsonify({
            'success': True,
            'patient_name': patient.name,
            'patient_id': patient_id,
            'canonical_json': canonical_json,
            'has_canonical': has_canonical,
            'warning': None if has_canonical else 'No canonical data found for this patient. You can still enter data manually.'
        })
        
    except Exception as e:
        logger.error(f"Error loading patient data: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@level3_report_bp.route('/api/level3_report/autocomplete', methods=['GET'])
@login_required
def level3_autocomplete():
    """Return autocomplete suggestions for structural observations."""
    field = request.args.get('field', '')
    query = request.args.get('q', '').lower()
    
    if field not in AUTOCOMPLETE_DATA:
        return jsonify({'suggestions': []})
    
    suggestions = AUTOCOMPLETE_DATA[field]
    
    if query:
        # Filter suggestions that contain the query
        suggestions = [s for s in suggestions if query in s.lower()]
    
    return jsonify({'suggestions': suggestions})


@level3_report_bp.route('/api/level3_report/translate', methods=['POST'])
@login_required
def level3_translate():
    """
    Translate Hebrew-containing strings in the report data to English.
    Same approach as Level 4 clinician review translation.
    """
    try:
        data = request.get_json() or {}
        patient_id = data.get('patient_id')
        report_data = data.get('report_data')

        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400
        if not report_data or not isinstance(report_data, dict):
            return jsonify({'success': False, 'error': 'report_data must be a JSON object'}), 400

        patient = Patient.query.get(patient_id)
        if not patient or not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        # Collect Hebrew strings (skip patient.name to preserve exact spelling)
        all_pairs = _collect_hebrew_strings(report_data)
        skip_paths = {'patient_name', 'name'}
        pairs = [(p, v) for (p, v) in all_pairs if p not in skip_paths]

        if not pairs:
            return jsonify({
                'success': True,
                'report_data': report_data,
                'translated_count': 0,
            })

        # Import Bedrock service (same as Level 4)
        try:
            from flask_app.services.bedrock_service import get_bedrock_service
            bedrock_service = get_bedrock_service()
            if not bedrock_service or not bedrock_service.is_available():
                return jsonify({'success': False, 'error': 'Translation service unavailable'}), 500
        except ImportError as e:
            logger.error(f"Failed to import bedrock service: {e}")
            return jsonify({'success': False, 'error': 'Translation service not configured'}), 500

        # Build a compact translation request: { "path": "hebrew string", ... }
        payload = {p: v for (p, v) in pairs}

        system_prompt = (
            "You are a medical translation engine. Translate Hebrew text into English.\n"
            "Return ONLY valid JSON.\n"
            "Rules:\n"
            "- Input is a JSON object mapping field paths to strings.\n"
            "- Output must be a JSON object with the SAME keys.\n"
            "- Translate values to clear clinical English, preserving meaning.\n"
            "- Keep numbers/units as-is.\n"
            "- If a value is already English or not Hebrew, return it unchanged.\n"
            "- Do NOT add extra keys, comments, or explanations.\n"
        )
        user_prompt = (
            "Translate the following JSON values to English.\n\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

        result = bedrock_service.invoke_model(
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            max_tokens=2000,
            temperature=0.0,
            patient_id=patient_id,
            endpoint='level3_report_translate',
        )

        if not result.get('success'):
            return jsonify({'success': False, 'error': result.get('error', 'Translation failed')}), 500

        translated_map = _parse_json_from_llm(result.get('response', ''))
        if not isinstance(translated_map, dict):
            return jsonify({'success': False, 'error': 'Translation output was not valid JSON'}), 500

        # Apply translations back into the report_data
        applied = 0
        for path, original_value in pairs:
            translated_value = translated_map.get(path)
            if isinstance(translated_value, str) and translated_value.strip():
                if translated_value != original_value:
                    if _set_value_by_path(report_data, path, translated_value):
                        applied += 1

        logger.info(f"Level 3 translate: patient {patient_id}, translated {applied} Hebrew strings")
        return jsonify({
            'success': True,
            'report_data': report_data,
            'translated_count': applied,
        })

    except Exception as exc:
        logger.error(f"Level 3 translation error: {exc}", exc_info=True)
        return jsonify({'success': False, 'error': str(exc)}), 500


@level3_report_bp.route('/api/level3_report/generate_pathway', methods=['POST'])
@login_required
def level3_generate_pathway():
    """
    Generate '5. Integrated Therapeutic Pathway and Next Steps' using the LLM (same logic as Level 4).
    Accepts patient_id and report_data; returns generated text for the user to review and edit.
    """
    try:
        data = request.get_json() or {}
        patient_id = data.get('patient_id')
        report_data = data.get('report_data', {})

        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400

        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({'success': False, 'error': 'Patient not found'}), 404

        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        canonical_json = _load_canonical_data(patient_id)
        content = _generate_level3_pathway_content(patient_id, report_data, canonical_json)

        if not content:
            return jsonify({
                'success': False,
                'error': 'Could not generate pathway. Bedrock may be unavailable or returned empty content.'
            }), 500

        return jsonify({'success': True, 'content': content})
    except Exception as e:
        logger.error(f"Level 3 generate_pathway error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@level3_report_bp.route('/api/level3_report/generate_overview', methods=['POST'])
@login_required
def level3_generate_overview():
    """
    Generate Patient Overview (clinical background, complaints, goals) using the LLM from canonical data.
    Returns generated text for the user to review and edit in the form.
    """
    try:
        data = request.get_json() or {}
        patient_id = data.get('patient_id')

        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400

        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({'success': False, 'error': 'Patient not found'}), 404

        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        canonical_json = _load_canonical_data(patient_id)
        overview = _generate_level3_overview_content(patient_id, canonical_json)

        return jsonify({
            'success': True,
            'clinical_background': overview.get('clinical_background', ''),
            'complaints': overview.get('complaints', ''),
            'goals': overview.get('goals', ''),
        })
    except Exception as e:
        logger.error(f"Level 3 generate_overview error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@level3_report_bp.route('/api/level3_report/generate', methods=['POST'])
@login_required
def level3_generate():
    """Generate Level 3 PDF report and store temporarily in S3 for preview (NOT in user reports)."""
    try:
        data = request.get_json() or {}
        patient_id = data.get('patient_id')
        report_data = data.get('report_data', {})
        
        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400
        
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({'success': False, 'error': 'Patient not found'}), 404
        
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        patient_name = patient.name
        
        # Generate PDF
        logger.info(f"Generating Level 3 report for patient {patient_id}")
        logger.info(f"Report data keys: {list(report_data.keys())}")
        logger.debug(f"Report data: {report_data}")
        pdf_content = generate_level3_pdf(report_data, patient_id, patient_name)
        
        # Upload to S3 temp location (for preview only, not registered in AdminFile)
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        pdf_filename = f"Level_3_Report_Patient_{patient_id}_{timestamp}.pdf"
        s3_key = f"patients/{patient_id}/reports/level3/temp/{pdf_filename}"
        
        _upload_to_s3(pdf_content, s3_key)
        
        # Generate presigned URL for immediate preview
        pdf_url = _generate_presigned_url(s3_key, expires_in=3600)
        
        logger.info(f"Level 3 report generated (temp): {s3_key}")
        
        return jsonify({
            'success': True,
            'message': 'Level 3 report generated successfully. Click "Upload to Reports" to save permanently.',
            'pdf_filename': pdf_filename,
            'pdf_s3_key': s3_key,
            'pdf_url': pdf_url,
            'pdf_size': len(pdf_content),
            'is_temp': True  # Indicates this is not yet saved to user reports
        })
        
    except Exception as e:
        logger.error(f"Error generating Level 3 report: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@level3_report_bp.route('/api/level3_report/upload_to_reports', methods=['POST'])
@login_required
def level3_upload_to_reports():
    """Upload a generated Level 3 PDF report to user reports (AdminFile)."""
    try:
        data = request.get_json() or {}
        patient_id = data.get('patient_id')
        temp_s3_key = data.get('s3_key')
        pdf_filename = data.get('filename')
        pdf_size = data.get('size', 0)
        
        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400
        if not temp_s3_key:
            return jsonify({'success': False, 'error': 's3_key is required'}), 400
        
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({'success': False, 'error': 'Patient not found'}), 404
        
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Copy from temp location to permanent location
        s3_client = _get_s3_client()
        bucket_name = os.getenv('S3_BUCKET_NAME')
        
        # New permanent S3 key (remove /temp/ from path)
        permanent_s3_key = temp_s3_key.replace('/temp/', '/')
        
        # Copy the file to permanent location
        try:
            s3_client.copy_object(
                Bucket=bucket_name,
                CopySource={'Bucket': bucket_name, 'Key': temp_s3_key},
                Key=permanent_s3_key
            )
            logger.info(f"Copied Level 3 report from {temp_s3_key} to {permanent_s3_key}")
        except Exception as copy_err:
            logger.error(f"Failed to copy S3 object: {copy_err}")
            return jsonify({'success': False, 'error': 'Failed to save report'}), 500
        
        # Create/update AdminFile record
        existing_report = AdminFile.query.filter(
            AdminFile.patient_id == patient_id,
            AdminFile.name.like('Level_3_Report_%')
        ).first()
        
        if existing_report:
            # Update existing record
            existing_report.name = pdf_filename
            existing_report.s3_key = permanent_s3_key
            existing_report.file_type = 'application/pdf'
            existing_report.file_size = pdf_size
            existing_report.upload_date = datetime.utcnow()
        else:
            # Create new record
            admin_file = AdminFile(
                patient_id=patient_id,
                name=pdf_filename,
                s3_key=permanent_s3_key,
                file_type='application/pdf',
                file_size=pdf_size,
                upload_date=datetime.utcnow(),
                file_category='level3_report'
            )
            db.session.add(admin_file)
        
        db.session.commit()
        
        # Optionally delete the temp file
        try:
            s3_client.delete_object(Bucket=bucket_name, Key=temp_s3_key)
            logger.info(f"Deleted temp file: {temp_s3_key}")
        except Exception as del_err:
            logger.warning(f"Failed to delete temp file {temp_s3_key}: {del_err}")
        
        # Generate presigned URL for the permanent file
        pdf_url = _generate_presigned_url(permanent_s3_key, expires_in=3600)
        
        logger.info(f"Level 3 report saved to user reports: {permanent_s3_key}")
        
        return jsonify({
            'success': True,
            'message': 'Report uploaded to user reports successfully',
            'pdf_filename': pdf_filename,
            'pdf_s3_key': permanent_s3_key,
            'pdf_url': pdf_url
        })
        
    except Exception as e:
        logger.error(f"Error uploading Level 3 report to user reports: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@level3_report_bp.route('/api/level3_report/upload_image', methods=['POST'])
@login_required
def level3_upload_image():
    """Upload clinical image for Level 3 report."""
    try:
        if 'image' not in request.files:
            return jsonify({'success': False, 'error': 'No image file provided'}), 400
        
        image = request.files['image']
        patient_id = request.form.get('patient_id')
        
        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400
        
        patient = Patient.query.get(patient_id)
        if not patient or not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Generate unique filename
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')
        ext = os.path.splitext(image.filename)[1] or '.png'
        filename = f"level3_image_{timestamp}{ext}"
        s3_key = f"patients/{patient_id}/reports/level3/images/{filename}"
        
        # Upload to S3
        image_bytes = image.read()
        content_type = image.content_type or 'image/png'
        _upload_to_s3(image_bytes, s3_key, content_type)
        
        # Generate presigned URL for immediate access
        image_url = _generate_presigned_url(s3_key, expires_in=3600)
        
        return jsonify({
            'success': True,
            'url': image_url,
            's3_key': s3_key
        })
        
    except Exception as e:
        logger.error(f"Error uploading image: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
