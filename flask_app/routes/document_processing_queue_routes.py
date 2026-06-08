"""
Document Processing Queue Routes
Handles API endpoints for managing document processing queue
"""

from flask import Blueprint, request, jsonify, render_template, current_app
from flask_login import login_required, current_user
from sqlalchemy import text
from flask_app import db
import logging
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from flask_app.services.document_queue_sla import DEFAULT_SLA_MINUTES

# Pending/processing older than this SLA are removed from the queue (never analyzed on that entry).
QUEUE_SLA_MINUTES = DEFAULT_SLA_MINUTES

# Legacy "clear stuck" endpoint uses the same window
STUCK_PROCESSING_TIMEOUT_HOURS = QUEUE_SLA_MINUTES / 60.0

document_processing_queue_bp = Blueprint('document_processing_queue', __name__)


@document_processing_queue_bp.route('/api/document-processing/diagnostic', methods=['GET'])
@login_required
def diagnostic():
    """
    Diagnostic endpoint to check table and database connection
    """
    try:
        diagnostics = {
            'database_connected': False,
            'table_exists': False,
            'table_has_data': False,
            'row_count': 0,
            'errors': []
        }
        
        # Test database connection
        try:
            db.session.execute(text("SELECT 1"))
            diagnostics['database_connected'] = True
        except Exception as e:
            diagnostics['errors'].append(f"Database connection failed: {str(e)}")
            return jsonify(diagnostics), 200
        
        # Check if table exists
        try:
            result = db.session.execute(text("""
                SELECT COUNT(*) as count FROM information_schema.tables 
                WHERE table_schema = DATABASE() 
                AND table_name = 'document_processing_queue'
            """))
            table_exists = result.fetchone()[0] > 0
            diagnostics['table_exists'] = table_exists
            
            if table_exists:
                # Check if table has data
                try:
                    count_result = db.session.execute(text("SELECT COUNT(*) as count FROM document_processing_queue"))
                    row_count = count_result.fetchone()[0]
                    diagnostics['row_count'] = row_count
                    diagnostics['table_has_data'] = row_count > 0
                except Exception as e:
                    diagnostics['errors'].append(f"Error counting rows: {str(e)}")
            else:
                diagnostics['errors'].append("Table 'document_processing_queue' does not exist")
        except Exception as e:
            diagnostics['errors'].append(f"Error checking table: {str(e)}")
        
        return jsonify(diagnostics), 200
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'errors': [str(e)]
        }), 500


@document_processing_queue_bp.route('/api/document-processing/queue/add', methods=['POST'])
@login_required
def add_to_queue():
    """
    Add a patient to the document processing queue
    """
    try:
        data = request.get_json()
        patient_id = data.get('patient_id')
        priority = data.get('priority', 0)
        batch_size = data.get('batch_size', 3)
        notes = data.get('notes', '')
        
        if not patient_id:
            return jsonify({'success': False, 'message': 'Patient ID is required'}), 400
        
        # Check if patient exists
        patient_check = db.session.execute(
            text("SELECT id FROM patients WHERE id = :patient_id"),
            {'patient_id': patient_id}
        ).fetchone()
        
        if not patient_check:
            return jsonify({'success': False, 'message': 'Patient not found'}), 404
        
        # Check if patient is already in queue (pending or processing)
        existing_queue = db.session.execute(
            text("""
                SELECT id, status FROM document_processing_queue 
                WHERE patient_id = :patient_id 
                AND status IN ('pending', 'processing')
            """),
            {'patient_id': patient_id}
        ).fetchone()
        
        if existing_queue:
            return jsonify({
                'success': False, 
                'message': f'Patient is already in queue with status: {existing_queue.status}',
                'queue_id': existing_queue.id
            }), 409
        
        # Add to queue
        result = db.session.execute(
            text("""
                INSERT INTO document_processing_queue 
                (patient_id, priority, source, requested_by, batch_size, notes, status)
                VALUES (:patient_id, :priority, 'ui', :user_id, :batch_size, :notes, 'pending')
            """),
            {
                'patient_id': patient_id,
                'priority': priority,
                'user_id': current_user.id,
                'batch_size': batch_size,
                'notes': notes
            }
        )
        db.session.commit()
        
        queue_id = result.lastrowid
        
        logger.info(f"Added patient {patient_id} to document processing queue (ID: {queue_id}) by user {current_user.id}")
        
        return jsonify({
            'success': True,
            'message': 'Patient added to processing queue',
            'queue_id': queue_id
        }), 200
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding patient to queue: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@document_processing_queue_bp.route('/api/document-processing/queue/status/<int:patient_id>', methods=['GET'])
@login_required
def get_queue_status(patient_id):
    """
    Get the current queue status for a patient
    """
    try:
        queue_entry = db.session.execute(
            text("""
                SELECT id, status, requested_at, started_at, completed_at, 
                       error_message, retry_count, priority
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
            'priority': queue_entry.priority
        }
        return jsonify(out), 200
        
    except Exception as e:
        logger.error(f"Error getting queue status: {e}")
        err = {'success': False, 'message': str(e)}
        return jsonify(err), 500


@document_processing_queue_bp.route('/api/document-processing/queue/list', methods=['GET'])
@login_required
def list_queue():
    """
    List all entries in the processing queue from database
    """
    try:
        status_filter = request.args.get('status', 'all')
        limit = int(request.args.get('limit', 100))
        
        # Simple query - just get queue entries with patient names
        query = """
            SELECT q.id, q.patient_id, q.status, q.priority, q.source, 
                   q.requested_at, q.started_at, q.completed_at, 
                   q.error_message, q.retry_count,
                   p.name as patient_name
            FROM document_processing_queue q
            LEFT JOIN patients p ON q.patient_id = p.id
        """
        
        params = {}
        
        if status_filter and status_filter != 'all':
            query += " WHERE q.status = :status"
            params['status'] = status_filter
        
        query += f" ORDER BY q.priority DESC, q.requested_at DESC LIMIT {limit}"
        
        queue_entries = db.session.execute(text(query), params).fetchall()
        
        result = []
        for entry in queue_entries:
            result.append({
                'id': entry.id,
                'patient_id': entry.patient_id,
                'patient_name': entry.patient_name if entry.patient_name else "Unknown",
                'status': entry.status,
                'priority': entry.priority,
                'source': entry.source,
                'requested_at': entry.requested_at.isoformat() if entry.requested_at else None,
                'started_at': entry.started_at.isoformat() if entry.started_at else None,
                'completed_at': entry.completed_at.isoformat() if entry.completed_at else None,
                'error_message': entry.error_message,
                'retry_count': entry.retry_count
            })
        
        return jsonify({
            'success': True,
            'queue': result,
            'count': len(result)
        }), 200
        
    except Exception as e:
        logger.error(f"Error listing queue: {e}")
        return jsonify({
            'success': False, 
            'message': str(e)
        }), 500


@document_processing_queue_bp.route('/document-processing/monitor', methods=['GET'])
@login_required
def monitor_queue():
    """
    Monitoring page for document processing queue
    """
    return render_template('document_processing_monitor.html')


@document_processing_queue_bp.route('/api/document-processing/queue/trigger/<int:patient_id>', methods=['POST'])
@login_required
def trigger_async_processing(patient_id):
    """
    Trigger async processing for a patient and send email notification when complete
    """
    try:
        # Check if patient exists
        patient_check = db.session.execute(
            text("SELECT id, email, name FROM patients WHERE id = :patient_id"),
            {'patient_id': patient_id}
        ).fetchone()
        
        if not patient_check:
            resp = {'success': False, 'message': 'Patient not found'}
            return jsonify(resp), 404
        
        # Check if patient is already in queue (pending or processing)
        existing_queue = db.session.execute(
            text("""
                SELECT id, status FROM document_processing_queue 
                WHERE patient_id = :patient_id 
                AND status IN ('pending', 'processing')
            """),
            {'patient_id': patient_id}
        ).fetchone()
        
        if existing_queue:
            resp = {
                'success': False,
                'message': f'Patient is already in queue with status: {existing_queue.status}',
                'queue_id': existing_queue.id
            }
            return jsonify(resp), 409
        
        # Add to queue with high priority for UI-triggered requests
        result = db.session.execute(
            text("""
                INSERT INTO document_processing_queue 
                (patient_id, priority, source, requested_by, batch_size, notes, status)
                VALUES (:patient_id, 10, 'ui_async', :user_id, 3, 'Triggered from clinical tab', 'pending')
            """),
            {
                'patient_id': patient_id,
                'user_id': current_user.id
            }
        )
        db.session.commit()
        
        queue_id = result.lastrowid
        
        # Capture user info before starting background thread (Flask-Login context won't be available in thread)
        user_email = current_user.email if hasattr(current_user, 'email') else None
        user_name = f"{current_user.first_name} {current_user.last_name}" if hasattr(current_user, 'first_name') and current_user.first_name else (current_user.email if user_email else "User")
        
        # Start async processing in background thread
        def process_and_notify():
            try:
                from flask_app.services.direct_sleep_extraction import (
                    run_direct_sleep_extraction_for_patient,
                )
                from flask_app.routes.file_management_routes import send_email_with_sendgrid
                from flask import current_app
                
                with current_app.app_context():
                    try:
                        from flask_app.services.document_queue_sla import (
                            abandon_expired_document_queue_rows,
                        )

                        _n = abandon_expired_document_queue_rows()
                        if _n:
                            logger.info(
                                "document_queue_sla: abandoned %s row(s) before processing",
                                _n,
                            )
                    except Exception as _sla_e:
                        logger.warning("document_queue_sla (async): %s", _sla_e)

                    # Update status to processing
                    db.session.execute(
                        text("UPDATE document_processing_queue SET status = 'processing', started_at = NOW() WHERE id = :queue_id"),
                        {'queue_id': queue_id}
                    )
                    db.session.commit()
                    
                    logger.info(f"Starting async direct sleep analysis for patient {patient_id} (queue ID: {queue_id})")
                    
                    result = run_direct_sleep_extraction_for_patient(patient_id)
                    success = result.get("queue_outcome") == "completed"
                    sp = result.get("sleep_pipeline") or {}

                    # Update status
                    if success:
                        db.session.execute(
                            text("""
                                UPDATE document_processing_queue 
                                SET status = 'completed', completed_at = NOW() 
                                WHERE id = :queue_id
                            """),
                            {'queue_id': queue_id}
                        )
                        status = 'completed'
                    else:
                        db.session.execute(
                            text("""
                                UPDATE document_processing_queue 
                                SET status = 'failed', completed_at = NOW(),
                                    error_message = :error_msg
                                WHERE id = :queue_id
                            """),
                            {
                                'queue_id': queue_id,
                                'error_msg': (
                                    result.get("queue_message")
                                    or "Direct sleep analysis did not complete"
                                )[:1000],
                            },
                        )
                        status = 'failed'
                    
                    db.session.commit()
                    
                    # Send email notification to the user who triggered processing
                    # Use captured user info from outer scope
                    # Get patient info for email content
                    patient_info = db.session.execute(
                        text("SELECT name FROM patients WHERE id = :patient_id"),
                        {'patient_id': patient_id}
                    ).fetchone()
                    patient_name = patient_info.name if patient_info and patient_info.name else f"Patient {patient_id}"
                    
                    if user_email:
                        try:
                            if success:
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
                                email_type='document_processing_notification',
                                sender_type='system'
                            )
                            logger.info(f"Sent processing notification email to {user_email}")
                        except Exception as email_error:
                            logger.error(f"Failed to send notification email: {email_error}")
                    
                    logger.info(f"Completed async processing for patient {patient_id}: {status}")
                    
            except Exception as e:
                logger.error(f"Error in async processing for patient {patient_id}: {e}")
                try:
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
                except:
                    pass
        
        # Start background thread
        thread = threading.Thread(target=process_and_notify, daemon=True)
        thread.start()
        
        logger.info(f"Triggered async processing for patient {patient_id} (queue ID: {queue_id})")
        
        resp = {
            'success': True,
            'message': 'Processing started in background. You will receive an email notification when complete.',
            'queue_id': queue_id
        }
        return jsonify(resp), 200
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error triggering async processing: {e}")
        err = {'success': False, 'message': str(e)}
        return jsonify(err), 500


@document_processing_queue_bp.route('/api/document-processing/canonical/<int:patient_id>', methods=['GET'])
@login_required
def get_canonical_json(patient_id):
    """
    Get canonical JSON for a patient
    """
    try:
        from flask_app.services.cache_service import CacheService
        
        canonical_data = CacheService.cached_canonical_data(patient_id)
        
        if canonical_data is None:
            # Try to create it
            from flask_app.config.document_observation_extractor_phase2 import create_minimal_canonical_json_for_patient
            result = create_minimal_canonical_json_for_patient(patient_id)
            if result.get('success'):
                canonical_data = CacheService.cached_canonical_data(patient_id)
        
        if canonical_data is None:
            return jsonify({
                'success': False,
                'message': 'Canonical data not available for this patient'
            }), 404
        
        return jsonify({
            'success': True,
            'canonical': canonical_data
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting canonical JSON for patient {patient_id}: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@document_processing_queue_bp.route(
    "/api/document-processing/sleep-study-pipeline/<int:patient_id>", methods=["POST"]
)
@login_required
def run_sleep_study_pipeline_endpoint(patient_id):
    """
    Run the dedicated sleep-study LLM pipeline for this patient (sleep-like admin PDFs).
    Optional JSON body: { "force": true, "admin_file_id": 123 }
    """
    try:
        from flask_app.config.sleep_study_analysis_pipeline import run_sleep_study_pipeline_for_patient
        from flask_app.config.document_observation_extractor_phase2 import create_minimal_canonical_json_for_patient

        body = request.get_json(silent=True) or {}
        force = bool(body.get("force"))
        admin_file_id = body.get("admin_file_id")
        if admin_file_id is not None:
            try:
                admin_file_id = int(admin_file_id)
            except (TypeError, ValueError):
                admin_file_id = None

        out = run_sleep_study_pipeline_for_patient(
            patient_id, force=force, admin_file_id=admin_file_id
        )
        if (out.get("processed") or 0) > 0:
            try:
                create_minimal_canonical_json_for_patient(patient_id)
            except Exception as e:
                logger.warning("Canonical refresh after sleep pipeline: %s", e)
        resp_ok = {"success": True, "result": out}
        return jsonify(resp_ok), 200
    except Exception as e:
        logger.error("sleep-study-pipeline endpoint: %s", e, exc_info=True)
        err = {"success": False, "message": str(e)}
        return jsonify(err), 500


@document_processing_queue_bp.route('/api/document-processing/logs/<int:patient_id>', methods=['GET'])
@login_required
def get_processing_logs(patient_id):
    """
    Get processing logs for a specific patient
    """
    try:
        import os
        from datetime import datetime
        
        # Get logs from queue entries
        queue_logs = db.session.execute(
            text("""
                SELECT id, status, requested_at, started_at, completed_at, 
                       error_message, retry_count, source, notes
                FROM document_processing_queue 
                WHERE patient_id = :patient_id 
                ORDER BY requested_at DESC 
                LIMIT 50
            """),
            {'patient_id': patient_id}
        ).fetchall()
        
        queue_entries = []
        for entry in queue_logs:
            queue_entries.append({
                'type': 'queue_entry',
                'timestamp': entry.requested_at.isoformat() if entry.requested_at else None,
                'status': entry.status,
                'message': f"Queue ID {entry.id}: {entry.status}",
                'details': {
                    'queue_id': entry.id,
                    'started_at': entry.started_at.isoformat() if entry.started_at else None,
                    'completed_at': entry.completed_at.isoformat() if entry.completed_at else None,
                    'error_message': entry.error_message,
                    'retry_count': entry.retry_count,
                    'source': entry.source,
                    'notes': entry.notes
                }
            })
        
        # Try to get logs from log file (if accessible)
        file_logs = []
        log_file_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'document_extraction_phase2.log')
        
        if os.path.exists(log_file_path):
            try:
                # Read last 1000 lines and filter for this patient
                with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    # Get last 2000 lines to search
                    recent_lines = lines[-2000:] if len(lines) > 2000 else lines
                    
                    for line in recent_lines:
                        if f"patient {patient_id}" in line.lower() or f"patient_id={patient_id}" in line.lower():
                            # Parse log line
                            try:
                                # Extract timestamp if present
                                timestamp_match = None
                                if ' - ' in line:
                                    parts = line.split(' - ', 1)
                                    if len(parts) == 2:
                                        timestamp_str = parts[0].strip()
                                        message = parts[1].strip()
                                        try:
                                            timestamp_match = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
                                        except:
                                            pass
                                
                                file_logs.append({
                                    'type': 'file_log',
                                    'timestamp': timestamp_match.isoformat() if timestamp_match else None,
                                    'status': 'info',
                                    'message': message if 'message' in locals() else line.strip(),
                                    'raw_line': line.strip()
                                })
                            except:
                                file_logs.append({
                                    'type': 'file_log',
                                    'timestamp': None,
                                    'status': 'info',
                                    'message': line.strip(),
                                    'raw_line': line.strip()
                                })
            except Exception as e:
                logger.warning(f"Could not read log file: {e}")
        
        # Combine and sort by timestamp
        all_logs = queue_entries + file_logs
        all_logs.sort(key=lambda x: x.get('timestamp') or '', reverse=True)
        
        return jsonify({
            'success': True,
            'logs': all_logs[:100],  # Limit to 100 most recent
            'count': len(all_logs)
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting processing logs for patient {patient_id}: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@document_processing_queue_bp.route('/api/document-processing/logs/all', methods=['GET'])
@login_required
def get_all_processing_logs():
    """
    Get processing logs for all patients (admin view)
    """
    try:
        # Check if user is admin (you may need to adjust this based on your user model)
        # For now, allow all logged-in users - you can add admin check later
        import os
        from datetime import datetime
        import re
        
        # Get recent queue entries for all patients
        queue_logs = db.session.execute(
            text("""
                SELECT q.id, q.patient_id, q.status, q.requested_at, q.started_at, 
                       q.completed_at, q.error_message, q.retry_count, q.source,
                       p.name as patient_name
                FROM document_processing_queue q
                LEFT JOIN patients p ON q.patient_id = p.id
                ORDER BY q.requested_at DESC 
                LIMIT 200
            """)
        ).fetchall()
        
        queue_entries = []
        for entry in queue_logs:
            patient_name = entry.patient_name if entry.patient_name else f"Patient {entry.patient_id}"
            queue_entries.append({
                'type': 'queue_entry',
                'patient_id': entry.patient_id,
                'patient_name': patient_name,
                'timestamp': entry.requested_at.isoformat() if entry.requested_at else None,
                'status': entry.status,
                'message': f"Patient {entry.patient_id} ({patient_name}): {entry.status}",
                'details': {
                    'queue_id': entry.id,
                    'started_at': entry.started_at.isoformat() if entry.started_at else None,
                    'completed_at': entry.completed_at.isoformat() if entry.completed_at else None,
                    'error_message': entry.error_message,
                    'retry_count': entry.retry_count,
                    'source': entry.source
                }
            })
        
        # Try to get recent logs from log file
        file_logs = []
        log_file_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'document_extraction_phase2.log')
        
        if os.path.exists(log_file_path):
            try:
                with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    # Get last 500 lines
                    recent_lines = lines[-500:] if len(lines) > 500 else lines
                    
                    for line in recent_lines:
                        # Extract patient ID if present
                        patient_match = re.search(r'patient[_\s]+(\d+)', line.lower())
                        patient_id_from_log = int(patient_match.group(1)) if patient_match else None
                        
                        if patient_id_from_log:
                            try:
                                timestamp_match = None
                                if ' - ' in line:
                                    parts = line.split(' - ', 1)
                                    if len(parts) == 2:
                                        timestamp_str = parts[0].strip()
                                        message = parts[1].strip()
                                        try:
                                            timestamp_match = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
                                        except:
                                            pass
                                
                                file_logs.append({
                                    'type': 'file_log',
                                    'patient_id': patient_id_from_log,
                                    'timestamp': timestamp_match.isoformat() if timestamp_match else None,
                                    'status': 'info',
                                    'message': message if 'message' in locals() else line.strip(),
                                    'raw_line': line.strip()
                                })
                            except:
                                pass
            except Exception as e:
                logger.warning(f"Could not read log file: {e}")
        
        # Combine and sort
        all_logs = queue_entries + file_logs
        all_logs.sort(key=lambda x: x.get('timestamp') or '', reverse=True)
        
        return jsonify({
            'success': True,
            'logs': all_logs[:300],  # Limit to 300 most recent
            'count': len(all_logs)
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting all processing logs: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@document_processing_queue_bp.route('/api/document-processing/queue/clear-stuck', methods=['POST'])
@login_required
def clear_stuck_entries():
    """
    Mark stuck 'processing' entries as 'failed' (max retries, no re-queue) or delete on unique conflict.
    Entries are considered stuck if they've been processing longer than QUEUE_SLA_MINUTES.
    """
    try:
        timeout_threshold = datetime.utcnow() - timedelta(minutes=QUEUE_SLA_MINUTES)
        
        # First, get the stuck entries for reporting
        stuck_entries = db.session.execute(
            text("""
                SELECT id, patient_id, started_at 
                FROM document_processing_queue 
                WHERE status = 'processing' 
                AND COALESCE(started_at, requested_at) < :timeout_threshold
            """),
            {'timeout_threshold': timeout_threshold}
        ).fetchall()
        
        if not stuck_entries:
            return jsonify({
                'success': True,
                'message': 'No stuck entries found',
                'cleared_count': 0,
                'cleared_entries': []
            }), 200
        
        # Process each entry individually to handle unique constraint conflicts
        cleared_count = 0
        cleared_entries = []
        errors = []
        user_email = current_user.email if hasattr(current_user, 'email') else 'admin'
        
        for entry in stuck_entries:
            try:
                db.session.execute(
                    text("""
                        UPDATE document_processing_queue 
                        SET status = 'failed',
                            completed_at = NOW(),
                            error_message = :error_msg,
                            retry_count = max_retries
                        WHERE id = :entry_id
                        AND status = 'processing'
                    """),
                    {
                        'entry_id': entry.id,
                        'error_msg': f'Process terminated unexpectedly (stuck). Cleared by {user_email}'
                    }
                )
                db.session.commit()
                cleared_count += 1
                cleared_entries.append({'id': entry.id, 'patient_id': entry.patient_id})
            except Exception as entry_error:
                db.session.rollback()
                error_msg = str(entry_error)
                
                # If failed also conflicts (unique patient_id+status), delete the stuck row
                if 'Duplicate entry' in error_msg or 'unique_active_patient' in error_msg:
                    try:
                        db.session.execute(
                            text("DELETE FROM document_processing_queue WHERE id = :entry_id"),
                            {'entry_id': entry.id}
                        )
                        db.session.commit()
                        cleared_count += 1
                        cleared_entries.append({'id': entry.id, 'patient_id': entry.patient_id, 'deleted': True})
                        logger.info(f"Deleted stuck entry {entry.id} for patient {entry.patient_id} due to constraint conflict")
                    except Exception as delete_error:
                        db.session.rollback()
                        errors.append(f"Entry {entry.id}: {str(delete_error)}")
                else:
                    errors.append(f"Entry {entry.id}: {error_msg}")
        
        logger.info(f"Cleared {cleared_count} stuck processing entries: {cleared_entries}")
        if errors:
            logger.warning(f"Errors while clearing stuck entries: {errors}")
        
        return jsonify({
            'success': True,
            'message': f'Cleared {cleared_count} stuck entries' + (f' ({len(errors)} errors)' if errors else ''),
            'cleared_count': cleared_count,
            'cleared_entries': cleared_entries,
            'errors': errors if errors else None
        }), 200
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error clearing stuck entries: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@document_processing_queue_bp.route('/api/document-processing/queue/purge', methods=['POST'])
@login_required
def purge_queue():
    """
    DELETE all rows from document_processing_queue. Body: {"confirm": true}.
    """
    try:
        data = request.get_json(silent=True) or {}
        if data.get('confirm') is not True:
            return jsonify({
                'success': False,
                'message': 'Send JSON {"confirm": true} to wipe the entire queue',
            }), 400

        from flask_app.services.document_queue_sla import purge_entire_document_queue

        deleted = purge_entire_document_queue()
        return jsonify({
            'success': True,
            'message': f'Purged {deleted} queue row(s)',
            'deleted_count': deleted,
        }), 200
    except Exception as e:
        logger.error(f"Error purging queue: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@document_processing_queue_bp.route('/api/document-processing/queue/cancel/<int:entry_id>', methods=['POST'])
@login_required
def cancel_entry(entry_id):
    """
    Cancel a specific queue entry (mark as failed with max retries, or delete on unique conflict).
    Allows user to manually reset stuck entries.
    """
    try:
        user_email = current_user.email if hasattr(current_user, 'email') else 'admin'
        
        # First check if entry exists
        entry = db.session.execute(
            text("SELECT id, patient_id, status FROM document_processing_queue WHERE id = :entry_id"),
            {'entry_id': entry_id}
        ).fetchone()
        
        if not entry:
            return jsonify({'success': False, 'message': 'Entry not found'}), 404
        
        if entry.status not in ('pending', 'processing'):
            return jsonify({
                'success': False, 
                'message': f'Entry is already {entry.status}, cannot cancel'
            }), 400
        
        try:
            db.session.execute(
                text("""
                    UPDATE document_processing_queue 
                    SET status = 'failed',
                        completed_at = NOW(),
                        error_message = :error_msg,
                        retry_count = max_retries
                    WHERE id = :entry_id
                """),
                {
                    'entry_id': entry_id,
                    'error_msg': f'Manually cancelled by {user_email}'
                }
            )
            db.session.commit()
            
            logger.info(f"Entry {entry_id} for patient {entry.patient_id} cancelled by {user_email}")
            
            return jsonify({
                'success': True,
                'message': 'Entry cancelled successfully',
                'entry_id': entry_id,
                'patient_id': entry.patient_id
            }), 200
            
        except Exception as update_error:
            db.session.rollback()
            error_msg = str(update_error)
            
            # If unique constraint conflict, delete instead
            if 'Duplicate entry' in error_msg or 'unique_active_patient' in error_msg:
                try:
                    db.session.execute(
                        text("DELETE FROM document_processing_queue WHERE id = :entry_id"),
                        {'entry_id': entry_id}
                    )
                    db.session.commit()
                    
                    logger.info(f"Entry {entry_id} for patient {entry.patient_id} deleted (constraint conflict) by {user_email}")
                    
                    return jsonify({
                        'success': True,
                        'message': 'Entry deleted (due to constraint)',
                        'entry_id': entry_id,
                        'patient_id': entry.patient_id,
                        'deleted': True
                    }), 200
                    
                except Exception as delete_error:
                    db.session.rollback()
                    raise delete_error
            else:
                raise update_error
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error cancelling entry {entry_id}: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@document_processing_queue_bp.route('/api/document-processing/queue/stuck-count', methods=['GET'])
@login_required
def get_stuck_count():
    """
    Get count of stuck processing entries.
    """
    try:
        timeout_threshold = datetime.utcnow() - timedelta(minutes=QUEUE_SLA_MINUTES)
        
        result = db.session.execute(
            text("""
                SELECT COUNT(*) as count
                FROM document_processing_queue 
                WHERE status = 'processing' 
                AND COALESCE(started_at, requested_at) < :timeout_threshold
            """),
            {'timeout_threshold': timeout_threshold}
        ).fetchone()
        
        return jsonify({
            'success': True,
            'stuck_count': result.count if result else 0,
            'timeout_minutes': QUEUE_SLA_MINUTES,
            'timeout_hours': STUCK_PROCESSING_TIMEOUT_HOURS,
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting stuck count: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
