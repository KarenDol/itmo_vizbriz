#!/usr/bin/env python3
"""
Level 4 Report Processing Script
Main script to process Level 4 reports and extract device design data
"""

import os
import sys
import logging
import json
from pathlib import Path
from typing import Dict, List, Any, Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from flask_app import create_app
from flask_app.services.l4_document_processor import L4DocumentProcessor
from flask_app.services.l4_extraction_service import L4ExtractionService
from flask_app.services.l4_validation_service import L4ValidationService
from flask_app.services.l4_persistence_service import L4PersistenceService
from flask_app.services.l4_qa_service import L4QAService

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class L4ReportProcessor:
    """Main processor for Level 4 reports"""
    
    def __init__(self):
        self.document_processor = L4DocumentProcessor()
        self.extraction_service = L4ExtractionService()
        self.validation_service = L4ValidationService()
        self.persistence_service = L4PersistenceService()
        self.qa_service = L4QAService()
    
    def process_single_report(self, docx_path: str, run_qa: bool = False) -> Dict[str, Any]:
        """
        Process a single Level 4 report
        
        Args:
            docx_path: Path to DOCX file
            run_qa: Whether to run QA validation
            
        Returns:
            Processing result dictionary
        """
        result = {
            "filename": Path(docx_path).name,
            "success": False,
            "steps": {}
        }
        
        try:
            logger.info(f"Processing report: {docx_path}")
            
            # Step 1: Pre-process (extract text and split into sections)
            logger.info("Step 1: Pre-processing document...")
            processed = self.document_processor.process_document(docx_path)
            result["steps"]["preprocess"] = {
                "success": True,
                "sections_found": list(processed["sections"].keys()),
                "patient_id": processed["patient_id"]
            }
            
            # Step 2: LLM extraction
            logger.info("Step 2: Extracting device data with LLM...")
            extraction = self.extraction_service.extract_device_data(
                sections=processed["sections"],
                patient_id=processed["patient_id"],
                filename=processed["filename"]
            )
            result["steps"]["extraction"] = {
                "success": True,
                "designs_found": len(extraction.get("l4_device_design", [])),
                "options_found": len(extraction.get("l4_device_options", []))
            }
            
            # Step 3: Normalize extraction
            logger.info("Step 3: Normalizing extraction...")
            normalized = self.extraction_service.normalize_extraction(extraction)
            result["steps"]["normalization"] = {"success": True}
            
            # Step 4: Validate
            logger.info("Step 4: Validating extraction...")
            is_valid, error_msg = self.validation_service.validate_extraction(normalized)
            result["steps"]["validation"] = {
                "success": is_valid,
                "error": error_msg
            }
            
            if not is_valid:
                logger.error(f"Validation failed: {error_msg}")
                result["error"] = error_msg
                return result
            
            # Step 5: Persist
            logger.info("Step 5: Persisting to database...")
            persisted = self.persistence_service.persist_extraction(
                source_report_id=processed["filename"],
                patient_id=processed["patient_id"],
                extraction=normalized
            )
            result["steps"]["persistence"] = {
                "success": True,
                "designs_persisted": len(persisted.get("device_designs", [])),
                "options_persisted": len(persisted.get("device_options", []))
            }
            
            # Step 6: QA (optional)
            if run_qa:
                logger.info("Step 6: Running QA validation...")
                source_texts = {processed["filename"]: processed["full_text"]}
                qa_result = self.qa_service.validate_extraction_against_source(
                    source_report_id=processed["filename"],
                    source_text=processed["full_text"],
                    extraction=normalized
                )
                result["steps"]["qa"] = {
                    "success": True,
                    "score": qa_result.get("overall_score", 0.0),
                    "issues": qa_result.get("issues", [])
                }
            
            result["success"] = True
            logger.info(f"Successfully processed: {docx_path}")
            
        except Exception as e:
            logger.error(f"Error processing report {docx_path}: {e}", exc_info=True)
            result["error"] = str(e)
            result["success"] = False
        
        return result
    
    def process_directory(self, directory_path: str, run_qa: bool = False) -> Dict[str, Any]:
        """
        Process all DOCX files in a directory
        
        Args:
            directory_path: Path to directory containing DOCX files
            run_qa: Whether to run QA validation
            
        Returns:
            Processing summary
        """
        directory = Path(directory_path)
        docx_files = list(directory.glob("*.docx"))
        
        logger.info(f"Found {len(docx_files)} DOCX files in {directory_path}")
        
        results = []
        successful = 0
        failed = 0
        
        for docx_file in docx_files:
            result = self.process_single_report(str(docx_file), run_qa=run_qa)
            results.append(result)
            
            if result["success"]:
                successful += 1
            else:
                failed += 1
        
        summary = {
            "total_files": len(docx_files),
            "successful": successful,
            "failed": failed,
            "results": results
        }
        
        return summary
    
    def run_qa_loop(self, sample_percentage: float = 0.1) -> Dict[str, Any]:
        """
        Run QA validation loop on processed reports
        
        Args:
            sample_percentage: Percentage of reports to sample
            
        Returns:
            QA report
        """
        logger.info(f"Running QA validation loop (sampling {sample_percentage*100}%)...")
        qa_report = self.qa_service.run_qa_validation(sample_percentage=sample_percentage)
        return qa_report


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Process Level 4 reports and extract device design data")
    parser.add_argument("--input", "-i", required=True, help="Input file or directory")
    parser.add_argument("--qa", action="store_true", help="Run QA validation")
    parser.add_argument("--qa-only", action="store_true", help="Only run QA validation (skip processing)")
    parser.add_argument("--qa-sample", type=float, default=0.1, help="QA sample percentage (0.0 to 1.0)")
    
    args = parser.parse_args()
    
    # Create Flask app context
    app = create_app()
    
    with app.app_context():
        processor = L4ReportProcessor()
        
        if args.qa_only:
            # Only run QA
            qa_report = processor.run_qa_loop(sample_percentage=args.qa_sample)
            print("\n" + "="*80)
            print("QA VALIDATION REPORT")
            print("="*80)
            print(json.dumps(qa_report, indent=2))
        else:
            # Process files
            input_path = Path(args.input)
            
            if input_path.is_file():
                # Single file
                result = processor.process_single_report(str(input_path), run_qa=args.qa)
                print("\n" + "="*80)
                print("PROCESSING RESULT")
                print("="*80)
                print(json.dumps(result, indent=2))
            elif input_path.is_dir():
                # Directory
                summary = processor.process_directory(str(input_path), run_qa=args.qa)
                print("\n" + "="*80)
                print("PROCESSING SUMMARY")
                print("="*80)
                print(f"Total files: {summary['total_files']}")
                print(f"Successful: {summary['successful']}")
                print(f"Failed: {summary['failed']}")
                
                if args.qa:
                    # Run additional QA loop
                    qa_report = processor.run_qa_loop(sample_percentage=args.qa_sample)
                    print("\n" + "="*80)
                    print("QA VALIDATION REPORT")
                    print("="*80)
                    print(json.dumps(qa_report, indent=2))
            else:
                print(f"Error: {args.input} is not a valid file or directory")
                sys.exit(1)


if __name__ == "__main__":
    main()
