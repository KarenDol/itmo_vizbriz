#!/usr/bin/env python3
"""
Check Level 4 data status and help diagnose issues
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask_app import create_app
from flask_app.models import L4DeviceDesign, L4DeviceOption
from flask_app.extensions import db

def check_status():
    """Check Level 4 data status"""
    
    app = create_app()
    
    with app.app_context():
        print("="*80)
        print("Level 4 Data Status Check")
        print("="*80)
        
        # Check if tables exist
        try:
            inspector = db.inspect(db.engine)
            tables = inspector.get_table_names()
            
            if 'l4_device_design' not in tables:
                print("\n❌ ERROR: l4_device_design table does not exist")
                print("   Run the SQL migration to create tables")
                return
            
            if 'l4_device_options' not in tables:
                print("\n❌ ERROR: l4_device_options table does not exist")
                print("   Run the SQL migration to create tables")
                return
            
            print("\n✓ Tables exist")
            
        except Exception as e:
            print(f"\n❌ Error checking tables: {e}")
            return
        
        # Check data
        try:
            design_count = L4DeviceDesign.query.count()
            option_count = L4DeviceOption.query.count()
            
            print(f"\nData in database:")
            print(f"  Device Designs: {design_count}")
            print(f"  Device Options: {option_count}")
            
            if design_count == 0:
                print("\n⚠ No data found in database")
                print("\nTo process reports:")
                print("  python scripts/process_l4_reports.py --input '/home/ec2-user/patient_data/Report Examples/Level 4 Structure/'")
                return
            
            # Check sample record
            sample = L4DeviceDesign.query.first()
            print(f"\nSample record:")
            print(f"  Report: {sample.source_report_id}")
            print(f"  Patient ID: {sample.patient_id}")
            print(f"  Context: {sample.design_context}")
            
            # Check for clinical context columns
            inspector = db.inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('l4_device_design')]
            
            has_clinical = 'ahi' in columns
            print(f"\nClinical context columns:")
            print(f"  Has AHI column: {has_clinical}")
            
            if not has_clinical:
                print("\n⚠ Clinical context columns missing!")
                print("   Run migration: migrations/add_clinical_context_columns_mysql.sql")
                print("   Then re-process reports to capture clinical data")
            else:
                print(f"  AHI value: {sample.ahi}")
                print(f"  O2 Nadir: {sample.o2_nadir}")
                print(f"  Tongue Position: {sample.tongue_position}")
            
            # Show all reports
            print(f"\nAll reports in database:")
            all_designs = L4DeviceDesign.query.all()
            for design in all_designs:
                print(f"  - {design.source_report_id} ({design.design_context})")
            
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    check_status()
