"""
CBCT OSA Annotator Routes

All routes for the annotator tool.
"""

import base64
import logging
import json
import os
import re
import time
import boto3
from flask import jsonify, request, render_template, current_app, send_file, make_response
from flask_login import login_required, current_user
from io import BytesIO
from flask_app.annotator import cbct_annotator_bp
from flask_app.annotator.structure_config import (
    SUPPORTED_STRUCTURES,
    ensure_manifest_structures,
)
# Lazy import to avoid circular dependency - import inside function when needed
# from scripts.llm_segment_cbct_slice import generate_mask_bytes
from flask_app.services.annotator_bedrock_service import get_annotator_bedrock_service
from flask_app.models import Patient

logger = logging.getLogger(__name__)

MPR_ANALYSIS_PROMPT = """
You are an expert in upper-airway CBCT interpretation, sleep-apnea imaging,
and multi-modal clinical reasoning.

Your task is to analyze MULTIPLE axial MPR CBCT slices and provide a
COMPREHENSIVE SUMMARY of anatomical observations across all slices.

Your analysis must be strictly image-based.
Never guess. Never infer anatomy that cannot be clearly seen.

---------------------------------------------------------------------
WORKFLOW (MUST FOLLOW)
---------------------------------------------------------------------
1. Analyze EACH individual axial image:
   - Determine anatomical level using HARD RULES below.
   - Identify visible structures only.
   - Describe airway shape and narrowing if clearly visible.

2. Then SYNTHESIZE all findings across all slices into ONE unified summary.

3. If a slice is ambiguous, distorted by artifact, or lacks definable anatomy:
   - Mark the slice as INVALID and exclude it from conclusions.

4. If uncertain between two adjacent levels:
   - You MUST choose "valid_slice": false.
   - DO NOT guess anatomical level.

5. Only return ONE final JSON object for all slices.

---------------------------------------------------------------------
SECTION 1 — LEARN THE STYLE AND CLINICAL PATTERNS
---------------------------------------------------------------------

Study the structural-imaging excerpts below. Extract only the STYLE:
- How obstruction sites are described
- How palate/uvula are described
- How tongue posture is phrased
- How craniofacial constraints are expressed

DO NOT copy text. Only learn the descriptive pattern.

EXAMPLE 1:
{
  "obstruction_sites": "Velopharyngeal, oropharyngeal, and tongue base restriction.",
  "soft_palate_uvula": "Elongated and swollen, partially obstructing the oropharynx.",
  "tongue_position": "Large, posteriorly positioned tongue base encroaching the airway.",
  "jaw_structure": "Minimal overjet/overbite and narrow arches.",
  "airway_conclusion": "Obstruction from soft palate hypertrophy and posterior tongue."
}

EXAMPLE 2:
{
  "obstruction_sites": "Tongue base–related oropharyngeal narrowing.",
  "soft_palate_uvula": "Elongated and swollen soft palate.",
  "tongue_position": "Posterior tongue base contacting airway.",
  "jaw_structure": "Narrow jaw arches.",
  "airway_conclusion": "Combined soft palate hypertrophy and tongue-related narrowing."
}

---------------------------------------------------------------------
SECTION 2 — STRICT ANATOMICAL LEVEL RULES
---------------------------------------------------------------------

You MUST classify slices using ONLY the rules below.
If ANY rule is violated → slice is INVALID.

----------------------------
HARD NEGATIVE RULES (NO EXCEPTIONS)
----------------------------
A slice CANNOT be nasopharynx if ANY of the following are visible:
1. Tongue (any part: body or base)
2. Mandible (body, ramus, alveolar ridge)
3. Cervical vertebrae
4. Epiglottis or laryngeal structures
5. Soft palate or uvula
6. Oral cavity soft tissue

A slice CANNOT be retropalatal if:
1. Soft palate or uvula is NOT clearly visible
2. Tongue dominates the anterior half of the slice
3. Mandible and tongue are the primary structures

A slice CANNOT be tongue-body level if:
1. Soft palate/uvula is visible
2. Epiglottis is visible

A slice CANNOT be tongue-base level if:
1. Uvula/soft palate is visible
2. Epiglottis is visible

A slice MUST be rejected ("valid_slice": false) if:
1. Only sinus structures are present
2. Only laryngeal/tracheal structures are present
3. Dental scatter prevents airway visualization
4. Anatomical level cannot be clearly determined

----------------------------
POSITIVE DEFINITIONS (WHAT EACH LEVEL MUST SHOW)
----------------------------
1. NASOPHARYNX:
   MUST include:
   - Nasal septum
   - Turbinates
   - Posterior choanae
   - Air-filled nasopharyngeal airway
   AND must show NONE of the negative structures above.

2. RETROPALATAL (uvula/soft palate level):
   MUST show:
   - Soft palate OR uvula clearly
   - Airway posterior to palate
   - NOT dominated by tongue

3. TONGUE BODY:
   MUST show:
   - Tongue filling most anterior oral cavity
   - No palate/uvula
   - Airway posterior to tongue body

4. TONGUE BASE:
   MUST show:
   - Posterior downward sloping tongue base
   - Narrowing of the oropharynx behind tongue
   - No palate/uvula visible

5. HYPOPHARYNX / EPIGLOTTIS:
   MUST show:
   - Epiglottis or arytenoid region
   - Lower posterior airway

----------------------------
PARTIAL STRUCTURE VISIBILITY RULES
----------------------------
If a structure is partially visible, use these thresholds:

- Soft palate: Must see at least 50% of the arch/curvature to classify as retropalatal
- Uvula: Must see the teardrop/pendant shape clearly (not just a small soft tissue blob)
- Tongue: Must see substantial portion (>60% of anterior space) to classify as tongue body level
- Epiglottis: Must see the characteristic leaf-like shape (not just a shadow)

If structure is <50% visible or ambiguous → do not use it for level classification

----------------------------
TRANSITION ZONE HANDLING
----------------------------
When a slice appears to be at a transition between two levels:

1. Look for the DOMINANT anatomical feature
2. If neither feature clearly dominates → mark as invalid (don't guess)
3. If one feature is clearly present but the other is just beginning → classify at the level with the clear feature
4. Confidence should be <0.7 for any transition zone classification

Examples:
- Soft palate just beginning to appear but mostly nasopharynx → nasopharynx (confidence 0.65)
- Tongue body transitioning to tongue base (slope just beginning) → tongue body (confidence 0.7)
- Equal presence of palate and tongue → invalid (too ambiguous)

----------------------------
EDGE CASE EXAMPLES
----------------------------
Case 1: Palate attachment zone
- Soft palate just beginning to attach to hard palate
- Mostly nasopharynx but palate visible superiorly
- Classification: Retropalatal (confidence 0.7) - palate presence takes precedence
- Note: "Transition zone between nasopharynx and retropalatal"

Case 2: Tongue body/base transition
- Tongue mostly horizontal but beginning to slope posteriorly
- No clear hyoid visible
- Classification: Tongue body (confidence 0.75) - horizontal orientation dominant
- Note: "Transition zone, tongue beginning to slope posteriorly"

Case 3: Artifact affecting airway
- Metal restoration creating scatter over airway
- Airway partially visible but narrowing assessment uncertain
- Classification: Valid slice, but severity = "unknown" for airway narrowing
- Note: "Dental artifact partially obscures airway assessment"

---------------------------------------------------------------------
SECTION 3 — IMAGE FEATURE ANALYSIS
---------------------------------------------------------------------
For each valid slice, identify ONLY what is visible:
- Airway lumen shape (round, slit-like, compressed, asymmetric)
- AP vs lateral narrowing
- Lateral pharyngeal wall behavior
- Soft palate/uvula if present
- Tongue body/base if present
- Epiglottis if present
- Jaw/craniofacial constraints if visible
- Hyoid bone position if visible (see hyoid assessment below)
Never infer beyond the image.

----------------------------
HYOID BONE ASSESSMENT
----------------------------
When hyoid is visible:
- Note its position relative to mandible (if mandible visible in same slice)
- "Inferior to mandibular plane" = hyoid below mandible (low position, clinically significant)
- "At mandibular plane" = hyoid at same level as mandible
- "Superior to mandibular plane" = hyoid above mandible (unusual)

If hyoid is visible but mandible is not in same slice:
- Still note hyoid presence
- Cannot assess relative position → note in quality_notes

----------------------------
ARTIFACT AND IMAGE QUALITY HANDLING
----------------------------
Dental scatter/metal artifacts:
- If metal restorations or implants obscure >50% of airway → mark slice as invalid
- If scatter affects airway assessment but anatomy still identifiable → note in quality_notes, reduce confidence
- If artifact is peripheral and doesn't affect airway → proceed with normal analysis

Motion blur:
- If anatomical structures are blurred/unclear → mark as invalid
- If only minor blur at edges → proceed with reduced confidence

Incomplete slice/partial anatomy:
- If <70% of expected anatomy visible → mark as invalid
- If key structures (airway, tongue, palate) are partially visible → proceed with confidence <0.7

Noise/poor contrast:
- If airway cannot be distinguished from soft tissue → mark as invalid
- If contrast is poor but structures still identifiable → proceed with confidence <0.6

----------------------------
SEVERITY GRADING CRITERIA
----------------------------
FOR AIRWAY NARROWING:
- LOW: Airway lumen reduced by <30% compared to expected normal, shape still round/oval, minimal compression
- MODERATE: Airway lumen reduced by 30-60%, shape may be compressed or elliptical, visible narrowing
- HIGH: Airway lumen reduced by >60%, slit-like or nearly collapsed, severe compression
- UNKNOWN: Unable to assess due to image quality, anatomical ambiguity, or slice positioning

FOR SOFT TISSUE FINDINGS (palate, tongue, lateral walls):
- LOW: Minimal anatomical variation, unlikely to be obstructive, within normal limits
- MODERATE: Notable anatomical variation or enlargement, potentially contributory to obstruction
- HIGH: Severe anatomical abnormality or enlargement, clearly obstructive based on imaging
- UNKNOWN: Unable to assess due to image quality or anatomical ambiguity

FOR STRUCTURAL FINDINGS (jaw, hyoid):
- LOW: Minor variation, unlikely clinically significant
- MODERATE: Moderate abnormality, potentially contributory
- HIGH: Severe abnormality, clearly significant anatomical factor
- UNKNOWN: Structure not visible or assessment not possible

----------------------------
DECISION: UNKNOWN SEVERITY vs INVALID SLICE
----------------------------
Use "severity": "unknown" when:
- Structure is visible but narrowing cannot be quantified (e.g., airway partially obscured)
- Anatomical level is certain but specific finding is ambiguous
- Image quality reduces confidence but anatomy is still identifiable

Use "valid_slice": false when:
- Anatomical level cannot be determined
- Key structures are not visible
- Artifact prevents reliable assessment
- Slice is clearly outside airway region (pure sinus, pure larynx)

Rule: If you can identify the level → use "unknown" severity for ambiguous findings
       If you cannot identify the level → mark slice as invalid

----------------------------
HANDLING VARIABILITY AT SAME LEVEL
----------------------------
When multiple images at the same anatomical level show different narrowing patterns:

1. Consider sub-level variation:
   - Retropalatal region spans ~10-15mm superior-inferior
   - Different positions within this region can show different patterns
   - Report: "Variability within retropalatal region suggests different sub-level positions"

2. Consider slice orientation/angle:
   - Slight angulation differences can affect apparent narrowing
   - If patterns are very different → note possible angulation artifact

3. Report both patterns:
   - "Image X shows severe lateral collapse; Image Y shows moderate AP narrowing"
   - "Likely represents different superior-inferior positions within retropalatal region"
   - Do not force consistency if images genuinely differ

---------------------------------------------------------------------
SECTION 4 — OUTPUT FORMAT (MANDATORY JSON)
---------------------------------------------------------------------

Return one JSON object:

{
  "images_analyzed": <integer: number of images analyzed>,
  
  "anatomical_levels_present": [
    "<level_name>",
    "<level_name>",
    ...
  ],
  
  "anatomical_levels_missing": [
    "<level_name that is not sampled>",
    ...
  ],
  
  "level_by_image": [
    {
      "image_number": <integer: 1, 2, 3, etc.>,
      "level": "<level_name>",
      "confidence": <0.0-1.0>,
      "notes": "<optional: any relevant notes about this image>"
    },
    ...
  ],
  
  "summary_observations": [
    {
      "name": "<observation_category>",
      "description": "<comprehensive description synthesizing findings across all relevant images at this level>",
      "severity": "<low | moderate | high | unknown>",
      "confidence": <0.0-1.0>,
      "images_where_observed": [<integer>, <integer>, ...],
      "anatomical_level": "<level_name>"
    }
  ],
  "key_findings": {
    "airway_patency": "<overall from all slices>",
    "obstruction_sites": "<sites identified across series>",
    "soft_palate_uvula": "<combined description>",
    "tongue_position": "<combined description>",
    "tongue_base": "<findings at tongue base level if present>",
    "lateral_walls": "<combined description>",
    "jaw_structure": "<mandibular/maxillary findings if visible>",
    "hyoid": "<hyoid position if visible (e.g., 'Inferior to mandibular plane' or 'Not seen')>",
    "other_structures": "<any other relevant findings (sinuses, adenoids, etc.)>"
  },
  "mca_assessment": {
    "likely_mca_level": "<anatomical level>",
    "likely_mca_image": <integer: image number>,
    "reasoning": "<why this is considered narrowest airway among images provided>",
    "confidence": <0.0-1.0: see MCA confidence calibration below>,
    "limitation": "<note about non-consecutive sampling limiting definitive MCA identification>"
  },
  "airway_conclusion": "<final integrated anatomical summary synthesizing all findings from sampled levels. Note which levels were analyzed and acknowledge gaps in sampling. Describe findings at each level present.>",
  "analysis_limitations": "<specific limitations due to: non-consecutive sampling, missing anatomical levels, image quality issues, or other factors>",
  "quality_notes": "<any issues with image quality, ambiguous images, or uncertainties in level identification>",
  "style_learned_from_examples": true
}

If NO VALID IMAGES:
{
  "valid_images": false,
  "reason": "<explanation: e.g., 'all images are above/below airway region', 'image quality insufficient', etc.>",
  "images_analyzed": <integer>
}

----------------------------
MCA CONFIDENCE CALIBRATION
----------------------------
- Confidence 0.9+: Consecutive sampling with clear narrowest point identified
- Confidence 0.7-0.9: Non-consecutive but multiple images at likely MCA level
- Confidence 0.5-0.7: Non-consecutive sampling, narrowest point identified but gaps present
- Confidence <0.5: Very sparse sampling, MCA assessment highly uncertain

Always include limitation note when confidence <0.8

----------------------------
OUTPUT COMPLETENESS CHECKLIST
----------------------------
Before finalizing, verify:
□ Each image has a level assignment (if valid)
□ All identified levels have at least one observation in summary_observations
□ Key findings reference specific image numbers where applicable
□ MCA assessment acknowledges sampling limitations
□ analysis_limitations explicitly notes non-consecutive sampling (if applicable)
□ airway_conclusion synthesizes findings from all valid images
□ Confidence scores reflect uncertainty appropriately
□ quality_notes includes any image quality issues or ambiguities
□ Hyoid position noted if visible (even if relative position cannot be determined)

---------------------------------------------------------------------
END
---------------------------------------------------------------------
"""

MPR_ANALYSIS_MAX_TOKENS = int(os.getenv("ANNOTATOR_MPR_ANALYSIS_MAX_TOKENS", "4000"))

# Multi-chunk MPR prompts
MPR_CHUNK_ANALYSIS_PROMPT = """
You are an expert in upper-airway CBCT interpretation, sleep-apnea imaging,
and multi-modal clinical reasoning.

Your task is to analyze multiple CBCT slice positions, where EACH position includes
3 orthogonal views: Axial, Coronal, and Sagittal.

IMAGES PROVIDED:
- Multiple slice positions × 3 views per position
- Images are grouped in triplets: [Sagittal N] [Coronal N] [Axial N] for each slice position
- Use ALL THREE views together to determine anatomical level and findings

CRITICAL: Cross-plane reasoning is essential:
- Sagittal view shows palate/uvula position and posterior displacement
- Coronal view shows lateral symmetry and palate width
- Axial view shows airway shape and cross-sectional narrowing
- Use all three views together for accurate anatomical level detection

---------------------------------------------------------------------
WORKFLOW (MUST FOLLOW)
---------------------------------------------------------------------
1. For EACH slice position (triplet of 3 views):
   - Examine all 3 views together (sagittal, coronal, axial)
   - Determine anatomical level using cross-plane evidence
   - Identify visible structures using information from all views
   - Describe airway findings using axial view primarily, but confirm with other views

2. After analyzing all 20 slice positions:
   - Synthesize findings into ONE unified summary for this chunk
   - Group findings by anatomical level
   - Note which slice positions show key findings

3. Return ONE JSON summary for this chunk of 20 slices.

---------------------------------------------------------------------
ANATOMICAL LEVEL IDENTIFICATION (USE ALL 3 VIEWS)
---------------------------------------------------------------------

RETROPALATAL LEVEL:
- Sagittal: Soft palate/uvula visible as curved structure
- Coronal: Palate width visible, airway posterior to palate
- Axial: Retropalatal airway space, palate/uvula visible
- If ANY view shows palate/uvula → classify as retropalatal

TONGUE BODY/BASE LEVEL:
- Sagittal: Tongue slope visible, airway posterior to tongue
- Coronal: Tongue width and oral cavity dimensions
- Axial: Tongue filling anterior space, airway posterior
- Use sagittal to distinguish body (horizontal) vs base (sloping)

NASOPHARYNX:
- Sagittal: No palate/uvula, nasal structures visible
- Coronal: Nasal cavity width, turbinates
- Axial: Nasopharyngeal airway, no tongue/palate

Use the most informative view for each structure, but confirm with other views.

---------------------------------------------------------------------
OUTPUT FORMAT (MANDATORY JSON)
---------------------------------------------------------------------

{
  "chunk_number": <chunk number>,
  "slices_analyzed": <number of slices in this chunk>,
  "slice_positions": [
    {
      "position": <1-20>,
      "anatomical_level": "<level_name>",
      "confidence": <0.0-1.0>,
      "key_findings": "<brief description>",
      "views_used": ["sagittal", "coronal", "axial"]
    },
    ...
  ],
  "level_summary": {
    "<level_name>": {
      "slice_positions": [<list of positions>],
      "findings": "<combined findings at this level>",
      "severity": "<low | moderate | high | unknown>"
    },
    ...
  },
  "chunk_conclusion": "<summary of findings across this chunk of 20 slices>"
}

---------------------------------------------------------------------
END
---------------------------------------------------------------------
"""

MPR_SYNTHESIS_PROMPT = """
You are synthesizing findings from 3 separate chunk analyses of CBCT images.

You have received 3 chunk summaries, each analyzing 20 slice positions (60 images per chunk).

Your task: Create a FINAL COMPREHENSIVE SUMMARY that integrates all findings
from all 3 chunks into one unified airway assessment.

---------------------------------------------------------------------
WORKFLOW
---------------------------------------------------------------------
1. Review all 3 chunk summaries
2. Identify all anatomical levels present across all chunks
3. Synthesize findings at each level (combining data from all chunks)
4. Identify the likely MCA (minimum cross-sectional area) across all slices
5. Create final comprehensive report

---------------------------------------------------------------------
OUTPUT FORMAT (MANDATORY JSON)
---------------------------------------------------------------------

Use the same format as the single-chunk analysis, but synthesize findings
from all 3 chunks:

{
  "images_analyzed": 60,
  "chunks_processed": 3,
  "anatomical_levels_present": [...],
  "anatomical_levels_missing": [...],
  "level_by_image": [...],
  "summary_observations": [...],
  "key_findings": {...},
  "mca_assessment": {...},
  "airway_conclusion": "<comprehensive conclusion synthesizing all 3 chunks>",
  "analysis_limitations": "<note that analysis was done in 3 chunks and synthesized>"
}

---------------------------------------------------------------------
END
---------------------------------------------------------------------
"""

_mpr_llm_service = get_annotator_bedrock_service()


def get_s3_client():
    """Get S3 client with proper region."""
    region = (current_app.config.get('AWS_REGION')
              or os.getenv('AWS_REGION')
              or 'us-east-1')
    return boto3.client('s3', region_name=region)


def get_annotation_bucket():
    """Get annotation dataset bucket."""
    return 'vizbrizknowledgebase'


def _build_image_content(image_bytes: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(image_bytes).decode("utf-8"),
        },
    }


def _extract_json_payload(text: str) -> dict:
    """
    Extract JSON payload(s) from LLM response.
    Handles multiple JSON objects (one per image) and combines them.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()

    # Find all JSON objects in the response
    json_objects = []
    i = 0
    while i < len(cleaned):
        # Find next opening brace
        start = cleaned.find("{", i)
        if start == -1:
            break
        
        # Find matching closing brace using a simple bracket counter
        depth = 0
        end = start
        for j in range(start, len(cleaned)):
            if cleaned[j] == "{":
                depth += 1
            elif cleaned[j] == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        
        if depth == 0:
            # Extract and parse this JSON object
            snippet = cleaned[start : end + 1]
            try:
                obj = json.loads(snippet)
                json_objects.append(obj)
            except json.JSONDecodeError:
                # Skip malformed JSON
                pass
            i = end + 1
        else:
            # Unmatched braces, skip this attempt
            i = start + 1
    
    if not json_objects:
        raise ValueError("No valid JSON objects found in response.")
    
    # If only one object, return it directly
    if len(json_objects) == 1:
        return json_objects[0]
    
    # Multiple objects: combine into a structured response
    return {
        "multiple_slices": True,
        "slice_count": len(json_objects),
        "slices": json_objects,
        "summary": {
            "valid_slices": sum(1 for obj in json_objects if obj.get("valid_slice", True) != False),
            "invalid_slices": sum(1 for obj in json_objects if obj.get("valid_slice") == False),
        }
    }


@cbct_annotator_bp.route('/annotator/cbct/<case_id>', methods=['GET'])
@login_required
def annotator_view(case_id):
    """
    Main annotator UI route.
    
    Args:
        case_id: Training case identifier (format: CBCT_0001, CBCT_0002, etc.)
        Each case represents data from one patient (anonymized for training)
        Each CBCT_MPR folder = exactly ONE annotation case
    
    Returns:
        Rendered annotator template
    """
    # Permission check - restrict to admin/annotator roles
    # TODO: Implement role-based access control
    # if not current_user.has_role('admin') and not current_user.has_role('annotator'):
    #     return jsonify({'error': 'Access denied'}), 403
    
    # Validate case_id format
    if not case_id.startswith('CBCT_') or not case_id[5:].isdigit():
        return jsonify({'error': f'Invalid case_id format. Expected CBCT_XXXX, got {case_id}'}), 400
    
    return render_template('annotator/cbct_annotator.html', case_id=case_id)


@cbct_annotator_bp.route('/api/cases', methods=['GET'])
@login_required
def list_cases():
    """
    List all available annotation cases.
    
    Returns:
    {
        "success": true,
        "cases": [
            {
                "case_id": "CBCT_0001",
                "num_slices": 180,
                "created": "2025-01-01T00:00:00Z" (from manifest if available)
            },
            ...
        ]
    }
    """
    bucket = get_annotation_bucket()
    s3_client = get_s3_client()
    
    cases = []
    prefix = "annotation_dataset/"
    
    try:
        # List all folders under annotation_dataset/
        response = s3_client.list_objects_v2(
            Bucket=bucket,
            Prefix=prefix,
            Delimiter='/'
        )
        
        # Extract case IDs from common prefixes
        if 'CommonPrefixes' in response:
            for prefix_obj in response['CommonPrefixes']:
                folder_path = prefix_obj['Prefix']
                # Extract case_id from path like "annotation_dataset/CBCT_0001/"
                if folder_path.startswith(prefix) and folder_path.endswith('/'):
                    case_id = folder_path[len(prefix):-1]  # Remove prefix and trailing /
                    
                    # Validate case_id format
                    if case_id.startswith('CBCT_') and case_id[5:].isdigit():
                        # Try to get case info from manifest
                        manifest_key = f"{folder_path}manifest.json"
                        try:
                            manifest_obj = s3_client.get_object(Bucket=bucket, Key=manifest_key)
                            manifest = json.loads(manifest_obj['Body'].read().decode('utf-8'))
                            cases.append({
                                'case_id': case_id,
                                'num_slices': manifest.get('num_slices', 0),
                                'created': manifest.get('created', None)
                            })
                        except Exception:
                            # Manifest not found or error reading it, still include case
                            cases.append({
                                'case_id': case_id,
                                'num_slices': 0,
                                'created': None
                            })
        
        # Sort by case_id
        cases.sort(key=lambda x: x['case_id'])
        
        return jsonify({
            'success': True,
            'cases': cases
        })
    except Exception as e:
        logger.error(f"Error listing cases: {e}", exc_info=True)
        return jsonify({'error': f'Failed to list cases: {str(e)}'}), 500


@cbct_annotator_bp.route('/api/case/<case_id>/info', methods=['GET'])
@login_required
def get_case_info(case_id):
    """
    Get case metadata.
    
    Returns:
    {
        "case_id": "CBCT_0001",
        "num_slices": 180,
        "voxel_spacing": [0.3, 0.3, 0.3],
        "structures": ["airway", "uvula", ...]
    }
    """
    # Validate case_id format
    if not case_id.startswith('CBCT_') or not case_id[5:].isdigit():
        return jsonify({'error': f'Invalid case_id format. Expected CBCT_XXXX, got {case_id}'}), 400
    
    bucket = get_annotation_bucket()
    s3_client = get_s3_client()
    
    # Load annotation manifest
    manifest_key = f"annotation_dataset/{case_id}/manifest.json"
    try:
        response = s3_client.get_object(Bucket=bucket, Key=manifest_key)
        manifest = json.loads(response['Body'].read().decode('utf-8'))
    except s3_client.exceptions.NoSuchKey:
        return jsonify({'error': f'Case {case_id} not found'}), 404
    except Exception as e:
        logger.error(f"Error loading manifest: {e}")
        return jsonify({'error': 'Failed to load case info'}), 500
    
    # Load voxel spacing
    voxel_spacing_key = f"annotation_dataset/{case_id}/metadata/voxel_spacing.json"
    voxel_spacing = None
    try:
        response = s3_client.get_object(Bucket=bucket, Key=voxel_spacing_key)
        voxel_spacing_data = json.loads(response['Body'].read().decode('utf-8'))
        voxel_spacing = [voxel_spacing_data.get('x_mm', 0.3),
                        voxel_spacing_data.get('y_mm', 0.3),
                        voxel_spacing_data.get('z_mm', 0.3)]
    except Exception as e:
        logger.warning(f"Error loading voxel spacing: {e}")
        voxel_spacing = manifest.get('voxel_spacing_mm', [0.3, 0.3, 0.3])
    
    # Ensure manifest has all supported structures
    ensure_manifest_structures(manifest)

    structures = list(manifest.get('structures', {}).keys())
    if not structures:
        structures = SUPPORTED_STRUCTURES
    
    return jsonify({
        'case_id': case_id,
        'num_slices': manifest.get('num_slices', 0),
        'voxel_spacing': voxel_spacing,
        'structures': structures
    })


@cbct_annotator_bp.route('/api/case/<case_id>/slice/<axis>/<int:slice_index>', methods=['GET'])
@login_required
def get_slice(case_id, axis, slice_index):
    """
    Get slice image for any axis (axial, coronal, sagittal).
    
    First tries to load from annotation bucket. If not found, falls back to source patient bucket.
    This avoids needing to copy all files upfront.
    
    Args:
        case_id: Case identifier
        axis: 'axial', 'coronal', or 'sagittal'
        slice_index: Slice index
    
    Returns PNG image.
    """
    annotation_bucket = get_annotation_bucket()
    s3_client = get_s3_client()
    
    try:
        # Try annotation bucket first
        if axis == 'axial':
            filename = f"axial_{slice_index:03d}.png"
            key = f"annotation_dataset/{case_id}/slices/{filename}"
            bucket = annotation_bucket
        else:
            # Coronal and sagittal are optional, in metadata/mpr_extra/
            # Try multiple possible locations and filename formats
            filename = f"{axis}_{slice_index:03d}.png"
            possible_keys = [
                f"annotation_dataset/{case_id}/metadata/mpr_extra/{axis}/{filename}",
                f"annotation_dataset/{case_id}/metadata/mpr_extra/{axis}/{axis}_{slice_index:04d}.png",
                f"annotation_dataset/{case_id}/metadata/mpr_extra/{axis}/{axis}_{slice_index}.png",
            ]
            
            # Try each possible key
            key = None
            bucket = annotation_bucket  # Set bucket for sagittal/coronal
            for possible_key in possible_keys:
                try:
                    s3_client.head_object(Bucket=bucket, Key=possible_key)
                    key = possible_key
                    break
                except s3_client.exceptions.ClientError:
                    continue
            
            if not key:
                # If no exact match, try listing the directory to find the actual filename
                prefix = f"annotation_dataset/{case_id}/metadata/mpr_extra/{axis}/"
                try:
                    response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1000)
                    if 'Contents' in response:
                        # Find slice by index (could be named differently)
                        for obj in response['Contents']:
                            obj_key = obj['Key']
                            # Extract index from filename (handle various naming patterns)
                            if f"{axis}_{slice_index:03d}" in obj_key or f"{axis}_{slice_index:04d}" in obj_key or f"{axis}_{slice_index}" in obj_key:
                                key = obj_key
                                break
                except Exception as e:
                    logger.debug(f"Could not list objects in {prefix}: {e}")
        
        # Try to load from annotation bucket first (if key was found)
        if key:
            logger.info(f"Trying to load from annotation bucket: {bucket}/{key}")
            try:
                response = s3_client.get_object(Bucket=bucket, Key=key)
                image_data = response['Body'].read()
                logger.info(f"Successfully loaded from annotation bucket: {key} ({len(image_data)} bytes)")
                return send_file(
                    BytesIO(image_data),
                    mimetype='image/png',
                    as_attachment=False
                )
            except s3_client.exceptions.NoSuchKey:
                # File not in annotation bucket - will try source bucket below
                logger.info(f"Slice not found in annotation bucket, trying source bucket: {key}")
                key = None  # Mark as not found so we try source bucket
            except Exception as e:
                logger.error(f"Error loading from annotation bucket: {e}", exc_info=True)
                key = None  # Mark as not found so we try source bucket
        
        # If key not found in annotation bucket, try loading from source patient bucket
        if not key:
            logger.info(f"Slice not found in annotation bucket for {axis} slice {slice_index}, trying source bucket")
            
            # Load manifest to get source location
            manifest_key = f"annotation_dataset/{case_id}/manifest.json"
            try:
                manifest_response = s3_client.get_object(Bucket=annotation_bucket, Key=manifest_key)
                manifest = json.loads(manifest_response['Body'].read().decode('utf-8'))
                source_info = manifest.get('source', {})
                logger.info(f"Loaded manifest for case {case_id}, source_info: {source_info}")
                
                if source_info:
                    source_bucket = source_info.get('bucket')
                    source_patient_id = source_info.get('patient_id')
                    source_folder = source_info.get('folder_name')
                    
                    logger.info(f"Source bucket: {source_bucket}, patient_id: {source_patient_id}, folder: {source_folder}")
                    
                    if source_bucket and source_patient_id and source_folder:
                        # Construct source path
                        if axis == 'axial':
                            source_prefix = f"patients/{source_patient_id}/imaging/cbct_mpr/{source_folder}/axial/"
                        else:
                            source_prefix = f"patients/{source_patient_id}/imaging/cbct_mpr/{source_folder}/{axis}/"
                        
                        logger.info(f"Looking for slice in source bucket at prefix: {source_prefix}")
                        
                        # List files in source directory to find the actual filename
                        try:
                            list_response = s3_client.list_objects_v2(
                                Bucket=source_bucket,
                                Prefix=source_prefix,
                                MaxKeys=1000
                            )
                            logger.info(f"List response for {source_prefix}: {len(list_response.get('Contents', []))} files found")
                            
                            if 'Contents' in list_response:
                                # Find file by index (files are usually sorted)
                                files = sorted([obj['Key'] for obj in list_response['Contents'] if obj['Key'].endswith('.png')])
                                logger.info(f"Found {len(files)} PNG files, looking for slice index {slice_index}")
                                if slice_index < len(files):
                                    source_key = files[slice_index]
                                    logger.info(f"Loading slice from source bucket: {source_key}")
                                    # Load from source bucket
                                    source_response = s3_client.get_object(Bucket=source_bucket, Key=source_key)
                                    image_data = source_response['Body'].read()
                                    logger.info(f"Successfully loaded slice from source bucket: {source_key} ({len(image_data)} bytes)")
                                    return send_file(
                                        BytesIO(image_data),
                                        mimetype='image/png',
                                        as_attachment=False
                                    )
                                else:
                                    logger.warning(f"Slice index {slice_index} out of range (found {len(files)} files)")
                            else:
                                logger.warning(f"No files found at prefix {source_prefix} in bucket {source_bucket}")
                        except Exception as e:
                            logger.error(f"Failed to load from source bucket: {e}", exc_info=True)
                    else:
                        logger.warning(f"Missing source info: bucket={source_bucket}, patient_id={source_patient_id}, folder={source_folder}")
                else:
                    logger.warning(f"No source_info found in manifest for case {case_id}")
            except Exception as e:
                logger.error(f"Could not load manifest or source info: {e}", exc_info=True)
            
            # If we get here, file wasn't found in either location
            return jsonify({
                'error': f'Slice {slice_index} not found for {axis}',
                'message': f'Slice not found in annotation bucket or source bucket. Re-create the case with include_reference_views=True.'
            }), 404
    except Exception as e:
        logger.error(f"Error loading slice: {e}")
        return jsonify({'error': 'Failed to load slice'}), 500


@cbct_annotator_bp.route('/api/case/<case_id>/mask_pred/<structure>/<int:slice_index>', methods=['GET', 'HEAD'])
@login_required
def get_mask_pred(case_id, structure, slice_index):
    """
    Get predicted mask.
    
    Returns PNG image or 404 if not found.
    """
    bucket = get_annotation_bucket()
    s3_client = get_s3_client()
    
    filename = f"axial_{slice_index:03d}.png"
    key = f"annotation_dataset/{case_id}/masks_pred/{structure}/{filename}"
    
    try:
        # For HEAD requests, just check if object exists without downloading
        if request.method == 'HEAD':
            s3_client.head_object(Bucket=bucket, Key=key)
            response = make_response('', 200)
            response.headers['Content-Type'] = 'image/png'
            return response
        
        response = s3_client.get_object(Bucket=bucket, Key=key)
        image_data = response['Body'].read()
        return send_file(
            BytesIO(image_data),
            mimetype='image/png',
            as_attachment=False
        )
    except s3_client.exceptions.NoSuchKey:
        # Predicted mask doesn't exist - this is expected for new cases, log at debug level
        logger.debug(f"Predicted mask not found for {case_id}/{structure}/slice_{slice_index} - this is expected for new cases")
        return jsonify({'error': f'Predicted mask not found'}), 404
    except s3_client.exceptions.ClientError as e:
        # boto3 sometimes raises ClientError instead of NoSuchKey for 404s
        error_code = e.response.get('Error', {}).get('Code', '')
        if error_code == '404' or error_code == 'NoSuchKey':
            logger.debug(f"Predicted mask not found for {case_id}/{structure}/slice_{slice_index} (404) - this is expected for new cases")
            return jsonify({'error': f'Predicted mask not found'}), 404
        logger.error(f"Error loading predicted mask: {e}")
        return jsonify({'error': 'Failed to load mask'}), 500
    except Exception as e:
        # Check if it's a 404 in the error message (fallback)
        if '404' in str(e) or 'Not Found' in str(e):
            logger.debug(f"Predicted mask not found for {case_id}/{structure}/slice_{slice_index} (404) - this is expected for new cases")
            return jsonify({'error': f'Predicted mask not found'}), 404
        logger.error(f"Error loading predicted mask: {e}")
        return jsonify({'error': 'Failed to load mask'}), 500


@cbct_annotator_bp.route('/api/case/<case_id>/mask_corrected/<structure>/<int:slice_index>', methods=['GET', 'HEAD'])
@login_required
def get_mask_corrected(case_id, structure, slice_index):
    """
    Get corrected mask.
    
    Returns PNG image or 404 if not found.
    """
    bucket = get_annotation_bucket()
    s3_client = get_s3_client()
    
    filename = f"axial_{slice_index:03d}.png"
    key = f"annotation_dataset/{case_id}/masks_corrected/{structure}/{filename}"
    
    try:
        # For HEAD requests, just check if object exists without downloading
        if request.method == 'HEAD':
            s3_client.head_object(Bucket=bucket, Key=key)
            response = make_response('', 200)
            response.headers['Content-Type'] = 'image/png'
            return response
        
        response = s3_client.get_object(Bucket=bucket, Key=key)
        image_data = response['Body'].read()
        return send_file(
            BytesIO(image_data),
            mimetype='image/png',
            as_attachment=False
        )
    except s3_client.exceptions.NoSuchKey:
        # Corrected mask doesn't exist - this is expected if not yet corrected, log at debug level
        logger.debug(f"Corrected mask not found for {case_id}/{structure}/slice_{slice_index} - this is expected if not yet corrected")
        return jsonify({'error': f'Corrected mask not found'}), 404
    except s3_client.exceptions.ClientError as e:
        # boto3 sometimes raises ClientError instead of NoSuchKey for 404s
        error_code = e.response.get('Error', {}).get('Code', '')
        if error_code == '404' or error_code == 'NoSuchKey':
            logger.debug(f"Corrected mask not found for {case_id}/{structure}/slice_{slice_index} (404) - this is expected if not yet corrected")
            return jsonify({'error': f'Corrected mask not found'}), 404
        logger.error(f"Error loading corrected mask: {e}")
        return jsonify({'error': 'Failed to load mask'}), 500
    except Exception as e:
        # Check if it's a 404 in the error message (fallback)
        if '404' in str(e) or 'Not Found' in str(e):
            logger.debug(f"Corrected mask not found for {case_id}/{structure}/slice_{slice_index} (404) - this is expected if not yet corrected")
            return jsonify({'error': f'Corrected mask not found'}), 404
        logger.error(f"Error loading corrected mask: {e}")
        return jsonify({'error': 'Failed to load mask'}), 500


@cbct_annotator_bp.route('/api/case/<case_id>/mask_corrected/<structure>/<int:slice_index>', methods=['POST'])
@login_required
def save_mask_corrected(case_id, structure, slice_index):
    """
    Save corrected mask.
    
    Body: PNG binary data
    
    Updates manifest.json to track corrected slices.
    """
    bucket = get_annotation_bucket()
    s3_client = get_s3_client()
    
    # Validate structure
    if structure not in SUPPORTED_STRUCTURES:
        return jsonify({'error': f'Invalid structure: {structure}'}), 400
    
    # Get PNG data from request
    if not request.data:
        return jsonify({'error': 'No image data provided'}), 400
    
    filename = f"axial_{slice_index:03d}.png"
    key = f"annotation_dataset/{case_id}/masks_corrected/{structure}/{filename}"
    
    try:
        # Upload mask to S3
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=request.data,
            ContentType='image/png'
        )
        
        # Update manifest
        manifest_key = f"annotation_dataset/{case_id}/manifest.json"
        try:
            response = s3_client.get_object(Bucket=bucket, Key=manifest_key)
            manifest = json.loads(response['Body'].read().decode('utf-8'))
        except:
            # Create new manifest if doesn't exist
            manifest = {
                'case_id': case_id,
                'num_slices': 0,
                'voxel_spacing_mm': [0.3, 0.3, 0.3],
            }
        ensure_manifest_structures(manifest)
        
        # Update structure status
        ensure_manifest_structures(manifest)
        
        # Add slice to corrected list if not already there
        if slice_index not in manifest['structures'][structure]['slices_corrected']:
            manifest['structures'][structure]['slices_corrected'].append(slice_index)
            manifest['structures'][structure]['slices_corrected'].sort()
        
        # Update status
        if manifest['structures'][structure]['slices_corrected']:
            manifest['structures'][structure]['status'] = 'in_progress'
        
        # Save updated manifest
        s3_client.put_object(
            Bucket=bucket,
            Key=manifest_key,
            Body=json.dumps(manifest, indent=2).encode('utf-8'),
            ContentType='application/json'
        )
        
        return jsonify({
            'success': True,
            'message': f'Mask saved for {structure} slice {slice_index}',
            'slices_corrected': manifest['structures'][structure]['slices_corrected']
        })
        
    except Exception as e:
        logger.error(f"Error saving mask: {e}")
        return jsonify({'error': 'Failed to save mask'}), 500


@cbct_annotator_bp.route('/api/case/<case_id>/update_manifest', methods=['POST'])
@login_required
def update_manifest(case_id):
    """
    Update annotation manifest.
    
    Body: JSON with manifest updates
    """
    bucket = get_annotation_bucket()
    s3_client = get_s3_client()
    
    manifest_key = f"annotation_dataset/{case_id}/manifest.json"
    
    try:
        # Load current manifest
        response = s3_client.get_object(Bucket=bucket, Key=manifest_key)
        manifest = json.loads(response['Body'].read().decode('utf-8'))
        
        # Update with request data
        updates = request.get_json()
        if 'structures' in updates:
            for structure, data in updates['structures'].items():
                if structure in manifest.get('structures', {}):
                    manifest['structures'][structure].update(data)
        
        # Save updated manifest
        s3_client.put_object(
            Bucket=bucket,
            Key=manifest_key,
            Body=json.dumps(manifest, indent=2).encode('utf-8'),
            ContentType='application/json'
        )
        
        return jsonify({'success': True, 'manifest': manifest})
        
    except s3_client.exceptions.NoSuchKey:
        return jsonify({'error': 'Manifest not found'}), 404
    except Exception as e:
        logger.error(f"Error updating manifest: {e}")
        return jsonify({'error': 'Failed to update manifest'}), 500


def _load_slice_bytes(case_id: str, slice_index: int) -> bytes:
    bucket = get_annotation_bucket()
    s3_client = get_s3_client()
    filename = f"axial_{slice_index:03d}.png"
    key = f"annotation_dataset/{case_id}/slices/{filename}"

    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        return response['Body'].read()
    except s3_client.exceptions.NoSuchKey:
        raise FileNotFoundError(key)

@cbct_annotator_bp.route('/api/case/<case_id>/tongue_analysis/<int:slice_index>', methods=['POST'])
@login_required
def analyze_tongue_slice(case_id, slice_index):
    """Analyze a slice to determine tongue presence and classification (body vs base)."""
    try:
        image_bytes = _load_slice_bytes(case_id, slice_index)
    except FileNotFoundError:
        return jsonify({'error': f'Slice {slice_index} not found for case {case_id}'}), 404
    except Exception as e:
        logger.error(f"Failed to load slice: {e}")
        return jsonify({'error': 'Failed to load slice'}), 500

    try:
        from scripts.llm_segment_cbct_slice import analyze_tongue_classification
        
        result = analyze_tongue_classification(case_id, slice_index, image_bytes)
        
        if result is None:
            return jsonify({'error': 'Failed to analyze tongue classification'}), 500
        
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        logger.error(f"Tongue analysis failed: {e}", exc_info=True)
        return jsonify({'error': f'Tongue analysis failed: {str(e)}'}), 500


@cbct_annotator_bp.route('/api/case/<case_id>/segment/<structure>/<int:slice_index>', methods=['POST'])
@login_required
def segment_slice(case_id, structure, slice_index):
    """Segment a single slice using the preprocessing + two-phase pipeline."""
    if structure not in SUPPORTED_STRUCTURES:
        return jsonify({'error': 'Unsupported structure'}), 400

    try:
        image_bytes = _load_slice_bytes(case_id, slice_index)
    except FileNotFoundError:
        return jsonify({'error': f'Slice {slice_index} not found for case {case_id}'}), 404
    except Exception as e:
        logger.error(f"Failed to load slice: {e}")
        return jsonify({'error': 'Failed to load slice'}), 500

    try:
        # Lazy import to avoid circular dependency
        from scripts.llm_segment_cbct_slice import generate_mask_bytes, save_mask_to_s3, save_blank_mask
        import numpy as np
        from PIL import Image
        import io
        
        debug_info_list = []
        try:
            mask_bytes, debug_info = generate_mask_bytes(
                case_id=case_id,
                structure=structure,
                slice_index=slice_index,
                center_raw_bytes=image_bytes,
                test_pattern=False,
            )
            
            # Collect debug info for LLM interactions
            if debug_info:
                if debug_info.get("bbox_prompt"):
                    debug_info_list.append({
                        "type": "Bbox Generation",
                        "prompt": debug_info.get("bbox_prompt"),
                        "response": debug_info.get("bbox_response"),
                        "confidence": debug_info.get("bbox_confidence"),
                    })
        except (RuntimeError, ValueError) as e:
            error_msg = str(e)
            # Check if this is a "not visible" case (valid response, not an error)
            if "not visible" in error_msg.lower() or "structure not visible" in error_msg.lower():
                logger.info(f"Structure {structure} not visible on slice {slice_index} - saving blank mask")
                save_blank_mask(case_id, structure, slice_index, image_bytes)
                return jsonify({
                    'success': True,
                    'mask_base64': None,
                    'not_visible': True,
                    'message': f'Structure {structure} is not visible on this slice'
                })
            # Re-raise other errors
            raise
        
        # Validate mask_bytes before processing
        if not mask_bytes:
            logger.error(f"generate_mask_bytes returned empty/None for {case_id}/{structure}/{slice_index}")
            return jsonify({'error': 'Segmentation returned empty mask data'}), 500
        
        if len(mask_bytes) == 0:
            logger.error(f"generate_mask_bytes returned zero-length bytes for {case_id}/{structure}/{slice_index}")
            return jsonify({'error': 'Segmentation returned zero-length mask data'}), 500
        
        # Check if mask is empty (all zeros or all same value)
        try:
            mask_img = Image.open(io.BytesIO(mask_bytes))
        except Exception as img_error:
            logger.error(f"Cannot open mask image for {case_id}/{structure}/{slice_index}: {img_error}. Mask bytes length: {len(mask_bytes)}")
            # Try to log first few bytes for debugging
            logger.error(f"First 100 bytes (hex): {mask_bytes[:100].hex() if len(mask_bytes) >= 100 else mask_bytes.hex()}")
            return jsonify({'error': f'Invalid mask image data: {str(img_error)}'}), 500
        
        mask_array = np.array(mask_img)
        
        # Check if mask has any non-zero pixels
        if mask_array.size == 0:
            logger.warning(f"Generated mask is empty for {case_id}/{structure}/{slice_index}")
            return jsonify({'error': 'Generated mask is empty', 'mask_base64': None}), 400
        
        # Check if mask is all zeros (black) or all 255 (white but might be blank)
        unique_values = np.unique(mask_array)
        if len(unique_values) == 1:
            # All pixels have the same value
            if unique_values[0] == 0:
                logger.warning(f"Generated mask is all zeros (black) for {case_id}/{structure}/{slice_index}")
                return jsonify({'error': 'Generated mask is empty (all black)', 'mask_base64': None}), 400
            # If all 255, it might be valid, but log it
            logger.info(f"Generated mask has single value {unique_values[0]} for {case_id}/{structure}/{slice_index}")
        
        # Count non-zero pixels
        non_zero_pixels = np.count_nonzero(mask_array)
        total_pixels = mask_array.size
        non_zero_ratio = non_zero_pixels / total_pixels if total_pixels > 0 else 0
        
        logger.info(f"Mask stats for {case_id}/{structure}/{slice_index}: {non_zero_pixels}/{total_pixels} non-zero pixels ({non_zero_ratio:.2%})")
        
        # Save mask to S3 (generate_mask_bytes should have saved it, but ensure it's saved)
        try:
            save_mask_to_s3(case_id, structure, slice_index, mask_bytes)
            logger.info(f"✓ Saved mask to S3 for {case_id}/{structure}/{slice_index}")
        except Exception as save_error:
            logger.error(f"Failed to save mask to S3: {save_error}")
            # Continue anyway - mask_bytes is still valid
        
        mask_b64 = base64.b64encode(mask_bytes).decode('utf-8')
        response_data = {
            'success': True, 
            'mask_base64': mask_b64,
            'mask_stats': {
                'non_zero_pixels': int(non_zero_pixels),
                'total_pixels': int(total_pixels),
                'non_zero_ratio': float(non_zero_ratio)
            }
        }
        
        # Add debug info if available
        if debug_info_list:
            response_data['debug_info'] = debug_info_list
        
        return jsonify(response_data)
    except Exception as e:
        logger.error(f"Segmentation call failed: {e}", exc_info=True)
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Segmentation service failed: {str(e)}'}), 500


@cbct_annotator_bp.route('/annotator/api/mpr_analysis', methods=['POST'])
@login_required
def analyze_mpr_images():
    """Analyze uploaded MPR slices and return structured observations."""
    uploaded_files = request.files.getlist('mpr_images')
    if not uploaded_files:
        return jsonify({'error': 'Please upload at least one MPR image (PNG).'}), 400

    processed_images = []
    for file_storage in uploaded_files:
        try:
            data = file_storage.read()
        except Exception:
            data = None
        if not data:
            continue
        processed_images.append(
            {
                "name": file_storage.filename or f"image_{len(processed_images) + 1}",
                "bytes": data,
            }
        )

    if not processed_images:
        return jsonify({'error': 'Uploaded files were empty or unreadable.'}), 400

    # Build prompt with explicit instruction about multiple images
    num_images = len(processed_images)
    prompt_text = MPR_ANALYSIS_PROMPT.strip()
    if num_images > 1:
        prompt_text += f"\n\nNOTE: You are being provided with {num_images} images. Analyze ALL {num_images} images, then provide a SINGLE comprehensive summary JSON that synthesizes findings across all slices."
    else:
        prompt_text += f"\n\nNOTE: Analyze this image and provide a comprehensive summary JSON."
    
    content = [{"type": "text", "text": prompt_text}]

    for idx, image in enumerate(processed_images, start=1):
        caption = f"Image #{idx} of {num_images}: {image['name']}"
        content.append({"type": "text", "text": caption})
        content.append(_build_image_content(image["bytes"]))

    response_text = None
    parsed = None
    
    try:
        response_text = _mpr_llm_service.invoke(
            content,
            max_tokens=MPR_ANALYSIS_MAX_TOKENS,
        )
        parsed = _extract_json_payload(response_text)
    except Exception as exc:
        logger.error("MPR anatomical analysis failed: %s", exc)
        # Still return the raw response so user can review it
        return (
            jsonify(
                {
                    'success': False,
                    'error': 'LLM analysis failed',
                    'details': str(exc),
                    'raw_response': response_text or 'No response received',
                }
            ),
            500,
        )

    return jsonify(
        {
            'success': True,
            'images_analyzed': len(processed_images),
            'report': parsed,
            'raw_response': response_text,
        }
    )


@cbct_annotator_bp.route('/annotator/api/mpr_analysis_chunked', methods=['POST'])
@login_required
def analyze_mpr_images_chunked():
    """
    Analyze MPR images in chunks of 6 slices × 3 views (18 images per chunk).
    Bedrock API limit is 20 images per request, so we use 6 slices (18 images) per chunk.
    
    Expected file naming: 
    - sagittal_<N>.png, coronal_<N>.png, axial_<N>.png for each slice position N
    - Or: <slice_N>_sagittal.png, <slice_N>_coronal.png, <slice_N>_axial.png
    
    Processes up to 60 slices (10 chunks of 6) = 180 images total.
    """
    uploaded_files = request.files.getlist('mpr_images')
    if not uploaded_files:
        return jsonify({'error': 'Please upload MPR images in triplets (sagittal, coronal, axial per slice).'}), 400

    # Parse and organize images into triplets by slice position
    image_triplets = {}  # {slice_position: {"sagittal": bytes, "coronal": bytes, "axial": bytes}}
    
    for file_storage in uploaded_files:
        try:
            data = file_storage.read()
            if not data:
                continue
            
            filename = file_storage.filename or ""
            filename_lower = filename.lower()
            
            # Extract slice position and view type from filename
            slice_pos = None
            view_type = None
            
            # Try different filename patterns
            # Pattern 1: sagittal_0.png, coronal_0.png, axial_0.png
            match = re.search(r'(sagittal|coronal|axial)[_\-](\d+)', filename_lower)
            if match:
                view_type = match.group(1)
                slice_pos = int(match.group(2))
            else:
                # Pattern 2: slice_0_sagittal.png, 0_coronal.png, etc.
                match = re.search(r'(\d+)[_\-](sagittal|coronal|axial)', filename_lower)
                if match:
                    slice_pos = int(match.group(1))
                    view_type = match.group(2)
                else:
                    # Pattern 3: Check if filename contains view type
                    if 'sagittal' in filename_lower:
                        view_type = 'sagittal'
                    elif 'coronal' in filename_lower:
                        view_type = 'coronal'
                    elif 'axial' in filename_lower:
                        view_type = 'axial'
                    
                    # Try to extract number
                    num_match = re.search(r'(\d+)', filename)
                    if num_match:
                        slice_pos = int(num_match.group(1))
            
            if slice_pos is None or view_type is None:
                logger.warning(f"Could not parse slice position/view from filename: {filename}")
                continue
            
            if slice_pos not in image_triplets:
                image_triplets[slice_pos] = {}
            
            image_triplets[slice_pos][view_type] = data
            
        except Exception as e:
            logger.error(f"Error processing file {file_storage.filename}: {e}")
            continue
    
    if not image_triplets:
        return jsonify({'error': 'No valid image triplets found. Expected sagittal/coronal/axial for each slice position.'}), 400
    
    # Sort slice positions and group into chunks of 6 (18 images = 6 × 3 views, under 20 image limit)
    # Bedrock API limit is 20 images per request
    sorted_positions = sorted(image_triplets.keys())
    chunks = []
    chunk_size = 6  # 6 slices × 3 views = 18 images (under 20 limit)
    for i in range(0, len(sorted_positions), chunk_size):
        chunk_positions = sorted_positions[i:i+chunk_size]
        chunks.append(chunk_positions)
    
    max_chunks = 10  # Allow up to 10 chunks (60 slices total)
    if len(chunks) > max_chunks:
        return jsonify({'error': f'Too many slices ({len(sorted_positions)}). Maximum {max_chunks * chunk_size} slices supported.'}), 400
    
    logger.info(f"Processing {len(sorted_positions)} slice positions in {len(chunks)} chunk(s)")
    
    # Process each chunk
    chunk_summaries = []
    for chunk_idx, chunk_positions in enumerate(chunks, start=1):
        logger.info(f"Processing chunk {chunk_idx}/{len(chunks)}: {len(chunk_positions)} slice positions")
        
        # Build content for this chunk
        prompt_text = MPR_CHUNK_ANALYSIS_PROMPT.strip()
        num_images = len(chunk_positions) * 3
        prompt_text += f"\n\nCHUNK {chunk_idx} of {len(chunks)}: Analyzing {len(chunk_positions)} slice positions ({num_images} images: {len(chunk_positions)}×3 views)."
        
        content = [{"type": "text", "text": prompt_text}]
        
        # Add images in triplet blocks: sagittal, coronal, axial for each position
        for pos in chunk_positions:
            triplet = image_triplets[pos]
            
            # Add sagittal
            if 'sagittal' in triplet:
                content.append({"type": "text", "text": f"Slice Position {pos} - Sagittal view:"})
                content.append(_build_image_content(triplet['sagittal']))
            else:
                content.append({"type": "text", "text": f"Slice Position {pos} - Sagittal view: MISSING"})
            
            # Add coronal
            if 'coronal' in triplet:
                content.append({"type": "text", "text": f"Slice Position {pos} - Coronal view:"})
                content.append(_build_image_content(triplet['coronal']))
            else:
                content.append({"type": "text", "text": f"Slice Position {pos} - Coronal view: MISSING"})
            
            # Add axial
            if 'axial' in triplet:
                content.append({"type": "text", "text": f"Slice Position {pos} - Axial view:"})
                content.append(_build_image_content(triplet['axial']))
            else:
                content.append({"type": "text", "text": f"Slice Position {pos} - Axial view: MISSING"})
        
        try:
            response_text = _mpr_llm_service.invoke(
                content,
                max_tokens=MPR_ANALYSIS_MAX_TOKENS * 2,  # More tokens for chunk analysis
            )
            chunk_parsed = _extract_json_payload(response_text)
            chunk_summaries.append({
                'chunk_number': chunk_idx,
                'summary': chunk_parsed,
                'raw_response': response_text,
            })
            logger.info(f"Chunk {chunk_idx} analysis complete")
        except Exception as exc:
            logger.error(f"Chunk {chunk_idx} analysis failed: {exc}")
            return jsonify({
                'success': False,
                'error': f'Chunk {chunk_idx} analysis failed',
                'details': str(exc),
                'chunks_completed': chunk_idx - 1,
            }), 500
    
    # Final synthesis step
    if len(chunk_summaries) > 1:
        logger.info("Synthesizing results from all chunks")
        synthesis_prompt = MPR_SYNTHESIS_PROMPT.strip()
        synthesis_prompt += "\n\nCHUNK SUMMARIES TO SYNTHESIZE:\n\n"
        
        for chunk_data in chunk_summaries:
            synthesis_prompt += f"=== CHUNK {chunk_data['chunk_number']} SUMMARY ===\n"
            synthesis_prompt += json.dumps(chunk_data['summary'], indent=2)
            synthesis_prompt += "\n\n"
        
        synthesis_content = [{"type": "text", "text": synthesis_prompt}]
        
        try:
            synthesis_response = _mpr_llm_service.invoke(
                synthesis_content,
                max_tokens=MPR_ANALYSIS_MAX_TOKENS,
            )
            final_parsed = _extract_json_payload(synthesis_response)
        except Exception as exc:
            logger.error(f"Final synthesis failed: {exc}")
            # Return chunk summaries even if synthesis fails
            return jsonify({
                'success': True,
                'warning': 'Synthesis step failed, returning individual chunk summaries',
                'chunks': chunk_summaries,
                'raw_synthesis_response': synthesis_response if 'synthesis_response' in locals() else None,
            })
    else:
        # Only one chunk, no synthesis needed
        final_parsed = chunk_summaries[0]['summary']
        synthesis_response = chunk_summaries[0]['raw_response']
    
    return jsonify({
        'success': True,
        'images_analyzed': len(sorted_positions) * 3,  # 3 views per slice
        'slices_analyzed': len(sorted_positions),
        'chunks_processed': len(chunks),
        'report': final_parsed,
        'chunk_summaries': chunk_summaries,
        'raw_response': synthesis_response if len(chunk_summaries) > 1 else chunk_summaries[0]['raw_response'],
    })


@cbct_annotator_bp.route('/annotator/api/create_case_from_patient', methods=['POST'])
@login_required
def create_case_from_patient():
    """
    Create case from patient MPR data (copy data to case directory).
    
    Body (JSON):
    {
        "patient_id": 10312,
        "folder_name": "Michael_Helle",
        "case_id": "CBCT_XXXX" (optional - auto-generated if not provided)
    }
    """
    try:
        logger.info(f"=== create_case_from_patient called ===")
        logger.info(f"Request method: {request.method}")
        logger.info(f"Request content type: {request.content_type}")
        logger.info(f"Request is_json: {request.is_json}")
        logger.info(f"Current user: {current_user.id if current_user else 'None'}")
        
        if not request.is_json:
            logger.error("Request is not JSON")
            return jsonify({'error': 'Request must be JSON'}), 400
        
        data = request.get_json()
        logger.info(f"create_case_from_patient request data: {data}")
        patient_id = data.get('patient_id')
        folder_name = data.get('folder_name')
        case_id = data.get('case_id')
        
        if not patient_id or not folder_name:
            logger.error(f"Missing required fields: patient_id={patient_id}, folder_name={folder_name}")
            return jsonify({'error': 'patient_id and folder_name are required'}), 400
        
        # Convert patient_id to int if it's a string
        try:
            original_patient_id = patient_id
            patient_id = int(patient_id)
            logger.info(f"Converted patient_id from {original_patient_id} (type: {type(original_patient_id)}) to {patient_id} (type: {type(patient_id)})")
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid patient_id format: {patient_id} (type: {type(patient_id)}), error: {e}")
            return jsonify({'error': f'Invalid patient_id: {patient_id}. Must be a number.'}), 400
        
        # Check patient access
        try:
            logger.info(f"Checking access for patient_id={patient_id} (type: {type(patient_id)}), user_id={current_user.id if current_user else 'None'}")
            patient = Patient.query.get(patient_id)
            if not patient:
                logger.error(f"Patient {patient_id} not found in database")
                # Try to see if there are any patients at all
                total_patients = Patient.query.count()
                logger.info(f"Total patients in database: {total_patients}")
                return jsonify({'error': f'Patient {patient_id} not found in database'}), 404
            logger.info(f"Found patient {patient_id}: {patient}")
            if not current_user.can_access_patient(patient):
                logger.warning(f"User {current_user.id} does not have access to patient {patient_id}")
                return jsonify({'error': 'Access denied to this patient'}), 403
            logger.info(f"Access granted for user {current_user.id} to patient {patient_id}")
        except Exception as e:
            logger.error(f"Error checking patient access: {e}", exc_info=True)
            import traceback
            logger.error(traceback.format_exc())
            return jsonify({'error': f'Error checking patient access: {str(e)}'}), 500
        
        # Get S3 client and bucket
        # For annotation cases, use vizbrizknowledgebase (where annotation_dataset is stored)
        # For patient data, use vizbrizpatients (where patients/ is stored)
        annotation_bucket = 'vizbrizknowledgebase'  # Annotation cases go here
        patient_bucket = current_app.config.get('S3_BUCKET') or current_app.config.get('S3_BUCKET_NAME')
        
        logger.info(f"Annotation bucket (for cases): {annotation_bucket}")
        logger.info(f"Patient bucket (for source data): {patient_bucket}")
        logger.info(f"S3_BUCKET env var: {os.getenv('S3_BUCKET')}")
        logger.info(f"S3_BUCKET_NAME env var: {os.getenv('S3_BUCKET_NAME')}")
        
        if not patient_bucket:
            logger.error("S3 bucket not configured in Flask app config")
            return jsonify({'error': 'S3 bucket not configured'}), 500
        
        region = (current_app.config.get('AWS_REGION')
                  or os.getenv('AWS_REGION')
                  or 'us-east-1')
        s3_client = boto3.client('s3', region_name=region)
        
        normalized_folder = folder_name.strip('/')
        
        # Generate case_id if not provided
        # Must be in format CBCT_XXXX where XXXX is all digits (validation requirement)
        # Make it deterministic: same patient_id + folder_name always generates same case_id
        if not case_id:
            import hashlib
            patient_str = str(patient_id)
            
            # Create a deterministic hash from patient_id + folder_name
            # This ensures the same patient+folder always gets the same case_id
            combined_input = f"{patient_id}_{normalized_folder}"
            hash_value = int(hashlib.md5(combined_input.encode()).hexdigest()[:8], 16)
            # Use last 4 digits of hash to ensure it's always 4 digits
            case_id_suffix = hash_value % 10000
            case_id = f"CBCT_{case_id_suffix:04d}"
            
            logger.info(f"Generated case_id {case_id} for patient {patient_id}, folder {normalized_folder}")
        
        # Validate case_id format matches requirements (CBCT_XXXX where XXXX is digits)
        if not case_id.startswith('CBCT_') or not case_id[5:].isdigit():
            return jsonify({'error': f'Invalid case_id format. Expected CBCT_XXXX (all digits), got {case_id}'}), 400
        
        # Check if case already exists (in annotation bucket)
        case_prefix = f'annotation_dataset/{case_id}/'
        case_manifest_key = f'{case_prefix}manifest.json'
        case_exists = False
        try:
            s3_client.head_object(Bucket=annotation_bucket, Key=case_manifest_key)
            case_exists = True
            logger.info(f"Case {case_id} already exists in bucket {annotation_bucket}")
            return jsonify({
                'success': True,
                'case_id': case_id,
                'patient_id': patient_id,
                'folder_name': folder_name,
                'already_exists': True,
                'message': f'Case {case_id} already exists'
            })
        except s3_client.exceptions.ClientError:
            case_exists = False
        
        # Create case
        try:
            logger.info(f"Creating case {case_id} from patient {patient_id}, folder {normalized_folder}")
            logger.info(f"Source bucket (patient data): {patient_bucket}, Dest bucket (annotation case): {annotation_bucket}")
            from scripts.convert_cbct_mpr_to_annotation_case import convert_cbct_mpr_to_annotation_case
            # Allow skipping file copy - API will load from source bucket if files don't exist
            skip_file_copy = data.get('skip_file_copy', True)  # Default to True to avoid slow copying
            
            result = convert_cbct_mpr_to_annotation_case(
                patient_id=patient_id,
                folder_name=normalized_folder,
                case_id=case_id,
                source_bucket=patient_bucket,      # Source: patient data in vizbrizpatients
                dest_bucket=annotation_bucket,      # Dest: annotation cases in vizbrizknowledgebase
                include_reference_views=True,
                dry_run=False,
                skip_file_copy=skip_file_copy
            )
            
            # Check if there were errors during conversion
            errors = result.get('errors', [])
            warnings = result.get('warnings', [])
            logger.info(f"Conversion result: files_copied={result.get('files_copied', 0)}, errors={len(errors)}, warnings={len(warnings)}")
            if errors:
                logger.error(f"Errors from conversion: {errors}")
            if warnings:
                logger.warning(f"Warnings from conversion: {warnings}")
            
            # Check for critical errors (like manifest creation failure)
            critical_errors = [e for e in errors if 'manifest' in e.lower()]
            
            if critical_errors:
                error_msg = '; '.join(critical_errors)
                logger.error(f"Critical errors during case creation: {error_msg}")
                return jsonify({
                    'success': False,
                    'error': f'Case creation failed: {error_msg}',
                    'case_id': case_id,
                    'errors': errors,
                    'warnings': warnings
                }), 500
            
            # Verify manifest was created - wait a moment for S3 eventual consistency
            time.sleep(0.5)  # Brief delay for S3 eventual consistency
            
            # Try multiple times to account for S3 eventual consistency
            manifest_found = False
            for attempt in range(3):
                try:
                    s3_client.head_object(Bucket=annotation_bucket, Key=case_manifest_key)
                    manifest_found = True
                    logger.info(f"✓ Verified manifest exists for case {case_id} in bucket {annotation_bucket} (attempt {attempt + 1})")
                    break
                except s3_client.exceptions.ClientError as e:
                    if attempt < 2:
                        logger.warning(f"Manifest not found on attempt {attempt + 1}, retrying...")
                        time.sleep(0.5)
                    else:
                        logger.error(f"⚠ Manifest not found after case creation for {case_id} in bucket {annotation_bucket} after 3 attempts: {e}")
                        # List objects in the case directory to see what was actually created
                        try:
                            response = s3_client.list_objects_v2(Bucket=annotation_bucket, Prefix=case_prefix, MaxKeys=10)
                            if 'Contents' in response:
                                logger.info(f"Files found in {case_prefix} in bucket {annotation_bucket}:")
                                for obj in response['Contents']:
                                    logger.info(f"  - {obj['Key']}")
                            else:
                                logger.warning(f"No files found in {case_prefix} in bucket {annotation_bucket}")
                        except Exception as list_error:
                            logger.error(f"Could not list objects in {case_prefix}: {list_error}")
            
            if not manifest_found:
                # If there were other errors, include them
                if errors:
                    error_details = '; '.join(errors)
                    return jsonify({
                        'success': False,
                        'error': f'Case creation failed - manifest not created: {error_details}',
                        'case_id': case_id,
                        'errors': errors
                    }), 500
                else:
                    return jsonify({
                        'success': False,
                        'error': f'Case creation completed but manifest.json was not created. This may be an S3 permissions issue. Please check server logs.',
                        'case_id': case_id
                    }), 500
            
            # If there were non-critical errors/warnings, still return success but note them
            if errors or warnings:
                logger.warning(f"Case {case_id} created with {len(errors)} errors and {len(warnings)} warnings")
                return jsonify({
                    'success': True,
                    'case_id': case_id,
                    'patient_id': patient_id,
                    'folder_name': folder_name,
                    'files_copied': result.get('files_copied', 0),
                    'already_exists': False,
                    'warnings': warnings,
                    'errors': errors,
                    'message': f'Case {case_id} created with {result.get("files_copied", 0)} files (some warnings occurred)'
                })
            
            logger.info(f"✓ Case {case_id} created successfully: {result.get('files_copied', 0)} files copied")
            
            return jsonify({
                'success': True,
                'case_id': case_id,
                'patient_id': patient_id,
                'folder_name': folder_name,
                'files_copied': result.get('files_copied', 0),
                'already_exists': False,
                'message': f'Case {case_id} created successfully with {result.get("files_copied", 0)} files'
            })
        except Exception as e:
            logger.error(f"Failed to create case {case_id}: {e}", exc_info=True)
            import traceback
            logger.error(traceback.format_exc())
            return jsonify({'error': f'Failed to create case: {str(e)}'}), 500
    except Exception as e:
        # Catch any unhandled exceptions at the route level
        logger.error(f"Unhandled exception in create_case_from_patient: {e}", exc_info=True)
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500


@cbct_annotator_bp.route('/annotator/api/mpr_analysis_from_patient', methods=['POST'])
@login_required
def analyze_mpr_from_patient():
    """
    Run MPR analysis on an existing case (case must already exist - use create_case_from_patient first).
    
    Body (JSON):
    {
        "case_id": "CBCT_XXXX" (required - case must exist),
        "slice_indices": [0, 10, 20, ...] (optional - if omitted, samples evenly)
        "num_slices": 60 (optional - default 60, max 60)
        "sampling_strategy": "even" | "range" | "custom" (default: "even")
    }
    """
    if not request.is_json:
        return jsonify({'error': 'Request must be JSON'}), 400
    
    data = request.get_json()
    case_id = data.get('case_id')
    slice_indices = data.get('slice_indices')
    num_slices = data.get('num_slices', 60)
    sampling_strategy = data.get('sampling_strategy', 'even')
    
    if not case_id:
        return jsonify({'error': 'case_id is required'}), 400
    
    if num_slices > 60:
        return jsonify({'error': 'Maximum 60 slices supported (3 chunks of 20)'}), 400
    
    try:
        # Get S3 client and bucket
        # Annotation cases are stored in vizbrizknowledgebase
        bucket = 'vizbrizknowledgebase'
        
        region = (current_app.config.get('AWS_REGION')
                  or os.getenv('AWS_REGION')
                  or 'us-east-1')
        s3_client = boto3.client('s3', region_name=region)
        
        # Check if case exists
        case_prefix = f'annotation_dataset/{case_id}/'
        case_manifest_key = f'{case_prefix}manifest.json'
        try:
            obj = s3_client.get_object(Bucket=bucket, Key=case_manifest_key)
            manifest = json.loads(obj['Body'].read().decode('utf-8'))
            logger.info(f"Found case {case_id} manifest with {manifest.get('num_slices', 0)} slices")
        except s3_client.exceptions.ClientError as e:
            logger.error(f"Case {case_id} manifest not found: {e}")
            # Also check if slices directory exists as fallback
            try:
                # List objects in case directory to see if it exists at all
                response = s3_client.list_objects_v2(Bucket=bucket, Prefix=case_prefix, MaxKeys=1)
                if 'Contents' in response and len(response['Contents']) > 0:
                    logger.warning(f"Case {case_id} directory exists but manifest.json is missing")
                    return jsonify({'error': f'Case {case_id} exists but manifest.json is missing. Please recreate the case.'}), 404
            except Exception:
                pass
            return jsonify({'error': f'Case {case_id} does not exist. Please create case first.'}), 404
        
        # Get slice count from case manifest
        # The convert script creates manifest with 'num_slices' field
        # But we also check for 'counts.axial' for backward compatibility
        num_slices_manifest = manifest.get('num_slices', 0)
        counts = manifest.get('counts', {})
        axial_count_from_counts = int(counts.get('axial', 0))
        
        # Use num_slices if available, otherwise fall back to counts.axial
        axial_count = num_slices_manifest if num_slices_manifest > 0 else axial_count_from_counts
        
        logger.info(f"Case {case_id} manifest: num_slices={num_slices_manifest}, counts.axial={axial_count_from_counts}, using axial_count={axial_count}")
        
        if axial_count == 0:
            # Try to count slices by listing the directory
            logger.warning(f"No slice count in manifest, attempting to count slices in S3...")
            try:
                slices_prefix = f'{case_prefix}slices/'
                response = s3_client.list_objects_v2(Bucket=bucket, Prefix=slices_prefix)
                if 'Contents' in response:
                    # Count axial slices (files matching axial_*.png)
                    axial_files = [obj for obj in response['Contents'] if 'axial_' in obj['Key'] and obj['Key'].endswith('.png')]
                    axial_count = len(axial_files)
                    logger.info(f"Found {axial_count} axial slices by listing S3 directory")
            except Exception as e:
                logger.error(f"Error counting slices: {e}")
            
            if axial_count == 0:
                return jsonify({'error': 'No axial slices found in case'}), 404
        
        # Use case slices directory - read from case, not patient MPR
        # Case slices are stored as: annotation_dataset/CBCT_XXXX/slices/axial_000.png, etc.
        base_prefix = f'{case_prefix}slices/'
        
        # Determine which slices to sample
        if slice_indices:
            selected_indices = slice_indices[:num_slices]
        elif sampling_strategy == 'even':
            # Sample evenly across available slices
            step = max(1, axial_count // num_slices) if num_slices < axial_count else 1
            selected_indices = list(range(0, min(axial_count, num_slices * step), step))[:num_slices]
        elif sampling_strategy == 'range':
            # Sample from a range
            start = data.get('range_start', axial_count // 3)
            end = data.get('range_end', 2 * axial_count // 3)
            step = max(1, (end - start) // num_slices) if num_slices < (end - start) else 1
            selected_indices = list(range(start, min(end, start + num_slices * step), step))[:num_slices]
        else:
            # Custom - use provided indices or default
            selected_indices = list(range(min(num_slices, axial_count)))
        
        logger.info(f"Sampling {len(selected_indices)} slices from case {case_id}")
        
        # Fetch images from case directory
        image_triplets = {}
        for idx in selected_indices:
            if idx >= axial_count:
                continue
            
            triplet = {}
            # Case stores slices as: axial_000.png, axial_001.png, etc.
            for plane in ['sagittal', 'coronal', 'axial']:
                slice_filename = f"{plane}_{idx:03d}.png"
                key = f'{base_prefix}{slice_filename}'
                try:
                    obj = s3_client.get_object(Bucket=bucket, Key=key)
                    triplet[plane] = obj['Body'].read()
                except s3_client.exceptions.NoSuchKey:
                    logger.warning(f"Missing {plane} view for slice {idx} in case {case_id}")
                    triplet[plane] = None
            
            # Only add if at least axial view exists
            if triplet.get('axial'):
                image_triplets[idx] = triplet
        
        if not image_triplets:
            return jsonify({'error': 'No valid image triplets found in case'}), 404
        
        logger.info(f"Found {len(image_triplets)} valid triplets from case {case_id}")
        
        # Group into chunks of 6 slices (18 images = 6 × 3 views, under 20 image limit)
        # Bedrock API limit is 20 images per request
        sorted_positions = sorted(image_triplets.keys())
        chunks = []
        chunk_size = 6  # 6 slices × 3 views = 18 images (under 20 limit)
        for i in range(0, len(sorted_positions), chunk_size):
            chunk_positions = sorted_positions[i:i+chunk_size]
            chunks.append(chunk_positions)
        
        max_chunks = 10  # Allow up to 10 chunks (60 slices total)
        if len(chunks) > max_chunks:
            return jsonify({'error': f'Too many slices ({len(sorted_positions)}). Maximum {max_chunks * chunk_size} slices supported.'}), 400
        
        # Process each chunk (same logic as chunked endpoint)
        chunk_summaries = []
        for chunk_idx, chunk_positions in enumerate(chunks, start=1):
            logger.info(f"Processing chunk {chunk_idx}/{len(chunks)}: {len(chunk_positions)} slice positions")
            
            prompt_text = MPR_CHUNK_ANALYSIS_PROMPT.strip()
            num_images = len(chunk_positions) * 3
            prompt_text += f"\n\nCHUNK {chunk_idx} of {len(chunks)}: Analyzing {len(chunk_positions)} slice positions ({num_images} images: {len(chunk_positions)}×3 views)."
            
            content = [{"type": "text", "text": prompt_text}]
            
            for pos in chunk_positions:
                triplet = image_triplets[pos]
                
                if triplet.get('sagittal'):
                    content.append({"type": "text", "text": f"Slice Position {pos} - Sagittal view:"})
                    content.append(_build_image_content(triplet['sagittal']))
                else:
                    content.append({"type": "text", "text": f"Slice Position {pos} - Sagittal view: MISSING"})
                
                if triplet.get('coronal'):
                    content.append({"type": "text", "text": f"Slice Position {pos} - Coronal view:"})
                    content.append(_build_image_content(triplet['coronal']))
                else:
                    content.append({"type": "text", "text": f"Slice Position {pos} - Coronal view: MISSING"})
                
                if triplet.get('axial'):
                    content.append({"type": "text", "text": f"Slice Position {pos} - Axial view:"})
                    content.append(_build_image_content(triplet['axial']))
                else:
                    content.append({"type": "text", "text": f"Slice Position {pos} - Axial view: MISSING"})
            
            try:
                response_text = _mpr_llm_service.invoke(
                    content,
                    max_tokens=MPR_ANALYSIS_MAX_TOKENS * 2,
                )
                chunk_parsed = _extract_json_payload(response_text)
                chunk_summaries.append({
                    'chunk_number': chunk_idx,
                    'summary': chunk_parsed,
                    'raw_response': response_text,
                })
                logger.info(f"Chunk {chunk_idx} analysis complete")
            except Exception as exc:
                logger.error(f"Chunk {chunk_idx} analysis failed: {exc}")
                return jsonify({
                    'success': False,
                    'error': f'Chunk {chunk_idx} analysis failed',
                    'details': str(exc),
                    'chunks_completed': chunk_idx - 1,
                }), 500
        
        # Final synthesis
        if len(chunk_summaries) > 1:
            logger.info("Synthesizing results from all chunks")
            synthesis_prompt = MPR_SYNTHESIS_PROMPT.strip()
            synthesis_prompt += "\n\nCHUNK SUMMARIES TO SYNTHESIZE:\n\n"
            
            for chunk_data in chunk_summaries:
                synthesis_prompt += f"=== CHUNK {chunk_data['chunk_number']} SUMMARY ===\n"
                synthesis_prompt += json.dumps(chunk_data['summary'], indent=2)
                synthesis_prompt += "\n\n"
            
            synthesis_content = [{"type": "text", "text": synthesis_prompt}]
            
            try:
                synthesis_response = _mpr_llm_service.invoke(
                    synthesis_content,
                    max_tokens=MPR_ANALYSIS_MAX_TOKENS,
                )
                final_parsed = _extract_json_payload(synthesis_response)
            except Exception as exc:
                logger.error(f"Final synthesis failed: {exc}")
                return jsonify({
                    'success': True,
                    'warning': 'Synthesis step failed, returning individual chunk summaries',
                    'chunks': chunk_summaries,
                    'raw_synthesis_response': synthesis_response if 'synthesis_response' in locals() else None,
                })
        else:
            final_parsed = chunk_summaries[0]['summary']
            synthesis_response = chunk_summaries[0]['raw_response']
        
        return jsonify({
            'success': True,
            'case_id': case_id,
            'slices_sampled': sorted_positions,
            'slices_analyzed': len(sorted_positions),  # Number of slices analyzed
            'images_analyzed': len(sorted_positions) * 3,  # Total images (slices × 3 views)
            'chunks_processed': len(chunks),
            'report': final_parsed,
            'chunk_summaries': chunk_summaries,
            'raw_response': synthesis_response if len(chunk_summaries) > 1 else chunk_summaries[0]['raw_response'],
        })
    except s3_client.exceptions.NoSuchKey:
        return jsonify({'error': f'Case {case_id} manifest not found'}), 404
    except Exception as exc:
        logger.error(f"Error analyzing MPR from case {case_id}: {exc}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': str(exc)}), 500


@cbct_annotator_bp.route('/annotator/api/patient/<int:patient_id>/mpr_folders', methods=['GET'])
@login_required
def list_patient_mpr_folders(patient_id):
    """
    List available MPR folders for a patient.
    
    Returns:
    {
        "success": true,
        "patient_id": 22650,
        "folders": [
            {"name": "pricilla", "has_manifest": true, "slice_count": 413}
        ]
    }
    """
    # Check patient access
    try:
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'error': 'Access denied'}), 403
    except Exception as e:
        logger.error(f"Error checking patient access: {e}")
        return jsonify({'error': 'Patient not found or access denied'}), 404
    
    # Patient MPR data is in vizbrizpatients bucket
    patient_bucket = current_app.config.get('S3_BUCKET') or current_app.config.get('S3_BUCKET_NAME')
    if not patient_bucket:
        return jsonify({'error': 'S3 bucket not configured'}), 500
    
    region = (current_app.config.get('AWS_REGION')
              or os.getenv('AWS_REGION')
              or 'us-east-1')
    s3_client = boto3.client('s3', region_name=region)
    
    base_prefix = f'patients/{patient_id}/imaging/cbct_mpr/'
    
    try:
        # List folders under cbct_mpr/
        response = s3_client.list_objects_v2(
            Bucket=patient_bucket,
            Prefix=base_prefix,
            Delimiter='/'
        )
        
        folders = []
        if 'CommonPrefixes' in response:
            for cp in response['CommonPrefixes']:
                folder_path = cp['Prefix']
                # Extract folder name
                parts = folder_path.rstrip('/').split('/')
                if len(parts) >= 5:
                    folder_name = parts[4]
                    
                    # Check for manifest
                    manifest_key = f"{folder_path}manifest.json"
                    has_manifest = False
                    slice_count = 0
                    
                    try:
                        manifest_obj = s3_client.get_object(Bucket=patient_bucket, Key=manifest_key)
                        manifest = json.loads(manifest_obj['Body'].read().decode('utf-8'))
                        has_manifest = True
                        slice_count = int(manifest.get('counts', {}).get('axial', 0))
                    except:
                        pass
                    
                    folders.append({
                        'name': folder_name,
                        'has_manifest': has_manifest,
                        'slice_count': slice_count
                    })
        
        return jsonify({
            'success': True,
            'patient_id': patient_id,
            'folders': folders
        })
        
    except Exception as exc:
        logger.error(f"Error listing MPR folders for patient {patient_id}: {exc}")
        return jsonify({'error': str(exc)}), 500


@cbct_annotator_bp.route('/api/case/<case_id>/llm_segment', methods=['POST'])
@login_required
def trigger_llm_segmentation(case_id):
    """
    Trigger LLM pre-annotation for a case/structure.
    
    This endpoint starts the pre-annotation process in the background.
    Use GET /api/case/<id>/llm_segment/status to check progress.
    
    Body (JSON):
    {
        "structure": "<supported-structure>",
        "slice_range": [start, end] (optional - if omitted, processes all slices)
    }
    
    Returns:
    {
        "success": true,
        "message": "Pre-annotation started",
        "case_id": "CBCT_0001",
        "structure": "airway"
    }
    """
    # Validate case_id format
    if not case_id.startswith('CBCT_') or not case_id[5:].isdigit():
        return jsonify({'error': f'Invalid case_id format. Expected CBCT_XXXX, got {case_id}'}), 400
    
    if not request.is_json:
        return jsonify({'error': 'Request must be JSON'}), 400
    
    data = request.get_json()
    structure = data.get('structure')
    slice_range = data.get('slice_range')  # Optional [start, end]
    slice_index = data.get('slice_index')  # Optional single slice index
    
    if not structure or structure not in SUPPORTED_STRUCTURES:
        return jsonify({'error': f'Invalid structure. Must be one of {SUPPORTED_STRUCTURES}'}), 400
    
    # Convert single slice_index to slice_range if provided
    if slice_index is not None and slice_range is None:
        slice_range = [slice_index, slice_index]
    
    # Import the batch processing function
    import subprocess
    import threading
    
    def run_preannotation():
        """Run pre-annotation in background thread."""
        try:
            cmd = [
                'python3',
                'scripts/llm_preannotate_case.py',
                case_id,
                structure
            ]
            
            if slice_range and len(slice_range) == 2:
                cmd.extend(['--slice-range', str(slice_range[0]), str(slice_range[1])])
            
            # Run the script - need to go up 3 levels from routes.py to get to vizbriz root
            # routes.py is at: vizbriz/flask_app/annotator/routes.py
            # Need cwd to be: vizbriz/
            routes_dir = os.path.dirname(os.path.abspath(__file__))  # vizbriz/flask_app/annotator
            flask_app_dir = os.path.dirname(routes_dir)  # vizbriz/flask_app
            vizbriz_root = os.path.dirname(flask_app_dir)  # vizbriz/
            
            result = subprocess.run(
                cmd,
                cwd=vizbriz_root,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                logger.info(f"Pre-annotation completed for {case_id}/{structure}")
            else:
                logger.error(f"Pre-annotation failed for {case_id}/{structure}: {result.stderr}")
                
        except Exception as e:
            logger.error(f"Error running pre-annotation: {e}")
    
    # Start background thread
    thread = threading.Thread(target=run_preannotation)
    thread.daemon = True
    thread.start()
    
    return jsonify({
        'success': True,
        'message': 'Pre-annotation started in background',
        'case_id': case_id,
        'structure': structure,
        'slice_range': slice_range
    })


@cbct_annotator_bp.route('/api/case/<case_id>/llm_segment/status', methods=['GET'])
@login_required
def get_llm_segmentation_status(case_id):
    """
    Get LLM segmentation status for a case/structure.
    
    Query params:
        structure: "<supported-structure>" (required)
    
    Returns:
    {
        "status": "pending" | "in_progress" | "completed" | "completed_with_errors" | "not_started",
        "progress": {
            "total_slices": 180,
            "processed": 45,
            "failed": 2
        },
        "structure": "airway",
        "processed_slices": [0, 1, 2, ...],
        "failed_slices": []
    }
    """
    # Validate case_id format
    if not case_id.startswith('CBCT_') or not case_id[5:].isdigit():
        return jsonify({'error': f'Invalid case_id format. Expected CBCT_XXXX, got {case_id}'}), 400
    
    structure = request.args.get('structure')
    if not structure or structure not in SUPPORTED_STRUCTURES:
        return jsonify({'error': f'Invalid or missing structure parameter. Choose from {SUPPORTED_STRUCTURES}'}), 400
    
    bucket = get_annotation_bucket()
    s3_client = get_s3_client()
    
    # Load manifest
    manifest_key = f"annotation_dataset/{case_id}/manifest.json"
    try:
        response = s3_client.get_object(Bucket=bucket, Key=manifest_key)
        manifest = json.loads(response['Body'].read().decode('utf-8'))
    except s3_client.exceptions.NoSuchKey:
        return jsonify({'error': f'Case {case_id} not found'}), 404
    except Exception as e:
        logger.error(f"Error loading manifest: {e}")
        return jsonify({'error': 'Failed to load case info'}), 500
    
    # Get LLM status from manifest
    structures = manifest.get('structures', {})
    structure_info = structures.get(structure, {})
    
    llm_status = structure_info.get('llm_status', 'not_started')
    processed_slices = structure_info.get('llm_processed_slices', [])
    failed_slices = structure_info.get('llm_failed_slices', [])
    total_slices = manifest.get('num_slices', manifest.get('num_axial_slices', 0))
    
    return jsonify({
        'status': llm_status,
        'progress': {
            'total_slices': total_slices,
            'processed': len(processed_slices),
            'failed': len(failed_slices)
        },
        'structure': structure,
        'processed_slices': processed_slices,
        'failed_slices': failed_slices
    })

