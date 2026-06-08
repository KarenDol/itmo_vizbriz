#!/usr/bin/env python3
"""
Check what observations exist for patient 10318 to see if demographics are being extracted.
"""

import sys
import os
import json
import mysql.connector

# Database configuration
DB_CONFIG = {
    'host': 'vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com',
    'user': 'admin',
    'password': 'Vizbriz2025!',
    'database': 'vizbriz',
    'port': 3306
}

def check_observations(patient_id: int):
    """Check what observations exist for the patient."""
    conn = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # Get all observations for the patient
        cursor.execute("""
            SELECT source_type, source_text, extracted_observations, created_at
            FROM observation_store 
            WHERE patient_id = %s 
            ORDER BY created_at DESC
        """, (patient_id,))
        
        observations = cursor.fetchall()
        
        print(f"📊 Observations for Patient {patient_id}")
        print("=" * 60)
        print(f"Total observations: {len(observations)}")
        print()
        
        # Analyze observations
        demographics_found = []
        sleep_study_found = []
        anatomy_found = []
        tmj_found = []
        
        for i, obs in enumerate(observations):
            print(f"Observation {i+1}:")
            print(f"  Source Type: {obs['source_type']}")
            print(f"  Source Text: {obs['source_text'][:100]}...")
            
            try:
                obs_data = json.loads(obs['extracted_observations'])
                path = obs_data.get('path', '')
                value = obs_data.get('value', '')
                observation = obs_data.get('observation', '')
                
                print(f"  Path: {path}")
                print(f"  Value: {value}")
                print(f"  Observation: {observation[:100]}...")
                
                # Categorize observations
                if 'demographics' in path.lower():
                    demographics_found.append(obs_data)
                elif 'sleep_study' in path.lower():
                    sleep_study_found.append(obs_data)
                elif 'anatomy' in path.lower():
                    anatomy_found.append(obs_data)
                elif 'tmj' in path.lower():
                    tmj_found.append(obs_data)
                    
            except json.JSONDecodeError:
                print(f"  Error: Could not parse JSON")
            
            print()
        
        # Summary
        print("📋 SUMMARY")
        print("=" * 60)
        print(f"Demographics observations: {len(demographics_found)}")
        print(f"Sleep study observations: {len(sleep_study_found)}")
        print(f"Anatomy observations: {len(anatomy_found)}")
        print(f"TMJ observations: {len(tmj_found)}")
        
        if demographics_found:
            print("\n🎯 DEMOGRAPHICS OBSERVATIONS FOUND:")
            for obs in demographics_found:
                print(f"  - {obs.get('path')}: {obs.get('value')}")
        else:
            print("\n❌ NO DEMOGRAPHICS OBSERVATIONS FOUND")
            print("This explains why demographics are empty in the canonical JSON!")
        
        return observations
        
    except Exception as e:
        print(f"Error checking observations: {e}")
        return []
    finally:
        if conn:
            conn.close()

def main():
    """Main function."""
    patient_id = 10318
    
    print("🔍 Checking Observations for Patient 10318")
    print("=" * 60)
    
    observations = check_observations(patient_id)
    
    if not observations:
        print("\n❌ No observations found for patient 10318")
        print("This means the document processing may not have completed successfully.")
    else:
        print(f"\n✅ Found {len(observations)} observations for patient 10318")

if __name__ == "__main__":
    main()
