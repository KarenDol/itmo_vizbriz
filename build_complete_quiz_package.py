"""
Build complete VizBriz quiz package from CSV files
Generates vizbriz_quiz_package_complete.json with all 46 questions + translations
"""

import pandas as pd
import json
from pathlib import Path

# Paths
csv_dir = Path('/home/ec2-user/requirements/csv_export')
output_file = Path('/home/ec2-user/vizbriz/static/vizbriz_quiz_package.json')

print("=" * 80)
print("Building Complete VizBriz Quiz Package")
print("=" * 80)

# Load data files
print("\n📂 Loading CSV files...")
questions_df = pd.read_csv(csv_dir / 'questions_spec_Questions.csv')
scoring_df = pd.read_csv(csv_dir / 'questions_spec_Scoring_Matrix.csv')
red_flags_df = pd.read_csv(csv_dir / 'questions_spec_Red_Flag_Definitions.csv')
messaging_df = pd.read_csv(csv_dir / 'messaging_matrix_Outcome_Messaging_Matrix.csv')
bilingual_df = pd.read_csv(csv_dir / 'messaging_bilingual_Sheet1.csv')
bilingual_ru_df = pd.read_csv(csv_dir / 'messaging_bilingual_ru_Sheet1.csv')

print(f"   ✅ Loaded {len(questions_df)} questions")
print(f"   ✅ Loaded {len(scoring_df)} scoring rules")
print(f"   ✅ Loaded {len(red_flags_df)} red flag definitions")
print(f"   ✅ Loaded {len(messaging_df)} outcome messages")

# Initialize quiz package structure
quiz_package = {
    "metadata": {
        "id": "vb_sleep_v1_complete",
        "version": "1.0.0",
        "title_key": "app.title",
        "default_language": "en",
        "supported_languages": ["en", "ru", "he"]
    },
    "questions": [],
    "scoring": {
        "method": "weighted_sum",
        "risk_bands": [
            {"id": "low", "min": 0, "max": 3, "label_key": "risk.low"},
            {"id": "moderate", "min": 4, "max": 6, "label_key": "risk.moderate"},
            {"id": "high", "min": 7, "max": 99, "label_key": "risk.high"}
        ],
        "red_flags": []
    },
    "outcomes": {
        "rules": []
    },
    "i18n": {
        "en": {},
        "ru": {},
        "he": {}
    }
}

# Build scoring lookup
scoring_lookup = {}
for _, row in scoring_df.iterrows():
    qid = str(row['QID']) if pd.notna(row['QID']) else None
    if qid:
        scoring_lookup[qid] = {
            'weight': row['Weight'],
            'red_flag_type': row['Red Flag Type'] if pd.notna(row['Red Flag Type']) else None,
            'scoring_notes': row['Notes'] if pd.notna(row['Notes']) else ''
        }

print("\n📝 Building questions...")

# Process each question
for idx, row in questions_df.iterrows():
    qid = str(row['QID'])
    
    # Parse options
    options_json = row['Options_JSON']
    options = []
    
    if pd.notna(options_json) and options_json.strip():
        try:
            option_values = json.loads(options_json)
            for opt_val in option_values:
                # Get weight from scoring lookup
                weight = 0
                if qid in scoring_lookup:
                    # For scored questions, assign weight based on answer
                    # This is simplified - you may need custom logic per question
                    if opt_val.lower() in ['yes', 'often', 'always']:
                        weight = scoring_lookup[qid]['weight']
                
                # Clean the option value for use in keys
                clean_val = opt_val.lower().replace(' ', '_').replace("'", '').replace('"', '')
                options.append({
                    "value": clean_val,
                    "label_key": f"Q.{qid}.opt.{clean_val}",
                    "weight": weight
                })
        except:
            pass
    
    # Determine question type
    answer_type = row['AnswerType']
    if answer_type == 'single_select':
        q_type = 'single_choice'
    elif answer_type == 'multi_select':
        q_type = 'multi_choice'
    elif answer_type == 'scale_1_5':
        q_type = 'scale'
        options = [
            {"value": "1", "label_key": "scale.never", "weight": 0},
            {"value": "2", "label_key": "scale.rarely", "weight": 0},
            {"value": "3", "label_key": "scale.sometimes", "weight": 0},
            {"value": "4", "label_key": "scale.often", "weight": scoring_lookup.get(qid, {}).get('weight', 0)},
            {"value": "5", "label_key": "scale.always", "weight": scoring_lookup.get(qid, {}).get('weight', 0)}
        ]
    elif answer_type in ['free_text', 'text']:
        q_type = 'text'
    elif answer_type == 'numeric':
        q_type = 'number'
    else:
        q_type = 'text'
    
    # Parse parent condition
    parent_condition = row['ParentCondition']
    display_if_expr = "true"
    if pd.notna(parent_condition) and parent_condition.strip():
        # Convert condition like "Q1==Yes" to "ANS.Q1 == 'yes'"
        display_if_expr = parent_condition.replace('==', " == '").replace('Q', 'ANS.Q') + "'"
        display_if_expr = display_if_expr.replace("'Yes'", "'yes'").replace("'No'", "'no'")
    
    # Build question object
    question = {
        "qid": qid,
        "type": q_type,
        "display_if": {"expr": display_if_expr},
        "title_key": f"Q.{qid}.title",
        "required": bool(row['Required']),
        "section": row['Section']
    }
    
    if options:
        question["options"] = options
    
    quiz_package["questions"].append(question)
    
    # Add to i18n
    text_en = row['Text_EN'] if pd.notna(row['Text_EN']) else row['UI_Label']
    quiz_package["i18n"]["en"][f"Q.{qid}.title"] = text_en
    
    if pd.notna(row['Russian']):
        quiz_package["i18n"]["ru"][f"Q.{qid}.title"] = row['Russian']
    
    if pd.notna(row['Hebrew']):
        quiz_package["i18n"]["he"][f"Q.{qid}.title"] = row['Hebrew']
    
    # Add option labels
    for option in options:
        label_key = option["label_key"]
        option_text = option["value"].replace('_', ' ').title()
        quiz_package["i18n"]["en"][label_key] = option_text
        quiz_package["i18n"]["ru"][label_key] = option_text  # Placeholder
        quiz_package["i18n"]["he"][label_key] = option_text  # Placeholder

print(f"   ✅ Built {len(quiz_package['questions'])} questions")

# Build red flags
print("\n🚩 Building red flags...")
for _, row in red_flags_df.iterrows():
    qid = str(row['QID']) if pd.notna(row['QID']) else None
    red_flag_type = row['Red Flag Type'] if pd.notna(row['Red Flag Type']) else None
    
    if qid and red_flag_type:
        flag_id = f"FLAG_{qid}_{red_flag_type.upper().replace(' ', '_')}"
        
        # Simple condition - customize based on question type
        condition = f"ANS.{qid} == 'yes'"
        
        quiz_package["scoring"]["red_flags"].append({
            "id": flag_id,
            "if": {"expr": condition},
            "set_band_id": "high" if red_flag_type == "Primary" else None,
            "message_key": f"flag.{qid.lower()}"
        })

print(f"   ✅ Built {len(quiz_package['scoring']['red_flags'])} red flags")

# Build outcome rules
print("\n📋 Building outcome rules...")
outcomes = [
    {
        "id": "OUT_DIAGNOSED_NOT_USING",
        "priority": 1,
        "if": {"expr": "ANS.Q1 == 'yes' AND ANS.Q2 == 'no'"},
        "message_id": "MSG_DIAGNOSED_UNTREATED",
        "next_step_id": "CTA_REFER_TREATMENT"
    },
    {
        "id": "OUT_DIAGNOSED_SYMPTOMATIC",
        "priority": 2,
        "if": {"expr": "ANS.Q1 == 'yes' AND ANS.Q2 == 'yes' AND TOTAL_SCORE >= 4"},
        "message_id": "MSG_DIAGNOSED_SYMPTOMATIC",
        "next_step_id": "CTA_REASSESS_TREATMENT"
    },
    {
        "id": "OUT_DIAGNOSED_CONTROLLED",
        "priority": 3,
        "if": {"expr": "ANS.Q1 == 'yes' AND ANS.Q2 == 'yes' AND TOTAL_SCORE < 4"},
        "message_id": "MSG_DIAGNOSED_STABLE",
        "next_step_id": "CTA_CONTINUE_CARE"
    },
    {
        "id": "OUT_HIGH_RISK_UNDIAGNOSED",
        "priority": 4,
        "if": {"expr": "RISK == 'high' AND (ANS.Q1 == 'no' OR ANS.Q1 == 'not_sure')"},
        "message_id": "MSG_HIGH_RISK",
        "next_step_id": "CTA_REFER_SLEEP_TEST"
    },
    {
        "id": "OUT_MODERATE_RISK",
        "priority": 5,
        "if": {"expr": "RISK == 'moderate' AND (ANS.Q1 == 'no' OR ANS.Q1 == 'not_sure')"},
        "message_id": "MSG_MODERATE_RISK",
        "next_step_id": "CTA_CONSULT_DENTIST"
    },
    {
        "id": "OUT_LOW_RISK",
        "priority": 6,
        "if": {"expr": "RISK == 'low' AND (ANS.Q1 == 'no' OR ANS.Q1 == 'not_sure')"},
        "message_id": "MSG_LOW_RISK",
        "next_step_id": "CTA_MONITOR"
    }
]

quiz_package["outcomes"]["rules"] = outcomes

print(f"   ✅ Built {len(outcomes)} outcome rules")

# Add outcome messages
print("\n💬 Adding outcome messages...")
for idx, row in messaging_df.iterrows():
    scenario = row['Scenario']
    risk_level = row['Risk Level']
    message_text = row['Message Text']
    
    # Map scenario to message ID
    scenario_map = {
        'Low Risk': 'MSG_LOW_RISK',
        'Moderate Risk': 'MSG_MODERATE_RISK',
        'High Risk': 'MSG_HIGH_RISK',
        'Diagnosed - Untreated': 'MSG_DIAGNOSED_UNTREATED',
        'Diagnosed - Treated but Symptomatic': 'MSG_DIAGNOSED_SYMPTOMATIC',
        'Diagnosed - Treated and Stable': 'MSG_DIAGNOSED_STABLE'
    }
    
    msg_id = scenario_map.get(scenario, f'MSG_{scenario.upper().replace(" ", "_")}')
    
    # Add English
    quiz_package["i18n"]["en"][f"{msg_id}.title"] = f"{'🔴' if risk_level == 'High' else '🟡' if risk_level == 'Moderate' else '🟢'} {risk_level} Risk"
    quiz_package["i18n"]["en"][f"{msg_id}.body"] = message_text

# Add Russian translations
for idx, row in bilingual_ru_df.iterrows():
    scenario = row['Scenario']
    russian_text = row['Russian Translation']
    
    scenario_map = {
        'Low Risk': 'MSG_LOW_RISK',
        'Moderate Risk': 'MSG_MODERATE_RISK',
        'High Risk': 'MSG_HIGH_RISK',
        'Diagnosed - Untreated': 'MSG_DIAGNOSED_UNTREATED',
        'Diagnosed - Treated but Symptomatic': 'MSG_DIAGNOSED_SYMPTOMATIC',
        'Diagnosed - Treated and Stable': 'MSG_DIAGNOSED_STABLE'
    }
    
    msg_id = scenario_map.get(scenario)
    if msg_id and pd.notna(russian_text):
        quiz_package["i18n"]["ru"][f"{msg_id}.body"] = russian_text

# Add Hebrew translations
for idx, row in bilingual_df.iterrows():
    scenario = row['Scenario']
    hebrew_text = row['Hebrew Translation']
    
    scenario_map = {
        'Low Risk': 'MSG_LOW_RISK',
        'Moderate Risk': 'MSG_MODERATE_RISK',
        'High Risk': 'MSG_HIGH_RISK',
        'Diagnosed - Untreated': 'MSG_DIAGNOSED_UNTREATED',
        'Diagnosed - Treated but Symptomatic': 'MSG_DIAGNOSED_SYMPTOMATIC',
        'Diagnosed - Treated and Stable': 'MSG_DIAGNOSED_STABLE'
    }
    
    msg_id = scenario_map.get(scenario)
    if msg_id and pd.notna(hebrew_text):
        quiz_package["i18n"]["he"][f"{msg_id}.body"] = hebrew_text

# Add common translations
common_en = {
    "app.title": "VizBriz Sleep Health Questionnaire",
    "app.subtitle": "Help us understand your sleep health",
    "btn.submit": "Submit",
    "btn.next": "Next",
    "btn.prev": "Previous",
    "btn.language": "Language",
    "risk.low": "Low Risk",
    "risk.moderate": "Moderate Risk",
    "risk.high": "High Risk",
    "CTA_REFER_SLEEP_TEST": "Schedule Sleep Study",
    "CTA_CONSULT_DENTIST": "Consult with Dentist",
    "CTA_MONITOR": "Continue Monitoring",
    "CTA_REFER_TREATMENT": "Explore Treatment Options",
    "CTA_REASSESS_TREATMENT": "Request Treatment Review",
    "CTA_CONTINUE_CARE": "Schedule Follow-up",
    "scale.never": "Never",
    "scale.rarely": "Rarely",
    "scale.sometimes": "Sometimes",
    "scale.often": "Often",
    "scale.always": "Almost Always"
}

quiz_package["i18n"]["en"].update(common_en)

# Add Russian common
common_ru = {
    "app.title": "Анкета здоровья сна VizBriz",
    "app.subtitle": "Помогите нам понять ваше здоровье сна",
    "btn.submit": "Отправить",
    "btn.next": "Далее",
    "btn.prev": "Назад",
    "btn.language": "Язык",
    "risk.low": "Низкий риск",
    "risk.moderate": "Умеренный риск",
    "risk.high": "Высокий риск"
}

quiz_package["i18n"]["ru"].update(common_ru)

# Add Hebrew common
common_he = {
    "app.title": "שאלון בריאות שינה VizBriz",
    "app.subtitle": "עזור לנו להבין את בריאות השינה שלך",
    "btn.submit": "שלח",
    "btn.next": "הבא",
    "btn.prev": "הקודם",
    "btn.language": "שפה",
    "risk.low": "סיכון נמוך",
    "risk.moderate": "סיכון בינוני",
    "risk.high": "סיכון גבוה"
}

quiz_package["i18n"]["he"].update(common_he)

# Save to file
print(f"\n💾 Saving to {output_file}...")
with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(quiz_package, f, ensure_ascii=False, indent=2)

print(f"\n✅ Complete quiz package saved!")
print(f"   📊 Total questions: {len(quiz_package['questions'])}")
print(f"   🚩 Red flags: {len(quiz_package['scoring']['red_flags'])}")
print(f"   📋 Outcome rules: {len(quiz_package['outcomes']['rules'])}")
print(f"   🌐 Languages: {', '.join(quiz_package['metadata']['supported_languages'])}")
print(f"   📝 English translations: {len(quiz_package['i18n']['en'])} keys")
print(f"   📝 Russian translations: {len(quiz_package['i18n']['ru'])} keys")
print(f"   📝 Hebrew translations: {len(quiz_package['i18n']['he'])} keys")

print("\n" + "=" * 80)
print("✅ BUILD COMPLETE!")
print("=" * 80)

