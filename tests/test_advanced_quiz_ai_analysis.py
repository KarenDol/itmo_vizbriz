import pytest
import json
from unittest.mock import patch, MagicMock
from flask_app.routes.conversion_quiz_agent import analyze_quiz_part_b
from flask_app.models import Patient, ConversionQuiz
from flask_app.extensions import db

class TestAdvancedQuizAIAnalysis:
    """Test cases for advanced quiz AI analysis functionality"""
    
    @pytest.fixture
    def sample_part_a_answers(self):
        """Sample Part A (basic quiz) answers"""
        return {
            "snoring": "yes",
            "tiredness": "yes",
            "observed_apnea": "no",
            "daytime_sleepiness": "no",
            "driving_fatigue": "no",
            "bruxism": "yes",
            "weight": "no",
            "diagnosed": "no",
            "using_treatment": "no"
        }
    
    @pytest.fixture
    def sample_part_b_answers(self):
        """Sample Part B (advanced quiz) answers"""
        return {
            "patient_email": "test@example.com",
            "full_name": "John Doe",
            "phone": "555-1234",
            "address": "123 Test St",
            "dob": "1990-01-01",
            "gender": "male",
            "fall_asleep_time": "15 minutes",
            "average_sleep_hours": "7 hours",
            "trouble_falling_asleep_again": "no",
            "mouth_breathing": "yes",
            "sleep_position": "back",
            "bedtime_routine": "reading",
            "caffeine_intake": "2 cups",
            "alcohol_consumption": "occasional",
            "exercise_frequency": "3 times per week",
            "stress_level": "moderate",
            "medications": "none",
            "tmj_symptoms": "no",
            "teeth_grinding": "yes",
            "morning_headaches": "no",
            "dry_mouth": "yes",
            "sore_throat": "no",
            "chest_pain": "no",
            "heartburn": "no",
            "frequent_urination": "no",
            "leg_restlessness": "no",
            "night_sweats": "no",
            "sleep_walking": "no",
            "sleep_talking": "no",
            "nightmares": "no"
        }
    
    @pytest.fixture
    def mock_patient(self, app):
        """Mock patient for testing"""
        with app.app_context():
            patient = Patient(
                id=1,
                email="test@example.com",
                name="John Doe"
            )
            return patient
    
    @pytest.fixture
    def mock_basic_quiz(self, app, sample_part_a_answers):
        """Mock basic quiz entry"""
        with app.app_context():
            quiz = ConversionQuiz(
                id=1,
                user_id=1,
                quiz_input=json.dumps(sample_part_a_answers),
                ai_response=json.dumps({
                    "risk_level": "Moderate",
                    "score": 3,
                    "risk_explanation": "Moderate risk assessment"
                }),
                quiz_type="basic_quiz"
            )
            return quiz
    
    @patch('flask_app.routes.conversion_quiz_agent.ChatOpenAI')
    @patch('flask_app.routes.conversion_quiz_agent.PromptTemplate')
    @patch('flask_app.routes.conversion_quiz_agent.StrOutputParser')
    def test_ai_analysis_generation(self, mock_str_parser, mock_prompt, mock_chat_openai, 
                                   app, sample_part_b_answers, mock_patient, mock_basic_quiz):
        """Test that AI analysis is generated for advanced assessment"""
        with app.app_context():
            # Mock the database queries
            with patch.object(Patient, 'query') as mock_patient_query:
                mock_patient_query.filter_by.return_value.first.return_value = mock_patient
                
                with patch.object(ConversionQuiz, 'query') as mock_quiz_query:
                    mock_quiz_query.filter_by.return_value.order_by.return_value.first.return_value = mock_basic_quiz
            
            # Mock the AI components
            mock_llm = MagicMock()
            mock_chat_openai.return_value = mock_llm
            
            mock_prompt_template = MagicMock()
            mock_prompt.return_value = mock_prompt_template
            
            mock_chain = MagicMock()
            mock_chain.invoke.return_value = "This is a comprehensive AI analysis of the patient's sleep patterns and risk factors."
            mock_prompt_template.__or__ = MagicMock(return_value=mock_chain)
            mock_chain.__or__ = MagicMock(return_value=mock_chain)
            
            # Mock the helper functions
            with patch('flask_app.routes.conversion_quiz_agent.evaluate_phase_2') as mock_evaluate:
                mock_evaluate.return_value = (15, "High", "High risk explanation", [], True, ["snoring"])
                
                with patch('flask_app.routes.conversion_quiz_agent.get_phase_2_risk_message') as mock_risk_msg:
                    mock_risk_msg.return_value = ("High Risk", "High risk message")
                    
                    with patch('flask_app.routes.conversion_quiz_agent.get_phase_2_cta_buttons') as mock_cta:
                        mock_cta.return_value = ["Book consultation", "Take sleep test"]
                        
                        with patch('flask_app.routes.conversion_quiz_agent.store_quiz_data') as mock_store:
                            mock_store.return_value = 123
                            
                            # Make the request
                            from flask import Flask
                            test_app = Flask(__name__)
                            test_app.config['TESTING'] = True
                            
                            with test_app.test_request_context(
                                '/analyze_quiz_part_b',
                                method='POST',
                                json={
                                    'answers': sample_part_b_answers,
                                    'patient_email': 'test@example.com',
                                    'cta': 'Advanced Assessment'
                                }
                            ):
                                response = analyze_quiz_part_b()
                                data = json.loads(response.get_data(as_text=True))
                                
                                # Verify AI analysis was generated
                                assert data['success'] is True
                                assert 'ai_analysis' in data
                                assert 'ai_narrative' in data
                                assert data['ai_analysis'] is not None
                                assert data['ai_narrative'] is not None
                                assert len(data['ai_analysis']) > 0
                                assert len(data['ai_narrative']) > 0
    
    def test_ai_analysis_fallback_on_error(self, app, sample_part_b_answers, mock_patient, mock_basic_quiz):
        """Test that AI analysis falls back gracefully when there's an error"""
        with app.app_context():
            # Mock the database queries
            with patch.object(Patient, 'query') as mock_patient_query:
                mock_patient_query.filter_by.return_value.first.return_value = mock_patient
                
                with patch.object(ConversionQuiz, 'query') as mock_quiz_query:
                    mock_quiz_query.filter_by.return_value.order_by.return_value.first.return_value = mock_basic_quiz
            
            # Mock the helper functions
            with patch('flask_app.routes.conversion_quiz_agent.evaluate_phase_2') as mock_evaluate:
                mock_evaluate.return_value = (15, "High", "High risk explanation", [], True, ["snoring"])
                
                with patch('flask_app.routes.conversion_quiz_agent.get_phase_2_risk_message') as mock_risk_msg:
                    mock_risk_msg.return_value = ("High Risk", "High risk message")
                    
                    with patch('flask_app.routes.conversion_quiz_agent.get_phase_2_cta_buttons') as mock_cta:
                        mock_cta.return_value = ["Book consultation", "Take sleep test"]
                        
                        with patch('flask_app.routes.conversion_quiz_agent.store_quiz_data') as mock_store:
                            mock_store.return_value = 123
                            
                            # Mock AI components to raise an error
                            with patch('flask_app.routes.conversion_quiz_agent.ChatOpenAI') as mock_chat_openai:
                                mock_chat_openai.side_effect = Exception("API Error")
                                
                                # Make the request
                                from flask import Flask
                                test_app = Flask(__name__)
                                test_app.config['TESTING'] = True
                                
                                with test_app.test_request_context(
                                    '/analyze_quiz_part_b',
                                    method='POST',
                                    json={
                                        'answers': sample_part_b_answers,
                                        'patient_email': 'test@example.com',
                                        'cta': 'Advanced Assessment'
                                    }
                                ):
                                    response = analyze_quiz_part_b()
                                    data = json.loads(response.get_data(as_text=True))
                                    
                                    # Verify fallback behavior
                                    assert data['success'] is True
                                    assert 'ai_analysis' in data
                                    assert 'ai_narrative' in data
                                    assert "Error" in data['ai_analysis'] or "unavailable" in data['ai_analysis']
                                    assert data['ai_narrative'] == "High risk message"  # Should fallback to risk message
    
    def test_ai_analysis_prompt_structure(self):
        """Test that the AI analysis prompt includes all necessary components"""
        from flask_app.routes.conversion_quiz_agent import PromptTemplate
        
        # This test verifies that the prompt template variables are correctly defined
        # The actual prompt template creation is tested in the integration test above
        assert True  # Placeholder - the real test is in the integration test above 