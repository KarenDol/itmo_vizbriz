from flask_login import UserMixin
from datetime import datetime
from flask_app.extensions import db
import random
from werkzeug.security import generate_password_hash, check_password_hash

class Dentist(UserMixin, db.Model):
    __tablename__ = 'dentists'  # Case-sensitive
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    DSO = db.Column(db.String(100))
    status = db.Column(db.String(20))
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='dentist')
    comment = db.Column(db.Text)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)
    country = db.Column(db.String(100), nullable=False)
    patients = db.relationship('Patient', backref='dentist', lazy='dynamic')

    def set_password(self, password):
        self.password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)

class Patient(db.Model):
    __tablename__ = 'patients'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'), nullable=False)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    gender = db.Column(db.String(10))
    insurer = db.Column(db.String(100))
    policy_id = db.Column(db.String(50))
    address = db.Column(db.String(255))
    dob = db.Column(db.Date)
    status = db.Column(db.String(20), default='New')
    create_date = db.Column(db.DateTime, default=datetime.utcnow)
    last_update = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    claim = db.Column(db.String(50))

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

class AdminFile(db.Model):
    __tablename__ = 'adminfiles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    file_type = db.Column(db.String(50))
    file_size = db.Column(db.Integer)
    s3_key = db.Column(db.String(255), nullable=False)
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    patient = db.relationship('Patient', back_populates='admin_files')

class PatientComment(db.Model):
    __tablename__ = 'patientcomments'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'), nullable=False)
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
    ai_response = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f'<ConversionQuiz {self.id}>'
    
    from flask_app.extensions import db
from datetime import datetime

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
    extracted_observations = db.Column(db.JSON, nullable=True)
    labeled_result = db.Column(db.JSON, nullable=True)  # Optional ground truth
    provider = db.Column(db.String(50), nullable=True)  # 'openai', 'claude', 'bedrock'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    manual_score = db.Column(db.Integer, nullable=True)
    auto_score = db.Column(db.Integer, nullable=True)
    label_match = db.Column(db.Boolean, nullable=True)  # whether AI prediction matched manual rule
    section = db.Column(db.String(100), nullable=True)

    def __repr__(self):
        return f'<ObservationStore {self.id}>'from flask_login import UserMixin
from datetime import datetime
from flask_app.extensions import db
import random
from werkzeug.security import generate_password_hash, check_password_hash

class Dentist(UserMixin, db.Model):
    __tablename__ = 'dentists'  # Case-sensitive
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    DSO = db.Column(db.String(100))
    status = db.Column(db.String(20))
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='dentist')
    comment = db.Column(db.Text)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)
    country = db.Column(db.String(100), nullable=False)
    patients = db.relationship('Patient', backref='dentist', lazy='dynamic')

    def set_password(self, password):
        self.password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)

class Patient(db.Model):
    __tablename__ = 'patients'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'), nullable=False)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    gender = db.Column(db.String(10))
    insurer = db.Column(db.String(100))
    policy_id = db.Column(db.String(50))
    address = db.Column(db.String(255))
    dob = db.Column(db.Date)
    status = db.Column(db.String(20), default='New')
    create_date = db.Column(db.DateTime, default=datetime.utcnow)
    last_update = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    claim = db.Column(db.String(50))

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

class AdminFile(db.Model):
    __tablename__ = 'adminfiles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    file_type = db.Column(db.String(50))
    file_size = db.Column(db.Integer)
    s3_key = db.Column(db.String(255), nullable=False)
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    patient = db.relationship('Patient', back_populates='admin_files')

class PatientComment(db.Model):
    __tablename__ = 'patientcomments'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'), nullable=False)
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
    ai_response = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f'<ConversionQuiz {self.id}>'
    
    from flask_app.extensions import db
from datetime import datetime

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
    extracted_observations = db.Column(db.JSON, nullable=True)
    labeled_result = db.Column(db.JSON, nullable=True)  # Optional ground truth
    provider = db.Column(db.String(50), nullable=True)  # 'openai', 'claude', 'bedrock'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    manual_score = db.Column(db.Integer, nullable=True)
    auto_score = db.Column(db.Integer, nullable=True)
    label_match = db.Column(db.Boolean, nullable=True)  # whether AI prediction matched manual rule
    section = db.Column(db.String(100), nullable=True)

    def __repr__(self):
        return f'<ObservationStore {self.id}>'