"""
Forms Management Routes - DSO-based access control for consultation requests and quiz submissions
"""

from flask import Blueprint, render_template, request, jsonify, send_file, current_app, redirect, url_for, flash
from flask_login import login_required, current_user
from flask_app.models import (
    Patient, Dentist, DSO, Clinic, ConsultationRequest, ConversionQuiz, 
    db, PageViewLog, CTAInteractionLog
)
from datetime import datetime, timedelta
import csv
import io
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
import json
import os
import openai

# Create forms management blueprint
forms_mgmt = Blueprint('forms_mgmt', __name__, url_prefix='/forms')

@forms_mgmt.route('/dashboard')
@login_required
def dashboard():
    """Forms management dashboard with DSO-filtered overview"""
    
    try:
        # Get user's accessible data using new DSO system
        consultation_requests = current_user.get_accessible_consultation_requests()
        quiz_submissions = current_user.get_accessible_quiz_submissions()
        new_patients = current_user.get_accessible_patients_new_system()
        
        # Calculate summary statistics
        total_consultations = len(consultation_requests)
        total_quizzes = len(quiz_submissions)
        total_new_patients = len(new_patients)
        
        # Recent activity (last 7 days)
        week_ago = datetime.utcnow() - timedelta(days=7)
        recent_consultations = [cr for cr in consultation_requests if cr.submitted_at >= week_ago]
        recent_quizzes = [qs for qs in quiz_submissions if qs.created_at >= week_ago]
        
        # Sort recent items by most recent first
        recent_consultations.sort(key=lambda x: x.submitted_at, reverse=True)
        recent_quizzes.sort(key=lambda x: x.created_at, reverse=True)
        
        # Quiz type breakdown
        basic_quizzes = [q for q in quiz_submissions if q.quiz_type == 'basic_quiz']
        advanced_quizzes = [q for q in quiz_submissions if q.quiz_type == 'advanced_quiz']
        
        # Consultation status breakdown
        pending_consultations = [c for c in consultation_requests if c.status == 'pending']
        contacted_consultations = [c for c in consultation_requests if c.status == 'contacted']
        completed_consultations = [c for c in consultation_requests if c.status == 'completed']
        
        # DSO information for current user
        user_dsos = current_user.dsos.all() if hasattr(current_user, 'dsos') else []
        
        summary_stats = {
            'total_consultations': total_consultations,
            'total_quizzes': total_quizzes,
            'total_new_patients': total_new_patients,
            'recent_consultations': len(recent_consultations),
            'recent_quizzes': len(recent_quizzes),
            'basic_quizzes': len(basic_quizzes),
            'advanced_quizzes': len(advanced_quizzes),
            'pending_consultations': len(pending_consultations),
            'contacted_consultations': len(contacted_consultations),
            'completed_consultations': len(completed_consultations),
            'user_dsos': [{'id': dso.id, 'name': dso.name} for dso in user_dsos]
        }
        
        return render_template('forms_management/conversion_dashboard.html', 
                             summary_stats=summary_stats,
                             recent_consultations=recent_consultations[:10],
                             recent_quizzes=recent_quizzes[:10])
        
    except Exception as e:
        current_app.logger.error(f"Error in forms management dashboard: {str(e)}")
        flash('Error loading dashboard data', 'error')
        return redirect(url_for('main.index'))

@forms_mgmt.route('/consultation-requests')
@login_required
def consultation_requests():
    """Manage consultation requests with DSO filtering"""
    
    try:
        # Get accessible consultation requests
        requests = current_user.get_accessible_consultation_requests()
        
        # Apply additional filters from query parameters
        status_filter = request.args.get('status')
        date_filter = request.args.get('date_range')
        
        if status_filter and status_filter != 'all':
            requests = [r for r in requests if r.status == status_filter]
        
        if date_filter:
            if date_filter == 'today':
                today = datetime.utcnow().date()
                requests = [r for r in requests if r.submitted_at.date() == today]
            elif date_filter == 'week':
                week_ago = datetime.utcnow() - timedelta(days=7)
                requests = [r for r in requests if r.submitted_at >= week_ago]
            elif date_filter == 'month':
                month_ago = datetime.utcnow() - timedelta(days=30)
                requests = [r for r in requests if r.submitted_at >= month_ago]
        
        # Enhance each request with quiz and CTA information
        enhanced_requests = []
        for req in requests:
            # Find the most recent quiz submission for this email
            latest_quiz = ConversionQuiz.query.filter_by(patient_email=req.email).order_by(ConversionQuiz.created_at.desc()).first()
            
            # Find the most recent CTA interaction for this email that led to consultation
            consultation_cta = CTAInteractionLog.query.filter_by(
                patient_email=req.email, 
                cta_type='consultation_form_submitted'
            ).order_by(CTAInteractionLog.created_at.desc()).first()
            
            # If no consultation CTA found, look for other relevant CTAs around the time of submission
            if not consultation_cta:
                time_window_start = req.submitted_at - timedelta(hours=24)
                time_window_end = req.submitted_at + timedelta(hours=1)
                consultation_cta = CTAInteractionLog.query.filter(
                    CTAInteractionLog.patient_email == req.email,
                    CTAInteractionLog.created_at >= time_window_start,
                    CTAInteractionLog.created_at <= time_window_end,
                    CTAInteractionLog.cta_type.in_(['schedule_consultation', 'contact_clinic', 'email_link_click'])
                ).order_by(CTAInteractionLog.created_at.desc()).first()
            
            # Create enhanced request object with additional info
            req_dict = {
                'request': req,
                'quiz_type': latest_quiz.quiz_type if latest_quiz else None,
                'quiz_date': latest_quiz.created_at if latest_quiz else None,
                'cta_type': consultation_cta.cta_type if consultation_cta else None,
                'cta_text': consultation_cta.cta_text if consultation_cta else None,
                'cta_source': consultation_cta.email_source if consultation_cta else None,
                'original_comment': req.comment
            }
            
            # Build enhanced comment with origin information
            origin_info = []
            if latest_quiz:
                quiz_display = "Advanced Quiz" if latest_quiz.quiz_type == 'advanced_quiz' else "Basic Quiz"
                origin_info.append(f"Quiz Type: {quiz_display}")
                origin_info.append(f"Completed: {latest_quiz.created_at.strftime('%Y-%m-%d %H:%M')}")
            
            if consultation_cta:
                cta_display = consultation_cta.cta_type.replace('_', ' ').title()
                origin_info.append(f"CTA Used: {cta_display}")
                if consultation_cta.cta_text:
                    origin_info.append(f"Button Text: {consultation_cta.cta_text}")
                if consultation_cta.email_source:
                    source_display = consultation_cta.email_source.replace('_', ' ').title()
                    origin_info.append(f"Source: {source_display}")
            
            # Combine original comment with origin information
            enhanced_comment = req.comment or ""
            if origin_info:
                origin_section = "\n\n--- User Registration Info ---\n" + "\n".join(origin_info)
                enhanced_comment = enhanced_comment + origin_section
            
            req_dict['enhanced_comment'] = enhanced_comment
            enhanced_requests.append(req_dict)
        
        # Apply filters and sort by most recent first
        enhanced_requests.sort(key=lambda x: x['request'].submitted_at, reverse=True)
        
        return render_template('forms_management/consultation_requests.html', 
                             consultation_requests=enhanced_requests,
                             status_filter=status_filter,
                             date_filter=date_filter)
        
    except Exception as e:
        current_app.logger.error(f"Error loading consultation requests: {str(e)}")
        flash('Error loading consultation requests', 'error')
        return redirect(url_for('forms_mgmt.dashboard'))

@forms_mgmt.route('/quiz-submissions')
@login_required
def quiz_submissions():
    """Manage quiz submissions with DSO filtering"""
    
    try:
        # Get accessible quiz submissions
        submissions = current_user.get_accessible_quiz_submissions()
        
        # Apply filters
        quiz_type_filter = request.args.get('quiz_type')
        date_filter = request.args.get('date_range')
        risk_filter = request.args.get('risk_level')
        
        if quiz_type_filter and quiz_type_filter != 'all':
            submissions = [s for s in submissions if s.quiz_type == quiz_type_filter]
        
        if date_filter and date_filter != '':
            if date_filter == 'today':
                today = datetime.utcnow().date()
                submissions = [s for s in submissions if s.created_at.date() == today]
            elif date_filter == 'week':
                week_ago = datetime.utcnow() - timedelta(days=7)
                submissions = [s for s in submissions if s.created_at >= week_ago]
            elif date_filter == 'month':
                month_ago = datetime.utcnow() - timedelta(days=30)
                submissions = [s for s in submissions if s.created_at >= month_ago]
        
        if risk_filter and risk_filter != 'all':
            # Filter by risk level (parsed from ai_response JSON)
            filtered_submissions = []
            for submission in submissions:
                try:
                    if submission.ai_response:
                        ai_data = json.loads(submission.ai_response)
                        if ai_data.get('risk_level') == risk_filter:
                            filtered_submissions.append(submission)
                except:
                    continue
            submissions = filtered_submissions
        
        # Sort by most recent first
        submissions.sort(key=lambda x: x.created_at, reverse=True)
        
        return render_template('forms_management/quiz_submissions.html', 
                             quiz_submissions=submissions,
                             quiz_type_filter=quiz_type_filter,
                             date_filter=date_filter,
                             risk_filter=risk_filter)
        
    except Exception as e:
        current_app.logger.error(f"Error loading quiz submissions: {str(e)}")
        flash('Error loading quiz submissions', 'error')
        return redirect(url_for('forms_mgmt.dashboard'))

@forms_mgmt.route('/consultation-request/<int:request_id>')
@login_required
def consultation_request_detail(request_id):
    """View detailed consultation request"""
    
    try:
        consultation_req = ConsultationRequest.query.get_or_404(request_id)
        
        # Check access using new DSO system
        accessible_requests = current_user.get_accessible_consultation_requests()
        if consultation_req not in accessible_requests:
            flash('You do not have permission to view this consultation request', 'error')
            return redirect(url_for('forms_mgmt.consultation_requests'))
        
        # Redirect to main consultation requests page since no detail page exists
        flash('Consultation request detail view not available. Redirecting to main page.', 'info')
        return redirect(url_for('forms_mgmt.consultation_requests'))
        
    except Exception as e:
        current_app.logger.error(f"Error loading consultation request detail: {str(e)}")
        flash('Error loading consultation request', 'error')
        return redirect(url_for('forms_mgmt.consultation_requests'))

@forms_mgmt.route('/quiz-submission/<int:submission_id>')
@login_required
def quiz_submission_detail(submission_id):
    """View detailed quiz submission"""
    
    try:
        submission = ConversionQuiz.query.get_or_404(submission_id)
        
        # Check access using new DSO system
        accessible_submissions = current_user.get_accessible_quiz_submissions()
        if submission not in accessible_submissions:
            flash('You do not have permission to view this quiz submission', 'error')
            return redirect(url_for('forms_mgmt.quiz_submissions'))
        
        # Parse AI response if available
        ai_data = {}
        if submission.ai_response:
            try:
                ai_data = json.loads(submission.ai_response)
            except:
                ai_data = {}
        
        # Parse quiz input
        quiz_data = {}
        if submission.quiz_input:
            try:
                quiz_data = json.loads(submission.quiz_input)
            except:
                quiz_data = {}
        
        return render_template('forms_management/quiz_detail.html', 
                             submission=submission,
                             ai_data=ai_data,
                             quiz_data=quiz_data)
        
    except Exception as e:
        current_app.logger.error(f"Error loading quiz submission detail: {str(e)}")
        flash('Error loading quiz submission', 'error')
        return redirect(url_for('forms_mgmt.quiz_submissions'))

@forms_mgmt.route('/update-consultation-status/<int:request_id>', methods=['POST'])
@login_required
def update_consultation_status(request_id):
    """Update consultation request status"""
    
    try:
        consultation_req = ConsultationRequest.query.get_or_404(request_id)
        
        # Check access
        accessible_requests = current_user.get_accessible_consultation_requests()
        if consultation_req not in accessible_requests:
            return jsonify({'success': False, 'message': 'Access denied'}), 403
        
        data = request.get_json()
        new_status = data.get('status')
        
        if new_status not in ['pending', 'contacted', 'completed']:
            return jsonify({'success': False, 'message': 'Invalid status'}), 400
        
        consultation_req.status = new_status
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Status updated successfully'})
        
    except Exception as e:
        current_app.logger.error(f"Error updating consultation status: {str(e)}")
        return jsonify({'success': False, 'message': 'Error updating status'}), 500

@forms_mgmt.route('/api/consultation-requests/<int:request_id>/comments', methods=['POST'])
@login_required
def add_consultation_request_comment(request_id):
    """Add a comment to a consultation request"""
    
    try:
        # Get the consultation request
        consultation_req = ConsultationRequest.query.get_or_404(request_id)
        
        # Check access using new DSO system
        accessible_requests = current_user.get_accessible_consultation_requests()
        if consultation_req not in accessible_requests:
            return jsonify({'success': False, 'message': 'You do not have permission to comment on this consultation request'}), 403
        
        # Get the comment from request
        data = request.get_json()
        if not data or not data.get('content'):
            return jsonify({'success': False, 'message': 'Comment content is required'}), 400
        
        comment_text = data['content'].strip()
        if not comment_text:
            return jsonify({'success': False, 'message': 'Comment cannot be empty'}), 400
        
        # Format the comment with timestamp and admin name
        from datetime import datetime
        now = datetime.utcnow()
        month_names = ["January", "February", "March", "April", "May", "June",
                      "July", "August", "September", "October", "November", "December"]
        
        month = month_names[now.month - 1]
        day = now.day
        year = now.year
        
        hours = now.hour
        minutes = str(now.minute).zfill(2)
        ampm = 'p' if hours >= 12 else 'a'
        
        # Convert to 12-hour format
        hours = hours % 12
        hours = hours if hours else 12
        
        formatted_date = f"{month} {day},{year}:{str(hours).zfill(2)}:{minutes}{ampm}"
        
        # Create the admin comment with the requested format
        admin_comment = f"\n\n--- Admin Comment ---\n{formatted_date} - {current_user.name}: {comment_text}"
        
        # Store comment in PatientComment table for unified comment system
        from ..models import PatientComment
        new_comment = PatientComment(
            patient_id=consultation_req.patient_id,
            content=comment_text,
            created_date=datetime.utcnow(),
            dentist_id=current_user.id,
            comment_type='consultation',  # Mark as consultation comment
            is_urgent=False,
            is_internal=False
        )
        
        db.session.add(new_comment)
        
        # Also update the consultation request comment field for backward compatibility
        if consultation_req.comment:
            consultation_req.comment = consultation_req.comment + admin_comment
        else:
            consultation_req.comment = f"Admin Comment:\n{formatted_date} - {current_user.name}: {comment_text}"
        
        db.session.commit()
        
        current_app.logger.info(f"Admin comment added to consultation request {request_id} by {current_user.name} (stored in PatientComment table)")
        
        return jsonify({
            'success': True, 
            'message': 'Admin comment added successfully',
            'formatted_comment': admin_comment,
            'updated_comment': consultation_req.comment,
            'comment_id': new_comment.id
        })
        
    except Exception as e:
        current_app.logger.error(f"Error adding comment to consultation request {request_id}: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error adding comment: {str(e)}'}), 500

@forms_mgmt.route('/api/consultation-requests/<int:request_id>/comments', methods=['GET'])
@login_required
def get_consultation_request_comments(request_id):
    """Get all comments for a consultation request from PatientComment table"""
    
    try:
        # Get the consultation request
        consultation_req = ConsultationRequest.query.get_or_404(request_id)
        
        # Check access using new DSO system
        accessible_requests = current_user.get_accessible_consultation_requests()
        if consultation_req not in accessible_requests:
            return jsonify({'success': False, 'message': 'You do not have permission to view comments for this consultation request'}), 403
        
        # Get all comments for this patient from PatientComment table
        from ..models import PatientComment
        comments = PatientComment.query.filter_by(patient_id=consultation_req.patient_id).order_by(PatientComment.created_date.desc()).all()
        
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
        
        current_app.logger.info(f"Found {len(comments)} unified comments for consultation request {request_id}")
        
        return jsonify({
            'success': True,
            'comments': comments_data
        })
        
    except Exception as e:
        current_app.logger.error(f"Error fetching comments for consultation request {request_id}: {str(e)}")
        return jsonify({'success': False, 'message': f'Error fetching comments: {str(e)}'}), 500

@forms_mgmt.route('/api/generate-patient-narrative/<int:request_id>', methods=['POST'])
@login_required
def generate_patient_narrative(request_id):
    """Generate a personalized patient communication narrative based on form data"""
    
    try:
        # Get the consultation request
        consultation_req = ConsultationRequest.query.get_or_404(request_id)
        
        # Check access using DSO system
        accessible_requests = current_user.get_accessible_consultation_requests()
        if consultation_req not in accessible_requests:
            return jsonify({'success': False, 'message': 'You do not have permission to access this consultation request'}), 403
        
        # Find the most recent quiz submission for this email
        latest_quiz = ConversionQuiz.query.filter_by(
            patient_email=consultation_req.email
        ).order_by(ConversionQuiz.created_at.desc()).first()
        
        if not latest_quiz:
            return jsonify({
                'success': False, 
                'message': 'No quiz data found for this patient. Unable to generate personalized narrative.'
            }), 404
        
        # Parse quiz data
        try:
            quiz_answers = json.loads(latest_quiz.quiz_input) if latest_quiz.quiz_input else {}
            ai_response_data = json.loads(latest_quiz.ai_response) if latest_quiz.ai_response else {}
        except json.JSONDecodeError:
            return jsonify({
                'success': False, 
                'message': 'Invalid quiz data format. Unable to generate narrative.'
            }), 400
        
        # Extract key information
        risk_level = ai_response_data.get('risk_level', 'UNKNOWN')
        risk_explanation = ai_response_data.get('risk_explanation', '')
        recommendations = ai_response_data.get('recommendations', '')
        ai_analysis = ai_response_data.get('ai_analysis', '')
        
        # Extract specific patient symptoms for Assessment Details
        specific_symptoms = extract_patient_symptoms(quiz_answers)
        
        # Generate patient communication narrative
        narrative = generate_patient_communication_narrative(
            patient_name=consultation_req.name,
            patient_email=consultation_req.email,
            quiz_answers=quiz_answers,
            risk_level=risk_level,
            risk_explanation=risk_explanation,
            recommendations=recommendations,
            ai_analysis=ai_analysis,
            quiz_type=latest_quiz.quiz_type
        )
        
        if narrative:
            return jsonify({
                'success': True,
                'narrative': narrative,
                'patient_name': consultation_req.name,
                'risk_level': risk_level,
                'quiz_type': latest_quiz.quiz_type,
                'specific_symptoms': specific_symptoms,
                'risk_explanation': risk_explanation,
                'quiz_answers': quiz_answers,
                'total_symptoms': len(specific_symptoms)
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to generate patient narrative. Please try again.'
            }), 500
        
    except Exception as e:
        current_app.logger.error(f"Error generating patient narrative for request {request_id}: {str(e)}")
        return jsonify({'success': False, 'message': f'Error generating narrative: {str(e)}'}), 500

def generate_patient_communication_narrative(patient_name, patient_email, quiz_answers, risk_level, risk_explanation, recommendations, ai_analysis, quiz_type):
    """Generate a personalized, clear patient communication narrative based on specific symptoms and observations"""
    
    try:
        # Extract specific patient symptoms from quiz answers
        specific_symptoms = extract_patient_symptoms(quiz_answers)
        symptom_summary = format_symptoms_for_narrative(specific_symptoms, risk_level)
        
        # Try to use Bedrock LLM service
        try:
            from flask_app.services.llm_service import get_llm_service
            llm_service = get_llm_service()
            
            # Check if Bedrock service is available
            if llm_service.bedrock_service.is_available():
                # Create system prompt
                system = """DO NOT WRITE LIFESTYLE ADVICE. DO NOT WRITE SLEEP HYGIENE TIPS. DO NOT WRITE RECOMMENDATIONS.

THIS IS WRONG (DO NOT DO THIS):
"## Immediate Recommendations
1. Consistent Sleep Schedule
2. Limit caffeine after 2 PM
3. Keep bedroom cool"

THIS IS CORRECT (DO THIS):

SECTION: OPENING
Hi Sarah, thanks for completing your sleep quiz.

SECTION: QUIZ RESULTS
Your quiz shows high risk for sleep apnea. You reported snoring and daytime fatigue.

SECTION: WHAT THIS MEANS
High risk means your symptoms suggest sleep disruption that needs attention.

SECTION: NEXT STEPS
I'd like to schedule you urgently for a sleep test with iSleep Physicians this week. Would mornings or evenings work better?

SECTION: CLOSING
Does Wednesday or Thursday work?

RULES:
- Maximum 150 words
- NO ## headers
- NO numbered lists
- NO caffeine advice
- NO sleep hygiene
- NO bedroom temperature
- NO exercise tips
- ONLY state quiz results + schedule next step

Based on risk:
- HIGH: Urgent sleep test needed
- MODERATE: Sleep test soon
- LOW: Monitoring only, no treatment

LOW RISK EXAMPLE:

SECTION: OPENING
Hi John, thanks for completing your sleep quiz.

SECTION: QUIZ RESULTS
Your quiz shows low risk. You reported minimal symptoms.

SECTION: WHAT THIS MEANS
Low risk means no urgent treatment needed.

SECTION: NEXT STEPS
I recommend a periodic check-up in 6-12 months to monitor your sleep health.

SECTION: CLOSING
Would you like us to schedule that follow-up?

Write ONLY in plain conversational text. 5 sections total."""
                
                # Create user message
                user_message = f"""PATIENT: {patient_name}
QUIZ TYPE: {quiz_type}
RISK LEVEL: {risk_level}
SPECIFIC SYMPTOMS REPORTED: {specific_symptoms}
SYMPTOM SUMMARY: {symptom_summary}

Write a short phone script following the format above. DO NOT include lifestyle advice, sleep hygiene tips, or caffeine recommendations. ONLY state the quiz results and schedule next steps based on risk level."""
                
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
                    endpoint="forms_management"
                )
                
                if result.get('success'):
                    narrative = result.get('response', '').strip()
                    current_app.logger.info(f"Generated narrative using Bedrock LLM for {patient_name}")
                    return narrative
                else:
                    current_app.logger.warning(f"Bedrock LLM call failed: {result.get('message')}")
                    # NO FALLBACK - Return unavailable message
                    return "Dr. Briz is currently not available. Please try again in a few minutes."
            else:
                current_app.logger.warning("Bedrock service not available")
                # NO FALLBACK - Return unavailable message
                return "Dr. Briz is currently not available. Please try again in a few minutes."
                
        except ImportError as e:
            current_app.logger.error(f"Could not import LLM service: {e}")
            # NO FALLBACK - Return unavailable message
            return "Dr. Briz is currently not available. Please try again in a few minutes."
        
    except Exception as e:
        current_app.logger.error(f"Error generating patient communication narrative: {str(e)}")
        # NO FALLBACK - Return unavailable message
        return "Dr. Briz is currently not available. Please try again in a few minutes."

def extract_patient_symptoms(quiz_answers):
    """Extract specific symptoms from patient's quiz answers"""
    symptoms = []
    
    # Parse quiz_answers if it's a string
    if isinstance(quiz_answers, str):
        try:
            quiz_data = json.loads(quiz_answers)
        except:
            quiz_data = {}
    else:
        quiz_data = quiz_answers or {}
    
    # Snoring patterns
    snoring = quiz_data.get('snoring', '').lower()
    if snoring in ['yes', 'often', 'always']:
        snoring_details = quiz_data.get('snoring_details', '')
        if snoring_details:
            symptoms.append(f"Snoring: {snoring_details}")
        else:
            symptoms.append("Regular snoring")
    elif snoring == 'sometimes':
        symptoms.append("Occasional snoring")
    
    # Witnessed apneas
    observed_apnea = quiz_data.get('observed_apnea', '').lower()
    if observed_apnea in ['yes', 'often', 'always']:
        symptoms.append("Witnessed breathing interruptions during sleep")
    elif observed_apnea == 'sometimes':
        symptoms.append("Occasional breathing interruptions")
    
    # Daytime symptoms
    if quiz_data.get('tiredness', '').lower() in ['yes', 'often', 'always']:
        symptoms.append("Feeling tired during the day")
    if quiz_data.get('daytime_sleepiness', '').lower() == 'yes':
        symptoms.append("Unintentionally falling asleep during the day")
    if quiz_data.get('driving_fatigue', '').lower() == 'yes':
        symptoms.append("Trouble staying awake while driving")
    
    # Physical factors
    if quiz_data.get('weight', '').lower() in ['yes', 'overweight', 'obese']:
        symptoms.append("Weight concerns")
    if quiz_data.get('blood_pressure', '').lower() == 'yes':
        symptoms.append("High blood pressure")
    if quiz_data.get('bruxism', '').lower() == 'yes':
        symptoms.append("Teeth grinding or jaw issues")
    
    # Advanced assessment symptoms (if available)
    if quiz_data.get('mouth_breathing', '').lower() == 'yes':
        symptoms.append("Mouth breathing at night")
    if quiz_data.get('tmj_problems', '').lower() == 'yes':
        symptoms.append("TMJ problems")
    if quiz_data.get('night_urination', '').lower() == 'yes':
        symptoms.append("Frequent nighttime urination")
    
    return symptoms

def format_symptoms_for_narrative(symptoms, risk_level):
    """Format symptoms into a summary for the narrative"""
    if not symptoms:
        return "No specific symptoms reported"
    
    if len(symptoms) == 1:
        return symptoms[0]
    elif len(symptoms) == 2:
        return f"{symptoms[0]} and {symptoms[1]}"
    elif len(symptoms) <= 4:
        return f"{', '.join(symptoms[:-1])}, and {symptoms[-1]}"
    else:
        # For many symptoms, highlight the most serious ones
        key_symptoms = []
        for symptom in symptoms:
            if any(word in symptom.lower() for word in ['breathing', 'apnea', 'driving', 'falling asleep']):
                key_symptoms.append(symptom)
        
        if key_symptoms:
            return f"{', '.join(key_symptoms[:2])} and {len(symptoms) - 2} other symptoms"
        else:
            return f"{', '.join(symptoms[:3])} and {len(symptoms) - 3} other concerns"

def generate_fallback_narrative(patient_name, risk_level):
    """Generate a simple fallback narrative if AI generation fails"""
    if risk_level.upper() == 'HIGH':
        return f"Hi {patient_name}, based on your sleep assessment, your results show significant signs that suggest sleep apnea, which definitely needs our attention. I'd strongly recommend either scheduling a home sleep test right away or setting up a call with our dental sleep team so we can get you the help you need as soon as possible."
    elif risk_level.upper() == 'MODERATE':
        return f"Hi {patient_name}, your sleep assessment shows some potential indicators of sleep apnea that we should definitely look into further. I'd recommend scheduling a consultation with our dental sleep team or considering a sleep study to get a clearer picture of what's going on."
    else:
        return f"Hi {patient_name}, thank you for completing your sleep assessment. While your results suggest lower risk, I'd still recommend speaking with our dental sleep team to discuss your sleep health and any concerns you might have."

@forms_mgmt.route('/export/consultation-requests/<format>')
@login_required
def export_consultation_requests(format):
    """Export consultation requests as CSV or PDF"""
    
    try:
        # Get accessible consultation requests
        requests = current_user.get_accessible_consultation_requests()
        
        if format == 'csv':
            return export_consultation_requests_csv(requests)
        else:
            flash('Invalid export format', 'error')
            return redirect(url_for('forms_mgmt.consultation_requests'))
            
    except Exception as e:
        current_app.logger.error(f"Error exporting consultation requests: {str(e)}")
        flash('Error exporting data', 'error')
        return redirect(url_for('forms_mgmt.consultation_requests'))

@forms_mgmt.route('/export/quiz-submissions/<format>')
@login_required
def export_quiz_submissions(format):
    """Export quiz submissions as CSV or PDF"""
    
    try:
        # Get accessible quiz submissions
        submissions = current_user.get_accessible_quiz_submissions()
        
        if format == 'csv':
            return export_quiz_submissions_csv(submissions)
        elif format == 'pdf':
            return export_quiz_submissions_pdf(submissions)
        else:
            flash('Invalid export format', 'error')
            return redirect(url_for('forms_mgmt.quiz_submissions'))
            
    except Exception as e:
        current_app.logger.error(f"Error exporting quiz submissions: {str(e)}")
        flash('Error exporting data', 'error')
        return redirect(url_for('forms_mgmt.quiz_submissions'))

def export_consultation_requests_csv(requests):
    """Generate comprehensive CSV export for consultation requests with all page information"""
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Enhanced CSV Headers - all information from the page
    writer.writerow([
        'Request ID', 'Patient Name', 'Email', 'Phone', 'Status', 'Submitted Date',
        'Comment/Request Details', 'Patient ID', 'Sleep Apnea Risk Level', 'Quiz Type',
        'Specific Symptoms', 'Symptom Count', 'Clinical Assessment', 'AI Analysis',
        'Risk Explanation', 'Recommendations', 'Quiz Score', 'Created Date',
        'DSO', 'Has Patient Link', 'Last Updated'
    ])
    
    # Data rows with comprehensive information
    for req in requests:
        # Initialize variables
        risk_level = 'Not Available'
        quiz_type = 'Not Available'
        specific_symptoms = 'Not Available'
        symptom_count = 0
        clinical_assessment = 'Not Available'
        ai_analysis = 'Not Available'
        risk_explanation = 'Not Available'
        recommendations = 'Not Available'
        quiz_score = 'Not Available'
        quiz_created_date = 'Not Available'
        
        # Try to get quiz data for this patient
        try:
            from flask_app.models import ConversionQuiz
            latest_quiz = ConversionQuiz.query.filter_by(
                patient_email=req.email
            ).order_by(ConversionQuiz.created_at.desc()).first()
            
            if latest_quiz:
                quiz_created_date = latest_quiz.created_at.strftime('%Y-%m-%d %H:%M:%S')
                quiz_type = latest_quiz.quiz_type or 'Unknown'
                
                # Parse quiz data
                try:
                    quiz_answers = json.loads(latest_quiz.quiz_input) if latest_quiz.quiz_input else {}
                    ai_response_data = json.loads(latest_quiz.ai_response) if latest_quiz.ai_response else {}
                    
                    # Extract detailed information
                    risk_level = ai_response_data.get('risk_level', 'Not Available')
                    ai_analysis = ai_response_data.get('ai_analysis', 'Not Available')
                    risk_explanation = ai_response_data.get('risk_explanation', 'Not Available')
                    recommendations = ai_response_data.get('recommendations', 'Not Available')
                    
                    # Get specific symptoms using the same function as the narrative
                    symptoms = extract_patient_symptoms(quiz_answers)
                    if symptoms:
                        specific_symptoms = '; '.join(symptoms)
                        symptom_count = len(symptoms)
                    else:
                        specific_symptoms = 'No specific symptoms reported'
                        symptom_count = 0
                    
                    # Try to get quiz score
                    quiz_score = ai_response_data.get('score', 'Not Available')
                    
                    # Generate clinical assessment summary
                    if risk_level and risk_explanation:
                        clinical_assessment = f"Risk: {risk_level}. {risk_explanation}"
                    
                except (json.JSONDecodeError, AttributeError):
                    pass
                    
        except Exception as e:
            current_app.logger.warning(f"Could not retrieve quiz data for consultation request {req.id}: {str(e)}")
        
        # Get DSO information
        dso_name = 'Not Available'
        if hasattr(req, 'dso') and req.dso:
            dso_name = req.dso.name
        
        # Check if patient link exists
        has_patient_link = 'Yes' if req.patient_id else 'No'
        
        # Write comprehensive row
        writer.writerow([
            req.id,
            req.name or 'Unknown',
            req.email or '',
            req.phone or '',
            req.status.title(),
            req.submitted_at.strftime('%Y-%m-%d %H:%M:%S'),
            req.comment or '',
            req.patient_id or '',
            risk_level,
            quiz_type,
            specific_symptoms,
            symptom_count,
            clinical_assessment,
            ai_analysis,
            risk_explanation,
            recommendations,
            quiz_score,
            quiz_created_date,
            dso_name,
            has_patient_link,
            req.submitted_at.strftime('%Y-%m-%d %H:%M:%S')  # Using submitted_at as last updated
        ])
    
    output.seek(0)
    
    # Create response with enhanced filename
    response = send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'consultation_requests_comprehensive_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    )
    
    return response

def export_quiz_submissions_csv(submissions):
    """Generate CSV export for quiz submissions"""
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # CSV Headers
    writer.writerow([
        'ID', 'Patient Email', 'Clinic Email', 'Quiz Type', 'Risk Level', 
        'Created Date', 'Clinic ID', 'Referral Doctor', 'AI Analysis'
    ])
    
    # Data rows
    for submission in submissions:
        # Parse risk level from AI response
        risk_level = ''
        try:
            if submission.ai_response:
                ai_data = json.loads(submission.ai_response)
                risk_level = ai_data.get('risk_level', '')
        except:
            pass
        
        writer.writerow([
            submission.id,
            submission.patient_email,
            submission.clinic_email,
            submission.quiz_type,
            risk_level,
            submission.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            submission.clinic_id or '',
            submission.referral_doctor or '',
            ai_data.get('ai_narrative', '') if 'ai_data' in locals() else ''
        ])
    
    output.seek(0)
    
    # Create response
    response = send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'quiz_submissions_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    )
    
    return response



def export_quiz_submissions_pdf(submissions):
    """Generate PDF export for quiz submissions"""
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    
    # Styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=16,
        spaceAfter=30,
        alignment=1  # Center alignment
    )
    
    # Content
    content = []
    
    # Title
    content.append(Paragraph("Quiz Submissions Report", title_style))
    content.append(Spacer(1, 20))
    
    # Table data
    data = [['ID', 'Patient Email', 'Quiz Type', 'Risk Level', 'Date']]
    
    for submission in submissions:
        # Parse risk level from AI response
        risk_level = 'N/A'
        try:
            if submission.ai_response:
                ai_data = json.loads(submission.ai_response)
                risk_level = ai_data.get('risk_level', 'N/A')
        except:
            pass
        
        data.append([
            str(submission.id),
            submission.patient_email[:30] + '...' if len(submission.patient_email) > 30 else submission.patient_email,
            submission.quiz_type,
            risk_level,
            submission.created_at.strftime('%Y-%m-%d')
        ])
    
    # Create table
    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    content.append(table)
    
    # Build PDF
    doc.build(content)
    buffer.seek(0)
    
    return send_file(
        buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'quiz_submissions_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf'
    ) 