"""
L3 Autoreport routes — observation builder, OpenAI conclusion, merged PDF output.
"""

import logging
import os
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from flask_app.config.l3_autoreport_observations import L3_AUTOREPORT_OBSERVATIONS
from flask_app.models import Patient
from flask_app.routes.level3_report_routes import _generate_presigned_url, _load_canonical_data
from flask_app.services.l3_autoreport_service import (
    build_l3_autoreport_pdf,
    generate_conclusion_openai,
    get_clinical_pictures,
    get_latest_l2_report,
    get_latest_l3_report,
    prefill_observations_from_canonical,
    store_l3_autoreport,
)

logger = logging.getLogger(__name__)

l3_autoreport_bp = Blueprint("l3_autoreport", __name__, url_prefix="/reports")


@l3_autoreport_bp.route("/l3-autoreport", methods=["GET"])
@login_required
def l3_autoreport_page():
    patient_id = request.args.get("patient_id", type=int)
    return render_template(
        "l3_autoreport.html",
        patient_id=patient_id,
        observations_config=L3_AUTOREPORT_OBSERVATIONS,
    )


@l3_autoreport_bp.route("/api/l3_autoreport/patient/<int:patient_id>/context", methods=["GET"])
@login_required
def l3_autoreport_context(patient_id: int):
    try:
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({"success": False, "error": "Patient not found"}), 404
        if not current_user.can_access_patient(patient):
            return jsonify({"success": False, "error": "Access denied"}), 403

        canonical = _load_canonical_data(patient_id)
        prefill = prefill_observations_from_canonical(canonical)

        return jsonify(
            {
                "success": True,
                "patient_id": patient_id,
                "patient_name": patient.name,
                "clinical_pictures": get_clinical_pictures(patient_id),
                "latest_l2": get_latest_l2_report(patient_id),
                "latest_l3": get_latest_l3_report(patient_id),
                "prefill_observations": prefill,
                "observations_config": L3_AUTOREPORT_OBSERVATIONS,
            }
        )
    except Exception as e:
        logger.error("l3_autoreport context error: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@l3_autoreport_bp.route("/api/l3_autoreport/generate_conclusion", methods=["POST"])
@login_required
def l3_autoreport_generate_conclusion():
    try:
        data = request.get_json() or {}
        patient_id = data.get("patient_id")
        observations = data.get("observations") or {}
        if not patient_id:
            return jsonify({"success": False, "error": "patient_id is required"}), 400

        patient = Patient.query.get(patient_id)
        if not patient or not current_user.can_access_patient(patient):
            return jsonify({"success": False, "error": "Access denied"}), 403

        canonical = _load_canonical_data(patient_id)
        patient_context = {
            "patient_id": patient_id,
            "demographics": canonical.get("demographics") or {},
            "sleep_study": canonical.get("sleep_study") or {},
        }

        conclusion, err = generate_conclusion_openai(
            patient_id, observations, patient_context=patient_context
        )
        if err:
            return jsonify({"success": False, "error": err}), 400

        return jsonify({"success": True, "conclusion": conclusion})
    except Exception as e:
        logger.error("l3_autoreport conclusion error: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@l3_autoreport_bp.route("/api/l3_autoreport/generate", methods=["POST"])
@login_required
def l3_autoreport_generate():
    """Build merged L3 autoreport PDF (L2 + L3 + observations section) and save to patient reports."""
    try:
        data = request.get_json() or {}
        patient_id = data.get("patient_id")
        observations = data.get("observations") or {}
        conclusion = (data.get("conclusion") or "").strip()
        save_to_reports = data.get("save_to_reports", True)
        include_l2 = data.get("include_l2", True)
        include_l3 = data.get("include_l3", True)

        if not patient_id:
            return jsonify({"success": False, "error": "patient_id is required"}), 400

        patient = Patient.query.get(patient_id)
        if not patient or not current_user.can_access_patient(patient):
            return jsonify({"success": False, "error": "Access denied"}), 403

        if not conclusion:
            conclusion, err = generate_conclusion_openai(
                patient_id,
                observations,
                patient_context={"patient_id": patient_id},
            )
            if err and not any((observations.get(o["key"]) or "").strip() for o in L3_AUTOREPORT_OBSERVATIONS):
                return jsonify(
                    {
                        "success": False,
                        "error": "Add observations and/or provide a conclusion before generating.",
                    }
                ), 400
            if err:
                return jsonify(
                    {
                        "success": False,
                        "error": f"Conclusion generation failed: {err}. Enter a conclusion manually or retry.",
                    }
                ), 400

        pdf_bytes, meta = build_l3_autoreport_pdf(
            patient_id,
            observations,
            conclusion,
            include_l2=include_l2,
            include_l3=include_l3,
        )

        response = {
            "success": True,
            "message": "L3 autoreport generated.",
            "merge_meta": meta,
            "pdf_size": len(pdf_bytes),
        }

        if save_to_reports:
            admin_file = store_l3_autoreport(patient_id, pdf_bytes)
            response["filename"] = admin_file.name
            response["s3_key"] = admin_file.s3_key
            response["pdf_url"] = _generate_presigned_url(admin_file.s3_key, expires_in=3600)
            response["admin_file_id"] = admin_file.id
        else:
            from flask_app.routes.level3_report_routes import _upload_to_s3

            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            temp_key = f"patients/{patient_id}/reports/level3/temp/L3_Autoreport_{ts}.pdf"
            _upload_to_s3(pdf_bytes, temp_key)
            response["s3_key"] = temp_key
            response["pdf_url"] = _generate_presigned_url(temp_key, expires_in=3600)
            response["is_temp"] = True

        return jsonify(response)
    except Exception as e:
        logger.error("l3_autoreport generate error: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
