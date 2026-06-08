#!/usr/bin/env python3
"""
Level 4 Validation Service
Validates extracted data against JSON schema and performs normalization
"""

import json
import logging
import re
from typing import Dict, List, Any, Optional

# Try to import jsonschema, but don't fail if not available
try:
    from jsonschema import validate, ValidationError
    JSONSCHEMA_AVAILABLE = True
except ImportError:
    JSONSCHEMA_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("jsonschema not installed. Validation will be limited. Install with: pip install jsonschema")

logger = logging.getLogger(__name__)


class L4ValidationService:
    """Service for validating and normalizing extracted Level 4 device data"""
    
    # JSON schema for validation
    VALIDATION_SCHEMA = {
        "type": "object",
        "properties": {
            "l4_device_design": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["design_context", "extraction_confidence"],
                    "properties": {
                        "design_context": {
                            "type": "string",
                            "enum": ["nighttime_MAD", "daytime_TMJ", "unknown"]
                        },
                        "device_family": {"type": ["string", "null"]},
                        "mandibular_advancement": {"type": ["string", "null"]},
                        "preset_mm": {"type": ["string", "null"]},
                        "vertical_opening": {"type": ["string", "null"]},
                        "anterior_window": {"type": ["string", "null"]},
                        "retention_features": {"type": ["string", "null"]},
                        "material": {"type": ["string", "null"]},
                        "anterior_acrylic": {"type": ["string", "null"]},
                        "coverage_notes": {"type": ["string", "null"]},
                        "clinical_notes": {"type": ["string", "null"]},
                        "extraction_confidence": {
                            "type": "string",
                            "enum": ["high", "med", "low"]
                        }
                    }
                }
            },
            "l4_device_options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["design_context", "device_name"],
                    "properties": {
                        "design_context": {
                            "type": "string",
                            "enum": ["nighttime_MAD", "daytime_TMJ", "unknown"]
                        },
                        "device_name": {"type": "string"},
                        "device_family": {"type": ["string", "null"]},
                        "key_features": {"type": ["string", "null"]}
                    }
                }
            }
        },
        "required": ["l4_device_design", "l4_device_options"]
    }
    
    def __init__(self):
        pass
    
    def validate_extraction(self, extraction: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Validate extraction against JSON schema
        
        Args:
            extraction: Extracted data dictionary
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not JSONSCHEMA_AVAILABLE:
            # Basic validation without jsonschema
            if not isinstance(extraction, dict):
                return False, "Extraction must be a dictionary"
            if "l4_device_design" not in extraction:
                return False, "Missing required key: l4_device_design"
            if "l4_device_options" not in extraction:
                return False, "Missing required key: l4_device_options"
            return True, None
        
        try:
            validate(instance=extraction, schema=self.VALIDATION_SCHEMA)
            return True, None
        except ValidationError as e:
            error_msg = f"Validation error: {e.message} at path: {'.'.join(str(p) for p in e.path)}"
            logger.error(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"Unexpected validation error: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return False, error_msg
    
    def normalize_anterior_window(self, value: Optional[str]) -> Optional[str]:
        """
        Normalize anterior window to controlled vocabulary
        
        Args:
            value: Raw anterior window value
            
        Returns:
            Normalized value (Small/Medium/Large) or original if unclear
        """
        if not value:
            return None
        
        value_lower = value.lower().strip()
        
        # Map to controlled vocabulary
        if any(word in value_lower for word in ["small", "minimal", "narrow", "limited"]):
            return "Small"
        elif any(word in value_lower for word in ["medium", "moderate", "standard", "normal"]):
            return "Medium"
        elif any(word in value_lower for word in ["large", "wide", "extensive", "full", "maximum"]):
            return "Large"
        
        # Return original if unclear
        return value
    
    def extract_numeric_mm(self, text: Optional[str]) -> Optional[str]:
        """
        Extract numeric mm value from text
        
        Args:
            text: Text containing mm value (e.g., "+2mm", "2 mm", "edge-to-edge +2mm")
            
        Returns:
            Numeric value as string, or None if not found
        """
        if not text:
            return None
        
        # Look for patterns like "+2mm", "2mm", "2 mm", "+2 mm", "-2mm"
        patterns = [
            r'[+\-]?\s*(\d+(?:\.\d+)?)\s*mm',
            r'(\d+(?:\.\d+)?)\s*mm',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    def normalize_extraction(self, extraction: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize extracted data (numeric extraction, controlled vocabulary)
        
        Args:
            extraction: Raw extraction result
            
        Returns:
            Normalized extraction result
        """
        normalized = {
            "l4_device_design": [],
            "l4_device_options": extraction.get("l4_device_options", [])
        }
        
        for design in extraction.get("l4_device_design", []):
            normalized_design = design.copy()
            
            # Normalize anterior_window
            if "anterior_window" in normalized_design:
                normalized_design["anterior_window"] = self.normalize_anterior_window(
                    normalized_design.get("anterior_window")
                )
            
            # Extract numeric preset_mm if not already numeric
            if not normalized_design.get("preset_mm") or not self._is_numeric(normalized_design.get("preset_mm")):
                # Try to extract from mandibular_advancement
                mand_adv = normalized_design.get("mandibular_advancement")
                if mand_adv:
                    numeric_mm = self.extract_numeric_mm(mand_adv)
                    if numeric_mm:
                        normalized_design["preset_mm"] = numeric_mm
            
            # If preset_mm exists but is not numeric, try to extract
            if normalized_design.get("preset_mm"):
                preset_text = str(normalized_design["preset_mm"])
                if not self._is_numeric(preset_text):
                    numeric_mm = self.extract_numeric_mm(preset_text)
                    if numeric_mm:
                        normalized_design["preset_mm"] = numeric_mm
            
            normalized["l4_device_design"].append(normalized_design)
        
        return normalized
    
    def _is_numeric(self, value: Optional[str]) -> bool:
        """Check if a string value is numeric"""
        if not value:
            return False
        try:
            float(str(value).replace(",", ""))
            return True
        except (ValueError, AttributeError):
            return False
    
    def validate_and_normalize(self, extraction: Dict[str, Any]) -> tuple[Dict[str, Any], bool, Optional[str]]:
        """
        Validate and normalize extraction in one step
        
        Args:
            extraction: Raw extraction result
            
        Returns:
            Tuple of (normalized_extraction, is_valid, error_message)
        """
        # First normalize
        normalized = self.normalize_extraction(extraction)
        
        # Then validate
        is_valid, error_msg = self.validate_extraction(normalized)
        
        return normalized, is_valid, error_msg
