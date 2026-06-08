from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash
import os
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
# Use the correct RDS host directly
app.config['SQLALCHEMY_DATABASE_URI'] = "mysql+pymysql://admin:Vizbriz2025!@vizbrizapp222.ch8koiygcu36.us-east-2.rds.amazonaws.com:3306/vizbriz"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Print connection details (without password)
print(f"Connecting to database:")
print(f"Host: vizbrizapp222.ch8koiygcu36.us-east-2.rds.amazonaws.com")
print(f"Port: 3306")
print(f"Database: vizbriz")
print(f"Username: admin")

db = SQLAlchemy(app)

class Dentist(db.Model):
    __tablename__ = 'Dentists'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    DSO = db.Column(db.String(100))
    status = db.Column(db.String(20))
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)  # Increased length for hashed password
    role = db.Column(db.String(20))
    comment = db.Column(db.Text)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)
    country = db.Column(db.String(100), nullable=False)

def insert_dentist():
    with app.app_context():
        # Create quiz dentist record
        dentist_data = {
            'name': 'Quiz Dentist',
            'DSO': 'Quiz System',
            'status': 'Active',
            'email': 'quiz@vizbriz.com',
            'password': 'quiz123',  # This is a default password
            'role': 'Dentist',
            'country': 'US'  # Default country
        }

        try:
            # Check if dentist already exists
            existing_dentist = Dentist.query.filter_by(email=dentist_data['email']).first()
            if existing_dentist:
                print(f"Quiz dentist already exists with ID: {existing_dentist.id}")
                return existing_dentist.id

            # Create new dentist
            hashed_password = generate_password_hash(dentist_data['password'])
            new_dentist = Dentist(
                name=dentist_data['name'],
                DSO=dentist_data['DSO'],
                status=dentist_data['status'],
                email=dentist_data['email'],
                password=hashed_password,
                role=dentist_data['role'],
                country=dentist_data['country']
            )

            # Add and commit to database
            db.session.add(new_dentist)
            db.session.commit()
            print(f"Successfully added quiz dentist with ID: {new_dentist.id}")
            return new_dentist.id

        except Exception as e:
            db.session.rollback()
            print(f"Error adding quiz dentist: {str(e)}")
            return None

if __name__ == '__main__':
    # Create tables if they don't exist
    with app.app_context():
        db.create_all()
    
    # Insert dentist
    insert_dentist()