#!/usr/bin/env python3
"""
Complete missing options for questions that need them.
This script will add options for Q38 (Personal Goals) and Q24 (TMJ symptoms).
"""

import json
import os

def complete_missing_options():
    """Add missing options for questions."""
    
    # Load the current JSON file
    json_file = '/home/ec2-user/vizbriz/flask_app/config/New_quiz_v1.json'
    
    with open(json_file, 'r', encoding='utf-8') as f:
        quiz_data = json.load(f)
    
    # Define missing options
    options_updates = {
        "Q24": {  # TMJ symptoms (multi-choice)
            "type": "multi_choice",
            "input": "checkbox",
            "options": [
                {
                    "value": "ear_pain",
                    "label": "Ear pain"
                },
                {
                    "value": "tinnitus",
                    "label": "Tinnitus (ringing in ears)"
                },
                {
                    "value": "vertigo",
                    "label": "Vertigo (dizziness)"
                },
                {
                    "value": "other",
                    "label": "Other (please specify)"
                }
            ]
        },
        "Q38": {  # Personal goals (multi-choice)
            "options": [
                {
                    "value": "better_sleep_quality",
                    "label": "Better sleep quality"
                },
                {
                    "value": "reduce_snoring",
                    "label": "Reduce snoring"
                },
                {
                    "value": "more_energy",
                    "label": "More energy during the day"
                },
                {
                    "value": "better_concentration",
                    "label": "Better concentration"
                },
                {
                    "value": "reduce_daytime_sleepiness",
                    "label": "Reduce daytime sleepiness"
                },
                {
                    "value": "improve_mood",
                    "label": "Improve mood"
                },
                {
                    "value": "better_relationship",
                    "label": "Better relationship with partner"
                },
                {
                    "value": "other",
                    "label": "Other (please specify)"
                }
            ]
        }
    }
    
    # Update questions with missing options
    for question in quiz_data['questions']:
        qid = question['qid']
        if qid in options_updates:
            update_data = options_updates[qid]
            question.update(update_data)
            print(f"Updated options for {qid}: {len(update_data['options'])} options")
    
    # Update validation summary
    quiz_data['validation_summary']['options_with_text'] = 36  # 34 + 2 new
    
    # Save the updated JSON file
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(quiz_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Successfully updated options for 2 questions!")
    print(f"📁 Updated file: {json_file}")
    print(f"📊 Total options with text: 36")
    
    return True

if __name__ == "__main__":
    complete_missing_options()

