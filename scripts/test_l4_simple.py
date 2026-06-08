#!/usr/bin/env python3
"""
Simple test script for Level 4 extraction - tests document processing only
Doesn't require full Flask app setup
"""

import sys
import json
from pathlib import Path

# Add services to path
sys.path.insert(0, str(Path(__file__).parent.parent / "flask_app" / "services"))
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_document_processing(docx_path: str):
    """Test document processing without Flask app"""
    
    print("="*80)
    print("TESTING LEVEL 4 DOCUMENT PROCESSING")
    print("="*80)
    print(f"\nProcessing: {docx_path}\n")
    
    try:
        from l4_document_processor import L4DocumentProcessor
        
        processor = L4DocumentProcessor()
        result = processor.process_document(docx_path)
        
        print("✓ Document Processing Successful!\n")
        print(f"Filename: {result['filename']}")
        print(f"Patient ID: {result['patient_id']}")
        print(f"\nSections found: {len(result['sections'])}")
        
        for section_name, section_text in result['sections'].items():
            print(f"\n--- {section_name} ---")
            print(f"Length: {len(section_text)} characters")
            print(f"Preview (first 400 chars):")
            print(section_text[:400])
            print("...")
        
        # Check for device design sections
        design_sections = [k for k in result['sections'].keys() if 'Design Data' in k or 'Design' in k]
        options_sections = [k for k in result['sections'].keys() if 'Options' in k or 'Appliance' in k]
        
        print("\n" + "="*80)
        print("SECTION ANALYSIS")
        print("="*80)
        print(f"Device Design Sections: {len(design_sections)}")
        for sec in design_sections:
            print(f"  - {sec}")
        
        print(f"\nDevice Options Sections: {len(options_sections)}")
        for sec in options_sections:
            print(f"  - {sec}")
        
        # Show full device design section if found
        if design_sections:
            print("\n" + "="*80)
            print("FULL DEVICE DESIGN SECTION")
            print("="*80)
            full_design = result['sections'][design_sections[0]]
            print(full_design)
        
        # Show device options section if found
        if options_sections:
            print("\n" + "="*80)
            print("FULL DEVICE OPTIONS SECTION")
            print("="*80)
            full_options = result['sections'][options_sections[0]]
            print(full_options)
        
        return result
        
    except ImportError as e:
        print(f"✗ Import Error: {e}")
        print("\nTrying to install python-docx...")
        import subprocess
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "python-docx", "-q"])
            print("✓ python-docx installed, retrying...")
            return test_document_processing(docx_path)
        except:
            print("✗ Could not install python-docx automatically")
            print("Please install manually: pip install python-docx")
            return None
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    if len(sys.argv) > 1:
        docx_path = sys.argv[1]
    else:
        docx_path = "/home/ec2-user/patient_data/Report Examples/Level 4 Structure/Example 1 (case YS 1982).docx"
    
    test_document_processing(docx_path)
