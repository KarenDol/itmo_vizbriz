"""
VizBriz Multilingual Quiz Routes
Handles quiz display, submission, dashboard, and reporting
"""

from flask import Blueprint, request, jsonify, render_template, current_app, send_file
import os
import base64
from flask_login import login_required, current_user
from flask_app.extensions import db
from flask_app.models import VizBrizQuiz, Patient, Clinic, AdminFile
from flask_app.helpers.vizbriz_quiz_helpers import (
    load_quiz_package,
    load_followup_quiz_package,
    clear_quiz_package_cache,
    get_localized_text,
    evaluate_quiz,
    evaluate_followup_quiz,
    build_enhanced_answers_from_package,
    save_vizbriz_quiz,
    save_observations_to_store,
    get_quiz_by_id,
    get_patient_quizzes,
    create_and_store_questionnaire_pdf,
    create_and_store_l2_assessment_pdf,
    _static_folder_path,
)
from datetime import datetime
import json
import csv
import io
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_RIGHT
import boto3
import os
from xhtml2pdf import pisa
from flask import render_template_string
import requests
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.parse import urljoin, urlparse

EXTERNAL_REPORT_API_DEFAULT_URL = "https://vizbriz.dvir.us/reports"
EXTERNAL_REPORT_API_DEFAULT_TIMEOUT = 15


def _post_external_report(payload: dict) -> tuple[Optional[dict], Optional[str]]:
    """Send payload to external report API and return response or error message."""

    if not isinstance(payload, dict):
        return None, "invalid_payload"

    token = os.getenv("LEVEL_1_REPORT_API_TOKEN")
    if not token:
        current_app.logger.warning("LEVEL_1_REPORT_API_TOKEN not configured; skipping external report API call")
        return None, "missing_token"

    url = (
        current_app.config.get("EXTERNAL_REPORT_API_URL")
        or EXTERNAL_REPORT_API_DEFAULT_URL
    )

    timeout = current_app.config.get("EXTERNAL_REPORT_API_TIMEOUT")
    if not isinstance(timeout, (int, float)):
        timeout = EXTERNAL_REPORT_API_DEFAULT_TIMEOUT

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    try:
        current_app.logger.info(f"=== EXTERNAL REPORT API REQUEST ===")
        current_app.logger.info(f"URL: {url}")
        current_app.logger.info(f"Timeout: {timeout}")
        current_app.logger.info(f"Token present: {bool(token)}")
        current_app.logger.info(f"Payload size: {len(json.dumps(payload))} bytes")
        current_app.logger.info(f"Payload evaluation_summary keys: {list(payload.get('evaluation_summary', {}).keys())}")
        
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
        current_app.logger.info(f"Response status code: {response.status_code}")
        current_app.logger.info(f"Response headers: {dict(response.headers)}")
        
        response.raise_for_status()
    except requests.RequestException as exc:
        # Log detailed response information for debugging
        response_details = {}
        if hasattr(exc, 'response') and exc.response is not None:
            response_details = {
                'status_code': exc.response.status_code,
                'headers': dict(exc.response.headers),
                'response_body': exc.response.text[:1000] if exc.response.text else None,
                'url': exc.response.url if hasattr(exc.response, 'url') else url
            }
            current_app.logger.error(
                "External report API request failed: %s. Response details: status_code=%s, url=%s, response_body=%s",
                exc, 
                response_details.get('status_code'),
                response_details.get('url'),
                response_details.get('response_body')
            )
        else:
            current_app.logger.error(
                "External report API request failed: %s (no response object available)", exc, exc_info=True
            )
        return None, str(exc)

    try:
        data = response.json()
        current_app.logger.info(f"Response JSON parsed successfully")
    except ValueError as json_err:
        current_app.logger.error(f"❌ JSON PARSE ERROR ===")
        current_app.logger.error(f"External report API returned non-JSON response: {response.text[:500]}")
        current_app.logger.error(f"Response status: {response.status_code}")
        current_app.logger.error(f"JSON parse error: {json_err}")
        return None, "invalid_json_response"

    if isinstance(data, dict):
        # Log full API response for debugging
        current_app.logger.info(f"External report API response keys: {list(data.keys())}")
        current_app.logger.info(f"External report API response (truncated): {str(data)[:500]}")
        
        # Construct full URLs from relative paths
        base_url = current_app.config.get("EXTERNAL_REPORT_BASE_URL")
        if not base_url:
            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
        
        current_app.logger.info(f"Base URL for constructing full URLs: {base_url}")
        
        # Handle PDF URL - the API returns a relative path like '/reports/15533.pdf'
        if data.get("pdf"):
            pdf_path = data["pdf"]
            current_app.logger.info(f"PDF path from API: {pdf_path}")
            if not data.get("pdf_url"):
                # Construct full URL
                if pdf_path.startswith('http://') or pdf_path.startswith('https://'):
                    # Already a full URL
                    data["pdf_url"] = pdf_path
                    current_app.logger.info(f"PDF path is already a full URL: {data['pdf_url']}")
                else:
                    # Relative path - join with base URL
                    data["pdf_url"] = urljoin(base_url, pdf_path)
                    current_app.logger.info(f"Created pdf_url from relative path: {data['pdf_url']}")
            else:
                current_app.logger.info(f"pdf_url already exists in response: {data['pdf_url']}")
        
        # Handle HTML/Frame URL
        if data.get("html") and not data.get("frame_url"):
            html_path = data["html"]
            current_app.logger.info(f"HTML path from API: {html_path}")
            if html_path.startswith('http://') or html_path.startswith('https://'):
                data["frame_url"] = html_path
            else:
                data["frame_url"] = urljoin(base_url, html_path)
            current_app.logger.info(f"Created frame_url: {data['frame_url']}")
        
        # Log final URLs
        current_app.logger.info(f"Final URLs - pdf_url: {data.get('pdf_url')}, frame_url: {data.get('frame_url')}")
        current_app.logger.info(f"Original API fields - pdf: {data.get('pdf')}, html: {data.get('html')}")
    return data, None


def _download_and_upload_pdf(pdf_url: str, patient_id: int, quiz_id: int) -> tuple[Optional[str], Optional[bytes], Optional[str]]:
    """
    Download PDF from external API and upload to S3 as Level 1 Report.
    Returns (s3_key, pdf_content, error_message) tuple.
    """
    import time
    
    current_app.logger.info(f"=== PDF DOWNLOAD/UPLOAD START ===")
    current_app.logger.info(f"pdf_url: {pdf_url}")
    current_app.logger.info(f"patient_id: {patient_id}")
    current_app.logger.info(f"quiz_id: {quiz_id}")
    
    if not pdf_url:
        current_app.logger.error(f"❌ No PDF URL provided")
        return None, None, "No PDF URL provided"
    
    if not patient_id:
        current_app.logger.error(f"❌ No patient_id provided")
        return None, None, "No patient_id provided"
    
    try:
        # Download PDF from external API with retry logic
        # Add a delay to allow PDF to be created before fetching
        current_app.logger.info(f"Waiting 3s before first download attempt to allow PDF to be created...")
        time.sleep(3)  # 3 second delay before first attempt
        
        max_retries = 3
        retry_delay = 2  # seconds
        pdf_response = None
        
        for attempt in range(max_retries):
            current_app.logger.info(f"Downloading PDF from {pdf_url} for patient {patient_id} (attempt {attempt + 1}/{max_retries})")
            pdf_response = requests.get(pdf_url, timeout=30, allow_redirects=True)
            
            # Check if we got actual content (not 204 No Content or empty response)
            if pdf_response.status_code == 200 and len(pdf_response.content) > 0:
                current_app.logger.info(f"PDF download successful on attempt {attempt + 1}")
                break
            elif pdf_response.status_code == 204 or len(pdf_response.content) == 0:
                current_app.logger.warning(f"PDF not ready yet (status={pdf_response.status_code}, size={len(pdf_response.content)}), waiting {retry_delay}s before retry...")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 1.5  # Exponential backoff
            else:
                # Got a response but not success, try raising for status
                pdf_response.raise_for_status()
        
        # Final check after retries
        if pdf_response is None or pdf_response.status_code != 200:
            if pdf_response is None:
                error_msg = f"PDF download failed: No response received after {max_retries} attempts"
            elif pdf_response.status_code == 204:
                error_msg = f"PDF still not ready after {max_retries} attempts (HTTP 204 No Content). The PDF may be generating asynchronously and needs more time. PDF URL: {pdf_url}"
            else:
                error_msg = f"PDF download failed with status {pdf_response.status_code}: {pdf_response.text[:200]}"
            current_app.logger.error(f"❌ {error_msg}")
            return None, None, error_msg
        
        # Log response details for debugging
        content_type = pdf_response.headers.get('Content-Type', '').lower()
        content_length = pdf_response.headers.get('Content-Length')
        status_code = pdf_response.status_code
        final_url = pdf_response.url  # May differ from pdf_url if redirected
        
        current_app.logger.info(
            f"PDF download response: status={status_code}, content_type={content_type}, "
            f"content_length={content_length}, final_url={final_url}, actual_size={len(pdf_response.content)} bytes"
        )
        
        # Validate PDF content
        pdf_content = pdf_response.content
        
        # Debug: Log raw response details
        current_app.logger.debug(f"PDF response raw content length: {len(pdf_content)}, type: {type(pdf_content)}")
        if len(pdf_content) > 0:
            current_app.logger.debug(f"First 50 bytes (hex): {pdf_content[:50].hex()}")
            current_app.logger.debug(f"First 50 bytes (ascii): {pdf_content[:50]}")
        
        # Check if content is empty
        if not pdf_content or len(pdf_content) == 0:
            # Log more details about why it might be empty
            error_msg = f"Downloaded PDF is empty (0 bytes) from {pdf_url}. Response status: {status_code}, Content-Type: {content_type}, Content-Length header: {content_length}, Final URL: {final_url}"
            current_app.logger.error(error_msg)
            # Check if maybe the file is being generated asynchronously
            if status_code == 200 and content_type and 'pdf' not in content_type:
                current_app.logger.warning(f"Response is not PDF type - might be HTML error page or redirect. Content-Type: {content_type}")
            return None, None, error_msg
        
        # Check if it's actually a PDF by checking magic bytes (PDF files start with %PDF)
        if not pdf_content.startswith(b'%PDF'):
            # Log first 200 bytes for debugging
            preview = pdf_content[:200].decode('utf-8', errors='ignore')
            error_msg = f"Downloaded file is not a valid PDF (does not start with %PDF). Content-Type: {content_type}, Size: {len(pdf_content)} bytes, Preview: {preview[:100]}"
            current_app.logger.error(error_msg)
            return None, None, error_msg
        
        # Log PDF details for debugging
        current_app.logger.info(f"PDF validation passed: Content-Type={content_type}, Size={len(pdf_content)} bytes, Starts with PDF magic bytes")
        
        # Generate filename
        filename = f"Level_1_Report_Quiz_{quiz_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
        s3_key = f"patients/{patient_id}/reports/{filename}"
        
        # Upload to S3
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-west-2')
        )
        
        bucket_name = os.getenv('S3_BUCKET_NAME')
        if not bucket_name:
            return None, None, "S3_BUCKET_NAME not configured"
        
        # Upload PDF to S3
        current_app.logger.info(f"=== S3 UPLOAD ===")
        current_app.logger.info(f"S3 Bucket: {bucket_name}")
        current_app.logger.info(f"S3 Key: {s3_key}")
        current_app.logger.info(f"PDF size: {len(pdf_content)} bytes")
        current_app.logger.info(f"AWS Region: {os.getenv('AWS_REGION', 'us-west-2')}")
        current_app.logger.info(f"AWS Access Key ID present: {bool(os.getenv('AWS_ACCESS_KEY_ID'))}")
        current_app.logger.info(f"AWS Secret Key present: {bool(os.getenv('AWS_SECRET_ACCESS_KEY'))}")
        
        pdf_file = io.BytesIO(pdf_content)
        pdf_file.seek(0)  # Reset file pointer to beginning
        try:
            s3_client.upload_fileobj(
                pdf_file,
                bucket_name,
                s3_key,
                ExtraArgs={'ContentType': 'application/pdf'}
            )
            current_app.logger.info(f"✅ PDF uploaded to S3: {s3_key}, Size: {len(pdf_content)} bytes")
        except Exception as s3_err:
            current_app.logger.error(f"❌ S3 UPLOAD FAILED ===")
            current_app.logger.error(f"S3 upload error: {str(s3_err)}")
            current_app.logger.error(f"Error type: {type(s3_err).__name__}")
            current_app.logger.error(f"S3 details: bucket={bucket_name}, key={s3_key}", exc_info=True)
            # Return the PDF bytes so the caller can still email the report to the patient.
            return None, pdf_content, f"S3 upload failed: {str(s3_err)}"
        
        # Verify the file was uploaded correctly by checking if it exists in S3
        try:
            s3_client.head_object(Bucket=bucket_name, Key=s3_key)
            current_app.logger.info(f"Verified PDF exists in S3: {s3_key}")
        except Exception as verify_error:
            current_app.logger.error(f"Failed to verify PDF in S3 after upload: {verify_error}")
            # Return the PDF bytes so the caller can still email the report to the patient.
            return None, pdf_content, f"PDF uploaded but verification failed: {str(verify_error)}"
        
        # Save to adminfiles table
        current_app.logger.info(f"=== SAVING TO ADMINFILES ===")
        current_app.logger.info(f"Filename: {filename}")
        current_app.logger.info(f"Patient ID: {patient_id}")
        try:
            new_admin_file = AdminFile(
                name=filename,
                patient_id=patient_id,
                file_type='application/pdf',
                file_size=len(pdf_content),
                s3_key=s3_key,
                upload_date=datetime.utcnow(),
                file_category='Level 1 - Screening (Questionnaire Only)',
                is_public=False
            )
            db.session.add(new_admin_file)
            db.session.commit()
            current_app.logger.info(f"✅ Level 1 Report saved to adminfiles for patient {patient_id}: {filename}, Size: {len(pdf_content)} bytes, S3 Key: {s3_key}, AdminFile ID: {new_admin_file.id}")
        except Exception as db_err:
            current_app.logger.error(f"❌ ADMINFILES SAVE FAILED ===")
            current_app.logger.error(f"Failed to save to adminfiles: {str(db_err)}")
            current_app.logger.error(f"Error type: {type(db_err).__name__}", exc_info=True)
            db.session.rollback()
            # Don't fail the whole operation if DB save fails - PDF is already in S3
            current_app.logger.warning(f"Continuing despite adminfiles save failure - PDF is in S3")
        
        current_app.logger.info(f"=== PDF DOWNLOAD/UPLOAD SUCCESS ===")
        return s3_key, pdf_content, None
        
    except requests.RequestException as e:
        error_msg = f"Failed to download PDF: {str(e)}"
        current_app.logger.error(error_msg, exc_info=True)
        return None, None, error_msg
    except Exception as e:
        db.session.rollback()
        error_msg = f"Failed to upload PDF to S3: {str(e)}"
        current_app.logger.error(error_msg, exc_info=True)
        return None, None, error_msg


def _upload_level1_pdf_bytes(pdf_content: bytes, patient_id: int, quiz_id: int, filename: Optional[str] = None) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Upload already-generated PDF bytes to S3 + adminfiles as a Level 1 report.
    Returns (s3_key, filename, error_message).
    """
    if not pdf_content:
        return None, None, "No PDF content provided"
    if not patient_id:
        return None, None, "No patient_id provided"

    try:
        import boto3

        final_filename = filename or f"Level_1_Report_Quiz_{quiz_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
        s3_key = f"patients/{patient_id}/reports/{final_filename}"

        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-west-2')
        )

        bucket_name = os.getenv('S3_BUCKET_NAME')
        if not bucket_name:
            return None, None, "S3_BUCKET_NAME not configured"

        pdf_file = io.BytesIO(pdf_content)
        pdf_file.seek(0)
        s3_client.upload_fileobj(
            pdf_file,
            bucket_name,
            s3_key,
            ExtraArgs={'ContentType': 'application/pdf'}
        )

        # Save to adminfiles table (best-effort)
        try:
            new_admin_file = AdminFile(
                name=final_filename,
                patient_id=patient_id,
                file_type='application/pdf',
                file_size=len(pdf_content),
                s3_key=s3_key,
                upload_date=datetime.utcnow(),
                file_category='Level 1 - Screening (Questionnaire Only)',
                is_public=False
            )
            db.session.add(new_admin_file)
            db.session.commit()
        except Exception as db_err:
            current_app.logger.error(f"Failed to save Level 1 PDF to adminfiles (continuing): {db_err}")
            db.session.rollback()

        return s3_key, final_filename, None
    except Exception as e:
        current_app.logger.error(f"Upload PDF bytes failed: {e}", exc_info=True)
        return None, None, f"Upload failed: {str(e)}"


def _send_patient_email_with_pdf(patient_email: str, patient_id: int, pdf_content: bytes, pdf_filename: str, 
                                  clinic_name: str = None, clinic_logo_url: str = None, clinic_phone: str = None,
                                  dso_name: str = None, dso_logo_url: str = None, dso_id: int = None,
                                  patient_name: str = None, evaluation_result: dict = None, language: str = 'en') -> bool:
    """
    Send email to patient with PDF attachment, thanking them and suggesting to contact clinic.
    Includes DSO logo and VizBriz logo, with fallback to DSO if no clinic exists.
    """
    if not patient_email:
        current_app.logger.warning("No patient email provided, skipping patient email")
        return False
    
    # Log function entry with details
    current_app.logger.info(f"_send_patient_email_with_pdf called: email={patient_email}, patient_id={patient_id}, pdf_filename={pdf_filename}, pdf_size={len(pdf_content) if pdf_content else 0} bytes")
    
    try:
        from flask_mail import Mail, Message
        from flask import url_for
        import os
        
        mail = Mail(current_app)
        sender_email = current_app.config.get('MAIL_DEFAULT_SENDER', 'info@vizbriz.com')
        
        # Log attempt to send email
        current_app.logger.info(f"Attempting to send patient email to {patient_email} with PDF attachment {pdf_filename}")
        
        # Get base URL for images and consultation link
        try:
            from flask import request
            base_url = request.host_url.rstrip('/') if hasattr(request, 'host_url') else current_app.config.get('BASE_URL', 'https://vizbriz.com')
        except:
            base_url = current_app.config.get('BASE_URL', 'https://vizbriz.com')
        
        # Build consultation URL with DSO ID if available
        consultation_url = f"{base_url}/consultation_form?email={patient_email}"
        if dso_id:
            consultation_url += f"&dso_id={dso_id}"
        
        # Get patient name for personalization
        display_name = "Valued Patient"
        if patient_name:
            # Filter out test names
            if patient_name.lower() not in ['test patient', 'patient', 'unknown']:
                display_name = patient_name  # Use full name
        
        # Determine organization name - use clinic name, fall back to DSO name
        organization_name = clinic_name if clinic_name else (dso_name if dso_name else "Vizbriz")
        
        # Get phone number - use clinic phone, fall back to DSO phone if available
        phone_number = clinic_phone if clinic_phone else "+1 647 867 8346"
        
        # If no clinic phone but we have DSO, try to get DSO phone
        if not clinic_phone and dso_id:
            try:
                from flask_app.models import DSO
                dso = DSO.query.get(dso_id)
                if dso and dso.telephone:
                    phone_number = dso.telephone
            except Exception as e:
                current_app.logger.warning(f"Could not get DSO phone: {e}")
        
        # Assessment name
        assessment_name = "VizBriz Sleep Apnea Assessment"
        
        # VizBriz logo - hardcoded absolute URL
        vizbriz_logo_url = "https://app.vizbriz.com/flask_static/images/logos/vizbrizz_logo%20color%20without%20grad.png"
        
        # Build logo HTML.
        # For Hebrew patient emails: remove partner/DSO logo and center VizBriz logo.
        if language == 'he':
            logos_html = (
                '<div style="text-align: center; margin-bottom: 30px; padding: 20px 0; border-bottom: 1px solid #eee;">'
                f'<img src="{vizbriz_logo_url}" alt="VizBriz" style="max-height: 140px; max-width: 420px; object-fit: contain; display: block; margin: 0 auto;">'
                '</div>'
            )
        else:
            # Default: DSO logo (if available) + VizBriz logo
            logos_html = '<div style="text-align: center; margin-bottom: 30px; padding: 20px 0; border-bottom: 1px solid #eee;">'
            
            # Add DSO logo if available
            if dso_logo_url:
                # Ensure DSO logo URL is absolute
                if not dso_logo_url.startswith(('http://', 'https://')):
                    # Handle various path formats stored in database
                    # Could be: "logos/file.jpg", "images/logos/file.jpg", "/flask_static/images/logos/file.jpg", etc.
                    clean_logo = dso_logo_url.replace('\\', '/').lstrip('/')
                    # Remove flask_static prefix if present
                    clean_logo = clean_logo.replace('flask_static/', '')
                    # Ensure images/logos/ prefix is present
                    if not clean_logo.startswith('images/'):
                        if clean_logo.startswith('logos/'):
                            clean_logo = f"images/{clean_logo}"
                        else:
                            clean_logo = f"images/logos/{clean_logo}"
                    dso_logo_url = f"https://app.vizbriz.com/flask_static/{clean_logo}"
                current_app.logger.info(f"Final DSO logo URL for email: {dso_logo_url}")
                logos_html += f'<img src="{dso_logo_url}" alt="{dso_name or "Partner"}" style="max-height: 140px; margin: 0 20px; object-fit: contain; vertical-align: middle;">'
            
            # Always add VizBriz logo
            logos_html += f'<img src="{vizbriz_logo_url}" alt="VizBriz" style="max-height: 140px; margin: 0 20px; object-fit: contain; vertical-align: middle;">'
            logos_html += '</div>'
        
        # Build email subject and content based on language
        if language == 'he':
            email_subject = "תוצאות ההערכה שלך לדום נשימה בשינה מוכנות כעת"
            display_name_he = display_name if display_name != "Valued Patient" else "מטופל יקר"
            
            html_content = f"""
        <!DOCTYPE html>
        <html dir="rtl" lang="he">
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: "Assistant", "Heebo", Arial, sans-serif; line-height: 1.8; color: #333; background-color: #f9f9f9; direction: rtl; text-align: right; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 30px; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); direction: rtl; text-align: right; }}
                .container p, .container div {{ text-align: right; direction: rtl; }}
                .signature {{ margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; text-align: right; direction: rtl; }}
                .ps-note {{ font-size: 13px; color: #888; margin-top: 25px; padding: 15px; background-color: #f5f5f5; border-radius: 5px; font-style: italic; text-align: right; direction: rtl; }}
            </style>
        </head>
        <body>
            <div class="container">
                {logos_html}
                
                <p style="font-size: 16px;">שלום {display_name_he},</p>

                <p style="font-size: 16px;">התוצאות שלך מההערכה האחרונה לדום נשימה בשינה של Vizbriz בשיתוף עם המרפאה שלך מוכנות כעת.</p>

                <p style="font-size: 16px;"><strong>אנא מצא את הדוח מצורף לאימייל זה.</strong></p>

                <p style="font-size: 16px;">VizBriz היא פלטפורמה מבוססת בינה מלאכותית, הפועלת בתחום הפרעות הנשימה בשינה. היא מסייעת להבין טוב יותר מה עשוי להשפיע על הנשימה ואיכות השינה שלך, ולתמוך בהתאמה אישית של הגישה הטיפולית בשיתוף עם הצוות המטפל.</p>

                <p style="font-size: 16px;">אנו מעודדים אותך לעיין במסמך בקפידה. הבנת הממצאים הללו ועמידה בצעדים המומלצים הבאים היא חלק חשוב בתמיכה בבריאות שלך ובהשגת התוצאות הטובות ביותר האפשריות.</p>

                <p style="font-size: 16px;">אם יש לך שאלות כלשהן לגבי הדוח, הממצאים שלו, או מה הלאה, אנא אל תהסס ליצור קשר עם Vizbriz ישירות באמייל - <strong>info@vizbriz.com</strong>. צוות הטיפול שלנו ישמח לסייע לך.</p>
                
                <div class="signature">
                    <p style="font-size: 16px; margin-bottom: 5px;">בברכה,</p>
                    <p style="font-size: 16px; margin-bottom: 3px; color: #333;">צוות הטיפול של Vizbriz</p>
                </div>
                
            </div>
        </body>
        </html>
        """
            
            text_content = f"""תוצאות ההערכה שלך לדום נשימה בשינה מוכנות כעת

שלום {display_name_he},

התוצאות שלך מההערכה האחרונה לדום נשימה בשינה של Vizbriz בשיתוף עם המרפאה שלך מוכנות כעת.

אנא מצא את הדוח מצורף לאימייל זה.

VizBriz היא פלטפורמה מבוססת בינה מלאכותית, הפועלת בתחום הפרעות הנשימה בשינה. היא מסייעת להבין טוב יותר מה עשוי להשפיע על הנשימה ואיכות השינה שלך, ולתמוך בהתאמה אישית של הגישה הטיפולית בשיתוף עם הצוות המטפל.

אנו מעודדים אותך לעיין במסמך בקפידה. הבנת הממצאים הללו ועמידה בצעדים המומלצים הבאים היא חלק חשוב בתמיכה בבריאות שלך ובהשגת התוצאות הטובות ביותר האפשריות.

אם יש לך שאלות כלשהן לגבי הדוח, הממצאים שלו, או מה הלאה, אנא אל תהסס ליצור קשר עם Vizbriz ישירות באמייל - info@vizbriz.com. צוות הטיפול שלנו ישמח לסייע לך.

בברכה,

צוות הטיפול של Vizbriz
        """
        else:
            # English (default)
            email_subject = f"Your {organization_name} & Vizbriz Assessment Results – Important Next Steps"
            
            html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.8; color: #333; background-color: #f9f9f9; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 30px; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                .signature {{ margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; }}
                .ps-note {{ font-size: 13px; color: #888; margin-top: 25px; padding: 15px; background-color: #f5f5f5; border-radius: 5px; font-style: italic; }}
            </style>
        </head>
        <body>
            <div class="container">
                {logos_html}
                
                <p style="font-size: 16px;">Dear {display_name},</p>
                
                <p style="font-size: 16px;">We hope this message finds you well.</p>
                
                <p style="font-size: 16px;">Your results from the recent <strong>{assessment_name}</strong>, completed as part of your care with <strong>{organization_name}</strong> in collaboration with Vizbriz, are now ready.</p>
                
                <p style="font-size: 16px;"><strong>Please find your report attached to this email.</strong></p>
                
                <p style="font-size: 16px;">We encourage you to review the document carefully. Understanding these findings and following the recommended next steps is an important part of supporting your health and achieving the best possible outcomes.</p>
                
                <p style="font-size: 16px;">If you have any questions about the report, its findings, or what comes next, please feel free to contact {organization_name} directly at <strong>{phone_number}</strong> during business hours. Your care team will be happy to assist you.</p>
                
                <div class="signature">
                    <p style="font-size: 16px; margin-bottom: 5px;">Warm regards,</p>
                    <p style="font-size: 16px; margin-bottom: 3px; color: #333;">The {organization_name} Care Team</p>
                    <p style="font-size: 16px; margin-top: 0; color: #333;">in collaboration with Vizbriz</p>
                </div>
                
                <p class="ps-note">P.S. Please save this email and the attached report in a secure location for your personal records.</p>
            </div>
        </body>
        </html>
        """
            
            text_content = f"""Your {organization_name} & Vizbriz Assessment Results – Important Next Steps

Dear {display_name},

We hope this message finds you well.

Your results from the recent {assessment_name}, completed as part of your care with {organization_name} in collaboration with Vizbriz, are now ready.

Please find your report attached to this email.

We encourage you to review the document carefully. Understanding these findings and following the recommended next steps is an important part of supporting your health and achieving the best possible outcomes.

If you have any questions about the report, its findings, or what comes next, please feel free to contact {organization_name} directly at {phone_number} during business hours. Your care team will be happy to assist you.

Warm regards,

The {organization_name} Care Team
in collaboration with Vizbriz

P.S. Please save this email and the attached report in a secure location for your personal records.
        """
        
        # Create message with attachment
        # Set charset to UTF-8 for proper Hebrew encoding
        msg = Message(
            subject=email_subject,
            sender=sender_email,
            recipients=[patient_email],
            charset='utf-8'
        )
        msg.body = text_content
        msg.html = html_content
        
        # Attach PDF
        msg.attach(
            filename=pdf_filename,
            content_type='application/pdf',
            data=pdf_content
        )
        
        # Send email
        current_app.logger.info(f"=== FLASK-MAIL SEND ATTEMPT ===")
        current_app.logger.info(f"Mail config - MAIL_SERVER: {current_app.config.get('MAIL_SERVER', 'NOT SET')}")
        current_app.logger.info(f"Mail config - MAIL_PORT: {current_app.config.get('MAIL_PORT', 'NOT SET')}")
        current_app.logger.info(f"Mail config - MAIL_USE_TLS: {current_app.config.get('MAIL_USE_TLS', 'NOT SET')}")
        current_app.logger.info(f"Mail config - MAIL_USERNAME: {current_app.config.get('MAIL_USERNAME', 'NOT SET')}")
        current_app.logger.info(f"Sender: {sender_email}, Recipient: {patient_email}")
        current_app.logger.info(f"Message subject: {msg.subject}")
        current_app.logger.info(f"Message has HTML: {bool(msg.html)}, has body: {bool(msg.body)}")
        current_app.logger.info(f"Message attachments count: {len(msg.attachments)}")
        if msg.attachments:
            for i, att in enumerate(msg.attachments):
                current_app.logger.info(f"  Attachment {i+1}: {att.filename}, content_type: {att.content_type}, size: {len(att.data) if hasattr(att, 'data') else 'unknown'}")
        
        try:
            mail.send(msg)
            current_app.logger.info(f"✅ Patient email with PDF sent successfully to {patient_email} via Flask-Mail")
        except Exception as send_error:
            current_app.logger.error(f"❌ FLASK-MAIL SEND FAILED ===")
            current_app.logger.error(f"Failed to send patient email via Flask-Mail: {str(send_error)}")
            current_app.logger.error(f"Error type: {type(send_error).__name__}")
            current_app.logger.error(f"Error details: {repr(send_error)}", exc_info=True)
            raise  # Re-raise to be caught by outer exception handler
        
        # Log to database
        try:
            from flask_app.models import EmailLog
            email_log = EmailLog(
                patient_id=patient_id,
                sender_id=None,
                sender_type='system',
                sender_email=sender_email,
                recipient_email=patient_email,
                subject=email_subject,
                message_content=html_content,
                email_type='patient_report',
                status='sent'
            )
            db.session.add(email_log)
            db.session.commit()
            current_app.logger.info(f"Patient email logged to database with ID: {email_log.id}")
        except Exception as log_error:
            current_app.logger.error(f"Failed to log patient email to database: {str(log_error)}", exc_info=True)
            db.session.rollback()
            # Don't fail the function if logging fails - email was already sent
        
        return True
        
    except Exception as e:
        current_app.logger.error(f"Failed to send patient email with PDF: {str(e)}", exc_info=True)
        return False


def translate_text_to_english(text, source_language='auto'):
    """
    Translate text from Hebrew/Russian to English using Bedrock LLM.
    
    Args:
        text: Text to translate
        source_language: Source language code ('he', 'ru', 'auto' for auto-detect)
    
    Returns:
        Translated text in English, or original text if translation fails
    """
    if not text or not isinstance(text, str):
        return text
    
    # Skip translation if text is already English or very short
    # Check if text contains mostly ASCII characters (likely English)
    ascii_ratio = sum(1 for c in text if ord(c) < 128) / len(text) if text else 0
    if ascii_ratio > 0.9 or len(text.strip()) < 3:
        return text
    
    try:
        from flask_app.services.bedrock_service import BedrockService
        
        bedrock = BedrockService()
        if not bedrock.is_available():
            current_app.logger.warning("Bedrock service not available for translation, returning original text")
            return text
        
        # Determine source language for prompt
        if source_language == 'auto':
            # Detect language based on characters
            if any('\u0590' <= c <= '\u05FF' for c in text):  # Hebrew range
                source_language = 'Hebrew'
            elif any('\u0400' <= c <= '\u04FF' for c in text):  # Cyrillic range
                source_language = 'Russian'
            else:
                # Likely already English or other language
                return text
        elif source_language == 'he':
            source_language = 'Hebrew'
        elif source_language == 'ru':
            source_language = 'Russian'
        
        # Create translation prompt
        messages = [
            {
                'role': 'user',
                'content': f"""Translate the following text from {source_language} to English. 
Provide ONLY the English translation, nothing else. Do not add any explanations or comments.

Text to translate:
{text}

Translation:"""
            }
        ]
        
        # Call Bedrock for translation
        result = bedrock.invoke_model(
            messages=messages,
            model='claude_35_sonnet_v2',  # Use faster model for translation
            max_tokens=500,
            temperature=0.1,
            endpoint='translate_quiz_text'
        )
        
        if result.get('success') and result.get('response'):
            translated = result['response'].strip()
            current_app.logger.info(f"Translated text ({len(text)} chars) from {source_language} to English")
            return translated
        else:
            current_app.logger.warning(f"Translation failed: {result.get('error', 'Unknown error')}, returning original")
            return text
            
    except Exception as e:
        current_app.logger.error(f"Error translating text: {str(e)}, returning original")
        return text

def batch_translate_texts(texts_to_translate, source_language='auto'):
    """
    Batch translate multiple texts together in a single API call for efficiency.
    
    Args:
        texts_to_translate: List of text strings to translate
        source_language: Source language code ('he', 'ru', 'auto' for auto-detect)
    
    Returns:
        Dictionary mapping original text to translated text
    """
    if not texts_to_translate:
        return {}
    
    # Filter out texts that don't need translation
    texts_needing_translation = []
    for text in texts_to_translate:
        if text and isinstance(text, str):
            ascii_ratio = sum(1 for c in text if ord(c) < 128) / len(text) if text else 0
            if ascii_ratio <= 0.9 and len(text.strip()) >= 3:
                texts_needing_translation.append(text)
    
    if not texts_needing_translation:
        return {text: text for text in texts_to_translate}
    
    try:
        from flask_app.services.bedrock_service import BedrockService
        
        bedrock = BedrockService()
        if not bedrock.is_available():
            current_app.logger.warning("Bedrock service not available for batch translation, returning originals")
            return {text: text for text in texts_to_translate}
        
        # Determine source language
        if source_language == 'auto':
            # Check first text to determine language
            first_text = texts_needing_translation[0]
            if any('\u0590' <= c <= '\u05FF' for c in first_text):
                source_language = 'Hebrew'
            elif any('\u0400' <= c <= '\u04FF' for c in first_text):
                source_language = 'Russian'
            else:
                return {text: text for text in texts_to_translate}
        elif source_language == 'he':
            source_language = 'Hebrew'
        elif source_language == 'ru':
            source_language = 'Russian'
        
        # Create batch translation prompt
        texts_list = '\n'.join([f"{i+1}. {text}" for i, text in enumerate(texts_needing_translation)])
        
        messages = [
            {
                'role': 'user',
                'content': f"""Translate the following {len(texts_needing_translation)} texts from {source_language} to English. 
Provide ONLY the English translations, one per line, in the same order. 
Do not add any explanations, comments, or numbering - just the translations.

Texts to translate:
{texts_list}

Translations (one per line, same order):"""
            }
        ]
        
        # Call Bedrock for batch translation
        # Use a timeout to prevent hanging - if translation takes too long, return originals
        try:
            result = bedrock.invoke_model(
                messages=messages,
                model='claude_35_sonnet_v2',
                max_tokens=min(2000, len(texts_needing_translation) * 100),  # Scale tokens with batch size
                temperature=0.1,
                endpoint='batch_translate_quiz_texts'
            )
        except Exception as bedrock_error:
            current_app.logger.error(f"Bedrock API error during batch translation: {str(bedrock_error)}")
            # Return originals if Bedrock fails
            return {text: text for text in texts_to_translate}
        
        if result.get('success') and result.get('response'):
            translated_response = result['response'].strip()
            # Parse the response - split by newlines
            translated_lines = [line.strip() for line in translated_response.split('\n') if line.strip()]
            
            # Create mapping
            translation_map = {}
            for i, original in enumerate(texts_needing_translation):
                if i < len(translated_lines):
                    # Remove any numbering that might have been added (e.g., "1. Translation")
                    translated = translated_lines[i].lstrip('0123456789. ').strip()
                    translation_map[original] = translated
                else:
                    # Fallback to original if translation is missing
                    translation_map[original] = original
            
            # Add texts that didn't need translation
            for text in texts_to_translate:
                if text not in translation_map:
                    translation_map[text] = text
            
            current_app.logger.info(f"Batch translated {len(texts_needing_translation)} texts from {source_language} to English")
            return translation_map
        else:
            current_app.logger.warning(f"Batch translation failed: {result.get('error', 'Unknown error')}, returning originals")
            return {text: text for text in texts_to_translate}
            
    except Exception as e:
        current_app.logger.error(f"Error in batch translation: {str(e)}, returning originals")
        return {text: text for text in texts_to_translate}

def translate_enhanced_answers(enhanced_answers, source_language='auto'):
    """
    Translate Unicode text (Hebrew, Russian, etc.) to English using Bedrock LLM.
    This ensures all stored text is in English, which will render correctly in PDFs.
    Uses batch translation for efficiency - collects all texts and translates them in one API call.
    
    Args:
        enhanced_answers: The enhanced answers dictionary to translate
        source_language: Source language code ('he', 'ru', 'auto' for auto-detect)
    """
    if not enhanced_answers:
        return enhanced_answers
    
    # First pass: collect all texts that need translation
    texts_to_translate = []
    text_positions = []  # Track where each text came from for reconstruction
    
    def collect_texts(obj, path=[]):
        """Recursively collect all texts that need translation"""
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in ['timestamp', 'language', 'total_questions_answered', 'question_id', 'question_type', 'section']:
                    continue  # Skip metadata
                if key == 'question_text':
                    continue  # Already English
                if isinstance(value, str) and value:
                    ascii_ratio = sum(1 for c in value if ord(c) < 128) / len(value) if value else 0
                    if ascii_ratio <= 0.9 and len(value.strip()) >= 3:
                        texts_to_translate.append(value)
                        text_positions.append(path + [key])
                elif isinstance(value, list):
                    for i, item in enumerate(value):
                        if isinstance(item, str) and item:
                            ascii_ratio = sum(1 for c in item if ord(c) < 128) / len(item) if item else 0
                            if ascii_ratio <= 0.9 and len(item.strip()) >= 3:
                                texts_to_translate.append(item)
                                text_positions.append(path + [key, i])
                        else:
                            collect_texts(item, path + [key, i])
                else:
                    collect_texts(value, path + [key])
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                collect_texts(item, path + [i])
        elif isinstance(obj, str) and obj:
            ascii_ratio = sum(1 for c in obj if ord(c) < 128) / len(obj) if obj else 0
            if ascii_ratio <= 0.9 and len(obj.strip()) >= 3:
                texts_to_translate.append(obj)
                text_positions.append(path)
    
    # Collect all texts
    collect_texts(enhanced_answers)
    
    # Batch translate all texts at once
    # Limit batch size to prevent timeouts (max 20 texts per batch)
    if texts_to_translate:
        if len(texts_to_translate) > 20:
            current_app.logger.warning(f"Large batch of {len(texts_to_translate)} texts, splitting into chunks")
            # Split into chunks of 20
            translation_map = {}
            for i in range(0, len(texts_to_translate), 20):
                chunk = texts_to_translate[i:i+20]
                chunk_map = batch_translate_texts(chunk, source_language)
                translation_map.update(chunk_map)
        else:
            translation_map = batch_translate_texts(texts_to_translate, source_language)
    else:
        translation_map = {}
        current_app.logger.info("No texts found that need translation")
    
    # Second pass: apply translations
    def apply_translations(obj, path=[]):
        """Recursively apply translations to the structure"""
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                if key in ['timestamp', 'language', 'total_questions_answered']:
                    result[key] = value
                elif isinstance(value, str) and value:
                    if key == 'question_text':
                        result[key] = value  # Already English
                    else:
                        # Check if this text needs translation
                        if value in translation_map:
                            result[key] = translation_map[value]
                        else:
                            result[key] = value
                elif isinstance(value, list):
                    result[key] = [apply_translations(item, path + [key, i]) if isinstance(item, (dict, list)) else 
                                  (translation_map.get(item, item) if isinstance(item, str) and item in translation_map else item)
                                  for i, item in enumerate(value)]
                else:
                    result[key] = apply_translations(value, path + [key]) if isinstance(value, (dict, list)) else value
            return result
        elif isinstance(obj, list):
            return [apply_translations(item, path + [i]) if isinstance(item, (dict, list)) else 
                   (translation_map.get(item, item) if isinstance(item, str) and item in translation_map else item)
                   for i, item in enumerate(obj)]
        elif isinstance(obj, str) and obj in translation_map:
            return translation_map[obj]
        else:
            return obj
    
    # Apply translations
    return apply_translations(enhanced_answers)

def generate_personalized_risk_message(evaluation_result, patient_email, patient_name, language='en'):
    """
    Generate a personalized risk message using Bedrock LLM based on the assessment results.
    Creates dynamic, contextual messages tailored to the specific patient's situation.
    """
    try:
        from flask_app.services.bedrock_service import BedrockService
        
        risk_band = evaluation_result.get('risk_band', 'low')
        total_score = evaluation_result.get('total_score', 0)
        red_flags = evaluation_result.get('red_flags', [])
        
        # Risk level mapping for VizBriz quiz
        risk_level_mapping = {
            'low': 'Low',
            'mild': 'Low', 
            'moderate': 'Moderate',
            'high': 'High',
            'diagnosed_not_treated_not_symptomatic': 'Moderate',
            'diagnosed_not_treated_symptomatic': 'High',
            'diagnosed_treated_stable': 'Moderate',
            'diagnosed_treated_symptomatic': 'High'
        }
        
        mapped_risk = risk_level_mapping.get(risk_band, 'Low')
        
        # Create context for the LLM
        red_flags_text = ', '.join(red_flags) if red_flags else 'None identified'
        
        # Determine patient status
        if 'diagnosed' in risk_band:
            if 'not_treated_not_symptomatic' in risk_band:
                patient_status = "diagnosed with sleep apnea but not currently receiving treatment and not experiencing symptoms"
            elif 'not_treated_symptomatic' in risk_band:
                patient_status = "diagnosed with sleep apnea but not currently receiving treatment and experiencing symptoms"
            elif 'stable' in risk_band:
                patient_status = "diagnosed with sleep apnea and currently receiving treatment with stable results"
            else:  # treated_symptomatic
                patient_status = "diagnosed with sleep apnea and currently receiving treatment but still experiencing symptoms"
        else:
            patient_status = "not previously diagnosed with sleep apnea"
        
        # Determine language instructions
        language_instructions = ""
        if language == 'he':
            language_instructions = "IMPORTANT: Write the entire message in Hebrew. Use proper Hebrew grammar and medical terminology. The message should be culturally appropriate for Hebrew speakers."
        elif language == 'ru':
            language_instructions = "IMPORTANT: Write the entire message in Russian. Use proper Russian grammar and medical terminology. The message should be culturally appropriate for Russian speakers."
        else:
            language_instructions = "Write the message in English."

        # Create the prompt for Bedrock - request both English and target language
        # Only include name in prompt if it's a real name (not default/placeholder)
        use_real_name = patient_name and patient_name != 'Patient' and patient_name.lower() not in ['test patient', 'patient']
        prompt_patient_name = patient_name if use_real_name else 'Patient'
        
        # Build paragraph 1 instruction based on whether we have a real name
        if use_real_name:
            paragraph1_instruction = "Address them by name using the exact name provided in PATIENT DATA."
        else:
            paragraph1_instruction = "Do not use a personal name - use a generic greeting like 'Thank you for completing your sleep health assessment' or similar."
        
        if language == 'en':
            # For English, just generate normally
            prompt = f"""You are a compassionate sleep health specialist writing a personalized follow-up message to a patient about their sleep apnea assessment results.

PATIENT DATA:
- Name: {prompt_patient_name}
- Diagnosis Status: {patient_status}
- Risk Level: {mapped_risk}
- Red Flags: {red_flags_text}
- Assessment Summary: Risk level {mapped_risk.lower()}

OBJECTIVE:
Write a clear, friendly message (3–4 paragraphs) that:
1. {paragraph1_instruction}
2. Clearly explains their results and risk in **plain, reassuring language** (avoid medical jargon).
3. Acknowledges their personal situation (e.g., previously diagnosed, not treated).
4. Provides **empathy + motivation** (focus on achievable improvement).
5. Ends with a **specific call to action** that offers two options: (1) consultation with OSA experts to discuss their results, or (2) scheduling a sleep test to provide more accurate diagnosis.
6. Includes **two reputable educational links** at the end.

STRUCTURE:
- **Paragraph 1 (Greeting + brief thank you)**: Thank them for completing the assessment. {paragraph1_instruction}
- **Paragraph 2 (Result summary)**: Explain their risk level and what it means in plain language. Briefly mention red flags if relevant, but in a reassuring way.
- **Paragraph 3 (Motivation and reassurance)**: Provide encouragement based on their status:
   - If undiagnosed: emphasize that assessment is a positive first step and that treatment can significantly improve quality of life
   - If diagnosed but untreated: acknowledge their situation and encourage them that now is a great time to take action
   - If in treatment but symptomatic: acknowledge their commitment and suggest that treatment adjustments may help
   - If stable: acknowledge their success and encourage continued follow-up
- **Paragraph 4 (Call to action)**: End with a friendly offer of two options. Do NOT mention phone numbers or websites - there are action buttons at the end of the page they can use. Offer: (1) consultation with one of our OSA experts to discuss their results and explore treatment options, or (2) scheduling a sleep test to provide us with more accurate diagnosis. Example: "To help you take the next step, I'd like to offer you two options: you can schedule a consultation with one of our OSA experts to discuss your results and explore personalized treatment options, or you can schedule a sleep test to provide us with a more accurate diagnosis. Both options will help us create a treatment plan tailored to your needs. You can use the consultation request options below to get started."

STYLE REQUIREMENTS:
- Warm and encouraging, not alarming.
- Short paragraphs (2–3 sentences each).
- Use second-person voice ("you," "your").
- No generic phrases like "I want to emphasize"; prefer "Now is a great time to…" or "The good news is…".
- Maintain readability at a 7th–8th grade level.
- Use HTML line breaks (<br><br>) between paragraphs.
- Avoid medical jargon or negative framing.
- DO NOT mention any scores or numbers.

EDUCATIONAL LINKS:
At the end of the message, include two reputable educational links in HTML format:
- Understanding Sleep Apnea from American Academy of Sleep Medicine: <a href="https://sleepeducation.org/sleep-disorders/obstructive-sleep-apnea/" target="_blank">Understanding Sleep Apnea (AASM)</a>
- Treatment Options from Mayo Clinic: <a href="https://www.mayoclinic.org/diseases-conditions/sleep-apnea/diagnosis-treatment/drc-20377636" target="_blank">Treatment Options (Mayo Clinic)</a>

TONE:
Professional, warm, and encouraging.
Focus on empathy, motivation, and actionable next steps.
Avoid alarmist phrasing or excessive medical terminology.

OUTPUT FORMAT:
Start with: <h4 style="color: #2c3e50; margin-bottom: 15px;">📋 Your Personalized Sleep Health Assessment</h4>
Then provide the message paragraphs with <br><br> between paragraphs.

Please generate the personalized message now:"""
        else:
            # For non-English languages, request both English and target language
            language_name = "Hebrew" if language == 'he' else "Russian"
            prompt = f"""You are a compassionate sleep health specialist writing a personalized follow-up message to a patient about their sleep apnea assessment results.

PATIENT DATA:
- Name: {prompt_patient_name}
- Diagnosis Status: {patient_status}
- Risk Level: {mapped_risk}
- Red Flags: {red_flags_text}
- Assessment Summary: Risk level {mapped_risk.lower()}

INSTRUCTIONS:
Please provide TWO versions of the same message:

1. **ENGLISH VERSION** (for database storage and consistency)
2. **{language_name.upper()} VERSION** (for patient display)

Each version should be a clear, friendly message (3–4 paragraphs) that:
1. {paragraph1_instruction}
2. Clearly explains their results and risk in **plain, reassuring language** (avoid medical jargon).
3. Acknowledges their personal situation (e.g., previously diagnosed, not treated).
4. Provides **empathy + motivation** (focus on achievable improvement).
5. Ends with a **specific call to action** that offers two options: (1) consultation with OSA experts to discuss their results, or (2) scheduling a sleep test to provide more accurate diagnosis.
6. Includes **two reputable educational links** at the end.

STRUCTURE:
- **Paragraph 1 (Greeting + brief thank you)**: Thank them for completing the assessment. {paragraph1_instruction}
- **Paragraph 2 (Result summary)**: Explain their risk level and what it means in plain language. Briefly mention red flags if relevant, but in a reassuring way.
- **Paragraph 3 (Motivation and reassurance)**: Provide encouragement based on their status:
   - If undiagnosed: emphasize that assessment is a positive first step and that treatment can significantly improve quality of life
   - If diagnosed but untreated: acknowledge their situation and encourage them that now is a great time to take action
   - If in treatment but symptomatic: acknowledge their commitment and suggest that treatment adjustments may help
   - If stable: acknowledge their success and encourage continued follow-up
- **Paragraph 4 (Call to action)**: End with a friendly offer of two options. Do NOT mention phone numbers or websites - there are action buttons at the end of the page they can use. Offer: (1) consultation with one of our OSA experts to discuss their results and explore treatment options, or (2) scheduling a sleep test to provide us with more accurate diagnosis. Example: "To help you take the next step, I'd like to offer you two options: you can schedule a consultation with one of our OSA experts to discuss your results and explore personalized treatment options, or you can schedule a sleep test to provide us with a more accurate diagnosis. Both options will help us create a treatment plan tailored to your needs. You can use the consultation request options below to get started."

STYLE REQUIREMENTS:
- Warm and encouraging, not alarming.
- Short paragraphs (2–3 sentences each).
- Use second-person voice ("you," "your").
- No generic phrases like "I want to emphasize"; prefer "Now is a great time to…" or "The good news is…".
- Maintain readability at a 7th–8th grade level.
- Use HTML line breaks (<br><br>) between paragraphs.
- Avoid medical jargon or negative framing.
- DO NOT mention any scores or numbers.

EDUCATIONAL LINKS:
At the end of each message version, include two reputable educational links in HTML format:
- Understanding Sleep Apnea from American Academy of Sleep Medicine: <a href="https://sleepeducation.org/sleep-disorders/obstructive-sleep-apnea/" target="_blank">Understanding Sleep Apnea (AASM)</a>
- Treatment Options from Mayo Clinic: <a href="https://www.mayoclinic.org/diseases-conditions/sleep-apnea/diagnosis-treatment/drc-20377636" target="_blank">Treatment Options (Mayo Clinic)</a>

TONE:
Professional, warm, and encouraging.
Focus on empathy, motivation, and actionable next steps.
Avoid alarmist phrasing or excessive medical terminology.

OUTPUT FORMAT:
For each version, start with: <h4 style="color: #2c3e50; margin-bottom: 15px;">📋 Your Personalized Sleep Health Assessment</h4>
Then provide the message paragraphs with <br><br> between paragraphs.

FORMAT YOUR RESPONSE AS:
**ENGLISH VERSION:**
<h4 style="color: #2c3e50; margin-bottom: 15px;">📋 Your Personalized Sleep Health Assessment</h4>
[English message here with paragraphs separated by <br><br>]

**{language_name.upper()} VERSION:**
<h4 style="color: #2c3e50; margin-bottom: 15px;">📋 Your Personalized Sleep Health Assessment</h4>
[{language_name} message here with paragraphs separated by <br><br>]

Please generate both versions now:"""

        # Initialize Bedrock service
        bedrock_service = BedrockService()
        
        # Prepare messages for Bedrock (Claude format)
        messages = [
            {
                "role": "user",
                "content": prompt
            }
        ]
        
        # Get patient ID from email (if possible)
        patient_id = None
        try:
            from flask_app.models import Patient
            patient = Patient.query.filter_by(email=patient_email).first()
            if patient:
                patient_id = patient.id
        except Exception:
            pass  # Continue without patient_id if lookup fails
        
        # Generate the message using Bedrock
        response = bedrock_service.invoke_model(
            messages=messages,
            model="claude_35_sonnet_v2",  # Use Claude 3.5 Sonnet v2
            max_tokens=800,  # Increased for 3-4 paragraph messages with title and links
            temperature=0.7,
            patient_id=patient_id,
            endpoint="vizbriz_quiz"
        )
        
        if response and response.get('success'):
            full_response = response.get('response', '').strip()
            
            if language == 'en':
                # For English, return the response directly
                message = full_response
            else:
                # For non-English, parse both versions
                language_name = "Hebrew" if language == 'he' else "Russian"
                
                # Try to extract both versions from the response
                english_version = None
                translated_version = None
                
                # Look for the structured response format
                if f"**{language_name.upper()} VERSION:**" in full_response:
                    parts = full_response.split(f"**{language_name.upper()} VERSION:**")
                    if len(parts) >= 2:
                        # Extract English version (before the translated version)
                        english_part = parts[0].replace("**ENGLISH VERSION:**", "").strip()
                        english_version = english_part
                        
                        # Extract translated version (after the translated version marker)
                        translated_part = parts[1].strip()
                        translated_version = translated_part
                else:
                    # Fallback: if structured format not found, treat entire response as translated
                    translated_version = full_response
                    # Generate a simple English fallback
                    english_version = f"Assessment completed. Risk level: {mapped_risk}. Please consult with our dental sleep team for personalized treatment options."
                
                # Use the translated version for display
                message = translated_version or full_response
                
                # Log both versions to database for multilingual responses
                if english_version and language != 'en':
                    try:
                        from flask_app.services.llm_logger_service import LLMLoggerService
                        import uuid
                        
                        # Generate a new session ID for the English version logging
                        english_session_id = str(uuid.uuid4())
                        
                        # Log the English version separately
                        LLMLoggerService.log_prompt(
                            session_id=english_session_id,
                            model_name="claude_35_sonnet_v2",
                            model_id="us.anthropic.claude-3-5-sonnet-20241022-v2:0",
                            prompt_content=messages,
                            patient_id=patient_id,
                            page_endpoint="vizbriz_quiz_english",
                            max_tokens=800,
                            temperature=0.7,
                            top_p=0.9
                        )
                        
                        # Log the English response
                        LLMLoggerService.log_response(
                            session_id=english_session_id,
                            response_text=english_version,
                            status='success',
                            response_time_ms=response.get('response_time_ms', 0),
                            response_data={'language': 'english', 'original_language': language}
                        )
                        
                        current_app.logger.info(f"Logged English version for {language} response, session: {english_session_id}")
                        
                    except Exception as log_error:
                        current_app.logger.warning(f"Failed to log English version: {log_error}")
                
                # Store the English version in the response data for reference
                if english_version:
                    response['english_version'] = english_version
                    response['translated_version'] = translated_version or full_response
            
            # Ensure HTML line breaks are properly formatted
            message = message.replace('\n\n', '<br><br>').replace('\n', '<br>')
            return message
        else:
            # Fallback to template-based message if LLM fails
            current_app.logger.warning(f"Bedrock LLM failed to generate message, using fallback template")
            return generate_fallback_message(evaluation_result, patient_name)
            
    except Exception as e:
        current_app.logger.error(f"Error generating personalized message with Bedrock: {str(e)}")
        # Fallback to template-based message
        return generate_fallback_message(evaluation_result, patient_name)

def generate_fallback_message(evaluation_result, patient_name):
    """
    Fallback template-based message generation if LLM fails
    """
    risk_band = evaluation_result.get('risk_band', 'low')
    total_score = evaluation_result.get('total_score', 0)
    red_flags = evaluation_result.get('red_flags', [])
    
    # Risk level mapping for VizBriz quiz
    risk_level_mapping = {
        'low': 'Low',
        'mild': 'Low', 
        'moderate': 'Moderate',
        'high': 'High',
        'diagnosed_not_treated_not_symptomatic': 'Moderate',
        'diagnosed_not_treated_symptomatic': 'High',
        'diagnosed_treated_stable': 'Moderate',
        'diagnosed_treated_symptomatic': 'High'
    }
    
    mapped_risk = risk_level_mapping.get(risk_band, 'Low')
    
    # Personalized messages based on risk level
    if mapped_risk == 'Low':
        if 'diagnosed' in risk_band:
            return f"""Great news, {patient_name}! Your assessment shows that your sleep apnea treatment is working well and you're maintaining stable, healthy sleep patterns.<br><br>
            This is excellent progress! Continue following your treatment plan and regular check-ups with your dental sleep specialist.<br><br>
            If you have any concerns about your sleep or treatment, don't hesitate to reach out to our team for support."""
        else:
            return f"""Good news, {patient_name}! Your assessment suggests a low risk for obstructive sleep apnea (OSA).<br><br>
            That's reassuring — but it doesn't completely rule out the possibility of a sleep-related issue. Continue monitoring for signs like snoring, fatigue, or disturbed sleep patterns.<br><br>
            If you're experiencing any symptoms or want peace of mind, consider scheduling a consultation with our dental sleep team or a simple home sleep test."""
    
    elif mapped_risk == 'Moderate':
        if 'diagnosed_not_treated_not_symptomatic' in risk_band:
            return f"""Your assessment shows that you've been diagnosed with sleep apnea but are not currently receiving treatment and are not experiencing symptoms, {patient_name}.<br><br>
            While you may not be experiencing symptoms right now, it's important to monitor your condition and consider treatment options to prevent future complications.<br><br>
            We recommend scheduling a consultation with our dental sleep specialists to discuss your results and explore preventive treatment options.<br><br>
            Early intervention can help maintain your current good status and prevent symptoms from developing."""
        else:
            return f"""Your assessment indicates a moderate risk for sleep apnea, {patient_name}.<br><br>
            Even mild forms of OSA can affect your focus, energy, and long-term health — and symptoms often worsen if left untreated.<br><br>
            The good news is that this condition can often be managed effectively with early detection and proper treatment.<br><br>
            We strongly recommend scheduling a consultation with our dental sleep specialists to discuss your results and explore the best treatment options for you."""
    
    else:  # High risk
        if 'diagnosed' in risk_band:
            if 'not_treated_symptomatic' in risk_band:
                return f"""Your assessment shows that you've been diagnosed with sleep apnea but are not currently receiving treatment and are experiencing symptoms, {patient_name}.<br><br>
                This is concerning because untreated sleep apnea can significantly impact your health, energy, and quality of life.<br><br>
                We strongly encourage you to start treatment as soon as possible. Our dental sleep specialists can help you find the most effective treatment approach for your specific needs.<br><br>
                Don't wait — take action now to protect your health and improve your sleep."""
            else:  # diagnosed_treated_symptomatic
                return f"""Your assessment indicates that despite being treated for sleep apnea, you're still experiencing symptoms, {patient_name}.<br><br>
                This suggests that your current treatment may need adjustment or a different approach.<br><br>
                It's important to work with our dental sleep specialists to optimize your treatment plan and ensure you're getting the best possible results.<br><br>
                Let's get you back on track to better sleep and better health."""
        else:
            return f"""Your assessment indicates a high risk for sleep apnea, {patient_name}.<br><br>
            This is important information that shouldn't be ignored. Sleep apnea can significantly impact your health, energy, and quality of life if left untreated.<br><br>
            The good news is that effective treatment options are available, including oral appliance therapy, CPAP, and lifestyle changes.<br><br>
            We strongly recommend scheduling a consultation with our dental sleep specialists immediately to discuss your results and develop a personalized treatment plan.<br><br>
            Don't wait — take action now to protect your health and improve your sleep."""

vizbriz_quiz = Blueprint('vizbriz_quiz', __name__, url_prefix='/vizbriz')


def _resolve_dso_logo_url(dso_info):
    raw_logo = dso_info.logo_url if (hasattr(dso_info, 'logo_url') and dso_info.logo) else None
    if not raw_logo:
        return None
    if raw_logo.startswith(('http://', 'https://')):
        return raw_logo
    if raw_logo.startswith('/'):
        if not raw_logo.startswith(('/flask_static/', '/static/')):
            if raw_logo.startswith('/logos/'):
                raw_logo = '/flask_static/images' + raw_logo
            else:
                raw_logo = '/flask_static/images/' + raw_logo.lstrip('/')
    else:
        raw_logo = '/flask_static/images/' + (raw_logo if raw_logo.startswith('logos/') else raw_logo)
    rel_path = raw_logo.replace('/flask_static/', '').lstrip('/')
    static_dir = current_app.static_folder
    full_path = os.path.join(static_dir, rel_path) if static_dir else None
    if not full_path or not os.path.isfile(full_path):
        current_app.logger.warning(f"DSO logo file not found, skipping: {full_path or raw_logo}")
        return None
    return raw_logo


def _build_quiz_page_context(
    language,
    quiz_package,
    package_filename,
    quiz_mode='assessment',
    clinic_id=None,
    referral_doctor=None,
    dso_id=None,
    dentist_id=None,
):
    """Shared context builder for assessment and follow-up quiz pages."""
    from flask_app.models import DSO, Clinic, Dentist, dentist_clinic_association

    try:
        mtime = os.path.getmtime(os.path.join(_static_folder_path(), package_filename))
        build_ts = datetime.utcfromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S UTC')
    except Exception:
        build_ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')

    dso_info = None
    clinics = []
    clinic_info = None
    dentist_info = None

    try:
        if dso_id:
            dso_info = DSO.query.filter_by(id=dso_id).first()
            clinics = Clinic.query.filter_by(dso_id=dso_id).all()
        else:
            clinics = Clinic.query.all()
    except Exception as db_error:
        current_app.logger.error(f"Database error: {str(db_error)}")
        clinics = []

    clinics_data = [
        {
            'id': c.id,
            'name': c.name,
            'dso_id': c.dso_id,
            'email': c.email,
            'address': c.address,
            'contact_person': c.contact_person,
        }
        for c in clinics
    ]
    if not clinics_data:
        clinics_data = [{
            'id': 0,
            'name': 'No clinics available - Please contact administrator',
            'dso_id': None,
            'email': None,
            'address': None,
            'contact_person': None,
        }]

    if clinic_id:
        clinic_info = Clinic.query.get(clinic_id)
        if clinic_info:
            dentist = (
                Dentist.query
                .join(dentist_clinic_association, Dentist.id == dentist_clinic_association.c.dentist_id)
                .filter(dentist_clinic_association.c.clinic_id == clinic_id)
                .first()
            )
            if dentist:
                dentist_info = dentist

    dso_data = None
    if dso_info:
        dso_data = {
            'id': dso_info.id,
            'name': dso_info.name,
            'email': dso_info.email,
            'contact_person': dso_info.contact_person,
            'logo_url': _resolve_dso_logo_url(dso_info),
        }

    clinic_data = None
    if clinic_info:
        clinic_data = {
            'id': clinic_info.id,
            'name': clinic_info.name,
            'dso_id': clinic_info.dso_id,
            'email': clinic_info.email,
            'address': clinic_info.address,
            'contact_person': clinic_info.contact_person,
        }

    dentist_data = None
    if dentist_info:
        dentist_data = {
            'id': dentist_info.id,
            'name': dentist_info.name,
            'email': dentist_info.email,
        }

    return {
        'quiz_package': quiz_package,
        'language': language,
        'clinic_id': clinic_id,
        'referral_doctor': referral_doctor,
        'dso_id': dso_id,
        'dentist_id': dentist_id,
        'dso_info': dso_data,
        'clinics': clinics_data,
        'clinic_info': clinic_data,
        'dentist_info': dentist_data,
        'is_rtl': language == 'he',
        'quiz_version': (quiz_package.get('metadata') or {}).get('version', 'v?'),
        'build_ts': build_ts,
        'quiz_mode': quiz_mode,
    }


def _parse_quiz_url_params(default_dso_id=27, default_clinic_id=None):
    from flask_app.models import Clinic

    clinic_id = request.args.get('clinic_id') or default_clinic_id
    referral_doctor = request.args.get('referral')
    dso_id = request.args.get('dso_id')
    dentist_id = request.args.get('dentist_id')
    if dentist_id is not None:
        try:
            dentist_id = int(dentist_id)
        except (ValueError, TypeError):
            dentist_id = None
    if clinic_id is not None:
        try:
            clinic_id = int(clinic_id)
        except (ValueError, TypeError):
            clinic_id = default_clinic_id
    if not dso_id:
        if clinic_id:
            clinic_for_dso = Clinic.query.get(clinic_id)
            if clinic_for_dso and clinic_for_dso.dso_id:
                dso_id = clinic_for_dso.dso_id
        if not dso_id:
            dso_id = default_dso_id
    else:
        try:
            dso_id = int(dso_id)
        except (ValueError, TypeError):
            dso_id = default_dso_id
    return clinic_id, referral_doctor, dso_id, dentist_id


@vizbriz_quiz.route('/quiz/clear-cache', methods=['GET'])
def clear_cache():
    """
    Clear the quiz package cache to force reload.
    """
    try:
        clear_quiz_package_cache()
        return jsonify({'status': 'success', 'message': 'Quiz package cache cleared'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@vizbriz_quiz.route('/quiz', methods=['GET'])
def quiz_page():
    """
    Display the VizBriz multilingual quiz.
    Supports language selection via ?lang=en|ru|he parameter.
    Supports clinic assignment via ?clinic_id= and ?referral= parameters.
    """
    # Language is always English (language selector removed from UI)
    # System still supports multiple languages internally, but default is English
    language = 'en'
    
    # Get optional clinic/referral/dentist parameters
    clinic_id = request.args.get('clinic_id')
    referral_doctor = request.args.get('referral')
    dso_id = request.args.get('dso_id')
    dentist_id = request.args.get('dentist_id')
    if dentist_id is not None:
        try:
            dentist_id = int(dentist_id)
        except (ValueError, TypeError):
            dentist_id = None
    # Use default DSO ID 27 if not provided
    if not dso_id:
        dso_id = 27
    else:
        try:
            dso_id = int(dso_id)
        except (ValueError, TypeError):
            dso_id = 27
    
    # Load quiz package
    try:
        current_app.logger.info("Loading quiz package...")
        quiz_package = load_quiz_package()
        # Compute a deterministic timestamp from quiz package file mtime
        try:
            static_folder = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'static')
            quiz_package_path = os.path.join(static_folder, 'vizbriz_quiz_package.json')
            mtime = os.path.getmtime(quiz_package_path)
            build_ts = datetime.utcfromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S UTC')
        except Exception:
            build_ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        current_app.logger.info(f"Quiz package loaded successfully. Keys: {list(quiz_package.keys())}")
        
        # Get DSO and clinics
        dso_info = None
        clinics = []
        clinic_info = None
        dentist_info = None
        
        try:
            from flask_app.models import DSO, Clinic, Dentist, dentist_clinic_association
            
            if dso_id:
                # Fetch DSO for logo and branding (used in brand-bar at top of quiz)
                dso_info = DSO.query.filter_by(id=dso_id).first()
                current_app.logger.info(f"DSO {dso_id} exists: {dso_info is not None}")
                
                # Simple query: SELECT * FROM clinics WHERE dso_id = ?
                clinics = Clinic.query.filter_by(dso_id=dso_id).all()
                current_app.logger.info(f"Found {len(clinics)} clinics for DSO {dso_id}")
            else:
                # Get all clinics if no DSO specified
                clinics = Clinic.query.all()
                current_app.logger.info(f"Found {len(clinics)} total clinics")
        except Exception as db_error:
            current_app.logger.error(f"Database error: {str(db_error)}")
            clinics = []
        
        # Convert clinic objects to dictionaries for JSON serialization
        clinics_data = []
        for clinic in clinics:
            clinics_data.append({
                'id': clinic.id,
                'name': clinic.name,
                'dso_id': clinic.dso_id,
                'email': clinic.email,
                'address': clinic.address,
                'contact_person': clinic.contact_person
            })
        
        # If no clinics found, log warning
        if not clinics_data:
            current_app.logger.warning("No active clinics found in database")
            # Add a placeholder clinic for testing
            clinics_data = [{'id': 0, 'name': 'No clinics available - Please contact administrator', 'dso_id': None, 'email': None, 'address': None, 'contact_person': None}]
        
        # Get clinic and dentist info if clinic_id is provided
        if clinic_id:
            clinic_info = Clinic.query.get(clinic_id)
            if clinic_info:
                # Get first dentist associated with clinic
                dentist = (
                    Dentist.query
                    .join(dentist_clinic_association, Dentist.id == dentist_clinic_association.c.dentist_id)
                    .filter(dentist_clinic_association.c.clinic_id == clinic_id)
                    .first()
                )
                if dentist:
                    dentist_info = dentist
        
        # Convert other objects to dictionaries if they exist
        dso_data = None
        if dso_info:
            # Resolve DSO logo - only if DSO has one configured (no synthetic or fallback logo)
            raw_logo = dso_info.logo_url if (hasattr(dso_info, 'logo_url') and dso_info.logo) else None
            
            # Convert relative paths to absolute paths; for local files, verify they exist
            if raw_logo:
                if raw_logo.startswith(('http://', 'https://')):
                    # External URL - use as-is (no file check)
                    pass
                else:
                    if raw_logo.startswith('/'):
                        if not raw_logo.startswith(('/flask_static/', '/static/')):
                            if raw_logo.startswith('/logos/'):
                                raw_logo = '/flask_static/images' + raw_logo
                            else:
                                raw_logo = '/flask_static/images/' + raw_logo.lstrip('/')
                    else:
                        if raw_logo.startswith('logos/'):
                            raw_logo = '/flask_static/images/' + raw_logo
                        else:
                            raw_logo = '/flask_static/images/' + raw_logo
                    # Verify local file exists - relative URLs require uploaded file
                    rel_path = raw_logo.replace('/flask_static/', '').lstrip('/')
                    static_dir = current_app.static_folder
                    full_path = os.path.join(static_dir, rel_path) if static_dir else None
                    if not full_path or not os.path.isfile(full_path):
                        current_app.logger.warning(f"DSO logo file not found, skipping: {full_path or raw_logo}")
                        raw_logo = None
            
            dso_data = {
                'id': dso_info.id,
                'name': dso_info.name,
                'email': dso_info.email,
                'contact_person': dso_info.contact_person,
                'logo_url': raw_logo
            }
        
        clinic_data = None
        if clinic_info:
            clinic_data = {
                'id': clinic_info.id,
                'name': clinic_info.name,
                'dso_id': clinic_info.dso_id,
                'email': clinic_info.email,
                'address': clinic_info.address,
                'contact_person': clinic_info.contact_person
            }
        
        dentist_data = None
        if dentist_info:
            dentist_data = {
                'id': dentist_info.id,
                'name': dentist_info.name,
                'email': dentist_info.email
            }
        
        # Prepare data for template
        context = {
            'quiz_package': quiz_package,
            'language': language,
            'clinic_id': clinic_id,
            'referral_doctor': referral_doctor,
            'dso_id': dso_id,
            'dentist_id': dentist_id,
            'dso_info': dso_data,
            'clinics': clinics_data,
            'clinic_info': clinic_data,
            'dentist_info': dentist_data,
            'is_rtl': language == 'he',  # Hebrew is RTL
            'quiz_version': (quiz_package.get('metadata') or {}).get('version', 'v?'),
            'build_ts': build_ts
        }
        
        return render_template('vizbriz_quiz_multi.html', **context)
    
    except Exception as e:
        current_app.logger.error(f"Error loading quiz: {str(e)}")
        current_app.logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        current_app.logger.error(f"Traceback: {traceback.format_exc()}")
        
        # Check if it's an i18n error
        if 'i18n' in str(e):
            current_app.logger.error("i18n error detected - checking quiz package structure")
            try:
                quiz_package = load_quiz_package()
                current_app.logger.error(f"Quiz package keys: {list(quiz_package.keys())}")
                current_app.logger.error(f"Has i18n: {'i18n' in quiz_package}")
            except Exception as debug_e:
                current_app.logger.error(f"Debug error: {debug_e}")
        
        return jsonify({'error': f'Failed to load quiz: {str(e)}'}), 500


@vizbriz_quiz.route('/quiz_hebrew', methods=['GET'])
def quiz_page_hebrew():
    """
    Display the VizBriz multilingual quiz (Hebrew-only sandbox route).
    This is intentionally a copy of `quiz_page()` so we can iterate safely without
    changing the original /vizbriz/quiz behavior.
    """
    # Force Hebrew (RTL)
    language = 'he'
    
    # Get optional clinic/referral/dentist parameters from URL
    clinic_id = request.args.get('clinic_id')
    referral_doctor = request.args.get('referral')
    dso_id = request.args.get('dso_id')
    dentist_id = request.args.get('dentist_id')
    if dentist_id is not None:
        try:
            dentist_id = int(dentist_id)
        except (ValueError, TypeError):
            dentist_id = None
    
    # Default to clinic_id=6 and dso_id=27 for Hebrew quiz (unless overridden by URL params)
    if not clinic_id:
        clinic_id = 6
    else:
        try:
            clinic_id = int(clinic_id)
        except (ValueError, TypeError):
            clinic_id = 6
    
    # Use default DSO ID 27 if not provided; infer from clinic when clinic_id in URL but no dso_id
    if not dso_id:
        if clinic_id:
            clinic_for_dso = Clinic.query.get(clinic_id)
            if clinic_for_dso and clinic_for_dso.dso_id:
                dso_id = clinic_for_dso.dso_id
                current_app.logger.info(f"Hebrew quiz: inferred dso_id={dso_id} from clinic_id={clinic_id}")
        if not dso_id:
            dso_id = 27
    else:
        try:
            dso_id = int(dso_id)
        except (ValueError, TypeError):
            dso_id = 27
    
    # Load quiz package
    try:
        current_app.logger.info("Loading quiz package...")
        quiz_package = load_quiz_package()
        # Compute a deterministic timestamp from quiz package file mtime
        try:
            static_folder = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'static')
            quiz_package_path = os.path.join(static_folder, 'vizbriz_quiz_package.json')
            mtime = os.path.getmtime(quiz_package_path)
            build_ts = datetime.utcfromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S UTC')
        except Exception:
            build_ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        current_app.logger.info(f"Quiz package loaded successfully. Keys: {list(quiz_package.keys())}")
        
        # Get DSO and clinics
        dso_info = None
        clinics = []
        clinic_info = None
        dentist_info = None
        
        try:
            from flask_app.models import DSO, Clinic, Dentist, dentist_clinic_association
            
            if dso_id:
                # Fetch DSO for logo and branding (used in brand-bar at top of quiz)
                dso_info = DSO.query.filter_by(id=dso_id).first()
                current_app.logger.info(f"DSO {dso_id} exists: {dso_info is not None}")
                
                # Simple query: SELECT * FROM clinics WHERE dso_id = ?
                clinics = Clinic.query.filter_by(dso_id=dso_id).all()
                current_app.logger.info(f"Found {len(clinics)} clinics for DSO {dso_id}")
            else:
                # Get all clinics if no DSO specified
                clinics = Clinic.query.all()
                current_app.logger.info(f"Found {len(clinics)} total clinics")
        except Exception as db_error:
            current_app.logger.error(f"Database error: {str(db_error)}")
            clinics = []
        
        # Convert clinic objects to dictionaries for JSON serialization
        clinics_data = []
        for clinic in clinics:
            clinics_data.append({
                'id': clinic.id,
                'name': clinic.name,
                'dso_id': clinic.dso_id,
                'email': clinic.email,
                'address': clinic.address,
                'contact_person': clinic.contact_person
            })
        
        # If no clinics found, log warning
        if not clinics_data:
            current_app.logger.warning("No active clinics found in database")
            # Add a placeholder clinic for testing
            clinics_data = [{'id': 0, 'name': 'No clinics available - Please contact administrator', 'dso_id': None, 'email': None, 'address': None, 'contact_person': None}]
        
        # Get clinic and dentist info if clinic_id is provided
        if clinic_id:
            clinic_info = Clinic.query.get(clinic_id)
            if clinic_info:
                # Get first dentist associated with clinic
                dentist = (
                    Dentist.query
                    .join(dentist_clinic_association, Dentist.id == dentist_clinic_association.c.dentist_id)
                    .filter(dentist_clinic_association.c.clinic_id == clinic_id)
                    .first()
                )
                if dentist:
                    dentist_info = dentist
        
        # Convert other objects to dictionaries if they exist
        dso_data = None
        if dso_info:
            # Resolve DSO logo - only if DSO has one configured (no synthetic or fallback logo)
            raw_logo = dso_info.logo_url if (hasattr(dso_info, 'logo_url') and dso_info.logo) else None
            
            # Convert relative paths to absolute paths; for local files, verify they exist
            if raw_logo:
                if raw_logo.startswith(('http://', 'https://')):
                    # External URL - use as-is (no file check)
                    pass
                else:
                    if raw_logo.startswith('/'):
                        if not raw_logo.startswith(('/flask_static/', '/static/')):
                            if raw_logo.startswith('/logos/'):
                                raw_logo = '/flask_static/images' + raw_logo
                            else:
                                raw_logo = '/flask_static/images/' + raw_logo.lstrip('/')
                    else:
                        if raw_logo.startswith('logos/'):
                            raw_logo = '/flask_static/images/' + raw_logo
                        else:
                            raw_logo = '/flask_static/images/' + raw_logo
                    # Verify local file exists - relative URLs require uploaded file
                    rel_path = raw_logo.replace('/flask_static/', '').lstrip('/')
                    static_dir = current_app.static_folder
                    full_path = os.path.join(static_dir, rel_path) if static_dir else None
                    if not full_path or not os.path.isfile(full_path):
                        current_app.logger.warning(f"DSO logo file not found, skipping: {full_path or raw_logo}")
                        raw_logo = None
            
            dso_data = {
                'id': dso_info.id,
                'name': dso_info.name,
                'email': dso_info.email,
                'contact_person': dso_info.contact_person,
                'logo_url': raw_logo
            }
        
        clinic_data = None
        if clinic_info:
            clinic_data = {
                'id': clinic_info.id,
                'name': clinic_info.name,
                'dso_id': clinic_info.dso_id,
                'email': clinic_info.email,
                'address': clinic_info.address,
                'contact_person': clinic_info.contact_person
            }
        
        dentist_data = None
        if dentist_info:
            dentist_data = {
                'id': dentist_info.id,
                'name': dentist_info.name,
                'email': dentist_info.email
            }
        
        # Prepare data for template
        context = {
            'quiz_package': quiz_package,
            'language': language,
            'clinic_id': clinic_id,
            'referral_doctor': referral_doctor,
            'dso_id': dso_id,
            'dentist_id': dentist_id,
            'dso_info': dso_data,
            'clinics': clinics_data,
            'clinic_info': clinic_data,
            'dentist_info': dentist_data,
            'is_rtl': language == 'he',  # Hebrew is RTL
            'quiz_version': (quiz_package.get('metadata') or {}).get('version', 'v?'),
            'build_ts': build_ts
        }
        
        return render_template('vizbriz_quiz_multi.html', **context)
    
    except Exception as e:
        current_app.logger.error(f"Error loading quiz: {str(e)}")
        current_app.logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        current_app.logger.error(f"Traceback: {traceback.format_exc()}")
        
        # Check if it's an i18n error
        if 'i18n' in str(e):
            current_app.logger.error("i18n error detected - checking quiz package structure")
            try:
                quiz_package = load_quiz_package()
                current_app.logger.error(f"Quiz package keys: {list(quiz_package.keys())}")
                current_app.logger.error(f"Has i18n: {'i18n' in quiz_package}")
            except Exception as debug_e:
                current_app.logger.error(f"Debug error: {debug_e}")
        
        return jsonify({'error': f'Failed to load quiz: {str(e)}'}), 500


@vizbriz_quiz.route('/followup', methods=['GET'])
def followup_quiz_page():
    """1st follow-up questionnaire (English) — in-system SQUARE form."""
    language = (request.args.get('lang') or 'en').strip().lower()
    if language not in ('en', 'he'):
        language = 'en'
    clinic_id, referral_doctor, dso_id, dentist_id = _parse_quiz_url_params()
    try:
        quiz_package = load_followup_quiz_package()
        context = _build_quiz_page_context(
            language=language,
            quiz_package=quiz_package,
            package_filename='vizbriz_followup_quiz_package.json',
            quiz_mode='followup',
            clinic_id=clinic_id,
            referral_doctor=referral_doctor,
            dso_id=dso_id,
            dentist_id=dentist_id,
        )
        return render_template('vizbriz_quiz_multi.html', **context)
    except Exception as e:
        current_app.logger.error(f"Error loading follow-up quiz: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to load follow-up questionnaire: {str(e)}'}), 500


@vizbriz_quiz.route('/followup_hebrew', methods=['GET'])
def followup_quiz_page_hebrew():
    """1st follow-up questionnaire (Hebrew route — same package, RTL)."""
    clinic_id, referral_doctor, dso_id, dentist_id = _parse_quiz_url_params(default_clinic_id=6)
    try:
        quiz_package = load_followup_quiz_package()
        context = _build_quiz_page_context(
            language='he',
            quiz_package=quiz_package,
            package_filename='vizbriz_followup_quiz_package.json',
            quiz_mode='followup',
            clinic_id=clinic_id,
            referral_doctor=referral_doctor,
            dso_id=dso_id,
            dentist_id=dentist_id,
        )
        return render_template('vizbriz_quiz_multi.html', **context)
    except Exception as e:
        current_app.logger.error(f"Error loading follow-up quiz (Hebrew): {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to load follow-up questionnaire: {str(e)}'}), 500


@vizbriz_quiz.route('/followup/submit', methods=['POST'])
@vizbriz_quiz.route('/followup_hebrew/submit', methods=['POST'])
def submit_followup_quiz():
    """Process 1st follow-up questionnaire submission."""
    try:
        data = request.get_json() or {}
        answers = data.get('answers', {})
        language = data.get('language', 'en')
        if request.path.endswith('/followup_hebrew/submit'):
            language = 'he'
        clinic_id = data.get('clinic_id')
        referral_doctor = data.get('referral_doctor')
        enhanced_answers = data.get('enhanced_answers')

        if not clinic_id and 'DEMO_REFERRING_DENTIST_OR_CLI' in answers:
            clinic_id = answers['DEMO_REFERRING_DENTIST_OR_CLI']

        patient_email = answers.get('EMAIL') or answers.get('DEMO_EMAIL')
        patient_id_param = data.get('patient_id') or answers.get('_PATIENT_ID')
        if patient_id_param and not patient_email:
            try:
                p = Patient.query.get(int(patient_id_param))
                if p and getattr(p, 'email', None):
                    patient_email = p.email
                    answers['EMAIL'] = patient_email
                    answers['DEMO_EMAIL'] = patient_email
            except (TypeError, ValueError):
                pass
        if not patient_email:
            return jsonify({'error': 'Email is required'}), 400

        answers['PHONE'] = answers.get('PHONE', '') or answers.get('DEMO_PHONE', '')
        if answers.get('DEMO_EMAIL') and not answers.get('EMAIL'):
            answers['EMAIL'] = answers['DEMO_EMAIL']

        evaluation_result = evaluate_followup_quiz(answers, language)

        clinic_email = None
        if clinic_id:
            clinic = Clinic.query.get(clinic_id)
            if clinic:
                clinic_email = clinic.email

        dentist_id = data.get('dentist_id')
        if dentist_id is not None:
            try:
                dentist_id = int(dentist_id)
            except (ValueError, TypeError):
                dentist_id = None

        patient_record = None
        if patient_id_param:
            try:
                patient_record = Patient.query.get(int(patient_id_param))
            except (TypeError, ValueError):
                patient_record = None

        followup_package = load_followup_quiz_package()
        enhanced_answers = build_enhanced_answers_from_package(
            answers,
            followup_package,
            language=language,
            patient=patient_record,
        )

        quiz_id = save_vizbriz_quiz(
            answers=answers,
            evaluation_result=evaluation_result,
            enhanced_answers=enhanced_answers,
            patient_email=patient_email,
            language=language,
            clinic_email=clinic_email,
            clinic_id=clinic_id,
            referral_doctor=referral_doctor,
            dentist_id=dentist_id,
            quiz_type='vizbriz_followup_v1',
        )

        quiz = get_quiz_by_id(quiz_id)
        pdf_s3_key = None
        if quiz and quiz.user_id:
            if not patient_record and quiz.user_id:
                patient_record = Patient.query.get(quiz.user_id)
            if not enhanced_answers.get('questions_and_answers'):
                enhanced_answers = build_enhanced_answers_from_package(
                    answers,
                    followup_package,
                    language=language,
                    patient=patient_record,
                )
            try:
                pdf_s3_key = create_and_store_questionnaire_pdf(
                    patient_id=quiz.user_id,
                    enhanced_answers=enhanced_answers,
                    evaluation_result=evaluation_result,
                    language=language,
                    report_kind='followup',
                    quiz_id=quiz_id,
                )
            except Exception as pdf_err:
                current_app.logger.error(
                    f"Follow-up questionnaire PDF upload failed for quiz {quiz_id}: {pdf_err}",
                    exc_info=True,
                )

        return jsonify({
            'success': True,
            'quiz_id': quiz_id,
            'patient_id': quiz.user_id if quiz else None,
            'evaluation_result': evaluation_result,
            'patient_email': patient_email,
            'language': language,
            'questionnaire_pdf_s3_key': pdf_s3_key,
        })
    except Exception as e:
        current_app.logger.error(f"Follow-up quiz submission failed: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@vizbriz_quiz.route('/quiz/submit', methods=['POST'])
@vizbriz_quiz.route('/quiz_hebrew/submit', methods=['POST'])
def submit_quiz():
    current_app.logger.info(f"=== QUIZ SUBMISSION REQUEST RECEIVED ===")
    current_app.logger.info(f"Request method: {request.method}")
    current_app.logger.info(f"Request content type: {request.content_type}")
    current_app.logger.info(f"Request has JSON: {request.is_json}")
    """
    Process quiz submission, calculate score, determine risk, and return outcome.
    """
    try:
        data = request.get_json()
        
        # Extract submission data
        answers = data.get('answers', {})
        language = data.get('language', 'en')
        clinic_id = data.get('clinic_id')
        referral_doctor = data.get('referral_doctor')
        enhanced_answers = data.get('enhanced_answers')
        
        # Hebrew quiz route: force language='he', use clinic_id from request (fallback to 6 for backward compat)
        if request.path.endswith('/vizbriz/quiz_hebrew/submit'):
            language = 'he'
            if not clinic_id:
                clinic_id = 6
                current_app.logger.info("Hebrew submit: no clinic_id in request, using fallback clinic_id=6")
            else:
                current_app.logger.info(f"Hebrew submit: using clinic_id={clinic_id} from request")
        
        # For Hebrew quiz: capture referring dentist name as metadata (independent of clinic_id)
        if language == 'he':
            if not clinic_id:
                clinic_id = 6
            if not referral_doctor and 'REFERRING_DENTIST_NAME' in answers:
                referral_doctor = answers.get('REFERRING_DENTIST_NAME')
                current_app.logger.info(f"Hebrew quiz: Captured referring dentist name: {referral_doctor}")
        
        current_app.logger.info(f"=== QUIZ SUBMISSION DATA EXTRACTED ===")
        current_app.logger.info(f"Language: {language}")
        current_app.logger.info(f"Clinic ID: {clinic_id}")
        current_app.logger.info(f"Referral Doctor: {referral_doctor}")
        current_app.logger.info(f"Answers count: {len(answers)}")
        current_app.logger.info(f"Enhanced answers present: {enhanced_answers is not None}")
        if enhanced_answers:
            current_app.logger.info(f"Enhanced answers has submission_info: {'submission_info' in enhanced_answers}")
            current_app.logger.info(f"Enhanced answers has questions_and_answers: {'questions_and_answers' in enhanced_answers}")
            if 'questions_and_answers' in enhanced_answers:
                current_app.logger.info(f"Questions count in enhanced_answers: {len(enhanced_answers.get('questions_and_answers', []))}")
        
        # Translate Unicode text (Hebrew, Russian, etc.) to English using Bedrock LLM
        # This ensures all stored text is in English, which will render correctly in PDFs
        # Wrap in try-except to ensure translation failures don't break submission
        if enhanced_answers:
            try:
                import traceback
                current_app.logger.info(f"Starting translation for language: {language}")
                # Only translate if there are actually texts that need translation
                # Skip if language is English or if no texts need translation
                if language != 'en':
                    enhanced_answers = translate_enhanced_answers(enhanced_answers, source_language=language)
                    current_app.logger.info("Translation completed successfully")
                else:
                    current_app.logger.info("Skipping translation - language is English")
            except Exception as translation_error:
                import traceback
                current_app.logger.error(f"Translation failed but continuing with submission: {str(translation_error)}")
                current_app.logger.error(f"Translation error traceback: {traceback.format_exc()}")
                # Continue with original enhanced_answers if translation fails
                # The PDF might have rendering issues, but submission won't fail
        
        # Get clinic_id from form answers if not provided in request
        if not clinic_id and 'DEMO_REFERRING_DENTIST_OR_CLI' in answers:
            clinic_id = answers['DEMO_REFERRING_DENTIST_OR_CLI']
            current_app.logger.info(f"Using clinic_id from form: {clinic_id}")
        
        # Validate required fields
        patient_email = answers.get('EMAIL')
        if not patient_email:
            return jsonify({'error': 'Email is required'}), 400

        # Hebrew quiz: ID (teudat zehut) is mandatory
        if language == 'he':
            id_val = (answers.get('DEMO_ID') or '').strip()
            if not id_val:
                return jsonify({'error': 'תעודת זהות (ID) היא שדה חובה'}), 400
        
        # Extract phone number from demographics
        patient_phone = answers.get('DEMO_PHONE', '')
        
        # Add phone number to answers for processing
        answers['PHONE'] = patient_phone
        
        current_app.logger.info(f"Processing VizBriz quiz submission for {patient_email} in {language}")
        
        # Evaluate quiz
        evaluation_result = evaluate_quiz(answers, language)
        current_app.logger.info(
            f"RISK_DEBUG: language={language} Q1={answers.get('Q1')} Q2={answers.get('Q2')} "
            f"ssi_status={evaluation_result.get('ssi_status')} -> risk_band={evaluation_result.get('risk_band')} "
            f"score={evaluation_result.get('total_score')} red_flags={evaluation_result.get('red_flags')}"
        )
        
        # Determine clinic email
        clinic_email = None
        if clinic_id:
            clinic = Clinic.query.get(clinic_id)
            if clinic:
                clinic_email = clinic.email
        
        # Get dentist_id from request (from URL when admin generated link/QR)
        dentist_id = data.get('dentist_id')
        if dentist_id is not None:
            try:
                dentist_id = int(dentist_id)
            except (ValueError, TypeError):
                dentist_id = None

        # Save quiz to database
        quiz_id = save_vizbriz_quiz(
            answers=answers,
            evaluation_result=evaluation_result,
            enhanced_answers=enhanced_answers,
            patient_email=patient_email,
            language=language,
            clinic_email=clinic_email,
            clinic_id=clinic_id,
            referral_doctor=referral_doctor,
            dentist_id=dentist_id
        )
        
        # Record consent information if Q39 is present
        if 'Q39' in answers:
            from flask_app.models import PatientConsent
            consent_given = answers['Q39'] == 'yes'
            
            # Get DSO ID if available
            dso_id = None
            if clinic_id:
                clinic = Clinic.query.get(clinic_id)
                if clinic:
                    dso_id = clinic.dso_id
            
            # Record consent with audit trail
            # Q39 is specifically about third-party sharing with sleep labs and dental labs
            consent_record = PatientConsent.record_consent(
                patient_email=patient_email,
                consent_given=consent_given,
                clinic_id=clinic_id,
                dso_id=dso_id,
                patient_id=None,  # Will be set after we get quiz
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent'),
                consent_type=PatientConsent.CONSENT_TYPE_THIRD_PARTY_SHARING,
                consent_version='v1.0'  # Version of the Q39 consent question
            )
            
            if consent_record:
                current_app.logger.info(f"Consent recorded for {patient_email}: {consent_given}")
            else:
                current_app.logger.error(f"Failed to record consent for {patient_email}")
        
        # Get patient_id for observations
        quiz = get_quiz_by_id(quiz_id)
        current_app.logger.info(f"=== QUIZ SUBMISSION DEBUG START ===")
        current_app.logger.info(f"Quiz ID: {quiz_id}, Quiz found: {quiz is not None}")
        level1_context = None
        if quiz:
            current_app.logger.info(f"Quiz user_id: {quiz.user_id}, Quiz language: {quiz.language if hasattr(quiz, 'language') else 'N/A'}")
        else:
            current_app.logger.error(f"❌ QUIZ OBJECT IS NONE - Cannot proceed with external report API call")
        current_app.logger.info(f"Patient email: {patient_email}")
        current_app.logger.info(f"Clinic ID: {clinic_id}")
        
        # Call external report API with unified JSON (evaluation_result + enhanced_answers)
        external_report_data = None
        external_report_error = None
        
        if quiz:
            current_app.logger.info(f"✅ Quiz object exists, proceeding with external report API call")
            # Build unified payload combining scoring/risk assessment and answers
            # quiz_input structure: {raw_answers, enhanced_answers, evaluation_summary}
            try:
                quiz_input_data = json.loads(quiz.quiz_input) if quiz.quiz_input else {}
                # Extract enhanced_answers from the stored structure
                enhanced_answers_json = quiz_input_data.get('enhanced_answers', {})
                # If enhanced_answers is not found, try using the whole structure (backward compatibility)
                if not enhanced_answers_json and quiz_input_data:
                    # Check if it's the old format where quiz_input was just enhanced_answers
                    if 'submission_info' in quiz_input_data or 'questions_and_answers' in quiz_input_data:
                        enhanced_answers_json = quiz_input_data
                    else:
                        enhanced_answers_json = {}
            except (TypeError, ValueError):
                enhanced_answers_json = {}
            
            # Ensure submission_info exists and patient_id is set
            if "submission_info" not in enhanced_answers_json:
                enhanced_answers_json["submission_info"] = {}
            
            # Set patient_id in submission_info if available
            if quiz.user_id:
                enhanced_answers_json["submission_info"]["patient_id"] = quiz.user_id
            
            # Ensure questions_and_answers exists (required by API)
            if "questions_and_answers" not in enhanced_answers_json:
                enhanced_answers_json["questions_and_answers"] = []
            
            # Create unified payload matching Postman format: evaluation_summary + enhanced_answers
            unified_payload = {
                "enhanced_answers": enhanced_answers_json,
                "evaluation_summary": {
                    "total_score": evaluation_result.get('total_score'),
                    "risk_band": evaluation_result.get('risk_band'),
                    "risk_label": evaluation_result.get('risk_label'),
                    "internal_risk_band": evaluation_result.get('internal_risk_band'),  # Added for Hebrew-specific display
                    "red_flags": evaluation_result.get('red_flags', []),
                    "outcome_title": evaluation_result.get('outcome_title'),
                    "outcome_body": evaluation_result.get('outcome_body'),
                    "cta_text": evaluation_result.get('cta_text'),
                    "diagnosed": evaluation_result.get('diagnosed'),  # Added for Hebrew-specific risk band display
                    "treatment": evaluation_result.get('treatment')  # Added for Hebrew-specific risk band display
                }
            }
            
            # Store the API payload in the database for audit trail and backward compatibility
            try:
                quiz.api_payload = json.dumps(unified_payload)
                db.session.commit()
                current_app.logger.info(f"Stored API payload in database for quiz {quiz_id}")
            except Exception as save_error:
                current_app.logger.warning(f"Failed to save API payload to database: {save_error}")
                db.session.rollback()
            
            current_app.logger.info(f"=== EXTERNAL REPORT API CALL ===")
            current_app.logger.info(f"Calling external report API for quiz {quiz_id}")
            current_app.logger.info(f"Payload keys: {list(unified_payload.keys())}")
            current_app.logger.info(f"Evaluation summary keys: {list(unified_payload.get('evaluation_summary', {}).keys())}")
            current_app.logger.info(f"Enhanced answers has submission_info: {'submission_info' in unified_payload.get('enhanced_answers', {})}")
            current_app.logger.info(f"Enhanced answers has questions_and_answers: {'questions_and_answers' in unified_payload.get('enhanced_answers', {})}")
            current_app.logger.info(f"Questions count: {len(unified_payload.get('enhanced_answers', {}).get('questions_and_answers', []))}")
            
            # Internal Level-1 report only (same-origin /flask_static images in iframe avoids COEP blocks on cross-origin CDN assets).
            external_report_data = None
            external_report_error = None
            try:
                external_report_data = {
                    "pdf_url": f"/vizbriz/reports/level1/pdf/{quiz_id}",
                    "frame_url": f"/vizbriz/reports/level1/frame/{quiz_id}",
                }
            except Exception as _internal_report_err:
                current_app.logger.error(f"Internal report URL generation failed for quiz {quiz_id}: {_internal_report_err}")
                external_report_error = "internal_report_failed"

            # Hebrew: generate patient-facing narrative sections from the stored quiz JSON via Bedrock
            if language == 'he' and quiz:
                try:
                    from flask_app.helpers.level1_report_hebrew import generate_level1_hebrew_narrative_with_bedrock

                    quiz_payload = json.loads(quiz.quiz_input or "{}") if quiz.quiz_input else {}
                    risk_category = (quiz_payload.get("evaluation_summary") or {}).get("risk_band") or quiz.risk_band or "other"

                    narrative = generate_level1_hebrew_narrative_with_bedrock(
                        patient_quiz_json=quiz_payload,
                        risk_category=str(risk_category),
                        patient_id=quiz.user_id,
                    )
                    if narrative:
                        quiz.ai_response = json.dumps({"level1_report_he": narrative}, ensure_ascii=False)
                        db.session.commit()
                except Exception as _narr_err:
                    current_app.logger.error(f"Hebrew narrative generation failed for quiz {quiz_id}: {_narr_err}")
                    try:
                        quiz.ai_response = json.dumps({"level1_report_he_error": str(_narr_err)}, ensure_ascii=False)
                        db.session.commit()
                    except Exception:
                        pass
            
            current_app.logger.info(f"=== EXTERNAL REPORT API RESPONSE ===")
            current_app.logger.info(f"external_report_data is None: {external_report_data is None}")
            current_app.logger.info(f"external_report_error: {external_report_error}")
            
            if external_report_data:
                current_app.logger.info(f"external_report_data keys: {list(external_report_data.keys())}")
                current_app.logger.info(f"external_report_data has pdf_url: {'pdf_url' in external_report_data}")
                current_app.logger.info(f"external_report_data has pdf: {'pdf' in external_report_data}")
                if 'pdf_url' in external_report_data:
                    current_app.logger.info(f"PDF URL value: {external_report_data.get('pdf_url')}")
                if 'pdf' in external_report_data:
                    current_app.logger.info(f"PDF value: {external_report_data.get('pdf')}")
                current_app.logger.info(f"Internal Level-1 report URLs ready for quiz {quiz_id}")
                
                # Generate PDF from same HTML as iframe and upload to S3 as Level 1 Report
                pdf_url = external_report_data.get('pdf_url') or external_report_data.get('pdf')
                current_app.logger.info(f"=== PDF PROCESSING ===")
                current_app.logger.info(f"PDF URL check for quiz {quiz_id}: pdf_url={pdf_url}, quiz.user_id={quiz.user_id}")
                current_app.logger.info(f"PDF URL exists: {bool(pdf_url)}, quiz.user_id exists: {bool(quiz.user_id)}")

                # Internal HTML->PDF (Playwright); avoids reliance on external report host/CDN.
                s3_key, pdf_content, pdf_error = None, None, None
                pdf_filename = None

                if quiz.user_id:
                    try:
                        from flask_app.helpers.level1_report_hebrew import (
                            build_level1_context_from_vizbriz_quiz,
                            prepare_context_for_pdf,
                            render_level1_report_html,
                            html_to_pdf_bytes,
                        )

                        context = build_level1_context_from_vizbriz_quiz(quiz)
                        level1_context = context
                        html = render_level1_report_html(prepare_context_for_pdf(context))
                        pdf_content = html_to_pdf_bytes(html)
                        pdf_filename = f"Level_1_Report_Quiz_{quiz_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"

                        s3_key, _saved_filename, pdf_error = _upload_level1_pdf_bytes(
                            pdf_content=pdf_content,
                            patient_id=quiz.user_id,
                            quiz_id=quiz_id,
                            filename=pdf_filename,
                        )
                        current_app.logger.info(
                            f"Level-1 internal PDF generated/uploaded - s3_key={s3_key}, size={len(pdf_content) if pdf_content else 0}, error={pdf_error}"
                        )
                    except Exception as level1_pdf_err:
                        s3_key, pdf_content, pdf_error = None, None, str(level1_pdf_err)
                        current_app.logger.error(f"Level-1 PDF generation/upload failed for quiz {quiz_id}: {level1_pdf_err}", exc_info=True)

                # We want to email the patient the report even if S3 upload fails.
                # (S3 upload is desirable for storage/audit, but email delivery should not depend on it.)
                if pdf_content:
                    if s3_key:
                        current_app.logger.info(f"Level 1 Report PDF uploaded successfully: {s3_key}")
                    else:
                        current_app.logger.warning(f"Level 1 Report PDF email will be sent without S3 key (upload failed or skipped). pdf_error={pdf_error}")
                    current_app.logger.info(f"PDF content size: {len(pdf_content)} bytes")

                    # Send email to patient with PDF attachment
                    current_app.logger.info(f"=== EMAIL SENDING ===")
                    current_app.logger.info(f"Patient email check: {patient_email}")
                    if patient_email:
                        # Get clinic and DSO information
                        clinic_name = None
                        clinic_logo_url = None
                        dso_name = None
                        dso_logo_url = None
                        dso_id = None
                        clinic = None

                        if clinic_id:
                            clinic = Clinic.query.get(clinic_id)
                            if clinic:
                                clinic_name = clinic.name
                                # Get clinic logo URL
                                try:
                                    if hasattr(clinic, 'logo_url') and callable(clinic.logo_url):
                                        clinic_logo_url = clinic.logo_url()
                                    elif hasattr(clinic, 'logo_url'):
                                        clinic_logo_url = clinic.logo_url
                                except Exception:
                                    pass
                                # Get DSO info if available
                                if clinic.dso_id:
                                    dso_id = clinic.dso_id
                                    from flask_app.models import DSO
                                    dso = DSO.query.get(clinic.dso_id)
                                    if dso:
                                        dso_name = dso.name
                                        # Get DSO logo URL - fall back to raw logo field
                                        try:
                                            raw_logo = dso.logo.strip() if hasattr(dso, 'logo') and dso.logo else None
                                            dso_logo_url = raw_logo
                                        except Exception as e:
                                            current_app.logger.warning(f"Error getting DSO logo URL: {e}")

                        current_app.logger.info(f"Email will use: dso_name='{dso_name}', dso_logo_url='{dso_logo_url}', clinic_name='{clinic_name}'")

                        # PDF filename for attachment (prefer the actual uploaded filename)
                        pdf_filename_for_email = pdf_filename or f"Level_1_Report_Quiz_{quiz_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"

                        # Get patient name for email personalization
                        patient_name_for_email = None
                        try:
                            from flask_app.models import Patient
                            patient = Patient.query.get(quiz.user_id) if quiz.user_id else None
                            if patient and patient.name:
                                patient_name_for_email = patient.name
                            else:
                                # Fallback to quiz answer
                                quiz_input_json = json.loads(quiz.quiz_input) if quiz.quiz_input else {}
                                raw_answers = quiz_input_json.get('raw_answers', {})
                                quiz_name = raw_answers.get('DEMO_FULL_NAME', '')
                                if quiz_name and quiz_name.lower() not in ['test patient', 'test', 'patient']:
                                    patient_name_for_email = quiz_name
                        except Exception as e:
                            current_app.logger.warning(f"Could not get patient name for email: {e}")

                        # Get clinic phone number (optional)
                        clinic_phone = None
                        if clinic and hasattr(clinic, 'telephone') and clinic.telephone:
                            clinic_phone = clinic.telephone

                        current_app.logger.info(f"Calling _send_patient_email_with_pdf with:")
                        current_app.logger.info(f"  patient_email: {patient_email}")
                        current_app.logger.info(f"  patient_id: {quiz.user_id}")
                        current_app.logger.info(f"  pdf_filename: {pdf_filename_for_email}")
                        current_app.logger.info(f"  pdf_content size: {len(pdf_content)} bytes")
                        current_app.logger.info(f"  clinic_name: {clinic_name}")
                        current_app.logger.info(f"  dso_name: {dso_name}")
                        current_app.logger.info(f"  patient_name: {patient_name_for_email}")

                        email_sent = _send_patient_email_with_pdf(
                            patient_email=patient_email,
                            patient_id=quiz.user_id,
                            pdf_content=pdf_content,
                            pdf_filename=pdf_filename_for_email,
                            clinic_name=clinic_name,
                            clinic_logo_url=clinic_logo_url,
                            clinic_phone=clinic_phone,
                            dso_name=dso_name,
                            dso_logo_url=dso_logo_url,
                            dso_id=dso_id,
                            patient_name=patient_name_for_email,
                            evaluation_result=evaluation_result,
                            language=language
                        )
                        current_app.logger.info(f"=== EMAIL SEND RESULT ===")
                        current_app.logger.info(f"email_sent returned: {email_sent}")
                        if email_sent:
                            current_app.logger.info(f"✅ Patient email with PDF sent successfully to {patient_email}")
                        else:
                            current_app.logger.error(f"❌ Failed to send patient email with PDF to {patient_email}")
                    else:
                        current_app.logger.error("No patient email provided; cannot send patient report email.")
                elif pdf_error:
                    current_app.logger.error(f"❌ PDF GENERATION/UPLOAD FAILED ===")
                    current_app.logger.error(f"Failed to generate/upload PDF for quiz {quiz_id}, patient {patient_email}: {pdf_error}")
                    current_app.logger.error(f"PDF URL was: {pdf_url}, quiz.user_id: {quiz.user_id}")
                    current_app.logger.error(f"Email will NOT be sent due to PDF error")
                else:
                    current_app.logger.error(f"❌ PDF GENERATION RETURNED NO CONTENT ===")
                    current_app.logger.warning(f"PDF generation returned no content for quiz {quiz_id}: s3_key={s3_key}, pdf_content_size={len(pdf_content) if pdf_content else 0}")
                    current_app.logger.error(f"Email will NOT be sent - no PDF content")
            elif external_report_error and external_report_error != "missing_token":
                current_app.logger.error(f"❌ EXTERNAL REPORT API CALL FAILED ===")
                current_app.logger.error(f"External report API call failed for quiz {quiz_id}, patient {patient_email}: {external_report_error}")
                current_app.logger.error(f"Patient email will NOT be sent - external report PDF generation failed")
        else:
            current_app.logger.error(f"❌ QUIZ OBJECT IS NONE - Cannot proceed with external report API call or patient email")
            current_app.logger.error(f"Quiz ID: {quiz_id}, Quiz found: False")
            current_app.logger.error(f"Patient email will NOT be sent - quiz object not found")
        
        # Handle case where external report API was not called or returned no data
        if not external_report_data and not external_report_error:
            current_app.logger.error(f"❌ NO EXTERNAL REPORT DATA ===")
            current_app.logger.error(f"No external_report_data returned for quiz {quiz_id}, patient {patient_email}")
            current_app.logger.error(f"Patient email will NOT be sent - no external report PDF available")
        elif external_report_error == "missing_token":
            current_app.logger.warning(f"LEVEL_1_REPORT_API_TOKEN not configured - this is expected in some environments")
            current_app.logger.error(f"Level 1 Report will NOT be generated")
        
        if quiz and quiz.user_id:
            # Update consent record with patient_id if we recorded consent
            if 'Q39' in answers:
                from flask_app.models import PatientConsent
                consent_record = PatientConsent.query.filter_by(
                    patient_email=patient_email
                ).order_by(PatientConsent.created_at.desc()).first()
                
                if consent_record and not consent_record.patient_id:
                    consent_record.patient_id = quiz.user_id
                    db.session.commit()
                    current_app.logger.info(f"Updated consent record with patient_id: {quiz.user_id}")
            
            # Save observations
            save_observations_to_store(
                patient_id=quiz.user_id,
                quiz_id=quiz_id,
                observations=evaluation_result.get('observations', []),
                language=language
            )
        
        # Get patient name - prefer database, fallback to quiz answer
        patient_name_from_db = None
        try:
            from flask_app.models import Patient
            patient = Patient.query.filter_by(email=patient_email).first()
            if patient and patient.name:
                patient_name_from_db = patient.name
        except Exception:
            pass
        
        # Get name from quiz answer, but filter out test values
        quiz_name = answers.get('DEMO_FULL_NAME', '')
        if quiz_name in ['Test Patient', 'test patient', 'TEST PATIENT']:
            quiz_name = None
        
        # Return results - only include external report data, no AI-generated messages
        response = {
            'success': True,
            'quiz_id': quiz_id,
            'patient_id': quiz.user_id if quiz else None,
            'evaluation_result': {
                'total_score': evaluation_result['total_score'],
                'risk_band': evaluation_result['risk_band'],
                'risk_label': evaluation_result['risk_label'],
                'internal_risk_band': evaluation_result.get('internal_risk_band'),  # Added for Hebrew-specific display
                'red_flags': evaluation_result['red_flags'],
                'outcome_title': evaluation_result['outcome_title'],
                'outcome_body': evaluation_result['outcome_body'],
                'cta_text': evaluation_result['cta_text'],
                'diagnosed': evaluation_result.get('diagnosed'),  # Added for Hebrew-specific risk band display
                'treatment': evaluation_result.get('treatment')  # Added for Hebrew-specific risk band display
            },
            'patient_email': patient_email,
            'language': language
        }
        
        # Add external report data if available (this is what will be displayed to user)
        if external_report_data:
            response['external_report'] = external_report_data
        if external_report_error and external_report_error != "missing_token":
            response['external_report_error'] = external_report_error
        
        current_app.logger.info(f"=== QUIZ SUBMISSION SUMMARY ===")
        current_app.logger.info(f"Quiz ID: {quiz_id}")
        current_app.logger.info(f"Patient Email: {patient_email}")
        current_app.logger.info(f"Patient ID: {quiz.user_id if quiz else None}")
        current_app.logger.info(f"Risk Band: {evaluation_result['risk_band']}")
        current_app.logger.info(f"External Report Data: {'Present' if external_report_data else 'Missing'}")
        current_app.logger.info(f"External Report Error: {external_report_error if external_report_error else 'None'}")
        current_app.logger.info(f"Level 1 Report Generated: {'Yes' if external_report_data and external_report_data.get('pdf_url') else 'No'}")
        current_app.logger.info(f"Patient Email Sent: {'Check logs above' if patient_email else 'No email address'}")
        current_app.logger.info(f"=== QUIZ SUBMISSION COMPLETE ===")

        # Create and upload questionnaire + L2 PDFs (separate try blocks — failures are independent)
        if quiz and quiz.user_id:
            try:
                create_and_store_questionnaire_pdf(
                    patient_id=quiz.user_id,
                    enhanced_answers=enhanced_answers,
                    evaluation_result=evaluation_result,
                    language=language,
                    report_kind='assessment',
                    quiz_id=quiz_id,
                )
            except Exception as pdf_err:
                current_app.logger.error(f"Questionnaire PDF upload failed for quiz {quiz_id}: {pdf_err}", exc_info=True)
            try:
                create_and_store_l2_assessment_pdf(
                    patient_id=quiz.user_id,
                    quiz_id=quiz_id,
                    answers=answers,
                    enhanced_answers=enhanced_answers,
                    evaluation_result=evaluation_result,
                    level1_context=level1_context,
                )
            except Exception as l2_err:
                current_app.logger.error(f"L2 OSA assessment PDF failed for quiz {quiz_id}: {l2_err}", exc_info=True)

        # Send notification emails to VizBriz and clinic
        try:
            # Helper function to get patient initials for privacy
            def get_patient_initials(name):
                """Extract initials from patient name."""
                if not name or name.lower() in ['unknown', 'test patient', 'patient']:
                    return 'N/A'
                # Split name into parts and get first letter of each
                parts = name.strip().split()
                if len(parts) == 0:
                    return 'N/A'
                elif len(parts) == 1:
                    # Only one name part, return first letter
                    return parts[0][0].upper() if parts[0] else 'N/A'
                else:
                    # Multiple parts, get first letter of first and last
                    return (parts[0][0] + parts[-1][0]).upper() if parts[0] and parts[-1] else 'N/A'
            
            # Determine patient and clinic details
            target_quiz = get_quiz_by_id(quiz_id)
            patient_id = target_quiz.user_id if target_quiz else None
            patient = Patient.query.get(patient_id) if patient_id else None
            patient_name_full = (patient.name if patient and getattr(patient, 'name', None) else answers.get('DEMO_FULL_NAME')) or 'Unknown'
            
            # Get patient initials for privacy in emails to clinic/info
            patient_initials = get_patient_initials(patient_name_full)
            patient_identifier = f"{patient_initials} (ID: {patient_id})" if patient_id else patient_initials
            
            # Get patient contact information
            patient_email_address = patient_email or (patient.email if patient and getattr(patient, 'email', None) else 'Not provided')
            patient_phone = answers.get('DEMO_PHONE') or answers.get('PHONE') or (patient.phone if patient and getattr(patient, 'phone', None) else 'Not provided')
            
            clinic_email_target = clinic_email or (Clinic.query.get(clinic_id).email if clinic_id and Clinic.query.get(clinic_id) else None)
            viz_email = current_app.config.get('VIZBRIZ_INFO_EMAIL', 'info@vizbriz.com')
            
            # Build dashboard URL using BASE_URL from environment (set in __init__.py)
            # This ensures it works in both dev and production environments
            # BASE_URL is always set in __init__.py based on ENVIRONMENT variable
            base_url = (
                os.getenv('BASE_URL') or 
                current_app.config.get('BASE_URL') or 
                ''
            ).rstrip('/')
            
            # Always use absolute URL - never use relative path
            # BASE_URL should always be set from __init__.py
            if not base_url:
                current_app.logger.warning("BASE_URL is not set! Using fallback. Check __init__.py configuration.")
                base_url = 'https://app.vizbriz.com'  # Production fallback
            
            dashboard_url = f"{base_url}/unified-dashboard"

            # Embed logo as base64 to ensure it always displays in email (mail server independent)
            # This works in all environments and doesn't require external image loading
            logo_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                'flask_static', 'images', 'logos', 'vizbrizz_logo color without grad.png'
            )
            
            logo_base64 = ''
            if os.path.exists(logo_path):
                try:
                    with open(logo_path, 'rb') as logo_file:
                        logo_data = logo_file.read()
                        logo_base64 = base64.b64encode(logo_data).decode('utf-8')
                        logo_data_uri = f"data:image/png;base64,{logo_base64}"
                except Exception as e:
                    current_app.logger.error(f"Failed to encode logo: {str(e)}")
                    # Fallback to absolute URL if base64 encoding fails
                    # base_url is guaranteed to be set above
                    logo_data_uri = f"{base_url}/flask_static/images/logos/vizbrizz_logo%20color%20without%20grad.png"
            else:
                current_app.logger.warning(f"Logo file not found at {logo_path}, using URL fallback")
                # Fallback to absolute URL if logo file doesn't exist
                # base_url is guaranteed to be set above
                logo_data_uri = f"{base_url}/flask_static/images/logos/vizbrizz_logo%20color%20without%20grad.png"
            
            html = f"""
            <div style='font-family:Segoe UI,Arial,sans-serif; color:#2c3e50;'>
              <div style='text-align:center; margin-bottom:30px; padding:20px 0;'>
                <img src='{logo_data_uri}' alt='VizBriz Logo' style='height:120px; max-width:400px; object-fit:contain; display:block; margin:0 auto;'>
              </div>
              <h2 style='margin:0 0 8px 0;'>New VizBriz OSA Quiz Submitted</h2>
              <div style='margin:0 0 18px 0; padding:12px; background:#f8f9fa; border-radius:6px;'>
                <p style='margin:0 0 6px 0;'><strong>Patient:</strong> {patient_identifier}</p>
                <p style='margin:0 0 6px 0;'><strong>Email:</strong> {patient_email_address}</p>
                <p style='margin:0 0 0 0;'><strong>Phone:</strong> {patient_phone}</p>
              </div>
              <p style='margin:0 0 18px 0;'>Risk: <strong>{evaluation_result['risk_band'].upper()}</strong> • Score: <strong>{evaluation_result['total_score']}</strong></p>
              <a href='{dashboard_url}' style='display:inline-block; background:#2563eb; color:#fff; padding:10px 16px; border-radius:6px; text-decoration:none;'>Open Unified Conversion Dashboard</a>
            </div>
            """
            text = f"New VizBriz OSA quiz submitted.\nPatient: {patient_identifier}\nEmail: {patient_email_address}\nPhone: {patient_phone}\nRisk: {evaluation_result['risk_band'].upper()}\nScore: {evaluation_result['total_score']}\nDashboard: {dashboard_url}"

            from flask_app.routes.file_management_routes import send_email_with_sendgrid
            # Send to VizBriz
            send_email_with_sendgrid(viz_email, 'New VizBriz OSA Quiz Submission', html, text, patient_id=patient_id, email_type='vizbriz_quiz_submission', sender_type='system')
            # Send to clinic if available
            if clinic_email_target:
                send_email_with_sendgrid(clinic_email_target, 'New VizBriz OSA Quiz Submission', html, text, patient_id=patient_id, email_type='vizbriz_quiz_submission', sender_type='system')
        except Exception as em_err:
            current_app.logger.error(f"Quiz submission email send failed for quiz {quiz_id}: {em_err}")

        return jsonify(response), 200
    
    except Exception as e:
        current_app.logger.error(f"Error submitting quiz: {str(e)}")
        import traceback
        current_app.logger.error(traceback.format_exc())
        return jsonify({'error': 'Failed to process quiz submission'}), 500


@vizbriz_quiz.route('/dashboard')
@login_required
def dashboard():
    """
    Admin dashboard for viewing quiz submissions.
    Shows all submissions with filtering and search capabilities.
    """
    # Get query parameters for filtering
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    language_filter = request.args.get('language')
    risk_filter = request.args.get('risk')
    
    # Build query
    query = VizBrizQuiz.query
    
    # Apply filters
    if language_filter:
        query = query.filter_by(language=language_filter)
    if risk_filter:
        query = query.filter_by(risk_band=risk_filter)
    
    # Order by most recent first
    query = query.order_by(VizBrizQuiz.created_at.desc())
    
    # Paginate
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    submissions = pagination.items
    
    # Get statistics
    total_submissions = VizBrizQuiz.query.count()
    high_risk_count = VizBrizQuiz.query.filter_by(risk_band='high').count()
    moderate_risk_count = VizBrizQuiz.query.filter_by(risk_band='moderate').count()
    low_risk_count = VizBrizQuiz.query.filter_by(risk_band='low').count()
    
    stats = {
        'total': total_submissions,
        'high_risk': high_risk_count,
        'moderate_risk': moderate_risk_count,
        'low_risk': low_risk_count,
        'by_language': {
            'en': VizBrizQuiz.query.filter_by(language='en').count(),
            'ru': VizBrizQuiz.query.filter_by(language='ru').count(),
            'he': VizBrizQuiz.query.filter_by(language='he').count()
        }
    }
    
    return render_template(
        'quiz_dashboard.html',
        submissions=submissions,
        pagination=pagination,
        stats=stats,
        language_filter=language_filter,
        risk_filter=risk_filter
    )


@vizbriz_quiz.route('/submission/<int:submission_id>')
@login_required
def view_submission(submission_id):
    """
    View detailed information about a specific quiz submission.
    For Hebrew quizzes, redirect to Level 1 report frame.
    For English/Russian, redirect to dashboard.
    """
    quiz = get_quiz_by_id(submission_id)
    
    if not quiz:
        return jsonify({'error': 'Submission not found'}), 404
    
    # Hebrew quizzes use the Level 1 report frame route
    if quiz.language == 'he':
        from flask import redirect
        return redirect(f'/vizbriz/reports/level1/frame/{submission_id}')
    
    # For English/Russian, redirect to dashboard
    from flask import redirect
    return redirect(f'/vizbriz/dashboard')


@vizbriz_quiz.route('/export_csv')
@login_required
def export_csv():
    """
    Export all quiz submissions to CSV file.
    """
    try:
        # Get all submissions
        submissions = VizBrizQuiz.query.order_by(VizBrizQuiz.created_at.desc()).all()
        
        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow([
            'ID', 'Date', 'Patient Email', 'Language', 'Total Score', 
            'Risk Band', 'Red Flags', 'Outcome', 'Clinic', 'Referral Doctor'
        ])
        
        # Write data
        for submission in submissions:
            red_flags_str = ', '.join(submission.red_flags) if submission.red_flags else ''
            writer.writerow([
                submission.id,
                submission.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                submission.patient_email,
                submission.language,
                submission.total_score,
                submission.risk_band,
                red_flags_str,
                submission.outcome_message_id,
                submission.clinic_email or '',
                submission.referral_doctor or ''
            ])
        
        # Prepare response
        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8-sig')),  # UTF-8 with BOM for Excel
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'vizbriz_quiz_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        )
    
    except Exception as e:
        current_app.logger.error(f"Error exporting CSV: {str(e)}")
        return jsonify({'error': 'Failed to export data'}), 500


@vizbriz_quiz.route('/pdf/<int:quiz_id>')
@login_required
def generate_pdf(quiz_id):
    """
    Generate PDF report for a quiz submission.
    """
    try:
        quiz = get_quiz_by_id(quiz_id)
        if not quiz:
            return jsonify({'error': 'Quiz not found'}), 404
        
        # Parse answers
        answers = json.loads(quiz.quiz_input) if quiz.quiz_input else {}
        
        # Get patient
        patient = Patient.query.get(quiz.user_id) if quiz.user_id else None
        
        # Load quiz package
        quiz_package = load_quiz_package()
        
        # Create PDF in memory using HTML-to-PDF for better Unicode support
        # PDFs are always generated in English regardless of quiz language
        buffer = io.BytesIO()
        pdf_language = 'en'
        
        # Helper function to escape HTML properly while preserving Unicode
        def escape_html(text):
            """Escape HTML special characters while preserving Unicode characters"""
            if text is None:
                return ''
            # Convert to string, preserving Unicode
            text_str = str(text)
            # Only escape HTML special characters, preserve all Unicode including Hebrew
            text_str = text_str.replace('&', '&amp;')
            text_str = text_str.replace('<', '&lt;')
            text_str = text_str.replace('>', '&gt;')
            text_str = text_str.replace('"', '&quot;')
            # Don't escape single quotes - they're fine in HTML attributes
            return text_str
        
        # Register Unicode fonts FIRST, before building HTML
        # Use @font-face with file:/// URL approach for better Unicode support
        font_name = 'DejaVuSans'
        font_path = None
        try:
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            
            font_paths = [
                '/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf',
                '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                '/usr/share/fonts/dejavu/DejaVuSans.ttf',
            ]
            
            for path in font_paths:
                if os.path.exists(path):
                    try:
                        # Register the font with ReportLab
                        pdfmetrics.registerFont(TTFont('DejaVuSans', path))
                        font_path = path
                        current_app.logger.info(f"Registered DejaVu Sans font from: {path}")
                        
                        # Optional: Register fallback Unicode font
                        try:
                            pdfmetrics.registerFont(UnicodeCIDFont('HeiseiMin-W3'))
                            current_app.logger.info("Registered fallback Unicode font")
                        except:
                            pass
                        break
                    except Exception as font_err:
                        current_app.logger.warning(f"Could not register font from {path}: {font_err}")
                        continue
        except Exception as font_reg_err:
            current_app.logger.warning(f"Font registration failed: {font_reg_err}")
            font_name = 'Helvetica'  # Fallback
        
        # Get enhanced answers data
        enhanced_answers_data = None
        if quiz.ai_response:
            try:
                ai_data = json.loads(quiz.ai_response) if isinstance(quiz.ai_response, str) else quiz.ai_response
                if isinstance(ai_data, dict) and 'enhanced_answers' in ai_data:
                    enhanced_answers_data = ai_data['enhanced_answers']
                elif isinstance(ai_data, dict) and 'questions_and_answers' in ai_data:
                    enhanced_answers_data = ai_data
            except:
                pass
        
        # Build HTML content with @font-face for explicit font loading
        font_face_css = ''
        if font_path:
            font_face_css = f"""
            @font-face {{
                font-family: "{font_name}";
                src: url("file://{font_path}");
            }}
            """
        
        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <style>
                {font_face_css}
                @page {{
                    size: A4;
                    margin: 2cm;
                }}
                body {{
                    font-family: "{font_name}", Arial, sans-serif;
                    font-size: 11pt;
                    line-height: 1.6;
                    color: #333;
                    direction: ltr;
                }}
                /* Support for RTL languages like Hebrew */
                .rtl {{
                    direction: rtl;
                    text-align: right;
                    font-family: "{font_name}", Arial, sans-serif;
                }}
                /* Ensure Unicode characters are preserved */
                * {{
                    unicode-bidi: embed;
                    font-family: "{font_name}", Arial, sans-serif;
                }}
                /* Force Unicode font for all text */
                p, div, span, td, th {{
                    font-family: "{font_name}", Arial, sans-serif;
                }}
                h1 {{
                    color: #2c3e50;
                    border-bottom: 3px solid #3498db;
                    padding-bottom: 10px;
                    margin-bottom: 20px;
                }}
                h2 {{
                    color: #34495e;
                    margin-top: 25px;
                    margin-bottom: 15px;
                }}
                .info-row {{
                    margin-bottom: 10px;
                }}
                .label {{
                    font-weight: bold;
                    color: #555;
                }}
                .qa-section {{
                    margin-top: 30px;
                }}
                .qa-item {{
                    margin-bottom: 20px;
                    padding: 10px;
                    background-color: #f8f9fa;
                    border-left: 4px solid #3498db;
                }}
                .question {{
                    font-weight: bold;
                    margin-bottom: 8px;
                    color: #2c3e50;
                }}
                .answer {{
                    color: #555;
                    white-space: pre-wrap;
                    word-wrap: break-word;
                    unicode-bidi: embed;
                    font-family: "{font_name}", Arial, sans-serif;
                }}
                /* Detect and style Hebrew/RTL text */
                .answer:lang(he), .answer[dir="rtl"] {{
                    direction: rtl;
                    text-align: right;
                    font-family: "{font_name}", Arial, sans-serif;
                }}
                .results-box {{
                    background-color: #e8f4fd;
                    border: 1px solid #3498db;
                    padding: 15px;
                    margin: 20px 0;
                    border-radius: 5px;
                }}
            </style>
        </head>
        <body>
            <h1>{escape_html(get_localized_text('app.title', pdf_language, quiz_package))}</h1>
            
            <div class="info-row">
                <span class="label">{escape_html(get_localized_text('Q.full_name.title', pdf_language, quiz_package))}:</span>
                <span class="answer">{escape_html(patient.name if patient else 'N/A')}</span>
            </div>
            <div class="info-row">
                <span class="label">{escape_html(get_localized_text('Q.email.title', pdf_language, quiz_package))}:</span>
                <span>{escape_html(patient.email if patient else quiz.patient_email)}</span>
            </div>
            <div class="info-row">
                <span class="label">Date:</span>
                <span>{quiz.created_at.strftime('%Y-%m-%d %H:%M')}</span>
            </div>
            
            <div class="results-box">
                <h2>Results</h2>
                <div class="info-row">
                    <span class="label">Total Score:</span>
                    <span>{quiz.total_score}</span>
                </div>
                <div class="info-row">
                    <span class="label">Risk Level:</span>
                    <span>{quiz.risk_band.upper()}</span>
                </div>
        """
        
        if quiz.red_flags:
            red_flags_text = ', '.join([escape_html(flag) for flag in quiz.red_flags])
            html_content += f"""
                <div class="info-row">
                    <span class="label">Red Flags:</span>
                    <span>{red_flags_text}</span>
                </div>
            """
        
        html_content += """
            </div>
        """
        
        # Outcome message
        outcome_title = get_localized_text(f"{quiz.outcome_message_id}.title", pdf_language, quiz_package)
        outcome_body = get_localized_text(f"{quiz.outcome_message_id}.body", pdf_language, quiz_package)
        
        html_content += f"""
            <h2>{escape_html(outcome_title)}</h2>
            <p>{escape_html(outcome_body)}</p>
        """
        
        # Questions and Answers section
        if answers:
            html_content += """
            <div class="qa-section">
                <h2>Questions and Answers</h2>
            """
            
            if enhanced_answers_data and 'questions_and_answers' in enhanced_answers_data:
                qa_data = enhanced_answers_data['questions_and_answers']
                for qa in qa_data:
                    question_text = qa.get('question_text', '')
                    answer_text = qa.get('user_answer', '')
                    if question_text and answer_text:
                        html_content += f"""
                <div class="qa-item">
                    <div class="question">{escape_html(question_text)}</div>
                    <div class="answer">{escape_html(answer_text)}</div>
                </div>
                        """
            else:
                # Fallback: display raw answers
                for qid, answer in answers.items():
                    if qid.startswith('_') or qid.endswith('_other_text'):
                        continue
                    question = next((q for q in quiz_package.get('questions', []) if q.get('qid') == qid), None)
                    if question and answer:
                        question_text = question.get('title_en', question.get('title', qid))
                        html_content += f"""
                <div class="qa-item">
                    <div class="question">{escape_html(question_text)}</div>
                    <div class="answer">{escape_html(str(answer))}</div>
                </div>
                        """
            
            html_content += """
            </div>
            """
        
        html_content += """
        </body>
        </html>
        """
        
        # Convert HTML to PDF using xhtml2pdf with proper Unicode support
        try:
            # Encode HTML to UTF-8 bytes as per xhtml2pdf best practices
            # This ensures proper Unicode character handling
            if isinstance(html_content, str):
                html_content_bytes = html_content.encode('utf-8')
            else:
                html_content_bytes = html_content
            
            # Font is already registered above, HTML uses @font-face with file:/// URL
            # Create PDF with Unicode support
            # xhtml2pdf uses ReportLab under the hood
            pisa_status = pisa.CreatePDF(
                src=html_content_bytes,
                dest=buffer,
                encoding='utf-8',
                show_error_as_pdf=False,
                link_callback=None
            )
            
            if pisa_status.err:
                current_app.logger.error(f"Error creating PDF: {pisa_status.err}")
                raise Exception(f"PDF generation failed: {pisa_status.err}")
        except Exception as e:
            current_app.logger.error(f"Error in HTML to PDF conversion: {str(e)}")
            # Fallback to simple text-based PDF if HTML conversion fails
            raise e
        
        buffer.seek(0)
        
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'vizbriz_quiz_{quiz_id}.pdf'
        )
    
    except Exception as e:
        current_app.logger.error(f"Error generating PDF: {str(e)}")
        import traceback
        current_app.logger.error(traceback.format_exc())
        return jsonify({'error': 'Failed to generate PDF'}), 500


@vizbriz_quiz.route('/api/quiz-package')
def api_quiz_package():
    """
    API endpoint to retrieve the quiz package JSON.
    Used by frontend to render the quiz dynamically.
    """
    try:
        language = request.args.get('lang', 'en')
        quiz_package = load_quiz_package()
        
        return jsonify({
            'success': True,
            'quiz_package': quiz_package,
            'language': language
        })
    except Exception as e:
        current_app.logger.error(f"Error loading quiz package: {str(e)}")
        return jsonify({'error': 'Failed to load quiz package'}), 500


@vizbriz_quiz.route('/analytics')
@login_required
def analytics():
    """
    Analytics page showing quiz statistics and trends.
    """
    # Get date range from query params
    from datetime import timedelta
    days = request.args.get('days', 30, type=int)
    start_date = datetime.utcnow() - timedelta(days=days)
    
    # Get submissions in date range
    submissions = VizBrizQuiz.query.filter(VizBrizQuiz.created_at >= start_date).all()
    
    # Calculate analytics
    total_count = len(submissions)
    
    # Risk distribution
    risk_distribution = {
        'high': len([s for s in submissions if s.risk_band == 'high']),
        'moderate': len([s for s in submissions if s.risk_band == 'moderate']),
        'low': len([s for s in submissions if s.risk_band == 'low'])
    }
    
    # Language distribution
    language_distribution = {
        'en': len([s for s in submissions if s.language == 'en']),
        'ru': len([s for s in submissions if s.language == 'ru']),
        'he': len([s for s in submissions if s.language == 'he'])
    }
    
    # Average scores
    if submissions:
        avg_score = sum(s.total_score for s in submissions if s.total_score) / len(submissions)
    else:
        avg_score = 0
    
    # Red flags frequency
    red_flag_counts = {}
    for submission in submissions:
        if submission.red_flags:
            for flag in submission.red_flags:
                red_flag_counts[flag] = red_flag_counts.get(flag, 0) + 1
    
    analytics_data = {
        'total_submissions': total_count,
        'date_range_days': days,
        'risk_distribution': risk_distribution,
        'language_distribution': language_distribution,
        'average_score': round(avg_score, 2),
        'red_flag_counts': red_flag_counts
    }
    
    return render_template('vizbriz_analytics.html', analytics=analytics_data)


# Health check endpoint
@vizbriz_quiz.route('/health')
def health_check():
    """Health check endpoint for monitoring"""
    return jsonify({
        'status': 'healthy',
        'service': 'vizbriz_quiz',
        'timestamp': datetime.utcnow().isoformat()
    })


def generate_default_quiz_answers(quiz_package, language='en'):
    """
    Generate default answers for all quiz questions.
    Used for automated testing to skip manual form filling.
    """
    answers = {}
    questions = quiz_package.get('questions', [])
    
    # First pass: set basic answers
    for question in questions:
        qid = question.get('qid')
        qtype = question.get('type')
        
        # Skip hidden questions
        if question.get('display_if') and question.get('display_if').get('expr') == 'false':
            continue
        
        # Skip age - it's calculated from DOB
        if qid == 'DEMO_AGE':
            continue
        
        if qtype == 'text' or qtype == 'email' or qtype == 'tel' or qtype == 'number':
            if qid == 'DEMO_FULL_NAME':
                answers[qid] = 'Test Patient'
            elif qid == 'DEMO_EMAIL':
                answers[qid] = 'eran@vizbriz.com'
            elif qid == 'DEMO_PHONE':
                answers[qid] = '050-1234567'
            elif qid == 'DEMO_DOB':
                # Set DOB to make age ~40
                from datetime import datetime, timedelta
                dob = datetime.now() - timedelta(days=40*365)
                answers[qid] = dob.strftime('%Y-%m-%d')
            elif qid == 'DEMO_HEIGHT_CM':
                answers[qid] = '175'
            elif qid == 'DEMO_WEIGHT_KG':
                answers[qid] = '80'
            elif qid == 'Q16':  # Sleep latency
                answers[qid] = '30'
            elif qid == 'Q17':  # Sleep hours
                answers[qid] = '7'
            elif qid == 'Q8a' or qid == 'Q9a' or qid == 'Q10a' or qid == 'Q13a' or qid == 'Q32a' or qid == 'Q38a' or qid == 'Q38b':
                answers[qid] = 'Test response'
            else:
                answers[qid] = 'Test answer'
        
        elif qtype == 'date':
            if qid == 'DEMO_DOB':
                from datetime import datetime, timedelta
                dob = datetime.now() - timedelta(days=40*365)
                answers[qid] = dob.strftime('%Y-%m-%d')
            elif qid == 'Q6a':
                from datetime import datetime, timedelta
                sleep_study_date = datetime.now() - timedelta(days=365)
                answers[qid] = sleep_study_date.strftime('%Y-%m-%d')
            else:
                from datetime import datetime
                answers[qid] = datetime.now().strftime('%Y-%m-%d')
        
        elif qtype == 'single_choice':
            options = question.get('options', [])
            if options:
                # Pick first non-empty option
                for opt in options:
                    if opt.get('value') and opt.get('value') not in ['none', 'prefer_not_to_say']:
                        answers[qid] = opt.get('value')
                        break
                # Fallback to first option if none found
                if qid not in answers and options:
                    answers[qid] = options[0].get('value', 'yes')
            else:
                answers[qid] = 'yes'
        
        elif qtype == 'multi_choice':
            options = question.get('options', [])
            if options:
                # Pick first 2-3 options (avoid 'none' and 'other' if possible)
                selected = []
                for opt in options:
                    val = opt.get('value')
                    if val and val not in ['none', 'other'] and len(selected) < 2:
                        selected.append(val)
                if not selected and options:
                    selected = [options[0].get('value')]
                answers[qid] = selected
            else:
                answers[qid] = ['yes']
        
        elif qtype == 'scale':
            # Pick middle value (3) for scales
            answers[qid] = '3'
    
    # Second pass: handle conditional questions based on answers set in first pass
    for question in questions:
        qid = question.get('qid')
        display_if = question.get('display_if')
        
        if not display_if or not display_if.get('expr'):
            continue
        
        expr = display_if.get('expr')
        # Check if this question should be shown based on previous answers
        if 'ANS.Q1' in expr and answers.get('Q1') == 'yes':
            # Q2, Q3, Q4, Q6, Q6a depend on Q1
            if qid == 'Q2':
                answers[qid] = 'yes'  # Currently receiving treatment
            elif qid == 'Q3' and answers.get('Q2') == 'yes':
                answers[qid] = 'oral_appliance'  # Type of treatment
            elif qid == 'Q4':
                answers[qid] = 'no'  # No surgery
            elif qid == 'Q6':
                answers[qid] = 'date'  # Can provide date
            elif qid == 'Q6a' and answers.get('Q6') == 'date':
                from datetime import datetime, timedelta
                sleep_study_date = datetime.now() - timedelta(days=365)
                answers[qid] = sleep_study_date.strftime('%Y-%m-%d')
        elif 'ANS.Q2' in expr and answers.get('Q2') == 'yes' and qid == 'Q3':
            answers[qid] = 'oral_appliance'
        elif 'ANS.Q6' in expr and answers.get('Q6') == 'date' and qid == 'Q6a':
            from datetime import datetime, timedelta
            sleep_study_date = datetime.now() - timedelta(days=365)
            answers[qid] = sleep_study_date.strftime('%Y-%m-%d')
        elif 'ANS.Q8' in expr and 'other' in str(answers.get('Q8', [])) and qid == 'Q8a':
            answers[qid] = 'Test other condition'
        elif 'ANS.Q9' in expr and answers.get('Q9') == 'yes' and qid == 'Q9a':
            answers[qid] = 'Test medications'
        elif 'ANS.Q10' in expr and answers.get('Q10') == 'yes' and qid == 'Q10a':
            answers[qid] = 'Test allergies'
        elif 'ANS.Q13' in expr and answers.get('Q13') == 'yes' and qid == 'Q13a':
            answers[qid] = 'Test sedatives'
        elif 'ANS.Q32' in expr and 'other' in str(answers.get('Q32', [])) and qid == 'Q32a':
            answers[qid] = 'Test other symptoms'
        elif 'ANS.Q38' in expr and 'other' in str(answers.get('Q38', [])) and qid == 'Q38a':
            answers[qid] = 'Test other goals'
    
    # Ensure required fields are set
    if 'DEMO_REFERRING_DENTIST_OR_CLI' not in answers:
        # Get first clinic ID if available
        clinic = Clinic.query.first()
        if clinic:
            answers['DEMO_REFERRING_DENTIST_OR_CLI'] = str(clinic.id)
        else:
            answers['DEMO_REFERRING_DENTIST_OR_CLI'] = '1'
    
    # Map DEMO_EMAIL to EMAIL for backend compatibility
    if 'DEMO_EMAIL' in answers:
        answers['EMAIL'] = answers['DEMO_EMAIL']
    
    # Calculate age from DOB
    if 'DEMO_DOB' in answers:
        try:
            from datetime import datetime
            dob = datetime.strptime(answers['DEMO_DOB'], '%Y-%m-%d')
            age = (datetime.now() - dob).days // 365
            answers['DEMO_AGE'] = str(age)
        except:
            pass
    
    return answers


def create_enhanced_answers_from_defaults(answers, quiz_package, language='en'):
    """
    Create enhanced_answers structure from default answers.
    Mimics what the frontend createsEnhancedAnswers() function does.
    """
    enhanced = {
        'submission_info': {
            'timestamp': datetime.utcnow().isoformat(),
            'language': language,
            'total_questions_answered': len([k for k in answers.keys() if not k.endswith('_other_text')]),
            'patient_id': 0
        },
        'questions_and_answers': []
    }
    
    questions = quiz_package.get('questions', [])
    for question in questions:
        qid = question.get('qid')
        if qid not in answers:
            continue
        
        user_answer = answers[qid]
        if user_answer is None or user_answer == '':
            continue
        
        # Get English question text
        question_text = question.get('title_en') or question.get('title', qid)
        
        # Format answer based on type
        formatted_answer = ''
        if question.get('type') == 'single_choice':
            option = next((opt for opt in (question.get('options') or []) if opt.get('value') == user_answer), None)
            if option:
                formatted_answer = option.get('label') or option.get('label_en', user_answer)
        elif question.get('type') == 'multi_choice':
            if isinstance(user_answer, list):
                options = question.get('options', [])
                labels = []
                for val in user_answer:
                    option = next((opt for opt in options if opt.get('value') == val), None)
                    if option:
                        labels.append(option.get('label') or option.get('label_en', val))
                formatted_answer = ', '.join(labels) if labels else str(user_answer)
            else:
                formatted_answer = str(user_answer)
        else:
            formatted_answer = str(user_answer)
        
        enhanced['questions_and_answers'].append({
            'question_id': qid,
            'question_text': question_text,
            'question_type': question.get('type'),
            'section': question.get('section', 'General'),
            'user_answer': formatted_answer,
            'raw_answer': user_answer
        })
    
    return enhanced


@vizbriz_quiz.route('/quiz/test', methods=['GET'])
@login_required
def quiz_test_page():
    """
    Test UI page for automated quiz submission.
    Allows quick testing without going through all questions.
    """
    return render_template('vizbriz_quiz_test.html')


@vizbriz_quiz.route('/quiz/test-submit', methods=['POST'])
@login_required
def quiz_test_submit():
    """
    Automated quiz submission endpoint for testing.
    Generates default answers and submits the quiz, then redirects to results.
    """
    try:
        data = request.get_json(silent=True) or {}
        language = data.get('language', 'en')
        clinic_id = data.get('clinic_id')
        risk_scenario = data.get('risk_scenario', 'moderate')  # 'low', 'moderate', 'high'
        
        # Load quiz package
        quiz_package = load_quiz_package()
        
        # Generate default answers
        answers = generate_default_quiz_answers(quiz_package, language)
        
        # Override answers based on risk scenario
        if risk_scenario == 'high':
            answers['Q1'] = 'yes'  # Diagnosed
            answers['Q2'] = 'no'  # Not treated
            answers['Q19'] = '5'  # Always tired
            answers['Q20'] = '5'  # Always gasping
            answers['Q24'] = 'yes'  # Trouble staying awake
            answers['Q8'] = ['high_blood_pressure', 'diabetes']  # Comorbidities
        elif risk_scenario == 'low':
            answers['Q1'] = 'no'  # Not diagnosed
            answers['Q19'] = '1'  # Never tired
            answers['Q20'] = '1'  # Never gasping
            answers['Q24'] = 'no'  # No trouble staying awake
            answers['Q8'] = ['none']  # No comorbidities
        else:  # moderate (default)
            answers['Q1'] = 'no'  # Not diagnosed
            answers['Q19'] = '3'  # Sometimes tired
            answers['Q20'] = '3'  # Sometimes gasping
            answers['Q24'] = 'no'  # No trouble staying awake
            answers['Q8'] = ['high_blood_pressure']  # One comorbidity
        
        # Ensure email is set
        answers['DEMO_EMAIL'] = 'eran@vizbriz.com'
        answers['EMAIL'] = 'eran@vizbriz.com'
        
        # Create enhanced answers
        enhanced_answers = create_enhanced_answers_from_defaults(answers, quiz_package, language)
        
        # Use clinic_id from request or default to clinic 35 (DSO 41)
        if not clinic_id:
            clinic = Clinic.query.get(35)
            if clinic:
                clinic_id = 35
                answers['DEMO_REFERRING_DENTIST_OR_CLI'] = '35'
                current_app.logger.info(f"Using default clinic 35 (DSO 41) for test quiz")
            else:
                current_app.logger.warning(f"Clinic 35 not found, using first available clinic")
                clinic = Clinic.query.first()
                if clinic:
                    clinic_id = clinic.id
                    answers['DEMO_REFERRING_DENTIST_OR_CLI'] = str(clinic.id)
        
        # Submit quiz using the same logic as the normal submission endpoint
        # We'll call submit_quiz() logic directly
        from flask_app.helpers.vizbriz_quiz_helpers import evaluate_quiz, save_vizbriz_quiz
        
        # Evaluate quiz
        evaluation_result = evaluate_quiz(answers, language)
        
        # Get clinic email
        clinic_email = None
        if clinic_id:
            clinic = Clinic.query.get(clinic_id)
            if clinic:
                clinic_email = clinic.email
        
        # Save quiz
        quiz_id = save_vizbriz_quiz(
            answers=answers,
            evaluation_result=evaluation_result,
            enhanced_answers=enhanced_answers,
            patient_email='eran@vizbriz.com',
            language=language,
            clinic_email=clinic_email,
            clinic_id=clinic_id,
            referral_doctor=None
        )
        
        # Get the saved quiz object
        quiz = get_quiz_by_id(quiz_id)
        if not quiz:
            return jsonify({
                'success': False,
                'error': 'Failed to retrieve saved quiz'
            }), 500
        
        # Generate LLM narrative for Hebrew (if not already generated)
        if language == 'he' and quiz:
            try:
                from flask_app.helpers.level1_report_hebrew import generate_level1_hebrew_narrative_with_bedrock
                
                quiz_payload = json.loads(quiz.quiz_input or "{}") if quiz.quiz_input else {}
                risk_category = (quiz_payload.get("evaluation_summary") or {}).get("risk_band") or quiz.risk_band or "other"
                
                narrative = generate_level1_hebrew_narrative_with_bedrock(
                    patient_quiz_json=quiz_payload,
                    risk_category=str(risk_category),
                    patient_id=quiz.user_id,
                )
                if narrative:
                    quiz.ai_response = json.dumps({"level1_report_he": narrative}, ensure_ascii=False)
                    db.session.commit()
                    current_app.logger.info(f"LLM narrative generated for test quiz {quiz_id}")
            except Exception as narr_err:
                current_app.logger.error(f"Hebrew narrative generation failed for quiz {quiz_id}: {narr_err}")
        
        # Generate PDF and send email (following full submission process)
        pdf_content = None
        pdf_filename = None
        email_sent = False
        
        # Internal Level-1 PDF (same as production submit flow)
        try:
            from flask_app.helpers.level1_report_hebrew import (
                build_level1_context_from_vizbriz_quiz,
                prepare_context_for_pdf,
                render_level1_report_html,
                html_to_pdf_bytes,
            )

            context = build_level1_context_from_vizbriz_quiz(quiz)
            html = render_level1_report_html(prepare_context_for_pdf(context))
            pdf_content = html_to_pdf_bytes(html)
            pdf_filename = f"Level_1_Report_Quiz_{quiz_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
            current_app.logger.info(
                f"PDF generated for quiz {quiz_id} (lang={language}), size: {len(pdf_content)} bytes"
            )
        except Exception as pdf_err:
            current_app.logger.error(f"PDF generation failed for quiz {quiz_id}: {pdf_err}")
            import traceback
            current_app.logger.error(traceback.format_exc())
        
        # Upload PDF to S3 and send email if PDF was generated
        if pdf_content and quiz.user_id:
            try:
                # Upload internally generated PDF to S3
                if pdf_content:
                    import boto3
                    import io
                    filename = pdf_filename or f"Level_1_Report_Quiz_{quiz_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
                    s3_key = f"patients/{quiz.user_id}/reports/{filename}"

                    s3_client = boto3.client(
                        's3',
                        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
                        region_name=os.getenv('AWS_REGION', 'us-west-2')
                    )

                    bucket_name = os.getenv('S3_BUCKET_NAME')
                    if bucket_name:
                        pdf_file = io.BytesIO(pdf_content)
                        s3_client.upload_fileobj(
                            pdf_file,
                            bucket_name,
                            s3_key,
                            ExtraArgs={'ContentType': 'application/pdf'}
                        )

                        # Save to adminfiles
                        new_admin_file = AdminFile(
                            name=filename,
                            patient_id=quiz.user_id,
                            file_type='application/pdf',
                            file_size=len(pdf_content),
                            s3_key=s3_key,
                            upload_date=datetime.utcnow(),
                            file_category='Level 1 - Screening (Questionnaire Only)',
                            is_public=False
                        )
                        db.session.add(new_admin_file)
                        db.session.commit()
                        current_app.logger.info(f"PDF uploaded to S3 for test quiz {quiz_id}")

                # Send email with PDF
                if pdf_content:
                    # Get clinic and DSO info
                    clinic_name = None
                    clinic_logo_url = None
                    clinic_phone = None
                    dso_name = None
                    dso_logo_url = None
                    dso_id = None
                    
                    if clinic_id:
                        clinic = Clinic.query.get(clinic_id)
                        if clinic:
                            clinic_name = clinic.name
                            clinic_phone = getattr(clinic, 'telephone', None)
                            if hasattr(clinic, 'logo_url') and callable(clinic.logo_url):
                                clinic_logo_url = clinic.logo_url()
                            elif hasattr(clinic, 'logo_url'):
                                clinic_logo_url = clinic.logo_url
                            if clinic.dso_id:
                                dso_id = clinic.dso_id
                                from flask_app.models import DSO
                                dso = DSO.query.get(clinic.dso_id)
                                if dso:
                                    dso_name = dso.name
                                    raw_logo = dso.logo.strip() if hasattr(dso, 'logo') and dso.logo else None
                                    dso_logo_url = raw_logo
                    
                    # Get patient name
                    patient_name_for_email = None
                    try:
                        from flask_app.models import Patient
                        patient = Patient.query.get(quiz.user_id) if quiz.user_id else None
                        if patient and patient.name:
                            patient_name_for_email = patient.name
                        else:
                            quiz_input_json = json.loads(quiz.quiz_input) if quiz.quiz_input else {}
                            raw_answers = quiz_input_json.get('raw_answers', {})
                            quiz_name = raw_answers.get('DEMO_FULL_NAME', '')
                            if quiz_name and quiz_name.lower() not in ['test patient', 'test', 'patient']:
                                patient_name_for_email = quiz_name
                    except Exception:
                        pass
                    
                    email_sent = _send_patient_email_with_pdf(
                        patient_email='eran@vizbriz.com',
                        patient_id=quiz.user_id,
                        pdf_content=pdf_content,
                        pdf_filename=pdf_filename or f"Level_1_Report_Quiz_{quiz_id}.pdf",
                        clinic_name=clinic_name,
                        clinic_logo_url=clinic_logo_url,
                        clinic_phone=clinic_phone,
                        dso_name=dso_name,
                        dso_logo_url=dso_logo_url,
                        dso_id=dso_id,
                        patient_name=patient_name_for_email,
                        evaluation_result=evaluation_result,
                        language=language
                    )
                    
                    if email_sent:
                        current_app.logger.info(f"✅ Email with PDF sent successfully for test quiz {quiz_id}")
                    else:
                        current_app.logger.warning(f"⚠️ Email sending failed for test quiz {quiz_id}")
                    
                    # Send clinic notification email (same as submit_quiz)
                    try:
                        from flask_app.routes.file_management_routes import send_email_with_sendgrid
                        
                        patient_identifier = patient_name_for_email or f"Patient ID: {quiz.user_id}"
                        patient_email_address = 'eran@vizbriz.com'
                        patient_phone = "N/A"
                        
                        base_url = os.environ.get('BASE_URL', current_app.config.get('BASE_URL', 'https://app.vizbriz.com')).rstrip('/')
                        dashboard_url = f"{base_url}/unified-dashboard"
                        
                        html = f"""
                        <div style='font-family:Segoe UI,Arial,sans-serif; color:#2c3e50;'>
                          <h2 style='margin:0 0 8px 0;'>New VizBriz OSA Quiz Submitted</h2>
                          <div style='margin:0 0 18px 0; padding:12px; background:#f8f9fa; border-radius:6px;'>
                            <p style='margin:0 0 6px 0;'><strong>Patient:</strong> {patient_identifier}</p>
                            <p style='margin:0 0 6px 0;'><strong>Email:</strong> {patient_email_address}</p>
                            <p style='margin:0 0 0 0;'><strong>Phone:</strong> {patient_phone}</p>
                          </div>
                          <p style='margin:0 0 18px 0;'>Risk: <strong>{evaluation_result['risk_band'].upper()}</strong> • Score: <strong>{evaluation_result['total_score']}</strong></p>
                          <a href='{dashboard_url}' style='display:inline-block; background:#2563eb; color:#fff; padding:10px 16px; border-radius:6px; text-decoration:none;'>Open Unified Conversion Dashboard</a>
                        </div>
                        """
                        text = f"New VizBriz OSA quiz submitted.\nPatient: {patient_identifier}\nEmail: {patient_email_address}\nPhone: {patient_phone}\nRisk: {evaluation_result['risk_band'].upper()}\nScore: {evaluation_result['total_score']}\nDashboard: {dashboard_url}"
                        
                        if clinic_email:
                            send_email_with_sendgrid(
                                clinic_email, 
                                'New VizBriz OSA Quiz Submission', 
                                html, 
                                text, 
                                patient_id=quiz.user_id, 
                                email_type='vizbriz_quiz_submission', 
                                sender_type='system'
                            )
                            current_app.logger.info(f"✅ Clinic notification email sent to {clinic_email} for test quiz {quiz_id}")
                        else:
                            current_app.logger.warning(f"⚠️ No clinic email found for clinic_id {clinic_id}, skipping clinic notification")
                    except Exception as clinic_email_err:
                        current_app.logger.error(f"Error sending clinic notification email for test quiz {quiz_id}: {clinic_email_err}")
                        import traceback
                        current_app.logger.error(traceback.format_exc())
            except Exception as email_err:
                current_app.logger.error(f"Error in PDF upload/email process for test quiz {quiz_id}: {email_err}")
                import traceback
                current_app.logger.error(traceback.format_exc())
        
        # Determine redirect URL based on language
        if language == 'he':
            redirect_url = f'/vizbriz/reports/level1/frame/{quiz_id}'
        else:
            redirect_url = f'/vizbriz/dashboard'
        
        # Return quiz ID and redirect URL
        return jsonify({
            'success': True,
            'quiz_id': quiz_id,
            'redirect_url': redirect_url,
            'evaluation': evaluation_result,
            'language': language,
            'pdf_generated': pdf_content is not None,
            'email_sent': email_sent
        })
        
    except Exception as e:
        current_app.logger.error(f"Error in test quiz submission: {str(e)}")
        import traceback
        current_app.logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

