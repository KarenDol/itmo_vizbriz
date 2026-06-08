import json
from datetime import datetime
from flask_app.extensions import db
from flask_app.models import ConversionQuiz, ObservationStore, PatientObservation, Patient, Dentist, Clinic, dentist_dso_association
import os
from flask import current_app
from sqlalchemy import text
import secrets

# Determine current AI provider from environment variable (default to openai)
CURRENT_AI_PROVIDER = os.environ.get("LLM_PROVIDER", "openai")


def get_or_create_default_dentist():
    """
    Get the first available dentist or create a default one if none exists.
    Returns the dentist ID.
    """
    # Try to find any existing dentist
    dentist = Dentist.query.first()
    
    if not dentist:
        # Create a default dentist
        dentist = Dentist(
            name="Default Dentist",
            email="default@vizbriz.com",
            password="changeme",  # This should be changed by the admin
            status="active",
            role="dentist",
            country="US"  # Default country
        )
        dentist.set_password("changeme")  # Set the password hash
        db.session.add(dentist)
        db.session.flush()  # Get the ID without committing
    
    return dentist.id


def get_or_create_patient(patient_email, clinic_id=None):
    """
    Get existing patient by email or create a new one if not found.
    Returns the patient ID.
    """
    try:
        # Try to find existing patient
        patient = Patient.query.filter_by(email=patient_email).first()
        
        if not patient:
            # No default dentist ID - let system determine based on DSO/clinic
            dentist_id = None
            current_app.logger.info(f"DEBUG: get_or_create_patient - email={patient_email}, clinic_id={clinic_id}")

            # Try to find a dentist for the clinic's DSO
            if clinic_id:
                clinic = Clinic.query.get(clinic_id)
                if clinic and clinic.dso_id:
                    # Find any dentist associated with this DSO
                    dentist = (
                        Dentist.query
                        .join(dentist_dso_association, Dentist.id == dentist_dso_association.c.dentist_id)
                        .filter(dentist_dso_association.c.dso_id == clinic.dso_id)
                        .first()
                    )
                    if dentist:
                        dentist_id = dentist.id
                        current_app.logger.info(f"DEBUG: Found dentist {dentist_id} for DSO {clinic.dso_id}")
                    else:
                        current_app.logger.info(f"DEBUG: No dentist found for DSO {clinic.dso_id}")
                else:
                    current_app.logger.info(f"DEBUG: Clinic {clinic_id} not found or has no DSO")
            
            # Fallback to any dentist if none found
            if dentist_id is None:
                fallback_dentist = Dentist.query.first()
                if fallback_dentist:
                    dentist_id = fallback_dentist.id
                    current_app.logger.info(f"DEBUG: Using fallback dentist {dentist_id}")
                else:
                    current_app.logger.info(f"DEBUG: No dentists found in database")
            # Create new patient
            patient = Patient(
                name="Quiz Respondent",
                email=patient_email,
                status="New",
                dentist_id=dentist_id,
                clinic_id=clinic_id,
                create_date=datetime.utcnow(),
                last_update=datetime.utcnow(),
                upload_token=secrets.token_urlsafe(32)
            )
            db.session.add(patient)
            db.session.commit()
            current_app.logger.info(f"DEBUG: Patient created - ID={patient.id}, dentist_id={patient.dentist_id}, clinic_id={patient.clinic_id}")
            current_app.logger.info(f"Created new patient with ID: {patient.id}, email: {patient_email}, clinic_id: {clinic_id}, dentist_id: {dentist_id}")
        else:
            # Update existing patient's clinic_id if provided and not already set
            if clinic_id and not patient.clinic_id:
                patient.clinic_id = clinic_id
                db.session.commit()
                current_app.logger.info(f"Updated existing patient {patient.id} with clinic_id: {clinic_id}")
            else:
                current_app.logger.info(f"Found existing patient with ID: {patient.id} and email: {patient_email}")
        
        return patient.id
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in get_or_create_patient: {str(e)}")
        raise


def calculate_quiz_score(answers):
    """
    Calculate the sleep apnea risk score based on the provided answers.

    Args:
        answers (dict): Dictionary containing quiz answers

    Returns:
        tuple: (total_score, risk_label, risk_explanation, observations)
    """
    observations = []
    total_score = 0

    def add_observation(name, value, score, explanation):
        observations.append({
            "observation": name,
            "value": value,
            "unit": None,
            "score": score,
            "evidence": explanation,
            "confidence": 100,
            "source": "quiz-scoring-v1"
        })

    # Snoring (Question 1)
    if answers.get("snoring") in ["often", "always"]:
        add_observation("Snoring Frequency", answers["snoring"], 2, "Frequent snoring is a major OSA risk factor")
        total_score += 2
    elif answers.get("snoring") == "sometimes":
        add_observation("Snoring Frequency", answers["snoring"], 1, "Intermittent snoring is a moderate risk factor")
        total_score += 1

    # Tiredness (Question 2)
    if answers.get("tiredness") in ["often", "always"]:
        add_observation("Daytime Fatigue", answers["tiredness"], 2, "Frequent daytime fatigue suggests sleep disruption")
        total_score += 2
    elif answers.get("tiredness") == "sometimes":
        add_observation("Daytime Fatigue", answers["tiredness"], 1, "Occasional fatigue may indicate sleep issues")
        total_score += 1

    # Observed Apnea (Question 3)
    if answers.get("observed_apnea") in ["often", "always"]:
        add_observation("Witnessed Apneas", answers["observed_apnea"], 3, "Frequent witnessed apneas are a strong risk indicator")
        total_score += 3
    elif answers.get("observed_apnea") == "sometimes":
        add_observation("Witnessed Apneas", answers["observed_apnea"], 2, "Occasional witnessed apneas suggest potential OSA")
        total_score += 2

    # Blood Pressure (Question 4)
    if answers.get("blood_pressure") == "yes":
        add_observation("High Blood Pressure", "Yes", 1, "Hypertension is associated with increased OSA risk")
        total_score += 1

    # Weight (Question 5)
    if answers.get("weight") == "obese":
        add_observation("BMI Category", answers["weight"], 2, "Obesity is a major risk factor for OSA")
        total_score += 2
    elif answers.get("weight") == "overweight":
        add_observation("BMI Category", answers["weight"], 1, "Overweight status increases OSA risk")
        total_score += 1

    # Neck Circumference (Question 6)
    if answers.get("neck_circumference") == "yes":
        add_observation("Large Neck Circumference", "Yes", 1, "Large neck circumference is associated with OSA risk")
        total_score += 1

    # Gender (Question 7)
    if answers.get("gender") == "male":
        add_observation("Gender", "Male", 1, "Male gender is associated with higher OSA risk")
        total_score += 1

    # Age (Question 8)
    if answers.get("age") == "over_50":
        add_observation("Age", "Over 50", 1, "Age over 50 is associated with increased OSA risk")
        total_score += 1

    # Daytime Sleepiness (Question 9)
    if answers.get("daytime_sleepiness") == "yes":
        add_observation("Daytime Sleepiness", "Yes", 1, "Daytime sleepiness is a common OSA symptom")
        total_score += 1

    # Driving Fatigue (Question 10)
    if answers.get("driving_fatigue") == "yes":
        add_observation("Driving Fatigue", "Yes", 2, "Falling asleep while driving is a serious OSA symptom")
        total_score += 2

    # Treatment Attempts (Question 11)
    if answers.get("treatment_attempts") == "yes":
        add_observation("Previous Treatment", "Yes", 1, "Previous treatment attempts may indicate OSA history")
        total_score += 1

    # Determine risk label based on total score
    if total_score >= 8:
        risk_label = "High"
        risk_explanation = "Multiple strong risk factors suggest a high likelihood of OSA. A sleep study is strongly recommended."
    elif total_score >= 5:
        risk_label = "Moderate"
        risk_explanation = "Several risk factors are present. A clinical assessment and possible sleep study are recommended."
    else:
        risk_label = "Low"
        risk_explanation = "Few risk factors are present, but OSA cannot be ruled out without a professional evaluation."

    # Final observation for total score and risk
    add_observation("Total Risk Score", total_score, total_score, "Calculated from weighted quiz responses")
    add_observation("Risk Category", risk_label, 0, risk_explanation)

    return total_score, risk_label, risk_explanation, observations


def get_clinic_email_from_dso_id(dso_id):
    """
    Look up the clinic email based on DSO ID.
    Returns the first clinic's email found for the DSO, or default email if none found.
    """
    if not dso_id:
        current_app.logger.info("No DSO ID provided, using default clinic email")
        return 'info@vizbriz.com'
    
    try:
        from flask_app.models import Clinic
        current_app.logger.info(f"Looking up clinics for DSO ID: {dso_id}")
        
        # Find the first active clinic for this DSO
        clinic = Clinic.query.filter_by(dso_id=dso_id, status='active').first()
        
        if clinic and clinic.email:
            current_app.logger.info(f"Found clinic: {clinic.name} with email: {clinic.email}")
            return clinic.email
        else:
            current_app.logger.info(f"No active clinic found for DSO ID {dso_id}, using default email")
            return 'info@vizbriz.com'
            
    except Exception as e:
        current_app.logger.error(f"Error looking up clinic email for DSO {dso_id}: {str(e)}")
        return 'info@vizbriz.com'


def get_clinic_email_and_dentist_id(clinic_name):
    """
    Look up the clinic's email and dentist ID based on the clinic name.
    Returns a tuple of (clinic_email, dentist_id).
    If no match is found, returns (None, 133) for the default dentist.
    """
    if not clinic_name:
        current_app.logger.info(f"No clinic name provided, using default dentist ID 133")
        return None, 133  # Default dentist ID

    current_app.logger.info(f"Looking up clinic name: '{clinic_name}'")
    
    # First try to find by DSO name
    dentist = Dentist.query.filter(Dentist.DSO.ilike(f'%{clinic_name}%')).first()
    current_app.logger.info(f"DSO lookup result: {dentist}")
    
    # If no DSO match, try to find by dentist name
    if not dentist:
        dentist = Dentist.query.filter(Dentist.name.ilike(f'%{clinic_name}%')).first()
        current_app.logger.info(f"Dentist name lookup result: {dentist}")
    
    if dentist:
        current_app.logger.info(f"Found dentist: {dentist.name} (ID: {dentist.id}) with DSO: {dentist.DSO}")
        return dentist.email, dentist.id
    
    current_app.logger.info(f"No dentist found for '{clinic_name}', using default dentist ID 133")
    return None, 133  # Default dentist ID


def store_quiz_data(user_id, quiz_answers, cta, clinic_email, patient_email, ai_response, client_risk_score=None, quiz_type='basic_quiz', clinic_id=None, referral_doctor=None):
    try:
        current_app.logger.info(f"store_quiz_data called with patient_email: {patient_email}, quiz_type: {quiz_type}")
        
        # Look up clinic email and dentist ID using fuzzy search only if clinic_email is not provided
        if not clinic_email and quiz_answers.get('doctor_referral'):
            clinic_email, dentist_id = get_clinic_email_and_dentist_id(quiz_answers.get('doctor_referral', ''))
        elif not clinic_email:
            # Use default clinic email if none provided
            clinic_email = 'info@vizbriz.com'
            dentist_id = None  # No default dentist ID
        else:
            # Use provided clinic_email, but still need to get dentist_id
            _, dentist_id = get_clinic_email_and_dentist_id(quiz_answers.get('doctor_referral', ''))
        
        current_app.logger.info(f"Using clinic_email: {clinic_email}, dentist_id: {dentist_id}")
        
        # Create or update patient record
        patient = Patient.query.filter_by(email=patient_email).first()
        current_app.logger.info(f"Existing patient lookup result: {patient}")
        
        if not patient:
            current_app.logger.info(f"Creating new patient with email: {patient_email}")
            
            patient = Patient(
                name=quiz_answers.get('full_name'),
                email=patient_email,
                phone=quiz_answers.get('phone'),
                gender=quiz_answers.get('gender'),
                dentist_id=dentist_id,  # Use looked up dentist ID
                create_date=datetime.utcnow(),
                last_update=datetime.utcnow(),
                upload_token=secrets.token_urlsafe(32)  # Generate upload token for new patients
            )
            current_app.logger.info(f"New patient object created: {patient}")
            db.session.add(patient)
            db.session.flush()  # Get the patient ID
            current_app.logger.info(f"New patient added to session with ID: {patient.id}")
        else:
            current_app.logger.info(f"Updating existing patient with ID: {patient.id}")
            # Update existing patient if gender is provided
            if quiz_answers.get('gender'):
                patient.gender = quiz_answers.get('gender')
                patient.last_update = datetime.utcnow()
            # Update dentist_id if a referral was provided
            if quiz_answers.get('doctor_referral'):
                patient.dentist_id = dentist_id  # Update to the looked-up dentist
                current_app.logger.info(f"Updated patient dentist_id to: {dentist_id}")
            # Ensure existing patients also have an upload token
            if not patient.upload_token:
                patient.upload_token = secrets.token_urlsafe(32)

        # Store quiz data
        quiz_input = json.dumps(quiz_answers)
        new_quiz = ConversionQuiz(
            user_id=patient.id,
            quiz_input=quiz_input,
            cta=cta,
            clinic_email=clinic_email,  # Use the clinic_email parameter
            patient_email=patient_email,  # Use the patient_email parameter
            ai_response=ai_response,
            quiz_type=quiz_type,
            clinic_id=clinic_id,  # Store the clinic ID
            referral_doctor=referral_doctor or quiz_answers.get('doctor_referral'),  # Store referral doctor
            created_at=datetime.utcnow()
        )
        db.session.add(new_quiz)
        current_app.logger.info(f"Quiz data added to session for patient ID: {patient.id}, quiz_type: {quiz_type}")
        db.session.commit()
        current_app.logger.info(f"Database commit successful. Quiz ID: {new_quiz.id}, Patient ID: {patient.id}")
        return new_quiz.id
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error storing quiz data: {str(e)}")
        current_app.logger.error(f"Exception type: {type(e)}")
        import traceback
        current_app.logger.error(f"Traceback: {traceback.format_exc()}")
        raise


def lookup_clinic_email_by_referral(referral_name):
    """
    Looks up the clinic/dentist email by referral name using DSO or dentist name.
    Returns the email if found, otherwise returns the default clinic email from env.
    """
    if not referral_name:
        return os.getenv('DEFAULT_CLINIC_EMAIL', 'default@vizbriz.com')
    try:
        email, _ = get_clinic_email_and_dentist_id(referral_name)
        return email or os.getenv('DEFAULT_CLINIC_EMAIL', 'default@vizbriz.com')
    except Exception as e:
        current_app.logger.error(f"Clinic email lookup failed: {str(e)}")
        return os.getenv('DEFAULT_CLINIC_EMAIL', 'default@vizbriz.com')


def save_observations_to_store(patient_id, quiz_id, observations, source_type='quiz', section='advanced_assessment'):
    """
    Save observations to the observation_store table.
    """
    from flask_app.models import ObservationStore
    from flask import current_app
    
    try:
        for observation in observations:
            # Handle both 'name' and 'observation' keys for compatibility
            observation_name = observation.get('name') or observation.get('observation', 'Unknown Observation')
            
            observation_store = ObservationStore(
                patient_id=patient_id,
                quiz_id=quiz_id,
                source_type=source_type,
                source_text=f"Advanced Assessment - {observation_name}",
                extracted_observations=observation,
                provider='quiz-scoring-phase2',
                section=section,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.session.add(observation_store)
        
        db.session.commit()
        current_app.logger.info(f"Saved {len(observations)} observations to observation_store for patient {patient_id}, quiz {quiz_id}")
        return True
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error saving observations to store: {str(e)}")
        return False


def evaluate_phase_2(part_a_answers, part_b_answers):
    """
    Evaluates Phase 2 (advanced assessment) using combined scoring from Part A and Part B.
    Implements the comprehensive scoring logic from the PRD.
    """
    def add_observation(name, value, score, explanation):
        observations.append({
            "name": name,
            "value": value,
            "score": score,
            "explanation": explanation,
            "evidence": explanation,
            "confidence": 100,
            "source": "quiz-scoring-phase2"
        })

    observations = []
    total_score = 0
    red_flags = []
    
    # ===== PART A SCORING (carry over from phase 1) =====
    part_a_score, part_a_risk, part_a_explanation, part_a_observations = calculate_quiz_score(part_a_answers)
    total_score += part_a_score
    observations.extend(part_a_observations)
    
    # Check Part A red flags
    if part_a_answers.get("observed_apnea") in ["often", "always"]:
        red_flags.append("Witnessed apneas or gasping during sleep")
    if part_a_answers.get("driving_fatigue") == "yes":
        red_flags.append("Trouble staying awake while driving")
    if part_a_answers.get("blood_pressure") == "yes":
        red_flags.append("High blood pressure")
    if part_a_answers.get("weight") in ["overweight", "obese"]:
        red_flags.append("BMI ≥ 25")
    if part_a_answers.get("diagnosed_osa") == "yes":
        red_flags.append("Already diagnosed with OSA")
    
    # ===== PART B SCORING =====
    
    # 1. Sleep Patterns & Quality Section
    sleep_symptoms_count = 0
    if part_b_answers.get("trouble_falling_asleep_again") == "yes":
        sleep_symptoms_count += 1
        add_observation("Trouble falling back asleep", "Yes", 0, "Difficulty returning to sleep after waking")
    
    if part_b_answers.get("mouth_breathing") == "yes":
        sleep_symptoms_count += 1
        add_observation("Mouth breathing at night", "Yes", 0, "Mouth breathing can indicate airway obstruction")
    
    if part_b_answers.get("day_sleepy") == "yes":
        sleep_symptoms_count += 1
        add_observation("Daytime sleepiness", "Yes", 0, "Excessive daytime sleepiness is a key OSA symptom")
    
    if part_b_answers.get("napping") == "yes":
        sleep_symptoms_count += 1
        add_observation("Daytime napping", "Yes", 0, "Frequent napping may indicate poor sleep quality")
    
    if part_b_answers.get("night_urination") == "yes":
        sleep_symptoms_count += 1
        add_observation("Nighttime urination", "Yes", 0, "Frequent nighttime urination can be OSA-related")
    
    # Sleep symptoms scoring
    if sleep_symptoms_count >= 3:
        total_score += 2
        add_observation("Sleep Symptoms Section", f"{sleep_symptoms_count} symptoms", 2, "Multiple sleep symptoms indicate potential OSA")
    elif sleep_symptoms_count >= 1:
        total_score += 1
        add_observation("Sleep Symptoms Section", f"{sleep_symptoms_count} symptoms", 1, "Some sleep symptoms present")
    
    # 2. TMJ/Bruxism Section
    tmj_symptoms_count = 0
    if part_b_answers.get("tmj_problems") == "yes":
        tmj_symptoms_count += 1
        add_observation("TMJ problems", "Yes", 0, "TMJ issues can be related to sleep bruxism")
    
    if part_b_answers.get("jaw_pain_morning") == "yes":
        tmj_symptoms_count += 1
        add_observation("Morning jaw pain", "Yes", 0, "Morning jaw pain may indicate nighttime clenching")
    
    if part_b_answers.get("worn_teeth") == "yes":
        tmj_symptoms_count += 1
        add_observation("Worn teeth", "Yes", 0, "Tooth wear can indicate bruxism during sleep")
    
    # Check for ear symptoms
    ear_symptoms = part_b_answers.get("ear_symptoms", [])
    if isinstance(ear_symptoms, str):
        ear_symptoms = [ear_symptoms] if ear_symptoms else []
    if ear_symptoms:
        tmj_symptoms_count += 1
        add_observation("Ear symptoms", ", ".join(ear_symptoms), 0, "Ear symptoms can be TMJ-related")
    
    # TMJ/Bruxism scoring
    if tmj_symptoms_count >= 2:
        total_score += 2
        add_observation("TMJ/Bruxism Section", f"{tmj_symptoms_count} symptoms", 2, "Multiple TMJ/bruxism symptoms")
        red_flags.append("TMJ/Bruxism section score ≥ 2")
    elif tmj_symptoms_count >= 1:
        total_score += 1
        add_observation("TMJ/Bruxism Section", f"{tmj_symptoms_count} symptoms", 1, "Some TMJ/bruxism symptoms")
    
    # 3. Lifestyle Factors
    if part_b_answers.get("tobacco") == "yes":
        total_score += 1
        add_observation("Tobacco use", "Yes", 1, "Tobacco use increases OSA risk")
    
    alcohol_use = part_b_answers.get("alcohol_use", "none")
    if alcohol_use == "daily":
        total_score += 3
        add_observation("Alcohol use", "Daily/heavy", 3, "Heavy alcohol use significantly increases OSA risk")
    elif alcohol_use == "3_6":
        total_score += 2
        add_observation("Alcohol use", "3-6 drinks/week", 2, "Moderate alcohol use increases OSA risk")
    elif alcohol_use == "1_2":
        total_score += 1
        add_observation("Alcohol use", "1-2 drinks/week", 1, "Light alcohol use may affect sleep")
    
    if part_b_answers.get("sedatives") == "yes":
        total_score += 1
        add_observation("Sedative use", "Yes", 1, "Sedative use can affect breathing during sleep")
    
    # 4. Family History
    if part_b_answers.get("family_history") == "yes":
        total_score += 1
        add_observation("Family history", "Yes", 1, "Family history of sleep disorders increases OSA risk")
    
    # 5. Treatment Goals
    goals = part_b_answers.get("goals", [])
    if isinstance(goals, str):
        goals = [goals] if goals else []
    
    goals_count = len(goals)
    if goals_count >= 5:
        total_score += 3
        add_observation("Treatment Goals", f"{goals_count} goals", 3, "Multiple treatment goals indicate significant sleep concerns")
        red_flags.append("Treatment goals section score ≥ 2")
    elif goals_count >= 2:
        total_score += 2
        add_observation("Treatment Goals", f"{goals_count} goals", 2, "Several treatment goals identified")
        red_flags.append("Treatment Goals section score ≥ 2")
    elif goals_count >= 1:
        total_score += 1
        add_observation("Treatment Goals", f"{goals_count} goals", 1, "Some treatment goals identified")
    
    # ===== FINAL RISK ASSESSMENT =====
    
    # Check for diagnosed OSA and treatment status
    diagnosed_osa = part_a_answers.get("diagnosed_osa") == "yes"
    
    if diagnosed_osa:
        # Diagnosed patient logic
        using_treatment = part_a_answers.get("using_cpap") == "yes"
        
        if using_treatment:
            # Check if still symptomatic
            still_symptomatic = (
                part_a_answers.get("observed_apnea") in ["often", "always"] or
                part_a_answers.get("driving_fatigue") == "yes" or
                part_a_answers.get("daytime_sleepiness") == "yes" or
                part_b_answers.get("day_sleepy") == "yes" or
                part_b_answers.get("napping") == "yes" or
                sleep_symptoms_count >= 2
            )
            
            if still_symptomatic:
                risk_level = "Diagnosed – Using but Symptomatic"
                risk_explanation = "You reported that you're currently using CPAP or another treatment for sleep apnea — but your answers suggest you're still experiencing symptoms."
            else:
                risk_level = "Diagnosed – Using & No Symptoms"
                risk_explanation = "You indicated that you've been diagnosed with sleep apnea and are currently undergoing treatment — and your answers show no major active symptoms."
        else:
            risk_level = "Diagnosed – Not Using Treatment"
            risk_explanation = "You reported that you've been diagnosed with obstructive sleep apnea — but you're not currently using any form of treatment."
    else:
        # Standard risk assessment
        if len(red_flags) >= 2:
            risk_level = "High"
            risk_explanation = "Multiple red flags indicate a high likelihood of OSA."
        elif len(red_flags) >= 1:
            risk_level = "High"
            risk_explanation = f"Red flag detected: {red_flags[0]}. This indicates a high likelihood of OSA."
        elif total_score >= 7:
            risk_level = "High"
            risk_explanation = "Your symptoms, lifestyle factors, and medical history suggest that OSA may already be affecting your sleep, heart, brain, and overall well-being."
        elif total_score >= 4:
            risk_level = "Moderate"
            risk_explanation = "Some of your symptoms and health factors may already be affecting your sleep, energy, and overall health."
        else:
            risk_level = "Low"
            risk_explanation = "Your answers suggest a low risk for obstructive sleep apnea (OSA)."
    
    # Check for snoring message trigger
    snoring_triggered = part_a_answers.get("snoring") in ["often", "always", "sometimes"]
    
    # Final observation for total score
    add_observation("Total Combined Score", total_score, total_score, f"Combined score from Part A ({part_a_score}) and Part B ({total_score - part_a_score})")
    add_observation("Risk Category", risk_level, 0, risk_explanation)
    
    if red_flags:
        add_observation("Red Flags", ", ".join(red_flags), 0, "Red flags override standard scoring")
    
    return total_score, risk_level, risk_explanation, observations, snoring_triggered, red_flags


def get_phase_2_risk_message(risk_level, snoring_triggered=False):
    """
    Returns the appropriate message for Phase 2 risk levels according to the PRD.
    """
    messages = {
        "Low": {
            "title": "🟢 Final Result: Low Risk",
            "message": """Your answers suggest a low risk for obstructive sleep apnea (OSA).
That's good news — but it doesn't completely rule out the possibility of a sleep-related issue.
Some symptoms of OSA can develop gradually or may go unnoticed in the early stages.
Since you did report some symptoms or concerns, it's worth discussing your results with your dentist to decide if any further steps are needed.
If you continue to experience fatigue, poor sleep, snoring, or changes in focus, energy, or mood — don't ignore it.
Take the next step toward better sleep and better health.
Keep paying attention — your sleep matters."""
        },
        "Moderate": {
            "title": "🟡 Final Result: Moderate Risk",
            "message": """Your answers suggest a moderate risk for obstructive sleep apnea (OSA).
Some of your symptoms and health factors may already be affecting your sleep, energy, and overall health — even if the condition is only mild to moderate.
If left unaddressed, OSA can worsen over time and increase your risk for fatigue, memory issues, and cardiovascular problems.
Don't ignore the signs.
The good news is that effective treatment options are available — including oral appliance therapy, PAP therapy, lifestyle changes, and, in some cases, surgery.
Take the next step toward better sleep and better health.
Take this seriously — even moderate signs can have long-term impact."""
        },
        "High": {
            "title": "🔴 Final Result: High Risk",
            "message": """Your answers indicate a high likelihood of obstructive sleep apnea (OSA).
Your symptoms, lifestyle factors, and medical history suggest that this condition may already be affecting your sleep, heart, brain, and overall well-being.
Untreated OSA can lead to serious complications — including high blood pressure, cardiovascular disease, memory issues, and reduced quality of life.
You should not wait.
The good news is that effective treatment options are available — including oral appliance therapy, PAP therapy, lifestyle changes, and, in some cases, surgery.
Taking action now can help you sleep better, protect your health, and feel more energized throughout the day.
Take the next step toward better sleep and better health.
Take this seriously — your health depends on it."""
        },
        "Diagnosed – Not Using Treatment": {
            "title": "🩺 Final Result: Diagnosed with OSA — Not Currently Using Treatment",
            "message": """You reported that you've been diagnosed with obstructive sleep apnea — but you're not currently using any form of treatment.
That means your condition may be unmanaged, or that you've had difficulty continuing with previous recommendations.
Untreated or undertreated OSA can impact more than just your sleep — it can affect memory, energy levels, cardiovascular health, and overall well-being.
The good news is that several effective and personalized treatment approaches are available — including oral appliance therapy, PAP therapy, and lifestyle changes.
Don't delay.
Taking action now can help you sleep better, restore your energy, and reduce the serious health risks linked to untreated or undertreated OSA.
Take the next step toward better sleep and better health.
You deserve to feel better.
Take this seriously — your health depends on it."""
        },
        "Diagnosed – Using but Symptomatic": {
            "title": "🩺 Final Result: Diagnosed with OSA — Using CPAP or Other Treatment but Still Symptomatic",
            "message": """You reported that you're currently using CPAP or another treatment for sleep apnea — but your answers suggest you're still experiencing symptoms.
This may mean your current treatment isn't fully effective, or that your needs have changed.
OSA management isn't one-size-fits-all — and sometimes it takes reassessment to find what really works.
Don't settle for "just okay."
There are multiple effective options available — including oral appliance therapy, PAP therapy, and surgical or lifestyle modifications.
Taking action now can help you sleep better, restore your energy, and reduce your long-term health risks.
You deserve to feel better.
Take this seriously — your health depends on it."""
        },
        "Diagnosed – Using & No Symptoms": {
            "title": "🩺 Final Result: Diagnosed with OSA — Using Treatment and No Current Symptoms",
            "message": """You indicated that you've been diagnosed with sleep apnea and are currently undergoing treatment — and your answers show no major active symptoms.
That's a great sign — and it likely means your therapy is working.
Still, this assessment covers only a limited set of indicators.
Some issues may develop silently or go unnoticed, even during treatment.
Stay proactive — your sleep health is worth protecting.
Great sleep should feel great every day."""
        }
    }
    
    risk_message = messages.get(risk_level, messages["Low"])
    # Remove test string for email tracing
    # Add snoring message if triggered
    if snoring_triggered:
        snoring_message = """
📢 Snoring Message
Did you know that loud snoring can increase your risk of stroke?
If you or someone close to you has noticed heavy snoring, it may be a sign of airway obstruction during sleep.
And beyond that — the vibrations from snoring themselves can pose a health risk.
Snoring can also affect your relationships, social life, and the sleep of those around you.
The good news? It's treatable — and you don't have to deal with it alone."""
        return risk_message["title"], risk_message["message"] + "\n\n" + snoring_message
    
    return risk_message["title"], risk_message["message"]


def get_phase_2_cta_buttons(risk_level):
    """
    Returns the appropriate CTA buttons for Phase 2 risk levels.
    Only shows consultation button for all risk levels.
    """
    # Only show consultation button for all risk levels
    return [
        "📌 Schedule a consultation with a sleep dental specialist to discuss your results and explore next steps.",
        "🔘 [Schedule a Consult]"
    ]
