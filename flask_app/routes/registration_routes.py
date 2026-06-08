from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required
from . import app, db  # Import the existing app and db instances from your package
from .models import Patient, File, AdminFile

@app.route('/register', methods=['GET', 'POST'])
@login_required  # Use this if user authentication is required
def register():
    if request.method == 'POST':
        # Handle form submission logic here
        flash('Form submitted successfully!', 'success')
        return redirect(url_for('register'))  # Adjust as needed for the redirection path
    
    # Render the patient registration wizard template for GET requests
    return render_template('patient_wizard.html')

