from __future__ import annotations

import logging
import os
import secrets
import traceback
import re
from datetime import datetime, timedelta
from typing import Any

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, text
from werkzeug.security import check_password_hash, generate_password_hash

from flask_app.extensions import db
from flask_app.models import Dentist, Clinic, DSO
from flask_app.s3_utils import get_s3_client

logger = logging.getLogger(__name__)

# Replace any direct s3_client creation with the utility function
s3_client = get_s3_client()


def test_s3_access() -> None:
    bucket_name = os.getenv("S3_BUCKET_NAME")  # Replace with your actual bucket name
    try:
        response = s3_client.list_objects_v2(Bucket=bucket_name, MaxKeys=5)
        if "Contents" in response:
            logger.info(
                "Successfully accessed S3. Files in bucket '%s': %s",
                bucket_name,
                [file["Key"] for file in response["Contents"]],
            )
        else:
            logger.info("Bucket '%s' is empty or does not have accessible files.", bucket_name)
    except Exception as e:
        logger.error("Failed to access S3 bucket '%s': %s", bucket_name, str(e))


def check_db_connection() -> bool:
    try:
        result = db.session.execute(text("SELECT COUNT(*) FROM dentists"))
        count = result.scalar()
        logger.info("Successfully connected to the database. Number of dentists: %s", count)
        return True
    except Exception as e:
        logger.error("Error connecting to the database: %s", str(e))
        return False


@login_required
def index() -> Any:
    logger.debug("Accessing index page")
    test_s3_access()  # Test S3 access on homepage load

    # Redirect everyone to admin home
    return redirect(url_for("main.admin_home"))


def login() -> Any:
    """Redirect to MFA login - MFA is now the default login method"""
    logger.debug("Accessing login page - redirecting to MFA login")
    return redirect(url_for("main.login_mfa"))


@login_required
def logout() -> Any:
    logger.debug("Logging out user")
    session.pop("clinic_id", None)
    session.pop("dso_id", None)
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.login_mfa"))


def login_mfa() -> Any:
    """MFA login page - step 1: email input"""
    logger.debug("Accessing MFA login page")

    if not check_db_connection():
        flash("Unable to connect to the database. Please try again later.")
        return render_template("login_mfa.html")

    if current_user.is_authenticated:
        logger.debug("User is already authenticated, redirecting to admin_home")
        return redirect(url_for("main.admin_home"))

    # Check if we're on verification step
    step = request.args.get("step")
    email = request.args.get("email")

    if step == "verify" and email:
        return render_template("login_mfa.html", step="verify", email=email)

    return render_template("login_mfa.html")


def login_mfa_send_code() -> Any:
    """Send MFA verification code to user's email"""
    logger.debug("MFA: Sending verification code")

    if not check_db_connection():
        return (
            jsonify(
                {
                    "success": False,
                    "message": "Unable to connect to the database. Please try again later.",
                }
            ),
            500,
        )

    # Get email from form data or JSON
    email = None
    if request.form:
        email = request.form.get("email")
    elif request.is_json and request.json:
        email = request.json.get("email")

    if not email:
        logger.error("MFA: No email provided in request")
        return jsonify({"success": False, "message": "Email is required."}), 400

    email = email.strip().lower()

    # Check if user exists (case-insensitive email search)
    dentist = Dentist.query.filter(func.lower(Dentist.email) == email).first()

    if not dentist:
        # Inform user that email is not registered
        logger.warning("MFA: Login attempt for non-existent email: %s", email)
        return (
            jsonify(
                {
                    "success": False,
                    "message": "This email is not registered. Please contact your administrator if you believe this is an error.",
                }
            ),
            200,
        )

    # Generate 6-digit code (using secrets for cryptographically secure random)
    mfa_code = str(secrets.randbelow(900000) + 100000)  # Generates 100000-999999

    # Store code in session with expiration (10 minutes) - normalize email to lowercase
    session["mfa_code"] = mfa_code
    session["mfa_email"] = email.lower().strip()
    session["mfa_expires"] = (datetime.utcnow() + timedelta(minutes=10)).isoformat()

    logger.debug("MFA: Generated code for %s (code stored in session)", email)

    # Send email with verification code
    try:
        from flask_app.routes.file_management_routes import send_email_with_sendgrid

        subject = "Your Vizbriz Login Verification Code"
        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <h2 style="color: #03dac6;">Vizbriz Login Verification</h2>
                <p>Hello {dentist.name},</p>
                <p>Your verification code for login is:</p>
                <div style="background-color: #1e1e1e; color: #03dac6; padding: 20px; text-align: center; font-size: 32px; font-weight: bold; letter-spacing: 8px; border-radius: 8px; margin: 20px 0;">
                    {mfa_code}
                </div>
                <p>This code will expire in 10 minutes.</p>
                <p>If you did not request this code, please ignore this email.</p>
                <p style="margin-top: 30px; color: #666; font-size: 12px;">
                    This is an automated message from Vizbriz Sleep Apnea Solutions.
                </p>
            </div>
        </body>
        </html>
        """

        text_content = f"""
        Vizbriz Login Verification

        Hello {dentist.name},

        Your verification code for login is: {mfa_code}

        This code will expire in 10 minutes.

        If you did not request this code, please ignore this email.

        This is an automated message from Vizbriz Sleep Apnea Solutions.
        """

        email_sent = send_email_with_sendgrid(
            recipient_email=email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            patient_id=None,
            sender_id=None,
            email_type="mfa_verification",
            sender_type="system",
            skip_db_logging=True,  # Skip DB logging for MFA codes
        )

        if email_sent:
            logger.info("MFA: Verification code sent successfully to %s", email)
            return jsonify(
                {"success": True, "email": email, "message": "Verification code sent to your email."}
            )

        logger.error("MFA: Failed to send verification code to %s", email)
        return jsonify({"success": False, "message": "Failed to send verification code. Please try again."}), 500

    except Exception as e:
        logger.error("MFA: Error sending verification code: %s", str(e))
        logger.error(traceback.format_exc())
        return jsonify({"success": False, "message": "An error occurred. Please try again."}), 500


def login_mfa_verify() -> Any:
    """Verify MFA code and log user in"""
    logger.debug("MFA: Verifying code")

    if not check_db_connection():
        flash("Unable to connect to the database. Please try again later.")
        return redirect(url_for("main.login_mfa"))

    email = request.form.get("email")
    code = request.form.get("code", "").strip()

    if not email or not code:
        flash("Email and verification code are required.")
        return redirect(url_for("main.login_mfa", step="verify", email=email))

    # Check session for stored code
    stored_code = session.get("mfa_code")
    stored_email = session.get("mfa_email")
    expires_str = session.get("mfa_expires")

    if not stored_code or not stored_email or not expires_str:
        flash("Verification code expired or invalid. Please request a new code.")
        return redirect(url_for("main.login_mfa"))

    # Check expiration
    try:
        expires = datetime.fromisoformat(expires_str)
        if datetime.utcnow() > expires:
            # Clear expired session data
            session.pop("mfa_code", None)
            session.pop("mfa_email", None)
            session.pop("mfa_expires", None)
            flash("Verification code expired. Please request a new code.")
            return redirect(url_for("main.login_mfa"))
    except Exception as e:
        logger.error("MFA: Error parsing expiration: %s", str(e))
        flash("Verification code expired. Please request a new code.")
        return redirect(url_for("main.login_mfa"))

    # Verify email matches
    if stored_email.lower() != email.lower():
        flash("Email mismatch. Please request a new code.")
        return redirect(url_for("main.login_mfa"))

    # Verify code
    if stored_code != code:
        logger.warning("MFA: Invalid code attempt for %s", email)
        flash("Invalid verification code. Please try again.")
        return redirect(url_for("main.login_mfa", step="verify", email=email))

    # Code is valid - get user and log them in (case-insensitive email search)
    email_lower = email.lower().strip()
    dentist = Dentist.query.filter(func.lower(Dentist.email) == email_lower).first()

    if not dentist:
        logger.error("MFA: User not found after code verification: %s", email)
        flash("User not found. Please contact support.")
        return redirect(url_for("main.login_mfa"))

    # Clear MFA session data
    session.pop("mfa_code", None)
    session.pop("mfa_email", None)
    session.pop("mfa_expires", None)

    # Log user in
    logger.info("MFA: Successful login for %s", email)
    login_user(dentist)

    next_page = request.args.get("next")
    if next_page:
        return redirect(next_page)

    # Check if user has multiple clinics/DSOs - require context selection
    clinic_ids = dentist.get_clinic_ids() if hasattr(dentist, "get_clinic_ids") else []
    if len(clinic_ids) > 1:
        logger.debug("MFA: User has multiple clinics, redirecting to select context")
        return redirect(url_for("main.login_select_context"))
    elif len(clinic_ids) == 1:
        clinic = Clinic.query.get(clinic_ids[0])
        if clinic:
            session["clinic_id"] = clinic.id
            session["dso_id"] = clinic.dso_id
            logger.debug("MFA: Set session clinic_id=%s, dso_id=%s", clinic.id, clinic.dso_id)
    else:
        dso_ids = dentist.get_dso_ids() if hasattr(dentist, "get_dso_ids") else []
        if len(dso_ids) == 1:
            session["dso_id"] = dso_ids[0]
            logger.debug("MFA: Set session dso_id=%s (no clinics)", dso_ids[0])

    # Redirect to admin home after successful login
    logger.debug("MFA: User logged in successfully, redirecting to admin_home")
    return redirect(url_for("main.admin_home"))


@login_required
def login_select_context() -> Any:
    """Show clinic selection when user has multiple clinics; save choice to session."""
    clinic_ids = current_user.get_clinic_ids() if hasattr(current_user, "get_clinic_ids") else []
    if len(clinic_ids) <= 1:
        # Single or no clinics - set session and redirect
        if len(clinic_ids) == 1:
            clinic = Clinic.query.get(clinic_ids[0])
            if clinic:
                session["clinic_id"] = clinic.id
                session["dso_id"] = clinic.dso_id
        return redirect(url_for("main.admin_home"))

    if request.method == "POST":
        clinic_id = request.form.get("clinic_id")
        if clinic_id and int(clinic_id) in clinic_ids:
            clinic_id = int(clinic_id)
            clinic = Clinic.query.get(clinic_id)
            if clinic:
                session["clinic_id"] = clinic.id
                session["dso_id"] = clinic.dso_id
                logger.info("User %s selected clinic %s (%s)", current_user.email, clinic.id, clinic.name)
        return redirect(request.args.get("next") or url_for("main.admin_home"))

    # Build clinic list with DSO names
    clinics = []
    for cid in clinic_ids:
        clinic = Clinic.query.get(cid)
        if clinic:
            dso_name = None
            if clinic.dso_id:
                dso = DSO.query.get(clinic.dso_id)
                if dso:
                    dso_name = dso.name
            clinics.append({"id": clinic.id, "name": clinic.name, "dso_name": dso_name})
    return render_template("login_select_context.html", clinics=clinics)


@login_required
def change_password() -> Any:
    if request.method == "POST":
        # Retrieve form data
        current_password = request.form.get("current_password")
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        # Validate current password
        if not check_password_hash(current_user.password, current_password):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("main.change_password"))

        # Validate new password meets HIPAA requirements
        if not is_hipaa_compliant(new_password):
            flash(
                "New password must be at least 8 characters long, contain uppercase, lowercase, a number, and a special character.",
                "error",
            )
            return redirect(url_for("main.change_password"))

        # Check if new password matches confirmation
        if new_password != confirm_password:
            flash("New password and confirmation do not match.", "error")
            return redirect(url_for("main.change_password"))

        # Update password
        current_user.password = generate_password_hash(new_password)
        db.session.commit()
        flash("Password changed successfully!", "success")
        return redirect(url_for("main.index"))

    # Render the change password template
    return render_template("change_password.html")


def is_hipaa_compliant(password: str) -> bool:
    """Check if the password meets HIPAA requirements."""
    return bool(
        len(password) >= 8
        and re.search(r"[A-Z]", password)
        and re.search(r"[a-z]", password)
        and re.search(r"[0-9]", password)
        and re.search(r"[@$!%*?&]", password)
    )


def register_auth_routes(main: Blueprint) -> None:
    # Keep endpoint names stable (e.g. url_for('main.login_mfa')) by setting endpoint= explicitly.
    main.add_url_rule("/", endpoint="index", view_func=index)
    main.add_url_rule("/home", endpoint="index", view_func=index)

    main.add_url_rule("/login", endpoint="login", view_func=login, methods=["GET", "POST"])
    main.add_url_rule("/logout", endpoint="logout", view_func=logout)

    main.add_url_rule("/login_mfa", endpoint="login_mfa", view_func=login_mfa, methods=["GET"])
    main.add_url_rule(
        "/login_mfa/send_code", endpoint="login_mfa_send_code", view_func=login_mfa_send_code, methods=["POST"]
    )
    main.add_url_rule(
        "/login_mfa/verify", endpoint="login_mfa_verify", view_func=login_mfa_verify, methods=["POST"]
    )
    main.add_url_rule(
        "/login/select_context",
        endpoint="login_select_context",
        view_func=login_select_context,
        methods=["GET", "POST"],
    )

    main.add_url_rule(
        "/change_password", endpoint="change_password", view_func=change_password, methods=["GET", "POST"]
    )

