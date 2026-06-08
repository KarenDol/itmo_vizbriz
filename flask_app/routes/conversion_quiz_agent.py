
from flask import Blueprint, request, jsonify, render_template, Response, current_app, abort, flash, redirect, url_for
from flask_mail import Message
import json
from flask_app.extensions import db
from flask_app.models import ConversionQuiz, Patient, Dentist, ConsultationRequest, CTAInteractionLog, File, dentist_clinic_association
from flask_app.helpers.quiz_helpers import store_quiz_data, lookup_clinic_email_by_referral, get_clinic_email_and_dentist_id
from datetime import datetime
import boto3
import os
from langchain_community.chat_models import BedrockChat
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_community.chat_models import ChatOpenAI
from flask_app.models import ObservationStore
import csv
import io
from flask_app.helpers.quiz_helpers import evaluate_phase_2, get_phase_2_risk_message, get_phase_2_cta_buttons
from sqlalchemy import func, and_, or_
from flask_login import current_user, login_required
from flask_app.models import DSO, Clinic
import re
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from datetime import datetime
import io as pdf_io
from flask_app.routes.file_management_routes import filemgmt


conversion_quiz_agent = Blueprint('conversion_quiz_agent', __name__)

def generate_presigned_url_for_viewing(s3_key: str, inline: bool = True, expires_in: int = 3600) -> str:
    """
    Generate a presigned URL for viewing/downloading files from S3.
    Uses the same pattern as the patient files system.
    """
    try:
        # Use same S3 client configuration as short wizard upload
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-west-2')
        )
        bucket = os.getenv('S3_BUCKET_NAME', 'vizbrizpatients')
        params = {'Bucket': bucket, 'Key': s3_key}
        if inline:
            params['ResponseContentDisposition'] = 'inline'
        return s3_client.generate_presigned_url('get_object', Params=params, ExpiresIn=expires_in)
    except Exception as e:
        current_app.logger.error(f"Error generating presigned URL for viewing: {e}")
        return None

def clean_html_for_pdf(text):
    """
    Clean HTML content for PDF generation by removing HTML tags and converting to plain text
    """
    if not text:
        return ""
    
    import re
    
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    
    # Replace common HTML entities
    text = text.replace('&nbsp;', ' ')
    text = text.replace('&amp;', '&')
    text = text.replace('&lt;', '<')
    text = text.replace('&gt;', '>')
    text = text.replace('&quot;', '"')
    text = text.replace('&#39;', "'")
    
    # Clean up extra whitespace
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    
    return text

def save_file_to_database(filename, patient_id, file_type, file_size, s3_key, category, subcategory):
    """
    Save file information to database using the exact same pattern as file_management_routes
    """
    try:
        print(f"DEBUG: === DATABASE SAVE TRACE START ===")
        print(f"DEBUG: Parameters received:")
        print(f"DEBUG:   filename: {filename}")
        print(f"DEBUG:   patient_id: {patient_id}")
        print(f"DEBUG:   file_type: {file_type}")
        print(f"DEBUG:   file_size: {file_size}")
        print(f"DEBUG:   s3_key: {s3_key}")
        print(f"DEBUG:   category: {category}")
        print(f"DEBUG:   subcategory: {subcategory}")
        
        print(f"DEBUG: Step 1: Creating File object...")
        new_file = File(
            name=filename,
            patient_id=patient_id,
            file_type=file_type or 'application/octet-stream',
            file_size=file_size or 0,
            s3_key=s3_key,
            category=category,
            subcategory=subcategory
        )
        print(f"DEBUG: Step 1 COMPLETE: File object created successfully")
        
        print(f"DEBUG: Step 2: Checking db.session availability...")
        print(f"DEBUG:   db.session type: {type(db.session)}")
        print(f"DEBUG:   db.session bound: {db.session.is_active}")
        
        print(f"DEBUG: Step 3: Adding file to database session...")
        db.session.add(new_file)
        print(f"DEBUG: Step 3 COMPLETE: File added to session")
        
        print(f"DEBUG: Step 4: Committing to database...")
        db.session.commit()
        print(f"DEBUG: Step 4 COMPLETE: Database commit successful")
        
        print(f"DEBUG: Step 5: Getting file ID...")
        file_id = new_file.id
        print(f"DEBUG: Step 5 COMPLETE: File saved to database with ID: {file_id}")
        
        print(f"DEBUG: === DATABASE SAVE TRACE END - SUCCESS ===")
        return file_id
        
    except Exception as e:
        print(f"DEBUG: === DATABASE SAVE TRACE - ERROR ===")
        print(f"DEBUG: Error type: {type(e).__name__}")
        print(f"DEBUG: Error message: {str(e)}")
        print(f"DEBUG: Error occurred at step: {getattr(e, 'step', 'unknown')}")
        import traceback
        print(f"DEBUG: Full traceback:")
        print(traceback.format_exc())
        
        print(f"DEBUG: Attempting rollback...")
        try:
            db.session.rollback()
            print(f"DEBUG: Rollback successful")
        except Exception as rollback_error:
            print(f"DEBUG: Rollback failed: {str(rollback_error)}")
        
        print(f"DEBUG: === DATABASE SAVE TRACE END - FAILED ===")
        raise e

@conversion_quiz_agent.route('/quiz', methods=['GET'])
def show_quiz():
    from flask_app.models import DSO, Clinic
    dso_id = request.args.get('dso_id', None, type=int)  # No hardcoded default
    testing = request.args.get('testing', 0, type=int)

    default_dso_id = current_app.config.get('DEFAULT_OSA_DSO_ID', 28)

    if dso_id is not None:
        dso = DSO.query.get(dso_id)
    else:
        dso = DSO.query.get(default_dso_id) or DSO.query.first()
    clinics = Clinic.query.filter_by(dso_id=dso.id, status='active').all() if dso else []
    
    # Always set default clinic ID based on DSO (first active clinic)
    default_clinic_id = clinics[0].id if clinics else None

    default_answers = None
    if testing:
        default_answers = {
            # Personal Information
            'full_name': 'John Doe',
            'patient_email': 'john.doe@example.com',
            'phone': '555-123-4567',
            'address': '123 Main St, Springfield, IL 62704',
            'dob': '1990-01-01',
            'gender': 'male',
            # 'clinic_id': default_clinic_id,  # Leave empty so user must select
            'doctor_referral': 'Dr. Smith',
            
            # Clinical Assessment
            'diagnosed': 'no',
            'using_treatment': 'no',
            'treatment_details': 'N/A',
            'snoring': 'yes',
            'snoring_details': 'Loud snoring every night',
            'tiredness': 'yes',
            'observed_apnea': 'yes',
            'daytime_sleepiness': 'yes',
            'driving_fatigue': 'no',
            'bruxism': 'no',
            'bruxism_details': '',
            'weight': 'yes',
        }

    return render_template('conversion_quiz.html', dso=dso, clinics=clinics, default_clinic_id=default_clinic_id, default_answers=default_answers)

@conversion_quiz_agent.route('/test_quiz', methods=['GET'])
def test_quiz():
    return jsonify({'message': 'Quiz blueprint is working!'})

@conversion_quiz_agent.route('/test-consultation-button')
def test_consultation_button():
    """Test page to validate consultation button functionality"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Test Consultation Button</title>
    </head>
    <body>
        <h1>Test Consultation Button</h1>
        
        <button id="consult-btn" onclick="testConsultation()">
            👨‍⚕️ Consult with our dental sleep team
        </button>
        
        <div id="result"></div>
        
        <script>
            async function testConsultation() {
                const email = 'buttontest@example.com';
                console.log('Button clicked! Testing consultation API...');
                
                try {
                    const response = await fetch('/api/consultation', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({
                            email: email,
                            comment: 'Test from button validation page'
                        })
                    });
                    
                    const result = await response.json();
                    console.log('API Response:', result);
                    
                    if (result.success) {
                        document.getElementById('result').innerHTML = `
                            <div style="color: green; padding: 10px; border: 1px solid green;">
                                ✅ SUCCESS!<br>
                                Consultation ID: ${result.consultation_id}<br>
                                Message: ${result.message}
                            </div>
                        `;
                    } else {
                        document.getElementById('result').innerHTML = `
                            <div style="color: red; padding: 10px; border: 1px solid red;">
                                ❌ ERROR: ${result.error}
                            </div>
                        `;
                    }
                    
                } catch (error) {
                    console.error('Error:', error);
                    document.getElementById('result').innerHTML = `
                        <div style="color: red; padding: 10px; border: 1px solid red;">
                            ❌ FAILED: ${error.message}
                        </div>
                    `;
                }
            }
        </script>
    </body>
    </html>
    """

# Standardized messages for each risk level
risk_messages = {
    "Low": {
        "title": "🟢 Final Result: Low Risk",
        "basic": """Your answers suggest a low risk for obstructive sleep apnea (OSA).<br>That's good news — but it doesn't completely rule out the possibility of a sleep-related issue.<br>Low-risk individuals should continue monitoring for signs like snoring, fatigue, or disturbed sleep patterns.<br>If you're experiencing symptoms or want peace of mind, consider a consultation or a simple home sleep test.<br>Your sleep still matters — stay proactive and informed.""",
        "advanced": """Your answers suggest you're at low risk for obstructive sleep apnea (OSA).<br>That's reassuring — but OSA and related issues can still develop gradually or go unnoticed.<br>If you've experienced occasional symptoms or changes in your sleep quality, we recommend discussing these with your dental sleep provider.<br>Ongoing monitoring and healthy sleep habits can go a long way in prevention.<br>If concerns arise later, know that testing is easy and painless — and help is always available."""
    },
    "Moderate": {
        "title": "🟡 Final Result: Moderate Risk",
        "basic": """Your answers suggest a moderate risk for sleep apnea.<br>Even mild forms of OSA can affect your focus, energy, and long-term health — and symptoms often worsen if left untreated.<br>This condition can often be managed effectively — but early detection is key.<br>Want more clarity? <a href=\"/advanced_quiz?email={patient_email}\">Complete the full assessment</a> to get a clearer picture of your sleep health and your true level of risk.<br>Take a proactive step now - schedule a consultation with a sleep dental specialist to discuss your results and explore next steps.""",
        "advanced": """Your answers suggest a moderate risk for obstructive sleep apnea (OSA).<br>Some of your symptoms and health factors may already be affecting your sleep, energy, and overall health — even if the condition is only mild to moderate.<br>If left unaddressed, OSA can worsen over time and increase your risk for fatigue, memory issues, and cardiovascular problems.<br>Don't ignore the signs.<br>The good news is that effective treatment options are available — including oral appliance therapy, PAP therapy, lifestyle changes, and, in some cases, surgery.<br>Take the next step toward better sleep and better health.<br>Take this seriously — even moderate signs can have long-term impact.<br>You can schedule a consultation with a sleep dental specialist to discuss your results and explore the most suitable treatment approach for you — and prevent your symptoms from escalating.<br>Or take a proactive step now — book a home sleep test to find out if OSA is affecting your nights."""
    },
    "High": {
        "title": "🔴 Final Result: High Risk",
        "basic": """One or more of your answers suggest a high likelihood of sleep apnea — or warning signs that should not be ignored.<br>This condition may already be affecting your sleep, focus, and overall health — and symptoms often get worse over time if left untreated.<br>The good news is that sleep apnea can often be managed effectively — but early detection is key.<br>We strongly recommend <a href=\"/advanced_quiz?email={patient_email}\">completing the full sleep health quiz</a> to better understand your risks and treatment options.<br>Take a proactive step now - schedule a consultation with a sleep dental specialist to discuss your results and explore next steps.""",
        "advanced": """Your answers indicate a high likelihood of obstructive sleep apnea (OSA).<br>Your symptoms, lifestyle factors, and medical history suggest that this condition may already be affecting your sleep, heart, brain, and overall well-being.<br>Untreated OSA can lead to serious complications — including high blood pressure, cardiovascular disease, memory issues, and reduced quality of life.<br>You should not wait.<br>The good news is that effective treatment options are available — including oral appliance therapy, PAP therapy, lifestyle changes, and, in some cases, surgery.<br>Taking action now can help you sleep better, protect your health, and feel more energized throughout the day.<br>Take the next step toward better sleep and better health.<br>Take this seriously — your health depends on it."""
    },
    "Diagnosed – Not Using Treatment": {
        "title": "🟠 Final Result: Diagnosed – Not Using Treatment",
        "basic": """You reported that you've been diagnosed with obstructive sleep apnea — but are not currently receiving treatment. That means your condition may be unmanaged.<br>Untreated or undertreated OSA doesn't just affect your sleep — it can impact your energy, mood, memory, heart health, and overall quality of life.<br>The good news? There are proven, personalized treatments that can help — including oral appliance therapy, PAP therapy, and lifestyle changes.<br>Don't wait.<br>Taking action now can restore your sleep, improve your daytime energy, and reduce serious long-term health risks.<br>To better understand what's still affecting your sleep and how to improve it, continue to the <a href=\"/advanced_quiz?email={patient_email}\">full sleep health assessment</a>.<br>Take a proactive step now - schedule a consultation with a sleep dental specialist to reassess the treatment approach that's right for you.""",
        "advanced": """You reported that you've been diagnosed with obstructive sleep apnea — but you're not currently using any form of treatment.<br>That means your condition may be unmanaged, or that you've had difficulty continuing with previous recommendations.<br>Untreated or undertreated OSA can impact more than just your sleep — it can affect memory, energy levels, cardiovascular health, and overall well-being.<br>The good news is that several effective and personalized treatment approaches are available — including oral appliance therapy, PAP therapy, and lifestyle changes.<br>Don't delay.<br>Taking action now can help you sleep better, restore your energy, and reduce the serious health risks linked to untreated or undertreated OSA.<br>Take the next step toward better sleep and better health.<br>You deserve to feel better.<br>Take this seriously — your health depends on it.<br>Speak with a sleep dental specialist to explore a treatment path that fits your needs and lifestyle.<br>Or book a home sleep test to check whether your condition has progressed — it's painless, easy, and done in your own bed."""
    },
    "Diagnosed – Using & No Symptoms": {
        "title": "🟢 Final Result: Diagnosed – Using & No Symptoms",
        "basic": """You reported that you've already been diagnosed with obstructive sleep apnea and are currently receiving treatment.<br>Your answers suggest that your symptoms are well-managed — that's great news.<br>Effective OSA treatment can significantly improve sleep quality, boost daytime energy, and reduce long-term health risks.<br>Still, it's important to keep an eye on your condition. Changes in weight, health, or sleep habits over time may affect how well your current treatment works.<br>To stay on track, we recommend completing the <a href=\"/advanced_quiz?email={patient_email}\">full sleep health assessment</a>. It can help spot early signs of change — and make sure your treatment is still right for you.<br>Great sleep is worth protecting.<br>Take a proactive step now — schedule a consultation with a sleep dental specialist to ensure your current treatment remains the best fit for your needs.""",
        "advanced": """You indicated that you've been diagnosed with sleep apnea and are currently undergoing treatment — and your answers show no major active symptoms.<br>That's a great sign — and it likely means your therapy is working.<br>Still, this assessment covers only a limited set of indicators.<br>Some issues may develop silently or go unnoticed, even during treatment.<br>Stay proactive — your sleep health is worth protecting.<br>Great sleep should feel great every day.<br>To confirm your progress or explore whether better-suited options exist, you can schedule a follow-up consultation.<br>Or book a home sleep test — especially if your last study was over 12 months ago or you're unsure about your current treatment status."""
    },
    "Diagnosed – Still Symptomatic": {
        "title": "🟡 Final Result: Diagnosed – Still Symptomatic",
        "basic": """You reported that you've already been diagnosed with obstructive sleep apnea.<br>However, your answers suggest that symptoms may still be interfering with your sleep, energy, or overall well-being.<br>This may indicate that your current treatment is no longer fully effective — or that your needs have changed over time.<br>OSA management isn't one-size-fits-all. Fortunately, there are multiple effective options available — including oral appliance therapy, PAP therapy, lifestyle modifications, and, in some cases, surgery.<br>To better understand what's still affecting your sleep and how to improve it, continue to the <a href=\"/advanced_quiz?email={patient_email}\">full sleep health assessment</a>.<br>Take a proactive step now - schedule a consultation with a sleep dental specialist to reassess the treatment approach that's right for you.""",
        "advanced": """You reported that you're currently using CPAP or another treatment for sleep apnea — but your answers suggest you're still experiencing symptoms.<br>This may mean your current treatment isn't fully effective, or that your needs have changed.<br>OSA management isn't one-size-fits-all — and sometimes it takes reassessment to find what really works.<br>Don't settle for 'just okay.'<br>There are multiple effective options available — including oral appliance therapy, PAP therapy, and surgical or lifestyle modifications.<br>Taking action now can help you sleep better, restore your energy, and reduce your long-term health risks.<br>You deserve to feel better.<br>Take this seriously — your health depends on it.<br>We recommend consulting with a sleep dental specialist to reevaluate your treatment approach.<br>Or book a home sleep test to check whether your condition has progressed — especially if your last study was over 12 months ago or your symptoms have changed."""
    }
}


def evaluate_phase_1(quiz_answers):
    score = 0
    reasons = []
    red_flags = []
    bmi_flag = quiz_answers.get("weight") == "yes"

    if quiz_answers.get("observed_apnea") == "yes":
        red_flags.append("observed_apnea")
        reasons.append("Witnessed apneas or gasping during sleep")
    if quiz_answers.get("daytime_sleepiness") == "yes":
        red_flags.append("daytime_sleepiness")
        reasons.append("Unintentionally falling asleep during the day")
    if quiz_answers.get("driving_fatigue") == "yes":
        red_flags.append("driving_fatigue")
        reasons.append("Trouble staying awake while driving")

    yes_questions = [
        ("snoring", "Snoring at night"),
        ("tiredness", "Waking up tired or unrested"),
        ("observed_apnea", "Witnessed apneas or choking"),
        ("daytime_sleepiness", "Daytime sleepiness"),
        ("driving_fatigue", "Fatigue while driving"),
        ("bruxism", "Signs of teeth grinding or jaw issues"),
        ("weight", "BMI above normal")
    ]

    for q, reason in yes_questions:
        if quiz_answers.get(q) == "yes":
            score += 1
            if reason not in reasons:
                reasons.append(reason)

    diagnosed = quiz_answers.get("diagnosed") == "yes"
    using_treatment = quiz_answers.get("using_treatment") == "yes"

    if diagnosed:
        if using_treatment:
            if score == 0:
                risk_level = "Diagnosed – Using & No Symptoms"
                risk_data = risk_messages[risk_level]
                risk_message = risk_data["basic"]  # Use basic message for basic quiz
                recommendations = "Continue your current therapy and consult your sleep specialist if new symptoms arise."
            else:
                risk_level = "Diagnosed – Still Symptomatic"
                risk_data = risk_messages[risk_level]
                risk_message = risk_data["basic"]  # Use basic message for basic quiz
                recommendations = "Consult with your sleep specialist to reassess your current treatment. Adjustments or alternative therapies may improve your results."
        else:
            risk_level = "Diagnosed – Not Using Treatment"
            risk_data = risk_messages[risk_level]
            risk_message = risk_data["basic"]  # Use basic message for basic quiz
            recommendations = "Take the next step toward better sleep and better health. You deserve to feel better."
    else:
        if any(red_flags) or score >= 3:
            risk_level = "High"
            risk_data = risk_messages[risk_level]
            risk_message = risk_data["basic"]  # Use basic message for basic quiz
            recommendations = "Take the next step toward better sleep and better health. Take this seriously — your health depends on it."
        elif score == 2 or bmi_flag:
            risk_level = "Moderate"
            risk_data = risk_messages[risk_level]
            risk_message = risk_data["basic"]  # Use basic message for basic quiz
            recommendations = "Take the next step toward better sleep and better health. Take this seriously — even moderate signs can have long-term impact."
        else:
            risk_level = "Low"
            risk_data = risk_messages[risk_level]
            risk_message = risk_data["basic"]  # Use basic message for basic quiz
            recommendations = "Take the next step toward better sleep and better health. Keep paying attention — your sleep matters."

    suggested_actions = []
    if diagnosed:
        suggested_actions = ["continue_to_phase2", "book_test", "book_consult"]
    elif risk_level == "High":
        suggested_actions = ["book_test", "book_consult"]
    elif risk_level == "Moderate":
        suggested_actions = ["continue_to_phase2", "book_test"]
    else:
        suggested_actions = ["continue_to_phase2"]

    prompt_used = f"""
You are a clinical sleep apnea assessment expert.

Analyze the quiz answers below and return a JSON object with:
- risk_level: One of ["Low", "Moderate", "High", or "Diagnosed – Not Using Treatment", "Diagnosed – Still Symptomatic", "Diagnosed – Using & No Symptoms"]
- risk_explanation: A clear summary of the main reasons for the assigned risk, including which symptoms or factors triggered it.
- recommendations: Patient-friendly guidance on next steps, such as home sleep testing, consultation, or monitoring.

If the patient answered "yes" to diagnosed, adjust your logic accordingly:
- If using_treatment = no → they are "Diagnosed – Not Using Treatment"
- If using_treatment = yes AND still has symptoms → "Diagnosed – Still Symptomatic"
- If using_treatment = yes AND no current symptoms → "Diagnosed – Using & No Symptoms"

Quiz answers:
{json.dumps(quiz_answers, indent=2)}
"""

    return {
        "risk_level": risk_level,
        "risk_explanation": risk_message,
        "recommendations": recommendations,
        "score": score,
        "red_flags": red_flags,
        "suggested_actions": suggested_actions,
        "reasons": reasons,
        "prompt": prompt_used
    }


@conversion_quiz_agent.route('/analyze_quiz', methods=['POST'])
def analyze_quiz():
    import os
    base_url = os.getenv('BASE_URL', 'http://localhost:7000')
    current_app.logger.info("DEBUG: analyze_quiz called")
    try:
        data = request.get_json()
        
        if not data:
            current_app.logger.info("DEBUG: No JSON data received")
            return jsonify({'error': 'No JSON data received'}), 400
        
        quiz_answers = data.get('answers', {})
        patient_email = data.get('patient_email')
        dso_id = data.get('dso_id', None)
        
        current_app.logger.info(f"DEBUG: Processing quiz for email={patient_email}, dso_id={dso_id}")
        
        # Look up clinic email from DSO ID or use default
        from flask_app.helpers.quiz_helpers import get_clinic_email_from_dso_id
        clinic_email = get_clinic_email_from_dso_id(dso_id) if dso_id else 'info@vizbriz.com'
        
        # Determine clinic_id: use provided clinic_id or first clinic from DSO
        clinic_id = quiz_answers.get('clinic_id')  # Extract clinic_id from form answers (optional)
        print(f"DEBUG: Raw quiz_answers: {quiz_answers}")
        print(f"DEBUG: clinic_id from form: {clinic_id}")
        
        if not clinic_id and dso_id:
            # If no clinic_id provided, get first clinic from DSO
            try:
                from flask_app.models import Clinic
                clinics = Clinic.query.filter_by(dso_id=dso_id, status='active').all()
                print(f"DEBUG: Found {len(clinics)} clinics for DSO {dso_id}")
                if clinics:
                    clinic_id = clinics[0].id
                    print(f"DEBUG: No clinic_id provided, using first clinic {clinic_id} from DSO {dso_id}")
                else:
                    print(f"DEBUG: No active clinics found for DSO {dso_id}")
            except Exception as e:
                print(f"DEBUG: Error getting clinics for DSO {dso_id}: {str(e)}")
        elif clinic_id:
            print(f"DEBUG: Using provided clinic_id: {clinic_id}")
        
        print(f"DEBUG: Final clinic_id: {clinic_id}")
        print(f"DEBUG: Processing for email: {patient_email}, clinic_email: {clinic_email} (from DSO {dso_id}), clinic_id: {clinic_id}, dso_id: {dso_id}")
        
        # First check if patient exists
        try:
            existing_patient = Patient.query.filter_by(email=patient_email).first()
            print(f"DEBUG: Existing patient check result: {existing_patient}")
        except Exception as e:
            print(f"DEBUG: Error checking for existing patient: {str(e)}")
            raise

        try:
            if existing_patient:
                print("DEBUG: Updating existing patient")
                # Update existing patient information
                existing_patient.name = quiz_answers.get('full_name', existing_patient.name)
                existing_patient.phone = quiz_answers.get('phone', existing_patient.phone)
                existing_patient.address = quiz_answers.get('address', existing_patient.address)
                if quiz_answers.get('dob'):
                    existing_patient.dob = datetime.strptime(quiz_answers.get('dob'), '%Y-%m-%d').date()
                existing_patient.gender = quiz_answers.get('gender', existing_patient.gender)
                
                # Update sleep-related fields
                existing_patient.snoring = quiz_answers.get('snoring', existing_patient.snoring)
                existing_patient.snoring_other = quiz_answers.get('snoring_details', existing_patient.snoring_other)
                existing_patient.daytime_sleepiness = quiz_answers.get('daytime_sleepiness', existing_patient.daytime_sleepiness)
                
                # Update clinic_id - respect user's clinic selection over DSO default
                if clinic_id:
                    # User selected a specific clinic - use that
                    existing_patient.clinic_id = clinic_id
                    print(f"DEBUG: Updated existing patient with user-selected clinic ID: {clinic_id}")
                elif dso_id:
                    # Only use DSO default if no clinic was selected by user
                    try:
                        from flask_app.models import DSO, Clinic
                        dso = DSO.query.get(dso_id)
                        if dso:
                            clinics = Clinic.query.filter_by(dso_id=dso_id, status='active').all()
                            if clinics:
                                # Use first clinic of the DSO as fallback
                                new_clinic_id = clinics[0].id
                                old_clinic_id = existing_patient.clinic_id
                                existing_patient.clinic_id = new_clinic_id
                                print(f"DEBUG: Updated existing patient clinic from {old_clinic_id} to {new_clinic_id} for DSO {dso_id} (fallback)")
                            else:
                                print(f"DEBUG: No active clinics found for DSO {dso_id}")
                    except Exception as e:
                        print(f"DEBUG: Error assigning clinic to existing patient: {str(e)}")
                
                patient = existing_patient
                db.session.commit()
                print(f"DEBUG: Updated existing patient with ID: {patient.id}")
            else:
                print("DEBUG: Creating new patient")
                
                # Smart dentist assignment: DSO → Clinic → Dentist > referral dentist > None
                dentist_id = None  # No hardcoded fallback
                assigned_clinic_id = clinic_id  # Start with provided clinic_id (might be None)
                
                current_app.logger.info(f"DEBUG: Creating patient - dso_id={dso_id}, clinic_id={clinic_id}")
                
                # Step 1: If no clinic_id provided, get clinics from DSO and assign first one
                if not clinic_id and dso_id:
                    try:
                        from flask_app.models import DSO, Clinic
                        dso = DSO.query.get(dso_id)
                        if dso:
                            # Get active clinics for this DSO
                            clinics = Clinic.query.filter_by(dso_id=dso_id, status='active').all()
                            if clinics:
                                # Select the first clinic
                                selected_clinic = clinics[0]
                                assigned_clinic_id = selected_clinic.id
                                current_app.logger.info(f"DEBUG: Assigned clinic {assigned_clinic_id} from DSO {dso_id}")
                                
                                # Get dentists from this clinic
                                dentists = selected_clinic.dentists.all()
                                if dentists:
                                    # Select the first dentist from the clinic
                                    selected_dentist = dentists[0]
                                    dentist_id = selected_dentist.id
                                    current_app.logger.info(f"DEBUG: Assigned dentist {dentist_id} from clinic")
                                else:
                                    current_app.logger.info(f"DEBUG: No dentists found for clinic")
                            else:
                                current_app.logger.info(f"DEBUG: No active clinics for DSO {dso_id}")
                        else:
                            current_app.logger.info(f"DEBUG: DSO {dso_id} not found")
                    except Exception as e:
                        current_app.logger.info(f"DEBUG: Error looking up DSO: {str(e)}")
                
                # Step 2: If we have a clinic_id but no dentist_id yet, get dentist from that clinic
                elif clinic_id and dentist_id is None:
                    try:
                        from flask_app.models import Clinic
                        clinic = Clinic.query.get(clinic_id)
                        if clinic:
                            dentists = clinic.dentists.all()
                            if dentists:
                                selected_dentist = dentists[0]
                                dentist_id = selected_dentist.id
                                current_app.logger.info(f"DEBUG: Assigned dentist {dentist_id} from provided clinic")
                            else:
                                current_app.logger.info(f"DEBUG: No dentists found for provided clinic")
                    except Exception as e:
                        current_app.logger.info(f"DEBUG: Error looking up clinic dentists: {str(e)}")
                
                # Step 3: If still no dentist found, try referral dentist
                if dentist_id is None:  # Still no dentist assigned, try referral
                    referral_name = quiz_answers.get('doctor_referral')
                    if referral_name:
                        # Query for a dentist whose name matches the referral
                        referring_dentist = Dentist.query.filter(Dentist.name.ilike(f"%{referral_name}%")).first()
                        if referring_dentist:
                            dentist_id = referring_dentist.id
                            current_app.logger.info(f"DEBUG: Found referral dentist {dentist_id}")
                        else:
                            current_app.logger.info(f"DEBUG: No dentist found for referral")
                    else:
                        current_app.logger.info("DEBUG: No doctor referral provided")
                
                # Step 4: Final fallback - find any dentist if still None
                if dentist_id is None:
                    fallback_dentist = Dentist.query.first()
                    if fallback_dentist:
                        dentist_id = fallback_dentist.id
                        current_app.logger.info(f"DEBUG: Using fallback dentist {dentist_id}")
                    else:
                        current_app.logger.info("DEBUG: No dentists found in database")
                
                current_app.logger.info(f"DEBUG: Final assignment - dentist_id={dentist_id}, clinic_id={assigned_clinic_id}")

                # Create new patient
                patient = Patient(
                    name=quiz_answers.get('full_name'),
                    email=patient_email,
                    phone=quiz_answers.get('phone'),
                    address=quiz_answers.get('address'),
                    dob=datetime.strptime(quiz_answers.get('dob'), '%Y-%m-%d').date() if quiz_answers.get('dob') else None,
                    gender=quiz_answers.get('gender'),
                    status='New',
                    snoring=quiz_answers.get('snoring'),
                    snoring_other=quiz_answers.get('snoring_details'),
                    daytime_sleepiness=quiz_answers.get('daytime_sleepiness'),
                    dentist_id=dentist_id,
                    clinic_id=assigned_clinic_id  # Use assigned clinic ID (could be from DSO lookup or provided)
                )
                db.session.add(patient)
                db.session.commit()
                current_app.logger.info(f"DEBUG: Patient created - ID={patient.id}, dentist_id={patient.dentist_id}, clinic_id={patient.clinic_id}")
        except Exception as e:
            current_app.logger.info(f"DEBUG: Error handling patient record: {str(e)}")
            db.session.rollback()
            raise

        result_data = evaluate_phase_1(quiz_answers)

        # Generate AI analysis first
        try:
            # Generate AI analysis
            
            # Use the same API key configuration as in __init__.py
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY is not set")
            
            print("DEBUG: OpenAI API key configured")
            print(f"DEBUG: API key length: {len(api_key)}")
            
            # Initialize OpenAI client
            llm = ChatOpenAI(
                temperature=0.1,
                model_name="gpt-3.5-turbo",
                openai_api_key=api_key
            )
            
            print("DEBUG: ChatOpenAI initialized successfully")
            
            # Create AI analysis prompt
            ai_prompt = PromptTemplate(
                input_variables=["quiz_answers", "risk_level", "score", "reasons"],
                template="""
                You are a clinical sleep apnea expert. Analyze the following sleep assessment data and provide a detailed clinical analysis.
                
                Quiz Answers: {quiz_answers}
                Risk Level: {risk_level}
                Score: {score}
                Identified Symptoms: {reasons}
                
                Provide a comprehensive clinical analysis including:
                1. Clinical interpretation of the reported symptoms
                2. Risk factors identified from the assessment
                3. Potential differential diagnoses to consider
                4. Clinical recommendations for next steps
                
                Keep it professional and medical in tone. Be specific about the clinical implications.
                """
            )
            
            print("DEBUG: AI prompt template created")
            print(f"DEBUG: Prompt variables: quiz_answers, risk_level, score, reasons")
            
            # Prepare the input data
            prompt_input = {
                "quiz_answers": json.dumps(quiz_answers, indent=2),
                "risk_level": result_data['risk_level'],
                "score": result_data['score'],
                "reasons": ", ".join(result_data['reasons']) if result_data['reasons'] else "None reported"
            }
            
            print(f"DEBUG: Prompt input prepared - Risk Level: {prompt_input['risk_level']}, Score: {prompt_input['score']}")
            print(f"DEBUG: Reasons: {prompt_input['reasons']}")
            
            print("DEBUG: Creating LLM chain")
            
            # Generate AI analysis using new RunnableSequence pattern
            chain = ai_prompt | llm | StrOutputParser()
            print("DEBUG: LLM chain created, running analysis...")
            
            ai_analysis = chain.invoke(prompt_input)
            
            print(f"DEBUG: AI analysis generated successfully: {ai_analysis[:100]}...")
            print(f"DEBUG: Full AI analysis length: {len(ai_analysis)} characters")
            
        except Exception as e:
            print(f"DEBUG: AI analysis failed with error: {str(e)}")
            print(f"DEBUG: Error type: {type(e).__name__}")
            print(f"DEBUG: Error details: {e}")
            ai_analysis = f"AI analysis unavailable - Error: {str(e)}"
        
        # Generate AI narrative
        try:
            print("DEBUG: Generating AI narrative")
            
            # Create AI narrative prompt
            narrative_prompt = PromptTemplate(
                input_variables=["quiz_answers", "risk_level", "score", "reasons", "ai_analysis"],
                template="""
                You are a clinical sleep apnea expert. Write a simple, professional narrative that encourages the patient to start treatment for OSA based on their risk level.
                
                Quiz Answers: {quiz_answers}
                Risk Level: {risk_level}
                Score: {score}
                Identified Symptoms: {reasons}
                AI Analysis: {ai_analysis}
                
                Write a concise clinical narrative (2-3 sentences) that:
                
                For HIGH RISK: Emphasize urgency and immediate action needed
                For MODERATE RISK: Highlight the importance of early intervention
                For LOW RISK: Focus on monitoring and preventive measures
                For DIAGNOSED cases: Focus on treatment compliance and optimization
                
                Keep it simple, professional, and action-oriented. The goal is to motivate the patient to take the next step toward OSA treatment.
                """
            )
            
            print("DEBUG: Narrative prompt created, running LLM chain")
            
            # Generate AI narrative using new RunnableSequence pattern
            narrative_chain = narrative_prompt | llm | StrOutputParser()
            ai_narrative = narrative_chain.invoke({
                "quiz_answers": json.dumps(quiz_answers, indent=2),
                "risk_level": result_data['risk_level'],
                "score": result_data['score'],
                "reasons": ", ".join(result_data['reasons']) if result_data['reasons'] else "None reported",
                "ai_analysis": ai_analysis
            })
            
            print(f"DEBUG: AI narrative generated successfully: {ai_narrative[:100]}...")
            
        except Exception as e:
            print(f"DEBUG: AI narrative failed with error: {str(e)}")
            ai_narrative = result_data['recommendations']  # Fallback to templated response

        print(f"DEBUG: About to start quiz data storage section...")
        try:
            # Store quiz data with enhanced ai_response structure
            enhanced_result_data = {
                **result_data,
                'ai_analysis': ai_analysis,
                'ai_narrative': ai_narrative
            }
            
            quiz_entry = ConversionQuiz(
                user_id=patient.id,
                quiz_input=json.dumps(quiz_answers),
                ai_response=json.dumps(enhanced_result_data),
                cta='',  # Will be updated after email templates are generated
                clinic_email=clinic_email,  # Add clinic_email field
                patient_email=patient_email,  # Add patient_email field
                quiz_type='basic_quiz',  # Add quiz_type field
                clinic_id=clinic_id,  # Add clinic_id from form selection
                referral_doctor=quiz_answers.get('doctor_referral')  # Add referral_doctor field
            )
            db.session.add(quiz_entry)
            db.session.commit()
            print(f"DEBUG: Stored quiz entry with ID: {quiz_entry.id}")
            print(f"DEBUG: About to start PDF generation section...")
            print(f"DEBUG: ==========================================")
            print(f"DEBUG: ABOUT TO CALL PDF GENERATION FUNCTION!")
            print(f"DEBUG: ==========================================")
            
            # Generate PDF and upload to S3
            print(f"DEBUG: ==========================================")
            print(f"DEBUG: ABOUT TO START PDF GENERATION!")
            print(f"DEBUG: ==========================================")
            print(f"DEBUG: Starting PDF generation for quiz ID: {quiz_entry.id}")
            print(f"DEBUG: About to call generate_quiz_pdf_and_upload function")
            try:
                print(f"DEBUG: ==========================================")
                print(f"DEBUG: CALLING PDF GENERATION FUNCTION NOW!")
                print(f"DEBUG: ==========================================")
                print(f"DEBUG: Calling generate_quiz_pdf_and_upload...")
                pdf_result = generate_quiz_pdf_and_upload(
                    quiz_entry, 
                    patient, 
                    quiz_answers, 
                    enhanced_result_data, 
                    'basic_quiz'
                )
                print(f"DEBUG: generate_quiz_pdf_and_upload returned: {pdf_result}")
                
                if pdf_result['success']:
                    print(f"DEBUG: PDF generated and uploaded successfully: {pdf_result['filename']}")
                    current_app.logger.info(f"PDF generated and uploaded successfully: {pdf_result['filename']}")
                else:
                    print(f"DEBUG: PDF generation failed: {pdf_result.get('error', 'Unknown error')}")
                    current_app.logger.error(f"PDF generation failed: {pdf_result.get('error', 'Unknown error')}")
            except Exception as pdf_error:
                print(f"DEBUG: Exception during PDF generation: {str(pdf_error)}")
                current_app.logger.error(f"Exception during PDF generation: {str(pdf_error)}")
                import traceback
                current_app.logger.error(f"PDF generation traceback: {traceback.format_exc()}")
            
            print(f"DEBUG: PDF generation section completed")
            
            # Save observations to observation_store
            try:
                from flask_app.helpers.quiz_helpers import save_observations_to_store
                # Get observations from the result_data
                observations = []
                if result_data.get('red_flags'):
                    for flag in result_data['red_flags']:
                        observations.append({
                            'observation': flag,
                            'value': 'Yes',
                            'score': 2,
                            'explanation': f'Critical symptom: {flag}',
                            'evidence': f'Critical symptom: {flag}',
                            'confidence': 100,
                            'source': 'quiz-scoring-v1'
                        })
                
                if result_data.get('reasons'):
                    for reason in result_data['reasons']:
                        if reason not in result_data.get('red_flags', []):
                            observations.append({
                                'observation': reason,
                                'value': 'Yes',
                                'score': 1,
                                'explanation': f'Supporting symptom: {reason}',
                                'evidence': f'Supporting symptom: {reason}',
                                'confidence': 100,
                                'source': 'quiz-scoring-v1'
                            })
                
                # Add total score observation
                observations.append({
                    'observation': 'Total Risk Score',
                    'value': result_data['score'],
                    'score': result_data['score'],
                    'explanation': f'Calculated risk score: {result_data["score"]}',
                    'evidence': f'Calculated risk score: {result_data["score"]}',
                    'confidence': 100,
                    'source': 'quiz-scoring-v1'
                })
                
                save_observations_to_store(patient.id, quiz_entry.id, observations, source_type='basic_quiz', section='basic_assessment')
                print(f"DEBUG: Saved {len(observations)} observations to observation_store")
            except Exception as e:
                print(f"DEBUG: Error saving observations to store: {str(e)}")
                # Don't fail the entire request if observation saving fails
                
        except Exception as e:
            print(f"DEBUG: Error storing quiz data: {str(e)}")
            db.session.rollback()
            raise
        
        print(f"DEBUG: Quiz data storage section completed successfully")

        # Generate email templates and update quiz entry
        try:
            # Use clinic_email from request data (already extracted above)
            # clinic_email is already set from the request data
            
            # Generate patient email content (CTA)
            # Create a proper ai_response structure instead of parsing non-existent field
            ai_response_data = {
                'risk_explanation': result_data.get('risk_explanation', ''),
                'recommendations': result_data.get('recommendations', ''),
                'ai_analysis': ai_analysis,
                'ai_narrative': ai_narrative
            }
            
            patient_cta = f"""
            <h2>Your Personalized AI Assessment</h2>
            <p>Thank you for completing the sleep apnea quiz. Here are your results:</p>
            <p>{ai_response_data.get('risk_explanation', '')}</p>
            <h3>Recommendations:</h3>
            <p>{ai_response_data.get('recommendations', '')}</p>
            <div style='margin-top: 30px;'>
                <a href='https://portal.isleepemr.com/booking/create-appointment/?booking=6809ea85e24b0b0ae4bdce75' target='_blank' style='display:inline-block;margin:8px 12px 8px 0;padding:12px 24px;background:#4CAF50;color:#fff;border-radius:6px;text-decoration:none;font-weight:600;'>🏠 Schedule Home Sleep Test</a>
                <a href='/consultation_form?email={patient_email}&dso_id={dso_id}' target='_blank' style='display:inline-block;margin:8px 12px 8px 0;padding:12px 24px;background:#3498db;color:#fff;border-radius:6px;text-decoration:none;font-weight:600;'>👨‍⚕️ Consult with Our Dental Sleep Team</a>
                <a href='/advanced_quiz?email={patient_email}' style='display:inline-block;margin:8px 0;padding:12px 24px;background:#9b59b6;color:#fff;border-radius:6px;text-decoration:none;font-weight:600;'>📋 Complete Detailed Assessment</a>
            </div>
            """
            
            # Generate clinic email template with proper structure
            clinic_msg = None
            if clinic_email:
                clinic_msg = Message(
                    subject="New Sleep Apnea Quiz Submission",
                    recipients=[clinic_email],
                    html=f"""
                    <html>
                    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                        <div style="max-width: 800px; margin: 0 auto; padding: 20px;">
                            <h2 style="color: #2c3e50;">New Sleep Quiz Submission</h2>
                            
                            <!-- Patient Details -->
                            <div style="background-color: #f8f9fa; padding: 20px; border-radius: 10px; margin: 20px 0;">
                                <h3 style="color: #2c3e50; margin-top: 0;">Patient Details</h3>
                                <p><strong>Patient ID:</strong> {patient.id}</p>
                                <p><strong>Name:</strong> {quiz_answers.get('full_name', 'N/A')}</p>
                                <p><strong>Email:</strong> {patient_email}</p>
                                <p><strong>Phone:</strong> {quiz_answers.get('phone', 'N/A')}</p>
                                <p><strong>Address:</strong> {quiz_answers.get('address', 'N/A')}</p>
                                <p><strong>Date of Birth:</strong> {quiz_answers.get('dob', 'N/A')}</p>
                                <p><strong>Gender:</strong> {quiz_answers.get('gender', 'N/A')}</p>
                            </div>
                            
                            <!-- Risk Assessment -->
                            <div style="background-color: #fff; padding: 20px; border-radius: 10px; margin: 20px 0; border-left: 4px solid #3498db;">
                                <h3 style="color: #2c3e50; margin-top: 0;">Risk Assessment</h3>
                                <div style="display: inline-block; padding: 8px 16px; border-radius: 20px; font-weight: bold; margin-bottom: 15px; {get_risk_badge_style(result_data['risk_level'])}">
                                    {result_data['risk_level']} Risk
                                </div>
                                <div style="font-size: 24px; font-weight: bold; color: #2c3e50; margin-bottom: 10px;">
                                    Score: {result_data['score']}/10
                                </div>
                            </div>
                            
                            <!-- Symptoms Analysis -->
                            <div style="background-color: #fff; padding: 20px; border-radius: 10px; margin: 20px 0; border-left: 4px solid #f39c12;">
                                <h3 style="color: #2c3e50; margin-top: 0;">Symptoms Analysis</h3>
                                
                                <!-- Critical Symptoms -->
                                <div style="margin-bottom: 20px;">
                                    <h4 style="color: #e74c3c; margin-bottom: 10px;">🚨 Critical Symptoms</h4>
                                    <ul style="color: #6c757d; font-size: 16px; line-height: 1.6;">
                                        {''.join([f'<li>{flag}</li>' for flag in result_data['red_flags']]) if result_data['red_flags'] else '<li>No critical symptoms identified</li>'}
                                    </ul>
                                </div>
                                
                                <!-- Supporting Symptoms -->
                                <div>
                                    <h4 style="color: #f39c12; margin-bottom: 10px;">⚠️ Supporting Symptoms</h4>
                                    <ul style="color: #6c757d; font-size: 16px; line-height: 1.6;">
                                        {''.join([f'<li>{reason}</li>' for reason in result_data['reasons'] if reason not in ['Witnessed apneas or gasping during sleep', 'Unintentionally falling asleep during the day', 'Trouble staying awake while driving']]) if result_data['reasons'] else '<li>No supporting symptoms identified</li>'}
                                    </ul>
                                </div>
                            </div>
                            
                            <!-- AI Analysis -->
                            <div style="background-color: #fff; padding: 20px; border-radius: 10px; margin: 20px 0; border-left: 4px solid #9b59b6;">
                                <h3 style="color: #2c3e50; margin-top: 0;">AI Analysis</h3>
                                <div style="color: #6c757d; font-size: 16px; line-height: 1.6; font-family: 'Courier New', monospace; background-color: #f8f9fa; padding: 15px; border-radius: 5px; border-left: 3px solid #9b59b6;">
                                    {ai_analysis}
                                </div>
                            </div>
                            
                            <!-- AI Narrative -->
                            <div style="background-color: #fff; padding: 20px; border-radius: 10px; margin: 20px 0; border-left: 4px solid #27ae60;">
                                <h3 style="color: #2c3e50; margin-top: 0;">AI Narrative</h3>
                                <div style="color: #6c757d; font-size: 16px; line-height: 1.6; font-family: 'Georgia', serif; font-style: italic; background-color: #f8f9fa; padding: 15px; border-radius: 5px; border-left: 3px solid #27ae60;">
                                    {ai_narrative}
                                </div>
                            </div>
                        </div>
                    </body>
                    </html>
                    """
                )
            
            # Update quiz entry with CTA
            quiz_entry.cta = patient_cta
            db.session.commit()
            print("DEBUG: Updated quiz entry with CTA")
            
            # Send emails to patient and clinic
            try:
                print(f"DEBUG: Attempting to send emails to patient: {patient_email}, clinic: {clinic_email}")
                
                # Create ai_response_json for the send_emails function
                ai_response_json = json.dumps({
                    'risk_level': result_data['risk_level'],
                    'risk_explanation': result_data['risk_explanation'],
                    'recommendations': result_data['recommendations'],
                    'ai_analysis': ai_analysis,
                    'ai_narrative': ai_narrative
                })
                
                # DSO ID should already be determined from URL parameter
                # The dso_id variable is already set from the request data
                print(f"DEBUG: Using DSO ID {dso_id} from URL parameter")
                print(f"DEBUG: Using DSO ID: {dso_id}")
                
                email_success, email_patient_cta = send_emails(patient_email, clinic_email, ai_response_json, dso_id=dso_id, clinic_id=clinic_id)
                if email_success:
                    print("DEBUG: Emails sent successfully")
                else:
                    print("DEBUG: Email sending failed")
                    
            except Exception as email_error:
                print(f"DEBUG: Error sending emails: {str(email_error)}")
                # Don't fail the whole request if email sending fails
            
            print("DEBUG: About to prepare final JSON response")
            print(f"DEBUG: result_data keys: {list(result_data.keys())}")
            print(f"DEBUG: risk_level: {result_data.get('risk_level')}")
            
            try:
                response_data = {
                    'success': True,
                    'risk_level': result_data['risk_level'],
                    'risk_explanation': risk_messages[result_data['risk_level']]['basic'].format(patient_email=patient_email),
                    'recommendations': result_data['recommendations'],
                    'score': result_data['score'],
                    'red_flags': result_data['red_flags'],
                    'reasoning': result_data['reasons'],
                    'templated_risk_message': risk_messages[result_data['risk_level']]['basic'].format(patient_email=patient_email),
                    'patient_email_content': email_patient_cta if 'email_patient_cta' in locals() else 'Email content generated by send_emails function',
                    'doctor_email_content': clinic_msg.html if clinic_msg else 'No clinic email available',
                    'ai_analysis': ai_analysis,
                    'ai_narrative': ai_narrative,
                    'prompt': result_data.get('prompt', 'No prompt available'),
                    'pdf_generation': pdf_result if 'pdf_result' in locals() else {'success': False, 'error': 'PDF generation not called'}
                }
                print("DEBUG: Response data prepared successfully")
                return jsonify(response_data)
            except Exception as json_error:
                print(f"DEBUG: Error preparing JSON response: {str(json_error)}")
                print(f"DEBUG: Error type: {type(json_error).__name__}")
                raise
            
        except Exception as e:
            print(f"DEBUG: Error in email generation/sending: {str(e)}")
            db.session.rollback()
            raise

    except Exception as e:
        print(f"DEBUG: Unhandled error in analyze_quiz: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'An error occurred while processing your quiz: {str(e)}'
        }), 500

def get_risk_badge_style(risk_level):
    styles = {
        "Low": "background-color: #d4edda; color: #155724;",
        "Moderate": "background-color: #fff3cd; color: #856404;",
        "High": "background-color: #f8d7da; color: #721c24;",
        "Diagnosed – Not Using Treatment": "background-color: #f8d7da; color: #721c24;",
        "Diagnosed – Using & No Symptoms": "background-color: #d4edda; color: #155724;",
        "Diagnosed – Still Symptomatic": "background-color: #fff3cd; color: #856404;"
    }
    return styles.get(risk_level, "background-color: #f8f9fa; color: #2c3e50;")

def get_action_message_by_risk_level(risk_level):
    messages = {
        "Low": "Complete a detailed assessment to monitor your symptoms",
        "Moderate": "Complete a detailed assessment and consider a home sleep test",
        "High": "Schedule a home sleep test and consultation with our sleep expert",
        "Diagnosed – Not Using Treatment": "Let's get you back on track with your treatment",
        "Diagnosed – Using & No Symptoms": "Continue your current treatment and schedule follow-ups as needed",
        "Diagnosed – Still Symptomatic": "Let's adjust your treatment plan for better results"
    }
    return f'<p style="color: #6c757d; margin-bottom: 20px; font-size: 16px;">{messages.get(risk_level, "Take the next step toward better sleep")}</p>'

def generate_email_cta_buttons(risk_level):
    """Generate CTA buttons for email - only show consultation button for advanced quiz"""
    base_button_style = """
        display: inline-block;
        text-decoration: none;
        border-radius: 30px;
        font-size: 18px;
        font-weight: 600;
        margin: 10px;
        padding: 18px 35px;
        text-align: center;
    """
    
    # Consultation button style
    consult_style = f"""
        {base_button_style}
        background: linear-gradient(135deg, #3498db 0%, #2980b9 100%);
        color: white;
        box-shadow: 0 8px 25px rgba(52, 152, 219, 0.3);
    """
    
    # Only show consultation button for all risk levels
    buttons = [
        f"""<a href="#" onclick="alert('Thank You!\\n\\nThank you for participating in the survey. One of our Sleep Experts will contact you shortly.');" style="{consult_style}">
            👨‍⚕️ Schedule a Consult
        </a>"""
    ]
    
    return f"""
        <div style="display: flex; gap: 20px; justify-content: center; flex-wrap: wrap; margin-top: 20px;">
            {' '.join(buttons)}
        </div>
    """

@conversion_quiz_agent.route('/quiz-dashboard')
def quiz_dashboard():
    """
    Renders the quiz dashboard page with clinic-based access control.
    Dentists can only see quiz submissions from patients in their clinics.
    Admins can see all quiz submissions.
    """
    from flask import request
    from datetime import datetime, timedelta
    import json
    from flask_login import current_user
    
    # Get filter parameters
    quiz_type_filter = request.args.get('quiz_type')
    risk_filter = request.args.get('risk_level')
    date_filter = request.args.get('date_range')
    
    # Start with base query
    query = db.session.query(
        ConversionQuiz,
        Patient
    ).join(
        Patient, ConversionQuiz.user_id == Patient.id
    )
    
    # Apply clinic-based access control (mirrors patient_list logic)
    if current_user.is_authenticated:
        # Check if user is admin (can see all)
        if current_user.role != 'admin':
            # Regular dentist - restrict results using clinic + legacy dentist associations
            user_clinic_ids = current_user.get_clinic_ids() if hasattr(current_user, 'get_clinic_ids') else []
            clinic_ids = [cid for cid in user_clinic_ids if cid is not None]

            if clinic_ids:
                clinic_condition = Patient.clinic_id.in_(clinic_ids)
                legacy_condition = and_(
                    Patient.clinic_id.is_(None),
                    Patient.dentist_id.isnot(None),
                    db.exists().where(
                        and_(
                            dentist_clinic_association.c.dentist_id == Patient.dentist_id,
                            dentist_clinic_association.c.clinic_id.in_(clinic_ids)
                        )
                    )
                )

                query = query.filter(or_(clinic_condition, legacy_condition))
            else:
                # No clinic associations found - try DSO fallback
                dentist_dso_ids = current_user.get_dso_ids() if hasattr(current_user, 'get_dso_ids') else []
                if dentist_dso_ids:
                    query = (query
                             .join(Dentist, Patient.dentist_id == Dentist.id, isouter=True)
                             .join(Clinic, Patient.clinic_id == Clinic.id, isouter=True)
                             .filter(
                                 or_(
                                     Clinic.dso_id.in_(dentist_dso_ids),
                                     and_(
                                         Patient.clinic_id.is_(None),
                                         Dentist.DSO == getattr(current_user, 'DSO', None)
                                     )
                                 )
                             ))
                else:
                    # If dentist has no associations, show no results
                    query = query.filter(False)
    else:
        # Not authenticated - show no results
        query = query.filter(False)
    
    # Exclude archived patients (match patient list behavior)
    normalized_status = func.lower(func.trim(Patient.status))
    query = query.filter(
        or_(
            Patient.status.is_(None),
            normalized_status != 'archived'
        )
    )

    # Apply quiz type filter
    if quiz_type_filter:
        query = query.filter(ConversionQuiz.quiz_type == quiz_type_filter)
    
    # Apply date filter
    if date_filter:
        if date_filter == 'today':
            today = datetime.utcnow().date()
            query = query.filter(ConversionQuiz.created_at >= today)
        elif date_filter == 'week':
            week_ago = datetime.utcnow() - timedelta(days=7)
            query = query.filter(ConversionQuiz.created_at >= week_ago)
        elif date_filter == 'month':
            month_ago = datetime.utcnow() - timedelta(days=30)
            query = query.filter(ConversionQuiz.created_at >= month_ago)
    
    # Get all quiz submissions with patient data
    all_submissions = query.order_by(ConversionQuiz.created_at.asc()).all()
    
    # Group submissions by patient email
    patient_groups = {}
    
    for quiz, patient in all_submissions:
        email = quiz.patient_email
        
        if email not in patient_groups:
            patient_groups[email] = {
                'patient_email': email,
                'submissions': [],
                'latest_submission_date': quiz.created_at,
                'cta_summary': None
            }
        
        # Update latest submission date to the most recent
        if quiz.created_at > patient_groups[email]['latest_submission_date']:
            patient_groups[email]['latest_submission_date'] = quiz.created_at
        
        patient_groups[email]['submissions'].append((quiz, patient))
    
    # Process CTA tracking for each patient group
    grouped_submissions = []
    
    for email, group_data in patient_groups.items():
        # Get CTA interactions for this patient email (across all their submissions)
        cta_interactions = CTAInteractionLog.query.filter_by(
            patient_email=email
        ).order_by(CTAInteractionLog.created_at.desc()).all()
        
        # Categorize the CTA actions
        cta_summary = {
            'scheduled_sleep_test': False,
            'requested_consultation': False,
            'completed_advanced': False,
            'email_clicks': 0,
            'web_clicks': 0,
            'total_interactions': len(cta_interactions),
            'latest_action': None,
            'all_actions': []
        }
        
        for cta in cta_interactions:
            # Count email vs web clicks
            if cta.email_source:
                cta_summary['email_clicks'] += 1
            else:
                cta_summary['web_clicks'] += 1
            
            # Track specific actions
            if 'schedule' in cta.cta_type.lower() or 'sleep_test' in cta.cta_type.lower():
                cta_summary['scheduled_sleep_test'] = True
            elif 'consult' in cta.cta_type.lower() or 'consultation' in cta.cta_type.lower():
                cta_summary['requested_consultation'] = True
            elif 'advanced' in cta.cta_type.lower() or 'detailed' in cta.cta_type.lower():
                cta_summary['completed_advanced'] = True
            
            # Store latest action
            if not cta_summary['latest_action']:
                cta_summary['latest_action'] = {
                    'type': cta.cta_type,
                    'date': cta.created_at,
                    'source': 'Email' if cta.email_source else 'Web'
                }
            
            # Store all actions for detailed view
            cta_summary['all_actions'].append({
                'type': cta.cta_type,
                'date': cta.created_at,
                'source': 'Email' if cta.email_source else 'Web',
                'text': cta.cta_text
            })
        
        # Sort submissions within each group (basic first, then advanced)
        group_data['submissions'].sort(key=lambda x: (x[0].quiz_type or 'basic_quiz', x[0].created_at))
        group_data['cta_summary'] = cta_summary
        grouped_submissions.append(group_data)
    
    # Apply risk level filter to grouped submissions
    if risk_filter:
        filtered_groups = []
        for group in grouped_submissions:
            # Check if any submission in this group matches the risk filter
            has_matching_risk = False
            for quiz, patient in group['submissions']:
                if quiz.ai_response:
                    try:
                        ai_data = json.loads(quiz.ai_response)
                        if ai_data.get('risk_level') == risk_filter:
                            has_matching_risk = True
                            break
                    except:
                        continue
            if has_matching_risk:
                filtered_groups.append(group)
        grouped_submissions = filtered_groups
    
    # Sort groups by latest submission date (most recent first)
    grouped_submissions.sort(key=lambda x: x['latest_submission_date'], reverse=True)
    
    return render_template('quiz_dashboard.html', 
                         submissions=grouped_submissions, 
                         grouped_view=True,
                         quiz_type_filter=quiz_type_filter,
                         risk_filter=risk_filter,
                         date_filter=date_filter)

@conversion_quiz_agent.route('/export_csv')
def export_csv():
    """
    Exports quiz submissions to a CSV file with applied filters and clinic-based access control.
    """
    from flask import request
    from datetime import datetime, timedelta
    import json
    from flask_login import current_user
    
    # Get the same filter parameters as the dashboard
    quiz_type_filter = request.args.get('quiz_type')
    risk_filter = request.args.get('risk_level')
    date_filter = request.args.get('date_range')
    
    # Start with base query for ConversionQuiz (not ObservationStore)
    query = db.session.query(
        ConversionQuiz,
        Patient
    ).join(
        Patient, ConversionQuiz.user_id == Patient.id
    )
    
    # Apply clinic-based access control (same as dashboard)
    if current_user.is_authenticated:
        # Check if user is admin (can see all)
        if current_user.role != 'admin':
            user_clinic_ids = current_user.get_clinic_ids() if hasattr(current_user, 'get_clinic_ids') else []
            clinic_ids = [cid for cid in user_clinic_ids if cid is not None]

            if clinic_ids:
                clinic_condition = Patient.clinic_id.in_(clinic_ids)
                legacy_condition = and_(
                    Patient.clinic_id.is_(None),
                    Patient.dentist_id.isnot(None),
                    db.exists().where(
                        and_(
                            dentist_clinic_association.c.dentist_id == Patient.dentist_id,
                            dentist_clinic_association.c.clinic_id.in_(clinic_ids)
                        )
                    )
                )

                query = query.filter(or_(clinic_condition, legacy_condition))
            else:
                dentist_dso_ids = current_user.get_dso_ids() if hasattr(current_user, 'get_dso_ids') else []
                if dentist_dso_ids:
                    query = (query
                             .join(Dentist, Patient.dentist_id == Dentist.id, isouter=True)
                             .join(Clinic, Patient.clinic_id == Clinic.id, isouter=True)
                             .filter(
                                 or_(
                                     Clinic.dso_id.in_(dentist_dso_ids),
                                     and_(
                                         Patient.clinic_id.is_(None),
                                         Dentist.DSO == getattr(current_user, 'DSO', None)
                                     )
                                 )
                             ))
                else:
                    query = query.filter(False)
    else:
        # Not authenticated - show no results
        query = query.filter(False)
    
    # Exclude archived patients (match patient list behavior)
    normalized_status = func.lower(func.trim(Patient.status))
    query = query.filter(
        or_(
            Patient.status.is_(None),
            normalized_status != 'archived'
        )
    )

    # Apply the same filters as the dashboard
    if quiz_type_filter:
        query = query.filter(ConversionQuiz.quiz_type == quiz_type_filter)
    
    if date_filter:
        if date_filter == 'today':
            today = datetime.utcnow().date()
            query = query.filter(ConversionQuiz.created_at >= today)
        elif date_filter == 'week':
            week_ago = datetime.utcnow() - timedelta(days=7)
            query = query.filter(ConversionQuiz.created_at >= week_ago)
        elif date_filter == 'month':
            month_ago = datetime.utcnow() - timedelta(days=30)
            query = query.filter(ConversionQuiz.created_at >= month_ago)
    
    submissions = query.order_by(ConversionQuiz.created_at.desc()).all()
    
    # Apply risk level filter (needs to be done after query since it's in JSON)
    if risk_filter:
        filtered_submissions = []
        for quiz, patient in submissions:
            if quiz.ai_response:
                try:
                    ai_data = json.loads(quiz.ai_response)
                    if ai_data.get('risk_level') == risk_filter:
                        filtered_submissions.append((quiz, patient))
                except:
                    continue
        submissions = filtered_submissions
    
    # Use an in-memory string buffer for the CSV data
    si = io.StringIO()
    cw = csv.writer(si)
    
    # Write CSV headers that match ConversionQuiz model
    headers = [
        'ID', 'Patient Email', 'Clinic Email', 'Quiz Type', 'Risk Level', 
        'Referral Doctor', 'Clinic ID', 'User ID', 'Created At', 'Quiz Input', 'AI Response'
    ]
    cw.writerow(headers)
    
    # Write CSV rows
    for quiz, patient in submissions:
        # Parse AI response to get risk level
        risk_level = ''
        if quiz.ai_response:
            try:
                ai_data = json.loads(quiz.ai_response)
                risk_level = ai_data.get('risk_level', '')
            except:
                pass
        
        cw.writerow([
            quiz.id,
            quiz.patient_email,
            quiz.clinic_email,
            'Basic Quiz' if quiz.quiz_type == 'basic_quiz' else 'Advanced Quiz',
            risk_level,
            quiz.referral_doctor or '',
            quiz.clinic_id or '',
            quiz.user_id or '',
            quiz.created_at.strftime('%Y-%m-%d %H:%M:%S') if quiz.created_at else '',
            quiz.quiz_input or '',
            quiz.ai_response or ''
        ])
    
    # Create a response with the CSV data
    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=quiz_submissions.csv"}
    )

@conversion_quiz_agent.route('/submission/<int:submission_id>')
def submission_details(submission_id):
    from flask_app.models import Clinic
    from flask_login import current_user
    
    # Build query with DSO-based access control
    query = db.session.query(
        ConversionQuiz,
        Patient,
        Clinic
    ).join(
        Patient, ConversionQuiz.user_id == Patient.id
    ).outerjoin(
        Clinic, ConversionQuiz.clinic_id == Clinic.id
    ).filter(
        ConversionQuiz.id == submission_id
    )
    
    # Apply DSO-based access control
    if current_user.is_authenticated:
        if current_user.role != 'admin':
            # Regular dentist - filter by DSO associations
            user_dso_ids = current_user.get_dso_ids()
            if user_dso_ids:
                query = query.filter(Clinic.dso_id.in_(user_dso_ids))
            else:
                # If dentist has no DSO associations, deny access
                query = query.filter(False)
    else:
        # Not authenticated - deny access
        query = query.filter(False)
    
    submission_data = query.first_or_404()
    quiz_answers = json.loads(submission_data[0].quiz_input)
    return render_template('submission_details.html', submission=submission_data, quiz_answers=quiz_answers)

@conversion_quiz_agent.route('/download_quiz_answers/<int:submission_id>')
def download_quiz_answers(submission_id):
    """
    Download quiz answers for a specific submission as a JSON file with DSO access control.
    """
    from flask_login import current_user
    from flask_app.models import Clinic, Patient
    
    # Build query with DSO-based access control
    query = db.session.query(ConversionQuiz).join(
        Patient, ConversionQuiz.user_id == Patient.id
    ).outerjoin(
        Clinic, Patient.clinic_id == Clinic.id
    ).filter(
        ConversionQuiz.id == submission_id
    )
    
    # Apply DSO-based access control
    if current_user.is_authenticated:
        if current_user.role != 'admin':
            # Regular dentist - filter by DSO associations
            user_dso_ids = current_user.get_dso_ids()
            if user_dso_ids:
                query = query.filter(Clinic.dso_id.in_(user_dso_ids))
            else:
                # If dentist has no DSO associations, deny access
                query = query.filter(False)
    else:
        # Not authenticated - deny access
        query = query.filter(False)
    
    submission = query.first_or_404()
    
    try:
        # Parse quiz answers
        quiz_answers = json.loads(submission.quiz_input)
        
        # Parse AI response for additional context
        ai_data = {}
        if submission.ai_response:
            try:
                ai_data = json.loads(submission.ai_response)
            except:
                ai_data = {}
        
        # Create downloadable data structure
        download_data = {
            'submission_info': {
                'id': submission.id,
                'patient_email': submission.patient_email,
                'clinic_email': submission.clinic_email,
                'quiz_type': submission.quiz_type,
                'referral_doctor': submission.referral_doctor,
                'created_at': submission.created_at.isoformat(),
                'clinic_id': submission.clinic_id,
                'user_id': submission.user_id
            },
            'quiz_answers': quiz_answers,
            'ai_analysis': {
                'risk_level': ai_data.get('risk_level', ''),
                'risk_explanation': ai_data.get('risk_explanation', ''),
                'recommendations': ai_data.get('recommendations', '')
            }
        }
        
        # Create JSON response
        json_data = json.dumps(download_data, indent=2, ensure_ascii=False)
        
        # Create response with proper headers for download
        response = Response(
            json_data,
            mimetype="application/json",
            headers={
                "Content-disposition": f"attachment; filename=quiz_answers_{submission_id}_{submission.patient_email}_{submission.created_at.strftime('%Y%m%d')}.json"
            }
        )
        
        return response
        
    except Exception as e:
        current_app.logger.error(f"Error downloading quiz answers for submission {submission_id}: {str(e)}")
        return f"Error downloading quiz answers: {str(e)}", 500

def send_emails(patient_email, clinic_email, ai_response, quiz_type='basic_quiz', dso_id=None, clinic_id=None):
    """Send emails to both patient and clinic with quiz results."""
    current_app.logger.info(f"DEBUG: send_emails called with patient_email={patient_email}, clinic_email={clinic_email}, dso_id={dso_id}, clinic_id={clinic_id}")
    try:
        # Parse the AI response
        response_data = json.loads(ai_response)
        risk_level = response_data.get('risk_level', 'Low')
        
        # Import the email tracking helper
        import sys
        import os
        sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        from email_tracking_helper import create_email_tracking_link
        
        # Base URL for tracking - use environment variable
        import os
        base_url = os.getenv('BASE_URL', 'http://localhost:7000')
        
        # Create tracked CTA links based on risk level
        def create_tracked_button(destination_url, button_text, cta_type, button_style):
            tracking_url = create_email_tracking_link(
                base_url=base_url,
                destination_url=destination_url,
                patient_email=patient_email,
                email_type='quiz_results',
                clinic_id=clinic_id,  # Use the determined clinic_id
                cta_type=cta_type
            )
            return f"""<a href='{tracking_url}' target='_blank' style='{button_style}'>{button_text}</a>"""
        
        # Email-friendly button styles (more visible and consistent)
        button_styles = {
            'test': 'display:inline-block;margin:10px 5px;padding:15px 30px;background:#4CAF50;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600;font-size:16px;border:none;',
            'consult': 'display:inline-block;margin:10px 5px;padding:15px 30px;background:#3498db;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600;font-size:16px;border:none;',
            'phase2': 'display:inline-block;margin:10px 5px;padding:15px 30px;background:#9b59b6;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600;font-size:16px;border:none;'
        }
        
        # Generate CTA buttons based on quiz type
        cta_buttons = []
        
        # Always include the consultation CTA for all quiz types
        cta_buttons = [
            create_tracked_button(
                f"{base_url}/consultation_form?email={patient_email}&dso_id={dso_id}",
                "👨‍⚕️ Consult with Our Dental Sleep Team",
                "email_link_click - consult with dental team",
                button_styles['consult']
            )
        ]
        
        # Get risk badge styling (same as results page)
        risk_badge_styles = {
            "Low": "background-color: #d4edda; color: #155724;",
            "Moderate": "background-color: #fff3cd; color: #856404;", 
            "High": "background-color: #f8d7da; color: #721c24;",
            "Diagnosed – Not Using Treatment": "background-color: #f8d7da; color: #721c24;",
            "Diagnosed – Using & No Symptoms": "background-color: #d4edda; color: #155724;",
            "Diagnosed – Still Symptomatic": "background-color: #fff3cd; color: #856404;"
        }
        risk_badge_style = risk_badge_styles.get(risk_level, "background-color: #f8f9fa; color: #2c3e50;")
        
        # Get the formatted risk message for the email - use the appropriate message type
        message_type = 'advanced' if quiz_type == 'advanced_quiz' else 'basic'
        email_risk_message_html = risk_messages[risk_level][message_type].replace(
            '{patient_email}', patient_email
        ).replace(
            '{dso_id}', str(dso_id)
        )
        
        # Ensure all links are absolute for email
        email_risk_message_html = email_risk_message_html.replace(
            'href="/advanced_quiz', f'href="{base_url}/advanced_quiz'
        ).replace(
            'href="/consultation_form', f'href="{base_url}/consultation_form'
        )
        
        patient_cta = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="text-align: center; margin-bottom: 30px;">
                <h2 style="color: #333;">📋 Your Personalized AI Assessment</h2>
            </div>
            <div style="background: white; padding: 20px; border-radius: 10px; border-left: 4px solid #3498db; margin-bottom: 30px;">
                <div style="display: inline-block; padding: 8px 16px; border-radius: 20px; font-weight: bold; margin-bottom: 15px; {risk_badge_style}">
                    {risk_level} Risk
                </div>
                <p style="font-size: 16px; line-height: 1.6; margin: 20px 0;"><strong>What this means:</strong></p>
                <div style="white-space: pre-line; font-size: 16px; line-height: 1.6;">
                    {email_risk_message_html}
                </div>
            </div>
            <div style="text-align: center; margin-top: 40px; padding: 20px; background: #ffffff; border-radius: 8px;">
                <h3 style="color: #333; margin-bottom: 20px;">Recommended Actions</h3>
                <div style="display: block; text-align: center;">
                    {' '.join(cta_buttons)}
                </div>
            </div>
            <p style="font-size: 14px; color: #777; text-align: center; margin-top: 30px;">
                Questions? Contact our team at <a href=\"mailto:info@vizbriz.com\">info@vizbriz.com</a>
            </p>
        </div>
        """
        
        # Debug: Print the email HTML to see what's being sent
        print(f"DEBUG: Email HTML for {patient_email}:")
        print(f"DEBUG: CTA buttons: {cta_buttons}")
        print(f"DEBUG: Risk message HTML: {email_risk_message_html[:200]}...")
        print(f"DEBUG: Base URL: {base_url}")
        print(f"DEBUG: Quiz type: {quiz_type}")
        print(f"DEBUG: Risk level: {risk_level}")
        print(f"DEBUG: Message type used: {message_type}")
        
        # Create patient email
        email_subject = "Your Comprehensive Sleep Assessment Results" if quiz_type == 'advanced_quiz' else "Your Sleep Apnea Quiz Results"
        patient_msg = Message(
            subject=email_subject,
            recipients=[patient_email],
            html=patient_cta
        )
        
        # Create clinic email (this still contains the risk level for the clinic's reference)
        clinic_msg = Message(
            subject="New Sleep Apnea Quiz Submission",
            recipients=[clinic_email],
            html=f"""
            <h2>New Sleep Apnea Quiz Submission</h2>
            <p>A new patient has completed the sleep apnea quiz:</p>
            <h3>Patient Email: {patient_email}</h3>
            <h3>Risk Assessment: {response_data.get('risk_level', 'Not Available')}</h3>
            <p>{response_data.get('risk_explanation', '')}</p>
            <h3>Recommendations:</h3>
            <p>{response_data.get('recommendations', '')}</p>
            """
        )
        
        # Send emails using Flask-Mail
        current_app.extensions['mail'].send(patient_msg)
        current_app.extensions['mail'].send(clinic_msg)
        
        return True, patient_cta
    except Exception as e:
        current_app.logger.error(f"Error sending emails: {str(e)}")
        return False, None

@conversion_quiz_agent.route('/consultation_form', methods=['GET', 'POST'])
def consultation_form():
    """Handle consultation form for dental team contact."""
    if request.method == 'GET':
        from flask_app.models import DSO
        
        # Show the consultation form
        email = request.args.get('email', '')
        dso_id = request.args.get('dso_id', None, type=int)  # No hardcoded default
        
        # Get DSO information for header display
        dso = DSO.query.get(dso_id)
        if not dso:
            # Fallback to first available DSO
            dso = DSO.query.first()
        
        return render_template('consultation_form.html', patient_email=email, dso=dso)
    
    elif request.method == 'POST':
        # Process the consultation form submission
        try:
            data = request.get_json() if request.is_json else request.form
            
            # Extract form data
            name = data.get('name', '').strip()
            email = data.get('email', '').strip()
            phone = data.get('phone', '').strip()
            comment = data.get('comment', '').strip()
            
            # Validate required fields
            if not all([name, email, phone]):
                return jsonify({'success': False, 'error': 'Name, email, and phone are required'}), 400
            
            # Find if this email belongs to an existing patient
            patient = Patient.query.filter_by(email=email).first()
            patient_id = patient.id if patient else None
            
            # Store consultation request in database
            try:
                consultation_request = ConsultationRequest(
                    name=name,
                    email=email,
                    phone=phone,
                    comment=comment,
                    patient_id=patient_id,
                    status='pending'
                )
                
                db.session.add(consultation_request)
                db.session.commit()
                
                current_app.logger.info(f"Consultation request stored in database with ID: {consultation_request.id}")
                
            except Exception as e:
                current_app.logger.error(f"Failed to store consultation request in database: {str(e)}")
                db.session.rollback()
                return jsonify({'success': False, 'error': 'Failed to save consultation request'}), 500
            
            # Send consultation request email to dental team
            try:
                from flask_mail import Message
                
                consultation_email = Message(
                    subject=f"Consultation Request from {name} (ID: {consultation_request.id})",
                    recipients=['info@vizbriz.com'],
                    html=f"""
                    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                        <h2 style="color: #333;">New Consultation Request</h2>
                        
                        <div style="background: #e3f2fd; padding: 15px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #2196f3;">
                            <p><strong>Request ID:</strong> {consultation_request.id}</p>
                            <p><strong>Submitted:</strong> {consultation_request.submitted_at.strftime('%Y-%m-%d %H:%M:%S')}</p>
                            <p><strong>Status:</strong> {consultation_request.status.title()}</p>
                            {f'<p><strong>Existing Patient ID:</strong> {patient_id}</p>' if patient_id else '<p><strong>New Lead:</strong> Not an existing patient</p>'}
                        </div>
                        
                        <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0;">
                            <h3 style="color: #333; margin-top: 0;">Contact Information</h3>
                            <p><strong>Name:</strong> {name}</p>
                            <p><strong>Email:</strong> <a href="mailto:{email}">{email}</a></p>
                            <p><strong>Phone:</strong> <a href="tel:{phone}">{phone}</a></p>
                        </div>
                        
                        <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0;">
                            <h3 style="color: #333; margin-top: 0;">Message/Comments</h3>
                            <p style="background: white; padding: 15px; border-radius: 5px; border-left: 3px solid #3498db;">
                                {comment if comment else 'No additional comments provided.'}
                            </p>
                        </div>
                        
                        <p style="color: #666; font-size: 14px;">
                            This consultation request was submitted through the sleep assessment email follow-up and has been saved to the database for tracking.
                        </p>
                    </div>
                    """
                )
                
                current_app.extensions['mail'].send(consultation_email)
                current_app.logger.info(f"Consultation request sent for {name} ({email})")
                
            except Exception as e:
                current_app.logger.error(f"Failed to send consultation email: {str(e)}")
                # Don't fail the request if email fails
            
            # Track the consultation form submission
            try:
                import requests
                import json
                
                base_url = os.getenv('BASE_URL', 'http://localhost:7000')
                tracking_data = {
                    'patient_email': email,
                    'cta_type': 'consultation_form_submitted',
                    'page_type': 'consultation_form',
                    'email_source': 'email'  # Since this came from email CTA
                }
                
                # Post to our own tracking endpoint
                requests.post(f"{base_url}/api/tracking/track-cta-click", 
                            json=tracking_data, 
                            timeout=5)
                
            except Exception as e:
                current_app.logger.warning(f"Failed to track consultation form submission: {str(e)}")
            
            return jsonify({
                'success': True, 
                'message': 'Thank you! Your consultation request has been submitted. Our dental sleep team will contact you soon.'
            })
            
        except Exception as e:
            current_app.logger.error(f"Error processing consultation form: {str(e)}")
            return jsonify({'success': False, 'error': 'An error occurred while processing your request'}), 500

@conversion_quiz_agent.route('/api/patient-lookup', methods=['GET'])
def api_patient_lookup():
    """Look up patient information by email for consultation forms."""
    try:
        email = request.args.get('email')
        if not email:
            return jsonify({'success': False, 'error': 'Email parameter required'}), 400
        
        # Find patient by email
        patient = Patient.query.filter_by(email=email).first()
        
        if not patient:
            return jsonify({'success': False, 'error': 'Patient not found'}), 404
        
        # Return patient information
        patient_data = {
            'name': patient.name,
            'email': patient.email,
            'phone': patient.phone,
            'gender': patient.gender
        }
        
        return jsonify({'success': True, 'patient': patient_data})
        
    except Exception as e:
        current_app.logger.error(f"Error in patient lookup: {str(e)}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@conversion_quiz_agent.route('/api/consultation-test', methods=['GET', 'POST'])
def api_consultation_test():
    """Test route to verify routing is working"""
    return jsonify({
        'success': True,
        'message': 'Test route working!',
        'method': request.method,
        'endpoint': '/api/consultation-test'
    })

@conversion_quiz_agent.route('/test-consultation-direct')
def test_consultation_direct():
    """Direct URL test for consultation API - you can call this in browser"""
    try:
        # Test data
        test_data = {
            'email': 'directtest@example.com',
            'name': 'Direct Test User',
            'phone': '555-123-4567',
            'comment': 'Test from direct URL call'
        }
        
        # Simulate the API call logic
        email = test_data.get('email', '').strip()
        if not email:
            return jsonify({'success': False, 'error': 'Email is required'}), 400
        
        # Look up patient in database
        patient = Patient.query.filter_by(email=email).first()
        
        if patient:
            # Patient found - use their details
            name = patient.name or 'Patient'
            phone = patient.phone or 'Not provided'
            patient_id = patient.id
            result_msg = f"Found existing patient {patient_id} - {name} ({email})"
        else:
            # Patient not found - use form data or defaults
            name = test_data.get('name', 'Quiz Participant').strip() or 'Quiz Participant'
            phone = test_data.get('phone', 'Not provided').strip() or 'Not provided'
            patient_id = None
            result_msg = f"New patient - {name} ({email})"
        
        # Get comment
        comment = test_data.get('comment', f'Consultation request from {name}').strip()
        
        # Check if table exists and create record
        try:
            # First test if table exists by trying a simple query
            existing_count = ConsultationRequest.query.count()
            table_status = f"Table exists with {existing_count} existing records"
            
            consultation_request = ConsultationRequest(
                name=name,
                email=email,
                phone=phone,
                comment=comment,
                patient_id=patient_id,
                status='pending'
            )
            
            db.session.add(consultation_request)
            db.session.commit()
            
            db_status = f"Saved to database with ID {consultation_request.id}"
            
        except Exception as db_error:
            db.session.rollback()
            return jsonify({
                'success': False, 
                'error': f'Database error: {str(db_error)}',
                'table_status': 'Failed to query table',
                'result_msg': result_msg
            }), 500
        
        return jsonify({
            'success': True,
            'message': 'Direct URL test completed successfully!',
            'consultation_id': consultation_request.id,
            'table_status': table_status,
            'db_status': db_status,
            'result_msg': result_msg,
            'test_data': test_data
        })
        
    except Exception as e:
        return jsonify({
            'success': False, 
            'error': f'Test failed: {str(e)}',
            'endpoint': '/test-consultation-direct'
        }), 500

@conversion_quiz_agent.route('/api/consultation', methods=['POST'])
def api_consultation():
    """Simple API: email → lookup patient → save consultation request."""
    try:
        data = request.get_json() if request.is_json else request.form
        
        # Get email (required)
        email = data.get('email', '').strip()
        if not email:
            return jsonify({'success': False, 'error': 'Email is required'}), 400
        
        # Look up patient in database
        patient = Patient.query.filter_by(email=email).first()
        
        if patient:
            # Patient found - use their details
            name = patient.name or 'Patient'
            phone = patient.phone or 'Not provided'
            patient_id = patient.id
            print(f"CONSULTATION: Found patient {patient_id} - {name} ({email})")
        else:
            # Patient not found - use form data or defaults
            name = data.get('name', 'Quiz Participant').strip() or 'Quiz Participant'
            phone = data.get('phone', 'Not provided').strip() or 'Not provided'
            patient_id = None
            print(f"CONSULTATION: New patient - {name} ({email})")
        
        # Get comment
        comment = data.get('comment', f'Consultation request from {name}').strip()
        
        # Check if table exists and create record
        try:
            # First test if table exists by trying a simple query
            existing_count = ConsultationRequest.query.count()
            print(f"CONSULTATION: Table exists with {existing_count} existing records")
            
            consultation_request = ConsultationRequest(
                name=name,
                email=email,
                phone=phone,
                comment=comment,
                patient_id=patient_id,
                status='pending'
            )
            
            db.session.add(consultation_request)
            db.session.commit()
            
            print(f"CONSULTATION: Saved to database with ID {consultation_request.id}")
            
        except Exception as db_error:
            print(f"CONSULTATION DB ERROR: {str(db_error)}")
            db.session.rollback()
            return jsonify({'success': False, 'error': f'Database error: {str(db_error)}'}), 500
        
        # Send email to dental team
        try:
            from flask_mail import Message
            email_msg = Message(
                subject=f"Consultation Request from {name} (ID: {consultation_request.id})",
                recipients=['info@vizbriz.com'],
                html=f"""
                <h2>New Consultation Request</h2>
                <p><strong>ID:</strong> {consultation_request.id}</p>
                <p><strong>Name:</strong> {name}</p>
                <p><strong>Email:</strong> {email}</p>
                <p><strong>Phone:</strong> {phone}</p>
                <p><strong>Patient ID:</strong> {patient_id or 'New lead'}</p>
                <p><strong>Comment:</strong> {comment}</p>
                <p><strong>Submitted:</strong> {consultation_request.submitted_at}</p>
                """
            )
            current_app.extensions['mail'].send(email_msg)
            print(f"CONSULTATION: Email sent for {name}")
        except Exception as e:
            print(f"CONSULTATION: Email failed - {str(e)}")
        
        return jsonify({
            'success': True,
            'message': 'Thank you! Your consultation request has been submitted. Our dental sleep team will contact you soon.',
            'consultation_id': consultation_request.id
        })
        
    except Exception as e:
        print(f"CONSULTATION ERROR: {str(e)}")
        return jsonify({'success': False, 'error': 'Failed to process consultation request'}), 500

@conversion_quiz_agent.route('/advanced_quiz', methods=['GET'])
def show_advanced_quiz():
    from flask_app.models import DSO, Clinic, Patient
    dso_id = request.args.get('dso_id', None, type=int)
    testing = request.args.get('testing', 0, type=int)
    patient_email = request.args.get('email', '')

    # If dso_id is provided, use it; otherwise try to derive from patient's clinic
    if dso_id:
        dso = DSO.query.get(dso_id)
    elif patient_email:
        # Try to find patient and derive DSO from their clinic
        patient = Patient.query.filter_by(email=patient_email).first()
        if patient and patient.clinic_id:
            clinic = Clinic.query.get(patient.clinic_id)
            if clinic:
                dso = clinic.dso_info
            else:
                dso = DSO.query.first()
        else:
            dso = DSO.query.first()
    else:
        dso = DSO.query.first()
    
    clinics = Clinic.query.filter_by(dso_id=dso.id, status='active').all() if dso else []
    default_clinic_id = clinics[0].id if clinics else None

    default_answers = None
    if testing:
        default_answers = {
            'sleep_pattern': 'fragmented',
            'insomnia': 'yes',
            'restless_legs': 'yes',
            'medications': 'yes',
            'alcohol': 'yes',
            'caffeine': 'yes',
            'shift_work': 'yes',
            'naps': 'yes',
            'epworth_score': 15,
            'other_conditions': 'hypertension',
        }

    return render_template('advanced_quiz.html', dso=dso, clinics=clinics, default_clinic_id=default_clinic_id, default_answers=default_answers, patient_email=patient_email)

@conversion_quiz_agent.route('/analyze_quiz_part_b', methods=['POST'])
def analyze_quiz_part_b():
    """
    Handles advanced (phase 2) assessment submissions. Updates ConversionQuiz and patient records.
    Expects JSON: { 'answers': {...}, 'patient_email': '...', 'cta': '...' }
    """
    from flask_app.helpers.quiz_helpers import store_quiz_data, evaluate_phase_2, get_phase_2_risk_message, get_phase_2_cta_buttons
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No JSON data received'}), 400
        
        part_b_answers = data.get('answers', {})
        patient_email = data.get('patient_email')
        cta = data.get('cta', '')
        
        if not patient_email:
            return jsonify({'success': False, 'error': 'Missing patient_email'}), 400
        
        # Check if this is a standalone advanced quiz (no basic quiz required)
        standalone_mode = data.get('standalone', False)
        
        if standalone_mode:
            # For standalone mode, create a minimal patient record and use default Part A answers
            patient = Patient.query.filter_by(email=patient_email).first()
            if not patient:
                # Create a basic patient record
                print(f"DEBUG: Creating patient in analyze_quiz_part_b for email: {patient_email}")
                print(f"DEBUG: clinic_id from part_b_answers: {part_b_answers.get('clinic_id')}")
                
                # Find a dentist for the clinic if clinic_id is provided
                dentist_id = None
                clinic_id = part_b_answers.get('clinic_id')
                if clinic_id:
                    from flask_app.models import Clinic, Dentist
                    clinic = Clinic.query.get(clinic_id)
                    if clinic:
                        dentists = clinic.dentists.all()
                        if dentists:
                            dentist_id = dentists[0].id
                            print(f"DEBUG: Found dentist {dentist_id} for clinic {clinic_id}")
                        else:
                            print(f"DEBUG: No dentists found for clinic {clinic_id}")
                    else:
                        print(f"DEBUG: Clinic {clinic_id} not found")
                
                # Fallback to any dentist if none found
                if dentist_id is None:
                    fallback_dentist = Dentist.query.first()
                    if fallback_dentist:
                        dentist_id = fallback_dentist.id
                        print(f"DEBUG: Using fallback dentist {dentist_id}")
                
                print(f"DEBUG: Creating patient with dentist_id={dentist_id}, clinic_id={clinic_id}")
                patient = Patient(
                    email=patient_email,
                    name=part_b_answers.get('full_name', 'Advanced Quiz Participant'),
                    phone=part_b_answers.get('phone', ''),
                    address=part_b_answers.get('address', ''),
                    gender=part_b_answers.get('gender', ''),
                    dentist_id=dentist_id,
                    clinic_id=clinic_id
                )
                print(f"DEBUG: Patient object created, dentist_id={patient.dentist_id}, clinic_id={patient.clinic_id}")
                db.session.add(patient)
                db.session.commit()
                print(f"DEBUG: Patient committed to database with ID: {patient.id}")
                print(f"DEBUG: Final patient dentist_id after commit: {patient.dentist_id}")
                current_app.logger.info(f"Created new patient for standalone advanced quiz: {patient.id}")
            
            # Use default Part A answers for standalone mode
            part_a_answers = {
                'snoring': 'yes',
                'tiredness': 'yes', 
                'observed_apnea': 'no',
                'daytime_sleepiness': 'yes',
                'driving_fatigue': 'no',
                'bruxism': 'no',
                'weight': 'yes',
                'diagnosed': 'no',
                'using_treatment': 'no'
            }
            basic_quiz = None
        else:
            # Standard mode - require existing patient and basic quiz
            patient = Patient.query.filter_by(email=patient_email).first()
            if not patient:
                current_app.logger.error(f"Patient not found for email: {patient_email}")
                return jsonify({'success': False, 'error': 'Patient not found. Please complete the basic assessment first.'}), 400
            
            current_app.logger.info(f"Found patient: {patient.id} for email: {patient_email}")
            
            # Find the most recent basic quiz for this patient
            basic_quiz = ConversionQuiz.query.filter_by(
                user_id=patient.id,
                quiz_type='basic_quiz'
            ).order_by(ConversionQuiz.created_at.desc()).first()
            
            if not basic_quiz:
                # Check if there are any quizzes for this patient
                all_quizzes = ConversionQuiz.query.filter_by(user_id=patient.id).all()
                current_app.logger.error(f"No basic quiz found for patient {patient.id}. Available quizzes: {[(q.id, q.quiz_type) for q in all_quizzes]}")
                return jsonify({'success': False, 'error': 'Basic assessment not found. Please complete the basic assessment first.'}), 400
            
            # Parse Part A answers from the basic quiz
            part_a_answers = json.loads(basic_quiz.quiz_input)
        
        if basic_quiz:
            current_app.logger.info(f"Found basic quiz: {basic_quiz.id} for patient {patient.id}")
            # Parse Part A answers from the basic quiz
            part_a_answers = json.loads(basic_quiz.quiz_input)
        else:
            current_app.logger.info(f"Using standalone mode for patient {patient.id}")
            # part_a_answers is already set in standalone mode above
        
        # Evaluate Phase 2 using combined scoring
        try:
            total_score, risk_level, risk_explanation, observations, snoring_triggered, red_flags = evaluate_phase_2(part_a_answers, part_b_answers)
            current_app.logger.info(f"Phase 2 evaluation completed: risk_level={risk_level}, total_score={total_score}")
        except Exception as e:
            current_app.logger.error(f"Error in evaluate_phase_2: {str(e)}")
            current_app.logger.error(f"part_a_answers: {part_a_answers}")
            current_app.logger.error(f"part_b_answers: {part_b_answers}")
            return jsonify({'success': False, 'error': f'Error evaluating assessment: {str(e)}'}), 500
        
        # Generate risk message and CTA buttons
        try:
            risk_title, risk_message = get_phase_2_risk_message(risk_level, snoring_triggered)
            cta_buttons = get_phase_2_cta_buttons(risk_level)
            current_app.logger.info(f"Risk message and CTA buttons generated successfully")
        except Exception as e:
            current_app.logger.error(f"Error generating risk message or CTA buttons: {str(e)}")
            return jsonify({'success': False, 'error': f'Error generating results: {str(e)}'}), 500
        
        # Generate AI analysis for advanced assessment
        try:
            current_app.logger.info("Starting AI analysis generation for advanced assessment")
            
            # Use the same API key configuration as in __init__.py
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY is not set")
            
            # Initialize OpenAI client
            llm = ChatOpenAI(
                temperature=0.1,
                model_name="gpt-3.5-turbo",
                openai_api_key=api_key
            )
            
            # Create AI analysis prompt for advanced assessment
            ai_prompt = PromptTemplate(
                input_variables=["part_a_answers", "part_b_answers", "risk_level", "total_score", "red_flags", "observations"],
                template="""
                You are a clinical sleep apnea expert. Analyze the following comprehensive sleep assessment data from both basic and advanced evaluations and provide a detailed clinical analysis.
                
                BASIC ASSESSMENT ANSWERS: {part_a_answers}
                ADVANCED ASSESSMENT ANSWERS: {part_b_answers}
                COMBINED RISK LEVEL: {risk_level}
                TOTAL COMBINED SCORE: {total_score}
                CRITICAL RED FLAGS: {red_flags}
                DETAILED OBSERVATIONS: {observations}
                
                Provide a comprehensive clinical analysis including:
                1. Clinical interpretation of the combined symptoms from both assessments
                2. Risk factors identified from the comprehensive evaluation
                3. Potential differential diagnoses to consider
                4. Clinical recommendations for next steps based on the advanced assessment
                5. Specific insights from the detailed lifestyle and TMJ/bruxism evaluation
                
                Keep it professional and medical in tone. Be specific about the clinical implications of the combined assessment.
                """
            )
            
            # Prepare the input data
            prompt_input = {
                "part_a_answers": json.dumps(part_a_answers, indent=2),
                "part_b_answers": json.dumps(part_b_answers, indent=2),
                "risk_level": risk_level,
                "total_score": total_score,
                "red_flags": ", ".join(red_flags) if red_flags else "None identified",
                "observations": json.dumps(observations, indent=2)
            }
            
            # Save the actual prompt string for display
            ai_prompt_string = ai_prompt.format(**prompt_input)
            
            # Generate AI analysis using new RunnableSequence pattern
            chain = ai_prompt | llm | StrOutputParser()
            ai_analysis = chain.invoke(prompt_input)
            
            current_app.logger.info(f"AI analysis generated successfully for advanced assessment: {ai_analysis[:100]}...")
            
        except Exception as e:
            current_app.logger.error(f"AI analysis failed for advanced assessment with error: {str(e)}")
            ai_analysis = f"AI analysis unavailable for advanced assessment - Error: {str(e)}"
        
        # Generate AI narrative for advanced assessment
        try:
            # Create AI narrative prompt for advanced assessment
            narrative_prompt = PromptTemplate(
                input_variables=["part_a_answers", "part_b_answers", "risk_level", "total_score", "red_flags", "observations", "ai_analysis"],
                template="""
                You are a clinical sleep apnea expert. Write a simple, professional narrative that encourages the patient to take action based on their comprehensive sleep assessment results.
                
                BASIC ASSESSMENT ANSWERS: {part_a_answers}
                ADVANCED ASSESSMENT ANSWERS: {part_b_answers}
                COMBINED RISK LEVEL: {risk_level}
                TOTAL COMBINED SCORE: {total_score}
                CRITICAL RED FLAGS: {red_flags}
                DETAILED OBSERVATIONS: {observations}
                AI ANALYSIS: {ai_analysis}
                
                Write a concise clinical narrative (2-3 sentences) that:
                
                For HIGH RISK: Emphasize urgency and immediate action needed based on comprehensive evaluation
                For MODERATE RISK: Highlight the importance of early intervention with specific insights from advanced assessment
                For LOW RISK: Focus on monitoring and preventive measures with lifestyle recommendations
                For DIAGNOSED cases: Focus on treatment compliance and optimization with advanced insights
                
                Keep it simple, professional, and action-oriented. The goal is to motivate the patient to take the next step toward OSA treatment based on the comprehensive assessment.
                """
            )
            
            # Generate AI narrative using new RunnableSequence pattern
            narrative_chain = narrative_prompt | llm | StrOutputParser()
            ai_narrative = narrative_chain.invoke({
                "part_a_answers": json.dumps(part_a_answers, indent=2),
                "part_b_answers": json.dumps(part_b_answers, indent=2),
                "risk_level": risk_level,
                "total_score": total_score,
                "red_flags": ", ".join(red_flags) if red_flags else "None identified",
                "observations": json.dumps(observations, indent=2),
                "ai_analysis": ai_analysis
            })
            
            current_app.logger.info(f"AI narrative generated successfully for advanced assessment: {ai_narrative[:100]}...")
            
        except Exception as e:
            current_app.logger.error(f"AI narrative failed for advanced assessment with error: {str(e)}")
            ai_narrative = risk_message  # Fallback to templated response

        # Create AI response with all the information
        ai_response = {
            'risk_level': risk_level,
            'total_score': total_score,
            'risk_explanation': risk_explanation,
            'risk_title': risk_title,
            'risk_message': risk_message,
            'cta_buttons': cta_buttons,
            'observations': observations,
            'red_flags': red_flags,
            'snoring_triggered': snoring_triggered,
            'part_a_score': basic_quiz.ai_response if basic_quiz.ai_response else '{}',
            'part_b_score': total_score,
            'ai_analysis': ai_analysis,
            'ai_narrative': ai_narrative
        }
        
        # Use store_quiz_data to update ConversionQuiz and patient
        user_id = None
        try:
            # Try to get or create patient with clinic information
            from flask_app.helpers.quiz_helpers import get_or_create_patient
            # Get clinic_id from the patient's basic quiz
            clinic_id = basic_quiz.clinic_id if basic_quiz else None
            user_id = get_or_create_patient(patient_email, clinic_id=clinic_id)
        except Exception as e:
            current_app.logger.error(f"Error getting/creating patient: {str(e)}")
            return jsonify({'success': False, 'error': 'Failed to get or create patient'}), 500
        
        try:
            # Look up clinic email from DSO ID instead of using hardcoded value
            dso_id = data.get('dso_id', None)
            from flask_app.helpers.quiz_helpers import get_clinic_email_from_dso_id
            clinic_email = get_clinic_email_from_dso_id(dso_id)
            
            # Get clinic_id and referral_doctor from the patient's basic quiz or form data
            clinic_id = basic_quiz.clinic_id if basic_quiz else part_b_answers.get('clinic_id')
            referral_doctor = basic_quiz.referral_doctor if basic_quiz else part_b_answers.get('doctor_referral')
            quiz_id = store_quiz_data(user_id, part_b_answers, cta, clinic_email, patient_email, json.dumps(ai_response), quiz_type='advanced_quiz', clinic_id=clinic_id, referral_doctor=referral_doctor)
            
            # Get the quiz entry for PDF generation
            quiz_entry = ConversionQuiz.query.get(quiz_id)
            if quiz_entry:
                print(f"DEBUG: ==========================================")
                print(f"DEBUG: ADVANCED QUIZ - ABOUT TO START PDF GENERATION!")
                print(f"DEBUG: ==========================================")
                print(f"DEBUG: Advanced quiz - Starting PDF generation for quiz ID: {quiz_id}")
                print(f"DEBUG: Advanced quiz - About to call generate_quiz_pdf_and_upload function")
                try:
                    print(f"DEBUG: ==========================================")
                    print(f"DEBUG: ADVANCED QUIZ - CALLING PDF GENERATION FUNCTION NOW!")
                    print(f"DEBUG: ==========================================")
                    print(f"DEBUG: Advanced quiz - Calling generate_quiz_pdf_and_upload...")
                    # Generate PDF and upload to S3
                    pdf_result = generate_quiz_pdf_and_upload(
                        quiz_entry, 
                        patient, 
                        part_b_answers, 
                        ai_response, 
                        'advanced_quiz'
                    )
                    
                    print(f"DEBUG: Advanced quiz - generate_quiz_pdf_and_upload returned: {pdf_result}")
                    
                    if pdf_result['success']:
                        print(f"DEBUG: Advanced quiz - PDF generated and uploaded successfully: {pdf_result['filename']}")
                        current_app.logger.info(f"Advanced quiz PDF generated and uploaded successfully: {pdf_result['filename']}")
                    else:
                        print(f"DEBUG: Advanced quiz - PDF generation failed: {pdf_result.get('error', 'Unknown error')}")
                        current_app.logger.error(f"Advanced quiz PDF generation failed: {pdf_result.get('error', 'Unknown error')}")
                        
                except Exception as pdf_error:
                    print(f"DEBUG: Advanced quiz - Exception during PDF generation: {str(pdf_error)}")
                    current_app.logger.error(f"Advanced quiz - Exception during PDF generation: {str(pdf_error)}")
                    import traceback
                    current_app.logger.error(f"Advanced quiz - PDF generation traceback: {traceback.format_exc()}")
            
            # Save observations to observation_store
            from flask_app.helpers.quiz_helpers import save_observations_to_store
            save_observations_to_store(patient.id, quiz_id, observations, source_type='advanced_quiz', section='advanced_assessment')
            
        except Exception as e:
            current_app.logger.error(f"Error storing advanced quiz data: {str(e)}")
            return jsonify({'success': False, 'error': 'Failed to store quiz data'}), 500
        
        # Email content will be generated by the unified send_emails function
        
        # Send emails to patient and clinic for advanced assessment
        try:
            current_app.logger.info(f"Attempting to send advanced quiz emails to patient: {patient_email}, clinic: {clinic_email}")
            
            # DSO ID should already be determined from URL parameter
            # The dso_id variable is already set from the request data
            current_app.logger.info(f"Using DSO ID {dso_id} from URL parameter")
            current_app.logger.info(f"Using DSO ID: {dso_id}")
            
            # Create ai_response_json for the send_emails function
            # Use the advanced risk message from our risk_messages structure
            try:
                advanced_risk_message = risk_messages[risk_level]['advanced'].format(patient_email=patient_email)
                current_app.logger.info(f"Advanced risk message formatted successfully for risk_level: {risk_level}")
            except Exception as format_error:
                current_app.logger.error(f"Error formatting advanced risk message: {str(format_error)}")
                # Fallback to basic message if advanced fails
                advanced_risk_message = risk_messages[risk_level]['basic'].format(patient_email=patient_email)
                current_app.logger.info("Using basic message as fallback")
            
            ai_response_json = json.dumps({
                'risk_level': risk_level,
                'risk_explanation': advanced_risk_message,
                'templated_risk_message': advanced_risk_message,
                'recommendations': risk_message,
                'ai_analysis': ai_analysis,
                'ai_narrative': ai_narrative
            })
            
            current_app.logger.info(f"About to call send_emails with: patient_email={patient_email}, clinic_email={clinic_email}, quiz_type=advanced_quiz, dso_id={dso_id}")
            current_app.logger.info(f"AI response JSON length: {len(ai_response_json)}")
            
            # Check if Flask-Mail extension is available
            try:
                mail_extension = current_app.extensions.get('mail')
                if mail_extension:
                    current_app.logger.info("Flask-Mail extension found")
                else:
                    current_app.logger.error("Flask-Mail extension not found!")
                    return jsonify({'success': False, 'error': 'Email service not configured'}), 500
            except Exception as ext_error:
                current_app.logger.error(f"Error checking Flask-Mail extension: {str(ext_error)}")
            
            email_success, email_patient_cta = send_emails(patient_email, clinic_email, ai_response_json, quiz_type='advanced_quiz', dso_id=dso_id, clinic_id=clinic_id)
            if email_success:
                current_app.logger.info("Advanced quiz emails sent successfully")
            else:
                current_app.logger.warning("Advanced quiz email sending failed")
                
        except Exception as email_error:
            current_app.logger.error(f"Error sending advanced quiz emails: {str(email_error)}")
            current_app.logger.error(f"Error type: {type(email_error).__name__}")
            import traceback
            current_app.logger.error(f"Traceback: {traceback.format_exc()}")
            # Don't fail the whole request if email sending fails
        
        return jsonify({
            'success': True, 
            'quiz_id': quiz_id,
            'risk_level': risk_level,
            'total_score': total_score,
            'risk_title': risk_title,
            'risk_message': risk_message,
            'cta_buttons': cta_buttons,
            'snoring_triggered': snoring_triggered,
            'red_flags': red_flags,
            'supporting_symptoms': [obs.get('name', obs.get('observation', 'Unknown')) for obs in observations if obs.get('score', 0) > 0],
            'ai_analysis': ai_analysis,
            'ai_narrative': ai_narrative,
            'ai_prompt': ai_prompt_string,
            'patient_email_content': email_patient_cta if 'email_patient_cta' in locals() else 'Email content generated by send_emails function',
            'doctor_email_content': 'Email content generated by send_emails function'
        })

    except Exception as e:
        print(f"DEBUG: Unhandled error in analyze_quiz_part_b: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@conversion_quiz_agent.route('/funnel-analytics')
@login_required
def funnel_analytics():
    """
    Comprehensive funnel analytics based purely on tracking data.
    Uses page_view_log and cta_interaction_log tables for accurate funnel analysis.
    Implements DSO-based access control - dentists can only see data from their associated clinics.
    """
    from flask_app.models import PageViewLog, CTAInteractionLog, Clinic
    from sqlalchemy import func, and_
    from flask_login import current_user
    
    # Get date range filters if provided
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    clinic_id = request.args.get('clinic_id')
    
    # Debug: Log what filters are being applied
    current_app.logger.info(f"DEBUG: Funnel analytics filters - clinic_id: '{clinic_id}' (type: {type(clinic_id)}), date_from: {date_from}, date_to: {date_to}")
    
    # Base queries
    page_view_query = PageViewLog.query
    cta_query = CTAInteractionLog.query
    
    # Apply DSO-based access control
    if current_user.role == 'admin':
        # Admin can see all data
        current_app.logger.info("DEBUG: Admin user - showing all data")
    else:
        # Regular dentist - filter by DSO associations
        user_dso_ids = current_user.get_dso_ids()
        if user_dso_ids:
            # Get clinics associated with user's DSOs
            user_clinic_ids = [clinic.id for clinic in Clinic.query.filter(Clinic.dso_id.in_(user_dso_ids)).all()]
            current_app.logger.info(f"DEBUG: User DSO IDs: {user_dso_ids}, User clinic IDs: {user_clinic_ids}")
            
            # Filter queries to only show data from user's clinics
            page_view_query = page_view_query.filter(PageViewLog.clinic_id.in_(user_clinic_ids))
            cta_query = cta_query.filter(CTAInteractionLog.clinic_id.in_(user_clinic_ids))
            
            # If a specific clinic is selected, verify it's accessible to the user
            if clinic_id and clinic_id.strip():
                if int(clinic_id) not in user_clinic_ids:
                    current_app.logger.warning(f"DEBUG: User attempted to access clinic {clinic_id} which is not in their allowed clinics {user_clinic_ids}")
                    flash('You do not have access to the selected clinic.', 'error')
                    clinic_id = None  # Reset to show all user's clinics
        else:
            # If dentist has no DSO associations, show no data
            current_app.logger.info("DEBUG: User has no DSO associations - showing no data")
            page_view_query = page_view_query.filter(False)  # This will return empty results
            cta_query = cta_query.filter(False)  # This will return empty results
    
    # Debug: Check total data before filtering
    total_page_views_before = page_view_query.count()
    total_cta_before = cta_query.count()
    current_app.logger.info(f"DEBUG: Total data before filtering - page_views: {total_page_views_before}, cta: {total_cta_before}")
    
    # Apply additional filters
    if clinic_id and clinic_id.strip():  # Only filter if clinic_id is not empty or just whitespace
        page_view_query = page_view_query.filter_by(clinic_id=clinic_id)
        cta_query = cta_query.filter_by(clinic_id=clinic_id)
        current_app.logger.info(f"DEBUG: Applied clinic_id filter: '{clinic_id}'")
    else:
        current_app.logger.info("DEBUG: No clinic_id filter applied - showing all accessible data")
    
    if date_from:
        from datetime import datetime
        date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
        page_view_query = page_view_query.filter(PageViewLog.created_at >= date_from_obj)
        cta_query = cta_query.filter(CTAInteractionLog.created_at >= date_from_obj)
        current_app.logger.info(f"DEBUG: Applied date_from filter: {date_from}")
    
    if date_to:
        from datetime import datetime
        date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
        page_view_query = page_view_query.filter(PageViewLog.created_at <= date_to_obj)
        cta_query = cta_query.filter(CTAInteractionLog.created_at <= date_to_obj)
        current_app.logger.info(f"DEBUG: Applied date_to filter: {date_to}")

    # Debug: Check total data after filtering
    total_page_views_after = page_view_query.count()
    total_cta_after = cta_query.count()
    current_app.logger.info(f"DEBUG: Total data after filtering - page_views: {total_page_views_after}, cta: {total_cta_after}")
    
    # Debug: Check what clinic_ids exist in the data
    clinic_ids_in_page_views = [row[0] for row in page_view_query.with_entities(PageViewLog.clinic_id).distinct().all()]
    clinic_ids_in_cta = [row[0] for row in cta_query.with_entities(CTAInteractionLog.clinic_id).distinct().all()]
    current_app.logger.info(f"DEBUG: Available clinic_ids in page_views: {clinic_ids_in_page_views}")
    current_app.logger.info(f"DEBUG: Available clinic_ids in cta: {clinic_ids_in_cta}")
    
    # Debug: Check if there are any NULL clinic_ids
    null_clinic_page_views = page_view_query.filter(PageViewLog.clinic_id.is_(None)).count()
    null_clinic_cta = cta_query.filter(CTAInteractionLog.clinic_id.is_(None)).count()
    current_app.logger.info(f"DEBUG: Records with NULL clinic_id - page_views: {null_clinic_page_views}, cta: {null_clinic_cta}")
    
    # Debug: Show some sample records to see what clinic_ids are being stored
    sample_page_views = page_view_query.limit(5).all()
    sample_cta = cta_query.limit(5).all()
    current_app.logger.info(f"DEBUG: Sample page view records:")
    for pv in sample_page_views:
        current_app.logger.info(f"  - ID: {pv.id}, clinic_id: {pv.clinic_id}, page_type: {pv.page_type}, session_id: {pv.session_id}")
    current_app.logger.info(f"DEBUG: Sample CTA records:")
    for cta in sample_cta:
        current_app.logger.info(f"  - ID: {cta.id}, clinic_id: {cta.clinic_id}, cta_type: {cta.cta_type}, session_id: {cta.session_id}")
    
    # BASIC QUIZ FUNNEL (Stage A) - Based purely on tracking data
    # Count unique sessions that progressed through each phase
    
    # Debug: Check total page views
    total_page_views = page_view_query.count()
    total_cta_clicks = cta_query.count()
    current_app.logger.info(f"DEBUG: Total page views: {total_page_views}, Total CTA clicks: {total_cta_clicks}")
    
    # Debug: Check specific page types
    stage_a_step_1_count = page_view_query.filter_by(page_type='stage_a_step_1').count()
    stage_a_step_2_count = page_view_query.filter_by(page_type='stage_a_step_2').count()
    stage_a_step_3_count = page_view_query.filter_by(page_type='stage_a_step_3').count()
    current_app.logger.info(f"DEBUG: stage_a_step_1: {stage_a_step_1_count}, stage_a_step_2: {stage_a_step_2_count}, stage_a_step_3: {stage_a_step_3_count}")
    
    # Debug: Check all page types that exist
    all_page_types = [row[0] for row in page_view_query.with_entities(PageViewLog.page_type).distinct().all()]
    current_app.logger.info(f"DEBUG: All page types in filtered data: {all_page_types}")
    
    # Debug: Check all CTA types that exist
    all_cta_types = [row[0] for row in cta_query.with_entities(CTAInteractionLog.cta_type).distinct().all()]
    current_app.logger.info(f"DEBUG: All CTA types in filtered data: {all_cta_types}")
    
    basic_funnel = {
        'started': page_view_query.filter_by(page_type='stage_a_step_1').with_entities(PageViewLog.session_id).distinct().count(),
        'phase1_completed': page_view_query.filter_by(page_type='stage_a_step_2').with_entities(PageViewLog.session_id).distinct().count(),
        'phase2_completed': page_view_query.filter_by(page_type='stage_a_step_3').with_entities(PageViewLog.session_id).distinct().count(),
        'submitted': cta_query.filter_by(cta_type='submit_stage_a', quiz_type='basic_quiz').with_entities(CTAInteractionLog.session_id).distinct().count(),
    }
    
    current_app.logger.info(f"DEBUG: Basic funnel counts: {basic_funnel}")
    
    # Get CTA click data for basic quiz (final conversion step) - Count unique sessions
    try:
        basic_submitted_sessions = set(row[0] for row in cta_query.filter_by(cta_type='submit_stage_a', quiz_type='basic_quiz').with_entities(CTAInteractionLog.session_id).distinct().all())
        
        # Get detailed CTA breakdown for basic quiz
        basic_cta_breakdown = {}
        
        # Schedule sleep test
        schedule_sessions = set(row[0] for row in cta_query.filter(
            and_(
                CTAInteractionLog.cta_type.in_(['schedule_sleep_test', 'email_link_click - scheduled a sleep test']),
                CTAInteractionLog.quiz_type == 'basic_quiz'
            )
        ).with_entities(CTAInteractionLog.session_id).distinct().all())
        basic_cta_breakdown['schedule_sleep_test'] = len(schedule_sessions & basic_submitted_sessions)
        
        # Requested consultation
        consultation_sessions = set(row[0] for row in cta_query.filter(
            and_(
                CTAInteractionLog.cta_type.in_(['consult_dental_team', 'consultation_form_submitted', 'email_link_click - consult with dental team']),
                CTAInteractionLog.quiz_type == 'basic_quiz'
            )
        ).with_entities(CTAInteractionLog.session_id).distinct().all())
        basic_cta_breakdown['requested_consultation'] = len(consultation_sessions & basic_submitted_sessions)
        
        # Moved to advanced
        advanced_sessions = set(row[0] for row in cta_query.filter(
            and_(
                CTAInteractionLog.cta_type.in_(['complete_advanced_assessment', 'email_link_click - advanced sleep assessment']),
                CTAInteractionLog.quiz_type == 'basic_quiz'
            )
        ).with_entities(CTAInteractionLog.session_id).distinct().all())
        basic_cta_breakdown['moved_to_advanced'] = len(advanced_sessions & basic_submitted_sessions)
        
        # Email link clicks (general)
        email_sessions = set(row[0] for row in cta_query.filter(
            and_(
                CTAInteractionLog.cta_type.like('email_link_click%'),
                CTAInteractionLog.quiz_type == 'basic_quiz'
            )
        ).with_entities(CTAInteractionLog.session_id).distinct().all())
        basic_cta_breakdown['email_link_clicks'] = len(email_sessions & basic_submitted_sessions)
        
        # Phone clicks
        phone_sessions = set(row[0] for row in cta_query.filter(
            and_(
                CTAInteractionLog.cta_type == 'phone_click',
                CTAInteractionLog.quiz_type == 'basic_quiz'
            )
        ).with_entities(CTAInteractionLog.session_id).distinct().all())
        basic_cta_breakdown['phone_clicks'] = len(phone_sessions & basic_submitted_sessions)
        
        # Calculate total unique sessions that took any CTA action
        all_cta_sessions = schedule_sessions | consultation_sessions | advanced_sessions | email_sessions | phone_sessions
        basic_cta_sessions = all_cta_sessions & basic_submitted_sessions
        
        basic_funnel['cta_actions'] = len(basic_cta_sessions)
        basic_funnel['cta_breakdown'] = basic_cta_breakdown
        
    except Exception as e:
        print(f"CTA tracking not available: {e}")
        basic_funnel['cta_actions'] = 0
        basic_funnel['cta_breakdown'] = {
            'schedule_sleep_test': 0,
            'requested_consultation': 0,
            'moved_to_advanced': 0,
            'email_link_clicks': 0,
            'phone_clicks': 0
        }
    
    # ADVANCED QUIZ FUNNEL (Stage B) - Based purely on tracking data
    # Count unique sessions that progressed through each phase
    advanced_submitted_sessions = set(row[0] for row in cta_query.filter_by(cta_type='submit_stage_b', quiz_type='advanced_quiz').with_entities(CTAInteractionLog.session_id).distinct().all())
    
    # Get detailed CTA breakdown for advanced quiz
    advanced_cta_breakdown = {}
    
    # Schedule sleep test
    advanced_schedule_sessions = set(row[0] for row in cta_query.filter(
        and_(
            CTAInteractionLog.cta_type.in_(['schedule_sleep_test', 'email_link_click - scheduled a sleep test']),
            CTAInteractionLog.quiz_type == 'advanced_quiz'
        )
    ).with_entities(CTAInteractionLog.session_id).distinct().all())
    advanced_cta_breakdown['schedule_sleep_test'] = len(advanced_schedule_sessions & advanced_submitted_sessions)
    
    # Requested consultation
    advanced_consultation_sessions = set(row[0] for row in cta_query.filter(
        and_(
            CTAInteractionLog.cta_type.in_(['consult_dental_team', 'consultation_form_submitted', 'email_link_click - consult with dental team']),
            CTAInteractionLog.quiz_type == 'advanced_quiz'
        )
    ).with_entities(CTAInteractionLog.session_id).distinct().all())
    advanced_cta_breakdown['requested_consultation'] = len(advanced_consultation_sessions & advanced_submitted_sessions)
    
    # Email link clicks (general)
    advanced_email_sessions = set(row[0] for row in cta_query.filter(
        and_(
            CTAInteractionLog.cta_type.like('email_link_click%'),
            CTAInteractionLog.quiz_type == 'advanced_quiz'
        )
    ).with_entities(CTAInteractionLog.session_id).distinct().all())
    advanced_cta_breakdown['email_link_clicks'] = len(advanced_email_sessions & advanced_submitted_sessions)
    
    # Phone clicks
    advanced_phone_sessions = set(row[0] for row in cta_query.filter(
        and_(
            CTAInteractionLog.cta_type == 'phone_click',
            CTAInteractionLog.quiz_type == 'advanced_quiz'
        )
    ).with_entities(CTAInteractionLog.session_id).distinct().all())
    advanced_cta_breakdown['phone_clicks'] = len(advanced_phone_sessions & advanced_submitted_sessions)
    
    # Calculate total unique sessions that took any CTA action
    all_advanced_cta_sessions = advanced_schedule_sessions | advanced_consultation_sessions | advanced_email_sessions | advanced_phone_sessions
    advanced_cta_sessions = all_advanced_cta_sessions & advanced_submitted_sessions

    advanced_funnel = {
        'started': page_view_query.filter_by(page_type='stage_b_step_1').with_entities(PageViewLog.session_id).distinct().count(),
        'phase1_completed': page_view_query.filter_by(page_type='stage_b_step_2').with_entities(PageViewLog.session_id).distinct().count(),
        'phase2_completed': page_view_query.filter_by(page_type='stage_b_step_3').with_entities(PageViewLog.session_id).distinct().count(),
        'submitted': len(advanced_submitted_sessions),
        'cta_actions': len(advanced_cta_sessions),
        'cta_breakdown': advanced_cta_breakdown
    }
    
    current_app.logger.info(f"DEBUG: Advanced funnel counts: {advanced_funnel}")
    
    # OVERALL CONVERSION METRICS - Based on tracking data
    overall_metrics = {
        'total_unique_visitors': page_view_query.with_entities(PageViewLog.session_id).distinct().count(),
        'total_unique_patients': page_view_query.filter(PageViewLog.patient_email.isnot(None)).with_entities(PageViewLog.patient_email).distinct().count(),
        'basic_to_advanced_conversion': basic_funnel['submitted'] and advanced_funnel['started'] and round((advanced_funnel['started'] / basic_funnel['submitted']) * 100, 2) or 0,
        'overall_cta_conversion': (basic_funnel['cta_actions'] + advanced_funnel['cta_actions']),
        'tracking_gaps': {
            'basic_gap': False,  # No gaps when using pure tracking data
            'advanced_gap': False,
            'basic_gap_count': 0,
            'advanced_gap_count': 0
        }
    }
    
    current_app.logger.info(f"DEBUG: Overall metrics: {overall_metrics}")
    
    # Calculate conversion rates for basic quiz with logical funnel flow
    started = basic_funnel['started']
    phase1 = basic_funnel['phase1_completed']
    phase2 = basic_funnel['phase2_completed']
    submitted = basic_funnel['submitted']
    cta = basic_funnel['cta_actions']

    if started > 0:
        # Cumulative (from started)
        phase1_cum = round((phase1 / started) * 100, 2)
        phase2_cum = round((phase2 / started) * 100, 2)
        submitted_cum = round((submitted / started) * 100, 2)
        cta_cum = round((cta / started) * 100, 2)
        # Step-to-step
        phase1_step = phase1_cum  # always same as cumulative for first step
        phase2_step = round((phase2 / max(1, phase1)) * 100, 2) if phase1 > 0 else 0
        submitted_step = round((submitted / max(1, phase2)) * 100, 2) if phase2 > 0 else 0
        cta_step = round((cta / max(1, submitted)) * 100, 2) if submitted > 0 else 0
        basic_funnel['conversion_rates'] = {
            'phase1_cum': phase1_cum,
            'phase1_step': phase1_step,
            'phase2_cum': phase2_cum,
            'phase2_step': phase2_step,
            'submitted_cum': submitted_cum,
            'submitted_step': submitted_step,
            'cta_cum': cta_cum,
            'cta_step': cta_step
        }
    else:
        basic_funnel['conversion_rates'] = {
            'phase1_cum': 0, 'phase1_step': 0,
            'phase2_cum': 0, 'phase2_step': 0,
            'submitted_cum': 0, 'submitted_step': 0,
            'cta_cum': 0, 'cta_step': 0
        }

    # Advanced funnel (same logic)
    adv_started = advanced_funnel['started']
    adv_phase1 = advanced_funnel['phase1_completed']
    adv_phase2 = advanced_funnel['phase2_completed']
    adv_submitted = advanced_funnel['submitted']
    adv_cta = advanced_funnel['cta_actions']

    if adv_started > 0:
        adv_phase1_cum = round((adv_phase1 / adv_started) * 100, 2)
        adv_phase2_cum = round((adv_phase2 / adv_started) * 100, 2)
        adv_submitted_cum = round((adv_submitted / adv_started) * 100, 2)
        adv_cta_cum = round((adv_cta / adv_started) * 100, 2)
        adv_phase1_step = adv_phase1_cum
        adv_phase2_step = round((adv_phase2 / max(1, adv_phase1)) * 100, 2) if adv_phase1 > 0 else 0
        adv_submitted_step = round((adv_submitted / max(1, adv_phase2)) * 100, 2) if adv_phase2 > 0 else 0
        adv_cta_step = round((adv_cta / max(1, adv_submitted)) * 100, 2) if adv_submitted > 0 else 0
        advanced_funnel['conversion_rates'] = {
            'phase1_cum': adv_phase1_cum,
            'phase1_step': adv_phase1_step,
            'phase2_cum': adv_phase2_cum,
            'phase2_step': adv_phase2_step,
            'submitted_cum': adv_submitted_cum,
            'submitted_step': adv_submitted_step,
            'cta_cum': adv_cta_cum,
            'cta_step': adv_cta_step
        }
    else:
        advanced_funnel['conversion_rates'] = {
            'phase1_cum': 0, 'phase1_step': 0,
            'phase2_cum': 0, 'phase2_step': 0,
            'submitted_cum': 0, 'submitted_step': 0,
            'cta_cum': 0, 'cta_step': 0
        }

    # Get clinic list for filtering - only show clinics the user has access to
    if current_user.role == 'admin':
        # Admin can see all clinics
        clinics = Clinic.query.all()
    else:
        # Regular dentist - only show clinics from their DSO associations
        user_dso_ids = current_user.get_dso_ids()
        if user_dso_ids:
            clinics = Clinic.query.filter(Clinic.dso_id.in_(user_dso_ids)).all()
        else:
            clinics = []
    
    return render_template('funnel_analytics.html',
                         basic_funnel=basic_funnel,
                         advanced_funnel=advanced_funnel,
                         overall_metrics=overall_metrics,
                         clinics=clinics,
                         selected_clinic_id=clinic_id,
                         date_from=date_from,
                         date_to=date_to)

@conversion_quiz_agent.route('/test-tracking-debug')
def test_tracking_debug():
    """Debug page for testing tracking functionality"""
    return render_template('test_tracking_debug.html')

def generate_quiz_pdf_and_upload(quiz_entry, patient, quiz_answers, ai_response_data, quiz_type):
    """
    Generate a PDF with quiz results and upload it to S3, then update the database.
    
    Args:
        quiz_entry: ConversionQuiz object
        patient: Patient object
        quiz_answers: Dictionary of quiz answers
        ai_response_data: Dictionary containing AI analysis and results
        quiz_type: 'basic_quiz' or 'advanced_quiz'
    
    Returns:
        dict: Success status and file information
    """
    print(f"DEBUG: ==========================================")
    print(f"DEBUG: PDF GENERATION FUNCTION CALLED!")
    print(f"DEBUG: ==========================================")
    print(f"DEBUG: generate_quiz_pdf_and_upload called with quiz_type: {quiz_type}")
    print(f"DEBUG: Patient ID: {patient.id}, Patient Email: {patient.email}")
    print(f"DEBUG: Quiz answers keys: {list(quiz_answers.keys())}")
    print(f"DEBUG: AI response data keys: {list(ai_response_data.keys())}")
    print(f"DEBUG: Function execution started successfully")
    

    
    try:
        # Create PDF in memory
        pdf_buffer = pdf_io.BytesIO()
        doc = SimpleDocTemplate(pdf_buffer, pagesize=A4)
        story = []
        
        # Get styles
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=18,
            spaceAfter=30,
            alignment=1,  # Center alignment
            textColor=colors.HexColor('#1976D2')
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=14,
            spaceAfter=12,
            spaceBefore=20,
            textColor=colors.HexColor('#1565C0')
        )
        
        normal_style = styles['Normal']
        normal_style.fontSize = 10
        normal_style.spaceAfter = 6
        
        # Title
        quiz_title = "Basic Sleep Apnea Assessment" if quiz_type == 'basic_quiz' else "Advanced Sleep Apnea Assessment"
        story.append(Paragraph(f"<b>{quiz_title} Results</b>", title_style))
        story.append(Spacer(1, 20))
        
        # Patient Information
        story.append(Paragraph("<b>Patient Information</b>", heading_style))
        patient_info = [
            ["Name:", patient.name or "Not provided"],
            ["Email:", patient.email],
            ["Phone:", patient.phone or "Not provided"],
            ["Assessment Date:", quiz_entry.created_at.strftime("%B %d, %Y")],
            ["Assessment Type:", quiz_title]
        ]
        
        patient_table = Table(patient_info, colWidths=[2*inch, 4*inch])
        patient_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#E3F2FD')),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#1565C0')),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#BDBDBD'))
        ]))
        story.append(patient_table)
        story.append(Spacer(1, 20))
        
        # Assessment Results
        story.append(Paragraph("<b>Assessment Results</b>", heading_style))
        
        # Risk Level and Score
        risk_level = ai_response_data.get('risk_level', 'Unknown')
        total_score = ai_response_data.get('total_score', 'N/A')
        
        risk_info = [
            ["Risk Level:", risk_level.upper()],
            ["Assessment Score:", str(total_score)],
        ]
        
        risk_table = Table(risk_info, colWidths=[2*inch, 4*inch])
        risk_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#E8F5E8') if risk_level.lower() == 'low' else 
             colors.HexColor('#FFF3E0') if risk_level.lower() == 'moderate' else colors.HexColor('#FFEBEE')),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#2E7D32') if risk_level.lower() == 'low' else 
             colors.HexColor('#F57C00') if risk_level.lower() == 'moderate' else colors.HexColor('#C62828')),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 11),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#BDBDBD'))
        ]))
        story.append(risk_table)
        story.append(Spacer(1, 15))
        
        # Quiz Answers
        story.append(Paragraph("<b>Assessment Responses</b>", heading_style))
        
        # Format quiz answers for display
        answer_rows = []
        for question, answer in quiz_answers.items():
            if isinstance(answer, (list, tuple)):
                answer = ', '.join(answer)
            elif isinstance(answer, bool):
                answer = 'Yes' if answer else 'No'
            elif answer is None:
                answer = 'Not answered'
            
            # Clean up question names for display
            question_display = question.replace('_', ' ').title()
            answer_rows.append([question_display, str(answer)])
        
        if answer_rows:
            answers_table = Table(answer_rows, colWidths=[2.5*inch, 3.5*inch])
            answers_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#F5F5F5')),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E0E0E0'))
            ]))
            story.append(answers_table)
        story.append(Spacer(1, 20))
        
        # AI Analysis
        if ai_response_data.get('ai_analysis'):
            story.append(Paragraph("<b>Clinical Analysis</b>", heading_style))
            ai_analysis = ai_response_data.get('ai_analysis', '')
            # Clean up the AI analysis text - remove HTML tags
            ai_analysis_clean = clean_html_for_pdf(ai_analysis)
            story.append(Paragraph(ai_analysis_clean, normal_style))
            story.append(Spacer(1, 15))
        
        # Risk Explanation
        if ai_response_data.get('risk_explanation'):
            story.append(Paragraph("<b>Risk Assessment</b>", heading_style))
            risk_explanation = ai_response_data.get('risk_explanation', '')
            risk_explanation_clean = clean_html_for_pdf(risk_explanation)
            story.append(Paragraph(risk_explanation_clean, normal_style))
            story.append(Spacer(1, 15))
        
        # Recommendations
        if ai_response_data.get('ai_narrative'):
            story.append(Paragraph("<b>Recommendations</b>", heading_style))
            recommendations = ai_response_data.get('ai_narrative', '')
            recommendations_clean = clean_html_for_pdf(recommendations)
            story.append(Paragraph(recommendations_clean, normal_style))
        
        # Build PDF
        doc.build(story)
        
        # Get file size BEFORE the buffer gets closed
        pdf_buffer.seek(0, 2)  # Seek to end
        file_size = pdf_buffer.tell()
        pdf_buffer.seek(0)  # Reset to beginning
        
        # Generate filename and S3 key
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"OSA_Patient_Questionnaire_{quiz_type}_{timestamp}.pdf"
        s3_key = f"patients/{patient.id}/medical/questionnaire/{filename}"
        
        print(f"DEBUG: Generated filename: {filename}")
        print(f"DEBUG: S3 key: {s3_key}")
        print(f"DEBUG: File size: {file_size} bytes")
        
        # Upload to S3
        print(f"DEBUG: Creating S3 client...")
        print(f"DEBUG: AWS_REGION: {os.getenv('AWS_REGION', 'us-west-2')}")
        print(f"DEBUG: S3_BUCKET_NAME: {os.getenv('S3_BUCKET_NAME')}")
        print(f"DEBUG: AWS_ACCESS_KEY_ID exists: {bool(os.getenv('AWS_ACCESS_KEY_ID'))}")
        print(f"DEBUG: AWS_SECRET_ACCESS_KEY exists: {bool(os.getenv('AWS_SECRET_ACCESS_KEY'))}")
        
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-west-2')
        )
        print(f"DEBUG: S3 client created successfully")
        
        s3_client.upload_fileobj(
            pdf_buffer,
            os.getenv('S3_BUCKET_NAME'),
            s3_key,
            ExtraArgs={'ContentType': 'application/pdf'}
        )
        
        # Save to database
        print(f"DEBUG: Saving file to database...")
        print(f"DEBUG: Patient ID: {patient.id}")
        print(f"DEBUG: Filename: {filename}")
        print(f"DEBUG: S3 Key: {s3_key}")
        print(f"DEBUG: File Size: {file_size}")
        
        # Save to database using the same db.session that's already working
        try:
            print(f"DEBUG: ==========================================")
            print(f"DEBUG: USING EXISTING DB.SESSION")
            print(f"DEBUG: ==========================================")
            print(f"DEBUG: Using the same db.session that stored the ConversionQuiz entry...")
            
            from flask_app.models import File
            
            print(f"DEBUG: Creating File object...")
            # Create File object using the same db.session
            new_file = File(
                name=filename,
                patient_id=patient.id,
                file_type='application/pdf',
                file_size=file_size,  # Now this has the correct value
                s3_key=s3_key,
                category='medical',
                subcategory='questionnaire'
            )
            print(f"DEBUG: File object created successfully")
            print(f"DEBUG:   Name: {new_file.name}")
            print(f"DEBUG:   Patient ID: {new_file.patient_id}")
            print(f"DEBUG:   S3 Key: {new_file.s3_key}")
            
            print(f"DEBUG: Adding file to existing db.session...")
            # Save to database using the same session
            db.session.add(new_file)
            print(f"DEBUG: File added to db.session")
            
            print(f"DEBUG: Committing to database...")
            db.session.commit()
            print(f"DEBUG: Database commit successful")
            
            file_id = new_file.id
            print(f"DEBUG: Getting file ID: {file_id}")
            
            print(f"DEBUG: ==========================================")
            print(f"DEBUG: DATABASE SAVE COMPLETED SUCCESSFULLY")
            print(f"DEBUG: File saved to database with ID: {file_id} using existing db.session")
            print(f"DEBUG: ==========================================")
            
        except Exception as db_error:
            print(f"DEBUG: ==========================================")
            print(f"DEBUG: DATABASE SAVE FAILED")
            print(f"DEBUG: ==========================================")
            print(f"DEBUG: Database save failed: {str(db_error)}")
            print(f"DEBUG: Error type: {type(db_error).__name__}")
            import traceback
            print(f"DEBUG: Database error traceback: {traceback.format_exc()}")
            print(f"DEBUG: ==========================================")
            
            # Rollback the session
            try:
                db.session.rollback()
                print(f"DEBUG: Session rollback successful")
            except Exception as rollback_error:
                print(f"DEBUG: Session rollback failed: {str(rollback_error)}")
            
            return {
                'success': True,
                'filename': filename,
                's3_key': s3_key,
                'file_size': file_size,
                'file_id': None,
                'patient_id': patient.id,
                'warning': 'PDF uploaded to S3 but database save failed',
                'debug_info': f'Database save failed: {str(db_error)}'
            }
        
        try:
            current_app.logger.info(f"PDF generated and uploaded successfully: {s3_key}")
        except:
            print(f"DEBUG: PDF generated and uploaded successfully: {s3_key}")
        
        return {
            'success': True,
            'filename': filename,
            's3_key': s3_key,
            'file_size': file_size,
            'file_id': file_id,
            'patient_id': patient.id,
            'debug_info': f'PDF generated and saved to database with ID: {file_id}'
        }
        
    except Exception as e:
        print(f"DEBUG: Error generating PDF and uploading to S3: {str(e)}")
        try:
            current_app.logger.error(f"Error generating PDF and uploading to S3: {str(e)}")
        except:
            print(f"Could not log to current_app.logger: {str(e)}")
        try:
            db.session.rollback()
        except:
            print(f"Could not rollback database session: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }


def generate_dentist_treatment_quiz_pdf(quiz_entry, patient, quiz_data, language):
    """
    Generate a clean HTML report for dentist treatment quiz results.
    HTML can be printed to PDF from browser for best results.
    Always generate in English for consistency and reliability.
    
    Args:
        quiz_entry: DentistTreatmentQuiz object
        patient: Patient object
        quiz_data: Dictionary of quiz answers (should be in English, but will be cleaned if not)
        language: 'en' or 'he' (for display purposes only, PDF always in English)
    
    Returns:
        Flask Response: HTML report for viewing/printing
    """
    print(f"DEBUG: ==========================================")
    print(f"DEBUG: DENTIST TREATMENT QUIZ HTML REPORT GENERATION!")
    print(f"DEBUG: ==========================================")
    print(f"DEBUG: Patient ID: {patient.id}, Patient Name: {patient.name}")
    print(f"DEBUG: Language: {language} (Report will be generated in English)")
    print(f"DEBUG: Quiz data keys: {list(quiz_data.keys())}")
    
    # Clean quiz data based on language - preserve Hebrew if Hebrew, convert to English if English
    print(f"DEBUG: Original quiz data: {quiz_data}")
    cleaned_quiz_data = clean_quiz_data_for_pdf(quiz_data, language)
    print(f"DEBUG: Quiz data cleaned for report generation (language: {language})")
    print(f"DEBUG: Cleaned quiz data: {cleaned_quiz_data}")
    
    try:
        # Generate HTML content for the report
        html_content = generate_report_html(quiz_entry, patient, cleaned_quiz_data, language)
        
        print(f"DEBUG: HTML report generated successfully")
        
        # Return HTML directly to browser (can be printed to PDF)
        from flask import Response
        return Response(html_content, mimetype='text/html')
        
    except Exception as e:
        print(f"DEBUG: Report generation failed: {str(e)}")
        import traceback
        print(f"DEBUG: Error traceback: {traceback.format_exc()}")
        
        # Return error response
        from flask import jsonify
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Old PDF generation function removed - now using inline generation in submission route

def generate_report_html(quiz_entry, patient, cleaned_quiz_data, language='en'):
    """Generate HTML content for the assessment report (printable to PDF)"""
    from datetime import datetime
    from flask_app.helpers.dentist_treatment_quiz_structure import get_quiz_structure
    import re
    
    print(f"DEBUG: Requested language: {language}")
    quiz_structure = get_quiz_structure(language)
    print(f"DEBUG: Quiz structure language: {list(quiz_structure.keys()) if isinstance(quiz_structure, dict) else 'Not a dict'}")
    print(f"DEBUG: Quiz structure sections count: {len(quiz_structure.get('sections', []))}")
    
    # Process the quiz data for the template
    sections = []
    for section in quiz_structure.get('sections', []):
        section_title = section['section_title']
        # Clean HTML tags
        section_title = re.sub(r'<[^>]+>', '', section_title)
        section_title = re.sub(r'&[a-zA-Z0-9#]+;', '', section_title)
        
        # Shorten section titles
        if section_title.startswith("4. Additional Notes"):
            section_title = "4. Additional Notes"
        elif section_title.startswith("3. TMJ"):
            section_title = "3. TMJ Evaluation"
        elif section_title.startswith("2. Oral"):
            section_title = "2. Oral Measurements"
        elif section_title.startswith("1. Suitability"):
            section_title = "1. Suitability Assessment"
        
        questions = []
        for question in section.get('questions', []):
            question_id = question['question_id']
            if question_id in cleaned_quiz_data:
                answer = cleaned_quiz_data[question_id]
                
                # Handle different answer types
                if isinstance(answer, dict) and 'aux_value' in answer:
                    main_answer = answer.get('value', 'N/A')
                    aux_answer = answer.get('aux_value', '')
                    if aux_answer:
                        answer_display = f"{main_answer} ({aux_answer})"
                    else:
                        answer_display = main_answer
                elif isinstance(answer, (list, tuple)):
                    answer_display = ', '.join(str(item) for item in answer)
                elif isinstance(answer, bool):
                    answer_display = 'Yes' if answer else 'No'
                elif answer is None:
                    answer_display = 'Not answered'
                else:
                    answer_display = str(answer)
                
                # Add unit if specified
                if question.get('unit'):
                    answer_display += f" {question['unit']}"
                
                # Clean HTML tags from answer and question
                answer_display = re.sub(r'<[^>]+>', '', str(answer_display))
                answer_display = re.sub(r'&[a-zA-Z0-9#]+;', '', answer_display)
                
                question_label = question['question_label']
                question_label = re.sub(r'<[^>]+>', '', question_label)
                question_label = re.sub(r'&[a-zA-Z0-9#]+;', '', question_label)
                
                questions.append({
                    'label': question_label,
                    'answer': answer_display
                })
        
        if questions:  # Only add sections that have answered questions
            sections.append({
                'title': section_title,
                'questions': questions
            })
    
    # Use render_template to generate HTML from template
    return render_template(
        'dentist_treatment_quiz_report.html',
        quiz_entry=quiz_entry,
        patient=patient,
        language=language,
        sections=sections,
        report_css=get_report_css(),
        current_time=datetime.now().strftime('%B %d, %Y at %I:%M %p')
    )

def get_report_css():
    """Get CSS styles for the assessment report (printable to PDF)"""
    return """
        @page {
            size: A4;
            margin: 1in;
            @bottom-center {
                content: "Page " counter(page) " of " counter(pages);
            }
        }
        
        body {
            font-family: Arial, sans-serif;
            font-size: 12px;
            line-height: 1.4;
            color: #333;
        }
        
        /* Hebrew language support */
        .hebrew {
            direction: rtl;
            text-align: right;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        .hebrew .title,
        .hebrew h1,
        .hebrew h2,
        .hebrew h3 {
            direction: rtl;
            text-align: center;
        }
        
        .hebrew .info-table td:first-child {
            text-align: right;
        }
        
        .hebrew .question-label {
            text-align: right;
        }
        
        .hebrew .answer {
            text-align: right;
        }
        
        .container {
            max-width: 100%;
        }
        
        .title {
            text-align: center;
            color: #1976D2;
            font-size: 24px;
            margin-bottom: 30px;
            border-bottom: 2px solid #1976D2;
            padding-bottom: 10px;
        }
        
        .patient-info {
            margin-bottom: 30px;
        }
        
        .patient-info h2 {
            color: #1565C0;
            font-size: 18px;
            margin-bottom: 15px;
        }
        
        .info-table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 20px;
        }
        
        .info-table td {
            padding: 8px;
            border: 1px solid #ddd;
        }
        
        .info-table td:first-child {
            background-color: #E3F2FD;
            color: #1565C0;
            font-weight: bold;
            width: 30%;
        }
        
        .assessment-responses h2 {
            color: #1565C0;
            font-size: 18px;
            margin-bottom: 20px;
        }
        
        .section {
            margin-bottom: 25px;
            page-break-inside: avoid;
        }
        
        .section-title {
            background-color: #E3F2FD;
            color: #1565C0;
            padding: 10px;
            margin: 0 0 15px 0;
            border-radius: 5px;
            font-size: 16px;
            font-weight: bold;
        }
        
        .question {
            margin-bottom: 15px;
            padding: 10px;
            border: 1px solid #eee;
            border-radius: 5px;
            background-color: #fafafa;
        }
        
        .question-label {
            font-weight: bold;
            color: #555;
            margin-bottom: 5px;
        }
        
        .answer {
            color: #333;
            word-wrap: break-word;
            overflow-wrap: break-word;
            max-width: 100%;
        }
        
        .footer {
            margin-top: 30px;
            text-align: center;
            color: #666;
            font-size: 10px;
            border-top: 1px solid #ddd;
            padding-top: 10px;
        }
        
        .print-instructions {
            margin: 30px 0;
            padding: 20px;
            background-color: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 5px;
            text-align: center;
        }
        
        .print-btn {
            background-color: #007bff;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 16px;
            margin-top: 10px;
        }
        
        .print-btn:hover {
            background-color: #0056b3;
        }
        
        @media print {
            .print-instructions {
                display: none;
            }
            
            body {
                margin: 0;
                padding: 0;
            }
            
            .container {
                max-width: none;
            }
        }
    """


@conversion_quiz_agent.route('/dentist-treatment-quiz')
@login_required
def show_dentist_treatment_quiz():
    """Show dentist treatment quiz form with patient selection.
    Uses shared get_accessible_patients - identical to patient_list and forms."""
    from flask_app.helpers.patient_access_helpers import get_accessible_patients
    from flask_app.helpers.dentist_treatment_quiz_structure import ENGLISH_QUIZ_STRUCTURE, HEBREW_QUIZ_STRUCTURE

    language = 'en'
    include_archived = False
    if current_user.role == 'admin':
        include_archived = request.args.get('include_archived', 'false').lower() == 'true'

    patients = get_accessible_patients(include_archived=include_archived)

    return render_template('dentist_treatment_quiz.html',
                         patients=patients,
                         language=language,
                         english_quiz_structure=ENGLISH_QUIZ_STRUCTURE,
                         hebrew_quiz_structure=HEBREW_QUIZ_STRUCTURE)

def convert_hebrew_answer_to_english(answer, question_id):
    """
    Convert Hebrew quiz answers to English for consistent storage and PDF generation.
    
    Args:
        answer: The answer value (could be string, dict, list, etc.)
        question_id: The question ID for context
    
    Returns:
        The answer converted to English
    """
    if answer is None:
        return None
    
    # Handle different answer types
    if isinstance(answer, dict):
        # Handle auxiliary inputs
        converted_answer = {}
        if 'value' in answer:
            converted_answer['value'] = convert_hebrew_text_to_english(answer['value'], question_id)
        if 'aux_value' in answer:
            converted_answer['aux_value'] = convert_hebrew_text_to_english(answer['aux_value'], question_id)
        return converted_answer
    elif isinstance(answer, (list, tuple)):
        # Handle list answers (like multi-select)
        return [convert_hebrew_text_to_english(item, question_id) for item in answer]
    else:
        # Handle simple text/number answers
        return convert_hebrew_text_to_english(str(answer), question_id)

def convert_hebrew_text_to_english(text, question_id):
    """
    Convert Hebrew text to English based on common patterns and question context.
    
    Args:
        text: The text to convert
        question_id: The question ID for context
    
    Returns:
        English equivalent of the Hebrew text
    """
    if not text or not isinstance(text, str):
        return text
    
    # Common Hebrew to English mappings
    hebrew_to_english = {
        # Yes/No answers
        'כן': 'Yes',
        'לא': 'No',
        'yes': 'Yes',
        'no': 'No',
        
        # Common Hebrew words
        'ללא': 'None',
        'ימין': 'Right',
        'שמאל': 'Left',
        'מרכז': 'Center',
        
        # Units
        'מ"מ': 'mm',
        'ס"מ': 'cm',
        'מ"ל': 'ml',
        
        # Common medical terms
        'דום נשימה': 'Sleep apnea',
        'התקן דנטלי': 'Dental appliance',
        'חריקת שיניים': 'Teeth grinding',
        'הידוק שיניים': 'Teeth clenching',
        'רפלקס הקאה': 'Gag reflex',
        'רגישות': 'Sensitivity',
        'שחיקה': 'Wear',
        'סטייה': 'Deviation',
        'תנועה': 'Movement',
        'לסת': 'Jaw',
        'שיניים': 'Teeth',
        'פה': 'Mouth',
        'לשון': 'Tongue',
        'חניכיים': 'Gums',
        
        # Question-specific mappings
        'התאמה': 'Suitability',
        'פתיחה': 'Opening',
        'סגר': 'Occlusion',
        'פרוטרוזיה': 'Protrusion',
        'overjet': 'Overjet',
        'overbite': 'Overbite',
        'crossbite': 'Crossbite',
    }
    
    # Convert the text
    converted_text = text
    for hebrew, english in hebrew_to_english.items():
        converted_text = converted_text.replace(hebrew, english)
    
    # If the text still contains Hebrew characters, try to provide a meaningful English equivalent
    if any('\u0590' <= char <= '\u05FF' for char in converted_text):
        # Still contains Hebrew characters, provide fallback
        if question_id.startswith('suitability_'):
            if 'adequate' in question_id or 'dentition' in question_id:
                return 'Adequate dentition for OSA appliance'
            elif 'upcoming' in question_id or 'work' in question_id:
                return 'No upcoming dental work'
            elif 'wear' in question_id or 'sensitivity' in question_id:
                return 'No tooth wear or sensitivity'
            elif 'bruxism' in question_id or 'clenching' in question_id:
                return 'No bruxism or clenching'
            elif 'gag' in question_id or 'reflex' in question_id:
                return 'Normal gag reflex'
        elif question_id.startswith('oral_'):
            if 'opening' in question_id:
                return 'Normal opening'
            elif 'deviation' in question_id:
                return 'No deviation'
            elif 'protrusion' in question_id:
                return 'Normal protrusion'
            elif 'overjet' in question_id:
                return 'Normal overjet'
            elif 'overbite' in question_id:
                return 'Normal overbite'
            elif 'crossbite' in question_id:
                return 'No crossbite'
        else:
            return 'Not specified'
    
    return converted_text

def clean_quiz_data_for_pdf(quiz_data, language='en'):
    """
    Clean quiz data to ensure it's suitable for PDF generation.
    Always converts Hebrew text to English for PDF storage (regardless of UI language).
    Removes HTML tags in all cases.
    
    Args:
        quiz_data: Dictionary of quiz answers
        language: 'en' or 'he' - UI language (for compatibility, but Hebrew is always converted to English)
    
    Returns:
        Cleaned quiz data suitable for PDF generation (always in English)
    """
    if not quiz_data:
        return {}
    
    cleaned_data = {}
    for question_id, answer in quiz_data.items():
        cleaned_answer = clean_answer_for_pdf(answer, question_id, language)
        cleaned_data[question_id] = cleaned_answer
    
    return cleaned_data

def clean_answer_for_pdf(answer, question_id, language='en'):
    """
    Clean a single answer for PDF generation.
    
    Args:
        answer: The answer value
        question_id: The question ID for context
        language: 'en' or 'he' - determines how to handle Hebrew text
    
    Returns:
        Cleaned answer suitable for PDF
    """
    if answer is None:
        return None
    
    # Handle different answer types
    if isinstance(answer, dict):
        # Handle auxiliary inputs
        cleaned_answer = {}
        if 'value' in answer:
            cleaned_answer['value'] = clean_text_for_pdf(answer['value'], question_id, language)
        if 'aux_value' in answer:
            cleaned_answer['aux_value'] = clean_text_for_pdf(answer['aux_value'], question_id, language)
        return cleaned_answer
    elif isinstance(answer, (list, tuple)):
        # Handle list answers
        return [clean_text_for_pdf(item, question_id, language) for item in answer]
    else:
        # Handle simple text/number answers
        return clean_text_for_pdf(str(answer), question_id, language)

def clean_text_for_pdf(text, question_id, language='en'):
    """
    Clean text for PDF generation by removing HTML tags.
    Always converts Hebrew text to English for PDF storage (regardless of UI language).
    
    Args:
        text: The text to clean
        question_id: The question ID for context
        language: 'en' or 'he' - UI language (for compatibility, but Hebrew is always converted to English)
    
    Returns:
        Cleaned text suitable for PDF (always in English)
    """
    if not text or not isinstance(text, str):
        return text
    
    # Remove HTML tags more aggressively
    import re
    
    # First, remove common HTML tags
    cleaned_text = re.sub(r'<[^>]+>', '', text)
    
    # Also remove any remaining HTML entities
    cleaned_text = re.sub(r'&[a-zA-Z0-9#]+;', '', cleaned_text)
    
    # Remove extra whitespace
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
    
    # Always convert Hebrew to English for PDF storage (regardless of UI language)
    if any('\u0590' <= char <= '\u05FF' for char in cleaned_text):
        # Contains Hebrew characters, convert to English
        return convert_hebrew_text_to_english(cleaned_text, question_id)
    
    return cleaned_text

@conversion_quiz_agent.route('/dentist-treatment-quiz/submit', methods=['POST'])
@login_required
def submit_dentist_treatment_quiz():
    """Handle dentist treatment quiz submission"""
    from flask_app.models import DentistTreatmentQuiz
    from flask_app.helpers.dentist_treatment_quiz_structure import validate_quiz_answers
    
    try:
        data = request.get_json()
        patient_id = data.get('patient_id')
        language = data.get('language', 'en')  # Get language from submitted data
        quiz_data = data.get('quiz_data', {})
        
        # Validate patient access - doctors can only submit for patients from their assigned clinics
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied - patient not in your assigned clinics'}), 403
        
        # Validate quiz data using the submitted language
        is_valid, errors = validate_quiz_answers(quiz_data, language)
        if not is_valid:
            err_msg = '; '.join(errors) if errors else 'Validation failed'
            return jsonify({
                'success': False,
                'error': err_msg,
                'errors': errors,
            }), 400
        
        # Clean quiz data based on language - preserve Hebrew if Hebrew, convert to English if English
        quiz_data_for_storage = clean_quiz_data_for_pdf(quiz_data, language)
        
        # Create quiz entry
        quiz_entry = DentistTreatmentQuiz(
            patient_id=patient_id,
            dentist_id=current_user.id,
            clinic_id=current_user.get_primary_clinic_id(),
            quiz_input=json.dumps(quiz_data_for_storage),  # Store in appropriate language format
            language=language,  # Store the actual language used
            status='completed'
        )
        
        db.session.add(quiz_entry)
        db.session.commit()
        
        # Generate PDF and upload to S3 (using working patient quiz pattern)
        try:
            print(f"DEBUG: ==========================================")
            print(f"DEBUG: DENTIST QUIZ PDF GENERATION CALLED!")
            print(f"DEBUG: ==========================================")
            print(f"DEBUG: Quiz ID: {quiz_entry.id}, Patient ID: {patient_id}")
            print(f"DEBUG: Patient Email: {patient.email}, Language: {language}")
            print(f"DEBUG: Quiz data keys: {list(quiz_data_for_storage.keys())}")
            
            # Create PDF in memory using same pattern as patient quiz
            pdf_buffer = pdf_io.BytesIO()
            doc = SimpleDocTemplate(pdf_buffer, pagesize=A4)
            story = []
            
            # Get styles
            styles = getSampleStyleSheet()
            title_style = ParagraphStyle(
                'CustomTitle',
                parent=styles['Heading1'],
                fontSize=18,
                spaceAfter=30,
                alignment=1,  # Center alignment
                textColor=colors.HexColor('#1976D2')
            )
            
            heading_style = ParagraphStyle(
                'CustomHeading',
                parent=styles['Heading2'],
                fontSize=14,
                spaceAfter=12,
                spaceBefore=20,
                textColor=colors.HexColor('#1565C0')
            )
            
            normal_style = styles['Normal']
            normal_style.fontSize = 10
            normal_style.spaceAfter = 6
            
            # Title
            quiz_title = "הערכת טיפול דנטלי" if language == 'he' else "Dentist Treatment Assessment"
            story.append(Paragraph(f"<b>{quiz_title}</b>", title_style))
            story.append(Spacer(1, 20))
            
            # Patient Information
            story.append(Paragraph("<b>Patient Information</b>", heading_style))
            patient_info = [
                ["Name:", patient.name or "Not provided"],
                ["Email:", patient.email],
                ["Phone:", patient.phone or "Not provided"],
                ["Assessment Date:", quiz_entry.created_at.strftime("%B %d, %Y")],
                ["Dentist:", (quiz_entry.dentist.name if quiz_entry.dentist else "Not specified")]
            ]
            
            patient_table = Table(patient_info, colWidths=[2*inch, 4*inch])
            patient_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#E3F2FD')),
                ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#1565C0')),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#BDBDBD'))
            ]))
            story.append(patient_table)
            story.append(Spacer(1, 20))
            
            # Assessment Responses
            story.append(Paragraph("<b>Assessment Responses</b>", heading_style))
            
            # Get quiz structure for proper question labels
            from flask_app.helpers.dentist_treatment_quiz_structure import get_quiz_structure
            quiz_structure = get_quiz_structure(language)
            
            # Process each section
            for section in quiz_structure.get('sections', []):
                story.append(Paragraph(f"<b>{section['section_title']}</b>", heading_style))
                
                for question in section.get('questions', []):
                    question_id = question['question_id']
                    question_label = question['question_label']
                    answer = quiz_data_for_storage.get(question_id, '')
                    
                    if answer:
                        # Clean the answer for PDF display
                        if isinstance(answer, dict):
                            if 'value' in answer:
                                answer_text = str(answer['value'])
                            else:
                                answer_text = str(answer)
                        elif isinstance(answer, list):
                            answer_text = ', '.join(str(item) for item in answer)
                        else:
                            answer_text = str(answer)
                        
                        # Remove HTML tags and clean text
                        import re
                        answer_text = re.sub(r'<[^>]+>', '', answer_text)
                        answer_text = re.sub(r'&[a-zA-Z0-9#]+;', '', answer_text)
                        
                        # Create question-answer table
                        qa_data = [
                            [f"Q: {question_label}", f"A: {answer_text}"]
                        ]
                        
                        qa_table = Table(qa_data, colWidths=[3*inch, 3*inch])
                        qa_table.setStyle(TableStyle([
                            ('BACKGROUND', (0, 0), (0, 0), colors.HexColor('#E8F5E8')),
                            ('BACKGROUND', (1, 0), (1, 0), colors.HexColor('#F5F5F5')),
                            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                            ('FONTSIZE', (0, 0), (-1, -1), 9),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#BDBDBD')),
                            ('VALIGN', (0, 0), (-1, -1), 'TOP')
                        ]))
                        story.append(qa_table)
                        story.append(Spacer(1, 8))
            
            # Build PDF
            doc.build(story)
            
            # Get file size BEFORE the buffer gets closed (SAME AS PATIENT QUIZ)
            pdf_buffer.seek(0, 2)  # Seek to end
            file_size = pdf_buffer.tell()
            pdf_buffer.seek(0)  # Reset to beginning
            
            # Generate filename and S3 key (SAME PATTERN AS PATIENT QUIZ)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"Dentist_Treatment_Assessment_{timestamp}.pdf"
            s3_key = f"patients/{patient.id}/medical/questionnaire/{filename}"
            
            print(f"DEBUG: Generated filename: {filename}")
            print(f"DEBUG: S3 key: {s3_key}")
            print(f"DEBUG: File size: {file_size} bytes")
            
            # Upload to S3 (SAME AS PATIENT QUIZ)
            print(f"DEBUG: Creating S3 client...")
            print(f"DEBUG: AWS_REGION: {os.getenv('AWS_REGION', 'us-west-2')}")
            print(f"DEBUG: S3_BUCKET_NAME: {os.getenv('S3_BUCKET_NAME')}")
            print(f"DEBUG: AWS_ACCESS_KEY_ID exists: {bool(os.getenv('AWS_ACCESS_KEY_ID'))}")
            print(f"DEBUG: AWS_SECRET_ACCESS_KEY exists: {bool(os.getenv('AWS_SECRET_ACCESS_KEY'))}")
            
            s3_client = boto3.client(
                's3',
                aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
                region_name=os.getenv('AWS_REGION', 'us-west-2')
            )
            print(f"DEBUG: S3 client created successfully")
            
            s3_client.upload_fileobj(
                pdf_buffer,
                os.getenv('S3_BUCKET_NAME'),
                s3_key,
                ExtraArgs={'ContentType': 'application/pdf'}
            )
            
            # Save to database (SAME AS PATIENT QUIZ)
            print(f"DEBUG: Saving file to database...")
            print(f"DEBUG: Patient ID: {patient.id}")
            print(f"DEBUG: Filename: {filename}")
            print(f"DEBUG: S3 Key: {s3_key}")
            print(f"DEBUG: File Size: {file_size}")
            
            # Save to database using the same pattern as patient quiz
            try:
                new_file = File(
                    name=filename,
                    patient_id=patient.id,
                    file_type='application/pdf',
                    file_size=file_size,
                    s3_key=s3_key,
                    category='medical',
                    subcategory='questionnaire',
                    mapping=f'dentist_quiz_{quiz_entry.id}'  # Link to specific quiz
                )
                db.session.add(new_file)
                db.session.commit()
                
                print(f"DEBUG: File saved to database successfully with ID: {new_file.id}")
                current_app.logger.info(f"Dentist assessment PDF generated and uploaded: {s3_key}")
                
                # Update the quiz record with the file_id
                quiz_entry.file_id = new_file.id
                db.session.commit()
                print(f"DEBUG: Quiz {quiz_entry.id} updated with file_id: {new_file.id}")

            except Exception as db_error:
                print(f"DEBUG: Database error: {str(db_error)}")
                db.session.rollback()
                raise db_error
                
        except Exception as pdf_error:
            print(f"DEBUG: PDF generation error: {str(pdf_error)}")
            import traceback
            print(f"DEBUG: Error traceback: {traceback.format_exc()}")
            current_app.logger.error(f"Error generating dentist assessment PDF: {str(pdf_error)}")
            # Don't fail the quiz submission if PDF generation fails
        
        # Assessment saved successfully with PDF uploaded to S3
        current_app.logger.info(f"Dentist treatment quiz saved successfully: {quiz_entry.id}")
        return jsonify({
            'success': True,
            'quiz_id': quiz_entry.id,
            'message': 'Assessment saved successfully with PDF uploaded'
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error submitting dentist treatment quiz: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@conversion_quiz_agent.route('/dentist-treatment-quiz/history/<int:patient_id>')
@login_required
def dentist_treatment_quiz_history(patient_id):
    """Show treatment quiz history for a patient"""
    from flask_app.models import DentistTreatmentQuiz
    
    patient = Patient.query.get_or_404(patient_id)

    # Check access - doctors can only view patients from their assigned clinics
    if not current_user.can_access_patient(patient):
        flash('You do not have access to this patient.', 'error')
        return redirect(url_for('conversion_quiz_agent.show_dentist_treatment_quiz'))
    
    quizzes = DentistTreatmentQuiz.query.filter_by(
        patient_id=patient_id
    ).order_by(DentistTreatmentQuiz.created_at.desc()).all()
    
    return render_template('dentist_treatment_quiz_history.html', 
                         quizzes=quizzes, 
                         patient=patient)


@conversion_quiz_agent.route('/api/dentist-treatment-quiz/<int:quiz_id>/files')
@login_required
def get_dentist_treatment_quiz_files(quiz_id):
    """Get files for a dentist treatment quiz (similar to patient files endpoint)"""
    from flask_app.models import DentistTreatmentQuiz, File
    from datetime import timedelta
    
    try:
        current_app.logger.info(f"DEBUG: Looking for files for quiz_id: {quiz_id}")
        
        # Get the quiz entry
        quiz = DentistTreatmentQuiz.query.get_or_404(quiz_id)
        current_app.logger.info(f"DEBUG: Found quiz - patient_id: {quiz.patient_id}, created_at: {quiz.created_at}")
        
        # Check access - doctors can only access patients from their assigned clinics
        patient = Patient.query.get_or_404(quiz.patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Find the PDF file for this quiz
        time_window_start = quiz.created_at - timedelta(minutes=5)
        time_window_end = quiz.created_at + timedelta(minutes=5)
        
        # Search for the specific PDF
        pdf_file = File.query.filter(
            File.patient_id == quiz.patient_id,
            File.category == 'medical',
            File.subcategory == 'dentist_questionnaire',
            File.upload_date >= time_window_start,
            File.upload_date <= time_window_end
        ).order_by(File.upload_date.desc()).first()
        
        if not pdf_file:
            # Fallback: try to find the most recent dentist_questionnaire file for this patient
            pdf_file = File.query.filter(
                File.patient_id == quiz.patient_id,
                File.subcategory == 'dentist_questionnaire'
            ).order_by(File.upload_date.desc()).first()
        
        if pdf_file:
            current_app.logger.info(f"DEBUG: Found PDF file: {pdf_file.name}")
            current_app.logger.info(f"DEBUG: PDF file s3_key: {pdf_file.s3_key}")
            current_app.logger.info(f"DEBUG: PDF file category: {pdf_file.category}")
            current_app.logger.info(f"DEBUG: PDF file subcategory: {pdf_file.subcategory}")
            current_app.logger.info(f"DEBUG: PDF file upload_date: {pdf_file.upload_date}")
            
            # Serialize the file with presigned URL (same pattern as patient files)
            def serialize_file(file):
                view_url = generate_presigned_url_for_viewing(file.s3_key, inline=True, expires_in=3600)
                current_app.logger.info(f"DEBUG: Generated view_url: {view_url}")
                
                return {
                    'id': file.id,
                    'name': file.name,
                    'file_type': file.file_type,
                    'category': file.category,
                    'subcategory': file.subcategory,
                    'upload_date': file.upload_date.isoformat() if file.upload_date else None,
                    'source': 'dentist_quiz',
                    'view_url': view_url
                }
            
            files = [serialize_file(pdf_file)]
            current_app.logger.info(f"DEBUG: Returning {len(files)} files for quiz {quiz_id}")
            
            return jsonify({
                'success': True,
                'files': files
            })
        else:
            current_app.logger.error(f"DEBUG: No PDF file found for quiz {quiz_id}")
            return jsonify({
                'success': False,
                'error': 'PDF not found for this assessment'
            }), 404
            
    except Exception as e:
        current_app.logger.error(f"DEBUG: Exception in get_dentist_treatment_quiz_files: {str(e)}")
        import traceback
        current_app.logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

@conversion_quiz_agent.route('/dentist-treatment-quiz/<int:quiz_id>/html-report')
@login_required
def get_dentist_treatment_quiz_html_report(quiz_id):
    """Generate and serve HTML report for immediate viewing (printable to PDF)"""
    from flask_app.models import DentistTreatmentQuiz
    
    try:
        current_app.logger.info(f"DEBUG: Generating HTML report for quiz_id: {quiz_id}")
        
        # Get the quiz entry
        quiz = DentistTreatmentQuiz.query.get_or_404(quiz_id)
        current_app.logger.info(f"DEBUG: Found quiz - patient_id: {quiz.patient_id}")
        
        # Check access - doctors can only access patients from their assigned clinics
        patient = Patient.query.get_or_404(quiz.patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Parse quiz data from stored JSON
        quiz_data = json.loads(quiz.quiz_input) if quiz.quiz_input else {}
        
        # Generate HTML report for immediate viewing
        return generate_dentist_treatment_quiz_pdf(quiz, patient, quiz_data, quiz.language)
        
    except Exception as e:
        current_app.logger.error(f"DEBUG: Exception in get_dentist_treatment_quiz_html_report: {str(e)}")
        import traceback
        current_app.logger.error(f"DEBUG: Error traceback: {traceback.format_exc()}")
        return f"Error generating HTML report: {str(e)}", 500

@conversion_quiz_agent.route('/dentist-treatment-quiz/<int:quiz_id>/pdf')
@login_required
def get_dentist_treatment_quiz_pdf(quiz_id):
    """Serve PDF for a dentist treatment quiz from S3 (standardized process)"""
    from flask_app.models import DentistTreatmentQuiz, File
    
    try:
        current_app.logger.info(f"DEBUG: Serving PDF for quiz_id: {quiz_id}")
        
        # Get the quiz entry
        quiz = DentistTreatmentQuiz.query.get_or_404(quiz_id)
        current_app.logger.info(f"DEBUG: Found quiz - patient_id: {quiz.patient_id}")
        
        # Check access - doctors can only access patients from their assigned clinics
        patient = Patient.query.get_or_404(quiz.patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Find the PDF file for this quiz using the direct file_id reference
        file_record = None
        if quiz.file_id:
            file_record = File.query.get(quiz.file_id)
        
        if file_record:
            # Generate presigned URL for S3 file
            presigned_url = generate_presigned_url_for_viewing(file_record.s3_key, inline=True)
            if presigned_url:
                return redirect(presigned_url)
            else:
                return jsonify({'success': False, 'error': 'Failed to generate presigned URL'}), 500
        else:
            # Fallback: generate PDF on-demand if file not found
            current_app.logger.warning(f"PDF file not found for quiz {quiz_id}, generating on-demand")
            quiz_data = json.loads(quiz.quiz_input) if quiz.quiz_input else {}
            return generate_dentist_treatment_quiz_pdf(quiz, patient, quiz_data, quiz.language)
            
    except Exception as e:
        current_app.logger.error(f"DEBUG: Exception in get_dentist_treatment_quiz_pdf: {str(e)}")
        import traceback
        current_app.logger.error(f"DEBUG: Error traceback: {traceback.format_exc()}")
        return f"Error serving PDF: {str(e)}", 500

@conversion_quiz_agent.route('/dentist-treatment-quiz')
@login_required
def dentist_treatment_quiz_home():
    """Main entry point for dentist treatment quiz system"""
    return redirect(url_for('conversion_quiz_agent.show_dentist_treatment_quiz'))



@conversion_quiz_agent.route('/test-dentist-quiz')
def test_dentist_quiz_route():
    """Test route to verify the blueprint is working"""
    return jsonify({
        'success': True,
        'message': 'Dentist treatment quiz routes are working!',
        'available_routes': [
            '/dentist-treatment-quiz',
            '/dentist-treatment-quiz/select-patient',
            '/dentist-treatment-quiz/<patient_id>',
            '/dentist-treatment-quiz/submit',
            '/dentist-treatment-quiz/history/<patient_id>'
        ]
    })

@conversion_quiz_agent.route('/admin/dentist-treatment-quizzes')
@login_required
def admin_dentist_treatment_quizzes():
    """Admin view of dentist treatment quizzes - doctors see only quizzes for patients from their assigned clinics"""
    from flask_app.models import DentistTreatmentQuiz, Patient, Dentist
    import os

    try:
        if current_user.role == 'admin':
            # Admin can see all quizzes
            quizzes = db.session.query(DentistTreatmentQuiz).join(
                Patient, DentistTreatmentQuiz.patient_id == Patient.id
            ).join(
                Dentist, DentistTreatmentQuiz.dentist_id == Dentist.id
            ).order_by(DentistTreatmentQuiz.created_at.desc()).all()
        elif current_user.role in ['Dentist', 'dentist', 'Dentists']:
            # Dentists see only quizzes for patients from clinics they are assigned to
            dentist_clinic_ids = current_user.get_clinic_ids()
            if dentist_clinic_ids:
                quizzes = (db.session.query(DentistTreatmentQuiz)
                    .join(Patient, DentistTreatmentQuiz.patient_id == Patient.id)
                    .join(Dentist, DentistTreatmentQuiz.dentist_id == Dentist.id)
                    .filter(
                        db.or_(
                            Patient.clinic_id.in_(dentist_clinic_ids),
                            db.and_(
                                Patient.clinic_id.is_(None),
                                Patient.dentist_id.isnot(None),
                                db.exists().where(
                                    db.and_(
                                        dentist_clinic_association.c.dentist_id == Patient.dentist_id,
                                        dentist_clinic_association.c.clinic_id.in_(dentist_clinic_ids),
                                    )
                                ),
                            ),
                        )
                    )
                    .order_by(DentistTreatmentQuiz.created_at.desc())
                    .all())
            else:
                quizzes = []
        else:
            quizzes = []
        
        # Get base_url for template
        base_url = os.getenv('BASE_URL', 'https://app.vizbriz.com')
        
        return render_template('admin_dentist_treatment_quizzes.html', quizzes=quizzes, base_url=base_url)
        
    except Exception as e:
        print(f"Error in admin_dentist_treatment_quizzes: {str(e)}")
        return f"Error loading page: {str(e)}", 500

@conversion_quiz_agent.route('/dentist-treatment-quiz/analytics')
@login_required
def dentist_treatment_quiz_analytics():
    """Show analytics dashboard for dentist treatment quizzes"""
    from flask_app.models import DentistTreatmentQuiz
    from datetime import datetime, timedelta
    import json
    
    # Get date range filter
    date_filter = request.args.get('date_range', 'month')
    
    # Calculate date range
    end_date = datetime.utcnow()
    if date_filter == 'week':
        start_date = end_date - timedelta(days=7)
    elif date_filter == 'month':
        start_date = end_date - timedelta(days=30)
    elif date_filter == 'quarter':
        start_date = end_date - timedelta(days=90)
    else:
        start_date = end_date - timedelta(days=30)  # Default to month
    
    # Build query with access control
    query = DentistTreatmentQuiz.query.filter(
        DentistTreatmentQuiz.created_at >= start_date,
        DentistTreatmentQuiz.created_at <= end_date
    )
    
    # Apply clinic-based access control - doctors see only patients from their assigned clinics
    if current_user.role != 'admin':
        from flask_app.models import Patient
        dentist_clinic_ids = current_user.get_clinic_ids()
        if dentist_clinic_ids:
            patient_ids = [p.id for p in Patient.query.filter(
                db.or_(
                    Patient.clinic_id.in_(dentist_clinic_ids),
                    db.and_(
                        Patient.clinic_id.is_(None),
                        Patient.dentist_id.isnot(None),
                        db.exists().where(
                            db.and_(
                                dentist_clinic_association.c.dentist_id == Patient.dentist_id,
                                dentist_clinic_association.c.clinic_id.in_(dentist_clinic_ids),
                            )
                        ),
                    ),
                ),
                Patient.status != 'Archived',
            ).all()]
            if patient_ids:
                query = query.filter(DentistTreatmentQuiz.patient_id.in_(patient_ids))
            else:
                query = query.filter(False)
        else:
            query = query.filter(False)  # No clinic associations - no access
    
    quizzes = query.order_by(DentistTreatmentQuiz.created_at.desc()).all()
    
    # Calculate analytics
    analytics = {
        'total_quizzes': len(quizzes),
        'language_breakdown': {},
        'status_breakdown': {},
        'patients_assessed': set(),
        'recent_activity': [],
        'common_findings': {}
    }
    
    for quiz in quizzes:
        # Language breakdown
        lang = quiz.language
        analytics['language_breakdown'][lang] = analytics['language_breakdown'].get(lang, 0) + 1
        
        # Status breakdown
        status = quiz.status
        analytics['status_breakdown'][status] = analytics['status_breakdown'].get(status, 0) + 1
        
        # Patients assessed
        analytics['patients_assessed'].add(quiz.patient_id)
        
        # Recent activity
        if len(analytics['recent_activity']) < 10:
            analytics['recent_activity'].append({
                'id': quiz.id,
                'patient_id': quiz.patient_id,
                'created_at': quiz.created_at,
                'language': quiz.language,
                'status': quiz.status
            })
        
        # Analyze quiz data for common findings
        try:
            quiz_data = json.loads(quiz.quiz_input)
            for key, value in quiz_data.items():
                if isinstance(value, dict) and 'value' in value:
                    value = value['value']
                if key not in analytics['common_findings']:
                    analytics['common_findings'][key] = {}
                if value not in analytics['common_findings'][key]:
                    analytics['common_findings'][key][value] = 0
                analytics['common_findings'][key][value] += 1
        except:
            pass
    
    # Convert set to count
    analytics['unique_patients'] = len(analytics['patients_assessed'])
    
    return render_template('dentist_treatment_quiz_analytics.html', 
                         analytics=analytics,
                         date_filter=date_filter,
                         start_date=start_date,
                         end_date=end_date)
