#!/usr/bin/env python3
"""
Level 4 QA Service
Samples and validates extractions against source text
"""

import logging
import random
from typing import Dict, List, Any, Optional, Tuple
from flask_app.models import L4DeviceDesign, L4DeviceOption
from flask_app.extensions import db

logger = logging.getLogger(__name__)


class L4QAService:
    """Service for QA validation of extracted data"""
    
    def __init__(self):
        pass
    
    def sample_reports(self, sample_percentage: float = 0.1, min_samples: int = 1, max_samples: int = 10) -> List[str]:
        """
        Randomly sample reports for QA validation
        
        Args:
            sample_percentage: Percentage of reports to sample (0.0 to 1.0)
            min_samples: Minimum number of samples
            max_samples: Maximum number of samples
            
        Returns:
            List of source_report_id values to validate
        """
        try:
            # Get all unique report IDs
            all_reports = db.session.query(L4DeviceDesign.source_report_id).distinct().all()
            report_ids = [r[0] for r in all_reports]
            
            if not report_ids:
                logger.warning("No reports found for QA sampling")
                return []
            
            # Calculate sample size
            sample_size = max(
                min_samples,
                min(max_samples, int(len(report_ids) * sample_percentage))
            )
            
            # Randomly sample
            sampled = random.sample(report_ids, min(sample_size, len(report_ids)))
            
            logger.info(f"Sampled {len(sampled)} reports out of {len(report_ids)} total for QA")
            return sampled
            
        except Exception as e:
            logger.error(f"Error sampling reports: {e}", exc_info=True)
            return []
    
    def validate_extraction_against_source(self,
                                          source_report_id: str,
                                          source_text: str,
                                          extraction: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate extracted data against source text
        
        Args:
            source_report_id: Source report ID
            source_text: Original document text
            extraction: Extracted data dictionary
            
        Returns:
            Validation report dictionary
        """
        validation_report = {
            "source_report_id": source_report_id,
            "validations": [],
            "overall_score": 0.0,
            "issues": []
        }
        
        try:
            source_lower = source_text.lower()
            issues = []
            total_checks = 0
            passed_checks = 0
            
            # Validate each device design
            for design in extraction.get("l4_device_design", []):
                design_context = design.get("design_context", "unknown")
                validation = {
                    "type": "device_design",
                    "design_context": design_context,
                    "checks": [],
                    "score": 0.0
                }
                
                # Check mandibular_advancement
                mand_adv = design.get("mandibular_advancement")
                if mand_adv:
                    total_checks += 1
                    if any(word in source_lower for word in mand_adv.lower().split()[:3]):  # Check first few words
                        validation["checks"].append({
                            "field": "mandibular_advancement",
                            "status": "pass",
                            "value": mand_adv
                        })
                        passed_checks += 1
                    else:
                        validation["checks"].append({
                            "field": "mandibular_advancement",
                            "status": "fail",
                            "value": mand_adv,
                            "issue": "Value not found in source text"
                        })
                        issues.append(f"mandibular_advancement '{mand_adv}' not found in source")
                
                # Check vertical_opening
                vert_open = design.get("vertical_opening")
                if vert_open:
                    total_checks += 1
                    if any(word in source_lower for word in vert_open.lower().split()[:3]):
                        validation["checks"].append({
                            "field": "vertical_opening",
                            "status": "pass",
                            "value": vert_open
                        })
                        passed_checks += 1
                    else:
                        validation["checks"].append({
                            "field": "vertical_opening",
                            "status": "fail",
                            "value": vert_open,
                            "issue": "Value not found in source text"
                        })
                        issues.append(f"vertical_opening '{vert_open}' not found in source")
                
                # Check material
                material = design.get("material")
                if material:
                    total_checks += 1
                    if any(word in source_lower for word in material.lower().split()[:3]):
                        validation["checks"].append({
                            "field": "material",
                            "status": "pass",
                            "value": material
                        })
                        passed_checks += 1
                    else:
                        validation["checks"].append({
                            "field": "material",
                            "status": "fail",
                            "value": material,
                            "issue": "Value not found in source text"
                        })
                        issues.append(f"material '{material}' not found in source")
                
                # Calculate score for this design
                if validation["checks"]:
                    passed = sum(1 for c in validation["checks"] if c.get("status") == "pass")
                    validation["score"] = passed / len(validation["checks"])
                
                validation_report["validations"].append(validation)
            
            # Validate device options
            for option in extraction.get("l4_device_options", []):
                device_name = option.get("device_name", "")
                if device_name:
                    total_checks += 1
                    if device_name.lower() in source_lower:
                        passed_checks += 1
                    else:
                        issues.append(f"device_name '{device_name}' not found in source")
            
            # Calculate overall score
            if total_checks > 0:
                validation_report["overall_score"] = passed_checks / total_checks
            else:
                validation_report["overall_score"] = 0.0
            
            validation_report["issues"] = issues
            
            return validation_report
            
        except Exception as e:
            logger.error(f"Error validating extraction: {e}", exc_info=True)
            validation_report["error"] = str(e)
            return validation_report
    
    def run_qa_validation(self,
                         sample_percentage: float = 0.1,
                         source_texts: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Run QA validation on sampled reports
        
        Args:
            sample_percentage: Percentage of reports to sample
            source_texts: Optional dictionary mapping report_id to source text
            
        Returns:
            QA validation report
        """
        qa_report = {
            "sampled_reports": [],
            "overall_stats": {
                "total_sampled": 0,
                "total_validated": 0,
                "average_score": 0.0,
                "reports_with_issues": 0
            }
        }
        
        try:
            # Sample reports
            sampled_ids = self.sample_reports(sample_percentage=sample_percentage)
            qa_report["overall_stats"]["total_sampled"] = len(sampled_ids)
            
            scores = []
            
            for report_id in sampled_ids:
                try:
                    # Get extracted data from database
                    designs = L4DeviceDesign.query.filter_by(source_report_id=report_id).all()
                    options = L4DeviceOption.query.filter_by(source_report_id=report_id).all()
                    
                    # Reconstruct extraction dictionary
                    extraction = {
                        "l4_device_design": [d.to_dict() for d in designs],
                        "l4_device_options": [o.to_dict() for o in options]
                    }
                    
                    # Get source text if provided
                    source_text = source_texts.get(report_id, "") if source_texts else ""
                    
                    if source_text:
                        # Validate
                        validation = self.validate_extraction_against_source(
                            source_report_id=report_id,
                            source_text=source_text,
                            extraction=extraction
                        )
                        
                        qa_report["sampled_reports"].append(validation)
                        scores.append(validation.get("overall_score", 0.0))
                        
                        if validation.get("issues"):
                            qa_report["overall_stats"]["reports_with_issues"] += 1
                        
                        qa_report["overall_stats"]["total_validated"] += 1
                    else:
                        logger.warning(f"No source text provided for {report_id}, skipping validation")
                        
                except Exception as e:
                    logger.error(f"Error validating report {report_id}: {e}", exc_info=True)
                    qa_report["sampled_reports"].append({
                        "source_report_id": report_id,
                        "error": str(e)
                    })
            
            # Calculate average score
            if scores:
                qa_report["overall_stats"]["average_score"] = sum(scores) / len(scores)
            
            return qa_report
            
        except Exception as e:
            logger.error(f"Error running QA validation: {e}", exc_info=True)
            qa_report["error"] = str(e)
            return qa_report
