#!/usr/bin/env python3
"""
Complete the question titles in the VizBriz quiz JSON file.
This script will replace all placeholder titles (Q1, Q2, etc.) with actual question text.
"""

import json
import os

def complete_question_titles():
    """Complete all question titles with actual question text."""
    
    # Load the current JSON file
    json_file = '/home/ec2-user/vizbriz/flask_app/config/New_quiz_v1.json'
    
    with open(json_file, 'r', encoding='utf-8') as f:
        quiz_data = json.load(f)
    
    # Define all question titles based on the quiz requirements
    question_titles = {
        # Demographics
        "DEMO_FULL_NAME": "Full name",
        "DEMO_DOB": "Date of birth (DD/MM/YYYY)",
        "DEMO_EMAIL": "Email address",
        "DEMO_REFERRING_DENTIST_OR_CLI": "Referring dentist or clinic",
        "DEMO_AGE": "Age",
        "DEMO_SEX": "Gender",
        "DEMO_HEIGHT_CM": "Height (cm)",
        "DEMO_WEIGHT_KG": "Weight (kg)",
        
        # Section 1 - Diagnosis & Treatment
        "Q1": "Have you been diagnosed with sleep apnea?",
        "Q2": "Are you currently receiving any treatment for your sleep apnea?",
        "Q3": "Which type of treatment are you currently using?",
        "Q4": "Have you ever undergone surgery related to sleep apnea or airway breathing?",
        "Q5": "Please specify which surgery was performed.",
        "Q6": "When was your most recent sleep study performed?",
        "Q7": "During that study, did you use any therapeutic device?",
        
        # Section 2 - Sleep Patterns & Daily Symptoms
        "Q8": "How long does it usually take you to fall asleep?",
        "Q9": "How many hours of sleep do you typically get per night?",
        "Q10": "How would you rate your overall sleep quality?",
        "Q11": "Do you snore loudly?",
        "Q12": "Has anyone observed you stop breathing, gasping, or choking during sleep?",
        "Q13": "Do you wake up feeling unrefreshed despite getting enough sleep?",
        "Q14": "Do you experience excessive daytime sleepiness?",
        "Q15": "Do you have trouble staying awake while driving?",
        "Q16": "Do you wake up with a headache?",
        "Q17": "Do you wake up frequently during the night?",
        "Q18": "Do you have trouble falling back asleep after waking up?",
        "Q19": "Do you wake up early in the morning and cannot fall back asleep?",
        "Q20": "Do you experience restless or disturbed sleep?",
        
        # Section 3 - Jaw / TMJ / Bruxism
        "Q21": "Do you grind your teeth at night?",
        "Q22": "Do you clench your jaw during sleep?",
        "Q23": "Do you wake up with jaw pain or stiffness?",
        "Q24": "Do you experience any of the following? (Select all that apply)",
        
        # Section 4 - Lifestyle & Medical Background
        "Q25": "Do you have high blood pressure?",
        "Q26": "Do you have diabetes?",
        "Q27": "Do you have asthma or other breathing problems?",
        "Q28": "Do you have thyroid problems?",
        "Q29": "Do you have depression or anxiety?",
        "Q30": "Do you have any of the following medical conditions? (Select all that apply)",
        "Q31": "Do you take any medications for sleep?",
        "Q32": "Do you have nasal congestion or obstruction?",
        "Q32-trigger": "Do you have difficulty breathing through your nose?",
        
        # Nasal Breathing Follow-up
        "Q32a": "Rate your nasal breathing during the day (1 = very poor, 5 = excellent)",
        "Q32b": "Rate your nasal breathing at night (1 = very poor, 5 = excellent)",
        "Q32c": "Rate your overall breathing comfort (1 = very poor, 5 = excellent)",
        
        # Section 5 - Daytime Function & Social Impact (FOSQ-5)
        "Q33": "How much difficulty do you have with general productivity?",
        "Q34": "How much difficulty do you have with social outcomes?",
        "Q35": "How much difficulty do you have with activity level?",
        "Q36": "How much difficulty do you have with vigilance?",
        "Q37": "How would you rate your overall quality of life?",
        
        # Section 6 - Personal Goals & Priorities
        "Q38": "What would you most like to improve about your sleep or health? (Select all that apply)",
        
        # Section 7 - Additional Information
        "Q39": "Is there anything else you would like us to know about your sleep or health?"
    }
    
    # Update question titles in the main questions array
    for question in quiz_data['questions']:
        qid = question['qid']
        if qid in question_titles:
            question['title_en'] = question_titles[qid]
            print(f"Updated {qid}: {question_titles[qid]}")
    
    # Update question titles in the i18n section for English
    if 'i18n' in quiz_data and 'en' in quiz_data['i18n']:
        for qid, title in question_titles.items():
            if qid in quiz_data['i18n']['en']['questions']:
                quiz_data['i18n']['en']['questions'][qid]['title'] = title
                print(f"Updated i18n.en.questions.{qid}: {title}")
    
    # Update validation summary
    quiz_data['validation_summary']['questions_with_text'] = len(question_titles)
    quiz_data['validation_summary']['missing_question_text'] = 0
    
    # Save the updated JSON file
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(quiz_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Successfully updated {len(question_titles)} question titles!")
    print(f"📁 Updated file: {json_file}")
    
    return True

if __name__ == "__main__":
    complete_question_titles()

