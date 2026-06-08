"""
Token Usage Dashboard Routes
Admin-only routes for tracking LLM token usage, costs, and analytics
"""

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from flask_app.models import db, LLMInteraction, Dentist
from sqlalchemy import func, desc, case
from datetime import datetime, timedelta
import logging
import pytz

# Create token usage blueprint
token_usage = Blueprint('token_usage', __name__, url_prefix='/admin/token-usage')

logger = logging.getLogger(__name__)

# Eastern timezone for display
EASTERN = pytz.timezone('US/Eastern')

def format_datetime_eastern(dt):
    """Convert datetime to Eastern timezone and format"""
    if dt is None:
        return 'N/A'
    # If datetime is naive, assume UTC
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    # Convert to Eastern
    eastern_dt = dt.astimezone(EASTERN)
    # Format with timezone abbreviation (EDT or EST)
    return eastern_dt.strftime('%Y-%m-%d %I:%M %p %Z')

def admin_required(f):
    """Decorator to ensure only admin users can access the route"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Access denied. Admin privileges required.', 'error')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated_function

@token_usage.route('/')
@login_required
@admin_required
def dashboard():
    """Main token usage dashboard"""
    try:
        return render_template('admin_token_usage.html')
    except Exception as e:
        logger.error(f"Error loading token usage dashboard: {str(e)}")
        flash('Error loading token usage dashboard', 'error')
        return redirect(url_for('main.admin_home'))

@token_usage.route('/api/overview')
@login_required
@admin_required
def api_overview():
    """Get overview statistics for selected time period"""
    try:
        period = request.args.get('period', 'day')  # day, week, month, year
        
        # Calculate date range
        end_date = datetime.now()
        if period == 'day':
            start_date = end_date - timedelta(days=1)
        elif period == 'week':
            start_date = end_date - timedelta(weeks=1)
        elif period == 'month':
            start_date = end_date - timedelta(days=30)
        elif period == 'year':
            start_date = end_date - timedelta(days=365)
        else:
            start_date = end_date - timedelta(days=1)
        
        # Get total tokens
        total_tokens = db.session.query(
            func.sum(LLMInteraction.token_count_estimated)
        ).filter(
            LLMInteraction.created_at >= start_date,
            LLMInteraction.created_at <= end_date
        ).scalar() or 0
        
        # Get input/output tokens
        input_tokens = db.session.query(
            func.sum(LLMInteraction.token_count_estimated)
        ).filter(
            LLMInteraction.created_at >= start_date,
            LLMInteraction.created_at <= end_date,
            LLMInteraction.interaction_type == 'prompt'
        ).scalar() or 0
        
        output_tokens = db.session.query(
            func.sum(LLMInteraction.token_count_estimated)
        ).filter(
            LLMInteraction.created_at >= start_date,
            LLMInteraction.created_at <= end_date,
            LLMInteraction.interaction_type == 'response'
        ).scalar() or 0
        
        # Get total sessions (unique session_ids)
        total_sessions = db.session.query(
            func.count(func.distinct(LLMInteraction.session_id))
        ).filter(
            LLMInteraction.created_at >= start_date,
            LLMInteraction.created_at <= end_date
        ).scalar() or 0
        
        # Get most used model
        most_used_model = db.session.query(
            LLMInteraction.model_name,
            func.count(LLMInteraction.id).label('count')
        ).filter(
            LLMInteraction.created_at >= start_date,
            LLMInteraction.created_at <= end_date
        ).group_by(
            LLMInteraction.model_name
        ).order_by(
            desc('count')
        ).first()
        
        # Get error count
        error_count = db.session.query(
            func.count(LLMInteraction.id)
        ).filter(
            LLMInteraction.created_at >= start_date,
            LLMInteraction.created_at <= end_date,
            LLMInteraction.status != 'success'
        ).scalar() or 0
        
        # Convert dates to Eastern timezone for display
        start_date_eastern = pytz.utc.localize(start_date).astimezone(EASTERN)
        end_date_eastern = pytz.utc.localize(end_date).astimezone(EASTERN)
        
        return jsonify({
            'success': True,
            'period': period,
            'total_tokens': int(total_tokens),
            'input_tokens': int(input_tokens),
            'output_tokens': int(output_tokens),
            'total_sessions': int(total_sessions),
            'most_used_model': most_used_model[0] if most_used_model else 'N/A',
            'error_count': int(error_count),
            'start_date': start_date_eastern.strftime('%Y-%m-%d %I:%M %p %Z'),
            'end_date': end_date_eastern.strftime('%Y-%m-%d %I:%M %p %Z')
        })
        
    except Exception as e:
        logger.error(f"Error in token usage overview: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@token_usage.route('/api/by-model')
@login_required
@admin_required
def api_by_model():
    """Get token usage aggregated by model"""
    try:
        period = request.args.get('period', 'month')
        
        # Calculate date range
        end_date = datetime.now()
        if period == 'day':
            start_date = end_date - timedelta(days=1)
        elif period == 'week':
            start_date = end_date - timedelta(weeks=1)
        elif period == 'month':
            start_date = end_date - timedelta(days=30)
        elif period == 'year':
            start_date = end_date - timedelta(days=365)
        else:
            start_date = end_date - timedelta(days=30)
        
        # Get aggregated data by model
        model_stats = db.session.query(
            LLMInteraction.model_name,
            func.sum(case(
                (LLMInteraction.interaction_type == 'prompt', LLMInteraction.token_count_estimated),
                else_=0
            )).label('input_tokens'),
            func.sum(case(
                (LLMInteraction.interaction_type == 'response', LLMInteraction.token_count_estimated),
                else_=0
            )).label('output_tokens'),
            func.count(func.distinct(LLMInteraction.session_id)).label('session_count')
        ).filter(
            LLMInteraction.created_at >= start_date,
            LLMInteraction.created_at <= end_date
        ).group_by(
            LLMInteraction.model_name
        ).all()
        
        # Format response
        models = []
        for stat in model_stats:
            models.append({
                'model_name': stat.model_name,
                'input_tokens': int(stat.input_tokens or 0),
                'output_tokens': int(stat.output_tokens or 0),
                'total_tokens': int((stat.input_tokens or 0) + (stat.output_tokens or 0)),
                'session_count': int(stat.session_count or 0)
            })
        
        return jsonify({
            'success': True,
            'models': models,
            'period': period
        })
        
    except Exception as e:
        logger.error(f"Error in token usage by model: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@token_usage.route('/api/by-user')
@login_required
@admin_required
def api_by_user():
    """Get token usage aggregated by user"""
    try:
        period = request.args.get('period', 'month')
        
        # Calculate date range
        end_date = datetime.now()
        if period == 'day':
            start_date = end_date - timedelta(days=1)
        elif period == 'week':
            start_date = end_date - timedelta(weeks=1)
        elif period == 'month':
            start_date = end_date - timedelta(days=30)
        elif period == 'year':
            start_date = end_date - timedelta(days=365)
        else:
            start_date = end_date - timedelta(days=30)
        
        # Get aggregated data by user
        user_stats = db.session.query(
            LLMInteraction.user_id,
            Dentist.name.label('user_name'),
            Dentist.email.label('user_email'),
            func.sum(LLMInteraction.token_count_estimated).label('total_tokens'),
            func.count(func.distinct(LLMInteraction.session_id)).label('session_count')
        ).outerjoin(
            Dentist, LLMInteraction.user_id == Dentist.id
        ).filter(
            LLMInteraction.created_at >= start_date,
            LLMInteraction.created_at <= end_date
        ).group_by(
            LLMInteraction.user_id,
            Dentist.name,
            Dentist.email
        ).order_by(
            desc('total_tokens')
        ).all()
        
        # Format response
        users = []
        for stat in user_stats:
            users.append({
                'user_id': stat.user_id,
                'user_name': stat.user_name or 'System',
                'user_email': stat.user_email or 'N/A',
                'total_tokens': int(stat.total_tokens or 0),
                'session_count': int(stat.session_count or 0)
            })
        
        return jsonify({
            'success': True,
            'users': users,
            'period': period
        })
        
    except Exception as e:
        logger.error(f"Error in token usage by user: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@token_usage.route('/api/trends')
@login_required
@admin_required
def api_trends():
    """Get token usage trends over time"""
    try:
        period = request.args.get('period', 'month')
        granularity = request.args.get('granularity', 'day')  # day, week
        
        # Calculate date range
        end_date = datetime.now()
        if period == 'day':
            start_date = end_date - timedelta(days=1)
            date_format = '%Y-%m-%d %H:00'
        elif period == 'week':
            start_date = end_date - timedelta(weeks=1)
            date_format = '%Y-%m-%d'
        elif period == 'month':
            start_date = end_date - timedelta(days=30)
            date_format = '%Y-%m-%d'
        elif period == 'year':
            start_date = end_date - timedelta(days=365)
            date_format = '%Y-%m'
        else:
            start_date = end_date - timedelta(days=30)
            date_format = '%Y-%m-%d'
        
        # Get daily/hourly trends by model
        trends = db.session.query(
            func.date_format(LLMInteraction.created_at, date_format).label('time_period'),
            LLMInteraction.model_name,
            func.sum(LLMInteraction.token_count_estimated).label('total_tokens')
        ).filter(
            LLMInteraction.created_at >= start_date,
            LLMInteraction.created_at <= end_date
        ).group_by(
            'time_period',
            LLMInteraction.model_name
        ).order_by(
            'time_period'
        ).all()
        
        # Format response - organize by time period
        trends_data = {}
        for trend in trends:
            time_period = trend.time_period
            if time_period not in trends_data:
                trends_data[time_period] = {}
            trends_data[time_period][trend.model_name] = int(trend.total_tokens or 0)
        
        # Convert to list format for Chart.js
        labels = sorted(trends_data.keys())
        datasets = {}
        
        for time_period in labels:
            for model_name, tokens in trends_data[time_period].items():
                if model_name not in datasets:
                    datasets[model_name] = []
                datasets[model_name].append(tokens)
        
        return jsonify({
            'success': True,
            'labels': labels,
            'datasets': [
                {
                    'label': model_name,
                    'data': data
                }
                for model_name, data in datasets.items()
            ],
            'period': period
        })
        
    except Exception as e:
        logger.error(f"Error in token usage trends: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@token_usage.route('/api/by-endpoint')
@login_required
@admin_required
def api_by_endpoint():
    """Get token usage aggregated by endpoint"""
    try:
        period = request.args.get('period', 'month')
        
        # Calculate date range
        end_date = datetime.now()
        if period == 'day':
            start_date = end_date - timedelta(days=1)
        elif period == 'week':
            start_date = end_date - timedelta(weeks=1)
        elif period == 'month':
            start_date = end_date - timedelta(days=30)
        elif period == 'year':
            start_date = end_date - timedelta(days=365)
        else:
            start_date = end_date - timedelta(days=30)
        
        # Get aggregated data by endpoint with input/output breakdown
        endpoint_stats = db.session.query(
            LLMInteraction.page_endpoint,
            func.sum(case(
                (LLMInteraction.interaction_type == 'prompt', LLMInteraction.token_count_estimated),
                else_=0
            )).label('input_tokens'),
            func.sum(case(
                (LLMInteraction.interaction_type == 'response', LLMInteraction.token_count_estimated),
                else_=0
            )).label('output_tokens'),
            func.count(func.distinct(LLMInteraction.session_id)).label('session_count')
        ).filter(
            LLMInteraction.created_at >= start_date,
            LLMInteraction.created_at <= end_date,
            LLMInteraction.page_endpoint.isnot(None)
        ).group_by(
            LLMInteraction.page_endpoint
        ).order_by(
            desc(func.sum(LLMInteraction.token_count_estimated))
        ).limit(20).all()
        
        # Format response
        endpoints = []
        for stat in endpoint_stats:
            input_tokens = int(stat.input_tokens or 0)
            output_tokens = int(stat.output_tokens or 0)
            endpoints.append({
                'endpoint': stat.page_endpoint,
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'total_tokens': input_tokens + output_tokens,
                'session_count': int(stat.session_count or 0)
            })
        
        return jsonify({
            'success': True,
            'endpoints': endpoints,
            'period': period
        })
        
    except Exception as e:
        logger.error(f"Error in token usage by endpoint: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@token_usage.route('/api/by-patient')
@login_required
@admin_required
def api_by_patient():
    """Get token usage aggregated by patient"""
    try:
        from flask_app.models import Patient
        
        period = request.args.get('period', 'month')
        limit = int(request.args.get('limit', 50))  # Top 50 patients by default
        
        # Calculate date range
        end_date = datetime.now()
        if period == 'day':
            start_date = end_date - timedelta(days=1)
        elif period == 'week':
            start_date = end_date - timedelta(weeks=1)
        elif period == 'month':
            start_date = end_date - timedelta(days=30)
        elif period == 'year':
            start_date = end_date - timedelta(days=365)
        else:
            start_date = end_date - timedelta(days=30)
        
        # Get aggregated data by patient with input/output tokens (including NULL patients)
        patient_stats = db.session.query(
            LLMInteraction.patient_id,
            Patient.name.label('patient_name'),
            Patient.email.label('patient_email'),
            func.sum(case(
                (LLMInteraction.interaction_type == 'prompt', LLMInteraction.token_count_estimated),
                else_=0
            )).label('input_tokens'),
            func.sum(case(
                (LLMInteraction.interaction_type == 'response', LLMInteraction.token_count_estimated),
                else_=0
            )).label('output_tokens'),
            func.count(func.distinct(LLMInteraction.session_id)).label('session_count'),
            func.max(LLMInteraction.created_at).label('last_interaction')
        ).outerjoin(
            Patient, LLMInteraction.patient_id == Patient.id
        ).filter(
            LLMInteraction.created_at >= start_date,
            LLMInteraction.created_at <= end_date
        ).group_by(
            LLMInteraction.patient_id,
            Patient.name,
            Patient.email
        ).order_by(
            desc(func.sum(LLMInteraction.token_count_estimated))
        ).limit(limit).all()
        
        # Format response
        patients = []
        for stat in patient_stats:
            input_tokens = int(stat.input_tokens or 0)
            output_tokens = int(stat.output_tokens or 0)
            total_tokens = input_tokens + output_tokens
            
            # Handle NULL patient_id (system operations, no patient context)
            if stat.patient_id is None:
                patient_display_id = 'N/A'
                patient_name = 'No Patient (System Usage)'
                patient_email = 'System operations, background jobs'
            else:
                patient_display_id = stat.patient_id
                patient_name = stat.patient_name or f'Patient #{stat.patient_id}'
                patient_email = stat.patient_email or 'N/A'
            
            patients.append({
                'patient_id': patient_display_id,
                'patient_name': patient_name,
                'patient_email': patient_email,
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'total_tokens': total_tokens,
                'session_count': int(stat.session_count or 0),
                'last_interaction': format_datetime_eastern(stat.last_interaction)
            })
        
        return jsonify({
            'success': True,
            'patients': patients,
            'period': period
        })
        
    except Exception as e:
        logger.error(f"Error in token usage by patient: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

