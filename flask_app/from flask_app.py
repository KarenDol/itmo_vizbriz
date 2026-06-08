from flask_app.extensions import db
from datetime import datetime

class DataSources(db.Model):
    __tablename__ = 'DataSources'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<DataSources(id={self.id}, name='{self.name}')>"

class Indexes(db.Model):
    __tablename__ = 'indexes'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)

    def __repr__(self):
        return f"<Indexes(id={self.id}, name='{self.name}')>"

class IndexMappings(db.Model):
    __tablename__ = 'IndexMappings'
    id = db.Column(db.Integer, primary_key=True)
    data_source_id = db.Column(db.Integer, db.ForeignKey('DataSources.id'), nullable=False)
    index_id = db.Column(db.Integer, db.ForeignKey('indexes.id'), nullable=False)
    mapping_details = db.Column(db.Text)

    data_source = db.relationship('DataSources', backref=db.backref('index_mappings', lazy=True))
    index_obj = db.relationship('Indexes', backref=db.backref('index_mappings', lazy=True))

    def __repr__(self):
        return f"<IndexMappings(id={self.id}, data_source_id={self.data_source_id}, index_id={self.index_id})>"

class Observations(db.Model):
    __tablename__ = 'Observations'
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.Text, nullable=False)
    origin = db.Column(db.String(255))  # e.g., source origin

    def __repr__(self):
        return f"<Observations(id={self.id})>"

class TreatmentRecommendations(db.Model):
    __tablename__ = 'TreatmentRecommendations'
    id = db.Column(db.Integer, primary_key=True)
    recommendation_text = db.Column(db.Text, nullable=False)
    observation_id = db.Column(db.Integer, db.ForeignKey('Observations.id'))

    observation = db.relationship('Observations', backref=db.backref('treatment_recommendations', lazy=True))

    def __repr__(self):
        return f"<TreatmentRecommendations(id={self.id}, observation_id={self.observation_id})>"

class PatientReports(db.Model):
    __tablename__ = 'PatientReports'
    id = db.Column(db.Integer, primary_key=True)
    patient_name = db.Column(db.String(255))
    report_text = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<PatientReports(id={self.id}, patient_name='{self.patient_name}')>"

class ReportObservations(db.Model):
    __tablename__ = 'ReportObservations'
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('PatientReports.id'), nullable=False)
    observation_id = db.Column(db.Integer, db.ForeignKey('Observations.id'), nullable=False)

    report = db.relationship('PatientReports', backref=db.backref('report_observations', lazy=True))
    observation = db.relationship('Observations', backref=db.backref('report_observations', lazy=True))

    def __repr__(self):
        return f"<ReportObservations(id={self.id}, report_id={self.report_id}, observation_id={self.observation_id})>"
