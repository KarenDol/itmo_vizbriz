import os
import tempfile
import pytest
from unittest.mock import patch
from flask import Flask
from flask_app import create_app
from flask_app.models import db, Patient, File

@pytest.fixture
def client():
    db_fd, db_path = tempfile.mkstemp()
    app = create_app()
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['WTF_CSRF_ENABLED'] = False
    with app.test_client() as client:
        with app.app_context():
            db.create_all()
        yield client
    os.close(db_fd)
    os.unlink(db_path)

def post_personal_info(client, email, first_name='John', last_name='Doe', files=None):
    data = {
        'first_name': first_name,
        'middle_name': '',
        'last_name': last_name,
        'email': email,
        'phone': '1234567890',
        'dob': '1990-01-01',
        'gender': 'M',
        'address': '123 Main St',
        'doctor_name': 'Dr. Smith',
    }
    if files:
        return client.post('/wizard/stage1_personal_info', data={**data, **files}, content_type='multipart/form-data', follow_redirects=True)
    return client.post('/wizard/stage1_personal_info', data=data, follow_redirects=True)

@patch('flask_app.routes.wizard_routes.s3_client.upload_fileobj')
def test_new_patient_creation(mock_s3, client):
    email = 'newpatient@example.com'
    rv = post_personal_info(client, email)
    assert b'Personal information submitted successfully!' in rv.data
    patient = Patient.query.filter_by(email=email).first()
    assert patient is not None
    assert 'John' in patient.name

@patch('flask_app.routes.wizard_routes.s3_client.upload_fileobj')
def test_update_existing_patient_no_files(mock_s3, client):
    email = 'existing@example.com'
    # Create patient first
    post_personal_info(client, email, first_name='Alice')
    # Update patient info
    rv = post_personal_info(client, email, first_name='Alicia')
    assert b'Personal information submitted successfully!' in rv.data
    patient = Patient.query.filter_by(email=email).first()
    assert patient is not None
    assert 'Alicia' in patient.name
    # Ensure only one patient with this email
    assert Patient.query.filter_by(email=email).count() == 1

@patch('flask_app.routes.wizard_routes.s3_client.upload_fileobj')
def test_update_existing_patient_with_files(mock_s3, client):
    email = 'filepatient@example.com'
    # Create patient and add a file
    post_personal_info(client, email, first_name='Filey')
    patient = Patient.query.filter_by(email=email).first()
    file = File(name='testfile.pdf', patient_id=patient.id, file_type='application/pdf', file_size=123, s3_key='dummy/path', category='billing', subcategory='billing')
    db.session.add(file)
    db.session.commit()
    # Update patient info and add another file
    dummy_file = (tempfile.NamedTemporaryFile(delete=False), 'newfile.pdf')
    with open(dummy_file[0].name, 'wb') as f:
        f.write(b'dummy data')
    with open(dummy_file[0].name, 'rb') as f:
        files = {'driver_license_front': (f, dummy_file[1])}
        rv = post_personal_info(client, email, first_name='FileUpdated', files=files)
    assert b'Personal information submitted successfully!' in rv.data
    patient = Patient.query.filter_by(email=email).first()
    assert patient is not None
    assert 'FileUpdated' in patient.name
    # Ensure only one patient with this email
    assert Patient.query.filter_by(email=email).count() == 1
    # Ensure both files are present
    files = File.query.filter_by(patient_id=patient.id).all()
    assert len(files) >= 2 