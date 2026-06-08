"""
Level 4 Report Generator - Micro-Section Architecture
=====================================================

This module implements a micro-section approach where each report section
is generated independently by the LLM with a focused, minimal prompt.

This approach:
- Eliminates section overlap and content bleed-through
- Provides consistent formatting per section
- Allows independent validation of each section
- Follows industry-standard clinical reporting patterns

Compare with: reports_files_routes.py (single mega-prompt approach)
"""

import os
import io
import json
import logging
import re
import boto3
from datetime import datetime
from flask import Blueprint, request, jsonify
from html import escape

# ReportLab imports for PDF generation
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, Image as RLImage
)

# Use the same Bedrock service as the main report generator
from flask_app.services.bedrock_service import get_bedrock_service

logger = logging.getLogger(__name__)

# Blueprint registration
level4_micro_bp = Blueprint('level4_micro', __name__)


# =============================================================================
# GLOBAL META PROMPT - Shared rules for ALL sections
# =============================================================================

GLOBAL_META_PROMPT = """MASTER PROMPT — LEVEL-4 OSA REPORT GENERATOR

You are Dr. Briz, an AI clinical summarization assistant.
Your role is to generate ONE SECTION of a Level-4 OSA Data Assessment Report using ONLY the canonical JSON provided.

Canonical JSON = sole data source.

==============================================================
################### OUTPUT FORMAT RULES #######################
==============================================================

Output = PLAIN TEXT ONLY

FORBIDDEN (will cause PDF rendering failures):
✘ NO markdown: ##, **, *, -, |, ```, [text](link)
✘ NO LaTeX: $, \\mathrm{}, \\text{}, \\frac{}, ^2, \\%
✘ NO footnotes: [^1], [^0]
✘ NO pipes for tables: | Field | Value |

CORRECT FORMAT:
• Write "28.7 kg/m²" NOT "$28.7 \\mathrm{kg/m^2}$"
• Write "77%" NOT "$77\\%$" or "77 \\%"
• Use "•" for bullets, NOT "-" or "*"
• Use spaces for table alignment, NOT | pipes

Fixed-width monospace formatting:
• Column 1 (labels): 30 chars, left-aligned
• Column 2 (values): 40 chars, left-aligned
• Two spaces between columns
• Max line width = 110 chars

==============================================================
############### CORE NON-NEGOTIABLE DATA RULES ################
==============================================================

1. HALLUCINATION GUARD

If a value is missing from canonical JSON → output "Not provided".
Never infer anatomy, TMJ findings, DISE findings, sleep stages, obstruction patterns, or AHI subtotals.
Never infer treatment success, CPAP intolerance, or mechanical requirements.

2. EXCLUSIVE DATA SOURCE

Allowable fields come ONLY from:
patient.*, clinical_background, complaints, goals, ent_findings, anatomy.*,
sleep_study.*, treatment_history.*, treatment_considerations.*, follow_up_plan.*,
device_design.*, observations.*, position_stats.*

3. DEDUPLICATE CLINICAL BACKGROUND

Canonical clinical_background often contains repeated phrases.
You MUST: split by comma, trim, deduplicate, remove schema artifacts like "heart_disease",
output clean comma-separated list.

4. COMPLAINTS vs SYMPTOMS

Complaints → from complaints array
Goals → from goals array
Symptoms → ONLY from patient_self_report.symptoms where value = true

If no symptom map exists → "Patient Self-Reported Symptoms: Not provided".
Never convert complaints into symptoms. Never convert goals into symptoms.

5. ENT & DISE

ENT Findings: from ent_findings + anatomy.nasal_sinus + nose/sinus terms from clinical_background
DISE Findings: ONLY if observations.dise exists and contains data; otherwise "Not provided"
Never infer collapse degrees or patterns.

6. SLEEP STUDY RULES

Use only sleep_study.* values.
Non-supine AHI: missing & no positional % → "Not provided"; missing & positional % exists → "Not available (slept X% supine)"
REM AHI: If REM AHI = 0 and no REM time exists → "REM AHI of 0 may indicate minimal REM scoring, limited REM sleep, or no REM scoring available."

7. OBSERVATIONS SECTION

Allowed: • OSA severity • oxygen nadir • snoring % • REM AHI interpretation • severity confirmation
Forbidden: ✘ anatomy ✘ ENT ✘ DISE ✘ TMJ ✘ treatment ✘ goals ✘ positional interpretations unless positional % exists

8. STRUCTURAL IMAGING FINDINGS

Use EXACTLY: anatomy.bite_jaw, anatomy.soft_palate, anatomy.tongue_base, anatomy.hyoid, anatomy.arches, anatomy.primary_obstruction_site, anatomy.conclusion
Do NOT include: ✘ nasal/sinus data ✘ ENT data ✘ DISE observations

9. POSSIBLE TREATMENT CONSIDERATIONS

If treatment_considerations exists → use only those.
Otherwise output exactly:
CPAP may support airway stability
Oral appliance therapy may be considered based on anatomy
Nasal/sinus management may support airway patency
Weight management may support improvement

Never mention: ✘ appliance classes ✘ mechanical properties ✘ specific brands

10. DEVICE DESIGN DATA CONSIDERATIONS

Rows MUST always appear: Current Therapy, Pressure Settings, Average Usage, Mandibular Advancement, Vertical Opening, Protrusion Range, Condylar Position, Titration Status
Missing values → "Not provided".

11A. ORAL APPLIANCE THERAPY PATHWAY (GENERAL)

Clinical categories only: Mandibular advancement devices, Combination therapy (MAD + CPAP), TMJ-friendly low-profile appliances, Rigid acrylic appliances (if bruxism goal exists)
NO mechanical design classes here. NO Herbst, dorsal fin, etc.

11B. RECOMMENDED APPLIANCE DESIGN CLASSES

Mechanical design only. Mapping: Retruded mandible → Herbst-style telescopic; Tongue-base obstruction → Herbst or dorsal fin; Narrow arches/high palate → dorsal fin or slim profile; TMJ goals → low-profile or TMJ-friendly; Bruxism goals → reinforced rigid acrylic; Inferior hyoid → designs minimizing vertical opening
Do NOT restate OAT categories or list brands.

12. RECOMMENDATIONS FOR FURTHER EVALUATION

If follow_up_plan exists → use it. Otherwise: Consider ENT evaluation, Consider sleep medicine follow-up, Consider cardiovascular evaluation in severe OSA

13. FINAL DISCLAIMER

Must match the standard disclaimer exactly.

==============================================================

OUTPUT YOUR SECTION CONTENT ONLY. NO EXPLANATIONS. NO PREAMBLE.
"""


# =============================================================================
# SECTION-SPECIFIC PROMPTS - Combined with GLOBAL_META_PROMPT at runtime
# =============================================================================

SECTION_PROMPTS = {
    
    "disclaimer": """SECTION TO GENERATE: Opening Disclaimer

Output EXACTLY this text (no changes, no quotes around it):
DISCLAIMER: This report is generated for clinical reference purposes only and should not replace professional medical judgment. All treatment decisions should be made in consultation with qualified healthcare providers.""",

    "personal_details": """SECTION TO GENERATE: Personal Details

CANONICAL JSON:
{canonical_data}

OUTPUT exactly this format (plain text table, NO headers):
Gender                         [patient.sex or "Not provided"]
Age                            [patient.age] years
BMI                            [patient.bmi] kg/m²
Weight                         [patient.weight_kg] kg
Height                         [patient.height_cm] cm

REMEMBER: Write "28.7 kg/m²" NOT "$28.7 \\mathrm{{kg/m^2}}$". NO LaTeX!""",

    "clinical_background": """SECTION TO GENERATE: Clinical Background, Complaints & Goals

CANONICAL JSON:
{canonical_data}

OUTPUT FORMAT:
Clinical background:
[Deduplicated conditions from clinical_background - remove duplicates like "OSA, severe OSA"]

Patient complaints:
• [complaint 1]
• [complaint 2]
...

Patient goals:
• [goal 1]
• [goal 2]
...

Use • for bullets. NO ** for bold. NO markdown.""",

    "ent_dise_findings": """Generate ONLY the ENT and DISE Findings section.

Use this canonical JSON data:
{canonical_data}

ENT Findings:
- Use ent_findings field
- Include anatomy.nasal_sinus if it describes nasal/sinus issues
- Include sinonasal conditions from clinical_background

DISE Findings:
- Use observations.dise if it exists and has data
- If empty or missing → "DISE Findings: Not provided"

DO NOT include dental, jaw, palate, or airway findings here.
DO NOT invent collapse patterns or grades.

Output plain text only. No markdown.""",

    "sleep_study_data": """SECTION TO GENERATE: Sleep Study Data

CANONICAL JSON:
{canonical_data}

OUTPUT FORMAT (plain text table, NO headers, NO | pipes):
AHI                            [sleep_study.ahi]
RDI                            [sleep_study.rdi]
ODI                            [sleep_study.odi]
Supine AHI                     [sleep_study.supine_ahi]
Non-Supine AHI                 [value or "Not provided"]
REM AHI                        [value, if 0 add "(may indicate minimal REM scoring)"]
Snoring %                      [sleep_study.snoring_pct]%
O2 Nadir                       [sleep_study.o2_nadir]%

Write "77%" NOT "$77\\%$". NO LaTeX symbols!""",

    "observations": """SECTION TO GENERATE: Observations

CANONICAL JSON:
{canonical_data}

OUTPUT 4-6 bullet points using • character:
• [OSA severity based on AHI - e.g., "Severe OSA with AHI of 33.3"]
• [Oxygen nadir finding - e.g., "Oxygen nadir of 77%"]
• [Snoring percentage]
• [REM AHI interpretation]
• [Positional findings if data exists]

FORBIDDEN in this section:
✘ Anatomy findings
✘ ENT findings
✘ Treatment recommendations
✘ Patient goals

Write "77%" NOT "$77\\%$". NO LaTeX!""",

    "structural_observations": """Generate ONLY the Structural Observations from Imaging Data section.

Use this canonical JSON data:
{canonical_data}

Use ONLY these anatomy.* fields:
- bite_jaw
- soft_palate
- tongue_base
- hyoid
- arches
- primary_obstruction_site
- conclusion

Format as a two-column table:
Obstruction Sites              velopharynx, oropharynx, tongue base
Soft Palate & Uvula           elongated soft palate
Tongue Position               large posteriorly positioned tongue
Bite & Jaw                    retruded mandible, increased overjet
Hyoid Bone                    inferiorly positioned
Nasal & Sinus                 [from anatomy.nasal_sinus or "Not provided"]

Then add 1-2 sentence conclusion from anatomy.conclusion.

DO NOT include ENT findings or sinonasal diseases here.
No markdown. Plain text only.""",

    "treatment_considerations": """Generate ONLY the Possible Treatment Considerations section.

Use this canonical JSON data:
{canonical_data}

If treatment_considerations exists in JSON → use those values.

Otherwise output ONLY these 4 neutral statements (one per line, no bullets):
CPAP may support airway stability in severe OSA
Oral appliance therapy may be considered based on anatomy
Nasal/sinus management may support airway patency
Weight management may support improvement

DO NOT mention specific device types or mechanical designs.
No markdown. Plain text only.""",

    "device_design_data": """Generate ONLY the Device Design Data Considerations table.

Use this canonical JSON data:
{canonical_data}

This table MUST have EXACTLY these 8 rows (no more, no less):
1. Current Therapy
2. Pressure Settings
3. Average Usage
4. Mandibular Advancement
5. Vertical Opening
6. Protrusion Range
7. Condylar Position
8. Titration Status

Pull values from treatment_history.* and device_design.* fields.
If a value is missing → output "Not provided".

Format as plain text table:
Current Therapy                CPAP
Pressure Settings              Not provided
Average Usage                  Not provided
Mandibular Advancement         Not provided
Vertical Opening               Not provided
Protrusion Range               Not provided
Condylar Position              Not provided
Titration Status               Not provided

DO NOT add extra rows. DO NOT remove rows. DO NOT change row names.
No markdown.""",

    "oral_appliance_options": """Generate ONLY the Oral Appliance Options for Consideration section.

Use this canonical JSON data:
{canonical_data}

This section answers: "WHY might oral appliances help this patient?"

Include ONLY:
- Clinical rationale based on anatomy
- Why patient's specific anatomy suggests OAT benefit
- TMJ or bruxism goals as rationale (if present in goals)

STRICTLY FORBIDDEN:
✘ Device names (Herbst, dorsal fin, etc.)
✘ Mechanical designs
✘ Treatment pathways or strategies

Format as short statements or "•" bullet list.
No markdown.""",

    "oral_appliance_pathway": """Generate ONLY the Oral Appliance Therapy Pathway section.

Use this canonical JSON data:
{canonical_data}

This section answers: "HOW should oral appliances be used in the treatment plan?"

Include ONLY high-level therapy strategies:
- Mandibular advancement devices (MAD)
- Combination therapy (MAD + CPAP) if applicable
- TMJ-friendly pathway if TMJ goals present
- Positional + MAD (ONLY if position_stats data exists)

STRICTLY FORBIDDEN:
✘ Mechanical device designs (Herbst, dorsal fin, acrylic)
✘ Engineering features
✘ Device specifications

Format as "•" bullet list.
No markdown.""",

    "appliance_design_classes": """Generate ONLY the Recommended Appliance Design Classes section.

Use this canonical JSON data:
{canonical_data}

This section answers: "WHAT mechanical design classes are appropriate?"

Use this mapping based on anatomy findings:
- Retruded mandible → Herbst-style telescopic
- Tongue-base obstruction → Herbst or dorsal fin
- Narrow arches / high palate → slim or dorsal fin
- TMJ goals → low-profile / TMJ-friendly
- Bruxism goals → reinforced rigid acrylic
- Inferior hyoid → avoid large vertical opening designs

Format as table:
Herbst-style telescopic        For retruded mandible and tongue-base obstruction
TMJ-friendly designs           For TMJ area pain goals
Reinforced rigid acrylic       For teeth grinding goals

STRICTLY FORBIDDEN:
✘ Treatment strategy content
✘ Clinical rationale (belongs in Options section)
✘ References to CPAP or therapy pathways

No markdown.""",

    "recommendations": """Generate ONLY the Recommendations for Further Evaluation section.

Use this canonical JSON data:
{canonical_data}

If follow_up_plan exists in JSON → use those values.

Otherwise output these neutral recommendations using "•" bullets:
• Consider ENT evaluation for nasal obstruction if present
• Consider sleep medicine follow-up
• Consider cardiovascular evaluation if severe OSA present

DO NOT include any disclaimer text in this section.
No markdown.""",

    "final_disclaimer": """SECTION TO GENERATE: Final Disclaimer

Output EXACTLY this text (no quotes, no changes):
FINAL DISCLAIMER: This AI-generated report assists in analyzing sleep and anatomical data. It does not replace physician evaluation, DISE assessment, or radiologic interpretation. All findings must be reviewed by a qualified healthcare provider before making clinical decisions."""
}

# Section order for assembly
SECTION_ORDER = [
    "disclaimer",
    "personal_details", 
    "clinical_background",
    "ent_dise_findings",
    "sleep_study_data",
    "observations",
    "structural_observations",
    "treatment_considerations",
    "device_design_data",
    "oral_appliance_options",
    "oral_appliance_pathway",
    "appliance_design_classes",
    "recommendations",
    "final_disclaimer"
]

# Section titles for PDF
SECTION_TITLES = {
    "disclaimer": "DISCLAIMER",
    "personal_details": "Personal Details",
    "clinical_background": "Clinical Background, Complaints & Goals",
    "ent_dise_findings": "ENT and DISE Findings",
    "sleep_study_data": "Sleep Study Data",
    "observations": "Observations",
    "structural_observations": "Structural Observations from Imaging Data",
    "treatment_considerations": "Possible Treatment Considerations",
    "device_design_data": "Device Design Data Considerations",
    "oral_appliance_options": "Oral Appliance Options for Consideration",
    "oral_appliance_pathway": "Oral Appliance Therapy Pathway",
    "appliance_design_classes": "Recommended Appliance Design Classes",
    "recommendations": "Recommendations for Further Evaluation",
    "final_disclaimer": "FINAL DISCLAIMER"
}


# =============================================================================
# LLM SECTION GENERATOR
# =============================================================================

def generate_section(section_name: str, canonical_json: dict, style_guide: str = "") -> str:
    """
    Generate a single report section using GLOBAL_META_PROMPT + style guide + section-specific prompt.
    
    Args:
        section_name: Name of the section to generate
        canonical_json: The patient's canonical data
        style_guide: Condensed style guide from KB examples
        
    Returns:
        Generated section text
    """
    if section_name not in SECTION_PROMPTS:
        logger.error(f"Unknown section: {section_name}")
        return ""
    
    section_prompt_template = SECTION_PROMPTS[section_name]
    
    # Format section prompt with canonical data
    canonical_str = json.dumps(canonical_json, indent=2, default=str)
    section_prompt = section_prompt_template.replace("{canonical_data}", canonical_str)
    
    # COMBINE: Global meta prompt + Style guide (from KB) + Section-specific prompt
    if style_guide:
        full_prompt = f"""{GLOBAL_META_PROMPT}

---

{style_guide}

---

{section_prompt}"""
    else:
        full_prompt = f"""{GLOBAL_META_PROMPT}

---

{section_prompt}"""
    
    try:
        # Use the same Bedrock service as the main report generator
        service = get_bedrock_service()
        if not service or not service.is_available():
            logger.error(f"Bedrock service unavailable for section '{section_name}'")
            return f"[Bedrock service unavailable]"
        
        # Build messages for the service
        messages = [
            {
                "role": "user",
                "content": full_prompt
            }
        ]
        
        # Invoke the model using the service
        result = service.invoke_model(
            messages=messages,
            max_tokens=1000,  # Smaller limit for micro-sections
            temperature=0.1,  # Low temperature for consistency
            patient_id=None,
            endpoint='level4_microsection'
        )
        
        if 'error' in result:
            logger.error(f"Error from Bedrock for section '{section_name}': {result['error']}")
            return f"[Error: {result['error']}]"
        
        generated_text = result.get('response', '')
        
        # Post-process: Remove any remaining LaTeX/markdown that slipped through
        generated_text = _clean_section_output(generated_text)
        
        logger.info(f"Generated section '{section_name}': {len(generated_text)} chars")
        return generated_text.strip()
        
    except Exception as e:
        logger.error(f"Error generating section '{section_name}': {e}", exc_info=True)
        return f"[Error generating {section_name}]"


def _clean_section_output(text: str) -> str:
    """
    Post-process section output to remove any LaTeX/markdown that slipped through.
    """
    # Remove LaTeX math mode
    text = re.sub(r'\$([^$]+)\$', r'\1', text)
    
    # Remove \mathrm{} and \text{}
    text = re.sub(r'\\mathrm\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\text\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\textbf\{([^}]*)\}', r'\1', text)
    
    # Fix kg/m² patterns
    text = re.sub(r'kg\s*/\s*m\s*[\^]?\s*2', 'kg/m²', text)
    text = re.sub(r'kg/m\^2', 'kg/m²', text)
    
    # Remove ~ (LaTeX non-breaking space)
    text = text.replace('~', ' ')
    
    # Remove backslashes before % 
    text = text.replace('\\%', '%')
    
    # Remove markdown bold/italic
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    
    # Remove markdown headers
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    
    # Remove footnote markers
    text = re.sub(r'\[\^?\d+\]:?', '', text)
    
    # Remove markdown table pipes at start/end of lines
    text = re.sub(r'^\s*\|', '', text, flags=re.MULTILINE)
    text = re.sub(r'\|\s*$', '', text, flags=re.MULTILINE)
    
    # Clean up escaped characters
    text = text.replace('\\#', '#')
    text = text.replace('\\|', '|')
    
    # Clean up multiple spaces
    text = re.sub(r'  +', ' ', text)
    
    # Clean up multiple newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()


def retrieve_kb_examples(canonical_json: dict) -> dict:
    """
    Retrieve KB examples ONCE at the start of generation.
    
    Returns:
        Dictionary with 'style_docs' and 'clinic_docs'
    """
    from flask_app.services.bedrock_service import get_bedrock_service
    
    kb_data = {
        'style_docs': '',
        'clinic_docs': '',
        'style_guide': ''
    }
    
    try:
        bedrock_service = get_bedrock_service()
        if not bedrock_service:
            logger.warning("Bedrock service unavailable for KB retrieval")
            return kb_data
        
        # Build retrieval query based on patient data
        query_parts = []
        ahi = canonical_json.get('sleep_study', {}).get('ahi')
        if ahi:
            query_parts.append(f"AHI: {ahi}")
        
        obstruction = canonical_json.get('anatomy', {}).get('primary_obstruction_site')
        if obstruction:
            query_parts.append(f"Obstruction: {obstruction}")
        
        query = "Level-4 OSA Data Assessment Report " + ", ".join(query_parts) if query_parts else "Level-4 OSA Report example"
        
        # Retrieve from KB_Level4_Style
        KB_STYLE_ID = os.getenv('BEDROCK_KB_LEVEL4_STYLE_ID', 'KB_Level4_Style')
        try:
            style_result = bedrock_service.retrieve_from_kb(
                knowledge_base_id=KB_STYLE_ID,
                query=query,
                top_k=3
            )
            if style_result and 'results' in style_result:
                kb_data['style_docs'] = '\n\n'.join([
                    r.get('content', '') for r in style_result['results'][:3]
                ])[:3000]  # Limit to 3000 chars
                logger.info(f"Retrieved {len(kb_data['style_docs'])} chars from KB_Level4_Style")
        except Exception as e:
            logger.warning(f"KB_Level4_Style retrieval failed: {e}")
        
        # Retrieve from KB_Level4_Clinic (optional)
        KB_CLINIC_ID = os.getenv('BEDROCK_KB_LEVEL4_CLINIC_ID', 'KB_Level4_Clinic')
        try:
            clinic_result = bedrock_service.retrieve_from_kb(
                knowledge_base_id=KB_CLINIC_ID,
                query=query,
                top_k=2
            )
            if clinic_result and 'results' in clinic_result:
                kb_data['clinic_docs'] = '\n\n'.join([
                    r.get('content', '') for r in clinic_result['results'][:2]
                ])[:2000]  # Limit to 2000 chars
                logger.info(f"Retrieved {len(kb_data['clinic_docs'])} chars from KB_Level4_Clinic")
        except Exception as e:
            logger.warning(f"KB_Level4_Clinic retrieval failed: {e}")
        
        # Create condensed style guide from KB examples
        kb_data['style_guide'] = create_style_guide(kb_data['style_docs'], kb_data['clinic_docs'])
        
    except Exception as e:
        logger.error(f"KB retrieval error: {e}", exc_info=True)
    
    return kb_data


def create_style_guide(style_docs: str, clinic_docs: str) -> str:
    """
    Create a condensed style guide from KB examples.
    This lightweight guide is passed to each section prompt.
    
    Args:
        style_docs: Raw style examples from KB_Level4_Style (formatting)
        clinic_docs: Raw clinic examples from KB_Level4_Clinic (clinical reasoning)
        
    Returns:
        Condensed style guide (~800 tokens)
    """
    if not style_docs and not clinic_docs:
        return ""
    
    style_guide = """STYLE GUIDE (from KB examples):

=== FORMAT PATTERNS (from KB_Level4_Style) ===
• Tables use fixed-width columns with spaces (not pipes)
• Bullet points use "•" character only
• Section headers are plain text (no markdown ##)
• Values include units: "33.3 events/hour", "77%", "28.7 kg/m²"
• Missing values show "Not provided"
• No LaTeX symbols ($, \\mathrm, etc.)

"""
    
    # Extract key patterns from style_docs
    if style_docs:
        style_guide += "STYLE EXAMPLES:\n"
        
        # Extract a small sample of actual formatting
        lines = style_docs.split('\n')
        sample_lines = []
        for line in lines[:50]:  # Only look at first 50 lines
            line = line.strip()
            if line and len(line) < 80 and not line.startswith('#'):
                sample_lines.append(line)
            if len(sample_lines) >= 8:
                break
        
        if sample_lines:
            style_guide += '\n'.join(sample_lines[:8]) + '\n\n'
    
    # Add clinical examples
    if clinic_docs:
        style_guide += """=== CLINICAL REASONING PATTERNS (from KB_Level4_Clinic) ===
• Observations section: Only sleep metrics, no anatomy
• Structural section: Only imaging findings, no ENT
• OAT Options: WHY appliances help (clinical rationale)
• OAT Pathway: HOW to use appliances (therapy strategy)
• Design Classes: WHAT mechanical designs (Herbst, dorsal fin)

CLINICAL EXAMPLES:\n"""
        
        # Extract clinical reasoning snippets
        lines = clinic_docs.split('\n')
        clinic_samples = []
        
        # Look for key clinical patterns
        keywords = ['OSA', 'AHI', 'obstruction', 'mandible', 'tongue', 'Herbst', 'MAD', 'CPAP']
        for line in lines[:80]:
            line = line.strip()
            if line and len(line) < 100 and any(kw.lower() in line.lower() for kw in keywords):
                clinic_samples.append(line)
            if len(clinic_samples) >= 6:
                break
        
        if clinic_samples:
            style_guide += '\n'.join(clinic_samples[:6]) + '\n\n'
    
    style_guide += """=== APPLY THESE PATTERNS ===
Use the style and clinical patterns above as guidance.
Your output must match the formatting and clinical tone shown in these examples.
"""
    
    # Keep it under 800 tokens (~3200 chars)
    if len(style_guide) > 3200:
        style_guide = style_guide[:3200] + "..."
    
    return style_guide


def generate_all_sections(canonical_json: dict) -> dict:
    """
    Generate all report sections independently.
    
    1. Retrieves KB examples ONCE
    2. Creates condensed style guide
    3. Passes style guide to each section generation
    
    Args:
        canonical_json: The patient's canonical data
        
    Returns:
        Dictionary mapping section names to generated content
    """
    sections = {}
    
    # STEP 1: Retrieve KB examples ONCE (stored in memory)
    logger.info("Retrieving KB examples...")
    kb_data = retrieve_kb_examples(canonical_json)
    style_guide = kb_data.get('style_guide', '')
    
    if style_guide:
        logger.info(f"Created style guide: {len(style_guide)} chars")
    else:
        logger.warning("No style guide created (KB retrieval may have failed)")
    
    # STEP 2: Generate each section with style guide
    for section_name in SECTION_ORDER:
        logger.info(f"Generating section: {section_name}")
        sections[section_name] = generate_section(section_name, canonical_json, style_guide)
    
    return sections


# =============================================================================
# PDF ASSEMBLY
# =============================================================================

def assemble_pdf(sections: dict, patient_id: int, patient_name: str) -> bytes:
    """
    Assemble generated sections into a PDF document.
    
    Args:
        sections: Dictionary of section name -> content
        patient_id: Patient ID
        patient_name: Patient name
        
    Returns:
        PDF content as bytes
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=50,
        leftMargin=50,
        topMargin=50,
        bottomMargin=50
    )
    
    # Styles
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=16,
        spaceAfter=12,
        textColor=colors.HexColor('#1a1a1a'),
        fontName='Helvetica-Bold'
    )
    
    section_style = ParagraphStyle(
        'SectionHeader',
        parent=styles['Heading2'],
        fontSize=12,
        spaceBefore=16,
        spaceAfter=8,
        textColor=colors.HexColor('#1d4ed8'),  # Blue
        fontName='Helvetica-Bold',
        borderWidth=0,
        borderColor=colors.HexColor('#2563eb'),
        borderPadding=4,
        backColor=colors.HexColor('#eff6ff')  # Light blue
    )
    
    body_style = ParagraphStyle(
        'BodyText',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=6,
        textColor=colors.HexColor('#1a1a1a'),
        fontName='Helvetica',
        leading=14
    )
    
    bold_style = ParagraphStyle(
        'BoldText',
        parent=body_style,
        fontName='Helvetica-Bold'
    )
    
    elements = []
    
    # Header with logo
    logo_path = '/home/ec2-user/vizbriz/flask_app/flask_static/images/logos/vizbrizz_logo color without grad.png'
    if os.path.exists(logo_path):
        try:
            logo = RLImage(logo_path, width=1.5*inch, height=0.5*inch)
            elements.append(logo)
            elements.append(Spacer(1, 12))
        except Exception as e:
            logger.warning(f"Could not add logo: {e}")
    
    # Title
    elements.append(Paragraph("VizBriz Level-4 — OSA Data Assessment Report", title_style))
    elements.append(Paragraph(f"Patient: {patient_name} (ID: {patient_id})", body_style))
    elements.append(Paragraph(f"Date: {datetime.now().strftime('%B %d, %Y')}", body_style))
    elements.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#2563eb'), spaceAfter=12))
    
    # Sections that contain tables (need special handling)
    TABLE_SECTIONS = [
        'personal_details', 
        'sleep_study_data', 
        'structural_observations',
        'device_design_data', 
        'appliance_design_classes'
    ]
    
    # Table cell styles
    table_cell_style = ParagraphStyle(
        'TableCell',
        parent=body_style,
        fontSize=9,
        leading=12
    )
    
    def parse_table_content(content):
        """Parse fixed-width table content into rows."""
        rows = []
        for line in content.split('\n'):
            line = line.strip()
            if not line or line.startswith('•'):
                continue
            # Try to split by multiple spaces (fixed-width format)
            # Pattern: "Label                          Value"
            parts = []
            # Find the split point (multiple spaces)
            match = re.match(r'^(.+?)\s{2,}(.+)$', line)
            if match:
                parts = [match.group(1).strip(), match.group(2).strip()]
            else:
                parts = [line, ""]
            if parts[0]:  # Only add if label exists
                rows.append(parts)
        return rows
    
    def create_table_element(rows):
        """Create a ReportLab Table from rows."""
        if not rows:
            return None
        
        table_data = []
        for row in rows:
            label = row[0] if len(row) > 0 else ""
            value = row[1] if len(row) > 1 else ""
            table_data.append([
                Paragraph(label, table_cell_style),
                Paragraph(value, table_cell_style)
            ])
        
        if not table_data:
            return None
        
        # Create table with fixed column widths
        col_widths = [200, 280]  # Label: 200, Value: 280
        table = Table(table_data, colWidths=col_widths)
        table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),  # Bold labels
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),  # Normal values
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#1a1a1a')),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            # Light gray grid
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
            # Alternate row colors
            ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
        ]))
        return table
    
    # Add each section
    for section_name in SECTION_ORDER:
        content = sections.get(section_name, "")
        title = SECTION_TITLES.get(section_name, section_name.replace("_", " ").title())
        
        # Section header
        elements.append(Spacer(1, 12))
        elements.append(Paragraph(title, section_style))
        
        # Section content
        if content:
            # Check if this section contains a table
            if section_name in TABLE_SECTIONS:
                # Parse and render as table
                table_rows = parse_table_content(content)
                if table_rows:
                    table_element = create_table_element(table_rows)
                    if table_element:
                        elements.append(table_element)
                        elements.append(Spacer(1, 8))
                
                # Also add any non-table content (like conclusions)
                for line in content.split('\n'):
                    line = line.strip()
                    if line and not re.match(r'^.+?\s{2,}.+$', line):
                        # This is not a table row, add as paragraph
                        if line.startswith('•'):
                            elements.append(Paragraph(line, body_style))
                        elif ':' in line and len(line) < 50:
                            # Subsection header like "Anatomical Conclusion:"
                            elements.append(Spacer(1, 6))
                            elements.append(Paragraph(f"<b>{line}</b>", bold_style))
                        else:
                            elements.append(Paragraph(line, body_style))
            else:
                # Handle as regular paragraphs/bullets
                for line in content.split('\n'):
                    line = line.strip()
                    if line:
                        # Check for bullet points
                        if line.startswith('•'):
                            elements.append(Paragraph(line, body_style))
                        # Check for FINAL DISCLAIMER - make bold
                        elif 'FINAL DISCLAIMER' in section_name or 'DISCLAIMER' in line.upper():
                            elements.append(Paragraph(f"<b>{line}</b>", bold_style))
                        # Check for subsection headers (ending with :)
                        elif line.endswith(':') and len(line) < 60:
                            elements.append(Spacer(1, 6))
                            elements.append(Paragraph(f"<b>{line}</b>", bold_style))
                        else:
                            elements.append(Paragraph(line, body_style))
        else:
            elements.append(Paragraph("Not provided", body_style))
    
    # Build PDF
    doc.build(elements)
    pdf_content = buffer.getvalue()
    buffer.close()
    
    return pdf_content


# =============================================================================
# ROUTES
# =============================================================================

@level4_micro_bp.route('/api/level4-micro/generate', methods=['POST'])
def generate_micro_report():
    """
    Generate Level-4 report using micro-section approach.
    
    Expected JSON body:
    {
        "patient_id": 71100,
        "canonical_json": { ... }
    }
    """
    try:
        data = request.get_json()
        patient_id = data.get('patient_id')
        canonical_json = data.get('canonical_json', {})
        
        if not patient_id:
            return jsonify({'success': False, 'error': 'patient_id required'}), 400
        
        if not canonical_json:
            return jsonify({'success': False, 'error': 'canonical_json required'}), 400
        
        # Get patient name from canonical
        patient_name = canonical_json.get('patient', {}).get('name', f'Patient {patient_id}')
        
        # Generate all sections
        logger.info(f"Starting micro-section generation for patient {patient_id}")
        sections = generate_all_sections(canonical_json)
        
        # Assemble PDF
        pdf_content = assemble_pdf(sections, patient_id, patient_name)
        
        # Upload to S3
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-west-2')
        )
        
        bucket_name = os.getenv('S3_BUCKET_NAME')
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        pdf_filename = f"Level_4_Micro_Report_Patient_{patient_id}_{timestamp}.pdf"
        pdf_s3_key = f"patients/{patient_id}/reports/{pdf_filename}"
        
        pdf_file = io.BytesIO(pdf_content)
        pdf_file.seek(0)
        s3_client.upload_fileobj(
            pdf_file,
            bucket_name,
            pdf_s3_key,
            ExtraArgs={'ContentType': 'application/pdf'}
        )
        
        logger.info(f"Micro-section report uploaded: {pdf_s3_key}")
        
        return jsonify({
            'success': True,
            'message': 'Micro-section report generated successfully',
            'pdf_filename': pdf_filename,
            'pdf_s3_key': pdf_s3_key,
            'sections_generated': list(sections.keys())
        })
        
    except Exception as e:
        logger.error(f"Error in micro-section generation: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@level4_micro_bp.route('/api/level4-micro/generate-section', methods=['POST'])
def generate_single_section():
    """
    Generate a single section (for testing/debugging).
    
    Expected JSON body:
    {
        "section_name": "sleep_study_data",
        "canonical_json": { ... }
    }
    """
    try:
        data = request.get_json()
        section_name = data.get('section_name')
        canonical_json = data.get('canonical_json', {})
        
        if not section_name:
            return jsonify({'success': False, 'error': 'section_name required'}), 400
        
        if section_name not in SECTION_PROMPTS:
            return jsonify({
                'success': False, 
                'error': f'Unknown section: {section_name}',
                'available_sections': list(SECTION_PROMPTS.keys())
            }), 400
        
        # Generate single section
        content = generate_section(section_name, canonical_json)
        
        return jsonify({
            'success': True,
            'section_name': section_name,
            'content': content,
            'char_count': len(content)
        })
        
    except Exception as e:
        logger.error(f"Error generating section: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@level4_micro_bp.route('/api/level4-micro/sections', methods=['GET'])
def list_sections():
    """List all available micro-sections and their prompts."""
    return jsonify({
        'success': True,
        'sections': SECTION_ORDER,
        'section_titles': SECTION_TITLES,
        'prompts': {k: v[:200] + '...' for k, v in SECTION_PROMPTS.items()}  # Truncated
    })

