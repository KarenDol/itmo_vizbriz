#!/usr/bin/env python3
"""
PHI Redaction Script for S3 Knowledge Base
Removes personally identifiable information from files in s3://vizbrizknowledgebase
"""

import os
import io
import re
import sys
import pathlib
import json
import logging
import time
import gc
import psutil
from typing import Tuple, Iterable
import pdfplumber
from docx import Document
import boto3
from botocore.config import Config

# Lightweight NLP frameworks for PHI detection (replace Presidio)
import spacy
from flair.models import SequenceTagger
from flair.data import Sentence

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('phi_redaction.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ---------- Configuration ----------
S3_KNOWLEDGE_BUCKET = os.environ.get('S3_BUCKET_NAME', 'vizbrizknowledgebase')
S3_INPUT_PREFIX = "records/"  # Scan files in records/ folder
S3_OUTPUT_BUCKET = "vizbrizknowledgebase"  # Same bucket, different prefix
S3_OUTPUT_PREFIX = "redacted/"

# Checkpoint file to track processed files
CHECKPOINT_FILE = "phi_redaction_checkpoint.json"

# Allowed file types for processing
SUPPORTED_EXTENSIONS = {".txt", ".md", ".docx", ".pdf"}

# ---------- Resource Management Configuration ----------
MAX_MEMORY_PERCENT = 75  # Stop processing if memory usage exceeds 75%
MAX_FILE_SIZE_MB = 50    # Skip files larger than 50MB
BATCH_SIZE = 10          # Process files in batches of 10 (increased for speed)
BATCH_DELAY_SECONDS = 1  # Wait 1 second between batches (reduced for speed)
MAX_TEXT_LENGTH = 1000000  # Limit text processing to 1M characters
MEMORY_CHECK_INTERVAL = 20  # Check memory every 20 files (reduced frequency)

# NOTE: Presidio-specific Pattern objects removed. We will rely on
# lightweight NLP (spaCy/Flair) and existing regex redaction below.

# Initialize lightweight NLP frameworks
def initialize_spacy_model():
    try:
        logger.info("Initializing spaCy en_core_web_sm model")
        nlp = spacy.load("en_core_web_sm")
        return nlp
    except Exception as e:
        logger.warning(f"spaCy initialization failed: {e}")
        return None

def initialize_flair_tagger():
    try:
        logger.info("Initializing Flair NER (ner-fast)")
        tagger = SequenceTagger.load('ner-fast')
        return tagger
    except Exception as e:
        logger.warning(f"Flair initialization failed: {e}")
        return None

# Terms that should never be redacted (medical/dental terminology)
ALLOWLIST = {
    "OSA", "CPAP", "MAD", "AHI", "RDI", "ODI", "SpO2", "PSG", "HSAT",
    "apnea", "hypopnea", "snoring", "sleep", "breathing", "airway",
    "mandibular", "advancement", "appliance", "titration", "compliance",
    "AASM", "AADSM", "FDA", "FDA-approved", "FDA cleared",
    "positional", "therapy", "treatment", "diagnosis", "prognosis",
    "polysomnography", "home sleep test", "sleep study", "sleep lab",
    "dentist", "dental", "orthodontist", "periodontist", "oral surgeon",
    "TMJ", "TMD", "bruxism", "clenching", "grinding", "occlusion",
    "overjet", "overbite", "crossbite", "retrusion", "protrusion",
    "interincisal", "opening", "deviation", "clicking", "crepitus",
    "masseter", "temporalis", "pterygoid", "SCM", "palpation",
    "tinnitus", "vertigo", "ear pain", "gag reflex", "sensitivity",
    "wear", "erosion", "abrasion", "attrition", "fracture", "crown",
    "bridge", "implant", "filling", "restoration", "extraction",
    "root canal", "endodontic", "periodontal", "gingivitis", "periodontitis",
    "plaque", "calculus", "tartar", "bleeding", "pocket", "attachment",
    "bone loss", "recession", "graft", "regeneration", "maintenance",
    "hygiene", "brushing", "flossing", "mouthwash", "fluoride",
    "x-ray", "radiograph", "CBCT", "cone beam", "panoramic", "periapical",
    "bitewing", "occlusal", "lateral", "cephalometric", "imaging",
    "scan", "impression", "model", "cast", "wax", "articulator",
    "occlusal", "centric", "eccentric", "lateral", "protrusive",
    "condyle", "fossa", "eminence", "disc", "ligament", "muscle",
    "nerve", "artery", "vein", "lymph", "gland", "saliva", "mucosa",
    "epithelium", "connective", "tissue", "bone", "cartilage", "joint",
    "inflammation", "infection", "abscess", "cellulitis", "swelling",
    "pain", "discomfort", "tenderness", "sensitivity", "numbness",
    "tingling", "burning", "itching", "dryness", "taste", "smell",
    "breath", "halitosis", "xerostomia", "sialorrhea", "dysphagia",
    "dysarthria", "trismus", "lockjaw", "stiffness", "spasm", "cramp",
    "fatigue", "weakness", "paralysis", "palsy", "tremor", "tic",
    "migraine", "headache", "tension", "cluster", "sinus", "allergy",
    "asthma", "COPD", "diabetes", "hypertension", "cardiovascular",
    "medication", "drug", "antibiotic", "analgesic", "anti-inflammatory",
    "steroid", "corticosteroid", "antihistamine", "decongestant",
    "antacid", "proton pump inhibitor", "H2 blocker", "anticoagulant",
    "blood thinner", "aspirin", "ibuprofen", "acetaminophen", "codeine",
    "morphine", "fentanyl", "lidocaine", "novocaine", "epinephrine",
    "adrenaline", "nitrous oxide", "oxygen", "nitrogen", "helium",
    "anesthesia", "sedation", "conscious", "deep", "general", "local",
    "regional", "spinal", "epidural", "intravenous", "intramuscular",
    "subcutaneous", "topical", "oral", "sublingual", "rectal", "vaginal",
    "dose", "dosage", "frequency", "duration", "contraindication",
    "allergy", "adverse", "side effect", "interaction", "toxicity",
    "overdose", "withdrawal", "dependence", "addiction", "tolerance",
    "efficacy", "effectiveness", "safety", "tolerability", "compliance",
    "adherence", "persistence", "discontinuation", "switching", "titration",
    "monitoring", "follow-up", "outcome", "prognosis", "recurrence",
    "remission", "cure", "healing", "recovery", "rehabilitation",
    "therapy", "treatment", "intervention", "procedure", "surgery",
    "operation", "resection", "excision", "incision", "drainage",
    "debridement", "irrigation", "packing", "suturing", "stapling",
    "grafting", "transplantation", "reconstruction", "repair", "revision",
    "replacement", "implantation", "insertion", "removal", "extraction",
    "biopsy", "aspiration", "injection", "infusion", "transfusion",
    "dialysis", "ventilation", "resuscitation", "defibrillation",
    "pacemaker", "stent", "catheter", "tube", "drain", "bag", "pump",
    "monitor", "sensor", "probe", "electrode", "lead", "wire", "cable",
    "connector", "adapter", "filter", "valve", "regulator", "controller",
    "display", "screen", "keyboard", "mouse", "printer", "scanner",
    "camera", "microscope", "endoscope", "laparoscope", "arthroscope",
    "bronchoscope", "colonoscope", "gastroscope", "cystoscope", "hysteroscope",
    "otoscope", "ophthalmoscope", "stethoscope", "sphygmomanometer",
    "thermometer", "pulse oximeter", "glucometer", "sphygmomanometer",
    "electrocardiogram", "ECG", "EKG", "electroencephalogram", "EEG",
    "electromyogram", "EMG", "nerve conduction", "evoked potential",
    "auditory", "visual", "somatosensory", "brainstem", "cortical",
    "subcortical", "cerebellar", "spinal", "peripheral", "autonomic",
    "sympathetic", "parasympathetic", "cholinergic", "adrenergic",
    "dopaminergic", "serotonergic", "GABAergic", "glutamatergic",
    "opioid", "cannabinoid", "endocannabinoid", "neurotransmitter",
    "receptor", "agonist", "antagonist", "partial", "inverse", "allosteric",
    "competitive", "non-competitive", "irreversible", "reversible",
    "binding", "affinity", "efficacy", "potency", "selectivity",
    "specificity", "cross-reactivity", "tolerance", "sensitization",
    "desensitization", "downregulation", "upregulation", "internalization",
    "trafficking", "endocytosis", "exocytosis", "secretion", "release",
    "uptake", "reuptake", "metabolism", "catabolism", "anabolism",
    "synthesis", "degradation", "clearance", "elimination", "excretion",
    "distribution", "absorption", "bioavailability", "pharmacokinetics",
    "pharmacodynamics", "pharmacogenomics", "pharmacogenetics",
    "genotype", "phenotype", "allele", "mutation", "polymorphism",
    "SNP", "CNV", "indel", "translocation", "inversion", "duplication",
    "deletion", "insertion", "substitution", "missense", "nonsense",
    "frameshift", "splice", "promoter", "enhancer", "silencer",
    "transcription", "translation", "replication", "repair", "recombination",
    "chromosome", "chromatin", "histone", "nucleosome", "telomere",
    "centromere", "kinetochore", "spindle", "microtubule", "actin",
    "myosin", "troponin", "tropomyosin", "calmodulin", "calcium",
    "sodium", "potassium", "chloride", "bicarbonate", "phosphate",
    "magnesium", "iron", "zinc", "copper", "selenium", "iodine",
    "vitamin", "mineral", "electrolyte", "osmolarity", "pH", "acidosis",
    "alkalosis", "buffer", "homeostasis", "feedback", "regulation",
    "control", "modulation", "inhibition", "stimulation", "activation",
    "deactivation", "phosphorylation", "dephosphorylation", "acetylation",
    "methylation", "ubiquitination", "sumoylation", "glycosylation",
    "lipidation", "prenylation", "myristoylation", "palmitoylation",
    "farnesylation", "geranylgeranylation", "cholesterol", "sterol",
    "lipid", "fatty acid", "triglyceride", "phospholipid", "sphingolipid",
    "glycolipid", "lipoprotein", "chylomicron", "VLDL", "LDL", "HDL",
    "apolipoprotein", "receptor", "ligand", "binding", "dissociation",
    "equilibrium", "kinetics", "thermodynamics", "entropy", "enthalpy",
    "free energy", "activation energy", "transition state", "intermediate",
    "product", "substrate", "catalyst", "enzyme", "cofactor", "coenzyme",
    "prosthetic group", "allosteric", "cooperative", "competitive",
    "non-competitive", "uncompetitive", "mixed", "irreversible",
    "reversible", "suicide", "mechanism-based", "time-dependent",
    "concentration-dependent", "dose-dependent", "time-independent",
    "concentration-independent", "dose-independent", "linear", "non-linear",
    "saturable", "non-saturable", "first-order", "zero-order", "mixed-order",
    "Michaelis-Menten", "Hill", "Langmuir", "Freundlich", "BET",
    "adsorption", "desorption", "absorption", "desorption", "partition",
    "distribution", "extraction", "purification", "isolation", "separation",
    "chromatography", "electrophoresis", "centrifugation", "filtration",
    "dialysis", "ultrafiltration", "nanofiltration", "reverse osmosis",
    "distillation", "crystallization", "precipitation", "flocculation",
    "coagulation", "sedimentation", "flotation", "extraction", "leaching",
    "elution", "washing", "drying", "freeze-drying", "lyophilization",
    "spray drying", "fluidized bed", "rotary", "tray", "tunnel", "belt",
    "conveyor", "pneumatic", "mechanical", "thermal", "electrical",
    "magnetic", "optical", "acoustic", "ultrasonic", "microwave",
    "radiofrequency", "infrared", "ultraviolet", "visible", "laser",
    "plasma", "ionization", "mass spectrometry", "nuclear magnetic resonance",
    "electron paramagnetic resonance", "X-ray diffraction", "crystallography",
    "spectroscopy", "spectrophotometry", "fluorometry", "chemiluminescence",
    "bioluminescence", "electrochemiluminescence", "radioimmunoassay",
    "enzyme-linked immunosorbent assay", "ELISA", "western blot",
    "northern blot", "southern blot", "dot blot", "slot blot", "colony",
    "plaque", "hybridization", "polymerase chain reaction", "PCR",
    "real-time PCR", "quantitative PCR", "qPCR", "RT-PCR", "nested PCR",
    "multiplex PCR", "long PCR", "hot start PCR", "touchdown PCR",
    "gradient PCR", "asymmetric PCR", "inverse PCR", "anchored PCR",
    "cassette PCR", "vectorette PCR", "panhandle PCR", "booster PCR",
    "ligation-mediated PCR", "methylation-specific PCR", "MSP",
    "bisulfite sequencing", "pyrosequencing", "Sanger sequencing",
    "next-generation sequencing", "NGS", "whole genome sequencing",
    "WGS", "whole exome sequencing", "WES", "targeted sequencing",
    "RNA sequencing", "RNA-seq", "ChIP sequencing", "ChIP-seq",
    "ATAC sequencing", "ATAC-seq", "Hi-C", "3C", "4C", "5C",
    "single-cell sequencing", "scRNA-seq", "scATAC-seq", "scChIP-seq",
    "spatial transcriptomics", "spatial genomics", "spatial proteomics",
    "spatial metabolomics", "multi-omics", "integrated omics",
    "systems biology", "network biology", "pathway analysis",
    "gene set enrichment", "GSEA", "overrepresentation", "ORA",
    "functional enrichment", "GO", "KEGG", "Reactome", "WikiPathways",
    "BioCyc", "MetaCyc", "HumanCyc", "EcoCyc", "AraCyc", "SoyCyc",
    "RiceCyc", "MaizeCyc", "SorghumCyc", "PoplarCyc", "GrapeCyc",
    "TomatoCyc", "PotatoCyc", "ChlamyCyc", "YeastCyc", "PseudoCyc",
    "BsubCyc", "EcoliCyc", "BacillusCyc", "LactoCyc", "StrepCyc",
    "MycoCyc", "TuberCyc", "CoryneCyc", "RhizoCyc", "AgroCyc",
    "XanthoCyc", "BurkCyc", "RalstoCyc", "PseudoCyc", "AcinetoCyc",
    "MethyloCyc", "MethanoCyc", "ArchaeoCyc", "SulfolobusCyc",
    "PyrococcusCyc", "ThermoplasmaCyc", "FerroplasmaCyc", "PicroCyc",
    "HalobacteriumCyc", "HaloferaxCyc", "NatronomonasCyc", "HaloarculaCyc",
    "HalorubrumCyc", "HalogeometricumCyc", "HalobaculumCyc", "HaloterrigenaCyc",
    "NatrialbaCyc", "NatronobacteriumCyc", "NatronococcusCyc", "NatronolimnobiusCyc",
    "NatronorubrumCyc", "NatronospiraCyc", "NatronotaleaCyc", "NatronovirgaCyc",
    "NatronolimnobiusCyc", "NatronobacteriumCyc", "NatronococcusCyc", "NatronolimnobiusCyc",
    "NatronorubrumCyc", "NatronospiraCyc", "NatronotaleaCyc", "NatronovirgaCyc"
}

# Resource monitoring functions
def check_memory_usage():
    """Check current memory usage and return percentage"""
    try:
        memory = psutil.virtual_memory()
        return memory.percent
    except Exception as e:
        logger.warning(f"Could not check memory usage: {e}")
        return 0

def check_file_size_limit(s3_client, bucket: str, key: str) -> bool:
    """Check if file size is within processing limits"""
    try:
        response = s3_client.head_object(Bucket=bucket, Key=key)
        file_size_mb = response['ContentLength'] / (1024 * 1024)
        
        if file_size_mb > MAX_FILE_SIZE_MB:
            logger.warning(f"Skipping large file ({file_size_mb:.1f}MB): {key}")
            return False
        return True
    except Exception as e:
        logger.error(f"Error checking file size for {key}: {e}")
        return False

def cleanup_memory():
    """Force garbage collection to free memory"""
    gc.collect()

def load_checkpoint():
    """Load the checkpoint file to see which files have been processed"""
    try:
        if os.path.exists(CHECKPOINT_FILE):
            with open(CHECKPOINT_FILE, 'r') as f:
                checkpoint_data = json.load(f)
                logger.info(f"Loaded checkpoint with {len(checkpoint_data.get('processed_files', []))} processed files")
                return checkpoint_data
        else:
            logger.info("No checkpoint file found, starting fresh")
            return {"processed_files": [], "last_run": None}
    except Exception as e:
        logger.warning(f"Error loading checkpoint: {e}, starting fresh")
        return {"processed_files": [], "last_run": None}

def save_checkpoint(processed_files, last_run_time):
    """Save the checkpoint file with processed files"""
    try:
        checkpoint_data = {
            "processed_files": processed_files,
            "last_run": last_run_time,
            "total_processed": len(processed_files)
        }
        with open(CHECKPOINT_FILE, 'w') as f:
            json.dump(checkpoint_data, f, indent=2)
        logger.info(f"Checkpoint saved with {len(processed_files)} processed files")
    except Exception as e:
        logger.error(f"Error saving checkpoint: {e}")

def is_file_already_processed(file_key, processed_files):
    """Check if a file has already been processed"""
    return file_key in processed_files

def add_to_processed_files(file_key, processed_files):
    """Add a file to the processed files list"""
    if file_key not in processed_files:
        processed_files.append(file_key)

def truncate_text_if_needed(text: str) -> str:
    """Truncate text if it's too long to prevent memory issues"""
    if len(text) > MAX_TEXT_LENGTH:
        logger.warning(f"Text truncated from {len(text)} to {MAX_TEXT_LENGTH} characters")
        return text[:MAX_TEXT_LENGTH] + "\n\n[TEXT TRUNCATED DUE TO SIZE LIMITS]"
    return text

# Initialize S3 client
def initialize_s3():
    """Initialize S3 client using the same configuration as the app"""
    try:
        region = os.environ.get('AWS_REGION', 'us-west-2')
        s3_client = boto3.client('s3', region_name=region, config=Config(signature_version='s3v4'))
        
        # Test connection
        s3_client.head_bucket(Bucket=S3_KNOWLEDGE_BUCKET)
        logger.info(f"S3 client initialized successfully for bucket: {S3_KNOWLEDGE_BUCKET}")
        return s3_client
        
    except Exception as e:
        logger.error(f"Failed to initialize S3 client: {e}")
        raise

def redact_text_regex_only(text: str) -> str:
    """Fallback PHI redaction using only regex patterns"""
    try:
        # Protect allowlisted terms by replacing with placeholders
        placeholders = {}
        for term in ALLOWLIST:
            key = f"__ALLOW_{hash(term) % 1000000}__"
            placeholders[key] = term
            text = re.sub(rf"\b{re.escape(term)}\b", key, text, flags=re.IGNORECASE)
        
        # Apply enhanced regex patterns for PHI detection
        redacted_text = text
        
        # First, preserve age references by temporarily replacing them
        age_placeholders = {}
        age_patterns = [
            # Common age formats
            r'\b(\d{1,3})\s*(?:yo|y\.o\.|year[s]?\s*old|yr[s]?\s*old)\b',  # "62yo", "62 years old"
            r'\b(?:age|aged?)\s*:?\s*(\d{1,3})\b',  # "age: 62", "aged 62"
            r'\((\d{1,3})yo\b',  # "(62yo"
            # Additional age patterns for better coverage
            r'\b(\d{1,3})\s*years?\s*of\s*age\b',  # "62 years of age"
            r'\b(\d{1,3})\s*y\.o\.\b',  # "62 y.o."
            r'\b(\d{1,3})\s*yrs?\s*old\b',  # "62 yrs old"
            r'\b(\d{1,3})\s*years?\b(?=\s*(?:old|of\s*age|male|female|patient))',  # "62 years" followed by context
            r'\b(?:patient|pt)\.?\s*(\d{1,3})\b',  # "patient 62" or "pt. 62"
            r'\b(\d{1,3})\s*(?:year|yr)\.?\s*old\b',  # "62 year old" or "62 yr. old"
            r'\b(\d{1,3})\s*yo\b',  # "62 yo" (standalone)
            r'\b(\d{1,3})\s*years?\s*old\b',  # "62 years old"
            r'\b(?:age|aged)\s*(\d{1,3})\b',  # "age 62" or "aged 62"
            r'\b(\d{1,3})\s*(?:year|yr)s?\s*of\s*age\b',  # "62 years of age"
        ]
        
        age_counter = 0
        for pattern in age_patterns:
            matches = re.finditer(pattern, redacted_text, flags=re.IGNORECASE)
            for match in matches:
                age_value = match.group(1)
                if 0 <= int(age_value) <= 120:  # Reasonable age range
                    age_counter += 1
                    placeholder = f"__AGE_PRESERVE_{age_counter}__"
                    age_placeholders[placeholder] = match.group(0)
                    redacted_text = redacted_text.replace(match.group(0), placeholder, 1)
        
        # Patient names in formal format (LASTNAME, Firstname)
        redacted_text = re.sub(r'\b[A-Z]{2,20},\s+[A-Z][a-z]{2,20}(?:\s+[A-Z]\.?)?\b', '[PATIENT_NAME]', redacted_text)
        
        # Patient ID numbers (like "id #1589")
        redacted_text = re.sub(r'\b(?:id|ID|patient\s*(?:id|ID))\s*#?\s*:?\s*\d{3,}\b', '[PATIENT_ID]', redacted_text, flags=re.IGNORECASE)
        
        # Provider names - split into separate patterns for better matching
        # Pattern 1: Name followed by DDS/MD credentials
        redacted_text = re.sub(r'\b[A-Z][A-Z\s]+,?\s*(?:DDS|MD|NP|PA)\b', '[PROVIDER_NAME]', redacted_text)
        # Pattern 2: Dr. followed by name
        redacted_text = re.sub(r'\bDr\.?\s+[A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b', '[PROVIDER_NAME]', redacted_text)
        
        # Complete street addresses with ZIP codes
        redacted_text = re.sub(r'\b\d{1,5}\s+[A-Za-z0-9.\'\-\s]+\s+(?:Street|St|Road|Rd|Ave|Avenue|Blvd|Boulevard|Dr|Drive|Lane|Ln|Court|Ct|Way|Place|Pl)(?:\s+(?:Apt|Suite|Unit|#)\s*[A-Za-z0-9\-]+)?(?:,?\s*[A-Za-z\s]+,?\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?)?', '[ADDRESS]', redacted_text, flags=re.IGNORECASE)
        
        # ZIP codes (standalone)
        redacted_text = re.sub(r'\b\d{5}(?:-\d{4})?\b', '[ZIP_CODE]', redacted_text)
        
        # Phone numbers
        redacted_text = re.sub(r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b', '[PHONE_NUMBER]', redacted_text)
        
        # Email addresses
        redacted_text = re.sub(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b', '[EMAIL_ADDRESS]', redacted_text)
        
        # NPI numbers (must be exactly 10 digits and preceded by NPI)
        redacted_text = re.sub(r'\bNPI\s*:?\s*\d{10}\b', '[NPI_NUMBER]', redacted_text, flags=re.IGNORECASE)
        
        # Insurance policy numbers and group numbers
        redacted_text = re.sub(r'\b(?:Policy|Group|Member|Subscriber|Insurance)\s*(?:ID|No\.?|Number|#)\s*[:#]?\s*[A-Z0-9\-]{6,}\b', '[INSURANCE_POLICY]', redacted_text, flags=re.IGNORECASE)
        
        # Standalone insurance/policy numbers (like BHP837801715)
        redacted_text = re.sub(r'\b[A-Z]{2,4}\d{6,12}\b', '[INSURANCE_NUMBER]', redacted_text)
        
        # SSN
        redacted_text = re.sub(r'\b\d{3}-?\d{2}-?\d{4}\b', '[SSN]', redacted_text)
        
        # Credit card numbers
        redacted_text = re.sub(r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b', '[CREDIT_CARD]', redacted_text)
        
        # Medical record numbers
        redacted_text = re.sub(r'\b(MRN|Med(?:ical)?\s*Record|Patient\s*ID)\s*#?:?\s*[A-Z0-9\-]{6,}\b', '[MEDICAL_RECORD]', redacted_text, flags=re.IGNORECASE)
        
        # Dental/medical specific IDs
        redacted_text = re.sub(r'\b(Dental\s*ID|Patient\s*#|Chart\s*#|File\s*#)\s*[:#]?\s*[A-Z0-9\-]{4,}\b', '[MEDICAL_ID]', redacted_text, flags=re.IGNORECASE)
        
        # Dates (enhanced pattern)
        redacted_text = re.sub(r'\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})\b', '[DATE]', redacted_text, flags=re.IGNORECASE)
        
        # Restore allowlisted terms
        for key, term in placeholders.items():
            redacted_text = redacted_text.replace(key, term)
        
        # Restore age references (these should be preserved)
        for placeholder, age_text in age_placeholders.items():
            redacted_text = redacted_text.replace(placeholder, age_text)
        
        return redacted_text
        
    except Exception as e:
        logger.error(f"Error in regex redaction: {e}")
        return text

def redact_with_spacy(text: str, nlp) -> str:
    """Redact PHI using spaCy NER (lightweight)."""
    if nlp is None or not text:
        return text
    try:
        # Limit text length for processing safety
        truncated = text if len(text) <= 10000 else text[:10000]
        doc = nlp(truncated)
        redacted = truncated
        for ent in doc.ents:
            if ent.label_ in ['PERSON', 'GPE', 'ORG', 'DATE', 'TIME']:
                redacted = redacted.replace(ent.text, f'[SPACY_{ent.label_}]')
        # If truncated, append remainder unchanged
        return redacted + (text[10000:] if len(text) > 10000 else '')
    except Exception as e:
        logger.warning(f"spaCy redaction failed: {e}")
        return text

def redact_with_flair(text: str, tagger) -> str:
    """Redact PHI using Flair NER (fast model)."""
    if tagger is None or not text:
        return text
    try:
        truncated = text if len(text) <= 5000 else text[:5000]
        sentence = Sentence(truncated)
        tagger.predict(sentence)
        redacted = truncated
        for entity in sentence.get_spans('ner'):
            if entity.tag in ['PER', 'LOC', 'ORG']:
                redacted = redacted.replace(entity.text, f'[FLAIR_{entity.tag}]')
        return redacted + (text[5000:] if len(text) > 5000 else '')
    except Exception as e:
        logger.warning(f"Flair redaction failed: {e}")
        return text

def redact_text(text: str, nlp, tagger) -> str:
    """Sequential redaction: spaCy -> Flair -> Regex."""
    if not text:
        return text
    # Protect allowlisted terms first using regex-only helper
    redacted = redact_text_regex_only(text)  # start with regex pass for strong identifiers
    # Then apply spaCy and Flair for names/locations/organizations/dates
    redacted = redact_with_spacy(redacted, nlp)
    redacted = redact_with_flair(redacted, tagger)
    return redacted

def extract_text_from_file(file_content: bytes, file_extension: str) -> str:
    """Extract text from different file types"""
    try:
        if file_extension in {".txt", ".md"}:
            return file_content.decode("utf-8", errors="ignore")
        
        elif file_extension == ".docx":
            # Write to temporary file and read with python-docx
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp_file:
                tmp_file.write(file_content)
                tmp_file.flush()
                
                doc = Document(tmp_file.name)
                text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
                
                # Clean up temp file
                os.unlink(tmp_file.name)
                return text
        
        elif file_extension == ".pdf":
            # Write to temporary file and read with pdfplumber
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
                tmp_file.write(file_content)
                tmp_file.flush()
                
                text_chunks = []
                with pdfplumber.open(tmp_file.name) as pdf:
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text_chunks.append(page_text)
                
                # Clean up temp file
                os.unlink(tmp_file.name)
                return "\n\n".join(text_chunks)
        
        else:
            logger.warning(f"Unsupported file extension: {file_extension}")
            return ""
            
    except Exception as e:
        logger.error(f"Error extracting text from {file_extension} file: {e}")
        return ""

def process_s3_file(s3_client, bucket: str, key: str, nlp, tagger) -> Tuple[bool, str]:
    """Process a single file from S3 with resource management"""
    try:
        # Check memory usage before processing
        memory_percent = check_memory_usage()
        if memory_percent > MAX_MEMORY_PERCENT:
            logger.error(f"Memory usage too high ({memory_percent:.1f}%), stopping processing")
            return False, key
        
        # Get file extension
        file_extension = pathlib.Path(key).suffix.lower()
        if file_extension not in SUPPORTED_EXTENSIONS:
            logger.info(f"Skipping unsupported file type: {file_extension}")
            return False, key
        
        # Check file size limits
        if not check_file_size_limit(s3_client, bucket, key):
            return False, key
        
        # Create output key - preserve path structure to avoid naming conflicts
        # Replace slashes with underscores to create unique filenames
        safe_key = key.replace('/', '_').replace('\\', '_')
        base_name = pathlib.Path(safe_key).stem
        output_key = f"{S3_OUTPUT_PREFIX}{base_name}.md"
        
        # Download file content
        response = s3_client.get_object(Bucket=bucket, Key=key)
        file_content = response['Body'].read()
        
        # Extract text
        original_text = extract_text_from_file(file_content, file_extension)
        if not original_text or not original_text.strip():
            logger.warning(f"No text content found in: {key}")
            return False, key
        
        # Truncate text if too long to prevent memory issues
        original_text = truncate_text_if_needed(original_text)
        
        # Redact PHI (sequential, resource-light)
        redacted_text = redact_text(original_text, nlp, tagger)
        
        # Clean up memory after processing
        del original_text
        cleanup_memory()
        
        # Always save the file, even if no PHI was found
        # This helps with tracking what's been processed
        
        # Upload redacted content
        s3_client.put_object(
            Bucket=S3_OUTPUT_BUCKET,
            Key=output_key,
            Body=redacted_text.encode('utf-8'),
            ContentType='text/markdown',
            Metadata={
                'original-file': key,
                'redaction-date': str(pathlib.Path().cwd()),
                'phi-redacted': 'true'
            }
        )
        
        logger.info(f"Processed and saved: s3://{S3_OUTPUT_BUCKET}/{output_key}")
        return True, key
        
    except Exception as e:
        logger.error(f"Error processing {key}: {e}")
        return False, key

def scan_and_process_s3_bucket(s3_client, bucket: str, nlp, tagger):
    """Scan S3 bucket and process all supported files with checkpoint support"""
    try:
        logger.info(f"Starting scan of bucket: {bucket}")
        
        # Load checkpoint to see what's already been processed
        checkpoint_data = load_checkpoint()
        processed_files = checkpoint_data.get('processed_files', [])
        already_processed = set(processed_files)
        
        processed_count = 0
        error_count = 0
        skipped_count = 0
        total_files = 0
        new_processed_files = list(processed_files)  # Copy the list
        
        # First, count total files to process
        paginator = s3_client.get_paginator('list_objects_v2')
        logger.info("Counting files to process...")
        
        files_to_process = []
        for page in paginator.paginate(Bucket=bucket, Prefix=S3_INPUT_PREFIX):
            if 'Contents' not in page:
                continue
                
            for obj in page['Contents']:
                key = obj['Key']
                
                # Skip files in the redacted folder
                if key.startswith(S3_OUTPUT_PREFIX):
                    continue
                
                # Skip directories
                if key.endswith('/'):
                    continue
                
                # Check if it's a supported file type
                file_extension = pathlib.Path(key).suffix.lower()
                if file_extension in SUPPORTED_EXTENSIONS:
                    files_to_process.append(key)
                    total_files += 1
        
        logger.info(f"Found {total_files} files to process")
        logger.info(f"Already processed: {len(already_processed)} files")
        
        # Process files with batch processing and memory monitoring
        for i, key in enumerate(files_to_process, 1):
            try:
                # Skip if already processed
                if is_file_already_processed(key, already_processed):
                    logger.info(f"⏭ Skipping already processed file {i}/{total_files}: {key}")
                    skipped_count += 1
                    continue
                
                # Check memory usage every MEMORY_CHECK_INTERVAL files
                if i % MEMORY_CHECK_INTERVAL == 0:
                    memory_percent = check_memory_usage()
                    logger.info(f"Memory usage: {memory_percent:.1f}%")
                    
                    if memory_percent > MAX_MEMORY_PERCENT:
                        logger.error(f"Memory usage too high ({memory_percent:.1f}%), stopping processing")
                        break
                
                logger.info(f"Processing file {i}/{total_files}: {key}")
                
                success, file_key = process_s3_file(s3_client, bucket, key, nlp, tagger)
                if success:
                    processed_count += 1
                    add_to_processed_files(file_key, new_processed_files)
                    logger.info(f"✓ Successfully processed {key}")
                else:
                    skipped_count += 1
                    logger.info(f"⚠ Skipped {key}")
                    
                # Save checkpoint every 5 files to prevent data loss
                if i % 5 == 0:
                    save_checkpoint(new_processed_files, time.strftime('%Y-%m-%d %H:%M:%S'))
                    
                # Batch processing with delays to reduce system load
                if i % BATCH_SIZE == 0:
                    logger.info(f"Batch completed. Waiting {BATCH_DELAY_SECONDS}s before next batch...")
                    time.sleep(BATCH_DELAY_SECONDS)
                    cleanup_memory()
                    
                # Log progress every 10 files
                if i % 10 == 0:
                    logger.info(f"Progress: {i}/{total_files} files processed ({(i/total_files)*100:.1f}%)")
                    
            except Exception as e:
                error_count += 1
                logger.error(f"✗ Error processing {key}: {e}")
                cleanup_memory()  # Clean up memory after errors
                continue
        
        # Final checkpoint save
        save_checkpoint(new_processed_files, time.strftime('%Y-%m-%d %H:%M:%S'))
        
        logger.info(f"Processing complete:")
        logger.info(f"  - Total files found: {total_files}")
        logger.info(f"  - Files processed: {processed_count}")
        logger.info(f"  - Files skipped: {skipped_count}")
        logger.info(f"  - Files with errors: {error_count}")
        logger.info(f"  - Total processed (including previous runs): {len(new_processed_files)}")
        
    except Exception as e:
        logger.error(f"Error scanning S3 bucket: {e}")
        raise

def main():
    """Main function"""
    try:
        logger.info("Starting PHI redaction script")
        
        # Initialize NLP frameworks (lightweight)
        logger.info("Initializing spaCy and Flair (lightweight frameworks)...")
        nlp = initialize_spacy_model()
        tagger = initialize_flair_tagger()
        
        logger.info("Initializing S3 client...")
        s3_client = initialize_s3()
        
        # Process files
        logger.info("Starting file processing...")
        scan_and_process_s3_bucket(s3_client, S3_KNOWLEDGE_BUCKET, nlp, tagger)
        
        logger.info("PHI redaction script completed successfully")
        
    except Exception as e:
        logger.error(f"Script failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
