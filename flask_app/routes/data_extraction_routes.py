"""
Data Extraction Routes
Handles document extraction and observation processing via web interface
"""

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from sqlalchemy import text
from flask_app import db
import logging
import threading
from datetime import datetime
from functools import wraps

logger = logging.getLogger(__name__)

data_extraction_bp = Blueprint('data_extraction', __name__)


def require_api_key_or_login(f):
    """
    Decorator that allows either API key authentication (for Lambda) or login (for UI)
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check for API key first (Lambda calls)
        api_key = request.headers.get('X-API-Key')
        expected_key = current_app.config.get('EXTRACTION_API_KEY')
        
        if api_key and expected_key and api_key == expected_key:
            # API key authentication - allow without login
            return f(*args, **kwargs)
        
        # Fall back to login required
        return login_required(f)(*args, **kwargs)
    
    return decorated_function


@data_extraction_bp.route('/api/data-extraction/process/<int:patient_id>', methods=['POST'])
@require_api_key_or_login
def process_patient_extraction(patient_id):
    """
    Process document extraction for a patient
    This is the web interface to trigger document extraction
    """
    try:
        # Get request parameters
        data = request.get_json() or {}

        # Check if patient exists
        patient_check = db.session.execute(
            text("SELECT id, name, email FROM patients WHERE id = :patient_id"),
            {'patient_id': patient_id}
        ).fetchone()
        
        if not patient_check:
            return jsonify({'success': False, 'message': 'Patient not found'}), 404
        
        # Get request parameters (already loaded above)
        max_documents = data.get('max_documents')  # Optional limit for testing
        batch_size = data.get('batch_size', 3)
        async_mode = data.get('async', True)  # Default to async
        
        # Check if patient is already being processed
        existing_processing = db.session.execute(
            text("""
                SELECT id, status FROM document_processing_queue 
                WHERE patient_id = :patient_id 
                AND status IN ('pending', 'processing')
            """),
            {'patient_id': patient_id}
        ).fetchone()
        
        if existing_processing:
            resp = {
                'success': False,
                'message': f'Patient is already being processed (status: {existing_processing.status})',
                'queue_id': existing_processing.id
            }
            return jsonify(resp), 409
        
        # Check if this is a Lambda call (API key authentication)
        api_key = request.headers.get('X-API-Key')
        is_lambda_call = api_key and api_key == current_app.config.get('EXTRACTION_API_KEY', '')
        
        # Add to queue with error handling for unique constraint
        # Determine source and user_id
        source = 'lambda' if is_lambda_call else 'ui_extraction'
        user_id = None if is_lambda_call else (current_user.id if hasattr(current_user, 'id') else None)
        
        try:
            queue_result = db.session.execute(
                text("""
                    INSERT INTO document_processing_queue 
                    (patient_id, priority, source, requested_by, batch_size, notes, status)
                    VALUES (:patient_id, 10, :source, :user_id, :batch_size, :notes, 'pending')
                """),
                {
                    'patient_id': patient_id,
                    'source': source,
                    'user_id': user_id,
                    'batch_size': batch_size,
                    'notes': 'Triggered from Lambda' if is_lambda_call else 'Triggered from data extraction API'
                }
            )
            db.session.commit()
            queue_id = queue_result.lastrowid
        except Exception as insert_error:
            db.session.rollback()
            error_msg = str(insert_error)
            
            # Handle unique constraint violation (race condition)
            if 'unique_active_patient' in error_msg or 'Duplicate entry' in error_msg:
                # Another request got there first, check what happened
                existing = db.session.execute(
                    text("""
                        SELECT id, status FROM document_processing_queue 
                        WHERE patient_id = :patient_id 
                        AND status IN ('pending', 'processing')
                    """),
                    {'patient_id': patient_id}
                ).fetchone()
                
                if existing:
                    # Another request already added it
                    logger.info(f"Race condition: Patient {patient_id} already in queue (queue_id: {existing.id})")
                    resp = {
                        'success': False,
                        'message': f'Patient is already being processed (status: {existing.status})',
                        'queue_id': existing.id
                    }
                    return jsonify(resp), 409
                else:
                    # Unexpected - re-raise
                    raise
            
            # Other error - re-raise
            raise
        
        # Capture user info for notifications (if from UI, not Lambda)
        if is_lambda_call:
            # Lambda call - use system user
            user_email = current_app.config.get('SYSTEM_EMAIL', 'system@vizbriz.com')
            user_name = "System (Lambda)"
        else:
            # UI call - use logged in user
            user_email = current_user.email if hasattr(current_user, 'email') else None
            user_name = f"{current_user.first_name} {current_user.last_name}" if hasattr(current_user, 'first_name') and current_user.first_name else (current_user.email if user_email else "User")
        
        if async_mode:
            # Capture app reference BEFORE starting thread (critical for app context in threads)
            app = current_app._get_current_object()
            
            # Start async processing in background thread
            def process_extraction():
                try:
                    from flask_app.services.direct_sleep_extraction import (
                        run_direct_sleep_extraction_for_patient,
                    )
                    from flask_app.routes.file_management_routes import send_email_with_sendgrid
                    import signal
                    import threading
                    
                    logger.info(f"Background thread started for patient {patient_id} (queue ID: {queue_id})")
                    
                    # Direct sleep pipeline is much faster than full phase2; allow 90 minutes
                    processing_timeout = 90 * 60  # seconds
                    timeout_occurred = threading.Event()
                    
                    def timeout_handler():
                        """Handle timeout - mark as failed"""
                        timeout_occurred.set()
                        logger.error(f"Processing timeout for patient {patient_id} (queue ID: {queue_id}) after {processing_timeout} seconds")
                        try:
                            with app.app_context():
                                db.session.execute(
                                    text("""
                                        UPDATE document_processing_queue 
                                        SET status = 'failed', completed_at = NOW(),
                                            error_message = :error_msg
                                        WHERE id = :queue_id
                                    """),
                                    {'queue_id': queue_id, 'error_msg': f'Processing timeout after {processing_timeout} seconds'}
                                )
                                db.session.commit()
                        except Exception as timeout_db_error:
                            logger.error(f"Failed to update timeout status: {timeout_db_error}")
                    
                    # Start timeout timer
                    timeout_timer = threading.Timer(processing_timeout, timeout_handler)
                    timeout_timer.start()
                    
                    def update_progress(message):
                        """Update notes field with progress for UI visibility"""
                        try:
                            with app.app_context():
                                from datetime import datetime
                                timestamp = datetime.now().strftime('%H:%M:%S')
                                db.session.execute(
                                    text("""
                                        UPDATE document_processing_queue 
                                        SET notes = CONCAT(COALESCE(notes, ''), :msg)
                                        WHERE id = :queue_id
                                    """),
                                    {'queue_id': queue_id, 'msg': f"[{timestamp}] {message}\n"}
                                )
                                db.session.commit()
                        except Exception as e:
                            logger.warning(f"Could not update progress: {e}")
                    
                    try:
                        with app.app_context():
                            # Update status to processing
                            db.session.execute(
                                text("UPDATE document_processing_queue SET status = 'processing', started_at = NOW(), notes = '' WHERE id = :queue_id"),
                                {'queue_id': queue_id}
                            )
                            db.session.commit()
                            
                            logger.info(f"Starting extraction for patient {patient_id} (queue ID: {queue_id})")
                            update_progress(f"Started processing for patient {patient_id}")
                            
                            # Check if timeout occurred before starting
                            if timeout_occurred.is_set():
                                logger.warning(f"Timeout occurred before processing started for patient {patient_id}")
                                return
                            
                            # Direct sleep-study Bedrock analysis only (no full phase2 extract)
                            logger.info(
                                "Calling run_direct_sleep_extraction_for_patient for patient %s",
                                patient_id,
                            )
                            result = run_direct_sleep_extraction_for_patient(
                                patient_id,
                                progress_callback=update_progress,
                            )
                            logger.info(
                                "run_direct_sleep_extraction_for_patient returned for patient %s: %s",
                                patient_id,
                                result,
                            )
                            success = result.get("queue_outcome") == "completed"
                            sp = result.get("sleep_pipeline") or {}
                            update_progress("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                            update_progress(
                                "✅ DIRECT SLEEP ANALYSIS COMPLETE"
                                if success
                                else "❌ DIRECT SLEEP ANALYSIS DID NOT COMPLETE"
                            )
                            update_progress(
                                f"📄 Sleep-like files: {sp.get('files_considered', 0)}"
                            )
                            update_progress(
                                f"📊 Processed / skipped / failed: {sp.get('processed', 0)} / "
                                f"{sp.get('skipped', 0)} / {sp.get('failed', 0)}"
                            )
                            if result.get("canonical_refreshed"):
                                update_progress("📋 Minimal canonical JSON refreshed")
                            update_progress(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                            
                            # Cancel timeout if processing completed
                            timeout_timer.cancel()
                            
                            # Check if timeout occurred during processing
                            if timeout_occurred.is_set():
                                logger.warning(f"Timeout occurred during processing for patient {patient_id}")
                                return
                            
                            # Update status based on direct sleep pipeline outcome
                            logger.info(
                                "Processing result for patient %s: queue_outcome=%s",
                                patient_id,
                                result.get("queue_outcome"),
                            )

                            # Use WHERE status = 'processing' to avoid unique constraint issues
                            try:
                                if success:
                                    logger.info(f"Marking patient {patient_id} as completed")
                                    update_result = db.session.execute(
                                        text("""
                                            UPDATE document_processing_queue 
                                            SET status = 'completed', completed_at = NOW() 
                                            WHERE id = :queue_id
                                            AND status = 'processing'
                                        """),
                                        {'queue_id': queue_id}
                                    )
                                    if update_result.rowcount > 0:
                                        status = 'completed'
                                        success = True
                                        db.session.commit()
                                    else:
                                        # Entry was already updated or deleted - check current status
                                        current = db.session.execute(
                                            text("SELECT status FROM document_processing_queue WHERE id = :queue_id"),
                                            {'queue_id': queue_id}
                                        ).fetchone()
                                        if current:
                                            logger.info(f"Queue entry {queue_id} already {current.status} - skipping update")
                                            status = current.status
                                            success = (status == 'completed')
                                        else:
                                            logger.warning(f"Queue entry {queue_id} doesn't exist - assuming success")
                                            status = 'completed'
                                            success = True
                                else:
                                    update_result = db.session.execute(
                                        text("""
                                            UPDATE document_processing_queue 
                                            SET status = 'failed', completed_at = NOW(),
                                                error_message = :error_msg
                                            WHERE id = :queue_id
                                            AND status = 'processing'
                                        """),
                                        {
                                            'queue_id': queue_id,
                                            'error_msg': (
                                                result.get("queue_message")
                                                or "Direct sleep analysis did not complete"
                                            )[:1000],
                                        },
                                    )
                                    if update_result.rowcount > 0:
                                        status = 'failed'
                                        success = False
                                        db.session.commit()
                                    else:
                                        # Entry was already updated
                                        current = db.session.execute(
                                            text("SELECT status FROM document_processing_queue WHERE id = :queue_id"),
                                            {'queue_id': queue_id}
                                        ).fetchone()
                                        if current:
                                            logger.info(f"Queue entry {queue_id} already {current.status}")
                                            status = current.status
                                            success = False
                                        else:
                                            logger.warning(f"Queue entry {queue_id} doesn't exist")
                                            status = 'failed'
                                            success = False
                            except Exception as update_error:
                                db.session.rollback()
                                error_msg = str(update_error)
                                if 'unique_active_patient' in error_msg or 'Duplicate entry' in error_msg:
                                    # Unique constraint violation - there's already a completed/failed entry for this patient
                                    # Delete the current processing entry since patient already has final status
                                    logger.info(f"Unique constraint: Patient {patient_id} already has a completed/failed entry")
                                    try:
                                        db.session.execute(
                                            text("DELETE FROM document_processing_queue WHERE id = :queue_id"),
                                            {'queue_id': queue_id}
                                        )
                                        db.session.commit()
                                        logger.info(f"Deleted duplicate queue entry {queue_id} for patient {patient_id}")
                                        status = 'completed'
                                        success = True
                                    except Exception as delete_error:
                                        logger.error(f"Failed to delete duplicate entry: {delete_error}")
                                        db.session.rollback()
                                        status = 'completed'
                                        success = True
                                else:
                                    raise
                            
                            # Send email notification
                            if user_email:
                                try:
                                    patient_name = patient_check.name if patient_check.name else f"Patient {patient_id}"
                                    
                                    if success:
                                        sp = result.get("sleep_pipeline") or {}
                                        subject = f"Sleep study analysis complete - Patient {patient_id}"
                                        html_content = f"""
                                        <html>
                                        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                                            <h2 style="color: #059669;">Sleep study analysis complete</h2>
                                            <p>Dear {user_name},</p>
                                            <p>Direct model analysis finished for {patient_name} (Patient ID: {patient_id}).</p>
                                            <p><strong>Summary:</strong></p>
                                            <ul>
                                                <li>Sleep-like files considered: {sp.get('files_considered', 0)}</li>
                                                <li>Newly analyzed: {sp.get('processed', 0)}</li>
                                                <li>Already up to date (skipped): {sp.get('skipped', 0)}</li>
                                                <li>Failed files: {sp.get('failed', 0)}</li>
                                                <li>Minimal canonical refreshed: {'yes' if result.get('canonical_refreshed') else 'no'}</li>
                                            </ul>
                                            <p>{result.get('queue_message', '')}</p>
                                            <p>You can view updated sleep metrics in the patient workflow.</p>
                                            <p style="margin-top: 20px; color: #666; font-size: 12px;">This is an automated notification from VizBriz.</p>
                                        </body>
                                        </html>
                                        """
                                    else:
                                        subject = f"Sleep study analysis failed - Patient {patient_id}"
                                        html_content = f"""
                                        <html>
                                        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                                            <h2 style="color: #dc2626;">Sleep study analysis failed</h2>
                                            <p>Dear {user_name},</p>
                                            <p>Direct model analysis for {patient_name} (Patient ID: {patient_id}) did not complete successfully.</p>
                                            <p>{result.get('queue_message', 'See processing queue for details.')}</p>
                                            <p style="margin-top: 20px; color: #666; font-size: 12px;">This is an automated notification from VizBriz.</p>
                                        </body>
                                        </html>
                                        """
                                    
                                    send_email_with_sendgrid(
                                        user_email,
                                        subject,
                                        html_content,
                                        text_content=html_content,
                                        patient_id=patient_id,
                                        email_type='document_extraction_notification',
                                        sender_type='system'
                                    )
                                    logger.info(f"Sent extraction notification email to {user_email}")
                                except Exception as email_error:
                                    logger.error(f"Failed to send notification email: {email_error}")
                            
                            logger.info(f"✅ Completed extraction for patient {patient_id}: status={status}, queue_id={queue_id}")
                            logger.info(f"Final result stats: {result}")
                    
                    except Exception as processing_error:
                        # Cancel timeout on error
                        timeout_timer.cancel()
                        raise processing_error
                        
                except Exception as e:
                    logger.error(f"Error in extraction processing for patient {patient_id}: {e}", exc_info=True)
                    import traceback
                    error_trace = traceback.format_exc()
                    logger.error(f"Full traceback: {error_trace}")
                    try:
                        with app.app_context():
                            db.session.execute(
                                text("""
                                    UPDATE document_processing_queue 
                                    SET status = 'failed', completed_at = NOW(),
                                        error_message = :error_msg
                                    WHERE id = :queue_id
                                """),
                                {'queue_id': queue_id, 'error_msg': str(e)[:1000]}
                            )
                            db.session.commit()
                        logger.info(f"Updated queue entry {queue_id} to failed status")
                    except Exception as db_error:
                        logger.error(f"Failed to update queue status: {db_error}")
                        # Try one more time with a new session
                        try:
                            from flask_app import db as db_new
                            with app.app_context():
                                db_new.session.execute(
                                    text("""
                                        UPDATE document_processing_queue 
                                        SET status = 'failed', completed_at = NOW(),
                                            error_message = :error_msg
                                        WHERE id = :queue_id
                                    """),
                                    {'queue_id': queue_id, 'error_msg': f'Processing error: {str(e)[:500]}'}
                                )
                                db_new.session.commit()
                        except:
                            logger.error("Could not update queue status even with new session")
            
            # Start background thread
            thread = threading.Thread(target=process_extraction, daemon=True)
            thread.start()
            
            logger.info(f"Triggered async extraction for patient {patient_id} (queue ID: {queue_id})")
            resp_async = {
                'success': True,
                'message': 'Extraction started in background. You will receive an email notification when complete.',
                'queue_id': queue_id,
                'async': True
            }
            return jsonify(resp_async), 200
        
        else:
            # Synchronous processing (for testing/debugging) — direct sleep pipeline only
            from flask_app.services.direct_sleep_extraction import (
                run_direct_sleep_extraction_for_patient,
            )

            # Update status to processing
            db.session.execute(
                text("UPDATE document_processing_queue SET status = 'processing', started_at = NOW() WHERE id = :queue_id"),
                {'queue_id': queue_id}
            )
            db.session.commit()
            
            try:
                result = run_direct_sleep_extraction_for_patient(patient_id)
                success = result.get("queue_outcome") == "completed"
                
                # Update status - use WHERE status = 'processing' to avoid unique constraint issues
                try:
                    if success:
                        update_result = db.session.execute(
                            text("""
                                UPDATE document_processing_queue 
                                SET status = 'completed', completed_at = NOW() 
                                WHERE id = :queue_id
                                AND status = 'processing'
                            """),
                            {'queue_id': queue_id}
                        )
                        if update_result.rowcount > 0:
                            success = True
                            db.session.commit()
                        else:
                            # Already updated
                            logger.info(f"Queue entry {queue_id} already updated")
                            success = True
                    else:
                        update_result = db.session.execute(
                            text("""
                                UPDATE document_processing_queue 
                                SET status = 'failed', completed_at = NOW(),
                                    error_message = :error_msg
                                WHERE id = :queue_id
                                AND status = 'processing'
                            """),
                            {
                                'queue_id': queue_id,
                                'error_msg': (
                                    result.get("queue_message") or "Direct sleep analysis did not complete"
                                )[:1000],
                            },
                        )
                        if update_result.rowcount > 0:
                            success = False
                            db.session.commit()
                        else:
                            logger.info(f"Queue entry {queue_id} already updated")
                            success = False
                except Exception as update_error:
                    db.session.rollback()
                    error_msg = str(update_error)
                    if 'unique_active_patient' in error_msg or 'Duplicate entry' in error_msg:
                        logger.warning(f"Unique constraint error updating queue {queue_id}: {error_msg}")
                        # Check current status
                        current = db.session.execute(
                            text("SELECT status FROM document_processing_queue WHERE id = :queue_id"),
                            {'queue_id': queue_id}
                        ).fetchone()
                        if current:
                            success = (current.status == 'completed')
                        else:
                            success = False
                    else:
                        raise
                
                resp_sync = {
                    'success': success,
                    'message': 'Extraction completed',
                    'queue_id': queue_id,
                    'result': result,
                    'async': False
                }
                return jsonify(resp_sync), 200
                
            except Exception as e:
                db.session.execute(
                    text("""
                        UPDATE document_processing_queue 
                        SET status = 'failed', completed_at = NOW(),
                            error_message = :error_msg
                        WHERE id = :queue_id
                    """),
                    {'queue_id': queue_id, 'error_msg': str(e)[:1000]}
                )
                db.session.commit()
                raise
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error triggering extraction: {e}", exc_info=True)
        err_body = {'success': False, 'message': str(e)}
        return jsonify(err_body), 500


@data_extraction_bp.route('/api/data-extraction/status/<int:patient_id>', methods=['GET'])
@require_api_key_or_login
def get_extraction_status(patient_id):
    """
    Get extraction status for a patient
    """
    try:
        queue_entry = db.session.execute(
            text("""
                SELECT id, status, requested_at, started_at, completed_at, 
                       error_message, retry_count, priority, source
                FROM document_processing_queue 
                WHERE patient_id = :patient_id 
                ORDER BY requested_at DESC 
                LIMIT 1
            """),
            {'patient_id': patient_id}
        ).fetchone()
        
        if not queue_entry:
            out = {
                'success': True,
                'in_queue': False,
                'status': None
            }
            return jsonify(out), 200
        
        out = {
            'success': True,
            'in_queue': True,
            'queue_id': queue_entry.id,
            'status': queue_entry.status,
            'requested_at': queue_entry.requested_at.isoformat() if queue_entry.requested_at else None,
            'started_at': queue_entry.started_at.isoformat() if queue_entry.started_at else None,
            'completed_at': queue_entry.completed_at.isoformat() if queue_entry.completed_at else None,
            'error_message': queue_entry.error_message,
            'retry_count': queue_entry.retry_count,
            'priority': queue_entry.priority,
            'source': queue_entry.source
        }
        return jsonify(out), 200
        
    except Exception as e:
        logger.error(f"Error getting extraction status: {e}")
        err = {'success': False, 'message': str(e)}
        return jsonify(err), 500

