#!/usr/bin/env python3
"""
Check prerequisites before uploading to KB
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask_app import create_app
from flask_app.models import L4DeviceDesign, L4DeviceOption
from flask_app.extensions import db

def check_prerequisites():
    """Check if ready to upload"""
    
    app = create_app()
    
    with app.app_context():
        print("="*80)
        print("Checking Prerequisites for KB Upload")
        print("="*80)
        
        # Check if tables exist
        try:
            inspector = db.inspect(db.engine)
            tables = inspector.get_table_names()
            
            if 'l4_device_design' not in tables:
                print("❌ ERROR: l4_device_design table does not exist")
                print("   Run the SQL migration to create tables first")
                return False
            
            if 'l4_device_options' not in tables:
                print("❌ ERROR: l4_device_options table does not exist")
                print("   Run the SQL migration to create tables first")
                return False
            
            print("✓ Tables exist")
            
        except Exception as e:
            print(f"❌ ERROR checking tables: {e}")
            return False
        
        # Check if data exists
        try:
            design_count = L4DeviceDesign.query.count()
            option_count = L4DeviceOption.query.count()
            
            print(f"\nData in database:")
            print(f"  Device Designs: {design_count}")
            print(f"  Device Options: {option_count}")
            
            if design_count == 0:
                print("\n⚠ WARNING: No device designs found in database")
                print("   Process some Level 4 reports first:")
                print("   python scripts/process_l4_reports.py --input '/path/to/reports/'")
                return False
            
            print("✓ Data exists")
            
        except Exception as e:
            print(f"❌ ERROR checking data: {e}")
            return False
        
        # Check for clinical context fields (optional)
        try:
            inspector = db.inspect(db.engine)
            design_columns = [col['name'] for col in inspector.get_columns('l4_device_design')]
            
            has_clinical = 'ahi' in design_columns and 'tongue_position' in design_columns
            
            if not has_clinical:
                print("\n⚠ NOTE: Clinical context fields not found")
                print("   (Optional) Run migration to add clinical context:")
                print("   migrations/add_clinical_context_to_l4.sql")
                print("   Then re-process reports to capture clinical data")
            else:
                print("\n✓ Clinical context fields exist")
            
        except Exception as e:
            print(f"⚠ Could not check clinical fields: {e}")
        
        # Check S3 configuration
        import os
        s3_bucket = os.getenv('S3_BUCKET_NAME')
        if not s3_bucket:
            print("\n⚠ WARNING: S3_BUCKET_NAME not set")
            print("   Upload will save locally only")
        else:
            print(f"\n✓ S3 bucket configured: {s3_bucket}")
        
        print("\n" + "="*80)
        print("READY TO UPLOAD!")
        print("="*80)
        print("\nYou can now run:")
        print("  python scripts/upload_l4_to_kb.py")
        print("\nOr test locally first:")
        print("  python scripts/upload_l4_to_kb.py --no-s3 --output-dir /tmp/case-cards")
        
        return True

if __name__ == "__main__":
    check_prerequisites()
