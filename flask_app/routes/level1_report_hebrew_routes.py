import io
import os

from flask import Blueprint, jsonify, request, send_file
from flask_login import login_required

from flask_app.helpers.level1_report_hebrew import (
    build_level1_placeholder_context,
    build_level1_context_from_vizbriz_quiz,
    html_to_pdf_bytes,
    prepare_context_for_pdf,
    render_level1_report_html,
)
from flask_app.models import VizBrizQuiz


level1_report_hebrew_bp = Blueprint("level1_report_hebrew", __name__, url_prefix="/vizbriz")

# Serve Hebrew font files so the HTML frame renders the same as the PDF.
# We rely on system-installed fonts on the EC2 host to avoid adding large binaries to git.
@level1_report_hebrew_bp.route("/assets/fonts/<path:filename>", methods=["GET"])
def level1_assets_fonts(filename: str):
    allowed = {
        "NotoSansHebrew-Regular.ttf": "/usr/share/fonts/google-noto/NotoSansHebrew-Regular.ttf",
        "NotoSansHebrew-Bold.ttf": "/usr/share/fonts/google-noto/NotoSansHebrew-Bold.ttf",
    }
    font_path = allowed.get(filename)
    if not font_path or not os.path.exists(font_path):
        return jsonify({"success": False, "error": "Font not found"}), 404
    return send_file(
        font_path,
        mimetype="font/ttf",
        as_attachment=False,
        download_name=filename,
        max_age=60 * 60 * 24 * 7,  # 7 days
    )


@level1_report_hebrew_bp.route("/reports/level1/preview", methods=["GET"])
@login_required
def level1_preview_html():
    """
    HTML-first Level-1 report preview (supports RTL via ?lang=he).
    Intended for iframe embedding and fast iteration on layout/CSS.
    """
    overrides = {k: v for k, v in request.args.items()}
    lang = (overrides.get("lang") or "he").strip().lower()
    context = build_level1_placeholder_context(lang=lang, overrides=overrides)
    return render_level1_report_html(context)


@level1_report_hebrew_bp.route("/reports/level1/render", methods=["POST"])
def internal_level1_report_generate():
    """
    Internal report generator API (replacement for the external report API).
    Input: { "quiz_id": <int> }
    Output: { "pdf_url": "...", "frame_url": "..." }
    """
    data = request.get_json(silent=True) or {}
    quiz_id = data.get("quiz_id")
    if not quiz_id:
        return jsonify({"success": False, "error": "quiz_id is required"}), 400

    try:
        quiz_id_int = int(quiz_id)
    except Exception:
        return jsonify({"success": False, "error": "quiz_id must be an int"}), 400

    # The report is rendered on-demand by these routes
    return jsonify(
        {
            "pdf_url": f"/vizbriz/reports/level1/pdf/{quiz_id_int}",
            "frame_url": f"/vizbriz/reports/level1/frame/{quiz_id_int}",
        }
    )


@level1_report_hebrew_bp.route("/reports/level1/frame/<int:quiz_id>", methods=["GET"])
def level1_frame_html(quiz_id: int):
    """
    Public HTML frame for the generated report (patient-facing).
    Renders from the stored quiz JSON.
    If LLM narrative hasn't been generated yet, generates it on-demand.
    """
    quiz = VizBrizQuiz.query.get(quiz_id)
    if not quiz:
        return jsonify({"success": False, "error": "Quiz not found"}), 404
    
    # Hebrew-only: on-demand Bedrock narrative (English reports use evaluation_summary text from quiz JSON).
    try:
        import json

        lang = (getattr(quiz, "language", None) or "en").strip().lower()
        if lang.startswith("he"):
            ai_data = None
            if quiz.ai_response:
                ai_data = json.loads(quiz.ai_response) if isinstance(quiz.ai_response, str) else quiz.ai_response
            narrative = (ai_data or {}).get("level1_report_he") if isinstance(ai_data, dict) else None

            if not narrative or isinstance(narrative, dict) and narrative.get("level1_report_he_error"):
                from flask_app.helpers.level1_report_hebrew import generate_level1_hebrew_narrative_with_bedrock
                from flask_app.extensions import db

                quiz_payload = json.loads(quiz.quiz_input or "{}") if quiz.quiz_input else {}
                risk_category = (quiz_payload.get("evaluation_summary") or {}).get("risk_band") or quiz.risk_band or "other"

                narrative = generate_level1_hebrew_narrative_with_bedrock(
                    patient_quiz_json=quiz_payload,
                    risk_category=str(risk_category),
                    patient_id=quiz.user_id,
                )
                if narrative:
                    quiz.ai_response = json.dumps({"level1_report_he": narrative}, ensure_ascii=False)
                    db.session.commit()
    except Exception as e:
        from flask import current_app
        current_app.logger.error(f"Error generating LLM narrative for quiz {quiz_id}: {str(e)}")
        # Continue with placeholder text if generation fails

    context = build_level1_context_from_vizbriz_quiz(quiz)
    return render_level1_report_html(context)


@level1_report_hebrew_bp.route("/reports/level1/pdf/<int:quiz_id>", methods=["GET"])
def level1_pdf(quiz_id: int):
    """
    Public PDF for the generated report (patient-facing).
    Uses the same HTML template as the frame.
    """
    quiz = VizBrizQuiz.query.get(quiz_id)
    if not quiz:
        return jsonify({"success": False, "error": "Quiz not found"}), 404
    context = build_level1_context_from_vizbriz_quiz(quiz)
    html = render_level1_report_html(prepare_context_for_pdf(context))
    pdf_bytes = html_to_pdf_bytes(html)
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"Level_1_Report_Quiz_{quiz_id}.pdf",
    )


@level1_report_hebrew_bp.route("/reports/level1/hebrew-preview.pdf", methods=["GET"])
@login_required
def level1_hebrew_preview_pdf():
    """
    Generate a Hebrew Level-1 report preview PDF using placeholder text.
    Query params can override placeholders for layout testing.
    """
    try:
        overrides = {k: v for k, v in request.args.items()}
        context = build_level1_placeholder_context(lang="he", overrides=overrides)
        html = render_level1_report_html(prepare_context_for_pdf(context))
        pdf_bytes = html_to_pdf_bytes(html)

        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=False,
            download_name="level1_report_hebrew_preview.pdf",
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


