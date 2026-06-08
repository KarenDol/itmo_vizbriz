#!/usr/bin/env python3

import sys
import os
import json
import mysql.connector
from vizbriz.flask_app import create_app
from vizbriz.flask_app.config.document_observation_extractor_phase2 import create_minimal_canonical_json_for_patient

# Database configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'vizbriz_user',
    'password': 'vizbriz_pass',
    'database': 'vizbriz_db'
}

def clean_and_recreate_canonical():
    """Delete all existing envelope data and recreate it properly"""
    
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    
    # Get all patients with observations
    cursor.execute("""
        SELECT DISTINCT patient_id, COUNT(*) as obs_count
        FROM observation_store 
        GROUP BY patient_id 
        ORDER BY obs_count DESC
    """)
    
    patients = cursor.fetchall()
    print(f"Found {len(patients)} patients with observations")
    
    # Delete ALL existing envelope data
    cursor.execute("DELETE FROM patient_case_envelope")
    deleted_count = cursor.rowcount
    conn.commit()
    print(f"Deleted {deleted_count} existing envelope records (all types)")
    
    conn.close()
    
    # Recreate envelope data for each patient
    results = []
    for patient in patients:
        patient_id = patient['patient_id']
        print(f"\nProcessing patient {patient_id} ({patient['obs_count']} observations)...")
        
        try:
            # Create canonical envelope
            result = create_minimal_canonical_json_for_patient(patient_id)
            results.append({'patient_id': patient_id, 'result': result})
            
            if result.get('success'):
                print(f"✓ Successfully created canonical envelope for patient {patient_id}")
            else:
                print(f"✗ Failed to create canonical envelope for patient {patient_id}: {result.get('message')}")
                
        except Exception as e:
            print(f"✗ Error processing patient {patient_id}: {e}")
            results.append({'patient_id': patient_id, 'result': {'success': False, 'message': str(e)}})
    
    # Summary
    successful = sum(1 for r in results if r['result'].get('success'))
    failed = len(results) - successful
    
    print(f"\n=== SUMMARY ===")
    print(f"Total patients processed: {len(results)}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    
    return results

if __name__ == "__main__":
    # Create Flask app and run within application context
    app = create_app()
    with app.app_context():
        clean_and_recreate_canonical()
