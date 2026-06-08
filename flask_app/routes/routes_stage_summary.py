"""
Stage Summary routes blueprint.

Phase 1 provides a scaffolded API route that exposes the manifest structure
so downstream development and integration testing can proceed in parallel
with the existing operational summary workflow.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, current_app, render_template, request, abort
from flask_login import login_required, current_user

from flask_app.config.stage_summary_manifest import get_stage_summary_manifest
from flask_app.models import Patient, PatientStageSummaryCache, VizBrizQuiz
from flask_app.services.stage_summary_service import (
    evaluate_stage_completion,
    generate_stage_ai_guidance,
    generate_all_stages_ai_guidance_batch,
    generate_overall_workflow_summary,
    get_cached_ai_summary,
    save_cached_ai_summary,
    invalidate_cache,
)
from flask_app.services.manifest_service import ManifestService
import logging

logger = logging.getLogger(__name__)

stage_summary_bp = Blueprint("stage_summary", __name__, url_prefix="/vizbriz")


def _build_stage_payload(
    patient_id: int,
    manifest_entry: Dict[str, Any],
    use_evaluation: bool = False,
    use_ai_guidance: bool = False,
    all_stages_status: Dict[str, Dict[str, Any]] = None,
    all_stages_manifest: List[Dict[str, Any]] = None,
    pre_generated_ai_comment: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build stage payload with completion status and optional AI guidance.
    
    Args:
        patient_id: Patient ID for evaluation
        manifest_entry: Stage manifest entry
        use_evaluation: If True, use real evaluation; if False, return placeholder
        use_ai_guidance: If True, generate AI guidance comments
        all_stages_status: Dict of all stages' completion status for AI context
    """
    
    if use_evaluation:
        # Use pre-evaluated status if available, otherwise evaluate now
        stage_key = manifest_entry.get("key")
        if all_stages_status and stage_key in all_stages_status:
            completion_result = all_stages_status[stage_key]
        else:
            completion_result = evaluate_stage_completion(patient_id, manifest_entry, all_stages_status)
        
        # Use pre-generated AI comment if available, otherwise generate individually
        ai_comment = None
        if use_ai_guidance:
            if pre_generated_ai_comment is not None:
                ai_comment = pre_generated_ai_comment
            else:
                # Fallback to individual generation if batch didn't work
                ai_comment = generate_stage_ai_guidance(
                    patient_id,
                    manifest_entry,
                    completion_result,
                    all_stages_status,
                    all_stages_manifest
                )
        
        return {
            "key": manifest_entry["key"],
            "status": completion_result["status"],
            "completed_on": completion_result.get("completed_on"),
            "metadata": completion_result.get("metadata", {}),
            "guidance": manifest_entry["guidance"],
            "ai_comment": ai_comment,
            "title": manifest_entry["title"],
            "description": manifest_entry["description"],
            "prerequisites": manifest_entry.get("prerequisites", []),
            "skip_if": manifest_entry.get("skip_if", []),
            "optional": manifest_entry.get("optional", False),
            "skip_reason": completion_result.get("skip_reason"),
            "skipped_by": completion_result.get("skipped_by"),
            "completion_rule": manifest_entry.get("completion_rule", ""),
            "completion_type": manifest_entry.get("completion_type", ""),
            "completion_args": manifest_entry.get("completion_args", {}),
        }
    else:
        # Placeholder for Phase 1
        return {
            "key": manifest_entry["key"],
            "status": "pending",
            "completed_on": None,
            "guidance": manifest_entry["guidance"],
            "ai_comment": None,
            "title": manifest_entry["title"],
            "description": manifest_entry["description"],
            "prerequisites": manifest_entry.get("prerequisites", []),
            "skip_if": manifest_entry.get("skip_if", []),
            "optional": manifest_entry.get("optional", False),
            "skip_reason": None,
            "skipped_by": None,
            "completion_rule": manifest_entry.get("completion_rule", ""),
            "completion_type": manifest_entry.get("completion_type", ""),
            "completion_args": manifest_entry.get("completion_args", {}),
        }


@stage_summary_bp.route(
    "/api/stage_summary_manifest/<int:patient_id>", methods=["GET"]
)
@login_required
def get_stage_summary(patient_id: int):
    """
    Return the stage summary manifest for the requested patient.

    Phase 1 returns placeholder completion data but keeps the response schema
    aligned with the long-term contract so frontend work can progress without
    blocking on backend logic.
    """

    feature_flag_enabled = current_app.config.get(
        "FEATURE_FLAG_STAGE_SUMMARY", True
    )
    if not feature_flag_enabled:
        return jsonify({"message": "Stage summary manifest disabled"}), 404

    # Validate patient exists and user has access
    patient = Patient.query.get_or_404(patient_id)
    if not current_user.can_access_patient(patient):
        return jsonify({"error": "Access denied"}), 403

    # Check if we should use real evaluation (Phase 2) or placeholders (Phase 1)
    # Allow override via query parameter for testing
    use_eval_param = request.args.get("use_evaluation")
    if use_eval_param is not None:
        use_real_evaluation = use_eval_param.lower() == "true"
    else:
        use_real_evaluation = current_app.config.get("STAGE_SUMMARY_USE_EVALUATION", False)
    
    # Check if AI guidance should be generated
    # Allow override via query parameter for testing
    use_ai_param = request.args.get("use_ai_guidance")
    if use_ai_param is not None:
        use_ai_guidance = use_ai_param.lower() == "true"
    else:
        use_ai_guidance = current_app.config.get("STAGE_SUMMARY_USE_AI_GUIDANCE", False)
    
    manifest_entries: List[Dict[str, Any]] = get_stage_summary_manifest()
    
    # Evaluate all stages if using real evaluation (needed for AI context and for actual status)
    # First pass: evaluate all stages without skip_if checks
    all_stages_status = {}
    if use_real_evaluation:
        for entry in manifest_entries:
            stage_key = entry["key"]
            completion_result = evaluate_stage_completion(patient_id, entry, all_stages_status)
            all_stages_status[stage_key] = completion_result
    
    # Second pass: re-evaluate stages that might be skipped now that we have all statuses
    if use_real_evaluation:
        for entry in manifest_entries:
            stage_key = entry["key"]
            # Re-evaluate if stage has skip_if conditions
            if entry.get("skip_if"):
                completion_result = evaluate_stage_completion(patient_id, entry, all_stages_status)
                all_stages_status[stage_key] = completion_result
    
    # Check cache first, then generate AI guidance if needed
    all_stages_ai_comments = {}
    overall_summary_data = None
    use_cache = request.args.get("use_cache", "true").lower() == "true"
    force_refresh = request.args.get("force_refresh", "false").lower() == "true"
    
    if use_ai_guidance and use_real_evaluation:
        # Check cache first (unless force refresh)
        cached_data = None
        if use_cache and not force_refresh:
            cached_data = get_cached_ai_summary(patient_id, all_stages_status)
        
        if cached_data:
            # Use cached data
            all_stages_ai_comments = cached_data.get("stage_comments", {})
            overall_summary_data = cached_data.get("overall_summary")
        else:
            # Generate new AI guidance
            all_stages_ai_comments = generate_all_stages_ai_guidance_batch(
                patient_id,
                manifest_entries,
                all_stages_status
            )
            
            # Generate overall summary
            overall_summary_data = generate_overall_workflow_summary(
                patient_id,
                manifest_entries,
                all_stages_status
            )
            
            # Save to cache
            if use_cache:
                save_cached_ai_summary(
                    patient_id,
                    overall_summary_data,
                    all_stages_ai_comments,
                    all_stages_status
                )
    
    stages_payload = [
        _build_stage_payload(
            patient_id,
            entry,
            use_evaluation=use_real_evaluation,
            use_ai_guidance=use_ai_guidance,
            all_stages_status=all_stages_status if use_ai_guidance else None,
            all_stages_manifest=manifest_entries if use_ai_guidance else None,
            pre_generated_ai_comment=all_stages_ai_comments.get(entry["key"])
        )
        for entry in manifest_entries
    ]
    
    # Calculate patient status based on actual stage evaluation (not old manifest)
    # This ensures consistency between header progress and AI summary
    if use_real_evaluation:
        completed_count = sum(1 for status in all_stages_status.values() if status.get("status") == "completed")
        total_count = len(manifest_entries)
        completion_percentage = round((completed_count / total_count * 100)) if total_count > 0 else 0
        
        # Find current stage (first incomplete stage that's not blocked)
        # For simplicity: next_stage is the first pending (incomplete, non-skipped) stage
        # This is what needs to be done next
        current_stage = None
        next_stage = None
        
        for i, entry in enumerate(manifest_entries):
            stage_status = all_stages_status.get(entry["key"], {}).get("status", "pending")
            
            # Skip skipped stages
            if stage_status == "skipped":
                logger.info(f"Skipping stage {entry['key']} ({entry['title']}) - marked as skipped in stage summary")
                continue
            
            # Skip completed stages
            if stage_status == "completed":
                logger.info(f"Skipping stage {entry['key']} ({entry['title']}) - already completed")
                continue
            
            # Found the first pending (incomplete, non-skipped) stage - this is the next stage
            next_stage = {
                "stage_number": i + 1,
                "stage_name": entry["title"],
                "stage_key": entry["key"]
            }
            logger.info(f"Selected next stage: {entry['key']} ({entry['title']})")
            # For compatibility, also set as current_stage
            current_stage = next_stage
            break
        
        # If all stages completed, current is the last stage
        if not current_stage and manifest_entries:
            last_entry = manifest_entries[-1]
            current_stage = {
                "stage_number": len(manifest_entries),
                "stage_name": last_entry["title"],
                "stage_key": last_entry["key"]
            }
        
        patient_status_response = {
            "current_stage_number": current_stage["stage_number"] if current_stage else total_count,
            "current_stage_name": current_stage["stage_name"] if current_stage else "Workflow Complete",
            "current_stage_key": current_stage["stage_key"] if current_stage else None,
            "next_stage_number": next_stage["stage_number"] if next_stage else None,
            "next_stage_name": next_stage["stage_name"] if next_stage else "Workflow Complete",
            "next_stage_key": next_stage["stage_key"] if next_stage else None,
            "workflow_completion_percentage": completion_percentage,
            "completed_stages_count": completed_count,
            "total_stages_count": total_count,
        }
    else:
        # Fallback to old manifest system if not using real evaluation
        patient_status = ManifestService.get_patient_current_and_next_stage(patient_id)
        patient_status_response = None
        if patient_status:
            completion_percentage = round(patient_status.get("workflow_completion_percentage", 0))
            patient_status_response = {
                "current_stage_number": patient_status.get("current_stage_number"),
                "current_stage_name": patient_status.get("current_stage_name"),
                "current_stage_key": patient_status.get("current_stage_key"),
                "next_stage_number": patient_status.get("next_stage_number"),
                "next_stage_name": patient_status.get("next_stage_name"),
                "next_stage_key": patient_status.get("next_stage_key"),
                "workflow_completion_percentage": completion_percentage,
                "completed_stages_count": patient_status.get("completed_stages_count"),
                "total_stages_count": patient_status.get("total_stages_count"),
            }
    
    # Get patient details
    from datetime import date
    patient_details = {
        "name": patient.name or "N/A",
        "email": patient.email or "N/A",
        "phone": patient.phone or "N/A",
        "gender": patient.gender or "N/A",
        "age": None,
    }
    
    # Calculate age from date of birth
    if patient.dob:
        today = date.today()
        age = today.year - patient.dob.year - ((today.month, today.day) < (patient.dob.month, patient.dob.day))
        patient_details["age"] = age

    # Check if data came from cache
    cached_data = get_cached_ai_summary(patient_id, all_stages_status) if use_ai_guidance and use_real_evaluation else None
    is_cached = cached_data is not None and not force_refresh
    
    response = {
        "patient_id": patient_id,
        "patient_details": patient_details,
        "stages": stages_payload,
        "manifest_size": len(stages_payload),
        "phase": "phase_2_evaluation" if use_real_evaluation else "phase_1_placeholder",
        "ai_guidance_enabled": use_ai_guidance,
        "overall_summary": overall_summary_data,  # Now includes summary text and metadata
        "patient_status": patient_status_response,  # Overall patient status (current stage, completion %, etc.)
        "cache_info": {
            "is_cached": is_cached,
            "cached_at": cached_data.get("cached_at") if cached_data else None
        } if use_ai_guidance else None,
    }
    return jsonify(response), 200


@stage_summary_bp.route("/stage_summary_test", methods=["GET"])
@login_required
def stage_summary_test_page():
    """
    Test page for stage summary manifest.
    Allows testing the stage summary API with different patient IDs.
    """
    patient_id = request.args.get("patient_id", type=int)
    return render_template("stage_summary_test.html", patient_id=patient_id)


@stage_summary_bp.route("/stage_summary_dashboard", methods=["GET"])
@login_required
def stage_summary_dashboard():
    """
    Dashboard page showing all cached stage summaries.
    Lightweight view - no Bedrock calls, only cached data.
    """
    return render_template("stage_summary_dashboard.html")


@stage_summary_bp.route("/stage_summary_visualization", methods=["GET"])
@login_required
def stage_summary_visualization():
    """
    Visualization page showing stage relationships (prerequisites, skip_if, etc.)
    """
    return render_template("stage_summary_visualization.html")


@stage_summary_bp.route("/api/stage_summary_cache/invalidate/<int:patient_id>", methods=["POST"])
@login_required
def invalidate_stage_summary_cache(patient_id: int):
    """Invalidate cached AI summary for a patient"""
    patient = Patient.query.get_or_404(patient_id)
    if not current_user.can_access_patient(patient):
        return jsonify({"error": "Access denied"}), 403
    
    invalidate_cache(patient_id)
    return jsonify({"success": True, "message": "Cache invalidated"}), 200


@stage_summary_bp.route("/api/stage_summary_manifest_structure", methods=["GET"])
@login_required
def get_stage_summary_manifest_structure():
    """
    Returns the complete manifest structure with all relationships.
    Useful for visualization and understanding stage dependencies.
    """
    manifest_entries = get_stage_summary_manifest()
    
    # Build relationship map
    relationships = {
        "stages": [],
        "prerequisite_map": {},  # stage_key -> [prerequisite_keys]
        "skip_if_map": {},  # stage_key -> [skip_if_keys]
        "optional_stages": []  # List of optional stage keys
    }
    
    for entry in manifest_entries:
        stage_key = entry.get("key")
        relationships["stages"].append({
            "key": stage_key,
            "title": entry.get("title"),
            "description": entry.get("description"),
            "prerequisites": entry.get("prerequisites", []),
            "skip_if": entry.get("skip_if", []),
            "optional": entry.get("optional", False),
            "completion_type": entry.get("completion_type"),
            "completion_rule": entry.get("completion_rule", ""),
            "completion_args": entry.get("completion_args", {}),
            "data_source": entry.get("data_source", ""),
            "completion_field": entry.get("completion_field", ""),
            "guidance": entry.get("guidance", ""),
        })
        
        if entry.get("prerequisites"):
            relationships["prerequisite_map"][stage_key] = entry.get("prerequisites", [])
        
        if entry.get("skip_if"):
            relationships["skip_if_map"][stage_key] = entry.get("skip_if", [])
        
        if entry.get("optional"):
            relationships["optional_stages"].append(stage_key)
    
    return jsonify(relationships), 200


@stage_summary_bp.route("/api/stage_summary_list", methods=["GET"])
@login_required
def get_stage_summary_list():
    """
    Lightweight dashboard endpoint - returns list of patients with cached summaries.
    No Bedrock calls - only cached data.
    """
    from flask_app.models import PatientStageSummaryCache
    
    # Get all cached summaries for patients the user can access
    if current_user.role == 'admin':
        cached_summaries = PatientStageSummaryCache.query.filter_by(is_valid=True).all()
    else:
        # Get accessible patient IDs
        accessible_patients = current_user.get_accessible_patients_new_system()
        patient_ids = [p.id for p in accessible_patients]
        cached_summaries = PatientStageSummaryCache.query.filter(
            PatientStageSummaryCache.patient_id.in_(patient_ids),
            PatientStageSummaryCache.is_valid == True
        ).all()
    
    results = []
    for cache in cached_summaries:
        patient = cache.patient
        if not current_user.can_access_patient(patient):
            continue
        
        # Extract summary preview (first 200 chars)
        summary_text = ""
        if cache.overall_summary_metadata:
            summary_text = cache.overall_summary_metadata.get("summary", cache.overall_summary or "")
        else:
            summary_text = cache.overall_summary or ""
        
        results.append({
            "patient_id": patient.id,
            "patient_name": patient.name or "N/A",
            "summary_preview": summary_text[:200] + "..." if len(summary_text) > 200 else summary_text,
            "cached_at": cache.updated_at.isoformat() if cache.updated_at else None,
            "has_summary": bool(summary_text),
        })
    
    return jsonify({
        "count": len(results),
        "summaries": results
    }), 200


@stage_summary_bp.route("/api/patient/<int:patient_id>/email-logs", methods=["GET"])
@login_required
def get_patient_email_logs(patient_id: int):
    """Get recent email logs for a patient"""
    try:
        from flask_app.models import EmailLog
        
        # Verify patient exists
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({'success': False, 'message': 'Patient not found'}), 404
        
        # Fetch recent email logs (limit to 5 most recent)
        email_logs = EmailLog.query.filter_by(patient_id=patient_id)\
            .order_by(EmailLog.sent_at.desc())\
            .limit(5)\
            .all()
        
        data = [{
            'id': log.id,
            'subject': log.subject or 'No Subject',
            'sent_at': log.sent_at.isoformat() if log.sent_at else None,
            'recipient_email': log.recipient_email or 'N/A',
            'sender_email': log.sender_email or 'N/A',
            'status': log.status or 'unknown',
            'message_preview': (log.message_content[:200] + '...') if log.message_content and len(log.message_content) > 200 else (log.message_content or '')
        } for log in email_logs]
        
        return jsonify({'success': True, 'email_logs': data})
    except Exception as e:
        current_app.logger.error(f"Error fetching email logs for patient {patient_id}: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500


@stage_summary_bp.route("/api/patient/<int:patient_id>/send-email", methods=["POST"])
@login_required
def send_patient_email(patient_id: int):
    """Send email to patient using SendGrid"""
    try:
        from flask_app.routes.file_management_routes import send_email_with_sendgrid
        
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({'success': False, 'message': 'Patient not found'}), 404
        
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'message': 'Access denied'}), 403
        
        data = request.get_json()
        recipient_email = data.get('recipient_email', '').strip()
        subject = data.get('subject', '').strip()
        message = data.get('message', '').strip()
        
        if not recipient_email:
            return jsonify({'success': False, 'message': 'Recipient email is required'}), 400
        if not subject:
            return jsonify({'success': False, 'message': 'Subject is required'}), 400
        if not message:
            return jsonify({'success': False, 'message': 'Message is required'}), 400
        
        # Convert plain text message to HTML
        html_content = message.replace('\n', '<br>')
        
        # Send email via SendGrid
        sender_id = current_user.id if hasattr(current_user, 'id') else None
        sender_type = 'dentist' if hasattr(current_user, 'role') and current_user.role == 'dentist' else 'admin'
        
        success = send_email_with_sendgrid(
            recipient_email=recipient_email,
            subject=subject,
            html_content=html_content,
            text_content=message,
            patient_id=patient_id,
            sender_id=sender_id,
            email_type='manual',
            sender_type=sender_type,
            skip_db_logging=False
        )
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Email sent successfully'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to send email'
            }), 500
            
    except Exception as e:
        current_app.logger.error(f"Error sending email for patient {patient_id}: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500


@stage_summary_bp.route("/external-report-test", methods=["GET"])
def external_report_test():
    """Render a lightweight page to test the external report API."""

    patient_id = request.args.get("patient_id", default=74173, type=int)
    quiz_submission = (
        VizBrizQuiz.query.filter_by(user_id=patient_id)
        .order_by(VizBrizQuiz.created_at.desc())
        .first()
    )

    if not quiz_submission:
        abort(404, description=f"No VizBriz quiz submission found for patient {patient_id}")

    try:
        quiz_payload = json.loads(quiz_submission.quiz_input or "{}")
    except (TypeError, ValueError):
        # Fallback: wrap raw text in an object to keep JSON structure
        quiz_payload = {"quiz_input": quiz_submission.quiz_input}

    payload = quiz_payload
    payload_pretty = json.dumps(payload, indent=2, ensure_ascii=False)

    return render_template(
        "stage_summary_external_report_test.html",
        patient_id=patient_id,
        quiz_id=quiz_submission.id,
        payload=payload,
        payload_pretty=payload_pretty,
    )



