from flask import Blueprint, request, jsonify
from flask_app.models import Patient, PatientConsultSchedule
from flask_app.extensions import db
from datetime import datetime
import sys
import os

# Add the parent directory to the path to import our consultation functions
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Temporarily comment out to fix patient manifest issue
# from schedule_initial_consultation import (
#     schedule_initial_consultation,
#     update_consultation_status,
#     get_patient_consultations,
#     validate_stage2_completion
# )

consultation = Blueprint('consultation', __name__)

@consultation.route('/api/schedule_consultation', methods=['POST'])
def api_schedule_consultation():
    """API endpoint to schedule a consultation"""
    return jsonify({
        'success': False,
        'error': 'Consultation routes temporarily disabled'
    }), 503

@consultation.route('/api/get_consultation_details', methods=['GET'])
def api_get_consultation_details():
    """API endpoint to get consultation details"""
    return jsonify({
        'success': False,
        'error': 'Consultation routes temporarily disabled'
    }), 503

@consultation.route('/api/update_consultation_status', methods=['POST'])
def api_update_consultation_status():
    """API endpoint to update consultation status"""
    return jsonify({
        'success': False,
        'error': 'Consultation routes temporarily disabled'
    }), 503

@consultation.route('/api/get_stage2_status', methods=['GET'])
def api_get_stage2_status():
    """API endpoint to get Stage 2 status"""
    return jsonify({
        'success': False,
        'error': 'Consultation routes temporarily disabled'
    }), 503 