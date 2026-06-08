"""
Annotator structure configuration.

Central place for defining the structures we support and their default manifest
state. Keeping this in one module ensures the Flask routes, conversion scripts,
and segmentation helpers stay in sync (matching the CBCT_0001 reference case).
"""

# Ordered list so UI drop-downs show a consistent sequence.
SUPPORTED_STRUCTURES = [
    "airway",
    "oropharyngeal_airway_space",
    "uvula",  # legacy structure used in CBCT_0001 manifest
    "soft_palate",
    "tongue_base",
    "tongue_body",
    "mandible_outline",
    "lateral_pharyngeal_walls",
    "nasal_airway",
]

# Optional human-friendly labels (UI can fall back to title-casing if missing).
STRUCTURE_LABELS = {
    "airway": "Airway",
    "oropharyngeal_airway_space": "Oropharyngeal Airway",
    "uvula": "Uvula",
    "soft_palate": "Soft Palate",
    "tongue_base": "Tongue Base",
    "tongue_body": "Tongue Body",
    "mandible_outline": "Mandible Outline",
    "lateral_pharyngeal_walls": "Lateral Pharyngeal Walls",
    "nasal_airway": "Nasal Airway",
}


def build_default_structure_state():
    """
    Default manifest entry for a structure.
    """
    return {
        "status": "auto",
        "slices_corrected": [],
        "llm_status": "not_started",
        "llm_processed_slices": [],
        "llm_failed_slices": [],
        "llm_processed_count": 0,
        "llm_failed_count": 0,
    }


def ensure_manifest_structures(manifest: dict) -> dict:
    """
    Ensure manifest['structures'] contains all supported structures with defaults.
    Returns the manifest dict (mutated) for convenience.
    """
    structures = manifest.setdefault("structures", {})
    for struct in SUPPORTED_STRUCTURES:
        if struct not in structures:
            structures[struct] = build_default_structure_state()
        else:
            # Backfill missing fields on legacy entries.
            entry = structures[struct]
            if "status" not in entry:
                entry["status"] = "auto"
            entry.setdefault("slices_corrected", [])
            entry.setdefault("llm_status", "not_started")
            entry.setdefault("llm_processed_slices", [])
            entry.setdefault("llm_failed_slices", [])
            entry.setdefault("llm_processed_count", len(entry["llm_processed_slices"]))
            entry.setdefault("llm_failed_count", len(entry["llm_failed_slices"]))
    return manifest


def get_structure_label(structure: str) -> str:
    """
    Return a display label for the given structure key.
    """
    return STRUCTURE_LABELS.get(structure, structure.replace("_", " ").title())

