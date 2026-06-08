"""
Reports & Files Routes Blueprint

Dedicated routes for the Reports & Files tab functionality.
Separates this feature from the massive main_routes.py file.
"""

import logging
from functools import lru_cache
from pathlib import Path
from flask import Blueprint, jsonify, request, render_template, send_file, current_app
import os
import json
import threading
import re
from typing import Optional
from flask_login import login_required, current_user
from sqlalchemy import or_
from flask_app.extensions import db
from flask_app.models import Patient, File, AdminFile, PatientCaseEnvelope, Level4ReportHistory
from flask_app.services.file_categorization_service import FileCategorization
from flask_app.services.report_renderer_service import ReportRenderer
from flask_app.utils.cbct_mpr_generator import generate_cbct_mpr
from flask_app.services.bedrock_service import get_bedrock_service
import boto3
from datetime import datetime
import mimetypes
import io

# --- Cached S3 clients (build once, reuse). Presign client = SIGNING ONLY (local, no network);
# never route head_object/list through it (cross-region s3v4 retries make it slow). ---
from flask_app.utils.s3_presign_client import get_s3_client_for_presigning

@lru_cache(maxsize=8)
def _get_presign_client(region: str = None):
    """Cached IAM presign client — for generate_presigned_url signing only (long-lived links)."""
    return get_s3_client_for_presigning(region)

@lru_cache(maxsize=8)
def _get_s3_client(region: str = None):
    """Cached plain S3 client (instance role) for list/head/get network calls."""
    return boto3.client('s3', region_name=region)

try:
    import openai
except ImportError:  # pragma: no cover
    openai = None

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None

# Create blueprint
# NOTE: No URL prefix to maintain backward compatibility with existing template routes
reports_files_bp = Blueprint('reports_files', __name__)

logger = logging.getLogger(__name__)

_LEVEL4_SAMPLE_DIR = Path(os.getenv('LEVEL4_SAMPLE_TXT_DIR', '/home/ec2-user/patient_data/Report Examples/txt'))
_LEVEL4_REFERENCE_DIR = Path(os.getenv('LEVEL4_REFERENCE_DIR', '/home/ec2-user/vizbriz/uploads/level4_reference'))
_LEVEL4_OPENAI_KEY = os.getenv('LEVEL4_OPENAI_API_KEY') or os.getenv('OPENAI_API_KEY')
_LEVEL4_ANTHROPIC_KEY = os.getenv('LEVEL4_ANTHROPIC_API_KEY')

# Fixed, repo-shipped Level-4 structure templates (portable across servers)
_LEVEL4_STRUCTURE_TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent / 'resources' / 'level4_structure_templates'
)


def _level4_structure_templates_block() -> str:
    """Load bundled Level-4 structure templates from the repo (portable across servers)."""
    try:
        if not _LEVEL4_STRUCTURE_TEMPLATES_DIR.exists():
            logger.warning("Level-4 structure templates dir missing: %s", _LEVEL4_STRUCTURE_TEMPLATES_DIR)
            return "Structure templates unavailable."

        templates = []
        for idx, p in enumerate(sorted(_LEVEL4_STRUCTURE_TEMPLATES_DIR.glob('*.txt')), start=1):
            try:
                raw = p.read_text(encoding='utf-8', errors='replace').strip()
                if not raw:
                    continue
                # Strip metadata block if present
                if '<!--METADATA_END-->' in raw:
                    raw = raw.split('<!--METADATA_END-->', 1)[1].strip()
                templates.append(f"----- STRUCTURE TEMPLATE {idx}: {p.name} -----\n{raw}")
            except Exception as exc:
                logger.warning("Failed to read structure template %s: %s", p, exc)
        return "\n\n".join(templates) if templates else "Structure templates unavailable."
    except Exception as exc:
        logger.warning("Failed loading structure templates: %s", exc)
        return "Structure templates unavailable."

# Shared Level-4 Report Template (v1.0)
# This template ensures consistency between normalized historical reports and newly generated reports
# IMPORTANT: Both _LEVEL4_SYSTEM_PROMPT and _NORMALIZATION_SYSTEM_PROMPT must use this exact template
_LEVEL4_REPORT_TEMPLATE = """
# OSA Data Assessment Report

## Personal Details
| Personal details | Gender: | <value or "Not provided"> | Age: | <value or "Not provided"> | BMI: | <value or "Not provided"> |

## Clinical Background, Complaints & Goals
| Clinical background: | <value or "Not provided"> |
| Patient complaints: | <value or "Not provided"> |
| Patient goals: | <value or "Not provided"> |

## ENT Findings
<value or "Not provided.">

## Sleep Study Data
| Metric | Value |
|--------|-------|
| AHI | <value or "Not provided"> |
| REM AHI | <value or "Not provided"> |
| RDI | <value or "Not provided"> |
| REM RDI | <value or "Not provided"> |
| ODI | <value or "Not provided"> |
| REM ODI | <value or "Not provided"> |
| Supine AHI | <value or "Not provided"> |
| Supine RDI | <value or "Not provided"> |
| Supine ODI | <value or "Not provided"> |
| Non-Supine AHI | <value or "Not provided"> |
| Snoring % | <value or "Not provided"> |
| O2 Nadir | <value or "Not provided"> |
| Sleep Efficiency | <value or "Not provided"> |
| Total Sleep Time | <value or "Not provided"> |

## Observations
• <bullet-point summary of OSA severity & patterns>
• <bullet of positional dependence>
• <bullet of desaturation severity>
• <bullet of snoring patterns>
• <bullet of apnea/hypopnea breakdown>
• <bullet of any clinically relevant pattern>
• <If REM AHI = 0.0 or REM metrics are zero/missing, MUST include: "REM data may be limited or insufficient for interpretation. REM AHI of 0.0 may indicate missing REM scoring, very little REM recorded, device did not score REM, or true zero events in REM (unlikely).">

If data missing → write: "Sleep observations not provided."

## Structural Observations from Imaging Data
**Important Note:** This section presents observations based on imaging data and does not constitute an official radiological interpretation. Any imaging findings must be reviewed by a certified radiologist or physician before making clinical decisions.

| Structure | Finding |
|-----------|---------|
| Primary obstruction site | <value or "Not provided"> |
| Soft palate / uvula | <value or "Not provided"> |
| Tongue base | <value or "Not provided"> |
| Bite / Jaw | <value or "Not provided"> |
| Arches | <value or "Not provided"> |
| Hyoid | <value or "Not provided"> |
| Nose / Sinus | <value or "Not provided"> |

**Conclusion:**
<high-level imaging interpretation or "Not provided.">

## Possible Treatment Considerations
• <bullet of airway stabilization>
• <bullet of tongue positioning>
• <bullet of nasal airflow optimization>
• <bullet of positional therapy>
• <bullet of weight management if BMI > 30>
• <bullet of CPAP vs OAT logic>

If data missing → write general, non-diagnostic considerations.

## Device Design Data Considerations
| Parameter | Data-Based Consideration |
|-----------|-------------------------|
| Mandibular Advancement (mm) | <value or "Not provided"> |
| Vertical Opening (mm) | <value or "Not provided"> |
| Protrusive Range (%) | <value or "Not provided"> |
| Coverage | <value or "Not provided"> (e.g., incisors to molars) |
| Material | <value or "Not provided"> |
| Titration Protocol | <value or "Not provided"> |
| Clinical Notes | <value or "Not provided"> |
| Limitations Due to Anatomy | <value or "Not provided"> |

## Recommendations for Further Evaluation
• <ENT evaluation if nasal/sinus issues>
• <Follow-up sleep test after 90 days>
• <Weight management if BMI > 30>
• <DISE if airway unclear>

If nothing available → write: "No further evaluation recommendations provided."

## Oral Appliance Options for Consideration
| Device | | Key Features |
| Emerald Herbst | | Strong, durable, high-density acrylic |
| Respire Herbst Pink AT | | Metal mesh embedded, high-density acrylic |
| Daynaflex Herbst | | Enhanced tongue space, stain-resistant PMMA |

**Final Disclaimer**

This AI-generated report assists in analyzing sleep and anatomical data. It does not replace physician evaluation, DISE assessment, or radiologic interpretation. All findings must be reviewed by a qualified healthcare provider before making clinical decisions. This report is for informational purposes only and should not replace professional medical judgment or clinical decision-making.
"""

# Level-4 System Prompt for NEW report generation (uses shared template)
_LEVEL4_SYSTEM_PROMPT = """You are Dr. BRIZ — an AI-powered clinical analyst specialized in Dental Sleep Medicine, OSA physiology, CBCT imaging interpretation (non-diagnostic), airway biomechanics, and oral appliance therapy.

Your job is to generate a Level-4 OSA Data Assessment Report that follows the EXACT standardized format defined below. This format ensures consistency with normalized historical reports for knowledge base ingestion.

CRITICAL RULES:

1. DO NOT FABRICATE CLINICAL VALUES
   - Use ONLY data from canonical JSON provided
   - If a field is missing, write "Not provided"
   - NEVER infer or assume values (no O2 nadir, positional AHI, BMI, etc. unless explicitly in JSON)

2. PRESERVE CLINICAL VOICE
   - Use anatomical correctness
   - Use medical-grade terminology
   - Maintain consistent phrasing

3. NEVER INFER DATA NOT EXPLICITLY STATED
   - No assumptions about any clinical values
   - If not in the JSON, mark as "Not provided"

### STRICT DATA RULES (MANDATORY)

You MUST obey the following rules when generating the Level-4 report:

1. **EVERY clinical value must come ONLY from PATIENT_JSON.**
   - If a numeric value (e.g., AHI, ODI, BMI, snoring dB, overjet, sinus findings, REM AHI, etc.) is NOT in PATIENT_JSON, you MUST NOT invent, infer, assume, or copy it from example reports.

2. **NEVER borrow text, values, or measurements from example Level-4 reports.**
   - Example reports define STYLE ONLY.
   - They do NOT provide clinical content for this patient.

3. **If patient JSON does NOT contain a measurement, write: "Not provided."**
   - Never guess or fill missing values.
   - Do not import values from prior examples.

4. **STRICT ANATOMY RULE**
   - The "Structural Observations" section may ONLY use fields under:
     `patient.observations.anatomy_imaging`
     If something is missing, say "Not provided in available imaging data."

5. **STRICT TREATMENT RULE**
   - Treatment recommendations must be consistent with patient JSON.
   - DO NOT apply treatments seen in example reports unless the patient's JSON supports them.

6. **MEDICATION RULE**
   - Only list medications explicitly present in patient JSON exactly as written.
   - Do NOT merge with example medications.

7. **NO INFERRED VALUES**
   Forbidden examples:
     - Snoring dB if not in JSON.
     - Overjet/overbite degree if not in JSON.
     - REM RDI unless JSON contains it.
     - "Severe TMJ" unless JSON contains TMJ findings.

8. **STRICT FORMAT RULE**
   - Use Level-4 structure exactly.
   - If sample reports contradict system structure → SYSTEM structure wins.

9. **EXPLICIT VALUE REQUIREMENT**
   - If the patient JSON does not explicitly contain a value, YOU MUST NOT output it in any form.
   - This means: no partial values, no approximations, no "likely" or "probably" values.
   - Only output what is explicitly present in the JSON.

### If any clinical field is absent, empty, or unclear in the JSON:
Write: **"Not provided."**

4. ALWAYS INCLUDE ALL MANDATORY SECTIONS
   - Even if empty, include every section
   - Follow the exact section order below

5. ALWAYS INCLUDE DISCLAIMERS VERBATIM
   - Use the exact disclaimer text provided

OUTPUT FORMAT (MANDATORY - FOLLOW EXACTLY):
{template}

FORMATTING RULES:
- Single Markdown document
- Clean tables
- No bullet formatting drift
- No duplicate headings
- No extra commentary
- No images, footnotes, HTML, physician names, or PHI

Your output must contain ALL sections in this exact order, with exact section headings, even if data is missing. This ensures consistency with normalized historical reports.""".format(template=_LEVEL4_REPORT_TEMPLATE)

# Normalization System Prompt (v1.0) - for normalizing historical reports
_NORMALIZATION_SYSTEM_PROMPT = """You are a clinical report normalization specialist. Your job is to normalize ANY raw OSA clinical report into a standardized Level-4 OSA Report format suitable for knowledge base ingestion.

CRITICAL RULES:

1. DO NOT FABRICATE CLINICAL VALUES
   - If a field is missing, write "Not provided"
   - NEVER infer or assume values (no O2 nadir, positional AHI, BMI, etc. unless explicitly stated)

2. PRESERVE CLINICAL VOICE
   - Use anatomical correctness
   - Use medical-grade terminology
   - Maintain consistent phrasing

3. NEVER INFER DATA NOT EXPLICITLY STATED
   - No assumptions about any clinical values
   - If not in the source, mark as "Not provided"

4. ALWAYS INCLUDE ALL MANDATORY SECTIONS
   - Even if empty, include every section
   - Follow the exact section order

5. ALWAYS INCLUDE DISCLAIMERS VERBATIM
   - Top disclaimer and final disclaimer must be preserved exactly

OUTPUT FORMAT (MANDATORY - FOLLOW EXACTLY):

# OSA Data Assessment Report

## Personal Details
| Personal details | Gender: | <value or "Not provided"> | Age: | <value or "Not provided"> | BMI: | <value or "Not provided"> |

## Clinical Background, Complaints & Goals
| Clinical background: | <value or "Not provided"> |
| Patient complaints: | <value or "Not provided"> |
| Patient goals: | <value or "Not provided"> |

## ENT Findings
<value or "Not provided.">

## Sleep Study Data
| AHI | <value or "Not provided"> | REM AHI | <value or "Not provided"> |
| RDI | <value or "Not provided"> | REM RDI | <value or "Not provided"> |
| ODI | <value or "Not provided"> | REM ODI | <value or "Not provided"> |
| Supine AHI | <value or "Not provided"> | Supine RDI | <value or "Not provided"> |
| Supine ODI | <value or "Not provided"> | Non-Supine AHI | <value or "Not provided"> |
| Snoring % | <value or "Not provided"> | O2 Nadir | <value or "Not provided"> |
| Sleep Efficiency | <value or "Not provided"> | Total Sleep Time | <value or "Not provided"> |

## Observations
• <bullet-point summary of OSA severity & patterns>
• <bullet of positional dependence>
• <bullet of desaturation severity>
• <bullet of snoring patterns>
• <bullet of apnea/hypopnea breakdown>
• <bullet of any clinically relevant pattern>
• <If REM AHI = 0.0 or REM metrics are zero/missing, MUST include: "REM data may be limited or insufficient for interpretation. REM AHI of 0.0 may indicate missing REM scoring, very little REM recorded, device did not score REM, or true zero events in REM (unlikely).">

If data missing → write: "Sleep observations not provided."

## Structural Observations from Imaging Data
**Important Note:** This section presents observations based on imaging data and does not constitute an official radiological interpretation. Any imaging findings must be reviewed by a certified radiologist or physician before making clinical decisions.

| Structure | Finding |
|-----------|---------|
| Primary obstruction site | <value or "Not provided"> |
| Soft palate / uvula | <value or "Not provided"> |
| Tongue base | <value or "Not provided"> |
| Bite / Jaw | <value or "Not provided"> |
| Arches | <value or "Not provided"> |
| Hyoid | <value or "Not provided"> |
| Nose / Sinus | <value or "Not provided"> |

**Conclusion:**
<high-level imaging interpretation or "Not provided.">

## Possible Treatment Considerations
• <bullet of airway stabilization>
• <bullet of tongue positioning>
• <bullet of nasal airflow optimization>
• <bullet of positional therapy>
• <bullet of weight management if BMI > 30>
• <bullet of CPAP vs OAT logic>

If data missing → write general, non-diagnostic considerations.

## Device Design Data Considerations
| Parameter | Data-Based Consideration |
|-----------|-------------------------|
| Mandibular Advancement (mm) | <value or "Not provided"> |
| Vertical Opening (mm) | <value or "Not provided"> |
| Protrusive Range (%) | <value or "Not provided"> |
| Coverage | <value or "Not provided"> (e.g., incisors to molars) |
| Material | <value or "Not provided"> |
| Titration Protocol | <value or "Not provided"> |
| Clinical Notes | <value or "Not provided"> |
| Limitations Due to Anatomy | <value or "Not provided"> |

## Recommendations for Further Evaluation
• <ENT evaluation if nasal/sinus issues>
• <Follow-up sleep test after 90 days>
• <Weight management if BMI > 30>
• <DISE if airway unclear>

If nothing available → write: "No further evaluation recommendations provided."

## Oral Appliance Options for Consideration
| Device | | Key Features |
| Emerald Herbst | | Strong, durable, high-density acrylic |
| Respire Herbst Pink AT | | Metal mesh embedded, high-density acrylic |
| Daynaflex Herbst | | Enhanced tongue space, stain-resistant PMMA |

**Disclaimer**
This AI-generated report assists in analyzing medical imaging and clinical data. It does not constitute a medical diagnosis or treatment recommendation. All clinical decisions must be made by qualified healthcare professionals. This report is for informational purposes only and should not replace professional medical judgment.

FORMATTING RULES:
- Single Markdown document
- Clean tables
- No bullet formatting drift
- No duplicate headings
- No extra commentary
- No images, footnotes, HTML, physician names, or PHI

Your output must contain ALL sections in this exact order, with exact section headings, even if data is missing."""

# In-memory status tracking for MPR generation
# Format: {(patient_id, folder_name): {'status': 'running'|'completed'|'failed', 'message': str, 'start_time': datetime}}
_mpr_generation_status = {}
_mpr_status_lock = threading.Lock()


@reports_files_bp.route('/api/patient/<int:patient_id>/files', methods=['GET'])
@login_required
def get_patient_files_summary(patient_id):
    """
    Return a flat list of patient files (files + adminfiles) for Reports & Files tab.
    
    Query params:
        - include_cbct: '1' or 'true' to include CBCT files (default excluded)
        - exclude_categories: comma-separated list of categories to exclude
    
    Returns JSON with flat array of files
    """
    try:
        # Permission check
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        logger.info(f"Fetching files summary for patient {patient_id}")
        
        # Query files and adminfiles
        file_rows = File.query.filter_by(patient_id=patient_id).order_by(File.upload_date.desc()).all()
        admin_rows = AdminFile.query.filter_by(patient_id=patient_id).order_by(AdminFile.upload_date.desc()).all()
        
        def serialize(row, source):
            """Serialize file/adminfile row to dict"""
            result = {
                'id': row.id,
                'name': getattr(row, 'name', ''),
                'file_type': getattr(row, 'file_type', ''),
                'category': getattr(row, 'category', None) or getattr(row, 'file_category', None),
                'subcategory': getattr(row, 'subcategory', None),
                'upload_date': getattr(row, 'upload_date', None).isoformat() if getattr(row, 'upload_date', None) else None,
                'file_size': getattr(row, 'file_size', None),
                'source': source,
                'source_table': source,  # Alias for compatibility
                's3_key': getattr(row, 's3_key', None),
                'view_url': _generate_presigned_url(getattr(row, 's3_key', None), inline=True, expires_in=3600, verify_exists=False) if getattr(row, 's3_key', None) and str(getattr(row, 's3_key', None)).strip() not in ['None', 'null', 'NULL', ''] else None
            }
            
            # For adminfiles, include file_category for report level detection
            if source == 'adminfiles':
                result['file_category'] = getattr(row, 'file_category', None)
            
            return result
        
        payload = [serialize(r, 'files') for r in file_rows] + [serialize(r, 'adminfiles') for r in admin_rows]
        
        # Filtering logic
        include_cbct = request.args.get('include_cbct', '0') in ('1', 'true', 'True')
        exclude_param = request.args.get('exclude_categories')
        exclude_set = set()
        if exclude_param:
            exclude_set = {c.strip().lower() for c in exclude_param.split(',') if c.strip()}
        
        # Exclude CBCT by default unless explicitly included
        if not include_cbct:
            exclude_set.add('cbct')
        
        if exclude_set:
            payload = [
                p for p in payload
                if ((p.get('category') or '').lower() not in exclude_set
                    and (p.get('subcategory') or '').lower() not in exclude_set)
            ]
        
        logger.info(f"Returning {len(payload)} files for patient {patient_id}")
        return jsonify({'success': True, 'files': payload})
        
    except Exception as e:
        logger.error(f"Error fetching files summary for patient {patient_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@reports_files_bp.route('/api/patient/<int:patient_id>/cbct_folders', methods=['GET'])
@login_required
def get_cbct_folders(patient_id):
    """
    Get CBCT folders for a patient
    Returns list of CBCT DICOM folders from S3
    """
    try:
        # Permission check
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        logger.info(f"Fetching CBCT folders for patient {patient_id}")
        
        # Get CBCT folders from database instead of S3
        # Look for files with s3_key containing '/imaging/cbct/' to identify CBCT folders
        # Only include DICOM files (.dcm or .dicom extensions) to filter out ZIP files and other non-DICOM files
        cbct_files = File.query.filter(
            File.patient_id == patient_id,
            File.s3_key.like('%/imaging/cbct/%'),
            or_(
                File.s3_key.ilike('%.dcm'),
                File.s3_key.ilike('%.dicom'),
                File.s3_key.ilike('%.dcom')
            )
        ).all()
        
        # Group files by folder name and calculate folder sizes
        folder_data = {}
        for file in cbct_files:
            if file.s3_key:
                # Extract folder name from s3_key like "patients/10299/imaging/cbct/20250207_163341_69/file.dcm"
                parts = file.s3_key.split('/')
                if len(parts) >= 5 and parts[2] == 'imaging' and parts[3] == 'cbct':  # patients/{id}/imaging/cbct/{folder_name}/...
                    folder_name = parts[4]
                    if folder_name not in folder_data:
                        folder_data[folder_name] = {
                            'name': folder_name,
                            'total_size': 0,
                            'file_count': 0,
                            'first_file_date': None  # Track the earliest file date
                        }
                    
                    # Add file size to folder total
                    if file.file_size:
                        folder_data[folder_name]['total_size'] += file.file_size
                    folder_data[folder_name]['file_count'] += 1
                    
                    # Track the earliest file date (from first file or earliest created_at)
                    file_date = None
                    if hasattr(file, 'created_at') and file.created_at:
                        file_date = file.created_at
                    elif hasattr(file, 'upload_date') and file.upload_date:
                        file_date = file.upload_date
                    
                    if file_date:
                        if folder_data[folder_name]['first_file_date'] is None:
                            folder_data[folder_name]['first_file_date'] = file_date
                        elif file_date < folder_data[folder_name]['first_file_date']:
                            folder_data[folder_name]['first_file_date'] = file_date
                    
                    # Debug logging
                    logger.info(f"CBCT DICOM file: {file.s3_key} -> folder: {folder_name}, size: {file.file_size}, count: {folder_data[folder_name]['file_count']}")
        
        # Convert to list format and format dates
        # Only include folders that have at least one DICOM file
        from datetime import datetime
        from flask_app.utils.cbct_prezip_manager import prezip_exists
        
        folders = []
        for folder_name, folder_info in folder_data.items():
            # Skip folders with no DICOM files
            if folder_info['file_count'] == 0:
                logger.info(f"Skipping folder {folder_name} - no DICOM files found")
                continue
                
            folder_obj = {
                'name': folder_info['name'],
                'total_size': folder_info['total_size'],
                'file_count': folder_info['file_count']
            }
            
            # Format the date if available
            if folder_info.get('first_file_date'):
                try:
                    if isinstance(folder_info['first_file_date'], datetime):
                        folder_obj['date'] = folder_info['first_file_date'].strftime('%Y-%m-%d')
                    elif isinstance(folder_info['first_file_date'], str):
                        # Try to parse and format
                        parsed_date = datetime.fromisoformat(folder_info['first_file_date'].replace('Z', '+00:00'))
                        folder_obj['date'] = parsed_date.strftime('%Y-%m-%d')
                    else:
                        folder_obj['date'] = str(folder_info['first_file_date'])
                except Exception as e:
                    logger.warning(f"Error formatting date for folder {folder_name}: {e}")
                    folder_obj['date'] = ''
            else:
                folder_obj['date'] = ''
            
            # Check if pre-zip exists in S3
            folder_obj['prezip_ready'] = prezip_exists(patient_id, folder_info['name'])
            
            folders.append(folder_obj)
        
        logger.info(f"Found {len(folders)} CBCT folders for patient {patient_id} from database")
        for folder in folders:
            logger.info(f"Folder: {folder['name']}, size: {folder['total_size']}, count: {folder['file_count']}, date: {folder.get('date', 'N/A')}")
        return jsonify({
            'success': True,
            'folders': folders
        })
            
    except Exception as e:
        logger.error(f"Error fetching CBCT folders for patient {patient_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@reports_files_bp.route('/api/patient/<int:patient_id>/cbct_folders_s3', methods=['GET'])
@login_required
def get_cbct_folders_s3(patient_id):
    """
    Get CBCT folders for a patient directly from S3.
    This is useful for showing folders uploaded via RAR extraction (which bypasses the database).
    """
    try:
        # Permission check
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        logger.info(f"Fetching CBCT folders from S3 for patient {patient_id}")
        
        import boto3
        from flask_app.utils.cbct_prezip_manager import prezip_exists
        
        s3_client = _get_s3_client()
        bucket = os.environ.get('S3_BUCKET_NAME') or current_app.config.get('S3_BUCKET_NAME')
        
        prefix = f"patients/{patient_id}/imaging/cbct/"
        
        # Find unique folder names by listing all objects
        folder_data = {}
        paginator = s3_client.get_paginator('list_objects_v2')
        
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            if 'Contents' in page:
                for obj in page['Contents']:
                    key = obj['Key']
                    # Extract folder name from key like "patients/123/imaging/cbct/FolderName/file.dcm"
                    relative_path = key[len(prefix):]
                    if '/' in relative_path:
                        folder_name = relative_path.split('/')[0]
                        # Skip the prezip folder
                        if folder_name and folder_name != 'cbct_prezip':
                            if folder_name not in folder_data:
                                folder_data[folder_name] = {
                                    'name': folder_name,
                                    'total_size': 0,
                                    'file_count': 0
                                }
                            folder_data[folder_name]['total_size'] += obj.get('Size', 0)
                            folder_data[folder_name]['file_count'] += 1
        
        # Convert to list and check prezip status
        folders = []
        for folder_name, info in folder_data.items():
            folders.append({
                'name': info['name'],
                'total_size': info['total_size'],
                'file_count': info['file_count'],
                'has_prezip': prezip_exists(patient_id, folder_name)
            })
        
        # Sort by name
        folders.sort(key=lambda x: x['name'])
        
        logger.info(f"Found {len(folders)} CBCT folders from S3 for patient {patient_id}")
        
        return jsonify({
            'success': True,
            'folders': folders
        })
            
    except Exception as e:
        logger.error(f"Error fetching CBCT folders from S3 for patient {patient_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@reports_files_bp.route('/api/patient/<int:patient_id>/trigger_cbct_prezip', methods=['POST'])
@login_required
def trigger_cbct_prezip(patient_id):
    """
    Trigger pre-zipping of CBCT folders for a patient.
    This is called after CBCT files are uploaded to create ZIP files ahead of time,
    avoiding timeouts when downloading large CBCT folders (>1.9GB).
    
    The pre-zipping runs in a background thread and doesn't block the response.
    
    Request JSON (optional):
        {
            "folder_name": "specific_folder_name"  // Optional: pre-zip only this folder
        }
    
    Returns:
        {
            "success": true,
            "message": "Pre-zip triggered for patient X"
        }
    """
    logger.info(f"=== CBCT Pre-zip trigger received for patient {patient_id} ===")
    try:
        # Permission check
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            logger.warning(f"Access denied for patient {patient_id}")
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        logger.info(f"Permission check passed for patient {patient_id}")
        
        # Import the pre-zip manager
        from flask_app.utils.cbct_prezip_manager import trigger_prezip_background
        
        # Get optional folder name from request (silent=True to handle empty body)
        data = request.get_json(silent=True) or {}
        folder_name = data.get('folder_name')
        
        logger.info(f"Calling trigger_prezip_background for patient {patient_id}")
        
        # Trigger pre-zipping in background
        trigger_prezip_background(patient_id, app=current_app._get_current_object())
        
        message = f"Pre-zip triggered for patient {patient_id}"
        if folder_name:
            message += f" (folder: {folder_name})"
        
        logger.info(message)
        
        return jsonify({
            'success': True,
            'message': message
        })
        
    except Exception as e:
        logger.error(f"Error triggering CBCT pre-zip for patient {patient_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500



@reports_files_bp.route('/api/patient/<int:patient_id>/reports/<int:report_id>/html', methods=['GET'])
@login_required
def get_report_html(patient_id, report_id):
    """
    Get HTML-rendered version of a report PDF
    
    Query params:
        - table: 'files' or 'adminfiles' (default: 'adminfiles')
        - force_rerender: 'true' to bypass cache
    
    Returns HTML content or JSON error
    """
    try:
        # Permission check
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        file_table = request.args.get('table', 'adminfiles')
        force_rerender = request.args.get('force_rerender', 'false').lower() == 'true'
        
        logger.info(f"Rendering report {report_id} from {file_table} for patient {patient_id}")
        
        # Get file to determine report level
        if file_table == 'adminfiles':
            file_obj = AdminFile.query.get_or_404(report_id)
            report_level = getattr(file_obj, 'report_level', None)
        else:
            file_obj = File.query.get_or_404(report_id)
            report_level = None
        
        # Verify file belongs to patient
        if file_obj.patient_id != patient_id:
            return jsonify({'success': False, 'error': 'File does not belong to patient'}), 403
        
        # Render report
        html_content, metadata = ReportRenderer.render_report_to_html(
            file_id=report_id,
            file_table=file_table,
            patient_id=patient_id,
            report_level=report_level,
            force_rerender=force_rerender
        )
        
        # Log access
        _log_file_access(
            user_id=current_user.id,
            patient_id=patient_id,
            file_id=report_id,
            file_table=file_table,
            file_type='report',
            access_type='render'
        )
        
        # Return HTML with metadata in headers
        response = current_app.make_response(html_content)
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        response.headers['X-Cached'] = 'true' if metadata.get('cached') else 'false'
        response.headers['X-Render-Time'] = str(metadata.get('render_time_ms', 0))
        
        return response
        
    except Exception as e:
        logger.error(f"Error rendering report {report_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@reports_files_bp.route('/api/files/<int:file_id>/preview', methods=['GET'])
@login_required
def preview_file(file_id):
    """
    Preview a file (PDF, DOCX, images)
    
    Query params:
        - table: 'files' or 'adminfiles'
        - download: 'true' to force download instead of preview
    
    Returns file content or redirect to S3
    """
    try:
        file_table = request.args.get('table', 'files')
        force_download = request.args.get('download', 'false').lower() == 'true'
        
        # Get file
        if file_table == 'adminfiles':
            file_obj = AdminFile.query.get_or_404(file_id)
        else:
            file_obj = File.query.get_or_404(file_id)
        
        # Permission check
        patient = Patient.query.get_or_404(file_obj.patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Check if file is previewable
        filename = file_obj.name.lower()
        is_previewable = any(filename.endswith(ext) for ext in ['.pdf', '.jpg', '.jpeg', '.png', '.gif', '.txt'])
        
        if not is_previewable or force_download:
            # Return download URL
            download_url = _generate_presigned_url(file_obj.s3_key, expires_in=3600) if file_obj.s3_key and str(file_obj.s3_key).strip() not in ['None', 'null', 'NULL', ''] else None
            return jsonify({
                'success': True,
                'previewable': False,
                'download_url': download_url
            })
        
        # For PDF, try to render to HTML if it's a report
        if filename.endswith('.pdf') and file_table == 'adminfiles':
            try:
                html_content, metadata = ReportRenderer.render_report_to_html(
                    file_id=file_id,
                    file_table=file_table,
                    patient_id=file_obj.patient_id
                )
                
                # Log access
                _log_file_access(
                    user_id=current_user.id,
                    patient_id=file_obj.patient_id,
                    file_id=file_id,
                    file_table=file_table,
                    file_type='document',
                    access_type='preview'
                )
                
                return html_content
                
            except Exception as e:
                logger.warning(f"Could not render PDF to HTML, falling back to direct view: {e}")
        
        # For images and other previewable files, return presigned URL
        preview_url = _generate_presigned_url(file_obj.s3_key, expires_in=3600, inline=True) if file_obj.s3_key and str(file_obj.s3_key).strip() not in ['None', 'null', 'NULL', ''] else None
        
        # Log access
        _log_file_access(
            user_id=current_user.id,
            patient_id=file_obj.patient_id,
            file_id=file_id,
            file_table=file_table,
            file_type='document',
            access_type='preview'
        )
        
        return jsonify({
            'success': True,
            'previewable': True,
            'preview_url': preview_url,
            'file_name': file_obj.name
        })
        
    except Exception as e:
        logger.error(f"Error previewing file {file_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@reports_files_bp.route('/viewer/stl/<int:file_id>', methods=['GET'])
@login_required
def view_stl(file_id):
    """
    Render STL viewer page
    
    Query params:
        - table: 'files' or 'adminfiles'
    """
    try:
        file_table = request.args.get('table', 'files')
        
        # Get file
        if file_table == 'adminfiles':
            file_obj = AdminFile.query.get_or_404(file_id)
        else:
            file_obj = File.query.get_or_404(file_id)
        
        # Permission check
        patient = Patient.query.get_or_404(file_obj.patient_id)
        if not current_user.can_access_patient(patient):
            return "Access denied", 403
        
        # Generate presigned URL for STL file
        stl_url = _generate_presigned_url(file_obj.s3_key, expires_in=3600) if file_obj.s3_key and str(file_obj.s3_key).strip() not in ['None', 'null', 'NULL', ''] else None
        
        # Log access
        _log_file_access(
            user_id=current_user.id,
            patient_id=file_obj.patient_id,
            file_id=file_id,
            file_table=file_table,
            file_type='stl',
            access_type='view'
        )
        
        return render_template(
            'stl_viewer.html',
            file_id=file_id,
            file_name=file_obj.name,
            stl_url=stl_url,
            patient_id=file_obj.patient_id,
            patient_name=patient.name
        )
        
    except Exception as e:
        logger.error(f"Error loading STL viewer for file {file_id}: {e}")
        return f"Error loading STL viewer: {str(e)}", 500


@reports_files_bp.route('/api/patient/<int:patient_id>/cbct/<path:folder_name>/files', methods=['GET'])
@login_required
def get_cbct_folder_files(patient_id, folder_name):
    """
    Return DICOM files for a CBCT folder.

    Folder structure (S3 key):
        patients/<patient_id>/imaging/cbct/<folder_name>/.../<file>.dcm
    Only the first folder level after ``cbct`` should be considered.
    Source of truth is ``files`` table only.
    """
    try:
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        normalized_folder = folder_name.strip('/').split('/')[0]
        folder_prefix = f'patients/{patient_id}/imaging/cbct/{normalized_folder}/'

        logger.info(
            "Fetching CBCT files for patient %s, folder %s (prefix: %s)",
            patient_id,
            normalized_folder,
            folder_prefix
        )

        file_rows = File.query.filter(
            File.patient_id == patient_id,
            File.s3_key.like(f'{folder_prefix}%')
        ).order_by(File.s3_key.asc()).all()

        logger.info(
            "CBCT folder %s - raw rows from files table: %s",
            normalized_folder,
            len(file_rows)
        )

        dicom_files = []
        for row in file_rows:
            s3_key = getattr(row, 's3_key', None)
            if not s3_key:
                continue

            filename = s3_key.split('/')[-1]
            filename_lower = filename.lower()
            if not (filename_lower.endswith('.dcm')
                    or filename_lower.endswith('.dicom')
                    or filename_lower.endswith('.dcom')):
                continue

            presigned_url = _generate_presigned_url(s3_key, expires_in=3600)
            if not presigned_url:
                logger.warning("Failed to generate presigned URL for %s", s3_key)
                continue

            dicom_files.append({
                'id': row.id,
                'name': getattr(row, 'name', None) or filename,
                's3_key': s3_key,
                'url': presigned_url,
                'file_size': getattr(row, 'file_size', None),
                'upload_date': row.upload_date.isoformat() if getattr(row, 'upload_date', None) else None,
                'source_table': 'files'
            })

        logger.info(
            "CBCT folder %s - DICOM files after filtering: %s",
            normalized_folder,
            len(dicom_files)
        )

        debug_info = {
            'folder_name': normalized_folder,
            'folder_prefix': folder_prefix,
            'total_rows': len(file_rows),
            'dicom_count': len(dicom_files),
            'sample_keys': [row.s3_key for row in file_rows[:10]]
        }

        if not dicom_files:
            logger.warning(
                "No DICOM files found for patient %s folder %s. Sample keys: %s",
                patient_id,
                normalized_folder,
                [row.s3_key for row in file_rows[:10]]
            )

        return jsonify({
            'success': True,
            'files': dicom_files,
            'folder_name': normalized_folder,
            'patient_id': patient_id,
            'debug': debug_info
        })

    except Exception as e:
        logger.error(f"Error fetching CBCT folder files for patient {patient_id}, folder {folder_name}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@reports_files_bp.route('/api/patient/<int:patient_id>/cbct/<path:folder_name>/mpr_manifest', methods=['GET'])
@login_required
def get_cbct_mpr_manifest(patient_id, folder_name):
    """Return pre-generated MPR manifest with presigned PNG stacks if available."""
    try:
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        bucket = current_app.config.get('S3_BUCKET') or current_app.config.get('S3_BUCKET_NAME')
        if not bucket:
            logger.warning('S3 bucket not configured when fetching CBCT MPR manifest')
            return jsonify({'success': False, 'error': 'S3 bucket not configured'}), 500

        region = (current_app.config.get('AWS_REGION')
                  or os.getenv('AWS_REGION')
                  or current_app.config.get('BEDROCK_AWS_REGION')
                  or 'us-east-1')

        s3_client = _get_s3_client(region)

        normalized_folder = folder_name.strip('/')
        base_prefix = f'patients/{patient_id}/imaging/cbct_mpr/{normalized_folder}/'
        manifest_key = f'{base_prefix}manifest.json'

        logger.info(f'Looking for MPR manifest at: s3://{bucket}/{manifest_key}')

        try:
            obj = s3_client.get_object(Bucket=bucket, Key=manifest_key)
            manifest_bytes = obj['Body'].read()
            manifest = json.loads(manifest_bytes.decode('utf-8'))
            logger.info(f'Successfully loaded MPR manifest for patient {patient_id}, folder {normalized_folder}')
        except s3_client.exceptions.NoSuchKey:
            logger.warning(f'MPR manifest not found at s3://{bucket}/{manifest_key}')
            return jsonify({'success': False, 'error': 'MPR manifest not found'}), 404
        except Exception as exc:
            logger.error('Error retrieving CBCT MPR manifest %s: %s', manifest_key, exc)
            import traceback
            logger.error(traceback.format_exc())
            return jsonify({'success': False, 'error': 'Failed to load manifest'}), 500

        planes_response = {}
        counts = manifest.get('counts', {})
        for plane in ['axial', 'coronal', 'sagittal']:
            plane_count = int(counts.get(plane, 0))
            urls = []
            if plane_count > 0:
                for idx in range(plane_count):
                    key = f'{base_prefix}{plane}/{plane}_{idx:03d}.png'
                    url = _generate_presigned_url(key, inline=True, expires_in=86400)
                    if url:
                        urls.append(url)
            planes_response[plane] = {
                'count': len(urls),
                'urls': urls
            }

        return jsonify({
            'success': True,
            'manifest': manifest,
            'planes': planes_response
        })

    except Exception as e:
        logger.error(f"Error fetching CBCT MPR manifest for patient {patient_id}, folder {folder_name}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@reports_files_bp.route('/api/patient/<int:patient_id>/cbct/<path:folder_name>/generate_mpr', methods=['POST'])
@login_required
def generate_cbct_mpr_endpoint(patient_id, folder_name):
    """Start MPR generation in background thread."""
    try:
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        normalized_folder = folder_name.strip('/')
        status_key = (patient_id, normalized_folder)
        flask_app = current_app._get_current_object()

        with _mpr_status_lock:
            # Check if already running
            if status_key in _mpr_generation_status:
                current_status = _mpr_generation_status[status_key]
                if current_status['status'] == 'running':
                    return jsonify({
                        'success': False,
                        'error': 'MPR generation is already in progress'
                    }), 400

        def run_generation():
            """Background thread function to generate MPR."""
            with _mpr_status_lock:
                _mpr_generation_status[status_key] = {
                    'status': 'running',
                    'message': 'Starting MPR generation...',
                    'progress': 0,
                    'start_time': datetime.now()
                }
            
            def progress_callback(percent: int, msg: str):
                """Update progress in shared status dict."""
                with _mpr_status_lock:
                    if status_key in _mpr_generation_status:
                        _mpr_generation_status[status_key]['progress'] = percent
                        _mpr_generation_status[status_key]['message'] = msg
            
            try:
                logger.info(f"Starting MPR generation for patient {patient_id}, folder {normalized_folder}")
                with flask_app.app_context():
                    success, message = generate_cbct_mpr(patient_id, normalized_folder, overwrite=True, progress_callback=progress_callback)
                    
                    with _mpr_status_lock:
                        if success:
                            _mpr_generation_status[status_key] = {
                                'status': 'completed',
                                'message': message or 'MPR generation completed successfully',
                                'progress': 100,
                                'start_time': _mpr_generation_status.get(status_key, {}).get('start_time', datetime.now())
                            }
                        else:
                            _mpr_generation_status[status_key] = {
                                'status': 'failed',
                                'message': message or 'MPR generation failed',
                                'progress': _mpr_generation_status.get(status_key, {}).get('progress', 0),
                                'start_time': _mpr_generation_status.get(status_key, {}).get('start_time', datetime.now())
                            }
            except Exception as e:
                logger.error(f"Error in MPR generation thread: {e}")
                import traceback
                logger.error(traceback.format_exc())
                with _mpr_status_lock:
                    _mpr_generation_status[status_key] = {
                        'status': 'failed',
                        'message': f'Error: {str(e)}',
                        'progress': _mpr_generation_status.get(status_key, {}).get('progress', 0),
                        'start_time': _mpr_generation_status.get(status_key, {}).get('start_time', datetime.now())
                    }

        # Start generation in background thread
        thread = threading.Thread(target=run_generation, daemon=True)
        thread.start()

        return jsonify({
            'success': True,
            'message': 'MPR generation started'
        })

    except Exception as e:
        logger.error(f"Error starting MPR generation for patient {patient_id}, folder {folder_name}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@reports_files_bp.route('/api/patient/<int:patient_id>/cbct/<path:folder_name>/mpr_status', methods=['GET'])
@login_required
def get_cbct_mpr_status(patient_id, folder_name):
    """Get MPR generation status."""
    try:
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        normalized_folder = folder_name.strip('/')
        status_key = (patient_id, normalized_folder)

        with _mpr_status_lock:
            status = _mpr_generation_status.get(status_key)

        if not status:
            # Check if manifest exists (generation might have completed before status tracking)
            bucket = current_app.config.get('S3_BUCKET') or current_app.config.get('S3_BUCKET_NAME')
            if bucket:
                region = (current_app.config.get('AWS_REGION')
                          or os.getenv('AWS_REGION')
                          or current_app.config.get('BEDROCK_AWS_REGION')
                          or 'us-east-1')
                s3_client = _get_s3_client(region)
                manifest_key = f'patients/{patient_id}/imaging/cbct_mpr/{normalized_folder}/manifest.json'
                try:
                    s3_client.head_object(Bucket=bucket, Key=manifest_key)
                    return jsonify({
                        'success': True,
                        'status': 'completed',
                        'message': 'MPR files already exist',
                        'manifest_exists': True
                    })
                except s3_client.exceptions.ClientError:
                    pass

            return jsonify({
                'success': True,
                'status': 'not_started',
                'message': 'MPR generation not started'
            })

        # Calculate elapsed time
        elapsed = None
        if status.get('start_time'):
            elapsed = (datetime.now() - status['start_time']).total_seconds()

        return jsonify({
            'success': True,
            'status': status['status'],
            'message': status.get('message', ''),
            'progress': status.get('progress', 0),
            'elapsed_seconds': elapsed
        })

    except Exception as e:
        logger.error(f"Error getting MPR status for patient {patient_id}, folder {folder_name}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# CBCT Volume Packing API (for Cornerstone3D streaming)
# =============================================================================

@reports_files_bp.route('/api/patient/<int:patient_id>/cbct/<path:folder_name>/generate_volume', methods=['POST'])
@login_required
def generate_cbct_volume(patient_id, folder_name):
    """
    Trigger conversion of DICOM series to a single packed volume file (NRRD format).
    
    This creates a streamable volume file for efficient MPR rendering with Cornerstone3D.
    The volume is stored at: patients/{patient_id}/imaging/cbct/{folder_name}/volume/volume.nrrd
    """
    try:
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        from flask_app.utils.cbct_volume_packer import (
            trigger_volume_packing_background,
            volume_exists,
            get_conversion_status
        )
        
        normalized_folder = folder_name.strip('/')
        
        # Check if already exists
        if volume_exists(patient_id, normalized_folder):
            return jsonify({
                'success': True,
                'status': 'already_exists',
                'message': 'Volume already exists'
            })
        
        # Check if already running
        status = get_conversion_status(patient_id, normalized_folder)
        if status['status'] == 'running':
            return jsonify({
                'success': True,
                'status': 'running',
                'message': 'Volume generation already in progress',
                'progress': status.get('progress', 0)
            })
        
        # Start background conversion
        success, message = trigger_volume_packing_background(
            patient_id, 
            normalized_folder,
            app=current_app._get_current_object()
        )
        
        if success:
            logger.info(f"Started volume generation for patient {patient_id}, folder {normalized_folder}")
            return jsonify({
                'success': True,
                'status': 'started',
                'message': 'Volume generation started'
            })
        else:
            return jsonify({
                'success': False,
                'error': message
            }), 400
            
    except Exception as e:
        logger.error(f"Error starting volume generation for patient {patient_id}, folder {folder_name}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@reports_files_bp.route('/api/patient/<int:patient_id>/cbct/<path:folder_name>/volume_status', methods=['GET'])
@login_required
def get_cbct_volume_status(patient_id, folder_name):
    """Get status of volume generation/packing."""
    try:
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        from flask_app.utils.cbct_volume_packer import get_conversion_status
        
        normalized_folder = folder_name.strip('/')
        status = get_conversion_status(patient_id, normalized_folder)
        
        return jsonify({
            'success': True,
            **status
        })
        
    except Exception as e:
        logger.error(f"Error getting volume status for patient {patient_id}, folder {folder_name}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@reports_files_bp.route('/api/patient/<int:patient_id>/cbct/<path:folder_name>/volume_manifest', methods=['GET'])
@login_required
def get_cbct_volume_manifest(patient_id, folder_name):
    """
    Get volume manifest with metadata and presigned URL for streaming.
    
    Returns dimensions, spacing, origin, and a presigned URL to download the volume.
    """
    try:
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        from flask_app.utils.cbct_volume_packer import get_volume_manifest, volume_exists
        
        normalized_folder = folder_name.strip('/')
        
        if not volume_exists(patient_id, normalized_folder):
            return jsonify({
                'success': False,
                'error': 'Volume not found. Generate it first using /generate_volume endpoint.'
            }), 404
        
        manifest = get_volume_manifest(patient_id, normalized_folder)
        
        if not manifest:
            return jsonify({
                'success': False,
                'error': 'Could not load volume manifest'
            }), 500
        
        return jsonify({
            'success': True,
            'manifest': manifest
        })
        
    except Exception as e:
        logger.error(f"Error getting volume manifest for patient {patient_id}, folder {folder_name}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@reports_files_bp.route('/viewer/cbct/<int:patient_id>/<path:folder_name>', methods=['GET'])
@login_required
def view_cbct(patient_id, folder_name):
    """
    Render CBCT viewer page in MPT (Multi-Planar Tomography) mode
    
    Shows axial, coronal, and sagittal views simultaneously
    """
    try:
        # Permission check
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return "Access denied", 403
        
        logger.info(f"Loading CBCT viewer for patient {patient_id}, folder {folder_name}")
        
        # Log access
        _log_file_access(
            user_id=current_user.id,
            patient_id=patient_id,
            file_id=0,  # Folder, not a single file
            file_table='folder',
            file_type='cbct',
            access_type='view'
        )
        
        return render_template(
            'cbct_viewer.html',
            patient_id=patient_id,
            patient_name=patient.name,
            folder_name=folder_name
        )
        
    except Exception as e:
        logger.error(f"Error loading CBCT viewer for patient {patient_id}, folder {folder_name}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return f"Error loading CBCT viewer: {str(e)}", 500


@reports_files_bp.route('/viewer/cbct_dicom/<int:patient_id>/<path:folder_name>', methods=['GET'])
@login_required
def view_cbct_dicom(patient_id, folder_name):
    """
    Render CBCT viewer page that reads DICOM files directly and creates MPR views on-the-fly.
    
    This viewer loads DICOM files from S3 and generates multi-planar reconstructions
    directly from the DICOM volume, rather than using pre-computed MPR images.
    
    Shows axial, coronal, and sagittal views simultaneously with synchronized crosshairs.
    
    Performance optimization: Uses pre-zipped CBCT files from cbct_prezip/ if available,
    otherwise falls back to loading individual DICOM files via proxy.
    """
    try:
        # Permission check
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return "Access denied", 403
        
        logger.info(f"Loading DICOM-based CBCT viewer for patient {patient_id}, folder {folder_name}")
        
        # Try to get presigned ZIP URL first for faster loading
        from flask_app.utils.cbct_prezip_manager import get_prezip_url
        zip_url = get_prezip_url(patient_id, folder_name, expires_in=3600)  # 1 hour
        
        if zip_url:
            logger.info(f"Using pre-zipped CBCT for faster loading: {folder_name}")
        else:
            logger.info(f"No pre-zip available, will load individual DICOM files")
        
        # Log access
        _log_file_access(
            user_id=current_user.id,
            patient_id=patient_id,
            file_id=0,  # Folder, not a single file
            file_table='folder',
            file_type='cbct',
            access_type='view'
        )
        
        return render_template(
            'cbct_viewer_dicom.html',
            patient_id=patient_id,
            patient_name=patient.name,
            folder_name=folder_name,
            zip_url=zip_url or '',  # Empty string if no prezip
            use_api=not zip_url  # Flag to use API for individual files if no ZIP
        )
        
    except Exception as e:
        logger.error(f"Error loading DICOM-based CBCT viewer for patient {patient_id}, folder {folder_name}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return f"Error loading DICOM-based CBCT viewer: {str(e)}", 500


@reports_files_bp.route('/viewer/cbct_ohif/<int:patient_id>/<path:folder_name>', methods=['GET'])
@login_required
def view_cbct_ohif(patient_id, folder_name):
    """
    Render OHIF Viewer for CBCT DICOM files.
    OHIF Viewer is a professional medical imaging viewer that handles DICOM orientation correctly.
    """
    try:
        # Permission check
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return "Access denied", 403
        
        logger.info(f"Loading OHIF CBCT viewer for patient {patient_id}, folder {folder_name}")
        
        # Log access
        _log_file_access(
            user_id=current_user.id,
            patient_id=patient_id,
            file_id=0,
            file_table='folder',
            file_type='cbct',
            access_type='view'
        )
        
        return render_template(
            'cbct_viewer_ohif.html',
            patient_id=patient_id,
            patient_name=patient.name,
            folder_name=folder_name
        )
        
    except Exception as e:
        logger.error(f"Error loading OHIF CBCT viewer for patient {patient_id}, folder {folder_name}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return f"Error loading OHIF CBCT viewer: {str(e)}", 500


@reports_files_bp.route('/viewer/cbct_mpr/<int:patient_id>/<path:folder_name>', methods=['GET'])
@login_required
def view_cbct_mpr(patient_id, folder_name):
    """
    Render CBCT MPR Viewer - Modern viewer with Multi-Planar Reconstruction.
    Uses presigned ZIP URL if available, otherwise falls back to individual DICOM files.
    """
    try:
        # Permission check
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return "Access denied", 403
        
        logger.info(f"Loading MPR CBCT viewer for patient {patient_id}, folder {folder_name}")
        
        # Try to get presigned ZIP URL first
        from flask_app.utils.cbct_prezip_manager import get_prezip_url
        zip_url = get_prezip_url(patient_id, folder_name, expires_in=3600)  # 1 hour
        
        # Log access
        _log_file_access(
            user_id=current_user.id,
            patient_id=patient_id,
            file_id=0,
            file_table='folder',
            file_type='cbct',
            access_type='view'
        )
        
        return render_template(
            'cbct_viewer_mpr.html',
            patient_id=patient_id,
            patient_name=patient.name,
            folder_name=folder_name,
            zip_url=zip_url or '',  # Empty string if no prezip
            use_api=not zip_url  # Flag to use API for individual files
        )
        
    except Exception as e:
        logger.error(f"Error loading MPR CBCT viewer for patient {patient_id}, folder {folder_name}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return f"Error loading MPR CBCT viewer: {str(e)}", 500


@reports_files_bp.route('/api/patient/<int:patient_id>/cbct/<path:folder_name>/dicom_files', methods=['GET'])
@login_required
def get_cbct_dicom_files(patient_id, folder_name):
    """
    Get list of DICOM files for OHIF Viewer.
    Returns presigned URLs for DICOM files.
    """
    try:
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        bucket = os.getenv('S3_BUCKET_NAME')
        if not bucket:
            return jsonify({'success': False, 'error': 'S3 bucket not configured'}), 500
        
        prefix = f"patients/{patient_id}/imaging/cbct/{folder_name}/"
        
        s3_client = boto3.client('s3', region_name=os.getenv('AWS_REGION', 'us-east-1'))
        paginator = s3_client.get_paginator('list_objects_v2')
        
        dicom_files = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            if 'Contents' in page:
                for obj in page['Contents']:
                    key = obj['Key']
                    filename = os.path.basename(key)
                    filename_lower = filename.lower()
                    
                    # Detect DICOM files flexibly:
                    # 1. Files with .dcm, .dicom extensions
                    # 2. Files with no extension (common in CBCT exports)
                    # 3. Purely numeric filenames (000001, 000002, etc.)
                    is_dicom = (
                        filename_lower.endswith(('.dcm', '.dicom', '.dcom')) or
                        ('.' not in filename and filename) or  # No extension
                        filename.isdigit()  # Purely numeric
                    )
                    
                    # Skip directories and non-DICOM files
                    if not is_dicom or key.endswith('/'):
                        continue
                    
                        presigned_url = s3_client.generate_presigned_url(
                            'get_object',
                            Params={'Bucket': bucket, 'Key': key},
                            ExpiresIn=3600
                        )
                        dicom_files.append({
                            'url': presigned_url,
                        'filename': filename,
                        'name': filename,  # Add 'name' field for sorting
                            'key': key
                        })
        
        # Sort by filename to ensure consistent order
        dicom_files.sort(key=lambda x: x['filename'])
        
        return jsonify({
            'success': True,
            'files': dicom_files,
            'count': len(dicom_files)
        })
        
    except Exception as e:
        logger.error(f"Error getting DICOM files for OHIF: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@reports_files_bp.route('/api/patient/<int:patient_id>/cbct/<path:folder_name>/desktop_app', methods=['GET'])
@login_required
def get_cbct_desktop_app_data(patient_id, folder_name):
    """
    Get CBCT data for desktop app integration.
    Returns presigned URLs, session token, and metadata.
    Desktop app can use this endpoint with the session token for authentication.
    """
    try:
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        bucket = os.getenv('S3_BUCKET_NAME')
        if not bucket:
            return jsonify({'success': False, 'error': 'S3 bucket not configured'}), 500
        
        # Normalize folder name (remove leading/trailing slashes)
        folder_name = folder_name.strip('/').split('/')[0]
        prefix = f"patients/{patient_id}/imaging/cbct/{folder_name}/"
        
        s3_client = boto3.client('s3', region_name=os.getenv('AWS_REGION', 'us-east-1'))
        paginator = s3_client.get_paginator('list_objects_v2')
        
        # Get all DICOM files with presigned URLs (valid for 24 hours for desktop app)
        dicom_files = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            if 'Contents' in page:
                for obj in page['Contents']:
                    key = obj['Key']
                    if key.lower().endswith(('.dcm', '.dicom', '.dcom')):
                        presigned_url = s3_client.generate_presigned_url(
                            'get_object',
                            Params={'Bucket': bucket, 'Key': key},
                            ExpiresIn=86400  # 24 hours for desktop app
                        )
                        dicom_files.append({
                            'url': presigned_url,
                            'filename': os.path.basename(key),
                            's3_key': key,
                            'size': obj.get('Size', 0)
                        })
        
        # Sort by filename to ensure consistent order
        dicom_files.sort(key=lambda x: x['filename'])
        
        # Generate a temporary access token (using Flask session ID + timestamp)
        # Desktop app can use this to authenticate subsequent requests
        from flask import session
        import hashlib
        import time
        
        token_data = f"{session.get('_id', '')}_{current_user.id}_{patient_id}_{folder_name}_{int(time.time())}"
        access_token = hashlib.sha256(token_data.encode()).hexdigest()[:32]
        
        # Store token in session for validation (optional - can be removed if using direct auth)
        if 'desktop_app_tokens' not in session:
            session['desktop_app_tokens'] = {}
        session['desktop_app_tokens'][access_token] = {
            'user_id': current_user.id,
            'patient_id': patient_id,
            'folder_name': folder_name,
            'expires_at': int(time.time()) + 86400  # 24 hours
        }
        
        return jsonify({
            'success': True,
            'access_token': access_token,
            'patient': {
                'id': patient.id,
                'name': patient.name,
                'patient_id': patient.patient_id
            },
            'folder': {
                'name': folder_name,
                's3_prefix': prefix
            },
            'files': dicom_files,
            'file_count': len(dicom_files),
            'base_url': request.host_url.rstrip('/'),  # For API calls from desktop app
            'expires_in': 86400  # Token valid for 24 hours
        })
        
    except Exception as e:
        logger.error(f"Error getting desktop app data for CBCT: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@reports_files_bp.route('/api/desktop_app/validate_token', methods=['POST'])
def validate_desktop_app_token():
    """
    Validate desktop app access token.
    Desktop app calls this to verify token is still valid.
    """
    try:
        from flask import session
        data = request.get_json()
        token = data.get('access_token')
        
        if not token:
            return jsonify({'success': False, 'error': 'Missing access_token'}), 400
        
        # Check if token exists in session
        if 'desktop_app_tokens' not in session:
            return jsonify({'success': False, 'error': 'Invalid token'}), 401
        
        token_data = session['desktop_app_tokens'].get(token)
        if not token_data:
            return jsonify({'success': False, 'error': 'Invalid token'}), 401
        
        # Check if token expired
        import time
        if token_data['expires_at'] < int(time.time()):
            # Remove expired token
            del session['desktop_app_tokens'][token]
            return jsonify({'success': False, 'error': 'Token expired'}), 401
        
        return jsonify({
            'success': True,
            'valid': True,
            'user_id': token_data['user_id'],
            'patient_id': token_data['patient_id'],
            'folder_name': token_data['folder_name']
        })
        
    except Exception as e:
        logger.error(f"Error validating desktop app token: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@reports_files_bp.route('/api/dicom/proxy/<int:patient_id>/<path:folder_name>/<path:filename>', methods=['GET'])
@login_required
def proxy_dicom_file(patient_id, folder_name, filename):
    """
    Proxy DICOM files from S3 to avoid CORS issues with presigned URLs.
    This endpoint serves DICOM files with proper CORS headers.
    """
    try:
        # Permission check
        patient = Patient.query.get_or_404(patient_id)
        if not current_user.can_access_patient(patient):
            return "Access denied", 403
        
        bucket = os.getenv('S3_BUCKET_NAME')
        if not bucket:
            return "S3 bucket not configured", 500
        
        # Decode filename in case it was URL encoded
        from urllib.parse import unquote
        filename = unquote(filename)
        
        # Normalize folder name (same as get_cbct_folder_files)
        normalized_folder = folder_name.strip('/').split('/')[0]
        
        logger.info(f"Proxy request: patient={patient_id}, folder={normalized_folder}, filename={filename}")
        
        # Get file from database first to get the exact S3 key
        from ..models import File
        folder_prefix = f'patients/{patient_id}/imaging/cbct/{normalized_folder}/'
        
        # Try to find file by filename in s3_key
        file_record = File.query.filter(
            File.patient_id == patient_id,
            File.s3_key.like(f'{folder_prefix}%'),
            File.s3_key.like(f'%/{filename}')
        ).first()
        
        s3_client = boto3.client('s3', region_name=os.getenv('AWS_REGION', 'us-east-1'))
        
        # Try to get S3 key from database first
        s3_key = None
        if file_record and file_record.s3_key:
            s3_key = file_record.s3_key
            logger.info(f"Found file in database, using S3 key: {s3_key}")
        else:
            # Fallback: build S3 key from path
            s3_key = f"{folder_prefix}{filename}"
            logger.info(f"File not in database, trying S3 key: {s3_key}")
            
            # Also try to find any file with similar name (for debugging)
            similar_files = File.query.filter(
                File.patient_id == patient_id,
                File.s3_key.like(f'{folder_prefix}%'),
                File.s3_key.like('%.dcm')
            ).limit(5).all()
            if similar_files:
                logger.warning(f"File '{filename}' not found, but found {len(similar_files)} DICOM files in folder:")
                for f in similar_files:
                    logger.warning(f"  - {f.s3_key.split('/')[-1]} (s3_key: {f.s3_key})")
        
        # Get file from S3
        try:
            response = s3_client.get_object(Bucket=bucket, Key=s3_key)
            file_content = response['Body'].read()
            content_type = response.get('ContentType', 'application/dicom')
            logger.info(f"Successfully loaded DICOM file: {s3_key}")
        except s3_client.exceptions.NoSuchKey:
            # If not found, try to list files in the folder to see what's available
            logger.warning(f"DICOM file not found at: {s3_key}")
            try:
                prefix = f"patients/{patient_id}/imaging/cbct/{folder_name}/"
                paginator = s3_client.get_paginator('list_objects_v2')
                for page in paginator.paginate(Bucket=bucket, Prefix=prefix, MaxKeys=10):
                    if 'Contents' in page:
                        available_files = [obj['Key'].split('/')[-1] for obj in page['Contents']]
                        logger.warning(f"Available files in folder: {available_files[:5]}")
                        break
            except:
                pass
            from flask import jsonify
            return jsonify({'error': f'File not found: {filename}', 's3_key_tried': s3_key}), 404
        except Exception as e:
            logger.error(f"Error fetching DICOM file from S3: {e}")
            return f"Error fetching file: {str(e)}", 500
        
        # Return file with proper CORS headers
        from flask import Response
        import urllib.parse
        # Properly encode filename for Content-Disposition header to handle non-ASCII characters
        try:
            filename.encode('ascii')
            # ASCII-only filename - use simple format
            content_disposition = f'inline; filename="{filename}"'
        except UnicodeEncodeError:
            # Non-ASCII characters - use RFC 5987 format
            encoded_filename = urllib.parse.quote(filename, safe='')
            content_disposition = f'inline; filename="{filename}"; filename*=UTF-8\'\'{encoded_filename}'
        
        return Response(
            file_content,
            mimetype=content_type,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Content-Disposition': content_disposition
            }
        )
        
    except Exception as e:
        logger.error(f"Error proxying DICOM file: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return f"Error: {str(e)}", 500


# Helper functions

def _add_presigned_urls(categorized_files, patient_id):
    """Add presigned URLs to file objects"""
    try:
        # Add URLs for reports
        for report in categorized_files.get('reports', []):
            report['view_url'] = f"/reports-files/api/patient/{patient_id}/reports/{report['id']}/html?table={report['file_table']}"
            # Also add direct S3 URL for download
            if 's3_key' in report and report['s3_key'] and str(report['s3_key']).strip() not in ['None', 'null', 'NULL', '']:
                report['download_url'] = _generate_presigned_url(report['s3_key'], expires_in=3600)
        
        # Add URLs for images
        for image in categorized_files.get('images', []):
            if 's3_key' in image and image['s3_key'] and str(image['s3_key']).strip() not in ['None', 'null', 'NULL', '']:
                image['url'] = _generate_presigned_url(image['s3_key'], expires_in=3600, inline=True)
                image['url_thumb'] = image['url']  # Could generate thumbnails in future
        
        # Add URLs for DICOM folders
        for dicom in categorized_files.get('dicom', []):
            dicom['viewer_url'] = f"/share/viewer/{patient_id}?folder_id={dicom['folder_id']}"
        
        # Add URLs for STL files
        for stl in categorized_files.get('stl', []):
            stl['viewer_url'] = f"/reports-files/viewer/stl/{stl['id']}?table={stl['file_table']}"
        
        # Add URLs for documents
        for doc in categorized_files.get('documents', []):
            doc['preview_url'] = f"/reports-files/api/files/{doc['id']}/preview?table={doc['file_table']}"
            if 's3_key' in doc and doc['s3_key'] and str(doc['s3_key']).strip() not in ['None', 'null', 'NULL', '']:
                doc['download_url'] = _generate_presigned_url(doc['s3_key'], expires_in=3600)
        
        return categorized_files
        
    except Exception as e:
        logger.error(f"Error adding presigned URLs: {e}")
        return categorized_files


def _get_file_size_from_s3(s3_key):
    """Get file size from S3 object"""
    if not s3_key or str(s3_key).strip() in ['None', 'null', 'NULL', '']:
        return None
        
    try:
        s3_client = boto3.client('s3')
        bucket = current_app.config.get('S3_BUCKET') or current_app.config.get('S3_BUCKET_NAME')
        logger.debug(f"Presign bucket value: {bucket} (type: {type(bucket)})")
        if not bucket:
            logger.error("S3 bucket not configured in app config (S3_BUCKET or S3_BUCKET_NAME)")
            return None
        
        response = s3_client.head_object(
            Bucket=bucket,
            Key=s3_key
        )
        
        return response.get('ContentLength')
    except Exception as e:
        logger.error(f"Error getting file size from S3 for key {s3_key}: {e}")
        return None


def _get_folder_size_from_s3(folder_path):
    """Calculate total size of all files in an S3 folder"""
    try:
        s3_client = boto3.client('s3')
        bucket = current_app.config.get('S3_BUCKET')
        
        # List all objects in the folder
        response = s3_client.list_objects_v2(
            Bucket=bucket,
            Prefix=folder_path
        )
        
        total_size = 0
        if 'Contents' in response:
            for obj in response['Contents']:
                # Skip if it's a folder (ends with /)
                if not obj['Key'].endswith('/'):
                    total_size += obj.get('Size', 0)
        
        return total_size
    except Exception as e:
        logger.error(f"Error calculating folder size for {folder_path}: {e}")
        return None


def _generate_presigned_url(s3_key, expires_in=3600, inline=False, verify_exists=True):
    """Generate presigned URL for S3 object"""
    try:
        # Handle None or empty values first
        if s3_key is None:
            logger.warning(f"Invalid S3 key provided: None")
            return None
        
        # Handle tuple/list (edge case - should not happen but protect against it)
        if isinstance(s3_key, (list, tuple)):
            if len(s3_key) > 0:
                s3_key = s3_key[0]  # Take first element
            else:
                logger.warning(f"Invalid S3 key provided: empty {type(s3_key).__name__}")
                return None
        
        # Convert to string immediately to handle any type (SQLAlchemy objects, etc.)
        try:
            s3_key = str(s3_key)
        except (TypeError, AttributeError, ValueError) as e:
            logger.warning(f"Error converting S3 key to string: {repr(s3_key)} (type: {type(s3_key).__name__}), error: {e}")
            return None
        
        # Now that we have a string, check if it's valid
        # Only call strip() if it's actually a string (double-check)
        if not isinstance(s3_key, str):
            logger.warning(f"S3 key is not a string after conversion: {repr(s3_key)} (type: {type(s3_key).__name__})")
            return None
        
        s3_key = s3_key.strip()
        if not s3_key or s3_key in ['None', 'null', 'NULL', '']:
            logger.warning(f"Invalid S3 key after conversion: {repr(s3_key)}")
            return None
            
        bucket = current_app.config.get('S3_BUCKET') or current_app.config.get('S3_BUCKET_NAME')
        if not bucket:
            logger.error("S3 bucket not configured in app config (S3_BUCKET or S3_BUCKET_NAME)")
            return None

        region = (current_app.config.get('AWS_REGION')
                  or os.getenv('AWS_REGION')
                  or current_app.config.get('BEDROCK_AWS_REGION')
                  or 'us-east-1')

        if verify_exists:
            try:
                _get_s3_client(region).head_object(Bucket=bucket, Key=s3_key)
            except Exception as head_err:
                logger.warning("S3 object missing for presigned URL %s: %s", s3_key, head_err)
                return None

        s3_client = _get_presign_client(region)

        params = {
            'Bucket': bucket,
            'Key': s3_key
        }
        
        # Set content disposition for inline viewing vs download
        if inline:
            params['ResponseContentDisposition'] = 'inline'
        
        url = s3_client.generate_presigned_url(
            'get_object',
            Params=params,
            ExpiresIn=expires_in
        )
        
        return url
        
    except Exception as e:
        logger.error(f"Error generating presigned URL for {s3_key}: {e}")
        return None


def _log_file_access(user_id, patient_id, file_id, file_table, file_type, access_type):
    """Log file access for audit trail - DISABLED for Phase 1 (no DB migration)"""
    # Phase 1: Log to application log instead of database
    # TODO Phase 2: Enable database audit logging for compliance
    ip_address = request.remote_addr
    logger.info(f"FILE ACCESS: user={user_id}, patient={patient_id}, file={file_id}, "
               f"table={file_table}, type={file_type}, access={access_type}, ip={ip_address}")


# Level 4 Report Lab Functions
def _level4_examples_block() -> str:
    """Load all example reports from TXT directory. Not cached to allow dynamic updates."""
    if not _LEVEL4_SAMPLE_DIR.exists():
        current_app.logger.warning('Level-4 sample directory missing: %s', _LEVEL4_SAMPLE_DIR)
        return 'Example reports unavailable.'
    samples = []
    # Load all TXT files (no limit - include all example reports)
    for idx, sample_path in enumerate(sorted(_LEVEL4_SAMPLE_DIR.glob('*.txt')), start=1):
        try:
            samples.append(f"----- Example Report {idx} -----\n{sample_path.read_text(encoding='utf-8').strip()}")
        except Exception as exc:  # pragma: no cover
            current_app.logger.error('Failed to read sample %s: %s', sample_path, exc)
    current_app.logger.info('Loaded %d example reports from %s', len(samples), _LEVEL4_SAMPLE_DIR)
    return '\n\n'.join(samples) if samples else 'Example reports unavailable.'


def _level4_load_canonical(patient_id: int) -> dict:
    """
    Load canonical JSON for Level-4 report generation.
    
    The canonical stored in the database is the FULL canonical with all data.
    This function loads it and applies cleaning to create a minimal, LLM-friendly version
    for Level-4/Level-5 report generation.
    
    Cleaning removes:
    - Metadata fields (schema_version, document_type, patient_id)
    - Provenance blocks (report_mentions, reported_metrics, canonical_derived, etc.)
    - Provider/facility names from clinical_background
    - Device design (to avoid bias)
    - Adds required null fields to sleep_study
    """
    envelope = PatientCaseEnvelope.query.filter_by(patient_id=patient_id, report_id='canonical').first()
    if not envelope or not envelope.case_json:
        raise ValueError('Canonical patient JSON not found')
    if isinstance(envelope.case_json, str):
        canonical_json = json.loads(envelope.case_json)
    else:
        canonical_json = envelope.case_json
    
    # Always clean the canonical for LLM consumption (removes metadata, provenance, etc.)
    # This creates a minimal, LLM-friendly version for Level-4/Level-5 report generation
    try:
        from flask_app.config.document_observation_extractor_phase2 import create_clean_canonical_for_llm
        canonical_json = create_clean_canonical_for_llm(canonical_json, patient_id)
        logger.info(f"Patient {patient_id}: Cleaned canonical for Level-4 report generation")
    except Exception as e:
        logger.warning(f"Failed to clean canonical for patient {patient_id}: {e}, using original")
    
    return canonical_json


def _level4_build_prompt(patient_json: dict) -> str:
    patient_block = json.dumps(patient_json, indent=2, ensure_ascii=False)
    return f"""Below are the example Level-4 OSA Reports. Use their style, tone, and formatting, but ALWAYS follow the strict section structure defined by the system prompt. 

If examples contradict the system structure, the system structure wins.



EXAMPLE REPORTS:

{_level4_examples_block()}



PATIENT_CASE_DATA (JSON):

<<<PATIENT_JSON_HERE>>>

{patient_block}



TASK:

Generate a full Level-4 OSA Data Assessment Report using the exact required structure and formatting as defined in the system prompt.
"""


def _level4_invoke_bedrock(messages, patient_id):
    service = get_bedrock_service()
    if not service or not service.is_available():
        return {'error': 'Bedrock service unavailable'}
    result = service.invoke_model(
        messages=messages,
        max_tokens=4000,
        temperature=0.2,
        patient_id=patient_id,
        endpoint='reports_level4_lab',
    )
    if result.get('success'):
        return {'response': result.get('response'), 'model': 'bedrock_claude'}
    return {'error': result.get('error', 'Bedrock call failed')}


def _level4_invoke_openai(messages):
    if openai is None:
        return {'error': 'openai package not installed'}
    try:
        openai.api_key = _LEVEL4_OPENAI_KEY
        completion = openai.chat.completions.create(
            model=os.getenv('LEVEL4_OPENAI_MODEL', 'gpt-4o'),
            messages=messages,
            temperature=0.2,
            max_tokens=4000,
        )
        return {'response': completion.choices[0].message.content, 'model': completion.model}
    except Exception as exc:  # pragma: no cover
        current_app.logger.error('OpenAI error: %s', exc)
        return {'error': str(exc)}


def _level4_invoke_claude(messages):
    if Anthropic is None:
        return {'error': 'anthropic package not installed'}
    try:
        client = Anthropic(api_key=_LEVEL4_ANTHROPIC_KEY)
        resp = client.messages.create(
            model=os.getenv('LEVEL4_CLAUDE_MODEL', 'claude-3-5-sonnet-20241022-v2:0'),
            max_tokens=4000,
            temperature=0.2,
            system=messages[0]['content'],
            messages=[{'role': 'user', 'content': messages[1]['content']}],
        )
        text_blocks = [block.text for block in resp.content if getattr(block, 'type', '') == 'text']
        return {'response': '\n'.join(text_blocks), 'model': resp.model}
    except Exception as exc:  # pragma: no cover
        current_app.logger.error('Claude error: %s', exc)
        return {'error': str(exc)}


def _level4_invoke_provider(provider, user_prompt, patient_id):
    """Invoke provider with default system prompt"""
    return _level4_invoke_provider_with_prompts(provider, _LEVEL4_SYSTEM_PROMPT, user_prompt, patient_id)


def _level4_invoke_provider_with_prompts(provider, system_prompt, user_prompt, patient_id):
    """Invoke provider with custom system and user prompts"""
    provider = (provider or 'bedrock').lower()
    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt},
    ]
    if provider == 'openai':
        return _level4_invoke_openai(messages)
    if provider == 'claude':
        return _level4_invoke_claude(messages)
    return _level4_invoke_bedrock(messages, patient_id)


@reports_files_bp.route('/reports/level4-lab', methods=['GET'])
@login_required
def reports_level4_lab():
    return render_template('level4_report_lab.html')


@reports_files_bp.route('/reports/api/level4_report/patient_search', methods=['GET'])
@login_required
def reports_level4_patient_search():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'patients': []})

    candidates = (
        Patient.query.filter(Patient.name.ilike(f"%{query}%"))
        .order_by(Patient.name)
        .limit(20)
        .all()
    )

    payload = [
        {'id': patient.id, 'name': patient.name}
        for patient in candidates
        if current_user.can_access_patient(patient)
    ]
    return jsonify({'patients': payload})


@reports_files_bp.route('/reports/api/level4_report/patient/<int:patient_id>/canonical', methods=['GET'])
@login_required
def reports_level4_get_canonical(patient_id):
    """
    Get canonical JSON for a patient (for micro-section generation).
    """
    patient = Patient.query.get(patient_id)
    if not patient or not current_user.can_access_patient(patient):
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    try:
        canonical_json = _level4_load_canonical(patient_id)
        # Add patient name to canonical for convenience
        if 'patient' not in canonical_json:
            canonical_json['patient'] = {}
        canonical_json['patient']['name'] = patient.name
        canonical_json['patient']['id'] = patient_id
        
        return jsonify({
            'success': True,
            'patient_id': patient_id,
            'patient_name': patient.name,
            'canonical_json': canonical_json
        })
    except Exception as exc:
        logger.error(f"Failed to load canonical JSON for patient {patient_id}: {exc}", exc_info=True)
        return jsonify({'success': False, 'error': str(exc)}), 404


@reports_files_bp.route('/reports/api/level4_report/generate', methods=['POST'])
@login_required
def reports_level4_generate():
    data = request.get_json() or {}
    patient_id = data.get('patient_id')
    provider = data.get('provider', 'bedrock')
    custom_system_prompt = data.get('custom_system_prompt')
    custom_user_prompt = data.get('custom_user_prompt')

    if not patient_id:
        return jsonify({'success': False, 'error': 'patient_id is required'}), 400

    patient = Patient.query.get(patient_id)
    if not patient or not current_user.can_access_patient(patient):
        return jsonify({'success': False, 'error': 'Access denied'}), 403

    try:
        canonical_json = _level4_load_canonical(patient_id)
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 404

    # Use custom prompts if provided, otherwise use defaults
    system_prompt = custom_system_prompt if custom_system_prompt else _LEVEL4_SYSTEM_PROMPT
    user_prompt = custom_user_prompt if custom_user_prompt else _level4_build_prompt(canonical_json)
    
    llm_result = _level4_invoke_provider_with_prompts(provider, system_prompt, user_prompt, patient_id)
    if 'error' in llm_result:
        return jsonify({'success': False, 'error': llm_result['error']}), 500

    # Save to history
    history_entry = None
    try:
        history_entry = Level4ReportHistory(
            patient_id=patient_id,
            prompt=user_prompt,
            response=llm_result.get('response', ''),
            llm_provider=provider,
            model_used=llm_result.get('model'),
            created_by=current_user.id
        )
        db.session.add(history_entry)
        db.session.commit()
    except Exception as exc:
        current_app.logger.error('Failed to save Level 4 report history: %s', exc)
        # Don't fail the request if history save fails

    return jsonify({
        'success': True,
        'system_prompt': system_prompt,
        'user_prompt': user_prompt,
        'prompt': user_prompt,  # Keep for backward compatibility
        'response': llm_result.get('response'),
        'model_used': llm_result.get('model'),
        'history_id': history_entry.id if history_entry else None,
    })


def _validate_content_preservation(original_text: str, formatted_text: str) -> dict:
    """Validate that all content from original MD version is preserved in formatted version"""
    import re
    
    validation_result = {
        'passed': True,
        'warnings': [],
        'errors': [],
        'missing_sections': [],
        'missing_data': []
    }
    
    if not original_text or not formatted_text:
        validation_result['passed'] = False
        validation_result['errors'].append('Original or formatted text is empty')
        return validation_result
    
    # Extract key sections from original (ignore markdown formatting)
    original_sections = {}
    section_patterns = {
        'Personal Details': r'(?:#+\s*)?Personal Details.*?(?=\n\n|\n[A-Z][a-z]+ [A-Z]|$)',
        'Clinical Background': r'(?:#+\s*)?Clinical Background.*?(?=\n\n|\n[A-Z][a-z]+ [A-Z]|$)',
        'ENT Findings': r'(?:#+\s*)?ENT.*?Findings.*?(?=\n\n|\n[A-Z][a-z]+ [A-Z]|$)',
        'Sleep Study Data': r'(?:#+\s*)?Sleep Study Data.*?(?=\n\n|\n[A-Z][a-z]+ [A-Z]|$)',
        'Observations': r'(?:#+\s*)?Observations.*?(?=\n\n|\n[A-Z][a-z]+ [A-Z]|$)',
        'Structural Observations': r'(?:#+\s*)?Structural Observations.*?(?=\n\n|\n[A-Z][a-z]+ [A-Z]|$)',
        'Treatment Considerations': r'(?:#+\s*)?(?:Possible\s+)?Treatment Considerations.*?(?=\n\n|\n[A-Z][a-z]+ [A-Z]|$)',
        'Device Design': r'(?:#+\s*)?Device Design.*?(?=\n\n|\n[A-Z][a-z]+ [A-Z]|$)',
        'Recommendations': r'(?:#+\s*)?Recommendations.*?(?=\n\n|\n[A-Z][a-z]+ [A-Z]|$)',
        'Recommendations for Further Evaluation': r'(?:#+\s*)?Recommendations for Further Evaluation.*?(?=\n\n|\n[A-Z][a-z]+ [A-Z]|$)',
        'Oral Appliance Options': r'(?:#+\s*)?Oral Appliance Options.*?(?=\n\n|\nFINAL|$)'
    }
    
    for section_name, pattern in section_patterns.items():
        match = re.search(pattern, original_text, re.DOTALL | re.IGNORECASE)
        if match:
            original_sections[section_name] = match.group(0)
    
    # Check if sections exist in formatted version
    for section_name, original_content in original_sections.items():
        # Extract key data points from original
        key_data = extract_key_data(original_content)
        
        # Check if section exists in formatted (also check for SECTION_HEADER marker)
        formatted_pattern = section_patterns[section_name].replace(r'(?:#+\s*)?', r'(?:SECTION_HEADER:\s*)?')
        formatted_match = re.search(formatted_pattern, formatted_text, re.DOTALL | re.IGNORECASE)
        if not formatted_match:
            # Try without the header marker
            formatted_match = re.search(section_patterns[section_name].replace(r'(?:#+\s*)?', ''), formatted_text, re.DOTALL | re.IGNORECASE)
        if not formatted_match:
            validation_result['warnings'].append(f"Section '{section_name}' not found in formatted version")
            validation_result['missing_sections'].append(section_name)
            continue
        
        formatted_content = formatted_match.group(0)
        
        # Check if key data points are preserved
        for data_key, data_value in key_data.items():
            # Handle lists (bullet_points, plain_text_lines, numeric_values, etc.)
            if isinstance(data_value, list):
                if data_key in ['bullet_points', 'plain_text_lines']:
                    # Special handling for sections with format requirements
                    # Treatment Considerations: should be plain lines (no bullets) in formatted version
                    if section_name == 'Treatment Considerations' and data_key == 'bullet_points':
                        # For Treatment Considerations, check for plain lines (bullets removed)
                        # The formatted version should have the same content but without bullets
                        missing_items = []
                        for item in data_value:
                            if not item or len(item.strip()) < 5:
                                continue
                            # Remove bullets from item for comparison
                            clean_item = re.sub(r'^[•\-\*]\s*', '', str(item)).strip()
                            # Check if this item appears as a plain line (no bullet) in formatted content
                            found = False
                            # Look for the content without bullet prefix
                            if clean_item.lower() in formatted_content.lower():
                                found = True
                            else:
                                # Try partial match (first 20 chars)
                                if len(clean_item) > 20:
                                    if clean_item[:20].lower() in formatted_content.lower():
                                        found = True
                                # Try matching key phrases (words)
                                words = clean_item.split()
                                if len(words) >= 3:
                                    matching_words = sum(1 for word in words if len(word) > 3 and word.lower() in formatted_content.lower())
                                    if matching_words >= min(3, len(words) * 0.6):
                                        found = True
                            
                            if not found:
                                missing_items.append(clean_item[:80])
                        
                        if missing_items:
                            validation_result['warnings'].append(
                                f"Some items from '{data_key}' may be missing in '{section_name}' section ({len(missing_items)} items)"
                            )
                            for missing_item in missing_items[:3]:
                                validation_result['missing_data'].append({
                                    'section': section_name,
                                    'key': data_key,
                                    'value': missing_item
                                })
                        continue
                    
                    # Oral Appliance Options: should be a table in formatted version, not bullets
                    if section_name == 'Oral Appliance Options' and data_key == 'bullet_points':
                        # For Oral Appliance Options, the formatted version should have a table
                        # Check if table exists with device names
                        has_table = 'Device' in formatted_content and 'Key Features' in formatted_content
                        if not has_table:
                            validation_result['warnings'].append(
                                f"Oral Appliance Options section should be a table but appears to be missing"
                            )
                        # Don't check individual bullets since they're replaced with table rows
                        continue
                    
                    # Default handling for other sections
                    missing_items = []
                    for item in data_value:
                        if not item or len(item.strip()) < 5:  # Skip very short items
                            continue
                        # Remove bullets and markdown formatting from item for comparison
                        clean_item = re.sub(r'^[•\-\*]\s*', '', str(item)).strip()
                        clean_item = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean_item)  # Remove bold
                        clean_item = re.sub(r'^#+\s+', '', clean_item)  # Remove markdown headers
                        # Check if this item appears in formatted content (allowing for formatting changes)
                        found = False
                        # Try exact match first
                        if clean_item.lower() in formatted_content.lower():
                            found = True
                        else:
                            # Try partial match (first 20 chars)
                            if len(clean_item) > 20:
                                if clean_item[:20].lower() in formatted_content.lower():
                                    found = True
                            # Try matching key phrases (words) - be more lenient
                            words = [w for w in clean_item.split() if len(w) > 2]  # Filter out very short words
                            if len(words) >= 2:
                                # Check if at least 50% of meaningful words appear
                                matching_words = sum(1 for word in words if word.lower() in formatted_content.lower())
                                if matching_words >= max(2, len(words) * 0.5):  # At least 50% of words match
                                    found = True
                        
                        if not found:
                            missing_items.append(clean_item[:80])
                    
                    if missing_items:
                        validation_result['warnings'].append(
                            f"Some items from '{data_key}' may be missing in '{section_name}' section ({len(missing_items)} items)"
                        )
                        for missing_item in missing_items[:3]:  # Show first 3
                            validation_result['missing_data'].append({
                                'section': section_name,
                                'key': data_key,
                                'value': missing_item
                            })
                elif data_key == 'numeric_values':
                    # Check if any of the numeric values appear in formatted content
                    found_any = False
                    for val in data_value:
                        if str(val) in formatted_content:
                            found_any = True
                            break
                    if not found_any and data_value:
                        validation_result['warnings'].append(
                            f"Numeric values from '{data_key}' may be missing in '{section_name}' section"
                        )
                continue
            
            # Skip if data_value is not a string or is empty/placeholder
            if not isinstance(data_value, str) or not data_value or data_value in ['Not provided', '—', 'N/A', 'null', 'None']:
                continue
            
            # Remove markdown formatting for comparison
            clean_data_value = re.sub(r'\*\*([^*]+)\*\*', r'\1', str(data_value))
            clean_data_value = re.sub(r'^#+\s+', '', clean_data_value)
            clean_data_value = re.sub(r'^[•\-\*]\s*', '', clean_data_value)
            
            # Check if this data appears in formatted version (allowing for formatting changes)
            if clean_data_value.lower() not in formatted_content.lower():
                # Try to find similar content (allowing for minor formatting differences)
                similar_found = False
                # Check for partial matches
                if len(clean_data_value) > 15:
                    # Check first 15 chars
                    if clean_data_value[:15].lower() in formatted_content.lower():
                        similar_found = True
                # Try word-level matching
                if not similar_found and len(clean_data_value) > 20:
                    words = [w for w in clean_data_value.split() if len(w) > 3]
                    if len(words) >= 2:
                        matching_words = sum(1 for word in words if word.lower() in formatted_content.lower())
                        if matching_words >= max(2, len(words) * 0.5):
                            similar_found = True
                
                if not similar_found:
                    # Truncate value for display
                    display_value = clean_data_value[:50] if len(clean_data_value) > 50 else clean_data_value
                    validation_result['warnings'].append(
                        f"Data point '{data_key}' (value: {display_value}...) may be missing in '{section_name}' section"
                    )
                    validation_result['missing_data'].append({
                        'section': section_name,
                        'key': data_key,
                        'value': clean_data_value[:100] if isinstance(clean_data_value, str) else str(clean_data_value)[:100]
                    })
    
    # Check for critical sections that must exist
    critical_sections = ['Personal Details', 'Sleep Study Data', 'Structural Observations', 'Device Design', 'Oral Appliance Options', 'Recommendations', 'Recommendations for Further Evaluation']
    for section in critical_sections:
        # Check if section exists in original (try both exact name and variations)
        found_in_original = False
        for orig_section_name in original_sections.keys():
            if section.lower() in orig_section_name.lower() or orig_section_name.lower() in section.lower():
                found_in_original = True
                break
        
        if not found_in_original:
            # Only warn if we're sure it should exist (check if similar section exists)
            similar_exists = any(s.lower() in section.lower() or section.lower() in s.lower() for s in original_sections.keys())
            if not similar_exists:
                validation_result['warnings'].append(f"Critical section '{section}' not found in original")
        else:
            # Check if it's missing in formatted version
            found_in_formatted = False
            for formatted_section_name in section_patterns.keys():
                if section.lower() in formatted_section_name.lower() or formatted_section_name.lower() in section.lower():
                    # Check if this section exists in formatted text
                    formatted_match = re.search(section_patterns[formatted_section_name], formatted_text, re.DOTALL | re.IGNORECASE)
                    if formatted_match:
                        found_in_formatted = True
                        break
            
            if not found_in_formatted:
                validation_result['errors'].append(f"Critical section '{section}' is missing in formatted version")
                validation_result['passed'] = False
                validation_result['missing_sections'].append(section)
    
    # Check title preservation
    if 'VizBriz' not in formatted_text and 'OSA Data Assessment Report' in original_text:
        validation_result['warnings'].append("Title may not be correctly formatted")
    
    return validation_result

def extract_key_data(section_content: str) -> dict:
    """Extract key data points from a section"""
    import re
    key_data = {}
    
    # Extract common patterns
    patterns = {
        'AHI': r'AHI[:\s|]*([\d.]+)',
        'RDI': r'RDI[:\s|]*([\d.]+)',
        'ODI': r'ODI[:\s|]*([\d.]+)',
        'Gender': r'Gender[:\s|]*([MF]|Male|Female)',
        'Age': r'Age[:\s|]*(\d+)',
        'BMI': r'BMI[:\s|]*([\d.]+)',
        'Weight': r'Weight[:\s|]*([\d.]+[^\s]*)',
        'Height': r'Height[:\s|]*([\d.]+[^\s]*)',
    }
    
    for key, pattern in patterns.items():
        match = re.search(pattern, section_content, re.IGNORECASE)
        if match:
            key_data[key] = match.group(1)
    
    # Extract any numeric values that might be important
    numeric_values = re.findall(r'\b\d+\.?\d*\b', section_content)
    if numeric_values:
        key_data['numeric_values'] = numeric_values[:5]  # Store first 5 numeric values
    
    # Extract bullet points for Observations, Treatment Considerations, Recommendations, Oral Appliance Options
    # Handle both markdown bullets (*, -, •) and plain text lines
    bullet_points = re.findall(r'^[•\-\*]\s*(.+)$', section_content, re.MULTILINE)
    if bullet_points:
        key_data['bullet_points'] = bullet_points
    else:
        # If no bullets, extract plain text lines (non-empty lines that aren't headers or table rows)
        lines = [line.strip() for line in section_content.split('\n') if line.strip()]
        # Filter out headers, table rows, and very short lines
        plain_lines = []
        for line in lines:
            # Skip if it's a header (all caps, short, or contains section name)
            if re.match(r'^[A-Z][A-Z\s]{10,}$', line) and len(line) < 60:
                continue
            # Skip if it's a table row (contains |)
            if '|' in line:
                continue
            # Skip if it's very short (likely a header)
            if len(line) < 10:
                continue
            # Skip if it's a field label (ends with :)
            if line.endswith(':'):
                continue
            plain_lines.append(line)
        
        if plain_lines:
            key_data['plain_text_lines'] = plain_lines
    
    # Extract table-like data for Device Design
    if "Device Design Data Considerations" in section_content or "Device Design" in section_content:
        device_design_fields = [
            "Mandibular Advancement", "Vertical Opening", "Anterior Window",
            "Retention Features", "Material", "Pre-set", "Anterior Acrylic",
            "Coverage", "Clinical Notes"
        ]
        device_data = {}
        for field in device_design_fields:
            match = re.search(rf'{re.escape(field)}\s*[:\s]*([^\n]+)', section_content, re.IGNORECASE)
            if match:
                device_data[field] = match.group(1).strip()
        if device_data:
            key_data['device_design_data'] = device_data
    
    # Extract table-like data for Oral Appliance Options
    if "Oral Appliance Options for Consideration" in section_content or "Oral Appliance Options" in section_content:
        oral_appliance_data = re.findall(r'^(Emerald Herbst|Respire Herbst Pink AT|Daynaflex Herbst|OASYS|Dorsal)\s*[:\s]*([^\n]+)', section_content, re.MULTILINE | re.IGNORECASE)
        if oral_appliance_data:
            key_data['oral_appliance_data'] = oral_appliance_data
    
    # Extract all meaningful text content (for comprehensive comparison)
    # Remove headers, markdown formatting, and extract actual content
    content_text = section_content
    # Remove markdown headers
    content_text = re.sub(r'^#+\s+', '', content_text, flags=re.MULTILINE)
    # Remove markdown bold/italic
    content_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', content_text)
    content_text = re.sub(r'__([^_]+)__', r'\1', content_text)
    # Remove table pipes (keep content)
    content_text = re.sub(r'\|\s*', ' ', content_text)
    # Extract sentences and meaningful phrases
    sentences = re.findall(r'[A-Z][^.!?]*[.!?]', content_text)
    if sentences:
        key_data['sentences'] = sentences[:10]  # Store first 10 sentences
    
    return key_data

def _format_level4_report(response_text: str) -> str:
    """Format Level 4 report response into HTML - convert all content to tables"""
    import re
    from html import escape
    
    if not response_text:
        return '<p>No response content available.</p>'
    
    lines = response_text.split('\n')
    html_parts = ['<div class="formatted-report" style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, \'Helvetica Neue\', Arial, sans-serif; line-height: 1.6; color: #1a1a1a; background-color: #fff;">']
    
    current_section = None
    section_items = []  # Collect items for current section to make into table
    in_markdown_table = False
    table_rows = []
    
    def _render_markdown_table_for_section(rows, section_name):
        """
        Render a markdown table with optional section-aware normalization.
        Some LLM outputs produce "label rows" + many single-cell rows that are hard to read.
        This normalizes those into clearer key/value tables for specific sections.
        """
        if not rows:
            return _render_table(rows)

        sec = (section_name or '').strip().lower()

        # Normalize "Clinical Background, Complaints & Goals" style tables into 1 row per label.
        if 'clinical background' in sec and 'goal' in sec:
            # Drop obvious header rows (Field|Value, Item|Details, etc.)
            normalized = []
            start_idx = 0
            if rows and len(rows[0]) >= 2:
                h0 = (rows[0][0] or '').strip().lower()
                h1 = (rows[0][1] or '').strip().lower()
                if (h0, h1) in {('field', 'value'), ('item', 'details')}:
                    start_idx = 1

            labels = {
                'medical history': 'Medical History',
                'patient complaints': 'Patient Complaints',
                'treatment goals': 'Treatment Goals',
                'patient self-reported symptoms': 'Patient Self-Reported Symptoms',
                'clinical background': 'Clinical Background',
                'complaints': 'Patient Complaints',
                'goals': 'Treatment Goals',
                'medications': 'Medications',
            }

            current_label = None
            current_values = []

            def flush():
                nonlocal current_label, current_values
                if current_label is None:
                    return
                value = '; '.join([v for v in current_values if v]).strip()
                if not value:
                    value = 'Not provided'
                normalized.append([current_label, value])
                current_label = None
                current_values = []

            for r in rows[start_idx:]:
                if not r:
                    continue
                cells = [str(c).strip() for c in r if str(c).strip()]
                if not cells:
                    continue

                first = cells[0].rstrip(':').strip().lower()
                if first in labels:
                    # New labeled bucket
                    flush()
                    current_label = labels[first]
                    # Capture inline value if present
                    inline = cells[1:] if len(cells) > 1 else []
                    if inline:
                        current_values.append(' '.join(inline).strip())
                    continue

                # Otherwise: treat as part of current label's value list
                if current_label is None:
                    # No label yet: keep the row as-is (pad to two cols if needed)
                    if len(cells) == 1:
                        normalized.append([cells[0], ''])
                    else:
                        normalized.append([cells[0], ' '.join(cells[1:]).strip()])
                else:
                    if len(cells) == 1:
                        current_values.append(cells[0])
                    else:
                        # Preserve multi-cell content by joining as a phrase
                        current_values.append(' '.join(cells).strip())

            flush()

            # Ensure we end with a standard 2-column table
            return _render_table(normalized)

        return _render_table(rows)

    def flush_section_items():
        """Convert collected section items into a table"""
        nonlocal section_items, current_section
        if not section_items:
            return ''
        
        # Normalize/clean items (trim, drop obvious header artifacts)
        cleaned_items = []
        for raw in section_items:
            if raw is None:
                continue
            s = str(raw).strip()
            if not s:
                continue
            # These sometimes appear in LLM/plain-text tables and should not become rows
            if re.match(r'^(field|item)\s+value$', s, re.IGNORECASE):
                continue
            if re.match(r'^item\s+details$', s, re.IGNORECASE):
                continue
            cleaned_items.append(s)

        # Special handling: ENT + DISE often appear as sublabels within one section and
        # can otherwise render as "label row" + "value row beneath". Group into proper
        # key/value rows to keep alignment consistent.
        if current_section and ('ent' in current_section.lower() and 'dise' in current_section.lower() and 'finding' in current_section.lower()):
            buckets = {'ENT Findings': [], 'DISE Findings': []}
            active = None
            for s in cleaned_items:
                m_ent = re.match(r'^(ENT(?:\s*/\s*Sinus)?\s*Findings)\s*:?\s*(.*)$', s, re.IGNORECASE)
                if m_ent:
                    active = 'ENT Findings'
                    tail = (m_ent.group(2) or '').strip()
                    if tail:
                        buckets[active].append(tail)
                    continue
                m_dise = re.match(r'^(DISE\s*Findings)\s*:?\s*(.*)$', s, re.IGNORECASE)
                if m_dise:
                    active = 'DISE Findings'
                    tail = (m_dise.group(2) or '').strip()
                    if tail:
                        buckets[active].append(tail)
                    continue

                if active:
                    buckets[active].append(s)

            ent_val = ' '.join(buckets['ENT Findings']).strip()
            dise_val = ' '.join(buckets['DISE Findings']).strip()
            cleaned_items = [
                f"ENT Findings: {ent_val}" if ent_val else "ENT Findings: Not provided",
                f"DISE Findings: {dise_val}" if dise_val else "DISE Findings: Not provided",
            ]

        # Generic fix: merge "Key:" lines whose value is on the next line (no colon).
        merged_items = []
        i = 0
        while i < len(cleaned_items):
            cur = cleaned_items[i].strip()
            if cur.endswith(':') and (i + 1) < len(cleaned_items):
                nxt = cleaned_items[i + 1].strip()
                # Only merge when next line doesn't itself look like a key/value row
                if nxt and (':' not in nxt):
                    merged_items.append(f"{cur[:-1].strip()}: {nxt}")
                    i += 2
                    continue
            merged_items.append(cur)
            i += 1

        section_items = merged_items

        # NOTE: Oral Appliance sections are now generated by LLM with dynamic content
        # based on patient canonical data. No hardcoded device injection.
        
        # Determine the best table format based on content
        table_html = ['<table style="width: 100%; border-collapse: collapse; margin: 1rem 0; border: 1px solid #ddd;">']
        
        # Check if items have "Key: Value" format
        has_key_value = any(':' in item for item in section_items)
        
        if has_key_value:
            # Two-column table with Key | Value
            table_html.append('<tr><th style="background-color: #2563eb; color: white; padding: 10px; text-align: left; font-weight: 700; border: 1px solid #1d4ed8; width: 30%;">Item</th>')
            table_html.append('<th style="background-color: #2563eb; color: white; padding: 10px; text-align: left; font-weight: 700; border: 1px solid #1d4ed8;">Details</th></tr>')
            
            for item in section_items:
                # Split on first colon
                if ':' in item:
                    parts = item.split(':', 1)
                    key = parts[0].strip()
                    value = parts[1].strip() if len(parts) > 1 else ''
                    # Remove bold markdown from key
                    key = re.sub(r'\*\*([^*]+)\*\*', r'\1', key)
                    value = re.sub(r'\*\*([^*]+)\*\*', r'\1', value)
                    table_html.append(f'<tr><td style="padding: 8px; border: 1px solid #ddd; color: #1a1a1a; background-color: #fff; font-weight: 600;">{escape(key)}</td>')
                    table_html.append(f'<td style="padding: 8px; border: 1px solid #ddd; color: #1a1a1a; background-color: #fff;">{escape(value)}</td></tr>')
                else:
                    # No colon, put in single cell spanning both columns
                    item_clean = re.sub(r'\*\*([^*]+)\*\*', r'\1', item)
                    table_html.append(f'<tr><td colspan="2" style="padding: 8px; border: 1px solid #ddd; color: #1a1a1a; background-color: #fff;">{escape(item_clean)}</td></tr>')
        else:
            # Single column table
            table_html.append('<tr><th style="background-color: #2563eb; color: white; padding: 10px; text-align: left; font-weight: 700; border: 1px solid #1d4ed8;">Details</th></tr>')
            
            for item in section_items:
                item_clean = re.sub(r'\*\*([^*]+)\*\*', r'\1', item)
                table_html.append(f'<tr><td style="padding: 8px; border: 1px solid #ddd; color: #1a1a1a; background-color: #fff;">{escape(item_clean)}</td></tr>')
        
        table_html.append('</table>')
        section_items = []
        return ''.join(table_html)
    
    for line in lines:
        line_stripped = line.strip()
        
        # Skip empty lines
        if not line_stripped:
            if in_markdown_table:
                html_parts.append(_render_markdown_table_for_section(table_rows, current_section))
                table_rows = []
                in_markdown_table = False
            continue
        
        # Skip common plain-text table header artifacts that should never render as sections
        # (These often appear as "Field Value" or "Item Details" and cause misaligned sections.)
        if re.match(r'^(field\s+value|item\s+details)$', line_stripped, re.IGNORECASE):
            continue

        # Handle markdown headers (## Header)
        header_match = re.match(r'^(#{1,3})\s+(.+)$', line_stripped)
        if header_match:
            # Flush any pending section items
            if section_items:
                html_parts.append(flush_section_items())
            if in_markdown_table:
                html_parts.append(_render_table(table_rows))
                table_rows = []
                in_markdown_table = False
            
            level = len(header_match.group(1))
            text = header_match.group(2).strip()
            
            # Skip the main title - it's already in the template header
            if 'OSA Data Assessment Report' in text:
                continue
            
            current_section = text
            html_parts.append(f'<h{level+1} style="color: #1d4ed8; margin-top: 1.5rem; margin-bottom: 0.5rem; border-bottom: 2px solid #2563eb; padding-bottom: 0.3rem;">{escape(text)}</h{level+1}>')
            continue
        
        # Handle markdown tables (lines with |)
        if '|' in line_stripped and not line_stripped.startswith('•'):
            if re.match(r'^[\|\s\-:]+$', line_stripped):
                continue  # Skip separator rows
            
            # Flush section items first
            if section_items:
                html_parts.append(flush_section_items())
            
            if not in_markdown_table:
                in_markdown_table = True
                table_rows = []
            
            cells = [c.strip() for c in line_stripped.split('|')]
            cells = [c for c in cells if c]
            if cells:
                table_rows.append(cells)
            continue
        
        # If we were in a markdown table but this line doesn't have pipes, end the table
        if in_markdown_table:
            html_parts.append(_render_markdown_table_for_section(table_rows, current_section))
            table_rows = []
            in_markdown_table = False

        # ENT/DISE formatting: treat "ENT Findings:" and "DISE Findings:" as sub-labels
        # within the "ENT and DISE Findings" section, not as new section headers.
        if current_section and re.match(r'^ent\s+and\s+dise\s+findings$', str(current_section).strip(), re.IGNORECASE):
            if re.match(r'^(ENT(?:\s*/\s*Sinus)?\s*Findings|DISE\s*Findings)\s*:?\s*$', line_stripped, re.IGNORECASE):
                # Normalize to "Label:" so the next line can be merged as "Label: Value"
                label = re.sub(r'\s*:?\s*$', '', line_stripped).strip()
                section_items.append(f"{label}:")
                continue
        
        # Check if line looks like a section header
        is_section_header = False
        
        # Exact section header patterns from the master prompt (REQUIRED SECTION ORDER)
        # These must match the LLM output exactly
        exact_section_patterns = [
            # Section 1: DISCLAIMER (opening)
            'DISCLAIMER',
            # Section 3: Personal Details
            'Personal Details',
            # Section 4: Clinical Background, Complaints & Goals
            'Clinical Background, Complaints & Goals',
            'Clinical Background',
            # Section 5: ENT and DISE Findings
            'ENT and DISE Findings',
            'ENT / Sinus Findings',
            'ENT Findings',
            # Section 6: Sleep Study Data
            'Sleep Study Data',
            'Sleep Study',
            # Section 7: Observations
            'Observations',
            # Section 8: Structural Observations from Imaging Data
            'Structural Observations from Imaging Data',
            'Structural Observations',
            # Section 9: Possible Treatment Considerations
            'Possible Treatment Considerations',
            # Section 10: Device Design Data Considerations
            'Device Design Data Considerations',
            # Section 11A: Oral Appliance Therapy Pathway
            'Oral Appliance Therapy Pathway',
            # Section 11B: Recommended Appliance Design Classes
            'Recommended Appliance Design Classes',
            # Section 12: Recommendations for Further Evaluation
            'Recommendations for Further Evaluation',
            # Section 13: FINAL DISCLAIMER
            'FINAL DISCLAIMER',
            # Other common variations
            'Oral Appliance Options',
            'Conclusion',
        ]
        
        # Check for exact section header match
        for pattern in exact_section_patterns:
            if line_stripped == pattern or line_stripped.startswith(pattern + ' ') or (pattern in line_stripped and len(line_stripped) < 60):
                # Make sure it's not a "Key Value" line (contains common value words)
                value_indicators = ['Not provided', 'recommended', 'Not available', 'events/hour', 'kg', 'cm', 'years', '%']
                is_value_line = any(indicator in line_stripped for indicator in value_indicators)
                if not is_value_line:
                    is_section_header = True
                    break
        
        # Also check for all-caps section headers or title-case headers without value indicators
        if not is_section_header:
            # Check if it's a title-case header (starts with caps, all words capitalized, no numbers, short)
            if re.match(r'^[A-Z][A-Za-z\s/&,]+$', line_stripped) and len(line_stripped) < 50:
                # Make sure it's not a "Key Value" line
                value_indicators = ['Not provided', 'recommended', 'Not available', 'events/hour', 'kg', 'cm', 'years', '%', 'devices', 'therapy', 'obstruction']
                is_value_line = any(indicator.lower() in line_stripped.lower() for indicator in value_indicators)
                # Also check if it looks like "Parameter Value" format (last word is a value)
                words = line_stripped.split()
                if len(words) >= 2:
                    last_words = ' '.join(words[-2:]).lower()
                    common_values = ['not provided', 'not available', 'cpap recommended', 'cpap therapy']
                    is_value_line = is_value_line or any(v in last_words for v in common_values)
                if not is_value_line and len(words) <= 6:
                    is_section_header = True
        
        if is_section_header:
            # Skip the main title - it's already in the template header
            if 'OSA Data Assessment Report' in line_stripped:
                continue
            
            # Flush any pending section items
            if section_items:
                html_parts.append(flush_section_items())
            
            text = line_stripped
            current_section = text
            html_parts.append(f'<h3 style="color: #1d4ed8; font-size: 1.05rem; margin-top: 1.2rem; margin-bottom: 0.4rem; border-bottom: 2px solid #2563eb; padding-bottom: 0.25rem;">{escape(text)}</h3>')
            continue
        
        # Handle bullet points - collect for table (multiple bullet formats)
        # Check if line starts with any bullet-like character
        is_bullet = False
        bullet_text = line_stripped
        
        # Try multiple bullet patterns
        if line_stripped.startswith('•') or line_stripped.startswith('·'):
            is_bullet = True
            bullet_text = line_stripped[1:].strip()
        elif line_stripped.startswith('- ') or line_stripped.startswith('* '):
            is_bullet = True
            bullet_text = line_stripped[2:].strip()
        elif line_stripped.startswith('-') or line_stripped.startswith('*'):
            is_bullet = True
            bullet_text = line_stripped[1:].strip()
        elif re.match(r'^[\u2022\u2023\u2043\u2219\u25AA\u25AB\u25CF\u25CB\u25E6\u29BE\u29BF]', line_stripped):
            # Unicode bullet characters
            is_bullet = True
            bullet_text = line_stripped[1:].strip()
        
        if is_bullet and bullet_text and len(bullet_text) > 3:
            section_items.append(bullet_text)
            continue
        
        # Also catch lines that look like bullet items but without bullet character
        # (sometimes LLM outputs list items without bullets)
        if current_section and line_stripped and ':' not in line_stripped[:20]:
            # Check if this looks like a list item (starts with capital, reasonable length)
            if re.match(r'^[A-Z][a-z]', line_stripped) and 20 < len(line_stripped) < 200:
                # Check if previous items exist (we're in a list context)
                if section_items or 'Recommendation' in str(current_section) or 'Treatment' in str(current_section):
                    section_items.append(line_stripped)
                    continue
        
        # Handle "Key: Value" lines - collect for table
        if ':' in line_stripped and current_section:
            section_items.append(line_stripped)
            continue
        
        # Handle "Key Value" lines WITHOUT colons (LLM sometimes outputs like "Current Therapy CPAP recommended")
        # These are typically in Device Design or similar sections
        if current_section and line_stripped:
            # Check for common "Key Value" patterns without colon
            key_value_patterns = [
                (r'^(Current Therapy)\s+(.+)$', True),
                (r'^(Pressure Settings)\s+(.+)$', True),
                (r'^(Average Usage)\s+(.+)$', True),
                (r'^(Mandibular Advancement)\s+(.+)$', True),
                (r'^(Vertical Opening)\s+(.+)$', True),
                (r'^(Protrusion Range)\s+(.+)$', True),
                (r'^(Condylar Position)\s+(.+)$', True),
                (r'^(Titration Status)\s+(.+)$', True),
                (r'^(Anterior Window)\s+(.+)$', True),
                (r'^(Retention Features)\s+(.+)$', True),
                (r'^(Material)\s+(.+)$', True),
                (r'^(Pre-set)\s+(.+)$', True),
                (r'^(Anterior Acrylic)\s+(.+)$', True),
                (r'^(Coverage)\s+(.+)$', True),
                (r'^(Clinical Notes)\s+(.+)$', True),
            ]
            
            matched_kv = False
            for pattern, _ in key_value_patterns:
                match = re.match(pattern, line_stripped, re.IGNORECASE)
                if match:
                    # Convert to "Key: Value" format for consistent processing
                    key = match.group(1)
                    value = match.group(2)
                    section_items.append(f"{key}: {value}")
                    matched_kv = True
                    break
            
            if matched_kv:
                continue
        
        # Regular paragraph - if short and in a section, add to table; otherwise render as paragraph
        # Special handling for DISCLAIMER section - always capture as paragraph, not table
        if current_section and 'DISCLAIMER' in current_section.upper() and 'FINAL' not in current_section.upper():
            # DISCLAIMER section should render as paragraph, not table
            if section_items:
                html_parts.append(flush_section_items())
            text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', line_stripped)
            html_parts.append(f'<p style="margin-bottom: 0.5rem; color: #1a1a1a;">{text}</p>')
        elif current_section and len(line_stripped) > 10:
            section_items.append(line_stripped)
        else:
            # Flush section items first
            if section_items:
                html_parts.append(flush_section_items())
            text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', line_stripped)
            html_parts.append(f'<p style="margin-bottom: 0.5rem; color: #1a1a1a;">{text}</p>')
    
    # Flush any remaining items
    if section_items:
        html_parts.append(flush_section_items())
    if in_markdown_table:
        html_parts.append(_render_table(table_rows))
    
    # Post-process the HTML to:
    # 1. Add Important Note before Structural Observations table
    # 2. Ensure Oral Appliance section has specific devices and appears before FINAL DISCLAIMER
    # 3. Remove duplicate sections and content
    # 4. Fix "Time Below 90%" to be included in Sleep Study Data table
    
    html_result = ''.join(html_parts)
    
    # Remove duplicate text blocks (same paragraph appearing multiple times consecutively)
    # This is simpler and safer than removing entire sections
    import re
    para_pattern = r'<p[^>]*>([^<]+)</p>'
    seen_paras = set()
    def deduplicate_paras(match):
        para_text = match.group(1).strip()
        # Normalize whitespace for comparison
        para_normalized = re.sub(r'\s+', ' ', para_text)
        # Only remove if it's a substantial duplicate (more than 20 chars) and we've seen it before
        if para_normalized in seen_paras and len(para_normalized) > 20:
            return ''  # Remove duplicate
        seen_paras.add(para_normalized)
        return match.group(0)
    
    html_result = re.sub(para_pattern, deduplicate_paras, html_result)
    
    # Remove duplicate table rows (same content appearing in multiple tables)
    # Look for duplicate table rows within the same section
    table_row_pattern = r'<tr[^>]*>(.*?)</tr>'
    seen_rows = set()
    def deduplicate_rows(match):
        row_content = match.group(1).strip()
        # Normalize whitespace
        row_normalized = re.sub(r'\s+', ' ', row_content)
        if row_normalized in seen_rows and len(row_normalized) > 10:
            return ''  # Remove duplicate row
        seen_rows.add(row_normalized)
        return match.group(0)
    
    # Only deduplicate within the same table context (to avoid removing legitimate similar rows in different sections)
    # For now, just remove exact duplicate consecutive rows
    html_result = re.sub(table_row_pattern, deduplicate_rows, html_result)
    
    # Add Important Note before Structural Observations table
    structural_note = '''<div style="background-color: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; padding: 12px; margin-bottom: 1rem; font-size: 0.9rem; color: #856404;">
<strong>Important Note:</strong> This section presents observations based on imaging data and does not constitute an official radiological interpretation. Any imaging findings, including the accompanying CBCT image, should be reviewed and confirmed by a certified radiologist or physician before making clinical decisions.
</div>'''
    
    # Insert the note after the Structural Observations header
    if 'Structural Observations' in html_result:
        # Find the header and insert note after it
        import re
        html_result = re.sub(
            r'(<h3[^>]*>Structural Observations[^<]*</h3>)',
            r'\1' + structural_note,
            html_result
        )
    
    # NOTE: Clinical Images section is rendered in the template (level4_report_viewer.html)
    # at view time, not at generation time. This ensures it appears for all reports.
    
    # NOTE: Oral Appliance sections (6, 7, 8, 9) are now generated by the LLM
    # based on the system prompt. We no longer inject hardcoded device lists.
    # The LLM generates:
    #   Section 6/7: Oral Appliance Therapy Pathway Recommendations
    #   Section 7/8: Recommended Appliance Design Classes
    #   Section 8/9: Device Brand/Model Options (optional catalogue)
    
    # Fix "Details" header to be "FINAL DISCLAIMER"
    html_result = re.sub(
        r'<h3([^>]*)>Details</h3>',
        r'<h3\1>FINAL DISCLAIMER</h3>',
        html_result
    )
    
    # Also wrap FINAL DISCLAIMER content in a styled box
    if 'FINAL DISCLAIMER' in html_result:
        # Find the FINAL DISCLAIMER section and wrap its content in a styled disclaimer box
        html_result = re.sub(
            r'(<h3[^>]*>FINAL DISCLAIMER</h3>)\s*(<table[^>]*>.*?</table>)',
            r'\1<div style="background-color: #fef2f2; border: 1px solid #fca5a5; border-radius: 6px; padding: 16px; margin: 1rem 0; color: #991b1b;">\2</div>',
            html_result,
            flags=re.DOTALL
        )
    
    return html_result


def _render_table(rows):
    """Render table rows as HTML table"""
    if not rows:
        return ''
    
    from html import escape
    
    # Filter out empty rows (rows where all cells are empty or just whitespace)
    filtered_rows = []
    for row in rows:
        if any(cell.strip() for cell in row):
            filtered_rows.append(row)
    
    if not filtered_rows:
        return ''
    
    html = ['<table style="width: 100%; border-collapse: collapse; margin: 1rem 0; border: 1px solid #ddd;">']
    
    for i, row in enumerate(filtered_rows):
        html.append('<tr>')
        for cell in row:
            if i == 0:
                # First row is header - green background, white text
                html.append(f'<th style="background-color: #2563eb; color: white; padding: 10px; text-align: left; font-weight: 700; border: 1px solid #1d4ed8;">{escape(cell)}</th>')
            else:
                # Data rows - white/light gray background, BLACK text
                html.append(f'<td style="padding: 8px; border: 1px solid #ddd; color: #1a1a1a; background-color: #fff;">{escape(cell)}</td>')
        html.append('</tr>')
    
    html.append('</table>')
    return ''.join(html)


@reports_files_bp.route('/reports/level4-report/view/<int:history_id>', methods=['GET'])
@login_required
def view_level4_report_formatted(history_id):
    """View a formatted Level 4 report from history"""
    try:
        history_entry = Level4ReportHistory.query.get_or_404(history_id)
        
        # Check access
        patient = Patient.query.get(history_entry.patient_id)
        if not patient or not current_user.can_access_patient(patient):
            return "Access denied", 403
        
        # Check if edit mode is requested
        edit_mode = request.args.get('edit', 'false').lower() == 'true'
        
        if edit_mode:
            # Render editable version - use SAME formatted HTML but make it editable
            original_response = history_entry.response or ''
            formatted_html = _format_level4_report(original_response)
            
            # Make table cells and paragraphs editable
            formatted_html = formatted_html.replace('<td style="', '<td contenteditable="true" style="background-color: #fffef0; ')
            formatted_html = formatted_html.replace('<p style="', '<p contenteditable="true" style="')
            formatted_html = formatted_html.replace('<li style="', '<li contenteditable="true" style="')
            
            return render_template(
                'level4_report_viewer.html',
                report_html=formatted_html,
                patient_name=patient.name,
                patient_id=patient.id,
                created_at=history_entry.created_at,
                llm_provider=history_entry.llm_provider,
                model_used=history_entry.model_used,
                history_id=history_id,
                edit_mode=True  # Flag for template to show save button
            )
        else:
            # Render read-only formatted version
            original_response = history_entry.response or ''
            formatted_html = _format_level4_report(original_response)
            
            # Validate content preservation
            validation_result = _validate_content_preservation(original_response, formatted_html)
            
            return render_template(
                'level4_report_viewer.html',
                report_html=formatted_html,
                patient_name=patient.name,
                patient_id=patient.id,
                created_at=history_entry.created_at,
                llm_provider=history_entry.llm_provider,
                model_used=history_entry.model_used,
                history_id=history_id
            )
    except Exception as exc:
        logger.error('Error viewing formatted Level 4 report: %s', exc, exc_info=True)
        return f"Error loading report: {str(exc)}", 500


@reports_files_bp.route('/reports/level4-report/preview', methods=['POST'])
@login_required
def preview_level4_report_formatted():
    """Preview a formatted Level 4 report from response text"""
    try:
        # Handle both JSON and form data
        if request.is_json:
            data = request.get_json() or {}
        else:
            data = request.form.to_dict()
        
        response_text = data.get('response', '')
        patient_id = data.get('patient_id')
        provider = data.get('provider', 'Unknown')
        model_used = data.get('model_used', '')
        
        if not response_text:
            return jsonify({'success': False, 'error': 'No response text provided'}), 400
        
        # Get patient info if available
        patient_name = 'Unknown Patient'
        if patient_id:
            try:
                patient = Patient.query.get(int(patient_id))
                if patient and current_user.can_access_patient(patient):
                    patient_name = patient.name
            except Exception:
                pass
        
        formatted_html = _format_level4_report(response_text)
        
        # Validate content preservation
        validation_result = _validate_content_preservation(response_text, formatted_html)
        
        # Render the formatted view
        return render_template(
            'level4_report_viewer.html',
            report_html=formatted_html,
            patient_name=patient_name,
            patient_id=patient_id or 0,
            created_at=None,
            llm_provider=provider,
            model_used=model_used,
            history_id=None
        )
    except Exception as exc:
        logger.error('Error previewing formatted Level 4 report: %s', exc, exc_info=True)
        return f"Error formatting report: {str(exc)}", 500


@reports_files_bp.route('/reports/api/level4_report/history', methods=['GET'])
@login_required
def reports_level4_history():
    """Get Level 4 report generation history for a patient"""
    try:
        patient_id = request.args.get('patient_id', type=int)
        limit = request.args.get('limit', 20, type=int)
        
        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400
        
        patient = Patient.query.get(patient_id)
        if not patient or not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        try:
            history = (
                Level4ReportHistory.query
                .filter_by(patient_id=patient_id)
                .order_by(Level4ReportHistory.created_at.desc())
                .limit(limit)
                .all()
            )
        except Exception as db_exc:
            logger.error('Database error loading Level 4 history: %s', db_exc)
            # Check if it's a table doesn't exist error
            error_msg = str(db_exc)
            if 'does not exist' in error_msg.lower() or 'no such table' in error_msg.lower():
                return jsonify({
                    'success': False, 
                    'error': 'History table not found. Please run the SQL script to create the level4_report_history table.'
                }), 500
            return jsonify({'success': False, 'error': f'Database error: {error_msg}'}), 500
        
        payload = []
        for entry in history:
            # Explicitly get prompt and response, handling None cases
            prompt_text = getattr(entry, 'prompt', None) or ''
            response_text = getattr(entry, 'response', None) or ''
            
            # Ensure strings (in case of encoding issues)
            if prompt_text and not isinstance(prompt_text, str):
                prompt_text = str(prompt_text)
            if response_text and not isinstance(response_text, str):
                response_text = str(response_text)
            
            # Log for debugging
            logger.info('History entry %s: prompt length=%d, response length=%d, prompt preview=%s', 
                        entry.id, len(prompt_text), len(response_text), 
                        prompt_text[:50] if prompt_text else 'EMPTY')
            
            payload.append({
                'id': entry.id,
                'prompt': prompt_text,
                'response': response_text,
                'llm_provider': entry.llm_provider or '',
                'model_used': entry.model_used or '',
                'created_at': entry.created_at.isoformat() if entry.created_at else None,
                'created_by': entry.created_by,
            })
        
        logger.info('Returning %d history entries for patient %s', len(payload), patient_id)
        return jsonify({'success': True, 'history': payload})
    except Exception as exc:
        logger.error('Error in reports_level4_history: %s', exc, exc_info=True)
        return jsonify({'success': False, 'error': str(exc)}), 500


# ============================================================================
# LEVEL-4 REPORT WITH KNOWLEDGE BASE (RAG) ROUTES
# ============================================================================

# Level-4 KB System Prompt (Final Production Version - Dr. Briz)
_LEVEL4_KB_SYSTEM_PROMPT = """MASTER PROMPT — DR. BRIZ LEVEL-4 OSA REPORT GENERATOR

You are Dr. Briz, an AI clinical summarization assistant.

Your task is to generate a Level-4 OSA Data Assessment Report using ONLY the provided canonical JSON.

You must follow the report structure and section order demonstrated by the EXEMPLAR templates provided in the USER prompt.
Do not add, remove, rename, or reorder major sections beyond what the exemplars show.

Your output must be PLAIN TEXT ONLY
(no markdown symbols: #, ##, *, -, |, **, etc.).

All tables must follow the fixed-width monospace table rules defined below.
All bullets must use the "•" character only.

IMPORTANT:
- The exemplars you receive may contain markdown (headings/tables) as reference examples.
- Your output MUST still be plain text with NO markdown symbols.

===========================================================================
############################### FORMATTING RULES ###########################
===========================================================================

Fixed-width table model (mandatory)
- Use monospace alignment.
- First column width: 30 chars, left-aligned.
- Second column width: 40 chars, left-aligned.
- Third column (if present) = remaining width.
- Use 2 spaces between columns.
- Wrap lines cleanly when exceeding 110 chars.

Spacing rules
- 2 blank lines before each major section header.
- 1 blank line between content blocks.

Numeric precision (critical)
- Preserve the numeric precision from the canonical JSON (e.g., 5.6% must remain 5.6%).
- Do NOT round or approximate values.

Personal Details (mandatory rows)
- In the "Personal Details" section, ALWAYS output a 2-column fixed-width table with EXACT row labels:
  Sex
  Age
  BMI
  Weight (kg)
  Height (cm)
- If any are missing in canonical JSON → "Not provided".

Example table style:
Field                          Value
Gender                         Male
Age                            42 years
BMI                            28.7 kg/m²

===========================================================================
########################### HALLUCINATION GUARD ############################
===========================================================================

If a value is missing in the canonical JSON → output "Not provided".

Forbidden actions:
- No invented anatomy, pathology, TMJ findings, or DISE scores
- No guessing CPAP pressure, advancement amounts, mm, %
- No general medical knowledge
- No "clinical interpretation" beyond what data explicitly supports

===========================================================================
########################### DATA SOURCE RULESET ############################
===========================================================================

Use ONLY these sources from canonical JSON:
- patient.*
- patient_self_report.symptoms
- clinical_background
- complaints
- goals
- ent_findings
- anatomy.*
- observations.*
- sleep_study.*
- position_stats.*
- treatment_history.*
- treatment_considerations.*
- device_design.*
- follow_up_plan.*

Nothing else.

===========================================================================
########################### 1. BACKGROUND CLEANING #########################
===========================================================================

DEDUPLICATION RULE (clinical_background)
- Split by commas
- Trim whitespace
- Remove duplicates
- Remove schema-like labels (e.g., "heart_disease")
- Output clean list of unique conditions

===========================================================================
################## 2. COMPLAINTS VS SYMPTOMS (CRITICAL) ####################
===========================================================================

Complaints
- Use ONLY the complaints array.

Goals
- Use ONLY the goals array.

Symptoms
- Use ONLY items where: patient_self_report.symptoms.{symptom} == true
- If the symptoms object is missing → "Patient Self-Reported Symptoms: Not provided"

NEVER convert:
- complaints → symptoms
- goals → symptoms
- anatomy → symptoms
- ENT findings → symptoms

===========================================================================
########################### 3. HEBREW NEGATION LOGIC #######################
===========================================================================

If any text contains:
"לא", "ללא", "אין", "שלילי", "לא נמצא"
→ treat the condition as NEGATIVE, even if present: true.

===========================================================================
############################# 4. ENT & DISE RULES ###########################
===========================================================================

ENT Findings
Include:
- ent_findings
- anatomy.nasal_sinus
- sinonasal conditions from cleaned clinical_background

DISE Findings
- Only include if observations.dise exists AND contains data.
- If empty or missing → "DISE Findings: Not provided".
- Never invent collapse patterns, grades, maneuvers, or suitability.

===========================================================================
############################ 5. SLEEP STUDY RULES ###########################
===========================================================================

Use ONLY sleep_study.* fields.

Non-supine AHI rules:
- If missing AND no positional % → "Not provided"
- If missing AND positional % exists → "Not available (slept X% supine)"

REM AHI rule:
- If REM AHI = 0 AND REM time unknown → must include:
  "REM AHI of 0 may indicate minimal REM scoring, limited REM sleep, or no REM scoring available."

===========================================================================
########################### 6. OBSERVATIONS RULESET ########################
===========================================================================

Observations MUST be:
- A pure bullet list using "•"
- Short, objective, data-derived
- NO anatomy
- NO ENT
- NO DISE
- NO treatment
- NO goals

Allowed bullets:
• OSA severity
• oxygen nadir
• snoring percentage
• REM AHI interpretation
• severity confirmation

===========================================================================
################### 7. STRUCTURAL IMAGING FINDINGS RULES ###################
===========================================================================

Use ONLY anatomy fields:
- bite_jaw
- soft_palate
- tongue_base
- arches
- hyoid
- primary_obstruction_site
- conclusion

Never include ENT or sinus findings here.

===========================================================================
################# 8. POSSIBLE TREATMENT CONSIDERATIONS #####################
===========================================================================

If treatment_considerations exist → use them.
Else → output only the following 4 neutral statements:
- CPAP may support airway stability
- Oral appliance therapy may be considered based on anatomy
- Nasal/sinus management may support airway patency
- Weight management may support improvement

STRICT RULE:
This section may NOT mention device types or mechanical designs.

===========================================================================
################ 9. DEVICE DESIGN DATA CONSIDERATIONS TABLE ################
===========================================================================

This section is a DENTIST/CLINICIAN-COMPLETED section in the workflow.
You MUST NOT propose device design values. Only echo device_design.* values if present.

This table MUST ALWAYS include these 9 rows (even if all are "Not provided"):
- Mandibular Advancement
- Vertical Opening
- Anterior Window
- Retention Features
- Material
- Pre-set
- Anterior Acrylic
- Coverage
- Clinical Notes

If a row's value is missing in canonical JSON → "Not provided".

===========================================================================
##################### 10. ORAL APPLIANCE OPTIONS (CLINICIAN INPUT) #########
===========================================================================

This section is clinician-entered.
Only list device names/options IF they are explicitly present in oral_appliance_options.
If oral_appliance_options is missing/empty → output "Not provided".

Do NOT invent device names, brands, models, or features.

===========================================================================
##################### 11. RECOMMENDATIONS FOR FURTHER EVALUATION ###########
===========================================================================

Use follow_up_plan if present.
Else provide neutral:
- Consider ENT evaluation
- Consider sleep medicine follow-up
- Consider follow-up testing as clinically indicated

===========================================================================
############################### 12. FINAL DISCLAIMER #######################
===========================================================================

End with the standard legal disclaimer text.

===========================================================================
########################## REQUIRED SECTION ORDER ##########################
===========================================================================

1. DISCLAIMER
2. OSA Data Assessment Report
3. Personal Details
4. Clinical Background, Complaints & Goals
5. ENT and DISE Findings
6. Sleep Study Data
7. Observations
8. Structural Observations from Imaging Data
9. Possible Treatment Considerations
10. Device Design Data Considerations
11. Oral Appliance Options for Consideration
12. Recommendations for Further Evaluation
13. FINAL DISCLAIMER

===========================================================================
BEGIN REPORT AFTER THE USER SUPPLIES CANONICAL JSON
===========================================================================

"""


# Level-4 JSON Generation Prompt (for generating structured JSON data from patient data)
_LEVEL4_JSON_GENERATION_PROMPT = """You are a clinical data structuring assistant that generates structured JSON data for Level-4 OSA Data Assessment Reports.

Your job is to:
✔️ Extract and structure patient data into a well-formed JSON object
✔️ Include all available patient data from the provided canonical JSON
✔️ Never invent or estimate data
✔️ Use null or omit fields that are not available
✔️ Maintain strict clinical neutrality

OUTPUT FORMAT:
You MUST output ONLY valid JSON. No markdown, no explanations, no additional text.

The JSON structure must follow this exact schema:

{
  "disclaimer": "This AI-generated report assists in analyzing sleep and anatomical data. It does not replace professional diagnosis or radiological interpretation. All findings must be reviewed by a qualified healthcare provider before making clinical decisions.",
  "personal_details": {
    "gender": "<value or null>",
    "age": "<value or null>",
    "bmi": "<value or null>",
    "weight": "<value or null>",
    "height": "<value or null>"
  },
  "clinical_background": {
    "background": "<value or null>",
    "complaints": "<value or null>",
    "goals": "<value or null>"
  },
  "ent_sinus_findings": "<value or null>",
  "sleep_study_data": {
    "ahi": "<value or null>",
    "rdi": "<value or null>",
    "odi": "<value or null>",
    "rem_ahi": "<value or null>",
    "rem_rdi": "<value or null>",
    "rem_odi": "<value or null>",
    "supine_ahi": "<value or null>",
    "supine_rdi": "<value or null>",
    "supine_odi": "<value or null>",
    "non_supine_ahi": "<value or null>",
    "snoring_percent": "<value or null>",
    "o2_nadir": "<value or null>",
    "sleep_efficiency": "<value or null>",
    "total_sleep_time": "<value or null>"
  },
  "observations": [
    "<bullet point 1>",
    "<bullet point 2>",
    "..."
  ],
  "structural_observations": {
    "bite_jaw": "<value or null>",
    "soft_palate_uvula": "<value or null>",
    "tongue_base": "<value or null>",
    "hyoid": "<value or null>",
    "primary_obstruction_site": "<value or null>",
    "nasal_sinus": "<value or null>",
    "neck_findings": "<value or null>",
    "conclusion": "<value or null>"
  },
  "treatment_considerations": [
    "<consideration 1>",
    "<consideration 2>",
    "..."
  ],
  "device_design": {
    "mandibular_advancement": "<value or null>",
    "vertical_opening": "<value or null>",
    "anterior_window": "<value or null>",
    "retention_features": "<value or null>",
    "material": "<value or null>",
    "pre_set": "<value or null>",
    "anterior_acrylic": "<value or null>",
    "coverage": "<value or null>",
    "clinical_notes": "<value or null>"
  },
  "recommendations": [
    "<recommendation 1>",
    "<recommendation 2>",
    "..."
  ],
  "oral_appliance_options": [
    {
      "device_type": "<device name>",
      "key_features": "<features description>"
    },
    ...
  ],
  "final_disclaimer": "This assessment is based solely on available clinical data. All treatment decisions must be made by licensed healthcare professionals."
}

RULES:
- If a field is missing, use null (not "Not provided", not "—", not empty string)
- EXCEPTION: For device_design section, include all 9 fields even if null. The PDF formatter will show "Not provided" for null values (this section is mandatory with all 9 rows)
- For arrays (observations, treatment_considerations, recommendations), use empty array [] if no data
- For oral_appliance_options, include at least 3 devices if possible (Emerald Herbst, Respire Herbst, OASYS/Dorsal)
- All string values should be clean text, no markdown formatting
- Numbers should be strings in the JSON (e.g., "33.0" not 33.0)
- REM AHI special handling: If REM AHI = 0.0, include observation: "REM AHI of 0 may indicate minimal REM scoring, limited REM sleep, or no REM scoring available."
- Device Design fields: Use these exact field names: mandibular_advancement, vertical_opening, anterior_window, retention_features, material, pre_set, anterior_acrylic, coverage, clinical_notes

OUTPUT ONLY THE JSON. NO OTHER TEXT."""


# Level-4 PDF Formatting Prompt (for converting JSON data into PDF-ready formatted text)
_LEVEL4_PDF_FORMATTING_PROMPT = """⚠️⚠️⚠️ CRITICAL: YOU MUST OUTPUT PLAIN TEXT ONLY. ABSOLUTELY NO MARKDOWN WHATSOEVER. ⚠️⚠️⚠️

You are generating a VizBriz Level-4 — OSA Data Assessment Report in PDF layout style.

YOU RECEIVE: Structured JSON data (not text, not markdown - pure JSON)

YOUR JOB: Convert the JSON data into a formatted PDF-ready PLAIN TEXT report (NO markdown, NO ##, NO |, NO **).

⚠️⚠️⚠️ IF YOUR OUTPUT CONTAINS ANY OF THESE SYMBOLS, YOU HAVE COMPLETELY FAILED THE TASK:
- # or ## or ### (markdown headings) → REMOVE THEM
- | (pipes for tables) → REMOVE THEM
- ** or __ (bold markdown) → REMOVE THEM
- ANY markdown syntax → REMOVE IT

YOUR OUTPUT WILL BE REJECTED IF IT CONTAINS MARKDOWN.

This prompt defines the authoritative formatting.

You MUST follow the exact structure, section order, and compact formatting used in the original VizBriz Level-4 reports.

INPUT FORMAT:
You will receive a JSON object with the following structure:
- personal_details: {gender, age, bmi, weight, height}
- clinical_background: {background, complaints, goals}
- ent_sinus_findings: string
- sleep_study_data: {ahi, rdi, odi, rem_ahi, etc.}
- observations: array of strings
- structural_observations: {bite_jaw, soft_palate_uvula, tongue_base, hyoid, primary_obstruction_site, nasal_sinus, neck_findings, conclusion}
- treatment_considerations: array of strings
- device_design: {mandibular_advancement, vertical_opening, anterior_window, retention_features, material, pre_set, anterior_acrylic, coverage, clinical_notes}
- recommendations: array of strings
- oral_appliance_options: array of {device_type, key_features}

Extract values from this JSON and format them according to the rules below.

CRITICAL FORMATTING RULES - ABSOLUTELY NO MARKDOWN:

YOU MUST NEVER USE:
❌ Markdown headings: ##, #, ###, ####
❌ Markdown tables: |, |---|, |---|, |------|
❌ Markdown bold: **text**, __text__
❌ Markdown italic: *text*, _text_
❌ Markdown lists with # or ##
❌ ANY markdown syntax whatsoever

YOU MUST ALWAYS USE:
✅ Plain text section headers (just the text, e.g., "Personal Details")
✅ Plain text tables with spaces for alignment
✅ Plain text formatting only
✅ Title as plain text: "VizBriz Level-4 — OSA Data Assessment Report" (NO # symbol)

EXAMPLE OF CORRECT FORMAT:
VizBriz Level-4 — OSA Data Assessment Report

Personal Details

Field          Value
Gender         M
Age            42
BMI            28.7

EXAMPLE OF INCORRECT FORMAT (DO NOT USE):
# OSA Data Assessment Report
## Personal Details
| Field | Value |
|-------|-------|
| Gender | M |

If you use ANY markdown syntax (##, |, **, etc.), your output is WRONG.

IMPORTANT:
• DO NOT include any images.
• DO NOT reference CBCT slices, screenshots, or embedded visuals.
• Keep the layout extremely compact, clinical, and aligned with the original reports.
• You receive JSON data - extract values and format them according to the rules below.
• If a JSON field is null or missing, OMIT that field entirely (do not use "—", "Not provided", or "N/A").
• EXCEPTION: For Device Design Data Considerations section, ALL 9 rows are MANDATORY. If a value is null, use "Not provided" for that row.
• EXCEPTION: For Oral Appliance Options for Consideration section, it is MANDATORY. If JSON array is empty or null, generate default 3 devices (Emerald Herbst, Respire Herbst Pink AT, Daynaflex Herbst) with their standard features. This section must always appear as a two-column table, never as narrative text.

------------------------------------------------------------
GLOBAL DOCUMENT RULES
------------------------------------------------------------

• Title must be EXACTLY (plain text, NO markdown):

      VizBriz Level-4 — OSA Data Assessment Report

  (Note: Capital B in "VizBriz", NO # symbol, NO markdown)
  
  This title must appear ONLY ONCE at the top of the first page.
  
  ABSOLUTELY FORBIDDEN:
  ❌ "# OSA Data Assessment Report"
  ❌ "## OSA Data Assessment Report"
  ❌ Any title with # symbol
  
  REQUIRED:
  ✅ "VizBriz Level-4 — OSA Data Assessment Report" (plain text only)

• No duplicate titles.

• Maintain tight spacing and short sections.

• All metrics must appear only if present in the patient JSON.

• Never invent values.

• Never hallucinate anatomy or sleep metrics.

• Never output "Not provided", "—", "N/A".

  If a field is missing, simply OMIT it.

------------------------------------------------------------
SECTION ORDER (STRICT)
------------------------------------------------------------

The report must contain these sections in EXACT order:

1. Personal Details (two-column table with Field and Value columns)
2. Clinical Background, Complaints & Goals
3. ENT / Sinus Findings
4. Sleep Study Data (condensed table — 2 or 4 column rows)
5. Observations (5–6 bullets max)
6. Structural Observations from Imaging Data
      • Format: Two-column table with "Key Observations" and "Details" columns
      • Table must include: Obstruction Sites, Bite & Jaw, Soft Palate & Uvula, Tongue Position, Hyoid Bone, Nasal & Sinus
      • Then: 1–2 sentence synthesis paragraph (from conclusion field in JSON)
7. Conclusion (short, 2–3 sentences)
8. Possible Treatment Considerations (plain lines, NO bullets - each consideration on its own line without bullet points)
9. Device Design Data Considerations (MANDATORY two-column table with all 9 rows: Mandibular Advancement, Vertical Opening, Anterior Window, Retention Features, Material, Pre-set, Anterior Acrylic, Coverage, Clinical Notes)
10. Recommendations for Further Evaluation (MANDATORY - must include as bullet list, even if JSON array is empty or null)
    - Extract from JSON recommendations array
    - Format as bullet points (•)
    - If JSON is empty/null, check original text for this section and preserve it
    - This section MUST appear in the output
11. Oral Appliance Options for Consideration (MANDATORY two-column table with Device and Key Features columns - must always be included, even if JSON is empty)
12. Final Disclaimer

NO additional sections are allowed.

CRITICAL: You MUST include ALL sections listed above. If a section appears in the original text/JSON but is not in your output, you have FAILED the task.

------------------------------------------------------------
TABLE FORMAT RULES (STRICT)
------------------------------------------------------------

• Personal Details MUST be formatted as a plain text two-column table with header (NO markdown):

Personal Details

Field          Value
Gender         [value from JSON or omit if null]
Age            [value from JSON or omit if null]
BMI            [value from JSON or omit if null]
Weight         [value from JSON or omit if null]
Height         [value from JSON or omit if null]

DO NOT use markdown format like:
❌ ## Personal Details
❌ | Field | Value |
❌ |-------|-------|

Use plain text with spaces for alignment only.

Extract values from the JSON personal_details object:
- gender → "Gender"
- age → "Age"
- bmi → "BMI"
- weight → "Weight"
- height → "Height"

If a field is null in JSON, OMIT that row entirely (do not show "—" or "Not provided").

• Clinical Background, Complaints & Goals MUST be formatted as a plain text two-column table (NO markdown):

Clinical Background, Complaints & Goals

Field                      Value
Clinical background       [value from JSON or omit if null]
Patient complaints        [value from JSON or omit if null]
Patient goals             [value from JSON or omit if null]

DO NOT use markdown format like:
❌ | Clinical background: | value |
❌ | Patient complaints: | value |

Use plain text with spaces for alignment only.

Extract values from the JSON clinical_background object:
- background → "Clinical background"
- complaints → "Patient complaints"
- goals → "Patient goals"

If a field is null in JSON, OMIT that row entirely.

• Sleep Study Data MUST be presented in a multi-column compact layout (NO markdown):

Sleep Study Data

AHI: 33.0                REM AHI: 0.0
RDI: 34.7                REM RDI: —
ODI: 32.9                Snoring %: 30.54
Supine AHI: 35.1         O2 Nadir: 77%

DO NOT use markdown format like:
❌ | AHI | 33.0 | REM AHI | 0.0 |
❌ |-----|------|---------|-----|

Use plain text with spaces for alignment only.

Extract values from the JSON sleep_study_data object and format as shown above.

…but OMIT rows that don't exist — do NOT insert placeholders.

• Structural Observations MUST be formatted as a two-column table:

Key Observations          Details
Obstruction Sites        [value from JSON or omit if null]
Bite & Jaw               [value from JSON or omit if null]
Soft Palate & Uvula      [value from JSON or omit if null]
Tongue Position          [value from JSON or omit if null]
Hyoid Bone               [value from JSON or omit if null]
Nasal & Sinus            [value from JSON or omit if null]

Extract values from the JSON structural_observations object:
- primary_obstruction_site → "Obstruction Sites"
- bite_jaw → "Bite & Jaw"
- soft_palate_uvula → "Soft Palate & Uvula"
- tongue_base → "Tongue Position"
- hyoid → "Hyoid Bone"
- nasal_sinus → "Nasal & Sinus"

If a field is null in JSON, OMIT that row entirely (do not show "—" or "Not provided").

• Device Design Data Considerations MUST be formatted as a mandatory plain text two-column table with ALL 9 rows (NO markdown):

Device Design Data Considerations

Parameter                      Data-Based Consideration
Mandibular Advancement        [value from JSON or "Not provided" if null]
Vertical Opening              [value from JSON or "Not provided" if null]
Anterior Window               [value from JSON or "Not provided" if null]
Retention Features            [value from JSON or "Not provided" if null]
Material                      [value from JSON or "Not provided" if null]
Pre-set                       [value from JSON or "Not provided" if null]
Anterior Acrylic             [value from JSON or "Not provided" if null]
Coverage                      [value from JSON or "Not provided" if null]
Clinical Notes                [value from JSON or "Not provided" if null]

DO NOT use markdown format or narrative text. Use plain text table with spaces for alignment only.

Extract values from the JSON device_design object:
- mandibular_advancement → "Mandibular Advancement"
- vertical_opening → "Vertical Opening"
- anterior_window → "Anterior Window"
- retention_features → "Retention Features"
- material → "Material"
- pre_set → "Pre-set"
- anterior_acrylic → "Anterior Acrylic"
- coverage → "Coverage"
- clinical_notes → "Clinical Notes"

CRITICAL: This section is MANDATORY. ALL 9 rows must appear, even if values are null (use "Not provided" for null values). DO NOT output as a sentence or paragraph - it MUST be a table.

• Oral Appliance Options for Consideration MUST be formatted as a mandatory two-column table:

Device                      Key Features
Emerald Herbst             Strong, durable, high-density acrylic
Respire Herbst Pink AT     Metal mesh embedded, high-density acrylic
Daynaflex Herbst           Enhanced tongue space, stain-resistant PMMA

Extract devices from the JSON oral_appliance_options array:
- Each object has "device_type" and "key_features" fields
- Format as: {device_type}    {key_features}

CRITICAL: This section is MANDATORY and must always be included.
- If oral_appliance_options array is empty or null in JSON, generate at least 3 default devices:
  * Emerald Herbst: Strong, durable, high-density acrylic
  * Respire Herbst Pink AT: Metal mesh embedded, high-density acrylic
  * Daynaflex Herbst: Enhanced tongue space, stain-resistant PMMA
- This is a required Level-4 section and must always appear as a table, never as narrative text.

------------------------------------------------------------
CONTENT RULES
------------------------------------------------------------

• Use ONLY data from patient JSON + retrieved KB normalization.
• Do NOT add extra metrics.
• Observations must be clinically neutral and concise.
• No excessive descriptive text — keep bullets to 5–6 max.
• Conclusion must be factual, not interpretive.

------------------------------------------------------------
LENGTH & DENSITY RULES
------------------------------------------------------------

• The entire report must fit tightly into 1–2 PDF pages.
• Avoid long paragraphs.
• Avoid repeating data.
• Sleep Study Data must NOT take more than ~8 rows max.
• Structural Observations must be compact.

------------------------------------------------------------
FINAL DISCLAIMER
------------------------------------------------------------

Include the following exact text:

"FINAL DISCLAIMER: This AI-generated report assists in analyzing sleep and anatomical data. It does not replace physician evaluation, DISE assessment, or radiologic interpretation. All findings must be reviewed by a qualified healthcare provider before making clinical decisions."

------------------------------------------------------------
OUTPUT REQUIREMENTS
------------------------------------------------------------

CRITICAL: Your output MUST be plain text ONLY. NO MARKDOWN WHATSOEVER.

FORBIDDEN:
❌ NO markdown headings: ##, #, ###
❌ NO markdown tables: |, |---|, |---|
❌ NO markdown bold: **text**
❌ NO markdown formatting of any kind

REQUIRED:
✅ Plain text section headers (just the text, e.g., "Personal Details")
✅ Plain text tables with spaces for alignment (no pipes, no borders)
✅ Plain text formatting only
✅ Title must be exactly: "VizBriz Level-4 — OSA Data Assessment Report" (plain text, no #)

Example of CORRECT format:
VizBriz Level-4 — OSA Data Assessment Report

Personal Details

Field          Value
Gender         M
Age            42
BMI            28.7

Example of INCORRECT format (DO NOT USE):
# OSA Data Assessment Report
## Personal Details
| Field | Value |
|-------|-------|
| Gender | M |

Your output MUST be plain text formatted EXACTLY as the PDF generator expects.

Use ASCII table style with spaces for alignment (no pipes, no borders, no markdown).

Use plain text section headers (just the section name, no symbols).

No formatting artifacts.

No extra commentary.

No LLM thinking or meta text.

Preserve all content from the JSON data exactly as provided.

------------------------------------------------------------
VALIDATION CHECKLIST - VERIFY BEFORE OUTPUTTING
------------------------------------------------------------

Before you output your response, check EVERY line:

1. Title Check:
   ✅ Does your title say "VizBriz Level-4 — OSA Data Assessment Report" (plain text)?
   ❌ Does it say "# OSA Data Assessment Report"? → FIX IT

2. Section Headers Check:
   ✅ Do you have "Personal Details" (plain text)?
   ❌ Do you have "## Personal Details"? → FIX IT

3. Tables Check:
   ✅ Do you have "Field          Value" (spaces only)?
   ❌ Do you have "| Field | Value |"? → FIX IT

4. Structural Observations Check:
   ✅ Do you have a two-column table with "Key Observations" and "Details"?
   ❌ Do you have "**Bite/Jaw:** text"? → FIX IT

5. Device Design Check:
   ✅ Do you have a table with "Parameter" and "Data-Based Consideration" columns?
   ❌ Do you have a sentence like "Specific mandibular advancement measurements not available"? → FIX IT

6. Oral Appliance Options Check:
   ✅ Do you have a table with "Device" and "Key Features" columns?
   ❌ Do you have bullet points like "• Oral appliance therapy may be considered"? → FIX IT

            7. Final Check:
               ✅ Search your entire output for: #, ##, |, **
               ❌ If you find ANY of these, REMOVE THEM and reformat as plain text

            8. Section Completeness Check:
               ✅ Verify ALL 12 sections are present in your output:
                  1. Title block
                  2. DISCLAIMER
                  3. Personal Details
                  4. Clinical Background, Complaints & Goals
                  5. ENT / Sinus Findings
                  6. Sleep Study Data
                  7. Observations
                  8. Structural Observations from Imaging Data
                  9. Conclusion
                  10. Possible Treatment Considerations
                  11. Device Design Data Considerations
                  12. Recommendations for Further Evaluation ← CRITICAL: This section MUST appear
                  13. Oral Appliance Options for Consideration
                  14. FINAL DISCLAIMER
               ❌ If ANY section is missing, you have FAILED. Go back and add it.

            ONLY OUTPUT YOUR RESPONSE AFTER ALL CHECKS PASS.

            Generate the report now following these rules EXACTLY."""


def _level4_kb_build_retrieval_query(canonical_json: dict) -> str:
    """Build a retrieval query for Knowledge Base based on patient data"""
    parts = []
    
    # Extract key clinical data for retrieval
    ahi = canonical_json.get('sleep_study', {}).get('ahi') or canonical_json.get('ahi')
    if ahi:
        parts.append(f"AHI: {ahi}")
    
    # Positional data
    supine_ahi = canonical_json.get('sleep_study', {}).get('supine_ahi')
    if supine_ahi:
        parts.append(f"Positional: Supine AHI {supine_ahi}")
    
    # BMI
    bmi = canonical_json.get('bmi') or canonical_json.get('personal_details', {}).get('bmi')
    if bmi:
        parts.append(f"BMI: {bmi}")
    
    # Anatomy/obstruction sites
    obstruction_sites = canonical_json.get('imaging', {}).get('obstruction_sites') or canonical_json.get('obstruction_sites')
    soft_palate = canonical_json.get('imaging', {}).get('soft_palate') or canonical_json.get('soft_palate')
    tongue_base = canonical_json.get('imaging', {}).get('tongue_base') or canonical_json.get('tongue_base')
    bite = canonical_json.get('imaging', {}).get('bite') or canonical_json.get('bite')
    
    anatomy_parts = []
    if obstruction_sites:
        anatomy_parts.append(f"obstruction sites: {obstruction_sites}")
    if soft_palate:
        anatomy_parts.append(f"soft palate: {soft_palate}")
    if tongue_base:
        anatomy_parts.append(f"tongue base: {tongue_base}")
    if bite:
        anatomy_parts.append(f"bite: {bite}")
    
    if anatomy_parts:
        parts.append(f"Anatomy: {', '.join(anatomy_parts)}")
    
    # Sinus/Nasal findings
    sinus_findings = canonical_json.get('imaging', {}).get('sinus_findings') or canonical_json.get('sinus_findings')
    if sinus_findings:
        parts.append(f"Sinus/Nasal: {sinus_findings}")
    
    # Build query - STRICTLY request Level-4 reports only
    if parts:
        query = f"""Retrieve Level-4 OSA Data Assessment Reports (ONLY Level-4, not Level-5, Level-6, or other document types) with similar clinical characteristics:
- {chr(10).join('- ' + p for p in parts)}

Requirements:
- Must be Level-4 OSA Data Assessment Reports only
- Exclude Level-5, Level-6, Level-7 reports
- Exclude ENT letters, clinic summaries, follow-up notes, procedural notes
- Return 5–7 reports for style and formatting reference"""
    else:
        query = """Retrieve Level-4 OSA Data Assessment Reports (ONLY Level-4 format) for style and formatting examples.

Requirements:
- Must be Level-4 OSA Data Assessment Reports only
- Exclude Level-5, Level-6, Level-7 reports
- Exclude ENT letters, clinic summaries, follow-up notes, procedural notes
- Return 5–7 reports"""
    
    return query


def _level4_kb_build_user_prompt(canonical_json: dict, style_docs: str, clinic_docs: str) -> str:
    """Build user prompt with canonical JSON + fixed structure templates + KB clinical context."""
    patient_block = json.dumps(canonical_json, indent=2, ensure_ascii=False)

    structure_examples = _level4_structure_templates_block()

    user_message = f"""STRUCTURE EXEMPLARS (AUTHORITATIVE)

Use the following Level-4 templates as the fixed structure/layout reference.
Follow their section order, headings, and table patterns.
If KB snippets conflict with these templates about structure/layout, the templates win.

{structure_examples}


CLINICAL KNOWLEDGE BASE CONTEXT (OPTIONAL)

Use this only for clinical phrasing/considerations; do not invent patient facts.

{clinic_docs or "None"}


CANONICAL JSON DATA (ONLY SOURCE OF PATIENT FACTS)

{patient_block}

Generate the Level-4 OSA Data Assessment Report following the system instructions exactly."""
    
    return user_message


@reports_files_bp.route('/reports/level4-kb-lab', methods=['GET'])
@login_required
def reports_level4_kb_lab():
    """Render the Level-4 Report Lab with Knowledge Base UI"""
    return render_template('level4_kb_report_lab.html')


@reports_files_bp.route('/reports/api/level4_kb_report/generate', methods=['POST'])
@login_required
def reports_level4_kb_generate():
    """Generate Level-4 report using Knowledge Base for examples (always uses Bedrock)"""
    try:
        data = request.get_json() or {}
        patient_id = data.get('patient_id')
        provider = 'bedrock'  # KB version always uses Bedrock
        custom_system_prompt = data.get('custom_system_prompt')
        custom_user_prompt = data.get('custom_user_prompt')

        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400

        patient = Patient.query.get(patient_id)
        if not patient or not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        try:
            canonical_json = _level4_load_canonical(patient_id)
        except Exception as exc:
            logger.error(f"Failed to load canonical JSON: {exc}", exc_info=True)
            return jsonify({'success': False, 'error': f'Failed to load patient data: {str(exc)}'}), 404

        # Retrieve from two separate Knowledge Bases
        bedrock_service = get_bedrock_service()
        
        style_docs = ""
        clinic_docs = ""
        kb_error = None
        clinic_error = None  # Separate error for clinic KB (optional)
        style_query = "Level 4 OSA report structure"
        clinic_query = f"OSA clinical patterns for patient {patient_id}"
        
        if bedrock_service and bedrock_service.is_available():
            try:
                # ALWAYS retrieve from KB_Level4_Style (top_k = 6-8)
                logger.info(f"🔍 KB Style Query: {style_query}")
                style_result = bedrock_service.query_knowledge_base(
                    query=style_query,
                    patient_id=None,
                    max_results=7,  # 6-8 range, using 7
                    knowledge_base_id=bedrock_service.KB_LEVEL4_STYLE_ID
                )
                
                if style_result.get('success'):
                    style_texts = style_result.get('retrieved_texts', [])
                    if style_texts:
                        # Clean up texts (remove metadata blocks if present)
                        cleaned_style = []
                        for text in style_texts:
                            if not text or len(text.strip()) < 100:
                                continue
                            # Remove metadata block if present
                            if '<!--METADATA_END-->' in text:
                                content = text.split('<!--METADATA_END-->', 1)[1].strip()
                                if content:
                                    cleaned_style.append(content)
                            else:
                                cleaned_style.append(text)
                        if cleaned_style:
                            style_docs = '\n\n---STYLE_EXAMPLE---\n\n'.join(cleaned_style)
                            logger.info(f"🔍 Retrieved {len(cleaned_style)} style documents from KB_Level4_Style")
                    else:
                        logger.warning("KB_Level4_Style returned no documents")
                else:
                    logger.warning(f"KB_Level4_Style retrieval failed: {style_result.get('error')}")
                
                # OPTIONALLY retrieve from KB_Level4_Clinic (top_k = 1-3)
                # Wrap in try-catch to prevent clinic KB errors from breaking the whole request
                try:
                    logger.info(f"🔍 KB Clinic Query: {clinic_query}")
                    logger.info(f"🔍 KB Clinic KB ID: {bedrock_service.KB_LEVEL4_CLINIC_ID}")
                    clinic_result = bedrock_service.query_knowledge_base(
                        query=clinic_query,
                        patient_id=None,
                        max_results=2,  # 1-3 range, using 2
                        knowledge_base_id=bedrock_service.KB_LEVEL4_CLINIC_ID
                    )
                except Exception as clinic_kb_exc:
                    # Clinic KB is optional - log error but continue
                    clinic_error = f"KB_Level4_Clinic retrieval exception: {str(clinic_kb_exc)}"
                    logger.warning(f"🔍 ⚠️ KB_Level4_Clinic retrieval exception (continuing without clinic KB): {clinic_kb_exc}")
                    clinic_result = {'success': False, 'error': str(clinic_kb_exc)}
                
                if clinic_result.get('success'):
                    clinic_texts = clinic_result.get('retrieved_texts', [])
                    logger.info(f"🔍 KB_Level4_Clinic returned {len(clinic_texts)} raw results")
                    if clinic_texts:
                        # Clean up texts (remove metadata blocks if present)
                        cleaned_clinic = []
                        for text in clinic_texts:
                            if not text or len(text.strip()) < 100:
                                logger.warning(f"🔍 Skipping clinic text: length={len(text.strip()) if text else 0}")
                                continue
                            # Remove metadata block if present
                            if '<!--METADATA_END-->' in text:
                                content = text.split('<!--METADATA_END-->', 1)[1].strip()
                                if content:
                                    cleaned_clinic.append(content)
                                else:
                                    logger.warning("🔍 Clinic text had metadata but no content after removal")
                            else:
                                cleaned_clinic.append(text)
                        if cleaned_clinic:
                            clinic_docs = '\n\n---CLINICAL_EXAMPLE---\n\n'.join(cleaned_clinic)
                            logger.info(f"🔍 ✅ Retrieved {len(cleaned_clinic)} clinical documents from KB_Level4_Clinic (total {len(clinic_docs)} chars)")
                        else:
                            logger.warning("🔍 ⚠️ KB_Level4_Clinic returned documents but all were filtered out (too short or empty after cleanup)")
                    else:
                        logger.warning(f"🔍 ⚠️ KB_Level4_Clinic returned no documents. Query: '{clinic_query}' may not match any documents in the KB.")
                else:
                    error_msg = clinic_result.get('error', 'Unknown error')
                    clinic_error = f"KB_Level4_Clinic retrieval failed: {error_msg}"
                    logger.warning(f"🔍 ⚠️ KB_Level4_Clinic retrieval failed: {error_msg}")
                    logger.warning(f"🔍   Query was: '{clinic_query}'")
                    logger.warning(f"🔍   KB ID was: {bedrock_service.KB_LEVEL4_CLINIC_ID}")
                    
                    # Try a more generic query as fallback
                    logger.info(f"🔍 Trying fallback generic query for clinic KB...")
                    fallback_query = "OSA clinical patterns treatment considerations"
                    fallback_result = bedrock_service.query_knowledge_base(
                        query=fallback_query,
                        patient_id=None,
                        max_results=2,
                        knowledge_base_id=bedrock_service.KB_LEVEL4_CLINIC_ID
                    )
                    if fallback_result.get('success'):
                        fallback_texts = fallback_result.get('retrieved_texts', [])
                        if fallback_texts:
                            cleaned_fallback = []
                            for text in fallback_texts:
                                if not text or len(text.strip()) < 100:
                                    continue
                                if '<!--METADATA_END-->' in text:
                                    content = text.split('<!--METADATA_END-->', 1)[1].strip()
                                    if content:
                                        cleaned_fallback.append(content)
                                else:
                                    cleaned_fallback.append(text)
                            if cleaned_fallback:
                                clinic_docs = '\n\n---CLINICAL_EXAMPLE---\n\n'.join(cleaned_fallback)
                                logger.info(f"🔍 ✅ Fallback query retrieved {len(cleaned_fallback)} clinical documents")
                                clinic_error = None  # Clear error since fallback worked
                
                # Check for errors - only error if style KB failed (required)
                if not style_docs:
                    kb_error = "KB_Level4_Style returned no valid documents"
                    logger.warning("Required KB_Level4_Style retrieval failed or returned no documents")
                    
            except Exception as exc:
                kb_error = str(exc)
                logger.error(f"Knowledge Base retrieval error: {exc}", exc_info=True)
        else:
            kb_error = "Bedrock service unavailable"
            logger.error("Bedrock service not available for KB retrieval")
        
        # Use custom prompts if provided, otherwise use defaults
        system_prompt = custom_system_prompt if custom_system_prompt else _LEVEL4_KB_SYSTEM_PROMPT
        user_prompt = custom_user_prompt if custom_user_prompt else _level4_kb_build_user_prompt(canonical_json, style_docs, clinic_docs)
        
        # Invoke LLM
        try:
            llm_result = _level4_invoke_provider_with_prompts(provider, system_prompt, user_prompt, patient_id)
            if 'error' in llm_result:
                logger.error(f"LLM invocation failed: {llm_result['error']}")
                return jsonify({'success': False, 'error': llm_result['error']}), 500
        except Exception as exc:
            logger.error(f"LLM invocation exception: {exc}", exc_info=True)
            return jsonify({'success': False, 'error': f'LLM invocation failed: {str(exc)}'}), 500

        # Save to history (using same table)
        history_entry = None
        try:
            history_entry = Level4ReportHistory(
                patient_id=patient_id,
                prompt=user_prompt,
                response=llm_result.get('response', ''),
                llm_provider=provider,
                model_used=llm_result.get('model'),
                created_by=current_user.id
            )
            db.session.add(history_entry)
            db.session.commit()
        except Exception as exc:
            current_app.logger.error('Failed to save Level 4 KB report history: %s', exc)

        # Build query info for display
        retrieval_query_style = style_query
        retrieval_query_clinic = clinic_query
        
        return jsonify({
            'success': True,
            'system_prompt': system_prompt,
            'user_prompt': user_prompt,
            'retrieval_query': retrieval_query_style,  # Keep for backward compatibility
            'retrieval_query_a': retrieval_query_style,  # Style query
            'retrieval_query_b': retrieval_query_clinic,  # Clinic query
            'structural_templates': style_docs,  # Keep for backward compatibility
            'style_references': clinic_docs,  # Keep for backward compatibility
            'style_docs': style_docs,
            'clinic_docs': clinic_docs,
            'structural_templates_length': len(style_docs) if style_docs else 0,
            'style_references_length': len(clinic_docs) if clinic_docs else 0,
            'kb_error': kb_error,
            'clinic_error': clinic_error,  # Separate error for clinic KB (optional)
            'kb_success': kb_error is None and bool(style_docs),  # Style KB is required
            'response': llm_result.get('response'),
            'model_used': llm_result.get('model'),
            'history_id': history_entry.id if history_entry else None,
        })
    except Exception as exc:
        logger.error(f"Unhandled exception in reports_level4_kb_generate: {exc}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Internal server error: {str(exc)}'
        }), 500


@reports_files_bp.route('/reports/api/level4_kb_report/save', methods=['POST'])
@login_required
def reports_level4_kb_save():
    """Save edited Level-4 report, generate PDF, and upload to adminfiles"""
    try:
        data = request.get_json() or {}
        patient_id = data.get('patient_id')
        report_content = data.get('report_content', '').strip()
        history_id = data.get('history_id')
        original_response = data.get('original_response', '')
        
        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400
        
        if not report_content:
            return jsonify({'success': False, 'error': 'report_content is required'}), 400
        
        patient = Patient.query.get(patient_id)
        if not patient or not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Optional: Format the report using LLM before generating PDF
        # This step converts the analytical report into PDF-ready format
        format_for_pdf = data.get('format_for_pdf', True)  # Default to True
        if format_for_pdf:
            try:
                logger.info(f"Formatting Level-4 report for PDF (patient {patient_id})")
                # Use Bedrock for formatting (consistent with KB-based generation)
                patient_name = patient.name if patient else f"Patient {patient_id}"
                from datetime import datetime
                current_date = datetime.now().strftime('%B %d, %Y')
                
                # Check if report_content is JSON or text
                import json
                try:
                    # Try to parse as JSON
                    report_json = json.loads(report_content)
                    is_json = True
                except (json.JSONDecodeError, TypeError):
                    # Not JSON, treat as text
                    is_json = False
                    report_json = None
                
                if is_json:
                    # Format JSON data into PDF-ready text
                    formatting_user_prompt = f"""Here is the structured JSON data for a Level-4 OSA report. Convert it into PDF-ready plain text format following the template rules.

PATIENT INFORMATION:
Patient Name: {patient_name}
Patient ID: {patient_id}
Date: {current_date}

JSON DATA TO FORMAT:

{json.dumps(report_json, indent=2)}

TASK:
Extract values from this JSON and format them into a clean, PDF-ready plain text report following the exact structure, tone, and formatting conventions of professional VizBriz Level-4 OSA reports.

CRITICAL REQUIREMENTS:
- Use exact title: "VizBriz Level-4 — OSA Data Assessment Report" (appears ONCE only, capital B in "VizBriz", plain text - NO markdown #)
- Include patient line: "Patient: {patient_name} (ID: {patient_id})"
- Include date: "Date: {current_date}"
- Extract all values from the JSON structure
- Format all tables as plain text (NO markdown |, NO borders)
- OMIT null fields entirely (do NOT use "—", "Not provided", or "N/A")
- EXCEPTION: Device Design section - show all 9 rows, use "Not provided" for null values
- EXCEPTION: Oral Appliance Options - if array is empty, use default 3 devices
- Keep report compact (1-2 pages total)
- Observations: format as bullet points (5-6 max)
- Structural Observations: two-column table format
- Include Conclusion section from structural_observations.conclusion
- Sleep Study Data: compact multi-column format (omit null rows)
- All tables must be plain text with spaces for alignment (NO markdown)
- Preserve all content from JSON exactly as provided"""
                else:
                    # Legacy: format text report (for backward compatibility)
                    formatting_user_prompt = f"""Here is the Level-4 analysis text, now convert it into the PDF format exactly following the template rules.

PATIENT INFORMATION:
Patient Name: {patient_name}
Patient ID: {patient_id}
Date: {current_date}

ANALYTICAL REPORT TO FORMAT:

{report_content}

TASK:
Convert this analytical report into a clean, PDF-ready format following the exact structure, tone, and formatting conventions of professional VizBriz Level-4 OSA reports (Case_EpHa_1983, Case_MaRu_1990, Case_NaFa_1982, Case_SaSi_1980).

CRITICAL REQUIREMENTS:
- Use exact title: "VizBriz Level-4 — OSA Data Assessment Report" (appears ONCE only, capital B in "VizBriz", plain text - NO markdown #)
- Include patient line: "Patient: {patient_name} (ID: {patient_id})"
- Include date: "Date: {current_date}"
- Do not change any medical content, interpretations, metrics, or conclusions
- Only reformat the structure and style
- Format all tables as plain text (NO markdown |, NO borders)
- OMIT missing fields entirely (do NOT use "—", "Not provided", or "N/A")
- Keep report compact (1-2 pages total)
- Observations limited to 5-6 bullets max
- Structural Observations: compressed table format + 1-2 sentence synthesis paragraph
- Include Conclusion section (2-3 sentences, factual not interpretive)
- Sleep Study Data: compact multi-column format (omit missing rows)
- Use compact table format: "Label: value" (no markdown, no ASCII borders)
- Keep all sections short and tight
- Preserve all content from analytical report exactly as provided"""
                
                formatting_result = _level4_invoke_provider_with_prompts(
                    'bedrock',
                    _LEVEL4_PDF_FORMATTING_PROMPT,
                    formatting_user_prompt,
                    patient_id
                )
                
                if 'error' not in formatting_result and formatting_result.get('response'):
                    formatted_content = formatting_result.get('response', '').strip()
                    if formatted_content:
                        # Post-process to remove any markdown that the model might have added
                        import re
                        # Remove markdown headings
                        formatted_content = re.sub(r'^#+\s+', '', formatted_content, flags=re.MULTILINE)
                        # Remove markdown table pipes and borders
                        formatted_content = re.sub(r'^\|', '', formatted_content, flags=re.MULTILINE)
                        formatted_content = re.sub(r'\|$', '', formatted_content, flags=re.MULTILINE)
                        formatted_content = re.sub(r'^\|\s*[-:]+\s*\|', '', formatted_content, flags=re.MULTILINE)
                        # Remove markdown bold
                        formatted_content = re.sub(r'\*\*([^*]+)\*\*', r'\1', formatted_content)
                        formatted_content = re.sub(r'__([^_]+)__', r'\1', formatted_content)
                        # Fix title if it's wrong
                        formatted_content = re.sub(r'^#+\s*OSA Data Assessment Report', 'VizBriz Level-4 — OSA Data Assessment Report', formatted_content, flags=re.MULTILINE)
                        formatted_content = re.sub(r'^OSA Data Assessment Report$', 'VizBriz Level-4 — OSA Data Assessment Report', formatted_content, flags=re.MULTILINE)
                        
                        logger.info(f"Report formatted successfully (patient {patient_id}), length: {len(formatted_content)} chars")
                        report_content = formatted_content
                    else:
                        logger.warning(f"Formatting returned empty response, using original content")
                else:
                    error_msg = formatting_result.get('error', 'Unknown formatting error')
                    logger.warning(f"Report formatting failed: {error_msg}, using original content")
            except Exception as exc:
                logger.error(f"Exception during report formatting: {exc}", exc_info=True)
                # Continue with original content if formatting fails
        
        # Import required libraries for PDF generation
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, KeepTogether
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
        
        # Update or create history entry
        if history_id:
            history_entry = Level4ReportHistory.query.get(history_id)
            if history_entry and history_entry.patient_id == patient_id:
                history_entry.response = report_content
                db.session.commit()
                logger.info(f"Updated Level-4 report history entry {history_id} for patient {patient_id}")
            else:
                history_entry = None
        else:
            history_entry = None
        
        # If no history entry exists, create a new one
        if not history_entry:
            history_entry = Level4ReportHistory(
                patient_id=patient_id,
                prompt='',  # Empty since this is an edited version
                response=report_content,
                llm_provider='manual_edit',
                model_used='manual_edit',
                created_by=current_user.id
            )
            db.session.add(history_entry)
            db.session.flush()  # Get the ID
            logger.info(f"Created new Level-4 report history entry {history_entry.id} for patient {patient_id}")
        
        # Generate PDF from report content
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4,
                               rightMargin=72, leftMargin=72,
                               topMargin=72, bottomMargin=72)
        
        # Container for the 'Flowable' objects
        elements = []
        
        # Define professional styles
        styles = getSampleStyleSheet()
        
        # Title style - for main report title
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=20,
            textColor=colors.HexColor('#1a1a1a'),
            spaceAfter=16,
            spaceBefore=0,
            alignment=1,  # Center align
            fontName='Helvetica-Bold',
            leading=24
        )
        
        # Section heading style - for ## headings (styled with blue color)
        section_style = ParagraphStyle(
            'CustomSection',
            parent=styles['Heading2'],
            fontSize=14,
            textColor=colors.HexColor('#1d4ed8'),  # Blue color
            spaceAfter=10,
            spaceBefore=16,
            alignment=0,  # Left align
            fontName='Helvetica-Bold',
            leading=18,
            borderWidth=0,
            borderPadding=4,
            borderColor=colors.HexColor('#2563eb'),
            leftIndent=0,
            backColor=colors.HexColor('#eff6ff')  # Light blue background
        )
        
        # Subsection style - for ### headings
        subsection_style = ParagraphStyle(
            'CustomSubsection',
            parent=styles['Heading3'],
            fontSize=12,
            textColor=colors.HexColor('#1a1a1a'),
            spaceAfter=8,
            spaceBefore=12,
            alignment=0,
            fontName='Helvetica-Bold',
            leading=16
        )
        
        # Normal paragraph style - darker and bolder
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#000000'),  # Pure black for better visibility
            spaceAfter=8,
            leading=14,
            fontName='Helvetica-Bold',  # Use bold font for better visibility
            alignment=0
        )
        
        # Bold text style
        bold_style = ParagraphStyle(
            'CustomBold',
            parent=normal_style,
            fontName='Helvetica-Bold',
            fontSize=10,
            textColor=colors.HexColor('#000000')
        )
        
        # Special style for final disclaimer - italic, smaller font, different color
        disclaimer_style = ParagraphStyle(
            'CustomDisclaimer',
            parent=normal_style,
            fontName='Helvetica-Oblique',  # Italic
            fontSize=9,  # Smaller font
            textColor=colors.HexColor('#666666'),  # Gray color
            spaceBefore=12,
            spaceAfter=12,
            alignment=TA_LEFT
        )
        
        # Table header style - darker and bolder
        table_header_style = ParagraphStyle(
            'TableHeader',
            parent=normal_style,
            fontName='Helvetica-Bold',
            fontSize=11,  # Slightly larger
            textColor=colors.HexColor('#000000'),  # Pure black
            backColor=colors.HexColor('#e5e7eb'),  # Darker background
            alignment=0
        )
        
        # Table cell style - darker text
        table_cell_style = ParagraphStyle(
            'TableCell',
            parent=normal_style,
            fontSize=10,
            textColor=colors.HexColor('#000000'),  # Pure black
            fontName='Helvetica-Bold',  # Bold for visibility
            alignment=0
        )
        
        # Add VizBriz logo at the top
        from datetime import datetime
        current_date = datetime.now().strftime('%B %d, %Y')
        
        try:
            from reportlab.platypus import Image as RLImage
            # Use VizBriz logo - prefer the color version without gradient
            logo_paths = [
                '/home/ec2-user/vizbriz/flask_app/flask_static/images/logos/vizbrizz_logo color without grad.png',  # Color logo without gradient (preferred)
                '/home/ec2-user/vizbriz/flask_app/flask_static/images/logos/vizbriz_logo.png',
                '/home/ec2-user/vizbriz/flask_app/flask_static/branding/vizbrizz_logo color white long.png',
                os.path.join(os.path.dirname(os.path.dirname(__file__)), 'flask_static', 'images', 'logos', 'vizbriz_logo.png'),
            ]
            logo_added = False
            for logo_path in logo_paths:
                if os.path.exists(logo_path) and os.path.getsize(logo_path) > 1000:  # Make sure file has real content
                    try:
                        logo = RLImage(logo_path)
                        # Scale logo to reasonable size (max 2.5 inches wide for visibility)
                        max_width = 2.5 * inch
                        if logo.drawWidth > max_width:
                            scale = max_width / logo.drawWidth
                            logo.drawWidth = max_width
                            logo.drawHeight = logo.drawHeight * scale
                        # Center the logo
                        logo.hAlign = 'CENTER'
                        elements.append(logo)
                        elements.append(Spacer(1, 12))
                        logo_added = True
                        logger.info(f"Added VizBriz logo to PDF from: {logo_path}")
                        break
                    except Exception as img_err:
                        logger.warning(f"Failed to load logo from {logo_path}: {img_err}")
                        continue
            if not logo_added:
                logger.warning("No valid VizBriz logo file found for PDF")
        except Exception as logo_exc:
            logger.warning(f"Could not add logo to PDF: {logo_exc}")
        
        # Add report title with VizBriz branding
        title_text = "VizBriz Level-4 — OSA Data Assessment Report"
        elements.append(Paragraph(title_text, title_style))
        elements.append(Spacer(1, 6))
        
        # Add horizontal line under title
        from reportlab.platypus import HRFlowable
        elements.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#2563eb'), spaceAfter=12))
        
        # Add patient info header (subtitle)
        patient_info_style = ParagraphStyle(
            'PatientInfo',
            parent=normal_style,
            fontSize=11,
            textColor=colors.HexColor('#333333'),
            spaceAfter=4,
            alignment=1,  # Center align
            fontName='Helvetica'
        )
        patient_info = f"Patient: {patient.name} (ID: {patient.id})"
        elements.append(Paragraph(patient_info, patient_info_style))
        elements.append(Spacer(1, 4))
        
        # Add date
        date_text = f"Date: {current_date}"
        elements.append(Paragraph(date_text, patient_info_style))
        elements.append(Spacer(1, 16))
        
        # Comprehensive formatting function to convert markdown to plain text before PDF generation
        import re
        def format_report_for_pdf(text):
            """Convert markdown-formatted report to plain text format for PDF generation"""
            if not text:
                return text
            
            # Step 0: Clean up LaTeX formatting (LLM sometimes outputs LaTeX)
            # Remove $...$ wrappers
            text = re.sub(r'\$([^$]+)\$', r'\1', text)
            # Clean up \mathrm{} - extract content
            text = re.sub(r'\\mathrm\{([^}]*)\}', r'\1', text)
            # Clean up \text{} - extract content
            text = re.sub(r'\\text\{([^}]*)\}', r'\1', text)
            # Clean up ~ (non-breaking space in LaTeX) -> regular space
            text = re.sub(r'~', ' ', text)
            # Clean up common LaTeX commands
            text = re.sub(r'\\%', '%', text)  # \% -> %
            text = re.sub(r'\\&', '&', text)  # \& -> &
            text = re.sub(r'\\#', '#', text)  # \# -> #
            # Remove remaining backslash commands (e.g., \kg, \m, etc.)
            text = re.sub(r'\\([a-zA-Z]+)', r'\1', text)
            # Clean up multiple spaces
            text = re.sub(r'  +', ' ', text)
            
            # Step 1: Fix title - replace any markdown title with correct plain text title
            text = re.sub(r'^#+\s*OSA Data Assessment Report.*$', 'VizBriz Level-4 — OSA Data Assessment Report', text, flags=re.MULTILINE)
            text = re.sub(r'^OSA Data Assessment Report$', 'VizBriz Level-4 — OSA Data Assessment Report', text, flags=re.MULTILINE)
            
            # Step 2: Remove all markdown headings (##, ###, etc.) - convert to plain text
            text = re.sub(r'^#+\s+(.+)$', r'\1', text, flags=re.MULTILINE)
            
            # Step 3: Convert markdown tables to plain text tables
            lines = text.split('\n')
            formatted_lines = []
            in_table = False
            table_rows = []
            
            for line in lines:
                stripped = line.strip()
                
                # Detect markdown table rows
                if '|' in stripped and not stripped.startswith('|--'):
                    # This is a markdown table row
                    if not in_table:
                        in_table = True
                        table_rows = []
                    
                    # Extract cells (remove leading/trailing |)
                    cells = [cell.strip() for cell in stripped.split('|') if cell.strip()]
                    if len(cells) >= 2:
                        table_rows.append(cells)
                elif stripped.startswith('|--'):
                    # Skip markdown table separator
                    continue
                else:
                    # Not a table row
                    if in_table and table_rows:
                        # Process accumulated table rows
                        formatted_lines.extend(format_table_rows(table_rows))
                        table_rows = []
                        in_table = False
                    
                    # Process non-table line
                    formatted_lines.append(line)
            
            # Handle table at end of file
            if in_table and table_rows:
                formatted_lines.extend(format_table_rows(table_rows))
            
            text = '\n'.join(formatted_lines)
            
            # Step 4: Remove markdown bold (**text** or __text__)
            text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
            text = re.sub(r'__([^_]+)__', r'\1', text)
            
            # Step 5: Fix specific sections that should be tables
            text = fix_personal_details_table(text)
            text = fix_clinical_background_table(text)
            text = fix_sleep_study_table(text)
            text = fix_structural_observations_table(text)
            text = fix_device_design_table(text)
            text = fix_oral_appliance_table(text)
            
            return text
        
        def format_table_rows(rows):
            """Convert table rows to plain text format"""
            if not rows:
                return []
            
            formatted = []
            # Determine column widths
            num_cols = max(len(row) for row in rows) if rows else 0
            if num_cols < 2:
                # Not a proper table, return as-is
                for row in rows:
                    formatted.append(' '.join(row))
                return formatted
            
            # Calculate column widths
            col_widths = [0] * num_cols
            for row in rows:
                for i, cell in enumerate(row):
                    if i < num_cols:
                        col_widths[i] = max(col_widths[i], len(cell))
            
            # Format rows
            for row in rows:
                formatted_row = []
                for i in range(num_cols):
                    cell = row[i] if i < len(row) else ''
                    formatted_row.append(cell.ljust(col_widths[i] + 2))
                formatted.append(''.join(formatted_row).rstrip())
            
            return formatted
        
        def fix_personal_details_table(text):
            """Fix Personal Details section to be a proper two-column table"""
            pattern = r'Personal Details.*?(?=\n\n|\n##|\n[A-Z][a-z]+ [A-Z]|$)'
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                section = match.group(0)
                # Look for markdown table or key-value pairs
                if '|' in section or 'Gender:' in section or 'Age:' in section:
                    # Extract values
                    gender = re.search(r'Gender[:\s|]*([MF]|Male|Female)', section, re.IGNORECASE)
                    age = re.search(r'Age[:\s|]*(\d+)', section, re.IGNORECASE)
                    bmi = re.search(r'BMI[:\s|]*([\d.]+)', section, re.IGNORECASE)
                    weight = re.search(r'Weight[:\s|]*([\d.]+[^\s]*)', section, re.IGNORECASE)
                    height = re.search(r'Height[:\s|]*([\d.]+[^\s]*)', section, re.IGNORECASE)
                    
                    new_section = "Personal Details\n\nField          Value\n"
                    if gender:
                        new_section += f"Gender         {gender.group(1)}\n"
                    if age:
                        new_section += f"Age            {age.group(1)}\n"
                    if bmi:
                        new_section += f"BMI            {bmi.group(1)}\n"
                    if weight:
                        new_section += f"Weight         {weight.group(1)}\n"
                    if height:
                        new_section += f"Height         {height.group(1)}\n"
                    
                    text = text.replace(match.group(0), new_section)
            return text
        
        def fix_clinical_background_table(text):
            """Fix Clinical Background section to be a proper two-column table"""
            pattern = r'Clinical Background[^#]*?(?=\n\n|\n##|\n[A-Z][a-z]+ [A-Z]|$)'
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                section = match.group(0)
                if '|' in section:
                    # Extract values from markdown table
                    background = re.search(r'Clinical background[:\s|]*([^|]+)', section, re.IGNORECASE)
                    complaints = re.search(r'Patient complaints[:\s|]*([^|]+)', section, re.IGNORECASE)
                    goals = re.search(r'Patient goals[:\s|]*([^|]+)', section, re.IGNORECASE)
                    
                    new_section = "Clinical Background, Complaints & Goals\n\nField                      Value\n"
                    if background:
                        new_section += f"Clinical background       {background.group(1).strip()}\n"
                    if complaints:
                        new_section += f"Patient complaints        {complaints.group(1).strip()}\n"
                    if goals:
                        new_section += f"Patient goals             {goals.group(1).strip()}\n"
                    
                    text = text.replace(match.group(0), new_section)
            return text
        
        def fix_sleep_study_table(text):
            """Fix Sleep Study Data section to be compact multi-column format"""
            pattern = r'Sleep Study Data.*?(?=\n\n|\n##|\n[A-Z][a-z]+ [A-Z]|$)'
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                section = match.group(0)
                # Extract key metrics
                metrics = {}
                for metric in ['AHI', 'RDI', 'ODI', 'REM AHI', 'REM RDI', 'REM ODI', 'Supine AHI', 'Supine RDI', 'Supine ODI', 'Non-Supine AHI', 'Snoring %', 'O2 Nadir', 'Sleep Efficiency', 'Total Sleep Time']:
                    pattern_metric = rf'{re.escape(metric)}[:\s|]*([\d.]+|Not provided|—)'
                    m = re.search(pattern_metric, section, re.IGNORECASE)
                    if m:
                        metrics[metric] = m.group(1)
                
                if metrics:
                    new_section = "Sleep Study Data\n\n"
                    # Format as two columns
                    items = list(metrics.items())
                    for i in range(0, len(items), 2):
                        if i < len(items):
                            left = f"{items[i][0]}: {items[i][1]}"
                            right = f"{items[i+1][0]}: {items[i+1][1]}" if i+1 < len(items) else ""
                            new_section += f"{left:<25} {right}\n".rstrip() + "\n"
                    
                    text = text.replace(match.group(0), new_section)
            return text
        
        def fix_structural_observations_table(text):
            """Fix Structural Observations to be a two-column table"""
            pattern = r'Structural Observations from Imaging Data.*?(?=\n\n|\n##|\nConclusion|$)'
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                section = match.group(0)
                # Extract observations
                observations = {}
                for key in ['Bite/Jaw', 'Bite & Jaw', 'Soft palate/uvula', 'Soft Palate & Uvula', 'Tongue base', 'Tongue Position', 'Hyoid', 'Hyoid Bone', 'Primary obstruction site', 'Obstruction Sites', 'Nasal/Sinus', 'Nasal & Sinus']:
                    # Look for **key:** or key: patterns
                    pattern_key = rf'(?:\*\*)?{re.escape(key)}[:\*\s]*([^\n]+)'
                    m = re.search(pattern_key, section, re.IGNORECASE)
                    if m:
                        clean_key = key.replace('/', ' & ').replace('palate/uvula', 'Palate & Uvula').replace('base', 'Position').replace('site', 'Sites')
                        if 'Bite' in clean_key:
                            clean_key = 'Bite & Jaw'
                        elif 'Soft' in clean_key:
                            clean_key = 'Soft Palate & Uvula'
                        elif 'Tongue' in clean_key:
                            clean_key = 'Tongue Position'
                        elif 'Hyoid' in clean_key:
                            clean_key = 'Hyoid Bone'
                        elif 'obstruction' in clean_key.lower():
                            clean_key = 'Obstruction Sites'
                        elif 'Nasal' in clean_key:
                            clean_key = 'Nasal & Sinus'
                        observations[clean_key] = m.group(1).strip()
                
                if observations:
                    new_section = "Structural Observations from Imaging Data\n\nKey Observations          Details\n"
                    for key, value in observations.items():
                        new_section += f"{key:<30} {value}\n"
                    
                    # Add conclusion if present
                    conclusion_match = re.search(r'Conclusion[:\s]*([^\n]+)', section, re.IGNORECASE)
                    if conclusion_match:
                        new_section += f"\n{conclusion_match.group(1).strip()}\n"
                    
                    text = text.replace(match.group(0), new_section)
            return text
        
        def fix_device_design_table(text):
            """Fix Device Design Data Considerations to be a mandatory 9-row table"""
            pattern = r'Device Design Data Considerations.*?(?=\n\n|\n##|\n[A-Z][a-z]+ [A-Z]|$)'
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                section = match.group(0)
                # Check if it's already a sentence (wrong format)
                if 'not available' in section.lower() or 'measurements' in section.lower():
                    # Replace with proper table
                    new_section = """Device Design Data Considerations

Parameter                      Data-Based Consideration
Mandibular Advancement        Not provided
Vertical Opening              Not provided
Anterior Window               Not provided
Retention Features            Not provided
Material                      Not provided
Pre-set                       Not provided
Anterior Acrylic             Not provided
Coverage                      Not provided
Clinical Notes                Not provided
"""
                    text = text.replace(match.group(0), new_section)
            return text
        
        def fix_oral_appliance_table(text):
            """Fix Oral Appliance Options to be a two-column table"""
            pattern = r'Oral Appliance Options for Consideration.*?(?=\n\n|\n##|\nFINAL|$)'
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                section = match.group(0)
                # Check if it's bullet points (wrong format)
                if '•' in section or '* ' in section:
                    # Replace with default table
                    new_section = """Oral Appliance Options for Consideration

Device                      Key Features
Emerald Herbst             Strong, durable, high-density acrylic
Respire Herbst Pink AT     Metal mesh embedded, high-density acrylic
Daynaflex Herbst           Enhanced tongue space, stain-resistant PMMA
"""
                    text = text.replace(match.group(0), new_section)
            return text
        
        # Apply comprehensive formatting before PDF generation
        report_content = format_report_for_pdf(report_content)
        
        # Comprehensive cleanup function for LaTeX, Hebrew, and garbage text
        def clean_text_for_pdf(text):
            """Remove LaTeX formatting, Hebrew characters, and garbage text"""
            if not text:
                return text
            
            # ===== STEP 1: LaTeX Cleanup =====
            # Remove $...$ wrappers (LaTeX math mode) - handle nested and multiline
            # Handle patterns like: $28.7 \mathrm{~kg} / \mathrm{m}^{2}$
            text = re.sub(r'\$([^$]+)\$', r'\1', text)
            
            # Clean up \mathrm{} - extract content (handle various spacing including ~)
            text = re.sub(r'\\mathrm\s*\{\s*~?\s*([^}]*)\s*\}', r' \1', text)
            # Clean up \text{} - extract content  
            text = re.sub(r'\\text\s*\{\s*([^}]*)\s*\}', r'\1', text)
            # Clean up \textbf{} 
            text = re.sub(r'\\textbf\s*\{\s*([^}]*)\s*\}', r'\1', text)
            # Clean up \frac{}{} - convert to simple division
            text = re.sub(r'\\frac\s*\{([^}]*)\}\s*\{([^}]*)\}', r'\1/\2', text)
            
            # Clean up ~ (non-breaking space in LaTeX) - BEFORE other cleanup
            text = text.replace('~', ' ')
            
            # Clean up LaTeX escapes
            text = text.replace('\\%', '%')
            text = text.replace('\\&', '&')
            text = text.replace('\\#', '#')
            text = text.replace('\\$', '$')
            text = text.replace('\\_', '_')
            
            # Remove remaining backslash commands but preserve the content
            text = re.sub(r'\\([a-zA-Z]+)\s*', r'\1 ', text)
            
            # Clean up specific LaTeX patterns that might remain
            text = re.sub(r'\bmathrm\b', '', text)  # Remove leftover "mathrm"
            text = re.sub(r'\s*kg\s*/\s*m\s*\^\s*2', ' kg/m²', text)  # Fix kg/m^2
            text = re.sub(r'\s*kg\s*/\s*m\s*2', ' kg/m²', text)  # Fix kg/m2
            text = re.sub(r'\s*\^\s*2', '²', text)  # Convert ^2 to superscript
            text = re.sub(r'\s*\^\s*3', '³', text)  # Convert ^3 to superscript
            
            # Fix common LaTeX remnants in BMI values like "28.7  kg /  m²"
            text = re.sub(r'(\d+\.?\d*)\s+kg\s*/\s*m[²2]', r'\1 kg/m²', text)
            
            # Remove Hebrew characters (Unicode range for Hebrew: \u0590-\u05FF)
            text = re.sub(r'[\u0590-\u05FF]+', '', text)
            
            # ===== STEP 2: OCR Garbage Cleanup =====
            # Remove common OCR garbage patterns
            garbage_patterns = [
                r'^.*Digitized by Google.*$',  # Google digitization watermarks
                r'^.*DENTAL TAGLINE HERE.*$',  # Placeholder text
                r'^.*eye clinic room background.*$',  # Image description garbage
                r'^\s*\d{4},\s*[A-Za-z]+.*$',  # Pattern like "1100, Clinic, and Dentist"
                r'^.*Ward:.*$',  # Ward lines (OCR garbage)
                r'^.*Child:.*investigated.*$',  # OCR garbage
                r'^.*Insurance Information.*$',  # Repeated headers
                r'^.*Parental Information.*$',  # OCR garbage
                r'^.*Size of Birth.*$',  # OCR garbage
                r'^\s*\d+\s*$',  # Lines with just numbers
                r'^[A-Z]\s+.*background.*$',  # Image description like "A eye clinic room"
                r'^\s*---+\s*$',  # Horizontal rule lines
            ]
            
            for pattern in garbage_patterns:
                text = re.sub(pattern, '', text, flags=re.MULTILINE | re.IGNORECASE)
            
            # Remove repeated patient name/ID patterns (OCR artifacts)
            # Pattern: "1100, Efraim Hal" or similar repeated many times
            text = re.sub(r'(\d{4,},\s*[A-Za-z]+\s+[A-Za-z]+\s*\n?){3,}', '', text)
            
            # Remove blocks of repeated lines (same text appearing 3+ times)
            lines = text.split('\n')
            cleaned_lines = []
            seen_count = {}
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    cleaned_lines.append(line)
                    continue
                # Count occurrences
                if stripped in seen_count:
                    seen_count[stripped] += 1
                else:
                    seen_count[stripped] = 1
                # Only include if seen less than 3 times
                if seen_count[stripped] <= 2:
                    cleaned_lines.append(line)
            text = '\n'.join(cleaned_lines)
            
            # Remove other non-printable/garbage characters but keep basic punctuation
            text = re.sub(r'[^\x20-\x7E\n\r\t°²³±µ×÷€£¥©®™•–—''""…%/]', '', text)
            
            # Remove lines that are just squares/boxes (common PDF rendering issue)
            text = re.sub(r'^[■□▪▫]+\s*$', '', text, flags=re.MULTILINE)
            text = re.sub(r'■+', '', text)
            
            # Clean up multiple spaces
            text = re.sub(r'  +', ' ', text)
            # Clean up multiple newlines
            text = re.sub(r'\n{3,}', '\n\n', text)
            
            # Final cleanup - remove any remaining dollar signs that aren't currency
            text = re.sub(r'\$(\d)', r'\1', text)  # $28.7 -> 28.7
            
            return text.strip()
        
        report_content = clean_text_for_pdf(report_content)
        
        # Remove duplicate title/header blocks (LLM sometimes repeats the header)
        # Pattern matches: "VizBriz Level-4..." followed by Patient line and Date line
        report_content = re.sub(
            r'(?:^|\n)(VizBriz Level-4[^\n]*Report[^\n]*\n+(?:Patient:[^\n]*\n+)?(?:Date:[^\n]*\n+)?)+',
            '\n',
            report_content,
            flags=re.IGNORECASE | re.MULTILINE
        )
        
        # Also remove standalone duplicate headers without patient/date
        report_content = re.sub(
            r'(?:^|\n)(VizBriz Level-4[^\n]*Report[^\n]*\n){2,}',
            '\nVizBriz Level-4 — OSA Data Assessment Report\n',
            report_content,
            flags=re.IGNORECASE | re.MULTILINE
        )
        
        # Remove "Clinical Images" section and everything after it (this contains OCR garbage)
        report_content = re.sub(
            r'\n*#*\s*Clinical Images\s*\n.*$',
            '',
            report_content,
            flags=re.DOTALL | re.IGNORECASE
        )
        
        # Pre-process to convert plain text tables to markdown tables
        def convert_plain_tables_to_markdown(text):
            """Convert plain text tables to markdown format for proper rendering"""
            lines = text.split('\n')
            result = []
            i = 0
            
            while i < len(lines):
                line = lines[i].strip()
                
                # Special handling for Sleep Study Data (multi-column format: Key1: Val1   Key2: Val2)
                # This pattern matches lines like "AHI: 33.3                REM AHI: 0.0"
                # Also handles single key:value pairs like "O2 Nadir: 77%"
                sleep_keys = ['AHI', 'REM AHI', 'RDI', 'REM RDI', 'ODI', 'REM ODI', 'Supine AHI', 'Supine RDI', 
                              'Supine ODI', 'Non-Supine AHI', 'Non-supine AHI', 'Snoring', 'Snoring %', 
                              'O2 Nadir', 'Oxygen Nadir', 'Sleep Efficiency', 'Total Sleep Time']
                
                # Check if line contains sleep study data (has a known key followed by colon)
                is_sleep_data = any(f'{key}:' in line for key in sleep_keys)
                
                if is_sleep_data:
                    # Parse all key:value pairs from this line
                    # Pattern: find all "Key: Value" pairs where value ends at next key or end of line
                    remaining = line
                    parsed_pairs = []
                    
                    for key in sleep_keys:
                        pattern = rf'{re.escape(key)}:\s*([^\s]+(?:\s+[^\s:]+)*?)(?=\s+(?:{"|".join(re.escape(k) for k in sleep_keys)}):|\s*$)'
                        match = re.search(pattern, remaining, re.IGNORECASE)
                        if match:
                            value = match.group(1).strip()
                            if value and value != '—' and value != '-':
                                parsed_pairs.append((key, value))
                    
                    # If we found pairs, add them as table rows
                    if parsed_pairs:
                        for key, value in parsed_pairs:
                            result.append(f"| {key} | {value} |")
                        i += 1
                        continue
                
                # Detect table header patterns (2-column format)
                table_headers = [
                    ('Field', 'Value'),
                    ('Key Observations', 'Details'),
                    ('Parameter', 'Data-Based Consideration'),
                    ('Device', 'Key Features'),
                    ('Item', 'Details'),
                ]
                
                is_table_header = False
                header_cols = None
                
                for h1, h2 in table_headers:
                    if h1 in line and h2 in line:
                        is_table_header = True
                        header_cols = [h1.strip(), h2.strip()]
                        break
                
                if is_table_header and header_cols:
                    # Start a markdown table
                    result.append(f"| {header_cols[0]} | {header_cols[1]} |")
                    result.append("|---|---|")
                    i += 1
                    
                    # Collect subsequent rows until we hit an empty line or a section header
                    while i < len(lines):
                        row_line = lines[i].strip()
                        
                        # Stop if empty line or section header
                        if not row_line or row_line.startswith('#') or row_line.startswith('##'):
                            break
                        
                        # Check if it's a new section (title-case line)
                        if re.match(r'^[A-Z][a-z]+(\s+[A-Z][a-z]+)+$', row_line) and len(row_line) < 50:
                            break
                        
                        # Skip if it's a garbage pattern
                        if any(g in row_line.lower() for g in ['digitized', 'tagline', 'ward:', '00 kg']):
                            i += 1
                            continue
                        
                        # Try to split into key-value (multiple spaces between key and value)
                        kv_match = re.match(r'^([A-Za-z][A-Za-z\s&/]+?)\s{2,}(.+)$', row_line)
                        if kv_match:
                            key = kv_match.group(1).strip()
                            value = kv_match.group(2).strip()
                            result.append(f"| {key} | {value} |")
                        elif ':' in row_line:
                            parts = row_line.split(':', 1)
                            if len(parts) == 2 and len(parts[0]) < 40:
                                result.append(f"| {parts[0].strip()} | {parts[1].strip()} |")
                            else:
                                result.append(row_line)
                        else:
                            # Not a table row, add as-is and break
                            result.append(row_line)
                            break
                        
                        i += 1
                    
                    result.append('')  # Empty line after table
                else:
                    result.append(line)
                    i += 1
            
            return '\n'.join(result)
        
        report_content = convert_plain_tables_to_markdown(report_content)
        
        # ===== SECTION VALIDATION =====
        # Ensure all 13 required sections are present per the Level-4 prompt
        def validate_and_fix_sections(text):
            """Validate all required sections exist and add missing ones"""
            required_sections = [
                ('DISCLAIMER', r'(?:DISCLAIMER|This report is generated for clinical reference)'),
                ('Personal Details', r'Personal Details'),
                ('Clinical Background', r'Clinical Background'),
                ('ENT', r'ENT.*(?:Findings|Sinus)'),
                ('Sleep Study Data', r'Sleep Study Data'),
                ('Observations', r'^Observations$'),
                ('Structural Observations', r'Structural Observations'),
                ('Treatment Considerations', r'(?:Possible\s+)?Treatment Considerations'),
                ('Device Design', r'Device Design'),
                ('Oral Appliance Therapy Pathway', r'Oral Appliance Therapy Pathway'),
                ('Recommended Appliance Design Classes', r'(?:Recommended\s+)?Appliance Design Classes'),
                ('Recommendations for Further Evaluation', r'Recommendations for Further Evaluation'),
                ('FINAL DISCLAIMER', r'(?:FINAL DISCLAIMER|This AI-generated report assists)'),
            ]
            
            missing_sections = []
            for section_name, pattern in required_sections:
                if not re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
                    missing_sections.append(section_name)
            
            if missing_sections:
                logger.warning(f"Missing sections in Level-4 report: {missing_sections}")
                
                # Add missing sections at the end with placeholders
                additions = []
                
                if 'Oral Appliance Therapy Pathway' in missing_sections:
                    additions.append("""
Oral Appliance Therapy Pathway

Based on the patient's anatomy and sleep study findings, the following general therapy pathways may be considered:
• Mandibular advancement devices for moderate to severe OSA
• Combination therapy (MAD + CPAP) if single therapy insufficient
• TMJ-friendly low-profile appliances if jaw discomfort is a concern
""")
                
                if 'Recommended Appliance Design Classes' in missing_sections:
                    additions.append("""
Recommended Appliance Design Classes

Device                      Key Features
Herbst-style telescopic     Suitable for retruded mandible and tongue-base obstruction
TMJ-friendly designs        Controlled protrusion for jaw muscle discomfort goals
Reinforced rigid acrylic    Construction for bruxism management
""")
                
                if 'FINAL DISCLAIMER' in missing_sections and 'This AI-generated report' not in text:
                    additions.append("""
FINAL DISCLAIMER: This AI-generated report assists in analyzing sleep and anatomical data. It does not replace physician evaluation, DISE assessment, or radiologic interpretation. All findings must be reviewed by a qualified healthcare provider before making clinical decisions.
""")
                
                if additions:
                    # Find where to insert (before FINAL DISCLAIMER if it exists, or at end)
                    final_match = re.search(r'(FINAL DISCLAIMER|This AI-generated report assists)', text, re.IGNORECASE)
                    if final_match:
                        insert_pos = final_match.start()
                        text = text[:insert_pos] + '\n'.join(additions) + '\n\n' + text[insert_pos:]
                    else:
                        text = text + '\n' + '\n'.join(additions)
            
            return text
        
        report_content = validate_and_fix_sections(report_content)
        
        # Clean up report content - fix any malformed HTML tags before processing
        def clean_html_tags(text):
            """Clean up malformed HTML tags in text"""
            # Remove any existing <para> tags (ReportLab adds these automatically)
            text = re.sub(r'</?para>', '', text, flags=re.IGNORECASE)
            
            # Fix unclosed bold tags: <b>text<b> -> <b>text</b>
            # This handles cases where <b> appears twice without a closing tag
            text = re.sub(r'<b>([^<]*)<b>', r'<b>\1</b>', text)
            
            # Fix cases where we have <b>text</b>text<b> (should be <b>text</b>text<b>text</b>)
            # But we'll handle this by ensuring all <b> have matching </b>
            
            # Fix nested opening tags: <b><b>text -> <b>text
            text = re.sub(r'<b>\s*<b>', '<b>', text)
            # Fix nested closing tags: </b></b>text -> </b>text
            text = re.sub(r'</b>\s*</b>', '</b>', text)
            
            # Now balance bold tags - ensure every <b> has a matching </b>
            # We'll do this by tracking open/close pairs
            result = []
            open_count = 0
            i = 0
            while i < len(text):
                if text[i:i+3] == '<b>':
                    open_count += 1
                    result.append('<b>')
                    i += 3
                elif text[i:i+4] == '</b>':
                    if open_count > 0:
                        open_count -= 1
                        result.append('</b>')
                    # Otherwise skip this closing tag (it's extra)
                    i += 4
                else:
                    result.append(text[i])
                    i += 1
            
            # Close any remaining open tags
            text = ''.join(result) + '</b>' * open_count
            
            return text
        
        # Clean the report content first
        report_content = clean_html_tags(report_content)
        
        # Parse markdown-like content and convert to PDF
        lines = report_content.split('\n')
        current_table = []
        in_table = False
        table_headers = []
        skip_next_line = False  # Flag to skip separator lines
        current_section = None  # Track current section name
        structural_obs_done = False  # Flag to track if we've passed Structural Observations
        in_disclaimer_section = False  # Flag to track if we're in FINAL DISCLAIMER section
        
        # Pre-build clinical images gallery elements (to be inserted after Structural Observations)
        clinical_images_elements = []
        try:
            from reportlab.platypus import Image as RLImage
            from reportlab.lib.units import inch
            
            s3_client_images = boto3.client(
                's3',
                aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
                region_name=os.getenv('AWS_REGION', 'us-west-2')
            )
            bucket_name = os.getenv('S3_BUCKET_NAME')
            
            image_prefix = f"patients/{patient_id}/imaging/level4-images/"
            response = s3_client_images.list_objects_v2(
                Bucket=bucket_name,
                Prefix=image_prefix
            )
            
            clinical_images = []
            if 'Contents' in response:
                for obj in response['Contents']:
                    key = obj['Key']
                    if key.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                        clinical_images.append(key)
            
            if clinical_images:
                # Build gallery elements
                clinical_images_elements.append(Spacer(1, 16))
                clinical_images_elements.append(Paragraph("Clinical Images", subsection_style))
                clinical_images_elements.append(Spacer(1, 8))
                
                images_per_row = 3
                frame_width = 2.0 * inch
                frame_height = 1.5 * inch
                
                image_elements = []
                for img_key in clinical_images:
                    try:
                        img_obj = s3_client_images.get_object(Bucket=bucket_name, Key=img_key)
                        img_data = img_obj['Body'].read()
                        img_buffer = io.BytesIO(img_data)
                        
                        img = RLImage(img_buffer)
                        orig_width = img.drawWidth
                        orig_height = img.drawHeight
                        width_scale = frame_width / orig_width
                        height_scale = frame_height / orig_height
                        scale_factor = min(width_scale, height_scale)
                        img.drawWidth = orig_width * scale_factor
                        img.drawHeight = orig_height * scale_factor
                        image_elements.append(img)
                        logger.info(f"Pre-built clinical image for gallery: {img_key}")
                    except Exception as img_exc:
                        logger.warning(f"Failed to pre-build image {img_key}: {img_exc}")
                        continue
                
                if image_elements:
                    while len(image_elements) % images_per_row != 0:
                        image_elements.append('')
                    
                    gallery_rows = []
                    for i in range(0, len(image_elements), images_per_row):
                        row = image_elements[i:i + images_per_row]
                        gallery_rows.append(row)
                    
                    col_width = (doc.width - 20) / images_per_row
                    gallery_table = Table(gallery_rows, colWidths=[col_width] * images_per_row)
                    gallery_table.setStyle(TableStyle([
                        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                        ('LEFTPADDING', (0, 0), (-1, -1), 8),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                        ('TOPPADDING', (0, 0), (-1, -1), 8),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#e5e7eb')),
                        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
                        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f9fafb')),
                    ]))
                    clinical_images_elements.append(gallery_table)
                    clinical_images_elements.append(Spacer(1, 12))
                    logger.info(f"Pre-built image gallery with {len(image_elements)} images")
        except Exception as prebuild_exc:
            logger.warning(f"Could not pre-build clinical images: {prebuild_exc}")
        
        def process_table(table_rows, headers):
            """Convert table rows to PDF Table"""
            if not table_rows and not headers:
                return
            
            # Prepare table data
            table_data = []
            has_header = bool(headers)
            
            if headers:
                # Add header row
                header_row = [Paragraph(cell, table_header_style) for cell in headers]
                table_data.append(header_row)
            
            # Add data rows
            for row in table_rows:
                # Handle rows that might have different lengths
                if headers:
                    # Pad or truncate row to match header length
                    while len(row) < len(headers):
                        row.append('')
                    row = row[:len(headers)]
                
                cell_paras = []
                for cell in row:
                    # Process markdown in cells
                    cell_text = cell.strip()
                    # Convert markdown bold - handle multiple bold sections
                    import re
                    cell_text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', cell_text)
                    cell_paras.append(Paragraph(cell_text, table_cell_style))
                table_data.append(cell_paras)
            
            if table_data:
                # Calculate column widths - distribute evenly or use content-based
                num_cols = len(table_data[0])
                available_width = doc.width - doc.leftMargin - doc.rightMargin
                col_width = available_width / num_cols
                
                # Create table with proper styling
                table = Table(table_data, colWidths=[col_width] * num_cols)
                
                if has_header:
                    # Style with blue header
                    table.setStyle(TableStyle([
                        # Header row styling - blue
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),  # Blue header
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),  # White text on blue
                        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, 0), 11),
                        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
                        ('TOPPADDING', (0, 0), (-1, 0), 10),
                        # Data row styling
                        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                        ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#1a1a1a')),  # Dark text
                        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                        ('FONTSIZE', (0, 1), (-1, -1), 10),
                        # Grid lines
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d1d5db')),  # Light gray grid
                        ('LINEBELOW', (0, 0), (-1, 0), 2.0, colors.HexColor('#1d4ed8')),  # Thick blue line below header
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('LEFTPADDING', (0, 0), (-1, -1), 10),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                        ('TOPPADDING', (0, 1), (-1, -1), 8),
                        ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
                        # Alternate row colors
                        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
                    ]))
                else:
                    # Style WITHOUT header (for Sleep Study Data) - just data rows
                    table.setStyle(TableStyle([
                        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, -1), 10),
                        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#1a1a1a')),  # Dark text
                        ('BACKGROUND', (0, 0), (-1, -1), colors.white),
                        # Light grid lines only
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),  # Very light gray grid
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('LEFTPADDING', (0, 0), (-1, -1), 10),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                        ('TOPPADDING', (0, 0), (-1, -1), 8),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                        # Alternate row colors
                        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
                    ]))
                
                elements.append(KeepTogether(table))
                elements.append(Spacer(1, 12))
        
        for i, line in enumerate(lines):
            # Skip separator lines
            if skip_next_line:
                skip_next_line = False
                continue
            
            # Clean LaTeX and garbage from each line
            line = clean_text_for_pdf(line)
            line_stripped = line.strip()
            
            # Skip empty lines after cleanup
            if not line_stripped:
                continue
            
            # Skip duplicate title lines
            if 'VizBriz Level-4' in line_stripped and 'Report' in line_stripped:
                continue
            if line_stripped.startswith('Patient:') and 'ID:' in line_stripped:
                continue
            if line_stripped.startswith('Date:') and len(line_stripped) < 30:
                continue
            
            # Skip garbage/OCR text patterns (aggressive filtering)
            garbage_patterns = [
                'Digitized by Google',
                'DENTAL TAGLINE',
                'eye clinic room',
                'mailbox',
                '@gmail.com',
                'VHS, Clinic',
                'drbriz',
                'drpearl',
                'tamar',
                'actions',
                'Image Info',
                'By Image',
                'Patient Details -',
                'Parental Information',
                'Ward:',
                'HAS 12',
                'Deans',
                'Child:',
                'Insurance Information',
                'Health Records',
                '1100, Clinic',
                'not investigated',
                'Size of Birth',
                'ID: 77190',
                'ID: 54000',
                'ID: 69498',
                'ID: 10317',
                'koren',
                'Unterman',
                'Doukarsky',
            ]
            
            # Skip redundant disclaimer text in Recommendations section
            # (This belongs in FINAL DISCLAIMER, not in Recommendations)
            recommendations_disclaimer_patterns = [
                'This assessment is based on available clinical data',
                'Treatment recommendations should be individualized',
                'patient preferences, and ongoing monitoring',
                'All therapeutic interventions require',
                'appropriate medical supervision and follow-up',
                'Regular follow-up and monitoring are essential',
                'response to therapy',
            ]
            
            skip_line = False
            
            # Check if we're in Recommendations section and line contains disclaimer text
            if current_section and 'recommendation' in current_section.lower():
                for pattern in recommendations_disclaimer_patterns:
                    if pattern.lower() in line_stripped.lower():
                        skip_line = True
                        break
            
            # Also check garbage patterns
            if not skip_line:
                for garbage in garbage_patterns:
                    if garbage.lower() in line_stripped.lower():
                        skip_line = True
                        break
            
            if skip_line:
                continue
            
            # Skip lines that look like repeated patterns (OCR garbage)
            if re.match(r'^(Age|Sex):\s*(Male|Female|Age|Sex|\d+)[,\s]*(Age|Sex)?', line_stripped):
                if line_stripped.count('Sex') > 1 or line_stripped.count('Age') > 1:
                    continue
            
            # Skip lines with repeated "Ward:" or "kg" patterns (OCR garbage)
            if line_stripped.count('Ward') > 1 or line_stripped.count('00 kg') > 1:
                continue
            
            # Skip lines that are mostly numbers and colons (OCR garbage)
            if re.match(r'^[\d\s:\/]+$', line_stripped) and len(line_stripped) > 5:
                continue
            
            # Skip very short meaningless lines
            if len(line_stripped) < 3:
                continue
            
            # Skip the Clinical Images header if we're in the content processing (it's added separately)
            if line_stripped == 'Clinical Images':
                continue
            
            # Check for table rows
            if line_stripped.startswith('|') and '|' in line_stripped[1:]:
                cells = [cell.strip() for cell in line_stripped.split('|')[1:-1]]
                
                # Check if this is a header row (next line is separator)
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    # Check if next line is a markdown table separator (contains only |, -, :, spaces)
                    if next_line.startswith('|') and all(c in '|-: ' for c in next_line.replace('|', '')):
                        # This is a header row, save it and mark separator to skip
                        if in_table:
                            # Process previous table first
                            process_table(current_table, table_headers)
                            current_table = []
                            table_headers = []
                        
                        # Check if this is a generic "Field | Value" type header - these should be REMOVED
                        # (Personal Details, Device Design, Oral Appliance tables don't need column headers)
                        is_generic_header = (
                            len(cells) == 2 and 
                            cells[0].lower() in ['field', 'item', 'parameter', 'metric', 'key observations', 'device'] and 
                            cells[1].lower() in ['value', 'details', 'data-based consideration', 'clinician notes', 'key features']
                        )
                        
                        if is_generic_header:
                            # Skip this header entirely - render as headerless table
                            table_headers = []
                        else:
                            table_headers = cells
                        
                        in_table = True
                        current_table = []
                        # Mark next line (separator) to skip
                        skip_next_line = True
                        continue
                
                if in_table:
                    current_table.append(cells)
                else:
                    # Start a new table - might be a table without explicit header separator
                    # Check if this is a Sleep Study data row (has metric names like AHI, RDI)
                    # These should be rendered as data-only tables WITHOUT header styling
                    # Also check for "Time Below 90%" or "Time O2 < 90%" patterns
                    is_sleep_study_data = any(word.upper() in ['AHI', 'RDI', 'ODI', 'REM', 'SUPINE', 'NON-SUPINE', 
                                                               'SNORING', 'O2', 'NADIR', 'EFFICIENCY', 'TIME'] 
                                              for word in cells) or \
                                         any('time below' in ' '.join(cells).lower() or 
                                             'time o2' in ' '.join(cells).lower() or
                                             't90' in ' '.join(cells).lower()
                                             for cell in cells if cell)
                    
                    if is_sleep_study_data:
                        # This is Sleep Study data - NO headers, just data rows
                        if not in_table:
                            table_headers = []  # No headers for sleep study data
                            in_table = True
                            current_table = []
                        current_table.append(cells)
                    else:
                        # Regular table - use first row as header
                        if not in_table:
                            table_headers = cells
                            in_table = True
                            current_table = []
                continue
            
            # Process any pending table when we hit a non-table line
            if in_table and (not line_stripped or not line_stripped.startswith('|')):
                # Check if this non-table line is "Time Below 90%" that should be added to sleep study table
                if current_section and 'sleep study' in current_section.lower():
                    time_below_patterns = [
                        r'Time\s+Below\s+90%',
                        r'Time\s+O2\s*[<&lt;]\s*90%',
                        r'T90',
                        r'Time\s+O2\s+<90%'
                    ]
                    for pattern in time_below_patterns:
                        if re.search(pattern, line_stripped, re.IGNORECASE):
                            # This is "Time Below 90%" - add it to the table as a row
                            # Try to parse it as "Time Below 90%: X% (Y minutes)" or similar
                            time_below_match = re.search(r'Time\s+(?:Below|O2\s*[<&lt;])\s*90%[:\s]*([^\(\)]+)(?:\(([^\)]+)\))?', line_stripped, re.IGNORECASE)
                            if time_below_match:
                                value = time_below_match.group(1).strip()
                                minutes = time_below_match.group(2).strip() if time_below_match.group(2) else ''
                                if minutes:
                                    row_cells = [f"Time Below 90%", f"{value} ({minutes})"]
                                else:
                                    row_cells = [f"Time Below 90%", value]
                                current_table.append(row_cells)
                                continue  # Skip further processing of this line
                
                # Only process if we have data rows (not just headers)
                if current_table or (table_headers and not current_table):
                    process_table(current_table, table_headers)
                current_table = []
                table_headers = []
                in_table = False
            
            # Skip empty lines (but add spacing)
            if not line_stripped:
                if elements and not isinstance(elements[-1], Spacer):
                    elements.append(Spacer(1, 6))
                continue
            
            # Check for headings (markdown style)
            if line_stripped.startswith('# '):
                elements.append(Spacer(1, 8))
                elements.append(Paragraph(line_stripped[2:].strip(), title_style))
            elif line_stripped.startswith('## '):
                elements.append(Spacer(1, 8))
                section_text = line_stripped[3:].strip()
                
                # Check if we just finished "Structural Observations" and are starting a new section
                # Insert clinical images gallery before the new section
                if current_section and 'structural observations' in current_section.lower() and not structural_obs_done:
                    if clinical_images_elements:
                        elements.extend(clinical_images_elements)
                        logger.info("Inserted clinical images gallery after Structural Observations")
                    structural_obs_done = True
                
                # Update current section
                current_section = section_text
                elements.append(Paragraph(section_text, section_style))
            elif line_stripped.startswith('### '):
                elements.append(Paragraph(line_stripped[4:].strip(), subsection_style))
            elif line_stripped.startswith('* ') or line_stripped.startswith('- '):
                # Bullet point
                bullet_text = line_stripped[2:].strip()
                # Convert markdown bold - handle multiple bold sections correctly
                import re
                bullet_text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', bullet_text)
                # Clean up any malformed tags
                bullet_text = re.sub(r'<b>([^<]*)<b>', r'<b>\1</b>', bullet_text)  # Fix double <b>
                bullet_text = re.sub(r'</b>([^<]*)</b>', r'</b>\1</b>', bullet_text)  # Fix double </b>
                elements.append(Paragraph(f"• {bullet_text}", normal_style))
            elif line_stripped.startswith('**') and line_stripped.endswith('**'):
                # Bold text paragraph
                import re
                bold_text = line_stripped
                # Convert markdown bold - handle multiple bold sections correctly
                bold_text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', bold_text)
                # Clean up any malformed tags
                bold_text = re.sub(r'<b>([^<]*)<b>', r'<b>\1</b>', bold_text)  # Fix double <b>
                bold_text = re.sub(r'</b>([^<]*)</b>', r'</b>\1</b>', bold_text)  # Fix double </b>
                elements.append(Paragraph(bold_text, bold_style))
            else:
                # Check if this line looks like a section header (plain text, title case)
                # Common section headers in Level-4 reports
                section_headers = [
                    'Personal Details', 'Clinical Background', 'ENT', 'DISE', 'Sleep Study',
                    'Observations', 'Structural Observations', 'Treatment Considerations',
                    'Possible Treatment', 'Device Design', 'Oral Appliance', 'Recommendations',
                    'Conclusion', 'DISCLAIMER', 'FINAL DISCLAIMER'
                ]
                is_section_header = False
                for header in section_headers:
                    if line_stripped.lower().startswith(header.lower()) and len(line_stripped) < 60:
                        is_section_header = True
                        break
                
                # Also check if it's title case and short (likely a section header)
                if not is_section_header and len(line_stripped) < 50:
                    words = line_stripped.split()
                    if len(words) >= 2 and all(w[0].isupper() for w in words if len(w) > 2 and w[0].isalpha()):
                        # Check against known section words
                        section_words = ['details', 'background', 'findings', 'study', 'data', 
                                        'observations', 'considerations', 'design', 'appliance', 
                                        'recommendations', 'conclusion', 'disclaimer']
                        if any(sw in line_stripped.lower() for sw in section_words):
                            is_section_header = True
                
                if is_section_header:
                    # Check if we just finished "Structural Observations" and are starting a new section
                    if current_section and 'structural observations' in current_section.lower() and not structural_obs_done:
                        if clinical_images_elements:
                            elements.extend(clinical_images_elements)
                            logger.info("Inserted clinical images gallery after Structural Observations (plain text header)")
                        structural_obs_done = True
                    
                    # Special handling for FINAL DISCLAIMER - add extra spacing and track section
                    if 'FINAL DISCLAIMER' in line_stripped.upper() or ('disclaimer' in line_stripped.lower() and 'final' in line_stripped.lower()):
                        elements.append(Spacer(1, 36))  # Extra spacing before disclaimer (increased from 24)
                        in_disclaimer_section = True
                        
                        # If the disclaimer text is inline (e.g., "FINAL DISCLAIMER: This AI-generated...")
                        # Make the entire line bold
                        if ':' in line_stripped and len(line_stripped) > 20:
                            disclaimer_text = f'<b>{line_stripped}</b>'
                            elements.append(Paragraph(disclaimer_text, bold_style))
                            current_section = 'FINAL DISCLAIMER'
                            continue
                    else:
                        elements.append(Spacer(1, 8))
                        in_disclaimer_section = False
                    
                    # Update current section
                    current_section = line_stripped
                    elements.append(Paragraph(line_stripped, section_style))
                    continue
                
                # Regular paragraph
                # First, clean up any existing malformed HTML tags
                import re
                para_text = line_stripped
                
                # Check if this is FINAL DISCLAIMER content - use special disclaimer style
                if in_disclaimer_section or 'This AI-generated report' in para_text or 'This assessment is based on' in para_text:
                    # Use disclaimer style instead of bold
                    para_text = para_text  # Don't add bold tags, use disclaimer_style instead
                    elements.append(Paragraph(para_text, disclaimer_style))
                    continue
                
                # Fix malformed bold tags (e.g., <b>text<b> -> <b>text</b>)
                para_text = re.sub(r'<b>([^<]*)<b>', r'<b>\1</b>', para_text)
                para_text = re.sub(r'</b>([^<]*)</b>', r'</b>\1</b>', para_text)
                # Fix unclosed bold tags at end of line
                para_text = re.sub(r'<b>([^<]*)$', r'<b>\1</b>', para_text)
                # Escape HTML special characters (but preserve existing valid tags)
                # We need to escape &, <, > that are not part of valid HTML tags
                # This is tricky - we'll escape everything first, then restore valid tags
                para_text = para_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                # Restore valid HTML tags (b, i, u, br, etc.)
                para_text = para_text.replace('&lt;b&gt;', '<b>').replace('&lt;/b&gt;', '</b>')
                para_text = para_text.replace('&lt;i&gt;', '<i>').replace('&lt;/i&gt;', '</i>')
                para_text = para_text.replace('&lt;u&gt;', '<u>').replace('&lt;/u&gt;', '</u>')
                para_text = para_text.replace('&lt;br&gt;', '<br/>').replace('&lt;br/&gt;', '<br/>')
                # Convert markdown bold (handle multiple bold sections)
                para_text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', para_text)
                # Final cleanup - ensure all <b> tags have matching </b>
                open_bold_count = para_text.count('<b>')
                close_bold_count = para_text.count('</b>')
                if open_bold_count > close_bold_count:
                    # Add missing closing tags
                    para_text += '</b>' * (open_bold_count - close_bold_count)
                elif close_bold_count > open_bold_count:
                    # Remove extra closing tags
                    para_text = re.sub(r'</b>', '', para_text, count=close_bold_count - open_bold_count)
                    # Re-add correct number
                    para_text += '</b>' * open_bold_count
                # Use normal style (disclaimer was handled above)
                elements.append(Paragraph(para_text, normal_style))
        
        # Process any remaining table
        if in_table:
            process_table(current_table, table_headers)
        
        # Fallback: If clinical images weren't inserted after Structural Observations
        # (e.g., section header wasn't detected), add them before the final disclaimer
        if clinical_images_elements and not structural_obs_done:
            logger.info("Clinical images not inserted after Structural Observations, adding as fallback")
            elements.extend(clinical_images_elements)
        
        # Build PDF
        doc.build(elements)
        pdf_content = buffer.getvalue()
        buffer.close()
        
        # Generate filename and S3 key (PDF only)
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        pdf_filename = f"Level_4_Report_Patient_{patient_id}_{timestamp}.pdf"
        pdf_s3_key = f"patients/{patient_id}/reports/{pdf_filename}"
        
        # Upload PDF to S3
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-west-2')
        )
        
        bucket_name = os.getenv('S3_BUCKET_NAME')
        if not bucket_name:
            return jsonify({'success': False, 'error': 'S3_BUCKET_NAME not configured'}), 500
        
        pdf_file = io.BytesIO(pdf_content)
        pdf_file.seek(0)
        s3_client.upload_fileobj(
            pdf_file,
            bucket_name,
            pdf_s3_key,
            ExtraArgs={'ContentType': 'application/pdf'}
        )
        logger.info(f"PDF uploaded to S3: {pdf_s3_key}, Size: {len(pdf_content)} bytes")
        
        # Save PDF to adminfiles table
        new_admin_file_pdf = AdminFile(
            name=pdf_filename,
            patient_id=patient_id,
            file_type='application/pdf',
            file_size=len(pdf_content),
            s3_key=pdf_s3_key,
            upload_date=datetime.utcnow(),
            file_category='Level 4 Report',
            is_public=False
        )
        db.session.add(new_admin_file_pdf)
        db.session.commit()
        
        logger.info(f"Level 4 Report saved to adminfiles for patient {patient_id}: PDF={pdf_filename}")
        
        return jsonify({
            'success': True,
            'message': f'Report saved and uploaded as PDF',
            'history_id': history_entry.id,
            'admin_file_id': new_admin_file_pdf.id,
            'pdf_filename': pdf_filename
        })
        
    except Exception as exc:
        db.session.rollback()
        logger.error(f"Error saving Level-4 report: {exc}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Failed to save report: {str(exc)}'
        }), 500


# ============================================================================
# LEVEL-4 CLINICIAN REVIEW ROUTES (DATA VALIDATION BEFORE GENERATION)
# ============================================================================

@reports_files_bp.route('/reports/level4-clinician-review', methods=['GET'])
@login_required
def reports_level4_clinician_review():
    """Render the Clinician Review page for Level-4 reports.
    
    This page allows clinicians to:
    1. Select a patient
    2. Review and edit the canonical JSON data before generation
    3. Validate the data quality
    4. Generate a report with the validated/corrected data
    """
    return render_template('level4_clinician_review.html')


@reports_files_bp.route('/reports/api/level4_clinician/patient/<int:patient_id>/data', methods=['GET'])
@login_required
def reports_level4_clinician_get_data(patient_id):
    """Get canonical JSON for a patient for clinician review/editing.
    
    Returns the full canonical JSON structure organized for display in the
    clinician review form, including patient details from the Patient model.
    """
    try:
        patient = Patient.query.get(patient_id)
        if not patient or not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Load canonical JSON
        try:
            canonical_json = _level4_load_canonical(patient_id)
        except Exception as exc:
            logger.error(f"Failed to load canonical JSON for patient {patient_id}: {exc}", exc_info=True)
            return jsonify({'success': False, 'error': f'Failed to load patient data: {str(exc)}'}), 404
        
        # Ensure all expected sections exist (for form population)
        canonical_json.setdefault('patient', {})
        canonical_json.setdefault('sleep_study', {})
        canonical_json.setdefault('position_stats', {})
        canonical_json.setdefault('anatomy', {})
        canonical_json.setdefault('observations', {})
        canonical_json.setdefault('treatment_history', {})
        canonical_json.setdefault('treatment_considerations', [])
        canonical_json.setdefault('device_design', {})
        canonical_json.setdefault('follow_up_plan', [])
        canonical_json.setdefault('recommendations', [])
        canonical_json.setdefault('oral_appliance_options', [])

        # Ensure sleep_study extras exist (for stable form population)
        if isinstance(canonical_json.get('sleep_study'), dict):
            ss = canonical_json['sleep_study']
            ss.setdefault('rera_count', '')
            ss.setdefault('total_sleep_time_text', '')
            canonical_json['sleep_study'] = ss

        # Ensure position_stats expected keys exist (for stable form population)
        if isinstance(canonical_json.get('position_stats'), dict):
            ps = canonical_json['position_stats']
            ps.setdefault('supine_pct_of_sleep', '')
            ps.setdefault('non_supine_pct_of_sleep', '')
            canonical_json['position_stats'] = ps

        # Ensure device_design expected keys exist (for stable form population)
        if isinstance(canonical_json.get('device_design'), dict):
            dd = canonical_json['device_design']
            for k in [
                'mandibular_advancement', 'vertical_opening', 'anterior_window',
                'retention_features', 'material', 'pre_set', 'anterior_acrylic',
                'coverage', 'clinical_notes'
            ]:
                dd.setdefault(k, '')
            canonical_json['device_design'] = dd
        
        # Merge in patient details from Patient model (name, id, dob)
        # These aren't stored in the canonical JSON but are needed for the form
        patient_section = canonical_json.get('patient', {})
        patient_section['id'] = patient.id
        patient_section['name'] = patient.name or ''
        
        # Get date of birth from Patient model
        if hasattr(patient, 'date_of_birth') and patient.date_of_birth:
            if hasattr(patient.date_of_birth, 'strftime'):
                patient_section['date_of_birth'] = patient.date_of_birth.strftime('%Y-%m-%d')
            else:
                patient_section['date_of_birth'] = str(patient.date_of_birth)
        elif hasattr(patient, 'dob') and patient.dob:
            if hasattr(patient.dob, 'strftime'):
                patient_section['date_of_birth'] = patient.dob.strftime('%Y-%m-%d')
            else:
                patient_section['date_of_birth'] = str(patient.dob)
        else:
            patient_section['date_of_birth'] = ''
        
        canonical_json['patient'] = patient_section
        
        return jsonify({
            'success': True,
            'patient_id': patient_id,
            'patient_name': patient.name,
            'canonical_json': canonical_json
        })
        
    except Exception as exc:
        logger.error(f"Error getting patient data for clinician review: {exc}", exc_info=True)
        return jsonify({'success': False, 'error': str(exc)}), 500


def _contains_hebrew(text: str) -> bool:
    """Return True if text contains any Hebrew Unicode characters."""
    if not text or not isinstance(text, str):
        return False
    # Hebrew block: U+0590–U+05FF
    return bool(re.search(r'[\u0590-\u05FF]', text))


def _collect_hebrew_strings(obj, base_path: str = ""):
    """
    Collect (path, value) pairs for string values containing Hebrew characters.
    Only traverses dict/list structures.
    """
    items = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{base_path}.{k}" if base_path else str(k)
            items.extend(_collect_hebrew_strings(v, path))
        return items
    if isinstance(obj, list):
        for idx, v in enumerate(obj):
            path = f"{base_path}[{idx}]"
            items.extend(_collect_hebrew_strings(v, path))
        return items
    if isinstance(obj, str) and _contains_hebrew(obj):
        items.append((base_path, obj))
    return items


def _set_value_by_path(root, path: str, value: str) -> bool:
    """
    Set a value into a nested dict/list using a simple path format:
    - dict keys separated by '.'
    - list indices in brackets, e.g. 'goals[0]' or 'anatomy.other_findings[2]'
    Returns True if the path was resolved and set.
    """
    if not path:
        return False

    # Tokenize: "a.b[0].c" -> ["a", "b", 0, "c"]
    tokens = []
    buf = ""
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == '.':
            if buf:
                tokens.append(buf)
                buf = ""
            i += 1
            continue
        if ch == '[':
            if buf:
                tokens.append(buf)
                buf = ""
            j = path.find(']', i)
            if j == -1:
                return False
            idx_str = path[i + 1:j]
            try:
                tokens.append(int(idx_str))
            except Exception:
                return False
            i = j + 1
            continue
        buf += ch
        i += 1
    if buf:
        tokens.append(buf)

    ref = root
    for t in tokens[:-1]:
        if isinstance(t, int):
            if not isinstance(ref, list) or t < 0 or t >= len(ref):
                return False
            ref = ref[t]
        else:
            if not isinstance(ref, dict) or t not in ref:
                return False
            ref = ref[t]

    last = tokens[-1]
    if isinstance(last, int):
        if not isinstance(ref, list) or last < 0 or last >= len(ref):
            return False
        ref[last] = value
        return True
    if not isinstance(ref, dict):
        return False
    ref[last] = value
    return True


def _parse_json_from_llm(text: str):
    """Best-effort JSON extraction from an LLM response."""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    # Try to extract first JSON object in the response
    try:
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            return json.loads(m.group(0))
    except Exception:
        return None
    return None


def _level4_reference_patient_dir(patient_id: int) -> Path:
    base = _LEVEL4_REFERENCE_DIR / str(patient_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _level4_get_latest_reference_file(patient_id: int) -> Optional[Path]:
    """Return most recent reference PDF file path for a patient (server-local storage)."""
    try:
        pdir = _level4_reference_patient_dir(patient_id)
        pdfs = sorted(pdir.glob('*.pdf'), key=lambda p: p.stat().st_mtime, reverse=True)
        return pdfs[0] if pdfs else None
    except Exception:
        return None


def _extract_pdf_text_from_bytes(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes using PyPDF2 (best-effort)."""
    try:
        import PyPDF2
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"PyPDF2 not available: {exc}")

    reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
    parts = []
    for page in reader.pages:
        try:
            t = page.extract_text() or ''
        except Exception:
            t = ''
        if t.strip():
            parts.append(t.strip())
    return "\n\n".join(parts).strip()


@reports_files_bp.route('/reports/api/level4_clinician/translate', methods=['POST'])
@login_required
def reports_level4_clinician_translate():
    """
    Translate Hebrew-containing strings in the clinician-edited Level-4 canonical_json to English.
    This runs during clinician review so the subsequent generation payload is already English.
    """
    try:
        data = request.get_json() or {}
        patient_id = data.get('patient_id')
        canonical_json = data.get('canonical_json')

        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400
        if not canonical_json or not isinstance(canonical_json, dict):
            return jsonify({'success': False, 'error': 'canonical_json must be a JSON object'}), 400

        patient = Patient.query.get(patient_id)
        if not patient or not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        # Collect Hebrew strings (skip patient.name to preserve exact spelling)
        all_pairs = _collect_hebrew_strings(canonical_json)
        skip_paths = {'patient.name'}
        pairs = [(p, v) for (p, v) in all_pairs if p not in skip_paths]

        if not pairs:
            return jsonify({
                'success': True,
                'canonical_json': canonical_json,
                'translated_count': 0,
            })

        bedrock_service = get_bedrock_service()
        if not bedrock_service or not bedrock_service.is_available():
            return jsonify({'success': False, 'error': 'Bedrock service unavailable'}), 500

        # Build a compact translation request: { "path": "hebrew string", ... }
        payload = {p: v for (p, v) in pairs}

        system_prompt = (
            "You are a medical translation engine. Translate Hebrew text into English.\n"
            "Return ONLY valid JSON.\n"
            "Rules:\n"
            "- Input is a JSON object mapping field paths to strings.\n"
            "- Output must be a JSON object with the SAME keys.\n"
            "- Translate values to clear clinical English, preserving meaning.\n"
            "- Keep numbers/units as-is.\n"
            "- If a value is already English or not Hebrew, return it unchanged.\n"
            "- Do NOT add extra keys, comments, or explanations.\n"
        )
        user_prompt = (
            "Translate the following JSON values to English.\n\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

        result = bedrock_service.invoke_model(
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            max_tokens=2000,
            temperature=0.0,
            patient_id=patient_id,
            endpoint='reports_level4_clinician_translate',
        )

        if not result.get('success'):
            return jsonify({'success': False, 'error': result.get('error', 'Translation failed')}), 500

        translated_map = _parse_json_from_llm(result.get('response', ''))
        if not isinstance(translated_map, dict):
            return jsonify({'success': False, 'error': 'Translation output was not valid JSON'}), 500

        # Apply translations back into the canonical_json
        applied = 0
        for path, original_value in pairs:
            translated_value = translated_map.get(path)
            if isinstance(translated_value, str) and translated_value.strip():
                if translated_value != original_value:
                    if _set_value_by_path(canonical_json, path, translated_value):
                        applied += 1

        return jsonify({
            'success': True,
            'canonical_json': canonical_json,
            'translated_count': applied,
        })

    except Exception as exc:
        logger.error(f"[Clinician Review] Translation error: {exc}", exc_info=True)
        return jsonify({'success': False, 'error': str(exc)}), 500


@reports_files_bp.route('/reports/api/level4_clinician/reference/<int:patient_id>', methods=['GET'])
@login_required
def reports_level4_clinician_reference_info(patient_id: int):
    """Get the latest uploaded reference Level-4 PDF metadata (server-local storage)."""
    try:
        patient = Patient.query.get(patient_id)
        if not patient or not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        latest = _level4_get_latest_reference_file(patient_id)
        if not latest:
            return jsonify({'success': True, 'reference': None})

        return jsonify({
            'success': True,
            'reference': {
                'filename': latest.name,
                'uploaded_at': datetime.utcfromtimestamp(latest.stat().st_mtime).isoformat() + 'Z',
                'view_url': f"/reports/api/level4_clinician/reference/{patient_id}/view",
                'download_url': f"/reports/api/level4_clinician/reference/{patient_id}/download",
            }
        })
    except Exception as exc:
        logger.error(f"[Clinician Review] Failed to load reference metadata: {exc}", exc_info=True)
        return jsonify({'success': False, 'error': str(exc)}), 500


@reports_files_bp.route('/reports/api/level4_clinician/reference/<int:patient_id>/view', methods=['GET'])
@login_required
def reports_level4_clinician_reference_view(patient_id: int):
    """View the latest reference PDF inline (server-local storage)."""
    patient = Patient.query.get(patient_id)
    if not patient or not current_user.can_access_patient(patient):
        return "Access denied", 403

    latest = _level4_get_latest_reference_file(patient_id)
    if not latest:
        return "Reference not found", 404

    return send_file(str(latest), mimetype='application/pdf', as_attachment=False, download_name=latest.name)


@reports_files_bp.route('/reports/api/level4_clinician/reference/<int:patient_id>/download', methods=['GET'])
@login_required
def reports_level4_clinician_reference_download(patient_id: int):
    """Download the latest reference PDF (server-local storage)."""
    patient = Patient.query.get(patient_id)
    if not patient or not current_user.can_access_patient(patient):
        return "Access denied", 403

    latest = _level4_get_latest_reference_file(patient_id)
    if not latest:
        return "Reference not found", 404

    return send_file(str(latest), mimetype='application/pdf', as_attachment=True, download_name=latest.name)


@reports_files_bp.route('/reports/api/level4_clinician/reference/upload', methods=['POST'])
@login_required
def reports_level4_clinician_reference_upload():
    """Upload a reference Level-4 PDF (server-local storage; no S3)."""
    try:
        patient_id = request.form.get('patient_id', type=int)
        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400

        patient = Patient.query.get(patient_id)
        if not patient or not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400

        f = request.files['file']
        if not f or not f.filename:
            return jsonify({'success': False, 'error': 'No file selected'}), 400

        # Basic size guard (request.content_length can be None depending on proxy)
        if request.content_length and request.content_length > 25 * 1024 * 1024:
            return jsonify({'success': False, 'error': 'File too large (max 25MB)'}), 400

        content_type = f.content_type or ''
        if content_type != 'application/pdf' and not f.filename.lower().endswith('.pdf'):
            return jsonify({'success': False, 'error': 'Only PDF files are supported'}), 400

        from werkzeug.utils import secure_filename
        safe_name = secure_filename(f.filename) or 'level4_reference.pdf'

        file_bytes = f.read()
        if not file_bytes:
            return jsonify({'success': False, 'error': 'Uploaded file was empty'}), 400

        if len(file_bytes) > 25 * 1024 * 1024:
            return jsonify({'success': False, 'error': 'File too large (max 25MB)'}), 400

        ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        out_path = _level4_reference_patient_dir(patient_id) / f"{ts}_{safe_name}"

        out_path.write_bytes(file_bytes)

        return jsonify({
            'success': True,
            'reference': {
                'filename': out_path.name,
                'uploaded_at': datetime.utcfromtimestamp(out_path.stat().st_mtime).isoformat() + 'Z',
                'view_url': f"/reports/api/level4_clinician/reference/{patient_id}/view",
                'download_url': f"/reports/api/level4_clinician/reference/{patient_id}/download",
            }
        })

    except Exception as exc:
        logger.error(f"[Clinician Review] Reference upload failed: {exc}", exc_info=True)
        return jsonify({'success': False, 'error': str(exc)}), 500


@reports_files_bp.route('/reports/api/level4_clinician/grade', methods=['POST'])
@login_required
def reports_level4_clinician_grade():
    """
    Grade the generated Level-4 report against the latest uploaded reference Level-4 PDF.
    If no reference exists, returns success=True with reference=null so UI can skip.
    """
    try:
        data = request.get_json() or {}
        patient_id = data.get('patient_id')
        generated_report = (data.get('generated_report') or '').strip()

        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400
        if not generated_report:
            return jsonify({'success': False, 'error': 'generated_report is required'}), 400

        patient = Patient.query.get(int(patient_id))
        if not patient or not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        latest = _level4_get_latest_reference_file(int(patient_id))
        if not latest:
            return jsonify({'success': True, 'reference': None, 'grade': None})

        reference_bytes = latest.read_bytes()
        reference_text = _extract_pdf_text_from_bytes(reference_bytes)
        if not reference_text:
            return jsonify({'success': False, 'error': 'Could not extract text from reference PDF'}), 500

        bedrock_service = get_bedrock_service()
        if not bedrock_service or not bedrock_service.is_available():
            return jsonify({'success': False, 'error': 'Bedrock service unavailable'}), 500

        system_prompt = (
            "You are a clinical QA grader.\n"
            "Compare a GENERATED Level-4 OSA report against a REFERENCE Level-4 report.\n"
            "Return ONLY valid JSON (no markdown, no extra text).\n"
            "Scores must be 0-100 integers.\n"
        )
        user_prompt = (
            "REFERENCE REPORT (extracted text):\n"
            "-----\n"
            f"{reference_text[:20000]}\n"
            "-----\n\n"
            "GENERATED REPORT:\n"
            "-----\n"
            f"{generated_report[:20000]}\n"
            "-----\n\n"
            "Return JSON with this exact schema:\n"
            "{\n"
            "  \"scores\": {\"precision\": 0, \"observations\": 0, \"recommendations\": 0, \"overall\": 0},\n"
            "  \"summary\": \"...\",\n"
            "  \"high_precision_matches\": [\"...\"],\n"
            "  \"missing_from_generated\": [\"...\"],\n"
            "  \"unsupported_in_generated\": [\"...\"],\n"
            "  \"recommendation_deltas\": [\"...\"],\n"
            "  \"notes\": \"...\"\n"
            "}\n"
        )

        result = bedrock_service.invoke_model(
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            max_tokens=1200,
            temperature=0.0,
            patient_id=int(patient_id),
            endpoint='reports_level4_clinician_grade',
        )

        if not result.get('success'):
            return jsonify({'success': False, 'error': result.get('error', 'Grading failed')}), 500

        grade_json = _parse_json_from_llm(result.get('response', ''))
        if not isinstance(grade_json, dict) or 'scores' not in grade_json:
            return jsonify({'success': False, 'error': 'Grading output was not valid JSON'}), 500

        return jsonify({
            'success': True,
            'reference': {
                'filename': latest.name,
                'uploaded_at': datetime.utcfromtimestamp(latest.stat().st_mtime).isoformat() + 'Z',
            },
            'grade': grade_json,
        })

    except Exception as exc:
        logger.error(f"[Clinician Review] Grading error: {exc}", exc_info=True)
        return jsonify({'success': False, 'error': str(exc)}), 500


@reports_files_bp.route('/reports/api/level4_clinician/generate', methods=['POST'])
@login_required
def reports_level4_clinician_generate():
    """Generate Level-4 report using clinician-validated/edited canonical JSON.
    
    This endpoint accepts the edited canonical JSON from the clinician review form
    and generates the report using that validated data instead of re-fetching
    from the database.
    """
    try:
        data = request.get_json() or {}
        patient_id = data.get('patient_id')
        canonical_json = data.get('canonical_json')
        
        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400
        
        if not canonical_json:
            return jsonify({'success': False, 'error': 'canonical_json is required'}), 400
        
        patient = Patient.query.get(patient_id)
        if not patient or not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Retrieve from Knowledge Bases (same as KB generate)
        bedrock_service = get_bedrock_service()
        
        style_docs = ""
        clinic_docs = ""
        kb_error = None
        
        if bedrock_service and bedrock_service.is_available():
            try:
                # Retrieve from KB_Level4_Style
                style_query = "Level 4 OSA report structure formatting"
                logger.info(f"[Clinician Review] KB Style Query: {style_query}")
                style_result = bedrock_service.query_knowledge_base(
                    query=style_query,
                    patient_id=None,
                    max_results=7,
                    knowledge_base_id=bedrock_service.KB_LEVEL4_STYLE_ID
                )
                
                if style_result.get('success'):
                    style_texts = style_result.get('retrieved_texts', [])
                    if style_texts:
                        cleaned_style = []
                        for text in style_texts:
                            if not text or len(text.strip()) < 100:
                                continue
                            if '<!--METADATA_END-->' in text:
                                content = text.split('<!--METADATA_END-->', 1)[1].strip()
                                if content:
                                    cleaned_style.append(content)
                            else:
                                cleaned_style.append(text)
                        if cleaned_style:
                            style_docs = '\n\n---STYLE_EXAMPLE---\n\n'.join(cleaned_style)
                            logger.info(f"[Clinician Review] Retrieved {len(cleaned_style)} style documents")
                
                # Optionally retrieve from KB_Level4_Clinic
                try:
                    clinic_query = "OSA clinical patterns treatment"
                    clinic_result = bedrock_service.query_knowledge_base(
                        query=clinic_query,
                        patient_id=None,
                        max_results=2,
                        knowledge_base_id=bedrock_service.KB_LEVEL4_CLINIC_ID
                    )
                    if clinic_result.get('success'):
                        clinic_texts = clinic_result.get('retrieved_texts', [])
                        if clinic_texts:
                            cleaned_clinic = []
                            for text in clinic_texts:
                                if not text or len(text.strip()) < 100:
                                    continue
                                if '<!--METADATA_END-->' in text:
                                    content = text.split('<!--METADATA_END-->', 1)[1].strip()
                                    if content:
                                        cleaned_clinic.append(content)
                                else:
                                    cleaned_clinic.append(text)
                            if cleaned_clinic:
                                clinic_docs = '\n\n---CLINICAL_EXAMPLE---\n\n'.join(cleaned_clinic)
                except Exception as clinic_exc:
                    logger.warning(f"[Clinician Review] Clinic KB retrieval warning: {clinic_exc}")
                
                if not style_docs:
                    kb_error = "KB_Level4_Style returned no valid documents"
                    
            except Exception as exc:
                kb_error = str(exc)
                logger.error(f"[Clinician Review] Knowledge Base retrieval error: {exc}", exc_info=True)
        else:
            kb_error = "Bedrock service unavailable"
        
        # Build prompts using the EDITED canonical JSON
        system_prompt = _LEVEL4_KB_SYSTEM_PROMPT
        user_prompt = _level4_kb_build_user_prompt(canonical_json, style_docs, clinic_docs)
        
        # Invoke LLM with Bedrock
        try:
            llm_result = _level4_invoke_provider_with_prompts('bedrock', system_prompt, user_prompt, patient_id)
            if 'error' in llm_result:
                logger.error(f"[Clinician Review] LLM invocation failed: {llm_result['error']}")
                return jsonify({'success': False, 'error': llm_result['error']}), 500
        except Exception as exc:
            logger.error(f"[Clinician Review] LLM invocation exception: {exc}", exc_info=True)
            return jsonify({'success': False, 'error': f'LLM invocation failed: {str(exc)}'}), 500
        
        # Save to history
        history_entry = None
        try:
            history_entry = Level4ReportHistory(
                patient_id=patient_id,
                prompt=user_prompt,
                response=llm_result.get('response', ''),
                llm_provider='bedrock',
                model_used=llm_result.get('model'),
                created_by=current_user.id
            )
            db.session.add(history_entry)
            db.session.commit()
            logger.info(f"[Clinician Review] Saved report history ID: {history_entry.id}")
        except Exception as exc:
            logger.error(f"[Clinician Review] Failed to save report history: {exc}")
        
        return jsonify({
            'success': True,
            # Expose prompts for clinician review/debug (UI shows in dedicated tabs)
            'system_prompt': system_prompt,
            'user_prompt': user_prompt,
            'prompt': user_prompt,  # backward compatibility with other UI surfaces
            'response': llm_result.get('response'),
            'model_used': llm_result.get('model'),
            'history_id': history_entry.id if history_entry else None,
            'kb_error': kb_error,
            'style_docs_retrieved': bool(style_docs),
            'clinic_docs_retrieved': bool(clinic_docs)
        })
        
    except Exception as exc:
        logger.error(f"[Clinician Review] Unhandled exception: {exc}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Internal server error: {str(exc)}'
        }), 500


# ============================================================================
# CLINICAL IMAGE UPLOAD ROUTE
# ============================================================================

@reports_files_bp.route('/upload_clinical_image', methods=['POST'])
@login_required
def upload_clinical_image():
    """
    Upload a clinical image (pasted from clipboard) to S3.
    Stores in patients/{patient_id}/imaging/level4-images/
    Returns the S3 URL for display in the report.
    """
    try:
        # Get patient_id from form data
        patient_id = request.form.get('patient_id')
        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id is required'}), 400
        
        # Verify patient exists and user has access
        patient = Patient.query.get(patient_id)
        if not patient:
            return jsonify({'success': False, 'error': 'Patient not found'}), 404
        
        if not current_user.can_access_patient(patient):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Get the uploaded file
        if 'image' not in request.files:
            return jsonify({'success': False, 'error': 'No image file provided'}), 400
        
        image_file = request.files['image']
        if image_file.filename == '':
            return jsonify({'success': False, 'error': 'No image selected'}), 400
        
        # Determine file extension from content type or filename
        content_type = image_file.content_type or 'image/png'
        ext_map = {
            'image/png': 'png',
            'image/jpeg': 'jpg',
            'image/jpg': 'jpg',
            'image/gif': 'gif',
            'image/webp': 'webp'
        }
        ext = ext_map.get(content_type, 'png')
        
        # Generate unique filename with timestamp
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')
        filename = f"clinical_image_{timestamp}.{ext}"
        s3_key = f"patients/{patient_id}/imaging/level4-images/{filename}"
        
        # Read image data
        image_data = image_file.read()
        
        # Upload to S3
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-west-2')
        )
        
        bucket_name = os.getenv('S3_BUCKET_NAME')
        if not bucket_name:
            return jsonify({'success': False, 'error': 'S3_BUCKET_NAME not configured'}), 500
        
        # Upload image to S3 with public-read ACL for direct access
        image_buffer = io.BytesIO(image_data)
        image_buffer.seek(0)
        s3_client.upload_fileobj(
            image_buffer,
            bucket_name,
            s3_key,
            ExtraArgs={
                'ContentType': content_type,
                'ACL': 'public-read'  # Make image publicly accessible
            }
        )
        
        # Generate presigned URL for viewing (lasts 7 days)
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': s3_key},
            ExpiresIn=604800  # 7 days in seconds
        )
        
        logger.info(f"Clinical image uploaded to S3: {s3_key}, Size: {len(image_data)} bytes")
        
        return jsonify({
            'success': True,
            'url': presigned_url,
            's3_key': s3_key,
            'filename': filename
        })
        
    except Exception as exc:
        logger.error(f"Error uploading clinical image: {exc}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Upload failed: {str(exc)}'
        }), 500


# ============================================================================
# REPORT NORMALIZATION ROUTES
# ============================================================================

@reports_files_bp.route('/reports/normalize', methods=['GET'])
@login_required
def normalize_report_page():
    """Render the report normalization UI"""
    return render_template('normalize_report.html')


@reports_files_bp.route('/reports/api/normalize', methods=['POST'])
@login_required
def normalize_report_api():
    """Normalize a raw clinical report into standardized Level-4 format"""
    try:
        data = request.get_json()
        raw_report = data.get('raw_report', '').strip()
        provider = data.get('provider', 'bedrock')
        
        if not raw_report:
            return jsonify({'success': False, 'error': 'raw_report is required'}), 400
        
        # Build messages for LLM
        user_prompt = f"""Normalize the following raw patient report into the Standard Level-4 OSA Report Format defined in the "OSA REPORT NORMALIZATION SPECIFICATION v1.0".

Preserve all real values, insert "Not provided" for missing values, and follow all formatting and wording rules strictly.

RAW REPORT TO NORMALIZE:

{raw_report}

TASK:
Generate a fully normalized Level-4 OSA Report following the exact template structure. Include all mandatory sections even if data is missing."""
        
        messages = [
            {'role': 'system', 'content': _NORMALIZATION_SYSTEM_PROMPT},
            {'role': 'user', 'content': user_prompt}
        ]
        
        # Invoke LLM
        if provider == 'bedrock':
            result = _normalize_invoke_bedrock(messages)
        elif provider == 'openai':
            result = _normalize_invoke_openai(messages)
        elif provider == 'claude':
            result = _normalize_invoke_claude(messages)
        else:
            return jsonify({'success': False, 'error': f'Unknown provider: {provider}'}), 400
        
        if 'error' in result:
            return jsonify({'success': False, 'error': result['error']}), 500
        
        return jsonify({
            'success': True,
            'normalized_report': result.get('response', ''),
            'model_used': result.get('model', ''),
            'system_prompt': _NORMALIZATION_SYSTEM_PROMPT,
            'user_prompt': user_prompt,
        })
    except Exception as exc:
        logger.error('Error normalizing report: %s', exc, exc_info=True)
        return jsonify({'success': False, 'error': str(exc)}), 500


def _normalize_invoke_bedrock(messages):
    """Invoke Bedrock for normalization"""
    service = get_bedrock_service()
    if not service or not service.is_available():
        return {'error': 'Bedrock service unavailable'}
    result = service.invoke_model(
        messages=messages,
        max_tokens=4000,
        temperature=0.1,  # Lower temperature for more consistent normalization
        patient_id=None,  # No patient ID for normalization
        endpoint='reports_normalize',
    )
    if result.get('success'):
        return {'response': result.get('response'), 'model': 'bedrock_claude'}
    return {'error': result.get('error', 'Bedrock call failed')}


def _normalize_invoke_openai(messages):
    """Invoke OpenAI for normalization"""
    if openai is None:
        return {'error': 'openai package not installed'}
    try:
        openai.api_key = _LEVEL4_OPENAI_KEY
        completion = openai.chat.completions.create(
            model=os.getenv('LEVEL4_OPENAI_MODEL', 'gpt-4o'),
            messages=messages,
            temperature=0.1,  # Lower temperature for more consistent normalization
            max_tokens=4000,
        )
        return {'response': completion.choices[0].message.content, 'model': completion.model}
    except Exception as exc:
        current_app.logger.error('OpenAI normalization error: %s', exc)
        return {'error': str(exc)}


def _normalize_invoke_claude(messages):
    """Invoke Claude for normalization"""
    if Anthropic is None:
        return {'error': 'anthropic package not installed'}
    try:
        client = Anthropic(api_key=_LEVEL4_ANTHROPIC_KEY)
        resp = client.messages.create(
            model=os.getenv('LEVEL4_CLAUDE_MODEL', 'claude-3-5-sonnet-20241022-v2:0'),
            max_tokens=4000,
            temperature=0.1,  # Lower temperature for more consistent normalization
            system=messages[0]['content'],
            messages=[{'role': 'user', 'content': messages[1]['content']}],
        )
        text_blocks = [block.text for block in resp.content if getattr(block, 'type', '') == 'text']
        return {'response': '\n'.join(text_blocks), 'model': resp.model}
    except Exception as exc:
        current_app.logger.error('Claude normalization error: %s', exc)
        return {'error': str(exc)}


@reports_files_bp.route('/api/patient/<int:patient_id>/request-level4-report', methods=['POST'])
@login_required
def request_level4_report(patient_id):
    """Send email request for Level 4 Report generation"""
    try:
        # Get patient information
        patient = Patient.query.get_or_404(patient_id)
        patient_name = patient.name or 'Unknown'
        patient_id_val = patient.id
        
        # Get current user name
        user_name = current_user.name if current_user.is_authenticated and hasattr(current_user, 'name') else 'Unknown User'
        
        # Helper function to get patient initials
        def get_patient_initials(name):
            """Extract initials from patient name."""
            if not name or name.lower() in ['unknown', 'test patient', 'patient']:
                return 'N/A'
            parts = name.strip().split()
            if len(parts) == 0:
                return 'N/A'
            elif len(parts) == 1:
                return parts[0][0].upper() + '.' if len(parts[0]) > 0 else 'N/A'
            else:
                # Get first letter of first and last name with periods
                return (parts[0][0] + '.' + parts[-1][0] + '.').upper()
        
        patient_initials = get_patient_initials(patient_name)
        
        # Email recipients
        recipients = [
            'info@vizbriz.com',
            'Jenny@vizbriz.com',
            'Tali@vizbriz.com'
        ]
        
        # Email subject
        subject = f'Request for Report patient {patient_id_val}'
        
        # Email body
        message_body = f'User {user_name} has requested a report be generated for the following patient ({patient_initials} {patient_id_val}).'
        
        # HTML content for email
        html_content = f"""
        <html>
        <body>
            <p>{message_body}</p>
            <p><strong>Patient Details:</strong></p>
            <ul>
                <li>Patient ID: {patient_id_val}</li>
                <li>Patient Initials: {patient_initials}</li>
                <li>Requested by: {user_name}</li>
                <li>Request Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}</li>
            </ul>
        </body>
        </html>
        """
        
        # Send emails to all recipients
        from flask_app.routes.file_management_routes import send_email_with_sendgrid
        
        email_errors = []
        for recipient in recipients:
            try:
                send_email_with_sendgrid(
                    recipient_email=recipient,
                    subject=subject,
                    html_content=html_content,
                    text_content=message_body,
                    patient_id=patient_id_val,
                    sender_id=current_user.id if current_user.is_authenticated and hasattr(current_user, 'id') else None,
                    email_type='level4_report_request',
                    sender_type='system'
                )
                logger.info(f'Level 4 report request email sent to {recipient} for patient {patient_id_val}')
            except Exception as email_error:
                error_msg = f'Failed to send email to {recipient}: {str(email_error)}'
                logger.error(error_msg)
                email_errors.append(error_msg)
        
        if email_errors:
            return jsonify({
                'success': False,
                'message': f'Request sent but some emails failed: {"; ".join(email_errors)}'
            }), 500
        
        return jsonify({
            'success': True,
            'message': 'Level 4 Report request sent successfully'
        })
        
    except Exception as e:
        logger.error(f'Error requesting Level 4 Report for patient {patient_id}: {str(e)}', exc_info=True)
        return jsonify({
            'success': False,
            'message': f'Error sending request: {str(e)}'
        }), 500


# Health check endpoint
@reports_files_bp.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'reports_files',
        'timestamp': datetime.utcnow().isoformat()
    })
