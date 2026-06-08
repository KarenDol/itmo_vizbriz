#!/usr/bin/env python3
"""
Test the enhanced extractor to see if it catches more sleep study data
"""

import json
from textract_extractor_enhanced import extract_file

def test_enhanced_extractor():
    """Test the enhanced extractor on files that had missing data"""
    
    # Test files that had missing data
    test_files = [
        "/home/ec2-user/patient_data/Daniellle Kalfon/RentgenView.pdf",
        "/home/ec2-user/patient_data/Daniellle Kalfon/case_DaKa_1991.pdf",
        "/home/ec2-user/patient_data/Daniellle Kalfon/Case_DaKa_1991_vizbriz.pdf"
    ]
    
    for i, test_file in enumerate(test_files, 1):
        print(f"\n{'='*60}")
        print(f"🧪 ENHANCED TEST {i}: {test_file.split('/')[-1]}")
        print(f"{'='*60}")
        
        try:
            # Test the enhanced extractor
            result = extract_file(test_file, report_id=f"enhanced_test_{i}")
            
            # Print results
            print(f"✅ Enhanced extraction completed!")
            print(f"📊 Sleep study data found:")
            
            sleep_study = result.get('sleep_study', {})
            if sleep_study:
                for key, value in sleep_study.items():
                    if isinstance(value, dict):
                        print(f"   • {key}:")
                        for subkey, subvalue in value.items():
                            print(f"     - {subkey}: {subvalue}")
                    else:
                        print(f"   • {key}: {value}")
            else:
                print("   • No sleep study data found")
            
            # Print provenance summary
            provenance = result.get('provenance', [])
            print(f"📋 Provenance: {len(provenance)} items")
            
            # Save detailed results
            output_file = f"enhanced_test_result_{i}.json"
            with open(output_file, 'w') as f:
                json.dump(result, f, indent=2, default=str)
            print(f"💾 Results saved to: {output_file}")
            
        except Exception as e:
            print(f"❌ Error processing {test_file}: {e}")
            import traceback
            print(f"🔍 Traceback: {traceback.format_exc()}")
    
    print(f"\n{'='*60}")
    print("🎉 Enhanced testing completed!")
    print(f"{'='*60}")

if __name__ == "__main__":
    test_enhanced_extractor()
