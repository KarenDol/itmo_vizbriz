#!/usr/bin/env python3
"""
Level 4 Device Design Extraction Service
Uses LLM to extract structured data from Level 4 report sections
"""

import json
import logging
import re
from typing import Dict, List, Any, Optional
from flask_app.services.bedrock_service import BedrockService

logger = logging.getLogger(__name__)


class L4ExtractionService:
    """Service for extracting device design data from Level 4 reports using LLM"""
    
    # JSON schema for structured output
    EXTRACTION_SCHEMA = {
        "type": "object",
        "properties": {
            "l4_device_design": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "design_context": {
                            "type": "string",
                            "enum": ["nighttime_MAD", "daytime_TMJ", "unknown"],
                            "description": "Context: nighttime_MAD, daytime_TMJ, or unknown"
                        },
                        "device_family": {
                            "type": ["string", "null"],
                            "description": "Device family if explicitly stated, else null"
                        },
                        "mandibular_advancement": {
                            "type": ["string", "null"],
                            "description": "Mandibular advancement value/description (keep original if not cleanly numeric)"
                        },
                        "preset_mm": {
                            "type": ["string", "null"],
                            "description": "Pre-set mandibular advancement in mm (extract numeric if possible)"
                        },
                        "vertical_opening": {
                            "type": ["string", "null"],
                            "description": "Vertical opening value and location (anterior/posterior)"
                        },
                        "anterior_window": {
                            "type": ["string", "null"],
                            "description": "Anterior window size (Small/Medium/Large or original text)"
                        },
                        "retention_features": {
                            "type": ["string", "null"],
                            "description": "Retention features description"
                        },
                        "material": {
                            "type": ["string", "null"],
                            "description": "Material type"
                        },
                        "anterior_acrylic": {
                            "type": ["string", "null"],
                            "description": "Anterior acrylic details"
                        },
                        "coverage_notes": {
                            "type": ["string", "null"],
                            "description": "Coverage information"
                        },
                        "clinical_notes": {
                            "type": ["string", "null"],
                            "description": "Clinical notes"
                        },
                        "extraction_confidence": {
                            "type": "string",
                            "enum": ["high", "med", "low"],
                            "description": "Confidence level: high if all key fields present, med if most present, low if few present"
                        },
                        "ahi": {
                            "type": ["string", "null"],
                            "description": "AHI value and severity from sleep study (e.g., '10.9 (Mild OSA)')"
                        },
                        "rdi": {
                            "type": ["string", "null"],
                            "description": "RDI value if provided"
                        },
                        "odi": {
                            "type": ["string", "null"],
                            "description": "ODI value if provided"
                        },
                        "o2_nadir": {
                            "type": ["string", "null"],
                            "description": "O2 Nadir percentage"
                        },
                        "snoring_level": {
                            "type": ["string", "null"],
                            "description": "Snoring level/percentage"
                        },
                        "clinical_background": {
                            "type": ["string", "null"],
                            "description": "Clinical background (e.g., GERD, allergic rhinitis)"
                        },
                        "patient_complaints": {
                            "type": ["string", "null"],
                            "description": "Patient complaints"
                        },
                        "obstruction_sites": {
                            "type": ["string", "null"],
                            "description": "Primary obstruction sites (e.g., velopharynx, tongue base)"
                        },
                        "bite_structure": {
                            "type": ["string", "null"],
                            "description": "Bite and jaw structure observations"
                        },
                        "soft_palate_uvula": {
                            "type": ["string", "null"],
                            "description": "Soft palate and uvula findings"
                        },
                        "tongue_position": {
                            "type": ["string", "null"],
                            "description": "Tongue position observations"
                        },
                        "treatment_considerations": {
                            "type": ["string", "null"],
                            "description": "Treatment considerations that informed device design"
                        }
                    },
                    "required": ["design_context", "extraction_confidence"]
                }
            },
            "l4_device_options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "design_context": {
                            "type": "string",
                            "enum": ["nighttime_MAD", "daytime_TMJ", "unknown"],
                            "description": "Context to link options to device design"
                        },
                        "device_name": {
                            "type": "string",
                            "description": "Device name (required)"
                        },
                        "device_family": {
                            "type": ["string", "null"],
                            "description": "Device family if derivable"
                        },
                        "key_features": {
                            "type": ["string", "null"],
                            "description": "Key features if present"
                        }
                    },
                    "required": ["design_context", "device_name"]
                }
            }
        },
        "required": ["l4_device_design", "l4_device_options"]
    }
    
    def __init__(self):
        self.bedrock = BedrockService()
    
    def _build_extraction_prompt(self, sections: Dict[str, str], patient_id: Optional[str], filename: str) -> str:
        """
        Build the LLM prompt for extraction
        
        Args:
            sections: Dictionary of section text
            patient_id: Patient ID if extracted
            filename: Source filename
            
        Returns:
            Formatted prompt string
        """
        prompt = f"""You are extracting device design data from a Level 4 OSA report.

SOURCE FILE: {filename}
PATIENT ID: {patient_id or "Not found"}

REPORT SECTIONS:
"""
        
        # Add relevant sections
        design_sections = []
        options_sections = []
        clinical_sections = []
        
        for section_name, section_text in sections.items():
            if "Design Data Considerations" in section_name:
                design_sections.append(f"\n=== {section_name} ===\n{section_text}\n")
            elif "Oral Appliance Options" in section_name:
                options_sections.append(f"\n=== {section_name} ===\n{section_text}\n")
            elif any(keyword in section_name for keyword in ["Sleep Study", "Clinical Background", "Structural Observations", "Observations", "Treatment Considerations"]):
                clinical_sections.append(f"\n=== {section_name} ===\n{section_text}\n")
        
        if clinical_sections:
            prompt += "\n=== CLINICAL CONTEXT (for device design rationale) ===\n"
            prompt += "\n".join(clinical_sections)
            prompt += "\n"
        
        if design_sections:
            prompt += "\n".join(design_sections)
        else:
            prompt += "\n[No device design sections found]\n"
        
        if options_sections:
            prompt += "\n" + "\n".join(options_sections)
        else:
            prompt += "\n[No device options sections found]\n"
        
        prompt += """

EXTRACTION RULES:
1. NO GUESSING: If a device name isn't explicitly stated in the design block, output null for device_family
2. Keep original strings if not cleanly numeric (e.g., "edge-to-edge +2mm" → keep as-is)
3. Extract numeric values where possible (e.g., "+2mm" → extract "2" for preset_mm, but keep full string for mandibular_advancement)
4. Design context detection:
   - "nighttime_MAD" if section mentions "Nighttime" or "MAD" or "Mandibular Advancement Device"
   - "daytime_TMJ" if section mentions "Daytime" or "TMJ" or "Lower TMJ Appliance"
   - "unknown" if context is unclear
5. Anterior Window normalization: Try to map to Small/Medium/Large if clear, otherwise keep original text
6. Vertical Opening: Capture both value AND location (anterior/posterior) if mentioned
7. Clinical Context Extraction (IMPORTANT for understanding design rationale):
   - Extract AHI, RDI, ODI, O2 Nadir from "Sleep Study Data" section
   - Extract clinical_background, patient_complaints from "Clinical Background" section
   - Extract obstruction_sites, bite_structure, soft_palate_uvula, tongue_position from "Structural Observations" section
   - Extract treatment_considerations from "Possible Treatment Considerations" section
8. Confidence levels:
   - "high": All key fields (mandibular_advancement, vertical_opening, material) are present
   - "med": Most key fields present
   - "low": Few key fields present or unclear data

OUTPUT FORMAT:
Return a JSON object with two arrays:
- l4_device_design[]: One entry per device design section found (can be 1..n per report)
- l4_device_options[]: One entry per device option listed (0..n per report)

IMPORTANT: Return ONLY valid JSON, no markdown, no explanations, no code blocks.
"""
        
        return prompt
    
    def extract_device_data(self, sections: Dict[str, str], patient_id: Optional[str], filename: str) -> Dict[str, Any]:
        """
        Extract device design data from report sections using LLM
        
        Args:
            sections: Dictionary of section text
            patient_id: Patient ID if extracted
            filename: Source filename
            
        Returns:
            Dictionary with l4_device_design and l4_device_options arrays
        """
        try:
            prompt = self._build_extraction_prompt(sections, patient_id, filename)
            
            # Add JSON schema instruction to prompt
            schema_instruction = f"""

IMPORTANT: You must return ONLY valid JSON matching this schema:
{json.dumps(self.EXTRACTION_SCHEMA, indent=2)}

Return the JSON object directly, no markdown, no code blocks, no explanations.
"""
            full_prompt = prompt + schema_instruction
            
            # Use Bedrock invoke_model
            messages = [
                {
                    "role": "user",
                    "content": full_prompt
                }
            ]
            
            response = self.bedrock.invoke_model(
                messages=messages,
                model="claude_4_sonnet",
                max_tokens=4000,
                temperature=0.1,
                endpoint="l4_device_extraction"
            )
            
            # Parse response
            if not response.get("success"):
                logger.error(f"Bedrock invocation failed: {response.get('error')}")
                return {
                    "l4_device_design": [],
                    "l4_device_options": []
                }
            
            response_text = response.get("response", "")
            
            # Try to parse JSON from response
            try:
                # Remove markdown code blocks if present
                cleaned = response_text.strip()
                if "```json" in cleaned:
                    cleaned = cleaned.split("```json")[1].split("```")[0].strip()
                elif "```" in cleaned:
                    # Find first ``` and last ```
                    parts = cleaned.split("```")
                    if len(parts) >= 3:
                        cleaned = "```".join(parts[1:-1]).strip()
                
                # Try to find JSON object boundaries
                first_brace = cleaned.find('{')
                last_brace = cleaned.rfind('}')
                if first_brace >= 0 and last_brace > first_brace:
                    cleaned = cleaned[first_brace:last_brace + 1]
                
                result = json.loads(cleaned)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response: {e}")
                logger.error(f"Response was: {response_text[:500]}")
                # Return empty structure
                result = {
                    "l4_device_design": [],
                    "l4_device_options": []
                }
            
            # Ensure required keys exist
            if "l4_device_design" not in result:
                result["l4_device_design"] = []
            if "l4_device_options" not in result:
                result["l4_device_options"] = []
            
            return result
            
        except Exception as e:
            logger.error(f"Error extracting device data: {e}", exc_info=True)
            # Return empty structure on error
            return {
                "l4_device_design": [],
                "l4_device_options": []
            }
    
    def _normalize_anterior_window(self, value: Optional[str]) -> Optional[str]:
        """
        Normalize anterior window to controlled vocabulary
        
        Args:
            value: Raw anterior window value
            
        Returns:
            Normalized value (Small/Medium/Large) or original if unclear
        """
        if not value:
            return None
        
        value_lower = value.lower()
        
        # Map to controlled vocabulary
        if any(word in value_lower for word in ["small", "minimal", "narrow"]):
            return "Small"
        elif any(word in value_lower for word in ["medium", "moderate", "standard"]):
            return "Medium"
        elif any(word in value_lower for word in ["large", "wide", "extensive", "full"]):
            return "Large"
        
        # Return original if unclear
        return value
    
    def _extract_numeric_mm(self, text: Optional[str]) -> Optional[str]:
        """
        Extract numeric mm value from text
        
        Args:
            text: Text containing mm value (e.g., "+2mm", "2 mm", "edge-to-edge +2mm")
            
        Returns:
            Numeric value as string, or None if not found
        """
        if not text:
            return None
        
        # Look for patterns like "+2mm", "2mm", "2 mm", "+2 mm"
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
                normalized_design["anterior_window"] = self._normalize_anterior_window(
                    normalized_design.get("anterior_window")
                )
            
            # Extract numeric preset_mm if not already numeric
            if "preset_mm" not in normalized_design or not normalized_design["preset_mm"]:
                # Try to extract from mandibular_advancement
                mand_adv = normalized_design.get("mandibular_advancement")
                if mand_adv:
                    numeric_mm = self._extract_numeric_mm(mand_adv)
                    if numeric_mm:
                        normalized_design["preset_mm"] = numeric_mm
            
            # If preset_mm exists but is not numeric, try to extract
            if normalized_design.get("preset_mm"):
                preset_text = normalized_design["preset_mm"]
                if not preset_text.replace(".", "").replace("-", "").isdigit():
                    numeric_mm = self._extract_numeric_mm(preset_text)
                    if numeric_mm:
                        normalized_design["preset_mm"] = numeric_mm
            
            normalized["l4_device_design"].append(normalized_design)
        
        return normalized
