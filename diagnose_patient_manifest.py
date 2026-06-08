#!/usr/bin/env python3
"""
Diagnostic script to check patient manifest data and identify issues
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

def diagnose_patient_manifest(patient_id):
    """Diagnose patient manifest data"""
    print("=" * 80)
    print(f"DIAGNOSING PATIENT MANIFEST FOR PATIENT {patient_id}")
    print("=" * 80)
    
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # 1. Check if patient exists
        print("\n1. CHECKING PATIENT EXISTS:")
        print("-" * 40)
        cursor.execute("""
            SELECT id, name, email, status, create_date
            FROM patients 
            WHERE id = %s
        """, (patient_id,))
        
        patient = cursor.fetchone()
        if not patient:
            print(f"❌ Patient {patient_id} not found in database!")
            return
        
        print(f"✅ Patient found: {patient['name']} (ID: {patient['id']})")
        print(f"   Status: {patient['status']}")
        print(f"   Created: {patient['create_date']}")
        
        # 2. Check raw patient_manifest table data
        print("\n2. RAW PATIENT_MANIFEST TABLE DATA:")
        print("-" * 40)
        cursor.execute("""
            SELECT id, stage_key, stage_number, stage_name, is_completed, 
                   completion_date, stage_data, status_message, created_at, updated_at
            FROM patient_manifest 
            WHERE patient_id = %s
            ORDER BY stage_number
        """, (patient_id,))
        
        manifest_entries = cursor.fetchall()
        print(f"Found {len(manifest_entries)} manifest entries:")
        
        if not manifest_entries:
            print("❌ No manifest entries found for this patient!")
            print("   This means the patient has no stages defined in the manifest table.")
            return
        
        for entry in manifest_entries:
            status_icon = "✓" if entry['is_completed'] else "○"
            print(f"\n  {status_icon} Stage {entry['stage_number']}: {entry['stage_name']}")
            print(f"    Key: {entry['stage_key']}")
            print(f"    Completed: {entry['is_completed']}")
            print(f"    Completion Date: {entry['completion_date']}")
            print(f"    Status Message: {entry['status_message']}")
            print(f"    Stage Data: {entry['stage_data']}")
            print(f"    Created: {entry['created_at']}")
            print(f"    Updated: {entry['updated_at']}")
        
        # 3. Check what stages should exist according to manifest definition
        print("\n3. MANIFEST DEFINITION CHECK:")
        print("-" * 40)
        manifest_def = get_manifest_definition()
        print(f"Manifest definition has {len(manifest_def)} stages:")
        
        for stage in manifest_def:
            print(f"  Stage {stage['stage_number']}: {stage['stage_name']} (key: {stage['key']})")
        
        # 4. Compare manifest definition with actual data
        print("\n4. COMPARISON: MANIFEST DEFINITION vs ACTUAL DATA:")
        print("-" * 40)
        
        manifest_keys_in_db = {entry['stage_key'] for entry in manifest_entries}
        manifest_keys_defined = {stage['key'] for stage in manifest_def}
        
        print("Stages in database:")
        for key in sorted(manifest_keys_in_db):
            print(f"  ✓ {key}")
        
        print("\nStages in manifest definition:")
        for key in sorted(manifest_keys_defined):
            print(f"  ✓ {key}")
        
        missing_in_db = manifest_keys_defined - manifest_keys_in_db
        extra_in_db = manifest_keys_in_db - manifest_keys_defined
        
        if missing_in_db:
            print(f"\n❌ Missing in database: {missing_in_db}")
        if extra_in_db:
            print(f"\n⚠️  Extra in database: {extra_in_db}")
        if not missing_in_db and not extra_in_db:
            print("\n✅ All stages are present in database")
        
        # 5. Check completion status
        print("\n5. COMPLETION STATUS ANALYSIS:")
        print("-" * 40)
        
        completed_stages = [entry for entry in manifest_entries if entry['is_completed']]
        incomplete_stages = [entry for entry in manifest_entries if not entry['is_completed']]
        
        print(f"Completed stages: {len(completed_stages)}")
        for entry in completed_stages:
            print(f"  ✓ {entry['stage_name']} (completed on {entry['completion_date']})")
        
        print(f"\nIncomplete stages: {len(incomplete_stages)}")
        for entry in incomplete_stages:
            print(f"  ○ {entry['stage_name']}")
        
        # 6. Check for potential issues
        print("\n6. POTENTIAL ISSUES:")
        print("-" * 40)
        
        issues_found = []
        
        # Check for null values
        null_completion_dates = [entry for entry in manifest_entries if entry['is_completed'] and entry['completion_date'] is None]
        if null_completion_dates:
            issues_found.append(f"❌ {len(null_completion_dates)} completed stages have NULL completion dates")
        
        # Check for future completion dates
        future_dates = []
        for entry in manifest_entries:
            if entry['completion_date'] and entry['completion_date'] > datetime.utcnow():
                future_dates.append(entry)
        if future_dates:
            issues_found.append(f"⚠️  {len(future_dates)} stages have future completion dates")
        
        # Check for empty status messages
        empty_messages = [entry for entry in manifest_entries if not entry['status_message'] or entry['status_message'].strip() == '']
        if empty_messages:
            issues_found.append(f"⚠️  {len(empty_messages)} stages have empty status messages")
        
        # Check for invalid JSON in stage_data
        invalid_json = []
        for entry in manifest_entries:
            if entry['stage_data']:
                try:
                    json.loads(entry['stage_data'])
                except (json.JSONDecodeError, TypeError):
                    invalid_json.append(entry)
        if invalid_json:
            issues_found.append(f"❌ {len(invalid_json)} stages have invalid JSON in stage_data")
        
        if issues_found:
            for issue in issues_found:
                print(issue)
        else:
            print("✅ No obvious issues found")
        
        # 7. Check related tables for completion evidence
        print("\n7. CHECKING RELATED TABLES FOR COMPLETION EVIDENCE:")
        print("-" * 40)
        
        # Check conversion_quiz table
        cursor.execute("""
            SELECT id, quiz_type, created_at, patient_email
            FROM conversion_quiz 
            WHERE user_id = %s
        """, (patient_id,))
        
        quiz_entries = cursor.fetchall()
        print(f"Quiz entries: {len(quiz_entries)}")
        for entry in quiz_entries:
            print(f"  ✓ {entry['quiz_type']} completed on {entry['created_at']}")
        
        # Check files table
        cursor.execute("""
            SELECT id, name, category, subcategory, upload_date
            FROM files 
            WHERE patient_id = %s
            ORDER BY upload_date DESC
        """, (patient_id,))
        
        files = cursor.fetchall()
        print(f"\nFiles uploaded: {len(files)}")
        for file in files[:5]:  # Show first 5 files
            print(f"  📁 {file['name']} ({file['category']}/{file['subcategory']}) - {file['upload_date']}")
        if len(files) > 5:
            print(f"  ... and {len(files) - 5} more files")
        
        # Check adminfiles table
        cursor.execute("""
            SELECT id, name, file_category, upload_date
            FROM adminfiles 
            WHERE patient_id = %s
            ORDER BY upload_date DESC
        """, (patient_id,))
        
        admin_files = cursor.fetchall()
        print(f"\nAdmin files: {len(admin_files)}")
        for file in admin_files[:5]:  # Show first 5 files
            print(f"  📄 {file['name']} ({file['file_category']}) - {file['upload_date']}")
        if len(admin_files) > 5:
            print(f"  ... and {len(admin_files) - 5} more files")
        
        # 8. Recommendations
        print("\n8. RECOMMENDATIONS:")
        print("-" * 40)
        
        if not manifest_entries:
            print("🔧 Create manifest entries for this patient using the manifest definition")
        elif not completed_stages:
            print("🔧 No stages are marked as completed. Check if stages should be completed based on:")
            print("   - Quiz completion (conversion_quiz table)")
            print("   - File uploads (files/adminfiles tables)")
            print("   - Other completion criteria")
        else:
            print("✅ Patient has completed stages. The issue might be in the application logic.")
        
        print("\n" + "=" * 80)
        print("DIAGNOSIS COMPLETE")
        print("=" * 80)
        
    except Exception as e:
        print(f"❌ Error during diagnosis: {e}")
    finally:
        if conn:
            conn.close()

def main():
    """Main function"""
    print("🔍 PATIENT MANIFEST DIAGNOSTIC TOOL")
    print("=" * 60)
    print("This tool helps diagnose issues with patient manifest data")
    print()
    
    while True:
        try:
            # Get patient ID
            patient_id_input = input("Enter patient ID to diagnose (or 'quit' to exit): ").strip()
            
            if patient_id_input.lower() in ['quit', 'exit', 'q']:
                print("👋 Goodbye!")
                break
            
            patient_id = int(patient_id_input)
            
            # Run diagnosis
            diagnose_patient_manifest(patient_id)
            
            # Ask if user wants to continue
            continue_choice = input("\nDo you want to diagnose another patient? [y/N]: ").strip().lower()
            if continue_choice != 'y':
                print("👋 Goodbye!")
                break
                
        except ValueError:
            print("❌ Invalid patient ID. Please enter a number.")
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"❌ Unexpected error: {e}")

if __name__ == "__main__":
    main() 