import json
from flask_testing import TestCase

class TestQuizRoutes(TestCase):
    def test_analyze_quiz_with_clinic_email(self):
        """Test that analyze_quiz properly handles clinic_email from request data"""
        test_data = {
            'patient_email': 'test@example.com',
            'clinic_email': 'clinic@example.com',
            'answers': {
                'full_name': 'Test User',
                'phone': '123-456-7890',
                'address': '123 Test St',
                'dob': '1990-01-01',
                'gender': 'Male',
                'doctor_referral': 'Dr. Smith',
                'snoring': 'yes',
                'snoring_details': 'Loud snoring',
                'daytime_sleepiness': 'yes',
                'witnessed_apneas': 'no',
                'diagnosed': 'no',
                'using_treatment': 'no',
                'weight': 'yes',
                'bruxism': 'no'
            }
        }
        
        response = self.client.post('/analyze_quiz', 
                                  json=test_data,
                                  content_type='application/json')
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        
        # Verify that clinic_email was used in the response
        self.assertIn('doctor_email_content', data)
        self.assertIn('clinic@example.com', data['doctor_email_content']) 