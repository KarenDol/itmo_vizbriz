#!/usr/bin/env python3
"""
Level 4 Persistence Service
Handles database persistence for extracted device design data
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
from flask_app.extensions import db
from flask_app.models import L4DeviceDesign, L4DeviceOption

logger = logging.getLogger(__name__)


class L4PersistenceService:
    """Service for persisting extracted Level 4 device data to database"""
    
    def __init__(self):
        pass
    
    def upsert_device_design(self, 
                            source_report_id: str,
                            patient_id: Optional[str],
                            design_data: Dict[str, Any]) -> L4DeviceDesign:
        """
        Upsert device design record
        
        Args:
            source_report_id: Source report filename
            patient_id: Patient ID
            design_data: Device design data dictionary
            
        Returns:
            L4DeviceDesign object
        """
        try:
            design_context = design_data.get("design_context", "unknown")
            
            # Check if record exists
            existing = L4DeviceDesign.query.filter_by(
                source_report_id=source_report_id,
                design_context=design_context
            ).first()
            
            if existing:
                # Update existing record
                existing.patient_id = patient_id
                # Clinical context
                existing.ahi = design_data.get("ahi")
                existing.rdi = design_data.get("rdi")
                existing.odi = design_data.get("odi")
                existing.o2_nadir = design_data.get("o2_nadir")
                existing.snoring_level = design_data.get("snoring_level")
                existing.clinical_background = design_data.get("clinical_background")
                existing.patient_complaints = design_data.get("patient_complaints")
                existing.obstruction_sites = design_data.get("obstruction_sites")
                existing.bite_structure = design_data.get("bite_structure")
                existing.soft_palate_uvula = design_data.get("soft_palate_uvula")
                existing.tongue_position = design_data.get("tongue_position")
                existing.treatment_considerations = design_data.get("treatment_considerations")
                # Device design
                existing.device_family = design_data.get("device_family")
                existing.mandibular_advancement = design_data.get("mandibular_advancement")
                existing.preset_mm = design_data.get("preset_mm")
                existing.vertical_opening = design_data.get("vertical_opening")
                existing.anterior_window = design_data.get("anterior_window")
                existing.retention_features = design_data.get("retention_features")
                existing.material = design_data.get("material")
                existing.anterior_acrylic = design_data.get("anterior_acrylic")
                existing.coverage_notes = design_data.get("coverage_notes")
                existing.clinical_notes = design_data.get("clinical_notes")
                existing.extraction_confidence = design_data.get("extraction_confidence", "med")
                existing.updated_at = datetime.utcnow()
                
                db.session.commit()
                logger.info(f"Updated device design: {source_report_id} - {design_context}")
                return existing
            else:
                # Create new record
                new_design = L4DeviceDesign(
                    source_report_id=source_report_id,
                    patient_id=patient_id,
                    # Clinical context
                    ahi=design_data.get("ahi"),
                    rdi=design_data.get("rdi"),
                    odi=design_data.get("odi"),
                    o2_nadir=design_data.get("o2_nadir"),
                    snoring_level=design_data.get("snoring_level"),
                    clinical_background=design_data.get("clinical_background"),
                    patient_complaints=design_data.get("patient_complaints"),
                    obstruction_sites=design_data.get("obstruction_sites"),
                    bite_structure=design_data.get("bite_structure"),
                    soft_palate_uvula=design_data.get("soft_palate_uvula"),
                    tongue_position=design_data.get("tongue_position"),
                    treatment_considerations=design_data.get("treatment_considerations"),
                    # Device design
                    design_context=design_context,
                    device_family=design_data.get("device_family"),
                    mandibular_advancement=design_data.get("mandibular_advancement"),
                    preset_mm=design_data.get("preset_mm"),
                    vertical_opening=design_data.get("vertical_opening"),
                    anterior_window=design_data.get("anterior_window"),
                    retention_features=design_data.get("retention_features"),
                    material=design_data.get("material"),
                    anterior_acrylic=design_data.get("anterior_acrylic"),
                    coverage_notes=design_data.get("coverage_notes"),
                    clinical_notes=design_data.get("clinical_notes"),
                    extraction_confidence=design_data.get("extraction_confidence", "med")
                )
                
                db.session.add(new_design)
                db.session.commit()
                logger.info(f"Created device design: {source_report_id} - {design_context}")
                return new_design
                
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error upserting device design: {e}", exc_info=True)
            raise
    
    def insert_device_options(self,
                              source_report_id: str,
                              design_context: str,
                              options: List[Dict[str, Any]],
                              device_design_id: Optional[int] = None) -> List[L4DeviceOption]:
        """
        Insert device options (delete existing and insert new)
        
        Args:
            source_report_id: Source report filename
            design_context: Design context
            options: List of device option dictionaries
            device_design_id: Optional foreign key to device design
            
        Returns:
            List of L4DeviceOption objects
        """
        try:
            # Delete existing options for this report and context
            L4DeviceOption.query.filter_by(
                source_report_id=source_report_id,
                design_context=design_context
            ).delete()
            
            # Insert new options
            inserted_options = []
            for option_data in options:
                new_option = L4DeviceOption(
                    source_report_id=source_report_id,
                    design_context=design_context,
                    device_name=option_data.get("device_name", ""),
                    device_family=option_data.get("device_family"),
                    key_features=option_data.get("key_features"),
                    device_design_id=device_design_id
                )
                db.session.add(new_option)
                inserted_options.append(new_option)
            
            db.session.commit()
            logger.info(f"Inserted {len(inserted_options)} device options for {source_report_id} - {design_context}")
            return inserted_options
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error inserting device options: {e}", exc_info=True)
            raise
    
    def persist_extraction(self,
                          source_report_id: str,
                          patient_id: Optional[str],
                          extraction: Dict[str, Any]) -> Dict[str, Any]:
        """
        Persist complete extraction (designs + options)
        
        Args:
            source_report_id: Source report filename
            patient_id: Patient ID
            extraction: Complete extraction result with l4_device_design and l4_device_options
            
        Returns:
            Dictionary with created/updated records
        """
        result = {
            "device_designs": [],
            "device_options": []
        }
        
        try:
            # Process each device design
            for design_data in extraction.get("l4_device_design", []):
                design_context = design_data.get("design_context", "unknown")
                
                # Upsert device design
                device_design = self.upsert_device_design(
                    source_report_id=source_report_id,
                    patient_id=patient_id,
                    design_data=design_data
                )
                result["device_designs"].append(device_design.to_dict())
                
                # Get options for this design context
                options_for_context = [
                    opt for opt in extraction.get("l4_device_options", [])
                    if opt.get("design_context") == design_context
                ]
                
                # Insert options
                if options_for_context:
                    inserted_options = self.insert_device_options(
                        source_report_id=source_report_id,
                        design_context=design_context,
                        options=options_for_context,
                        device_design_id=device_design.id
                    )
                    result["device_options"].extend([opt.to_dict() for opt in inserted_options])
            
            # Handle options without matching design context (shouldn't happen, but handle gracefully)
            all_design_contexts = {d.get("design_context") for d in extraction.get("l4_device_design", [])}
            orphan_options = [
                opt for opt in extraction.get("l4_device_options", [])
                if opt.get("design_context") not in all_design_contexts
            ]
            
            if orphan_options:
                logger.warning(f"Found {len(orphan_options)} options without matching design context")
                # Insert with "unknown" context
                inserted_options = self.insert_device_options(
                    source_report_id=source_report_id,
                    design_context="unknown",
                    options=orphan_options
                )
                result["device_options"].extend([opt.to_dict() for opt in inserted_options])
            
            return result
            
        except Exception as e:
            logger.error(f"Error persisting extraction: {e}", exc_info=True)
            raise
