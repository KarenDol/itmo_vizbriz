#!/usr/bin/env python3
"""
Validation test for CBCT upload functionality
"""

import unittest
import json
import tempfile
import os
from unittest.mock import patch, MagicMock
from flask import Flask
from flask_login import LoginManager
import sys
import os

# Add the flask_app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'flask_app'))

from flask_app.routes.file_management_routes import filemgmt
from flask_app.models import db, Patient, Dentist
from flask_app.extensions import db as extensions_db

class TestCBCTUpload(unittest.TestCase):
    """Test cases for CBCT upload functionality"""
    
    def setUp(self):
        """Set up test environment"""
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.app.config['SECRET_KEY'] = 'test-secret-key'
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        
        # Initialize extensions
        extensions_db.init_app(self.app)
        
        # Initialize login manager
        self.login_manager = LoginManager()
        self.login_manager.init_app(self.app)
        self.login_manager.login_view = 'auth.login'
        
        # Register blueprint
        self.app.register_blueprint(filemgmt, url_prefix='/filemgmt')
        
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()
        
        # Create database tables
        extensions_db.create_all()
        
        # Create test data
        self.create_test_data()
    
    def tearDown(self):
        """Clean up after tests"""
        extensions_db.session.remove()
        extensions_db.drop_all()
        self.app_context.pop()
    
    def create_test_data(self):
        """Create test patient and dentist data"""
        # Create test dentist
        dentist = Dentist(
            email='test@example.com',
            password_hash='test_hash',
            name='Test Dentist',
            role='dentist'
        )
        extensions_db.session.add(dentist)
        extensions_db.session.commit()
        
        # Create test patient
        patient = Patient(
            id=12345,
            first_name='Test',
            last_name='Patient',
            dentist_id=dentist.id,
            email='patient@example.com'
        )
        extensions_db.session.add(patient)
        extensions_db.session.commit()
    
    @patch('flask_login.current_user')
    def test_trigger_cbct_page_authenticated(self, mock_current_user):
        """Test that the CBCT trigger page is accessible when authenticated"""
        # Mock authenticated user
        mock_user = MagicMock()
        mock_user.is_authenticated = True
        mock_current_user.return_value = mock_user
        
        response = self.client.get('/filemgmt/trigger_cbct_page')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'CBCT Upload to Orthanc', response.data)
    
    def test_trigger_cbct_page_unauthenticated(self):
        """Test that the CBCT trigger page redirects when not authenticated"""
        response = self.client.get('/filemgmt/trigger_cbct_page')
        self.assertEqual(response.status_code, 302)  # Redirect to login
    
    @patch('flask_login.current_user')
    @patch('boto3.client')
    @patch('requests.post')
    def test_process_cbct_success(self, mock_requests_post, mock_boto3_client, mock_current_user):
        """Test successful CBCT processing"""
        # Mock authenticated user
        mock_user = MagicMock()
        mock_user.is_authenticated = True
        mock_user.email = 'test@example.com'
        mock_current_user.return_value = mock_user
        
        # Mock S3 client
        mock_s3 = MagicMock()
        mock_boto3_client.return_value = mock_s3
        
        # Mock S3 list_objects_v2 response
        mock_s3.list_objects_v2.return_value = {
            'Contents': [
                {'Key': 'patients/12345/imaging/cbct/test1.dcm'},
                {'Key': 'patients/12345/imaging/cbct/test2.dcm'}
            ]
        }
        
        # Mock S3 get_object response
        mock_s3.get_object.return_value = {
            'Body': MagicMock()
        }
        mock_s3.get_object.return_value['Body'].read.return_value = b'dicom_data'
        
        # Mock requests.post response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_requests_post.return_value = mock_response
        
        # Test the endpoint
        response = self.client.post(
            '/filemgmt/process_cbct_for_orthanc',
            json={'patient_id': 12345},
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertIn('Processed', data['message'])
    
    @patch('flask_login.current_user')
    def test_process_cbct_missing_patient_id(self, mock_current_user):
        """Test CBCT processing with missing patient ID"""
        # Mock authenticated user
        mock_user = MagicMock()
        mock_user.is_authenticated = True
        mock_user.email = 'test@example.com'
        mock_current_user.return_value = mock_user
        
        # Test without patient_id
        response = self.client.post(
            '/filemgmt/process_cbct_for_orthanc',
            json={},
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertFalse(data['success'])
        self.assertIn('Missing patient_id', data['message'])
    
    @patch('flask_login.current_user')
    def test_process_cbct_unauthenticated(self, mock_current_user):
        """Test CBCT processing when not authenticated"""
        # Mock unauthenticated user
        mock_user = MagicMock()
        mock_user.is_authenticated = False
        mock_current_user.return_value = mock_user
        
        response = self.client.post(
            '/filemgmt/process_cbct_for_orthanc',
            json={'patient_id': 12345},
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 302)  # Redirect to login
    
    def test_process_cbct_no_auth(self):
        """Test CBCT processing without any authentication"""
        response = self.client.post(
            '/filemgmt/process_cbct_for_orthanc',
            json={'patient_id': 12345},
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 302)  # Redirect to login

if __name__ == '__main__':
    unittest.main() 