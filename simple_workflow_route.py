"""
Simple Enhanced Patient Workflow Route
Integrates the simplified enhanced workflow with the existing Flask application.
"""

from flask import Blueprint, render_template, jsonify, request, flash, redirect, url_for
from flask_login import login_required, current_user
from simple_enhanced_workflow import get_enhanced_patient_workflow
import logging

# Set up logging
logger = logging.getLogger(__name__)

# Create blueprint
simple_workflow = Blueprint('simple_workflow', __name__)

@simple_workflow.route('/simple_enhanced_workflow/<int:patient_id>', methods=['GET'])
@login_required
def simple_enhanced_workflow_page(patient_id):
    """Simple enhanced patient workflow page with manifest data and file links"""
    try:
        # Get enhanced workflow data
        workflow_data = get_enhanced_patient_workflow(patient_id)
        
        if 'error' in workflow_data:
            flash(f'Error loading patient workflow: {workflow_data["error"]}', 'error')
            return redirect(url_for('main.patient_list'))
        
        # Get patient information
        patient = workflow_data['patient']
        stages = workflow_data['stages']
        progress_percentage = workflow_data['progress_percentage']
        completed_stages = workflow_data['completed_stages']
        total_stages = workflow_data['total_stages']
        current_stage = workflow_data['current_stage']
        
        # Get doctor information
        doctor_name = current_user.name if hasattr(current_user, 'name') else "Doctor"
        
        # Get patient DSO information
        patient_dso_id = None
        if patient.get('clinic_id'):
            from flask_app.models import Clinic
            clinic = Clinic.query.get(patient['clinic_id'])
            if clinic:
                patient_dso_id = clinic.dso_id
        
        return render_template('simple_enhanced_journey.html',
                             patient=patient,
                             doctor_name=doctor_name,
                             stages=stages,
                             current_stage=current_stage,
                             completed_stages=completed_stages,
                             total_stages=total_stages,
                             progress_percentage=progress_percentage,
                             patient_dso_id=patient_dso_id)
                             
    except Exception as e:
        logger.error(f"Error in simple enhanced patient workflow: {e}")
        flash(f'Error loading enhanced patient workflow: {str(e)}', 'error')
        return redirect(url_for('main.patient_list'))

@simple_workflow.route('/api/simple_workflow/<int:patient_id>', methods=['GET'])
@login_required
def simple_workflow_api(patient_id):
    """API endpoint for simple workflow data"""
    try:
        workflow_data = get_enhanced_patient_workflow(patient_id)
        return jsonify(workflow_data)
    except Exception as e:
        logger.error(f"Error in simple workflow API: {e}")
        return jsonify({'error': str(e)}), 500

@simple_workflow.route('/api/stage_files/<int:patient_id>/<stage_key>', methods=['GET'])
@login_required
def stage_files_api(patient_id, stage_key):
    """API endpoint for stage-specific files"""
    try:
        from simple_enhanced_workflow import get_files_for_stage
        import mysql.connector
        
        # Database configuration
        DB_CONFIG = {
            'host': 'vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com',
            'user': 'admin',
            'password': 'Vizbriz2025!',
            'database': 'vizbriz',
            'port': 3306
        }
        
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        files = get_files_for_stage(patient_id, stage_key, cursor)
        
        conn.close()
        
        return jsonify({'files': files})
    except Exception as e:
        logger.error(f"Error in stage files API: {e}")
        return jsonify({'error': str(e)}), 500 