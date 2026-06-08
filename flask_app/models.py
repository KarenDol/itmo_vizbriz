from flask_login import UserMixin
from datetime import datetime
from typing import Dict, Any
from flask_app.extensions import db
import random
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import event

# Association table for dentist-DSO many-to-many relationship
dentist_dso_association = db.Table('dentist_dso_association',
    db.Column('dentist_id', db.Integer, db.ForeignKey('dentists.id'), primary_key=True),
    db.Column('dso_id', db.Integer, db.ForeignKey('dsos.id'), primary_key=True)
)

# Association table for dentist-clinic many-to-many relationship
dentist_clinic_association = db.Table('dentist_clinic_association',
    db.Column('dentist_id', db.Integer, db.ForeignKey('dentists.id'), primary_key=True),
    db.Column('clinic_id', db.Integer, db.ForeignKey('clinics.id'), primary_key=True),
    db.Column('is_primary', db.Boolean, default=False, comment='Indicates if this is the dentist\'s primary clinic'),
    db.Column('created_at', db.DateTime, default=datetime.utcnow),
    db.Column('updated_at', db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
)

class Dentist(UserMixin, db.Model):
    __tablename__ = 'dentists'  # Case-sensitive
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20))
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='dentist')
    comment = db.Column(db.Text)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)
    country = db.Column(db.String(100), nullable=False)
    DSO = db.Column(db.String(100), nullable=True)  # Legacy DSO field for backward compatibility
    
    # Many-to-many relationship with DSOs
    dsos = db.relationship('DSO', secondary=dentist_dso_association, 
                          backref=db.backref('dentists', lazy='dynamic'),
                          lazy='dynamic')
    
    # Many-to-many relationship with clinics
    clinics = db.relationship('Clinic', secondary=dentist_clinic_association,
                             backref=db.backref('dentists', lazy='dynamic'),
                             lazy='dynamic')
    
    patients = db.relationship('Patient', backref='dentist', lazy='dynamic')

    def set_password(self, password):
        self.password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)
    
    def get_dso_ids(self):
        """Get list of DSO IDs this dentist is associated with"""
        return [dso.id for dso in self.dsos]
    
    def get_clinic_ids(self):
        """Get list of clinic IDs this dentist is associated with"""
        return [clinic.id for clinic in self.clinics]
    
    def get_primary_clinic_id(self):
        """Get the primary clinic ID for this dentist"""
        # Query the association table to find the primary clinic
        result = db.session.query(dentist_clinic_association.c.clinic_id).filter(
            dentist_clinic_association.c.dentist_id == self.id,
            dentist_clinic_association.c.is_primary == True
        ).first()
        return result[0] if result else None
    
    def get_primary_clinic(self):
        """Get the primary clinic object for this dentist"""
        primary_clinic_id = self.get_primary_clinic_id()
        if primary_clinic_id:
            return Clinic.query.get(primary_clinic_id)
        return None
    
    def is_associated_with_dso(self, dso_id):
        """Check if dentist is associated with a specific DSO"""
        return self.dsos.filter_by(id=dso_id).first() is not None
    
    def is_associated_with_clinic(self, clinic_id):
        """Check if dentist is associated with a specific clinic"""
        return self.clinics.filter_by(id=clinic_id).first() is not None
    
    def set_primary_clinic(self, clinic_id):
        """Set a clinic as the primary clinic for this dentist"""
        # First, remove primary flag from all current associations
        db.session.execute(
            dentist_clinic_association.update().where(
                dentist_clinic_association.c.dentist_id == self.id
            ).values(is_primary=False)
        )
        
        # Then set the specified clinic as primary
        db.session.execute(
            dentist_clinic_association.update().where(
                dentist_clinic_association.c.dentist_id == self.id,
                dentist_clinic_association.c.clinic_id == clinic_id
            ).values(is_primary=True)
        )
        
        db.session.commit()
    
    def get_accessible_patients_new_system(self):
        """
        Get all patients this dentist can access via NEW DSO association system.
        This only returns quiz/form patients (those with clinic_id).
        """
        if self.role == 'admin':
            # Admin sees all quiz/form patients
            return Patient.query.filter(Patient.clinic_id.isnot(None)).all()
        
        # Get all DSO IDs this dentist is associated with
        dso_ids = self.get_dso_ids()
        if not dso_ids:
            return []
        
        # Get patients from clinics in those DSOs
        return (Patient.query
                .join(Clinic, Patient.clinic_id == Clinic.id)
                .filter(Clinic.dso_id.in_(dso_ids))
                .all())
    
    def get_accessible_consultation_requests(self):
        """Get all consultation requests this dentist can access based on DSO"""
        if self.role == 'admin':
            return ConsultationRequest.query.all()
        
        # Get consultation requests from patients this dentist can access
        accessible_patients = self.get_accessible_patients_new_system()
        patient_ids = [p.id for p in accessible_patients]
        
        return (ConsultationRequest.query
                .filter(ConsultationRequest.patient_id.in_(patient_ids))
                .all()) if patient_ids else []
    
    def get_accessible_quiz_submissions(self):
        """Get all quiz submissions this dentist can access based on DSO"""
        if self.role == 'admin':
            return ConversionQuiz.query.all()
        
        # Get quiz submissions from clinics in dentist's DSOs
        dso_ids = self.get_dso_ids()
        if not dso_ids:
            return []
        
        return (ConversionQuiz.query
                .join(Clinic, ConversionQuiz.clinic_id == Clinic.id)
                .filter(Clinic.dso_id.in_(dso_ids))
                .all())
    
    def can_access_patient(self, patient):
        """
        Check if dentist can access a specific patient based on clinic relationship.
        NEW: Uses clinic associations - dentist can view patients from clinics they work at
        LEGACY: Falls back to existing DSO string logic for backward compatibility
        """
        if self.role == 'admin':
            return True
        
        # NEW SYSTEM: Check if patient is from a clinic the dentist works at
        if hasattr(patient, 'clinic_id') and patient.clinic_id:
            # Check if dentist is associated with the patient's clinic
            return self.is_associated_with_clinic(patient.clinic_id)
        
        # LEGACY SYSTEM: Keep existing DSO-based access (for backward compatibility)
        # Only use if both dentists have the old DSO string field
        if (hasattr(patient, 'dentist') and patient.dentist and 
            hasattr(self, 'DSO') and hasattr(patient.dentist, 'DSO') and
            self.DSO and patient.dentist.DSO):
            # Current logic: dentist can see all patients in same DSO
            return patient.dentist.DSO == self.DSO
        
        # Fallback: direct ownership (dentist can always see their own patients)
        return patient.dentist_id == self.id

class Clinic(db.Model):
    __tablename__ = 'clinics'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)  # Mandatory
    dso_id = db.Column(db.Integer, db.ForeignKey('dsos.id'), nullable=False)  # Foreign key to DSO table
    address = db.Column(db.Text, nullable=True)       # Optional
    email = db.Column(db.String(120), nullable=False) # Mandatory
    telephone = db.Column(db.String(20), nullable=True)
    contact_person = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    status = db.Column(db.Enum('active', 'inactive', 'pending', name='clinic_status'), default='active')

    # Relationship to DSO
    dso_info = db.relationship('DSO', backref='clinics', lazy=True)

    def __repr__(self):
        return f'<Clinic {self.id}: {self.name}>'

class DSO(db.Model):
    __tablename__ = 'dsos'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)          # Mandatory
    email = db.Column(db.String(120), nullable=False)         # Mandatory
    contact_person = db.Column(db.String(255), nullable=False) # Mandatory
    telephone = db.Column(db.String(20), nullable=False)      # Mandatory
    logo = db.Column(db.String(500), nullable=True)           # Optional - link to logo
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    status = db.Column(db.Enum('active', 'inactive', 'pending', name='dso_status'), default='active')

    @property
    def logo_url(self):
        """Convert logo path to Flask static URL"""
        if not self.logo:
            return None
        
        # If it's already a full URL (starts with http), return as-is
        if self.logo.startswith(('http://', 'https://')):
            return self.logo
        
        # If it's a local file path, convert to static URL format
        # Handle both forward slashes and backslashes
        clean_path = self.logo.replace('\\', '/').replace('flask_app/flask_static/', '').replace('flask_static/', '')
        
        # Remove leading slash if present
        if clean_path.startswith('/'):
            clean_path = clean_path[1:]
            
        return clean_path

    def __repr__(self):
        return f'<DSO {self.id}: {self.name}>'

class Patient(db.Model):
    __tablename__ = 'patients'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'), nullable=False)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=True)  # For DSO-based access control
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    id_number = db.Column(db.String(20), nullable=True)  # Israeli ID (teudat zehut) or national ID
    gender = db.Column(db.String(10))
    insurer = db.Column(db.String(100))
    policy_id = db.Column(db.String(50))
    address = db.Column(db.String(255))
    # New structured address fields (keeping original address field for backward compatibility)
    street_address = db.Column(db.String(255), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    state = db.Column(db.String(50), nullable=True)
    zip_code = db.Column(db.String(20), nullable=True)
    country = db.Column(db.String(50), nullable=True)
    dob = db.Column(db.Date)
    status = db.Column(db.String(20), default='New')
    create_date = db.Column(db.DateTime, default=datetime.utcnow)
    last_update = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    claim = db.Column(db.String(50))

    def __init__(self, *args, **kwargs):
        super(Patient, self).__init__(*args, **kwargs)
        if not self.id:  # Only generate new ID if one isn't provided
            while True:
                random_id = random.randint(10000, 99999)
                # Check if this ID already exists
                if not Patient.query.get(random_id):
                    self.id = random_id
                    break

    # New fields for patient management
    payment_method = db.Column(db.String(20), nullable=True)  # Field for payment method
  

    # Sleep-related fields
    snoring = db.Column(db.String(50))  
    snoring_other = db.Column(db.Text)  
    daytime_sleepiness = db.Column(db.String(50))  
    daytime_sleepiness_other = db.Column(db.Text)  
    sleep_study = db.Column(db.String(50))  
    sleep_study_date = db.Column(db.Date)  
    sleep_study_doctor = db.Column(db.String(100))  
    cpap_intolerant = db.Column(db.String(50))  
    cpap_intolerant_other = db.Column(db.Text)  
    upload_token = db.Column(db.String(255), nullable=True)  # Add this field

    # Relationships
    admin_files = db.relationship('AdminFile', back_populates='patient')
    files = db.relationship('File', backref='patient', lazy=True)
    statuses = db.relationship('PatientStatus', backref='patient', lazy=True)
    comments = db.relationship('PatientComment', backref='patient', lazy=True)  # New relationship for comments
    claims = db.relationship('Claim', backref='patient', lazy=True)
    clinic = db.relationship('Clinic', backref='patients', lazy=True)  # For DSO-based access control

    @property
    def country(self):
        return self.dentist.country if self.dentist else None

class File(db.Model):
    __tablename__ = 'files'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    file_type = db.Column(db.String(50))
    file_size = db.Column(db.Integer)
    s3_key = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    subcategory = db.Column(db.String(50), nullable=False)
    comment = db.Column(db.Text)
    mapping = db.Column(db.String(255))
    analyzed = db.Column(db.Boolean, default=False, comment='Whether this file has been analyzed for observations')

class AdminFile(db.Model):
    __tablename__ = 'adminfiles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    file_type = db.Column(db.String(50))
    file_size = db.Column(db.Integer)
    s3_key = db.Column(db.String(255), nullable=False)
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    is_public = db.Column(db.Boolean, nullable=False, default=False)  # New: public/private flag
    file_category = db.Column(db.String(100), nullable=True)  # New: file category (patient report, scan, observation)
    analyzed = db.Column(db.Boolean, default=False, comment='Whether this file has been analyzed for observations')
    patient = db.relationship('Patient', back_populates='admin_files')


class PatientComment(db.Model):
    __tablename__ = 'patientcomments'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'), nullable=False)
    
    # New fields for enhanced comment functionality
    comment_type = db.Column(db.String(50), nullable=True, default='general')  # 'titration', 'consultation', 'delivery', 'initial', 'general'
    numeric_value = db.Column(db.Numeric(10, 2), nullable=True)  # For titration settings, ratings, etc.
    numeric_unit = db.Column(db.String(20), nullable=True)  # 'mm', 'rating', 'hours', etc.
    is_urgent = db.Column(db.Boolean, default=False)
    is_internal = db.Column(db.Boolean, default=False)
    
    dentist = db.relationship('Dentist', backref='comments')

class Claim(db.Model):
    __tablename__ = 'claims'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'), nullable=False)
    insurer = db.Column(db.String(255), nullable=False)
    created_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_update = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    treatment_recommendations = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), nullable=False)
    claim_amount = db.Column(db.Numeric(10, 2), nullable=False)
    deductible = db.Column(db.Numeric(10, 2), nullable=True)
    diagnosis = db.Column(db.String(255), nullable=True)
    comments = db.relationship('Comment', backref='claim', lazy=True)
    dentist = db.relationship('Dentist', backref='claims')
    
class Comment(db.Model):
    __tablename__ = 'comments'
    id = db.Column(db.Integer, primary_key=True)
    claim_id = db.Column(db.Integer, db.ForeignKey('claims.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_date = db.Column(db.DateTime, default=datetime.utcnow)

class PatientStatus(db.Model):
    __tablename__ = 'patient_status'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    status_type = db.Column(db.String(255), nullable=False)
    status_value = db.Column(db.String(255), nullable=False)
    comment = db.Column(db.Text, nullable=True)
    mapping = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class StatusOption(db.Model):
    __tablename__ = 'status_options'
    id = db.Column(db.Integer, primary_key=True)
    status_type = db.Column(db.String(255), nullable=False)
    status_value = db.Column(db.String(255), nullable=False)
    __table_args__ = (db.UniqueConstraint('status_type', 'status_value', name='unique_status_type_value'),)


class DataSources(db.Model):
    __tablename__ = 'datasources'
    DataSourceID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    Name = db.Column(db.String(255), nullable=False)
    Description = db.Column(db.Text)
    ReportType = db.Column(db.String(255))
    
    # One-to-many relationship with Observations
    observations = db.relationship('Observations', backref='datasource', lazy=True)
    
    def __repr__(self):
        return f"<DataSources(DataSourceID={self.DataSourceID}, Name='{self.Name}')>"

class Observations(db.Model):
    __tablename__ = 'observations'
    ObservationID = db.Column(db.Integer, primary_key=True, autoincrement=True)
    Text = db.Column(db.Text, nullable=False)
    # This assumes that each observation is linked to a data source
    DataSourceID = db.Column(db.Integer, db.ForeignKey('datasources.DataSourceID'), nullable=False)
    
    def __repr__(self):
        return f"<Observations(ObservationID={self.ObservationID}, Text='{self.Text[:20]}...', DataSourceID={self.DataSourceID})>"
    

class ObservationsAndPrompts(db.Model):
    __tablename__ = 'observationsandprompts'
    
    AnalysisID = db.Column(db.Integer, primary_key=True)
    DataSourceID = db.Column(db.Integer, db.ForeignKey('datasources.DataSourceID'), nullable=False)
    FileName = db.Column(db.String(255))
    FileURL = db.Column(db.String(1024))
    AnalysisDate = db.Column(db.DateTime, default=datetime.utcnow)
    
    # JSON columns for different types of data
    ObservationDetails = db.Column(db.JSON)  # Specific observations from analysis
    DataSourceObservations = db.Column(db.JSON)  # Observations related to data source
    GeneralObservations = db.Column(db.JSON)  # General observations
    PromptsUsed = db.Column(db.JSON)  # All prompts used in analysis
    
    # Relationship
    data_source = db.relationship('DataSources', backref='observations_and_prompts')

class ConversionQuiz(db.Model):
    __tablename__ = 'conversion_quiz'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer)
    quiz_input = db.Column(db.Text, nullable=False)  # JSON string of quiz answers
    cta = db.Column(db.Text)  # Call to action for patient
    clinic_email = db.Column(db.String(120), nullable=False)
    patient_email = db.Column(db.String(120), nullable=False)
    ai_response = db.Column(db.Text)
    quiz_type = db.Column(db.String(50), nullable=False, default='basic_quiz')  # 'basic_quiz' or 'advanced_quiz'
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=True)  # Which clinic this quiz is for
    referral_doctor = db.Column(db.String(255), nullable=True)  # Referring doctor name
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationship to clinic
    clinic = db.relationship('Clinic', backref='quiz_submissions', lazy=True)

    def __repr__(self):
        return f'<ConversionQuiz {self.id}>'


class VizBrizQuiz(db.Model):
    """Model for VizBriz multilingual sleep quiz submissions"""
    __tablename__ = 'vizbriz_quiz'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=True)
    quiz_input = db.Column(db.Text, nullable=False)  # JSON string of quiz answers
    api_payload = db.Column(db.Text, nullable=True)  # JSON string of payload sent to external API (for backward compatibility)
    language = db.Column(db.String(5), nullable=False, default='en')  # en, ru, he
    total_score = db.Column(db.Integer, nullable=True)
    risk_band = db.Column(db.String(20), nullable=True)  # low, moderate, high
    red_flags = db.Column(db.JSON, nullable=True)  # Array of triggered flags
    outcome_message_id = db.Column(db.String(50), nullable=True)
    clinic_email = db.Column(db.String(120), nullable=True)
    patient_email = db.Column(db.String(120), nullable=False)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=True)
    referral_doctor = db.Column(db.String(255), nullable=True)
    ai_response = db.Column(db.Text, nullable=True)
    quiz_type = db.Column(db.String(50), nullable=False, default='vizbriz_sleep_v1')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationships
    patient = db.relationship('Patient', backref='vizbriz_quizzes', lazy=True, foreign_keys=[user_id])
    clinic = db.relationship('Clinic', backref='vizbriz_submissions', lazy=True)
    
    def __repr__(self):
        return f'<VizBrizQuiz {self.id} - {self.language} - {self.risk_band}>'
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'quiz_input': self.quiz_input,
            'api_payload': self.api_payload,  # Payload sent to external API
            'language': self.language,
            'total_score': self.total_score,
            'risk_band': self.risk_band,
            'red_flags': self.red_flags,
            'outcome_message_id': self.outcome_message_id,
            'clinic_email': self.clinic_email,
            'patient_email': self.patient_email,
            'clinic_id': self.clinic_id,
            'referral_doctor': self.referral_doctor,
            'quiz_type': self.quiz_type,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class PatientObservation(db.Model):
    __tablename__ = 'patient_observations'

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    source_file = db.Column(db.String(255))  # S3 key or document name
    datasource_id = db.Column(db.Integer, db.ForeignKey('datasources.DataSourceID'))
    observation_id = db.Column(db.Integer, db.ForeignKey('observations.ObservationID'))  # optional if mapped
    observation_text = db.Column(db.String(255), nullable=False)
    value = db.Column(db.String(64))
    unit = db.Column(db.String(32))
    evidence = db.Column(db.Text)
    confidence = db.Column(db.Float)  # 0–100
    was_verified = db.Column(db.Boolean, default=False)  # optional flag for manual validation
    provider = db.Column(db.String(50), nullable=True)  # 'openai', 'claude', 'bedrock'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ObservationStore(db.Model):
    __tablename__ = 'observation_store'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, nullable=True)
    quiz_id = db.Column(db.Integer, nullable=True)
    file_name = db.Column(db.String(255), nullable=True)
    source_type = db.Column(db.String(50), nullable=False)  # 'quiz', 'pdf', 'cbct'
    source_text = db.Column(db.Text, nullable=True)
    # Canonical key path for the observation (e.g., 'sleep_study.ahi')
    path = db.Column(db.String(255), nullable=True)
    extracted_observations = db.Column(db.JSON, nullable=True)
    labeled_result = db.Column(db.JSON, nullable=True)  # Optional ground truth
    provider = db.Column(db.String(50), nullable=True)  # 'openai', 'claude', 'bedrock'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    manual_score = db.Column(db.Integer, nullable=True)
    auto_score = db.Column(db.Integer, nullable=True)
    label_match = db.Column(db.Boolean, nullable=True)  # whether AI prediction matched manual rule
    section = db.Column(db.String(100), nullable=True)

    # Extended, all optional (match DB schema; used for multi-value metrics & provenance)
    metric_key = db.Column(db.String(64), nullable=True)
    metric_value_decimal = db.Column(db.Numeric(10, 3), nullable=True)
    metric_unit = db.Column(db.String(32), nullable=True)
    metric_phase = db.Column(db.String(32), nullable=True)  # e.g., REM, NREM, supine

    observed_at = db.Column(db.DateTime, nullable=True)     # sleep study datetime
    mention_date = db.Column(db.DateTime, nullable=True)    # date mentioned in report
    document_date = db.Column(db.DateTime, nullable=True)   # parsed from document text/metadata
    observed_at_source = db.Column(db.Enum('document_text', 'file_metadata', 'inferred', 'upload_time', name='obs_observed_at_source'), nullable=True)

    source_kind = db.Column(db.Enum('sleep_study', 'report', 'questionnaire', 'database_fallback', 'numerical_extraction', name='obs_source_kind'), nullable=True)
    study_type = db.Column(db.Enum('HSAT', 'PSG', 'Titration', 'Unknown', name='obs_study_type'), nullable=True)

    episode_id = db.Column(db.String(64), nullable=True)    # groups values from the same test/file
    facility = db.Column(db.String(255), nullable=True)
    s3_key = db.Column(db.String(1024), nullable=True)
    file_section = db.Column(db.String(64), nullable=True)
    snippet = db.Column(db.Text, nullable=True)

    link_confidence = db.Column(db.Integer, nullable=True)  # 0–100
    link_status = db.Column(db.Enum('linked', 'review', 'rejected', name='obs_link_status'), nullable=True)

    __table_args__ = (
        db.Index('idx_obs_patient_metric', 'patient_id', 'metric_key', 'observed_at'),
        db.Index('idx_obs_episode', 'episode_id'),
        db.Index('idx_obs_observed_at', 'observed_at'),
        db.Index('idx_obs_mention_date', 'mention_date'),
        db.Index('idx_obs_source_kind', 'source_kind'),
        db.Index('idx_obs_document_date', 'document_date'),
        db.Index('idx_obs_path', 'path'),
    )

    def __repr__(self):
        return f'<ObservationStore {self.id}>'

class PageViewLog(db.Model):
    __tablename__ = 'page_view_log'
    id = db.Column(db.Integer, primary_key=True)
    patient_email = db.Column(db.String(120), nullable=True)  # Email if available
    session_id = db.Column(db.String(255), nullable=True)  # Browser session ID
    page_type = db.Column(db.String(50), nullable=False)  # 'stage_a', 'stage_b', 'results_page'
    page_url = db.Column(db.String(500), nullable=True)  # Full URL
    referrer = db.Column(db.String(500), nullable=True)  # Where they came from
    user_agent = db.Column(db.Text, nullable=True)  # Browser/device info
    ip_address = db.Column(db.String(45), nullable=True)  # IP address
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=True)
    utm_source = db.Column(db.String(100), nullable=True)  # Track campaign source
    utm_medium = db.Column(db.String(100), nullable=True)  # Track campaign medium
    utm_campaign = db.Column(db.String(100), nullable=True)  # Track campaign name
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationship to clinic
    clinic = db.relationship('Clinic', backref='page_views', lazy=True)

    def __repr__(self):
        return f'<PageViewLog {self.id}: {self.page_type} - {self.patient_email}>'

class PatientCaseEnvelope(db.Model):
    __tablename__ = 'patient_case_envelope'

    id = db.Column(db.BigInteger, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    report_id = db.Column(db.String(255), nullable=False)
    document_type = db.Column(db.String(32), nullable=True)
    source_uri = db.Column(db.Text, nullable=True)
    case_json = db.Column(db.JSON, nullable=False)
    provider = db.Column(db.String(32), nullable=False, default='system')
    imported_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('patient_id', 'report_id', name='uq_patient_report'),
        db.Index('idx_patient_id', 'patient_id'),
        db.Index('idx_report_id', 'report_id'),
    )

class Level4ReportHistory(db.Model):
    __tablename__ = 'level4_report_history'
    
    id = db.Column(db.BigInteger, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    prompt = db.Column(db.Text, nullable=False)
    response = db.Column(db.Text, nullable=False)
    llm_provider = db.Column(db.String(50), nullable=False)
    model_used = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('dentists.id'), nullable=True)
    
    # Relationships
    patient = db.relationship('Patient', backref='level4_report_history', lazy=True)
    creator = db.relationship('Dentist', backref='level4_reports_created', lazy=True)
    
    __table_args__ = (
        db.Index('idx_level4_patient_id', 'patient_id'),
        db.Index('idx_level4_created_at', 'created_at'),
        db.Index('idx_level4_provider', 'llm_provider'),
    )
    
    def __repr__(self):
        return f'<Level4ReportHistory {self.id}: patient={self.patient_id}, provider={self.llm_provider}, created={self.created_at}>'


class CTAInteractionLog(db.Model):
    __tablename__ = 'cta_interaction_log'
    id = db.Column(db.Integer, primary_key=True)
    patient_email = db.Column(db.String(120), nullable=True)  # Email if available
    session_id = db.Column(db.String(255), nullable=True)  # Browser session ID
    cta_type = db.Column(db.String(50), nullable=False)  # 'schedule_sleep_test', 'complete_advanced_assessment', 'email_link_click', 'phone_click'
    cta_text = db.Column(db.String(255), nullable=True)  # The actual button/link text
    page_type = db.Column(db.String(50), nullable=True)  # Which page the CTA was on
    quiz_type = db.Column(db.String(50), nullable=True)  # 'basic_quiz', 'advanced_quiz'
    quiz_id = db.Column(db.Integer, db.ForeignKey('conversion_quiz.id'), nullable=True)  # Link to quiz if available
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=True)
    user_agent = db.Column(db.Text, nullable=True)  # Browser/device info
    ip_address = db.Column(db.String(45), nullable=True)  # IP address
    referrer = db.Column(db.String(500), nullable=True)  # Page they were on when clicked
    email_source = db.Column(db.String(100), nullable=True)  # If from email: 'doctor_notification', 'patient_follow_up' (NULL if from web)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    quiz = db.relationship('ConversionQuiz', backref='cta_interactions', lazy=True)
    clinic = db.relationship('Clinic', backref='cta_interactions', lazy=True)

    def __repr__(self):
        return f'<CTAInteractionLog {self.id}: {self.cta_type} - {self.patient_email}>'

class ConsultationRequest(db.Model):
    __tablename__ = 'consultation_requests'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(50), nullable=False)
    comment = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default='pending')  # pending, contacted, completed
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=True)
    
    # Relationship to patient if they exist
    patient = db.relationship('Patient', backref='consultation_requests')
    
    def __repr__(self):
        return f'<ConsultationRequest {self.name} - {self.email}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'comment': self.comment,
            'status': self.status,
            'submitted_at': self.submitted_at.isoformat() if self.submitted_at else None,
            'patient_id': self.patient_id
        }

class PatientConsultSchedule(db.Model):
    __tablename__ = 'patient_consult_schedule'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    consult_type = db.Column(db.String(50), nullable=False)  # e.g., 'sleep_doctor', 'dental_expert', 'followup'
    scheduled_datetime = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='scheduled')  # 'scheduled', 'completed', 'cancelled', etc.
    doctor_name = db.Column(db.String(100))  # Name of the doctor for the consultation
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_datetime = db.Column(db.DateTime)  # When the consult was actually completed
    comment = db.Column(db.Text)  # Unique notes about the meeting

    patient = db.relationship('Patient', backref='consult_schedules')

    def __repr__(self):
        return f'<PatientConsultSchedule {self.id} Patient {self.patient_id} Type {self.consult_type}>'

class DentistReportApproval(db.Model):
    __tablename__ = 'dentist_report_approval'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    report_id = db.Column(db.Integer, db.ForeignKey('adminfiles.id'), nullable=False)
    report_file_path = db.Column(db.String(255))  # Optional: path or URL to the report file
    dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'))
    dentist_full_name = db.Column(db.String(100), nullable=False)
    dentist_signature = db.Column(db.Text, nullable=False)  # Store as text, base64, or a hash
    approval_status = db.Column(db.String(20), nullable=False, default='approved')
    approval_timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient = db.relationship('Patient', backref='report_approvals')
    dentist = db.relationship('Dentist', backref='report_approvals')
    report = db.relationship('AdminFile', backref='approvals')

    def __repr__(self):
        return f'<DentistReportApproval {self.id} Patient {self.patient_id} Report {self.report_id}>'

class PatientDeviceOrder(db.Model):
    __tablename__ = 'patient_device_order'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    device_type = db.Column(db.String(50), nullable=False)  # e.g., 'oral_appliance', 'CPAP'
    device_name = db.Column(db.String(100))  # Optional: specific model
    order_date = db.Column(db.DateTime, nullable=False)
    arrival_date = db.Column(db.DateTime)
    status = db.Column(db.String(20), nullable=False, default='ordered')  # 'ordered', 'shipped', 'arrived', etc.
    notes = db.Column(db.Text)
    fitting_date = db.Column(db.DateTime)  # Date of device fitting
    fitting_comment = db.Column(db.Text)   # Notes about device fitting
    
    # Morning aligner fields
    morning_aligner_used = db.Column(db.Boolean, default=False)  # Whether morning aligner was used
    morning_aligner_type = db.Column(db.String(50), nullable=True)  # 'Silicon', 'Prefabricated', etc.
    
    # Device advancement configuration
    advancement = db.Column(db.Numeric(5, 2), nullable=True)  # Starting advancement in mm (e.g., 2.5mm)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient = db.relationship('Patient', backref='device_orders')

    def __repr__(self):
        return f'<PatientDeviceOrder {self.id} Patient {self.patient_id} Device {self.device_type}>'

class PatientManifest(db.Model):
    """Stores patient manifest data for each treatment stage"""
    __tablename__ = 'patient_manifest'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    stage_key = db.Column(db.String(100), nullable=False)  # e.g., 'quiz_completion', 'initial_consult_scheduled'
    stage_number = db.Column(db.Integer, nullable=False)
    stage_name = db.Column(db.String(200), nullable=False)
    
    # Status and completion
    is_completed = db.Column(db.Boolean, default=False)
    completion_date = db.Column(db.DateTime, nullable=True)
    
    # Rich data storage (JSON)
    stage_data = db.Column(db.JSON, nullable=True)  # Store detailed data like quiz results, device info, etc.
    
    # Status message for display
    status_message = db.Column(db.String(500), nullable=True) # e.g., "No consult scheduled", "Completed on Jan 15"
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    patient = db.relationship('Patient', backref='manifest_entries')
    
    # Composite unique constraint
    __table_args__ = (
        db.UniqueConstraint('patient_id', 'stage_key', name='unique_patient_stage'),
    )
    
    def __repr__(self):
        return f'<PatientManifest {self.patient_id}:{self.stage_key}>'


class PatientStageSummaryCache(db.Model):
    """Caches AI-generated stage summaries to avoid repeated Bedrock calls"""
    __tablename__ = 'patient_stage_summary_cache'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False, unique=True)
    
    # Cached AI summary data
    overall_summary = db.Column(db.Text, nullable=True)  # Full AI summary text
    overall_summary_metadata = db.Column(db.JSON, nullable=True)  # Model, timestamp, etc.
    stage_ai_comments = db.Column(db.JSON, nullable=True)  # Dict of stage_key -> ai_comment
    
    # Cache metadata
    stages_snapshot = db.Column(db.JSON, nullable=True)  # Snapshot of stage statuses when cached
    cache_version = db.Column(db.Integer, default=1)  # Increment when cache structure changes
    is_valid = db.Column(db.Boolean, default=True)  # Set to False when stages change
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)  # Optional expiration
    
    # Relationships
    patient = db.relationship('Patient', backref='stage_summary_cache')
    
    def __repr__(self):
        return f'<PatientStageSummaryCache {self.patient_id}: valid={self.is_valid}>'
    
    def is_stale(self, current_stages_status: Dict[str, Any]) -> bool:
        """Check if cache is stale by comparing stage statuses"""
        if not self.is_valid or not self.stages_snapshot:
            return True
        
        # Compare current stage statuses with cached snapshot
        if set(current_stages_status.keys()) != set(self.stages_snapshot.keys()):
            return True
        
        # Quick check: Count completed stages in both snapshots
        current_completed = sum(1 for s in current_stages_status.values() if s.get("status") == "completed")
        cached_completed = sum(1 for s in self.stages_snapshot.values() if s.get("status") == "completed")
        
        if current_completed != cached_completed:
            return True
        
        # Detailed comparison
        for stage_key, current_status in current_stages_status.items():
            cached_status = self.stages_snapshot.get(stage_key, {})
            if current_status.get("status") != cached_status.get("status"):
                return True
            # Also check completion dates
            if current_status.get("completed_on") != cached_status.get("completed_on"):
                return True
        
        return False


class StageFileLink(db.Model):
    """Links patient manifest stages to files for efficient lookup"""
    __tablename__ = 'stage_file_links'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    stage_key = db.Column(db.String(100), nullable=False)  # e.g., 'quiz_completion', 'sleep_test_completed'
    file_id = db.Column(db.Integer, nullable=False)  # ID of the file
    file_table = db.Column(db.String(20), nullable=False)  # Which table the file is in ('files' or 'adminfiles')
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    patient = db.relationship('Patient', backref='stage_file_links')
    
    # Indexes for performance
    __table_args__ = (
        db.Index('idx_patient_stage', 'patient_id', 'stage_key'),
        db.Index('idx_file', 'file_id', 'file_table'),
    )
    
    def __repr__(self):
        return f'<StageFileLink {self.patient_id}:{self.stage_key} -> {self.file_table}:{self.file_id}>'


class EmailLog(db.Model):
    """Log of all emails sent to patients and other parties"""
    __tablename__ = 'email_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('dentists.id'), nullable=True)
    sender_type = db.Column(db.String(50), nullable=False, comment='dentist, admin, system')
    sender_email = db.Column(db.String(255), nullable=False)
    recipient_email = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(500), nullable=False)
    message_content = db.Column(db.Text, nullable=False)
    email_type = db.Column(db.String(100), nullable=False, comment='hipaa_consent, osa_report, follow_up, notification, etc.')
    status = db.Column(db.String(50), nullable=False, default='sent', comment='sent, failed, pending')
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    patient = db.relationship('Patient', backref='email_logs')
    sender = db.relationship('Dentist', backref='sent_emails')
    
    def __repr__(self):
        return f'<EmailLog {self.id}: {self.email_type} to {self.recipient_email}>'
    
    def to_dict(self):
        """Convert email log to dictionary for JSON serialization"""
        return {
            'id': self.id,
            'patient_id': self.patient_id,
            'sender_id': self.sender_id,
            'sender_type': self.sender_type,
            'sender_email': self.sender_email,
            'recipient_email': self.recipient_email,
            'subject': self.subject,
            'message_content': self.message_content,
            'email_type': self.email_type,
            'status': self.status,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

class DentistTreatmentQuiz(db.Model):
    __tablename__ = 'dentist_treatment_quiz'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'), nullable=False)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=True)
    file_id = db.Column(db.Integer, db.ForeignKey('files.id'), nullable=True)  # Link to PDF file
    
    # Quiz data
    quiz_input = db.Column(db.Text, nullable=False)  # JSON string of quiz answers
    language = db.Column(db.String(10), nullable=False, default='en')  # 'en' for English, 'he' for Hebrew
    
    # Metadata
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    status = db.Column(db.String(20), default='draft')  # 'draft', 'completed', 'archived'
    
    # Relationships
    patient = db.relationship('Patient', backref='treatment_quizzes', lazy=True)
    dentist = db.relationship('Dentist', backref='treatment_quizzes', lazy=True)
    clinic = db.relationship('Clinic', backref='treatment_quizzes', lazy=True)
    file = db.relationship('File', backref='dentist_quiz', lazy=True)

    def __repr__(self):
        return f'<DentistTreatmentQuiz {self.id}: Patient {self.patient_id}, Dentist {self.dentist_id}>'


class DentistCourseParticipation(db.Model):
    __tablename__ = 'dentist_course_participation'
    
    id = db.Column(db.Integer, primary_key=True)
    doctor_name = db.Column(db.String(255), nullable=False)
    dso_name = db.Column(db.String(255), nullable=True)
    clinic_name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    phone_number = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(100), nullable=False, default='Dentist')
    session_name = db.Column(db.String(255), nullable=False, default='Dental Sleep Medicine Course')
    session_id = db.Column(db.String(50), nullable=False, default='session_1')
    registration_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<DentistCourseParticipation {self.id}: {self.doctor_name} - {self.session_name}>'
    
    def to_dict(self):
        """Convert model instance to dictionary for JSON serialization"""
        return {
            'id': self.id,
            'doctor_name': self.doctor_name,
            'dso_name': self.dso_name,
            'clinic_name': self.clinic_name,
            'email': self.email,
            'phone_number': self.phone_number,
            'role': self.role,
            'session_name': self.session_name,
            'session_id': self.session_id,
            'registration_date': self.registration_date.isoformat() if self.registration_date else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class LLMInteraction(db.Model):
    """
    Model for tracking all LLM prompt and response interactions.
    Each session has TWO entries: one for prompt, one for response (linked by session_id).
    """
    __tablename__ = 'llm_interactions'
    
    # Primary identification
    id = db.Column(db.BigInteger, primary_key=True)
    session_id = db.Column(db.String(36), nullable=False, index=True, comment='UUID to link prompt with response')
    interaction_type = db.Column(db.Enum('prompt', 'response', name='interaction_type_enum'), 
                                  nullable=False, index=True, 
                                  comment='Whether this is a prompt or response entry')
    
    # Patient & Context
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='SET NULL'), 
                          nullable=True, index=True, 
                          comment='Associated patient ID (NULL for non-patient calls)')
    page_endpoint = db.Column(db.String(255), nullable=True, index=True, 
                             comment='Flask endpoint that triggered the call, e.g., main.patient_workflow_manifest')
    user_id = db.Column(db.Integer, db.ForeignKey('dentists.id', ondelete='SET NULL'), 
                       nullable=True, index=True, 
                       comment='Dentist/user who triggered the call')
    
    # Model Information
    model_name = db.Column(db.String(100), nullable=False, index=True, 
                          comment='Short model name, e.g., claude_4_sonnet')
    model_id = db.Column(db.String(255), nullable=False, 
                        comment='Full Bedrock model ID, e.g., us.anthropic.claude-sonnet-4-20250514-v1:0')
    
    # Content
    content_text = db.Column(db.Text, nullable=False, 
                            comment='The actual prompt or response text')
    content_json = db.Column(db.JSON, nullable=True, 
                            comment='Full structured payload (messages array for prompts, full response for responses)')
    
    # Token Metrics
    token_count = db.Column(db.Integer, nullable=True, 
                           comment='Actual token count if available from API')
    token_count_estimated = db.Column(db.Integer, nullable=True, 
                                     comment='Estimated tokens using tiktoken library')
    
    # Performance Metrics
    response_time_ms = db.Column(db.Integer, nullable=True, 
                                comment='Response time in milliseconds (only for response entries)')
    
    # Request Parameters (for prompts)
    max_tokens = db.Column(db.Integer, nullable=True, comment='Max tokens requested')
    temperature = db.Column(db.Numeric(3, 2), nullable=True, comment='Temperature setting')
    top_p = db.Column(db.Numeric(3, 2), nullable=True, comment='Top-p setting')
    
    # Status & Error Handling
    status = db.Column(db.Enum('success', 'error', 'throttled', 'timeout', name='llm_status_enum'), 
                      default='success', nullable=False, index=True, 
                      comment='Call status')
    error_message = db.Column(db.Text, nullable=True, 
                             comment='Error details if status is not success')
    
    # Timestamps
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, 
                          default=datetime.utcnow, index=True, 
                          comment='When this entry was created')
    
    # Relationships
    patient = db.relationship('Patient', backref='llm_interactions', lazy='select')
    user = db.relationship('Dentist', backref='llm_interactions', lazy='select')
    
    # Composite indexes defined at class level
    __table_args__ = (
        db.Index('idx_patient_created', 'patient_id', 'created_at'),
        {'comment': 'Stores all LLM prompt and response pairs for analytics, debugging, and cost monitoring'}
    )
    
    def __repr__(self):
        return f'<LLMInteraction {self.id}: {self.interaction_type} - {self.model_name} - {self.session_id[:8]}...>'
    
    def to_dict(self):
        """Convert model instance to dictionary for JSON serialization"""
        return {
            'id': self.id,
            'session_id': self.session_id,
            'interaction_type': self.interaction_type,
            'patient_id': self.patient_id,
            'page_endpoint': self.page_endpoint,
            'user_id': self.user_id,
            'model_name': self.model_name,
            'model_id': self.model_id,
            'content_text': self.content_text[:200] + '...' if len(self.content_text) > 200 else self.content_text,
            'token_count': self.token_count,
            'token_count_estimated': self.token_count_estimated,
            'response_time_ms': self.response_time_ms,
            'max_tokens': self.max_tokens,
            'temperature': float(self.temperature) if self.temperature else None,
            'top_p': float(self.top_p) if self.top_p else None,
            'status': self.status,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
    
    @classmethod
    def get_session_pair(cls, session_id):
        """Get both prompt and response for a session"""
        interactions = cls.query.filter_by(session_id=session_id).order_by(cls.interaction_type).all()
        return {
            'prompt': next((i for i in interactions if i.interaction_type == 'prompt'), None),
            'response': next((i for i in interactions if i.interaction_type == 'response'), None)
        }
    
    @classmethod
    def get_patient_history(cls, patient_id, limit=50):
        """Get LLM interaction history for a patient"""
        return cls.query.filter_by(patient_id=patient_id).order_by(cls.created_at.desc()).limit(limit).all()
    
    @classmethod
    def get_recent_errors(cls, hours=24, limit=100):
        """Get recent failed LLM calls"""
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return cls.query.filter(
            cls.status != 'success',
            cls.interaction_type == 'response',
            cls.created_at >= cutoff
        ).order_by(cls.created_at.desc()).limit(limit).all()


class DocumentProcessingQueue(db.Model):
    """
    Queue for managing document processing requests.
    Allows triggering document extraction from UI and tracks processing status.
    """
    __tablename__ = 'document_processing_queue'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    status = db.Column(db.Enum('pending', 'processing', 'completed', 'failed'), default='pending', nullable=False)
    priority = db.Column(db.Integer, default=0)  # Higher number = higher priority
    source = db.Column(db.String(50), default='manual')  # 'manual', 'cron', 'ui', 'api'
    requested_by = db.Column(db.Integer, db.ForeignKey('dentists.id'), nullable=True)
    requested_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    retry_count = db.Column(db.Integer, default=0)
    max_retries = db.Column(db.Integer, default=3)
    batch_size = db.Column(db.Integer, default=3)
    notes = db.Column(db.Text, nullable=True)
    
    # Relationships
    patient = db.relationship('Patient', backref=db.backref('processing_queue', lazy='dynamic'))
    requester = db.relationship('Dentist', backref=db.backref('requested_processing', lazy='dynamic'), foreign_keys=[requested_by])
    
    def __repr__(self):
        return f'<DocumentProcessingQueue {self.id}: Patient {self.patient_id} - {self.status}>'
    
    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'patient_id': self.patient_id,
            'status': self.status,
            'priority': self.priority,
            'source': self.source,
            'requested_by': self.requested_by,
            'requested_at': self.requested_at.isoformat() if self.requested_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'error_message': self.error_message,
            'retry_count': self.retry_count,
            'max_retries': self.max_retries,
            'batch_size': self.batch_size,
            'notes': self.notes
        }
    
    @classmethod
    def get_pending_queue(cls, limit=None):
        """Get pending items ordered by priority and request time"""
        query = cls.query.filter_by(status='pending').order_by(
            cls.priority.desc(),
            cls.requested_at.asc()
        )
        if limit:
            query = query.limit(limit)
        return query.all()
    
    @classmethod
    def get_patient_status(cls, patient_id):
        """Get current queue status for a patient"""
        return cls.query.filter_by(patient_id=patient_id).order_by(cls.requested_at.desc()).first()
    
    @classmethod
    def add_to_queue(cls, patient_id, source='manual', requested_by=None, priority=0, batch_size=3, notes=None):
        """Add a patient to the processing queue"""
        # Check if already in queue
        existing = cls.query.filter_by(
            patient_id=patient_id
        ).filter(
            cls.status.in_(['pending', 'processing'])
        ).first()
        
        if existing:
            return None, f"Patient already in queue with status: {existing.status}"
        
        queue_entry = cls(
            patient_id=patient_id,
            source=source,
            requested_by=requested_by,
            priority=priority,
            batch_size=batch_size,
            notes=notes,
            status='pending'
        )
        
        db.session.add(queue_entry)
        db.session.commit()
        
        return queue_entry, None


class PatientConsent(db.Model):
    """Model for tracking patient consent decisions for third-party information sharing"""
    __tablename__ = 'patient_consent'
    
    # Consent type constants
    CONSENT_TYPE_THIRD_PARTY_SHARING = 'third_party_sharing'  # Sharing with sleep labs, dental labs, etc.
    CONSENT_TYPE_HIPAA = 'hipaa_consent'  # HIPAA authorization
    CONSENT_TYPE_DATA_PROCESSING = 'data_processing'  # General data processing consent
    CONSENT_TYPE_MARKETING = 'marketing'  # Marketing communications
    CONSENT_TYPE_RESEARCH = 'research'  # Research participation

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=True)
    patient_email = db.Column(db.String(120), nullable=False)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=True)
    dso_id = db.Column(db.Integer, db.ForeignKey('dsos.id'), nullable=True)
    
    # Consent details
    consent_given = db.Column(db.Boolean, nullable=False)  # True if patient consented, False if declined
    consent_type = db.Column(db.String(50), nullable=False, default=CONSENT_TYPE_THIRD_PARTY_SHARING)
    consent_version = db.Column(db.String(20), nullable=False, default='v1.0')  # Track consent form version
    
    # Legal and compliance fields
    ip_address = db.Column(db.String(45), nullable=True)  # Store IP for audit trail
    user_agent = db.Column(db.Text, nullable=True)  # Browser info for audit trail
    consent_timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    # Withdrawal tracking
    withdrawn = db.Column(db.Boolean, default=False)
    withdrawal_timestamp = db.Column(db.DateTime, nullable=True)
    withdrawal_reason = db.Column(db.Text, nullable=True)
    
    # Audit fields
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    patient = db.relationship('Patient', backref='consent_records', lazy=True, foreign_keys=[patient_id])
    clinic = db.relationship('Clinic', backref='consent_records', lazy=True)
    dso = db.relationship('DSO', backref='consent_records', lazy=True)

    def __repr__(self):
        status = "Consented" if self.consent_given else "Declined"
        withdrawn = " (Withdrawn)" if self.withdrawn else ""
        return f'<PatientConsent {self.id} - {self.patient_email} - {status}{withdrawn}>'

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            'id': self.id,
            'patient_id': self.patient_id,
            'patient_email': self.patient_email,
            'clinic_id': self.clinic_id,
            'dso_id': self.dso_id,
            'consent_given': self.consent_given,
            'consent_type': self.consent_type,
            'consent_version': self.consent_version,
            'ip_address': self.ip_address,
            'user_agent': self.user_agent,
            'consent_timestamp': self.consent_timestamp.isoformat() if self.consent_timestamp else None,
            'withdrawn': self.withdrawn,
            'withdrawal_timestamp': self.withdrawal_timestamp.isoformat() if self.withdrawal_timestamp else None,
            'withdrawal_reason': self.withdrawal_reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    @classmethod
    def record_consent(cls, patient_email, consent_given, clinic_id=None, dso_id=None, 
                      patient_id=None, ip_address=None, user_agent=None, 
                      consent_type=None, consent_version='v1.0', update_existing=True):
        """
        Record a patient's consent decision.
        If update_existing is True, will update the most recent non-withdrawn consent of the same type.
        Otherwise, creates a new record (maintaining full history).
        
        Args:
            patient_email: Patient's email address
            consent_given: Boolean - True if patient consented, False if declined
            clinic_id: ID of the clinic (optional)
            dso_id: ID of the DSO (optional)
            patient_id: ID of existing patient record (optional)
            ip_address: Patient's IP address for audit trail
            user_agent: Browser user agent for audit trail
            consent_type: Type of consent (defaults to CONSENT_TYPE_THIRD_PARTY_SHARING)
            consent_version: Version of consent form (defaults to 'v1.0')
            update_existing: If True, update existing non-withdrawn consent; if False, always create new record
        
        Returns:
            PatientConsent object or None if error
        """
        try:
            # Default to third-party sharing if not specified
            if consent_type is None:
                consent_type = cls.CONSENT_TYPE_THIRD_PARTY_SHARING
            
            # If update_existing is True, try to find and update existing non-withdrawn consent
            if update_existing:
                existing_consent = cls.query.filter_by(
                    patient_email=patient_email,
                    consent_type=consent_type,
                    withdrawn=False
                ).order_by(cls.created_at.desc()).first()
                
                if existing_consent:
                    # Update existing record
                    existing_consent.consent_given = consent_given
                    existing_consent.consent_version = consent_version
                    existing_consent.ip_address = ip_address
                    existing_consent.user_agent = user_agent
                    existing_consent.updated_at = datetime.utcnow()
                    # Update clinic/dso/patient_id if provided
                    if clinic_id is not None:
                        existing_consent.clinic_id = clinic_id
                    if dso_id is not None:
                        existing_consent.dso_id = dso_id
                    if patient_id is not None:
                        existing_consent.patient_id = patient_id
                    
                    db.session.commit()
                    from flask import current_app
                    current_app.logger.info(f"Updated existing consent record {existing_consent.id} for {patient_email}")
                    return existing_consent
            
            # Create new consent record
            consent_record = cls(
                patient_id=patient_id,
                patient_email=patient_email,
                clinic_id=clinic_id,
                dso_id=dso_id,
                consent_given=consent_given,
                consent_type=consent_type,
                consent_version=consent_version,
                ip_address=ip_address,
                user_agent=user_agent
            )
            
            db.session.add(consent_record)
            db.session.commit()
            
            from flask import current_app
            current_app.logger.info(f"Created new consent record {consent_record.id} for {patient_email}")
            return consent_record
            
        except Exception as e:
            db.session.rollback()
            from flask import current_app
            current_app.logger.error(f"Error recording consent: {str(e)}")
            return None

    @classmethod
    def get_patient_consent(cls, patient_email, consent_type=None):
        """
        Get the latest consent record for a patient
        
        Args:
            patient_email: Patient's email address
            consent_type: Optional - filter by specific consent type
        
        Returns:
            PatientConsent object or None
        """
        query = cls.query.filter_by(patient_email=patient_email)
        
        if consent_type:
            query = query.filter_by(consent_type=consent_type)
        
        return query.order_by(cls.created_at.desc()).first()

    @classmethod
    def withdraw_consent(cls, patient_email, reason=None):
        """
        Withdraw a patient's consent
        
        Args:
            patient_email: Patient's email address
            reason: Optional reason for withdrawal
        
        Returns:
            Boolean - True if successful, False if error
        """
        try:
            consent_record = cls.get_patient_consent(patient_email)
            if consent_record and consent_record.consent_given and not consent_record.withdrawn:
                consent_record.withdrawn = True
                consent_record.withdrawal_timestamp = datetime.utcnow()
                consent_record.withdrawal_reason = reason
                
                db.session.commit()
                return True
            return False
            
        except Exception as e:
            db.session.rollback()
            from flask import current_app
            current_app.logger.error(f"Error withdrawing consent: {str(e)}")
            return False


# ============================================================================
# SQLAlchemy Event Listeners for Cache Invalidation
# ============================================================================
# Automatically invalidate stage summary cache when patient data changes
# that could affect stage completion status

def _invalidate_patient_cache(patient_id: int):
    """Helper function to invalidate cache for a patient
    
    Note: This is called from SQLAlchemy event listeners, so we need to be careful
    about database sessions. We mark the cache as invalid without committing,
    as the commit will happen as part of the main transaction.
    """
    try:
        cache = PatientStageSummaryCache.query.filter_by(patient_id=patient_id).first()
        if cache:
            cache.is_valid = False
            # Don't commit here - let the main transaction handle it
            # The event listener runs within the same transaction
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Error invalidating cache for patient {patient_id}: {e}")


# Listen for changes to PatientObservation (new observations can complete stages)
@event.listens_for(PatientObservation, 'after_insert')
@event.listens_for(PatientObservation, 'after_update')
def invalidate_cache_on_observation(mapper, connection, target):
    """Invalidate cache when patient observations are added or updated"""
    if target.patient_id:
        _invalidate_patient_cache(target.patient_id)


# Listen for changes to PatientCaseEnvelope (new reports can complete stages)
@event.listens_for(PatientCaseEnvelope, 'after_insert')
@event.listens_for(PatientCaseEnvelope, 'after_update')
def invalidate_cache_on_case_envelope(mapper, connection, target):
    """Invalidate cache when patient case envelopes (reports) are added or updated"""
    if target.patient_id:
        _invalidate_patient_cache(target.patient_id)


# Listen for changes to VizBrizQuiz (quiz completion affects stages)
@event.listens_for(VizBrizQuiz, 'after_insert')
@event.listens_for(VizBrizQuiz, 'after_update')
def invalidate_cache_on_quiz(mapper, connection, target):
    """Invalidate cache when quiz submissions are added or updated"""
    if target.user_id:  # user_id is the patient_id for VizBrizQuiz
        _invalidate_patient_cache(target.user_id)


# Listen for changes to DentistTreatmentQuiz (treatment quiz completion)
@event.listens_for(DentistTreatmentQuiz, 'after_insert')
@event.listens_for(DentistTreatmentQuiz, 'after_update')
def invalidate_cache_on_treatment_quiz(mapper, connection, target):
    """Invalidate cache when treatment quiz submissions are added or updated"""
    if target.patient_id:
        _invalidate_patient_cache(target.patient_id)


# Listen for changes to PatientConsultSchedule (consultation scheduling affects stages)
@event.listens_for(PatientConsultSchedule, 'after_insert')
@event.listens_for(PatientConsultSchedule, 'after_update')
@event.listens_for(PatientConsultSchedule, 'after_delete')
def invalidate_cache_on_consult_schedule(mapper, connection, target):
    """Invalidate cache when consultation schedules are added, updated, or deleted"""
    if target.patient_id:
        _invalidate_patient_cache(target.patient_id)


# Listen for changes to PatientManifest (direct stage updates)
@event.listens_for(PatientManifest, 'after_insert')
@event.listens_for(PatientManifest, 'after_update')
@event.listens_for(PatientManifest, 'after_delete')
def invalidate_cache_on_manifest(mapper, connection, target):
    """Invalidate cache when patient manifest (stage) data is modified"""
    if target.patient_id:
        _invalidate_patient_cache(target.patient_id)


# Listen for changes to PatientComment (comments might affect some stages)
@event.listens_for(PatientComment, 'after_insert')
@event.listens_for(PatientComment, 'after_update')
def invalidate_cache_on_comment(mapper, connection, target):
    """Invalidate cache when patient comments are added or updated"""
    if target.patient_id:
        _invalidate_patient_cache(target.patient_id)


# Listen for changes to Patient (direct patient updates)
@event.listens_for(Patient, 'after_update')
def invalidate_cache_on_patient_update(mapper, connection, target):
    """Invalidate cache when patient data is directly updated"""
    if target.id:
        _invalidate_patient_cache(target.id)


# Listen for changes to File (file uploads can complete stages like quiz_completion)
@event.listens_for(File, 'after_insert')
@event.listens_for(File, 'after_update')
def invalidate_cache_on_file_change(mapper, connection, target):
    """Invalidate cache when files are uploaded or updated (e.g., questionnaire files)"""
    if target.patient_id:
        _invalidate_patient_cache(target.patient_id)


# ============================================================================
# Level 4 Device Design Extraction Models
# ============================================================================

class L4DeviceDesign(db.Model):
    """Table A: One row per report (or per device type section if report has multiple devices)"""
    __tablename__ = 'l4_device_design'
    
    id = db.Column(db.Integer, primary_key=True)
    source_report_id = db.Column(db.String(255), nullable=False, comment='Filename of source report')
    patient_id = db.Column(db.String(100), nullable=True, comment='Patient ID / Case ID extracted from report')
    # Clinical context fields (diagnosis and findings that inform device design)
    ahi = db.Column(db.String(50), nullable=True, comment='AHI value and severity (e.g., "10.9 (Mild OSA)")')
    rdi = db.Column(db.String(50), nullable=True, comment='RDI value if provided')
    odi = db.Column(db.String(50), nullable=True, comment='ODI value if provided')
    o2_nadir = db.Column(db.String(50), nullable=True, comment='O2 Nadir percentage')
    snoring_level = db.Column(db.String(100), nullable=True, comment='Snoring level/percentage')
    clinical_background = db.Column(db.Text, nullable=True, comment='Clinical background (e.g., GERD, allergic rhinitis)')
    patient_complaints = db.Column(db.Text, nullable=True, comment='Patient complaints')
    obstruction_sites = db.Column(db.Text, nullable=True, comment='Primary obstruction sites (e.g., velopharynx, tongue base)')
    bite_structure = db.Column(db.Text, nullable=True, comment='Bite and jaw structure observations')
    soft_palate_uvula = db.Column(db.Text, nullable=True, comment='Soft palate and uvula findings')
    tongue_position = db.Column(db.Text, nullable=True, comment='Tongue position observations')
    treatment_considerations = db.Column(db.Text, nullable=True, comment='Treatment considerations that informed device design')
    # Device design fields
    design_context = db.Column(db.String(100), nullable=False, comment='e.g., nighttime_MAD, daytime_TMJ, unknown')
    device_family = db.Column(db.String(255), nullable=True, comment='Device family if explicitly stated')
    mandibular_advancement = db.Column(db.String(255), nullable=True, comment='Mandibular advancement value/description')
    preset_mm = db.Column(db.String(50), nullable=True, comment='Pre-set mandibular advancement in mm')
    vertical_opening = db.Column(db.String(255), nullable=True, comment='Vertical opening value and location')
    anterior_window = db.Column(db.String(100), nullable=True, comment='Anterior window size (Small/Medium/Large)')
    retention_features = db.Column(db.Text, nullable=True, comment='Retention features description')
    material = db.Column(db.String(255), nullable=True, comment='Material type')
    anterior_acrylic = db.Column(db.Text, nullable=True, comment='Anterior acrylic details')
    coverage_notes = db.Column(db.Text, nullable=True, comment='Coverage information')
    clinical_notes = db.Column(db.Text, nullable=True, comment='Clinical notes')
    extraction_confidence = db.Column(db.String(20), nullable=False, default='medium', comment='high/med/low')
    
    # Timestamps
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    device_options = db.relationship('L4DeviceOption', backref='device_design', lazy='dynamic', cascade='all, delete-orphan')
    
    # Unique constraint: one design per report + design context
    __table_args__ = (
        db.UniqueConstraint('source_report_id', 'design_context', name='uq_l4_device_design_report_context'),
    )
    
    def __repr__(self):
        return f'<L4DeviceDesign {self.id}: {self.source_report_id} - {self.design_context}>'
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            'id': self.id,
            'source_report_id': self.source_report_id,
            'patient_id': self.patient_id,
            'design_context': self.design_context,
            'device_family': self.device_family,
            'mandibular_advancement': self.mandibular_advancement,
            'preset_mm': self.preset_mm,
            'vertical_opening': self.vertical_opening,
            'anterior_window': self.anterior_window,
            'retention_features': self.retention_features,
            'material': self.material,
            'anterior_acrylic': self.anterior_acrylic,
            'coverage_notes': self.coverage_notes,
            'clinical_notes': self.clinical_notes,
            'extraction_confidence': self.extraction_confidence,
            'ahi': self.ahi,
            'rdi': self.rdi,
            'odi': self.odi,
            'o2_nadir': self.o2_nadir,
            'snoring_level': self.snoring_level,
            'clinical_background': self.clinical_background,
            'patient_complaints': self.patient_complaints,
            'obstruction_sites': self.obstruction_sites,
            'bite_structure': self.bite_structure,
            'soft_palate_uvula': self.soft_palate_uvula,
            'tongue_position': self.tongue_position,
            'treatment_considerations': self.treatment_considerations,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class L4DeviceOption(db.Model):
    """Table B: Many rows per report (device option list)"""
    __tablename__ = 'l4_device_options'

    id = db.Column(db.Integer, primary_key=True)
    source_report_id = db.Column(db.String(255), nullable=False, comment='Filename of source report')
    design_context = db.Column(db.String(100), nullable=False, comment='Links to device design context')
    device_name = db.Column(db.String(255), nullable=False, comment='Device name')
    device_family = db.Column(db.String(255), nullable=True, comment='Device family if derivable')
    key_features = db.Column(db.Text, nullable=True, comment='Key features if present')

    # Foreign key to device design (optional, for referential integrity)
    device_design_id = db.Column(db.Integer, db.ForeignKey('l4_device_design.id'), nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<L4DeviceOption {self.id}: {self.device_name} ({self.source_report_id})>'

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            'id': self.id,
            'source_report_id': self.source_report_id,
            'design_context': self.design_context,
            'device_name': self.device_name,
            'device_family': self.device_family,
            'key_features': self.key_features,
            'device_design_id': self.device_design_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class Lab(db.Model):
    """Labs (e.g. imaging centers) for referrals. Use several addresses via comma/semicolon in `email`."""
    __tablename__ = 'labs'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=False)  # one or more addresses: a@x.com,b@x.com
    phone = db.Column(db.String(50), nullable=True)
    address = db.Column(db.String(500), nullable=True)
    website = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<Lab {self.id}: {self.name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'phone': self.phone or '',
            'address': self.address or '',
            'website': self.website or '',
        }


class LabReference(db.Model):
    """Audit log of sent lab referrals."""
    __tablename__ = 'lab_references'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'), nullable=False)
    lab_id = db.Column(db.Integer, db.ForeignKey('labs.id'), nullable=False)
    image_types = db.Column(db.String(255), nullable=False)  # e.g. "CBCT,CLINICAL PICTURES,INTRAORAL SCANS"
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    patient = db.relationship('Patient', backref='lab_references')
    dentist = db.relationship('Dentist', backref='lab_references')
    lab = db.relationship('Lab', backref='lab_references')
