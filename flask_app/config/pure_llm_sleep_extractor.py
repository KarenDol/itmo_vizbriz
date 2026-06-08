#!/usr/bin/env python3
"""
Pure LLM Sleep Study Extractor
Extracts ALL sleep study metrics using ONLY LLM - no regex patterns

This mimics the experience of prompting Claude directly with a document.
The LLM handles all the parsing, pattern recognition, and data extraction.

Usage:
  python -m flask_app.config.pure_llm_sleep_extractor --patient-id 25793
  python -m flask_app.config.pure_llm_sleep_extractor --test-document path/to/document.pdf
"""

import argparse
import json
import sys
import mysql.connector
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from flask_app.config.document_observation_extractor_phase2 import (
    DB_CONFIG, extract_document_content
)
from flask_app.config.bedrock_config import query_bedrock_claude_enhanced
from flask_app.config.extract_o2_lt90_only import normalize_text


@dataclass
class SleepStudyData:
    """Complete sleep study dataset"""
    # Core respiratory metrics
    ahi: Optional[float] = None
    rdi: Optional[float] = None
    odi: Optional[float] = None
    
    # Oxygen saturation metrics
    o2_nadir_pct: Optional[float] = None
    o2_average_pct: Optional[float] = None
    time_below_90_pct: Optional[float] = None
    time_below_85_pct: Optional[float] = None
    time_below_80_pct: Optional[float] = None
    
    # Positional and sleep stage metrics
    supine_ahi: Optional[float] = None
    rem_ahi: Optional[float] = None
    nrem_ahi: Optional[float] = None
    supine_odi: Optional[float] = None
    rem_odi: Optional[float] = None
    
    # Sleep architecture
    total_sleep_time_hours: Optional[float] = None
    sleep_efficiency_pct: Optional[float] = None
    rem_sleep_pct: Optional[float] = None
    deep_sleep_pct: Optional[float] = None
    light_sleep_pct: Optional[float] = None
    sleep_onset_latency_min: Optional[float] = None
    rem_latency_min: Optional[float] = None
    
    # Other metrics
    arousal_index: Optional[float] = None
    limb_movement_index: Optional[float] = None
    snoring_pct: Optional[float] = None
    snoring_db_max: Optional[float] = None
    heart_rate_avg: Optional[float] = None
    heart_rate_min: Optional[float] = None
    heart_rate_max: Optional[float] = None
    
    # Study metadata
    study_date: Optional[str] = None
    study_type: Optional[str] = None
    recording_duration_hours: Optional[float] = None


def extract_sleep_data_pure_llm(document_text: str, document_name: str = "document") -> Dict[str, Any]:
    """
    Extract ALL sleep study data using pure LLM - exactly like prompting Claude directly
    """
    
    # Clean the text first
    normalized_text = normalize_text(document_text)
    
    system_prompt = """You are an expert sleep medicine physician analyzing a sleep study report. 
    
    Your task is to extract ALL numerical sleep study metrics from the provided document with the same precision you would use in clinical practice.
    
    IMPORTANT INSTRUCTIONS:
    1. Extract EXACT numerical values as they appear in the document
    2. Do NOT make assumptions or calculations
    3. If a metric has multiple values (e.g., "AHI 15.2 overall, 25.8 supine"), extract all of them
    4. Pay careful attention to units (percentages, events/hour, minutes, etc.)
    5. Return 'null' for any metric not found in the document
    6. Be thorough - look for metrics that might be described in various ways
    
    Return a JSON object with this EXACT structure:
    {
        "ahi": number or null,
        "rdi": number or null,
        "odi": number or null,
        "o2_nadir_pct": number or null,
        "o2_average_pct": number or null,
        "time_below_90_pct": number or null,
        "time_below_85_pct": number or null,
        "time_below_80_pct": number or null,
        "supine_ahi": number or null,
        "rem_ahi": number or null,
        "nrem_ahi": number or null,
        "supine_odi": number or null,
        "rem_odi": number or null,
        "total_sleep_time_hours": number or null,
        "sleep_efficiency_pct": number or null,
        "rem_sleep_pct": number or null,
        "deep_sleep_pct": number or null,
        "light_sleep_pct": number or null,
        "sleep_onset_latency_min": number or null,
        "rem_latency_min": number or null,
        "arousal_index": number or null,
        "limb_movement_index": number or null,
        "snoring_pct": number or null,
        "snoring_db_max": number or null,
        "heart_rate_avg": number or null,
        "heart_rate_min": number or null,
        "heart_rate_max": number or null,
        "study_date": "string or null",
        "study_type": "string or null",
        "recording_duration_hours": number or null
    }
    
    METRIC DEFINITIONS to help you identify them:
    - AHI: Apnea-Hypopnea Index (events per hour)
    - RDI: Respiratory Disturbance Index (events per hour)
    - ODI: Oxygen Desaturation Index (events per hour)
    - O2 Nadir: Lowest oxygen saturation level (percentage)
    - Time below 90%: Percent of sleep time with SpO2 < 90%
    - Sleep Efficiency: Percent of time in bed actually sleeping
    - REM/Deep/Light Sleep: Percentages of total sleep time
    - Sleep/REM Latency: Time to fall asleep / enter REM (minutes)
    
    Return ONLY the JSON object - no additional text."""
    
    user_prompt = f"""Analyze this sleep study document and extract all metrics:

DOCUMENT: {normalized_text[:12000]}

Return the JSON object with all extracted sleep study metrics."""
    
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        print(f"🤖 Sending document to LLM for analysis...")
        response = query_bedrock_claude_enhanced(
            messages=messages, 
            max_tokens=2000, 
            temperature=0.1, 
            top_p=0.9
        )
        
        if isinstance(response, dict) and response.get("success"):
            raw_response = response.get("response", "")
            print(f"✅ LLM responded successfully")
            
            try:
                # Parse the JSON response
                extracted_data = json.loads(raw_response)
                print(f"✅ Successfully parsed JSON response")
                
                # Count extracted metrics
                non_null_count = sum(1 for v in extracted_data.values() if v is not None)
                print(f"📊 Extracted {non_null_count} metrics from document")
                
                return {
                    "success": True,
                    "extracted_data": extracted_data,
                    "raw_llm_response": raw_response,
                    "document_name": document_name,
                    "metrics_count": non_null_count
                }
                
            except json.JSONDecodeError as e:
                print(f"❌ Failed to parse LLM JSON response: {str(e)}")
                print(f"Raw response: {raw_response[:500]}...")
                return {
                    "success": False,
                    "error": f"JSON parsing failed: {str(e)}",
                    "raw_llm_response": raw_response,
                    "document_name": document_name
                }
        else:
            error_msg = "LLM query failed"
            if isinstance(response, dict):
                error_msg = response.get("error", error_msg)
            print(f"❌ LLM query failed: {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "document_name": document_name
            }
            
    except Exception as e:
        print(f"❌ Exception during LLM extraction: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "document_name": document_name
        }


def get_patient_documents_simple(patient_id: int) -> List[Dict[str, Any]]:
    """Get patient documents from database"""
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        cursor = connection.cursor(dictionary=True)
        
        # Get patient documents from the database
        query = """
        SELECT DISTINCT file_name, s3_key, file_type 
        FROM observation_store 
        WHERE patient_id = %s AND s3_key IS NOT NULL
        """
        cursor.execute(query, (patient_id,))
        
        documents = []
        for row in cursor.fetchall():
            documents.append({
                'name': row['file_name'],
                's3_key': row['s3_key'], 
                'file_type': row['file_type']
            })
        
        cursor.close()
        connection.close()
        
        return documents
        
    except Exception as e:
        print(f"Error getting patient documents: {e}")
        return []


def process_patient_documents(patient_id: int) -> Dict[str, Any]:
    """Process all documents for a patient using pure LLM extraction"""
    
    print(f"\n🔍 Processing all documents for Patient {patient_id} with pure LLM extraction")
    
    try:
        # Get patient documents
        documents = get_patient_documents_simple(patient_id)
        
        if not documents:
            return {
                "patient_id": patient_id,
                "success": False,
                "error": "No documents found for patient"
            }
        
        print(f"📁 Found {len(documents)} documents for patient {patient_id}")
        
        results = {
            "patient_id": patient_id,
            "total_documents": len(documents),
            "document_results": [],
            "consolidated_metrics": {},
            "success": True
        }
        
        # Process each document
        for i, doc in enumerate(documents, 1):
            print(f"\n📄 Processing document {i}/{len(documents)}: {doc.get('name', 'unknown')}")
            
            # Extract document content
            content = extract_document_content(doc)
            
            if not content:
                print(f"⚠️  Could not extract content from {doc.get('name', 'unknown')}")
                continue
            
            print(f"📝 Extracted {len(content)} characters of content")
            
            # Run pure LLM extraction
            extraction_result = extract_sleep_data_pure_llm(
                content, 
                doc.get('name', f'document_{i}')
            )
            
            results["document_results"].append(extraction_result)
            
            # If successful, show key metrics
            if extraction_result.get("success"):
                extracted = extraction_result.get("extracted_data", {})
                key_metrics = ["ahi", "odi", "o2_nadir_pct", "sleep_efficiency_pct"]
                found_metrics = {k: v for k, v in extracted.items() if k in key_metrics and v is not None}
                if found_metrics:
                    print(f"🎯 Key metrics found: {found_metrics}")
        
        # Consolidate metrics from all documents
        all_metrics = {}
        for doc_result in results["document_results"]:
            if doc_result.get("success"):
                doc_metrics = doc_result.get("extracted_data", {})
                for key, value in doc_metrics.items():
                    if value is not None:
                        if key not in all_metrics:
                            all_metrics[key] = []
                        all_metrics[key].append({
                            "value": value,
                            "document": doc_result.get("document_name")
                        })
        
        results["consolidated_metrics"] = all_metrics
        
        return results
        
    except Exception as e:
        return {
            "patient_id": patient_id,
            "success": False,
            "error": str(e)
        }


def test_single_document(file_path: str) -> Dict[str, Any]:
    """Test pure LLM extraction on a single document"""
    
    print(f"\n📄 Testing pure LLM extraction on: {file_path}")
    
    try:
        # Create a mock document object
        document = {
            'name': file_path,
            's3_key': None,  # Local file
            'file_type': 'application/pdf' if file_path.endswith('.pdf') else 'text/plain'
        }
        
        # For local files, read content directly
        if file_path.endswith('.txt'):
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        else:
            # Use the document extractor for PDFs, etc.
            content = extract_document_content(document)
        
        if not content:
            return {
                "success": False,
                "error": f"Could not extract content from {file_path}"
            }
        
        print(f"📝 Extracted {len(content)} characters from document")
        
        # Run pure LLM extraction
        result = extract_sleep_data_pure_llm(content, file_path)
        
        return result
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "document_name": file_path
        }


def print_results(results: Dict[str, Any]):
    """Pretty print the extraction results"""
    
    print(f"\n" + "="*80)
    print(f"🤖 PURE LLM SLEEP STUDY EXTRACTION RESULTS")
    print(f"="*80)
    
    if "patient_id" in results:
        print(f"\n👤 Patient ID: {results['patient_id']}")
        
        if not results.get("success"):
            print(f"❌ Error: {results.get('error')}")
            return
        
        print(f"📁 Total documents processed: {results.get('total_documents', 0)}")
        
        # Show document-by-document results
        for doc_result in results.get("document_results", []):
            print(f"\n📄 Document: {doc_result.get('document_name')}")
            if doc_result.get("success"):
                count = doc_result.get("metrics_count", 0)
                print(f"   ✅ Success - extracted {count} metrics")
                
                # Show extracted data
                extracted = doc_result.get("extracted_data", {})
                for key, value in extracted.items():
                    if value is not None:
                        print(f"      • {key}: {value}")
            else:
                print(f"   ❌ Failed: {doc_result.get('error')}")
        
        # Show consolidated view
        consolidated = results.get("consolidated_metrics", {})
        if consolidated:
            print(f"\n📊 CONSOLIDATED METRICS ACROSS ALL DOCUMENTS:")
            for metric, values in consolidated.items():
                print(f"   {metric}:")
                for item in values:
                    print(f"      • {item['value']} (from {item['document']})")
    
    else:
        # Single document result
        print(f"\n📄 Document: {results.get('document_name')}")
        
        if results.get("success"):
            count = results.get("metrics_count", 0)
            print(f"✅ Success - extracted {count} metrics")
            
            extracted = results.get("extracted_data", {})
            for key, value in extracted.items():
                if value is not None:
                    print(f"   • {key}: {value}")
        else:
            print(f"❌ Failed: {results.get('error')}")


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Pure LLM Sleep Study Extractor")
    parser.add_argument("--patient-id", type=int, help="Extract data for specific patient")
    parser.add_argument("--test-document", type=str, help="Test extraction on a single document file")
    
    args = parser.parse_args()
    
    if args.patient_id:
        results = process_patient_documents(args.patient_id)
        print_results(results)
        
    elif args.test_document:
        results = test_single_document(args.test_document)
        print_results(results)
        
    else:
        print("🤖 Pure LLM Sleep Study Extractor")
        print("Extract sleep study metrics using ONLY LLM - no regex patterns")
        print("\nUsage:")
        print("  python -m flask_app.config.pure_llm_sleep_extractor --patient-id 25793")
        print("  python -m flask_app.config.pure_llm_sleep_extractor --test-document /path/to/study.pdf")
        sys.exit(1)


if __name__ == "__main__":
    main()
