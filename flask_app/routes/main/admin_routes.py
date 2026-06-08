from __future__ import annotations

import logging
import os
from typing import Any

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from flask_app import db
from flask_app.models import (
    Clinic,
    ConsultationRequest,
    ConversionQuiz,
    DSO,
    Dentist,
    Patient,
    dentist_clinic_association,
)

logger = logging.getLogger(__name__)


@login_required
def admin_home() -> Any:
    """
    Home page with two main sections:
    1. Undiagnosed Conversion - links to conversion dashboard
    2. Case Management - shows last 3 patients with links to patient workflow
    """
    try:
        # Apply the same security logic as patient_list to get patients user can access
        if current_user.role == "admin":
            # Admin can see all patients
            recent_patients = (
                Patient.query.filter(Patient.status != "Archived")
                .order_by(Patient.last_update.desc())
                .limit(3)
                .all()
            )
        elif current_user.role in ["Dentist", "dentist", "Dentists"]:
            # Dentist can only see patients associated with the same clinic(s) as the dentist
            dentist_clinic_ids = current_user.get_clinic_ids()
            dentist_dso_ids = current_user.get_dso_ids() if hasattr(current_user, "get_dso_ids") else []

            if dentist_clinic_ids:
                recent_patients = (
                    Patient.query.filter(
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
                        Patient.status != "Archived",
                    )
                    .order_by(Patient.last_update.desc())
                    .limit(3)
                    .all()
                )
            else:
                # No clinic associations found - try DSO fallback
                if dentist_dso_ids:
                    recent_patients = (
                        Patient.query.join(Dentist)
                        .join(Clinic, Patient.clinic_id == Clinic.id, isouter=True)
                        .filter(
                            db.or_(
                                Clinic.dso_id.in_(dentist_dso_ids),
                                db.and_(
                                    Patient.clinic_id.is_(None),
                                    Dentist.DSO == getattr(current_user, "DSO", None),
                                ),
                            ),
                            Patient.status != "Archived",
                        )
                        .order_by(Patient.last_update.desc())
                        .limit(3)
                        .all()
                    )
                else:
                    recent_patients = []
        else:
            # Other users get no patients
            recent_patients = []

        # Prepare patient data for display
        patients_data = []
        for patient in recent_patients:
            # Get patient workflow information - simplified approach
            try:
                # Try to get basic stage info without the complex build_enhanced_patient_packet
                current_stage = getattr(patient, "current_stage", "Unknown")
                next_stage = getattr(patient, "next_stage", "Unknown")

                # If not available, try a simpler approach
                if current_stage == "Unknown":
                    # You can add simple logic here to determine stage based on status
                    if hasattr(patient, "status") and patient.status:
                        if "new" in patient.status.lower():
                            current_stage = "Initial Assessment"
                            next_stage = "Consultation"
                        elif "consultation" in patient.status.lower():
                            current_stage = "Consultation"
                            next_stage = "Treatment Planning"
                        else:
                            current_stage = patient.status
                            next_stage = "Next Step"
                    else:
                        current_stage = "New Patient"
                        next_stage = "Initial Assessment"

            except Exception as e:
                logger.warning(f"Could not get stage info for patient {patient.id}: {str(e)}")
                current_stage = "Unknown"
                next_stage = "Unknown"

            patients_data.append(
                {
                    "id": patient.id,
                    "name": patient.name,
                    "created_at": patient.last_update.strftime("%Y-%m-%d %H:%M") if patient.last_update else "N/A",
                    "status": getattr(patient, "status", "Unknown"),
                    "current_stage": current_stage,
                    "next_stage": next_stage,
                }
            )

        # Get base_url from environment
        base_url = os.getenv("BASE_URL", "https://app.vizbriz.com")

        return render_template("admin_home.html", recent_patients=patients_data, base_url=base_url)

    except Exception as e:
        logger.error(f"Error in admin_home: {str(e)}")
        flash("Error loading admin home page", "error")
        return redirect(url_for("main.index"))


@login_required
def debug_unified_data() -> Any:
    """Debug route to check what data exists in the database"""
    try:
        if not current_user.is_authenticated or current_user.role != "admin":
            return jsonify({"error": "Admin access required"}), 403

        # Check what data exists
        consultation_count = ConsultationRequest.query.count()
        quiz_count = ConversionQuiz.query.count()
        patient_count = Patient.query.count()

        # Get sample data
        sample_consultations = ConsultationRequest.query.limit(3).all()
        sample_quizzes = ConversionQuiz.query.limit(3).all()
        sample_patients = Patient.query.limit(3).all()

        debug_info = {
            "counts": {
                "consultations": consultation_count,
                "quizzes": quiz_count,
                "patients": patient_count,
            },
            "sample_consultations": [
                {
                    "id": c.id,
                    "email": c.email,
                    "name": c.name,
                    "status": c.status,
                    "submitted_at": c.submitted_at.isoformat() if c.submitted_at else None,
                }
                for c in sample_consultations
            ],
            "sample_quizzes": [
                {
                    "id": q.id,
                    "patient_email": q.patient_email,
                    "quiz_type": q.quiz_type,
                    "created_at": q.created_at.isoformat() if q.created_at else None,
                }
                for q in sample_quizzes
            ],
            "sample_patients": [
                {
                    "id": p.id,
                    "name": p.name,
                    "email": p.email,
                    "status": p.status,
                }
                for p in sample_patients
            ],
        }

        return jsonify(debug_info)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@login_required
def admin_clinic_cases() -> Any:
    """
    Admin view for OSA clinic cases - shows patients by clinic with filtering
    """
    try:
        # Get all DSOs and clinics for the dropdown
        dsos = DSO.query.filter_by(status="active").all()
        clinics = Clinic.query.filter_by(status="active").all()

        # Get selected filters from request
        selected_dso_id = request.args.get("dso_id", type=int)
        selected_clinic_id = request.args.get("clinic_id", type=int)
        search_query = request.args.get("search", "").strip()

        # Build the query based on filters
        query = Patient.query.filter(Patient.status != "Archived")

        if selected_clinic_id:
            # Filter by specific clinic
            query = query.filter(Patient.clinic_id == selected_clinic_id)
        elif selected_dso_id:
            # Filter by DSO (all clinics in that DSO)
            query = query.join(Clinic).filter(Clinic.dso_id == selected_dso_id)

        if search_query:
            # Add search filter
            query = query.filter(
                db.or_(
                    Patient.name.ilike(f"%{search_query}%"),
                    Patient.email.ilike(f"%{search_query}%"),
                    Patient.phone.ilike(f"%{search_query}%"),
                )
            )

        # Get patients with clinic and DSO info
        patients = (
            query.join(Clinic, isouter=True)
            .join(DSO, Clinic.dso_id == DSO.id, isouter=True)
            .order_by(Patient.create_date.desc())
            .all()
        )

        return render_template(
            "admin_clinic_cases.html",
            patients=patients,
            dsos=dsos,
            clinics=clinics,
            selected_dso_id=selected_dso_id,
            selected_clinic_id=selected_clinic_id,
            search_query=search_query,
        )

    except Exception as e:
        logger.error(f"Error in admin_clinic_cases: {str(e)}")
        flash(f"Error loading clinic cases: {str(e)}", "error")
        return redirect(url_for("main.patient_list"))


@login_required
def get_admin_dsos() -> Any:
    """Get all DSOs for admin dropdown"""
    try:
        dsos = DSO.query.filter_by(status="active").all()
        dso_data = []
        for dso in dsos:
            dso_data.append({"id": dso.id, "name": dso.name})

        return jsonify({"success": True, "dsos": dso_data})

    except Exception as e:
        logger.error(f"Error getting DSOs: {str(e)}")
        return jsonify({"success": False, "message": f"Error retrieving DSOs: {str(e)}"}), 500


@login_required
def get_admin_clinics_by_dso(dso_id: int) -> Any:
    """Get clinics for a specific DSO"""
    try:
        clinics = Clinic.query.filter_by(dso_id=dso_id, status="active").all()
        clinic_data = []
        for clinic in clinics:
            clinic_data.append({"id": clinic.id, "name": clinic.name})

        return jsonify({"success": True, "clinics": clinic_data})

    except Exception as e:
        logger.error(f"Error getting clinics for DSO {dso_id}: {str(e)}")
        return jsonify({"success": False, "message": f"Error retrieving clinics: {str(e)}"}), 500


@login_required
def get_admin_patients_by_clinic(clinic_id: int) -> Any:
    """Get patients for a specific clinic - includes multiple association methods"""
    try:
        # Get search query from request
        search_query = request.args.get("search", "").strip()

        # Build the base query
        query = (
            Patient.query.join(Dentist, isouter=True)
            .join(Clinic, Patient.clinic_id == Clinic.id, isouter=True)
            .filter(
                db.or_(
                    # Direct clinic association
                    Patient.clinic_id == clinic_id,
                    # Patients whose dentists are associated with this clinic
                    db.and_(
                        Patient.clinic_id.is_(None),
                        Patient.dentist_id.isnot(None),
                        db.exists().where(
                            db.and_(
                                dentist_clinic_association.c.dentist_id == Patient.dentist_id,
                                dentist_clinic_association.c.clinic_id == clinic_id,
                            )
                        ),
                    ),
                ),
                Patient.status != "Archived",
            )
        )

        # Add search filter if provided
        if search_query:
            query = query.filter(
                db.or_(
                    Patient.name.ilike(f"%{search_query}%"),
                    Patient.email.ilike(f"%{search_query}%"),
                    Patient.phone.ilike(f"%{search_query}%"),
                    Patient.id.cast(db.String).ilike(f"%{search_query}%"),
                )
            )

        # Execute query and order by creation date
        patients = query.order_by(Patient.create_date.desc()).all()

        logger.info(f"Found {len(patients)} patients for clinic {clinic_id}")

        patient_data = []
        for patient in patients:
            # Determine the actual clinic name and DSO
            clinic_name = "Unknown"
            dso_name = "Unknown"

            if patient.clinic:
                clinic_name = patient.clinic.name
                if patient.clinic.dso_info:
                    dso_name = patient.clinic.dso_info.name
            elif patient.dentist:
                # If patient doesn't have direct clinic, check dentist's clinic associations
                dentist_clinics = patient.dentist.clinics.filter_by(id=clinic_id).all()
                if dentist_clinics:
                    clinic_name = dentist_clinics[0].name
                    if dentist_clinics[0].dso_info:
                        dso_name = dentist_clinics[0].dso_info.name

            patient_data.append(
                {
                    "id": patient.id,
                    "name": patient.name,
                    "email": patient.email,
                    "phone": patient.phone,
                    "gender": patient.gender,
                    "dob": patient.dob.isoformat() if patient.dob else None,
                    "create_date": patient.create_date.isoformat() if patient.create_date else None,
                    "status": patient.status,
                    "clinic_name": clinic_name,
                    "dso_name": dso_name,
                }
            )

        return jsonify({"success": True, "patients": patient_data})

    except Exception as e:
        logger.error(f"Error getting patients for clinic {clinic_id}: {str(e)}")
        return jsonify({"success": False, "message": f"Error retrieving patients: {str(e)}"}), 500


@login_required
def get_admin_all_patients() -> Any:
    """Get all patients for admin search when no DSO/clinic is selected"""
    try:
        # Get search query from request
        search_query = request.args.get("search", "").strip()

        # Build the query
        query = (
            Patient.query.join(Clinic, Patient.clinic_id == Clinic.id, isouter=True)
            .join(DSO, Clinic.dso_id == DSO.id, isouter=True)
            .filter(Patient.status != "Archived")
        )

        # Add search filter if provided
        if search_query:
            query = query.filter(
                db.or_(
                    Patient.name.ilike(f"%{search_query}%"),
                    Patient.email.ilike(f"%{search_query}%"),
                    Patient.phone.ilike(f"%{search_query}%"),
                    Patient.id.cast(db.String).ilike(f"%{search_query}%"),
                )
            )

        # Execute query and order by creation date
        patients = query.order_by(Patient.create_date.desc()).all()

        logger.info(f"Found {len(patients)} patients for admin search (search: '{search_query}')")

        patient_data = []
        for patient in patients:
            clinic_name = patient.clinic.name if patient.clinic else "No Clinic"
            dso_name = patient.clinic.dso_info.name if patient.clinic and patient.clinic.dso_info else "No DSO"

            patient_data.append(
                {
                    "id": patient.id,
                    "name": patient.name,
                    "email": patient.email,
                    "phone": patient.phone,
                    "gender": patient.gender,
                    "dob": patient.dob.isoformat() if patient.dob else None,
                    "create_date": patient.create_date.isoformat() if patient.create_date else None,
                    "status": patient.status,
                    "clinic_name": clinic_name,
                    "dso_name": dso_name,
                }
            )

        return jsonify({"success": True, "patients": patient_data})

    except Exception as e:
        logger.error(f"Error getting all patients: {str(e)}")
        return jsonify({"success": False, "message": f"Error retrieving patients: {str(e)}"}), 500


def register_admin_routes(main) -> None:
    """Register admin routes onto the main Blueprint."""
    # Keep endpoint names stable (e.g. url_for('main.admin_home')) by setting endpoint= explicitly.
    main.add_url_rule("/admin-home", endpoint="admin_home", view_func=admin_home, methods=["GET"])
    main.add_url_rule(
        "/debug-unified-data", endpoint="debug_unified_data", view_func=debug_unified_data, methods=["GET"]
    )
    main.add_url_rule(
        "/admin-clinic-cases", endpoint="admin_clinic_cases", view_func=admin_clinic_cases, methods=["GET"]
    )
    main.add_url_rule(
        "/api/admin/dsos", endpoint="get_admin_dsos", view_func=get_admin_dsos, methods=["GET"]
    )
    main.add_url_rule(
        "/api/admin/clinics/<int:dso_id>",
        endpoint="get_admin_clinics_by_dso",
        view_func=get_admin_clinics_by_dso,
        methods=["GET"],
    )
    main.add_url_rule(
        "/api/admin/patients/<int:clinic_id>",
        endpoint="get_admin_patients_by_clinic",
        view_func=get_admin_patients_by_clinic,
        methods=["GET"],
    )
    main.add_url_rule(
        "/api/admin/patients/all",
        endpoint="get_admin_all_patients",
        view_func=get_admin_all_patients,
        methods=["GET"],
    )
