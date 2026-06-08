import os
import sqlite3
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import uuid
import os
import sqlite3
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
#LCHEMY_DATABASE_URI'] = f"mysql+pymysql://{os.getenv('DB_USERNAME')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}/{os.getenv('DB_NAME')}"
app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{os.getenv('DB_USERNAME', 'root')}:{os.getenv('DB_PASSWORD', 'new_password')}@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '3307')}/{os.getenv('DB_NAME', 'vizbriz')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Existing Patient Model
class Patient(db.Model):
    __tablename__ = 'patients'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=True)
    telephone = db.Column(db.String(50), nullable=True)
    upload_token = db.Column(db.String(255), nullable=True)  # Add this field

@app.cli.command('populate_upload_token')
def populate_upload_token():
    """
    Populate the `upload_token` field for all existing patients.
    Run this with: flask populate_upload_token
    """
    patients = Patient.query.all()
    for patient in patients:
        if not patient.upload_token:  # Only populate if the token is missing
            patient.upload_token = str(uuid.uuid4())
    db.session.commit()
    print("Upload tokens have been populated for all patients.")

if __name__ == '__main__':
    app.run(debug=True)

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
            'name': 'Demo User ',
            'DSO': 'Sharkbiit',
            'status': 'Active',
            'email': 'demo@sharkbiit.com',
            'password': '0548887097',
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

if __name__ == '__main__':
    # Create tables if they don't exist
    with app.app_context():
        db.create_all()
    
    # Insert dentist
    insert_dentist()

# Get the directory of the current script
script_dir = os.path.dirname(os.path.abspath(__file__))

# Construct the path to the database
db_path = os.path.join(script_dir, 'dentists_data.db')

# Connect to the database (this will create it if it doesn't exist)
conn = sqlite3.connect(db_path)
cursor = conn.cursor()







# Create your table
cursor.execute('''
CREATE TABLE IF NOT EXISTS Dentists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    DSO TEXT,
    status TEXT,
    email TEXT,
    password TEXT,
    role TEXT,
    comment TEXT,
    last_updated DATE
)
''')

conn.commit()
conn.close()

print(f"Database created/connected at: {db_path}")