"""
Tracking Routes for Engagement Metrics and Conversions
Handles page views, CTA clicks, email tracking, and analytics
"""

from flask import Blueprint, request, jsonify, redirect, current_app
from flask_login import login_required
from flask_app.extensions import db
from flask_app.models import PageViewLog, CTAInteractionLog, ConversionQuiz
from datetime import datetime
import logging

# Create tracking blueprint
tracking = Blueprint('tracking', __name__, url_prefix='/api/tracking')

logger = logging.getLogger(__name__)

@tracking.route('/track-page-view', methods=['POST'])
def track_page_view():
    """Track when users view different pages (Stage A, Stage B, etc.)"""
    try:
        data = request.get_json()
        
        # Extract IP address
        ip_address = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
        if ip_address and ',' in ip_address:
            ip_address = ip_address.split(',')[0].strip()
        
        # Create page view log entry
        page_view = PageViewLog(
            patient_email=data.get('patient_email'),
            session_id=data.get('session_id'),
            page_type=data.get('page_type'),
            page_url=data.get('page_url'),
            referrer=data.get('referrer'),
            user_agent=data.get('user_agent'),
            ip_address=ip_address,
            clinic_id=data.get('clinic_id'),
            utm_source=data.get('utm_source'),
            utm_medium=data.get('utm_medium'),
            utm_campaign=data.get('utm_campaign')
        )
        
        db.session.add(page_view)
        db.session.commit()
        
        logger.info(f"Page view tracked: {data.get('page_type')} for {data.get('patient_email') or 'anonymous'}")
        
        return jsonify({
            'status': 'success',
            'message': 'Page view tracked',
            'page_view_id': page_view.id
        }), 200
        
    except Exception as e:
        logger.error(f"Error tracking page view: {str(e)}")
        db.session.rollback()
        return jsonify({
            'status': 'error',
            'message': 'Failed to track page view'
        }), 500

@tracking.route('/track-cta-click', methods=['POST'])
def track_cta_click():
    """Track when users click on call-to-action buttons"""
    try:
        data = request.get_json()
        
        # Extract IP address
        ip_address = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
        if ip_address and ',' in ip_address:
            ip_address = ip_address.split(',')[0].strip()
        
        # Try to find associated quiz ID if patient email is provided
        quiz_id = None
        if data.get('patient_email'):
            recent_quiz = ConversionQuiz.query.filter_by(
                patient_email=data.get('patient_email')
            ).order_by(ConversionQuiz.created_at.desc()).first()
            if recent_quiz:
                quiz_id = recent_quiz.id
        
        # Create CTA interaction log entry
        cta_interaction = CTAInteractionLog(
            patient_email=data.get('patient_email'),
            session_id=data.get('session_id'),
            cta_type=data.get('cta_type'),
            cta_text=data.get('cta_text'),
            page_type=data.get('page_type'),
            quiz_type=data.get('quiz_type'),
            quiz_id=quiz_id,
            clinic_id=data.get('clinic_id'),
            user_agent=data.get('user_agent'),
            ip_address=ip_address,
            referrer=data.get('referrer'),
            email_source=data.get('email_source')
        )
        
        db.session.add(cta_interaction)
        db.session.commit()
        
        logger.info(f"CTA click tracked: {data.get('cta_type')} by {data.get('patient_email') or 'anonymous'}")
        
        return jsonify({
            'status': 'success',
            'message': 'CTA click tracked',
            'interaction_id': cta_interaction.id
        }), 200
        
    except Exception as e:
        logger.error(f"Error tracking CTA click: {str(e)}")
        db.session.rollback()
        return jsonify({
            'status': 'error',
            'message': 'Failed to track CTA click'
        }), 500

@tracking.route('/track-email-delivery', methods=['POST'])
def track_email_delivery():
    """Track email delivery events (when emails are sent)"""
    try:
        data = request.get_json()
        
        # Log email delivery (could be stored in a separate table if needed)
        logger.info(f"Email delivery tracked: {data.get('email_type')} to {data.get('recipient_email')}")
        
        return jsonify({
            'status': 'success',
            'message': 'Email delivery tracked'
        }), 200
        
    except Exception as e:
        logger.error(f"Error tracking email delivery: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to track email delivery'
        }), 500

@tracking.route('/track-email-click', methods=['GET'])
def track_email_click():
    """Track when users click links from emails (GET request for email links)"""
    try:
        # Get parameters from URL
        patient_email = request.args.get('patient_email')
        email_type = request.args.get('email_type', 'unknown')
        clinic_id = request.args.get('clinic_id')
        quiz_id = request.args.get('quiz_id')
        cta_type = request.args.get('cta_type', 'email_link_click')
        
        # Extract IP address
        ip_address = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
        if ip_address and ',' in ip_address:
            ip_address = ip_address.split(',')[0].strip()
        
        # Create CTA interaction for email click
        cta_interaction = CTAInteractionLog(
            patient_email=patient_email,
            session_id=None,  # No session for email clicks
            cta_type=cta_type,
            cta_text=f'Email CTA: {cta_type}',
            page_type='email',
            quiz_type=None,
            quiz_id=quiz_id,
            clinic_id=clinic_id,
            user_agent=request.headers.get('User-Agent'),
            ip_address=ip_address,
            referrer=request.headers.get('Referer'),
            email_source=email_type
        )
        
        db.session.add(cta_interaction)
        db.session.commit()
        
        logger.info(f"Email click tracked: {email_type} by {patient_email or 'anonymous'}")
        
        # Redirect to the appropriate page based on email type
        redirect_url = request.args.get('redirect_url', '/')
        
        return redirect(redirect_url)
        
    except Exception as e:
        logger.error(f"Error tracking email click: {str(e)}")
        db.session.rollback()
        
        # Still redirect even if tracking fails
        redirect_url = request.args.get('redirect_url', '/')
        return redirect(redirect_url)

@tracking.route('/engagement-stats', methods=['GET'])
@login_required
def get_engagement_stats():
    """Get engagement statistics for analytics dashboard"""
    try:
        # Get query parameters for filtering
        clinic_id = request.args.get('clinic_id')
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        
        # Base queries
        page_view_query = PageViewLog.query
        cta_query = CTAInteractionLog.query
        
        # Apply filters
        if clinic_id:
            page_view_query = page_view_query.filter_by(clinic_id=clinic_id)
            cta_query = cta_query.filter_by(clinic_id=clinic_id)
        
        if date_from:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
            page_view_query = page_view_query.filter(PageViewLog.created_at >= date_from_obj)
            cta_query = cta_query.filter(CTAInteractionLog.created_at >= date_from_obj)
        
        if date_to:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
            page_view_query = page_view_query.filter(PageViewLog.created_at <= date_to_obj)
            cta_query = cta_query.filter(CTAInteractionLog.created_at <= date_to_obj)
        
        # Calculate metrics
        stats = {
            'page_views': {
                'stage_a_views': page_view_query.filter(PageViewLog.page_type.like('stage_a_step%')).count(),
                'stage_b_views': page_view_query.filter(PageViewLog.page_type.like('stage_b_step%')).count(),
                'total_page_views': page_view_query.count(),
                'unique_sessions': page_view_query.with_entities(PageViewLog.session_id).distinct().count()
            },
            'cta_interactions': {
                'schedule_sleep_test': cta_query.filter_by(cta_type='schedule_sleep_test').count(),
                'complete_advanced_assessment': cta_query.filter_by(cta_type='complete_advanced_assessment').count(),
                'quiz_submissions': cta_query.filter_by(cta_type='quiz_submission').count(),
                'email_link_clicks': cta_query.filter(CTAInteractionLog.email_source.isnot(None)).count(),
                'web_clicks': cta_query.filter(CTAInteractionLog.email_source.is_(None)).count(),
                'total_cta_clicks': cta_query.count()
            },
            'email_engagement': {
                'total_email_clicks': cta_query.filter(CTAInteractionLog.email_source.isnot(None)).count(),
                'doctor_notification_clicks': cta_query.filter_by(email_source='doctor_notification').count(),
                'patient_follow_up_clicks': cta_query.filter_by(email_source='patient_follow_up').count()
            },
            'conversion_metrics': {
                'stage_a_to_b_rate': 0,  # Will calculate below
                'stage_a_completion_rate': 0,  # Will calculate below
                'stage_b_completion_rate': 0   # Will calculate below
            }
        }
        
        # Calculate conversion rates
        stage_a_views = stats['page_views']['stage_a_views']
        stage_b_views = stats['page_views']['stage_b_views']
        quiz_submissions = stats['cta_interactions']['quiz_submissions']
        
        if stage_a_views > 0:
            stats['conversion_metrics']['stage_a_to_b_rate'] = round((stage_b_views / stage_a_views) * 100, 2)
            stats['conversion_metrics']['stage_a_completion_rate'] = round((quiz_submissions / stage_a_views) * 100, 2)
        
        if stage_b_views > 0:
            stage_b_submissions = cta_query.filter_by(cta_type='quiz_submission', quiz_type='advanced_quiz').count()
            stats['conversion_metrics']['stage_b_completion_rate'] = round((stage_b_submissions / stage_b_views) * 100, 2)
        
        return jsonify({
            'status': 'success',
            'stats': stats
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting engagement stats: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to get engagement stats'
        }), 500

@tracking.route('/conversion-funnel', methods=['GET'])
@login_required
def get_conversion_funnel():
    """Get detailed conversion funnel analytics"""
    try:
        clinic_id = request.args.get('clinic_id')
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        
        # Base queries with filters
        page_view_query = PageViewLog.query
        cta_query = CTAInteractionLog.query
        
        if clinic_id:
            page_view_query = page_view_query.filter_by(clinic_id=clinic_id)
            cta_query = cta_query.filter_by(clinic_id=clinic_id)
        
        if date_from:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
            page_view_query = page_view_query.filter(PageViewLog.created_at >= date_from_obj)
            cta_query = cta_query.filter(CTAInteractionLog.created_at >= date_from_obj)
        
        if date_to:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
            page_view_query = page_view_query.filter(PageViewLog.created_at <= date_to_obj)
            cta_query = cta_query.filter(CTAInteractionLog.created_at <= date_to_obj)
        
        # Conversion funnel steps
        funnel = {
            'step_1_stage_a_views': page_view_query.filter_by(page_type='stage_a_step_1').count(),
            'step_2_stage_a_submissions': cta_query.filter_by(cta_type='submit_stage_a', quiz_type='basic_quiz').count(),
            'step_3_stage_b_views': page_view_query.filter_by(page_type='stage_b_step_1').count(),
            'step_4_stage_b_submissions': cta_query.filter_by(cta_type='submit_stage_b', quiz_type='advanced_quiz').count(),
            'step_5_schedule_clicks': cta_query.filter_by(cta_type='schedule_sleep_test').count()
        }
        
        # Calculate drop-off rates
        step1 = funnel['step_1_stage_a_views']
        if step1 > 0:
            funnel['conversion_rates'] = {
                'stage_a_completion': round((funnel['step_2_stage_a_submissions'] / step1) * 100, 2),
                'stage_a_to_b': round((funnel['step_3_stage_b_views'] / step1) * 100, 2),
                'overall_completion': round((funnel['step_4_stage_b_submissions'] / step1) * 100, 2),
                'schedule_conversion': round((funnel['step_5_schedule_clicks'] / step1) * 100, 2)
            }
        else:
            funnel['conversion_rates'] = {
                'stage_a_completion': 0,
                'stage_a_to_b': 0,
                'overall_completion': 0,
                'schedule_conversion': 0
            }
        
        return jsonify({
            'status': 'success',
            'funnel': funnel
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting conversion funnel: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to get conversion funnel'
        }), 500

@tracking.route('/test', methods=['GET'])
def test_tracking():
    """Test endpoint to verify tracking is working"""
    return jsonify({
        'status': 'success',
        'message': 'Tracking blueprint is working!',
        'endpoints': [
            '/api/tracking/track-page-view',
            '/api/tracking/track-cta-click', 
            '/api/tracking/track-email-click',
            '/api/tracking/engagement-stats',
            '/api/tracking/conversion-funnel'
        ]
    }) 