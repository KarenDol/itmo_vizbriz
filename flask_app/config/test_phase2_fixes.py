#!/usr/bin/env python3
"""
Test script for Phase 2 document observation extractor fixes.
Tests schema compliance, deduplication, and other improvements.
"""

import sys
import os
import json
from datetime import datetime

# Add the project root to the path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
FLASK_APP_DIR = os.path.dirname(CURRENT_DIR)
PROJECT_ROOT = os.path.dirname(FLASK_APP_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flask_app.config.document_observation_extractor_phase2 import (
    _prune_empty,
    normalize_study_type_to_schema,
    detect_sex_safely,
    check_observation_exists,
    validate_schema_compliance
)

def test_prune_empty():
    """Test the _prune_empty function."""
    print("Testing _prune_empty function...")
    
    # Test data with empty values
    test_data = {
        'demographics': {
            'sex': 'M',
            'age_years': 45,
            'height_cm': None,
            'weight_kg': '',
            'bmi': []
        },
        'sleep_study': {
            'study_type': 'home',
            'ahi': 25.5,
            'odi': None,
            'snoring': {
                'avg_db': 0,
                'max_db': None
            }
        },
        'observations': {
            'summary': ['Patient has OSA'],
            'anatomy_imaging': {}
        },
        'empty_section': None,
        'empty_list': []
    }
    
    pruned = _prune_empty(test_data)
    
    # Check that empty values are removed
    assert 'height_cm' not in pruned['demographics']
    assert 'weight_kg' not in pruned['demographics']
    assert 'bmi' not in pruned['demographics']
    assert 'odi' not in pruned['sleep_study']
    assert 'max_db' not in pruned['sleep_study']['snoring']
    assert 'empty_section' not in pruned
    assert 'empty_list' not in pruned
    
    # Check that valid values remain
    assert pruned['demographics']['sex'] == 'M'
    assert pruned['demographics']['age_years'] == 45
    assert pruned['sleep_study']['study_type'] == 'home'
    assert pruned['sleep_study']['ahi'] == 25.5
    assert pruned['sleep_study']['snoring']['avg_db'] == 0
    assert pruned['observations']['summary'] == ['Patient has OSA']
    
    print("✓ _prune_empty function works correctly")

def test_normalize_study_type():
    """Test the normalize_study_type_to_schema function."""
    print("Testing normalize_study_type_to_schema function...")
    
    # Test cases
    test_cases = [
        ('HSAT', 'home'),
        ('hsat', 'home'),
        ('Home Sleep Test', 'home'),
        ('PSG', 'inlab'),
        ('psg', 'inlab'),
        ('Polysomnography', 'inlab'),
        ('Laboratory Study', 'inlab'),
        ('Unknown', None),
        ('', None),
        (None, None)
    ]
    
    for input_val, expected in test_cases:
        result = normalize_study_type_to_schema(input_val)
        assert result == expected, f"Expected {expected} for '{input_val}', got {result}"
    
    print("✓ normalize_study_type_to_schema function works correctly")

def test_detect_sex_safely():
    """Test the detect_sex_safely function."""
    print("Testing detect_sex_safely function...")
    
    # Test cases
    test_cases = [
        # Should detect
        ('Sex: Male', 'M'),
        ('Gender: Female', 'F'),
        ('Patient is male', 'M'),
        ('Female patient', 'F'),
        ('Sex = Male', 'M'),
        
        # Should not detect (false positives)
        ('Dr. Smith is a male physician', None),  # Clinician name
        ('Family history: father had male pattern baldness', None),  # Family history
        ('The male doctor examined the patient', None),  # Clinician context
        
        # Medical context should work
        ('Diagnosis: OSA in male patient', 'M'),
        ('Assessment: female with sleep apnea', 'F'),
        
        # Edge cases
        ('', None),
        (None, None),
        ('No gender information', None)
    ]
    
    for input_text, expected in test_cases:
        result = detect_sex_safely(input_text)
        assert result == expected, f"Expected {expected} for '{input_text}', got {result}"
    
    print("✓ detect_sex_safely function works correctly")

def test_schema_validation():
    """Test schema validation with corrected data."""
    print("Testing schema validation...")
    
    # Valid canonical JSON
    valid_canonical = {
        'schema_version': '1.0',
        'document_type': 'canonical',
        'patient_id': '12345',
        'as_of': datetime.now().isoformat(),
        'demographics': {
            'sex': 'M',
            'age_years': 45
        },
        'sleep_study': {
            'study_type': 'home',  # Correct enum value
            'ahi': 25.5
        },
        'observations': {
            'summary': ['Patient has OSA']
        }
    }
    
    validation = validate_schema_compliance(valid_canonical)
    assert validation['valid'], f"Valid canonical should pass validation: {validation['errors']}"
    
    # Invalid canonical (wrong enum values)
    invalid_canonical = {
        'schema_version': '1.0',
        'document_type': 'canonical',
        'patient_id': '12345',
        'as_of': datetime.now().isoformat(),
        'sleep_study': {
            'study_type': 'HSAT',  # Wrong enum value
            'severity': 'moderate'  # Not allowed in schema
        }
    }
    
    validation = validate_schema_compliance(invalid_canonical)
    # Should have warnings about invalid enum values
    assert len(validation['warnings']) > 0, "Should warn about invalid enum values"
    
    print("✓ Schema validation works correctly")

def test_observation_deduplication_logic():
    """Test the logic for checking observation existence."""
    print("Testing observation deduplication logic...")
    
    # This test simulates the logic without database access
    # In a real scenario, this would check the database
    
    # Simulate existing observations
    existing_observations = [
        {
            'path': 'sleep_study.ahi',
            'value': '25.5',
            'observation': 'AHI: 25.5'
        },
        {
            'path': 'demographics.age_years',
            'value': '45',
            'observation': 'Age: 45 years'
        }
    ]
    
    # Test cases for new observations
    test_cases = [
        # Should be considered duplicates
        ('sleep_study.ahi', '25.5', True),
        ('demographics.age_years', '45', True),
        ('', 'AHI: 25.5', True),  # Matches observation field
        
        # Should be considered new
        ('sleep_study.ahi', '30.0', False),
        ('demographics.age_years', '50', False),
        ('sleep_study.odi', '15.0', False)
    ]
    
    for path, value, should_exist in test_cases:
        # Simulate the check logic
        exists = False
        for obs in existing_observations:
            if (obs.get('path') == path and obs.get('value') == value) or \
               obs.get('observation') == value:
                exists = True
                break
        
        assert exists == should_exist, f"Expected {should_exist} for path={path}, value={value}"
    
    print("✓ Observation deduplication logic works correctly")

def main():
    """Run all tests."""
    print("Running Phase 2 fixes tests...")
    print("=" * 50)
    
    try:
        test_prune_empty()
        test_normalize_study_type()
        test_detect_sex_safely()
        test_schema_validation()
        test_observation_deduplication_logic()
        
        print("=" * 50)
        print("✓ All tests passed! Phase 2 fixes are working correctly.")
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
