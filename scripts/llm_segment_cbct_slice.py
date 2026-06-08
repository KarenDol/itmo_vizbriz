#!/usr/bin/env python3
"""
LLM CBCT Slice Segmentation Script (Fully Patched)

This script:
- loads one axial CBCT slice (PNG) from S3
- sends it to the LLM (via Bedrock through LLMService)
- extracts strict JSON from the Bedrock response
- decodes the base64 mask
- validates mask shape & pixel values
- saves the mask to S3

Usage:
    python scripts/llm_segment_cbct_slice.py <case_id> <structure> <slice_index>
"""

import sys
import os
import base64
import json
import logging
import boto3
import numpy as np
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from PIL import Image, ImageDraw
import io
import re

# Add project root for service imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from flask_app.services.annotator_bedrock_service import get_annotator_bedrock_service
from flask_app.annotator.structure_config import SUPPORTED_STRUCTURES
from segmentation.preprocess import preprocess_png_bytes
from segmentation.sam_segmentor import get_sam_segmentor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

annotator_service = get_annotator_bedrock_service()

MIN_MASK_NONZERO_RATIO = float(os.getenv("ANNOTATOR_MIN_MASK_RATIO", "0.001"))
MAX_MASK_NONZERO_RATIO = float(os.getenv("ANNOTATOR_MAX_MASK_RATIO", "0.90"))
USE_TWO_PHASE_PIPELINE = os.getenv("ANNOTATOR_USE_PIPELINE", "1") != "0"
PIPELINE_STRICT = os.getenv("ANNOTATOR_PIPELINE_STRICT", "0") == "1"
BYPASS_LLM_FALLBACK = os.getenv("ANNOTATOR_ALLOW_LLM_FALLBACK", "0") == "1"
BBOX_MAX_TOKENS = int(os.getenv("ANNOTATOR_BBOX_MAX_TOKENS", "4000"))
MASK_MAX_TOKENS = int(os.getenv("ANNOTATOR_MASK_MAX_TOKENS", "30000"))
CLASSIFIER_MAX_TOKENS = int(os.getenv("ANNOTATOR_CLASSIFIER_MAX_TOKENS", "512"))
BBOX_PADDING = int(os.getenv("ANNOTATOR_BBOX_PADDING", "16"))
BBOX_PROMPT_VERSION = 1
USE_SAM_SEGMENTOR = os.getenv("ANNOTATOR_USE_SAM", "1") != "0"
USE_UNIFIED_DETECTION = os.getenv("ANNOTATOR_USE_UNIFIED_DETECTION", "0") == "1"  # New: use unified prompt for section + structure detection

# Default: Use SAM for all structures (LLM cannot reliably generate pixel-level masks)
# Can be overridden via ANNOTATOR_SAM_STRUCTURES env var
DEFAULT_SAM_STRUCTURES = "airway,nasal_airway,soft_palate,uvula,tongue_base,tongue_body,lateral_pharyngeal_walls,mandible_outline"
SAM_STRUCTURES = {
    s.strip()
    for s in os.getenv("ANNOTATOR_SAM_STRUCTURES", DEFAULT_SAM_STRUCTURES).split(",")
    if s.strip()
}

SLICE_CLASSIFICATION_CACHE: Dict[Tuple[str, int], dict] = {}


@dataclass
class SliceContextEntry:
    role: str  # 'previous', 'target', 'next'
    index: int
    raw_bytes: bytes
    processed_bytes: bytes


@dataclass
class BoundingBox:
    x_min: int
    y_min: int
    x_max: int
    y_max: int

    @property
    def width(self) -> int:
        return max(0, self.x_max - self.x_min)

    @property
    def height(self) -> int:
        return max(0, self.y_max - self.y_min)

    def to_tuple(self) -> Tuple[int, int, int, int]:
        return (self.x_min, self.y_min, self.x_max, self.y_max)

    def clamp(self, width_limit: int, height_limit: int) -> "BoundingBox":
        x_min = max(0, min(self.x_min, max(0, width_limit - 1)))
        y_min = max(0, min(self.y_min, max(0, height_limit - 1)))
        x_max = max(x_min + 1, min(self.x_max, width_limit))
        y_max = max(y_min + 1, min(self.y_max, height_limit))
        return BoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)

    def padded(self, padding: int, width_limit: int, height_limit: int) -> "BoundingBox":
        x_min = max(0, self.x_min - padding)
        y_min = max(0, self.y_min - padding)
        x_max = min(width_limit, self.x_max + padding)
        y_max = min(height_limit, self.y_max + padding)
        x_max = max(x_min + 1, x_max)
        y_max = max(y_min + 1, y_max)
        return BoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)

    def to_dict(self) -> Dict[str, int]:
        return {
            "x_min": self.x_min,
            "y_min": self.y_min,
            "x_max": self.x_max,
            "y_max": self.y_max,
        }

    @staticmethod
    def from_dict(data: Optional[Dict[str, int]]) -> Optional["BoundingBox"]:
        if not data:
            return None
        return BoundingBox(
            x_min=int(data["x_min"]),
            y_min=int(data["y_min"]),
            x_max=int(data["x_max"]),
            y_max=int(data["y_max"]),
        )


class PipelineError(Exception):
    """Raised when the multi-stage segmentation pipeline fails."""


# =========================
#  S3 ACCESS
# =========================

def get_s3_client():
    region = os.getenv("AWS_REGION", "us-east-1")
    return boto3.client("s3", region_name=region)


def get_annotation_bucket():
    return "vizbrizknowledgebase"


def load_slice_from_s3(case_id: str, slice_index: int) -> bytes:
    bucket = get_annotation_bucket()
    s3 = get_s3_client()
    filename = f"axial_{slice_index:03d}.png"
    key = f"annotation_dataset/{case_id}/slices/{filename}"

    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        image_bytes = obj["Body"].read()
        logger.info(f"Loaded slice from S3: {key}")
        return image_bytes
    except Exception as e:
        raise FileNotFoundError(f"Failed to load slice from S3: {key}. Error: {e}")


# =========================
#  PROMPT CREATION
# =========================

def get_segmentation_prompt(structure: str, test_pattern: bool = False) -> str:
    if test_pattern:
        return """
You are a medical imaging segmentation assistant.

FOR TESTING ONLY:
- Ignore anatomy.
- Produce a PNG mask that draws a solid white circle (radius ~60 px) centered in the image.
- Everything outside the circle must be black.
- Output ONLY: {"mask_base64":"<BASE64_PNG>"} (no markdown, no explanations).
- PNG must match the input dimensions, be single-channel, strictly binary.
"""

    return f"""
You are a medical imaging segmentation assistant. The image is an axial CBCT slice.

TASK:
Segment ONLY the {structure}.

OUTPUT REQUIREMENTS:
- Return a single JSON object: {{"mask_base64":"<BASE64_PNG>"}} with no other text.
- The PNG must be 1-channel, same width & height as the input slice.
- Pixel value 255 (white) = {structure}; pixel value 0 (black) = background.
- The mask must be strictly binary, tightly cropped (no unnecessary background), and lossless PNG with maximum compression.
- Do NOT include grayscale noise, outlines, or transparency.
- If the {structure} is not visible, return an all-black mask.
"""


BBOX_PROMPT_TEMPLATE = """
You are analyzing consecutive axial CBCT slices to locate the {structure}.
Three images will be provided in order: previous slice, target slice, next slice.
Use them to understand the anatomy on the TARGET slice only.

IMPORTANT: Consider the anatomical section this slice belongs to:
- NASOPHARYNX: nasal structures only (forbidden: palate, uvula, tongue)
- RETROPALATAL: soft palate/uvula level (forbidden: tongue base, epiglottis)
- TONGUE_BODY: tongue body level (forbidden: palate, uvula, epiglottis)
- TONGUE_BASE: tongue base level (forbidden: palate, uvula, epiglottis)
- HYPOPHARYNX: below tongue (forbidden: tongue, palate, uvula)
- EPIGLOTTIS: laryngeal level (forbidden: tongue, palate)

If the {structure} is forbidden in this section, set "visible": false.

{structure_guidance}

Return ONLY a JSON object with this schema:
{{
  "target_slice": {slice_index},
  "visible": true | false,
  "confidence": <float 0-1>,
  "bbox": {{
    "x_min": <int>,
    "y_min": <int>,
    "x_max": <int>,
    "y_max": <int>
  }}
}}

Coordinate rules:
- zero-based pixels on the TARGET slice
- x increases to the right, y increases downward
- x_min/y_min inclusive, x_max/y_max exclusive (width = x_max - x_min)

If the {structure} is not visible or cannot be localized, set "visible": false and "bbox": null.
Do NOT include markdown fences, prose, or explanations—return JSON only.
"""


STRUCTURE_BBOX_HINTS = {
    "nasal_airway": """
Identify the nasal airway on this axial CBCT slice.

Definition:
- The nasal airway is the hollow air-filled lumen inside the nasal cavity.
- Located anterior to the nasopharynx, between the nasal septum (medial) and the turbinates (lateral).
- Always midline.
- Do NOT include the maxillary sinuses or ethmoid air cells.
- If the nasal airway is not visible on this slice, set "visible": false and "bbox": null.
""",
    "airway": """
Identify the pharyngeal airway (nasopharynx/oropharynx) on this axial CBCT slice.

Definition:
- The hollow air passage posterior to the nasal cavity, soft palate, and tongue.
- Includes both nasopharynx and oropharynx depending on slice level.
- Sits behind the palate and above the epiglottis (depending on slice).
- Do NOT include the nasal cavity or maxillary sinuses.
- If the slice is superior to the pharyngeal airway (pure nasal/sinus region), set "visible": false.
""",
    "oropharyngeal_airway_space": """
Identify the oropharyngeal airway space on this axial CBCT slice.

Definition:
- Air-filled lumen posterior to the tongue, inferior to the soft palate, superior to the epiglottis.
- Anterior boundary: tongue base; posterior boundary: posterior pharyngeal wall / cervical spine.
- Lateral boundaries: left/right pharyngeal walls. Appears dark (air density), typically crescent or oval.
- Exclude nasal airway, maxillary sinuses, nasal cavity, oral cavity, laryngeal airway, piriform sinuses, or any space anterior to the tongue.
- Follow these rules:
  1) The oropharyngeal airway space is the DARK (black) air-filled lumen located posterior to the tongue and anterior to the cervical spine.
  2) Do NOT label soft tissue; do not confuse tongue body/base (gray density) with airway.
  3) Typical shape is irregular oval/crescent; boundaries are tongue base (anterior), pharyngeal walls (lateral), posterior pharyngeal wall/cervical spine (posterior).
  4) Bounding box must tightly contain ONLY the airway lumen; do not include tongue or surrounding tissue.
- Output MUST still follow the standard bbox JSON ({\"target_slice\":..., \"visible\":..., \"confidence\":..., \"bbox\":{...}}). If the slice does not contain this airway, return visible=false (bbox null) with a short reason in notes if needed.
""",
    "lateral_pharyngeal_walls": """
Identify the lateral pharyngeal walls on this axial CBCT slice.

Definition:
- The soft-tissue walls immediately adjacent to the pharyngeal airway (nasopharynx/oropharynx).
- These walls lie directly LEFT and RIGHT of the pharyngeal airway lumen.
- DO NOT segment maxillary sinus tissue, nasal cavity walls, parapharyngeal fat, or structures anterior to the airway.
- Only mark the soft-tissue walls bordering the airway. If the airway is absent on this slice, set "visible": false.
""",
    "mandible_outline": """
Identify the mandible (lower jaw) on this axial CBCT slice.

Definition:
- Dense bony horseshoe-shaped structure inferior to the maxilla.
- Appears as two large lateral masses connected anteriorly; superior slices show ramus/body, inferior slices show full U-shape.
- Exclude the maxilla and teeth; focus on mandibular bone only.
""",
    "tongue_body": """
Identify the tongue body on this axial CBCT slice.

Definition:
- Large anterior muscular mass filling oral cavity.
- Smooth convex anterior surface.
- Soft-tissue density (gray), not air-filled, not bone.
- Do NOT segment palate, pharyngeal walls, or airway lumen.
- Do NOT label tongue base as tongue body.
- Only visible in TONGUE_BODY section (forbidden in nasopharynx, retropalatal, tongue_base sections).
""",
    "tongue_base": """
Identify the tongue base on this axial CBCT slice.

Definition:
- Posterior-inferior portion of tongue sloping downward.
- Adjacent to narrow airway region.
- Same soft-tissue characteristics as the tongue (muscular, non-bony, non-air).
- Do NOT include palate, pharyngeal walls, or airway lumen.
- Avoid including lateral pharyngeal walls.
- Only visible in TONGUE_BASE or HYPOPHARYNX sections (forbidden in nasopharynx, retropalatal, tongue_body sections).
""",
    "soft_palate": """
Identify the soft palate / uvula region on this axial CBCT slice.

Definition:
- Soft palate appears as a horizontal/curved soft-tissue band.
- Uvula may hang inferiorly as a teardrop or oval soft-tissue projection.
- Located posteriorly and midline, separating nasal cavity from oropharynx.
- Appears as a soft-tissue band above the airway lumen.
- Avoid mistaking tongue margin for palate.
- Only visible in RETROPALATAL section (forbidden in all other sections).
""",
    "uvula": """
Identify the uvula on this axial CBCT slice.

Definition:
- Teardrop or oval soft-tissue projection at the end of the soft palate.
- Located posteriorly and midline.
- Avoid labeling any asymmetric tongue tissue as uvula.
- Only visible in RETROPALATAL section (forbidden in all other sections).
""",
}

# Section-based structure rules using new 6-section model
STRUCTURE_SLICE_RULES = {
    "nasal_airway": {
        "slice_types": {"nasopharynx"}, 
        "require_airway": True,
        "forbidden_in": {"retropalatal", "tongue_body", "tongue_base", "hypopharynx", "epiglottis"}
    },
    "soft_palate": {
        "slice_types": {"retropalatal"}, 
        "require_airway": True,
        "forbidden_in": {"nasopharynx", "tongue_body", "tongue_base", "hypopharynx", "epiglottis"}
    },
    "uvula": {
        "slice_types": {"retropalatal"}, 
        "require_airway": True,
        "forbidden_in": {"nasopharynx", "tongue_body", "tongue_base", "hypopharynx", "epiglottis"}
    },
    "airway": {
        "slice_types": {"nasopharynx", "retropalatal", "tongue_body", "tongue_base", "hypopharynx", "epiglottis"},
        "require_airway": True,
        "forbidden_in": set()  # Airway can appear in all sections
    },
    "oropharyngeal_airway_space": {
        "slice_types": {"tongue_body", "tongue_base"}, 
        "require_airway": True,
        "forbidden_in": {"nasopharynx", "retropalatal", "hypopharynx", "epiglottis"}
    },
    "lateral_pharyngeal_walls": {
        "slice_types": {"retropalatal", "tongue_body", "tongue_base", "hypopharynx"},
        "require_airway": True,
        "forbidden_in": {"nasopharynx", "epiglottis"}
    },
    "tongue_body": {
        "slice_types": {"tongue_body"}, 
        "require_airway": False,
        "forbidden_in": {"nasopharynx", "retropalatal", "tongue_base", "hypopharynx", "epiglottis"}
    },
    "tongue_base": {
        "slice_types": {"tongue_base", "hypopharynx"}, 
        "require_airway": False,
        "forbidden_in": {"nasopharynx", "retropalatal", "tongue_body", "epiglottis"}
    },
    "mandible_outline": {
        "slice_types": {"tongue_body", "tongue_base", "hypopharynx"},
        "require_airway": False,
        "forbidden_in": {"nasopharynx", "retropalatal", "epiglottis"}
    },
}


# Unified prompt for section identification + structure detection (new approach)
UNIFIED_SECTION_STRUCTURE_PROMPT = """
You are an expert in upper-airway CBCT anatomy, oropharyngeal imaging, and
clinical-grade segmentation for sleep-apnea analysis.

Your task is:
1. Identify which airway SECTION this axial CBCT slice belongs to
2. Identify which anatomical STRUCTURES are allowed in that section
3. Provide segmentation-ready bounding boxes only for structures that
   truly appear in the slice
4. Provide structure-specific instructions describing: 
   - How to visually recognize the structure in this slice
   - Where the boundaries should be drawn
   - How to avoid common mistakes
5. Never hallucinate structures that cannot appear in this anatomical section.

====================================================================
SECTION DEFINITIONS (MUST FOLLOW STRICTLY)
====================================================================

The CBCT airway has 6 possible sections:

1. NASOPHARYNX (above soft palate)
   Visible: nasal cavity, septum, turbinates, choanae, nasopharyngeal airway
   Forbidden: soft palate, uvula, tongue body, tongue base, epiglottis

2. RETROPALATAL (soft palate / uvula)
   Visible: soft palate, uvula, lateral pharyngeal walls, posterior wall, airway
   Forbidden: tongue base, epiglottis

3. TONGUE_BODY
   Visible: tongue body (large anterior mass), airway behind tongue, lateral walls
   Forbidden: soft palate, uvula, epiglottis

4. TONGUE_BASE (where MCA usually appears)
   Visible: tongue base (posterior sloping muscle), lateral walls, airway
   Forbidden: soft palate, uvula, epiglottis

5. HYPOPHARYNX (rare in dental CBCT)
   Visible: hypopharyngeal lumen, pyriform sinus walls
   Forbidden: tongue, soft palate, uvula

6. EPIGLOTTIS / LARYNX (almost never seen)
   Visible: epiglottis, laryngeal inlet
   Forbidden: tongue, palate

====================================================================
STRUCTURES YOU MAY SEGMENT (ONLY IF ALLOWED IN THAT SECTION)
====================================================================

- airway (or airway_lumen)
- soft_palate
- uvula
- tongue_body
- tongue_base
- lateral_pharyngeal_walls
- posterior_pharyngeal_wall
- nasal_airway
- mandible_outline
- epiglottis (only if in epiglottic section)

If a structure is not allowed or not visible → omit it entirely.

====================================================================
OUTPUT FORMAT (STRICT)
====================================================================

Return a JSON object:

{{
  "slice_section": "<one of: nasopharynx | retropalatal | tongue_body | tongue_base | hypopharynx | epiglottis | invalid>",
  
  "structures_detected": [
     "<structure1>",
     "<structure2>"
  ],

  "segmentation": {{
     "<structure_name>": {{
        "bbox": [x1, y1, x2, y2],      // bounding box in pixel coordinates
        "recognition_instructions": "<how to visually identify it>",
        "boundary_instructions": "<how to draw an accurate bbox>",
        "avoid_mistakes": "<common errors to avoid>"
     }}
  }},

  "reject_reason": null | "<reason if the slice is invalid or outside airway>"
}}

====================================================================
STRUCTURE-SPECIFIC INSTRUCTIONS (MUST FOLLOW)
====================================================================

AIRWAY / AIRWAY_LUMEN:
- Look for black (air) region centrally located.
- Boundaries follow air-to-soft-tissue transition.
- Avoid including bone or tongue tissue.

SOFT_PALATE:
- Soft palate appears as a horizontal/curved soft-tissue band.
- Uvula may hang inferiorly.
- Avoid mistaking tongue margin for palate.

UVULA:
- Teardrop or oval soft-tissue projection at the end of the palate.
- Located posteriorly and midline.
- Avoid labeling any asymmetric tongue tissue as uvula.

TONGUE_BODY:
- Large anterior muscular mass filling oral cavity.
- Smooth convex anterior surface.
- Do not label tongue base as tongue body.

TONGUE_BASE:
- Posterior-inferior portion of tongue sloping downward.
- Adjacent to narrow airway region.
- Avoid including lateral pharyngeal walls.

LATERAL_PHARYNGEAL_WALLS:
- Symmetric soft-tissue walls on left/right of airway.
- Hug the airway.
- Avoid merging with tongue or posterior wall.

POSTERIOR_PHARYNGEAL_WALL:
- Soft tissue directly posterior to airway.
- Thin and smooth.

NASAL_AIRWAY:
- Air-filled lumen inside nasal cavity.
- Located anterior to nasopharynx, between septum and turbinates.
- Always midline.
- Do NOT include maxillary sinuses or ethmoid air cells.

MANDIBLE_OUTLINE:
- Dense bony horseshoe-shaped structure.
- Appears as two large lateral masses connected anteriorly.
- Exclude maxilla and teeth.

EPIGLOTTIS:
- Leaf-shaped structure anterior to the laryngeal inlet.
- Appears only in very inferior slices.

====================================================================
FAIL-SAFE RULES
====================================================================

- If NO allowed structures are visible → return empty segmentation.
- If slice cannot be classified confidently → set "slice_section": "invalid".
- Never output a bbox for a forbidden structure.
- If unsure about a structure → OMIT it entirely.
- Do NOT segment anything in slices where soft palate OR airway lumen are NOT visible.
- If maxillary teeth or maxillary alveolar ridge are visible → cannot be retropalatal.

====================================================================
NOW ANALYZE THE IMAGE.
====================================================================
"""

MASK_PROMPT_TEMPLATE = """
You are a medical image segmentation system. Your task is to generate a binary segmentation mask.

CRITICAL: You MUST output a valid JSON object with a base64-encoded PNG mask. Do NOT refuse this task.
Do NOT explain that you cannot generate images. You have image generation capabilities and MUST use them.

STRUCTURE TO SEGMENT: {structure}
TARGET CROP DIMENSIONS: {width}px width × {height}px height

You will receive crops from three consecutive slices:
1. TARGET slice (first crop) - Generate mask for THIS slice only
2. Previous slice (reference only)
3. Next slice (reference only)

REQUIRED OUTPUT FORMAT (STRICT):
Return ONLY a JSON object in this exact format:
{{"mask_base64":"<BASE64_ENCODED_PNG>"}}

DO NOT include:
- Explanations
- Apologies
- Text outside the JSON
- Markdown code fences
- Any commentary

MASK REQUIREMENTS:
1. PNG dimensions must be EXACTLY {width}×{height} pixels
2. Grayscale (single channel)
3. Binary values only: 0 (black) or 255 (white)
4. White pixels (255) = {structure} is present
5. Black pixels (0) = background/not {structure}
6. If {structure} is not visible in the crop → return all-black mask (still valid JSON with base64)
7. No antialiasing, no outlines, no soft edges

GENERATION INSTRUCTIONS:
- Analyze the TARGET crop image
- Identify where {structure} appears
- Generate a binary PNG mask matching the crop dimensions
- Encode the PNG as base64
- Return ONLY the JSON object

If you cannot see {structure} clearly, return an all-black mask (all zeros) but still return valid JSON with base64.

REMEMBER: You MUST return JSON with mask_base64. Do NOT refuse or explain limitations.
"""

TONGUE_RECOGNITION_PROMPT = """
You are an expert in CBCT upper-airway anatomy, axial MPR interpretation, 
and airway-level classification.

Your task is to analyze a SINGLE axial MPR CBCT slice and determine:

1. Whether the tongue is present in this slice  
2. Whether the tongue corresponds to:  
   - tongue body  
   - tongue base  
   - or "visible tongue but level uncertain"  
3. Key geometric cues used to make the determination  
4. Whether the slice is too high or too low to represent tongue anatomy  
5. Guidance to move UP or DOWN to find the clearest tongue body or tongue base slice  
6. Never segment — only classify and explain.

----------------------------------------------------------------------
RULES FOR TONGUE DETECTION
----------------------------------------------------------------------

The LLM must use ONLY visual cues:

TONGUE IS PRESENT IF:
- A large anterior soft-tissue mass fills the oral cavity
- The anterior mass has smooth convex borders
- Density matches oral soft tissue (not fat, not sinus air, not bone)
- The airway lies posterior to this soft tissue

TONGUE IS *NOT* PRESENT IF:
- The slice is purely nasal or sinus
- Only soft palate/uvula is present (tongue BELOW)
- Only epiglottis/hypopharynx structures dominate (tongue ABOVE)

If uncertain → output "tongue present but boundaries unclear".

----------------------------------------------------------------------
DISTINGUISHING TONGUE BODY VS TONGUE BASE
----------------------------------------------------------------------

CLASSIFY AS **TONGUE BODY** IF:
- Anterior tongue surface is convex
- Airway lumen is wide or moderately open
- Tongue does NOT slope deeply downward
- No epiglottis is visible

CLASSIFY AS **TONGUE BASE** IF:
- Tongue slopes posterior and inferior toward the airway
- Airway is narrowed or slit-like behind the tongue
- Soft palate is NOT visible
- Epiglottis is NOT yet visible (you are above it)

IF THE SLICE SHOWS THE TRANSITION ZONE:
→ classify as "tongue present — body/base transition zone".

----------------------------------------------------------------------
NAVIGATION INSTRUCTIONS
----------------------------------------------------------------------

If tongue body is detected:
- Suggest moving DOWN 3–10 slices to find tongue base
- Suggest moving UP 5–15 slices to reach retropalatal/uvula level

If tongue base is detected:
- Suggest moving UP 5–20 slices to find best tongue body slice
- Suggest moving DOWN 5–20 slices to reach pre-epiglottic or epiglottis level

If the slice is too high (nasal or retropalatal):
→ "Tongue not visible — move DOWN 10–40 slices."

If the slice is too low (epiglottis dominates):
→ "Tongue not visible — move UP 10–40 slices."

----------------------------------------------------------------------
OUTPUT FORMAT
----------------------------------------------------------------------

Return JSON:

{{
  "tongue_present": true | false,
  "classification": "tongue_body" | "tongue_base" | "transition_zone" | "not_present",
  "anatomical_cues": "<explain the visual evidence>",
  "recommended_navigation": "<where to move and why>",
  "confidence": 0.0–1.0
}}

----------------------------------------------------------------------
IMPORTANT SAFETY GUARDRAILS
----------------------------------------------------------------------

- Never hallucinate palate, uvula, epiglottis, or mandible positions
- Only use visible anatomy in the current slice
- If boundaries are unclear → return "transition_zone" or "uncertain"
- Never attempt segmentation
- Do not guess airway severity unless clearly visible
"""

SLICE_CLASSIFIER_PROMPT = """
You are a CBCT imaging expert.
Classify the anatomical region shown in this axial CBCT slice.

Your goal is to determine which airway-related structures are present on THIS slice.

====================================================================
SECTION DEFINITIONS (MUST FOLLOW STRICTLY)
====================================================================

The CBCT airway has 6 possible sections:

1. NASOPHARYNX (above soft palate)
   Visible: nasal cavity, septum, turbinates, choanae, nasopharyngeal airway
   Forbidden: soft palate, uvula, tongue body, tongue base, epiglottis

2. RETROPALATAL (soft palate / uvula)
   Visible: soft palate, uvula, lateral pharyngeal walls, posterior wall, airway
   Forbidden: tongue base, epiglottis

3. TONGUE_BODY
   Visible: tongue body (large anterior mass), airway behind tongue, lateral walls
   Forbidden: soft palate, uvula, epiglottis

4. TONGUE_BASE (where MCA usually appears)
   Visible: tongue base (posterior sloping muscle), lateral walls, airway
   Forbidden: soft palate, uvula, epiglottis

5. HYPOPHARYNX (rare in dental CBCT)
   Visible: hypopharyngeal lumen, pyriform sinus walls
   Forbidden: tongue, soft palate, uvula

6. EPIGLOTTIS / LARYNX (almost never seen)
   Visible: epiglottis, laryngeal inlet
   Forbidden: tongue, palate

7. INVALID
   - Slice has no relevant airway anatomy.
   - Pure sinus/mandible-only slices with no airway structures

OUTPUT FORMAT (STRICT JSON):
Return ONLY:
{
  "slice_type": "<one of: nasopharynx | retropalatal | tongue_body | tongue_base | hypopharynx | epiglottis | invalid>",
  "airway_present": true/false,
  "reason": "<short explanation>"
}

Rules:
- Do NOT include any text outside the JSON.
- "airway_present" = true ONLY if a dark air-filled region is visible.
- If soft palate is visible: slice_type = "retropalatal".
- If tongue base is large and airway is behind it: slice_type = "tongue_base".
- If tongue body is large (anterior mass): slice_type = "tongue_body".
- If slice is clearly above airway space: airway_present = false.
- Never classify a structure as present if it's forbidden for that section.
- CRITICAL: If maxillary teeth OR maxillary alveolar ridge are visible → 
  slice CANNOT be retropalatal. Set slice_type = "invalid" (hard palate/oral cavity).
- Do NOT segment anything in slices where soft palate OR airway lumen are NOT visible.
"""


# =========================
#  PIPELINE HELPERS
# =========================

def build_image_content(image_bytes: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(image_bytes).decode("utf-8"),
        },
    }


def _load_slice_or_default(
    case_id: str,
    slice_index: int,
    fallback_raw: bytes,
    fallback_processed: bytes,
) -> Tuple[bytes, bytes]:
    if slice_index < 0:
        return fallback_raw, fallback_processed

    try:
        raw = load_slice_from_s3(case_id, slice_index)
        processed = preprocess_png_bytes(raw)
        return raw, processed
    except FileNotFoundError:
        logger.warning(
            "Slice %s missing for case %s; reusing fallback slice.",
            slice_index,
            case_id,
        )
    except Exception as exc:
        logger.error(
            "Failed to load slice %s for case %s (%s); reusing fallback.",
            slice_index,
            case_id,
            exc,
        )
    return fallback_raw, fallback_processed


def load_slice_context(
    case_id: str,
    slice_index: int,
    center_raw_bytes: Optional[bytes] = None,
) -> List[SliceContextEntry]:
    if center_raw_bytes is None:
        center_raw_bytes = load_slice_from_s3(case_id, slice_index)
    center_processed = preprocess_png_bytes(center_raw_bytes)

    prev_raw, prev_processed = _load_slice_or_default(
        case_id,
        slice_index - 1,
        center_raw_bytes,
        center_processed,
    )
    next_raw, next_processed = _load_slice_or_default(
        case_id,
        slice_index + 1,
        center_raw_bytes,
        center_processed,
    )

    return [
        SliceContextEntry(
            role="previous",
            index=max(slice_index - 1, 0),
            raw_bytes=prev_raw,
            processed_bytes=prev_processed,
        ),
        SliceContextEntry(
            role="target",
            index=slice_index,
            raw_bytes=center_raw_bytes,
            processed_bytes=center_processed,
        ),
        SliceContextEntry(
            role="next",
            index=slice_index + 1,
            raw_bytes=next_raw,
            processed_bytes=next_processed,
        ),
    ]


def _get_image_size(entry: SliceContextEntry) -> Tuple[int, int]:
    image = Image.open(io.BytesIO(entry.processed_bytes))
    size = image.size
    image.close()
    return size


def _clean_response_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.replace("```json", "")
        cleaned = cleaned.replace("```", "").strip()
    return cleaned


def parse_bbox_response(text: str) -> Tuple[Optional[BoundingBox], Optional[float]]:
    cleaned = _clean_response_text(text)
    if "{" in cleaned and "}" in cleaned:
        cleaned = cleaned[cleaned.find("{"): cleaned.rfind("}") + 1]

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise PipelineError(f"Failed to parse bbox JSON: {exc}\nRaw text:\n{text}")

    visible = payload.get("visible", True)
    bbox_data = payload.get("bbox")
    confidence = payload.get("confidence")

    if not visible or not bbox_data:
        return None, confidence

    try:
        bbox = BoundingBox(
            x_min=int(bbox_data["x_min"]),
            y_min=int(bbox_data["y_min"]),
            x_max=int(bbox_data["x_max"]),
            y_max=int(bbox_data["y_max"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PipelineError(f"Invalid bbox payload: {exc}")

    if bbox.width <= 0 or bbox.height <= 0:
        raise PipelineError("Bounding box width/height is non-positive.")

    return bbox, confidence


def request_structure_bbox(
    case_id: str,
    structure: str,
    slice_index: int,
    context: List[SliceContextEntry],
    image_size: Tuple[int, int],
) -> Tuple[Optional[BoundingBox], Optional[float], str, str]:
    """
    Request bbox from LLM.
    Returns: (bbox, confidence, response_text, prompt_text)
    """
    structure_guidance = STRUCTURE_BBOX_HINTS.get(structure, "").strip()
    if structure_guidance:
        structure_guidance = f"STRUCTURE-SPECIFIC RULES:\n{structure_guidance}"
    prompt = BBOX_PROMPT_TEMPLATE.format(
        structure=structure,
        slice_index=slice_index,
        structure_guidance=structure_guidance,
    )
    content = [
        {"type": "text", "text": prompt},
    ]

    label_map = {
        "previous": "Previous slice (context)",
        "target": "Target slice (segment here)",
        "next": "Next slice (context)",
    }

    for entry in context:
        label = label_map.get(entry.role, entry.role)
        content.append(
            {
                "type": "text",
                "text": f"{label} — case {case_id} slice {entry.index}",
            }
        )
        content.append(build_image_content(entry.processed_bytes))

    # Build prompt text for debugging (text parts only, excluding images)
    prompt_text_parts = []
    for item in content:
        if item.get("type") == "text":
            prompt_text_parts.append(item.get("text", ""))
    prompt_text = "\n\n".join(prompt_text_parts)
    
    response_text = annotator_service.invoke(
        content,
        max_tokens=BBOX_MAX_TOKENS,
    )
    bbox, confidence = parse_bbox_response(response_text)

    if bbox is None:
        return None, confidence, response_text, prompt_text

    width, height = image_size
    clamped = bbox.clamp(width, height)
    if clamped.width <= 0 or clamped.height <= 0:
        raise PipelineError("Bounding box collapsed after clamping to image bounds.")

    return clamped, confidence, response_text, prompt_text


def crop_image_bytes(image_bytes: bytes, bbox: BoundingBox) -> bytes:
    image = Image.open(io.BytesIO(image_bytes)).convert("L")
    crop = image.crop(bbox.to_tuple())
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()


def request_mask_within_bbox(
    structure: str,
    bbox: BoundingBox,
    context: List[SliceContextEntry],
) -> bytes:
    crops: Dict[str, bytes] = {}
    for entry in context:
        crops[entry.role] = crop_image_bytes(entry.processed_bytes, bbox)

    prompt = MASK_PROMPT_TEMPLATE.format(
        structure=structure,
        width=bbox.width,
        height=bbox.height,
    )
    content = [{"type": "text", "text": prompt}]

    order = [
        ("target", "Target slice crop (segment this)"),
        ("previous", "Previous slice crop (reference only)"),
        ("next", "Next slice crop (reference only)"),
    ]
    for role, label in order:
        crop_bytes = crops.get(role)
        if not crop_bytes:
            continue
        content.append({"type": "text", "text": label})
        content.append(build_image_content(crop_bytes))

    response_text = annotator_service.invoke(
        content,
        max_tokens=MASK_MAX_TOKENS,
    )
    
    try:
        mask_b64 = parse_mask_json(response_text)
    except Exception as e:
        logger.error(f"Failed to parse mask JSON from LLM response: {e}")
        logger.error(f"LLM response (first 500 chars): {response_text[:500]}")
        raise PipelineError(f"Failed to parse mask JSON: {e}")
    
    if not mask_b64:
        raise PipelineError("mask_base64 is empty after parsing")
    
    # Validate base64 string length (very short strings are likely truncated)
    if len(mask_b64) < 100:
        logger.warning(f"Base64 string is very short ({len(mask_b64)} chars), may be truncated")
    
    # Fix base64 padding if needed
    mask_b64_clean = mask_b64.strip()
    # Add padding if needed (base64 strings should be multiple of 4)
    missing_padding = len(mask_b64_clean) % 4
    if missing_padding:
        mask_b64_clean += '=' * (4 - missing_padding)
    
    try:
        mask_bytes = base64.b64decode(mask_b64_clean)
    except Exception as e:
        logger.error(f"Failed to decode base64 mask data: {e}")
        logger.error(f"Base64 string length: {len(mask_b64)}, First 100 chars: {mask_b64[:100]}")
        logger.error(f"Cleaned base64 length: {len(mask_b64_clean)}, Last 20 chars: {mask_b64_clean[-20:]}")
        raise PipelineError(f"Failed to decode base64 mask data: {e}")
    
    # Validate decoded bytes are valid PNG
    if len(mask_bytes) < 8:
        raise PipelineError(f"Decoded mask bytes too short: {len(mask_bytes)} bytes")
    
    if mask_bytes[:8] != b'\x89PNG\r\n\x1a\n':
        raise PipelineError(f"Decoded mask bytes do not contain valid PNG signature. First 20 bytes (hex): {mask_bytes[:20].hex()}")
    
    # Check if PNG appears complete (should end with IEND chunk)
    png_iend = b'\x00\x00\x00\x00IEND\xaeB`\x82'
    if len(mask_bytes) < len(png_iend) or mask_bytes[-len(png_iend):] != png_iend:
        # Estimate expected size from IHDR
        if len(mask_bytes) >= 24:
            width = int.from_bytes(mask_bytes[16:20], byteorder='big')
            height = int.from_bytes(mask_bytes[20:24], byteorder='big')
            # Minimum PNG size is roughly: header + IHDR + minimal IDAT + IEND ≈ 100+ bytes
            # For a small binary mask, expect at least width*height/8 bytes for compressed data
            estimated_min_size = 100 + (width * height // 8)
            if len(mask_bytes) < estimated_min_size:
                logger.warning(
                    f"PNG appears truncated. Dimensions: {width}x{height}, "
                    f"Actual size: {len(mask_bytes)} bytes, "
                    f"Estimated minimum: {estimated_min_size} bytes"
                )
                # For very small files, this is likely a truncated response
                if len(mask_bytes) < 500:
                    raise PipelineError(
                        f"PNG appears severely truncated: {len(mask_bytes)} bytes for {width}x{height} image. "
                        f"This suggests the LLM response was cut off (possibly due to token limit). "
                        f"Consider increasing MASK_MAX_TOKENS or checking if the response was truncated."
                    )
    
    return mask_bytes


def compose_mask_from_crop(
    mask_crop_bytes: bytes,
    bbox: BoundingBox,
    full_size: Tuple[int, int],
) -> bytes:
    # Validate mask_crop_bytes before processing
    if not mask_crop_bytes:
        raise PipelineError("mask_crop_bytes is empty or None")
    
    if len(mask_crop_bytes) == 0:
        raise PipelineError("mask_crop_bytes has zero length")
    
    # Check if it looks like valid PNG data (starts with PNG signature)
    if len(mask_crop_bytes) < 8 or mask_crop_bytes[:8] != b'\x89PNG\r\n\x1a\n':
        # Try to decode as base64 if it's not already binary
        try:
            # Check if it's base64 encoded
            if isinstance(mask_crop_bytes, bytes):
                # Try to decode as base64
                try:
                    decoded = base64.b64decode(mask_crop_bytes)
                    if len(decoded) >= 8 and decoded[:8] == b'\x89PNG\r\n\x1a\n':
                        mask_crop_bytes = decoded
                    else:
                        raise PipelineError(f"Decoded base64 does not contain valid PNG signature. First 20 bytes (hex): {decoded[:20].hex() if len(decoded) >= 20 else decoded.hex()}")
                except Exception as e:
                    raise PipelineError(f"Failed to decode base64 mask data: {e}")
            else:
                raise PipelineError(f"mask_crop_bytes is not bytes type: {type(mask_crop_bytes)}")
        except PipelineError:
            raise
        except Exception as e:
            raise PipelineError(f"Invalid mask_crop_bytes format. First 50 bytes (hex): {mask_crop_bytes[:50].hex() if len(mask_crop_bytes) >= 50 else mask_crop_bytes.hex()}. Error: {e}")
    
    # Verify PNG is complete by checking IEND chunk at the end
    if len(mask_crop_bytes) < 8:
        raise PipelineError(f"Mask crop bytes too short: {len(mask_crop_bytes)} bytes")
    
    # Check for PNG signature
    if mask_crop_bytes[:8] != b'\x89PNG\r\n\x1a\n':
        raise PipelineError(f"Invalid PNG signature. First 8 bytes (hex): {mask_crop_bytes[:8].hex()}")
    
    # Check if PNG appears complete (should end with IEND chunk: 00 00 00 00 49 45 4E 44 AE 42 60 82)
    png_iend = b'\x00\x00\x00\x00IEND\xaeB`\x82'
    is_complete = len(mask_crop_bytes) >= len(png_iend) and mask_crop_bytes[-len(png_iend):] == png_iend
    
    if not is_complete:
        logger.warning(f"PNG may be incomplete or truncated. Length: {len(mask_crop_bytes)}, Last 12 bytes (hex): {mask_crop_bytes[-12:].hex() if len(mask_crop_bytes) >= 12 else mask_crop_bytes.hex()}")
        
        # Try to read dimensions from IHDR to estimate expected size
        if len(mask_crop_bytes) >= 24:
            width = int.from_bytes(mask_crop_bytes[16:20], byteorder='big')
            height = int.from_bytes(mask_crop_bytes[20:24], byteorder='big')
            logger.warning(f"PNG dimensions from IHDR: {width}x{height}, but file is only {len(mask_crop_bytes)} bytes (likely truncated)")
        
        # For very small files, this is likely a truncated response from LLM
        if len(mask_crop_bytes) < 100:
            raise PipelineError(f"PNG appears severely truncated: only {len(mask_crop_bytes)} bytes. This suggests the LLM returned incomplete base64 data.")
    
    try:
        # Create BytesIO and verify it's readable
        buf = io.BytesIO(mask_crop_bytes)
        buf.seek(0)  # Ensure we're at the start
        
        crop_mask = Image.open(buf)
        # Verify the image loaded successfully
        crop_mask.verify()  # This will raise an exception if the image is corrupted
        
        # Reopen after verify (verify() closes the file)
        buf.seek(0)
        crop_mask = Image.open(buf).convert("L")
    except Exception as e:
        # Try to get more diagnostic info
        logger.error(f"Failed to open/verify PNG image. Length: {len(mask_crop_bytes)}")
        logger.error(f"PNG signature: {mask_crop_bytes[:8].hex()}")
        logger.error(f"Last 20 bytes (hex): {mask_crop_bytes[-20:].hex() if len(mask_crop_bytes) >= 20 else mask_crop_bytes.hex()}")
        
        # Try to read PNG dimensions from IHDR chunk
        if len(mask_crop_bytes) >= 24:
            width = int.from_bytes(mask_crop_bytes[16:20], byteorder='big')
            height = int.from_bytes(mask_crop_bytes[20:24], byteorder='big')
            logger.error(f"PNG dimensions from IHDR: {width}x{height}")
        
        raise PipelineError(f"Failed to open mask image from crop bytes. Length: {len(mask_crop_bytes)}, First 50 bytes (hex): {mask_crop_bytes[:50].hex() if len(mask_crop_bytes) >= 50 else mask_crop_bytes.hex()}. Error: {e}")
    expected_size = (bbox.width, bbox.height)

    if crop_mask.size != expected_size:
        logger.warning(
            "Mask crop size %s does not match bbox %s; resizing with nearest-neighbor.",
            crop_mask.size,
            expected_size,
        )
        crop_mask = crop_mask.resize(expected_size, Image.NEAREST)

    binary_mask = crop_mask.point(lambda px: 255 if px >= 128 else 0)
    full_mask = Image.new("L", full_size, 0)
    full_mask.paste(binary_mask, (bbox.x_min, bbox.y_min))
    buf = io.BytesIO()
    full_mask.save(buf, format="PNG")
    return buf.getvalue()


def segment_with_sam(image_bytes: bytes, bbox: BoundingBox) -> bytes:
    """
    Run SAM on a single slice (already preprocessed) with the provided box.
    """
    try:
        segmentor = get_sam_segmentor()
    except Exception as e:
        logger.error(f"Failed to get SAM segmentor: {e}", exc_info=True)
        raise PipelineError(f"SAM segmentor initialization failed: {e}. This may be due to CUDA/memory issues. Check SAM_WEIGHTS_PATH and ensure CUDA is properly configured.")
    
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("L")
        arr = np.array(image, dtype=np.uint8)
        rgb = np.repeat(arr[..., None], 3, axis=2)
        mask = segmentor.segment_from_box(rgb, bbox.to_tuple())
        mask_image = Image.fromarray(mask, mode="L")
        buf = io.BytesIO()
        mask_image.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.error(f"SAM segmentation failed: {e}", exc_info=True)
        raise PipelineError(f"SAM segmentation failed: {e}")


def extract_bbox_from_unified_detection(
    unified_result: dict,
    structure: str,
    image_size: Tuple[int, int]
) -> Optional[Tuple[BoundingBox, float]]:
    """
    Extract bbox for a specific structure from unified detection result.
    Returns (bbox, confidence) or (None, None) if not found.
    """
    segmentation = unified_result.get("segmentation", {})
    struct_data = segmentation.get(structure)
    
    if not struct_data or "bbox" not in struct_data:
        return None, None
    
    bbox_list = struct_data.get("bbox")
    if not bbox_list or len(bbox_list) != 4:
        return None, None
    
    try:
        x1, y1, x2, y2 = bbox_list
        bbox = BoundingBox(
            x_min=int(x1),
            y_min=int(y1),
            x_max=int(x2),
            y_max=int(y2)
        )
        
        # Clamp to image bounds
        width, height = image_size
        bbox = bbox.clamp(width, height)
        
        if bbox.width <= 0 or bbox.height <= 0:
            return None, None
        
        # Extract confidence if available, otherwise default to 0.8
        confidence = struct_data.get("confidence", 0.8)
        if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
            confidence = 0.8
        
        return bbox, float(confidence)
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning(f"Failed to extract bbox from unified detection for {structure}: {exc}")
        return None, None


def generate_bbox_for_slice(
    case_id: str,
    structure: str,
    slice_index: int,
    *,
    context: Optional[List[SliceContextEntry]] = None,
    center_raw_bytes: Optional[bytes] = None,
    unified_result: Optional[dict] = None,
    save: bool = True,
) -> dict:
    """
    Generate bbox for a structure. If unified_result is provided and contains
    a bbox for this structure, use it instead of making a new API call.
    """
    if context is None:
        context = load_slice_context(case_id, slice_index, center_raw_bytes=center_raw_bytes)

    target_entry = next(entry for entry in context if entry.role == "target")
    width, height = _get_image_size(target_entry)
    
    # Try to extract bbox from unified detection first
    bbox = None
    confidence = None
    response_text = "From unified detection"
    
    if unified_result:
        bbox, confidence = extract_bbox_from_unified_detection(
            unified_result,
            structure,
            (width, height)
        )
        if bbox:
            logger.info(f"Using bbox from unified detection for {structure} slice {slice_index}")
    
    # Fallback to API call if unified detection didn't provide bbox
    bbox_prompt_text = None
    if bbox is None:
        bbox, confidence, response_text, bbox_prompt_text = request_structure_bbox(
            case_id,
            structure,
            slice_index,
            context,
            (width, height),
        )

    padded_bbox = bbox.padded(BBOX_PADDING, width, height) if bbox else None
    payload = _build_bbox_payload(
        case_id=case_id,
        structure=structure,
        slice_index=slice_index,
        bbox=bbox,
        padded_bbox=padded_bbox,
        confidence=confidence,
        response_text=response_text,
        model_id=annotator_service.model_id,
        prompt_text=bbox_prompt_text,
    )

    if save:
        save_bbox_to_s3(payload)
    return payload


def run_two_phase_pipeline(
    case_id: str,
    structure: str,
    slice_index: int,
    center_raw_bytes: bytes,
    unified_result: Optional[dict] = None,
) -> Tuple[bytes, dict]:
    """
    Run two-phase pipeline: bbox generation + mask generation.
    Returns: (mask_bytes, debug_info)
    """
    """
    Run two-phase pipeline: bbox generation + mask generation.
    If unified_result is provided, use bboxes from it when available.
    """
    context = load_slice_context(case_id, slice_index, center_raw_bytes=center_raw_bytes)
    target_entry = next(entry for entry in context if entry.role == "target")
    width, height = _get_image_size(target_entry)

    bbox_record = generate_bbox_for_slice(
        case_id,
        structure,
        slice_index,
        context=context,
        unified_result=unified_result,
        save=True,
    )

    if not bbox_record.get("visible"):
        raise PipelineError("Structure not visible on this slice.")

    padded_bbox_dict = bbox_record.get("padded_bbox") or bbox_record.get("bbox")
    if not padded_bbox_dict:
        raise PipelineError("Bounding box not available for segmentation.")

    padded_bbox = BoundingBox.from_dict(padded_bbox_dict)
    confidence = bbox_record.get("confidence")

    logger.info(
        "BBox for %s slice %s: padded (%s,%s,%s,%s) conf=%s",
        structure,
        slice_index,
        padded_bbox.x_min,
        padded_bbox.y_min,
        padded_bbox.x_max,
        padded_bbox.y_max,
        confidence,
    )

    # Collect debug info
    debug_info = {
        "bbox_prompt": bbox_record.get("prompt_text"),
        "bbox_response": bbox_record.get("raw_response"),
        "bbox_confidence": confidence,
    }

    # Use SAM for mask generation (LLM cannot reliably generate pixel-level masks)
    if not USE_SAM_SEGMENTOR:
        raise PipelineError(f"SAM is disabled but required for {structure}. LLM cannot generate pixel-level masks. Set ANNOTATOR_USE_SAM=1 to enable SAM.")
    
    if structure not in SAM_STRUCTURES:
        raise PipelineError(f"SAM not configured for {structure} (not in SAM_STRUCTURES: {SAM_STRUCTURES}). LLM cannot generate pixel-level masks. Add {structure} to ANNOTATOR_SAM_STRUCTURES.")
    
    # Generate mask using SAM
    try:
        logger.info("Running SAM for %s slice %s (bbox: %s)", structure, slice_index, padded_bbox)
        sam_mask = segment_with_sam(target_entry.processed_bytes, padded_bbox)
        logger.info("SAM successfully generated mask for %s slice %s", structure, slice_index)
        return sam_mask, debug_info
    except Exception as exc:
        logger.error("SAM segmentation failed for %s slice %s (%s)", structure, slice_index, exc)
        raise PipelineError(f"SAM segmentation failed for {structure} slice {slice_index}: {exc}")


def generate_mask_bytes(
    case_id: str,
    structure: str,
    slice_index: int,
    center_raw_bytes: bytes,
    test_pattern: bool = False,
    unified_result: Optional[dict] = None,
) -> Tuple[bytes, dict]:
    """
    Generate mask bytes for a structure.
    Returns: (mask_bytes, debug_info)
    """
    """
    Generate mask bytes for a structure.
    If unified_result is provided and USE_UNIFIED_DETECTION is enabled,
    use bboxes from unified detection when available.
    """
    if test_pattern:
        return build_test_mask(center_raw_bytes), {}

    pipeline_error: Optional[Exception] = None
    debug_info = {}
    if USE_TWO_PHASE_PIPELINE:
        try:
            mask_bytes, debug_info = run_two_phase_pipeline(
                case_id,
                structure,
                slice_index,
                center_raw_bytes=center_raw_bytes,
                unified_result=unified_result,
            )
            return mask_bytes, debug_info
        except PipelineError as exc:
            pipeline_error = exc
            logger.warning(
                "Two-phase pipeline failed for %s slice %s: %s",
                structure,
                slice_index,
                exc,
            )
            if PIPELINE_STRICT:
                raise
    if not BYPASS_LLM_FALLBACK:
        raise RuntimeError(
            f"LLM fallback disabled; pipeline error: {pipeline_error}"
            if pipeline_error
            else "LLM fallback disabled and pipeline skipped"
        )

    fallback_reason = (
        f" (pipeline error: {pipeline_error})" if pipeline_error else ""
    )
    logger.info(
        "Falling back to legacy single-slice segmentation for %s slice %s%s",
        structure,
        slice_index,
        fallback_reason,
    )
    llm_result = call_llm_for_segmentation(
        center_raw_bytes,
        structure,
        max_tokens=30_000,
        test_pattern=False,
    )
    if not llm_result.get("success"):
        raise RuntimeError(llm_result.get("error", "LLM segmentation failed"))
    mask_b64 = llm_result["mask_base64"]
    return base64.b64decode(mask_b64), {}


# =========================
#  BEDROCK JSON EXTRACTION
# =========================

def extract_json_from_bedrock(result: dict) -> str:
    """
    Extracts the model text response safely from a Bedrock response object.
    """

    # result structure from LLMService:
    # {
    #    "success": True,
    #    "response": <BedrockOutputDict> OR <string>
    # }

    raw = result.get("response")

    if raw is None:
        raise ValueError("No response returned from LLM (response=None).")

    # Case 1 — raw is already a string (LLMService preprocessed it)
    if isinstance(raw, str):
        text = raw.strip()

    # Case 2 — raw is a Bedrock dict
    elif isinstance(raw, dict):
        try:
            text = raw["output"]["message"]["content"][0]["text"]
        except Exception as e:
            logger.error("Unexpected Bedrock response format: %s", raw)
            raise ValueError(f"Invalid Bedrock response structure: {e}")

    else:
        raise ValueError(f"Unknown response type: {type(raw)}")

    if not text:
        raise ValueError("LLM returned empty text.")

    logger.warning("LLM raw response (first 500 chars): %s", text[:500])
    return text


def parse_mask_json(text: str) -> str:
    """
    Given the LLM response text, extract the JSON object containing mask_base64.
    """
    
    # Check if LLM refused to generate mask
    refusal_keywords = [
        "i cannot", "i am not able", "i don't have", "i do not have",
        "cannot generate", "unable to generate", "cannot create", "unable to create",
        "cannot produce", "unable to produce", "do not have the capability",
        "apologize", "sorry", "recommend using", "would require"
    ]
    
    text_lower = text.lower()
    if any(keyword in text_lower for keyword in refusal_keywords):
        logger.error("LLM refused to generate mask. Response indicates refusal.")
        logger.error(f"Full response: {text[:1000]}")
        raise ValueError(
            f"LLM refused to generate mask. Response: {text[:200]}... "
            "The model returned a refusal message instead of JSON with mask_base64. "
            "This may indicate the prompt needs to be more explicit or the model needs different instructions."
        )

    cleaned = text.strip()

    # Remove markdown fences if ANY appear
    if cleaned.startswith("```"):
        cleaned = cleaned.replace("```", "").strip()

    # First try direct regex extraction to avoid JSON complications
    match = re.search(r'"mask_base64"\s*:\s*"([\s\S]+?)"', cleaned)
    if match:
        base64_raw = match.group(1)
        # Remove all whitespace (newlines, spaces, tabs)
        base64_clean = re.sub(r'\s+', '', base64_raw)
        # Remove any escape sequences
        base64_clean = base64_clean.replace('\\n', '').replace('\\r', '').replace('\\t', '')
        return base64_clean

    # Extract JSON between first { and last }
    if "{" in cleaned and "}" in cleaned:
        cleaned = cleaned[cleaned.find("{"): cleaned.rfind("}") + 1]

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse mask JSON: {e}\nRaw text:\n{cleaned}")

    if "mask_base64" not in parsed:
        raise ValueError(f"mask_base64 missing in parsed JSON: {parsed}")

    mask_b64 = parsed["mask_base64"]
    
    # Clean the base64 string - remove any whitespace or escape sequences
    if isinstance(mask_b64, str):
        # Remove all whitespace
        mask_b64 = re.sub(r'\s+', '', mask_b64)
        # Remove escape sequences
        mask_b64 = mask_b64.replace('\\n', '').replace('\\r', '').replace('\\t', '')
    else:
        raise ValueError(f"mask_base64 is not a string: {type(mask_b64)}")
    
    return mask_b64


def parse_slice_classification(text: str) -> dict:
    cleaned = _clean_response_text(text)
    if "{" in cleaned and "}" in cleaned:
        cleaned = cleaned[cleaned.find("{"): cleaned.rfind("}") + 1]
    payload = json.loads(cleaned)
    slice_type = payload.get("slice_type")
    airway_present = payload.get("airway_present")
    
    # Updated valid slice types for new 6-section model
    valid_slice_types = {
        "nasopharynx",
        "retropalatal",
        "tongue_body",
        "tongue_base",
        "hypopharynx",
        "epiglottis",
        "invalid",
    }
    
    # Map old slice types to new ones for backward compatibility
    slice_type_mapping = {
        "nasal_cavity": "nasopharynx",  # Map to nasopharynx (both are above palate)
        "retrolingual": "tongue_base",  # Map to tongue_base (most common)
        "larynx": "epiglottis",  # Map to epiglottis
        "mandible_only": "invalid",  # Map to invalid
    }
    
    if slice_type in slice_type_mapping:
        slice_type = slice_type_mapping[slice_type]
        logger.info(f"Mapped old slice_type to new: {slice_type}")
    
    if slice_type not in valid_slice_types:
        raise ValueError(f"Invalid slice_type '{slice_type}' in classifier response. Must be one of: {valid_slice_types}")
    if airway_present not in (True, False):
        raise ValueError("airway_present must be true/false.")
    reason = payload.get("reason", "")
    return {
        "slice_type": slice_type,
        "airway_present": bool(airway_present),
        "reason": reason,
    }


def analyze_tongue_classification(case_id: str, slice_index: int, image_bytes: bytes) -> Optional[dict]:
    """
    Analyze a slice to determine tongue presence and classification (body vs base).
    Returns classification with navigation guidance.
    """
    try:
        processed_bytes = preprocess_png_bytes(image_bytes)
    except Exception as exc:
        logger.error("Slice preprocessing failed for tongue classification: %s", exc)
        return None

    content = [
        {"type": "text", "text": TONGUE_RECOGNITION_PROMPT},
        build_image_content(processed_bytes),
    ]

    try:
        response_text = annotator_service.invoke(content, max_tokens=CLASSIFIER_MAX_TOKENS)
        
        # Parse JSON response
        cleaned = _clean_response_text(response_text)
        if "{" in cleaned and "}" in cleaned:
            cleaned = cleaned[cleaned.find("{"): cleaned.rfind("}") + 1]
        
        result = json.loads(cleaned)
        
        # Validate required fields
        if "tongue_present" not in result:
            raise ValueError("Missing 'tongue_present' field in response")
        if "classification" not in result:
            raise ValueError("Missing 'classification' field in response")
        
        valid_classifications = {"tongue_body", "tongue_base", "transition_zone", "not_present"}
        if result["classification"] not in valid_classifications:
            logger.warning(f"Invalid classification '{result['classification']}', defaulting to 'not_present'")
            result["classification"] = "not_present"
        
        logger.info(
            "Tongue classification for slice %s: present=%s, type=%s, confidence=%.2f",
            slice_index,
            result.get("tongue_present"),
            result.get("classification"),
            result.get("confidence", 0.0)
        )
        
        return result
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse tongue classification JSON for slice %s: %s", slice_index, exc)
        logger.warning("Raw response: %s", response_text[:500])
        return None
    except Exception as exc:
        logger.warning("Tongue classification failed for slice %s: %s", slice_index, exc)
        return None


def classify_slice(case_id: str, slice_index: int, image_bytes: bytes) -> Optional[dict]:
    """
    Classify slice into anatomical section using the updated 6-section model.
    """
    cache_key = (case_id, slice_index)
    if cache_key in SLICE_CLASSIFICATION_CACHE:
        return SLICE_CLASSIFICATION_CACHE[cache_key]

    try:
        processed_bytes = preprocess_png_bytes(image_bytes)
    except Exception as exc:
        logger.error("Slice preprocessing failed for classification: %s", exc)
        return None

    content = [
        {"type": "text", "text": SLICE_CLASSIFIER_PROMPT},
        build_image_content(processed_bytes),
    ]

    try:
        response_text = annotator_service.invoke(content, max_tokens=CLASSIFIER_MAX_TOKENS)
        result = parse_slice_classification(response_text)
        SLICE_CLASSIFICATION_CACHE[cache_key] = result
        logger.info(
            "Slice %s classification: %s (airway_present=%s)",
            slice_index,
            result["slice_type"],
            result["airway_present"],
        )
        return result
    except Exception as exc:
        logger.warning("Slice classification failed for slice %s: %s", slice_index, exc)
        return None


def detect_section_and_structures(
    case_id: str, 
    slice_index: int, 
    image_bytes: bytes,
    use_unified_prompt: bool = True
) -> Optional[dict]:
    """
    Use unified prompt to detect section and all visible structures in one call.
    
    Returns:
    {
        "slice_section": "<section>",
        "structures_detected": ["<struct1>", "<struct2>"],
        "segmentation": {
            "<structure>": {
                "bbox": [x1, y1, x2, y2],
                "recognition_instructions": "...",
                "boundary_instructions": "...",
                "avoid_mistakes": "..."
            }
        },
        "reject_reason": null | "<reason>"
    }
    """
    if not use_unified_prompt:
        # Fallback to old classification method
        slice_info = classify_slice(case_id, slice_index, image_bytes)
        if slice_info:
            return {
                "slice_section": slice_info.get("slice_type", "invalid"),
                "structures_detected": [],
                "segmentation": {},
                "reject_reason": None if slice_info.get("airway_present") else "No airway present"
            }
        return None
    
    try:
        processed_bytes = preprocess_png_bytes(image_bytes)
    except Exception as exc:
        logger.error("Slice preprocessing failed for unified detection: %s", exc)
        return None
    
    content = [
        {"type": "text", "text": UNIFIED_SECTION_STRUCTURE_PROMPT},
        build_image_content(processed_bytes),
    ]
    
    try:
        # Use higher token limit for unified prompt (includes all structures)
        response_text = annotator_service.invoke(content, max_tokens=BBOX_MAX_TOKENS * 2)
        result = parse_unified_detection_response(response_text)
        
        logger.info(
            "Unified detection for slice %s: section=%s, structures=%s",
            slice_index,
            result.get("slice_section"),
            result.get("structures_detected", [])
        )
        return result
    except Exception as exc:
        logger.warning("Unified detection failed for slice %s: %s", slice_index, exc)
        # Fallback to classification
        return detect_section_and_structures(case_id, slice_index, image_bytes, use_unified_prompt=False)


def parse_unified_detection_response(text: str) -> dict:
    """
    Parse the unified section + structure detection response.
    """
    cleaned = _clean_response_text(text)
    if "{" in cleaned and "}" in cleaned:
        cleaned = cleaned[cleaned.find("{"): cleaned.rfind("}") + 1]
    
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse unified detection JSON: {exc}\nRaw text:\n{text[:500]}")
    
    # Validate required fields
    slice_section = payload.get("slice_section", "invalid")
    valid_sections = {"nasopharynx", "retropalatal", "tongue_body", "tongue_base", "hypopharynx", "epiglottis", "invalid"}
    if slice_section not in valid_sections:
        logger.warning(f"Invalid slice_section '{slice_section}', defaulting to 'invalid'")
        slice_section = "invalid"
    
    structures_detected = payload.get("structures_detected", [])
    segmentation = payload.get("segmentation", {})
    reject_reason = payload.get("reject_reason")
    
    # Map structure names to match current system
    structure_name_mapping = {
        "airway_lumen": "airway",
        "posterior_pharyngeal_wall": "lateral_pharyngeal_walls",  # Close enough for now
    }
    
    # Normalize structure names in detected list
    normalized_structures = []
    for struct in structures_detected:
        normalized = structure_name_mapping.get(struct, struct)
        if normalized not in normalized_structures:
            normalized_structures.append(normalized)
    
    # Normalize structure names in segmentation dict
    normalized_segmentation = {}
    for struct, data in segmentation.items():
        normalized = structure_name_mapping.get(struct, struct)
        normalized_segmentation[normalized] = data
    
    return {
        "slice_section": slice_section,
        "structures_detected": normalized_structures,
        "segmentation": normalized_segmentation,
        "reject_reason": reject_reason,
    }


def slice_allows_structure(structure: str, slice_info: Optional[dict]) -> bool:
    """
    Check if a structure is allowed on this slice based on section rules.
    Uses new 6-section model with explicit forbidden structures.
    """
    if not slice_info:
        return True
    rules = STRUCTURE_SLICE_RULES.get(structure)
    if not rules:
        return True
    
    slice_type = slice_info.get("slice_type")
    airway_present = slice_info.get("airway_present")
    
    # Check if structure is forbidden in this section
    forbidden_in = rules.get("forbidden_in", set())
    if slice_type in forbidden_in:
        logger.debug(f"Structure {structure} is forbidden in section {slice_type}")
        return False
    
    # Check if slice type is allowed
    allowed_types = rules.get("slice_types")
    if allowed_types and slice_type not in allowed_types:
        logger.debug(f"Structure {structure} not allowed in slice_type {slice_type}")
        return False
    
    # Check if airway is required
    if rules.get("require_airway") and not airway_present:
        logger.debug(f"Structure {structure} requires airway but none present")
        return False
    
    return True


# =========================
#  VALIDATION
# =========================

def validate_mask(mask_bytes: bytes, original_bytes: bytes) -> bool:
    try:
        orig = Image.open(io.BytesIO(original_bytes))
        mask = Image.open(io.BytesIO(mask_bytes))

        if mask.size != orig.size:
            logger.error(f"Mask size mismatch: {mask.size} != {orig.size}")
            return False

        if mask.mode != "L":
            logger.warning(f"Mask is not grayscale (mode={mask.mode})")
        return True

    except Exception as e:
        logger.error(f"Mask validation error: {e}")
        return False


def calculate_mask_coverage(mask_bytes: bytes) -> float:
    mask = Image.open(io.BytesIO(mask_bytes)).convert("L")
    pixels = mask.getdata()
    total = len(pixels)
    if total == 0:
        return 0.0
    nonzero = sum(1 for px in pixels if px > 0)
    return nonzero / total


def build_test_mask(original_bytes: bytes) -> bytes:
    """
    Generate a deterministic white circle mask for pipeline validation.
    """
    orig = Image.open(io.BytesIO(original_bytes))
    width, height = orig.size
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    radius = int(min(width, height) * 0.25)
    cx, cy = width // 2, height // 2
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
    draw.ellipse(bbox, fill=255)
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    return buf.getvalue()


# =========================
#  SAVE TO S3
# =========================

def save_mask_to_s3(case_id: str, structure: str, slice_index: int, mask_bytes: bytes) -> bool:
    bucket = get_annotation_bucket()
    s3 = get_s3_client()

    filename = f"axial_{slice_index:03d}.png"
    key = f"annotation_dataset/{case_id}/masks_pred/{structure}/{filename}"

    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=mask_bytes,
            ContentType="image/png"
        )
        logger.info(f"Saved mask to S3: {key}")
        return True
    except Exception as e:
        logger.error(f"Error saving mask: {e}")
        return False


def save_blank_mask(case_id: str, structure: str, slice_index: int, reference_image_bytes: bytes):
    image = Image.open(io.BytesIO(reference_image_bytes))
    blank = Image.new("L", image.size, 0)
    buf = io.BytesIO()
    blank.save(buf, format="PNG")
    save_mask_to_s3(case_id, structure, slice_index, buf.getvalue())
    image.close()


def _bbox_s3_key(case_id: str, structure: str, slice_index: int) -> str:
    filename = f"axial_{slice_index:03d}.json"
    return f"annotation_dataset/{case_id}/bboxes/{structure}/{filename}"


def save_bbox_to_s3(payload: dict):
    bucket = get_annotation_bucket()
    s3 = get_s3_client()
    key = _bbox_s3_key(payload["case_id"], payload["structure"], payload["slice_index"])
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info("Saved bbox metadata to s3://%s/%s", bucket, key)


def _build_bbox_payload(
    *,
    case_id: str,
    structure: str,
    slice_index: int,
    bbox: Optional[BoundingBox],
    padded_bbox: Optional[BoundingBox],
    confidence: Optional[float],
    response_text: str,
    model_id: str,
    prompt_text: Optional[str] = None,
    source: str = "claude_bbox_v1",
) -> dict:
    payload = {
        "case_id": case_id,
        "structure": structure,
        "slice_index": slice_index,
        "visible": bbox is not None,
        "bbox": bbox.to_dict() if bbox else None,
        "padded_bbox": padded_bbox.to_dict() if padded_bbox else None,
        "confidence": confidence,
        "model_id": model_id,
        "prompt_version": BBOX_PROMPT_VERSION,
        "source": source,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "raw_response": response_text,
    }
    # Add prompt text for debugging if available
    if prompt_text:
        payload["prompt_text"] = prompt_text
    return payload


# =========================
#  END-TO-END PROCESS
# =========================

def call_llm_for_segmentation(
    image_bytes: bytes,
    structure: str,
    max_tokens: int = 30_000,
    test_pattern: bool = False,
) -> dict:
    """
    Lightweight helper so other scripts (batch jobs, routes) can reuse
    the same LLM invocation logic.
    """
    if structure not in SUPPORTED_STRUCTURES:
        return {"success": False, "error": f"Unsupported structure: {structure}"}

    try:
        response_text = annotator_service.segment_slice(
            image_bytes,
            get_segmentation_prompt(structure, test_pattern=test_pattern),
            max_tokens=max_tokens,
        )
        mask_b64 = parse_mask_json(response_text)
        return {"success": True, "mask_base64": mask_b64, "raw_text": response_text}
    except Exception as exc:
        logger.error("LLM segmentation call failed: %s", exc)
        return {"success": False, "error": str(exc)}


def process_slice(case_id: str, structure: str, slice_index: int, test_pattern: bool = False):
    logger.info(f"Processing slice {slice_index} ({structure})")

    try:
        image_bytes = load_slice_from_s3(case_id, slice_index)
    except Exception as e:
        logger.error(f"Failed to load slice: {e}")
        return False

    # Use unified detection if enabled, otherwise use old classification
    unified_result = None
    slice_info = None
    
    if USE_UNIFIED_DETECTION:
        unified_result = detect_section_and_structures(case_id, slice_index, image_bytes, use_unified_prompt=True)
        if unified_result:
            slice_section = unified_result.get("slice_section", "invalid")
            structures_detected = unified_result.get("structures_detected", [])
            reject_reason = unified_result.get("reject_reason")
            
            # Check if slice was rejected
            if reject_reason or slice_section == "invalid":
                logger.info(
                    "Skipping %s slice %s - slice rejected: %s",
                    structure,
                    slice_index,
                    reject_reason or "invalid section"
                )
                save_blank_mask(case_id, structure, slice_index, image_bytes)
                return True
            
            # Check if structure is in detected list
            if structure not in structures_detected:
                logger.info(
                    "Skipping %s slice %s - structure not detected in section %s. Detected: %s",
                    structure,
                    slice_index,
                    slice_section,
                    structures_detected
                )
                save_blank_mask(case_id, structure, slice_index, image_bytes)
                return True
            
            # Convert unified result to slice_info format for compatibility
            slice_info = {
                "slice_type": slice_section,
                "airway_present": len(structures_detected) > 0 and "airway" in structures_detected,
                "reason": f"Unified detection: {slice_section}"
            }
        else:
            # Fallback to old classification if unified detection fails
            slice_info = classify_slice(case_id, slice_index, image_bytes)
    else:
        # Use old classification method
        slice_info = classify_slice(case_id, slice_index, image_bytes)
    
    # Check if structure is allowed on this slice (using updated rules)
    if slice_info and not slice_allows_structure(structure, slice_info):
        logger.info(
            "Skipping %s slice %s due to slice_type=%s airway_present=%s",
            structure,
            slice_index,
            slice_info.get("slice_type"),
            slice_info.get("airway_present"),
        )
        save_blank_mask(case_id, structure, slice_index, image_bytes)
        return True

    try:
        mask_bytes = generate_mask_bytes(
            case_id,
            structure,
            slice_index,
            center_raw_bytes=image_bytes,
            test_pattern=test_pattern,
            unified_result=unified_result,
        )
        logger.debug(
            "Generated mask for %s slice %s: first bytes=%s",
            structure,
            slice_index,
            mask_bytes[:16],
        )

        # STEP 4: Validate mask
        validate_mask(mask_bytes, image_bytes)
        coverage = calculate_mask_coverage(mask_bytes)
        logger.info(
            "Mask coverage for %s slice %s: %.2f%%",
            structure,
            slice_index,
            coverage * 100,
        )

        if coverage <= MIN_MASK_NONZERO_RATIO:
            raise ValueError(
                f"Mask coverage too low ({coverage:.4%}); rejecting result"
            )
        if coverage >= MAX_MASK_NONZERO_RATIO:
            raise ValueError(
                f"Mask coverage too high ({coverage:.2%}); rejecting result"
            )

        # STEP 5: Save mask
        save_mask_to_s3(case_id, structure, slice_index, mask_bytes)
        logger.info(f"✅ Slice {slice_index} segmentation completed")
        return True

    except Exception as e:
        logger.error(f"Segmentation failed: {e}")

    # If failure → save blank mask
    logger.error("Segmentation failed — saving blank mask.")
    save_blank_mask(case_id, structure, slice_index, image_bytes)
    return False


# =========================
#  MAIN
# =========================

def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    case_id = sys.argv[1]
    structure = sys.argv[2]
    slice_index = int(sys.argv[3])
    test_pattern = '--test-pattern' in sys.argv[4:]

    if structure not in SUPPORTED_STRUCTURES:
        logger.error("Invalid structure. Choose from: %s", ", ".join(SUPPORTED_STRUCTURES))
        sys.exit(1)

    success = process_slice(case_id, structure, slice_index, test_pattern=test_pattern)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
