"""Utility script to validate conversion dashboard access for a dentist/patient pair.

Usage example:
    python scripts/check_conversion_access.py --dentist-id 132 --patient-id 96394
"""

import argparse
import os
import sys
from typing import List

CURRENT_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flask_app import create_app
from flask_app.models import Dentist, Patient


def _normalize_clinic_ids(ids: List[int]) -> List[int]:
    return sorted({int(cid) for cid in ids if cid is not None})


def check_access(dentist_id: int, patient_id: int) -> None:
    app = create_app()

    with app.app_context():
        dentist: Dentist | None = Dentist.query.get(dentist_id)
        patient: Patient | None = Patient.query.get(patient_id)

        if dentist is None:
            print(f"❌ Dentist {dentist_id} not found")
            return

        if patient is None:
            print(f"❌ Patient {patient_id} not found")
            return

        clinic_ids = _normalize_clinic_ids(dentist.get_clinic_ids()) if hasattr(dentist, "get_clinic_ids") else []
        patient_clinic_id = patient.clinic_id
        patient_status = (patient.status or "").lower() if patient.status else None

        is_archived = patient_status == "archived"
        clinic_match = patient_clinic_id is not None and patient_clinic_id in clinic_ids

        if dentist.role == "admin":
            has_access = not is_archived
        else:
            has_access = bool(clinic_match and not is_archived)

        print("Dentist Info")
        print("-------------")
        print(f"ID: {dentist.id}")
        print(f"Name: {dentist.name}")
        print(f"Role: {dentist.role}")
        print(f"Clinics: {clinic_ids or 'None'}")
        print()

        print("Patient Info")
        print("-------------")
        print(f"ID: {patient.id}")
        print(f"Name: {patient.name}")
        print(f"Clinic ID: {patient_clinic_id}")
        print(f"Status: {patient.status or 'None'}")
        print()

        print("Access Evaluation")
        print("-----------------")
        print(f"Clinic match: {'Yes' if clinic_match else 'No'}")
        print(f"Archived: {'Yes' if is_archived else 'No'}")
        print(f"Access granted (conversion dashboard logic): {'✅' if has_access else '❌'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check conversion dashboard access for a dentist/patient pair")
    parser.add_argument("--dentist-id", type=int, required=True, help="Dentist ID to evaluate")
    parser.add_argument("--patient-id", type=int, required=True, help="Patient ID to evaluate")
    args = parser.parse_args()

    check_access(dentist_id=args.dentist_id, patient_id=args.patient_id)


if __name__ == "__main__":
    main()

