#!/usr/bin/env python3
"""
Validation test for multi-frame DICOM splitting functionality
"""

import unittest
import json
import tempfile
import os
import numpy as np
from unittest.mock import patch, MagicMock, mock_open
from flask import Flask
from flask_login import LoginManager
import sys
import pydicom
from pydicom.uid import generate_uid

# Add the flask_app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'flask_app'))

from flask_app.routes.file_management_routes import filemgmt
from flask_app.models import db, Patient, Dentist
from flask_app.extensions import db as extensions_db

class TestMultiframeDicomSplit(unittest.TestCase):
    """Test cases for multi-frame DICOM splitting functionality"""
    
    def setUp(self):
        """Set up test environment"""
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.app.config['SECRET_KEY'] = 'test-secret-key'
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        self.app.config['S3_BUCKET_NAME'] = 'test-bucket'
        
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
    
    def create_test_multiframe_dicom(self, temp_dir):
        """Create a test multi-frame DICOM file"""
        # Create a simple multi-frame DICOM dataset
        ds = pydicom.Dataset()
        ds.PatientName = "Test Patient"
        ds.PatientID = "12345"
        ds.StudyInstanceUID = generate_uid()
        ds.SeriesInstanceUID = generate_uid()
        ds.SOPInstanceUID = generate_uid()
        ds.Modality = "CT"
        ds.NumberOfFrames = 3
        
        # Create pixel data (3 frames, 64x64 pixels each)
        pixel_data = np.random.randint(0, 255, (3, 64, 64), dtype=np.uint8)
        ds.PixelData = pixel_data.tobytes()
        ds.Rows = 64
        ds.Columns = 64
        ds.BitsAllocated = 8
        ds.BitsStored = 8
        ds.HighBit = 7
        ds.PixelRepresentation = 0
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        
        # Save to temporary file
        output_path = os.path.join(temp_dir, 'test_multiframe.dcm')
        ds.save_as(output_path)
        return output_path
    
    @patch('flask_login.current_user')
    @patch('boto3.client')
    @patch('tempfile.TemporaryDirectory')
    def test_split_multiframe_dicom_success(self, mock_temp_dir, mock_boto3_client, mock_current_user):
        """Test successful multi-frame DICOM splitting"""
        # Mock authenticated user
        mock_user = MagicMock()
        mock_user.is_authenticated = True
        mock_user.email = 'test@example.com'
        mock_current_user.return_value = mock_user
        
        # Mock S3 client
        mock_s3 = MagicMock()
        mock_boto3_client.return_value = mock_s3
        
        # Mock temporary directory
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_temp_dir.return_value.__enter__.return_value = temp_dir
            
            # Create test multi-frame DICOM file
            test_dicom_path = self.create_test_multiframe_dicom(temp_dir)
            
            # Mock S3 download
            mock_s3.download_file.return_value = None
            
            # Mock S3 upload
            mock_s3.upload_file.return_value = None
            
            # Test the endpoint
            response = self.client.post(
                '/filemgmt/split_multiframe_dicom',
                json={
                    'patient_id': 12345,
                    'source_file': 'patients/12345/imaging/cbct/test_multiframe.dcm'
                },
                content_type='application/json'
            )
            
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.data)
            self.assertTrue(data['success'])
            self.assertIn('Successfully split multi-frame DICOM', data['message'])
            self.assertIn('created_files', data)
            self.assertIn('output_directory', data)
    
    @patch('flask_login.current_user')
    def test_split_multiframe_dicom_missing_parameters(self, mock_current_user):
        """Test multi-frame DICOM splitting with missing parameters"""
        # Mock authenticated user
        mock_user = MagicMock()
        mock_user.is_authenticated = True
        mock_user.email = 'test@example.com'
        mock_current_user.return_value = mock_user
        
        # Test without patient_id
        response = self.client.post(
            '/filemgmt/split_multiframe_dicom',
            json={'source_file': 'test.dcm'},
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertFalse(data['success'])
        self.assertIn('Missing patient_id', data['message'])
        
        # Test without source_file
        response = self.client.post(
            '/filemgmt/split_multiframe_dicom',
            json={'patient_id': 12345},
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertFalse(data['success'])
        self.assertIn('Missing', data['message'])
    
    @patch('flask_login.current_user')
    def test_split_multiframe_dicom_unauthenticated(self, mock_current_user):
        """Test multi-frame DICOM splitting when not authenticated"""
        # Mock unauthenticated user
        mock_user = MagicMock()
        mock_user.is_authenticated = False
        mock_current_user.return_value = mock_user
        
        response = self.client.post(
            '/filemgmt/split_multiframe_dicom',
            json={
                'patient_id': 12345,
                'source_file': 'test.dcm'
            },
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 302)  # Redirect to login
    
    def test_split_multiframe_dicom_no_auth(self):
        """Test multi-frame DICOM splitting without any authentication"""
        response = self.client.post(
            '/filemgmt/split_multiframe_dicom',
            json={
                'patient_id': 12345,
                'source_file': 'test.dcm'
            },
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 302)  # Redirect to login

if __name__ == '__main__':
    unittest.main() 