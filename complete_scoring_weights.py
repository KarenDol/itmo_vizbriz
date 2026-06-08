#!/usr/bin/env python3
"""
Complete the scoring weights for questions that need scoring review.
This script will add proper score_map configurations for Q10, Q30, Q37, Q38.
"""

import json
import os

def complete_scoring_weights():
    """Add scoring weights for questions needing review."""
    
    # Load the current JSON file
    json_file = '/home/ec2-user/vizbriz/flask_app/config/New_quiz_v1.json'
    
    with open(json_file, 'r', encoding='utf-8') as f:
        quiz_data = json.load(f)
    
    # Define scoring weights for questions needing review
    scoring_updates = {
        "Q10": {  # Sleep quality rating
            "score_map": {
                "mode": "option",
                "weights": {
                    "very_poor": 3,
                    "poor": 2,
                    "fair": 1,
                    "good": 0,
                    "very_good": 0
                },
                "polarity": "positive"
            },
            "score_flag": True,
            "score_source": "inferred_scale",
            "needs_scoring_review": False
        },
        "Q30": {  # Medical conditions (multi-choice)
            "score_map": {
                "mode": "multi_option",
                "weights": {
                    "hypertension": 1,
                    "diabetes": 1,
                    "asthma": 1,
                    "thyroid_disorder": 1,
                    "depression/anxiety": 1,
                    "other": 0
                },
                "polarity": "positive"
            },
            "score_flag": True,
            "score_source": "inferred_multi",
            "needs_scoring_review": False
        },
        "Q37": {  # Quality of life rating
            "score_map": {
                "mode": "option",
                "weights": {
                    "very_low": 3,
                    "low": 2,
                    "moderate": 1,
                    "high": 0,
                    "very_high": 0
                },
                "polarity": "positive"
            },
            "score_flag": True,
            "score_source": "inferred_scale",
            "needs_scoring_review": False
        },
        "Q38": {  # Personal goals (multi-choice)
            "score_map": {
                "mode": "multi_option",
                "weights": {
                    "better_sleep_quality": 1,
                    "reduce_snoring": 1,
                    "more_energy": 1,
                    "better_concentration": 1,
                    "reduce_daytime_sleepiness": 1,
                    "improve_mood": 1,
                    "better_relationship": 1,
                    "other": 0
                },
                "polarity": "positive"
            },
            "score_flag": True,
            "score_source": "inferred_multi",
            "needs_scoring_review": False
        }
    }
    
    # Update questions with scoring weights
    for question in quiz_data['questions']:
        qid = question['qid']
        if qid in scoring_updates:
            update_data = scoring_updates[qid]
            question.update(update_data)
            print(f"Updated scoring for {qid}: {update_data['score_map']['mode']}")
    
    # Update validation summary
    quiz_data['validation_summary']['needs_scoring_review'] = 0
    quiz_data['validation_summary']['scored_questions'] = 28  # 24 + 4 new
    
    # Save the updated JSON file
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(quiz_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Successfully updated scoring weights for 4 questions!")
    print(f"📁 Updated file: {json_file}")
    print(f"📊 Total scored questions: 28")
    print(f"📊 Questions needing review: 0")
    
    return True

if __name__ == "__main__":
    complete_scoring_weights()

