from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
from datetime import datetime
import os
import random
from faker import Faker
from sqlalchemy.exc import IntegrityError

# Load environment variables from .env file
load_dotenv()

# Initialize the app and the database
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{os.getenv('DB_USERNAME')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}/{os.getenv('DB_NAME')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Initialize Faker
fake = Faker()

# Define the Dentist model
class Dentist(db.Model):
    __tablename__ = 'Dentists'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    DSO = db.Column(db.String(100))
    status = db.Column(db.String(20))
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20))
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)

# Define the Patient model
class Patient(db.Model):
    __tablename__ = 'Patients'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    dentist_id = db.Column(db.Integer, nullable=False)

# Define the Claims model
class Claim(db.Model):
    __tablename__ = 'claims'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('Patients.id'), nullable=False)  # ForeignKey reference to Patients table
    dentist_id = db.Column(db.Integer, db.ForeignKey('Dentists.id'), nullable=False)  # ForeignKey reference to Dentists table
    insurer = db.Column(db.String(255), nullable=False)
    last_update = db.Column(db.Date, nullable=False)
    treatment_recommendations = db.Column(db.Text)
    status = db.Column(db.String(50), nullable=False)
    claim_amount = db.Column(db.Numeric(10, 2), nullable=False)
    deductible = db.Column(db.Numeric(10, 2))
    __table_args__ = (
        db.CheckConstraint("claim_amount >= 1500 AND claim_amount <= 5000", name="check_claim_amount"),
        db.CheckConstraint("status IN ('New', 'Submitted Pre-Auth', 'Pre-Auth Approved', 'Claim Submitted', 'Claim Approved', 'Received Money')", name="check_status"),
    )

# Function to insert random claims into the Claims table
def insert_claims():
    with app.app_context():
        # Fetch valid patient and dentist IDs
        patient_ids = [patient.id for patient in Patient.query.all()]  # Get valid patient IDs
        dentist_ids = [dentist.id for dentist in Dentist.query.all()]  # Get valid dentist IDs from the Dentists table
        insurers = ['Medicare', 'Blue Cross Blue Shield', 'Cigna', 'Aetna', 'UnitedHealthcare']
        statuses = ['New', 'Submitted Pre-Auth', 'Pre-Auth Approved', 'Claim Submitted', 'Claim Approved', 'Received Money']
        treatment_recommendations = ['MAD', 'TRD', 'Hybrid Device', 'Oral Shield', 'SomnoDent']

        # Check if there are valid dentist and patient IDs available
        if not patient_ids or not dentist_ids:
            print("No patient or dentist records found in the database. Please ensure patients and dentists are added before creating claims.")
            return

        # Insert 200 random claims
        for _ in range(200):
            try:
                claim = Claim(
                    patient_id=random.choice(patient_ids),  # Use only valid patient IDs
                    dentist_id=random.choice(dentist_ids),  # Use only valid dentist IDs
                    insurer=random.choice(insurers),
                    last_update=fake.date_between(start_date="-60d", end_date="today"),  # Dates from 60 days ago to today
                    treatment_recommendations=random.choice(treatment_recommendations),
                    status=random.choice(statuses),
                    claim_amount=round(random.uniform(1500, 5000), 2),
                    deductible=round(random.uniform(100, 1000), 2),
                    )
                db.session.add(claim)

            except IntegrityError as e:
                print(f"Error inserting claim: {e}")
                db.session.rollback()  # Roll back the session if there's an integrity error

        # Commit the changes if no errors
        try:
            db.session.commit()
            print(f"Successfully inserted 200 random claims into the database.")
        except IntegrityError as e:
            db.session.rollback()
            print(f"Failed to commit: {e}")

if __name__ == '__main__':
    with app.app_context():
        # Create tables if they don't exist
        db.create_all()
    
        # Insert random claims into the database
        insert_claims()
