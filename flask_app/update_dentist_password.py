from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash
import os
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{os.getenv('DB_USERNAME')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}/{os.getenv('DB_NAME')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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

def insert_dentist():
    with app.app_context():
        # Create single dentist record
        dentist_data = {
            'name': 'Jenny Cher',
            'DSO': 'Roligo-Wappingers',
            'status': 'Active',
            'email': 'arlene.perugini@roligo-dental.com',
            'password': '5648243431',
            'role': 'Dentist'
        }

        try:
            # Check if dentist already exists
            existing_dentist = Dentist.query.filter_by(email=dentist_data['email']).first()
            if existing_dentist:
                print(f"Dentist with email {dentist_data['email']} already exists.")
                return

            # Create new dentist
            hashed_password = generate_password_hash(dentist_data['password'])
            new_dentist = Dentist(
                name=dentist_data['name'],
                DSO=dentist_data['DSO'],
                status=dentist_data['status'],
                email=dentist_data['email'],
                password=hashed_password,
                role=dentist_data['role']
            )

            # Add and commit to database
            db.session.add(new_dentist)
            db.session.commit()
            print(f"Successfully added dentist: {dentist_data['name']}")

        except Exception as e:
            db.session.rollback()
            print(f"Error adding dentist: {str(e)}")


# Function to update a dentist's password
def update_password(email, new_password):
    with app.app_context():
        try:
            # Find the dentist by email
            dentist = Dentist.query.filter_by(email=email).first()
            if dentist:
                # Hash the new password
                hashed_password = generate_password_hash(new_password)
                
                # Update the password
                dentist.password = hashed_password
                dentist.last_updated = datetime.utcnow()  # Update last_updated field
                
                # Commit the change
                db.session.commit()
                print(f"Password updated successfully for {email}")
            else:
                print(f"No dentist found with email {email}")

        except Exception as e:
            db.session.rollback()
            print(f"Error updating password: {str(e)}")

if __name__ == '__main__':
    # Create tables if they don't exist
    with app.app_context():
        db.create_all()
    
    # Example usage:
    # Insert a new dentist
    insert_dentist()
    
    # Update the password for a dentist or admin by email
    update_password('eranmosss@gmail.com', 'newadminpassword')
