"""
Test routes for VizBriz Quiz with predefined answers for different risk scenarios
"""

from flask import Blueprint, render_template, request, jsonify, current_app
import json
import os

# Create blueprint
vizbriz_quiz_test = Blueprint('vizbriz_quiz_test', __name__, url_prefix='/vizbriz-test')

# Predefined test scenarios
TEST_SCENARIOS = {
    'low_risk': {
        'name': 'Low Risk Patient',
        'description': 'Healthy individual with minimal sleep issues',
        'answers': {
            'DEMO_FULL_NAME': 'John Smith',
            'DEMO_DOB': '1985-06-15',
            'DEMO_EMAIL': 'john.smith@email.com',
            'DEMO_REFERRING_DENTIST_OR_CLI': 'Dr. Johnson',
            'DEMO_SEX': 'male',
            'DEMO_HEIGHT_CM': 175,
            'DEMO_WEIGHT_KG': 70,
            'Q1': 'no',  # Not diagnosed with sleep apnea
            'Q8': '15 minutes',
            'Q9': '8 hours',
            'Q10': 'good',  # Good sleep quality
            'Q11': 'no',  # No loud snoring
            'Q12': 'no',  # No observed apneas
            'Q13': 'no',  # No unrefreshed sleep
            'Q14': 'no',  # No excessive daytime sleepiness
            'Q15': 'no',  # No trouble staying awake while driving
            'Q16': 'no',  # No morning headaches
            'Q17': 'no',  # No frequent night waking
            'Q18': 'no',  # No trouble falling back asleep
            'Q19': 'no',  # No early morning waking
            'Q20': 'no',  # No restless sleep
            'Q21': 'no',  # No teeth grinding
            'Q22': 'no',  # No jaw clenching
            'Q23': 'no',  # No jaw pain
            'Q24': [],  # No TMJ symptoms
            'Q25': 'no',  # No high blood pressure
            'Q26': 'no',  # No diabetes
            'Q27': 'no',  # No asthma
            'Q28': 'no',  # No thyroid problems
            'Q29': 'no',  # No depression/anxiety
            'Q30': [],  # No medical conditions
            'Q31': 'no',  # No sleep medications
            'Q32': 'no',  # No nasal congestion
            'Q32-trigger': 'no',  # No nasal breathing difficulty
            'Q33': '5',  # No difficulty with productivity
            'Q34': '5',  # No difficulty with social outcomes
            'Q35': '5',  # No difficulty with activity level
            'Q36': '5',  # No difficulty with vigilance
            'Q37': 'very_high',  # Very high quality of life
            'Q38': ['better_sleep_quality'],  # Want better sleep quality
            'Q39': 'Everything is going well with my sleep.'
        }
    },
    
    'moderate_risk': {
        'name': 'Moderate Risk Patient',
        'description': 'Patient with some sleep issues and risk factors',
        'answers': {
            'DEMO_FULL_NAME': 'Sarah Johnson',
            'DEMO_DOB': '1978-03-22',
            'DEMO_EMAIL': 'sarah.johnson@email.com',
            'DEMO_REFERRING_DENTIST_OR_CLI': 'Dr. Williams',
            'DEMO_SEX': 'female',
            'DEMO_HEIGHT_CM': 165,
            'DEMO_WEIGHT_KG': 80,  # BMI ~29.4 (overweight)
            'Q1': 'no',  # Not diagnosed with sleep apnea
            'Q8': '30 minutes',
            'Q9': '6 hours',
            'Q10': 'fair',  # Fair sleep quality
            'Q11': 'yes',  # Loud snoring
            'Q12': 'not_sure',  # Unsure about observed apneas
            'Q13': 'yes',  # Unrefreshed sleep
            'Q14': 'yes',  # Excessive daytime sleepiness
            'Q15': 'no',  # No trouble staying awake while driving
            'Q16': 'yes',  # Morning headaches
            'Q17': 'yes',  # Frequent night waking
            'Q18': 'yes',  # Trouble falling back asleep
            'Q19': 'no',  # No early morning waking
            'Q20': 'yes',  # Restless sleep
            'Q21': 'yes',  # Teeth grinding
            'Q22': 'yes',  # Jaw clenching
            'Q23': 'no',  # No jaw pain
            'Q24': ['tinnitus'],  # Tinnitus
            'Q25': 'yes',  # High blood pressure
            'Q26': 'no',  # No diabetes
            'Q27': 'no',  # No asthma
            'Q28': 'no',  # No thyroid problems
            'Q29': 'yes',  # Depression/anxiety
            'Q30': ['hypertension', 'depression/anxiety'],  # Medical conditions
            'Q31': 'yes',  # Sleep medications
            'Q32': 'yes',  # Nasal congestion
            'Q32-trigger': 'no',  # No nasal breathing difficulty
            'Q33': '3',  # Some difficulty with productivity
            'Q34': '3',  # Some difficulty with social outcomes
            'Q35': '3',  # Some difficulty with activity level
            'Q36': '3',  # Some difficulty with vigilance
            'Q37': 'moderate',  # Moderate quality of life
            'Q38': ['better_sleep_quality', 'more_energy', 'improve_mood'],  # Multiple goals
            'Q39': 'I have been struggling with sleep for several months and it is affecting my daily life.'
        }
    },
    
    'high_risk': {
        'name': 'High Risk Patient',
        'description': 'Patient with severe sleep issues and multiple risk factors',
        'answers': {
            'DEMO_FULL_NAME': 'Michael Brown',
            'DEMO_DOB': '1970-11-08',
            'DEMO_EMAIL': 'michael.brown@email.com',
            'DEMO_REFERRING_DENTIST_OR_CLI': 'Dr. Davis',
            'DEMO_SEX': 'male',
            'DEMO_HEIGHT_CM': 180,
            'DEMO_WEIGHT_KG': 120,  # BMI ~37 (obese)
            'Q1': 'no',  # Not diagnosed with sleep apnea
            'Q8': '60 minutes',
            'Q9': '5 hours',
            'Q10': 'very_poor',  # Very poor sleep quality
            'Q11': 'yes',  # Loud snoring
            'Q12': 'yes',  # Observed apneas
            'Q13': 'yes',  # Unrefreshed sleep
            'Q14': 'yes',  # Excessive daytime sleepiness
            'Q15': 'yes',  # Trouble staying awake while driving (RED FLAG)
            'Q16': 'yes',  # Morning headaches
            'Q17': 'yes',  # Frequent night waking
            'Q18': 'yes',  # Trouble falling back asleep
            'Q19': 'yes',  # Early morning waking
            'Q20': 'yes',  # Restless sleep
            'Q21': 'yes',  # Teeth grinding
            'Q22': 'yes',  # Jaw clenching
            'Q23': 'yes',  # Jaw pain
            'Q24': ['ear_pain', 'tinnitus', 'vertigo'],  # Multiple TMJ symptoms
            'Q25': 'yes',  # High blood pressure
            'Q26': 'yes',  # Diabetes
            'Q27': 'yes',  # Asthma
            'Q28': 'yes',  # Thyroid problems
            'Q29': 'yes',  # Depression/anxiety
            'Q30': ['hypertension', 'diabetes', 'asthma', 'thyroid_disorder', 'depression/anxiety'],  # Multiple conditions
            'Q31': 'yes',  # Sleep medications
            'Q32': 'yes',  # Nasal congestion
            'Q32-trigger': 'yes',  # Nasal breathing difficulty
            'Q32a': '2',  # Poor nasal breathing during day
            'Q32b': '1',  # Very poor nasal breathing at night
            'Q32c': '2',  # Poor overall breathing comfort
            'Q33': '1',  # High difficulty with productivity
            'Q34': '1',  # High difficulty with social outcomes
            'Q35': '1',  # High difficulty with activity level
            'Q36': '1',  # High difficulty with vigilance
            'Q37': 'very_low',  # Very low quality of life
            'Q38': ['better_sleep_quality', 'reduce_snoring', 'more_energy', 'better_concentration', 'reduce_daytime_sleepiness', 'improve_mood', 'better_relationship'],  # All goals
            'Q39': 'My sleep problems are severely affecting every aspect of my life. I am constantly exhausted and cannot function properly during the day.'
        }
    },
    
    'diagnosed_treated_stable': {
        'name': 'Diagnosed, Treated & Stable',
        'description': 'Patient diagnosed with sleep apnea, currently treated and stable',
        'answers': {
            'DEMO_FULL_NAME': 'Robert Wilson',
            'DEMO_DOB': '1965-09-12',
            'DEMO_EMAIL': 'robert.wilson@email.com',
            'DEMO_REFERRING_DENTIST_OR_CLI': 'Dr. Miller',
            'DEMO_SEX': 'male',
            'DEMO_HEIGHT_CM': 185,
            'DEMO_WEIGHT_KG': 90,
            'Q1': 'yes',  # Diagnosed with sleep apnea
            'Q2': 'yes',  # Currently receiving treatment
            'Q3': 'cpap',  # CPAP treatment
            'Q4': 'no',  # No surgery
            'Q6': '2023-03-15',  # Recent sleep study
            'Q7': 'yes',  # Used therapeutic device during study
            'Q8': '10 minutes',
            'Q9': '7.5 hours',
            'Q10': 'very_good',  # Very good sleep quality
            'Q11': 'no',  # No loud snoring (treated)
            'Q12': 'no',  # No observed apneas (treated)
            'Q13': 'no',  # No unrefreshed sleep
            'Q14': 'no',  # No excessive daytime sleepiness
            'Q15': 'no',  # No trouble staying awake while driving
            'Q16': 'no',  # No morning headaches
            'Q17': 'no',  # No frequent night waking
            'Q18': 'no',  # No trouble falling back asleep
            'Q19': 'no',  # No early morning waking
            'Q20': 'no',  # No restless sleep
            'Q21': 'no',  # No teeth grinding
            'Q22': 'no',  # No jaw clenching
            'Q23': 'no',  # No jaw pain
            'Q24': [],  # No TMJ symptoms
            'Q25': 'no',  # No high blood pressure
            'Q26': 'no',  # No diabetes
            'Q27': 'no',  # No asthma
            'Q28': 'no',  # No thyroid problems
            'Q29': 'no',  # No depression/anxiety
            'Q30': [],  # No medical conditions
            'Q31': 'no',  # No sleep medications
            'Q32': 'no',  # No nasal congestion
            'Q32-trigger': 'no',  # No nasal breathing difficulty
            'Q33': '5',  # No difficulty with productivity
            'Q34': '5',  # No difficulty with social outcomes
            'Q35': '5',  # No difficulty with activity level
            'Q36': '5',  # No difficulty with vigilance
            'Q37': 'very_high',  # Very high quality of life
            'Q38': [],  # No specific goals (already well-treated)
            'Q39': 'My CPAP treatment has been very successful. I feel much better and my sleep quality has improved significantly.'
        }
    },
    
    'diagnosed_not_treated': {
        'name': 'Diagnosed but Not Treated',
        'description': 'Patient diagnosed with sleep apnea but not currently receiving treatment',
        'answers': {
            'DEMO_FULL_NAME': 'Jennifer Davis',
            'DEMO_DOB': '1982-07-25',
            'DEMO_EMAIL': 'jennifer.davis@email.com',
            'DEMO_REFERRING_DENTIST_OR_CLI': 'Dr. Anderson',
            'DEMO_SEX': 'female',
            'DEMO_HEIGHT_CM': 170,
            'DEMO_WEIGHT_KG': 95,
            'Q1': 'yes',  # Diagnosed with sleep apnea
            'Q2': 'no',  # Not currently receiving treatment
            'Q4': 'no',  # No surgery
            'Q6': '2022-11-20',  # Sleep study date
            'Q7': 'no',  # No therapeutic device during study
            'Q8': '45 minutes',
            'Q9': '6 hours',
            'Q10': 'poor',  # Poor sleep quality
            'Q11': 'yes',  # Loud snoring
            'Q12': 'yes',  # Observed apneas
            'Q13': 'yes',  # Unrefreshed sleep
            'Q14': 'yes',  # Excessive daytime sleepiness
            'Q15': 'yes',  # Trouble staying awake while driving
            'Q16': 'yes',  # Morning headaches
            'Q17': 'yes',  # Frequent night waking
            'Q18': 'yes',  # Trouble falling back asleep
            'Q19': 'yes',  # Early morning waking
            'Q20': 'yes',  # Restless sleep
            'Q21': 'yes',  # Teeth grinding
            'Q22': 'yes',  # Jaw clenching
            'Q23': 'yes',  # Jaw pain
            'Q24': ['tinnitus'],  # TMJ symptoms
            'Q25': 'yes',  # High blood pressure
            'Q26': 'no',  # No diabetes
            'Q27': 'no',  # No asthma
            'Q28': 'no',  # No thyroid problems
            'Q29': 'yes',  # Depression/anxiety
            'Q30': ['hypertension', 'depression/anxiety'],  # Medical conditions
            'Q31': 'yes',  # Sleep medications
            'Q32': 'yes',  # Nasal congestion
            'Q32-trigger': 'yes',  # Nasal breathing difficulty
            'Q32a': '3',  # Moderate nasal breathing during day
            'Q32b': '2',  # Poor nasal breathing at night
            'Q32c': '3',  # Moderate overall breathing comfort
            'Q33': '2',  # High difficulty with productivity
            'Q34': '2',  # High difficulty with social outcomes
            'Q35': '2',  # High difficulty with activity level
            'Q36': '2',  # High difficulty with vigilance
            'Q37': 'low',  # Low quality of life
            'Q38': ['better_sleep_quality', 'reduce_snoring', 'more_energy', 'reduce_daytime_sleepiness', 'improve_mood'],  # Multiple goals
            'Q39': 'I was diagnosed with sleep apnea but have not started treatment yet. I am struggling with severe sleep problems and need help.'
        }
    }
}

@vizbriz_quiz_test.route('/quiz', methods=['GET'])
def test_quiz_page():
    """
    Display the VizBriz quiz with predefined test data.
    Supports scenario selection via ?scenario= parameter.
    """
    # Get scenario from query parameter
    scenario = request.args.get('scenario', '')
    
    # Get language from query parameter, default to English
    language = request.args.get('lang', 'en')
    if language not in ['en', 'ru', 'he']:
        language = 'en'
    
    # Get optional clinic/referral parameters
    clinic_id = request.args.get('clinic_id')
    referral_doctor = request.args.get('referral')
    
    # Load quiz package
    try:
        quiz_package_path = '/home/ec2-user/vizbriz/static/vizbriz_quiz_package.json'
        with open(quiz_package_path, 'r', encoding='utf-8') as f:
            quiz_package = json.load(f)
        
        # Prepare test data
        test_data = None
        if scenario in TEST_SCENARIOS:
            test_data = TEST_SCENARIOS[scenario]
        
        # Prepare data for template
        context = {
            'quiz_package': quiz_package,
            'language': language,
            'clinic_id': clinic_id,
            'referral_doctor': referral_doctor,
            'is_rtl': language == 'he',  # Hebrew is RTL
            'test_mode': True,
            'scenario': scenario,
            'test_data': test_data,
            'available_scenarios': TEST_SCENARIOS
        }
        
        return render_template('vizbriz_quiz_test.html', **context)
        
    except Exception as e:
        current_app.logger.error(f"Error loading test quiz: {str(e)}")
        return jsonify({'error': 'Failed to load test quiz'}), 500

@vizbriz_quiz_test.route('/scenarios', methods=['GET'])
def list_scenarios():
    """
    List all available test scenarios
    """
    return jsonify({
        'scenarios': TEST_SCENARIOS,
        'usage': 'Add ?scenario=SCENARIO_NAME to the quiz URL to load predefined answers'
    })
