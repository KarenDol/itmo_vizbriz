"""
Patient Workflow Manifest Routes

This module handles the main patient workflow manifest route - a large, complex route
that displays the patient workflow interface with LLM guidance.
"""

import logging
import os
import json
import time
import boto3
import mysql.connector
from datetime import datetime, date, timedelta

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from flask_app import db
from flask_app.models import (
    Patient, AdminFile, DSO, Clinic, Dentist, EmailLog, dentist_clinic_association
)
from flask_app.config.manifest_config import get_manifest_definition

logger = logging.getLogger(__name__)


def _merge_level1_demographics(patient, canonical_data, patient_age):
    """
    Merge sex, age_years, BMI (and height/weight) from the latest VizBriz quiz into
    canonical_data['demographics'], using the same fields as the Level 1 report.

    When patient_age is still unknown ('N/A', 0, etc.), prefer quiz-reported age.
    """
    from flask_app.helpers.level1_report_hebrew import extract_level1_demographics_from_vizbriz_quiz
    from flask_app.models import VizBrizQuiz

    out_canonical = canonical_data
    out_age = patient_age
    try:
        quiz = (
            VizBrizQuiz.query.filter_by(user_id=patient.id)
            .order_by(VizBrizQuiz.created_at.desc())
            .first()
        )
        if not quiz and getattr(patient, "email", None):
            quiz = (
                VizBrizQuiz.query.filter_by(patient_email=patient.email)
                .order_by(VizBrizQuiz.created_at.desc())
                .first()
            )
        l1 = extract_level1_demographics_from_vizbriz_quiz(quiz) if quiz else None
        if not l1:
            return out_canonical, out_age
        if out_canonical is None or not isinstance(out_canonical, dict):
            out_canonical = {}
        demo = dict(out_canonical.get("demographics") or {})
        for key in ("sex", "age_years", "bmi", "height_cm", "weight_kg"):
            val = l1.get(key)
            if val is not None and val != "":
                demo[key] = val
        out_canonical["demographics"] = demo
        if out_age in ("N/A", None, 0, "0") and l1.get("age_years") is not None:
            try:
                out_age = int(float(str(l1["age_years"]).replace(",", ".")))
            except (TypeError, ValueError):
                pass
    except Exception as ex:
        logger.warning("L1 demographics merge skipped for patient %s: %s", getattr(patient, "id", None), ex)
    return out_canonical, out_age


def _patient_self_report_is_effectively_empty(psr):
    """True when the clinical-summary self-report card has nothing meaningful to show."""
    if not psr or not isinstance(psr, dict):
        return True
    if (psr.get("primary_complaint") or "").strip():
        return False
    goals = psr.get("goals")
    if isinstance(goals, list) and len(goals) > 0:
        return False
    sym = psr.get("symptoms")
    if isinstance(sym, dict) and any(sym.values()):
        return False
    if isinstance(sym, list) and len(sym) > 0:
        return False
    scales = psr.get("scales")
    if isinstance(scales, dict) and any(v is not None and str(v).strip() != "" for v in scales.values()):
        return False
    return True


def _observations_summary_is_empty(canonical_data):
    if not isinstance(canonical_data, dict):
        return True
    obs = canonical_data.get("observations")
    if not isinstance(obs, dict):
        return True
    summary = obs.get("summary")
    if not isinstance(summary, list):
        return True
    return not any(isinstance(s, str) and s.strip() for s in summary)


def _enrich_clinical_manifest_cards(patient, canonical_data):
    """
    When Key Observations / Patient Self-Report are empty, add concise copy from:
    - sleep_study + sleep_studies (+ optional analysis_insights_text)
    - latest VizBriz Level 1 quiz (same source as the L1 PDF / screening narrative)
    """
    if not isinstance(canonical_data, dict) or patient is None:
        return

    def _metric_pick(metrics, key):
        if not isinstance(metrics, list):
            return None
        for m in metrics:
            if isinstance(m, dict) and m.get("key") == key and m.get("value") is not None:
                return m.get("value")
        return None

    # --- Key Observations (observations.summary list of strings) ---
    if _observations_summary_is_empty(canonical_data):
        bullets = []
        ss_root = canonical_data.get("sleep_study") or {}
        if isinstance(ss_root, dict):
            parts = []
            for key, label in (
                ("ahi", "AHI"),
                ("odi", "ODI"),
                ("o2_nadir_pct", "SpO₂ nadir"),
            ):
                v = ss_root.get(key)
                if v is not None:
                    suffix = "%" if "nadir" in key or "pct" in key else ""
                    parts.append(f"{label} {v}{suffix}")
            if parts:
                bullets.append("Sleep study summary (aggregate): " + ", ".join(parts) + ".")

        studies = canonical_data.get("sleep_studies") or []
        if isinstance(studies, list):
            for st in studies[:15]:
                if not isinstance(st, dict):
                    continue
                fn = (st.get("file_name") or "Sleep report").strip()
                dt_raw = st.get("observed_at") or ""
                dt = dt_raw[:10] if isinstance(dt_raw, str) else (
                    dt_raw.strftime("%Y-%m-%d") if hasattr(dt_raw, "strftime") else str(dt_raw)[:10]
                )
                metrics = st.get("metrics") or []
                bits = []
                for key, label in (("ahi", "AHI"), ("odi", "ODI"), ("o2_nadir_pct", "SpO₂ nadir")):
                    v = _metric_pick(metrics, key)
                    if v is not None:
                        bits.append(f"{label} {v}{'%' if 'nadir' in key else ''}")
                line = f"Sleep test ({dt or 'date n/a'} — {fn}): " + (
                    ", ".join(bits) if bits else "recorded metrics"
                )
                insight = (st.get("analysis_insights_text") or "").strip()
                if insight:
                    one = insight.replace("\n", " ").strip()
                    if len(one) > 240:
                        one = one[:237] + "…"
                    line += f" Note: {one}"
                bullets.append(line)

        # Level 1 quiz narrative (English/Hebrew context builder already resolves MSG_* keys)
        try:
            from flask_app.helpers.level1_report_hebrew import build_level1_context_from_vizbriz_quiz
            from flask_app.models import VizBrizQuiz

            quiz = VizBrizQuiz.query.filter_by(user_id=patient.id).order_by(VizBrizQuiz.created_at.desc()).first()
            if not quiz and getattr(patient, "email", None):
                quiz = (
                    VizBrizQuiz.query.filter_by(patient_email=patient.email)
                    .order_by(VizBrizQuiz.created_at.desc())
                    .first()
                )
            if quiz:
                l1ctx = build_level1_context_from_vizbriz_quiz(quiz)
                stxt = (l1ctx.get("symptoms_text") or "").strip()
                placeholders = {
                    "A detailed symptom summary will appear here based on your responses.",
                    "סיכום התסמינים יופיע כאן לאחר עיבוד התשובות.",
                    "טקסט לדוגמה לתסמינים שדווחו.",
                    "Placeholder narrative summary of reported symptoms.",
                }
                risk = (l1ctx.get("risk_level") or "").strip()
                if stxt and stxt not in placeholders and len(stxt) > 24:
                    lead = f"Level 1 screening ({risk})" if risk and risk != "—" else "Level 1 screening"
                    bullets.append(f"{lead}: {stxt[:480]}{'…' if len(stxt) > 480 else ''}")
                elif risk and risk != "—":
                    bullets.append(f"Level 1 screening risk category: {risk}.")
        except Exception as ex:
            logger.debug("L1 bullets for manifest skipped: %s", ex)

        if bullets:
            obs = canonical_data.setdefault("observations", {})
            if not isinstance(obs, dict):
                obs = {}
                canonical_data["observations"] = obs
            obs["summary"] = bullets[:25]

    # --- Patient Self-Report ---
    psr = canonical_data.get("patient_self_report")
    if not _patient_self_report_is_effectively_empty(psr):
        return
    try:
        from flask_app.helpers.level1_report_hebrew import build_level1_context_from_vizbriz_quiz
        from flask_app.models import VizBrizQuiz

        quiz = VizBrizQuiz.query.filter_by(user_id=patient.id).order_by(VizBrizQuiz.created_at.desc()).first()
        if not quiz and getattr(patient, "email", None):
            quiz = (
                VizBrizQuiz.query.filter_by(patient_email=patient.email)
                .order_by(VizBrizQuiz.created_at.desc())
                .first()
            )
        if not quiz:
            return
        l1ctx = build_level1_context_from_vizbriz_quiz(quiz)
        stxt = (l1ctx.get("symptoms_text") or "").strip()
        placeholders = {
            "A detailed symptom summary will appear here based on your responses.",
            "סיכום התסמינים יופיע כאן לאחר עיבוד התשובות.",
            "טקסט לדוגמה לתסמינים שדווחו.",
            "Placeholder narrative summary of reported symptoms.",
        }
        if stxt in placeholders:
            stxt = ""
        out_psr = dict(psr) if isinstance(psr, dict) else {}
        if stxt and len(stxt) > 24:
            out_psr["primary_complaint"] = stxt[:900] + ("…" if len(stxt) > 900 else "")
        risk = (l1ctx.get("risk_level") or "").strip()
        scales = dict(out_psr.get("scales") or {}) if isinstance(out_psr.get("scales"), dict) else {}
        if risk and risk != "—":
            scales["Screening risk"] = risk
        if getattr(quiz, "total_score", None) is not None:
            scales["Quiz score"] = quiz.total_score
        if scales:
            out_psr["scales"] = scales
        alert = (l1ctx.get("alert_text") or "").strip()
        if alert and len(alert) > 40 and alert not in out_psr.get("primary_complaint", ""):
            sym = out_psr.get("symptoms")
            if not isinstance(sym, dict):
                sym = {}
            else:
                sym = dict(sym)
            sym["Screening alert (L1)"] = True
            out_psr["symptoms"] = sym
            # short second line in goals so the card shows screening context
            goals = list(out_psr.get("goals") or []) if isinstance(out_psr.get("goals"), list) else []
            goals.append(alert[:300] + ("…" if len(alert) > 300 else ""))
            out_psr["goals"] = goals[:5]
        if out_psr.get("primary_complaint") or out_psr.get("scales") or out_psr.get("goals"):
            canonical_data["patient_self_report"] = out_psr
    except Exception as ex:
        logger.debug("patient_self_report manifest enrichment skipped: %s", ex)


def _format_queue_duration_seconds(secs: int) -> str:
    """Short human label for queue / processing wait times."""
    try:
        s = max(0, int(secs))
    except (TypeError, ValueError):
        return "0s"
    if s < 60:
        return f"{s}s"
    m, r = divmod(s, 60)
    if m < 60:
        return f"{m}m {r}s" if r else f"{m}m"
    h, m2 = divmod(m, 60)
    return f"{h}h {m2}m"


def _abandon_stale_queue_rows_and_get_active_processing(patient_id):
    """
    Drop SLA-expired document_processing_queue rows (see document_queue_sla), then
    return whether this patient still has a pending/processing row (manifest banner),
    plus queue insights: jobs ahead (pending only) and elapsed time.
    """
    try:
        from flask_app.services.document_queue_sla import abandon_expired_document_queue_rows

        n = abandon_expired_document_queue_rows()
        if n:
            logger.info(
                "document_queue_sla (manifest): removed %s stale queue row(s) before render",
                n,
            )
    except Exception as e:
        logger.warning("document_queue_sla (manifest): %s", e)

    processing_status = None
    try:
        from sqlalchemy import text

        queue_entry = db.session.execute(
            text(
                """
                SELECT id, status, requested_at, started_at, COALESCE(priority, 0) AS priority
                FROM document_processing_queue
                WHERE patient_id = :patient_id
                AND status IN ('pending', 'processing')
                ORDER BY requested_at DESC
                LIMIT 1
                """
            ),
            {"patient_id": patient_id},
        ).fetchone()
        if queue_entry:
            processing_status = queue_entry.status
            qid = queue_entry.id
            pri = int(queue_entry.priority or 0)
            req_at = queue_entry.requested_at

            jobs_ahead = 0
            if processing_status == "pending":
                ahead_row = db.session.execute(
                    text(
                        """
                        SELECT COUNT(*) AS c
                        FROM document_processing_queue q
                        WHERE q.status = 'pending'
                          AND q.id != :qid
                          AND (
                            COALESCE(q.priority, 0) > :priority
                            OR (
                                COALESCE(q.priority, 0) = :priority
                                AND q.requested_at < :req_at
                            )
                            OR (
                                COALESCE(q.priority, 0) = :priority
                                AND q.requested_at = :req_at
                                AND q.id < :qid
                            )
                          )
                        """
                    ),
                    {"qid": qid, "priority": pri, "req_at": req_at},
                ).fetchone()
                jobs_ahead = int((ahead_row[0] if ahead_row else 0) or 0)

            if processing_status == "processing":
                elapsed_row = db.session.execute(
                    text(
                        """
                        SELECT TIMESTAMPDIFF(
                            SECOND,
                            COALESCE(started_at, requested_at),
                            NOW()
                        ) AS secs
                        FROM document_processing_queue
                        WHERE id = :qid
                        """
                    ),
                    {"qid": qid},
                ).fetchone()
            else:
                elapsed_row = db.session.execute(
                    text(
                        """
                        SELECT TIMESTAMPDIFF(SECOND, requested_at, NOW()) AS secs
                        FROM document_processing_queue
                        WHERE id = :qid
                        """
                    ),
                    {"qid": qid},
                ).fetchone()

            elapsed_secs = int((elapsed_row[0] if elapsed_row else 0) or 0)
            processing_now = db.session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM document_processing_queue
                    WHERE status = 'processing'
                    """
                )
            ).fetchone()
            n_processing = int((processing_now[0] if processing_now else 0) or 0)

            queue_info = {
                "queue_jobs_ahead": jobs_ahead,
                "elapsed_seconds": elapsed_secs,
                "elapsed_label": _format_queue_duration_seconds(elapsed_secs),
                "workers_busy": n_processing,
            }

            logger.info(
                "Patient %s has active document queue work: status=%s ahead=%s elapsed=%ss",
                patient_id,
                processing_status,
                jobs_ahead,
                elapsed_secs,
            )
            return True, processing_status, queue_info
    except Exception as e:
        logger.warning("Error checking document_processing_queue for patient %s: %s", patient_id, e)
    return False, None, None


def patient_workflow_manifest(patient_id):
    """Display a manifest-aware patient workflow interface with LLM guidance using canonical schema"""
    # Lazy imports to avoid circular dependency with main_routes
    from flask_app.routes.main_routes import (
        load_document_observations,
        _build_phenotype_from_observations,
        build_enhanced_patient_packet,
        build_view_models_from_llm,
        _parse_llm_phenotype_summary,
        fetch_patient_details
    )
    
    print(f"DEBUG: patient_workflow_manifest route called for patient {patient_id}")
    logger.info(f"DEBUG: patient_workflow_manifest route called for patient {patient_id}")
    try:
        # AUTO-UPDATE MANIFEST: Validate and update patient manifest stages
        print(f"DEBUG: About to import ManifestValidatorService")
        from flask_app.services.manifest_validator import ManifestValidatorService
        print(f"DEBUG: ManifestValidatorService imported successfully")
        logger.info(f"Auto-validating manifest for patient {patient_id}")
        print(f"DEBUG: About to call ManifestValidatorService for patient {patient_id}")
        try:
            validation_results = ManifestValidatorService.validate_and_update_patient_stages(patient_id)
            print(f"DEBUG: ManifestValidatorService returned: {validation_results is not None}")
            if validation_results:
                print(f"DEBUG: Validation results keys: {list(validation_results.keys())}")
                for key, result in validation_results.items():
                    print(f"DEBUG: {key} -> completed: {result.get('is_completed', False)}")
            else:
                print("DEBUG: Validation results is None or empty")
            if validation_results:
                completed_count = sum(1 for result in validation_results.values() if result.get('is_completed', False))
                logger.info(f"Manifest validation completed: {completed_count}/{len(validation_results)} stages completed")
                
                # Debug: Log sleep test related stages
                sleep_stages = {k: v for k, v in validation_results.items() if 'sleep' in k.lower()}
                for stage_key, result in sleep_stages.items():
                    logger.info(f"SLEEP VALIDATION: {stage_key} -> completed: {result.get('is_completed', False)} -> message: {result.get('status_message', 'No message')}")
            else:
                logger.error(f"Manifest validation returned None for patient {patient_id}")
        except Exception as e:
            logger.error(f"Error in manifest validation for patient {patient_id}: {e}")
            import traceback
            traceback.print_exc()
        
        logger.info(f"CHECKPOINT 1: Starting patient data loading for patient {patient_id}")
        
        # PERFORMANCE OPTIMIZATION: Use optimized patient data loading
        from flask_app.services.performance_service import PerformanceService
        from flask_app.services.cache_service import CacheService
        
        logger.info(f"CHECKPOINT 2: About to call PerformanceService.get_optimized_patient_data")
        
        # Try optimized loading first
        optimized_data = PerformanceService.get_optimized_patient_data(patient_id)
        
        logger.info(f"CHECKPOINT 3: Optimized data loaded: {optimized_data is not None}")
        if optimized_data:
            patient = optimized_data['patient']
            canonical_data = optimized_data['canonical_data']
        else:
            # Fallback to original method if optimization fails
            patient = Patient.query.get(patient_id)
            canonical_data = None
        
        if not patient:
            flash('Patient not found', 'error')
            return redirect(url_for('main.patient_list'))
        
        # Check if the user has permission to access this patient
        if not current_user.can_access_patient(patient):
            logger.warning(f"User {current_user.email} does not have permission to view patient {patient_id}")
            flash('You do not have permission to view this patient.', 'error')
            return redirect(url_for('main.patient_list'))
        
        logger.info(f"CHECKPOINT 4: About to load execution manifest")
        
        # PERFORMANCE OPTIMIZATION: Use cached execution manifest
        # Check if we should force refresh (e.g., if checking for skipped stages fix)
        force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'
        
        logger.info(f"Loading execution manifest for patient {patient_id}, force_refresh: {force_refresh}")
        
        # Invalidate cache if force refresh is requested
        if force_refresh:
            logger.info(f"Force refresh requested - invalidating execution manifest cache for patient {patient_id}")
            CacheService.invalidate_patient_cache(patient_id)
        
        execution_manifest = CacheService.cached_execution_manifest(patient_id, force_refresh=force_refresh)
        
        logger.info(f"CHECKPOINT 5: Cached execution manifest: {execution_manifest is not None}, force_refresh: {force_refresh}")
        
        # Fallback to original method if cache fails or force refresh
        if not execution_manifest:
            logger.info(f"CHECKPOINT 6: Generating fresh execution manifest (cache miss or force refresh)")
            from flask_app.routes.cursor_routes import get_execution_manifest
            execution_manifest_response = get_execution_manifest(patient_id)
            
            # Check if it's a Flask Response object
            if hasattr(execution_manifest_response, 'get_json'):
                execution_manifest = execution_manifest_response.get_json()
            else:
                execution_manifest = execution_manifest_response
            
            # Log the next stage from the fresh manifest
            if execution_manifest and isinstance(execution_manifest, dict):
                next_stage_info = execution_manifest.get('next_stage')
                if next_stage_info:
                    logger.info(f"CHECKPOINT 7: Fresh execution manifest next_stage: {next_stage_info.get('stage_name')} (key: {next_stage_info.get('stage_key')})")
                else:
                    logger.warning(f"CHECKPOINT 7: Fresh execution manifest has no next_stage")
        else:
            # Log the next stage from cached manifest
            if execution_manifest and isinstance(execution_manifest, dict):
                next_stage_info = execution_manifest.get('next_stage')
                if next_stage_info:
                    logger.info(f"CHECKPOINT 7: Cached execution manifest next_stage: {next_stage_info.get('stage_name')} (key: {next_stage_info.get('stage_key')})")
                else:
                    logger.warning(f"CHECKPOINT 7: Cached execution manifest has no next_stage")
        
        if not execution_manifest or 'error' in execution_manifest:
            error_msg = execution_manifest.get('error', 'Failed to load execution manifest') if execution_manifest else 'Failed to load execution manifest'
            flash(error_msg, 'error')
            return redirect(url_for('main.patient_list'))
        
        manifest_data = execution_manifest
        
        logger.info(f"CHECKPOINT 6: Manifest data loaded, type: {type(manifest_data)}")
        
        # Validate manifest_data is a dict
        if not isinstance(manifest_data, dict):
            logger.error(f"manifest_data is not a dict in patient_workflow_manifest! Type: {type(manifest_data)}")
            flash(f'Invalid manifest data format (expected dict, got {type(manifest_data).__name__})', 'error')
            return redirect(url_for('main.patient_list'))
        
        # PERFORMANCE OPTIMIZATION: Use cached canonical data if not already loaded
        if canonical_data is None:
            canonical_data = CacheService.cached_canonical_data(patient_id)
            
            # Fallback to original method if cache fails
            if canonical_data is None:
                try:
                    from flask_app.models import PatientCaseEnvelope
                    canonical_envelope = PatientCaseEnvelope.query.filter_by(
                        patient_id=patient_id, 
                        report_id='canonical'
                    ).first()
                    
                    if canonical_envelope and canonical_envelope.case_json:
                        # Parse the JSON string into a Python dictionary
                        if isinstance(canonical_envelope.case_json, str):
                            canonical_data = json.loads(canonical_envelope.case_json)
                        else:
                            canonical_data = canonical_envelope.case_json
                        
                        # SANITIZE: Fix malformed evaluations in follow_up_plan
                        if canonical_data and isinstance(canonical_data, dict):
                            follow_up_plan = canonical_data.get('follow_up_plan', {})
                            if follow_up_plan and isinstance(follow_up_plan, dict):
                                evaluations = follow_up_plan.get('evaluations', [])
                                if isinstance(evaluations, list):
                                    cleaned_evaluations = []
                                    for eval_item in evaluations:
                                        if isinstance(eval_item, dict):
                                            # Check if 'type' field contains a string representation of a dict
                                            eval_type = eval_item.get('type', '')
                                            if isinstance(eval_type, str) and eval_type.startswith('{'):
                                                # Skip malformed entries that have dict strings as type
                                                logger.warning(f"Skipping malformed evaluation: {eval_type}")
                                                continue
                                            cleaned_evaluations.append(eval_item)
                                        elif isinstance(eval_item, str):
                                            # Skip string entries
                                            logger.warning(f"Skipping string evaluation: {eval_item}")
                                            continue
                                        else:
                                            cleaned_evaluations.append(eval_item)
                                    
                                    # Update with cleaned list
                                    follow_up_plan['evaluations'] = cleaned_evaluations
                                    canonical_data['follow_up_plan'] = follow_up_plan
                        
                        logger.info(f"CHECKPOINT 7: Loaded and sanitized canonical data for patient {patient_id}")
                        logger.info(f"Canonical data keys: {list(canonical_data.keys()) if isinstance(canonical_data, dict) else 'Not a dict'}")
                        if isinstance(canonical_data, dict) and 'sleep_study' in canonical_data:
                            logger.info(f"Sleep study AHI: {canonical_data['sleep_study'].get('ahi')}")
                    else:
                        logger.info(f"No canonical data found for patient {patient_id}")
                except Exception as e:
                    logger.error(f"Error loading canonical data for patient {patient_id}: {e}")
                    canonical_data = None
        
        logger.info(f"CHECKPOINT 8: About to calculate progress")
        
        # Calculate progress
        stage_manifest = manifest_data.get('stage_manifest', [])
        completed_stages = sum(1 for stage in stage_manifest if stage.get('value') == 'yes')
        total_stages = len(stage_manifest)
        progress_percentage = round((completed_stages / total_stages * 100)) if total_stages > 0 else 0
        
        logger.info(f"CHECKPOINT 9: Progress calculated: {progress_percentage}%")
        
        # Get eligible actions
        eligible_actions = manifest_data.get('eligible_actions', [])
        
        logger.info(f"CHECKPOINT 10: About to load all actions")
        
        # Get all actions for the "Show All" functionality
        from flask_app.config.action_manifest import get_all_actions
        all_actions = get_all_actions()
        all_actions_list = []
        
        # DEBUG: Check what type all_actions is
        logger.info(f"DEBUG: all_actions type: {type(all_actions)}")
        logger.info(f"DEBUG: all_actions is dict: {isinstance(all_actions, dict)}")
        logger.info(f"DEBUG: all_actions is list: {isinstance(all_actions, list)}")
        
        if not isinstance(all_actions, dict):
            logger.error(f"ERROR: all_actions is not a dict! Type: {type(all_actions)}, Value: {all_actions}")
            flash('Error loading action manifest - invalid data type', 'error')
            return redirect(url_for('main.patient_list'))
        
        for action_key, action_config in all_actions.items():
            all_actions_list.append({
                'action_key': action_key,
                'label': action_config.get('label', action_key),
                'ui_type': action_config.get('ui_type', 'button'),
                'endpoint': action_config.get('endpoint', ''),
                'input_fields': action_config.get('input_fields', []),
                'ai_guidance': action_config.get('ai_guidance', ''),
                'ui_enhancement': action_config.get('ui_enhancement', {}),
                'category': action_config.get('category', 'scheduling')
            })
        
        # Ensure both lists are actually lists and contain dictionaries
        if not isinstance(eligible_actions, list):
            logger.warning(f"eligible_actions is not a list: {type(eligible_actions)}")
            eligible_actions = []
        else:
            # Ensure each action is a dictionary
            cleaned_eligible_actions = []
            for i, action in enumerate(eligible_actions):
                if isinstance(action, dict):
                    cleaned_eligible_actions.append(action)
                else:
                    logger.warning(f"Patient {patient_id}: eligible_actions[{i}] is not a dict: {type(action)}, value: {action}")
                    # Convert tuple to dict if possible, otherwise skip
                    if isinstance(action, tuple):
                        try:
                            # Try to convert tuple to dict based on expected structure
                            action_dict = {
                                'action_key': action[0] if len(action) > 0 else 'unknown',
                                'label': action[1] if len(action) > 1 else 'Unknown Action',
                                'ui_type': action[2] if len(action) > 2 else 'button',
                                'endpoint': action[3] if len(action) > 3 else '',
                                'input_fields': action[4] if len(action) > 4 else []
                            }
                            cleaned_eligible_actions.append(action_dict)
                            logger.info(f"Patient {patient_id}: Converted tuple to dict: {action_dict}")
                        except Exception as e:
                            logger.error(f"Patient {patient_id}: Failed to convert tuple to dict: {e}")
                    else:
                        logger.error(f"Patient {patient_id}: Skipping non-dict, non-tuple action: {type(action)}")
            eligible_actions = cleaned_eligible_actions
            
        if not isinstance(all_actions_list, list):
            logger.warning(f"all_actions_list is not a list: {type(all_actions_list)}")
            all_actions_list = list(all_actions_list) if all_actions_list else []
        
        logger.info(f"CHECKPOINT 11: All actions loaded, count: {len(all_actions_list)}")
        
        # Prepare OSA policy manifest (patient-specific if available; fallback to base)
        osa_policy_manifest = {}
        osa_policy_manifest_source = 'none'
        try:
            import os, json, boto3
            from botocore.config import Config as BotoConfig
            # Load base policy
            base_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'osa_policy_base_v2.json')
            base_policy = {}
            try:
                with open(base_path, 'r') as f:
                    base_policy = json.load(f)
            except Exception:
                base_policy = {}

            # Try S3 per-patient policy first
            s3_policy = None
            try:
                s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-west-2'), config=BotoConfig(signature_version='s3v4'))
                bucket = os.getenv('S3_BUCKET_NAME')
                if bucket:
                    key = f"patients/{patient_id}/manifests/osa_policy_v2.json"
                    s3_obj = s3.get_object(Bucket=bucket, Key=key)
                    s3_policy = json.loads(s3_obj['Body'].read().decode('utf-8'))
            except Exception:
                s3_policy = None

            # Fallback to local patient policy file
            local_patient_policy = None
            try:
                cfg_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config')
                local_path = os.path.join(cfg_dir, f"osa_policy_v2_patient_{patient_id}_generic")
                if os.path.exists(local_path):
                    with open(local_path, 'r') as f:
                        local_patient_policy = json.load(f)
            except Exception:
                local_patient_policy = None

            def _deep_merge(a: dict, b: dict) -> dict:
                if not isinstance(a, dict):
                    return json.loads(json.dumps(b))
                out = json.loads(json.dumps(a))
                for k, v in (b or {}).items():
                    if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                        out[k] = _deep_merge(out[k], v)
                    else:
                        out[k] = json.loads(json.dumps(v))
                return out

            # Choose and merge
            if s3_policy:
                osa_policy_manifest = _deep_merge(base_policy, s3_policy)
                osa_policy_manifest_source = 's3_patient_policy+base'
            elif local_patient_policy:
                osa_policy_manifest = _deep_merge(base_policy, local_patient_policy)
                osa_policy_manifest_source = 'local_patient_policy+base'
            else:
                osa_policy_manifest = base_policy or {}
                osa_policy_manifest_source = 'base_only'
        except Exception:
            osa_policy_manifest = {}
            osa_policy_manifest_source = 'error'

        # Build phenotype from observations and attach to policy for UI view
        osa_policy_manifest_with_phenotype = {}
        # Helper functions for phenotype building
        def _extract_sleep_study_data(obs: dict) -> dict:
            """Extract and validate sleep study data with robust parsing"""
            import re
            from datetime import datetime
            
            sleep_data = {
                'type': None,
                'date': None,
                'AHI': None,
                'SpO2_nadir': None,
                'ODI': None,
                'source_doc_id': None
            }
            
            # AHI extraction patterns - enhanced to catch more formats
            ahi_patterns = [
                r'\bAHI[:=\s]+([0-9]+(?:\.[0-9]+)?)\b',  # AHI: 15
                r'AHI[:=\s]+([0-9]+(?:\.[0-9]+)?)\s*\([^)]*\)',  # AHI: 15 (Mild to Moderate OSA)
                r'Apnea[- ]?Hypopnea Index[:=\s]+([0-9]+(?:\.[0-9]+)?)',
                r'\bRDI[:=\s]+([0-9]+(?:\.[0-9]+)?)\b',  # optional fallback
                r'AHI\s*of\s*([0-9]+(?:\.[0-9]+)?)',
                r'([0-9]+(?:\.[0-9]+)?)\s*events?/hr?\b',
                r'Right side AHI[:=\s]+([0-9]+(?:\.[0-9]+)?)',  # Positional AHI
                r'Supine AHI[:=\s]+([0-9]+(?:\.[0-9]+)?)'  # Positional AHI
            ]
            
            # SpO2 extraction patterns - enhanced to catch more formats
            spo2_patterns = [
                r'SpO2\s*nadir[:=\s]+([0-9]+)',
                r'O2\s*Nadir[:=\s]+([0-9]+)',  # O2 Nadir: 83%
                r'oxygen\s*desaturation\s*to\s*([0-9]+)',
                r'lowest\s*SpO2[:=\s]+([0-9]+)',
                r'SpO2\s*drop.*?([0-9]+)',
                r'Time spent with O2 < 90%[:=\s]+([0-9]+(?:\.[0-9]+)?)%'  # Time spent with O2 < 90%: 0.1%
            ]
            
            # ODI extraction patterns - enhanced to catch more formats
            odi_patterns = [
                r'\bODI[:=\s]+([0-9]+(?:\.[0-9]+)?)\b',  # ODI: 6.7
                r'Oxygen\s*Desaturation\s*Index[:=\s]+([0-9]+(?:\.[0-9]+)?)',
                r'Right side ODI[:=\s]+([0-9]+(?:\.[0-9]+)?)',  # Right side ODI: 10.2
                r'Supine ODI[:=\s]+([0-9]+(?:\.[0-9]+)?)'  # Supine ODI: 8.7
            ]
            
            # Extract AHI
            ahi_value = _extract_number_from_observations(obs, ahi_patterns, 'ahi')
            if ahi_value is not None and 0 <= ahi_value <= 200:
                sleep_data['AHI'] = round(ahi_value, 1)
            else:
                if ahi_value is not None:
                    logger.warning(f"AHI value {ahi_value} outside valid range (0-200)")
                if 'data_quality' not in sleep_data:
                    sleep_data['data_quality'] = []
                sleep_data['data_quality'].append('AHI_invalid_or_missing')
            
            # Extract SpO2 nadir
            spo2_value = _extract_number_from_observations(obs, spo2_patterns, 'spo2')
            if spo2_value is not None and 50 <= spo2_value <= 100:
                sleep_data['SpO2_nadir'] = int(spo2_value)
            else:
                if spo2_value is not None:
                    logger.warning(f"SpO2 value {spo2_value} outside valid range (50-100)")
                if 'data_quality' not in sleep_data:
                    sleep_data['data_quality'] = []
                sleep_data['data_quality'].append('SpO2_invalid_or_missing')
            
            # Extract ODI
            odi_value = _extract_number_from_observations(obs, odi_patterns, 'odi')
            if odi_value is not None and 0 <= odi_value <= 200:
                sleep_data['ODI'] = round(odi_value, 1)
            
            # Determine study type
            study_text = ' '.join(str(v).lower() for v in obs.values())
            if 'hst' in study_text or 'home' in study_text:
                sleep_data['type'] = 'HST'
            elif 'psg' in study_text or 'polysomnography' in study_text:
                sleep_data['type'] = 'PSG'
            
            return sleep_data

        def _extract_number_from_observations(obs: dict, patterns: list, field_name: str) -> float:
            """Extract numeric value from observations using regex patterns"""
            import re
            
            # First try direct numeric values
            for source, items in (obs or {}).items():
                for item in (items or []):
                    name = (item.get('observation') or '').lower()
                    val = item.get('value')
                    
                    if field_name in name:
                        # Try value field first
                        if isinstance(val, (int, float)):
                            return float(val)
                        elif isinstance(val, str):
                            # Try to extract number from value string
                            for pattern in patterns:
                                match = re.search(pattern, val, flags=re.IGNORECASE)
                                if match:
                                    try:
                                        return float(match.group(1))
                                    except (ValueError, IndexError):
                                        continue
                        
                        # If value field doesn't contain the number, try observation field
                        observation_text = item.get('observation', '')
                        if observation_text:
                            for pattern in patterns:
                                match = re.search(pattern, observation_text, flags=re.IGNORECASE)
                                if match:
                                    try:
                                        return float(match.group(1))
                                    except (ValueError, IndexError):
                                        continue
            
            # Then try all observation values for patterns
            for source, items in (obs or {}).items():
                for item in (items or []):
                    val = item.get('value')
                    val_str = str(val).lower()
                    
                    # Try value field first
                    for pattern in patterns:
                        match = re.search(pattern, val_str, flags=re.IGNORECASE)
                        if match:
                            try:
                                return float(match.group(1))
                            except (ValueError, IndexError):
                                continue
                    
                    # If value field doesn't contain the number, try observation field
                    observation_text = item.get('observation', '')
                    if observation_text:
                        for pattern in patterns:
                            match = re.search(pattern, observation_text, flags=re.IGNORECASE)
                            if match:
                                try:
                                    return float(match.group(1))
                                except (ValueError, IndexError):
                                    continue
            
            return None

        def _ahi_to_severity(ahi: float) -> str:
            """Convert AHI to severity using AASM cutoffs"""
            if ahi is None:
                return "unknown"
            if ahi < 5:
                return "normal"
            if ahi < 15:
                return "mild"
            if ahi < 30:
                return "moderate"
            return "severe"

        def _determine_policy_eligibility(phenotype: dict) -> dict:
            """Determine policy eligibility based on phenotype data"""
            eligibility = {
                'osa_confirmed': False,
                'treatment_eligible': False,
                'oral_appliance_candidate': False,
                'requires_specialist_referral': False,
                'risk_level': 'low',
                'recommended_pathway': 'standard'
            }
            
            # Check OSA confirmation
            if phenotype.get('osa_assessment', {}).get('AHI'):
                ahi = phenotype['osa_assessment']['AHI']
                if isinstance(ahi, (int, float)) and ahi >= 5:
                    eligibility['osa_confirmed'] = True
                elif isinstance(ahi, str) and any(word in ahi.lower() for word in ['present', 'positive', 'abnormal']):
                    eligibility['osa_confirmed'] = True
            
            # Check treatment eligibility
            if eligibility['osa_confirmed']:
                eligibility['treatment_eligible'] = True
                
                # Check for CPAP intolerance (makes oral appliance more likely)
                if phenotype.get('treatment_history', {}).get('cpap_intolerance'):
                    eligibility['oral_appliance_candidate'] = True
                    eligibility['recommended_pathway'] = 'oral_appliance_first'
                
                # Check severity for specialist referral
                severity = phenotype.get('osa_assessment', {}).get('severity')
                if severity in ['severe']:
                    eligibility['requires_specialist_referral'] = True
                    eligibility['risk_level'] = 'high'
                elif severity in ['moderate']:
                    eligibility['risk_level'] = 'medium'
            
            # Check anatomical contraindications
            if phenotype.get('anatomical_findings', {}).get('tmj_findings', {}).get('present'):
                tmj_pain = phenotype['anatomical_findings']['tmj_findings'].get('pain_vas', 0)
                if isinstance(tmj_pain, (int, float)) and tmj_pain > 6:
                    eligibility['oral_appliance_candidate'] = False
                    eligibility['recommended_pathway'] = 'specialist_consultation'
            
            return eligibility

        try:
            observations = load_document_observations(patient_id)
            
            # Proactive check for no sleep study data
            has_sleep_study_observations = False
            if observations:
                for source, items in observations.items():
                    if items:
                        for item in items:
                            if item.get('observation', '').startswith('sleep_study.'):
                                has_sleep_study_observations = True
                                break
                    if has_sleep_study_observations:
                        break
            
            if not has_sleep_study_observations:
                logger.info(f"Patient {patient_id} - No sleep study observations found, creating normal phenotype for patient without sleep study data")
                phenotype = {
                    'sleep_study': {
                        'type': 'none',
                        'date': None,
                        'AHI': None,
                        'SpO2_nadir': None,
                        'ODI': None,
                        'severity': 'unknown',
                        'status': 'no_data_uploaded'
                    },
                    'osa_assessment': {'AHI': None, 'severity': 'unknown'},
                    'data_quality': ['no_sleep_study_data']
                }
            else:
                try:
                    phenotype = _build_phenotype_from_observations(observations or {})
                except AttributeError as attr_error:
                    if "'dict object' has no attribute 'ahi'" in str(attr_error):
                        logger.info(f"Patient {patient_id} - No sleep study data detected (normal state)")
                        # Create a normal phenotype for patients without sleep study data
                        phenotype = {
                            'sleep_study': {
                                'type': 'none',
                                'date': None,
                                'AHI': None,
                                'SpO2_nadir': None,
                                'ODI': None,
                                'severity': 'unknown',
                                'status': 'no_data_uploaded'
                            },
                            'osa_assessment': {'AHI': None, 'severity': 'unknown'},
                            'data_quality': ['no_sleep_study_data']
                        }
                    else:
                        raise attr_error
                
            # Force refresh phenotype data by rebuilding if needed
            if not phenotype.get('sleep_study', {}).get('AHI') or phenotype.get('sleep_study', {}).get('AHI') == 'Present (value not specified)':
                logger.warning(f"Patient {patient_id} - Phenotype AHI not properly parsed, rebuilding...")
                try:
                    # Clear and rebuild phenotype
                    phenotype = _build_phenotype_from_observations(observations or {})
                except AttributeError as attr_error:
                    if "'dict object' has no attribute 'ahi'" in str(attr_error):
                        logger.error(f"Patient {patient_id} - AHI attribute error in phenotype rebuilding: {attr_error}")
                        # Keep existing phenotype to prevent complete failure
                        pass
                    else:
                        raise attr_error
            
            # Check if we have any sleep study data at all
            has_sleep_study_data = (
                phenotype.get('sleep_study', {}).get('AHI') is not None and 
                phenotype.get('sleep_study', {}).get('AHI') != 'Present (value not specified)' and
                phenotype.get('sleep_study', {}).get('AHI') != 'unknown'
            )
            
            if not has_sleep_study_data:
                logger.info(f"Patient {patient_id} - No sleep study data found, creating fallback phenotype")
                phenotype['sleep_study'] = {
                    'type': 'none',
                    'date': None,
                    'AHI': None,
                    'SpO2_nadir': None,
                    'ODI': None,
                    'severity': 'unknown',
                    'status': 'no_data_uploaded'
                }
                phenotype['data_quality'] = phenotype.get('data_quality', []) + ['no_sleep_study_data']
            
            # Debug logging for AHI parsing using canonical schema
            logger.info(f"Patient {patient_id} - Raw observations: {observations}")
            logger.info(f"Patient {patient_id} - Built phenotype: {phenotype}")
            if phenotype.get('sleep_study'):
                logger.info(f"Patient {patient_id} - Sleep Study: {phenotype['sleep_study']}")
                logger.info(f"Patient {patient_id} - AHI Value: {phenotype['sleep_study'].get('AHI')}")
                logger.info(f"Patient {patient_id} - AHI Type: {type(phenotype['sleep_study'].get('AHI'))}")
                logger.info(f"Patient {patient_id} - Severity: {phenotype['sleep_study'].get('severity')}")
                logger.info(f"Patient {patient_id} - Data Quality: {phenotype.get('data_quality', [])}")
            import json as _json
            # Deep copy and inject applies_to
            osa_policy_manifest_with_phenotype = _json.loads(_json.dumps(osa_policy_manifest or {}))
            if osa_policy_manifest_with_phenotype is None:
                osa_policy_manifest_with_phenotype = {}
            osa_policy_manifest_with_phenotype['applies_to'] = {
                'patient_id': str(patient_id),
                'phenotype_summary': phenotype
            }
        except Exception as e:
            logger.error(f"Error building phenotype for patient {patient_id}: {e}")
            osa_policy_manifest_with_phenotype = osa_policy_manifest or {}

        # Get current stage and next steps using LLM - OPTIMIZED: Single call for both guidance and status
        from flask_app.routes.osaagent_routes import query_bedrock_claude_enhanced
        
        # Calculate age from date of birth
        patient_age = 'N/A'
        if patient.dob:
            from datetime import date
            today = date.today()
            try:
                patient_age = today.year - patient.dob.year - ((today.month, today.day) < (patient.dob.month, patient.dob.day))
            except:
                patient_age = 'N/A'
        
        # Create separate clinical and operational prompts using specialized templates
        current_stage_index = completed_stages
        next_stages = stage_manifest[current_stage_index:current_stage_index + 3] if current_stage_index < len(stage_manifest) else []
        
        # Initialize phenotype variable to avoid reference error
        phenotype = {}
        try:
            # Directly load observations from database and build phenotype
            logger.info(f"Patient {patient.id} - Loading observations directly from database...")
            
            # Direct database query for observations
            import mysql.connector
            conn = mysql.connector.connect(
                host='vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com',
                user='admin',
                password='Vizbriz2025!',
                database='vizbriz',
                port=3306
            )
            cursor = conn.cursor(dictionary=True)
            
            # Query for all observations for this patient
            query = """
                SELECT source_type, source_text, extracted_observations, created_at
                FROM observation_store 
                WHERE patient_id = %s 
                ORDER BY created_at DESC
            """
            cursor.execute(query, (patient_id,))
            db_observations = cursor.fetchall()
            
            logger.info(f"Patient {patient.id} - Found {len(db_observations)} observations in database")
            
            if db_observations:
                # Organize observations by source type (same as load_document_observations)
                organized_observations = {}
                source_type_mapping = {
                    'sleep_test': 'Sleep Study Results',
                    'questionnaire': 'Patient Questionnaires',
                    'intraoral_scan': 'Intraoral Scans',
                    'medical_background': 'Medical History',
                    'consent_form': 'Consent Forms',
                    'insurance_document': 'Insurance Documents',
                    'payment_document': 'Payment Documents',
                    'cbct_report': 'CBCT Reports',
                    'patient_report': 'Patient Reports',
                    'sleep_study': 'Sleep Studies',
                    'consultation_notes': 'Consultation Notes',
                    'treatment_plan': 'Treatment Plans',
                    'follow_up_notes': 'Follow-up Notes',
                    'prescription': 'Prescriptions',
                    'lab_results': 'Lab Results',
                    'imaging_report': 'Imaging Reports',
                    'medical_history': 'Medical History',
                    'surgical_notes': 'Surgical Notes',
                    'discharge_summary': 'Discharge Summaries',
                    'general_medical': 'General Medical Documents'
                }
                
                for obs in db_observations:
                    source_type = obs['source_type']
                    display_name = source_type_mapping.get(source_type, source_type.replace('_', ' ').title())
                    
                    if display_name not in organized_observations:
                        organized_observations[display_name] = []
                    
                    # Parse the JSON observations
                    try:
                        obs_data = json.loads(obs['extracted_observations']) if obs['extracted_observations'] else {}
                        
                        # Clean up observation title
                        observation = obs_data.get('observation', 'Unknown')
                        redundant_prefixes = [
                            'Observation: ', 'Finding: ', 'Clinical Finding: ', 'Medical Finding: ',
                            'Diagnosis: ', 'Assessment: ', 'Result: ', 'Note: ', 'Comment: ',
                            'Clinical Observation: ', 'Medical Observation: '
                        ]
                        
                        for prefix in redundant_prefixes:
                            if observation.lower().startswith(prefix.lower()):
                                observation = observation[len(prefix):]
                                break
                        
                        organized_observations[display_name].append({
                            'observation': observation,
                            'value': obs_data.get('value', ''),
                            'evidence': obs_data.get('evidence', ''),
                            'confidence': obs_data.get('confidence', 0),
                            'document_name': obs_data.get('document_name', ''),
                            'document_type': obs_data.get('document_type', ''),
                            'extraction_date': obs_data.get('extraction_date', ''),
                            'created_at': obs['created_at'].isoformat() if obs['created_at'] else None
                        })
                    except json.JSONDecodeError:
                        # If JSON parsing fails, create a simple observation
                        organized_observations[display_name].append({
                            'observation': 'Document Analysis',
                            'value': 'Extracted',
                            'evidence': obs['source_text'] or 'Document content analysis',
                            'confidence': 0.5,
                            'document_name': f"{source_type}_document",
                            'document_type': source_type,
                            'extraction_date': obs['created_at'].isoformat() if obs['created_at'] else None,
                            'created_at': obs['created_at'].isoformat() if obs['created_at'] else None
                        })
                
                logger.info(f"Patient {patient.id} - Organized observations by source: {list(organized_observations.keys())}")
                
                # Build phenotype from organized observations
                phenotype = _build_phenotype_from_observations(organized_observations)
                logger.info(f"Patient {patient.id} - Built phenotype from database observations: {phenotype}")
                
            else:
                logger.warning(f"Patient {patient.id} - No observations found in database, using empty phenotype")
            
            conn.close()
            
        except Exception as e:
            import traceback
            logger.warning(f"Patient {patient.id} - Could not build phenotype from database: {e}")
            logger.error(f"Patient {patient.id} - Exception details: {traceback.format_exc()}")
            phenotype = {}
        
        # Debug phenotype data
        logger.info(f"Patient {patient.id} - Raw phenotype data: {phenotype}")
        logger.info(f"Patient {patient.id} - Phenotype keys: {list(phenotype.keys()) if isinstance(phenotype, dict) else 'Not a dict'}")
        logger.info(f"Patient {patient.id} - Sleep study data: {phenotype.get('sleep_study', {}) if isinstance(phenotype, dict) else 'No sleep study'}")
        logger.info(f"Patient {patient.id} - OSA assessment: {phenotype.get('osa_assessment', {}) if isinstance(phenotype, dict) else 'No OSA assessment'}")
        
        # Build compact phenotype summary using canonical schema with better data extraction
        compact_phenotype = {
            "patient_id": str(patient.id),
            "AHI": phenotype.get('AHI') or phenotype.get('sleep_study', {}).get('AHI') or phenotype.get('osa_assessment', {}).get('AHI') or 'unknown',
            "severity": phenotype.get('osa_severity') or phenotype.get('sleep_study', {}).get('severity') or phenotype.get('osa_assessment', {}).get('severity') or 'unknown',
            "SpO2_nadir": phenotype.get('SpO2_nadir_percent') or phenotype.get('sleep_study', {}).get('SpO2_nadir') or phenotype.get('osa_assessment', {}).get('SpO2_nadir') or 'unknown',
            "cpap_intolerance": phenotype.get('cpap_intolerance') or phenotype.get('treatment_history', {}).get('cpap_intolerance', False),
            "nasal_obstruction": phenotype.get('nasal_obstruction_present') or phenotype.get('anatomical_findings', {}).get('nasal_obstruction', {}).get('present', False),
            "tmj_pain_vas": phenotype.get('tmj_findings', {}).get('pain_vas') or phenotype.get('anatomical_findings', {}).get('tmj_findings', {}).get('pain_vas', 'unknown'),
            "allergy_nickel": any('nickel' in str(obs.get('observation', '')).lower() for obs in phenotype.get('raw_observations', [])) if phenotype.get('raw_observations') else False,
            "primary_site": phenotype.get('primary_narrowing_site') or phenotype.get('anatomical_findings', {}).get('primary_narrowing_site', 'unknown'),
            "oral_appliance_candidate": phenotype.get('oral_appliance_candidate') or phenotype.get('policy_eligibility', {}).get('oral_appliance_candidate', False),
            "comorbidities": list(phenotype.get('comorbidities', {}).keys()) if phenotype.get('comorbidities') else [],
            "feature_schema_version": phenotype.get('feature_schema_version', 1)
        }
        
        logger.info(f"Patient {patient.id} - Compact phenotype: {compact_phenotype}")
        
        # Build compact operational summary for operational prompt
        compact_operational = {
            "patient_id": str(patient.id),
            "completion_pct": progress_percentage,
            "current_stage": stage_manifest[completed_stages]['stage_name'] if completed_stages < len(stage_manifest) else 'Completed',
            "pending_actions": [{"action": a['label'], "due_in_days": 0} for a in eligible_actions[:3]],
            "last_device_event": "delivery_2025-01-15",  # This would come from actual device history
            "alerts": []
        }
        
        # Add alerts based on phenotype
        if phenotype.get('anatomical_findings', {}).get('tmj_findings', {}).get('present'):
            compact_operational["alerts"].append("tmj_caution")
        if compact_phenotype.get("allergy_nickel"):
            compact_operational["alerts"].append("nickel_constraint")
        
        # Clinical Prompt
        clinical_prompt = f"""SYSTEM:
You are an AI clinical decision support assistant specializing in obstructive sleep apnea (OSA) in dental sleep medicine.
Your role is to interpret the patient's phenotype data, apply the Vizbriz OSA policy (including Lamberg, Vizbriz, and sOSA protocols),
and recommend the next immediate treatment step.

INPUTS PROVIDED:
- patient_id: {patient.id}
- Compact phenotype_summary JSON: {compact_phenotype}
- Current stage in policy workflow: {stage_manifest[completed_stages]['stage_name'] if completed_stages < len(stage_manifest) else 'Completed'}

TASK:
1. State current diagnosis and severity.
2. Explain phenotype-to-policy mapping (rules fired).
3. Recommend next clinical action.
4. List key risks and monitoring points.

Provide a concise clinical summary (2-3 sentences max) focusing on the most critical clinical information."""
        
        # Operational Prompt
        operational_prompt = f"""SYSTEM:
You are an AI OSA care operations coordinator.
Your role is to track patient workflow progress, identify upcoming operational steps,
and ensure timely follow-up according to the Vizbriz workflow and sOSA follow-up protocol.
INPUTS PROVIDED:
- patient_id: {patient.id}
- Compact operational_summary JSON: {compact_operational}
- Current stage: {stage_manifest[completed_stages]['stage_name'] if completed_stages < len(stage_manifest) else 'Completed'}
- Completion: {progress_percentage}%

TASK:
1. State current operational stage and completion %.
2. List immediate next operational actions (with deadlines).
3. Flag any overdue or at-risk tasks.
4. Provide a one-line status summary.

Provide a concise operational summary (1-2 sentences max) focusing on workflow progress and next steps."""
        
        # Use the enhanced single-prompt system with comprehensive schema
        from flask_app.config.bedrock_config import query_bedrock_claude_enhanced, get_bedrock_config
        from flask_app.config.vizbriz_prompt_helper import render_single_prompt, parse_llm_json, basic_validate_response
        
        config = get_bedrock_config("patient_summary")
        
        # Build enhanced packet using the new function with fallback values
        try:
            logger.info(f"Starting enhanced packet build for patient {patient.id}")
            
            # Get stage manifest from execution manifest if available
            stage_manifest = []
            completed_stages = 0
            progress_percentage = 0
            eligible_actions = []
            
            # Try to get manifest data if available
            if 'execution_manifest' in locals():
                logger.info(f"Found execution_manifest for patient {patient.id}")
                stage_manifest = execution_manifest.get('stage_manifest', [])
                completed_stages = sum(1 for stage in stage_manifest if stage.get('value') == 'yes')
                progress_percentage = round((completed_stages / len(stage_manifest) * 100)) if stage_manifest else 0
                eligible_actions = execution_manifest.get('eligible_actions', []) if isinstance(execution_manifest.get('eligible_actions'), list) else []
                logger.info(f"Stage manifest: {len(stage_manifest)} stages, {completed_stages} completed, {progress_percentage}% progress")
            else:
                logger.warning(f"No execution_manifest found for patient {patient.id}, using defaults")
            
            # Check if phenotype exists
            phenotype_data = phenotype if 'phenotype' in locals() else None
            if phenotype_data:
                logger.info(f"Found phenotype data for patient {patient.id}")
                logger.info(f"Phenotype keys: {list(phenotype_data.keys()) if isinstance(phenotype_data, dict) else 'Not a dict'}")
                if isinstance(phenotype_data, dict) and 'sleep_study' in phenotype_data:
                    logger.info(f"Sleep study data: {phenotype_data['sleep_study']}")
                if isinstance(phenotype_data, dict) and 'anatomical_findings' in phenotype_data:
                    logger.info(f"Anatomical findings: {phenotype_data['anatomical_findings']}")
            else:
                logger.warning(f"No phenotype data found for patient {patient.id}")
                
                # Check for available clinical files
                try:
                    from flask_app.routes.main_routes import fetch_patient_details
                    patient_details = fetch_patient_details(patient.id)
                    if patient_details and 'uploaded_files' in patient_details:
                        files = patient_details['uploaded_files']
                        logger.info(f"Patient {patient.id} - Available files: {list(files.keys())}")
                        
                        # Check for sleep study files
                        if 'sleep_test' in files and files['sleep_test']:
                            logger.info(f"Patient {patient.id} - Sleep test files: {files['sleep_test']}")
                        
                        # Check for clinical pictures
                        if 'clinical_pictures' in files and files['clinical_pictures']:
                            logger.info(f"Patient {patient.id} - Clinical picture files: {files['clinical_pictures']}")
                        
                        # Check for CBCT files
                        if 'cbct' in files and files['cbct']:
                            logger.info(f"Patient {patient.id} - CBCT files: {files['cbct']}")
                        
                        # Check for reports
                        if 'reports' in files and files['reports']:
                            logger.info(f"Patient {patient.id} - Report files: {files['reports']}")
                except Exception as e:
                    logger.warning(f"Could not check patient files for patient {patient.id}: {e}")
                
                # Try to build phenotype from observations directly from database
                try:
                    # Direct database query for observations
                    import mysql.connector
                    conn = mysql.connector.connect(
                        host='vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com',
                        user='admin',
                        password='Vizbriz2025!',
                        database='vizbriz',
                        port=3306
                    )
                    cursor = conn.cursor(dictionary=True)
                    
                    # Query for all observations for this patient
                    query = """
                        SELECT source_type, source_text, extracted_observations, created_at
                        FROM observation_store 
                        WHERE patient_id = %s 
                        ORDER BY created_at DESC
                    """
                    cursor.execute(query, (patient.id,))
                    db_observations = cursor.fetchall()
                    
                    logger.info(f"Patient {patient.id} - Found {len(db_observations)} observations in database for fallback")
                    
                    if db_observations:
                        # Organize observations by source type
                        organized_observations = {}
                        source_type_mapping = {
                            'sleep_test': 'Sleep Study Results',
                            'questionnaire': 'Patient Questionnaires',
                            'intraoral_scan': 'Intraoral Scans',
                            'medical_background': 'Medical History',
                            'consent_form': 'Consent Forms',
                            'insurance_document': 'Insurance Documents',
                            'payment_document': 'Payment Documents',
                            'cbct_report': 'CBCT Reports',
                            'patient_report': 'Patient Reports',
                            'sleep_study': 'Sleep Studies',
                            'consultation_notes': 'Consultation Notes',
                            'treatment_plan': 'Treatment Plans',
                            'follow_up_notes': 'Follow-up Notes',
                            'prescription': 'Prescriptions',
                            'lab_results': 'Lab Results',
                            'imaging_report': 'Imaging Reports',
                            'medical_history': 'Medical History',
                            'surgical_notes': 'Surgical Notes',
                            'discharge_summary': 'Discharge Summaries',
                            'general_medical': 'General Medical Documents'
                        }
                        
                        for obs in db_observations:
                            source_type = obs['source_type']
                            display_name = source_type_mapping.get(source_type, source_type.replace('_', ' ').title())
                            
                            if display_name not in organized_observations:
                                organized_observations[display_name] = []
                            
                            # Parse the JSON observations
                            try:
                                obs_data = json.loads(obs['extracted_observations']) if obs['extracted_observations'] else {}
                                
                                # Clean up observation title
                                observation = obs_data.get('observation', 'Unknown')
                                redundant_prefixes = [
                                    'Observation: ', 'Finding: ', 'Clinical Finding: ', 'Medical Finding: ',
                                    'Diagnosis: ', 'Assessment: ', 'Result: ', 'Note: ', 'Comment: ',
                                    'Clinical Observation: ', 'Medical Observation: '
                                ]
                                
                                for prefix in redundant_prefixes:
                                    if observation.lower().startswith(prefix.lower()):
                                        observation = observation[len(prefix):]
                                        break
                                
                                organized_observations[display_name].append({
                                    'observation': observation,
                                    'value': obs_data.get('value', ''),
                                    'evidence': obs_data.get('evidence', ''),
                                    'confidence': obs_data.get('confidence', 0),
                                    'document_name': obs_data.get('document_name', ''),
                                    'document_type': obs_data.get('document_type', ''),
                                    'extraction_date': obs_data.get('extraction_date', ''),
                                    'created_at': obs['created_at'].isoformat() if obs['created_at'] else None
                                })
                            except json.JSONDecodeError:
                                # If JSON parsing fails, create a simple observation
                                organized_observations[display_name].append({
                                    'observation': 'Document Analysis',
                                    'value': 'Extracted',
                                    'evidence': obs['source_text'] or 'Document content analysis',
                                    'confidence': 0.5,
                                    'document_name': f"{source_type}_document",
                                    'document_type': source_type,
                                    'extraction_date': obs['created_at'].isoformat() if obs['created_at'] else None,
                                    'created_at': obs['created_at'].isoformat() if obs['created_at'] else None
                                })
                        
                        # Build phenotype from organized observations
                        phenotype_data = _build_phenotype_from_observations(organized_observations)
                        logger.info(f"Built phenotype from database observations for patient {patient.id}")
                        logger.info(f"Built phenotype keys: {list(phenotype_data.keys()) if isinstance(phenotype_data, dict) else 'Not a dict'}")
                        logger.info(f"Built phenotype data: {phenotype_data}")
                    else:
                        logger.warning(f"No observations found in database for patient {patient.id}")
                        phenotype_data = {}
                    
                    conn.close()
                    
                except Exception as e:
                    logger.warning(f"Could not build phenotype from database for patient {patient.id}: {e}")
                    phenotype_data = {}
            
            logger.info(f"Calling build_enhanced_patient_packet with: patient_id={patient.id}, stage_manifest_len={len(stage_manifest)}, completed_stages={completed_stages}")
            
            # Try to build enhanced packet, but don't fail if it doesn't work
            try:
                packet = build_enhanced_patient_packet(
                    patient_id=patient.id,
                    phenotype=phenotype_data,
                    stage_manifest=stage_manifest,
                    completed_stages=completed_stages,
                    progress_percentage=progress_percentage,
                    eligible_actions=eligible_actions
                )
                
                if packet:
                    logger.info(f"Successfully built enhanced packet for patient {patient.id}")
                else:
                    logger.warning(f"build_enhanced_patient_packet returned None for patient {patient.id}")
                    packet = None
                    
            except Exception as e:
                logger.error(f"Error in build_enhanced_patient_packet for patient {patient.id}: {e}")
                packet = None
            
            # If enhanced packet failed, build a more informative fallback packet
            if not packet:
                logger.warning(f"Building enhanced fallback packet for patient {patient.id}")
                
                # Import datetime at the top level to avoid UnboundLocalError
                from datetime import datetime
                
                # Try to get some real data for the fallback
                try:
                    # Get patient age
                    age = None
                    if hasattr(patient, 'dob') and patient.dob:
                        try:
                            # Convert patient.dob to datetime.date if it's a datetime
                            if isinstance(patient.dob, datetime):
                                dob_date = patient.dob.date()
                            else:
                                dob_date = patient.dob
                            age = (datetime.now().date() - dob_date).days // 365
                        except Exception as e:
                            logger.warning(f"Error calculating age for patient {patient.id}: {e}")
                            age = None
                    
                    # Get current stage info (next stage to do, not last completed)
                    current_stage_name = "Unknown"
                    if stage_manifest and completed_stages < len(stage_manifest):
                        current_stage_name = stage_manifest[completed_stages].get('stage_name', 'Unknown')  # Next stage to do
                    elif stage_manifest:
                        current_stage_name = stage_manifest[-1].get('stage_name', 'Unknown')  # Last stage if all completed
                    
                    # Ensure we have valid stage information for AI
                    if current_stage_name == "Unknown" and stage_manifest:
                        # Fallback: use the first incomplete stage
                        for stage in stage_manifest:
                            if stage.get('value') != 'yes':
                                current_stage_name = stage.get('stage_name', 'Unknown')
                                break
                    
                    # Log stage information for debugging
                    logger.info(f"Patient {patient.id} - Stage manifest: {len(stage_manifest) if stage_manifest else 0} stages")
                    logger.info(f"Patient {patient.id} - Completed stages: {completed_stages}")
                    logger.info(f"Patient {patient.id} - Current stage name: {current_stage_name}")
                    logger.info(f"Patient {patient.id} - Progress percentage: {progress_percentage}")
                    
                    # Get some phenotype data if available
                    sleep_study_data = {}
                    phenotype_summary = {}
                    
                    if phenotype_data:
                        # Extract sleep study data from multiple possible locations
                        sleep_study_data = {
                            "type": phenotype_data.get('sleep_study', {}).get('type') or phenotype_data.get('sleep_study_type'),
                            "date": phenotype_data.get('sleep_study', {}).get('date') or phenotype_data.get('sleep_study_date'),
                            "AHI": phenotype_data.get('AHI') or phenotype_data.get('sleep_study', {}).get('AHI') or phenotype_data.get('osa_assessment', {}).get('AHI'),
                            "SpO2_nadir": phenotype_data.get('SpO2_nadir_percent') or phenotype_data.get('sleep_study', {}).get('SpO2_nadir') or phenotype_data.get('osa_assessment', {}).get('SpO2_nadir'),
                            "ODI": phenotype_data.get('ODI') or phenotype_data.get('sleep_study', {}).get('ODI'),
                            "severity": phenotype_data.get('osa_severity') or phenotype_data.get('sleep_study', {}).get('severity') or phenotype_data.get('osa_assessment', {}).get('severity')
                        }
                        
                        # Extract comprehensive phenotype summary
                        phenotype_summary = {
                            "anatomical_findings": {
                                "nasal_obstruction": {
                                    "present": phenotype_data.get('nasal_obstruction_present') or phenotype_data.get('anatomical_findings', {}).get('nasal_obstruction', {}).get('present', False),
                                    "source": phenotype_data.get('nasal_obstruction_source') or phenotype_data.get('anatomical_findings', {}).get('nasal_obstruction', {}).get('source'),
                                    "value": phenotype_data.get('anatomical_findings', {}).get('nasal_obstruction', {}).get('value')
                                },
                                "tmj_findings": {
                                    "present": phenotype_data.get('tmj_findings_present') or phenotype_data.get('anatomical_findings', {}).get('tmj_findings', {}).get('present', False),
                                    "pain_vas": phenotype_data.get('tmj_findings', {}).get('pain_vas') or phenotype_data.get('anatomical_findings', {}).get('tmj_findings', {}).get('pain_vas'),
                                    "clicking": phenotype_data.get('tmj_findings', {}).get('clicking') or phenotype_data.get('anatomical_findings', {}).get('tmj_findings', {}).get('clicking'),
                                    "locking": phenotype_data.get('tmj_findings', {}).get('locking') or phenotype_data.get('anatomical_findings', {}).get('tmj_findings', {}).get('locking')
                                },
                                "primary_narrowing_site": phenotype_data.get('primary_narrowing_site') or phenotype_data.get('anatomical_findings', {}).get('primary_narrowing_site')
                            },
                            "comorbidities": phenotype_data.get('comorbidities', {}),
                            "clinical_findings": phenotype_data.get('clinical_findings', {}),
                            "treatment_history": {
                                "cpap_experience": phenotype_data.get('cpap_experience') or phenotype_data.get('treatment_history', {}).get('cpap_experience', False),
                                "cpap_intolerance": phenotype_data.get('cpap_intolerance') or phenotype_data.get('treatment_history', {}).get('cpap_intolerance', False),
                                "cpap_intolerance_evidence": phenotype_data.get('cpap_intolerance_evidence') or phenotype_data.get('treatment_history', {}).get('cpap_intolerance_evidence'),
                                "oral_appliance_experience": phenotype_data.get('oral_appliance_experience') or phenotype_data.get('treatment_history', {}).get('oral_appliance_experience', False)
                            }
                        }
                        
                        logger.info(f"Patient {patient.id} - Extracted sleep study data: {sleep_study_data}")
                        logger.info(f"Patient {patient.id} - Extracted phenotype summary: {phenotype_summary}")
                    
                    # Build standardized packet using canonical schema for clinical data
                    packet = {
                        "patient": {
                            "id": str(patient.id),
                            "sex": patient.gender or "unknown",
                            "age": age,
                            "demographics": {
                                "name": patient.name or "Unknown",
                                "email": patient.email or "",
                                "phone": patient.phone or ""
                            }
                        },
                        "policy_context": {
                            "policy_version": "osa_policy_v2"
                        },
                        # Use canonical schema for clinical data (standardized)
                        "canonical_clinical_data": canonical_data if canonical_data else {
                            "demographics": {
                                "sex": patient.gender,
                                "age_years": age,
                                "height_cm": None,
                                "weight_kg": None,
                                "bmi": None
                            },
                            "sleep_study": {
                                "study_type": sleep_study_data.get('type', 'unknown'),
                                "sleep_duration_h": None,
                                "sleep_efficiency_pct": None,
                                "ahi": sleep_study_data.get('AHI'),
                                "odi": sleep_study_data.get('ODI'),
                                "desaturation_events": None,
                                "o2_nadir_pct": sleep_study_data.get('SpO2_nadir'),
                                "snoring": {"avg_db": None, "max_db": None}
                            },
                            "observations": {
                                "summary": [],
                                "anatomy_imaging": {
                                    "primary_obstruction_site": None,
                                    "soft_palate_uvula": None,
                                    "tongue_base": None,
                                    "bite_jaw": None,
                                    "hyoid": None,
                                    "nose_sinus": None,
                                    "tmj": None
                                },
                                "tmj_flags": {"pain": None, "clicking": None, "side": None}
                            },
                            "treatment_considerations": {
                                "primary_pathway": [],
                                "adjuncts": [],
                                "cautions": [],
                                "rationale": None
                            },
                            "device_design": {
                                "mandibular_advancement_mm": None,
                                "advancement_plan": None,
                                "vertical_opening_mm": None,
                                "anterior_window": None,
                                "retention_features": [],
                                "material": None,
                                "coverage": None,
                                "initial_accessories": []
                            },
                            "follow_up_plan": {
                                "evaluations": [],
                                "lifestyle": [],
                                "positional_therapy": None,
                                "retest_after_init_months": None
                            },
                            "device_options": [],
                            "provenance": [],
                            "validation": {},
                            "confidence": {},
                            "completeness_flags": {}
                        },
                        "stage_context": {
                            "stage": current_stage_name,
                            "completion_pct": progress_percentage
                        },
                        # Keep operational data as-is (no changes needed)
                        "operational_data": {
                            "workflow_progress": {
                                "current_stage": current_stage_name,
                                "completion_pct": progress_percentage,
                                "total_stages": len(stage_manifest) if stage_manifest else 0,
                                "current_stage_index": completed_stages
                            },
                            "pending_actions": [
                                {
                                    "action": a.get('label', 'Unknown action'),
                                    "due_in_days": 0,
                                    "priority": "normal",
                                    "blocking": True
                                } for a in (eligible_actions or [])[:3]
                            ],
                            "device_tracking": {
                                "last_device_event": "unknown",
                                "device_status": "unknown"
                            },
                            "alerts": [],
                            "consultations": []
                        },
                        "protocols": {
                            "Vizbriz_Workflow": {
                                "version": "2.0",
                                "steps": [stage.get('stage_name', 'Unknown stage') for stage in (stage_manifest or [])]
                            }
                        },
                        "meta": {
                            "schema_version": 2,
                            "packet_hash": "",
                            "generated_at": datetime.now().isoformat(),
                            "data_sources": ["Canonical Schema" if canonical_data else "Enhanced Fallback"]
                        }
                    }
                    
                    logger.info(f"Built enhanced fallback packet for patient {patient.id}")
                    
                except Exception as e:
                    logger.error(f"Error building enhanced fallback packet for patient {patient.id}: {e}")
                    # Ultimate fallback - basic packet
                    packet = {
                        "patient": {
                            "id": str(patient.id),
                            "sex": patient.gender or "unknown",
                            "age": None
                        },
                        "policy_context": {
                            "policy_version": "osa_policy_v2"
                        },
                        "sleep_study": {
                            "type": "unknown",
                            "date": None,
                            "AHI": None,
                            "SpO2_nadir": None,
                            "ODI": None,
                            "severity": "unknown"
                        },
                        "phenotype_highlights": {
                            "applies_to": {
                                "patient_id": str(patient.id),
                                "phenotype_summary": {}
                            }
                        },
                        "policy_features": {
                            "workflow_state": {
                                "current_stage": "Unknown",
                                "completed_stages": [],
                                "pending_actions": []
                            },
                            "clinical_flags": {
                                "contraindications": [],
                                "risk_factors": [],
                                "special_considerations": []
                            }
                        },
                        "stage_context": {
                            "stage": "Unknown",
                            "completion_pct": 0
                        },
                        "operational_data": {
                            "workflow_progress": {
                                "current_stage": "Unknown",
                                "completion_pct": 0,
                                "total_stages": 0,
                                "current_stage_index": 0
                            },
                            "pending_actions": [],
                            "device_tracking": {
                                "last_device_event": "unknown",
                                "device_status": "unknown"
                            },
                            "alerts": [],
                            "consultations": []
                        },
                        "clinical_data": {
                            "vitals": {},
                            "questionnaire_scores": {},
                            "imaging_data": {
                                "cbct_available": False,
                                "intraoral_scans_available": False,
                                "clinical_photos_available": False
                            }
                        },
                        "protocols": {
                            "Vizbriz_Workflow": {
                                "version": "2.0",
                                "steps": []
                            }
                        },
                        "meta": {
                            "schema_version": 2,
                            "packet_hash": "",
                            "generated_at": datetime.now().isoformat(),
                            "data_sources": ["Basic Fallback"]
                        }
                    }
            
            logger.info(f"Successfully built packet for patient {patient.id} (enhanced: {packet is not None})")
                
        except Exception as e:
            logger.error(f"Error building enhanced packet for patient {patient.id}: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return jsonify({'success': False, 'message': f'Error building patient packet: {str(e)}'}), 500
        
        # Load the enhanced prompt template
        try:
            template_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'enhanced_prompt_template.txt')
            with open(template_path, 'r') as f:
                template = f.read()
        except Exception as e:
            logger.error(f"Failed to load enhanced prompt template: {e}")
            # Fallback to basic template
            template = "SYSTEM\nYou are the Vizbriz OSA agent. Use only the provided JSON packet.\nProduce two sections in one response: (A) Clinical and (B) Operational.\n\nUSER\n## PATIENT EXECUTION PACKET (Enhanced JSON)\n<<<PACKET_JSON>>>\n\n## TASK\nReturn a single JSON object matching the schema in \"OUTPUT_SCHEMA\".\n- Be specific and concise.\n- Base clinical reasoning on `ai_phenotype_summary` + `phenotype_highlights` + `sleep_study` + `clinical_data`.\n- Pay special attention to:\n  * `ai_phenotype_summary.primary_pathway` for treatment pathway\n  * `ai_phenotype_summary.key_anatomical_findings` for TMJ dysfunction, nasal obstruction, and primary narrowing site\n  * `ai_phenotype_summary.comorbidities` for patient comorbidities\n  * `ai_phenotype_summary.sleep_study_data` for AHI, severity, and SpO2 nadir.\n- Base operational reasoning on `operational_data` + `stage_context`.\n- Do not include PII beyond patient_id.\n- Do not restate raw observations or long lists.\n- Respond with VALID JSON ONLY — no markdown or prose."
        
        # Render the prompt with the packet
        prompt = render_single_prompt(packet, template)
        logger.info(f"Generated prompt length for patient {patient.id}: {len(prompt)} characters")
        logger.info(f"Packet for patient {patient.id}: {packet}")
        
        # Comprehensive logging for AI Agent prompt
        logger.info(f" AI AGENT PROMPT for patient {patient.id}:")
        logger.info("=" * 80)
        logger.info(prompt)
        logger.info("=" * 80)
        
        # PROGRESSIVE LOADING: Skip LLM call for initial page load
        # LLM data will be loaded asynchronously via JavaScript
        logger.info(f"Skipping LLM call for initial page load - will load asynchronously")
        response = {"success": False, "response": "Loading asynchronously..."}
        
        # Comprehensive logging for AI Agent response
        logger.info(f" AI AGENT RESPONSE for patient {patient.id}:")
        logger.info("=" * 80)
        logger.info(f"Response success: {response.get('success')}")
        logger.info(f"Response content: {response}")
        logger.info("=" * 80)
        
        # Initialize clinical and operational data variables
        clinical = {}
        operational = {}
        
        # Parse and validate the response
        clinical_summary = 'Clinical assessment unavailable.'
        operational_summary = 'Operational status unavailable.'
        ai_guidance = 'AI guidance temporarily unavailable.'
        clinical_vm = None
        operational_vm = None
        
        if response.get('success'):
            try:
                # Parse JSON response with enhanced Bedrock response handling
                response_text = response.get('response', '')
                
                # Try to parse the response as JSON first
                try:
                    response_json = json.loads(response_text)
                    # Check if it's a nested Bedrock response
                    if 'content' in response_json and isinstance(response_json['content'], list) and len(response_json['content']) > 0:
                        # Extract the text from the first content item
                        inner_text = response_json['content'][0].get('text', '')
                        logger.info(f" EXTRACTED INNER TEXT from Bedrock response:")
                        logger.info(inner_text)
                        # Parse the inner text as JSON directly
                        try:
                            parsed_response = json.loads(inner_text)
                        except json.JSONDecodeError:
                            # If inner text is not valid JSON, fallback to regex parsing
                            parsed_response = parse_llm_json(inner_text)
                    else:
                        # Direct JSON response
                        parsed_response = response_json
                except json.JSONDecodeError:
                    # Fallback to regex parsing
                    parsed_response = parse_llm_json(response_text)
                
                # Basic validation
                basic_validate_response(parsed_response)
                
                # Extract clinical and operational summaries from enhanced response
                clinical = parsed_response.get('clinical', {})
                operational = parsed_response.get('operational', {})

                # Build view models for template consumption
                try:
                    clinical_vm, operational_vm = build_view_models_from_llm(parsed_response, fallback_packet=packet)
                except Exception as _vm_err:
                    logger.warning(f"View model build failed: {_vm_err}")
                
                # Build enhanced clinical summary
                diagnosis = clinical.get('diagnosis', 'Diagnosis pending')
                phenotype_summary = clinical.get('phenotype_summary', '')
                next_action = clinical.get('next_clinical_action', 'Next action pending')
                treatment_recommendations = clinical.get('treatment_recommendations', [])
                rules_fired = clinical.get('rules_fired', [])
                risks_and_monitoring = clinical.get('risks_and_monitoring', [])
                
                # Enhanced clinical summary with phenotype information
                if phenotype_summary:
                    clinical_summary = f"{diagnosis}. {phenotype_summary}. {next_action}"
                    
                    # Parse LLM phenotype summary to extract anatomical findings
                    llm_anatomical_findings = _parse_llm_phenotype_summary(phenotype_summary)
                    
                    # Merge LLM findings with existing (or new) phenotype data
                    if llm_anatomical_findings:
                        if not isinstance(phenotype_data, dict):
                            phenotype_data = {}
                        if 'anatomical_findings' not in phenotype_data or not isinstance(phenotype_data['anatomical_findings'], dict):
                            phenotype_data['anatomical_findings'] = {}
                        
                        # Merge airway findings
                        if 'airway_findings' in llm_anatomical_findings:
                            if 'airway_findings' not in phenotype_data['anatomical_findings'] or not isinstance(phenotype_data['anatomical_findings'].get('airway_findings'), dict):
                                phenotype_data['anatomical_findings']['airway_findings'] = {}
                            
                            for finding_key, finding_data in llm_anatomical_findings['airway_findings'].items():
                                phenotype_data['anatomical_findings']['airway_findings'][finding_key] = finding_data

                            # Map LLM-only terms to fields the template actually renders
                            # - If we detected wall collapse, show it under Primary Obstruction
                            if 'medial_wall_collapse' in phenotype_data['anatomical_findings']['airway_findings'] and not phenotype_data['anatomical_findings']['airway_findings'].get('primary_obstruction_level'):
                                phenotype_data['anatomical_findings']['airway_findings']['primary_obstruction_level'] = phenotype_data['anatomical_findings']['airway_findings']['medial_wall_collapse']
                        
                        # Merge TMJ findings
                        if 'tmj_findings' in llm_anatomical_findings:
                            phenotype_data['anatomical_findings']['tmj_findings'] = llm_anatomical_findings['tmj_findings']
                        
                        # Merge other findings
                        if 'other_findings' in llm_anatomical_findings:
                            phenotype_data['anatomical_findings']['other_findings'] = llm_anatomical_findings['other_findings']
                        
                        logger.info(f"Patient {patient.id} - Merged LLM anatomical findings: {llm_anatomical_findings}")

                        # ALSO inject merged phenotype into the structure used by the template
                        try:
                            if isinstance(osa_policy_manifest_with_phenotype, dict):
                                if 'applies_to' not in osa_policy_manifest_with_phenotype:
                                    osa_policy_manifest_with_phenotype['applies_to'] = {}
                                osa_policy_manifest_with_phenotype['applies_to']['phenotype_summary'] = phenotype_data
                                logger.info("Injected merged phenotype into osa_policy_manifest_with_phenotype for template rendering")
                        except Exception as _e:
                            logger.warning(f"Could not inject merged phenotype into UI object: {_e}")
                else:
                    clinical_summary = f"{diagnosis}. {next_action}"
                
                # Add treatment recommendations if available
                if treatment_recommendations:
                    clinical_summary += f" Recommendations: {', '.join(treatment_recommendations[:2])}."
                
                # Build enhanced operational summary
                stage = operational.get('stage', 'Stage unknown')
                completion = operational.get('completion_pct', 0)
                workflow_status = operational.get('workflow_status', '')
                next_actions = operational.get('next_actions', [])
                alerts = operational.get('alerts', [])
                
                if next_actions:
                    next_action_desc = next_actions[0].get('action', 'Next action pending')
                    priority = next_actions[0].get('priority', 'normal')
                    operational_summary = f"Currently in {stage} stage ({completion:.1f}% complete). {next_action_desc} (Priority: {priority})"
                else:
                    operational_summary = f"Currently in {stage} stage ({completion:.1f}% complete)."
                
                # Add workflow status and alerts
                if workflow_status:
                    operational_summary += f" {workflow_status}"
                if alerts:
                    operational_summary += f" Alerts: {', '.join(alerts)}."
                
                # Set AI guidance to enhanced clinical summary
                ai_guidance = clinical_summary
                
                # Comprehensive logging for parsed AI response
                logger.info(f" PARSED AI RESPONSE for patient {patient.id}:")
                logger.info("=" * 80)
                logger.info(f"CLINICAL SECTION:")
                logger.info(f"  - Diagnosis: {clinical.get('diagnosis', 'Not provided')}")
                logger.info(f"  - Phenotype Summary: {clinical.get('phenotype_summary', 'Not provided')}")
                logger.info(f"  - Next Clinical Action: {clinical.get('next_clinical_action', 'Not provided')}")
                logger.info(f"  - Treatment Recommendations: {clinical.get('treatment_recommendations', [])}")
                logger.info(f"  - Rules Fired: {clinical.get('rules_fired', [])}")
                logger.info(f"  - Risks and Monitoring: {clinical.get('risks_and_monitoring', [])}")
                logger.info(f"OPERATIONAL SECTION:")
                logger.info(f"  - Stage: {operational.get('stage', 'Not provided')}")
                logger.info(f"  - Completion %: {operational.get('completion_pct', 'Not provided')}")
                logger.info(f"  - Workflow Status: {operational.get('workflow_status', 'Not provided')}")
                logger.info(f"  - Next Actions: {operational.get('next_actions', [])}")
                logger.info(f"  - Alerts: {operational.get('alerts', [])}")
                logger.info("=" * 80)
                
                logger.info(f"Successfully parsed structured response for patient {patient.id}")
                
            except Exception as e:
                logger.error(f"Error parsing structured response: {e}")
                # Fallback to simple text parsing
                response_text = response.get('response', '')
                if 'CLINICAL SUMMARY:' in response_text and 'OPERATIONAL SUMMARY:' in response_text:
                    try:
                        clinical_start = response_text.find('CLINICAL SUMMARY:') + len('CLINICAL SUMMARY:')
                        operational_start = response_text.find('OPERATIONAL SUMMARY:')
                        clinical_summary = response_text[clinical_start:operational_start].strip()
                        
                        operational_start = response_text.find('OPERATIONAL SUMMARY:') + len('OPERATIONAL SUMMARY:')
                        operational_summary = response_text[operational_start:].strip()
                        
                        ai_guidance = clinical_summary
                    except Exception as e2:
                        logger.error(f"Fallback parsing also failed: {e2}")
                else:
                    # Use better fallback messages instead of raw JSON
                    ai_guidance = 'Clinical assessment temporarily unavailable. Please try refreshing the page.'
                    clinical_summary = 'Clinical assessment temporarily unavailable. Please try refreshing the page.'
                    operational_summary = 'Operational status temporarily unavailable. Please try refreshing the page.'
        else:
            logger.error(f"Single-prompt Bedrock call failed: {response}")
            # Set default messages when Bedrock call fails
            clinical_summary = 'Clinical assessment unavailable due to technical issues. Please try refreshing the page.'
            operational_summary = 'Operational status unavailable due to technical issues. Please try refreshing the page.'
            ai_guidance = 'AI guidance temporarily unavailable. Please try refreshing the page.'
        
        # Debug logging for single-prompt response
        logger.info(f"Patient {patient.id} - Single-prompt response success: {response.get('success')}")
        if response.get('success'):
            logger.info(f"Patient {patient.id} - Clinical summary: {clinical_summary}")
            logger.info(f"Patient {patient.id} - Operational summary: {operational_summary}")
        else:
            logger.error(f"Patient {patient.id} - Single-prompt response failed: {response}")
        
        # Combine for patient status summary
        patient_status_summary = f"{clinical_summary} {operational_summary}".strip()
        
        # Use clinical summary as AI guidance for treatment recommendations
        ai_guidance = clinical_summary
        
        # Debug logging for phenotype data
        logger.info(f"Patient {patient.id} - Compact phenotype: {compact_phenotype}")
        logger.info(f"Patient {patient.id} - OSA Assessment AHI: {phenotype.get('osa_assessment', {}).get('AHI')}")
        logger.info(f"Patient {patient.id} - OSA Assessment severity: {phenotype.get('osa_assessment', {}).get('severity')}")
        
        # Determine OSA assessment level for top-right indicator
        osa_assessment_level = 'none'
        
        # First try to get OSA severity from canonical data
        if canonical_data and canonical_data.get('sleep_study', {}).get('ahi'):
            try:
                ahi_value = float(canonical_data['sleep_study']['ahi'])
                if ahi_value >= 30:
                    osa_assessment_level = 'high'
                elif ahi_value >= 15:
                    osa_assessment_level = 'medium'
                elif ahi_value >= 5:
                    osa_assessment_level = 'low'
                logger.info(f"Patient {patient.id} - OSA assessment level from canonical AHI {ahi_value}: {osa_assessment_level}")
            except (ValueError, TypeError):
                pass
        
        # Fallback to phenotype data if canonical data doesn't have AHI
        if osa_assessment_level == 'none':
            if phenotype.get('osa_severity'):
                severity = phenotype.get('osa_severity', '').lower()
                if severity in ['severe', 'moderate']:
                    osa_assessment_level = 'high'
                elif severity == 'mild':
                    osa_assessment_level = 'medium'
                elif severity == 'normal':
                    osa_assessment_level = 'low'
            elif phenotype.get('AHI'):
                # If we have AHI but no severity, calculate it
                try:
                    ahi_value = float(phenotype.get('AHI', 0))
                    if ahi_value >= 30:
                        osa_assessment_level = 'high'
                    elif ahi_value >= 15:
                        osa_assessment_level = 'medium'
                    elif ahi_value >= 5:
                        osa_assessment_level = 'low'
                except:
                    pass
        
        import os
        import time
        upload_base = os.environ.get('BASE_URL', '').rstrip('/')
        
        # Convert patient object to dictionary for JSON serialization
        patient_dict = {
            'id': patient.id,
            'name': patient.name,
            'email': patient.email,
            'phone': patient.phone,
            'dob': patient.dob.isoformat() if patient.dob else None,
            'gender': patient.gender,
            'status': patient.status,
            'create_date': patient.create_date.isoformat() if patient.create_date else None,
            'last_update': patient.last_update.isoformat() if patient.last_update else None,
            'dentist_id': patient.dentist_id,
            'clinic_id': patient.clinic_id,
            'insurer': patient.insurer,
            'policy_id': patient.policy_id,
            'address': patient.address,
            'claim': patient.claim,
            'payment_method': patient.payment_method
        }
        
        # Prepare debug variables using the actual AI interaction data
        prompt_sent = None
        response_received = None
        timestamp = None
        session_id = None
        
        # Use the actual AI response data that was just generated
        if response and response.get('success'):
            try:
                # Get the actual prompt and response from the Bedrock call
                if 'prompt' in response:
                    prompt_sent = response['prompt']
                if 'response' in response:
                    response_received = response['response']
                if 'session_id' in response:
                    session_id = response['session_id']
                if 'timestamp' in response:
                    timestamp = response['timestamp']
                
                # If we don't have the data in the response, try to get it from the logger
                if not prompt_sent or not response_received:
                    from flask_app.config.bedrock_config import BedrockPromptLogger
                    logger_instance = BedrockPromptLogger()
                    recent_sessions = logger_instance.list_recent_sessions(hours=1)
                    
                    if recent_sessions:
                        # Get the most recent session
                        latest_session_id = list(recent_sessions.keys())[0]
                        session_files = logger_instance.get_session_files(latest_session_id)
                        
                        if session_files:
                            # Get the most recent prompt and response
                            for file_info in session_files:
                                if 'prompt' in file_info['filename'].lower() and not prompt_sent:
                                    with open(file_info['filepath'], 'r') as f:
                                        prompt_sent = f.read()
                                elif 'response' in file_info['filename'].lower() and not response_received:
                                    with open(file_info['filepath'], 'r') as f:
                                        response_received = f.read()
                            
                            if not session_id:
                                session_id = latest_session_id
                            if not timestamp:
                                timestamp = recent_sessions[latest_session_id].get('timestamp', '')
            except Exception as e:
                logger.warning(f"Could not load debug data: {e}")
        
        # Debug logging for template variables
        logger.info(f" TEMPLATE VARIABLES for patient {patient.id}:")
        logger.info(f"  - clinical: {clinical}")
        logger.info(f"  - operational: {operational}")
        logger.info(f"  - eligible_actions: {len(eligible_actions) if eligible_actions else 0} actions")
        logger.info(f"  - clinical_summary: {clinical_summary}")
        logger.info(f"  - operational_summary: {operational_summary}")
        logger.info(f"  - ai_guidance: {ai_guidance}")
        # Extra visibility: exactly what phenotype/anatomical data the template will see
        try:
            phenotype_for_template = {}
            if isinstance(osa_policy_manifest_with_phenotype, dict):
                phenotype_for_template = (
                    osa_policy_manifest_with_phenotype
                    .get('applies_to', {})
                    .get('phenotype_summary', {})
                ) or {}

            logger.info("  - phenotype available to template: %s", isinstance(phenotype_for_template, dict))
            if isinstance(phenotype_for_template, dict) and phenotype_for_template:
                # Sleep study snapshot
                ss = phenotype_for_template.get('sleep_study', {}) or {}
                logger.info(
                    "    sleep_study -> type=%s, AHI=%s, SpO2_nadir=%s, ODI=%s, severity=%s",
                    ss.get('type'), ss.get('AHI'), ss.get('SpO2_nadir'), ss.get('ODI'), ss.get('severity')
                )

                # Anatomical findings snapshot
                af = phenotype_for_template.get('anatomical_findings', {}) or {}
                logger.info("    anatomical_findings keys: %s", list(af.keys()))
                airway = af.get('airway_findings', {}) or {}
                tmj = af.get('tmj_findings', {}) or {}
                other = af.get('other_findings')
                logger.info("    airway_findings keys: %s", list(airway.keys()))
                logger.info("    tmj_findings present=%s, details=%s", tmj.get('present'), tmj.get('details'))
                if isinstance(airway, dict):
                    # Log any sources to confirm LLM merge
                    sources = {
                        k: (v.get('source') if isinstance(v, dict) else None)
                        for k, v in airway.items()
                    }
                    logger.info("    airway_findings sources: %s", sources)
                logger.info("    other_findings: %s", other)
        except Exception as _e:
            logger.warning(f"  - phenotype logging skipped due to error: {_e}")
        
        # Get the current stage from manifest (uses highest completed, not first incomplete)
        current_stage = None
        if manifest_data and isinstance(manifest_data, dict):
            current_stage = manifest_data.get('current_stage')
        
        if not current_stage:
            # Fallback to old logic if manifest doesn't have current_stage
            if completed_stages < len(stage_manifest):
                current_stage = stage_manifest[completed_stages]
            elif stage_manifest:
                current_stage = stage_manifest[-1]  # Last stage if all completed
        
        # Get email logs for patient timeline
        email_logs = []
        try:
            from flask_app.models import EmailLog
            email_logs = EmailLog.query.filter_by(patient_id=patient_id).order_by(EmailLog.sent_at.desc()).all()
            logger.info(f"Found {len(email_logs)} email logs for patient {patient_id}")
        except Exception as e:
            logger.error(f"Error fetching email logs for patient {patient_id}: {e}")
            email_logs = []
        
        # Get OSA reports (public admin files) for the patient
        osa_reports = []
        try:
            admin_files = AdminFile.query.filter_by(
                patient_id=patient_id, 
                is_public=True
            ).order_by(AdminFile.upload_date.desc()).all()
            
            # Convert AdminFile objects to dictionaries with presigned URLs
            osa_reports = []
            for file in admin_files:
                try:
                    # Generate presigned URL like the patient files API
                    s3_client = boto3.client('s3', region_name='us-west-2')
                    bucket = os.getenv('S3_BUCKET_NAME', 'vizbrizpatients')
                    presigned_url = s3_client.generate_presigned_url(
                        'get_object',
                        Params={
                            'Bucket': bucket,
                            'Key': file.s3_key,
                            'ResponseContentDisposition': 'inline',
                            'ResponseContentType': file.file_type or 'application/octet-stream'
                        },
                        ExpiresIn=3600
                    )
                    
                    # Convert to dictionary for JSON serialization
                    osa_reports.append({
                        'id': file.id,
                        'name': file.name,
                        'file_type': file.file_type,
                        'file_category': file.file_category,
                        'upload_date': file.upload_date,
                        's3_key': file.s3_key,
                        'view_url': presigned_url,
                        'is_public': file.is_public
                    })
                except Exception as e:
                    logger.error(f"Error generating presigned URL for file {file.id}: {e}")
                    # Still add the file without presigned URL
                    osa_reports.append({
                        'id': file.id,
                        'name': file.name,
                        'file_type': file.file_type,
                        'file_category': file.file_category,
                        'upload_date': file.upload_date,
                        's3_key': file.s3_key,
                        'view_url': None,
                        'is_public': file.is_public
                    })
            logger.info(f"Found {len(osa_reports)} OSA reports for patient {patient_id}")
            
            # Ensure all OSA reports have proper structure for template
            for report in osa_reports:
                if not isinstance(report, dict):
                    logger.warning(f"OSA report is not a dictionary: {type(report)}")
                    continue
                # Add default values for missing fields that template expects
                if 'date' not in report:
                    report['date'] = report.get('upload_date')
                if 'file_name' not in report:
                    report['file_name'] = report.get('name', 'Unknown File')
                # Ensure ahi field exists (even if null) to prevent template errors
                if 'ahi' not in report:
                    report['ahi'] = None
                    logger.info(f"OSA report {report.get('name', 'Unknown')} has no AHI data - this is normal for non-sleep-study files")
        except Exception as e:
            logger.error(f"Error fetching OSA reports for patient {patient_id}: {e}")
            osa_reports = []
        
        # Get DSOs, clinics, and dentists data for patient editing
        from flask_app.models import DSO, Clinic, Dentist
        dsos = DSO.query.all()
        clinics_by_dso = {}
        dentists_by_clinic = {}
        
        for dso in dsos:
            clinics = Clinic.query.filter_by(dso_id=dso.id, status='active').all()
            clinics_by_dso[dso.id] = [{'id': c.id, 'name': c.name} for c in clinics]
            
            for clinic in clinics:
                dentists = Dentist.query.join(dentist_clinic_association).filter(
                    dentist_clinic_association.c.clinic_id == clinic.id
                ).all()
                dentists_by_clinic[clinic.id] = [{'id': d.id, 'name': d.name} for d in dentists]

        is_processing, processing_status, processing_queue_info = _abandon_stale_queue_rows_and_get_active_processing(patient_id)

        canonical_data, patient_age = _merge_level1_demographics(patient, canonical_data, patient_age)

        if isinstance(canonical_data, dict) and getattr(patient, "id", None):
            if not canonical_data.get("patient_id"):
                canonical_data["patient_id"] = str(patient.id)
            _enrich_clinical_manifest_cards(patient, canonical_data)

        return render_template('patient_workflow_manifest.html', 
                             patient=patient, 
                             patient_dict=patient_dict,
                             patient_age=patient_age,
                             manifest_data=manifest_data,
                             progress_percentage=progress_percentage,
                             completed_stages=completed_stages,
                             total_stages=total_stages,
                             current_stage=current_stage,
                             eligible_actions=eligible_actions,
                             all_actions=all_actions_list,
                             ai_guidance=ai_guidance,
                             patient_status_summary=patient_status_summary,
                             clinical_summary=clinical_summary,
                             operational_summary=operational_summary,
                             clinical=clinical_vm or clinical,
                             operational=operational_vm or operational,
                             osa_assessment_level=osa_assessment_level,
                             osa_policy_manifest=osa_policy_manifest,
                             osa_policy_manifest_source=osa_policy_manifest_source,
                             osa_policy_manifest_with_phenotype=osa_policy_manifest_with_phenotype,
                             canonical_data=canonical_data,  # Add canonical schema data
                             base_url=upload_base,
                             prompt_sent=prompt_sent,
                             response_received=response_received,
                             timestamp=timestamp,
                             session_id=session_id,
                             email_logs=email_logs,  # Add email logs for timeline
                             osa_reports=osa_reports,  # Add OSA reports
                             is_admin=current_user.role == 'admin',  # Add is_admin flag
                             dsos=dsos,  # Add DSOs for admin clinic selection
                             clinics_by_dso=clinics_by_dso,  # Add clinics by DSO
                             dentists_by_clinic=dentists_by_clinic,  # Add dentists by clinic
                             is_processing=is_processing,  # Add processing status
                             processing_status=processing_status,  # Add processing status detail
                             processing_queue_info=processing_queue_info,
                             cache_buster=int(time.time()))  # Force cache refresh

    except Exception as e:
        import traceback
        logger.error(f"Error in patient_workflow_manifest: {e}")
        logger.error(f"Full traceback:\n{traceback.format_exc()}")
        flash(f'Error loading patient workflow: {str(e)}', 'error')
        # Check if it's the specific 'dict object' has no attribute 'ahi' error
        if "'dict object' has no attribute 'ahi'" in str(e):
            logger.info(f"Patient {patient_id} - No sleep study data detected (normal state) - creating fallback to allow page to load")
            
            # Create a normal phenotype for patients without sleep study data
            phenotype = {
                'sleep_study': {
                    'type': 'none',
                    'date': None,
                    'AHI': None,
                    'SpO2_nadir': None,
                    'ODI': None,
                    'severity': 'unknown',
                    'status': 'no_data_uploaded'
                },
                'osa_assessment': {'AHI': None, 'severity': 'unknown'},
                'data_quality': ['no_sleep_study_data']
            }
            
            # Create minimal canonical data
            canonical_data = {
                'canonical_derived': {
                    'sleep_study': phenotype['sleep_study'],
                    'timeline': {'sleep_studies': [], 'reports_grouped': [], 'reports': []}
                }
            }
            
            # Create minimal manifest data
            manifest_data = {
                'completed_stages': [],
                'current_stage': 'initial_consultation',
                'progress_percentage': 0
            }
            
            # Calculate patient age if not already available
            if 'patient_age' not in locals():
                try:
                    from datetime import datetime
                    if patient and patient.date_of_birth:
                        birth_date = datetime.strptime(patient.date_of_birth, '%Y-%m-%d')
                        today = datetime.now()
                        patient_age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
                    else:
                        patient_age = 0
                except:
                    patient_age = 0
            
            # Create minimal patient data
            patient_dict = {
                'id': patient_id,
                'name': patient.name if patient else 'Unknown',
                'age': patient_age
            }
            
            # Set default values for other required variables
            eligible_actions = []
            all_actions_list = []
            email_logs = []
            osa_reports = []
            
            # Continue with minimal data instead of redirecting
            logger.info(f"Patient {patient_id} - Using fallback phenotype for patient without sleep study data")
            
            # Get DSOs, clinics, and dentists data for patient editing (fallback)
            from flask_app.models import DSO, Clinic, Dentist
            dsos = DSO.query.all()
            clinics_by_dso = {}
            dentists_by_clinic = {}
            
            for dso in dsos:
                clinics = Clinic.query.filter_by(dso_id=dso.id, status='active').all()
                clinics_by_dso[dso.id] = [{'id': c.id, 'name': c.name} for c in clinics]
                
                for clinic in clinics:
                    dentists = Dentist.query.join(dentist_clinic_association).filter(
                        dentist_clinic_association.c.clinic_id == clinic.id
                    ).all()
                    dentists_by_clinic[clinic.id] = [{'id': d.id, 'name': d.name} for d in dentists]

            is_processing, processing_status, processing_queue_info = _abandon_stale_queue_rows_and_get_active_processing(patient_id)

            canonical_data, patient_age = _merge_level1_demographics(patient, canonical_data, patient_age)
            patient_dict['age'] = patient_age

            if isinstance(canonical_data, dict) and getattr(patient, "id", None):
                if not canonical_data.get("patient_id"):
                    canonical_data["patient_id"] = str(patient.id)
                _enrich_clinical_manifest_cards(patient, canonical_data)

            # Render template with fallback data
            return render_template('patient_workflow_manifest.html', 
                                 patient=patient, 
                                 patient_dict=patient_dict,
                                 patient_age=patient_age,
                                 manifest_data=manifest_data,
                                 progress_percentage=0,
                                 completed_stages=[],
                                 total_stages=0,
                                 current_stage='initial_consultation',
                                 eligible_actions=eligible_actions,
                                 all_actions=all_actions_list,
                                 canonical_data=canonical_data,
                                 phenotype=phenotype,
                                 prompt_sent=False,
                                 response_received=False,
                                 timestamp=None,
                                 session_id=None,
                                 email_logs=email_logs,
                                 osa_reports=osa_reports,
                                 is_admin=current_user.role == 'admin',  # Add is_admin flag
                                 dsos=dsos,  # Add DSOs for admin clinic selection
                                 clinics_by_dso=clinics_by_dso,  # Add clinics by DSO
                                 dentists_by_clinic=dentists_by_clinic,  # Add dentists by clinic
                                 is_processing=is_processing,  # Add processing status
                                 processing_status=processing_status,  # Add processing status detail
                                 processing_queue_info=processing_queue_info,
                                 cache_buster=int(time.time()))
        else:
            flash(f'Error loading patient workflow: {str(e)}', 'error')
            return redirect(url_for('main.patient_list'))



def register_workflow_manifest_routes(main):
    """Register workflow manifest routes onto the main Blueprint."""
    main.add_url_rule(
        '/patient_workflow_manifest/<int:patient_id>',
        endpoint='patient_workflow_manifest',
        view_func=login_required(patient_workflow_manifest),
        methods=['GET']
    )
