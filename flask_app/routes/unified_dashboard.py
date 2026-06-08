"""
Unified Dashboard Routes
Clean implementation of unified dashboard functionality
"""

from flask import Blueprint, request, render_template, jsonify, url_for
from flask_login import login_required, current_user
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
import json
import logging
import re
from datetime import datetime

# Import your existing models and utilities
from ..models import ConsultationRequest, ConversionQuiz, VizBrizQuiz, Patient, File, Clinic, Dentist, DSO, dentist_clinic_association, AdminFile
import os
from .. import db
from .conversion_quiz_agent import generate_presigned_url_for_viewing

# Create blueprint
unified_bp = Blueprint('unified', __name__)

# Get logger
logger = logging.getLogger(__name__)


@unified_bp.route('/unified-dashboard')
@login_required
def unified_dashboard():
    """Unified dashboard showing both consultation requests and quiz submissions"""
    try:
        # Initialize counters
        consultation_count = 0
        consultation_skipped_no_patient = 0
        quiz_count = 0
        quiz_skipped_no_patient = 0
        
        # Get filter parameters
        status_filter = request.args.get('status', 'all')
        date_filter = request.args.get('date_range', 'all')
        risk_filter = request.args.get('risk_level', 'all')
        search_query = request.args.get('search', '')
        
        logger.info(f"Unified dashboard filters: status={status_filter}, date={date_filter}, risk={risk_filter}, search={search_query}")
        
        # Apply the same security logic as patient_list to get patients user can access
        normalized_status = func.lower(func.trim(Patient.status))
        # Build query filter for accessible patients based on user role
        if current_user.role == 'admin':
            # Admin can see all patients
            accessible_patient_filter = db.and_(
                db.or_(Patient.status.is_(None), normalized_status != 'archived'),
                Patient.id.isnot(None),
                Patient.id > 0
            )
            logger.info("Admin user - showing all patients")
        elif current_user.role in ['Dentist', 'dentist', 'Dentists']:
            # Dentist can only see patients associated with the same clinic(s) as the dentist
            dentist_clinic_ids = current_user.get_clinic_ids()
            dentist_dso_ids = current_user.get_dso_ids() if hasattr(current_user, 'get_dso_ids') else []
            
            logger.info(f"Dentist {current_user.name} attempting to view unified dashboard based on clinic associations.")
            logger.info(f"Dentist is associated with clinics: {dentist_clinic_ids}")
            
            if dentist_clinic_ids:
                # Show patients from the dentist's associated clinics (handles multi-DSO clinics)
                accessible_patient_filter = db.and_(
                    db.or_(
                        # Patients directly assigned to dentist's clinics
                        Patient.clinic_id.in_(dentist_clinic_ids),
                        # Patients whose dentists work at the same clinics
                        db.and_(
                            Patient.clinic_id.is_(None),
                            Patient.dentist_id.isnot(None),
                            db.exists().where(
                                db.and_(
                                    dentist_clinic_association.c.dentist_id == Patient.dentist_id,
                                    dentist_clinic_association.c.clinic_id.in_(dentist_clinic_ids)
                                )
                            )
                        )
                    ),
                    db.or_(Patient.status.is_(None), normalized_status != 'archived'),
                    Patient.id.isnot(None),
                    Patient.id > 0
                )
                logger.info(f"Dentist works at clinics: {dentist_clinic_ids}")
            else:
                # No clinic associations found - try DSO fallback
                logger.warning(f'Dentist {current_user.name} has no clinic associations, trying DSO fallback')
                if dentist_dso_ids:
                    # Fallback to DSO-based query - need to join Clinic table
                    # For consultation requests, we'll filter after joining
                    # Store DSO info for later filtering
                    accessible_patient_filter = db.and_(
                        db.or_(Patient.status.is_(None), normalized_status != 'archived'),
                        Patient.id.isnot(None),
                        Patient.id > 0
                    )
                    logger.info(f"DSO fallback - dentist associated with DSOs: {dentist_dso_ids}")
                else:
                    # No associations at all - show no patients
                    logger.warning(f'Dentist {current_user.name} has no clinic or DSO associations')
                    accessible_patient_filter = db.and_(Patient.id < 0)  # Impossible condition - no patients
        else:
            # Other users get no patients
            logger.warning(f'Unauthorized user {current_user.name} with role {current_user.role}')
            accessible_patient_filter = db.and_(Patient.id < 0)  # Impossible condition - no patients
        
        # SUBMISSION-CENTRIC APPROACH: Start with actual submissions, then get patient info
        logger.info("=== SUBMISSION-CENTRIC APPROACH ===")
        
        unified_items = []
        
        # Get all consultation requests with patient info (only accessible patients)
        logger.info("Processing consultation requests...")
        query = ConsultationRequest.query.join(Patient, ConsultationRequest.patient_id == Patient.id)
        
        # Apply DSO fallback join if needed
        if current_user.role in ['Dentist', 'dentist', 'Dentists']:
            dentist_clinic_ids = current_user.get_clinic_ids()
            dentist_dso_ids = current_user.get_dso_ids() if hasattr(current_user, 'get_dso_ids') else []
            if not dentist_clinic_ids and dentist_dso_ids:
                # Need to join Clinic and Dentist for DSO fallback
                query = query.outerjoin(Clinic, Patient.clinic_id == Clinic.id).outerjoin(Dentist, Patient.dentist_id == Dentist.id)
        
        consultation_requests = query.filter(accessible_patient_filter).all()
        
        # Additional filtering for DSO fallback case
        if current_user.role in ['Dentist', 'dentist', 'Dentists']:
            dentist_clinic_ids = current_user.get_clinic_ids()
            dentist_dso_ids = current_user.get_dso_ids() if hasattr(current_user, 'get_dso_ids') else []
            if not dentist_clinic_ids and dentist_dso_ids:
                # Filter by DSO for fallback case
                filtered_requests = []
                for req in consultation_requests:
                    patient = req.patient
                    is_accessible = False
                    if patient.clinic_id:
                        clinic = Clinic.query.get(patient.clinic_id)
                        if clinic and clinic.dso_id in dentist_dso_ids:
                            is_accessible = True
                    elif patient.dentist_id and getattr(current_user, 'DSO', None):
                        patient_dentist = Dentist.query.get(patient.dentist_id)
                        if patient_dentist and patient_dentist.DSO == getattr(current_user, 'DSO', None):
                            is_accessible = True
                    if is_accessible:
                        filtered_requests.append(req)
                consultation_requests = filtered_requests
                logger.info(f"After DSO fallback filtering: {len(consultation_requests)} consultation requests")
        
        logger.info(f"Found {len(consultation_requests)} consultation requests with valid patients")
        
        for req in consultation_requests:
            patient = req.patient
            # Additional validation to ensure patient exists and is valid
            if not patient or not patient.id or patient.id <= 0:
                logger.warning(f"Invalid patient for consultation {req.id}")
                continue
            logger.info(f"Processing consultation {req.id} for patient {patient.name} ({patient.email})")
            
            unified_items.append({
                'id': f"consultation_{req.id}",
                'type': 'consultation',
                'patient_name': patient.name,
                'patient_email': patient.email,
                'patient_phone': patient.phone,
                'submitted_at': req.submitted_at,
                'status': req.status,
                'patient_id': patient.id,
                'patient_status': patient.status,
                'notes': req.comment
            })
            logger.info(f"Added consultation {req.id} for patient {patient.name} with status: {req.status}")
        
        # Get all ConversionQuiz submissions (from conversion_quiz table)
        logger.info("Processing ConversionQuiz submissions...")
        # Limit to most recent 10 quizzes per patient to avoid performance issues
        # Get all quizzes, then limit per patient
        patient_quiz_limits = {}
        for quiz in ConversionQuiz.query.filter(
            ConversionQuiz.patient_email.isnot(None),
            ConversionQuiz.patient_email != ''
        ).order_by(ConversionQuiz.created_at.desc()).all():
            patient_email = quiz.patient_email
            if patient_email not in patient_quiz_limits:
                patient_quiz_limits[patient_email] = []
            if len(patient_quiz_limits[patient_email]) < 10:
                patient_quiz_limits[patient_email].append(quiz)
        
        quiz_submissions = []
        for patient_email, quizzes in patient_quiz_limits.items():
            quiz_submissions.extend(quizzes)
        
        logger.info(f"Limited to {len(quiz_submissions)} ConversionQuiz submissions (max 10 per patient)")
        
        for quiz in quiz_submissions:
            logger.info(f"Processing ConversionQuiz {quiz.id}: patient_email={quiz.patient_email}, created_at={quiz.created_at}")
            
            # Try to find patient by email (case-insensitive)
            patient = None
            if quiz.patient_email:
                # Use case-insensitive email matching
                patient = Patient.query.filter(func.lower(Patient.email) == func.lower(quiz.patient_email)).first()
                if patient:
                    logger.info(f"Found patient by email (case-insensitive) {quiz.patient_email}: {patient.name} (ID: {patient.id})")
                else:
                    logger.warning(f"Patient not found for ConversionQuiz {quiz.id} - email: {quiz.patient_email}")
            
            patient_status_normalized = (patient.status or '').strip().lower() if patient else None
            if (not patient or not patient.id or patient.id <= 0 or patient_status_normalized == 'archived'):
                logger.warning(f"Skipping ConversionQuiz {quiz.id} - patient_email={quiz.patient_email}, patient_found={patient is not None}, patient_status={patient_status_normalized if patient else 'N/A'}")
                continue
            
            logger.info(f"Found patient for ConversionQuiz {quiz.id}: {patient.name} (ID: {patient.id}, email: {patient.email})")
            
            # Check if patient is accessible (apply the same filter logic)
            # For admin: all patients are accessible
            # For dentist: check clinic associations
            if current_user.role == 'admin':
                # Admin can see all patients
                is_accessible = True
                logger.info(f"Admin user - ConversionQuiz {quiz.id} for patient {patient.name} is accessible")
            elif current_user.role in ['Dentist', 'dentist', 'Dentists']:
                dentist_clinic_ids = current_user.get_clinic_ids()
                dentist_dso_ids = current_user.get_dso_ids() if hasattr(current_user, 'get_dso_ids') else []
                is_accessible = False
                
                if dentist_clinic_ids:
                    # Check if patient is in dentist's clinics
                    if patient.clinic_id and patient.clinic_id in dentist_clinic_ids:
                        is_accessible = True
                        logger.info(f"Patient {patient.name} is in dentist's clinic {patient.clinic_id}")
                    elif patient.dentist_id:
                        # Check if patient's dentist works at the same clinics
                        from sqlalchemy import exists
                        clinic_association_exists = db.session.query(exists().where(
                            db.and_(
                                dentist_clinic_association.c.dentist_id == patient.dentist_id,
                                dentist_clinic_association.c.clinic_id.in_(dentist_clinic_ids)
                            )
                        )).scalar()
                        if clinic_association_exists:
                            is_accessible = True
                            logger.info(f"Patient's dentist {patient.dentist_id} works at dentist's clinic")
                else:
                    # DSO fallback
                    if dentist_dso_ids and patient.clinic_id:
                        clinic = Clinic.query.get(patient.clinic_id)
                        if clinic and clinic.dso_id in dentist_dso_ids:
                            is_accessible = True
                            logger.info(f"Patient {patient.name} is in DSO {clinic.dso_id}")
                    elif patient.dentist_id and getattr(current_user, 'DSO', None):
                        patient_dentist = Dentist.query.get(patient.dentist_id)
                        if patient_dentist and patient_dentist.DSO == getattr(current_user, 'DSO', None):
                            is_accessible = True
                            logger.info(f"Patient's dentist {patient.dentist_id} is in same DSO")
                
                if not is_accessible:
                    logger.warning(f"Patient {patient.name} (ID: {patient.id}, clinic_id: {patient.clinic_id}) is NOT accessible to dentist {current_user.name} (clinics: {dentist_clinic_ids}, DSOs: {dentist_dso_ids}) for ConversionQuiz {quiz.id}")
                    continue
            else:
                # Other users - no access
                logger.warning(f"Patient {patient.name} not accessible to user {current_user.name} (role: {current_user.role}) for ConversionQuiz {quiz.id}")
                continue
            
            logger.info(f"ConversionQuiz {quiz.id} for patient {patient.name} passed accessibility check")
                
            logger.info(f"Processing quiz {quiz.id} for patient {patient.name} ({patient.email})")
            
            # Determine quiz type
            quiz_type = 'basic_quiz'  # Default
            if quiz.quiz_type:
                quiz_type = quiz.quiz_type
            
            # Get quiz files with presigned URLs
            quiz_files = get_quiz_files_with_presigned_urls(quiz.id, patient.id, quiz_type, submitted_at=quiz.created_at)
            
            # Parse AI response for risk level
            risk_level = 'Unknown'
            if quiz.ai_response:
                try:
                    ai_data = json.loads(quiz.ai_response)
                    risk_level = ai_data.get('risk_level', 'Unknown')
                except:
                    pass
            
            files_list = []
            for f in quiz_files:
                files_list.append({'name': f['name'], 'url': f['view_url']})

                # Group files by type for display
                grouped_files = group_files_by_name(quiz_files)

                unified_items.append({
                'id': f"quiz_{quiz.id}",
                'type': 'quiz',
                'quiz_id': quiz.id,
                'patient_name': patient.name,
                'patient_email': patient.email,
                'patient_phone': patient.phone,
                'submitted_at': quiz.created_at,
                'status': 'completed',
                'patient_id': patient.id,
            'patient_status': patient.status,
                'quiz_type': quiz_type,
                'quiz_name': f"Quiz {quiz.id}",
                'risk_level': risk_level,
                'quiz_files': quiz_files,
                'files': files_list,
                'file_groups': grouped_files,
                'quiz_view_url': url_for('conversion_quiz_agent.submission_details', submission_id=quiz.id),
                'quiz_answers_url': url_for('conversion_quiz_agent.download_quiz_answers', submission_id=quiz.id),
                'ai_response': quiz.ai_response,
                'quiz_details': quiz.quiz_input
            })
            logger.info(f"Added quiz {quiz.id} for patient {patient.name} with risk level: {risk_level}")
        
        # Get all VizBrizQuiz submissions
        logger.info("Processing VizBrizQuiz submissions...")
        vizbriz_quiz_limits = {}
        for quiz in VizBrizQuiz.query.filter(
            VizBrizQuiz.patient_email.isnot(None),
            VizBrizQuiz.patient_email != ''
        ).order_by(VizBrizQuiz.created_at.desc()).all():
            patient_email = quiz.patient_email
            if patient_email not in vizbriz_quiz_limits:
                vizbriz_quiz_limits[patient_email] = []
            if len(vizbriz_quiz_limits[patient_email]) < 10:
                vizbriz_quiz_limits[patient_email].append(quiz)
        
        vizbriz_submissions = []
        for patient_email, quizzes in vizbriz_quiz_limits.items():
            vizbriz_submissions.extend(quizzes)
        
        logger.info(f"Limited to {len(vizbriz_submissions)} VizBrizQuiz submissions (max 10 per patient)")
        
        for quiz in vizbriz_submissions:
            logger.info(f"Processing VizBrizQuiz {quiz.id}: patient_email={quiz.patient_email}, created_at={quiz.created_at}")
            
            # Try to find patient by email (case-insensitive)
            patient = None
            if quiz.patient_email:
                patient = Patient.query.filter(func.lower(Patient.email) == func.lower(quiz.patient_email)).first()
                if patient:
                    logger.info(f"Found patient by email for VizBrizQuiz {quiz.id}: {patient.name} (ID: {patient.id})")
                else:
                    logger.warning(f"Patient not found for VizBrizQuiz {quiz.id} - email: {quiz.patient_email}")
            
            # Also try by user_id if patient not found by email
            if not patient and quiz.user_id:
                patient = Patient.query.get(quiz.user_id)
                if patient:
                    logger.info(f"Found patient by user_id for VizBrizQuiz {quiz.id}: {patient.name} (ID: {patient.id})")
            
            if not patient or not patient.id or patient.id <= 0:
                logger.warning(f"Skipping VizBrizQuiz {quiz.id} - no valid patient found")
                continue
            
            # Check if patient is accessible (apply the same filter logic)
            if current_user.role == 'admin':
                is_accessible = True
            elif current_user.role in ['Dentist', 'dentist', 'Dentists']:
                dentist_clinic_ids = current_user.get_clinic_ids()
                dentist_dso_ids = current_user.get_dso_ids() if hasattr(current_user, 'get_dso_ids') else []
                is_accessible = False
                
                if dentist_clinic_ids:
                    if patient.clinic_id and patient.clinic_id in dentist_clinic_ids:
                        is_accessible = True
                    elif patient.dentist_id:
                        from sqlalchemy import exists
                        clinic_association_exists = db.session.query(exists().where(
                            db.and_(
                                dentist_clinic_association.c.dentist_id == patient.dentist_id,
                                dentist_clinic_association.c.clinic_id.in_(dentist_clinic_ids)
                            )
                        )).scalar()
                        if clinic_association_exists:
                            is_accessible = True
                else:
                    if dentist_dso_ids and patient.clinic_id:
                        clinic = Clinic.query.get(patient.clinic_id)
                        if clinic and clinic.dso_id in dentist_dso_ids:
                            is_accessible = True
                
                if not is_accessible:
                    logger.warning(f"Patient {patient.name} (ID: {patient.id}) is NOT accessible for VizBrizQuiz {quiz.id}")
                    continue
            else:
                logger.warning(f"Patient {patient.name} not accessible to user {current_user.name} for VizBrizQuiz {quiz.id}")
                continue
            
            # Parse risk level from VizBrizQuiz
            risk_level = quiz.risk_band or 'Unknown'
            
            # Get quiz type
            quiz_type = quiz.quiz_type or 'vizbriz_sleep_v1'
            
            unified_items.append({
                'id': f"vizbriz_quiz_{quiz.id}",
                'type': 'vizbriz_quiz',
                'quiz_id': quiz.id,
                'patient_name': patient.name,
                'patient_email': patient.email,
                'patient_phone': patient.phone,
                'submitted_at': quiz.created_at,
                'status': 'completed',
                'patient_id': patient.id,
                'patient_status': patient.status,
                'quiz_type': quiz_type,
                'quiz_name': f"VizBriz Quiz {quiz.id}",
                'risk_level': risk_level,
                'risk_band': quiz.risk_band,
                'total_score': quiz.total_score,
                'red_flags': quiz.red_flags,
                'clinic_email': quiz.clinic_email,
                'ai_response': quiz.ai_response,
                'quiz_details': quiz.quiz_input
            })
            logger.info(f"Added VizBrizQuiz {quiz.id} for patient {patient.name} with risk level: {risk_level}")
        
        logger.info(f"Total unified_items created: {len(unified_items)}")

        # Get Level 1 reports from adminfiles table (instead of VizBrizQuiz)
        logger.info("Processing Level 1 reports from adminfiles...")
        try:
            # Get Level 1 reports, limit to most recent 10 per patient to avoid performance issues
            patient_report_limits = {}
            for admin_file in AdminFile.query.filter(
                AdminFile.patient_id.isnot(None),
                AdminFile.file_category.like('%Level 1%')
            ).order_by(AdminFile.upload_date.desc()).all():
                patient_id = admin_file.patient_id
                if patient_id not in patient_report_limits:
                    patient_report_limits[patient_id] = []
                if len(patient_report_limits[patient_id]) < 10:
                    patient_report_limits[patient_id].append(admin_file)
            
            level1_reports = []
            for patient_id, reports in patient_report_limits.items():
                level1_reports.extend(reports)
            
            logger.info(f"Limited to {len(level1_reports)} Level 1 reports (max 10 per patient)")
            
            for admin_file in level1_reports:
                patient = Patient.query.get(admin_file.patient_id)
                if not patient:
                    logger.warning(f"Patient not found for Level 1 report {admin_file.id} - patient_id: {admin_file.patient_id}")
                    continue
                
                patient_status_normalized = (patient.status or '').strip().lower()
                if (not patient.id or patient.id <= 0 or patient_status_normalized == 'archived'):
                    logger.warning(f"Skipping Level 1 report {admin_file.id} - patient {patient.name} is archived or invalid")
                    continue
                
                logger.info(f"Processing Level 1 report {admin_file.id} for patient {patient.name} (ID: {patient.id})")
                
                # Check if patient is accessible (apply the same filter logic)
                if current_user.role == 'admin':
                    is_accessible = True
                elif current_user.role in ['Dentist', 'dentist', 'Dentists']:
                    dentist_clinic_ids = current_user.get_clinic_ids()
                    dentist_dso_ids = current_user.get_dso_ids() if hasattr(current_user, 'get_dso_ids') else []
                    is_accessible = False
                    
                    if dentist_clinic_ids:
                        if patient.clinic_id and patient.clinic_id in dentist_clinic_ids:
                            is_accessible = True
                        elif patient.dentist_id:
                            from sqlalchemy import exists
                            clinic_association_exists = db.session.query(exists().where(
                                db.and_(
                                    dentist_clinic_association.c.dentist_id == patient.dentist_id,
                                    dentist_clinic_association.c.clinic_id.in_(dentist_clinic_ids)
                                )
                            )).scalar()
                            if clinic_association_exists:
                                is_accessible = True
                    else:
                        if dentist_dso_ids and patient.clinic_id:
                            clinic = Clinic.query.get(patient.clinic_id)
                            if clinic and clinic.dso_id in dentist_dso_ids:
                                is_accessible = True
                        elif patient.dentist_id and getattr(current_user, 'DSO', None):
                            patient_dentist = Dentist.query.get(patient.dentist_id)
                            if patient_dentist and patient_dentist.DSO == getattr(current_user, 'DSO', None):
                                is_accessible = True
                    
                    if not is_accessible:
                        logger.warning(f"Patient {patient.name} (ID: {patient.id}) is NOT accessible for Level 1 report {admin_file.id}")
                        continue
                else:
                    logger.warning(f"Patient {patient.name} not accessible to user {current_user.name} for Level 1 report {admin_file.id}")
                    continue
                
                # Generate presigned URL for the report
                try:
                    report_url = generate_presigned_url_for_viewing(admin_file.s3_key, inline=True, expires_in=3600) if admin_file.s3_key else None
                except Exception as e:
                    logger.warning(f"Could not generate presigned URL for Level 1 report {admin_file.id}: {e}")
                    report_url = None
                
                unified_items.append({
                    'id': f"level1_report_{admin_file.id}",
                    'type': 'report',
                    'report_id': admin_file.id,
                    'patient_name': patient.name,
                    'patient_email': patient.email,
                    'patient_phone': patient.phone,
                    'submitted_at': admin_file.upload_date,
                    'status': 'completed',
                    'patient_id': patient.id,
                    'patient_status': patient.status,
                    'report_name': admin_file.name,
                    'report_category': admin_file.file_category,
                    'report_url': report_url,
                    's3_key': admin_file.s3_key,
                    'file_size': admin_file.file_size
                })
                logger.info(f"Added Level 1 report {admin_file.id} ({admin_file.name}) for patient {patient.name}")
        except Exception as e:
            logger.error(f"Error processing Level 1 reports: {str(e)}", exc_info=True)
        
        # Group items by patient_id (most reliable), fallback to email if patient_id is missing
        # This ensures each patient gets their own group, even if they share an email
        patient_groups = {}
        for item in unified_items:
            patient_id = item.get('patient_id')
            patient_email = item['patient_email']
            # Use patient_id as primary key (most reliable), fallback to email if no patient_id
            group_key = patient_id if patient_id else patient_email
            
            if group_key not in patient_groups:
                patient_groups[group_key] = {
                    'patient_name': item['patient_name'],
                    'patient_email': patient_email,
                    'patient_phone': item.get('patient_phone', ''),
                    'patient_id': patient_id,
                    'patient_status': item.get('patient_status'),
                    'submissions': [],
                    'latest_submission': item['submitted_at'],
                    'total_submissions': 0,
                    'cta_summary': None,
                    'has_consultation': False,
                    'consultation_status': None,
                    'consultation_id': None,
                    # Additional fields for template
                    'clinic_email': 'info@vizbriz.com',  # Default clinic email
                    'quiz_type': None,
                    'latest_quiz_date': None,
                    'risk_level': None,
                    'latest_action': None,
                    'latest_action_date': None,
                    'created_date': item['submitted_at'],
                    'submission_count': 0
                }
            
            patient_groups[group_key]['submissions'].append(item)
            patient_groups[group_key]['total_submissions'] += 1
            patient_groups[group_key]['submission_count'] = patient_groups[group_key]['total_submissions']

            status_value = item.get('patient_status')
            if status_value:
                patient_groups[group_key]['patient_status'] = status_value
            
            # Update latest submission date
            if item['submitted_at'] > patient_groups[group_key]['latest_submission']:
                patient_groups[group_key]['latest_submission'] = item['submitted_at']
                patient_groups[group_key]['created_date'] = item['submitted_at']
            
            # Set quiz type and risk level from the MOST RECENT quiz submission
            if item['type'] in ['quiz', 'vizbriz_quiz']:
                latest_quiz_date = patient_groups[group_key].get('latest_quiz_date')
                if latest_quiz_date is None or item['submitted_at'] >= latest_quiz_date:
                    patient_groups[group_key]['quiz_type'] = item.get('quiz_type', 'vizbriz_sleep_v1' if item['type'] == 'vizbriz_quiz' else 'advanced_quiz')
                    patient_groups[group_key]['latest_quiz_date'] = item['submitted_at']
                    # Update risk level from the most recent quiz, normalize it
                    raw_risk_level = item.get('risk_level') or item.get('risk_band') or 'Unknown'
                    normalized_risk = normalize_risk_level(raw_risk_level)
                    patient_groups[group_key]['risk_level'] = normalized_risk
                    logger.info(f"Normalized risk_level for patient {patient_id or patient_email}: '{raw_risk_level}' -> '{normalized_risk}'")
            
            # Track consultation status for this patient (use the most recent consultation)
            if item['type'] == 'consultation':
                # Always update consultation status when we find a consultation
                patient_groups[group_key]['has_consultation'] = True
                patient_groups[group_key]['consultation_status'] = item['status']
                # Extract the actual consultation ID from the item ID
                consultation_id = item['id'].replace('consultation_', '')
                patient_groups[group_key]['consultation_id'] = consultation_id
                logger.info(f"Set consultation status for patient {patient_id or patient_email}: {item['status']} (ID: {consultation_id})")
                logger.info(f"Patient group data after consultation update: {patient_groups[group_key]}")
        
        # After processing all items, ensure consultation status is properly set
        for group_key, group_data in patient_groups.items():
            patient_id = group_data.get('patient_id')
            patient_email = group_data.get('patient_email')
            if group_data['has_consultation']:
                logger.info(f"Patient {patient_id or patient_email} has consultation with status: {group_data['consultation_status']}")
            else:
                logger.info(f"Patient {patient_id or patient_email} has NO consultation")
        
        # Process CTA tracking for each patient group
        for group_key, group_data in patient_groups.items():
            try:
                from ..models import CTAInteractionLog
                patient_email = group_data['patient_email']
                cta_interactions = CTAInteractionLog.query.filter_by(
                    patient_email=patient_email
                ).order_by(CTAInteractionLog.created_at.desc()).all()
                
                # Categorize the CTA actions
                cta_summary = {
                    'scheduled_sleep_test': False,
                    'requested_consultation': False,
                    'completed_advanced': False,
                    'email_clicks': 0,
                    'web_clicks': 0,
                    'total_interactions': len(cta_interactions),
                    'latest_action': None,
                    'all_actions': []
                }
                
                for cta in cta_interactions:
                    # Count email vs web clicks
                    if cta.email_source:
                        cta_summary['email_clicks'] += 1
                    else:
                        cta_summary['web_clicks'] += 1
                    
                    # Track specific actions
                    if 'schedule' in cta.cta_type.lower() or 'sleep_test' in cta.cta_type.lower():
                        cta_summary['scheduled_sleep_test'] = True
                    elif 'consult' in cta.cta_type.lower() or 'consultation' in cta.cta_type.lower():
                        cta_summary['requested_consultation'] = True
                    elif 'advanced' in cta.cta_type.lower() or 'detailed' in cta.cta_type.lower():
                        cta_summary['completed_advanced'] = True
                    
                    # Store latest action
                    if not cta_summary['latest_action']:
                        cta_summary['latest_action'] = {
                            'type': cta.cta_type,
                            'date': cta.created_at,
                            'source': 'Email' if cta.email_source else 'Web'
                        }
                    
                    # Store all actions for detailed view
                    cta_summary['all_actions'].append({
                        'type': cta.cta_type,
                        'date': cta.created_at,
                        'source': 'Email' if cta.email_source else 'Web',
                        'text': cta.cta_text
                    })
                
                # If no CTA actions, check latest submission (quiz or consultation)
                if not cta_summary['latest_action'] and group_data['submissions']:
                    # Choose the submission with the most recent submitted_at timestamp
                    latest_submission = max(
                        group_data['submissions'],
                        key=lambda s: (s.get('submitted_at') or datetime.min)
                    )
                    if latest_submission['type'] in ['quiz', 'vizbriz_quiz']:
                        quiz_type = latest_submission.get('quiz_type', 'basic')
                        if 'vizbriz' in quiz_type.lower() or latest_submission['type'] == 'vizbriz_quiz':
                            cta_summary['latest_action'] = {
                                'type': 'VizBriz Quiz',
                                'date': latest_submission['submitted_at'],
                                'source': 'Web'
                            }
                        elif quiz_type == 'basic':
                            cta_summary['latest_action'] = {
                                'type': 'Basic Quiz',
                                'date': latest_submission['submitted_at'],
                                'source': 'Web'
                            }
                        elif quiz_type == 'advanced':
                            cta_summary['latest_action'] = {
                                'type': 'Advanced Quiz',
                                'date': latest_submission['submitted_at'],
                                'source': 'Web'
                            }
                        else:
                            cta_summary['latest_action'] = {
                                'type': 'Quiz',
                                'date': latest_submission['submitted_at'],
                                'source': 'Web'
                            }
                    elif latest_submission['type'] == 'consultation':
                        cta_summary['latest_action'] = {
                            'type': 'Consultation',
                            'date': latest_submission['submitted_at'],
                            'source': 'Web'
                        }
                
                # Include submissions as actions in total count
                submission_count = len(group_data.get('submissions', []))
                cta_summary['total_actions'] = cta_summary['total_interactions'] + submission_count
                group_data['cta_summary'] = cta_summary
                
                # Set latest action from CTA summary
                if cta_summary['latest_action']:
                    group_data['latest_action'] = cta_summary['latest_action']['type']
                    group_data['latest_action_date'] = cta_summary['latest_action']['date']
                
            except Exception as e:
                logger.warning(f"Could not fetch CTA interactions for {group_data.get('patient_email', group_key)}: {str(e)}")
                group_data['cta_summary'] = {
                    'scheduled_sleep_test': False,
                    'requested_consultation': False,
                    'completed_advanced': False,
                    'email_clicks': 0,
                    'web_clicks': 0,
                    'total_interactions': 0,
                    'total_actions': len(group_data.get('submissions', [])),
                    'latest_action': None,
                    'all_actions': []
                }
        
        # Fetch ALL comments for each patient group from PatientComment table (unified comment system)
        logger.info("Fetching unified comments for all patient groups from PatientComment table...")
        for group_key, group_data in patient_groups.items():
            try:
                from ..models import PatientComment
                patient_id = group_data.get('patient_id')
                patient_email = group_data.get('patient_email')
                # Try to get patient by ID first (more reliable), then by email
                if patient_id:
                    patient = Patient.query.get(patient_id)
                else:
                    patient = Patient.query.filter_by(email=patient_email).first()
                if patient:
                    # Fetch ALL comments for this patient (not only conversion)
                    comments = PatientComment.query.filter_by(
                        patient_id=patient.id
                    ).order_by(PatientComment.created_date.desc()).all()
                    group_data['comments'] = [
                        {
                            'id': comment.id,
                            'content': comment.content,
                            'created_date': comment.created_date,
                            'comment_type': comment.comment_type or 'general',
                            'is_urgent': comment.is_urgent or False,
                            'is_internal': comment.is_internal or False,
                            'dentist_name': comment.dentist.name if comment.dentist else 'Unknown'
                        }
                        for comment in comments
                    ]
                    logger.info(f"Found {len(comments)} conversion comments for patient {patient.name} (ID: {patient_id}, email: {patient_email})")
                else:
                    group_data['comments'] = []
                    logger.warning(f"No patient found for patient_id {patient_id} or email {patient_email}")
            except Exception as e:
                logger.warning(f"Could not fetch unified comments for patient {patient_id or patient_email}: {str(e)}")
                group_data['comments'] = []
        
        # Convert to list and sort by latest submission
        grouped_items = list(patient_groups.values())
        grouped_items.sort(key=lambda x: x['latest_submission'], reverse=True)
        
        # Build per-patient quiz groupings (Vizbriz/Advanced/Basic) one line per submission
        for group in grouped_items:
            # Build quiz groupings purely from files in DB (no submission aggregation)
            all_q_files = get_all_questionnaire_files(group.get('patient_id')) if group.get('patient_id') else []
            group['quiz_grouped'] = group_files_by_name(all_q_files)

        # Fetch consult schedules for each patient for compact display in expanded row
        try:
            from ..models import PatientConsultSchedule
            for group in grouped_items:
                patient_id = group.get('patient_id')
                schedule_items = []
                if patient_id:
                    try:
                        rows = PatientConsultSchedule.query.filter_by(patient_id=patient_id).order_by(PatientConsultSchedule.scheduled_datetime.desc()).all()
                        for sc in rows:
                            display_dt = sc.completed_datetime or sc.scheduled_datetime
                            schedule_items.append({
                                'consult_type': sc.consult_type,
                                'status': sc.status,
                                'doctor_name': sc.doctor_name,
                                'dt': display_dt
                            })
                    except Exception as e:
                        logger.warning(f"Could not fetch schedule for patient {patient_id}: {str(e)}")
                group['consult_schedule'] = schedule_items
        except Exception as e:
            logger.warning(f"Skipping consult schedule aggregation due to error: {str(e)}")

        # Calculate summary stats
        total_consultations = sum(1 for group in grouped_items for item in group['submissions'] if item['type'] == 'consultation')
        total_quizzes = sum(1 for group in grouped_items for item in group['submissions'] if item['type'] in ['quiz', 'vizbriz_quiz'])
        
        logger.info(f"Final stats: {len(grouped_items)} patient groups, {total_consultations} consultations, {total_quizzes} quizzes")
        
        # Get base_url from environment
        import os
        base_url = os.getenv('BASE_URL', 'https://app.vizbriz.com')
        
        return render_template('unified_dashboard.html',
                                 unified_items=grouped_items,
                                 patient_groups=grouped_items,  # Keep both for compatibility
                                 total_consultations=total_consultations,
                                 total_quizzes=total_quizzes,
                                 base_url=base_url,  # Pass base_url to template
                                 summary_stats={
                                     'total_items': len(grouped_items),
                                     'total_consultations': total_consultations,
                                     'total_quizzes': total_quizzes,
                                     'pending_consultations': sum(1 for group in grouped_items if group.get('consultation_status') == 'pending')
                                 },
                                 current_filters={
                                     'status': status_filter,
                                     'date_range': date_filter,
                                     'risk_level': risk_filter,
                                     'search': search_query
                                 })
    except Exception as e:
        logger.error(f"Error in unified_dashboard: {str(e)}")
        return render_template('error.html', error=str(e))


@unified_bp.route('/forms/update-consultation-status/<int:request_id>', methods=['POST'])
def update_consultation_status(request_id):
    """Update consultation request status"""
    try:
        logger.info(f"Updating consultation status for request_id: {request_id}")
        data = request.get_json()
        logger.info(f"Received data: {data}")
        new_status = data.get('status')
        logger.info(f"New status: {new_status}")
        
        if not new_status:
            logger.warning("No status provided")
            return jsonify({'success': False, 'message': 'Status is required'}), 400
        
        # Validate status
        if new_status not in ['pending', 'contacted', 'completed']:
            logger.warning(f"Invalid status: {new_status}")
            return jsonify({'success': False, 'message': 'Invalid status'}), 400
        
        # Get the consultation request
        consultation_request = ConsultationRequest.query.get(request_id)
        if not consultation_request:
            logger.warning(f"Consultation request not found: {request_id}")
            return jsonify({'success': False, 'message': 'Consultation request not found'}), 404
        
        logger.info(f"Found consultation request: {consultation_request.id}, current status: {consultation_request.status}")
        
        # Update status
        consultation_request.status = new_status
        from .. import db
        db.session.commit()
        
        logger.info(f"Successfully updated status to: {new_status}")
        return jsonify({'success': True, 'message': 'Status updated successfully'})
        
    except Exception as e:
        logger.error(f"Error updating consultation status: {str(e)}")
        return jsonify({'success': False, 'message': f'Error updating status: {str(e)}'}), 500


@unified_bp.route('/forms/api/generate-patient-narrative/<patient_email>', methods=['POST'])
def generate_patient_narrative_by_email(patient_email):
    """Generate a personalized patient communication narrative based on patient email"""
    try:
        logger.info(f"Generating narrative for patient email: {patient_email}")
        
        # Find the patient by email
        patient = Patient.query.filter(func.lower(Patient.email) == patient_email.lower()).first()
        if not patient:
            return jsonify({
                'success': False, 
                'message': 'Patient not found. Unable to generate narrative.'
            }), 404
        
        # Get patient name
        patient_name = patient.name
        patient_id = patient.id
        
        # Try to get canonical data from PatientCaseEnvelope
        canonical_data = None
        try:
            from ..models import PatientCaseEnvelope
            canonical_envelope = PatientCaseEnvelope.query.filter_by(
                patient_id=patient_id, 
                report_id='canonical'
            ).first()
            
            if canonical_envelope and canonical_envelope.case_json:
                # Parse the JSON string into a Python dictionary
                if isinstance(canonical_envelope.case_json, str):
                    canonical_data = json.loads(canonical_envelope.case_json)
                else:
                    canonical_data = canonical_envelope.case_json
                logger.info(f"Loaded canonical data for patient {patient_id}")
            else:
                logger.info(f"No canonical data found for patient {patient_id}")
        except Exception as e:
            logger.error(f"Error loading canonical data: {e}")
        
        # Note: Risk level is stored in canonical data's risk_assessment field
        # (populated from observation_store during canonical creation)
        # We'll read it from there when generating narratives from canonical data
        
        # Fetch quiz data for fallback scenarios
        # Get the most recent VizBrizQuiz (highest priority)
        vizbriz_quiz = VizBrizQuiz.query.filter_by(
            patient_email=patient_email
        ).order_by(VizBrizQuiz.created_at.desc()).first()
        
        # Fall back to ConversionQuiz if no VizBrizQuiz exists
        latest_quiz = None
        if not vizbriz_quiz:
            latest_quiz = ConversionQuiz.query.filter_by(
                patient_email=patient_email
            ).order_by(ConversionQuiz.created_at.desc()).first()
        
        # Check if canonical data has useful information
        has_useful_canonical_data = False
        if canonical_data:
            # Check if canonical data has meaningful information
            diagnosis = canonical_data.get('diagnosis', {})
            sleep_study = canonical_data.get('sleep_study', {})
            
            # Consider canonical data useful if it has severity or AHI
            has_severity = diagnosis.get('severity') and diagnosis.get('severity') != 'Unknown'
            has_ahi = sleep_study.get('ahi') and sleep_study.get('ahi') != 'Not available'
            
            has_useful_canonical_data = has_severity or has_ahi
            
            if not has_useful_canonical_data:
                logger.info(f"Canonical data exists but is incomplete/empty for patient {patient_id}, will try quiz data")
        
        # If no canonical data or canonical data is not useful, fall back to quiz data
        # (we already fetched the quiz above for risk level)
        if not canonical_data or not has_useful_canonical_data:
            logger.info(f"No canonical data available, using quiz data...")
            
            # If neither quiz type exists, return error
            if not latest_quiz and not vizbriz_quiz:
                return jsonify({
                    'success': False, 
                    'message': 'No patient data found. Unable to generate personalized narrative.'
                }), 404
            
            # Process VizBrizQuiz data if available (highest priority)
            if vizbriz_quiz:
                logger.info(f"Using VizBrizQuiz data for narrative generation")
                # Parse VizBriz quiz data
                try:
                    quiz_answers = json.loads(vizbriz_quiz.quiz_input) if vizbriz_quiz.quiz_input else {}
                    ai_response_data = json.loads(vizbriz_quiz.ai_response) if vizbriz_quiz.ai_response else {}
                except json.JSONDecodeError:
                    # If JSON parsing fails, create basic structure
                    quiz_answers = {}
                    ai_response_data = {}
                
                # Extract key information from VizBriz quiz
                risk_level = vizbriz_quiz.risk_band or ai_response_data.get('risk_level', 'Unknown')
                risk_explanation = ai_response_data.get('risk_explanation', f'Risk band: {risk_level}')
                recommendations = ai_response_data.get('recommendations', [])
                quiz_type = vizbriz_quiz.quiz_type or 'vizbriz_sleep_v1'
                
                # For diagnosed patients, re-evaluate to get the correct risk_band
                raw_answers = quiz_answers.get('raw_answers', {})
                if raw_answers.get('Q1') == 'yes':  # Diagnosed patient
                    logger.info("Re-evaluating diagnosed patient to get correct risk_band")
                    try:
                        from flask_app.helpers.vizbriz_quiz_helpers import evaluate_quiz, get_localized_text
                        re_evaluation = evaluate_quiz(raw_answers, 'en')
                        risk_level = re_evaluation.get('risk_band', risk_level)
                        risk_label = re_evaluation.get('risk_label', risk_level)
                        
                        # Update risk_explanation with more descriptive text for diagnosed patients
                        if 'diagnosed' in risk_level:
                            risk_explanation = f"Patient is diagnosed with OSA but {risk_label.lower()}"
                        else:
                            risk_explanation = f"Risk assessment: {risk_label}"
                        
                        logger.info(f"Re-evaluated risk_band: {risk_level}, risk_label: {risk_label}")
                    except Exception as e:
                        logger.error(f"Error re-evaluating quiz: {e}")
                        # Keep the original risk_level if re-evaluation fails
                
                # Check if we have specific outcome messages from quiz evaluation
                # The quiz_input contains: {raw_answers, enhanced_answers, evaluation_summary}
                evaluation_summary = quiz_answers.get('evaluation_summary', {})
                outcome_title = evaluation_summary.get('outcome_title')
                outcome_body = evaluation_summary.get('outcome_body')
                cta_text = evaluation_summary.get('cta_text')
                
                # Also check if we need to re-evaluate to get the correct risk_band
                if not outcome_title or not outcome_body:
                    logger.info("No evaluation_summary found, will use LLM-generated narrative")
                else:
                    logger.info(f"Found evaluation_summary: {outcome_title}")
                
                # Always use LLM for narrative generation, but with correct assessment data
                logger.info(f"Generating LLM narrative with risk_level: {risk_level}")
                
                # Extract specific symptoms from quiz answers
                from .forms_management_routes import extract_patient_symptoms
                specific_symptoms = extract_patient_symptoms(quiz_answers)
                
                # Generate narrative from VizBriz quiz data using LLM
                from .forms_management_routes import generate_patient_communication_narrative
                narrative = generate_patient_communication_narrative(
                    patient_name,
                    patient_email,
                    quiz_answers,
                    risk_level,  # This now has the correct re-evaluated risk_band
                    risk_explanation,
                    recommendations,
                    '',
                    quiz_type
                )
            
            # Process ConversionQuiz data if VizBrizQuiz not available
            elif latest_quiz:
                logger.info(f"Using ConversionQuiz data for narrative generation")
                # Parse quiz data
                try:
                    quiz_answers = json.loads(latest_quiz.quiz_input) if latest_quiz.quiz_input else {}
                    ai_response_data = json.loads(latest_quiz.ai_response) if latest_quiz.ai_response else {}
                except json.JSONDecodeError:
                    return jsonify({
                        'success': False,
                        'message': 'Invalid quiz data format. Unable to generate narrative.'
                    }), 400
                
                # Extract key information from quiz
                risk_level = ai_response_data.get('risk_level', 'Unknown')
                risk_explanation = ai_response_data.get('risk_explanation', 'Risk assessment not available')
                recommendations = ai_response_data.get('recommendations', [])
                quiz_type = latest_quiz.quiz_type or 'basic_quiz'
                
                # Extract specific symptoms from quiz answers
                from .forms_management_routes import extract_patient_symptoms
                specific_symptoms = extract_patient_symptoms(quiz_answers)
                
                # Generate narrative from quiz data
                from .forms_management_routes import generate_patient_communication_narrative
                narrative = generate_patient_communication_narrative(
                    patient_name,
                    patient_email,
                    quiz_answers,
                    risk_level,
                    risk_explanation,
                    recommendations,
                    '',
                    quiz_type
                )
        else:
            # Try to generate narrative from canonical data using LLM
            try:
                from ..services.llm_service import get_llm_service
                llm_service = get_llm_service()
                
                # Check if Bedrock service is available
                if not llm_service.bedrock_service.is_available():
                    logger.warning("Bedrock service not available for canonical data narrative")
                    return jsonify({
                        'success': False,
                        'message': 'Dr. Briz is currently not available. Please try again in a few minutes.'
                    }), 503
                
                # Get risk level from canonical data's risk_assessment field (populated from observations)
                risk_assessment = canonical_data.get('risk_assessment', {})
                risk_level = risk_assessment.get('risk_level')
                
                # If not in canonical risk_assessment, fallback to fetching directly from quiz tables
                if not risk_level:
                    logger.info(f"No risk_assessment in canonical, fetching directly from quiz tables...")
                    logger.info(f"vizbriz_quiz exists: {vizbriz_quiz is not None}, latest_quiz exists: {latest_quiz is not None}")
                    
                    # Try VizBrizQuiz first (most reliable)
                    if vizbriz_quiz:
                        logger.info(f"VizBrizQuiz found - ID: {vizbriz_quiz.id}, risk_band: {vizbriz_quiz.risk_band}")
                        if vizbriz_quiz.risk_band:
                            risk_level = vizbriz_quiz.risk_band.upper()  # Normalize to uppercase
                            logger.info(f"✅ Risk level from VizBrizQuiz: {risk_level}")
                    
                    # If not found, try ConversionQuiz
                    if not risk_level and latest_quiz:
                        logger.info(f"ConversionQuiz found - ID: {latest_quiz.id}, has ai_response: {latest_quiz.ai_response is not None}")
                        if latest_quiz.ai_response:
                            try:
                                ai_response_data = json.loads(latest_quiz.ai_response)
                                risk_level = (
                                    ai_response_data.get('risk_level') or 
                                    ai_response_data.get('risk_band') or 
                                    ai_response_data.get('riskLevel') or
                                    ai_response_data.get('riskBand')
                                )
                                if risk_level:
                                    risk_level = risk_level.upper()  # Normalize to uppercase
                                    logger.info(f"✅ Risk level from ConversionQuiz: {risk_level}")
                            except Exception as e:
                                logger.error(f"Error parsing ConversionQuiz ai_response: {e}")
                    
                    # Last resort: calculate from AHI/severity
                    if not risk_level:
                        risk_level = extract_risk_level_from_canonical(canonical_data)
                        logger.info(f"Risk level calculated from AHI/severity: {risk_level}")
                else:
                    logger.info(f"Risk level from canonical risk_assessment: {risk_level}")
                
                # Generate narrative from canonical data using LLM only
                narrative = generate_narrative_from_canonical_data(
                    patient_name,
                    patient_email,
                    canonical_data,
                    risk_level  # Pass the risk level explicitly
                )
                
                # If narrative generation failed (returned error message), return service unavailable
                if not narrative or "Unable to generate" in narrative or "Dr. Briz" in narrative:
                    logger.warning("LLM narrative generation failed")
                    return jsonify({
                        'success': False,
                        'message': 'Dr. Briz is currently not available. Please try again in a few minutes.'
                    }), 503
                
            except ImportError as e:
                logger.error(f"Could not import LLM service: {e}")
                return jsonify({
                    'success': False,
                    'message': 'Dr. Briz is currently not available. Please try again in a few minutes.'
                }), 503
            
            # Use the same risk level we passed to the LLM
            # (already calculated above)
            risk_explanation = extract_risk_explanation_from_canonical(canonical_data)
            recommendations = extract_recommendations_from_canonical(canonical_data)
            quiz_type = 'canonical_data'  # Indicate this came from canonical data
            
            # Extract symptoms from canonical data
            specific_symptoms = extract_symptoms_from_canonical(canonical_data)
        
        return jsonify({
            'success': True,
            'narrative': narrative,
            'risk_level': risk_level,
            'risk_explanation': risk_explanation,
            'recommendations': recommendations,
            'quiz_type': quiz_type if 'quiz_type' in locals() else 'unknown',
            'specific_symptoms': specific_symptoms if 'specific_symptoms' in locals() else []
        })
        
    except Exception as e:
        logger.error(f"Error generating patient narrative: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'message': 'Failed to generate patient narrative. Please try again.'
        }), 500


def generate_narrative_from_canonical_data(patient_name, patient_email, canonical_data, risk_level):
    """Generate patient narrative from canonical case envelope data using Bedrock LLM ONLY
    
    Args:
        patient_name: Patient's name
        patient_email: Patient's email
        canonical_data: Canonical case envelope data
        risk_level: Risk level from quiz (preferred) or calculated from canonical data
    """
    try:
        # Extract relevant information from canonical data
        demographics = canonical_data.get('demographics', {})
        sleep_study = canonical_data.get('sleep_study', {})
        symptoms = canonical_data.get('symptoms', {})
        medical_history = canonical_data.get('medical_history', {})
        diagnosis = canonical_data.get('diagnosis', {})
        treatment_plan = canonical_data.get('treatment_plan', {})
        follow_up_plan = canonical_data.get('follow_up_plan', {})
        
        # Use Bedrock LLM service - NO FALLBACK
        from ..services.llm_service import get_llm_service
        llm_service = get_llm_service()
        
        # Check if Bedrock service is available
        if not llm_service.bedrock_service.is_available():
            logger.warning("Bedrock service not available - returning unavailable message")
            return "Dr. Briz is currently not available. Please try again in a few minutes."
        
        # Build a summary of patient information
        patient_summary = f"""
Patient Name: {patient_name}
Age: {demographics.get('age_years', 'Unknown')}
Gender: {demographics.get('gender', 'Unknown')}

Sleep Study Results:
- AHI: {sleep_study.get('ahi', 'Not available')}
- Severity: {diagnosis.get('severity', 'Unknown')}

RISK LEVEL (use this exact value): {risk_level}

Key Symptoms:
{json.dumps(symptoms, indent=2) if symptoms else 'No symptoms recorded'}

Medical History:
{json.dumps(medical_history, indent=2) if medical_history else 'No medical history recorded'}

Diagnosis:
{json.dumps(diagnosis, indent=2) if diagnosis else 'No diagnosis recorded'}

Treatment Plan:
{json.dumps(treatment_plan, indent=2) if treatment_plan else 'No treatment plan recorded'}

Follow-up Plan:
{json.dumps(follow_up_plan, indent=2) if follow_up_plan else 'No follow-up plan recorded'}
"""
        
        # Create system prompt
        system = """DO NOT WRITE LIFESTYLE ADVICE. DO NOT WRITE SLEEP HYGIENE TIPS. DO NOT WRITE RECOMMENDATIONS.

IMPORTANT: Use the EXACT "RISK LEVEL" value provided in the patient data. Do not interpret or change it.

THIS IS WRONG (DO NOT DO THIS):
"## Immediate Recommendations
1. Consistent Sleep Schedule: Aim to go to bed at the same time
2. Limit caffeine after 2 PM
3. Keep bedroom cool (65-68°F)"

THIS IS CORRECT (DO THIS):

SECTION: OPENING
Hi Sarah, thanks for completing your sleep assessment.

SECTION: ASSESSMENT RESULTS
Your sleep study shows an AHI of 18 with moderate sleep apnea. You mentioned snoring and morning headaches.

SECTION: WHAT THIS MEANS
Your risk level is MODERATE. This means your breathing stops during sleep, affecting your energy and health.

SECTION: NEXT STEPS
I'd like to schedule you for a consultation with iSleep Physicians this week. Would mornings or evenings work better?

SECTION: CLOSING
Does Wednesday or Thursday work?

RULES:
- Maximum 150 words
- Use the EXACT RISK LEVEL provided in the patient information
- NO ## headers
- NO numbered lists
- NO caffeine advice
- NO sleep hygiene
- NO bedroom temperature
- NO exercise tips
- ONLY state assessment data + schedule next step

Based on the RISK LEVEL provided:
- HIGH: Urgent sleep test needed
- MODERATE: Sleep test soon
- LOW: Monitoring only, no treatment

Write ONLY in plain conversational text. 5 sections: OPENING, ASSESSMENT RESULTS, WHAT THIS MEANS, NEXT STEPS, CLOSING."""
        
        # Create user message
        user_message = f"""PATIENT INFORMATION:
{patient_summary}

Write a short phone script following the format above. DO NOT include lifestyle advice, sleep hygiene tips, or caffeine recommendations. ONLY state the assessment data and schedule next steps."""
        
        # Call Bedrock LLM
        messages = [{
            "role": "user",
            "content": user_message
        }]
        
        # Get patient_id for logging
        patient_id = None
        try:
            from flask_app.models import Patient
            patient = Patient.query.filter_by(email=patient_email).first()
            if patient:
                patient_id = patient.id
        except Exception:
            pass  # Continue without patient_id if lookup fails
        
        result = llm_service._make_bedrock_call(
            messages=messages,
            max_tokens=300,  # Force brevity - 150 words max
            temperature=0.05,  # Very low temperature for maximum consistency
            system=system,
            patient_id=patient_id,
            endpoint="unified_dashboard"
        )
        
        if result.get('success'):
            narrative = result.get('response', '').strip()
            logger.info(f"Generated narrative from canonical data using Bedrock LLM for {patient_name}")
            return narrative
        else:
            # NO FALLBACK - Return unavailable message
            logger.warning(f"Bedrock LLM call failed: {result.get('message')}")
            return "Dr. Briz is currently not available. Please try again in a few minutes."
        
    except Exception as e:
        logger.error(f"Error generating narrative from canonical data: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        # NO FALLBACK - Return unavailable message
        return "Dr. Briz is currently not available. Please try again in a few minutes."


def generate_narrative_from_schema_data(patient_name, demographics, sleep_study, symptoms, diagnosis, treatment_plan, follow_up_plan):
    """Generate a readable narrative directly from schema data without LLM"""
    try:
        # Extract key information
        age = demographics.get('age_years', 'Unknown')
        gender = demographics.get('gender', 'Unknown')
        ahi = sleep_study.get('ahi', 'Not available')
        severity = diagnosis.get('severity', 'Unknown')
        
        # Build narrative parts
        narrative_parts = []
        
        # Greeting and basic info
        narrative_parts.append(f"Hi {patient_name}, this is the dental sleep team calling.")
        
        # Sleep study results
        if ahi and ahi != 'Not available':
            try:
                ahi_value = float(ahi)
                if severity and severity != 'Unknown':
                    narrative_parts.append(f"Your sleep study results show an AHI of {ahi}, which indicates {severity} sleep apnea.")
                else:
                    narrative_parts.append(f"Your sleep study results show an AHI of {ahi}.")
            except (ValueError, TypeError):
                if severity and severity != 'Unknown':
                    narrative_parts.append(f"Your sleep study results indicate {severity} sleep apnea.")
        elif severity and severity != 'Unknown':
            narrative_parts.append(f"Based on your assessment, you have {severity} sleep apnea.")
        
        # Symptoms (if available)
        if symptoms:
            symptom_list = []
            if isinstance(symptoms, dict):
                for key, value in symptoms.items():
                    if value and value not in [False, 'false', 'no', 'No']:
                        # Format key to be more readable
                        readable_key = key.replace('_', ' ').title()
                        symptom_list.append(readable_key)
            
            if symptom_list:
                if len(symptom_list) > 3:
                    narrative_parts.append(f"You've reported symptoms including {', '.join(symptom_list[:3])}, and others.")
                elif len(symptom_list) > 1:
                    narrative_parts.append(f"You've reported symptoms including {', '.join(symptom_list[:-1])} and {symptom_list[-1]}.")
                else:
                    narrative_parts.append(f"You've reported {symptom_list[0]}.")
        
        # Treatment plan
        if treatment_plan:
            planned_treatments = treatment_plan.get('planned_treatments', [])
            if planned_treatments:
                treatment_names = []
                for treatment in planned_treatments:
                    if isinstance(treatment, dict):
                        name = treatment.get('treatment_name') or treatment.get('name')
                        if name:
                            treatment_names.append(name)
                    elif isinstance(treatment, str):
                        treatment_names.append(treatment)
                
                if treatment_names:
                    narrative_parts.append(f"We'd like to discuss your treatment options, including {', '.join(treatment_names)}.")
        
        # Follow-up plan
        if follow_up_plan:
            evaluations = follow_up_plan.get('evaluations', [])
            if evaluations:
                narrative_parts.append("We need to schedule a follow-up appointment to review your progress and next steps.")
        
        # Default next step if nothing specific
        if len(narrative_parts) <= 2:  # Only greeting and basic info
            narrative_parts.append("We'd like to discuss your sleep health assessment and available treatment options with you.")
        
        # Closing
        narrative_parts.append("When would be a good time to schedule your appointment?")
        
        return " ".join(narrative_parts)
        
    except Exception as e:
        logger.error(f"Error generating narrative from schema: {str(e)}")
        return f"Hi {patient_name}, this is the dental sleep team calling about your sleep apnea assessment. We'd like to schedule a time to discuss your results and treatment options. When would be convenient for you?"


def extract_risk_level_from_canonical(canonical_data):
    """Extract risk level from canonical data"""
    try:
        diagnosis = canonical_data.get('diagnosis', {})
        severity = diagnosis.get('severity', '').lower()
        
        if 'severe' in severity:
            return 'HIGH'
        elif 'moderate' in severity:
            return 'MODERATE'
        elif 'mild' in severity:
            return 'LOW'
        
        # Check AHI if severity not available
        sleep_study = canonical_data.get('sleep_study', {})
        ahi = sleep_study.get('ahi')
        if ahi:
            try:
                ahi_value = float(ahi)
                if ahi_value >= 30:
                    return 'HIGH'
                elif ahi_value >= 15:
                    return 'MODERATE'
                elif ahi_value >= 5:
                    return 'LOW'
            except (ValueError, TypeError):
                pass
        
        return 'Unknown'
    except Exception as e:
        logger.error(f"Error extracting risk level: {e}")
        return 'Unknown'


def extract_risk_explanation_from_canonical(canonical_data):
    """Extract risk explanation from canonical data"""
    try:
        diagnosis = canonical_data.get('diagnosis', {})
        sleep_study = canonical_data.get('sleep_study', {})
        
        severity = diagnosis.get('severity', '')
        ahi = sleep_study.get('ahi', '')
        
        # Build explanation only if we have real data
        if severity and severity != 'Unknown':
            explanation = f"Patient has {severity} sleep apnea"
            if ahi and ahi != 'Not available':
                explanation += f" with an AHI of {ahi}"
            return explanation
        elif ahi and ahi != 'Not available':
            return f"Patient has sleep apnea with an AHI of {ahi}"
        else:
            return 'Sleep apnea assessment data available - please review patient file for details'
    except Exception as e:
        logger.error(f"Error extracting risk explanation: {e}")
        return 'Risk assessment not available'


def extract_recommendations_from_canonical(canonical_data):
    """Extract recommendations from canonical data"""
    try:
        recommendations = []
        
        treatment_plan = canonical_data.get('treatment_plan', {})
        follow_up_plan = canonical_data.get('follow_up_plan', {})
        
        # Extract treatment recommendations
        if treatment_plan:
            planned_treatments = treatment_plan.get('planned_treatments', [])
            for treatment in planned_treatments:
                if isinstance(treatment, dict):
                    treatment_name = treatment.get('treatment_name') or treatment.get('name')
                    if treatment_name:
                        recommendations.append(treatment_name)
                elif isinstance(treatment, str):
                    recommendations.append(treatment)
        
        # Extract follow-up recommendations
        if follow_up_plan:
            evaluations = follow_up_plan.get('evaluations', [])
            for evaluation in evaluations:
                if isinstance(evaluation, dict):
                    eval_type = evaluation.get('type') or evaluation.get('evaluation_type')
                    if eval_type and isinstance(eval_type, str):
                        recommendations.append(f"Follow-up: {eval_type}")
                elif isinstance(evaluation, str):
                    recommendations.append(f"Follow-up: {evaluation}")
        
        return recommendations if recommendations else ['Consult with sleep specialist']
    except Exception as e:
        logger.error(f"Error extracting recommendations: {e}")
        return ['Consult with sleep specialist']


def extract_symptoms_from_canonical(canonical_data):
    """Extract relevant patient symptoms from canonical data - filtered and prioritized"""
    try:
        symptoms = []
        
        # Debug: Log the canonical data structure
        logger.info(f"Canonical data keys: {list(canonical_data.keys())}")
        
        # 1. Get symptoms from patient_self_report.symptoms section (patient-reported)
        patient_self_report = canonical_data.get('patient_self_report', {})
        logger.info(f"Patient self report: {patient_self_report}")
        patient_symptoms = patient_self_report.get('symptoms', {})
        logger.info(f"Patient symptoms: {patient_symptoms}")
        
        if isinstance(patient_symptoms, dict):
            for key, value in patient_symptoms.items():
                logger.info(f"Checking patient symptom: {key} = {value} (type: {type(value)})")
                if value and value not in [False, 'false', 'no', 'No', 'none', 'None']:
                    # Format the symptom name nicely
                    symptom_name = key.replace('_', ' ').title()
                    symptoms.append(symptom_name)
                    logger.info(f"Added patient symptom: {symptom_name}")
        
        # 2. Get primary complaint
        primary_complaint = patient_self_report.get('primary_complaint')
        if primary_complaint and primary_complaint.strip():
            if primary_complaint not in symptoms:
                symptoms.append(primary_complaint)
                logger.info(f"Added primary complaint: {primary_complaint}")
        
        # 3. Get relevant symptoms from observations.summary (filtered for patient symptoms only)
        observations = canonical_data.get('observations', {})
        observation_summary = observations.get('summary', [])
        logger.info(f"Found {len(observation_summary)} observations in summary")
        
        # Filter observations for patient symptoms only (not clinical measurements)
        symptom_keywords = [
            'snoring', 'snore', 'apnea', 'choking', 'gasping', 'breathing',
            'daytime sleepiness', 'fatigue', 'tired', 'exhausted',
            'morning headache', 'headache', 'dry mouth', 'mouth breathing',
            'nocturia', 'night urination', 'bruxism', 'teeth grinding',
            'reflux', 'heartburn', 'insomnia', 'trouble sleeping',
            'wake up', 'fragmented sleep', 'non-restorative sleep'
        ]
        
        for obs in observation_summary[:10]:  # Limit to top 10 observations
            if isinstance(obs, str):
                obs_lower = obs.lower()
                # Check if this observation contains patient symptoms
                if any(keyword in obs_lower for keyword in symptom_keywords):
                    # Clean up the observation text
                    clean_obs = obs.strip()
                    if clean_obs and clean_obs not in symptoms:
                        symptoms.append(clean_obs)
                        logger.info(f"Added symptom from observations: {clean_obs}")
        
        # 4. Limit to most important symptoms (max 8 to avoid overwhelming)
        if len(symptoms) > 8:
            symptoms = symptoms[:8]
            logger.info(f"Limited symptoms to top 8: {symptoms}")
        
        logger.info(f"Final extracted symptoms ({len(symptoms)}): {symptoms}")
        return symptoms
    except Exception as e:
        logger.error(f"Error extracting symptoms from canonical: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []


def get_quiz_files_with_presigned_urls(quiz_id, patient_id, quiz_type, submitted_at=None):
    """Get files for a quiz submission with presigned URLs"""
    try:
        # Get quiz files directly from File table using category and subcategory
        # Try specific subcategory first, then fall back to general questionnaire
        quiz_files = File.query.filter(
            File.patient_id == patient_id,
            File.category == 'medical',
            File.subcategory == 'questionnaire'
        ).all()
        
        # If no files found with 'questionnaire', try other medical files
        if not quiz_files:
            quiz_files = File.query.filter(
                File.patient_id == patient_id,
                File.category == 'medical'
            ).all()
        
        files_with_urls = []
        # If we know the submission timestamp, prefer file whose timestamp (upload_date or parsed from name) is closest
        best_match = None
        best_match_diff = None
        for file in quiz_files:
            # Filter files based on quiz type by filename
            should_include = False
            
            if quiz_type == 'basic_quiz':
                # Only include files with 'basic' in the name
                if 'basic' in file.name.lower():
                    should_include = True
                    logger.info(f"Including basic quiz file: {file.name}")
                else:
                    logger.info(f"Skipping non-basic file for basic quiz: {file.name}")
            elif quiz_type == 'advanced_quiz':
                # Only include files with 'advanced' in the name
                if 'advanced' in file.name.lower():
                    should_include = True
                    logger.info(f"Including advanced quiz file: {file.name}")
                else:
                    logger.info(f"Skipping non-advanced file for advanced quiz: {file.name}")
            else:
                # For unknown quiz type, include all files
                should_include = True
                logger.info(f"Including all files for unknown quiz type '{quiz_type}': {file.name}")
            
            if should_include:
                # Determine file timestamp to compare
                file_dt = file.upload_date
                if not file_dt:
                    try:
                        # Try to parse from filename pattern *_YYYYMMDD_HHMMSS.*
                        import re
                        m = re.search(r"_(\d{8})_(\d{6})", file.name or '')
                        if m:
                            ymd, hms = m.group(1), m.group(2)
                            from datetime import datetime as _dt
                            file_dt = _dt.strptime(ymd + hms, "%Y%m%d%H%M%S")
                    except Exception:
                        file_dt = None

                if submitted_at:
                    # Track closest-by-time file for this submission
                    diff = abs(((file_dt or submitted_at) - submitted_at))
                    if best_match is None or diff < best_match_diff:
                        best_match = file
                        best_match_diff = diff
                else:
                    # No submission timestamp – include all (legacy)
                    try:
                        presigned_url = generate_presigned_url_for_viewing(file.s3_key, inline=True, expires_in=3600)
                        if presigned_url:
                            files_with_urls.append({
                                'id': file.id,
                                'name': file.name,
                                'file_type': file.file_type,
                                'upload_date': file.upload_date,
                                'view_url': presigned_url,
                                's3_key': file.s3_key
                            })
                    except Exception as e:
                        logger.warning(f"Could not get presigned URL for file {file.name}: {str(e)}")

        # If we tracked the closest file, return only that as the match
        if submitted_at and best_match:
            try:
                presigned_url = generate_presigned_url_for_viewing(best_match.s3_key, inline=True, expires_in=3600)
                if presigned_url:
                    return [{
                        'id': best_match.id,
                        'name': best_match.name,
                        'file_type': best_match.file_type,
                        'upload_date': best_match.upload_date,
                        'view_url': presigned_url,
                        's3_key': best_match.s3_key
                    }]
            except Exception as e:
                logger.warning(f"Could not get presigned URL for best-match file {best_match.name}: {str(e)}")
        
        return files_with_urls
    except Exception as e:
        logger.error(f"Error getting quiz files for quiz {quiz_id}: {str(e)}")
        return []


def group_files_by_name(files_list):
    """Categorize files by filename keywords and sort each group by upload_date desc.
    Returns groups ordered with Vizbriz first, then Basic, Advanced, Other.
    Each item dict should contain: name, url or view_url, upload_date.
    """
    groups = {
        'vizbriz': [],
        'advanced': [],
        'basic': [],
        'other': []
    }
    for f in files_list:
        name = (f.get('name') or '').lower()
        target = 'other'
        if 'vizbriz' in name:
            target = 'vizbriz'
        elif 'advanced' in name:
            target = 'advanced'
        elif 'basic' in name:
            target = 'basic'
        groups[target].append(f)
    # Sort each group by upload_date desc (fallback to name if missing)
    for key in groups.keys():
        groups[key].sort(key=lambda x: (x.get('upload_date') or datetime.min), reverse=True)
    # Convert to a display-ready list with titles
    ordered = []
    title_map = {
        'vizbriz': 'Vizbriz Quiz',
        'advanced': 'Advanced Quiz',
        'basic': 'Basic Quiz',
        'other': 'Other'
    }
    # Order with Vizbriz first, then Basic, Advanced, Other
    for k in ['vizbriz', 'basic', 'advanced', 'other']:
        if groups[k]:
            ordered.append({'group': title_map[k], 'items': groups[k]})
    return ordered


def normalize_risk_level(risk_level):
    """Normalize risk level to handle spelling variations and formatting differences.
    
    Converts various formats like:
    - "Diagnosed – Not Using Treatment" -> "diagnosed_not_treated"
    - "diagnosed_not_treated" -> "diagnosed_not_treated"
    - "Diagnosed - Not Treated" -> "diagnosed_not_treated"
    - "low" -> "low"
    - "High" -> "high"
    """
    if not risk_level or risk_level == 'Unknown':
        return 'Unknown'
    
    # Normalize to lowercase
    normalized = str(risk_level).lower().strip()
    
    # First, check for diagnosed variations BEFORE normalization (more reliable)
    if 'diagnosed' in normalized:
        # Check for specific new risk bands first (most specific patterns first)
        if 'not_treated_not_symptomatic' in normalized or ('not' in normalized and 'treated' in normalized and 'not' in normalized and 'symptomatic' not in normalized):
            return 'diagnosed_not_treated_not_symptomatic'
        elif 'not_treated_symptomatic' in normalized or ('not' in normalized and 'treated' in normalized and 'symptomatic' in normalized):
            return 'diagnosed_not_treated_symptomatic'
        elif 'treated_stable' in normalized or ('treated' in normalized and 'stable' in normalized):
            return 'diagnosed_treated_stable'
        elif 'treated_symptomatic' in normalized or ('treated' in normalized and ('symptomatic' in normalized or 'still' in normalized)):
            return 'diagnosed_treated_symptomatic'
        elif ('not' in normalized and ('treated' in normalized or 'using' in normalized or 'treatment' in normalized)):
            # Legacy format - default to symptomatic for backward compatibility
            return 'diagnosed_not_treated_symptomatic'
        else:
            # If just "diagnosed" without other info, default to not_treated_symptomatic
            return 'diagnosed_not_treated_symptomatic'
    
    # For non-diagnosed risk levels, normalize formatting
    # Replace various dash characters and spaces with underscores
    normalized = normalized.replace('–', '_').replace('—', '_').replace('-', '_').replace(' ', '_')
    # Collapse multiple underscores into single underscore
    normalized = re.sub(r'_+', '_', normalized)
    # Remove leading/trailing underscores
    normalized = normalized.strip('_')
    
    # Handle standard risk levels
    if normalized in ['high', 'moderate', 'low']:
        return normalized
    
    # If it contains keywords, map them
    if 'high' in normalized:
        return 'high'
    elif 'moderate' in normalized:
        return 'moderate'
    elif 'low' in normalized:
        return 'low'
    
    # Return as-is if we can't normalize
    return normalized


def get_all_questionnaire_files(patient_id):
    """Fetch all questionnaire files for a patient and return with presigned URLs."""
    try:
        files = File.query.filter(
            File.patient_id == patient_id,
            File.category == 'medical',
            File.subcategory == 'questionnaire'
        ).order_by(File.upload_date.desc()).all()
        results = []
        for f in files:
            try:
                url = generate_presigned_url_for_viewing(f.s3_key, inline=True, expires_in=3600)
            except Exception:
                url = None
            results.append({'name': f.name, 'view_url': url, 'url': url, 'upload_date': f.upload_date})
        return results
    except Exception as e:
        logger.error(f"Error fetching questionnaire files for patient {patient_id}: {str(e)}")
        return []


@unified_bp.route('/forms/api/patient-email/<patient_email>/comments', methods=['POST'])
@login_required
def add_comment_by_patient_email(patient_email):
    """Add a comment for a patient by email (for unified dashboard)"""
    try:
        logger.info(f"Adding comment for patient email: {patient_email}")
        
        # Find the patient by email
        patient = Patient.query.filter(func.lower(Patient.email) == patient_email.lower()).first()
        if not patient:
            logger.warning(f"Patient not found for email: {patient_email}")
            return jsonify({'success': False, 'message': 'Patient not found'}), 404
        
        # Get the comment from request
        data = request.get_json()
        if not data or not data.get('content'):
            return jsonify({'success': False, 'message': 'Comment content is required'}), 400
        
        comment_text = data['content'].strip()
        if not comment_text:
            return jsonify({'success': False, 'message': 'Comment cannot be empty'}), 400
        
        # Get current user (dentist) ID
        dentist_id = current_user.id
        
        # Create new PatientComment with conversion type
        from ..models import PatientComment
        new_comment = PatientComment(
            patient_id=patient.id,
            content=comment_text,
            created_date=datetime.utcnow(),
            dentist_id=dentist_id,
            comment_type='conversion',  # Mark as conversion comment
            is_urgent=False,
            is_internal=False
        )
        
        db.session.add(new_comment)
        db.session.commit()
        
        logger.info(f"Comment added successfully for patient {patient.name} ({patient_email}) by {current_user.name}")
        
        return jsonify({
            'success': True, 
            'message': 'Comment added successfully',
            'comment_id': new_comment.id
        })
        
    except Exception as e:
        logger.error(f"Error adding comment for patient email {patient_email}: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error adding comment: {str(e)}'}), 500


@unified_bp.route('/forms/api/patient-email/<patient_email>/comments', methods=['GET'])
@login_required
def get_comments_by_patient_email(patient_email):
    """Get all comments for a patient by email"""
    try:
        logger.info(f"Fetching comments for patient email: {patient_email}")
        
        # Find the patient by email
        patient = Patient.query.filter_by(email=patient_email).first()
        if not patient:
            logger.warning(f"Patient not found for email: {patient_email}")
            return jsonify({'success': False, 'message': 'Patient not found'}), 404
        
        # Get only conversion comments for this patient
        from ..models import PatientComment
        comments = PatientComment.query.filter_by(
            patient_id=patient.id, 
            comment_type='conversion'
        ).order_by(PatientComment.created_date.desc()).all()
        
        comments_data = [
            {
                'id': comment.id,
                'content': comment.content,
                'created_date': comment.created_date.strftime('%Y-%m-%d %H:%M:%S'),
                'dentist': comment.dentist.name if comment.dentist else 'Unknown',
                'comment_type': comment.comment_type or 'general',
                'is_urgent': comment.is_urgent or False,
                'is_internal': comment.is_internal or False
            }
            for comment in comments
        ]
        
        logger.info(f"Found {len(comments)} conversion comments for patient {patient.name}")
        
        return jsonify({
            'success': True,
            'comments': comments_data
        })
        
    except Exception as e:
        logger.error(f"Error fetching comments for patient email {patient_email}: {str(e)}")
        return jsonify({'success': False, 'message': f'Error fetching comments: {str(e)}'}), 500
