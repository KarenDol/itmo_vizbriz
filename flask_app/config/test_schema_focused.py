#!/usr/bin/env python3
"""
Test the schema-focused extractor
"""

import json
from vizbriz.flask_app.config.textract_extractor_schema_focused import extract_file

def test_schema_focused():
    """Test the schema-focused extractor"""
    
    # Test file that had good data
    test_file = "/home/ec2-user/patient_data/Daniellle Kalfon/RentgenView.pdf"
    
    print(f"🧪 Testing schema-focused extractor on: {test_file}")
    print("="*60)
    
    try:
        # Test the schema-focused extractor
        result = extract_file(test_file, report_id="schema_test")
        
        # Print results
        print("✅ Schema-focused extraction completed!")
        print("\n📊 Schema-compliant results:")
        print(json.dumps(result, indent=2, default=str))
        
        # Analyze what we found vs schema
        print("\n🔍 Analysis:")
        sleep_study = result.get('sleep_study', {})
        demographics = result.get('demographics', {})
        
        print(f"📋 Sleep Study Fields Found: {len(sleep_study)}")
        for key, value in sleep_study.items():
            print(f"   • {key}: {value}")
        
        print(f"👤 Demographics Fields Found: {len(demographics)}")
        for key, value in demographics.items():
            print(f"   • {key}: {value}")
        
        # Check schema compliance
        print(f"\n✅ Schema Compliance:")
        print(f"   • schema_version: {result.get('schema_version')}")
        print(f"   • document_type: {result.get('document_type')}")
        print(f"   • as_of: {result.get('as_of')}")
        
        # Save results
        with open("schema_focused_result.json", "w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\n💾 Results saved to: schema_focused_result.json")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        print(f"🔍 Traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    test_schema_focused()
