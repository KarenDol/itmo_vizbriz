"""
Dentist Treatment Quiz Structure
Defines the structure and questions for the dentist treatment assessment quiz
in both English and Hebrew versions.
"""

# English version of the quiz structure
ENGLISH_QUIZ_STRUCTURE = {
    "sections": [
        {
            "section_id": 1,
            "section_title": "1. Suitability for Oral Appliance",
            "questions": [
                {
                    "question_id": "suitability_adequate_dentition",
                    "question_label": "Dentition adequate for OSA appliance?",
                    "type": "yes_no",
                    "required": True,
                    "order": 1
                },
                {
                    "question_id": "suitability_upcoming_work",
                    "question_label": "Upcoming dental work?",
                    "type": "text",
                    "required": True,
                    "order": 2
                },
                {
                    "question_id": "suitability_tooth_wear_sensitivity",
                    "question_label": "Tooth wear or sensitivity?",
                    "type": "yes_no",
                    "required": True,
                    "order": 3
                },
                {
                    "question_id": "suitability_bruxism_clenching",
                    "question_label": "Bruxism / clenching?",
                    "type": "yes_no",
                    "required": True,
                    "order": 4
                },
                {
                    "question_id": "suitability_gag_reflex",
                    "question_label": "Increased gag reflex?",
                    "type": "yes_no",
                    "required": True,
                    "order": 5
                }
            ]
        },
        {
            "section_id": 2,
            "section_title": "2. Oral Opening, Occlusion & Jaw Movement",
            "questions": [
                {
                    "question_id": "oral_max_interincisal_opening_mm",
                    "question_label": "Maximum interincisal opening",
                    "type": "number",
                    "unit": "mm",
                    "required": True,
                    "order": 1
                },
                {
                    "question_id": "oral_deviation_opening_side",
                    "question_label": "Deviation on opening",
                    "type": "single_select",
                    "options": ["None", "Right", "Left"],
                    "aux_input": "number",
                    "aux_label": "Magnitude",
                    "aux_unit": "mm",
                    "required": True,
                    "order": 2
                },
                {
                    "question_id": "oral_protrusion_deviation_side",
                    "question_label": "Consistent deviation with protrusion",
                    "type": "single_select",
                    "options": ["None", "Right", "Left"],
                    "aux_input": "number",
                    "aux_label": "Magnitude",
                    "aux_unit": "mm",
                    "required": True,
                    "order": 3
                },
                {
                    "question_id": "oral_overjet_mm",
                    "question_label": "Overjet",
                    "type": "number",
                    "unit": "mm",
                    "required": True,
                    "order": 4
                },
                {
                    "question_id": "oral_overbite_mm",
                    "question_label": "Overbite",
                    "type": "number",
                    "unit": "mm",
                    "required": True,
                    "order": 5
                },
                {
                    "question_id": "oral_crossbite",
                    "question_label": "Crossbite?",
                    "type": "yes_no",
                    "aux_input": "text",
                    "aux_label": "If yes, specify teeth",
                    "required": True,
                    "order": 6
                },
                {
                    "question_id": "jaw_max_retrusion_mm",
                    "question_label": "Maximum retrusion",
                    "type": "number",
                    "unit": "mm",
                    "required": True,
                    "order": 7
                },
                {
                    "question_id": "jaw_max_protrusion_mm",
                    "question_label": "Maximum protrusion",
                    "type": "number",
                    "unit": "mm",
                    "required": True,
                    "order": 8
                },
                {
                    "question_id": "jaw_protrusive_range_mm",
                    "question_label": "Protrusive range (difference)",
                    "type": "number",
                    "unit": "mm",
                    "required": True,
                    "order": 9
                }
            ]
        },
        {
            "section_id": 3,
            "section_title": "3. TMJ & Muscles",
            "questions": [
                {
                    "question_id": "tmj_clicking_side",
                    "question_label": "TMJ signs/symptoms – Clicking",
                    "type": "single_select",
                    "options": ["None", "Right", "Left", "Both"],
                    "required": True,
                    "order": 1
                },
                {
                    "question_id": "tmj_crepitus_side",
                    "question_label": "TMJ signs/symptoms – Crepitus",
                    "type": "single_select",
                    "options": ["None", "Right", "Left", "Both"],
                    "required": True,
                    "order": 2
                },
                {
                    "question_id": "tmj_masticatory_pain",
                    "question_label": "Masticatory muscle pain",
                    "type": "yes_no",
                    "required": True,
                    "order": 3
                },
                {
                    "question_id": "tmj_opening_limitation",
                    "question_label": "Mouth opening limitation",
                    "type": "yes_no",
                    "required": True,
                    "order": 4
                },
                {
                    "question_id": "ear_symptoms",
                    "question_label": "Ear-related symptoms",
                    "type": "multi_select",
                    "options": ["Ear pain", "Tinnitus", "Vertigo", "None"],
                    "required": True,
                    "order": 5
                },
                {
                    "question_id": "muscle_palpation",
                    "question_label": "Muscle palpation discomfort",
                    "type": "multi_select",
                    "options": ["Masseter", "Temporalis", "Pterygoid", "SCM", "None"],
                    "required": True,
                    "order": 6
                }
            ]
        },
        {
            "section_id": 4,
            "section_title": "4. Additional Notes",
            "questions": [
                {
                    "question_id": "additional_notes",
                    "question_label": "Other relevant observations, including retention considerations",
                    "type": "textarea",
                    "required": False,
                    "order": 1
                }
            ]
        }
    ]
}

# Hebrew version of the quiz structure
HEBREW_QUIZ_STRUCTURE = {
    "sections": [
        {
            "section_id": 1,
            "section_title": "1. התאמה להתקן דנטלי (Oral Appliance)",
            "questions": [
                {
                    "question_id": "suitability_adequate_dentition",
                    "question_label": "האם המשנן מתאים לטיפול בהתקן לדום נשימה בשינה (OSA)?",
                    "type": "yes_no",
                    "required": False,
                    "order": 1
                },
                {
                    "question_id": "suitability_upcoming_work",
                    "question_label": "האם מתוכננת עבודה דנטלית קרובה?",
                    "type": "text",
                    "required": False,
                    "order": 2
                },
                {
                    "question_id": "suitability_tooth_wear_sensitivity",
                    "question_label": "האם יש שחיקת שיניים או רגישות בשיניים?",
                    "type": "yes_no",
                    "required": False,
                    "order": 3
                },
                {
                    "question_id": "suitability_bruxism_clenching",
                    "question_label": "Bruxism / Clenching (חריקת/הידוק שיניים)?",
                    "type": "yes_no",
                    "required": False,
                    "order": 4
                },
                {
                    "question_id": "suitability_gag_reflex",
                    "question_label": "רפלקס הקאה מוגבר (gag reflex)?",
                    "type": "yes_no",
                    "required": False,
                    "order": 5
                }
            ]
        },
        {
            "section_id": 2,
            "section_title": "2. פתיחת פה, סגר ותנועת לסת",
            "questions": [
                {
                    "question_id": "oral_max_interincisal_opening_mm",
                    "question_label": "פתיחת פה מרבית (Interincisal)",
                    "type": "number",
                    "unit": "מ\"מ",
                    "required": False,
                    "order": 1
                },
                {
                    "question_id": "oral_deviation_opening_side",
                    "question_label": "סטייה בפתיחת הפה",
                    "type": "single_select",
                    "options": ["ללא", "ימין", "שמאל"],
                    "aux_input": "number",
                    "aux_label": "עוצמת הסטייה",
                    "aux_unit": "מ\"מ",
                    "required": False,
                    "order": 2
                },
                {
                    "question_id": "oral_protrusion_deviation_side",
                    "question_label": "סטייה עקבית בתנועת פרוטרוזיה",
                    "type": "single_select",
                    "options": ["ללא", "ימין", "שמאל"],
                    "aux_input": "number",
                    "aux_label": "עוצמת הסטייה",
                    "aux_unit": "מ\"מ",
                    "required": False,
                    "order": 3
                },
                {
                    "question_id": "oral_overjet_mm",
                    "question_label": "Overjet",
                    "type": "number",
                    "unit": "מ\"מ",
                    "required": False,
                    "order": 4
                },
                {
                    "question_id": "oral_overbite_mm",
                    "question_label": "Overbite",
                    "type": "number",
                    "unit": "מ\"מ",
                    "required": False,
                    "order": 5
                },
                {
                    "question_id": "oral_crossbite",
                    "question_label": "Crossbite?",
                    "type": "yes_no",
                    "aux_input": "text",
                    "aux_label": "אם כן – ציין/י מספרי שיניים",
                    "required": False,
                    "order": 6
                },
                {
                    "question_id": "jaw_max_retrusion_mm",
                    "question_label": "Maximum retrusion",
                    "type": "number",
                    "unit": "מ\"מ",
                    "required": False,
                    "order": 7
                },
                {
                    "question_id": "jaw_max_protrusion_mm",
                    "question_label": "Maximum protrusion",
                    "type": "number",
                    "unit": "מ\"מ",
                    "required": False,
                    "order": 8
                },
                {
                    "question_id": "jaw_protrusive_range_mm",
                    "question_label": "Protrusive range (הפרש)",
                    "type": "number",
                    "unit": "מ\"מ",
                    "required": False,
                    "order": 9
                }
            ]
        },
        {
            "section_id": 3,
            "section_title": "3. מפרק הלסת (TMJ) ושרירים",
            "questions": [
                {
                    "question_id": "tmj_clicking_side",
                    "question_label": "סימנים/סימפטומים – קליקים",
                    "type": "single_select",
                    "options": ["ללא", "ימין", "שמאל", "שניהם"],
                    "required": False,
                    "order": 1
                },
                {
                    "question_id": "tmj_crepitus_side",
                    "question_label": "סימנים/סימפטומים – קרפיטוס",
                    "type": "single_select",
                    "options": ["ללא", "ימין", "שמאל", "שניהם"],
                    "required": False,
                    "order": 2
                },
                {
                    "question_id": "tmj_masticatory_pain",
                    "question_label": "כאב בשרירי הלעיסה",
                    "type": "yes_no",
                    "required": False,
                    "order": 3
                },
                {
                    "question_id": "tmj_opening_limitation",
                    "question_label": "מגבלה בפתיחת הפה",
                    "type": "yes_no",
                    "required": False,
                    "order": 4
                },
                {
                    "question_id": "ear_symptoms",
                    "question_label": "סימפטומים הקשורים לאוזניים",
                    "type": "multi_select",
                    "options": ["כאבי אוזניים", "טינטון", "ורטיגו", "ללא"],
                    "required": False,
                    "order": 5
                },
                {
                    "question_id": "muscle_palpation",
                    "question_label": "רגישות במישוש שרירים",
                    "type": "multi_select",
                    "options": ["Masseter", "Temporalis", "Pterygoid", "SCM", "ללא"],
                    "required": False,
                    "order": 6
                }
            ]
        },
        {
            "section_id": 4,
            "section_title": "4. הערות נוספות",
            "questions": [
                {
                    "question_id": "additional_notes",
                    "question_label": "מידע רלוונטי נוסף, כולל שיקולי רטנציה",
                    "type": "textarea",
                    "required": False,
                    "order": 1
                }
            ]
        }
    ]
}

def get_quiz_structure(language='en'):
    """
    Get the quiz structure for the specified language
    
    Args:
        language (str): 'en' for English, 'he' for Hebrew
        
    Returns:
        dict: Quiz structure for the specified language
    """
    if language.lower() == 'he':
        return HEBREW_QUIZ_STRUCTURE
    else:
        return ENGLISH_QUIZ_STRUCTURE

def get_question_by_id(question_id, language='en'):
    """
    Get a specific question by its ID
    
    Args:
        question_id (str): The question ID to find
        language (str): 'en' for English, 'he' for Hebrew
        
    Returns:
        dict: Question structure or None if not found
    """
    structure = get_quiz_structure(language)
    
    for section in structure['sections']:
        for question in section['questions']:
            if question['question_id'] == question_id:
                return question
    
    return None

def get_section_by_id(section_id, language='en'):
    """
    Get a specific section by its ID
    
    Args:
        section_id (int): The section ID to find
        language (str): 'en' for English, 'he' for Hebrew
        
    Returns:
        dict: Section structure or None if not found
    """
    structure = get_quiz_structure(language)
    
    for section in structure['sections']:
        if section['section_id'] == section_id:
            return section
    
    return None

def validate_quiz_answers(answers, language='en'):
    """
    Validate quiz answers against the structure
    
    Args:
        answers (dict): The quiz answers to validate
        language (str): 'en' for English, 'he' for Hebrew
        
    Returns:
        tuple: (is_valid, errors_list)
    """
    structure = get_quiz_structure(language)
    errors = []
    
    for section in structure['sections']:
        for question in section['questions']:
            question_id = question['question_id']
            
            # Check if required questions are answered
            if question.get('required', False) and question_id not in answers:
                errors.append(f"Required question '{question_id}' is missing")
                continue
            
            if question_id in answers:
                answer = answers[question_id]
                
                # Validate based on question type
                if question['type'] == 'yes_no':
                    if answer not in ['yes', 'no']:
                        errors.append(f"Question '{question_id}' must be 'yes' or 'no'")
                
                elif question['type'] == 'number':
                    try:
                        float(answer)
                    except (ValueError, TypeError):
                        errors.append(f"Question '{question_id}' must be a number")
                
                elif question['type'] == 'single_select':
                    if 'options' in question and answer not in question['options']:
                        errors.append(f"Question '{question_id}' must be one of: {', '.join(question['options'])}")
                
                elif question['type'] == 'multi_select':
                    if not isinstance(answer, list):
                        errors.append(f"Question '{question_id}' must be a list")
                    elif 'options' in question:
                        for item in answer:
                            if item not in question['options']:
                                errors.append(f"Question '{question_id}' contains invalid option: {item}")
    
    return len(errors) == 0, errors
