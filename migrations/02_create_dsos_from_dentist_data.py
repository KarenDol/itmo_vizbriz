#!/usr/bin/env python3
"""
Migration: Create DSO records from unique dentist DSO strings, then create associations
This ensures every dentist has a matching DSO record
"""

import sys
import os
from datetime import datetime

# Add the parent directory to the path so we can import from flask_app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from flask_app import create_app
from flask_app.models import db, Dentist, DSO
from flask_app.models import dentist_dso_association

def migrate_dso_data():
    """
    Create DSO records from unique dentist DSO strings and create associations
    """
    print("🚀 Starting DSO migration from dentist data...")
    
    try:
        # Step 1: Get unique DSO values from dentists table
        print("\n📊 Step 1: Analyzing unique DSO values in dentists table...")
        
        unique_dsos = db.session.query(Dentist.DSO).filter(
            Dentist.DSO.isnot(None),
            Dentist.DSO != '',
            Dentist.DSO != 'NULL'
        ).distinct().all()
        
        print(f"Found {len(unique_dsos)} unique DSO values:")
        for dso_tuple in unique_dsos:
            dso_name = dso_tuple[0]
            dentist_count = db.session.query(Dentist).filter(Dentist.DSO == dso_name).count()
            print(f"  - '{dso_name}' ({dentist_count} dentists)")
        
        # Step 2: Create DSO records for each unique DSO string
        print(f"\n🏗️  Step 2: Creating DSO records...")
        
        created_dsos = []
        for dso_tuple in unique_dsos:
            dso_name = dso_tuple[0]
            
            # Check if DSO already exists
            existing_dso = DSO.query.filter_by(name=dso_name).first()
            if existing_dso:
                print(f"  ✅ DSO '{dso_name}' already exists (ID: {existing_dso.id})")
                created_dsos.append(existing_dso)
            else:
                # Create new DSO with placeholder data
                new_dso = DSO(
                    name=dso_name,
                    email=f"info@{dso_name.lower().replace(' ', '').replace('-', '')}.com",
                    contact_person="Admin",
                    telephone="000-000-0000",
                    status="active",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                
                db.session.add(new_dso)
                db.session.flush()  # Get the ID without committing
                created_dsos.append(new_dso)
                print(f"  ✨ Created DSO '{dso_name}' (ID: {new_dso.id})")
        
        # Commit all DSO creations
        db.session.commit()
        print(f"✅ Successfully created/verified {len(created_dsos)} DSO records")
        
        # Step 3: Create associations between dentists and DSOs
        print(f"\n🔗 Step 3: Creating dentist-DSO associations...")
        
        associations_created = 0
        for dso in created_dsos:
            # Find all dentists with this DSO string
            dentists_with_dso = Dentist.query.filter(Dentist.DSO == dso.name).all()
            
            for dentist in dentists_with_dso:
                # Check if association already exists
                existing_association = db.session.query(dentist_dso_association).filter_by(
                    dentist_id=dentist.id,
                    dso_id=dso.id
                ).first()
                
                if not existing_association:
                    # Create association
                    stmt = dentist_dso_association.insert().values(
                        dentist_id=dentist.id,
                        dso_id=dso.id
                    )
                    db.session.execute(stmt)
                    associations_created += 1
                    print(f"  🔗 Associated dentist '{dentist.name}' (ID: {dentist.id}) with DSO '{dso.name}' (ID: {dso.id})")
        
        # Commit all associations
        db.session.commit()
        print(f"✅ Successfully created {associations_created} dentist-DSO associations")
        
        # Step 4: Verify the results
        print(f"\n📈 Step 4: Verification Summary...")
        
        # Show DSO summary
        all_dsos = DSO.query.all()
        print(f"\n📋 DSO Summary ({len(all_dsos)} total DSOs):")
        for dso in all_dsos:
            dentist_count = db.session.query(dentist_dso_association).filter_by(dso_id=dso.id).count()
            print(f"  - DSO '{dso.name}' (ID: {dso.id}) - {dentist_count} dentists")
        
        # Show dentists with associations
        dentists_with_associations = db.session.query(Dentist).join(
            dentist_dso_association, Dentist.id == dentist_dso_association.c.dentist_id
        ).distinct().count()
        
        total_dentists = Dentist.query.count()
        print(f"\n👥 Dentist Summary:")
        print(f"  - Total dentists: {total_dentists}")
        print(f"  - Dentists with DSO associations: {dentists_with_associations}")
        print(f"  - Dentists without DSO associations: {total_dentists - dentists_with_associations}")
        
        print(f"\n🎉 Migration completed successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Error during migration: {str(e)}")
        db.session.rollback()
        return False

def main():
    """Main function to run the migration"""
    print("🔧 DSO Migration Script")
    print("=" * 50)
    
    # Create Flask app context
    app = create_app()
    
    with app.app_context():
        success = migrate_dso_data()
        
        if success:
            print("\n✅ Migration completed successfully!")
            print("Next steps:")
            print("1. Review the created DSO records and update contact information as needed")
            print("2. Run the final migration to drop the old DSO column")
            return 0
        else:
            print("\n❌ Migration failed!")
            return 1

if __name__ == "__main__":
    sys.exit(main()) 