from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask import current_app as app
from flask_login import current_user, login_required

from flask_app import db
from flask_app.models import (
    Clinic,
    Dentist,
    Patient,
    PatientStatus,
)

logger = logging.getLogger(__name__)


@login_required
def update_patient_status(patient_id: int) -> Any:
    data = request.get_json()

    # Log the incoming data for debugging
    app.logger.debug("Received data for update_patient_status endpoint:")
    app.logger.debug(data)

    try:
        # Extracting and processing information from the request
        status_id = int(data.get("status_id", -1))  # Ensure status_id is an integer
        status_type = data.get("status_type")  # Extract status_type from request data
        status_value = data.get("status_value")
        comment = data.get("comment", "").strip()
        mapping = data.get("mapping", "").strip()  # Extract mapping from request data

        app.logger.debug(
            f"Parsed data - status_id: {status_id} (type: {type(status_id)}), "
            f"status_type: {status_type}, status_value: {status_value}, "
            f"comment: {comment}, mapping: {mapping}"
        )

        if status_id == -1:
            # Ensure that status_type is not None for new entries
            if not status_type:
                app.logger.error("status_type is null for new status entry.")
                return (
                    jsonify({"success": False, "message": "Status type cannot be null for new entries."}),
                    400,
                )

            # Create a new PatientStatus entry if status_id is -1
            new_status = PatientStatus(
                patient_id=patient_id,
                status_type=status_type,  # Insert status_type
                status_value=status_value,
                comment=comment,
                mapping=mapping,  # Insert mapping
                updated_at=datetime.utcnow(),
            )
            db.session.add(new_status)
            app.logger.debug(f"New status created: {new_status}")
        else:
            # Fetch the existing patient status record
            status = PatientStatus.query.filter_by(id=status_id, patient_id=patient_id).first()
            if not status:
                app.logger.error(f"Status not found for status_id: {status_id}, patient_id: {patient_id}")
                return jsonify({"success": False, "message": "Status not found."}), 404

            # Update the existing status
            status.status_value = status_value
            status.comment = comment
            status.mapping = mapping  # Update mapping field
            status.updated_at = datetime.utcnow()
            app.logger.debug(f"Status updated: {status}")

        # Commit changes to the database
        db.session.commit()
        app.logger.info("Status update committed to the database successfully.")

        return jsonify({"success": True, "message": "Status updated successfully"})

    except ValueError as ve:
        app.logger.error(f"Invalid value for status_id: {data.get('status_id')}")
        return jsonify({"success": False, "message": "Invalid status ID format."}), 400

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error updating status: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


@login_required
def patient_status_list() -> Any:
    """
    Endpoint to fetch and render the patient status list page with all necessary patient details,
    including dynamic status headers.
    Implements DSO-based access control - dentists can only see patients from their associated DSOs.
    """
    logger.debug("Accessing the patient status list endpoint.")

    # Fetch distinct status types for headers
    try:
        status_headers = [
            status.status_type
            for status in PatientStatus.query.with_entities(PatientStatus.status_type).distinct()
        ]
        logger.debug(f"Fetched {len(status_headers)} distinct status headers: {status_headers}")
    except Exception as e:
        logger.error(f"Error fetching status headers: {e}")
        status_headers = []

    # Base query for all patients with proper access control
    try:
        # If the current user is an admin, they can see all patients
        if current_user.role == "admin":
            patients = (
                Patient.query.filter(Patient.status != "Archived").order_by(Patient.create_date.desc()).all()
            )
            logger.debug(f"Admin viewing all patients. Total patients found: {len(patients)}")

        elif current_user.role in ["Dentist", "dentist", "Dentists"]:
            # Dentist can only see patients treated by dentists in their same DSO
            logger.debug(
                f'Dentist {current_user.name} with DSO: {getattr(current_user, "DSO", "None")} attempting to view patient status list.'
            )

            # Try new DSO system first, then fall back to legacy
            if hasattr(current_user, "dsos") and current_user.dsos.count() > 0:
                # NEW SYSTEM: Use DSO associations
                logger.debug("Using new DSO association system")
                dso_ids = current_user.get_dso_ids()
                patients = (
                    Patient.query.join(Dentist)
                    .join(Clinic, Patient.clinic_id == Clinic.id, isouter=True)
                    .filter(
                        db.or_(
                            Clinic.dso_id.in_(dso_ids),  # New system patients
                            db.and_(
                                Patient.clinic_id.is_(None),
                                Dentist.DSO == getattr(current_user, "DSO", None),
                            ),  # Legacy patients
                        ),
                        Patient.status != "Archived",
                    )
                    .order_by(Patient.create_date.desc())
                    .all()
                )
            elif hasattr(current_user, "DSO") and current_user.DSO:
                # LEGACY SYSTEM: Use DSO string
                logger.debug("Using legacy DSO string system")
                patients = (
                    Patient.query.join(Dentist)
                    .filter(Dentist.DSO == current_user.DSO, Patient.status != "Archived")
                    .order_by(Patient.create_date.desc())
                    .all()
                )
            else:
                # No DSO association found
                logger.warning(f"Dentist {current_user.name} has no DSO associations")
                patients = []

            # Log the DSO of the current user and compare it with patients' dentists
            logger.debug(f"Number of patients found: {len(patients)}")
            for patient in patients[:5]:  # Log first 5 for debugging
                dentist_dso = getattr(patient.dentist, "DSO", "None") if patient.dentist else "None"
                clinic_dso = patient.clinic.dso_id if patient.clinic else "None"
                logger.debug(f"Patient: {patient.name}, Dentist DSO: {dentist_dso}, Clinic DSO: {clinic_dso}")

            if not patients:
                logger.warning(f"No patients found for dentist: {current_user.name}")
            else:
                logger.debug(f"{len(patients)} patients found for dentist: {current_user.name}")

        else:
            flash("Unauthorized access", "error")
            logger.warning(f"Unauthorized access attempt by user {current_user.name} with role {current_user.role}")
            return redirect(url_for("main.index"))

        logger.debug(f"Fetched {len(patients)} patients from the database.")
    except Exception as e:
        logger.error(f"Error fetching patients: {e}")
        patients = []

    # Collect patient data
    patient_data = []
    for patient in patients:
        try:
            # Directly use the `status` column from the Patient model
            patient_status = patient.status if patient.status else "N/A"
            logger.debug(f"Patient ID {patient.id} status: {patient_status}")

            # Fetch other patient details
            # Import fetch_patient_details from main_routes to avoid circular dependency
            from flask_app.routes.main_routes import fetch_patient_details  # noqa: E402

            patient_details = fetch_patient_details(patient.id)
            logger.debug(f"Fetched details for patient ID {patient.id}")

            patient_data.append(
                {
                    "id": patient.id,
                    "name": patient.name,
                    "status": patient_status,  # Use directly from Patient model
                    "phone": patient.phone,
                    "payment_method": patient.payment_method,
                    "last_update": (
                        patient.last_update.strftime("%Y-%m-%d %H:%M:%S") if patient.last_update else "N/A"
                    ),
                    "comments": patient_details["comments"],
                    "statuses": {
                        status.status_type: status.status_value
                        for status in patient_details["patient_statuses"].values()
                    },
                    "uploaded_files": patient_details["uploaded_files"],
                    "uploaded_files_one_dcm_file": patient_details["uploaded_files_one_dcm_file"],
                }
            )
        except Exception as e:
            logger.error(f"Error processing patient ID {patient.id}: {e}")

    if not patient_data:
        logger.warning("No patient data to render. Check database or query logic.")

    # Render the template with full patient data and dynamic headers
    try:
        response = render_template(
            "patient_status_list.html",
            patients=patient_data,
            status_headers=status_headers,  # Pass the dynamic headers to the template
        )
        logger.debug("Template rendered successfully.")
        return response
    except Exception as e:
        logger.error(f"Error rendering template: {e}")
        return "Error rendering the page.", 500


def get_status_types() -> Any:
    """
    Fetch distinct status types from the patient_status table for the client.
    """
    app.logger.debug(f"Received request for status types")
    try:
        # Fetch distinct status_type from the patient_status table
        status_types = [
            status.status_type for status in PatientStatus.query.distinct(PatientStatus.status_type).all()
        ]

        # Return the status types as JSON
        return jsonify({"status_types": status_types}), 200

    except Exception as e:
        # Log the error and return a 500 response
        logger.error(f"Error fetching status types: {e}")
        return jsonify({"error": "An error occurred while fetching status types."}), 500


@login_required
def update_patient_status_api(patient_id: int) -> Any:
    """Update patient status via API"""
    try:
        data = request.get_json()
        new_status = data.get("status")

        if not new_status:
            return jsonify({"success": False, "message": "Status is required"}), 400

        # Validate status values
        valid_statuses = ["New", "Onboarding", "In Treatment", "Followup", "Active", "Archived"]
        if new_status not in valid_statuses:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": f'Invalid status. Must be one of: {", ".join(valid_statuses)}',
                    }
                ),
                400,
            )

        # Get patient and update status
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({"success": False, "message": "Patient not found"}), 404

        # Check if user has permission to update this patient using the same method as other routes
        logger.info(
            f"Permission check: patient.dentist_id={patient.dentist_id}, current_user.id={current_user.id}, current_user.role={getattr(current_user, 'role', 'unknown')}"
        )

        if not current_user.can_access_patient(patient):
            logger.warning(
                f"Unauthorized status update attempt: patient {patient_id} (dentist_id={patient.dentist_id}) by user {current_user.id} (role={getattr(current_user, 'role', 'unknown')})"
            )
            return (
                jsonify(
                    {"success": False, "message": "Unauthorized - You do not have permission to update this patient"}
                ),
                403,
            )

        # Update status
        old_status = patient.status
        patient.status = new_status
        patient.last_update = datetime.utcnow()

        # Commit to database
        db.session.commit()

        # Log the status change
        logger.info(
            f"Patient {patient_id} status changed from '{old_status}' to '{new_status}' by dentist {current_user.id}"
        )

        return jsonify(
            {"success": True, "message": f"Patient status updated to {new_status}", "new_status": new_status}
        )

    except Exception as e:
        logger.error(f"Error updating patient status: {e}")
        db.session.rollback()
        return jsonify({"success": False, "message": "Error updating patient status"}), 500


def register_patient_status_routes(main) -> None:
    """Register patient status routes onto the main Blueprint."""
    # Keep endpoint names stable (e.g. url_for('main.update_patient_status')) by setting endpoint= explicitly.
    main.add_url_rule(
        "/patient/<int:patient_id>/status/update",
        endpoint="update_patient_status",
        view_func=update_patient_status,
        methods=["POST"],
    )
    main.add_url_rule(
        "/patient_status_list", endpoint="patient_status_list", view_func=patient_status_list, methods=["GET"]
    )
    main.add_url_rule("/get_status_types", endpoint="get_status_types", view_func=get_status_types, methods=["GET"])
    main.add_url_rule(
        "/api/patient/<int:patient_id>/update_status",
        endpoint="update_patient_status_api",
        view_func=update_patient_status_api,
        methods=["POST"],
    )
