#!/usr/bin/env python3
"""
Level 4 Case Card Generator
Creates anonymized case cards from Level 4 reports for knowledge base
Removes PII but keeps age, sex, and clinical requirements for clustering
"""

import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from flask_app.models import L4DeviceDesign, L4DeviceOption

logger = logging.getLogger(__name__)


class L4CaseCardGenerator:
    """Generates anonymized case cards from Level 4 device design data"""
    
    def __init__(self):
        pass
    
    def anonymize_patient_id(self, patient_id: Optional[str]) -> str:
        """
        Anonymize patient ID - keep format but remove identifying info
        Example: "YS 1982" -> "Patient_1982" or just remove if too identifying
        """
        if not patient_id:
            return "Anonymous"
        
        # Remove name initials, keep year if present
        # "YS 1982" -> "Patient_1982"
        # "Case 123" -> "Patient_123"
        import re
        year_match = re.search(r'\d{4}', patient_id)
        if year_match:
            return f"Patient_{year_match.group()}"
        
        # If no year, just return generic
        return "Anonymous"
    
    def generate_case_card(self, device_design: L4DeviceDesign, 
                          device_options: list = None) -> Dict[str, Any]:
        """
        Generate an anonymized case card from device design data
        
        Args:
            device_design: L4DeviceDesign object
            device_options: List of L4DeviceOption objects (optional)
            
        Returns:
            Dictionary representing the case card
        """
        if device_options is None:
            device_options = device_design.device_options.all()
        
        # Build case card
        case_card = {
            "case_type": "level4_device_design",
            "timestamp": datetime.utcnow().isoformat(),
            "source_report": device_design.source_report_id,
            
            # Demographics (anonymized)
            "patient_id": self.anonymize_patient_id(device_design.patient_id),
            "age": getattr(device_design, 'age', None),  # Extracted from report
            "sex": getattr(device_design, 'sex', None),  # Extracted from report
            
            # Clinical Diagnosis & Sleep Study
            "diagnosis": {
                "ahi": device_design.ahi,
                "rdi": device_design.rdi,
                "odi": device_design.odi,
                "o2_nadir": device_design.o2_nadir,
                "snoring_level": device_design.snoring_level,
                "severity": self._extract_severity(device_design.ahi)
            },
            
            # Clinical Background
            "clinical_background": {
                "background": device_design.clinical_background,
                "complaints": device_design.patient_complaints,
                "treatment_considerations": device_design.treatment_considerations
            },
            
            # Structural Findings (key for device design decisions)
            "structural_findings": {
                "obstruction_sites": device_design.obstruction_sites,
                "bite_structure": device_design.bite_structure,
                "soft_palate_uvula": device_design.soft_palate_uvula,
                "tongue_position": device_design.tongue_position
            },
            
            # Device Design Requirements (what was prescribed)
            "device_design": {
                "design_context": device_design.design_context,
                "mandibular_advancement": device_design.mandibular_advancement,
                "preset_mm": device_design.preset_mm,
                "vertical_opening": device_design.vertical_opening,
                "anterior_window": device_design.anterior_window,
                "retention_features": device_design.retention_features,
                "material": device_design.material,
                "anterior_acrylic": device_design.anterior_acrylic,
                "coverage_notes": device_design.coverage_notes,
                "clinical_notes": device_design.clinical_notes
            },
            
            # Device Options (what devices were recommended)
            "device_options": [
                {
                    "device_name": opt.device_name,
                    "device_family": opt.device_family,
                    "key_features": opt.key_features
                }
                for opt in device_options
            ],
            
            # Clustering Features (for similarity matching)
            "clustering_features": self._extract_clustering_features(device_design)
        }
        
        return case_card
    
    def _extract_severity(self, ahi: Optional[str]) -> Optional[str]:
        """Extract OSA severity from AHI string"""
        if not ahi:
            return None
        
        ahi_lower = ahi.lower()
        if "mild" in ahi_lower:
            return "mild"
        elif "moderate" in ahi_lower:
            return "moderate"
        elif "severe" in ahi_lower:
            return "severe"
        
        return None
    
    def _extract_clustering_features(self, device_design: L4DeviceDesign) -> Dict[str, Any]:
        """
        Extract features for clustering/similarity matching
        These are the key attributes that determine device design
        """
        return {
            "ahi_severity": self._extract_severity(device_design.ahi),
            "o2_nadir_range": self._categorize_o2_nadir(device_design.o2_nadir),
            "obstruction_type": self._categorize_obstruction(device_design.obstruction_sites),
            "tongue_position_type": self._categorize_tongue_position(device_design.tongue_position),
            "bite_type": self._categorize_bite(device_design.bite_structure),
            "mandibular_advancement_type": self._categorize_advancement(device_design.mandibular_advancement),
            "anterior_window_size": device_design.anterior_window,
            "material_type": device_design.material
        }
    
    def _categorize_o2_nadir(self, o2_nadir: Optional[str]) -> Optional[str]:
        """Categorize O2 nadir into ranges"""
        if not o2_nadir:
            return None
        
        try:
            # Extract numeric value
            import re
            match = re.search(r'(\d+)', o2_nadir)
            if match:
                value = int(match.group(1))
                if value >= 90:
                    return "normal"
                elif value >= 85:
                    return "mild_desaturation"
                elif value >= 80:
                    return "moderate_desaturation"
                else:
                    return "severe_desaturation"
        except:
            pass
        
        return None
    
    def _categorize_obstruction(self, obstruction_sites: Optional[str]) -> Optional[str]:
        """Categorize obstruction sites"""
        if not obstruction_sites:
            return None
        
        obstruction_lower = obstruction_sites.lower()
        if "velopharynx" in obstruction_lower or "velopharyngeal" in obstruction_lower:
            if "tongue" in obstruction_lower or "tongue base" in obstruction_lower:
                return "velopharyngeal_and_tongue"
            return "velopharyngeal"
        elif "tongue" in obstruction_lower or "tongue base" in obstruction_lower:
            return "tongue_base"
        elif "oropharyngeal" in obstruction_lower:
            return "oropharyngeal"
        
        return "multiple"
    
    def _categorize_tongue_position(self, tongue_position: Optional[str]) -> Optional[str]:
        """Categorize tongue position"""
        if not tongue_position:
            return None
        
        position_lower = tongue_position.lower()
        if "posterior" in position_lower:
            return "posterior"
        elif "anterior" in position_lower:
            return "anterior"
        elif "normal" in position_lower:
            return "normal"
        
        return None
    
    def _categorize_bite(self, bite_structure: Optional[str]) -> Optional[str]:
        """Categorize bite structure"""
        if not bite_structure:
            return None
        
        bite_lower = bite_structure.lower()
        if "deep bite" in bite_lower:
            return "deep_bite"
        elif "crossbite" in bite_lower:
            return "crossbite"
        elif "reduced overjet" in bite_lower or "decreased overjet" in bite_lower:
            return "reduced_overjet"
        elif "retrognathic" in bite_lower:
            return "retrognathic"
        
        return None
    
    def _categorize_advancement(self, mandibular_advancement: Optional[str]) -> Optional[str]:
        """Categorize mandibular advancement type"""
        if not mandibular_advancement:
            return None
        
        adv_lower = mandibular_advancement.lower()
        if "edge-to-edge" in adv_lower:
            return "edge_to_edge"
        elif "protrusive" in adv_lower:
            return "protrusive"
        elif any(word in adv_lower for word in ["mm", "millimeter", "advancement"]):
            return "measured_advancement"
        
        return None
    
    def generate_case_card_text(self, case_card: Dict[str, Any]) -> str:
        """
        Convert case card to text format for knowledge base
        
        Args:
            case_card: Case card dictionary
            
        Returns:
            Formatted text string
        """
        lines = []
        lines.append("=" * 80)
        lines.append("LEVEL 4 DEVICE DESIGN CASE CARD")
        lines.append("=" * 80)
        lines.append("")
        
        # Demographics
        lines.append("PATIENT DEMOGRAPHICS:")
        lines.append(f"  Patient ID: {case_card.get('patient_id', 'N/A')}")
        if case_card.get('age'):
            lines.append(f"  Age: {case_card['age']}")
        if case_card.get('sex'):
            lines.append(f"  Sex: {case_card['sex']}")
        lines.append("")
        
        # Diagnosis
        diagnosis = case_card.get('diagnosis', {})
        lines.append("CLINICAL DIAGNOSIS:")
        if diagnosis.get('ahi'):
            lines.append(f"  AHI: {diagnosis['ahi']}")
        if diagnosis.get('severity'):
            lines.append(f"  Severity: {diagnosis['severity'].upper()}")
        if diagnosis.get('o2_nadir'):
            lines.append(f"  O2 Nadir: {diagnosis['o2_nadir']}")
        if diagnosis.get('snoring_level'):
            lines.append(f"  Snoring: {diagnosis['snoring_level']}")
        lines.append("")
        
        # Clinical Background
        clinical = case_card.get('clinical_background', {})
        if clinical.get('background'):
            lines.append(f"CLINICAL BACKGROUND: {clinical['background']}")
        if clinical.get('complaints'):
            lines.append(f"PATIENT COMPLAINTS: {clinical['complaints']}")
        lines.append("")
        
        # Structural Findings
        findings = case_card.get('structural_findings', {})
        lines.append("STRUCTURAL FINDINGS:")
        if findings.get('obstruction_sites'):
            lines.append(f"  Obstruction Sites: {findings['obstruction_sites']}")
        if findings.get('tongue_position'):
            lines.append(f"  Tongue Position: {findings['tongue_position']}")
        if findings.get('bite_structure'):
            lines.append(f"  Bite Structure: {findings['bite_structure']}")
        if findings.get('soft_palate_uvula'):
            lines.append(f"  Soft Palate/Uvula: {findings['soft_palate_uvula']}")
        lines.append("")
        
        # Device Design
        design = case_card.get('device_design', {})
        lines.append("DEVICE DESIGN REQUIREMENTS:")
        lines.append(f"  Context: {design.get('design_context', 'N/A')}")
        if design.get('mandibular_advancement'):
            lines.append(f"  Mandibular Advancement: {design['mandibular_advancement']}")
        if design.get('preset_mm'):
            lines.append(f"  Preset: {design['preset_mm']} mm")
        if design.get('vertical_opening'):
            lines.append(f"  Vertical Opening: {design['vertical_opening']}")
        if design.get('anterior_window'):
            lines.append(f"  Anterior Window: {design['anterior_window']}")
        if design.get('material'):
            lines.append(f"  Material: {design['material']}")
        if design.get('clinical_notes'):
            lines.append(f"  Clinical Notes: {design['clinical_notes']}")
        lines.append("")
        
        # Device Options
        options = case_card.get('device_options', [])
        if options:
            lines.append("RECOMMENDED DEVICE OPTIONS:")
            for i, opt in enumerate(options, 1):
                lines.append(f"  {i}. {opt.get('device_name', 'N/A')}")
                if opt.get('device_family'):
                    lines.append(f"     Family: {opt['device_family']}")
                if opt.get('key_features'):
                    lines.append(f"     Features: {opt['key_features']}")
            lines.append("")
        
        # Clustering Features
        clustering = case_card.get('clustering_features', {})
        lines.append("CLUSTERING FEATURES (for similarity matching):")
        for key, value in clustering.items():
            if value:
                lines.append(f"  {key}: {value}")
        
        lines.append("")
        lines.append("=" * 80)
        
        return "\n".join(lines)
    
    def generate_case_card_json(self, case_card: Dict[str, Any]) -> str:
        """Convert case card to JSON format"""
        return json.dumps(case_card, indent=2, ensure_ascii=False)
