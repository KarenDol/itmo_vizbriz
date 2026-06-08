import json
import os
import sys
from pprint import pprint

import requests

API_URL = "https://vizbriz.dvir.us/reports"
API_TOKEN = os.getenv("LEVEL_1_REPORT_API_TOKEN")

PAYLOAD = {
    "scoring": {
        "total_score": 16,
        "risk_band": "diagnosed_not_treated",
        "risk_label": "Diagnosed - Not Treated",
        "red_flags": [
            "FOSQ driving item (1–2)",
            "BMI ≥ 25",
            "Driving sleepiness (Yes)",
            "Observed apneas/gasping/choking (≥Often)",
            "Bruxism (Yes)",
            "TMJ/Bruxism subtotal ≥2",
            "Sleep Symptoms subtotal ≥2",
            "Regular sedatives use",
            "Daily/heavy alcohol",
            "Hypertension / High BP"
        ],
        "outcome_title": "MSG_DIAGNOSED_NOT_TREATING.title",
        "outcome_body": "MSG_DIAGNOSED_NOT_TREATING.body",
        "cta_text": "CTA_START_TREATMENT"
    },
    "answers": {
        "submission_info": {
            "timestamp": "2025-11-11T11:17:29.598Z",
            "language": "en",
            "total_questions_answered": 53,
            "patient_id": 19114
        },
        "questions_and_answers": [],
        "raw_answers": {
            "DEMO_FULL_NAME": "Test Patient",
            "DEMO_EMAIL": "test@example.com",
            "DEMO_PHONE": "+1-555-123-4567",
            "Q1": "yes",
            "Q2": "no"
        }
    }
}


def main() -> None:
    if not API_TOKEN:
        print("Missing LEVEL_1_REPORT_API_TOKEN environment variable", file=sys.stderr)
        sys.exit(1)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_TOKEN}",
    }

    print(f"POST {API_URL}")
    response = requests.post(API_URL, json=PAYLOAD, headers=headers, timeout=30)
    print(f"HTTP {response.status_code}")
    print("Response headers:")
    for key, value in response.headers.items():
        print(f"  {key}: {value}")

    try:
        data = response.json()
    except ValueError:
        print("Response body (non-JSON):")
        print(response.text)
        sys.exit(0)

    print("Response JSON:")
    pprint(data)


if __name__ == "__main__":
    main()
