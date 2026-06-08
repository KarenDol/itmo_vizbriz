#!/usr/bin/env python3
"""
Script to initialize patient manifest entries for patients missing manifest data
"""

import os
import sys
from datetime import datetime
import json
from dotenv import load_dotenv

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load environment variables
load_dotenv()

# Set environment variables for database connection
os.environ['DB_USERNAME'] = 'admin'
os.environ['DB_PASSWORD'] = 'Vizbriz2025!'
os.environ['DB_HOST'] = 'vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com'
os.environ['DB_PORT'] = '3306'
os.environ['DB_NAME'] = 'vizbriz'

import mysql.connector
from flask_app.config.manifest_config import get_manifest_definition

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'user': os.getenv('DB_USERNAME'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'port': int(os.getenv('DB_PORT', 3306))
}

def get_patients_without_manifest():
    """Get all patients who don't have any manifest entries"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT p.id, p.name, p.email, p.status, p.create_date
            FROM patients p
            LEFT JOIN patient_manifest pm ON p.id = pm.patient_id
            WHERE pm.patient_id IS NULL
            AND p.status != 'Archived'
            ORDER BY p.create_date DESC
        """)
        
        patients = cursor.fetchall()
        return patients
        
    except Exception as e:
        print(f"❌ Error getting patients without manifest: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_patient_manifest_count(patient_id):
    """Get count of manifest entries for a patient"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT COUNT(*) 
            FROM patient_manifest 
            WHERE patient_id = %s
        """, (patient_id,))
        
        count = cursor.fetchone()[0]
        return count
        
    except Exception as e:
        print(f"❌ Error getting manifest count: {e}")
        return 0
    finally:
        if conn:
            conn.close()

def initialize_patient_manifest(patient_id, dry_run=True):
    """Initialize manifest entries for a patient"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        # Get manifest definition
        manifest_def = get_manifest_definition()
        
        print(f"\n{'[DRY RUN] ' if dry_run else ''}Initializing manifest for patient {patient_id}:")
        print("-" * 60)
        
        # Check if patient exists
        cursor.execute("SELECT id, name, email FROM patients WHERE id = %s", (patient_id,))
        patient = cursor.fetchone()
        if not patient:
            print(f"❌ Patient {patient_id} not found!")
            return False
        
        print(f"✅ Patient: {patient[1]} ({patient[2]})")
        
        # Check existing manifest entries
        existing_count = get_patient_manifest_count(patient_id)
        if existing_count > 0:
            print(f"⚠️  Patient already has {existing_count} manifest entries")
            if not dry_run:
                choice = input("Do you want to continue and add missing stages? [y/N]: ").strip().lower()
                if choice != 'y':
                    print("❌ Cancelled")
                    return False
        
        # Get existing stage keys for this patient
        cursor.execute("""
            SELECT stage_key 
            FROM patient_manifest 
            WHERE patient_id = %s
        """, (patient_id,))
        
        existing_stages = {row[0] for row in cursor.fetchall()}
        
        # Prepare manifest entries
        entries_to_create = []
        for stage in manifest_def:
            if stage['key'] not in existing_stages:
                entries_to_create.append({
                    'patient_id': patient_id,
                    'stage_key': stage['key'],
                    'stage_number': stage['stage_number'],
                    'stage_name': stage['stage_name'],
                    'is_completed': False,
                    'completion_date': None,
                    'stage_data': None,
                    'status_message': 'Not started',
                    'created_at': datetime.utcnow(),
                    'updated_at': datetime.utcnow()
                })
        
        print(f"📋 Will create {len(entries_to_create)} manifest entries:")
        for entry in entries_to_create:
            print(f"  ○ Stage {entry['stage_number']}: {entry['stage_name']} ({entry['stage_key']})")
        
        if dry_run:
            print(f"\n[DRY RUN] Would create {len(entries_to_create)} entries")
            return True
        
        # Actually create the entries
        if entries_to_create:
            insert_query = """
                INSERT INTO patient_manifest 
                (patient_id, stage_key, stage_number, stage_name, is_completed, 
                 completion_date, stage_data, status_message, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            for entry in entries_to_create:
                cursor.execute(insert_query, (
                    entry['patient_id'],
                    entry['stage_key'],
                    entry['stage_number'],
                    entry['stage_name'],
                    entry['is_completed'],
                    entry['completion_date'],
                    entry['stage_data'],
                    entry['status_message'],
                    entry['created_at'],
                    entry['updated_at']
                ))
            
            conn.commit()
            print(f"✅ Successfully created {len(entries_to_create)} manifest entries")
        else:
            print("✅ No new entries needed - all stages already exist")
        
        return True
        
    except Exception as e:
        print(f"❌ Error initializing manifest: {e}")
        if not dry_run and conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

def auto_complete_stages_based_on_evidence(patient_id, dry_run=True):
    """Auto-complete stages based on evidence in other tables"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        print(f"\n{'[DRY RUN] ' if dry_run else ''}Checking for completion evidence:")
        print("-" * 60)
        
        # Check quiz completion
        cursor.execute("""
            SELECT quiz_type, created_at 
            FROM conversion_quiz 
            WHERE user_id = %s
        """, (patient_id,))
        
        quiz_entries = cursor.fetchall()
        if quiz_entries:
            print(f"📝 Found {len(quiz_entries)} quiz entries:")
            for quiz in quiz_entries:
                print(f"  ✓ {quiz['quiz_type']} completed on {quiz['created_at']}")
        
        # Check files
        cursor.execute("""
            SELECT name, category, subcategory, upload_date
            FROM files 
            WHERE patient_id = %s
            ORDER BY upload_date
        """, (patient_id,))
        
        files = cursor.fetchall()
        if files:
            print(f"📁 Found {len(files)} files:")
            for file in files:
                print(f"  📄 {file['name']} ({file['category']}/{file['subcategory']}) - {file['upload_date']}")
        
        # Check admin files
        cursor.execute("""
            SELECT name, file_category, upload_date
            FROM adminfiles 
            WHERE patient_id = %s
            ORDER BY upload_date
        """, (patient_id,))
        
        admin_files = cursor.fetchall()
        if admin_files:
            print(f"📋 Found {len(admin_files)} admin files:")
            for file in admin_files:
                print(f"  📋 {file['name']} ({file['file_category']}) - {file['upload_date']}")
        
        # Auto-complete logic based on evidence
        updates_to_make = []
        
        # Quiz completion evidence
        if quiz_entries:
            quiz_types = {quiz['quiz_type'] for quiz in quiz_entries}
            if 'basic_quiz' in quiz_types or 'conversion_quiz' in quiz_types:
                updates_to_make.append({
                    'stage_key': 'quiz_completion',
                    'is_completed': True,
                    'completion_date': max(quiz['created_at'] for quiz in quiz_entries),
                    'status_message': f"Quiz completed on {max(quiz['created_at'] for quiz in quiz_entries).strftime('%B %d, %Y')}"
                })
        
        # File upload evidence
        if files or admin_files:
            all_files = files + admin_files
            file_categories = {file.get('category', file.get('file_category', '')) for file in all_files}
            
            # Sleep test files
            if any('sleep' in cat.lower() for cat in file_categories):
                updates_to_make.append({
                    'stage_key': 'sleep_test',
                    'is_completed': True,
                    'completion_date': max(file['upload_date'] for file in all_files if 'sleep' in file.get('category', file.get('file_category', '')).lower()),
                    'status_message': f"Sleep test files uploaded on {max(file['upload_date'] for file in all_files if 'sleep' in file.get('category', file.get('file_category', '')).lower()).strftime('%B %d, %Y')}"
                })
            
            # CBCT files
            if any('cbct' in cat.lower() for cat in file_categories):
                updates_to_make.append({
                    'stage_key': 'cbct_scan',
                    'is_completed': True,
                    'completion_date': max(file['upload_date'] for file in all_files if 'cbct' in file.get('category', file.get('file_category', '')).lower()),
                    'status_message': f"CBCT scan uploaded on {max(file['upload_date'] for file in all_files if 'cbct' in file.get('category', file.get('file_category', '')).lower()).strftime('%B %d, %Y')}"
                })
        
        if updates_to_make:
            print(f"\n🔄 Would update {len(updates_to_make)} stages based on evidence:")
            for update in updates_to_make:
                print(f"  ✓ {update['stage_key']}: {update['status_message']}")
            
            if not dry_run:
                # Check if manifest entries exist first
                cursor.execute("""
                    SELECT COUNT(*) 
                    FROM patient_manifest 
                    WHERE patient_id = %s
                """, (patient_id,))
                
                manifest_count = cursor.fetchone()['COUNT(*)']
                
                if manifest_count == 0:
                    print("⚠️  No manifest entries found. Creating them first...")
                    # Initialize manifest entries first
                    initialize_patient_manifest(patient_id, dry_run=False)
                
                # Now apply updates
                for update in updates_to_make:
                    cursor.execute("""
                        UPDATE patient_manifest 
                        SET is_completed = %s, completion_date = %s, status_message = %s, updated_at = %s
                        WHERE patient_id = %s AND stage_key = %s
                    """, (
                        update['is_completed'],
                        update['completion_date'],
                        update['status_message'],
                        datetime.utcnow(),
                        patient_id,
                        update['stage_key']
                    ))
                
                conn.commit()
                print(f"✅ Successfully updated {len(updates_to_make)} stages")
        else:
            print("ℹ️  No automatic updates based on evidence")
        
        return True
        
    except Exception as e:
        print(f"❌ Error auto-completing stages: {e}")
        if not dry_run and conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

def fix_patient_manifest_complete(patient_id, dry_run=True):
    """Complete fix for patient manifest - creates ALL manifest entries like the validation system"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        print(f"\n{'[DRY RUN] ' if dry_run else ''}COMPLETE MANIFEST FIX FOR PATIENT {patient_id}")
        print("=" * 80)
        
        # Check if patient exists
        cursor.execute("SELECT id, name, email FROM patients WHERE id = %s", (patient_id,))
        patient = cursor.fetchone()
        if not patient:
            print(f"❌ Patient {patient_id} not found!")
            return False
        
        print(f"✅ Patient: {patient['name']} ({patient['email']})")
        
        # Step 1: Validate ALL stages (like manifest_validator_complete does)
        print("\n🔍 STEP 1: VALIDATING ALL STAGES")
        print("-" * 50)
        
        validation_results = {}
        manifest_def = get_manifest_definition()
        
        # Validate each stage (simplified version of manifest_validator_complete)
        for stage in manifest_def:
            stage_key = stage['key']
            stage_name = stage['stage_name']
            
            print(f"  Validating {stage_name} ({stage_key})...")
            
            # Check for completion evidence based on stage type
            is_completed = False
            completion_date = None
            status_message = f"No {stage_name.lower()} completed"
            
            if stage_key == 'quiz_completion':
                # Check quiz completion
                cursor.execute("""
                    SELECT created_at FROM conversion_quiz 
                    WHERE user_id = %s AND quiz_type IN ('basic_quiz', 'conversion_quiz')
                """, (patient_id,))
                result = cursor.fetchone()
                if result:
                    is_completed = True
                    completion_date = result['created_at']
                    status_message = f"Quiz completed on {completion_date.strftime('%B %d, %Y')}"
            
            elif stage_key == 'sleep_test':
                # Check sleep test files
                cursor.execute("""
                    SELECT upload_date FROM files 
                    WHERE patient_id = %s AND (category LIKE '%sleep%' OR subcategory LIKE '%sleep%')
                """, (patient_id,))
                result = cursor.fetchone()
                if result:
                    is_completed = True
                    completion_date = result['upload_date']
                    status_message = f"Sleep test files uploaded on {completion_date.strftime('%B %d, %Y')}"
            
            elif stage_key == 'cbct_scan':
                # Check CBCT files
                cursor.execute("""
                    SELECT upload_date FROM files 
                    WHERE patient_id = %s AND (category LIKE '%cbct%' OR subcategory LIKE '%cbct%')
                """, (patient_id,))
                result = cursor.fetchone()
                if result:
                    is_completed = True
                    completion_date = result['upload_date']
                    status_message = f"CBCT scan uploaded on {completion_date.strftime('%B %d, %Y')}"
            
            # Add result for this stage (whether completed or not)
            validation_results[stage_key] = {
                'is_completed': is_completed,
                'completion_date': completion_date,
                'stage_data': None,
                'status_message': status_message
            }
            
            status_icon = "✅" if is_completed else "❌"
            print(f"    {status_icon} {status_message}")
        
        # Step 2: Update manifest table with ALL results (like update_manifest_from_validation)
        print(f"\n🔧 STEP 2: UPDATING MANIFEST TABLE WITH ALL {len(validation_results)} STAGES")
        print("-" * 50)
        
        if not dry_run:
            # Get stage number and name from manifest definition
            stage_dict = {stage['key']: (stage['stage_number'], stage['stage_name']) for stage in manifest_def}
            
            for stage_key, result in validation_results.items():
                print(f"\nProcessing stage: {stage_key}")
                
                # First try to UPDATE existing row
                cursor.execute("""
                    UPDATE patient_manifest 
                    SET 
                        is_completed = %s,
                        completion_date = %s,
                        stage_data = %s,
                        status_message = %s,
                        updated_at = NOW()
                    WHERE patient_id = %s AND stage_key = %s
                """, (
                    result['is_completed'],
                    result['completion_date'],
                    result['stage_data'],
                    result['status_message'],
                    patient_id,
                    stage_key
                ))
                
                # If no rows were updated, insert a new row
                if cursor.rowcount == 0:
                    stage_number, stage_name = stage_dict.get(stage_key, (None, None))
                    
                    if stage_number is None or stage_name is None:
                        print(f"⚠️  WARNING: No stage definition found for key '{stage_key}'")
                        continue
                    
                    print(f"   INSERTING new row for stage {stage_key}:")
                    print(f"   - Stage Number: {stage_number}")
                    print(f"   - Stage Name: {stage_name}")
                    print(f"   - Is Completed: {result['is_completed']}")
                    print(f"   - Status Message: {result['status_message']}")
                    
                    cursor.execute("""
                        INSERT INTO patient_manifest (patient_id, stage_key, stage_number, stage_name, is_completed, completion_date, stage_data, status_message, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    """, (
                        patient_id,
                        stage_key,
                        stage_number,
                        stage_name,
                        result['is_completed'],
                        result['completion_date'],
                        result['stage_data'],
                        result['status_message']
                    ))
                    print(f"   ✅ Successfully inserted new row for stage {stage_key}")
                else:
                    print(f"   ✅ Updated existing row for stage {stage_key}")
            
            conn.commit()
            print(f"✅ Successfully processed ALL {len(validation_results)} stages")
        else:
            print(f"[DRY RUN] Would process ALL {len(validation_results)} stages:")
            for stage_key, result in validation_results.items():
                status_icon = "✅" if result['is_completed'] else "❌"
                print(f"  {status_icon} {stage_key}: {result['status_message']}")
        
        print(f"\n{'[DRY RUN] ' if dry_run else ''}COMPLETE MANIFEST FIX FINISHED")
        print("=" * 80)
        
        return True
        
    except Exception as e:
        print(f"❌ Error in complete manifest fix: {e}")
        if not dry_run and conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

def main():
    """Main function"""
    print("🔧 PATIENT MANIFEST INITIALIZATION TOOL")
    print("=" * 60)
    print("This tool initializes manifest entries for patients missing manifest data")
    print()
    
    while True:
        print("\nOptions:")
        print("1. List patients without manifest entries")
        print("2. Initialize manifest for specific patient (dry run)")
        print("3. Initialize manifest for specific patient (actual)")
        print("4. Auto-complete stages based on evidence (dry run)")
        print("5. Auto-complete stages based on evidence (actual)")
        print("6. Full initialization + auto-completion (dry run)")
        print("7. Full initialization + auto-completion (actual)")
        print("8. Complete fix for patient (dry run)")
        print("9. Complete fix for patient (actual)")
        print("10. Quit")
        
        choice = input("\nSelect option (1-10): ").strip()
        
        if choice == '1':
            print("\n📋 PATIENTS WITHOUT MANIFEST ENTRIES:")
            print("-" * 50)
            patients = get_patients_without_manifest()
            if patients:
                print(f"Found {len(patients)} patients without manifest entries:")
                for patient in patients:
                    print(f"  ID: {patient['id']} | {patient['name']} | {patient['email']} | {patient['status']}")
            else:
                print("✅ All patients have manifest entries!")
        
        elif choice == '2':
            patient_id = input("Enter patient ID: ").strip()
            try:
                initialize_patient_manifest(int(patient_id), dry_run=True)
            except ValueError:
                print("❌ Invalid patient ID")
        
        elif choice == '3':
            patient_id = input("Enter patient ID: ").strip()
            try:
                initialize_patient_manifest(int(patient_id), dry_run=False)
            except ValueError:
                print("❌ Invalid patient ID")
        
        elif choice == '4':
            patient_id = input("Enter patient ID: ").strip()
            try:
                auto_complete_stages_based_on_evidence(int(patient_id), dry_run=True)
            except ValueError:
                print("❌ Invalid patient ID")
        
        elif choice == '5':
            patient_id = input("Enter patient ID: ").strip()
            try:
                auto_complete_stages_based_on_evidence(int(patient_id), dry_run=False)
            except ValueError:
                print("❌ Invalid patient ID")
        
        elif choice == '6':
            patient_id = input("Enter patient ID: ").strip()
            try:
                patient_id = int(patient_id)
                print(f"\n🔍 FULL INITIALIZATION + AUTO-COMPLETION (DRY RUN) FOR PATIENT {patient_id}")
                print("=" * 80)
                initialize_patient_manifest(patient_id, dry_run=True)
                auto_complete_stages_based_on_evidence(patient_id, dry_run=True)
            except ValueError:
                print("❌ Invalid patient ID")
        
        elif choice == '7':
            patient_id = input("Enter patient ID: ").strip()
            try:
                patient_id = int(patient_id)
                print(f"\n🔧 FULL INITIALIZATION + AUTO-COMPLETION FOR PATIENT {patient_id}")
                print("=" * 80)
                confirm = input("This will modify the database. Are you sure? [y/N]: ").strip().lower()
                if confirm == 'y':
                    initialize_patient_manifest(patient_id, dry_run=False)
                    auto_complete_stages_based_on_evidence(patient_id, dry_run=False)
                else:
                    print("❌ Cancelled")
            except ValueError:
                print("❌ Invalid patient ID")
        
        elif choice == '8':
            patient_id = input("Enter patient ID: ").strip()
            try:
                fix_patient_manifest_complete(int(patient_id), dry_run=True)
            except ValueError:
                print("❌ Invalid patient ID")
        
        elif choice == '9':
            patient_id = input("Enter patient ID: ").strip()
            try:
                patient_id = int(patient_id)
                confirm = input("This will modify the database. Are you sure? [y/N]: ").strip().lower()
                if confirm == 'y':
                    fix_patient_manifest_complete(patient_id, dry_run=False)
                else:
                    print("❌ Cancelled")
            except ValueError:
                print("❌ Invalid patient ID")
        
        elif choice == '10':
            print("👋 Goodbye!")
            break
        
        else:
            print("❌ Invalid option")

if __name__ == "__main__":
    main() 