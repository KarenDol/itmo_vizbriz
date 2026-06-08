from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime
from typing import Any

from flask import jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.utils import secure_filename

from flask_app import db
from flask_app.models import AdminFile, Clinic, Dentist, File, Patient
from flask_app.routes.file_management_routes import (
    upload_and_save_files,
    process_dicom_for_upload,
    is_dicom_file,
)
from flask_app.s3_utils import get_s3_client

logger = logging.getLogger(__name__)

# Import DSO for upload_new route
from flask_app.models import DSO, dentist_clinic_association  # noqa: E402

s3_client = get_s3_client()


@login_required
def upload() -> Any:
    logger.debug("Accessing upload page.")

    if request.method == "POST":
        logger.debug("Processing upload POST request")

        # Get form fields
        patient_name = request.form.get("patient_name")
        email = request.form.get("email")
        phone = request.form.get("phone")
        dob = request.form.get("dob")
        gender = request.form.get("gender")
        insurer = request.form.get("insurer")
        policy_id = request.form.get("policy_id")
        address = request.form.get("address")
        logger.debug("before OSA fields ")
        # OSA-related fields
        snoring = request.form.get("snoring")
        snoring_other = request.form.get("snoring_other") if snoring == "other" else None

        daytime_sleepiness = request.form.get("daytime_sleepiness")
        daytime_sleepiness_other = (
            request.form.get("daytime_sleepiness_other") if daytime_sleepiness == "other" else None
        )

        sleep_study = request.form.get("sleep_study")
        sleep_study_date = request.form.get("sleep_study_date") if sleep_study == "yes" else None

        cpap_intolerant = request.form.get("cpap_intolerant")
        cpap_intolerant_other = (
            request.form.get("cpap_intolerant_other") if cpap_intolerant == "other" else None
        )
        logger.debug("before zip file section")

        # Expecting a single zip file per section now
        billing_zip = request.files.get("billing")  # Expecting a single zip file
        logger.debug("before zi[ file section")
        clinical_zip = request.files.get("clinical")  # Expecting a single zip file
        cbct_zip = request.files.get("cbct")  # Expecting a single zip file
        intraoral_zip = request.files.get("intraoral")  # Expecting a single zip file
        sleep_test_zip = request.files.get("sleep")  # Expecting a single zip file
        questionnaire_zip = request.files.get("questionnaire")  # Expecting a single zip file
        medical_background_zip = request.files.get("medical")  # Expecting a single zip file
        logger.debug("before zip file section")
        try:
            # Parse DOB field into a datetime object (if provided)
            parsed_dob = None
            if dob:
                try:
                    parsed_dob = datetime.strptime(dob, "%Y-%m-%d")
                except ValueError:
                    logger.error(f"Invalid date format for DOB: {dob}")
                    return (
                        jsonify(
                            {
                                "success": False,
                                "message": "Invalid date format for DOB. Please use YYYY-MM-DD.",
                            }
                        ),
                        400,
                    )

            # Parse sleep study date field into a datetime object (if provided)
            logger.debug("parsed_sleep_stud")
            parsed_sleep_study_date = None
            if sleep_study_date:
                try:
                    parsed_sleep_study_date = datetime.strptime(sleep_study_date, "%Y-%m-%d")
                except ValueError:
                    logger.error(f"Invalid date format for sleep study date: {sleep_study_date}")
                    return (
                        jsonify(
                            {
                                "success": False,
                                "message": "Invalid date format for sleep study date. Please use YYYY-MM-DD.",
                            }
                        ),
                        400,
                    )

            # Get clinic_id from form or fall back to dentist's default clinic
            clinic_id = request.form.get("clinic_id")
            if clinic_id:
                try:
                    clinic_id = int(clinic_id)
                    # Verify the clinic is accessible to this dentist
                    if current_user.role != "admin":
                        dso_ids = current_user.get_dso_ids()
                        if dso_ids:
                            clinic = (
                                Clinic.query.filter(
                                    Clinic.id == clinic_id,
                                    Clinic.dso_id.in_(dso_ids),
                                    Clinic.status == "active",
                                )
                                .first()
                            )
                            if not clinic:
                                logger.warning(
                                    f"Dentist {current_user.name} attempted to assign patient to unauthorized clinic {clinic_id}"
                                )
                                return (
                                    jsonify({"success": False, "message": "Unauthorized clinic selection"}),
                                    403,
                                )
                        else:
                            logger.warning(f"Dentist {current_user.name} has no DSO associations")
                            return (
                                jsonify({"success": False, "message": "No DSO associations found"}),
                                403,
                            )
                    logger.debug(f"Patient assigned to selected clinic_id {clinic_id}")
                except ValueError:
                    logger.error(f"Invalid clinic_id format: {clinic_id}")
                    return jsonify({"success": False, "message": "Invalid clinic selection"}), 400
            else:
                # Fall back to dentist's default clinic (first clinic in their DSOs)
                clinic_id = None
                dso_ids = current_user.get_dso_ids()
                if dso_ids:
                    clinic = Clinic.query.filter(Clinic.dso_id.in_(dso_ids)).first()
                    clinic_id = clinic.id if clinic else None
                    logger.debug(
                        f"Assigned default clinic_id {clinic_id} to patient based on dentist DSO associations"
                    )
                else:
                    logger.debug(
                        "No DSO associations found for dentist, clinic_id will be NULL (legacy mode)"
                    )

            # Create new patient in the database
            new_patient = Patient(
                name=patient_name,
                email=email,
                phone=phone,
                dob=parsed_dob,
                gender=gender,
                insurer=insurer,
                policy_id=policy_id,
                address=address,
                dentist_id=current_user.id,
                clinic_id=clinic_id,  # Assign clinic based on dentist's DSO
                snoring=snoring,
                snoring_other=snoring_other,
                daytime_sleepiness=daytime_sleepiness,
                daytime_sleepiness_other=daytime_sleepiness_other,
                sleep_study=sleep_study,
                sleep_study_date=parsed_sleep_study_date,
                cpap_intolerant=cpap_intolerant,
                cpap_intolerant_other=cpap_intolerant_other,
                create_date=datetime.now(),  # Set current date as created date
                last_update=datetime.now(),  # Set current date as last updated date
                upload_token=secrets.token_urlsafe(32),  # Generate a 32-byte URL-safe token
            )

            db.session.add(new_patient)
            db.session.flush()  # Flush to get patient ID before commit
            logger.debug(f"Created new patient with ID: {new_patient.id}")

            # Ensure file exists before trying to process it
            if billing_zip:
                upload_and_save_files(billing_zip, "billing", "billing", new_patient, "billing")

            if clinical_zip:
                upload_and_save_files(
                    clinical_zip, "imaging/clinical_pictures", "imaging", new_patient, "clinical_pictures"
                )

            if cbct_zip:
                upload_and_save_files(cbct_zip, "imaging/cbct", "imaging", new_patient, "cbct")

            if intraoral_zip:
                upload_and_save_files(
                    intraoral_zip, "imaging/intraoral_scan", "imaging", new_patient, "intraoral_scan"
                )

            if sleep_test_zip:
                upload_and_save_files(
                    sleep_test_zip, "medical/sleep_test", "medical", new_patient, "sleep_test"
                )

            if questionnaire_zip:
                upload_and_save_files(
                    questionnaire_zip, "medical/questionnaire", "medical", new_patient, "questionnaire"
                )

            if medical_background_zip:
                upload_and_save_files(
                    medical_background_zip,
                    "medical/medical_background",
                    "medical",
                    new_patient,
                    "medical_background",
                )

            # Commit all changes to the database
            db.session.commit()
            logger.debug("All changes committed to the database successfully")

            # Return success response
            return jsonify({"success": True, "patient_id": new_patient.id})

        except Exception as e:
            db.session.rollback()  # Rollback on error
            logger.error(f"Error during upload: {str(e)}")
            return jsonify({"success": False, "message": f"Error uploading data: {str(e)}"}), 500

    return render_template("upload_form.html")


@login_required
def upload_new() -> Any:
    if request.method == "POST":
        logger.debug("Processing upload new POST request")
        try:
            # Get clinic_id from form or assign dentist's default clinic
            clinic_id = request.form.get("clinic_id")
            if clinic_id:
                try:
                    clinic_id = int(clinic_id)
                    # Admins are not in dentist_clinic_association; they may assign to any active clinic.
                    if current_user.role == "admin":
                        clinic = Clinic.query.filter(
                            Clinic.id == clinic_id, Clinic.status == "active"
                        ).first()
                        if not clinic:
                            return (
                                jsonify(
                                    {
                                        "success": False,
                                        "message": "Invalid or inactive clinic selected.",
                                    }
                                ),
                                400,
                            )
                    elif not current_user.is_associated_with_clinic(clinic_id):
                        return (
                            jsonify(
                                {
                                    "success": False,
                                    "message": "You do not have permission to assign patients to this clinic.",
                                }
                            ),
                            403,
                        )
                except (ValueError, TypeError):
                    clinic_id = None

            # If no clinic_id provided, get dentist's first clinic
            if not clinic_id:
                dentist_clinics = current_user.clinics.all()
                if dentist_clinics:
                    clinic_id = dentist_clinics[0].id
                    logger.debug(f"Auto-assigning dentist's default clinic: {clinic_id}")
                else:
                    logger.warning(f"Dentist {current_user.id} has no associated clinics")

            patient_data = {
                "name": request.form.get("patient_name"),
                "email": request.form.get("email"),
                "phone": request.form.get("phone"),
                "dob": datetime.strptime(request.form.get("dob"), "%Y-%m-%d")
                if request.form.get("dob")
                else None,
                "gender": request.form.get("gender"),
                "insurer": request.form.get("insurer"),
                "policy_id": request.form.get("policy_id"),
                "address": request.form.get("address"),
                "snoring": request.form.get("snoring"),
                "snoring_other": (
                    request.form.get("snoring_other") if request.form.get("snoring") == "other" else None
                ),
                "daytime_sleepiness": request.form.get("daytime_sleepiness"),
                "daytime_sleepiness_other": (
                    request.form.get("daytime_sleepiness_other")
                    if request.form.get("daytime_sleepiness") == "other"
                    else None
                ),
                "sleep_study": request.form.get("sleep_study"),
                "sleep_study_date": (
                    datetime.strptime(request.form.get("sleep_study_date"), "%Y-%m-%d")
                    if request.form.get("sleep_study_date")
                    else None
                ),
                "sleep_study_doctor": (
                    request.form.get("sleep_study_doctor")
                    if request.form.get("sleep_study") == "yes"
                    else None
                ),
                "cpap_intolerant": request.form.get("cpap_intolerant"),
                "cpap_intolerant_other": (
                    request.form.get("cpap_intolerant_other")
                    if request.form.get("cpap_intolerant") == "other"
                    else None
                ),
                "create_date": datetime.now(),
                "last_update": datetime.now(),
                "payment_method": request.form.get("payment_method"),
                "status": request.form.get("status") or "new",
                "dentist_id": (
                    int(request.form.get("dentist_id"))
                    if current_user.role == "admin" and request.form.get("dentist_id")
                    else current_user.id
                ),
                "clinic_id": clinic_id,
            }

            logger.debug(f"Patient form data: {patient_data}")

            new_patient = Patient(**patient_data)
            db.session.add(new_patient)
            db.session.commit()
            logger.debug(f"Created new patient with ID: {new_patient.id}")

            # Log the assigned clinic and DSO for debugging
            if new_patient.clinic_id:
                clinic = Clinic.query.get(new_patient.clinic_id)
                if clinic:
                    logger.debug(f"Patient {new_patient.id} assigned to clinic: {clinic.name} (ID: {clinic.id})")
                    if clinic.dso_info:
                        logger.debug(
                            f"Patient {new_patient.id} assigned to DSO: {clinic.dso_info.name} (ID: {clinic.dso_info.id})"
                        )
                else:
                    logger.warning(
                        f"Patient {new_patient.id} assigned to non-existent clinic ID: {new_patient.clinic_id}"
                    )
            else:
                logger.warning(f"Patient {new_patient.id} created without clinic assignment")

            return jsonify({"success": True, "patient_id": new_patient.id})

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error during patient creation: {str(e)}")
            return jsonify({"success": False, "message": f"Error uploading data: {str(e)}"}), 500

    # GET request - prepare the form with dropdowns
    dsos = DSO.query.all()
    clinics = Clinic.query.all()

    # Organize clinics by DSO
    clinics_by_dso = {}
    for clinic in clinics:
        clinics_by_dso.setdefault(clinic.dso_id, []).append(clinic)

    # Organize dentists by clinic
    dentists_by_clinic = {}
    dentist_clinic_rows = db.session.query(
        dentist_clinic_association.c.dentist_id, dentist_clinic_association.c.clinic_id
    ).all()

    dentist_ids = {row.dentist_id for row in dentist_clinic_rows}
    dentist_lookup = {d.id: d for d in Dentist.query.filter(Dentist.id.in_(dentist_ids)).all()}

    for row in dentist_clinic_rows:
        dentists_by_clinic.setdefault(row.clinic_id, []).append(
            {"id": row.dentist_id, "name": dentist_lookup[row.dentist_id].name}
        )

    return render_template(
        "upload_form_new.html",
        dsos=dsos,
        clinics_by_dso=clinics_by_dso,
        dentists_by_clinic=dentists_by_clinic,
        is_admin=current_user.role == "admin",
    )


@login_required
def store_file_metadata() -> Any:
    """
    Stores file metadata in the database after upload.
    """
    try:
        logger.debug("=== START store_file_metadata ===")
        data = request.json
        logger.debug(f"Received metadata: {data}")

        patient_id = data.get("patient_id")
        s3_key = data.get("s3_key")

        # Validate required fields
        if not patient_id:
            logger.error(f"Missing 'patient_id' in request: {data}")
            return jsonify({"success": False, "message": "Missing 'patient_id' in request"}), 400
        if not s3_key:
            logger.error(f"Missing 's3_key' in request: {data}")
            return jsonify({"success": False, "message": "Missing 's3_key' in request"}), 400

        # Handle patient_id as string - convert to int if it's a string
        if isinstance(patient_id, str):
            try:
                patient_id = int(patient_id)
                logger.debug(f"Converted patient_id from string to int: {patient_id}")
            except ValueError:
                logger.error(f"Invalid patient_id format (not convertible to int): {patient_id}")
                return (
                    jsonify({"success": False, "message": f"Invalid patient_id format: {patient_id}"}),
                    400,
                )

        filename = s3_key.split("/")[-1]
        file_size = data.get("file_size", 0)
        file_type = data.get("file_type", "application/octet-stream")
        category = data.get("category")  # Ensure category is provided
        subcategory = data.get("subcategory")  # Ensure subcategory is provided

        logger.debug(f"Extracted data - patient_id: {patient_id}, s3_key: {s3_key}")
        logger.debug(f"Extracted data - filename: {filename}, file_size: {file_size}, file_type: {file_type}")
        logger.debug(f"Extracted data - category: {category}, subcategory: {subcategory}")

        # Validate category and subcategory
        if not category:
            logger.error(f"Missing 'category' for file metadata: {filename}")
            return jsonify({"success": False, "message": "Missing 'category' for file metadata"}), 400
        if not subcategory:
            logger.error(f"Missing 'subcategory' for file metadata: {filename}")
            return jsonify({"success": False, "message": "Missing 'subcategory' for file metadata"}), 400

        # Check if patient exists
        patient = Patient.query.get(patient_id)
        if not patient:
            logger.error(f"Patient with ID {patient_id} not found.")
            return jsonify({"success": False, "message": f"Patient with ID {patient_id} not found"}), 404

        logger.debug(f"Patient found: {patient.name} (ID: {patient.id})")
        logger.debug(f"Creating file entry for {filename} with s3_key: {s3_key}")

        # Check if S3 key already exists in the database to avoid duplicates
        existing_file = File.query.filter_by(s3_key=s3_key).first()
        if existing_file:
            logger.warning(f"File with s3_key {s3_key} already exists in database with ID {existing_file.id}")
            return jsonify(
                {
                    "success": True,
                    "message": "File metadata already exists.",
                    "file_id": existing_file.id,
                    "already_exists": True,
                }
            )

        bucket_name = os.getenv("S3_BUCKET_NAME")

        # Verify S3 key exists in bucket (but don't fail if check doesn't work)
        try:
            logger.debug(f"Checking if S3 key exists in bucket: {bucket_name}/{s3_key}")
            s3_client.head_object(Bucket=bucket_name, Key=s3_key)
            logger.debug(f"S3 object exists: {s3_key}")
        except Exception as s3_error:
            logger.warning(f"Unable to verify S3 object exists (will proceed anyway): {str(s3_error)}")

        # For CBCT DICOM: split multi-frame at upload so MPR works
        files_to_store = []
        if subcategory == "cbct" and is_dicom_file(s3_key):
            logger.info(f"CBCT DICOM detected: {s3_key}, checking for multi-frame split")
            try:
                response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
                file_bytes = response["Body"].read()
                for item in process_dicom_for_upload(file_bytes, s3_key, patient_id=str(patient_id)):
                    files_to_store.append(item)
                if len(files_to_store) > 1:
                    s3_client.delete_object(Bucket=bucket_name, Key=s3_key)
                    logger.info(f"Split into {len(files_to_store)} slices, deleted original")
            except Exception as split_err:
                logger.warning(f"Could not process DICOM for split, storing as-is: {split_err}")
                files_to_store = [{"s3_key": s3_key, "filename": filename, "file_size": file_size}]
        else:
            files_to_store = [{"s3_key": s3_key, "filename": filename, "file_size": file_size}]

        if len(files_to_store) == 0:
            files_to_store = [{"s3_key": s3_key, "filename": filename, "file_size": file_size}]

        file_ids = []
        for item in files_to_store:
            if "content" in item:
                s3_client.put_object(
                    Bucket=bucket_name,
                    Key=item["s3_key"],
                    Body=item["content"],
                    ContentType="application/dicom",
                )
            new_file = File(
                name=item["filename"],
                patient_id=patient_id,
                s3_key=item["s3_key"],
                upload_date=datetime.utcnow(),
                file_size=item["file_size"],
                file_type=file_type if "content" not in item else "application/dicom",
                category=category,
                subcategory=subcategory,
            )
            db.session.add(new_file)
            file_ids.append(new_file.id)

        db.session.commit()
        file_id = file_ids[0] if file_ids else None

        logger.info(f"File metadata stored successfully for file: {filename}, ID: {file_id}")
        logger.debug("=== END store_file_metadata ===")
        return jsonify({"success": True, "message": "File metadata stored successfully.", "file_id": file_id})

    except SQLAlchemyError as db_error:
        db.session.rollback()
        error_message = str(db_error)
        logger.error(f"Database error storing file metadata: {error_message}")
        logger.error(
            f"Data that caused the error: {data if 'data' in locals() else 'data not available'}"
        )

        # Check for common database errors
        if "IntegrityError" in error_message:
            if "foreign key constraint" in error_message.lower():
                logger.error("Foreign key constraint violation - patient ID may not exist")
            elif "unique constraint" in error_message.lower():
                logger.error("Unique constraint violation - duplicate file entry")

        return jsonify({"success": False, "message": f"Database error: {error_message}"}), 500

    except Exception as e:
        db.session.rollback()
        error_message = str(e)
        logger.error(f"Error storing file metadata: {error_message}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Stack trace:", exc_info=True)
        logger.error(f"Request data: {request.json}")
        return jsonify({"success": False, "message": f"Error storing file metadata: {error_message}"}), 500


@login_required
def store_admin_file_metadata() -> Any:
    """
    Stores admin file metadata in the database after upload.
    """
    try:
        data = request.json
        patient_id = data.get("patient_id")
        s3_key = data.get("s3_key")
        filename = s3_key.split("/")[-1]
        file_size = data.get("file_size", 0)
        file_type = data.get("file_type", "application/octet-stream")
        # New fields - default to True for admin uploads so all files are visible to everyone
        is_public = data.get("is_public", True)
        file_category = data.get("file_category")

        # Validate required fields
        if not patient_id or not s3_key:
            logger.error(f"Missing 'patient_id' or 's3_key' for file metadata: {filename}")
            return (
                jsonify({"success": False, "message": "Missing 'patient_id' or 's3_key' for file metadata"}),
                400,
            )

        # Check if patient exists
        patient = Patient.query.get(patient_id)
        if not patient:
            logger.error(f"Patient with ID {patient_id} not found.")
            return jsonify({"success": False, "message": f"Patient with ID {patient_id} not found"}), 404

        bucket_name = os.getenv("S3_BUCKET_NAME")
        if not bucket_name:
            return jsonify({"success": False, "message": "S3 bucket not configured"}), 500

        try:
            s3_client.head_object(Bucket=bucket_name, Key=s3_key)
        except Exception as s3_error:
            logger.error(
                "Admin file metadata rejected: S3 object missing for patient %s key %s: %s",
                patient_id,
                s3_key,
                s3_error,
            )
            return (
                jsonify(
                    {
                        "success": False,
                        "message": (
                            "Upload did not reach storage. Please retry the upload; "
                            "the file was not saved."
                        ),
                    }
                ),
                400,
            )

        # Create a new AdminFile entry in the database
        new_admin_file = AdminFile(
            name=filename,
            patient_id=patient_id,
            s3_key=s3_key,
            upload_date=datetime.utcnow(),
            file_size=file_size,
            file_type=file_type,
            is_public=bool(is_public),
            file_category=file_category,
        )
        db.session.add(new_admin_file)
        db.session.commit()

        logger.info(f"Admin file metadata stored successfully for file: {filename}")
        return jsonify({"success": True, "message": "Admin file metadata stored successfully."})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error storing admin file metadata: {e}")
        return jsonify({"success": False, "message": f"Error storing admin file metadata: {str(e)}"}), 500


@login_required
def delete_admin_file(file_id: int) -> Any:
    logger.info(f"Delete admin file request received for file ID: {file_id}")
    logger.info(f"User: {current_user.email}, Role: {getattr(current_user, 'role', 'unknown')}")

    try:
        # Only allow admins
        if not hasattr(current_user, "role") or current_user.role != "admin":
            logger.warning(f"Permission denied for user {current_user.email}")
            return jsonify({"success": False, "message": "Permission denied"}), 403

        admin_file = AdminFile.query.get(file_id)
        if not admin_file:
            logger.warning(f"Admin file with ID {file_id} not found")
            return jsonify({"success": False, "message": "File not found"}), 404

        logger.info(f"Found admin file: {admin_file.name}, S3 key: {admin_file.s3_key}")

        # Delete from S3
        try:
            s3_client = get_s3_client()
            s3_client.delete_object(Bucket=os.getenv("S3_BUCKET_NAME"), Key=admin_file.s3_key)
            logger.info(f"Deleted admin file '{admin_file.name}' from S3")
        except Exception as s3_error:
            logger.warning(f"Could not delete file from S3: {s3_error}")

        # Delete from database
        db.session.delete(admin_file)
        db.session.commit()

        logger.info(f"Successfully deleted admin file ID {file_id}")
        return jsonify({"success": True, "message": "File deleted successfully"})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting admin file {file_id}: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


@login_required
def update_file_comment(file_id: int) -> Any:
    try:
        # Fetch the file and associated patient and dentist
        file = File.query.get_or_404(file_id)
        patient = Patient.query.get_or_404(file.patient_id)
        dentist = Dentist.query.get_or_404(patient.dentist_id)

        # Check user permissions using proper access control
        if not current_user.can_access_patient(patient):
            return jsonify({"success": False, "message": "Permission denied"}), 403

        # Retrieve the comment from the JSON request body
        data = request.get_json()
        new_comment = data.get("comment", "").strip()

        # Validate the comment
        if not new_comment:
            return jsonify({"success": False, "message": "Comment cannot be empty."}), 400

        # Update the file's comment
        file.comment = new_comment
        db.session.commit()

        return jsonify({"success": True, "message": "Comment updated successfully."})

    except SQLAlchemyError as db_error:
        db.session.rollback()
        logger.error(f"Database error updating comment for file {file_id}: {str(db_error)}")
        return jsonify({"success": False, "message": "Database error occurred."}), 500

    except Exception as e:
        logger.error(f"Error updating comment for file {file_id}: {str(e)}")
        return jsonify({"success": False, "message": "An unexpected error occurred."}), 500


@login_required
def update_file_mapping(file_id: int) -> Any:
    try:
        # Fetch the file by ID
        file = File.query.get_or_404(file_id)

        # Check if the user has permission to update the mapping
        patient = Patient.query.get_or_404(file.patient_id)
        if current_user.id != patient.dentist_id and not current_user.is_admin:
            return (
                jsonify({"success": False, "message": "You do not have permission to update this file."}),
                403,
            )

        # Get the new mapping from the form data
        new_mapping = request.form.get("mapping")
        if not new_mapping:
            return jsonify({"success": False, "message": "Mapping selection cannot be empty."}), 400

        # Update the mapping field in the File model
        file.mapping = new_mapping

        # Commit the changes to the database
        db.session.commit()
        return jsonify({"success": True, "message": "Mapping updated successfully."}), 200

    except Exception as e:
        # Log the exception for debugging (optional)
        print(f"Error updating mapping: {e}")
        return jsonify({"success": False, "message": "An error occurred while updating the mapping."}), 500


def register_file_upload_routes(main) -> None:
    """Register file upload/management routes onto the main Blueprint."""
    # Keep endpoint names stable (e.g. url_for('main.upload')) by setting endpoint= explicitly.
    main.add_url_rule("/upload", endpoint="upload", view_func=upload, methods=["GET", "POST"])
    main.add_url_rule("/upload_new", endpoint="upload_new", view_func=upload_new, methods=["GET", "POST"])
    main.add_url_rule(
        "/store_file_metadata",
        endpoint="store_file_metadata",
        view_func=store_file_metadata,
        methods=["POST"],
    )
    main.add_url_rule(
        "/store_admin_file_metadata",
        endpoint="store_admin_file_metadata",
        view_func=store_admin_file_metadata,
        methods=["POST"],
    )
    main.add_url_rule(
        "/delete_admin_file/<int:file_id>",
        endpoint="delete_admin_file",
        view_func=delete_admin_file,
        methods=["POST"],
    )
    main.add_url_rule(
        "/file/<int:file_id>/update_comment",
        endpoint="update_file_comment",
        view_func=update_file_comment,
        methods=["POST"],
    )
    main.add_url_rule(
        "/file/<int:file_id>/update_mapping",
        endpoint="update_file_mapping",
        view_func=update_file_mapping,
        methods=["POST"],
    )
