#!/usr/bin/env python3
"""
Reprocess clinical manifest for patient 10318 to extract all clinical observations
"""

import sys
import os
import json
import mysql.connector
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def reprocess_clinical_manifest():
    """Reprocess clinical manifest for patient 10318"""
    
    print("🔄 Reprocessing clinical manifest for patient 10318...")
    
    try:
        # Database connection
        conn = mysql.connector.connect(
            host='vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com',
            user='admin',
            password='Vizbriz2025!',
            database='vizbriz',
            port=3306
        )
        cursor = conn.cursor(dictionary=True)
        
        # Get the clinical manifest source text
        query = """
            SELECT source_type, source_text, extracted_observations, created_at
            FROM observation_store 
            WHERE patient_id = %s AND source_type = 'patient_report'
            ORDER BY created_at DESC
            LIMIT 1
        """
        cursor.execute(query, (10318,))
        result = cursor.fetchone()
        
        if not result:
            print("❌ No patient_report found for patient 10318")
            return
        
        source_text = result['source_text']
        print(f"✅ Found clinical manifest: {len(source_text)} characters")
        print(f"📋 Source text preview: {source_text[:200]}...")
        
        # Extract clinical observations from the source text
        clinical_observations = extract_clinical_observations(source_text)
        
        print(f"🎯 Extracted {len(clinical_observations)} clinical observations:")
        for obs in clinical_observations:
            print(f"  - {obs['observation']}: {obs['value']}")
        
        # Update the database with the new extracted observations
        for obs in clinical_observations:
            # Check if this observation already exists
            check_query = """
                SELECT id FROM observation_store 
                WHERE patient_id = %s AND source_type = 'patient_report' 
                AND extracted_observations LIKE %s
            """
            search_pattern = f'%"observation": "{obs["observation"]}"%'
            cursor.execute(check_query, (10318, search_pattern))
            existing = cursor.fetchone()
            
            if existing:
                print(f"  ⚠️  Observation '{obs['observation']}' already exists, skipping")
                continue
            
            # Insert new observation
            insert_query = """
                INSERT INTO observation_store 
                (patient_id, source_type, source_text, extracted_observations, created_at)
                VALUES (%s, %s, %s, %s, %s)
            """
            
            extracted_json = json.dumps(obs)
            cursor.execute(insert_query, (
                10318, 
                'patient_report', 
                source_text, 
                extracted_json,
                datetime.now()
            ))
            print(f"  ✅ Inserted: {obs['observation']}: {obs['value']}")
        
        conn.commit()
        print("✅ Clinical manifest reprocessing completed!")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

def extract_clinical_observations(source_text):
    """Extract clinical observations from source text"""
    observations = []
    
    # AHI patterns
    ahi_patterns = [
        r'AHI[:=\s]+([0-9]+(?:\.[0-9]+)?)',
        r'Apnea[- ]?Hypopnea Index[:=\s]+([0-9]+(?:\.[0-9]+)?)',
        r'([0-9]+(?:\.[0-9]+)?)\s*events?/hr?\b.*?AHI',
    ]
    
    # SpO2 patterns
    spo2_patterns = [
        r'SpO2\s*nadir[:=\s]+([0-9]+)',
        r'O2\s*Nadir[:=\s]+([0-9]+)',
        r'oxygen\s*desaturation\s*to\s*([0-9]+)',
        r'lowest\s*SpO2[:=\s]+([0-9]+)',
    ]
    
    # ODI patterns
    odi_patterns = [
        r'ODI[:=\s]+([0-9]+(?:\.[0-9]+)?)',
        r'Oxygen\s*Desaturation\s*Index[:=\s]+([0-9]+(?:\.[0-9]+)?)',
    ]
    
    import re
    
    # Extract AHI
    for pattern in ahi_patterns:
        match = re.search(pattern, source_text, re.IGNORECASE)
        if match:
            ahi_value = float(match.group(1))
            observations.append({
                'observation': 'AHI',
                'value': ahi_value,
                'evidence': f'Extracted from clinical manifest: {match.group(0)}',
                'confidence': 0.95,
                'document_name': 'Clinical Manifest',
                'document_type': 'patient_report',
                'extraction_date': datetime.now().isoformat()
            })
            break
    
    # Extract SpO2 nadir
    for pattern in spo2_patterns:
        match = re.search(pattern, source_text, re.IGNORECASE)
        if match:
            spo2_value = int(match.group(1))
            observations.append({
                'observation': 'SpO2_nadir',
                'value': spo2_value,
                'evidence': f'Extracted from clinical manifest: {match.group(0)}',
                'confidence': 0.95,
                'document_name': 'Clinical Manifest',
                'document_type': 'patient_report',
                'extraction_date': datetime.now().isoformat()
            })
            break
    
    # Extract ODI
    for pattern in odi_patterns:
        match = re.search(pattern, source_text, re.IGNORECASE)
        if match:
            odi_value = float(match.group(1))
            observations.append({
                'observation': 'ODI',
                'value': odi_value,
                'evidence': f'Extracted from clinical manifest: {match.group(0)}',
                'confidence': 0.95,
                'document_name': 'Clinical Manifest',
                'document_type': 'patient_report',
                'extraction_date': datetime.now().isoformat()
            })
            break
    
    # Extract other clinical findings
    clinical_findings = [
        ('TMJ', r'tmj|temporomandibular'),
        ('CPAP_intolerance', r'cpap.*intolerance|intolerance.*cpap'),
        ('nasal_obstruction', r'nasal.*obstruction|obstruction.*nasal'),
        ('nickel_allergy', r'nickel.*allergy|allergy.*nickel'),
        ('bruxism', r'bruxism|teeth.*grinding'),
        ('daytime_sleepiness', r'daytime.*sleepiness|sleepiness.*daytime'),
        ('nocturnal_urination', r'nocturnal.*urination|urination.*night'),
    ]
    
    for finding, pattern in clinical_findings:
        if re.search(pattern, source_text, re.IGNORECASE):
            observations.append({
                'observation': finding,
                'value': True,
                'evidence': f'Found in clinical manifest: {finding}',
                'confidence': 0.9,
                'document_name': 'Clinical Manifest',
                'document_type': 'patient_report',
                'extraction_date': datetime.now().isoformat()
            })
    
    return observations

if __name__ == "__main__":
    reprocess_clinical_manifest()
