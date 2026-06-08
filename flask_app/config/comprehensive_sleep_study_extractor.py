#!/usr/bin/env python3
"""
Comprehensive Sleep Study Metrics Extractor
Tests LLM extraction for ALL sleep study metrics, not just O2 < 90%

Features:
- Tests all major sleep study parameters
- Multiple LLM prompting strategies
- Regex pattern validation
- Data quality assessment
- Comprehensive reporting

Usage:
  python -m flask_app.config.comprehensive_sleep_study_extractor --patient-id 25793
  python -m flask_app.config.comprehensive_sleep_study_extractor --test-all-metrics
"""

import argparse
import json
import re
import sys
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

from flask_app.config.document_observation_extractor_phase2 import (
    DB_CONFIG, extract_document_content
)
from flask_app.config.bedrock_config import query_bedrock_claude_enhanced as bedrock_query_enhanced
from flask_app.config.extract_o2_lt90_only import normalize_text


@dataclass
class SleepStudyMetrics:
    """Complete set of sleep study metrics for testing"""
    # Primary metrics
    ahi: Optional[float] = None
    rdi: Optional[float] = None
    odi: Optional[float] = None
    
    # Oxygen metrics
    o2_nadir_pct: Optional[float] = None
    time_below_90_pct: Optional[float] = None
    time_below_85_pct: Optional[float] = None
    time_below_80_pct: Optional[float] = None
    average_spo2: Optional[float] = None
    
    # Sleep architecture
    total_sleep_time: Optional[float] = None
    sleep_efficiency_pct: Optional[float] = None
    rem_pct: Optional[float] = None
    deep_sleep_pct: Optional[float] = None
    sleep_onset_latency: Optional[float] = None
    rem_latency: Optional[float] = None
    
    # Positional metrics
    supine_ahi: Optional[float] = None
    rem_ahi: Optional[float] = None
    nrem_ahi: Optional[float] = None
    
    # Other metrics
    arousal_index: Optional[float] = None
    limb_movement_index: Optional[float] = None
    snoring_pct: Optional[float] = None
    heart_rate_avg: Optional[float] = None
    heart_rate_min: Optional[float] = None
    heart_rate_max: Optional[float] = None


@dataclass
class ExtractionResult:
    """Results from one extraction attempt"""
    method: str
    metrics: SleepStudyMetrics
    raw_response: str
    success: bool
    error: Optional[str] = None


def comprehensive_llm_extraction(text: str) -> ExtractionResult:
    """Use LLM to extract all sleep study metrics comprehensively"""
    
    system_prompt = """You are a medical data extraction specialist analyzing sleep study reports. 
    Extract ALL numerical sleep study metrics from the provided document.
    
    CRITICAL REQUIREMENTS:
    - Return ONLY a valid JSON object
    - Use null for metrics not found
    - Extract exact numerical values (no ranges, no approximations)
    - Pay attention to units and percentages
    - Validate ranges (AHI 0-100, ODI 0-100, O2 Nadir 70-100%, Sleep Efficiency 70-95%)
    
    JSON Structure:
    {
        "ahi": number or null,
        "rdi": number or null,
        "odi": number or null,
        "o2_nadir_pct": number or null,
        "time_below_90_pct": number or null,
        "time_below_85_pct": number or null,
        "time_below_80_pct": number or null,
        "average_spo2": number or null,
        "total_sleep_time": number or null,
        "sleep_efficiency_pct": number or null,
        "rem_pct": number or null,
        "deep_sleep_pct": number or null,
        "sleep_onset_latency": number or null,
        "rem_latency": number or null,
        "supine_ahi": number or null,
        "rem_ahi": number or null,
        "nrem_ahi": number or null,
        "arousal_index": number or null,
        "limb_movement_index": number or null,
        "snoring_pct": number or null,
        "heart_rate_avg": number or null,
        "heart_rate_min": number or null,
        "heart_rate_max": number or null
    }"""
    
    user_prompt = f"""Extract all sleep study metrics from this document:

{text[:8000]}

Return ONLY the JSON object with extracted values."""
    
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        response = bedrock_query_enhanced(messages, max_tokens=1000, temperature=0.1, top_p=0.9)
        
        if isinstance(response, dict) and response.get("success"):
            raw_response = response.get("response", "")
            try:
                # Parse JSON response
                metrics_data = json.loads(raw_response)
                metrics = SleepStudyMetrics(**{k: v for k, v in metrics_data.items() if hasattr(SleepStudyMetrics, k)})
                
                return ExtractionResult(
                    method="Comprehensive LLM",
                    metrics=metrics,
                    raw_response=raw_response,
                    success=True
                )
            except json.JSONDecodeError as e:
                return ExtractionResult(
                    method="Comprehensive LLM",
                    metrics=SleepStudyMetrics(),
                    raw_response=raw_response,
                    success=False,
                    error=f"JSON parsing failed: {str(e)}"
                )
        else:
            return ExtractionResult(
                method="Comprehensive LLM",
                metrics=SleepStudyMetrics(),
                raw_response="",
                success=False,
                error="LLM query failed"
            )
            
    except Exception as e:
        return ExtractionResult(
            method="Comprehensive LLM",
            metrics=SleepStudyMetrics(),
            raw_response="",
            success=False,
            error=str(e)
        )


def targeted_llm_extraction(text: str, metric_name: str, metric_key: str) -> Optional[float]:
    """Extract a specific metric using targeted LLM prompting"""
    
    system_prompt = f"""You extract a single numerical metric from sleep study documents.
    Return ONLY the number (no text, no units), representing {metric_name}.
    If not found, return 'null'."""
    
    user_prompt = f"""Extract {metric_name} from this document:

{text[:4000]}

Return only the numerical value."""
    
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        response = bedrock_query_enhanced(messages, max_tokens=64, temperature=0.0, top_p=0.9)
        
        if isinstance(response, dict) and response.get("success"):
            raw_response = (response.get("response", "")).strip()
            
            if raw_response.lower() == 'null':
                return None
                
            # Extract number from response
            match = re.search(r'(\d+(?:\.\d+)?)', raw_response)
            if match:
                return float(match.group(1))
                
        return None
        
    except Exception:
        return None


def regex_pattern_extraction(text: str) -> SleepStudyMetrics:
    """Extract metrics using regex patterns"""
    
    patterns = {
        'ahi': [
            (r'AHI\s+(\d+(?:\.\d+)?)', 'AHI X'),
            (r'apnea[^h]*hypopnea\s+index[^\n\r\d]{0,30}(\d+(?:\.\d+)?)', 'apnea hypopnea index X'),
        ],
        'rdi': [
            (r'RDI\s+(\d+(?:\.\d+)?)', 'RDI X'),
            (r'respiratory\s+disturbance\s+index[^\n\r\d]{0,30}(\d+(?:\.\d+)?)', 'respiratory disturbance index X'),
        ],
        'odi': [
            (r'ODI\s+(\d+(?:\.\d+)?)', 'ODI X'),
            (r'oxygen\s+desaturation\s+index[^\n\r\d]{0,30}(\d+(?:\.\d+)?)', 'oxygen desaturation index X'),
        ],
        'o2_nadir_pct': [
            (r'O2\s+Nadir\s+(\d+)\s*%?', 'O2 Nadir X%'),
            (r'oxygen\s+nadir[^\n\r\d]{0,30}(\d+)\s*%?', 'oxygen nadir X%'),
            (r'lowest\s+oxygen[^\n\r\d]{0,30}(\d+)\s*%?', 'lowest oxygen X%'),
        ],
        'time_below_90_pct': [
            (r'Less than 90%\s+O2\s+(\d+(?:\.\d+)?)\s*%?', 'Less than 90% O2 X%'),
            (r'time\s*<\s*90%[^\n\r\d]{0,40}(\d+(?:\.\d+)?)\s*%?', 'time < 90% X%'),
            (r'SpO2\s*<\s*90%[^\n\r\d]{0,40}(\d+(?:\.\d+)?)\s*%?', 'SpO2 < 90% X%'),
            (r'(\d+(?:\.\d+)?)\s*%\s+time\s+below\s+90%', 'X% time below 90%'),
        ],
        'supine_ahi': [
            (r'Supine\s+AHI\s+(\d+(?:\.\d+)?)', 'Supine AHI X'),
            (r'supine[^\n\r\d]{0,30}(\d+(?:\.\d+)?)', 'supine ... X'),
        ],
        'rem_ahi': [
            (r'REM\s+AHI\s+(\d+(?:\.\d+)?)', 'REM AHI X'),
            (r'REM[^\n\r\d]{0,30}(\d+(?:\.\d+)?)', 'REM ... X'),
        ],
        'sleep_efficiency_pct': [
            (r'sleep\s+efficiency[^\n\r\d]{0,30}(\d+(?:\.\d+)?)\s*%?', 'sleep efficiency X%'),
            (r'efficiency[^\n\r\d]{0,30}(\d+(?:\.\d+)?)\s*%?', 'efficiency X%'),
        ],
        'total_sleep_time': [
            (r'total\s+sleep\s+time[^\n\r\d]{0,30}(\d+(?:\.\d+)?)', 'total sleep time X'),
            (r'TST[^\n\r\d]{0,30}(\d+(?:\.\d+)?)', 'TST X'),
        ]
    }
    
    metrics = SleepStudyMetrics()
    
    for metric_key, pattern_list in patterns.items():
        for pattern, description in pattern_list:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                try:
                    value = float(matches[0])
                    setattr(metrics, metric_key, value)
                    break  # Use first match
                except (ValueError, TypeError):
                    continue
    
    return metrics


def validate_metrics(metrics: SleepStudyMetrics) -> Dict[str, List[str]]:
    """Validate extracted metrics for clinical plausibility"""
    warnings = []
    errors = []
    
    # AHI validation
    if metrics.ahi is not None:
        if metrics.ahi < 0 or metrics.ahi > 200:
            errors.append(f"AHI {metrics.ahi} outside valid range (0-200)")
        elif metrics.ahi > 100:
            warnings.append(f"AHI {metrics.ahi} is very high (>100)")
    
    # ODI validation
    if metrics.odi is not None:
        if metrics.odi < 0 or metrics.odi > 200:
            errors.append(f"ODI {metrics.odi} outside valid range (0-200)")
    
    # O2 Nadir validation
    if metrics.o2_nadir_pct is not None:
        if metrics.o2_nadir_pct < 50 or metrics.o2_nadir_pct > 100:
            errors.append(f"O2 Nadir {metrics.o2_nadir_pct}% outside valid range (50-100%)")
        elif metrics.o2_nadir_pct < 70:
            warnings.append(f"O2 Nadir {metrics.o2_nadir_pct}% is critically low (<70%)")
    
    # Sleep Efficiency validation
    if metrics.sleep_efficiency_pct is not None:
        if metrics.sleep_efficiency_pct < 30 or metrics.sleep_efficiency_pct > 100:
            errors.append(f"Sleep Efficiency {metrics.sleep_efficiency_pct}% outside valid range (30-100%)")
        elif metrics.sleep_efficiency_pct < 70:
            warnings.append(f"Sleep Efficiency {metrics.sleep_efficiency_pct}% is very low (<70%)")
    
    # Time below 90% validation
    if metrics.time_below_90_pct is not None:
        if metrics.time_below_90_pct < 0 or metrics.time_below_90_pct > 100:
            errors.append(f"Time below 90% {metrics.time_below_90_pct}% outside valid range (0-100%)")
    
    return {"warnings": warnings, "errors": errors}


def test_patient_extraction(patient_id: int) -> Dict[str, Any]:
    """Test comprehensive extraction on a specific patient"""
    
    print(f"\n🔍 Testing comprehensive sleep study extraction for Patient {patient_id}")
    
    # Get patient documents (simplified - you'd implement full document retrieval)
    # For now, return test structure
    
    results = {
        "patient_id": patient_id,
        "total_documents": 0,
        "extraction_results": [],
        "summary": {}
    }
    
    # This would be expanded to actually load patient documents
    print(f"⚠️  Patient document loading not implemented - this is a framework")
    
    return results


def test_sample_document() -> Dict[str, Any]:
    """Test extraction on a sample sleep study document"""
    
    sample_text = """
    Sleep Study Report - Patient Case Study
    
    Patient Demographics:
    Age: 45 years, Male, BMI: 28.5
    Height: 5'10", Weight: 185 lbs
    
    Sleep Study Results:
    Study Date: March 15, 2024
    Total Sleep Time: 420 minutes (7.0 hours)
    Sleep Efficiency: 85.2%
    Sleep Onset Latency: 12 minutes
    REM Latency: 95 minutes
    
    Respiratory Events:
    AHI: 28.2 events/hour (Moderate OSA)
    RDI: 32.8 events/hour
    Supine AHI: 42.5 events/hour (62% of sleep time)
    REM AHI: 30.1 events/hour (27.5% of sleep time)
    ODI: 16.1 events/hour
    Supine ODI: 25.6 events/hour
    REM ODI: 19.7 events/hour
    
    Oxygen Saturation:
    O2 Nadir: 83%
    Average SpO2: 94.2%
    Time with SpO2 < 90%: 0.5% of sleep time
    Time with SpO2 < 85%: 0.1% of sleep time
    
    Sleep Architecture:
    REM Sleep: 18.5% of total sleep time
    Deep Sleep (N3): 12.8% of total sleep time
    Light Sleep (N1+N2): 68.7% of total sleep time
    
    Other Metrics:
    Arousal Index: 22.3 events/hour
    Snoring: 56% of sleep time, >50dB = 13.2%
    Heart Rate: Average 68 bpm (range 52-89 bpm)
    Limb Movement Index: 8.2 events/hour
    """
    
    normalized_text = normalize_text(sample_text)
    
    print(f"\n📋 Testing extraction methods on sample document...")
    
    # Test comprehensive LLM extraction
    print(f"   🤖 Testing Comprehensive LLM extraction...")
    llm_result = comprehensive_llm_extraction(normalized_text)
    
    # Test regex pattern extraction
    print(f"   🔍 Testing Regex pattern extraction...")
    regex_metrics = regex_pattern_extraction(normalized_text)
    regex_result = ExtractionResult(
        method="Regex Patterns",
        metrics=regex_metrics,
        raw_response="",
        success=True
    )
    
    # Test targeted LLM for specific metrics
    print(f"   🎯 Testing Targeted LLM extraction...")
    targeted_metrics = SleepStudyMetrics()
    targeted_metrics.ahi = targeted_llm_extraction(normalized_text, "AHI (Apnea Hypopnea Index)", "ahi")
    targeted_metrics.o2_nadir_pct = targeted_llm_extraction(normalized_text, "O2 Nadir percentage", "o2_nadir_pct")
    targeted_metrics.time_below_90_pct = targeted_llm_extraction(normalized_text, "time with SpO2 below 90%", "time_below_90_pct")
    
    targeted_result = ExtractionResult(
        method="Targeted LLM",
        metrics=targeted_metrics,
        raw_response="",
        success=True
    )
    
    results = {
        "sample_document": True,
        "normalized_text": normalized_text[:500] + "...",
        "extraction_results": [llm_result, regex_result, targeted_result],
        "validation_results": {}
    }
    
    # Validate each result
    for result in results["extraction_results"]:
        validation = validate_metrics(result.metrics)
        results["validation_results"][result.method] = validation
    
    return results


def print_extraction_results(results: Dict[str, Any]):
    """Pretty print extraction results"""
    
    print(f"\n" + "="*80)
    print(f"📊 COMPREHENSIVE SLEEP STUDY EXTRACTION RESULTS")
    print(f"="*80)
    
    if results.get("sample_document"):
        print(f"📋 Sample Document Analysis")
    else:
        print(f"👤 Patient {results.get('patient_id', 'Unknown')} Analysis")
    
    for result in results.get("extraction_results", []):
        print(f"\n🔬 Method: {result.method}")
        print(f"   ✅ Success: {result.success}")
        if result.error:
            print(f"   ❌ Error: {result.error}")
        
        # Print extracted metrics
        metrics_dict = asdict(result.metrics)
        extracted_count = sum(1 for v in metrics_dict.values() if v is not None)
        print(f"   📈 Extracted {extracted_count} metrics:")
        
        for key, value in metrics_dict.items():
            if value is not None:
                print(f"      • {key}: {value}")
        
        # Print validation results
        validation = results.get("validation_results", {}).get(result.method, {})
        if validation.get("warnings"):
            print(f"   ⚠️  Warnings: {len(validation['warnings'])}")
            for warning in validation["warnings"]:
                print(f"      - {warning}")
        if validation.get("errors"):
            print(f"   🚨 Errors: {len(validation['errors'])}")
            for error in validation["errors"]:
                print(f"      - {error}")


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Comprehensive Sleep Study Metrics Extractor")
    parser.add_argument("--patient-id", type=int, help="Extract metrics for specific patient")
    parser.add_argument("--test-all-metrics", action="store_true", help="Test all metrics on sample document")
    
    args = parser.parse_args()
    
    if args.patient_id:
        results = test_patient_extraction(args.patient_id)
        print_extraction_results(results)
    elif args.test_all_metrics:
        results = test_sample_document()
        print_extraction_results(results)
    else:
        print("Usage:")
        print("  python -m flask_app.config.comprehensive_sleep_study_extractor --test-all-metrics")
        print("  python -m flask_app.config.comprehensive_sleep_study_extractor --patient-id 25793")
        sys.exit(1)


if __name__ == "__main__":
    main()
