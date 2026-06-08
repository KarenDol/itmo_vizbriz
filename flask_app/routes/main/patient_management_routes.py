"""
Patient Management Routes

This module handles patient field updates, archiving, and restoration:
- Update specific patient fields via API
- Archive patients
- Restore archived patients (admin only)
"""

import logging
from datetime import datetime

from flask import current_app, flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from flask_app import db
from flask_app.models import Patient

logger = logging.getLogger(__name__)


def update_patient_field(patient_id):
    """
    Endpoint to update a specific field of a patient based on a key-value pair.
    """
    app = current_app
    try:
        # Parse JSON data from the request
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400

        # Extract the key and value
        key = data.get('key')
        value = data.get('value')
        
        if not key or value is None:
            return jsonify({'success': False, 'message': 'Both key and value are required'}), 400

        # Fetch the patient record
        patient = Patient.query.get_or_404(patient_id)

        # Check if the user has permission to update this patient
        if current_user.role != 'admin' and patient.dentist_id != current_user.id:
            return jsonify({'success': False, 'message': 'Permission denied'}), 403

        # Update the corresponding field dynamically
        if hasattr(patient, key):
            setattr(patient, key, value)
            patient.last_update = datetime.utcnow()  # Update the timestamp
        else:
            return jsonify({'success': False, 'message': f'Invalid field: {key}'}), 400

        # Commit the changes to the database
        db.session.commit()
        return jsonify({'success': True, 'message': 'Patient updated successfully'}), 200

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error updating patient field: {str(e)}")
        return jsonify({'success': False, 'message': f'Error updating patient: {str(e)}'}), 500


def archive_patient(patient_id):
    """Archive a patient by setting status to 'Archived'"""
    from flask_app.models import _invalidate_patient_cache
    
    patient = Patient.query.get_or_404(patient_id)
    patient.status = "Archived"
    
    # Invalidate cache for this patient
    _invalidate_patient_cache(patient_id)
    
    db.session.commit()
    
    # Return JSON if requested (for AJAX calls), otherwise redirect
    if request.is_json or request.headers.get('Content-Type') == 'application/json':
        return jsonify({'success': True, 'message': 'Patient archived successfully'})
    
    # After archiving, redirect back to the list (which won't show archived patients)
    return redirect(url_for('main.patient_list'))


def restore_patient(patient_id):
    """Restore an archived patient by setting status to 'New' (admin only)"""
    from flask_app.models import _invalidate_patient_cache
    
    # Only admins can restore archived patients
    if current_user.role != 'admin':
        if request.is_json or request.headers.get('Content-Type') == 'application/json':
            return jsonify({'success': False, 'message': 'Unauthorized: Admin access required'}), 403
        flash('Unauthorized: Admin access required', 'error')
        return redirect(url_for('main.patient_list'))
    
    patient = Patient.query.get_or_404(patient_id)
    patient.status = "New"
    
    # Invalidate cache for this patient
    _invalidate_patient_cache(patient_id)
    
    db.session.commit()
    
    # Return JSON if requested (for AJAX calls), otherwise redirect
    if request.is_json or request.headers.get('Content-Type') == 'application/json':
        return jsonify({'success': True, 'message': 'Patient restored successfully'})
    
    # After restoring, redirect back to the list with archived patients shown
    return redirect(url_for('main.patient_list', include_archived='true'))


def register_patient_management_routes(main):
    """Register patient management routes onto the main Blueprint."""
    main.add_url_rule(
        '/api/patient/<int:patient_id>/update_field',
        endpoint='update_patient_field',
        view_func=login_required(update_patient_field),
        methods=['POST']
    )
    main.add_url_rule(
        '/archive_patient/<int:patient_id>',
        endpoint='archive_patient',
        view_func=archive_patient,
        methods=['POST']
    )
    main.add_url_rule(
        '/restore_patient/<int:patient_id>',
        endpoint='restore_patient',
        view_func=login_required(restore_patient),
        methods=['POST']
    )
