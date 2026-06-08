#!/usr/bin/env python3
"""
Test the expanded schema-focused extractor with complete Patient Case JSON v1 coverage
"""

import json
from vizbriz.flask_app.config.textract_extractor_schema_focused import extract_for_observations_db

def test_expanded_extractor():
    """Test the expanded extractor on a single file to show new capabilities"""
    
    # Test file
    test_file = "/home/ec2-user/patient_data/Daniellle Kalfon/case_DaKa_1991.pdf"
    patient_id = "patient_12345"
    
    print(f"🧪 Testing expanded extractor on: {test_file}")
    print("="*60)
    
    try:
        # Extract using expanded schema
        result = extract_for_observations_db(test_file, patient_id=patient_id)
        
        print("✅ Expanded extraction completed!")
        
        # Show all sections found
        print(f"\n📊 Schema Sections Found:")
        sections = []
        if result.get('sleep_study'):
            sections.append(f"sleep_study ({len(result['sleep_study'])} fields)")
        if result.get('demographics'):
            sections.append(f"demographics ({len(result['demographics'])} fields)")
        if result.get('observations'):
            sections.append(f"observations ({len(result['observations'])} fields)")
        if result.get('patient_self_report'):
            sections.append(f"patient_self_report ({len(result['patient_self_report'])} fields)")
        if result.get('medical_history'):
            sections.append(f"medical_history ({len(result['medical_history'])} fields)")
        if result.get('prior_therapies'):
            sections.append(f"prior_therapies ({len(result['prior_therapies'])} fields)")
        if result.get('device_design'):
            sections.append(f"device_design ({len(result['device_design'])} fields)")
        if result.get('follow_up_plan'):
            sections.append(f"follow_up_plan ({len(result['follow_up_plan'])} fields)")
        if result.get('positional_metrics'):
            sections.append(f"positional_metrics ({len(result['positional_metrics'])} fields)")
        
        for section in sections:
            print(f"   • {section}")
        
        # Show detailed breakdown
        print(f"\n🔍 Detailed Field Breakdown:")
        
        if result.get('sleep_study'):
            print(f"   📋 Sleep Study: {list(result['sleep_study'].keys())}")
        
        if result.get('demographics'):
            print(f"   👤 Demographics: {list(result['demographics'].keys())}")
        
        if result.get('observations'):
            print(f"   🔬 Observations:")
            for obs_type, obs_data in result['observations'].items():
                print(f"      • {obs_type}: {list(obs_data.keys())}")
        
        if result.get('patient_self_report'):
            print(f"   📝 Patient Self Report:")
            for report_type, report_data in result['patient_self_report'].items():
                print(f"      • {report_type}: {list(report_data.keys())}")
        
        # Show validation results
        if result.get('validation'):
            validation = result['validation']
            if validation.get('warnings'):
                print(f"\n⚠️  Validation Warnings:")
                for warning in validation['warnings']:
                    print(f"   • {warning}")
            
            if validation.get('errors'):
                print(f"\n❌ Validation Errors:")
                for error in validation['errors']:
                    print(f"   • {error}")
        
        # Save results
        with open("expanded_extraction_result.json", "w") as f:
            json.dump(result, f, indent=2, default=str)
        
        print(f"\n💾 Results saved to: expanded_extraction_result.json")
        
        # Show schema compliance
        print(f"\n✅ Schema Compliance:")
        print(f"   • schema_version: {result.get('schema_version')}")
        print(f"   • document_type: {result.get('document_type')}")
        print(f"   • patient_id: {result.get('patient_id')}")
        print(f"   • Total fields extracted: {sum(len(section) if isinstance(section, dict) else 0 for section in result.values() if isinstance(section, dict))}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        print(f"🔍 Traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    test_expanded_extractor()
