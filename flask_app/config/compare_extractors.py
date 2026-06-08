#!/usr/bin/env python3
"""
Compare LLM-powered extraction vs Schema-focused extraction
to identify gaps and opportunities for improvement.
"""

import os
import json
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime

# Add the project root to Python path for imports
sys.path.append('/home/ec2-user/vizbriz')

def run_llm_extraction(file_path: str) -> Dict[str, Any]:
    """Run LLM-powered extraction using osaagent_routes.py"""
    try:
        from flask_app.routes.osaagent_routes import extract_observations_from_text, extract_text_from_file
        
        print(f"🤖 Running LLM extraction on: {os.path.basename(file_path)}")
        
        # Extract text using the robust method
        extracted_text = extract_text_from_file(file_path)
        
        if not extracted_text or len(extracted_text.strip()) < 50:
            print("❌ No meaningful text for LLM extraction")
            return {"success": False, "error": "No meaningful text"}
        
        # Extract observations using LLM
        result = extract_observations_from_text(extracted_text, 1)  # datasource_id 1 for sleep test
        
        if result.get('success'):
            observations = result.get('data', {}).get('datasource_observations', [])
            print(f"✅ LLM found {len(observations)} observations")
            return {
                "success": True,
                "observations": observations,
                "text_length": len(extracted_text),
                "extracted_text": extracted_text[:500] + "..." if len(extracted_text) > 500 else extracted_text
            }
        else:
            print(f"❌ LLM extraction failed: {result.get('error', 'Unknown error')}")
            return {"success": False, "error": result.get('error', 'Unknown error')}
            
    except ImportError:
        print("⚠️  LLM extraction skipped - flask_app not available")
        return {"success": False, "error": "flask_app not available"}
    except Exception as e:
        print(f"❌ LLM extraction error: {str(e)}")
        return {"success": False, "error": str(e)}

def run_schema_extraction(file_path: str) -> Dict[str, Any]:
    """Run schema-focused extraction"""
    try:
        from vizbriz.flask_app.config.textract_extractor_schema_focused import extract_for_observations_db
        
        print(f"📋 Running schema extraction on: {os.path.basename(file_path)}")
        
        # Extract using our schema-focused method
        result = extract_for_observations_db(file_path, "test_patient", "test_report")
        
        if result and result.get('sleep_study') or result.get('demographics'):
            fields_found = 0
            if result.get('sleep_study'):
                fields_found += len([k for k, v in result['sleep_study'].items() if v is not None])
            if result.get('demographics'):
                fields_found += len([k for k, v in result['demographics'].items() if v is not None])
            if result.get('observations'):
                fields_found += len([k for k, v in result['observations'].items() if v is not None])
            if result.get('device_design'):
                fields_found += len([k for k, v in result['device_design'].items() if v is not None])
                
            print(f"✅ Schema extractor found {fields_found} fields")
            return {
                "success": True,
                "fields_found": fields_found,
                "result": result
            }
        else:
            print("❌ Schema extractor found 0 fields")
            return {"success": False, "fields_found": 0, "result": result}
            
    except Exception as e:
        print(f"❌ Schema extraction error: {str(e)}")
        return {"success": False, "error": str(e)}

def analyze_gaps(llm_result: Dict[str, Any], schema_result: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze gaps between LLM and schema extraction"""
    gaps = {
        "llm_only_observations": [],
        "schema_only_fields": [],
        "overlap": [],
        "missed_opportunities": []
    }
    
    # Extract LLM observations
    llm_observations = set()
    if llm_result.get('success') and llm_result.get('observations'):
        for obs in llm_result['observations']:
            obs_name = obs.get('observation', '').lower()
            obs_value = obs.get('value', '')
            llm_observations.add(f"{obs_name}: {obs_value}")
    
    # Extract schema fields
    schema_fields = set()
    if schema_result.get('success') and schema_result.get('result'):
        result = schema_result['result']
        
        # Sleep study fields
        if result.get('sleep_study'):
            for field, value in result['sleep_study'].items():
                if value is not None:
                    schema_fields.add(f"sleep_study.{field}: {value}")
        
        # Demographics fields
        if result.get('demographics'):
            for field, value in result['demographics'].items():
                if value is not None:
                    schema_fields.add(f"demographics.{field}: {value}")
        
        # Observations fields
        if result.get('observations'):
            for field, value in result['observations'].items():
                if value is not None:
                    schema_fields.add(f"observations.{field}: {value}")
        
        # Device design fields
        if result.get('device_design'):
            for field, value in result['device_design'].items():
                if value is not None:
                    schema_fields.add(f"device_design.{field}: {value}")
    
    # Find gaps
    gaps["llm_only_observations"] = list(llm_observations - schema_fields)
    gaps["schema_only_fields"] = list(schema_fields - llm_observations)
    gaps["overlap"] = list(llm_observations & schema_fields)
    
    return gaps

def main():
    """Main comparison function"""
    print("🔍 Comparing LLM vs Schema Extraction")
    print("=" * 50)
    
    # Test directory
    test_dir = "/home/ec2-user/patient_data/Daniellle Kalfon"
    
    if not os.path.exists(test_dir):
        print(f"❌ Test directory not found: {test_dir}")
        return
    
    # Find PDF files
    pdf_files = list(Path(test_dir).glob("*.pdf"))
    print(f"📁 Found {len(pdf_files)} PDF files to compare")
    
    comparison_results = []
    
    for i, pdf_file in enumerate(pdf_files, 1):
        print(f"\n📄 Comparing file {i}/{len(pdf_files)}: {pdf_file.name}")
        print("-" * 40)
        
        # Run both extractors
        llm_result = run_llm_extraction(str(pdf_file))
        schema_result = run_schema_extraction(str(pdf_file))
        
        # Analyze gaps
        gaps = analyze_gaps(llm_result, schema_result)
        
        # Store results
        file_result = {
            "filename": pdf_file.name,
            "llm_result": llm_result,
            "schema_result": schema_result,
            "gaps": gaps,
            "summary": {
                "llm_success": llm_result.get('success', False),
                "schema_success": schema_result.get('success', False),
                "llm_observations": len(llm_result.get('observations', [])),
                "schema_fields": schema_result.get('fields_found', 0),
                "llm_only_count": len(gaps["llm_only_observations"]),
                "schema_only_count": len(gaps["schema_only_fields"]),
                "overlap_count": len(gaps["overlap"])
            }
        }
        
        comparison_results.append(file_result)
        
        # Print summary for this file
        print(f"📊 Summary for {pdf_file.name}:")
        print(f"   • LLM: {file_result['summary']['llm_observations']} observations")
        print(f"   • Schema: {file_result['summary']['schema_fields']} fields")
        print(f"   • LLM only: {file_result['summary']['llm_only_count']} observations")
        print(f"   • Schema only: {file_result['summary']['schema_only_count']} fields")
        print(f"   • Overlap: {file_result['summary']['overlap_count']} fields")
        
        # Show some LLM-only observations
        if gaps["llm_only_observations"]:
            print(f"   • LLM-only examples: {gaps['llm_only_observations'][:3]}")
    
    # Overall analysis
    print(f"\n📈 OVERALL ANALYSIS")
    print("=" * 50)
    
    total_llm_observations = sum(r['summary']['llm_observations'] for r in comparison_results)
    total_schema_fields = sum(r['summary']['schema_fields'] for r in comparison_results)
    total_llm_only = sum(r['summary']['llm_only_count'] for r in comparison_results)
    total_schema_only = sum(r['summary']['schema_only_count'] for r in comparison_results)
    total_overlap = sum(r['summary']['overlap_count'] for r in comparison_results)
    
    print(f"📊 Totals across all files:")
    print(f"   • LLM observations: {total_llm_observations}")
    print(f"   • Schema fields: {total_schema_fields}")
    print(f"   • LLM only: {total_llm_only}")
    print(f"   • Schema only: {total_schema_only}")
    print(f"   • Overlap: {total_overlap}")
    
    # Find most common LLM-only observations
    all_llm_only = []
    for result in comparison_results:
        all_llm_only.extend(result['gaps']['llm_only_observations'])
    
    if all_llm_only:
        print(f"\n🎯 Most common LLM-only observations:")
        from collections import Counter
        counter = Counter(all_llm_only)
        for obs, count in counter.most_common(10):
            print(f"   • {obs} (found in {count} files)")
    
    # Save detailed results
    output_file = f"extractor_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump(comparison_results, f, indent=2, default=str)
    
    print(f"\n💾 Detailed results saved to: {output_file}")
    
    # Recommendations
    print(f"\n💡 RECOMMENDATIONS")
    print("=" * 50)
    
    if total_llm_only > 0:
        print(f"✅ LLM found {total_llm_only} observations that schema extractor missed")
        print("   • Consider adding these patterns to schema extractor")
        print("   • Or integrate LLM extraction as fallback")
    
    if total_schema_only > 0:
        print(f"✅ Schema extractor found {total_schema_only} fields that LLM missed")
        print("   • Schema extractor is good at structured data")
    
    if total_overlap > 0:
        print(f"✅ {total_overlap} fields found by both methods")
        print("   • Good validation of extraction accuracy")
    
    print(f"\n🚀 Next steps:")
    print("   1. Review LLM-only observations for new regex patterns")
    print("   2. Consider hybrid approach: schema first, LLM as fallback")
    print("   3. Add LLM confidence scores to schema extractor")

if __name__ == "__main__":
    main()
