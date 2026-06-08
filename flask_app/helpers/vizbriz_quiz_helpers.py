"""
VizBriz Multilingual Quiz Helper Functions
Handles scoring, evaluation, localization, and data storage for VizBriz sleep quiz
"""

import json
import os
import re
from xml.sax.saxutils import escape
from datetime import datetime
import io
from flask import current_app
from flask_app.extensions import db
from flask_app.models import VizBrizQuiz, Patient, Clinic, ObservationStore
import secrets
from typing import Any, Dict, Optional


# Cache the quiz package to avoid repeated file reads
_QUIZ_PACKAGE_CACHE = None
_FOLLOWUP_QUIZ_PACKAGE_CACHE = None

# Patient-facing strings for outcome_message_id + ".title"/".body" and CTA ids when manifest i18n is not wired.
# evaluate_quiz() calls get_localized_text() for these; returning the raw key leaks into PDFs/reports/emails.
_OUTCOME_AND_CTA_FALLBACKS_EN = {
    "MSG_HIGH_RISK_ACTION.title": "Higher risk — follow-up recommended",
    "MSG_HIGH_RISK_ACTION.body": (
        "Your answers suggest a higher level of concern for sleep apnea. "
        "We recommend discussing these results promptly with a qualified clinician who can advise on next steps, such as sleep testing."
    ),
    "MSG_MODERATE_RISK_ACTION.title": "Moderate risk — follow-up recommended",
    "MSG_MODERATE_RISK_ACTION.body": (
        "Your answers suggest a moderate level of sleep-related risk. "
        "Discussing these results with a clinician can help you decide on appropriate next steps, which may include a sleep evaluation."
    ),
    "MSG_LOW_RISK_ACTION.title": "Lower risk based on this screening",
    "MSG_LOW_RISK_ACTION.body": (
        "Your answers suggest a lower level of risk on this screening, but symptoms can change. "
        "If anything worries you, share these results with a clinician."
    ),
    "MSG_DIAGNOSED_NOT_TREATING.title": "Sleep apnea diagnosis — therapy not current",
    "MSG_DIAGNOSED_NOT_TREATING.body": (
        "You indicated a diagnosis of sleep apnea without consistent treatment. "
        "This is a good time to review options with a sleep clinician."
    ),
    "MSG_DIAGNOSED_TREATING_STABLE.title": "On therapy — continue follow-up",
    "MSG_DIAGNOSED_TREATING_STABLE.body": (
        "You indicated you are on treatment and feeling relatively stable. Keep regular follow-up with your care team."
    ),
    "MSG_DIAGNOSED_TREATING_SYMPTOMATIC.title": "On therapy — symptoms still present",
    "MSG_DIAGNOSED_TREATING_SYMPTOMATIC.body": (
        "You indicated you are on treatment but still have symptoms. Consider scheduling a review with your clinician to optimize therapy."
    ),
    "CTA_REFER_SLEEP_TEST": "Ask your clinician about a sleep test",
    "CTA_CONSULT_DENTIST": "Schedule a consultation with your care team",
    "CTA_MONITOR": "Continue to monitor symptoms and follow up as needed",
    "CTA_START_TREATMENT": "Discuss starting or resuming treatment",
    "CTA_CONTINUE_TREATMENT": "Continue your current treatment plan",
    "CTA_OPTIMIZE_TREATMENT": "Discuss adjusting or optimizing your therapy",
    "MSG_FOLLOWUP_THANK_YOU.title": "Thank you",
    "MSG_FOLLOWUP_THANK_YOU.body": (
        "Your follow-up questionnaire has been submitted successfully. "
        "Your care team will review your responses."
    ),
}


def clear_quiz_package_cache():
    """
    Clear the quiz package cache to force reload.
    """
    global _QUIZ_PACKAGE_CACHE, _FOLLOWUP_QUIZ_PACKAGE_CACHE
    _QUIZ_PACKAGE_CACHE = None
    _FOLLOWUP_QUIZ_PACKAGE_CACHE = None


def _static_folder_path():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'static')


def load_quiz_package():
    """
    Load the quiz package JSON from static folder.
    Returns the parsed quiz package dictionary.
    """
    global _QUIZ_PACKAGE_CACHE
    
    if _QUIZ_PACKAGE_CACHE is not None:
        return _QUIZ_PACKAGE_CACHE
    
    static_folder = _static_folder_path()
    quiz_package_path = os.path.join(static_folder, 'vizbriz_quiz_package.json')
    current_app.logger.info(f"Loading quiz package from: {quiz_package_path}")
    current_app.logger.info(f"File exists: {os.path.exists(quiz_package_path)}")
    
    try:
        with open(quiz_package_path, 'r', encoding='utf-8') as f:
            _QUIZ_PACKAGE_CACHE = json.load(f)
            current_app.logger.info(f"Loaded quiz package from {quiz_package_path}")
            current_app.logger.info(f"Quiz package keys: {list(_QUIZ_PACKAGE_CACHE.keys())}")
            current_app.logger.info(f"Has i18n key: {'i18n' in _QUIZ_PACKAGE_CACHE}")
            if 'i18n' in _QUIZ_PACKAGE_CACHE:
                current_app.logger.warning("Quiz package contains i18n key - this may cause issues")
            return _QUIZ_PACKAGE_CACHE
    except Exception as e:
        current_app.logger.error(f"Error loading quiz package: {str(e)}")
        raise


def load_followup_quiz_package():
    """Load the 1st follow-up (SQUARE) questionnaire package."""
    global _FOLLOWUP_QUIZ_PACKAGE_CACHE
    if _FOLLOWUP_QUIZ_PACKAGE_CACHE is not None:
        return _FOLLOWUP_QUIZ_PACKAGE_CACHE
    static_folder = _static_folder_path()
    quiz_package_path = os.path.join(static_folder, 'vizbriz_followup_quiz_package.json')
    try:
        with open(quiz_package_path, 'r', encoding='utf-8') as f:
            _FOLLOWUP_QUIZ_PACKAGE_CACHE = json.load(f)
            current_app.logger.info(f"Loaded follow-up quiz package from {quiz_package_path}")
            return _FOLLOWUP_QUIZ_PACKAGE_CACHE
    except Exception as e:
        current_app.logger.error(f"Error loading follow-up quiz package: {str(e)}")
        raise


def _option_label(question, value, language='en'):
    lang = (language or 'en').lower().split('-', 1)[0]
    for opt in question.get('options') or []:
        if opt.get('value') == value:
            if lang == 'he' and opt.get('label_he'):
                return opt['label_he']
            return opt.get('label') or opt.get('label_en') or str(value)
    return str(value)


def _format_answer_for_question(question, user_answer, language='en'):
    """Format a single answer for PDF/storage (localized labels for choices)."""
    qtype = question.get('type')
    if qtype == 'single_choice':
        text = _option_label(question, user_answer, language)
        other = None  # caller may append _other_text from answers
        return text
    if qtype == 'multi_choice' and isinstance(user_answer, list):
        parts = [_option_label(question, v, language) for v in user_answer]
        return ', '.join(parts)
    if isinstance(user_answer, list):
        return ', '.join(str(v) for v in user_answer)
    return str(user_answer) if user_answer is not None else ''


def _prepend_patient_identity_to_enhanced(enhanced, answers, patient=None):
    """Add name/email rows at top of Q&A table (from link params or patient record)."""
    name = (answers.get('DEMO_FULL_NAME') or answers.get('FULL_NAME') or '').strip()
    email = (answers.get('EMAIL') or answers.get('DEMO_EMAIL') or '').strip()
    if patient:
        name = name or (getattr(patient, 'name', None) or '').strip()
        email = email or (getattr(patient, 'email', None) or '').strip()

    identity_rows = []
    if name:
        identity_rows.append({
            'question_id': 'PATIENT_NAME',
            'question_text': 'Patient name',
            'question_type': 'text',
            'section': 'Patient Information',
            'user_answer': name,
            'raw_answer': name,
        })
    if email:
        identity_rows.append({
            'question_id': 'PATIENT_EMAIL',
            'question_text': 'Email address',
            'question_type': 'email',
            'section': 'Patient Information',
            'user_answer': email,
            'raw_answer': email,
        })

    if not identity_rows:
        return enhanced

    existing = enhanced.get('questions_and_answers') or []
    skip_ids = {'PATIENT_NAME', 'PATIENT_EMAIL'}
    rest = [q for q in existing if q.get('question_id') not in skip_ids]
    enhanced['questions_and_answers'] = identity_rows + rest
    return enhanced


def build_enhanced_answers_from_package(answers, quiz_package, language='en', patient=None):
    """
    Build enhanced_answers (questions_and_answers) server-side for reliable PDF generation.
    Mirrors client createEnhancedAnswers() logic.
    """
    enhanced = {
        'submission_info': {
            'timestamp': datetime.utcnow().isoformat(),
            'language': language,
            'report_kind': (quiz_package.get('metadata') or {}).get('quiz_mode') or 'assessment',
        },
        'questions_and_answers': [],
        'raw_answers': dict(answers or {}),
    }

    for question in quiz_package.get('questions') or []:
        qid = question.get('qid')
        if not qid:
            continue
        if not should_display_question(question, answers or {}):
            continue
        user_answer = (answers or {}).get(qid)
        if user_answer is None or user_answer == '':
            continue
        if isinstance(user_answer, list) and len(user_answer) == 0:
            continue

        lang = (language or 'en').lower().split('-', 1)[0]
        if lang == 'he' and question.get('title_he'):
            question_text = question['title_he']
        else:
            question_text = question.get('title_en') or question.get('title') or qid
        formatted = _format_answer_for_question(question, user_answer, language)
        other_text = (answers or {}).get(f'{qid}_other_text')
        if other_text and (
            user_answer == 'other'
            or (isinstance(user_answer, list) and 'other' in user_answer)
        ):
            formatted = f"{formatted} ({other_text})" if formatted else str(other_text)
        if user_answer == 'yes' and other_text and qid in ('FU_Q6', 'FU_Q23'):
            # Conditional describe fields stored separately
            pass
        # FU_Q6a / FU_Q23a are separate questions in package

        enhanced['questions_and_answers'].append({
            'question_id': qid,
            'question_text': question_text,
            'question_type': question.get('type'),
            'section': question.get('section'),
            'user_answer': formatted,
            'raw_answer': user_answer,
        })

    _prepend_patient_identity_to_enhanced(enhanced, answers, patient=patient)
    enhanced['submission_info']['total_questions_answered'] = len(enhanced['questions_and_answers'])
    return enhanced


def evaluate_followup_quiz(answers, language='en'):
    """Minimal evaluation for follow-up questionnaire (no risk scoring)."""
    lang = (language or 'en').lower().split('-', 1)[0]
    if lang == 'he':
        return {
            'total_score': 0,
            'risk_band': 'followup',
            'risk_label': 'שאלון מעקב',
            'red_flags': [],
            'outcome_message_id': 'MSG_FOLLOWUP_THANK_YOU',
            'outcome_title': 'תודה על מילוי שאלון המעקב',
            'outcome_body': (
                'התשובות שלך התקבלו ויסייעו לצוות המטפל להעריך את התקדמותך '
                'בטיפול באמצעות התקן דנטלי.'
            ),
            'cta_text': '',
            'observations': [],
        }
    return {
        'total_score': 0,
        'risk_band': 'followup',
        'risk_label': 'Follow-up Questionnaire',
        'red_flags': [],
        'outcome_message_id': 'MSG_FOLLOWUP_THANK_YOU',
        'outcome_title': 'Thank you for completing your follow-up questionnaire',
        'outcome_body': (
            'Your responses have been received and will help your care team '
            'evaluate your progress with oral appliance therapy.'
        ),
        'cta_text': '',
        'observations': [],
    }


def get_localized_text(key, language, quiz_package=None):
    """
    Retrieve localized text for a given key and language.
    Updated to work with new JSON structure without i18n.
    
    Args:
        key: The i18n key (e.g., 'Q.snoring.title')
        language: Language code ('en', 'ru', 'he')
        quiz_package: Optional quiz package dict (will load if not provided)
    
    Returns:
        Localized string or the key itself if not found
    """
    if quiz_package is None:
        quiz_package = load_quiz_package()
    
    # Handle specific risk band labels - now all map to simple risk levels
    # This function is kept for backward compatibility but risk_label is now set directly in evaluate_quiz
    risk_band_mappings = {
        'risk.low': 'Low Risk',
        'risk.mild': 'Low Risk',
        'risk.moderate': 'Moderate Risk',
        'risk.high': 'High Risk'
    }
    
    if key in risk_band_mappings:
        return risk_band_mappings[key]

    lang = (language or "en").lower().split("-", 1)[0]
    if lang not in ("en", "he", "ru"):
        lang = "en"

    # Known outcome/CTA keys (English copy; other languages fall back to English until manifest i18n exists)
    if lang == "en" or lang in ("he", "ru"):
        fb = _OUTCOME_AND_CTA_FALLBACKS_EN.get(key)
        if fb:
            return fb

    current_app.logger.info(f"get_localized_text: no fallback for key={key!r} language={language!r}")
    return key


def evaluate_expression(expr, answers, total_score=None, red_flags=None, risk_band=None):
    """
    Evaluate a conditional expression using quiz answers.
    
    Supports:
    - ANS.QID: Get answer for question ID
    - TOTAL_SCORE: Current total score
    - HAS_FLAG("FLAG_ID"): Check if red flag exists
    - RISK: Current risk band
    - Operators: ==, !=, >, <, >=, <=, AND, OR, NOT
    - Boolean: true, false
    
    Args:
        expr: Expression string
        answers: Dictionary of quiz answers
        total_score: Current calculated score
        red_flags: List of triggered red flags
        risk_band: Current risk band ('low', 'moderate', 'high')
    
    Returns:
        Boolean result of the expression
    """
    if not expr or expr.strip() == '':
        return True
    
    # Handle literal boolean
    if expr.strip().lower() == 'true':
        return True
    if expr.strip().lower() == 'false':
        return False
    
    # Replace ANS.QID with actual values
    def replace_ans(match):
        qid = match.group(1)
        value = answers.get(qid, '')
        # Wrap string values in quotes for eval
        if isinstance(value, str):
            return f"'{value}'"
        return str(value)
    
    expr = re.sub(r'ANS\.(\w+)', replace_ans, expr)
    
    # Replace TOTAL_SCORE
    if 'TOTAL_SCORE' in expr and total_score is not None:
        expr = expr.replace('TOTAL_SCORE', str(total_score))
    
    # Replace RISK
    if 'RISK' in expr and risk_band is not None:
        expr = expr.replace('RISK', f"'{risk_band}'")
    
    # Replace HAS_FLAG function
    def replace_has_flag(match):
        flag_id = match.group(1).strip('"\'')
        has_flag = flag_id in (red_flags or [])
        return str(has_flag)
    
    expr = re.sub(r'HAS_FLAG\(["\']?(\w+)["\']?\)', replace_has_flag, expr)
    
    # Replace logical/operators with Python equivalents
    expr = expr.replace(' && ', ' and ')
    expr = expr.replace('||', ' or ')
    expr = expr.replace(' AND ', ' and ')
    expr = expr.replace(' OR ', ' or ')
    expr = expr.replace(' NOT ', ' not ')
    expr = expr.replace('!==', '!=')
    expr = expr.replace('===', '==')
    
    try:
        # Safely evaluate the expression
        result = eval(expr)
        return bool(result)
    except Exception as e:
        current_app.logger.error(f"Error evaluating expression '{expr}': {str(e)}")
        return False


def should_display_question(question, answers):
    """
    Determine if a question should be displayed based on its display_if condition.
    
    Args:
        question: Question dictionary from quiz package
        answers: Current answers dictionary
    
    Returns:
        Boolean indicating if question should be shown
    """
    # display_if may be None in the manifest; treat as unconditional true
    display_if = question.get('display_if') or {}
    expr = display_if.get('expr', 'true') if isinstance(display_if, dict) else 'true'
    return evaluate_expression(expr, answers)


def calculate_score(answers, quiz_package=None):
    """
    Calculate the weighted score based on quiz answers with domain caps.
    
    Args:
        answers: Dictionary of quiz answers {qid: value}
        quiz_package: Optional quiz package (will load if not provided)
    
    Returns:
        tuple: (total_score, observations_list, domain_scores)
    """
    if quiz_package is None:
        quiz_package = load_quiz_package()
    
    questions = quiz_package.get('questions', [])
    observations = []
    domain_scores = {}
    domain_caps = quiz_package.get('domain_caps', {})
    
    # Initialize domain scores
    for domain in ['General', 'OSA History', 'TMJ/Bruxism', 'Nasal/Sinus', 
                   'Comorbidities & Lifestyle', 'Sleep Symptoms', 'Daytime Function']:
        domain_scores[domain] = 0
    
    for question in questions:
        qid = question.get('qid')
        answer_value = answers.get(qid)
        
        if answer_value is None:
            try:
                current_app.logger.debug(f"Score skip {qid}: no answer")
            except Exception:
                pass
            continue
        
        # Only score questions that should be displayed
        if not should_display_question(question, answers):
            try:
                current_app.logger.debug(f"Score skip {qid}: not displayed by condition")
            except Exception:
                pass
            continue
        
        # If score_flag is false but score_map exists, still score it (be tolerant of stale flags)
        if not question.get('score_flag', True):
            try:
                current_app.logger.info(f"Score notice {qid}: score_flag false, attempting to score due to present score_map")
            except Exception:
                pass
        
        score_map = question.get('score_map', {})
        if not score_map:
            try:
                current_app.logger.debug(f"Score skip {qid}: no score_map")
            except Exception:
                pass
            continue
        
        # Get the weight for this answer
        weights = score_map.get('weights', {})
        question_weight = 1.0  # Default weight
        
        if score_map.get('mode') == 'option':
            # Single choice question
            weight = weights.get(str(answer_value), 0)
        elif score_map.get('mode') == 'multi_option':
            # Multi-choice question
            if isinstance(answer_value, list):
                weight = sum(weights.get(str(val), 0) for val in answer_value)
            else:
                weight = weights.get(str(answer_value), 0)
        else:
            weight = 0
        
        if weight > 0:
            # Apply question weight multiplier
            final_weight = weight * question_weight
            
            # Determine domain for this question
            section = question.get('section', 'General')
            domain = determine_domain_from_section(section)
            
            # Add to domain score
            if domain in domain_scores:
                domain_scores[domain] += final_weight
            
            observations.append({
                'qid': qid,
                'answer': answer_value,
                'weight': final_weight,
                'question': question.get('title_en', qid),
                'domain': domain
            })
            try:
                current_app.logger.info(f"Scored {qid}: +{final_weight} to {domain} (mode={score_map.get('mode')}, raw={answer_value})")
            except Exception:
                pass
        else:
            try:
                current_app.logger.debug(f"Score zero {qid}: mode={score_map.get('mode')} raw={answer_value}")
            except Exception:
                pass
    
    # Apply domain caps
    for domain, cap in domain_caps.items():
        if domain in domain_scores and domain_scores[domain] > cap:
            domain_scores[domain] = cap
    
    # Calculate total score
    total_score = sum(domain_scores.values())
    try:
        current_app.logger.info(f"Domain scores: {domain_scores} -> total {total_score}")
    except Exception:
        pass
    
    return total_score, observations, domain_scores


def calculate_ssi_score(answers, quiz_package=None):
    """
    Calculate the Sleep Symptom Index (SSI) score based on specifications.
    SSI excludes OSA History, Comorbidities & Lifestyle, and Family History.
    Applies specific weighting and caps to domains.
    
    Args:
        answers: Dictionary of quiz answers {qid: value}
        quiz_package: Optional quiz package (will load if not provided)
    
    Returns:
        tuple: (ssi_score, ssi_observations, ssi_domain_scores)
    """
    if quiz_package is None:
        quiz_package = load_quiz_package()
    
    questions = quiz_package.get('questions', [])
    ssi_observations = []
    ssi_domain_scores = {}
    
    # Initialize SSI domain scores (only domains included in SSI)
    ssi_domains = {
        'Sleep Symptoms': 0,
        'Nasal/Sinus': 0,
        'Daytime Function': 0,
        'TMJ/Bruxism': 0,
        'General': 0  # Only symptomatic items
    }
    
    # Domain-specific settings
    domain_weights = {
        'Nasal/Sinus': 1.5,
        'Sleep Symptoms': 1.0,
        'Daytime Function': 1.0,
        'TMJ/Bruxism': 1.0,
        'General': 1.0
    }
    
    domain_caps = {
        'Nasal/Sinus': 4,
        'TMJ/Bruxism': 3
    }
    
    for question in questions:
        qid = question.get('qid')
        answer_value = answers.get(qid)
        
        if answer_value is None:
            continue
        
        # Only score questions that should be displayed
        if not should_display_question(question, answers):
            continue
        
        score_map = question.get('score_map', {})
        if not score_map:
            continue
        
        # Get the weight for this answer
        weights = score_map.get('weights', {})
        
        if score_map.get('mode') == 'option':
            # Single choice question
            weight = weights.get(str(answer_value), 0)
        elif score_map.get('mode') == 'multi_option':
            # Multi-choice question
            if isinstance(answer_value, list):
                weight = sum(weights.get(str(val), 0) for val in answer_value)
            else:
                weight = weights.get(str(answer_value), 0)
        else:
            weight = 0
        
        if weight > 0:
            # Determine domain for this question
            section = question.get('section', 'General')
            domain = determine_domain_from_section(section)
            
            # Skip domains excluded from SSI
            if domain in ['OSA History', 'Comorbidities & Lifestyle', 'Family History']:
                continue
            
            # Apply domain-specific weighting
            domain_weight = domain_weights.get(domain, 1.0)
            final_weight = weight * domain_weight
            
            # Add to SSI domain score
            if domain in ssi_domains:
                ssi_domains[domain] += final_weight
                
                ssi_observations.append({
                    'qid': qid,
                    'answer': answer_value,
                    'weight': final_weight,
                    'question': question.get('title_en', qid),
                    'domain': domain
                })
    
    # Apply domain caps
    for domain, cap in domain_caps.items():
        if domain in ssi_domains and ssi_domains[domain] > cap:
            ssi_domains[domain] = cap
    
    # Calculate SSI score
    ssi_score = sum(ssi_domains.values())
    
    return ssi_score, ssi_observations, ssi_domains


def determine_ssi_status(ssi_score, red_flags):
    """
    Determine SSI status based on SSI score and red flags.
    
    Args:
        ssi_score: Calculated SSI score
        red_flags: List of triggered red flag names
    
    Returns:
        SSI status ('Stable', 'Mild', 'Moderate/Severe')
    """
    # Safety-critical red flags that force at least Symptomatic
    safety_flags = ["Driving sleepiness (Yes)", "FOSQ driving item (1–2)"]
    has_safety_flag = any(flag in red_flags for flag in safety_flags)
    
    # Stable (Asymptomatic): SSI 0–2 AND no safety RF
    if ssi_score <= 2 and not has_safety_flag:
        return "Stable"
    
    # Symptomatic – Mild: SSI 3–6 OR any 1 non-safety RF
    elif ssi_score <= 6 or len(red_flags) == 1:
        return "Mild"
    
    # Symptomatic – Moderate/Severe: SSI ≥7 OR ≥2 RFs OR any safety RF
    else:
        return "Moderate/Severe"


def determine_domain_from_section(section):
    """Determine domain from section name"""
    if 'Patient Profile' in section:
        return 'General'
    elif 'Sleep Apnea History' in section:
        return 'OSA History'
    elif 'TMJ' in section:
        return 'TMJ/Bruxism'
    elif 'Medical History' in section:
        return 'Comorbidities & Lifestyle'
    elif 'Sleep Quality' in section:
        return 'Sleep Symptoms'
    elif 'Daytime Function' in section:
        return 'Daytime Function'
    else:
        return 'General'


def check_red_flags(answers, quiz_package=None):
    """
    Check for red flag conditions in the quiz answers.
    
    Args:
        answers: Dictionary of quiz answers
        quiz_package: Optional quiz package
    
    Returns:
        List of triggered red flag names
    """
    if quiz_package is None:
        quiz_package = load_quiz_package()
    
    red_flags = []
    flag_definitions = quiz_package.get('red_flags', [])
    
    for flag_def in flag_definitions:
        flag_id = flag_def.get('id')
        effect = flag_def.get('effect', '')
        
        if check_red_flag_condition(flag_id, answers):
            red_flags.append(flag_id)
            current_app.logger.info(f"Red flag triggered: {flag_id}")
    
    return red_flags


def calculate_ssi_red_flag_points(red_flags, quiz_package=None):
    """
    Calculate additional SSI points from red flags.
    
    Args:
        red_flags: List of triggered red flag names
        quiz_package: Optional quiz package
    
    Returns:
        Additional SSI points from red flags
    """
    if quiz_package is None:
        quiz_package = load_quiz_package()
    
    ssi_points = 0
    flag_definitions = quiz_package.get('red_flags', [])
    
    for flag_def in flag_definitions:
        flag_id = flag_def.get('id')
        effect = flag_def.get('effect', '')
        
        if flag_id in red_flags and effect.startswith('+'):
            # Extract points from effect string like "+2 points to SSI"
            try:
                points = int(effect.split()[0].replace('+', ''))
                ssi_points += points
                current_app.logger.info(f"Red flag {flag_id} adds {points} SSI points")
            except (ValueError, IndexError):
                current_app.logger.warning(f"Could not parse SSI points from effect: {effect}")
    
    return ssi_points


def check_red_flag_condition(flag_id, answers):
    """Check if a specific red flag condition is met"""
    if not flag_id:
        return False
    
    # BMI ≥ 25 — use CDC BMI computed from height/weight (DEMO_BMI_COMPUTED_CDC)
    if 'BMI ≥ 25' in flag_id:
        try:
            raw = answers.get('DEMO_BMI_COMPUTED_CDC')
            if raw is None or raw == '':
                return False
            bmi = float(str(raw).strip())
            return bmi >= 25.0
        except (TypeError, ValueError):
            return False
    
    # Driving sleepiness (Yes) → Q24 (trouble staying awake while driving)
    if 'Driving sleepiness (Yes)' in flag_id:
        return answers.get('Q24') == 'yes'
    
    # FOSQ driving item (1–2) → Q36 scale 1-4 (1,2 are risky)
    # NOTE: Q36 has been removed from the quiz, so this red flag is disabled
    if 'FOSQ driving item (1–2)' in flag_id:
        return False  # Q36 removed - red flag disabled
    
    # Observed apneas/gasping (≥Often) → Q20 frequency 1-5 (4=Often,5=Always)
    if 'Observed apneas/gasping (≥Often)' in flag_id:
        return answers.get('Q20') in ['4', '5']
    
    # Regular sedatives use → Q13 yes/no
    if 'Regular sedatives use' in flag_id:
        return answers.get('Q13') == 'yes'
    
    # Daily/heavy alcohol → Q12 in ['daily','3_6_times_per_week']
    if 'Daily/heavy alcohol' in flag_id:
        return answers.get('Q12') in ['daily', '3_6_times_per_week']
    
    # Hypertension → Q8 multiselect includes 'high_blood_pressure'
    if 'Hypertension' in flag_id:
        try:
            opts = answers.get('Q8') or []
            return 'high_blood_pressure' in opts
        except Exception:
            return False
    
    # Bruxism (any Yes) → Q31 yes/no
    if 'Bruxism (any Yes)' in flag_id:
        return answers.get('Q31') == 'yes'
    
    # TMJ/Bruxism subtotal ≥ 2 → sum of yes in Q29, Q30, Q31
    if 'TMJ/Bruxism subtotal ≥ 2' in flag_id:
        tmj_yes = sum(1 for q in ['Q29', 'Q30', 'Q31'] if answers.get(q) == 'yes')
        return tmj_yes >= 2
    
    # Sleep Symptoms subtotal ≥ 2 → combine binary yes + high frequency items
    if 'Sleep Symptoms subtotal ≥ 2' in flag_id:
        count = 0
        # Binary yes/no items in Section 3a
        for q in ['Q21', 'Q22', 'Q24', 'Q26', 'Q27']:  # Q28 removed
            if answers.get(q) == 'yes':
                count += 1
        # Frequency items where 4=Often, 5=Always
        for q in ['Q19', 'Q20', 'Q23', 'Q25']:
            if answers.get(q) in ['4', '5']:
                count += 1
        return count >= 2
    
    # Nasal obstruction ≥ Often → Q15a 1-5, trigger at 4 or 5
    # NOTE: Q15a has been removed from the quiz, so this red flag is disabled
    if 'Nasal obstruction ≥ Often' in flag_id:
        return False  # Q15a removed - red flag disabled
    
    # Observed apneas/gasping/choking (≥Often) → Q20 frequency 1-5 (4=Often,5=Always)
    if 'Observed apneas/gasping/choking (≥Often)' in flag_id:
        return answers.get('Q20') in ['4', '5']
    
    # Bruxism (Yes) → Q31 yes/no
    if 'Bruxism (Yes)' in flag_id:
        return answers.get('Q31') == 'yes'
    
    # TMJ/Bruxism subtotal ≥ 2 → sum of yes in Q29, Q30, Q31
    if 'TMJ/Bruxism subtotal ≥2' in flag_id:
        tmj_yes = sum(1 for q in ['Q29', 'Q30', 'Q31'] if answers.get(q) == 'yes')
        return tmj_yes >= 2
    
    # Sleep Symptoms subtotal ≥ 2 → combine binary yes + high frequency items
    if 'Sleep Symptoms subtotal ≥2' in flag_id:
        count = 0
        # Binary yes/no items in Section 3a
        for q in ['Q21', 'Q22', 'Q24', 'Q26', 'Q27']:  # Q28 removed
            if answers.get(q) == 'yes':
                count += 1
        # Frequency items where 4=Often, 5=Always
        for q in ['Q19', 'Q20', 'Q23', 'Q25']:
            if answers.get(q) in ['4', '5']:
                count += 1
        return count >= 2
    
    # Regular sedatives use → Q13 yes/no
    if 'Regular sedatives use' in flag_id:
        return answers.get('Q13') == 'yes'
    
    # Daily/heavy alcohol → Q12 in ['daily','3_6_times_per_week']
    if 'Daily/heavy alcohol' in flag_id:
        return answers.get('Q12') in ['daily', '3_6_times_per_week']
    
    # Hypertension / High BP → Q8 multiselect includes 'high_blood_pressure'
    if 'Hypertension / High BP' in flag_id:
        try:
            opts = answers.get('Q8') or []
            return 'high_blood_pressure' in opts
        except Exception:
            return False
    
    return False


def determine_risk_band(score, red_flags, answers, quiz_package=None):
    """
    Determine the risk band based on score and red flags.
    Red flags can override the score-based band.
    
    Args:
        score: Calculated total score
        red_flags: List of triggered red flag names
        answers: Quiz answers (for additional logic)
        quiz_package: Optional quiz package
    
    Returns:
        Risk band name ('low', 'moderate', 'high')
    """
    if quiz_package is None:
        quiz_package = load_quiz_package()
    
    risk_bands = quiz_package.get('scoring', {}).get('risk_bands', [])
    red_flag_definitions = quiz_package.get('red_flags', [])
    
    # Determine band based on score and red flag count
    red_flag_count = len(red_flags)
    
    # High Risk: ≥7 points OR ≥2 Red Flags (check this FIRST before overrides)
    if score >= 7 or red_flag_count >= 2:
        current_app.logger.info(f"High Risk: score={score} OR red_flag_count={red_flag_count} >= 2")
        return 'high'
    
    # Check for red flag overrides (only if not already High Risk)
    for red_flag_name in red_flags:
        for flag_def in red_flag_definitions:
            if flag_def.get('id') == red_flag_name:
                effect = flag_def.get('effect', '')
                if 'Override Low Risk' in effect:
                    current_app.logger.info(f"Risk band overridden to 'high' by red flag '{red_flag_name}'")
                    return 'high'
                elif 'Cannot be Low Risk' in effect:
                    # BMI ≥ 25 prevents Low Risk, minimum Moderate
                    if score < 4:
                        return 'moderate'
    
    # Moderate Risk: 4–6 points OR exactly 1 Red Flag
    if (4 <= score <= 6) or red_flag_count == 1:
        return 'moderate'
    
    # Low Risk: 0–3 points AND no Red Flags
    if score <= 3 and red_flag_count == 0:
        return 'low'
    
    # Default fallback
    return 'moderate'


def resolve_outcome(risk_band, answers, red_flags, total_score, quiz_package=None):
    """
    Resolve the appropriate outcome message based on risk band, answers, and flags.
    
    Args:
        risk_band: Calculated risk band
        answers: Quiz answers
        red_flags: List of triggered red flags
        total_score: Calculated score
        quiz_package: Optional quiz package
    
    Returns:
        tuple: (outcome_message_id, cta_id)
    """
    if quiz_package is None:
        quiz_package = load_quiz_package()
    
    outcome_rules = quiz_package.get('outcomes', {}).get('rules', [])
    
    # Sort rules by priority (lower number = higher priority)
    sorted_rules = sorted(outcome_rules, key=lambda r: r.get('priority', 999))
    
    for rule in sorted_rules:
        condition = rule.get('if', {})
        expr = condition.get('expr', 'false')
        
        if evaluate_expression(expr, answers, total_score, red_flags, risk_band):
            message_id = rule.get('message_id')
            cta_id = rule.get('next_step_id')
            current_app.logger.info(f"Outcome resolved: {rule.get('id')} -> {message_id}")
            return message_id, cta_id
    
    # Default fallback
    default_messages = {
        'high': ('MSG_HIGH_RISK_ACTION', 'CTA_REFER_SLEEP_TEST'),
        'moderate': ('MSG_MODERATE_RISK_ACTION', 'CTA_CONSULT_DENTIST'),
        'low': ('MSG_LOW_RISK_ACTION', 'CTA_MONITOR')
    }
    
    return default_messages.get(risk_band, ('MSG_LOW_RISK_ACTION', 'CTA_MONITOR'))


def resolve_diagnosed_patient_message(diagnosed, treatment, ssi_status):
    """
    Resolve appropriate message for diagnosed patients based on treatment and SSI status.
    
    Args:
        diagnosed: Boolean indicating if patient is diagnosed with OSA
        treatment: Boolean indicating if patient is receiving treatment
        ssi_status: SSI status ('Stable', 'Mild', 'Moderate/Severe')
    
    Returns:
        tuple: (message_id, cta_id) or None if not applicable
    """
    if not diagnosed:
        return None  # Use standard risk-based logic
    
    if not treatment:
        return ('MSG_DIAGNOSED_NOT_TREATING', 'CTA_START_TREATMENT')
    elif ssi_status == "Stable":
        return ('MSG_DIAGNOSED_TREATING_STABLE', 'CTA_CONTINUE_TREATMENT')
    else:
        return ('MSG_DIAGNOSED_TREATING_SYMPTOMATIC', 'CTA_OPTIMIZE_TREATMENT')


def evaluate_quiz(answers, language='en'):
    """
    Complete quiz evaluation: calculate score, check flags, determine risk, resolve outcome.
    
    Args:
        answers: Dictionary of quiz answers
        language: Language code for localization
    
    Returns:
        Dictionary with evaluation results
    """
    quiz_package = load_quiz_package()
    try:
        # Log manifest diagnostics and scoring config for key items
        meta = quiz_package.get('metadata', {})
        current_app.logger.info(
            f"Quiz manifest version={meta.get('version')} default_language={meta.get('default_language')}"
        )
        qids_to_check = ['Q8']  # Q33, Q34, Q35, Q36, Q37 removed
        qmap = {q.get('qid'): q for q in quiz_package.get('questions', []) if q.get('qid') in qids_to_check}
        for qid in qids_to_check:
            q = qmap.get(qid)
            if q:
                sm = q.get('score_map') or {}
                current_app.logger.info(
                    f"CFG {qid}: score_flag={q.get('score_flag')} mode={sm.get('mode')} weights_keys={list((sm.get('weights') or {}).keys())[:4]}..."
                )
            else:
                current_app.logger.info(f"CFG {qid}: not found in manifest")
    except Exception:
        pass
    
    # Calculate score (handle both 2-tuple and 3-tuple returns)
    score_result = calculate_score(answers, quiz_package)
    if isinstance(score_result, tuple):
        if len(score_result) == 3:
            total_score, score_observations, domain_scores = score_result
        elif len(score_result) == 2:
            total_score, score_observations = score_result
            domain_scores = {}
        else:
            total_score, score_observations, domain_scores = 0, [], {}
    else:
        total_score, score_observations, domain_scores = 0, [], {}
    
    # Calculate SSI score for diagnosed patients
    ssi_score, ssi_observations, ssi_domain_scores = calculate_ssi_score(answers, quiz_package)
    
    # Check red flags
    red_flags = check_red_flags(answers, quiz_package)
    
    # Red flags don't add points to SSI score according to requirements
    # They only affect risk band determination
    
    # Determine SSI status
    ssi_status = determine_ssi_status(ssi_score, red_flags)
    
    # Check if patient is diagnosed and receiving treatment
    diagnosed = answers.get('Q1') == 'yes'
    treatment = answers.get('Q2') == 'yes'
    
    # Resolve outcome - prioritize diagnosed patient logic
    diagnosed_message = resolve_diagnosed_patient_message(diagnosed, treatment, ssi_status)
    if diagnosed_message:
        message_id, cta_id = diagnosed_message
        # For diagnosed patients, determine internal risk band first, then map to simple risk level
        if not treatment:
            # Check if symptomatic or not for not-treated patients
            if ssi_status == "Stable":
                internal_risk_band = "diagnosed_not_treated_not_symptomatic"
            else:
                internal_risk_band = "diagnosed_not_treated_symptomatic"
        elif ssi_status == "Stable":
            internal_risk_band = "diagnosed_treated_stable"
        else:
            internal_risk_band = "diagnosed_treated_symptomatic"
        
        # Map diagnosed permutations to simple risk levels for external system alignment
        # Both risk_band and risk_label should use the same format: "High Risk", "Moderate Risk", or "Low Risk"
        # Product decision: diagnosed + not treating should be treated as High Risk,
        # even if SSI appears "Stable". This keeps results clinically conservative.
        risk_band_mapping = {
            "diagnosed_not_treated_not_symptomatic": "High Risk",
            "diagnosed_not_treated_symptomatic": "High Risk",
            "diagnosed_treated_stable": "Moderate Risk",
            "diagnosed_treated_symptomatic": "High Risk"
        }
        risk_band = risk_band_mapping.get(internal_risk_band, "Moderate Risk")
    else:
        # Use standard risk-based logic for non-diagnosed patients
        internal_risk_band = determine_risk_band(total_score, red_flags, answers, quiz_package)
        message_id, cta_id = resolve_outcome(internal_risk_band, answers, red_flags, total_score, quiz_package)
        
        # Map to "High Risk", "Moderate Risk", or "Low Risk" format
        risk_band_mapping = {
            "low": "Low Risk",
            "mild": "Low Risk",
            "moderate": "Moderate Risk",
            "high": "High Risk"
        }
        risk_band = risk_band_mapping.get(internal_risk_band, "Moderate Risk")
    
    # Get localized messages
    outcome_title = get_localized_text(f"{message_id}.title", language, quiz_package)
    outcome_body = get_localized_text(f"{message_id}.body", language, quiz_package)
    cta_text = get_localized_text(cta_id, language, quiz_package)
    
    # Both risk_band and risk_label should be the same
    risk_label = risk_band
    
    return {
        'total_score': total_score,
        'risk_band': risk_band,
        'risk_label': risk_label,
        'internal_risk_band': internal_risk_band,  # Added for Hebrew-specific risk band display
        'red_flags': red_flags,
        'outcome_message_id': message_id,
        'outcome_title': outcome_title,
        'outcome_body': outcome_body,
        'cta_id': cta_id,
        'cta_text': cta_text,
        'observations': score_observations,
        'domain_scores': domain_scores,
        'ssi_score': ssi_score,
        'ssi_status': ssi_status,
        'ssi_observations': ssi_observations,
        'ssi_domain_scores': ssi_domain_scores,
        'diagnosed': diagnosed,
        'treatment': treatment
    }


def get_or_create_patient(patient_email, full_name=None, phone=None, clinic_id=None, id_number=None, dentist_id=None):
    """
    Get existing patient by email or create a new one.

    Args:
        patient_email: Patient's email address
        full_name: Patient's full name
        phone: Patient's phone number
        clinic_id: Clinic ID for assignment
        id_number: Israeli ID (teudat zehut) or national ID (optional)
        dentist_id: Dentist ID for assignment (when provided, used instead of auto-lookup from clinic)

    Returns:
        Patient ID
    """
    try:
        patient = Patient.query.filter_by(email=patient_email).first()
        
        if not patient:
            # Use provided dentist_id, or determine from clinic when multiple dentists exist
            resolved_dentist_id = dentist_id
            if not resolved_dentist_id and clinic_id:
                clinic = Clinic.query.get(clinic_id)
                if clinic:
                    from flask_app.models import Dentist, dentist_clinic_association
                    
                    # First try to find dentist directly associated with clinic
                    dentist = (
                        Dentist.query
                        .join(dentist_clinic_association, Dentist.id == dentist_clinic_association.c.dentist_id)
                        .filter(dentist_clinic_association.c.clinic_id == clinic_id)
                        .first()
                    )
                    
                    # If no direct clinic association, try DSO association
                    if not dentist and clinic.dso_id:
                        from flask_app.models import dentist_dso_association
                        dentist = (
                            Dentist.query
                            .join(dentist_dso_association, Dentist.id == dentist_dso_association.c.dentist_id)
                            .filter(dentist_dso_association.c.dso_id == clinic.dso_id)
                            .first()
                        )
                    
                    if dentist:
                        resolved_dentist_id = dentist.id
                        current_app.logger.info(f"Auto-assigned patient to dentist: {dentist.name} (ID: {resolved_dentist_id})")
                    else:
                        current_app.logger.warning(f"No dentist found for clinic {clinic_id}")
            
            # Create new patient
            patient = Patient(
                name=full_name or "VizBriz Quiz Respondent",
                email=patient_email,
                phone=phone,
                id_number=(id_number or "").strip() or None,
                status="New",
                dentist_id=resolved_dentist_id,
                clinic_id=clinic_id,
                create_date=datetime.utcnow(),
                last_update=datetime.utcnow(),
                upload_token=secrets.token_urlsafe(32)
            )
            db.session.add(patient)
            db.session.commit()
            current_app.logger.info(f"Created new patient: {patient.id} - {patient_email}")
        else:
            # Track if we need to commit changes
            needs_commit = False
            
            # Reactivate patient if they were archived (they're taking a new quiz!)
            if patient.status and patient.status.lower() == 'archived':
                patient.status = 'New'
                needs_commit = True
                current_app.logger.info(f"Reactivated archived patient {patient.id} - {patient_email}")
            
            # Update patient name if provided and different
            if full_name and full_name.strip() and full_name != patient.name:
                old_name = patient.name
                patient.name = full_name
                needs_commit = True
                current_app.logger.info(f"Updated patient {patient.id} name: '{old_name}' -> '{full_name}'")
            
            # Update phone if provided and different
            if phone and phone.strip() and phone != patient.phone:
                old_phone = patient.phone
                patient.phone = phone
                needs_commit = True
                current_app.logger.info(f"Updated patient {patient.id} phone: '{old_phone}' -> '{phone}'")

            # Update id_number if provided and different
            id_val = (id_number or "").strip() or None
            if id_val is not None and id_val != (patient.id_number or "").strip():
                patient.id_number = id_val
                needs_commit = True
                current_app.logger.info(f"Updated patient {patient.id} id_number")
            
            # Always update last_update timestamp for returning patients
            patient.last_update = datetime.utcnow()
            needs_commit = True  # Always commit for returning patients
            
            # Update clinic and dentist if provided and not set
            if clinic_id and not patient.clinic_id:
                patient.clinic_id = clinic_id
                needs_commit = True
                
                # Also assign dentist if not already assigned (prefer explicit dentist_id from URL)
                if not patient.dentist_id:
                    resolved_dentist_id = dentist_id
                    if not resolved_dentist_id:
                        clinic = Clinic.query.get(clinic_id)
                        if clinic:
                            from flask_app.models import Dentist, dentist_clinic_association
                            
                            # First try to find dentist directly associated with clinic
                            dentist = (
                                Dentist.query
                                .join(dentist_clinic_association, Dentist.id == dentist_clinic_association.c.dentist_id)
                                .filter(dentist_clinic_association.c.clinic_id == clinic_id)
                                .first()
                            )
                            
                            # If no direct clinic association, try DSO association
                            if not dentist and clinic.dso_id:
                                from flask_app.models import dentist_dso_association
                                dentist = (
                                    Dentist.query
                                    .join(dentist_dso_association, Dentist.id == dentist_dso_association.c.dentist_id)
                                    .filter(dentist_dso_association.c.dso_id == clinic.dso_id)
                                    .first()
                                )
                            
                            if dentist:
                                resolved_dentist_id = dentist.id
                                current_app.logger.info(f"Auto-assigned existing patient to dentist: {dentist.name} (ID: {dentist.id})")
                    if resolved_dentist_id:
                        patient.dentist_id = resolved_dentist_id
                        needs_commit = True
            
            # Commit any changes made to existing patient
            if needs_commit:
                db.session.commit()
                current_app.logger.info(f"Updated existing patient {patient.id}")
            
            current_app.logger.info(f"Found existing patient: {patient.id} - {patient_email}")
        
        return patient.id
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in get_or_create_patient: {str(e)}")
        raise


def save_vizbriz_quiz(
    answers,
    evaluation_result,
    patient_email,
    language='en',
    clinic_email=None,
    clinic_id=None,
    referral_doctor=None,
    enhanced_answers=None,
    dentist_id=None,
    quiz_type='vizbriz_sleep_v1',
):
    """
    Save the VizBriz quiz submission to the database.
    
    Args:
        answers: Dictionary of quiz answers
        evaluation_result: Result from evaluate_quiz()
        patient_email: Patient's email
        language: Language code
        clinic_email: Clinic email
        clinic_id: Clinic ID
        referral_doctor: Referring doctor name
    
    Returns:
        Quiz ID
    """
    try:
        # Get or create patient
        # New quiz uses DEMO_FULL_NAME (all languages); keep FULL_NAME as backward-compatible fallback.
        full_name = (answers.get('DEMO_FULL_NAME') or answers.get('FULL_NAME') or '').strip()
        phone = answers.get('PHONE', '') or answers.get('DEMO_PHONE', '')
        id_number = answers.get('DEMO_ID', '').strip() or None
        patient_id = get_or_create_patient(patient_email, full_name, phone, clinic_id, id_number=id_number, dentist_id=dentist_id)

        # Ensure enhanced answers capture the resolved patient ID
        if isinstance(enhanced_answers, dict):
            submission_info = enhanced_answers.setdefault('submission_info', {})
            submission_info['patient_id'] = patient_id
        
        # Create quiz record
        # Store full payload in quiz_input for portability (raw + enhanced + summary)
        quiz_input_payload = {
            'raw_answers': answers,
            'enhanced_answers': enhanced_answers,
            'evaluation_summary': {
                'total_score': evaluation_result['total_score'],
                'risk_band': evaluation_result['risk_band'],
                'risk_label': evaluation_result.get('risk_label'),
                'red_flags': evaluation_result['red_flags'],
                'outcome_title': evaluation_result['outcome_title'],
                'outcome_body': evaluation_result['outcome_body'],
                'cta_text': evaluation_result['cta_text']
            }
        }

        quiz = VizBrizQuiz(
            user_id=patient_id,
            quiz_input=json.dumps(quiz_input_payload),
            language=language,
            total_score=evaluation_result['total_score'],
            risk_band=evaluation_result['risk_band'],
            red_flags=evaluation_result['red_flags'],
            outcome_message_id=evaluation_result['outcome_message_id'],
            ai_response=None,
            clinic_email=clinic_email,
            patient_email=patient_email,
            clinic_id=clinic_id,
            referral_doctor=referral_doctor,
            quiz_type=quiz_type,
            created_at=datetime.utcnow()
        )
        
        db.session.add(quiz)
        db.session.commit()
        
        current_app.logger.info(f"Saved VizBriz quiz ({quiz_type}): {quiz.id} for patient {patient_id}")
        
        return quiz.id
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error saving VizBriz quiz: {str(e)}")
        raise


def save_observations_to_store(patient_id, quiz_id, observations, language='en'):
    """
    Save quiz observations to the observation_store table.
    
    Args:
        patient_id: Patient ID
        quiz_id: Quiz ID
        observations: List of observation dictionaries
        language: Language code
    
    Returns:
        Boolean success status
    """
    try:
        for obs in observations:
            obs_store = ObservationStore(
                patient_id=patient_id,
                quiz_id=quiz_id,
                source_type='vizbriz_quiz',
                source_text=f"VizBriz Quiz - {obs.get('qid', 'Unknown')}",
                extracted_observations=obs,
                provider='vizbriz-quiz-v1',
                section='sleep_assessment',
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.session.add(obs_store)
        
        db.session.commit()
        current_app.logger.info(f"Saved {len(observations)} observations for quiz {quiz_id}")
        return True
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error saving observations: {str(e)}")
        return False


# ---------------- Questionnaire PDF generation & upload -----------------
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from flask_app.s3_utils import get_s3_client


def _register_dejavu_unicode_fonts():
    """
    Use embedded TrueType fonts with large Unicode coverage. ReportLab's default
    Helvetica is a built-in font limited to Western European encodings; glyphs
    outside that set render as black squares in the PDF.
    """
    try:
        pdfmetrics.getFont('DejaVuSans')
    except KeyError:
        for path in (
            '/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/dejavu/DejaVuSans.ttf',
        ):
            if path and os.path.exists(path):
                try:
                    pdfmetrics.registerFont(TTFont('DejaVuSans', path))
                    break
                except Exception:
                    continue
    try:
        pdfmetrics.getFont('DejaVuSans-Bold')
    except KeyError:
        for path in (
            '/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',
        ):
            if path and os.path.exists(path):
                try:
                    pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', path))
                    break
                except Exception:
                    continue

    try:
        pdfmetrics.getFont('DejaVuSans')
        try:
            pdfmetrics.getFont('DejaVuSans-Bold')
            return 'DejaVuSans', 'DejaVuSans-Bold'
        except KeyError:
            return 'DejaVuSans', 'DejaVuSans'
    except KeyError:
        return 'Helvetica', 'Helvetica-Bold'


# HTML <input type="date"> values are YYYY-MM-DD; labels (e.g. DOB) describe DD/MM/YYYY.
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

# Hebrew script — right-align cells; counter-reverse before Paragraph so RL's LTR pass
# ends up showing logical order (reverse-on-reverse).
_HAS_HEBREW_RE = re.compile(r"[\u0590-\u05FF\uFB1D-\uFB4F]")


def _questionnaire_pdf_counter_reverse_hebrew(text):
    if text is None:
        return ""
    s = str(text)
    if not s or not _HAS_HEBREW_RE.search(s):
        return s
    return s[::-1]

def _format_date_answer_for_questionnaire_pdf(qa, answer_str):
    """
    Show stored ISO dates as DD/MM/YYYY in the PDF for date questions so the
    value matches field labels and common UK/EU expectations.
    """
    if answer_str is None:
        return ""
    s = str(answer_str).strip()
    if not s:
        return s
    qtype = (qa.get("question_type") or "").lower()
    qid = (qa.get("question_id") or "").upper()
    is_date_field = qtype == "date" or qid in ("DEMO_DOB", "DOB") or qid.endswith("_DOB")
    if not is_date_field:
        return s
    m = _ISO_DATE_RE.match(s)
    if m:
        y, mo, d = m.groups()
        return f"{d}/{mo}/{y}"
    return s


def _generate_questionnaire_pdf_bytes(enhanced_answers, evaluation_result, language='en', report_kind='assessment'):
    """Create a clear two-column (Question | Answer) PDF with proper wrapping."""
    font_normal, font_bold = _register_dejavu_unicode_fonts()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle(
        'QTitle', parent=styles['Title'], fontName=font_bold
    )
    summary_style = ParagraphStyle(
        'QSummary', parent=styles['Normal'], fontName=font_normal
    )
    is_followup = report_kind == 'followup'
    if is_followup:
        lang = (language or 'en').lower().split('-', 1)[0]
        if lang == 'he':
            pdf_title = 'VizBriz SQUARE – שאלון מעקב ראשון'
        else:
            pdf_title = 'VizBriz SQUARE – 1st Follow-Up Questionnaire'
    else:
        pdf_title = 'VizBriz Sleep Apnea Risk Assessment – Questionnaire'
    story.append(Paragraph(escape(pdf_title), title_style))
    story.append(Spacer(1, 12))

    # Summary block
    submitted_at = (enhanced_answers or {}).get('submission_info', {}).get('timestamp')
    if submitted_at:
        try:
            submitted_display = submitted_at.replace('T', ' ').split('.')[0] + ' UTC'
        except Exception:
            submitted_display = str(submitted_at)
        story.append(Paragraph(escape(f'Submitted: {submitted_display}'), summary_style))

    if is_followup:
        outcome_title = evaluation_result.get('outcome_title') or ''
        if outcome_title:
            story.append(Paragraph(escape(outcome_title), summary_style))
        story.append(Paragraph(
            escape('Report type: 1st follow-up (oral appliance therapy progress)'),
            summary_style,
        ))
    else:
        total_score = evaluation_result.get('total_score')
        risk_band = (evaluation_result.get('risk_band') or '').upper()
        red_flags = evaluation_result.get('red_flags') or []
        story.append(Paragraph(escape(f'Total Score: {total_score}'), summary_style))
        story.append(Paragraph(escape(f'Risk Level: {risk_band}'), summary_style))
        if red_flags:
            story.append(Paragraph(escape('Red Flags: ' + ', '.join(str(x) for x in red_flags)), summary_style))
    story.append(Spacer(1, 12))

    # Column widths: fit within printable width to avoid overflow
    available_width = doc.width
    col_q = available_width * 0.58
    col_a = available_width * 0.42

    # Header/paragraph styles (user answers may be Cyrillic, Hebrew, Yiddish, etc.)
    header_style = ParagraphStyle(
        'TblHeader', parent=styles['Heading5'], spaceAfter=6, fontName=font_bold
    )
    cell_q_style = ParagraphStyle(
        'QCell', parent=styles['BodyText'], leading=12, fontName=font_normal, alignment=TA_LEFT
    )
    cell_a_style = ParagraphStyle(
        'ACell', parent=styles['BodyText'], leading=12, fontName=font_normal, alignment=TA_LEFT
    )
    cell_q_style_r = ParagraphStyle(
        'QCellR', parent=cell_q_style, alignment=TA_RIGHT
    )
    cell_a_style_r = ParagraphStyle(
        'ACellR', parent=cell_a_style, alignment=TA_RIGHT
    )

    # Questions table with wrapped paragraphs
    rows = [
        [Paragraph(escape('Question'), header_style), Paragraph(escape('Answer'), header_style)]
    ]
    row_align = []  # per data row: (question 'LEFT'|'RIGHT', answer 'LEFT'|'RIGHT')
    for qa in (enhanced_answers or {}).get('questions_and_answers', []):
        q = qa.get('question_text') or qa.get('question_id') or ''
        a = qa.get('user_answer')
        if isinstance(a, list):
            a = ', '.join(a)
        a_display = _format_date_answer_for_questionnaire_pdf(
            qa, str(a) if a is not None else ""
        )
        q_raw = str(q)
        a_raw = str(a) if a is not None else ""
        q_he = bool(_HAS_HEBREW_RE.search(q_raw))
        a_he = bool(_HAS_HEBREW_RE.search(a_raw) or _HAS_HEBREW_RE.search(a_display))

        q_safe = escape(_questionnaire_pdf_counter_reverse_hebrew(q_raw))
        a_safe = escape(_questionnaire_pdf_counter_reverse_hebrew(a_display))
        rows.append([
            Paragraph(q_safe, cell_q_style_r if q_he else cell_q_style),
            Paragraph(a_safe, cell_a_style_r if a_he else cell_a_style),
        ])
        row_align.append(
            ('RIGHT' if q_he else 'LEFT', 'RIGHT' if a_he else 'LEFT')
        )

    table = Table(rows, colWidths=[col_q, col_a], repeatRows=1)
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eef3f7')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
        ('FONTNAME', (0, 0), (-1, 0), font_bold),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (-1, 0), 'LEFT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]
    for i, (aq, aa) in enumerate(row_align, start=1):
        style_cmds.append(('ALIGN', (0, i), (0, i), aq))
        style_cmds.append(('ALIGN', (1, i), (1, i), aa))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def create_and_store_questionnaire_pdf(
    patient_id,
    enhanced_answers,
    evaluation_result,
    language='en',
    report_kind='assessment',
    quiz_id=None,
):
    """Generate PDF and upload to S3 under medical/questionnaire as a standard file."""
    from flask_app.models import File  # local import to avoid circular
    s3_client = get_s3_client()
    bucket = os.getenv('S3_BUCKET_NAME')
    pdf_bytes = _generate_questionnaire_pdf_bytes(
        enhanced_answers, evaluation_result, language, report_kind=report_kind
    )
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    if report_kind == 'followup':
        quiz_part = f"_quiz{quiz_id}" if quiz_id else ''
        filename = f"SQUARE_1st_FollowUp_Questionnaire{quiz_part}_{ts}.pdf"
        file_mapping = 'Level 5 - First Follow-Up Report'
        file_comment = f'vizbriz_followup_v1 quiz_id={quiz_id}' if quiz_id else 'vizbriz_followup_v1'
    else:
        filename = f"OSA_Patient_Questionnaire_vizbriz_{ts}.pdf"
        file_mapping = None
        file_comment = f'vizbriz_sleep_v1 quiz_id={quiz_id}' if quiz_id else None
    s3_key = f"patients/{patient_id}/medical/questionnaire/{filename}"

    s3_client.put_object(Bucket=bucket, Key=s3_key, Body=pdf_bytes, ContentType='application/pdf')

    file_row = File(
        name=filename,
        patient_id=patient_id,
        file_type='application/pdf',
        file_size=len(pdf_bytes),
        s3_key=s3_key,
        category='medical',
        subcategory='questionnaire',
        comment=file_comment,
        mapping=file_mapping,
        analyzed=False,
    )
    db.session.add(file_row)
    db.session.commit()
    current_app.logger.info(
        f"Questionnaire PDF saved ({report_kind}) for patient {patient_id}: {s3_key}"
    )
    return s3_key


def _safe_text(value, default="N/A"):
    if value is None:
        return default
    s = str(value).strip()
    return s if s else default


def _compute_age_from_dob_string(dob_value):
    if not dob_value:
        return None
    raw = str(dob_value).strip()
    if not raw:
        return None
    try:
        dob = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        try:
            dob = datetime.strptime(raw, "%d/%m/%Y")
        except ValueError:
            return None
    today = datetime.utcnow().date()
    years = today.year - dob.date().year - (
        (today.month, today.day) < (dob.date().month, dob.date().day)
    )
    return years if years >= 0 else None


def _build_qa_lookup(enhanced_answers):
    qa_lookup = {}
    qa_rows = (enhanced_answers or {}).get("questions_and_answers") or []
    for qa in qa_rows:
        if not isinstance(qa, dict):
            continue
        qid = str(qa.get("question_id") or "").strip()
        if qid:
            qa_lookup[qid] = qa.get("user_answer")
    return qa_lookup


def _normalize_list_answer(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value).strip()
    if not s:
        return []
    return [s]


def _sanitize_quiz_payload_for_l2_llm(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Strip email/phone from quiz payload before sending to Bedrock."""
    sanitized = dict(payload or {})
    raw = dict((sanitized.get("raw_answers") or {}))
    for k in ("EMAIL", "PHONE", "DEMO_EMAIL", "DEMO_PHONE"):
        raw.pop(k, None)
    sanitized["raw_answers"] = raw

    ea = sanitized.get("enhanced_answers") or {}
    if isinstance(ea, dict):
        qa = ea.get("questions_and_answers")
        if isinstance(qa, list):
            filtered = []
            for item in qa:
                if not isinstance(item, dict):
                    continue
                qid = str(item.get("question_id", "")).upper()
                if qid in {"EMAIL", "PHONE", "DEMO_EMAIL", "DEMO_PHONE"}:
                    continue
                filtered.append(item)
            ea = dict(ea)
            ea["questions_and_answers"] = filtered
        sanitized["enhanced_answers"] = ea

    return sanitized


def _fallback_l2_narrative(
    answers,
    enhanced_answers,
    evaluation_result,
    level1_context=None,
):
    """
    Deterministic narrative text if Bedrock is unavailable or returns invalid output.
    """
    qa_lookup = _build_qa_lookup(enhanced_answers)
    answers = answers or {}
    level1_context = level1_context or {}
    evaluation_result = evaluation_result or {}

    diagnosed = answers.get("Q1")
    on_treatment = answers.get("Q2")
    smoking = answers.get("Q11")
    alcohol = answers.get("Q12")
    sedatives = answers.get("Q13")
    allergies = answers.get("Q10")
    comorbidities = _normalize_list_answer(qa_lookup.get("Q8") or answers.get("Q8"))

    clinical_sentences = []
    if diagnosed == "yes":
        if on_treatment == "yes":
            clinical_sentences.append(
                "The patient reports a prior diagnosis of obstructive sleep apnea and is currently on treatment."
            )
        else:
            clinical_sentences.append(
                "The patient reports a prior diagnosis of obstructive sleep apnea and is currently not receiving treatment."
            )
    elif diagnosed == "no":
        clinical_sentences.append("The patient reports no prior diagnosis of obstructive sleep apnea.")

    if allergies == "yes":
        details = answers.get("Q10a") or qa_lookup.get("Q10a")
        if details:
            clinical_sentences.append(f"Known allergies were reported ({_safe_text(details, '')}).")
        else:
            clinical_sentences.append("Known allergies were reported.")
    elif allergies == "no":
        clinical_sentences.append("No known allergies were reported.")

    if smoking == "yes":
        clinical_sentences.append("The patient reports smoking or tobacco use.")
    elif smoking == "no":
        clinical_sentences.append("No smoking or tobacco use was reported.")

    if alcohol:
        alcohol_text = str(qa_lookup.get("Q12") or alcohol).replace("_", " ")
        clinical_sentences.append(f"Alcohol use was reported as: {alcohol_text}.")

    if sedatives == "yes":
        sedative_details = answers.get("Q13a") or qa_lookup.get("Q13a")
        if sedative_details:
            clinical_sentences.append(
                f"Regular sedative/sleeping pill use was reported ({_safe_text(sedative_details, '')})."
            )
        else:
            clinical_sentences.append("Regular sedative/sleeping pill use was reported.")
    elif sedatives == "no":
        clinical_sentences.append("No regular sedative/sleeping pill use was reported.")

    if comorbidities:
        clinical_sentences.append(
            "Comorbid medical history includes: " + ", ".join(comorbidities[:6]) + "."
        )

    clinical_background_text = (
        " ".join(clinical_sentences)
        or "Clinical background data was not fully provided in the questionnaire."
    )

    presentation_points = []
    if answers.get("Q21") == "yes":
        presentation_points.append("loud snoring")
    if answers.get("Q22") == "yes":
        presentation_points.append("mouth breathing or morning dry mouth/sore throat")
    if answers.get("Q26") == "yes":
        presentation_points.append("daytime napping")
    if answers.get("Q24") == "yes":
        presentation_points.append("daytime sleepiness episodes")
    if answers.get("Q20") in ("4", "5"):
        presentation_points.append("frequent gasping/choking events at night")
    if answers.get("Q25") in ("4", "5"):
        presentation_points.append("frequent night awakenings")

    sleep_quality = qa_lookup.get("Q18") or answers.get("Q18")
    sleep_hours = answers.get("Q17") or qa_lookup.get("Q17")
    outcome_body = level1_context.get("symptoms_text") or evaluation_result.get("outcome_body")

    presentation_chunks = []
    if presentation_points:
        presentation_chunks.append("The patient reports " + ", ".join(presentation_points) + ".")
    if sleep_quality:
        presentation_chunks.append(
            f"Sleep quality is described as {_safe_text(sleep_quality, '').lower()}."
        )
    if sleep_hours:
        presentation_chunks.append(
            f"Reported sleep duration is approximately {_safe_text(sleep_hours, '')} hours per night."
        )
    if outcome_body and isinstance(outcome_body, str) and not outcome_body.strip().startswith("MSG_"):
        presentation_chunks.append(outcome_body.strip())
    if not presentation_chunks:
        presentation_chunks.append(
            "Patient presentation details were limited in the submitted questionnaire."
        )
    patient_presentation_text = " ".join(presentation_chunks)

    goals = _normalize_list_answer(qa_lookup.get("Q38") or answers.get("Q38"))
    other_goal = answers.get("Q38a") or qa_lookup.get("Q38a")
    additional_notes = answers.get("Q38b") or qa_lookup.get("Q38b")

    goals_text_parts = []
    if goals:
        goals_text_parts.append("The patient seeks: " + ", ".join(goals) + ".")
    if other_goal:
        goals_text_parts.append(f"Additional goal: {_safe_text(other_goal, '')}.")
    if additional_notes:
        goals_text_parts.append(_safe_text(additional_notes, ""))
    patient_goals_text = (
        " ".join([x for x in goals_text_parts if x]).strip()
        or "No explicit patient goals were entered."
    )

    return {
        "clinical_background": clinical_background_text,
        "patient_presentation": patient_presentation_text,
        "patient_goals": patient_goals_text,
    }


def generate_l2_osa_assessment_narrative_with_bedrock(
    patient_quiz_json: Dict[str, Any],
    patient_id: Optional[int] = None,
) -> Optional[Dict[str, str]]:
    """
    Use Bedrock (Claude) to produce English narrative sections for the OSA Data Assessment (L2) PDF.
    Returns dict with clinical_background, patient_presentation, patient_goals or None on failure.
    """
    from flask_app.config.bedrock_config import query_bedrock_claude_enhanced

    safe_payload = _sanitize_quiz_payload_for_l2_llm(patient_quiz_json)

    prompt = f"""You are a clinical documentation assistant producing concise English prose for an "OSA Data Assessment Report" intended for clinicians.

Generate THREE narrative sections based STRICTLY on PATIENT_QUIZ_JSON. This is screening / intake documentation only.

────────────────────────
HARD RULES (MANDATORY)
────────────────────────
- ENGLISH ONLY.
- Use ONLY facts explicitly present in PATIENT_QUIZ_JSON (raw_answers, enhanced_answers.questions_and_answers, evaluation_summary, level1_snippets).
- If something is not in the JSON, do NOT mention it.
- If the JSON indicates "no" / none / never for a factor, reflect that accurately (e.g., no tobacco if Q11 is no).
- Do NOT diagnose obstructive sleep apnea from symptoms alone if the patient denied prior diagnosis (Q1=no); phrase as reported symptoms / screening context.
- Do NOT invent medications, test results, or exam findings.
- Do NOT mention AI, automation, or prompts.
- Do NOT use bullet lists inside the strings; write fluent paragraphs (2–4 sentences each section).
- Ignore evaluation_summary fields that look like message IDs (e.g. starting with MSG_ or CTA_) unless they contain readable natural-language sentences.

────────────────────────
SECTION DEFINITIONS
────────────────────────
1) clinical_background
   - Opening may reference age and sex IF present in raw_answers (e.g. DEMO_AGE, DEMO_SEX) combined with relevant history from the questionnaire (OSA history Q1–Q4, allergies Q10–Q10a, tobacco Q11, alcohol Q12, sedatives Q13–Q13a, selected comorbidities Q8, BMI-related context if DEMO_BMI_COMPUTED_CDC or height/weight present).
   - Stay faithful to answers; no speculation.

2) patient_presentation
   - Summarize reported sleep-related symptoms and daytime impact using questionnaire facts (e.g. sleep quality Q18, sleep duration Q17, snoring Q21, mouth breathing Q22, gasping/choking frequency Q20, daytime sleepiness Q23–Q24, awakenings Q25, naps Q26, optional nocturia Q27, TMJ/bruxism items if present).
   - You may incorporate plain-language meaning of screening outcome text from level1_snippets.symptoms_text OR evaluation_summary.outcome_body ONLY if they are real sentences (not MSG_/CTA_ keys).

3) patient_goals
   - Derive from sleep/health goals (Q38, Q38a) and optional free-text concerns (Q38b) when present.
   - If goals are absent or empty, write one short neutral sentence that goals were not specified.

────────────────────────
INPUT (SOURCE OF TRUTH)
────────────────────────
PATIENT_QUIZ_JSON:
{json.dumps(safe_payload, ensure_ascii=False)}

────────────────────────
OUTPUT FORMAT (STRICT)
────────────────────────
Return JSON ONLY with exactly these keys:
{{
  "clinical_background": "...",
  "patient_presentation": "...",
  "patient_goals": "..."
}}
"""

    result = query_bedrock_claude_enhanced(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1200,
        temperature=0.1,
        patient_id=patient_id,
        endpoint="osa_data_assessment_l2",
        use_knowledge_base=False,
    )

    if not result or not result.get("success"):
        return None

    text = result.get("response") or ""
    if not isinstance(text, str) or not text.strip():
        return None

    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    out = {}
    for key in ("clinical_background", "patient_presentation", "patient_goals"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out if out else None


def _first_valid_l2_logo_path():
    """
    Same primary logo as Level 1 (`level1_report_hebrew.build_level1_placeholder_context`):
    `flask_static/images/logos/vizbriz_logo.png`. Validate with PIL so broken files never reach ReportLab.
    Fall back to other bundled assets if missing.
    """
    flask_static = os.path.join(os.path.dirname(os.path.dirname(__file__)), "flask_static")
    branding = os.path.join(flask_static, "branding")
    candidates = [
        # Level 1 report (English + Hebrew) — canonical header branding
        os.path.join(flask_static, "images", "logos", "vizbriz_logo.png"),
        os.path.join(flask_static, "images", "logos", "vizbriz_logo_clean.png"),
        os.path.join(branding, "drbriz_logo.png"),
        os.path.join(branding, "vizbrizz_logo color white long.png"),
        os.path.join(branding, "11 drbriz_logo.png"),
    ]
    try:
        from PIL import Image

        for path in candidates:
            if not path or not os.path.isfile(path):
                continue
            if os.path.getsize(path) < 256:
                continue
            try:
                with Image.open(path) as img:
                    img.load()
                return path
            except Exception:
                continue
    except Exception:
        for path in candidates:
            if os.path.isfile(path) and os.path.getsize(path) > 500:
                return path
    return None


def _l2_logo_image_flowable():
    """
    Level 1 template CSS: `.header img { height: 32px; width: auto; }`
    32px @ 96dpi ≈ 24pt — use proportional width from intrinsic aspect ratio.
    """
    path = _first_valid_l2_logo_path()
    if not path:
        return None
    try:
        img = RLImage(path)
        target_h = 24  # points — matches L1 `.header img { height: 32px }` (32px @ 96dpi ≈ 24pt)
        ih = float(getattr(img, "drawHeight", 0) or 0)
        iw = float(getattr(img, "drawWidth", 0) or 0)
        if ih > 0 and iw > 0:
            img.drawHeight = target_h
            img.drawWidth = iw * target_h / ih
        else:
            img.drawHeight = target_h
            img.drawWidth = target_h * 2.6
        return img
    except Exception as e:
        current_app.logger.warning("L2 logo image flowable failed for %s: %s", path, e)
        return None


def _merge_l2_narrative(
    llm_narrative: Optional[Dict[str, str]],
    fallback: Dict[str, str],
) -> Dict[str, str]:
    merged = dict(fallback)
    if not llm_narrative:
        return merged
    for key in ("clinical_background", "patient_presentation", "patient_goals"):
        v = llm_narrative.get(key)
        if isinstance(v, str) and v.strip():
            merged[key] = v.strip()
    return merged


def _build_l2_report_pdf_bytes(
    quiz_id,
    answers,
    enhanced_answers,
    narrative,
):
    # Match Level 1 HTML report (`level1_report_hebrew_preview.html`): English uses DejaVuSans;
    # body 15px, h3 18px, patient-table 16px / labels font-weight 600, color #222 / subtitle #666.
    font_normal, font_bold = _register_dejavu_unicode_fonts()
    styles = getSampleStyleSheet()
    _text = colors.HexColor("#222222")
    _muted = colors.HexColor("#666666")

    title_style = ParagraphStyle(
        "L2Title",
        parent=styles["Title"],
        fontName=font_bold,
        fontSize=18,
        textColor=_text,
        leading=22,
        alignment=TA_LEFT,
    )
    subtitle_style = ParagraphStyle(
        "L2Subtitle",
        parent=styles["Normal"],
        fontName=font_normal,
        fontSize=15,
        textColor=_muted,
        leading=19,
    )
    section_label_style = ParagraphStyle(
        "L2SectionLabel",
        parent=styles["BodyText"],
        fontName=font_bold,
        fontSize=18,
        textColor=_text,
        leading=22,
    )
    body_style = ParagraphStyle(
        "L2Body",
        parent=styles["BodyText"],
        fontName=font_normal,
        fontSize=15,
        textColor=_text,
        leading=23,
    )
    detail_label_style = ParagraphStyle(
        "L2DetailLabel",
        parent=styles["BodyText"],
        fontName=font_bold,
        fontSize=16,
        textColor=_text,
        leading=20,
    )
    detail_value_style = ParagraphStyle(
        "L2DetailValue",
        parent=styles["BodyText"],
        fontName=font_normal,
        fontSize=16,
        textColor=_text,
        leading=20,
    )

    qa_lookup = _build_qa_lookup(enhanced_answers)
    answers = answers or {}
    narrative = narrative or {}

    # Personal details (always from questionnaire / computed fields, not LLM)
    gender_raw = (
        answers.get("DEMO_SEX")
        or answers.get("DEMO_GENDER")
        or answers.get("GENDER")
        or qa_lookup.get("DEMO_SEX")
    )
    gender_map = {"male": "M", "female": "F", "other": "Other"}
    gender = gender_map.get(str(gender_raw).strip().lower(), _safe_text(gender_raw))

    age_value = answers.get("DEMO_AGE")
    if not age_value:
        computed_age = _compute_age_from_dob_string(answers.get("DEMO_DOB"))
        age_value = computed_age if computed_age is not None else qa_lookup.get("DEMO_AGE")

    height_val = answers.get("DEMO_HEIGHT_CM") or answers.get("HEIGHT_CM")
    weight_val = answers.get("DEMO_WEIGHT_KG") or answers.get("WEIGHT_KG")
    bmi_val = (
        answers.get("DEMO_BMI_COMPUTED_CDC")
        or answers.get("DEMO_BMI")
        or qa_lookup.get("DEMO_BMI_COMPUTED_CDC")
        or qa_lookup.get("DEMO_BMI")
    )
    if (not bmi_val) and height_val and weight_val:
        try:
            h_m = float(str(height_val)) / 100.0
            if h_m > 0:
                bmi_val = f"{float(str(weight_val)) / (h_m * h_m):.1f}"
        except Exception:
            bmi_val = None

    clinical_background_text = narrative.get("clinical_background") or ""
    patient_presentation_text = narrative.get("patient_presentation") or ""
    patient_goals_text = narrative.get("patient_goals") or ""

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=32,
        rightMargin=32,
        topMargin=28,
        bottomMargin=28,
    )
    story = []

    logo_img = _l2_logo_image_flowable()
    if logo_img:
        story.append(logo_img)
        story.append(Spacer(1, 8))

    story.append(Paragraph("OSA Data Assessment Report", title_style))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Case SLe {quiz_id}", subtitle_style))
    story.append(Spacer(1, 12))

    details_table = Table(
        [
            [
                Paragraph("Personal details", detail_label_style),
                Paragraph("Gender:", detail_label_style),
                Paragraph(_safe_text(gender), detail_value_style),
                Paragraph("Age:", detail_label_style),
                Paragraph(_safe_text(age_value), detail_value_style),
                Paragraph("Weight & Height:", detail_label_style),
                Paragraph(
                    f"{_safe_text(weight_val)} kg<br/>{_safe_text(height_val)} cm",
                    detail_value_style,
                ),
                Paragraph("BMI:", detail_label_style),
                Paragraph(_safe_text(bmi_val), detail_value_style),
            ]
        ],
        colWidths=[95, 55, 52, 35, 32, 85, 70, 28, 35],
    )
    details_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d8dce3")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#eaf4fa")),
            ]
        )
    )
    story.append(details_table)
    story.append(Spacer(1, 14))

    for label, value in (
        ("Clinical background:", clinical_background_text),
        ("Patient presentation:", patient_presentation_text),
        ("Patient goals:", patient_goals_text),
    ):
        section_table = Table(
            [[Paragraph(label, section_label_style), Paragraph(escape(value), body_style)]],
            colWidths=[140, doc.width - 140],
        )
        section_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        story.append(section_table)
        story.append(Spacer(1, 4))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def create_and_store_l2_assessment_pdf(
    patient_id,
    quiz_id,
    answers,
    enhanced_answers,
    evaluation_result,
    level1_context=None,
):
    """
    Generate and store L2 OSA Data Assessment PDF in S3 and adminfiles.

    Patient Files UI lists "Reports" from AdminFile only (same as Level 1), not from File.
    """
    from flask_app.models import AdminFile  # local import to avoid circular

    evaluation_result = evaluation_result or {}
    level1_context = level1_context or {}

    fallback = _fallback_l2_narrative(
        answers=answers,
        enhanced_answers=enhanced_answers,
        evaluation_result=evaluation_result,
        level1_context=level1_context,
    )

    patient_quiz_json = {
        "raw_answers": answers or {},
        "enhanced_answers": enhanced_answers or {},
        "evaluation_summary": {
            "total_score": evaluation_result.get("total_score"),
            "risk_band": evaluation_result.get("risk_band"),
            "risk_label": evaluation_result.get("risk_label"),
            "red_flags": evaluation_result.get("red_flags"),
            "outcome_title": evaluation_result.get("outcome_title"),
            "outcome_body": evaluation_result.get("outcome_body"),
            "cta_text": evaluation_result.get("cta_text"),
            "diagnosed": evaluation_result.get("diagnosed"),
            "treatment": evaluation_result.get("treatment"),
        },
        "level1_snippets": {
            "symptoms_text": level1_context.get("symptoms_text"),
            "alert_text": level1_context.get("alert_text"),
            "risk_level_label": level1_context.get("risk_level"),
        },
    }

    llm_narrative = None
    try:
        llm_narrative = generate_l2_osa_assessment_narrative_with_bedrock(
            patient_quiz_json,
            patient_id=patient_id,
        )
    except Exception as e:
        current_app.logger.warning("L2 Bedrock narrative failed, using fallback: %s", e)

    narrative = _merge_l2_narrative(llm_narrative, fallback)
    if llm_narrative:
        current_app.logger.info("L2 OSA assessment narrative: Bedrock output merged with fallback for any missing fields")
    else:
        current_app.logger.info("L2 OSA assessment narrative: using deterministic fallback only")

    s3_client = get_s3_client()
    bucket = os.getenv("S3_BUCKET_NAME")
    pdf_bytes = _build_l2_report_pdf_bytes(
        quiz_id=quiz_id,
        answers=answers,
        enhanced_answers=enhanced_answers,
        narrative=narrative,
    )
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"OSA_Data_Assessment_Report_L2_{quiz_id}_{ts}.pdf"
    # Same prefix as Level 1 so reports stay under patients/<id>/reports/
    s3_key = f"patients/{patient_id}/reports/{filename}"

    s3_client.put_object(
        Bucket=bucket,
        Key=s3_key,
        Body=pdf_bytes,
        ContentType="application/pdf",
    )

    admin_row = AdminFile(
        name=filename,
        patient_id=patient_id,
        file_type="application/pdf",
        file_size=len(pdf_bytes),
        s3_key=s3_key,
        upload_date=datetime.utcnow(),
        file_category="Level 2 - OSA Data Assessment",
        is_public=False,
        analyzed=False,
    )
    db.session.add(admin_row)
    db.session.commit()
    current_app.logger.info(
        "L2 assessment PDF saved to adminfiles for patient %s: %s (AdminFile id=%s)",
        patient_id,
        s3_key,
        getattr(admin_row, "id", None),
    )
    return s3_key


def get_quiz_by_id(quiz_id):
    """
    Retrieve a quiz submission by ID.
    
    Args:
        quiz_id: Quiz ID
    
    Returns:
        VizBrizQuiz object or None
    """
    return VizBrizQuiz.query.get(quiz_id)


def get_patient_quizzes(patient_id, limit=10):
    """
    Get all quizzes for a patient.
    
    Args:
        patient_id: Patient ID
        limit: Maximum number of results
    
    Returns:
        List of VizBrizQuiz objects
    """
    return VizBrizQuiz.query.filter_by(user_id=patient_id).order_by(VizBrizQuiz.created_at.desc()).limit(limit).all()

