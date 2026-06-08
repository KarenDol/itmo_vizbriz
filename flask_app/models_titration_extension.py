# Database Extensions for Patient Comments and Titration Management
# Add these models to your existing models.py file

from flask_app.extensions import db
from datetime import datetime
from enum import Enum

class TitrationStatus(Enum):
    """Enum for titration status tracking"""
    NOT_STARTED = 'not_started'
    IN_PROGRESS = 'in_progress'
    COMPLETED = 'completed'
    PAUSED = 'paused'
    FAILED = 'failed'

class CommentCategory(Enum):
    """Enum for comment categories"""
    GENERAL = 'general'
    TITRATION = 'titration'
    DEVICE_FITTING = 'device_fitting'
    FOLLOW_UP = 'follow_up'
    COMPLIANCE = 'compliance'
    SIDE_EFFECTS = 'side_effects'
    SLEEP_QUALITY = 'sleep_quality'
    ADJUSTMENT = 'adjustment'

class PatientTitrationSession(db.Model):
    """Tracks individual titration sessions for a patient"""
    __tablename__ = 'patient_titration_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    device_order_id = db.Column(db.Integer, db.ForeignKey('patient_device_order.id'), nullable=True)
    
    # Session details
    session_date = db.Column(db.DateTime, nullable=False)
    session_type = db.Column(db.String(50), nullable=False)  # 'initial', 'follow_up', 'adjustment', 'emergency'
    status = db.Column(db.Enum(TitrationStatus), default=TitrationStatus.NOT_STARTED)
    
    # Device settings for this session
    mandibular_advancement_mm = db.Column(db.Numeric(5, 2), nullable=True)  # e.g., 2.5mm
    vertical_opening_mm = db.Column(db.Numeric(5, 2), nullable=True)  # e.g., 3.0mm
    device_pressure = db.Column(db.String(50), nullable=True)  # For CPAP devices
    
    # Patient feedback
    comfort_rating = db.Column(db.Integer, nullable=True)  # 1-10 scale
    sleep_quality_rating = db.Column(db.Integer, nullable=True)  # 1-10 scale
    side_effects = db.Column(db.Text, nullable=True)  # JSON or text description
    compliance_hours = db.Column(db.Numeric(4, 1), nullable=True)  # Hours worn per night
    
    # Clinical notes
    dentist_notes = db.Column(db.Text, nullable=True)
    next_adjustment_plan = db.Column(db.Text, nullable=True)
    next_follow_up_date = db.Column(db.DateTime, nullable=True)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'), nullable=False)
    
    # Relationships
    patient = db.relationship('Patient', backref='titration_sessions')
    device_order = db.relationship('PatientDeviceOrder', backref='titration_sessions')
    created_by = db.relationship('Dentist', backref='created_titration_sessions')
    
    def __repr__(self):
        return f'<PatientTitrationSession {self.id}: Patient {self.patient_id} - {self.session_date}>'

class PatientCommentExtended(db.Model):
    """Extended comment system with categories and threading"""
    __tablename__ = 'patient_comments_extended'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'), nullable=False)
    
    # Comment content
    content = db.Column(db.Text, nullable=False)
    category = db.Column(db.Enum(CommentCategory), default=CommentCategory.GENERAL)
    
    # Threading support
    parent_comment_id = db.Column(db.Integer, db.ForeignKey('patient_comments_extended.id'), nullable=True)
    is_urgent = db.Column(db.Boolean, default=False)
    is_internal = db.Column(db.Boolean, default=False)  # Internal notes vs patient-facing
    
    # Links to related records
    titration_session_id = db.Column(db.Integer, db.ForeignKey('patient_titration_sessions.id'), nullable=True)
    device_order_id = db.Column(db.Integer, db.ForeignKey('patient_device_order.id'), nullable=True)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    patient = db.relationship('Patient', backref='extended_comments')
    dentist = db.relationship('Dentist', backref='extended_comments')
    parent_comment = db.relationship('PatientCommentExtended', remote_side=[id], backref='replies')
    titration_session = db.relationship('PatientTitrationSession', backref='comments')
    device_order = db.relationship('PatientDeviceOrder', backref='comments')
    
    def __repr__(self):
        return f'<PatientCommentExtended {self.id}: {self.category.value} - {self.created_at}>'

class PatientTitrationProgress(db.Model):
    """Tracks overall titration progress and milestones"""
    __tablename__ = 'patient_titration_progress'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    device_order_id = db.Column(db.Integer, db.ForeignKey('patient_device_order.id'), nullable=False)
    
    # Progress tracking
    current_status = db.Column(db.Enum(TitrationStatus), default=TitrationStatus.NOT_STARTED)
    start_date = db.Column(db.DateTime, nullable=True)
    target_completion_date = db.Column(db.DateTime, nullable=True)
    actual_completion_date = db.Column(db.DateTime, nullable=True)
    
    # Milestones
    initial_fitting_completed = db.Column(db.Boolean, default=False)
    first_week_completed = db.Column(db.Boolean, default=False)
    first_month_completed = db.Column(db.Boolean, default=False)
    optimal_settings_found = db.Column(db.Boolean, default=False)
    
    # Current optimal settings
    optimal_advancement_mm = db.Column(db.Numeric(5, 2), nullable=True)
    optimal_vertical_mm = db.Column(db.Numeric(5, 2), nullable=True)
    optimal_pressure = db.Column(db.String(50), nullable=True)
    
    # Success metrics
    final_ahi_reduction = db.Column(db.Numeric(5, 2), nullable=True)  # % reduction
    patient_satisfaction_score = db.Column(db.Integer, nullable=True)  # 1-10
    compliance_rate = db.Column(db.Numeric(5, 2), nullable=True)  # % nights worn
    
    # Notes
    progress_notes = db.Column(db.Text, nullable=True)
    challenges_encountered = db.Column(db.Text, nullable=True)
    success_factors = db.Column(db.Text, nullable=True)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    patient = db.relationship('Patient', backref='titration_progress')
    device_order = db.relationship('PatientDeviceOrder', backref='titration_progress')
    
    def __repr__(self):
        return f'<PatientTitrationProgress {self.id}: Patient {self.patient_id} - {self.current_status.value}>'

class PatientComplianceLog(db.Model):
    """Tracks daily compliance and usage patterns"""
    __tablename__ = 'patient_compliance_log'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    device_order_id = db.Column(db.Integer, db.ForeignKey('patient_device_order.id'), nullable=False)
    
    # Date and usage
    log_date = db.Column(db.Date, nullable=False)
    hours_worn = db.Column(db.Numeric(4, 1), nullable=True)  # Hours device was worn
    was_worn = db.Column(db.Boolean, default=False)  # Simple yes/no
    
    # Quality metrics
    sleep_quality_rating = db.Column(db.Integer, nullable=True)  # 1-10
    comfort_rating = db.Column(db.Integer, nullable=True)  # 1-10
    side_effects_reported = db.Column(db.Boolean, default=False)
    side_effects_description = db.Column(db.Text, nullable=True)
    
    # Device settings used
    advancement_mm = db.Column(db.Numeric(5, 2), nullable=True)
    vertical_mm = db.Column(db.Numeric(5, 2), nullable=True)
    
    # Notes
    patient_notes = db.Column(db.Text, nullable=True)
    dentist_notes = db.Column(db.Text, nullable=True)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    patient = db.relationship('Patient', backref='compliance_logs')
    device_order = db.relationship('PatientDeviceOrder', backref='compliance_logs')
    
    # Indexes for performance
    __table_args__ = (
        db.Index('idx_patient_date', 'patient_id', 'log_date'),
        db.Index('idx_device_date', 'device_order_id', 'log_date'),
    )
    
    def __repr__(self):
        return f'<PatientComplianceLog {self.id}: Patient {self.patient_id} - {self.log_date}>'

# Migration script to add these tables
def create_titration_tables():
    """Create all titration-related tables"""
    db.create_all()
    print("Titration tables created successfully!")

# Example usage and relationships:
"""
# Get all titration sessions for a patient
patient = Patient.query.get(patient_id)
sessions = patient.titration_sessions

# Get all comments for a specific titration session
session = PatientTitrationSession.query.get(session_id)
comments = session.comments

# Get compliance logs for the last 30 days
from datetime import datetime, timedelta
thirty_days_ago = datetime.now() - timedelta(days=30)
compliance_logs = PatientComplianceLog.query.filter(
    PatientComplianceLog.patient_id == patient_id,
    PatientComplianceLog.log_date >= thirty_days_ago
).order_by(PatientComplianceLog.log_date.desc()).all()

# Get titration progress
progress = PatientTitrationProgress.query.filter_by(patient_id=patient_id).first()
"""
