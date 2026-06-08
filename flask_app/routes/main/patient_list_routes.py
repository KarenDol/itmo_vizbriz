from __future__ import annotations

import json
import logging
import traceback
from typing import Any

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from flask_app import db
from flask_app.models import (
    Clinic,
    Dentist,
    Patient,
    PatientComment,
    PatientConsultSchedule,
    PatientStageSummaryCache,
    dentist_clinic_association,
)

logger = logging.getLogger(__name__)


@login_required
def patients_operational() -> Any:
    """Operational patient list page"""
    return render_template("patients_operational.html")


@login_required
def patient_list() -> Any:
    """Patient list page - uses shared get_accessible_patients for consistency with forms."""
    from flask_app.helpers.patient_access_helpers import get_accessible_patients

    if current_user.role not in ["admin"] and current_user.role not in ["Dentist", "dentist", "Dentists"]:
        flash("Unauthorized access", "error")
        logger.warning(f"Unauthorized access attempt by user {current_user.name} with role {current_user.role}")
        return redirect(url_for("main.index"))

    include_archived = False
    if current_user.role == "admin":
        include_archived = request.args.get("include_archived", "false").lower() == "true"

    patients = get_accessible_patients(include_archived=include_archived)
    logger.debug(f"Patient list: {len(patients)} patients for {current_user.name}")

    return render_template("patient_list.html", patients=patients, include_archived=include_archived)


@login_required
def get_operational_patients_list() -> Any:
    """Get operational patient list with AI insights, progress, and priority"""
    try:
        from flask_app.config.stage_summary_manifest import get_stage_summary_manifest
        from flask_app.services.stage_summary_service import evaluate_stage_completion
        from flask_app.services.manifest_service import ManifestService

        # Get search and sort parameters
        search_query = request.args.get("search", "").strip()
        sort_by = request.args.get("sort", "last_update")  # last_update, priority, progress, stage

        # Apply same access control rules as patient_list
        # If the current user is an admin, they can see all patients
        if current_user.role == "admin":
            # Build base query - get all active patients (exclude only Archived status)
            query = Patient.query.filter(db.or_(Patient.status.is_(None), Patient.status != "Archived"))
            logger.debug("Admin viewing all patients for operational list")

        elif current_user.role in ["Dentist", "dentist", "Dentists"]:
            # Dentist can only see patients associated with the same clinic(s) as the dentist
            logger.debug(f"Dentist {current_user.name} attempting to view operational list based on clinic associations.")

            # Get the dentist's associated clinic IDs
            dentist_clinic_ids = current_user.get_clinic_ids()
            logger.debug(f"Dentist {current_user.name} is associated with clinics: {dentist_clinic_ids}")

            # Get DSO IDs as fallback
            dentist_dso_ids = current_user.get_dso_ids() if hasattr(current_user, "get_dso_ids") else []

            if dentist_clinic_ids:
                # Show patients from the dentist's associated clinics
                query = Patient.query.filter(
                    db.or_(
                        # Patients directly assigned to dentist's clinics
                        Patient.clinic_id.in_(dentist_clinic_ids),
                        # Patients whose dentists work at the same clinics
                        db.and_(
                            Patient.clinic_id.is_(None),
                            Patient.dentist_id.isnot(None),
                            db.exists().where(
                                db.and_(
                                    dentist_clinic_association.c.dentist_id == Patient.dentist_id,
                                    dentist_clinic_association.c.clinic_id.in_(dentist_clinic_ids),
                                )
                            ),
                        ),
                    ),
                    db.or_(Patient.status.is_(None), Patient.status != "Archived"),
                )
                logger.debug(f"Found patients for dentist {current_user.name} in their associated clinics")
            elif dentist_dso_ids:
                # Fallback to DSO-based query
                logger.debug("Using DSO fallback query for operational list")
                query = (
                    Patient.query.join(Dentist)
                    .join(Clinic, Patient.clinic_id == Clinic.id, isouter=True)
                    .filter(
                        db.or_(
                            Clinic.dso_id.in_(dentist_dso_ids),  # Patients in dentist's DSO clinics
                            db.and_(
                                Patient.clinic_id.is_(None),
                                Dentist.DSO == getattr(current_user, "DSO", None),
                            ),  # Legacy patients
                        ),
                        db.or_(Patient.status.is_(None), Patient.status != "Archived"),
                    )
                )
            else:
                # No clinic or DSO associations - show only directly assigned patients
                logger.debug("No clinic/DSO associations, showing only directly assigned patients")
                query = Patient.query.filter_by(dentist_id=current_user.id).filter(
                    db.or_(Patient.status.is_(None), Patient.status != "Archived")
                )
        else:
            # Other roles - show only directly assigned patients
            query = Patient.query.filter_by(dentist_id=current_user.id).filter(
                db.or_(Patient.status.is_(None), Patient.status != "Archived")
            )

        # Apply search filter and ensure Dentist is joined for doctor name
        needs_dentist_join = True
        if search_query:
            # Need to join Dentist before filtering by dentist name
            query = query.join(Dentist, Patient.dentist_id == Dentist.id, isouter=True)
            needs_dentist_join = False
            query = query.filter(
                db.or_(
                    Patient.name.ilike(f"%{search_query}%"),
                    Patient.email.ilike(f"%{search_query}%"),
                    Patient.phone.ilike(f"%{search_query}%"),
                    Patient.id.cast(db.String).ilike(f"%{search_query}%"),
                    Dentist.name.ilike(f"%{search_query}%"),
                )
            )

        # Join with dentist for doctor name if not already joined
        # Check if Dentist was already joined in DSO fallback query
        if needs_dentist_join and current_user.role in ["Dentist", "dentist", "Dentists"]:
            # Check if we used DSO fallback (which already joins Dentist)
            if not (
                hasattr(current_user, "get_dso_ids")
                and current_user.get_dso_ids()
                and not current_user.get_clinic_ids()
            ):
                # Only join if we didn't use DSO fallback
                query = query.join(Dentist, Patient.dentist_id == Dentist.id, isouter=True)
        elif needs_dentist_join:
            # For admin or other roles, always join Dentist
            query = query.join(Dentist, Patient.dentist_id == Dentist.id, isouter=True)

        # Get all patients
        patients = query.all()
        logger.info(f"Found {len(patients)} patients for operational list")

        # Batch-fetch latest note per patient (content + date)
        patient_ids = [p.id for p in patients]
        latest_by_patient = {}
        for pc in (
            PatientComment.query.filter(PatientComment.patient_id.in_(patient_ids))
            .order_by(PatientComment.created_date.desc())
            .all()
        ):
            if pc.patient_id not in latest_by_patient:
                latest_by_patient[pc.patient_id] = {
                    "content": (pc.content or "")[:200],
                    "date": pc.created_date.isoformat() if pc.created_date else None,
                }

        # Batch-fetch latest scheduled appointment per patient
        latest_appt_by_patient = {}
        for sched in (
            PatientConsultSchedule.query.filter(
                PatientConsultSchedule.patient_id.in_(patient_ids),
                PatientConsultSchedule.status == "scheduled",
            )
            .order_by(PatientConsultSchedule.scheduled_datetime.desc())
            .all()
        ):
            if sched.patient_id not in latest_appt_by_patient:
                type_display = (sched.consult_type or "").replace("_", " ").title()
                dt = sched.scheduled_datetime
                if dt:
                    hour = dt.hour
                    ampm = "AM" if hour < 12 else "PM"
                    hour12 = hour % 12 or 12
                    time_display = f"{hour12}:{dt.minute:02d} {ampm}"
                    date_display = dt.strftime("%b %d, %Y")
                else:
                    time_display = date_display = None
                latest_appt_by_patient[sched.patient_id] = {
                    "type": type_display,
                    "datetime": dt.isoformat() if dt else None,
                    "date": date_display,
                    "time": time_display,
                }

        manifest_entries = get_stage_summary_manifest()
        total_stages = len(manifest_entries)

        operational_patients = []

        for patient in patients:
            try:
                # Get doctor name
                doctor_name = patient.dentist.name if patient.dentist else "N/A"

                # Get stage summary cache
                cache = PatientStageSummaryCache.query.filter_by(patient_id=patient.id, is_valid=True).first()

                # Calculate progress and get current stage
                progress = 0
                current_stage = "Not Started"
                # Use patient.status field which is automatically updated according to the stage
                phase = patient.status if patient.status and patient.status != "Archived" else "Initial Assessment"
                priority = 3  # Default: Active (3), Pending (2), Blocked (1)
                status = "Active"

                # Use cached stages_snapshot if available and valid, otherwise evaluate
                all_stages_status = {}
                use_cached_stages = False

                if cache and cache.is_valid and cache.stages_snapshot:
                    try:
                        # Check if cached snapshot matches current manifest structure
                        cached_keys = set(cache.stages_snapshot.keys())
                        manifest_keys = set(entry.get("key") for entry in manifest_entries)

                        if cached_keys == manifest_keys:
                            # Use cached snapshot - much faster!
                            all_stages_status = cache.stages_snapshot
                            use_cached_stages = True
                            logger.debug(f"Using cached stages_snapshot for patient {patient.id}")
                    except Exception as e:
                        logger.warning(f"Error using cached stages_snapshot for patient {patient.id}: {str(e)}")

                # Only evaluate if we don't have valid cached data
                if not use_cached_stages:
                    try:
                        # First pass: evaluate all stages
                        for entry in manifest_entries:
                            stage_key = entry.get("key")
                            try:
                                completion_result = evaluate_stage_completion(patient.id, entry, all_stages_status)
                                all_stages_status[stage_key] = completion_result
                            except Exception as e:
                                logger.warning(f"Error evaluating stage {stage_key} for patient {patient.id}: {str(e)}")
                                all_stages_status[stage_key] = {"status": "pending", "completed_on": None}
                    except Exception as e:
                        logger.warning(f"Error evaluating stages for patient {patient.id}: {str(e)}")

                # Calculate counts from all_stages_status (whether cached or evaluated)
                blocked_count = 0
                pending_count = 0
                completed_count = 0

                try:
                    # Second pass: check for blocked stages (stages with incomplete prerequisites)
                    for entry in manifest_entries:
                        stage_key = entry.get("key")
                        stage_status = all_stages_status.get(stage_key, {}).get("status", "pending")

                        if stage_status == "completed":
                            completed_count += 1
                        elif stage_status == "skipped":
                            # Skip skipped stages in counts
                            pass
                        else:
                            # Check if blocked by prerequisites
                            prerequisites = entry.get("prerequisites", [])
                            is_blocked = False
                            if prerequisites:
                                for prereq_key in prerequisites:
                                    prereq_status = all_stages_status.get(prereq_key, {}).get("status", "pending")
                                    if prereq_status != "completed":
                                        is_blocked = True
                                        break

                            if is_blocked:
                                blocked_count += 1
                            else:
                                pending_count += 1
                except Exception as e:
                    logger.warning(f"Error calculating stage counts for patient {patient.id}: {str(e)}")

                # Calculate progress
                if total_stages > 0:
                    progress = round((completed_count / total_stages) * 100)

                # Find current stage (first incomplete stage)
                try:
                    for entry in manifest_entries:
                        stage_key = entry.get("key")
                        stage_status = all_stages_status.get(stage_key, {}).get("status", "pending")
                        if stage_status != "completed":
                            current_stage = entry.get("title", "Unknown Stage")
                            break
                except Exception as e:
                    logger.warning(f"Error finding current stage for patient {patient.id}: {str(e)}")

                # Determine priority, status, and blocked reason
                blocked_reason = None
                if blocked_count > 0:
                    priority = 1  # Blocked = highest priority
                    status = "Blocked"
                    # Find which stages are blocked and why
                    blocked_stages = []
                    for entry in manifest_entries:
                        stage_key = entry.get("key")
                        stage_status = all_stages_status.get(stage_key, {}).get("status", "pending")
                        if stage_status != "completed" and stage_status != "skipped":
                            prerequisites = entry.get("prerequisites", [])
                            if prerequisites:
                                missing_prereqs = []
                                for prereq_key in prerequisites:
                                    prereq_status = all_stages_status.get(prereq_key, {}).get("status", "pending")
                                    if prereq_status != "completed":
                                        prereq_entry = next(
                                            (e for e in manifest_entries if e.get("key") == prereq_key), None
                                        )
                                        if prereq_entry:
                                            missing_prereqs.append(prereq_entry.get("title", prereq_key))
                                if missing_prereqs:
                                    blocked_stages.append(
                                        {"stage": entry.get("title", stage_key), "missing": missing_prereqs}
                                    )

                    if blocked_stages:
                        # Create a concise blocked reason
                        first_blocked = blocked_stages[0]
                        if len(blocked_stages) == 1:
                            blocked_reason = f"Waiting for: {', '.join(first_blocked['missing'])}"
                        else:
                            blocked_reason = (
                                f"{len(blocked_stages)} stages blocked. First: {', '.join(first_blocked['missing'])}"
                            )
                elif pending_count > 0:
                    priority = 2  # Pending
                    status = "Pending"
                else:
                    priority = 3  # Active (all completed or in progress)
                    status = "Active"

                # Get last_update date
                last_update = patient.last_update.isoformat() if patient.last_update else None

                latest_note = latest_by_patient.get(patient.id)
                latest_appointment = latest_appt_by_patient.get(patient.id)
                operational_patients.append(
                    {
                        "patient_id": patient.id,
                        "name": patient.name or "N/A",
                        "doctor": doctor_name,
                        "email": patient.email or "N/A",
                        "phone": patient.phone or "N/A",
                        "phase": phase,
                        "current_stage": current_stage,
                        "progress": progress,
                        "status": status,
                        "priority": priority,
                        "blocked_reason": blocked_reason,
                        "last_update": last_update,
                        "latest_note": latest_note,
                        "latest_appointment": latest_appointment,
                    }
                )
            except Exception as e:
                logger.error(f"Error processing patient {patient.id}: {str(e)}")
                # Continue with next patient even if one fails
                continue

        # Sort the results
        if sort_by == "priority":
            operational_patients.sort(key=lambda x: x["priority"])
        elif sort_by == "last_update":
            operational_patients.sort(key=lambda x: x["last_update"] or "", reverse=True)
        elif sort_by == "progress":
            operational_patients.sort(key=lambda x: x["progress"], reverse=True)
        elif sort_by == "stage":
            operational_patients.sort(key=lambda x: x["current_stage"])

        # Calculate summary stats
        total = len(operational_patients)
        active_count = sum(1 for p in operational_patients if p["status"] == "Active")
        pending_count = sum(1 for p in operational_patients if p["status"] == "Pending")
        blocked_count = sum(1 for p in operational_patients if p["status"] == "Blocked")

        logger.info(f"Returning {len(operational_patients)} operational patients")

        return jsonify(
            {
                "success": True,
                "patients": operational_patients,
                "summary": {
                    "total": total,
                    "active": active_count,
                    "pending": pending_count,
                    "blocked": blocked_count,
                },
            }
        )

    except Exception as e:
        logger.error(f"Error getting operational patients list: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify(
            {
                "success": False,
                "message": f"Error retrieving operational patients: {str(e)}",
                "error_details": str(e),
            }
        ), 500


def register_patient_list_routes(main) -> None:
    """Register patient list routes onto the main Blueprint."""
    # Keep endpoint names stable (e.g. url_for('main.patient_list')) by setting endpoint= explicitly.
    main.add_url_rule("/patient-list", endpoint="patient_list", view_func=patient_list, methods=["GET"])
    main.add_url_rule(
        "/patients_operational", endpoint="patients_operational", view_func=patients_operational, methods=["GET"]
    )
    main.add_url_rule(
        "/api/patients/operational_list",
        endpoint="get_operational_patients_list",
        view_func=get_operational_patients_list,
        methods=["GET"],
    )
