#!/usr/bin/env python3
"""
Comprehensive validation script for Level 4 extraction and KB system
Tests the entire pipeline from database to case card generation
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask_app import create_app
from flask_app.models import L4DeviceDesign, L4DeviceOption
from flask_app.services.l4_case_card_generator import L4CaseCardGenerator
from flask_app.services.l4_kb_uploader import L4KBUploader
from flask_app.extensions import db

def validate_system():
    """Comprehensive validation of Level 4 system"""
    
    app = create_app()
    
    with app.app_context():
        print("="*80)
        print("LEVEL 4 SYSTEM VALIDATION")
        print("="*80)
        
        results = {
            "database": {},
            "case_card_generation": {},
            "kb_upload": {},
            "overall": "PASS"
        }
        
        # =====================================================================
        # 1. Database Validation
        # =====================================================================
        print("\n1. DATABASE VALIDATION")
        print("-" * 80)
        
        try:
            # Check tables
            inspector = db.inspect(db.engine)
            tables = inspector.get_table_names()
            
            has_design_table = 'l4_device_design' in tables
            has_options_table = 'l4_device_options' in tables
            
            print(f"  ✓ l4_device_design table: {'EXISTS' if has_design_table else 'MISSING'}")
            print(f"  ✓ l4_device_options table: {'EXISTS' if has_options_table else 'MISSING'}")
            
            if not has_design_table or not has_options_table:
                results["overall"] = "FAIL"
                results["database"]["error"] = "Tables missing"
                return results
            
            # Check data
            design_count = L4DeviceDesign.query.count()
            option_count = L4DeviceOption.query.count()
            
            print(f"\n  Data counts:")
            print(f"    Device Designs: {design_count}")
            print(f"    Device Options: {option_count}")
            
            if design_count == 0:
                print("\n  ⚠ WARNING: No device designs in database")
                print("     Process reports first: python scripts/process_l4_reports.py")
                results["database"]["warning"] = "No data"
            else:
                print("  ✓ Data exists")
            
            results["database"]["design_count"] = design_count
            results["database"]["option_count"] = option_count
            
            # Check sample record
            if design_count > 0:
                sample_design = L4DeviceDesign.query.first()
                print(f"\n  Sample record:")
                print(f"    ID: {sample_design.id}")
                print(f"    Report: {sample_design.source_report_id}")
                print(f"    Patient ID: {sample_design.patient_id}")
                print(f"    Context: {sample_design.design_context}")
                print(f"    Mandibular Advancement: {sample_design.mandibular_advancement}")
                print(f"    Material: {sample_design.material}")
                
                # Check for clinical context
                has_clinical = hasattr(sample_design, 'ahi') and sample_design.ahi is not None
                print(f"    Has Clinical Context: {'YES' if has_clinical else 'NO (optional)'}")
                
                results["database"]["sample_record"] = {
                    "id": sample_design.id,
                    "report": sample_design.source_report_id,
                    "has_clinical_context": has_clinical
                }
            
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            results["database"]["error"] = str(e)
            results["overall"] = "FAIL"
            return results
        
        # =====================================================================
        # 2. Case Card Generation Validation
        # =====================================================================
        print("\n2. CASE CARD GENERATION VALIDATION")
        print("-" * 80)
        
        try:
            if design_count == 0:
                print("  ⚠ SKIPPED: No data to generate case cards from")
                results["case_card_generation"]["skipped"] = True
            else:
                generator = L4CaseCardGenerator()
                sample_design = L4DeviceDesign.query.first()
                sample_options = sample_design.device_options.all()
                
                # Generate case card
                case_card = generator.generate_case_card(
                    device_design=sample_design,
                    device_options=sample_options
                )
                
                print("  ✓ Case card generated successfully")
                print(f"\n  Case card structure:")
                print(f"    Patient ID: {case_card.get('patient_id')} (anonymized)")
                print(f"    Age: {case_card.get('age', 'N/A')}")
                print(f"    Sex: {case_card.get('sex', 'N/A')}")
                print(f"    AHI: {case_card.get('diagnosis', {}).get('ahi', 'N/A')}")
                print(f"    Design Context: {case_card.get('device_design', {}).get('design_context', 'N/A')}")
                print(f"    Device Options: {len(case_card.get('device_options', []))}")
                print(f"    Clustering Features: {len(case_card.get('clustering_features', {}))}")
                
                # Validate anonymization
                patient_id = case_card.get('patient_id', '')
                if 'YS' in patient_id or '1982' in patient_id:
                    print("  ⚠ WARNING: Patient ID may not be fully anonymized")
                else:
                    print("  ✓ Patient ID appears anonymized")
                
                # Check clustering features
                clustering = case_card.get('clustering_features', {})
                if clustering:
                    print(f"\n  Clustering features:")
                    for key, value in clustering.items():
                        if value:
                            print(f"    {key}: {value}")
                
                # Generate text format
                text_card = generator.generate_case_card_text(case_card)
                print(f"\n  ✓ Text format: {len(text_card)} characters")
                
                # Generate JSON format
                json_card = generator.generate_case_card_json(case_card)
                print(f"  ✓ JSON format: {len(json_card)} characters")
                
                # Validate JSON
                try:
                    parsed = json.loads(json_card)
                    print("  ✓ JSON is valid")
                except:
                    print("  ❌ ERROR: JSON is invalid")
                    results["overall"] = "FAIL"
                
                results["case_card_generation"]["success"] = True
                results["case_card_generation"]["sample_card"] = {
                    "patient_id": case_card.get('patient_id'),
                    "has_age": case_card.get('age') is not None,
                    "has_sex": case_card.get('sex') is not None,
                    "device_options_count": len(case_card.get('device_options', [])),
                    "clustering_features_count": len(clustering)
                }
                
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            results["case_card_generation"]["error"] = str(e)
            results["overall"] = "FAIL"
        
        # =====================================================================
        # 3. KB Upload Validation
        # =====================================================================
        print("\n3. KB UPLOAD VALIDATION")
        print("-" * 80)
        
        try:
            import os
            s3_bucket = os.getenv('S3_BUCKET_NAME')
            
            if not s3_bucket:
                print("  ⚠ S3_BUCKET_NAME not configured")
                print("     Upload will save locally only")
                results["kb_upload"]["s3_configured"] = False
            else:
                print(f"  ✓ S3 bucket configured: {s3_bucket}")
                results["kb_upload"]["s3_configured"] = True
                results["kb_upload"]["s3_bucket"] = s3_bucket
            
            # Test uploader initialization
            uploader = L4KBUploader()
            print("  ✓ KB uploader initialized")
            
            # Test local save (without S3)
            if design_count > 0:
                test_output = "/tmp/l4_validation_test"
                Path(test_output).mkdir(parents=True, exist_ok=True)
                
                sample_design = L4DeviceDesign.query.first()
                saved_path = uploader.save_case_card_locally(
                    device_design=sample_design,
                    output_dir=test_output,
                    format="json"
                )
                
                if saved_path and Path(saved_path).exists():
                    print(f"  ✓ Local save test: {saved_path}")
                    file_size = Path(saved_path).stat().st_size
                    print(f"    File size: {file_size} bytes")
                    
                    # Show preview
                    with open(saved_path, 'r') as f:
                        preview = json.load(f)
                        print(f"    Preview: {preview.get('patient_id')} - {preview.get('diagnosis', {}).get('ahi', 'N/A')}")
                    
                    results["kb_upload"]["local_save_success"] = True
                    results["kb_upload"]["test_file"] = saved_path
                else:
                    print("  ❌ ERROR: Local save failed")
                    results["kb_upload"]["local_save_success"] = False
                    results["overall"] = "FAIL"
            
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            results["kb_upload"]["error"] = str(e)
            results["overall"] = "FAIL"
        
        # =====================================================================
        # 4. Summary
        # =====================================================================
        print("\n" + "="*80)
        print("VALIDATION SUMMARY")
        print("="*80)
        
        if results["overall"] == "PASS":
            print("\n✅ SYSTEM IS WORKING!")
            print("\nNext steps:")
            print("  1. Review test case card: /tmp/l4_validation_test/")
            print("  2. Upload to KB: python scripts/upload_l4_to_kb.py")
            print("  3. Or test locally: python scripts/upload_l4_to_kb.py --no-s3 --output-dir /tmp/case-cards")
        else:
            print("\n❌ VALIDATION FAILED")
            print("\nIssues found:")
            if results["database"].get("error"):
                print(f"  - Database: {results['database']['error']}")
            if results["case_card_generation"].get("error"):
                print(f"  - Case Card: {results['case_card_generation']['error']}")
            if results["kb_upload"].get("error"):
                print(f"  - KB Upload: {results['kb_upload']['error']}")
        
        print("\n" + "="*80)
        
        # Save results
        results_file = "/tmp/l4_validation_results.json"
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\nFull results saved to: {results_file}")
        
        return results

if __name__ == "__main__":
    validate_system()
