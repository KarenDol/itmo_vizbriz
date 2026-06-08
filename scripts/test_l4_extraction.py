#!/usr/bin/env python3
"""
Test script for Level 4 extraction with a single file
"""

import sys
import json
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from flask_app import create_app
from flask_app.services.l4_document_processor import L4DocumentProcessor
from flask_app.services.l4_extraction_service import L4ExtractionService
from flask_app.services.l4_validation_service import L4ValidationService

def test_single_file(docx_path: str):
    """Test processing a single file"""
    
    app = create_app()
    
    with app.app_context():
        print("="*80)
        print("TESTING LEVEL 4 EXTRACTION")
        print("="*80)
        print(f"\nProcessing: {docx_path}\n")
        
        # Step 1: Document Processing
        print("Step 1: Document Processing...")
        processor = L4DocumentProcessor()
        try:
            processed = processor.process_document(docx_path)
            print(f"  ✓ Filename: {processed['filename']}")
            print(f"  ✓ Patient ID: {processed['patient_id']}")
            print(f"  ✓ Sections found: {len(processed['sections'])}")
            for section_name in processed['sections'].keys():
                print(f"    - {section_name}")
            
            # Show preview of device design section
            design_sections = [k for k in processed['sections'].keys() if 'Design Data' in k]
            if design_sections:
                print(f"\n  Device Design Section Preview (first 300 chars):")
                print(f"  {processed['sections'][design_sections[0]][:300]}...")
            
        except Exception as e:
            print(f"  ✗ Error: {e}")
            import traceback
            traceback.print_exc()
            return
        
        # Step 2: Extraction (if LLM is available)
        print("\nStep 2: LLM Extraction...")
        extraction_service = L4ExtractionService()
        try:
            extraction = extraction_service.extract_device_data(
                sections=processed["sections"],
                patient_id=processed["patient_id"],
                filename=processed["filename"]
            )
            print(f"  ✓ Designs found: {len(extraction.get('l4_device_design', []))}")
            print(f"  ✓ Options found: {len(extraction.get('l4_device_options', []))}")
            
            # Show extracted data
            if extraction.get('l4_device_design'):
                print("\n  Extracted Device Design:")
                for i, design in enumerate(extraction['l4_device_design'], 1):
                    print(f"\n  Design {i}:")
                    print(f"    Context: {design.get('design_context')}")
                    print(f"    Mandibular Advancement: {design.get('mandibular_advancement')}")
                    print(f"    Preset (mm): {design.get('preset_mm')}")
                    print(f"    Vertical Opening: {design.get('vertical_opening')}")
                    print(f"    Anterior Window: {design.get('anterior_window')}")
                    print(f"    Material: {design.get('material')}")
                    print(f"    Confidence: {design.get('extraction_confidence')}")
            
            if extraction.get('l4_device_options'):
                print("\n  Extracted Device Options:")
                for i, option in enumerate(extraction['l4_device_options'], 1):
                    print(f"    {i}. {option.get('device_name')} - {option.get('key_features', '')[:50]}")
            
        except Exception as e:
            print(f"  ✗ Error: {e}")
            import traceback
            traceback.print_exc()
            return
        
        # Step 3: Normalization
        print("\nStep 3: Normalization...")
        try:
            normalized = extraction_service.normalize_extraction(extraction)
            print("  ✓ Normalization complete")
            
            # Show normalized values
            if normalized.get('l4_device_design'):
                for design in normalized['l4_device_design']:
                    if design.get('anterior_window'):
                        print(f"    Anterior Window normalized: {design.get('anterior_window')}")
                    if design.get('preset_mm'):
                        print(f"    Preset (mm) extracted: {design.get('preset_mm')}")
        except Exception as e:
            print(f"  ✗ Error: {e}")
            import traceback
            traceback.print_exc()
            return
        
        # Step 4: Validation
        print("\nStep 4: Validation...")
        validation_service = L4ValidationService()
        try:
            is_valid, error_msg = validation_service.validate_extraction(normalized)
            if is_valid:
                print("  ✓ Validation passed")
            else:
                print(f"  ✗ Validation failed: {error_msg}")
        except Exception as e:
            print(f"  ✗ Error: {e}")
            import traceback
            traceback.print_exc()
            return
        
        # Summary
        print("\n" + "="*80)
        print("EXTRACTION SUMMARY")
        print("="*80)
        print(json.dumps({
            "filename": processed['filename'],
            "patient_id": processed['patient_id'],
            "designs": len(normalized.get('l4_device_design', [])),
            "options": len(normalized.get('l4_device_options', [])),
            "valid": is_valid
        }, indent=2))
        
        # Show full extraction JSON
        print("\n" + "="*80)
        print("FULL EXTRACTION JSON")
        print("="*80)
        print(json.dumps(normalized, indent=2))

if __name__ == "__main__":
    if len(sys.argv) > 1:
        docx_path = sys.argv[1]
    else:
        # Default to Example 1
        docx_path = "/home/ec2-user/patient_data/Report Examples/Level 4 Structure/Example 1 (case YS 1982).docx"
    
    test_single_file(docx_path)
