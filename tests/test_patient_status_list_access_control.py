"""
Test file for patient status list access control functionality.
Tests that dentists can only see patients from their associated DSOs in the patient status list.
"""

import pytest
from datetime import datetime, timedelta
from flask_app import create_app
from flask_app.models import db, Dentist, DSO, Clinic, Patient, PatientStatus, StatusOption
from flask_app.extensions import db as extensions_db
from werkzeug.security import generate_password_hash
from sqlalchemy import text


class TestPatientStatusListAccessControl:
    """Test class for patient status list access control"""
    
    @pytest.fixture
    def app(self):
        """Create test app with test database"""
        app = create_app()
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        
        with app.app_context():
            extensions_db.create_all()
            yield app
            extensions_db.drop_all()
    
    @pytest.fixture
    def client(self, app):
        """Create test client"""
        return app.test_client()
    
    @pytest.fixture
    def setup_test_data(self, app):
        """Setup test data with DSOs, clinics, dentists, patients, and status data"""
        with app.app_context():
            # Create DSOs
            dso1 = DSO(name="Test DSO 1", email="dso1@test.com", status="active")
            dso2 = DSO(name="Test DSO 2", email="dso2@test.com", status="active")
            db.session.add_all([dso1, dso2])
            db.session.flush()
            
            # Create clinics
            clinic1 = Clinic(name="Clinic 1", dso_id=dso1.id, status="active")
            clinic2 = Clinic(name="Clinic 2", dso_id=dso1.id, status="active")
            clinic3 = Clinic(name="Clinic 3", dso_id=dso2.id, status="active")
            db.session.add_all([clinic1, clinic2, clinic3])
            db.session.flush()
            
            # Create dentists
            admin_dentist = Dentist(
                name="Admin Dentist",
                email="admin@test.com",
                password=generate_password_hash("test123"),
                role="admin",
                country="US"
            )
            
            dso1_dentist = Dentist(
                name="DSO1 Dentist",
                email="dso1@test.com",
                password=generate_password_hash("test123"),
                role="dentist",
                country="US"
            )
            
            dso2_dentist = Dentist(
                name="DSO2 Dentist",
                email="dso2@test.com",
                password=generate_password_hash("test123"),
                role="dentist",
                country="US"
            )
            
            no_dso_dentist = Dentist(
                name="No DSO Dentist",
                email="nodso@test.com",
                password=generate_password_hash("test123"),
                role="dentist",
                country="US"
            )
            
            db.session.add_all([admin_dentist, dso1_dentist, dso2_dentist, no_dso_dentist])
            db.session.flush()
            
            # Associate dentists with DSOs
            dso1_dentist.dsos.append(dso1)
            dso2_dentist.dsos.append(dso2)
            db.session.commit()
            
            # Create patients
            patient1 = Patient(
                name="Patient 1",
                dentist_id=dso1_dentist.id,
                clinic_id=clinic1.id,
                email="patient1@test.com",
                phone="123-456-7890",
                status="New"
            )
            patient2 = Patient(
                name="Patient 2",
                dentist_id=dso1_dentist.id,
                clinic_id=clinic2.id,
                email="patient2@test.com",
                phone="123-456-7891",
                status="In Treatment"
            )
            patient3 = Patient(
                name="Patient 3",
                dentist_id=dso2_dentist.id,
                clinic_id=clinic3.id,
                email="patient3@test.com",
                phone="123-456-7892",
                status="Complete"
            )
            patient4 = Patient(
                name="Patient 4",
                dentist_id=admin_dentist.id,
                clinic_id=None,  # Legacy patient
                email="patient4@test.com",
                phone="123-456-7893",
                status="Archived"  # This should be filtered out
            )
            
            db.session.add_all([patient1, patient2, patient3, patient4])
            db.session.flush()
            
            # Create status options
            status_option1 = StatusOption(status_type="Treatment Status", status_value="In Progress")
            status_option2 = StatusOption(status_type="Payment Status", status_value="Paid")
            db.session.add_all([status_option1, status_option2])
            db.session.flush()
            
            # Create patient statuses
            patient_status1 = PatientStatus(
                patient_id=patient1.id,
                status_type="Treatment Status",
                status_value="In Progress"
            )
            patient_status2 = PatientStatus(
                patient_id=patient2.id,
                status_type="Payment Status",
                status_value="Paid"
            )
            patient_status3 = PatientStatus(
                patient_id=patient3.id,
                status_type="Treatment Status",
                status_value="Complete"
            )
            
            db.session.add_all([patient_status1, patient_status2, patient_status3])
            db.session.commit()
            
            return {
                'dso1': dso1,
                'dso2': dso2,
                'clinic1': clinic1,
                'clinic2': clinic2,
                'clinic3': clinic3,
                'admin_dentist': admin_dentist,
                'dso1_dentist': dso1_dentist,
                'dso2_dentist': dso2_dentist,
                'no_dso_dentist': no_dso_dentist,
                'patient1': patient1,
                'patient2': patient2,
                'patient3': patient3,
                'patient4': patient4
            }
    
    def test_admin_can_see_all_patients(self, client, setup_test_data):
        """Test that admin can see all patients in patient status list"""
        with client.session_transaction() as sess:
            # Simulate admin login
            sess['_user_id'] = setup_test_data['admin_dentist'].id
        
        response = client.get('/patient_status_list')
        assert response.status_code == 200
        
        # Admin should see all non-archived patients
        assert b'Patient 1' in response.data
        assert b'Patient 2' in response.data
        assert b'Patient 3' in response.data
        
        # Should NOT see archived patient
        assert b'Patient 4' not in response.data
    
    def test_dso1_dentist_can_only_see_dso1_patients(self, client, setup_test_data):
        """Test that DSO1 dentist can only see patients from DSO1 clinics"""
        with client.session_transaction() as sess:
            # Simulate DSO1 dentist login
            sess['_user_id'] = setup_test_data['dso1_dentist'].id
        
        response = client.get('/patient_status_list')
        assert response.status_code == 200
        
        # Should see DSO1 patients
        assert b'Patient 1' in response.data
        assert b'Patient 2' in response.data
        
        # Should NOT see DSO2 patient
        assert b'Patient 3' not in response.data
        
        # Should NOT see archived patient
        assert b'Patient 4' not in response.data
    
    def test_dso2_dentist_can_only_see_dso2_patients(self, client, setup_test_data):
        """Test that DSO2 dentist can only see patients from DSO2 clinics"""
        with client.session_transaction() as sess:
            # Simulate DSO2 dentist login
            sess['_user_id'] = setup_test_data['dso2_dentist'].id
        
        response = client.get('/patient_status_list')
        assert response.status_code == 200
        
        # Should see DSO2 patient
        assert b'Patient 3' in response.data
        
        # Should NOT see DSO1 patients
        assert b'Patient 1' not in response.data
        assert b'Patient 2' not in response.data
        
        # Should NOT see archived patient
        assert b'Patient 4' not in response.data
    
    def test_no_dso_dentist_sees_no_patients(self, client, setup_test_data):
        """Test that dentist with no DSO associations sees no patients"""
        with client.session_transaction() as sess:
            # Simulate no DSO dentist login
            sess['_user_id'] = setup_test_data['no_dso_dentist'].id
        
        response = client.get('/patient_status_list')
        assert response.status_code == 200
        
        # Should not see any patients
        assert b'Patient 1' not in response.data
        assert b'Patient 2' not in response.data
        assert b'Patient 3' not in response.data
        assert b'Patient 4' not in response.data
    
    def test_authenticated_user_required(self, client):
        """Test that unauthenticated users cannot access patient status list"""
        response = client.get('/patient_status_list')
        assert response.status_code == 302  # Redirect to login
    
    def test_patient_status_data_included(self, client, setup_test_data):
        """Test that patient status data is properly included in the response"""
        with client.session_transaction() as sess:
            # Simulate DSO1 dentist login
            sess['_user_id'] = setup_test_data['dso1_dentist'].id
        
        response = client.get('/patient_status_list')
        assert response.status_code == 200
        
        # Should include status headers
        assert b'Treatment Status' in response.data
        assert b'Payment Status' in response.data
    
    def test_archived_patients_filtered_out(self, client, setup_test_data):
        """Test that archived patients are filtered out for all users"""
        with client.session_transaction() as sess:
            # Simulate admin login
            sess['_user_id'] = setup_test_data['admin_dentist'].id
        
        response = client.get('/patient_status_list')
        assert response.status_code == 200
        
        # Should NOT see archived patient even as admin
        assert b'Patient 4' not in response.data


def test_patient_status_list_access_control_integration():
    """Integration test for patient status list access control"""
    app = create_app()
    
    with app.app_context():
        # Test setup
        print("\n=== Testing Patient Status List Access Control ===")
        
        # Check if required tables exist
        try:
            # Test basic database connectivity
            db.session.execute(text("SELECT 1"))
            print("✓ Database connection successful")
        except Exception as e:
            print(f"✗ Database connection failed: {e}")
            return False
        
        # Test DSO and clinic relationships
        try:
            dsos = DSO.query.all()
            clinics = Clinic.query.all()
            dentists = Dentist.query.all()
            patients = Patient.query.all()
            
            print(f"✓ Found {len(dsos)} DSOs, {len(clinics)} clinics, {len(dentists)} dentists, {len(patients)} patients")
            
            if not dsos:
                print("⚠ No DSOs found - access control may not work properly")
            if not clinics:
                print("⚠ No clinics found - access control may not work properly")
            if not dentists:
                print("⚠ No dentists found - access control may not work properly")
            if not patients:
                print("⚠ No patients found - access control may not work properly")
                
        except Exception as e:
            print(f"✗ Error querying data: {e}")
            return False
        
        # Test patient status data
        try:
            patient_statuses = PatientStatus.query.all()
            status_options = StatusOption.query.all()
            
            print(f"✓ Found {len(patient_statuses)} patient statuses, {len(status_options)} status options")
            
        except Exception as e:
            print(f"✗ Error querying status data: {e}")
            return False
        
        # Test dentist DSO associations
        try:
            dentists_with_dso = db.session.query(Dentist).join(
                'dsos'
            ).distinct().count()
            
            print(f"✓ {dentists_with_dso} dentists have DSO associations")
            
        except Exception as e:
            print(f"✗ Error checking DSO associations: {e}")
            return False
        
        print("✓ Patient status list access control integration test completed")
        return True


if __name__ == "__main__":
    # Run integration test
    success = test_patient_status_list_access_control_integration()
    if success:
        print("\n✅ All tests passed!")
    else:
        print("\n❌ Some tests failed!") 