#!/usr/bin/env python3
"""
Migration: Add default clinics for DSOs with no clinics

This script ensures every DSO has at least one clinic by creating default clinics
for DSOs that currently have no clinics associated with them.
"""

import sys
import os
from datetime import datetime

# Add the parent directory to the path so we can import from flask_app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from flask_app import create_app
from flask_app.models import db, DSO, Clinic

def add_default_clinics_for_dsos():
    """
    Add default clinics for DSOs that don't have any clinics
    """
    print("🏥 ADDING DEFAULT CLINICS FOR DSOS WITH NO CLINICS")
    print("=" * 60)
    
    try:
        # Create Flask app and database context
        app = create_app()
        with app.app_context():
            
            # Step 1: Find all DSOs
            print("\n1. ANALYZING CURRENT DSO AND CLINIC SETUP:")
            print("-" * 40)
            
            all_dsos = DSO.query.all()
            print(f"   Found {len(all_dsos)} total DSOs")
            
            # Step 2: Check which DSOs have clinics
            dsos_without_clinics = []
            dsos_with_clinics = []
            
            for dso in all_dsos:
                clinic_count = Clinic.query.filter_by(dso_id=dso.id).count()
                if clinic_count == 0:
                    dsos_without_clinics.append(dso)
                    print(f"   ⚠️  DSO '{dso.name}' (ID: {dso.id}) - NO CLINICS")
                else:
                    dsos_with_clinics.append(dso)
                    print(f"   ✅ DSO '{dso.name}' (ID: {dso.id}) - {clinic_count} clinic(s)")
            
            if not dsos_without_clinics:
                print(f"\n🎉 All DSOs already have clinics! No action needed.")
                return True
            
            print(f"\n📊 SUMMARY:")
            print(f"   DSOs with clinics: {len(dsos_with_clinics)}")
            print(f"   DSOs without clinics: {len(dsos_without_clinics)}")
            
            # Step 3: Create default clinics for DSOs without clinics
            print(f"\n2. CREATING DEFAULT CLINICS:")
            print("-" * 40)
            
            created_clinics = []
            for dso in dsos_without_clinics:
                # Create a default clinic with the same name as the DSO
                default_clinic = Clinic(
                    name=f"{dso.name} Main Clinic",
                    dso_id=dso.id,
                    email=f"clinic@{dso.name.lower().replace(' ', '').replace('-', '').replace('_', '')}.com",
                    address=f"Main location for {dso.name}",
                    telephone="555-0000",
                    contact_person="Clinic Manager",
                    status="active",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                
                db.session.add(default_clinic)
                created_clinics.append(default_clinic)
                print(f"   ✨ Created default clinic: '{default_clinic.name}' for DSO '{dso.name}'")
            
            # Commit all new clinics
            db.session.commit()
            print(f"\n✅ Successfully created {len(created_clinics)} default clinics")
            
            # Step 4: Verify the results
            print(f"\n3. VERIFICATION:")
            print("-" * 40)
            
            # Check final state
            final_dsos_without_clinics = []
            for dso in DSO.query.all():
                clinic_count = Clinic.query.filter_by(dso_id=dso.id).count()
                if clinic_count == 0:
                    final_dsos_without_clinics.append(dso)
                else:
                    clinics = Clinic.query.filter_by(dso_id=dso.id).all()
                    clinic_names = [c.name for c in clinics]
                    print(f"   ✅ DSO '{dso.name}' now has {clinic_count} clinic(s): {', '.join(clinic_names)}")
            
            if final_dsos_without_clinics:
                print(f"\n❌ WARNING: {len(final_dsos_without_clinics)} DSOs still have no clinics!")
                for dso in final_dsos_without_clinics:
                    print(f"   - {dso.name} (ID: {dso.id})")
                return False
            else:
                print(f"\n🎉 SUCCESS: All DSOs now have at least one clinic!")
                return True
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def show_dso_clinic_summary():
    """
    Show a summary of DSO and clinic relationships
    """
    print("\n📋 DSO-CLINIC RELATIONSHIP SUMMARY:")
    print("=" * 60)
    
    try:
        app = create_app()
        with app.app_context():
            
            all_dsos = DSO.query.all()
            total_clinics = Clinic.query.count()
            
            print(f"   Total DSOs: {len(all_dsos)}")
            print(f"   Total Clinics: {total_clinics}")
            print(f"   Average clinics per DSO: {total_clinics / len(all_dsos):.1f}")
            
            print(f"\n   DSO Details:")
            for dso in all_dsos:
                clinics = Clinic.query.filter_by(dso_id=dso.id).all()
                clinic_names = [c.name for c in clinics]
                print(f"     • {dso.name}: {len(clinics)} clinic(s) - {', '.join(clinic_names)}")
            
    except Exception as e:
        print(f"❌ Error showing summary: {str(e)}")

if __name__ == "__main__":
    success = add_default_clinics_for_dsos()
    
    if success:
        show_dso_clinic_summary()
        print(f"\n✅ Migration completed successfully!")
        print(f"   You can now associate dentists with specific clinics.")
    else:
        print(f"\n❌ Migration failed - please check the errors above.") 