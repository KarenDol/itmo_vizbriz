# SQLAlchemy Model for DentistCourseParticipation
# Add this class to your flask_app/models.py file

class DentistCourseParticipation(db.Model):
    __tablename__ = 'dentist_course_participation'
    
    id = db.Column(db.Integer, primary_key=True)
    doctor_name = db.Column(db.String(255), nullable=False)
    dso_name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    phone_number = db.Column(db.String(50), nullable=False)
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
            'email': self.email,
            'phone_number': self.phone_number,
            'session_name': self.session_name,
            'session_id': self.session_id,
            'registration_date': self.registration_date.isoformat() if self.registration_date else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
