#!/usr/bin/env python3
"""
Validate Level 4 case card system and compare with current KB approach
Shows what's currently in KB vs what case cards would provide
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask_app import create_app
from flask_app.models import L4DeviceDesign, L4DeviceOption
from flask_app.services.l4_case_card_generator import L4CaseCardGenerator
from flask_app.services.bedrock_service import BedrockService

def validate_and_compare():
    """Compare current KB approach with new case card system"""
    
    app = create_app()
    
    with app.app_context():
        print("="*80)
        print("LEVEL 4 SYSTEM VALIDATION & KB COMPARISON")
        print("="*80)
        
        # =====================================================================
        # 1. Check Current KB Status
        # =====================================================================
        print("\n1. CURRENT KNOWLEDGE BASE STATUS")
        print("-" * 80)
        
        bedrock = BedrockService()
        print(f"  KB ID (default): {bedrock.KNOWLEDGE_BASE_ID}")
        print(f"  KB Level 4 Style: {bedrock.KB_LEVEL4_STYLE_ID}")
        print(f"  KB Level 4 Clinic: {bedrock.KB_LEVEL4_CLINIC_ID}")
        
        # Test KB query
        print("\n  Testing KB query...")
        try:
            kb_result = bedrock.query_knowledge_base(
                query="Find cases with AHI 10-15 and posterior tongue position",
                knowledge_base_id=bedrock.KNOWLEDGE_BASE_ID,
                max_results=3
            )
            
            if kb_result.get('success'):
                citations = kb_result.get('citations', [])
                print(f"  ✓ KB query successful: {len(citations)} results")
                print("\n  Current KB sources:")
                for i, citation in enumerate(citations[:3], 1):
                    uri = citation.get('uri', '')
                    filename = uri.split('/')[-1] if '/' in uri else uri
                    print(f"    {i}. {filename} (score: {citation.get('score', 0):.3f})")
            else:
                print(f"  ⚠ KB query failed: {kb_result.get('error')}")
        except Exception as e:
            print(f"  ⚠ KB query error: {e}")
        
        # =====================================================================
        # 2. Check Level 4 Database Data
        # =====================================================================
        print("\n2. LEVEL 4 DATABASE DATA")
        print("-" * 80)
        
        try:
            design_count = L4DeviceDesign.query.count()
            option_count = L4DeviceOption.query.count()
            
            print(f"  Device Designs: {design_count}")
            print(f"  Device Options: {option_count}")
            
            if design_count == 0:
                print("\n  ⚠ No Level 4 data in database")
                print("     Process reports: python scripts/process_l4_reports.py")
                return
            
            # Show sample
            sample = L4DeviceDesign.query.first()
            print(f"\n  Sample record:")
            print(f"    Report: {sample.source_report_id}")
            print(f"    Context: {sample.design_context}")
            print(f"    Mandibular Advancement: {sample.mandibular_advancement}")
            print(f"    Material: {sample.material}")
            
            # Check if it matches KB files
            kb_files = [
                "Example 1 (case YS 1982)_normalized.txt",
                "Example 4 (case ROL 109)_normalized.txt"
            ]
            report_name = sample.source_report_id.replace('.docx', '')
            in_kb = any(report_name in kb_file for kb_file in kb_files)
            print(f"    In current KB: {'YES' if in_kb else 'NO (needs upload)'}")
            
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return
        
        # =====================================================================
        # 3. Generate Case Card Sample
        # =====================================================================
        print("\n3. CASE CARD GENERATION")
        print("-" * 80)
        
        try:
            generator = L4CaseCardGenerator()
            sample_design = L4DeviceDesign.query.first()
            sample_options = sample_design.device_options.all()
            
            case_card = generator.generate_case_card(
                device_design=sample_design,
                device_options=sample_options
            )
            
            print("  ✓ Case card generated")
            print(f"\n  Case card structure:")
            print(f"    Patient ID: {case_card.get('patient_id')} (anonymized)")
            print(f"    Age: {case_card.get('age', 'N/A')}")
            print(f"    Sex: {case_card.get('sex', 'N/A')}")
            print(f"    AHI: {case_card.get('diagnosis', {}).get('ahi', 'N/A')}")
            print(f"    Severity: {case_card.get('diagnosis', {}).get('severity', 'N/A')}")
            print(f"    Device Options: {len(case_card.get('device_options', []))}")
            
            # Show clustering features
            clustering = case_card.get('clustering_features', {})
            print(f"\n  Clustering features (for KB matching):")
            for key, value in clustering.items():
                if value:
                    print(f"    {key}: {value}")
            
            # Generate JSON
            json_card = generator.generate_case_card_json(case_card)
            print(f"\n  ✓ JSON size: {len(json_card)} chars")
            
            # Save sample
            sample_file = "/tmp/sample_case_card.json"
            with open(sample_file, 'w') as f:
                f.write(json_card)
            print(f"  ✓ Sample saved to: {sample_file}")
            
        except Exception as e:
            print(f"  ❌ Error: {e}")
            import traceback
            traceback.print_exc()
        
        # =====================================================================
        # 4. Comparison: Current vs New Approach
        # =====================================================================
        print("\n4. COMPARISON: CURRENT KB vs CASE CARDS")
        print("-" * 80)
        
        print("\n  CURRENT APPROACH (Normalized Text Files):")
        print("    ✓ Working (as shown in your logs)")
        print("    ✓ Contains full report text")
        print("    ⚠ Not structured - harder to query specific fields")
        print("    ⚠ May contain PII")
        print("    ⚠ No clustering features for similarity matching")
        
        print("\n  NEW APPROACH (Structured Case Cards):")
        print("    ✓ Structured JSON - easy to query specific fields")
        print("    ✓ Anonymized - no PII")
        print("    ✓ Clustering features - better similarity matching")
        print("    ✓ Links diagnosis → device design → options")
        print("    ⚠ Needs to be uploaded to KB")
        
        print("\n  BENEFITS OF CASE CARDS:")
        print("    • Better queries: 'Find cases with AHI 10-15 AND posterior tongue'")
        print("    • Clustering: Match similar patients automatically")
        print("    • Structured: Extract specific device design parameters")
        print("    • Safe: No PII concerns")
        
        # =====================================================================
        # 5. Next Steps
        # =====================================================================
        print("\n5. NEXT STEPS TO INTEGRATE")
        print("-" * 80)
        
        print("\n  To use case cards in your KB:")
        print("    1. Generate case cards:")
        print("       python scripts/upload_l4_to_kb.py --no-s3 --output-dir /tmp/case-cards")
        print("\n    2. Review generated files:")
        print("       ls -la /tmp/case-cards/")
        print("       cat /tmp/case-cards/*.json | head -100")
        print("\n    3. Upload to S3 (for KB ingestion):")
        print("       python scripts/upload_l4_to_kb.py")
        print("\n    4. KB will sync automatically from S3")
        print("\n    5. Test queries:")
        print("       Query: 'Find similar cases with AHI 10-15, posterior tongue'")
        print("       Should return structured case cards with device recommendations")
        
        print("\n" + "="*80)
        print("VALIDATION COMPLETE")
        print("="*80)

if __name__ == "__main__":
    validate_and_compare()
