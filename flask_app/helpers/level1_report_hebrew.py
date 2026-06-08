import html as html_module
import io
import json
import os
import re
from typing import Any, Dict, Optional

from flask import current_app, render_template
from xhtml2pdf import pisa

from flask_app.helpers.vizbriz_quiz_helpers import get_localized_text

def _pisa_link_callback(uri: str, rel: Optional[str] = None) -> str:
    """
    Allow xhtml2pdf to resolve local assets referenced as file://... (fonts/images).
    """
    if not uri:
        return uri
    if uri.startswith("file://"):
        return uri.replace("file://", "", 1)
    return uri


_DEJAVU_FONT_CANDIDATES = [
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]


def _get_dejavu_font_path() -> Optional[str]:
    for p in _DEJAVU_FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


_HAS_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
_HEBREW_RUN_RE = re.compile(r"[\u0590-\u05FF]+")


def _fix_hebrew_for_pdf(value: str) -> str:
    """
    xhtml2pdf/ReportLab has limited RTL support and often renders Hebrew characters reversed.
    We pre-process strings so they visually render correctly in the generated PDF.

    Preference:
    - If python-bidi is installed, use it (best for mixed LTR/RTL).
    - Otherwise, reverse Hebrew character runs as a pragmatic fallback.
    """
    if not value:
        return value

    # Best-case: proper bidi algorithm if available (handles mixed Hebrew/English better)
    try:
        from bidi.algorithm import get_display  # type: ignore

        # base_dir='R' helps for predominantly RTL strings that include LTR tokens (e.g., CPAP/OAT, numbers)
        return get_display(value, base_dir="R")
    except Exception:
        pass

    # Fallback: reverse only Hebrew character runs; keep LTR tokens (numbers/CPAP/OAT) intact.
    if not _HAS_HEBREW_RE.search(value):
        return value

    def _rev(match: re.Match) -> str:
        return match.group(0)[::-1]

    return _HEBREW_RUN_RE.sub(_rev, value)


def _fix_context_rtl_strings(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply Hebrew fix to text fields only. Leave URLs/URIs as-is.
    """
    skip_suffixes = ("_uri", "_url")
    fixed: Dict[str, Any] = {}
    for k, v in ctx.items():
        if k.endswith(skip_suffixes):
            fixed[k] = v
            continue
        if isinstance(v, str):
            fixed[k] = _fix_hebrew_for_pdf(v)
        else:
            fixed[k] = v
    return fixed


def _level1_alert_risk_class(
    summary: Optional[Dict[str, Any]], diagnosed: bool, treatment: bool
) -> str:
    """
    Map quiz evaluation to CSS class for the screening alert box in level1_report_hebrew_preview.
    - alert--low: green (low / mild)
    - alert--moderate: amber
    - alert--high: red/pink (high, or diagnosed and not on therapy)
    """
    if diagnosed:
        return "alert--moderate" if treatment else "alert--high"
    s = f"{(summary or {}).get('risk_band') or ''} {(summary or {}).get('risk_label') or ''}".lower()
    if "high" in s:
        return "alert--high"
    if "moderate" in s:
        return "alert--moderate"
    if "mild" in s or "low" in s:
        return "alert--low"
    return "alert--high"


def build_level1_placeholder_context(lang: str, overrides: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a placeholder context for a Level-1 screening report.
    Values can be overridden via query params (strings).
    """
    base_dir = os.path.dirname(os.path.dirname(__file__))  # flask_app/
    flask_static_dir = os.path.join(base_dir, "flask_static")

    logo_path = os.path.join(flask_static_dir, "images", "logos", "vizbriz_logo.png")
    # Browser-accessible URLs (for HTML preview)
    logo_url = "/flask_static/images/logos/vizbriz_logo.png"
    # "Sleep Apnea Solution" graphic: prefer bundled oat_sleep.jpg (legacy CDN parity, small JPEG),
    # then optional alternates under images/reports/level1/.
    primary_oat_jpg = os.path.join(flask_static_dir, "images", "reports", "level1", "oat_sleep.jpg")
    preferred_solution_img_path_updated_png = os.path.join(
        flask_static_dir, "images", "reports", "level1", "cpap_vs_simple_sleep_updated.png"
    )
    preferred_solution_img_path_updated_jpg = os.path.join(
        flask_static_dir, "images", "reports", "level1", "cpap_vs_simple_sleep_updated.jpg"
    )
    preferred_solution_img_path_png = os.path.join(
        flask_static_dir, "images", "reports", "level1", "cpap_vs_simple_sleep.png"
    )
    preferred_solution_img_path_jpg = os.path.join(
        flask_static_dir, "images", "reports", "level1", "cpap_vs_simple_sleep.jpg"
    )
    fallback_solution_img_path_png = os.path.join(
        flask_static_dir, "images", "reports", "level1", "oat_sleep.png"
    )
    final_fallback = os.path.join(flask_static_dir, "cute_dog_sleeping.jpg")

    if os.path.exists(primary_oat_jpg):
        solution_img_path = primary_oat_jpg
        solution_image_url = "/flask_static/images/reports/level1/oat_sleep.jpg"
    elif os.path.exists(preferred_solution_img_path_updated_png):
        solution_img_path = preferred_solution_img_path_updated_png
        solution_image_url = "/flask_static/images/reports/level1/cpap_vs_simple_sleep_updated.png"
    elif os.path.exists(preferred_solution_img_path_updated_jpg):
        solution_img_path = preferred_solution_img_path_updated_jpg
        solution_image_url = "/flask_static/images/reports/level1/cpap_vs_simple_sleep_updated.jpg"
    elif os.path.exists(preferred_solution_img_path_png):
        solution_img_path = preferred_solution_img_path_png
        solution_image_url = "/flask_static/images/reports/level1/cpap_vs_simple_sleep.png"
    elif os.path.exists(preferred_solution_img_path_jpg):
        solution_img_path = preferred_solution_img_path_jpg
        solution_image_url = "/flask_static/images/reports/level1/cpap_vs_simple_sleep.jpg"
    elif os.path.exists(fallback_solution_img_path_png):
        solution_img_path = fallback_solution_img_path_png
        solution_image_url = "/flask_static/images/reports/level1/oat_sleep.png"
    else:
        solution_img_path = final_fallback
        solution_image_url = "/flask_static/cute_dog_sleeping.jpg"

    # Cache-bust the HTML image URL so browsers/CDNs refresh when the file changes.
    # Important: keep the file:// URI separate for PDF rendering (Playwright), so PDFs are not affected.
    solution_image_url_for_html = solution_image_url
    try:
        if solution_img_path and os.path.exists(solution_img_path) and solution_image_url.startswith("/flask_static/"):
            solution_image_url_for_html = f"{solution_image_url}?v={int(os.path.getmtime(solution_img_path))}"
    except Exception:
        # Best-effort only; never fail report generation due to cache-busting.
        solution_image_url_for_html = solution_image_url

    is_he = (lang or "en").lower().startswith("he")

    labels_en = {
        "patient_details": "Patient Details",
        "risk_level": "Risk Level",
        "full_name": "Full Name",
        "gender": "Gender",
        "age": "Age",
        "date_of_birth": "Date of birth",
        "bmi": "BMI",
        "height": "Height (cm)",
        "weight": "Weight (kg)",
        "recommendations": "Recommendations",
        "reported_symptoms": "Reported Symptoms",
        "solution_title": "Sleep Apnea Solution",
    }

    labels_he = {
        "patient_details": "פרטי מטופל",
        "risk_level": "רמת סיכון",
        "full_name": "שם מלא",
        "gender": "מגדר",
        "age": "גיל",
        "date_of_birth": "תאריך לידה",
        "bmi": "BMI",
        "height": "גובה (ס״מ)",
        "weight": "משקל (ק״ג)",
        "recommendations": "המלצות",
        "reported_symptoms": "תסמינים שדווחו",
        "solution_title": "פתרון לדום נשימה בשינה",
    }

    labels = labels_he if is_he else labels_en

    # Build recommendations HTML list items (safe, controlled).
    rec_1 = overrides.get("rec_1") or ("המלצה 1 (טקסט לדוגמה)." if is_he else "Recommendation 1 (placeholder).")
    rec_2 = overrides.get("rec_2") or ("המלצה 2 (טקסט לדוגמה)." if is_he else "Recommendation 2 (placeholder).")
    recommendations_list = f"<li>{rec_1}</li><li>{rec_2}</li>"

    ctx: Dict[str, Any] = {
        # New template variables (user-provided HTML)
        "report_title": "דוח סקר – ויזבריז" if is_he else "VizBriz Screening Report",
        "header_title": "תודה על השלמת ההערכה שלך" if is_he else "Thank you for completing your assessment",
        "header_subtitle": (
            ""
            if is_he
            else "This is a preview template (placeholder text). We will replace with real content."
        ),
        "logo_url": logo_url,
        "risk_level": overrides.get("risk_level") or ("בינוני" if is_he else "Moderate"),
        "full_name": overrides.get("full_name") or ("שם מלא לדוגמה" if is_he else "Sample Full Name"),
        "gender": overrides.get("gender") or ("אחר" if is_he else "Other"),
        "age": overrides.get("age") or "30",
        "dob": overrides.get("dob") or overrides.get("date_of_birth") or "1995-12-17",
        "bmi": overrides.get("bmi") or "25.5",
        "height": overrides.get("height") or overrides.get("height_cm") or "175",
        "weight": overrides.get("weight") or overrides.get("weight_kg") or "78",
        "alert_title": overrides.get("alert_title") or ("התראה: סיכון בינוני" if is_he else "Moderate Risk Alert"),
        "alert_risk_class": overrides.get("alert_risk_class", "alert--high"),
        "alert_text": overrides.get("alert_text") or overrides.get("alert_body") or ("טקסט לדוגמה להתראה." if is_he else "Placeholder alert text."),
        "cta_text": overrides.get("cta_text") or ("קבע/י בדיקת שינה עוד היום!" if is_he else "Schedule Your Sleep Test Today!"),
        "cta_url": overrides.get("cta_url") or "#",
        "recommendations_list": overrides.get("recommendations_list") or recommendations_list,
        "symptoms_text": overrides.get("symptoms_text") or overrides.get("reported_symptoms") or (
            "טקסט לדוגמה לתסמינים שדווחו."
            if is_he
            else "Placeholder narrative summary of reported symptoms."
        ),
        "solution_title": overrides.get("solution_title") or labels["solution_title"],
        "solution_text": overrides.get("solution_text") or overrides.get("solution_body") or (
            "טקסט לדוגמה לפתרון. נחליף לתוכן אמיתי בהמשך."
            if is_he
            else "Placeholder solution narrative."
        ),
        # Generic link (same for all clinics/languages).
        "solution_link": "https://www.aadsm.org/oral_appliance_therapy.php",
        "solution_link_text": overrides.get("solution_link_text") or ("למידע נוסף על טיפול באמצעות התקן אוראלי" if is_he else "Learn More About Oral Appliance Therapy"),
        "solution_image_url": overrides.get("solution_image_url") or solution_image_url_for_html,
        "solution_image_alt": overrides.get("solution_image_alt") or ("פתרון לטיפול בדום נשימה בשינה" if is_he else "Sleep apnea solution"),
        # Optional (if you switch to video later)
        "solution_video_url": overrides.get("solution_video_url") or "",
        "solution_video_poster": overrides.get("solution_video_poster") or "",
        "disclaimer_text": overrides.get("disclaimer_text") or (
            "דוח זה משמש ככלי סקר המסייע בקבלת החלטות קליניות, ואינו מהווה אבחנה רפואית."
            if is_he
            else "This report is a screening tool and does not constitute a medical diagnosis."
        ),

        # Keep these so PDF conversion can swap to file:// assets later
        "logo_file_uri": f"file://{logo_path}",
        "solution_image_file_uri": f"file://{solution_img_path}",
        # Keep the un-versioned URL here for logging/debugging and non-browser use.
        "solution_image_src": solution_image_url,
        "lang": "he" if is_he else "en",
        "dir": "rtl" if is_he else "ltr",
        # Section / table labels for templates (avoids hard-coded Hebrew in HTML)
        "labels": labels,
    }

    return ctx


# English OAT section (same clinical intent as Hebrew copy; HTML for template |safe)
_LEVEL1_EN_OAT_SOLUTION_HTML = """
<p>If sleep apnea is affecting your sleep quality, you deserve a solution that feels comfortable and calm—supporting quieter nights and better daytime energy.</p>
<p>For some patients, CPAP therapy (mask, tubing, and sometimes noise) can feel cumbersome or hard to stick with over time.</p>
<p>When therapy feels frustrating, people may use it inconsistently, which can get in the way of results.</p>
<p>Many people are not aware that additional options exist within dental sleep medicine that may be worth discussing with a clinician.</p>
<p>One option is oral appliance therapy (OAT).</p>
<p>This is a small custom-fitted dental appliance that works quietly and comfortably to help keep the airway more open during sleep. It is an established approach without hoses and without machine noise for appropriate candidates.</p>
<p>Choosing a solution that fits your needs can support more consistent, restorative sleep and better daytime focus—guided by a licensed professional.</p>
""".strip()


def render_level1_report_html(context: Dict[str, Any]) -> str:
    is_he = (context.get("lang") or "en").lower().startswith("he")

    # Prefer Noto Sans Hebrew for Hebrew PDFs (available as a system font on the EC2 host).
    if is_he:
        # For HTML frames, serve the font via HTTP so the browser can load it.
        # For PDFs, Playwright ignores @font-face and we force the font in the renderer.
        font_name = "Noto Sans Hebrew"
    else:
        font_name = "DejaVuSans"
        font_path = _get_dejavu_font_path()

    font_face_css = ""
    if is_he:
        # Use the blueprint font route so HTML frames match the PDF rendering.
        font_face_css = f"""
        @font-face {{
            font-family: "{font_name}";
            src: url("/vizbriz/assets/fonts/NotoSansHebrew-Regular.ttf") format("truetype");
            font-weight: normal;
            font-style: normal;
        }}
        @font-face {{
            font-family: "{font_name}";
            src: url("/vizbriz/assets/fonts/NotoSansHebrew-Bold.ttf") format("truetype");
            font-weight: bold;
            font-style: normal;
        }}
        """
    elif font_path and os.path.exists(font_path):
        font_face_css = f"""
        @font-face {{
            font-family: "{font_name}";
            src: url("file://{font_path}");
        }}
        """
    else:
        current_app.logger.warning(f"Font not found: {font_path}; rendering may be degraded")

    return render_template(
        "reports/level1_report_hebrew_preview.html",
        **context,
        font_name=font_name,
        font_face_css=font_face_css,
    )


def prepare_context_for_pdf(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prepare context for PDF output. 
    Converts relative URLs to absolute file:// paths for Playwright to load images.
    """
    ctx = dict(context)
    
    # Convert logo URL to absolute file path for Playwright
    if ctx.get("logo_file_uri"):
        ctx["logo_url"] = ctx["logo_file_uri"]
    elif ctx.get("logo_url") and not ctx["logo_url"].startswith(("http://", "https://", "file://")):
        # Convert relative path to absolute file:// path
        base_dir = os.path.dirname(os.path.dirname(__file__))  # flask_app/
        flask_static_dir = os.path.join(base_dir, "flask_static")
        logo_path = ctx["logo_url"].replace("/flask_static/", "").split("?", 1)[0]
        full_logo_path = os.path.join(flask_static_dir, logo_path)
        if os.path.exists(full_logo_path):
            ctx["logo_url"] = f"file://{full_logo_path}"
    
    # Convert solution image URL to absolute file path for Playwright
    if ctx.get("solution_image_file_uri"):
        ctx["solution_image_url"] = ctx["solution_image_file_uri"]
    elif ctx.get("solution_image_url") and not ctx["solution_image_url"].startswith(("http://", "https://", "file://")):
        # Convert relative path to absolute file:// path
        base_dir = os.path.dirname(os.path.dirname(__file__))  # flask_app/
        flask_static_dir = os.path.join(base_dir, "flask_static")
        img_path = ctx["solution_image_url"].replace("/flask_static/", "").split("?", 1)[0]
        full_img_path = os.path.join(flask_static_dir, img_path)
        if os.path.exists(full_img_path):
            ctx["solution_image_url"] = f"file://{full_img_path}"
        else:
            # Try to construct absolute URL if file doesn't exist locally
            current_app.logger.warning(f"Image not found at {full_img_path}, using original URL: {ctx['solution_image_url']}")

    # DO NOT apply Hebrew fixes - this was causing text corruption (black blocks)
    # The HTML template handles RTL properly, and we want PDF to match HTML exactly
    return ctx


def _build_level1_en_context_from_vizbriz_quiz(quiz, raw: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    """Build English (LTR) Level-1 context from stored quiz JSON + evaluation summary."""

    def _pick(*keys, default=""):
        for k in keys:
            v = raw.get(k)
            if v is not None and str(v).strip() != "":
                return str(v).strip()
        return default

    diagnosed = summary.get("diagnosed")
    if diagnosed is None:
        diagnosed = raw.get("Q1") == "yes"
    treatment = summary.get("treatment")
    if treatment is None:
        treatment = raw.get("Q2") == "yes"

    if diagnosed:
        risk_en = "Diagnosed – not on therapy" if not treatment else "Diagnosed – on therapy"
    else:
        risk_en = (summary.get("risk_label") or summary.get("risk_band") or "—").strip() or "—"

    full_name = _pick("FULL_NAME", "DEMO_FULL_NAME", default="") or "—"
    gender_raw = _pick("DEMO_SEX", "GENDER", "DEMO_GENDER", default="")
    gender_map = {"male": "Male", "female": "Female", "other": "Other"}
    gender = gender_map.get(gender_raw.strip().lower(), gender_raw or "—")

    dob = _pick("DOB", "DEMO_DOB", "DEMO_DATE_OF_BIRTH", default=_pick("DATE_OF_BIRTH", default=""))
    height = _pick("HEIGHT", "DEMO_HEIGHT", "DEMO_HEIGHT_CM", "HEIGHT_CM", default=_pick("height_cm", default=""))
    weight = _pick("WEIGHT", "DEMO_WEIGHT", "DEMO_WEIGHT_KG", "WEIGHT_KG", default=_pick("weight_kg", default=""))
    age = _pick("AGE", "DEMO_AGE", default="")

    lang_code = (getattr(quiz, "language", None) or "en").strip().lower().split("-", 1)[0]

    def _resolve_stored_message(val: Optional[str]) -> str:
        """Turn stored MSG_*.title / .body or CTA_* keys into copy (see get_localized_text fallbacks)."""
        if not val or not isinstance(val, str):
            return ""
        t = val.strip()
        if not t:
            return ""
        if t.startswith("MSG_") or t.startswith("CTA_"):
            resolved = get_localized_text(t, lang_code, None)
            if resolved != t:
                return resolved
            return ""
        return t

    ob = _resolve_stored_message(summary.get("outcome_body"))
    ob = (ob or "").strip()

    alert_title = _resolve_stored_message(summary.get("outcome_title"))
    if not alert_title:
        alert_title = (summary.get("risk_label") or summary.get("risk_band") or "Screening summary").strip()
    generic_alert = (
        "Based on your responses, follow-up with a qualified clinician may be appropriate to interpret these results in context."
    )
    symptoms_text = ob or "A detailed symptom summary will appear here based on your responses."

    if ob:
        parts = re.split(r"(?<=[.!?])\s+", ob.strip(), maxsplit=1)
        first = parts[0].strip() if parts and parts[0] else ""
        rest = parts[1].strip() if len(parts) > 1 and parts[1] else ""
        if first and rest:
            alert_text = first
            symptoms_text = ob.strip()
        elif first:
            alert_text = generic_alert
            symptoms_text = first
        else:
            alert_text = generic_alert
    else:
        alert_text = generic_alert

    rec_1 = "Consider sleep position and other sleep-hygiene strategies that support steadier breathing during sleep."
    rec_2 = "Ask your clinician whether a home sleep test or an in-lab sleep study is appropriate as a next step."
    flags = summary.get("red_flags") or []
    if isinstance(flags, list) and flags:
        rec_2 = (
            "Discuss your screening results and any concerns such as "
            + ", ".join(html_module.escape(str(f)) for f in flags[:3])
            + " with a clinician to decide on next steps."
        )

    recommendations_list = (
        f"<li>{html_module.escape(rec_1)}</li><li>{html_module.escape(rec_2)}</li>"
    )

    alert_risk_class = _level1_alert_risk_class(summary, bool(diagnosed), bool(treatment))

    overrides = {
        "report_title": "VizBriz Screening Report",
        "header_title": "Thank you for completing your assessment",
        "header_subtitle": "",
        "full_name": full_name,
        "gender": gender,
        "age": age or "—",
        "dob": dob or "—",
        "bmi": _pick("BMI", "DEMO_BMI", default="") or "—",
        "height": height or "—",
        "weight": weight or "—",
        "risk_level": risk_en,
        "alert_risk_class": alert_risk_class,
        "alert_title": alert_title,
        "alert_text": alert_text,
        "cta_text": _resolve_stored_message(summary.get("cta_text")) or "Schedule follow-up",
        "cta_url": "https://portal.isleepemr.com/booking/create-appointment/?booking=6809ea85e24b0b0ae4bdce75",
        "recommendations_list": recommendations_list,
        "symptoms_text": symptoms_text,
        "solution_text": _LEVEL1_EN_OAT_SOLUTION_HTML,
        "disclaimer_text": (
            "This AI-assisted screening report is intended as a decision-support tool and does not replace medical advice. "
            "Final decisions should be made by licensed healthcare professionals."
        ),
    }
    return build_level1_placeholder_context(lang="en", overrides=overrides)


def extract_level1_demographics_from_vizbriz_quiz(quiz) -> Optional[Dict[str, Any]]:
    """
    Read sex, age, BMI (and height/weight) from a VizBrizQuiz using the same fields as the
    questionnaire / L2 PDF: raw_answers plus enhanced_answers.questions_and_answers (qa_lookup).

    VizBriz OSA v1 often stores DEMO_DOB, DEMO_BMI_COMPUTED_CDC, DEMO_HEIGHT_CM, DEMO_WEIGHT_KG
    rather than literal AGE / BMI keys — those must be resolved here.
    """
    if quiz is None:
        return None
    try:
        payload = json.loads(quiz.quiz_input or "{}")
    except Exception:
        payload = {}

    raw = payload.get("raw_answers") or {}
    enhanced = payload.get("enhanced_answers") or {}

    from flask_app.helpers.vizbriz_quiz_helpers import _build_qa_lookup, _compute_age_from_dob_string

    qa_lookup = _build_qa_lookup(enhanced if isinstance(enhanced, dict) else {})

    def _val(*keys: str) -> str:
        for k in keys:
            for src in (raw, qa_lookup):
                if not isinstance(src, dict):
                    continue
                v = src.get(k)
                if v is not None and str(v).strip() != "":
                    return str(v).strip()
        return ""

    lang = (getattr(quiz, "language", None) or "en").strip().lower()
    gender_raw = _val("DEMO_SEX", "GENDER", "DEMO_GENDER")
    if not gender_raw:
        sex_display: Optional[str] = None
    elif lang.startswith("he"):
        gender_map = {"male": "זכר", "female": "נקבה", "other": "אחר"}
        sex_display = gender_map.get(gender_raw.strip().lower(), gender_raw)
    else:
        gender_map = {"male": "Male", "female": "Female", "other": "Other"}
        sex_display = gender_map.get(gender_raw.strip().lower(), gender_raw or None)

    age_years: Any = None
    age_s = _val("DEMO_AGE", "AGE")
    if age_s:
        try:
            age_years = int(float(age_s.replace(",", ".")))
        except (TypeError, ValueError):
            age_years = age_s
    if age_years is None:
        dob_s = _val("DEMO_DOB", "DOB", "DEMO_DATE_OF_BIRTH", "DATE_OF_BIRTH")
        computed = _compute_age_from_dob_string(dob_s) if dob_s else None
        if computed is not None:
            age_years = int(computed)

    height_s = _val("DEMO_HEIGHT_CM", "HEIGHT_CM", "HEIGHT", "DEMO_HEIGHT", "height_cm")
    weight_s = _val("DEMO_WEIGHT_KG", "WEIGHT_KG", "WEIGHT", "DEMO_WEIGHT", "weight_kg")

    bmi_str = _val("DEMO_BMI_COMPUTED_CDC", "DEMO_BMI", "BMI")
    bmi_out: Any = None
    if bmi_str:
        try:
            bmi_out = round(float(bmi_str.replace(",", ".")), 1)
        except (TypeError, ValueError):
            bmi_out = bmi_str
    if bmi_out is None and height_s and weight_s:
        try:
            h_m = float(str(height_s).replace(",", ".")) / 100.0
            if h_m > 0:
                bmi_out = round(float(str(weight_s).replace(",", ".")) / (h_m * h_m), 1)
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    if height_s:
        try:
            height_cm = round(float(height_s.replace(",", ".")), 1)
        except (TypeError, ValueError):
            pass
    if weight_s:
        try:
            weight_kg = round(float(weight_s.replace(",", ".")), 1)
        except (TypeError, ValueError):
            pass

    if not sex_display and age_years is None and bmi_out is None and height_cm is None and weight_kg is None:
        return None

    return {
        "sex": sex_display,
        "age_years": age_years,
        "bmi": bmi_out,
        "height_cm": height_cm,
        "weight_kg": weight_kg,
    }


def build_level1_context_from_vizbriz_quiz(quiz) -> Dict[str, Any]:
    """
    Build report template placeholders from a saved VizBrizQuiz record.
    Uses the JSON stored in VizBrizQuiz.quiz_input (raw_answers + evaluation_summary).
    """
    try:
        import json

        payload = json.loads(quiz.quiz_input or "{}")
    except Exception:
        payload = {}

    raw = payload.get("raw_answers") or {}
    summary = payload.get("evaluation_summary") or {}

    lang = (getattr(quiz, "language", None) or "en").strip().lower()
    if not lang.startswith("he"):
        return _build_level1_en_context_from_vizbriz_quiz(quiz, raw, summary)

    # Normalize risk band values coming from the quiz engine.
    # Engine may provide tokens like "high" / "moderate" OR labels like "High Risk" / "Moderate Risk".
    risk_band_raw = (summary.get("risk_band") or quiz.risk_band or "")

    # Check if patient is diagnosed for Hebrew-specific risk band display
    diagnosed = summary.get("diagnosed") or raw.get("Q1") == "yes"
    treatment = summary.get("treatment") or raw.get("Q2") == "yes"

    # For Hebrew version: use special labels for diagnosed patients
    if diagnosed:
        if not treatment:
            risk_he = "מאובחן - לא מטופל"
        else:
            risk_he = "מאובחן בטיפול"
    else:
        # Use standard risk band mapping for non-diagnosed patients
        risk_band_norm = str(risk_band_raw).lower().strip()
        if "high" in risk_band_norm:
            risk_key = "high"
        elif "moderate" in risk_band_norm:
            risk_key = "moderate"
        elif "mild" in risk_band_norm:
            risk_key = "mild"
        elif "low" in risk_band_norm:
            risk_key = "low"
        else:
            risk_key = "moderate"

        risk_map = {
            "low": "נמוך",
            "moderate": "בינוני",
            "high": "גבוה",
            "mild": "קל",
        }
        risk_he = risk_map.get(risk_key, "בינוני")

    def _pick(*keys, default=""):
        for k in keys:
            v = raw.get(k)
            if v is not None and str(v).strip() != "":
                return str(v).strip()
        return default

    full_name = _pick("FULL_NAME", "DEMO_FULL_NAME", default="")
    gender_raw = _pick("DEMO_SEX", "GENDER", "DEMO_GENDER", default="")
    gender_map = {"male": "זכר", "female": "נקבה", "other": "אחר"}
    gender = gender_map.get(gender_raw.strip().lower(), gender_raw or "אחר")

    dob = _pick("DOB", "DEMO_DOB", "DEMO_DATE_OF_BIRTH", default=_pick("DATE_OF_BIRTH", default=""))
    bmi = _pick("BMI", "DEMO_BMI", default=str(quiz.total_score or "")) if False else _pick("BMI", "DEMO_BMI", default="")
    height = _pick("HEIGHT", "DEMO_HEIGHT", "DEMO_HEIGHT_CM", "HEIGHT_CM", default=_pick("height_cm", default=""))
    weight = _pick("WEIGHT", "DEMO_WEIGHT", "DEMO_WEIGHT_KG", "WEIGHT_KG", default=_pick("weight_kg", default=""))
    age = _pick("AGE", "DEMO_AGE", default="")

    # Defaults (will be overridden by LLM narrative if present)
    rec_1 = "נסה/י לישון על הצד עם כרית תומכת כדי להפחית חסימת נתיב אוויר ולשפר את איכות השינה."
    rec_2 = "מומלץ לבצע בדיקת שינה ביתית כדי להעריך את דפוסי השינה ולהתאים את הצעד הבא."

    # For diagnosed patients, remove "סיכון" from alert title
    if diagnosed and (risk_he == "מאובחן - לא מטופל" or risk_he == "מאובחן בטיפול"):
        alert_title = f"התראה: {risk_he}"
    else:
        alert_title = f"התראה: סיכון {risk_he}"
    alert_text = "בהתבסס על התשובות שלך, ייתכן שקיים סיכון לדום נשימה בשינה. מומלץ לפנות להערכה מקצועית."
    cta_text = "קבע/י תור להמשך הערכה"

    # If outcome_body is a message key, avoid showing it to the patient
    ob = summary.get("outcome_body")
    if isinstance(ob, str) and ob.strip().startswith("MSG_"):
        ob = ""
    symptoms_text = ob or "סיכום התסמינים יופיע כאן לאחר עיבוד התשובות."

    # If we already generated a Hebrew narrative (stored in quiz.ai_response), use it
    try:
        ai_data = None
        if quiz.ai_response:
            ai_data = json.loads(quiz.ai_response) if isinstance(quiz.ai_response, str) else quiz.ai_response
        narrative = (ai_data or {}).get("level1_report_he") if isinstance(ai_data, dict) else None
        if isinstance(narrative, dict):
            if isinstance(narrative.get("alert_text"), str) and narrative.get("alert_text").strip():
                alert_text = narrative["alert_text"].strip()
            recs = narrative.get("recommendations")
            if isinstance(recs, list) and len(recs) >= 2:
                if isinstance(recs[0], str) and recs[0].strip():
                    rec_1 = recs[0].strip()
                if isinstance(recs[1], str) and recs[1].strip():
                    rec_2 = recs[1].strip()
            if isinstance(narrative.get("reported_symptoms"), str) and narrative.get("reported_symptoms").strip():
                symptoms_text = narrative["reported_symptoms"].strip()
        # If there was an error generating narrative, show it in logs only (not to patient)
        # Keep patient-facing placeholder text.
    except Exception:
        pass

    alert_risk_class_he = _level1_alert_risk_class(summary, bool(diagnosed), bool(treatment))

    overrides = {
        "full_name": full_name or "שם מלא",
        "gender": gender,
        "age": age or "—",
        "dob": dob or "—",
        "bmi": bmi or "—",
        "height": height or "—",
        "weight": weight or "—",
        "risk_level": risk_he,
        "alert_risk_class": alert_risk_class_he,
        "alert_title": alert_title,
        "alert_text": alert_text,
        "cta_text": cta_text,
        "cta_url": "https://portal.isleepemr.com/booking/create-appointment/?booking=6809ea85e24b0b0ae4bdce75",
        "rec_1": rec_1,
        "rec_2": rec_2,
        "symptoms_text": symptoms_text,
        "solution_text": (
            "<p>אם דום נשימה בשינה פוגע באיכות השינה שלך, מגיע לך פתרון שמרגיש נוח ורגוע – כזה שתומך בלילה שקט יותר ובתחושת רעננות טובה יותר במהלך היום.</p>"
            "<p>עבור חלק מהמטופלים, טיפול באמצעות מכשיר CPAP, הכולל מסכה, צינורות ולעיתים גם רעש, עלול להיות מסורבל ולא נוח.</p>"
            "<p>במקרים מסוימים, חוויית השימוש עשויה להוביל לתסכול, לקושי בהתמדה ולשימוש לא עקבי לאורך זמן.</p>"
            "<p>אנשים רבים אינם מודעים לכך שקיימים פתרונות טיפוליים נוספים במסגרת רפואת השיניים שניתן לשקול.</p>"
            "<p>אחת האפשרויות הקיימות היא טיפול באמצעות התקן אוראלי (OAT).</p>"
            "<p>זהו התקן קטן המותאם לשיניים, הפועל בשקט ובנוחות כדי לסייע בשמירה על דרכי האוויר פתוחות במהלך השינה. זהו טיפול מאושר ויעיל, ללא רעש וללא צינורות.</p>"
            "<p>התאמה נכונה של הפתרון לצרכים האישיים שלך תאפשר שינה נוחה, בריאה ורציפה יותר, ותתרום לערנות, ריכוז ותפקוד טוב יותר במהלך היום ולאורך זמן.</p>"
        ),
    }

    # Allow caller to override any of these later
    return build_level1_placeholder_context(lang="he", overrides=overrides)


def generate_level1_hebrew_narrative_with_bedrock(patient_quiz_json: Dict[str, Any], risk_category: str, patient_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Call the existing Bedrock Claude pipeline and ask it to generate:
    - alert_text
    - recommendations (2 items)
    - reported_symptoms
    Returns a dict on success or None on failure.
    """
    from flask_app.config.bedrock_config import query_bedrock_claude_enhanced

    def _sanitize_for_llm(payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Remove unnecessary PII and shrink payload for the LLM call.
        Keeps raw answers + evaluation summary + human-readable Q&A (minus email/phone).
        """
        sanitized = dict(payload or {})
        raw = dict((sanitized.get("raw_answers") or {}))

        for k in ["EMAIL", "PHONE", "DEMO_EMAIL", "DEMO_PHONE"]:
            raw.pop(k, None)
        sanitized["raw_answers"] = raw

        ea = sanitized.get("enhanced_answers") or {}
        if isinstance(ea, dict):
            qa = ea.get("questions_and_answers")
            if isinstance(qa, list):
                filtered = []
                for item in qa:
                    if not isinstance(item, dict):
                        continue
                    qid = str(item.get("question_id", "")).upper()
                    if qid in {"EMAIL", "PHONE", "DEMO_EMAIL", "DEMO_PHONE"}:
                        continue
                    filtered.append(item)
                ea = dict(ea)
                ea["questions_and_answers"] = filtered
            sanitized["enhanced_answers"] = ea

        return sanitized

    safe_payload = _sanitize_for_llm(patient_quiz_json)

    prompt = f"""You are a medical communication assistant writing a patient-facing Level-1 sleep-apnea screening summary in Hebrew.

Your task is to generate ONLY the missing narrative sections for the report, based STRICTLY on the provided quiz JSON.

OUTPUT MUST BE HEBREW ONLY.
DO NOT include English, transliteration, or explanations.

────────────────────────
HARD RULES (MANDATORY)
────────────────────────
- Use ONLY facts explicitly present in PATIENT_QUIZ_JSON.
- If a symptom, condition, goal, or history item is NOT present in the JSON, DO NOT mention it.
- If the JSON explicitly says "no"/"never"/empty for a symptom or factor, DO NOT mention it.
- This is screening only:
  - Do NOT diagnose.
  - Do NOT promise treatment outcomes.
  - Do NOT use absolute or definitive medical language.
- Tone:
  - Clear, empathetic, calm.
  - Non-alarming.
  - Increase urgency slightly ONLY if risk indicates high concern (e.g., high risk or diagnosed-not-treated).
- Do NOT mention AI, models, prompts, or automation.
- Do NOT add disclaimers or legal language.
- Do NOT ask questions.
- Do NOT recommend specific medications.
- Do NOT recommend specific treatment devices (e.g., do not prescribe CPAP/OAT as treatment).
  - Allowed next steps: sleep evaluation/consultation and sleep testing (home sleep test or lab sleep study) when supported by JSON.
- Do NOT use bullet points or line breaks inside any text field.
- Do NOT use colons “:” inside the Hebrew sentences (JSON syntax colons are fine).
- Do NOT use emojis.
- Output JSON only with the exact keys specified below.

────────────────────────
INPUT (SOURCE OF TRUTH)
────────────────────────
PATIENT_QUIZ_JSON:
{json.dumps(safe_payload, ensure_ascii=False)}

Risk category (must align with JSON; provided for convenience):
{risk_category}

IMPORTANT:
If evaluation_summary.outcome_title or outcome_body look like message IDs/keys (e.g., "MSG_..."),
do NOT treat them as patient facts. Use them only if they contain real natural-language content.

────────────────────────
ANTI-GENERIC PERSONALIZATION RULES (CRITICAL)
────────────────────────
1) Evidence requirement:
   - Every sentence must be supported by at least one explicit fact in the JSON.
   - Do NOT introduce symptoms or conditions not present.

2) Personalization anchors (use when available):
   - evaluation_summary.risk_band / risk_label
   - evaluation_summary.red_flags (may increase urgency wording only)
   - raw_answers (sleep quality, sleepiness, awakenings, choking/gasping, snoring, naps, comorbidities, diagnosis/treatment history)
   - patient goal(s) if present

3) Recommendation grounding:
   - Each of the 2 recommendations must reference at least one factor actually present in JSON.
   - Do NOT include generic advice that cannot be justified by the JSON.

────────────────────────
TASK
────────────────────────
Generate the following fields:

1) alert_text
- 1–2 Hebrew sentences.
- Anchored to specific JSON facts.
- Must NOT mention denied symptoms.

2) recommendations
- EXACTLY 2 Hebrew strings in an array.
- Each string = exactly 1 sentence (no line breaks).
- First = lifestyle-focused AND supported by JSON.
- Second = actionable next step (sleep test or consultation) AND supported by JSON.

3) reported_symptoms
- 3–5 Hebrew sentences (minimum 2 if limited data).
- Narrative paragraph only (no lists).
- Reference ONLY symptoms, conditions, and goals present in JSON.
- End with ONE sentence connecting to the patient’s goal IF a goal exists.
- If no goal exists, omit the goal sentence.

────────────────────────
OUTPUT FORMAT (STRICT)
────────────────────────
Return JSON ONLY, exactly in this structure:

{{
  "alert_text": "…",
  "recommendations": ["…", "…"],
  "reported_symptoms": "…"
}}
"""

    result = query_bedrock_claude_enhanced(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=650,
        temperature=0.1,
        patient_id=patient_id,
        endpoint="level1_report_hebrew",
        use_knowledge_base=False,
    )

    if not result or not result.get("success"):
        return None

    text = result.get("response") or ""
    if not isinstance(text, str):
        return None

    # Robust JSON extraction (handles accidental wrapping text)
    import re
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("alert_text"), str):
        return None
    recs = data.get("recommendations")
    if not (isinstance(recs, list) and len(recs) == 2 and all(isinstance(x, str) for x in recs)):
        return None
    if not isinstance(data.get("reported_symptoms"), str):
        return None
    return {
        "alert_text": data["alert_text"].strip(),
        "recommendations": [recs[0].strip(), recs[1].strip()],
        "reported_symptoms": data["reported_symptoms"].strip(),
    }


def html_to_pdf_bytes(html: str) -> bytes:
    """
    Convert HTML string to PDF bytes using Playwright (preferred) or xhtml2pdf (fallback).
    Playwright renders HTML exactly as it appears in browser with proper Hebrew font support.
    """
    # Try Playwright first (best for Hebrew/RTL support)
    is_hebrew = ('lang="he"' in (html or "")) or ('dir="rtl"' in (html or ""))
    try:
        from playwright.sync_api import sync_playwright
        
        current_app.logger.info("Attempting PDF generation with Playwright (best Hebrew support)")
        
        with sync_playwright() as p:
            # Launch browser
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # Set content with proper encoding
            # Use system fonts (Noto Sans Hebrew is installed on the host) and convert
            # /flask_static/... references to file:// paths so Chromium can load assets.
            base_dir = os.path.dirname(os.path.dirname(__file__))  # flask_app/
            flask_static_dir = os.path.join(base_dir, "flask_static")
            
            import re
            
            html_for_pdf = html

            # Strip broken @font-face rules from the template (they point to /flask_static/fonts/,
            # which doesn't exist in this repo). We'll rely on system fonts instead.
            html_for_pdf = re.sub(
                r"@font-face\s*\{[\s\S]*?\}",
                "",
                html_for_pdf,
                flags=re.IGNORECASE,
            )

            # Convert absolute /flask_static/... to file://... so Chromium can load images.
            # Works for both HTML attrs (src/href) and CSS url("/flask_static/..").
            html_for_pdf = html_for_pdf.replace(
                '"/flask_static/',
                f'"file://{flask_static_dir}/',
            )
            html_for_pdf = html_for_pdf.replace(
                "'/flask_static/",
                f"'file://{flask_static_dir}/",
            )

            # Playwright/Chromium can refuse to load local file:// images when using page.set_content(),
            # which shows as a "broken image" icon in the generated PDF. To make the PDF self-contained
            # and reliable, embed any local file:// images as data: URIs.
            try:
                import base64

                def _mime_for_path(p: str) -> str:
                    lp = (p or "").lower()
                    if lp.endswith(".png"):
                        return "image/png"
                    if lp.endswith(".jpg") or lp.endswith(".jpeg"):
                        return "image/jpeg"
                    if lp.endswith(".webp"):
                        return "image/webp"
                    return "application/octet-stream"

                # Collect unique local image paths referenced in src="file://..."
                img_paths = set(
                    m.group(1)
                    for m in re.finditer(r'src=["\']file://([^"\']+)["\']', html_for_pdf)
                )
                for abs_path in img_paths:
                    if not abs_path.startswith(flask_static_dir):
                        continue
                    if not os.path.exists(abs_path):
                        continue
                    # Guardrail: don't embed extremely large files
                    try:
                        if os.path.getsize(abs_path) > 12 * 1024 * 1024:
                            current_app.logger.warning(f"Skipping embed for large image: {abs_path}")
                            continue
                    except Exception:
                        pass

                    with open(abs_path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                    mime = _mime_for_path(abs_path)
                    data_uri = f"data:{mime};base64,{b64}"
                    html_for_pdf = html_for_pdf.replace(f'src="file://{abs_path}"', f'src="{data_uri}"')
                    html_for_pdf = html_for_pdf.replace(f"src='file://{abs_path}'", f"src='{data_uri}'")
                    current_app.logger.info(f"Embedded image for PDF: {abs_path}")
            except Exception as e:
                current_app.logger.warning(f"Could not embed local images for PDF: {e}")
            
            # Force a Hebrew-capable system font in case the CSS is overridden.
            # This avoids the "tofu"/square blocks when Chromium falls back to a font without Hebrew glyphs.
            page.set_content(html_for_pdf, wait_until="load")
            page.add_style_tag(
                content='body{font-family:"Noto Sans Hebrew","DejaVu Sans","Arial Unicode MS",sans-serif !important;}'
            )

            # Ensure web fonts are fully loaded before generating the PDF.
            # If we generate too early, Chromium may fall back to a non-Hebrew font (squares/blocks).
            try:
                page.evaluate(
                    """async () => {
                      if (document.fonts && document.fonts.ready) {
                        await document.fonts.ready;
                      }
                    }"""
                )
            except Exception:
                # If the Font Loading API isn't available for some reason, keep going.
                pass
            page.wait_for_timeout(250)
            
            # Generate PDF with minimal margins to fit on one page
            pdf_bytes = page.pdf(
                format="A4",
                print_background=True,
                prefer_css_page_size=False,  # Use A4 size
                margin={
                    "top": "0.3in",
                    "right": "0.3in",
                    "bottom": "0.3in",
                    "left": "0.3in"
                }
            )
            
            browser.close()
            current_app.logger.info(f"PDF generated successfully with Playwright, size: {len(pdf_bytes)} bytes")
            return pdf_bytes
            
    except ImportError:
        current_app.logger.warning("Playwright not available, falling back to xhtml2pdf")
    except Exception as e:
        current_app.logger.warning(f"Playwright PDF generation failed: {e}, falling back to xhtml2pdf")
        import traceback
        current_app.logger.warning(traceback.format_exc())

        # For Hebrew/RTL, xhtml2pdf often produces tofu blocks. Do not silently fall back.
        if is_hebrew:
            raise
    
    # Fallback to xhtml2pdf if Playwright fails or is not available
    current_app.logger.info("Using xhtml2pdf fallback for PDF generation")
    buffer = io.BytesIO()
    html_bytes = html.encode("utf-8") if isinstance(html, str) else html

    # Try to register a Hebrew-supporting font if available
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        # Try to find a Hebrew-supporting font (prioritize Noto Sans Hebrew)
        hebrew_font_paths = [
            "/usr/share/fonts/truetype/noto/NotoSansHebrew-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/Helvetica.ttc",  # macOS
        ]
        
        font_registered = False
        for font_path in hebrew_font_paths:
            if os.path.exists(font_path):
                try:
                    pdfmetrics.registerFont(TTFont("HebrewFont", font_path))
                    font_registered = True
                    current_app.logger.info(f"Registered Hebrew font: {font_path}")
                    break
                except Exception as e:
                    current_app.logger.warning(f"Could not register font {font_path}: {e}")
                    continue
        
        if not font_registered:
            # Fallback to DejaVu if available
            font_path = _get_dejavu_font_path()
            if font_path:
                pdfmetrics.registerFont(TTFont("DejaVuSans", font_path))
    except Exception as e:
        current_app.logger.warning(f"Could not register fonts for PDF: {e}")

    # Generate PDF - use default encoding and let xhtml2pdf handle RTL
    pisa_status = pisa.CreatePDF(
        src=html_bytes,
        dest=buffer,
        encoding="utf-8",
        show_error_as_pdf=False,
        link_callback=_pisa_link_callback,
    )
    if pisa_status.err:
        current_app.logger.error(f"PDF generation errors: {pisa_status.err}")
        # Log warnings but don't fail - sometimes warnings are non-critical
        if pisa_status.warn:
            current_app.logger.warning(f"PDF generation warnings: {pisa_status.warn}")
        # Only raise if there are actual errors (not just warnings)
        if pisa_status.err and len(str(pisa_status.err)) > 0:
            raise RuntimeError(f"PDF generation failed: {pisa_status.err}")

    return buffer.getvalue()


