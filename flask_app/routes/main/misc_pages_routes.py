from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from flask import jsonify, render_template
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError

from flask_app import db
from flask_app.models import File, Patient

logger = logging.getLogger(__name__)


@login_required
def billing() -> Any:
    logger.debug("Accessing billing page")
    return render_template("coming-soon.html")


@login_required
def notifications() -> Any:
    logger.debug("Accessing notifications page")
    return render_template("coming-soon.html")


def health_check() -> Any:
    return jsonify({"status": "healthy"}), 200


def support() -> Any:
    return render_template("support.html")


@login_required
def test_metadata_store() -> Any:
    """
    Test route for metadata storage.
    """
    try:
        logger.debug("=== START test_metadata_store ===")

        # Hardcoded test data for patient ID 10314
        test_data = {
            "patient_id": "10314",
            "s3_key": "patients/10314/billing/test_file.txt",
            "file_size": 1024,
            "file_type": "text/plain",
            "category": "billing",
            "subcategory": "billing",
        }

        logger.debug(f"Test metadata: {test_data}")

        patient_id = test_data.get("patient_id")
        s3_key = test_data.get("s3_key")
        filename = s3_key.split("/")[-1]
        file_size = test_data.get("file_size", 0)
        file_type = test_data.get("file_type", "application/octet-stream")
        category = test_data.get("category")
        subcategory = test_data.get("subcategory")

        logger.debug(f"Extracted data - patient_id: {patient_id}, s3_key: {s3_key}")
        logger.debug(f"Extracted data - filename: {filename}, file_size: {file_size}, file_type: {file_type}")
        logger.debug(f"Extracted data - category: {category}, subcategory: {subcategory}")

        # Check if patient exists
        patient = Patient.query.get(patient_id)
        if not patient:
            logger.error(f"Patient with ID {patient_id} not found.")
            return jsonify({"success": False, "message": f"Patient with ID {patient_id} not found"}), 404

        logger.debug(f"Patient found: {patient.name} (ID: {patient.id})")

        # Create a new file entry in the database
        new_file = File(
            name=filename,
            patient_id=patient_id,
            s3_key=s3_key,
            upload_date=datetime.utcnow(),
            file_size=file_size,
            file_type=file_type,
            category=category,
            subcategory=subcategory,
        )

        logger.debug(f"File object created, about to add to database session")
        db.session.add(new_file)
        logger.debug(f"File added to session, about to commit")
        db.session.commit()
        logger.debug(f"Database commit successful")

        # Get the ID of the newly created file entry
        file_id = new_file.id

        logger.info(f"Test file metadata stored successfully for file: {filename}, ID: {file_id}")
        logger.debug("=== END test_metadata_store ===")

        return jsonify({"success": True, "message": "Test file metadata stored successfully.", "file_id": file_id})

    except SQLAlchemyError as db_error:
        db.session.rollback()
        error_message = str(db_error)
        logger.error(f"Database error storing test file metadata: {error_message}")
        return jsonify({"success": False, "message": f"Database error: {error_message}"}), 500

    except Exception as e:
        db.session.rollback()
        error_message = str(e)
        logger.error(f"Error in test_metadata_store: {error_message}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Stack trace:", exc_info=True)
        return jsonify({"success": False, "message": f"Error in test_metadata_store: {error_message}"}), 500


def register_misc_pages_routes(main) -> None:
    """Register misc simple pages and health routes onto the main Blueprint."""
    # Keep endpoint names stable (e.g. url_for('main.billing')) by setting endpoint= explicitly.
    main.add_url_rule("/billing", endpoint="billing", view_func=billing, methods=["GET"])
    main.add_url_rule("/notifications", endpoint="notifications", view_func=notifications, methods=["GET"])
    main.add_url_rule("/health", endpoint="health_check", view_func=health_check, methods=["GET"])
    main.add_url_rule("/support", endpoint="support", view_func=support, methods=["GET"])
    main.add_url_rule(
        "/test_metadata_store",
        endpoint="test_metadata_store",
        view_func=test_metadata_store,
        methods=["GET"],
    )
