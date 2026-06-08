#!/usr/bin/env python3
"""
Check what data was saved to the Level 4 tables
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask_app import create_app
from flask_app.models import L4DeviceDesign, L4DeviceOption

def check_data():
    """Check what's in the database"""
    
    app = create_app()
    
    with app.app_context():
        print("="*80)
        print("Level 4 Device Design Data in Database")
        print("="*80)
        
        # Get all device designs
        designs = L4DeviceDesign.query.all()
        print(f"\nTotal Device Designs: {len(designs)}")
        
        for design in designs:
            print("\n" + "-"*80)
            print(f"Design ID: {design.id}")
            print(f"Source Report: {design.source_report_id}")
            print(f"Patient ID: {design.patient_id}")
            print(f"Design Context: {design.design_context}")
            print(f"Mandibular Advancement: {design.mandibular_advancement}")
            print(f"Preset (mm): {design.preset_mm}")
            print(f"Vertical Opening: {design.vertical_opening}")
            print(f"Anterior Window: {design.anterior_window}")
            print(f"Material: {design.material}")
            print(f"Confidence: {design.extraction_confidence}")
            
            # Get associated options
            options = L4DeviceOption.query.filter_by(
                source_report_id=design.source_report_id,
                design_context=design.design_context
            ).all()
            
            print(f"\n  Associated Device Options ({len(options)}):")
            for option in options:
                print(f"    - {option.device_name}")
                if option.key_features:
                    print(f"      Features: {option.key_features[:80]}...")
        
        # Get all options
        all_options = L4DeviceOption.query.all()
        print("\n" + "="*80)
        print(f"Total Device Options: {len(all_options)}")
        print("="*80)
        
        # Summary by report
        from collections import defaultdict
        by_report = defaultdict(lambda: {'designs': 0, 'options': 0})
        
        for design in designs:
            by_report[design.source_report_id]['designs'] += 1
        
        for option in all_options:
            by_report[option.source_report_id]['options'] += 1
        
        print("\nSummary by Report:")
        for report_id, counts in by_report.items():
            print(f"  {report_id}:")
            print(f"    Designs: {counts['designs']}")
            print(f"    Options: {counts['options']}")

if __name__ == "__main__":
    check_data()
