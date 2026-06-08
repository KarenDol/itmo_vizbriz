from __future__ import annotations

import logging
from typing import Any

from flask import jsonify
from flask_login import current_user, login_required

from flask_app.models import Clinic, Dentist, dentist_clinic_association

logger = logging.getLogger(__name__)


@login_required
def get_dentist_clinics() -> Any:
    """
    Get clinics available to the current dentist based on their direct clinic associations
    """
    try:
        if current_user.role == "admin":
            # Admin can see all clinics
            clinics = Clinic.query.filter_by(status="active").all()
        else:
            # Get clinics from dentist's direct clinic associations
            clinics = current_user.clinics.filter_by(status="active").all()

        clinic_data = []
        for clinic in clinics:
            clinic_data.append({"id": clinic.id, "name": clinic.name, "dso_id": clinic.dso_id})

        logger.debug(f"Found {len(clinic_data)} clinics for dentist {current_user.name} (ID: {current_user.id})")
        for clinic in clinic_data:
            logger.debug(f'  - Clinic: {clinic["name"]} (ID: {clinic["id"]})')

        return jsonify({"success": True, "clinics": clinic_data, "count": len(clinic_data)})

    except Exception as e:
        logger.error(f"Error getting dentist clinics: {str(e)}")
        return jsonify({"success": False, "message": f"Error retrieving clinics: {str(e)}"}), 500


@login_required
def get_clinics_for_dentist(dentist_id: int) -> Any:
    """
    Get all clinics associated with a specific dentist ID.
    Used for refreshing clinic dropdown when dentist is selected in patient edit.
    """
    try:
        dentist = Dentist.query.get(dentist_id)
        if not dentist:
            return jsonify({"success": False, "error": "Dentist not found"}), 404

        # Get all clinics associated with this dentist
        clinics = dentist.clinics.filter_by(status="active").all()

        clinic_data = []
        for clinic in clinics:
            clinic_data.append({"id": clinic.id, "name": clinic.name, "dso_id": clinic.dso_id})

        logger.debug(f"Found {len(clinic_data)} clinics for dentist ID {dentist_id}")

        return jsonify({"success": True, "clinics": clinic_data, "count": len(clinic_data)})

    except Exception as e:
        logger.error(f"Error fetching clinics for dentist {dentist_id}: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


@login_required
def get_dentists_for_clinic(clinic_id: int) -> Any:
    """
    Get all dentists associated with a specific clinic ID.
    Used for refreshing dentist dropdown when clinic is selected in patient edit.
    """
    try:
        clinic = Clinic.query.get(clinic_id)
        if not clinic:
            return jsonify({"success": False, "error": "Clinic not found"}), 404

        # Get all dentists associated with this clinic
        # Use the same query pattern as elsewhere in the codebase
        dentists = (
            Dentist.query.join(dentist_clinic_association)
            .filter(dentist_clinic_association.c.clinic_id == clinic_id)
            .all()
        )

        dentist_data = []
        for dentist in dentists:
            dentist_data.append({"id": dentist.id, "name": dentist.name})

        logger.info(f"Found {len(dentist_data)} dentists for clinic ID {clinic_id} (clinic name: {clinic.name})")
        for d in dentist_data:
            logger.info(f'  - Dentist: {d["name"]} (ID: {d["id"]})')

        return jsonify({"success": True, "dentists": dentist_data, "count": len(dentist_data)})

    except Exception as e:
        logger.error(f"Error fetching dentists for clinic {clinic_id}: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


def register_dentist_clinic_routes(main) -> None:
    """Register dentist/clinic API routes onto the main Blueprint."""
    # Keep endpoint names stable (e.g. url_for('main.get_dentist_clinics')) by setting endpoint= explicitly.
    main.add_url_rule(
        "/api/dentist/clinics",
        endpoint="get_dentist_clinics",
        view_func=get_dentist_clinics,
        methods=["GET"],
    )
    main.add_url_rule(
        "/api/dentist/<int:dentist_id>/clinics",
        endpoint="get_clinics_for_dentist",
        view_func=get_clinics_for_dentist,
        methods=["GET"],
    )
    main.add_url_rule(
        "/api/clinic/<int:clinic_id>/dentists",
        endpoint="get_dentists_for_clinic",
        view_func=get_dentists_for_clinic,
        methods=["GET"],
    )
