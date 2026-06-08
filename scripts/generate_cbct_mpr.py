import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flask_app import create_app
from flask_app.utils.cbct_mpr_generator import generate_cbct_mpr


def main():
    parser = argparse.ArgumentParser(description="Generate CBCT MPR dataset")
    parser.add_argument("patient_id", type=int, help="Patient ID")
    parser.add_argument("folder", help="CBCT folder name")
    parser.add_argument("--no-overwrite", action="store_true", help="Do not delete existing MPR data")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        success, message = generate_cbct_mpr(args.patient_id, args.folder, overwrite=not args.no_overwrite)
        status = "SUCCESS" if success else "FAILED"
        print(f"[{status}] {message}")


if __name__ == "__main__":
    main()

