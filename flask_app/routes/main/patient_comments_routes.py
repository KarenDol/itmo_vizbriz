"""
Patient Comments Routes

This module handles all patient comment-related API endpoints:
- Fetching comments (GET)
- Creating comments (POST)
- Updating comments (PUT)
- Deleting comments (DELETE)
"""

import logging
from datetime import datetime

from flask import current_app, jsonify, request
from flask_login import current_user, login_required

from flask_app import db
from flask_app.models import Patient, PatientComment

logger = logging.getLogger(__name__)


def patient_comments_enhanced(patient_id):
    """
    Enhanced endpoint for patient comments with titration and numeric value support.
    This is the new endpoint for the Titration & Comments tab.
    """
    app = current_app
    app.logger.debug(f"Enhanced comments API - Patient ID: {patient_id}")
    
    # Ensure the patient exists
    patient = Patient.query.get_or_404(patient_id)
    app.logger.debug(f"Patient retrieved: ID={patient.id}, Name={patient.name}")

    dentist_id = current_user.id
    app.logger.debug(f"Current user (dentist): ID={dentist_id}")

    if request.method == 'GET':
        try:
            app.logger.debug("Fetching enhanced comments for patient.")
            comments = PatientComment.query.filter_by(patient_id=patient_id).order_by(PatientComment.created_date.desc()).all()
            comments_data = [
                {
                    'id': comment.id,
                    'content': comment.content,
                    'created_date': comment.created_date.strftime('%Y-%m-%d %H:%M:%S'),
                    'dentist': comment.dentist.name if comment.dentist else 'Unknown',
                    'comment_type': comment.comment_type or 'general',
                    'numeric_value': float(comment.numeric_value) if comment.numeric_value else None,
                    'numeric_unit': comment.numeric_unit,
                    'is_urgent': comment.is_urgent or False,
                    'is_internal': comment.is_internal or False
                }
                for comment in comments
            ]
            app.logger.debug(f"Fetched {len(comments)} enhanced comments for patient ID {patient_id}")
            return jsonify({'success': True, 'comments': comments_data})
        except Exception as e:
            app.logger.error(f"Error fetching enhanced comments for patient ID {patient_id}: {str(e)}")
            return jsonify({'success': False, 'message': f'Error fetching comments: {str(e)}'}), 500

    elif request.method == 'POST':
        app.logger.debug("Processing POST request to add an enhanced comment.")
        try:
            data = request.get_json()
            app.logger.debug(f"Received enhanced comment data: {data}")
            
            if not data:
                app.logger.warning("No data received in POST request.")
                return jsonify({'success': False, 'message': 'No data provided'}), 400

            content = data.get('content', '').strip()
            if not content:
                app.logger.warning("Empty content provided for the comment.")
                return jsonify({'success': False, 'message': 'Comment cannot be empty'}), 400

            # Extract new fields from request data
            comment_type = data.get('comment_type', 'general')
            numeric_value = data.get('numeric_value')
            numeric_unit = data.get('numeric_unit')
            is_urgent = data.get('is_urgent', False)
            is_internal = data.get('is_internal', False)
            
            # Validate comment type
            valid_types = ['general', 'titration', 'consultation', 'delivery', 'initial', 'followup']
            if comment_type not in valid_types:
                comment_type = 'general'
            
            # Save the new enhanced comment
            new_comment = PatientComment(
                patient_id=patient_id,
                content=content,
                created_date=datetime.utcnow(),
                dentist_id=dentist_id,
                comment_type=comment_type,
                numeric_value=numeric_value,
                numeric_unit=numeric_unit,
                is_urgent=is_urgent,
                is_internal=is_internal
            )
            app.logger.debug(f"New enhanced comment to be added: {new_comment}")
            
            db.session.add(new_comment)
            db.session.commit()
            app.logger.info(f"Enhanced comment successfully added for patient ID {patient_id} by dentist ID {dentist_id}")
            return jsonify({'success': True, 'message': 'Comment added successfully'})
        except Exception as e:
            app.logger.error(f"Error saving enhanced comment for patient ID {patient_id}: {str(e)}")
            db.session.rollback()
            return jsonify({'success': False, 'message': f'Error saving comment: {str(e)}'}), 500


def patient_comments(patient_id):
    """
    Endpoint to handle fetching and saving comments for a patient.
    """
    app = current_app
    app.logger.debug(f"Received request for comments for patient ID: {patient_id}")
    
    # Ensure the patient exists
    patient = Patient.query.get_or_404(patient_id)
    app.logger.debug(f"Patient retrieved: ID={patient.id}, Name={patient.name}")

    dentist_id = current_user.id
    app.logger.debug(f"Current user (dentist): ID={dentist_id}")

    if request.method == 'GET':
        try:
            app.logger.debug("Fetching comments for patient.")
            comments = PatientComment.query.filter_by(patient_id=patient_id).order_by(PatientComment.created_date.desc()).all()
            comments_data = [
                {
                    'id': comment.id,
                    'content': comment.content,
                    'created_date': comment.created_date.strftime('%Y-%m-%d %H:%M:%S'),
                    'dentist': comment.dentist.name if comment.dentist else 'Unknown',
                    'comment_type': comment.comment_type or 'general',
                    'numeric_value': float(comment.numeric_value) if comment.numeric_value else None,
                    'numeric_unit': comment.numeric_unit,
                    'is_urgent': comment.is_urgent or False,
                    'is_internal': comment.is_internal or False
                }
                for comment in comments
            ]
            app.logger.debug(f"Fetched {len(comments)} comments for patient ID {patient_id}")
            return jsonify({'success': True, 'comments': comments_data})
        except Exception as e:
            app.logger.error(f"Error fetching comments for patient ID {patient_id}: {str(e)}")
            return jsonify({'success': False, 'message': f'Error fetching comments: {str(e)}'}), 500

    elif request.method == 'POST':
        app.logger.debug("Processing POST request to add a comment.")
        try:
            data = request.get_json()
            app.logger.debug(f"Received data: {data}")
            
            if not data:
                app.logger.warning("No data received in POST request.")
                return jsonify({'success': False, 'message': 'No data provided'}), 400

            content = data.get('content', '').strip()
            if not content:
                app.logger.warning("Empty content provided for the comment.")
                return jsonify({'success': False, 'message': 'Comment cannot be empty'}), 400

            # Extract new fields from request data
            comment_type = data.get('comment_type', 'general')
            numeric_value = data.get('numeric_value')
            numeric_unit = data.get('numeric_unit')
            is_urgent = data.get('is_urgent', False)
            is_internal = data.get('is_internal', False)
            
            # Save the new comment
            new_comment = PatientComment(
                patient_id=patient_id,
                content=content,
                created_date=datetime.utcnow(),
                dentist_id=dentist_id,
                comment_type=comment_type,
                numeric_value=numeric_value,
                numeric_unit=numeric_unit,
                is_urgent=is_urgent,
                is_internal=is_internal
            )
            app.logger.debug(f"New comment to be added: {new_comment}")
            
            db.session.add(new_comment)
            db.session.commit()
            app.logger.info(f"Comment successfully added for patient ID {patient_id} by dentist ID {dentist_id}")
            return jsonify({'success': True, 'message': 'Comment added successfully'})
        except Exception as e:
            app.logger.error(f"Error saving comment for patient ID {patient_id}: {str(e)}")
            db.session.rollback()
            return jsonify({'success': False, 'message': f'Error saving comment: {str(e)}'}), 500


def update_patient_comment(patient_id, comment_id):
    """
    API endpoint to update an existing comment for a patient.
    """
    app = current_app
    app.logger.debug(f"Updating comment {comment_id} for patient ID: {patient_id}")
    
    try:
        # Ensure the patient exists
        patient = Patient.query.get_or_404(patient_id)
        
        # Find the comment
        comment = PatientComment.query.filter_by(id=comment_id, patient_id=patient_id).first()
        if not comment:
            return jsonify({'success': False, 'message': 'Comment not found'}), 404
        
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        # Extract form data
        content = data.get('content', '').strip()
        if not content:
            return jsonify({'success': False, 'message': 'Comment cannot be empty'}), 400
        
        # Update the comment
        comment.content = content
        comment.numeric_value = data.get('numeric_value')
        comment.numeric_unit = data.get('numeric_unit')
        comment.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        app.logger.info(f"Comment {comment_id} updated successfully for patient ID {patient_id}")
        return jsonify({'success': True, 'message': 'Comment updated successfully', 'comment': {
            'id': comment.id,
            'content': comment.content,
            'created_date': comment.created_date.strftime('%Y-%m-%d %H:%M:%S'),
            'numeric_value': float(comment.numeric_value) if comment.numeric_value else None,
            'numeric_unit': comment.numeric_unit
        }})
        
    except Exception as e:
        app.logger.error(f"Error updating comment {comment_id} for patient ID {patient_id}: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error updating comment: {str(e)}'}), 500


def delete_patient_comment(patient_id, comment_id):
    """
    API endpoint to delete a comment for a patient.
    """
    app = current_app
    app.logger.debug(f"Deleting comment {comment_id} for patient ID: {patient_id}")
    
    try:
        # Ensure the patient exists
        patient = Patient.query.get_or_404(patient_id)
        
        # Find the comment
        comment = PatientComment.query.filter_by(id=comment_id, patient_id=patient_id).first()
        if not comment:
            return jsonify({'success': False, 'message': 'Comment not found'}), 404
        
        # Delete the comment
        db.session.delete(comment)
        db.session.commit()
        
        app.logger.info(f"Comment {comment_id} deleted successfully for patient ID {patient_id}")
        return jsonify({'success': True, 'message': 'Comment deleted successfully'})
        
    except Exception as e:
        app.logger.error(f"Error deleting comment {comment_id} for patient ID {patient_id}: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error deleting comment: {str(e)}'}), 500


def register_patient_comments_routes(main):
    """Register patient comments routes onto the main Blueprint."""
    main.add_url_rule(
        '/api/patient/<int:patient_id>/comments',
        endpoint='patient_comments_enhanced',
        view_func=login_required(patient_comments_enhanced),
        methods=['GET', 'POST']
    )
    main.add_url_rule(
        '/api/patients/<int:patient_id>/comments',
        endpoint='patient_comments',
        view_func=login_required(patient_comments),
        methods=['GET', 'POST']
    )
    main.add_url_rule(
        '/api/patient/<int:patient_id>/comments/<int:comment_id>',
        endpoint='update_patient_comment',
        view_func=login_required(update_patient_comment),
        methods=['PUT']
    )
    main.add_url_rule(
        '/api/patient/<int:patient_id>/comments/<int:comment_id>',
        endpoint='delete_patient_comment',
        view_func=login_required(delete_patient_comment),
        methods=['DELETE']
    )
