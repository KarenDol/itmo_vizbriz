# Simple Database Extension for Patient Comments and Titration
# Add these fields to your existing PatientComment model in models.py

from flask_app.extensions import db
from datetime import datetime

# Simple extension to existing PatientComment model
# Just add these fields to the existing PatientComment class:

class PatientCommentExtended(db.Model):
    """Extended version of PatientComment with additional fields for titration and other types"""
    __tablename__ = 'patientcomments'  # Keep the same table name
    
    # Existing fields (keep these as they are)
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    dentist_id = db.Column(db.Integer, db.ForeignKey('dentists.id'), nullable=False)
    
    # NEW FIELDS - Add these to your existing PatientComment model:
    comment_type = db.Column(db.String(50), nullable=True, default='general')  # 'titration', 'consultation', 'delivery', 'initial', 'general'
    numeric_value = db.Column(db.Numeric(10, 2), nullable=True)  # For titration settings, ratings, etc.
    numeric_unit = db.Column(db.String(20), nullable=True)  # 'mm', 'rating', 'hours', etc.
    is_urgent = db.Column(db.Boolean, default=False)
    is_internal = db.Column(db.Boolean, default=False)
    
    # Relationships (keep existing)
    dentist = db.relationship('Dentist', backref='comments')
    
    def __repr__(self):
        return f'<PatientComment {self.id}: {self.comment_type} - {self.created_date}>'

# Migration script to add the new columns to existing table
def add_comment_extension_columns():
    """Add new columns to existing patientcomments table"""
    from flask_app.extensions import db
    
    # Add new columns to existing table
    db.engine.execute("""
        ALTER TABLE patientcomments 
        ADD COLUMN comment_type VARCHAR(50) DEFAULT 'general',
        ADD COLUMN numeric_value DECIMAL(10,2) NULL,
        ADD COLUMN numeric_unit VARCHAR(20) NULL,
        ADD COLUMN is_urgent BOOLEAN DEFAULT FALSE,
        ADD COLUMN is_internal BOOLEAN DEFAULT FALSE
    """)
    
    print("Successfully added extension columns to patientcomments table!")

# Example usage:
"""
# Add a titration comment with numeric value
comment = PatientComment(
    patient_id=123,
    dentist_id=456,
    content="Patient reports good comfort with current settings",
    comment_type="titration",
    numeric_value=2.5,
    numeric_unit="mm",
    is_urgent=False,
    is_internal=False
)

# Add a consultation comment
comment = PatientComment(
    patient_id=123,
    dentist_id=456,
    content="Initial consultation completed, patient shows good understanding",
    comment_type="consultation",
    is_urgent=False,
    is_internal=False
)

# Add a delivery comment
comment = PatientComment(
    patient_id=123,
    dentist_id=456,
    content="Device delivered and fitted successfully",
    comment_type="delivery",
    numeric_value=8,
    numeric_unit="rating",
    is_urgent=False,
    is_internal=False
)
"""
