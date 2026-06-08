from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash
from datetime import datetime

# Assuming the Flask app and db are already set up in your app
from flask_app import create_app, db
from flask_app.models import Dentist

# Create the Flask app context
app = create_app()

# Use the app context to interact with the database
with app.app_context():
    # Create new dentists with the role "dentist"
    dentist_1 = Dentist(
        name="Dr. Alice Smith",
        DSO="Bright Smiles Clinic",
        status="Active",
        email="alice@brightsmiles.com",
        password=generate_password_hash("password1", method='sha256'),  # Hash the password
        role="dentist",
        comment="Experienced in cosmetic dentistry",
        last_updated=datetime.utcnow()
    )
    
    dentist_2 = Dentist(
        name="Dr. Bob Johnson",
        DSO="Healthy Teeth Co.",
        status="Active",
        email="bob@healthyteeth.com",
        password=generate_password_hash("password2", method='sha256'),  # Hash the password
        role="dentist",
        comment="Orthodontics specialist",
        last_updated=datetime.utcnow()
    )

    # Create an admin user with the role "admin"
    admin_user = Dentist(
        name="Dr. Emily Davis",
        DSO="Admin HQ",
        status="Active",
        email="emily@adminhq.com",
        password=generate_password_hash("adminpassword", method='sha256'),  # Hash the password
        role="admin",
        comment="Super Admin",
        last_updated=datetime.utcnow()
    )

    # Add the new users to the session and commit them to the database
    db.session.add(dentist_1)
    db.session.add(dentist_2)
    db.session.add(admin_user)
    
    db.session.commit()
    
    print("Two dentists and one admin created successfully!")
