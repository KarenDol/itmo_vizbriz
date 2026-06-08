from __future__ import annotations

import json
import logging
import os
from typing import Any

from flask import abort, current_app, redirect, render_template, send_file, url_for
from flask_login import login_required

logger = logging.getLogger(__name__)


@login_required
def download_imaging_protocol(protocol_key: str) -> Any:
    """Serve imaging protocol PDFs from Documentation/english/Imaging protocols, else static fallback."""
    protocol_files = {
        "intraoral": "Intraoral Scanning Instructions for Oral Appliance (Obstructive Sleep Apnea & Neuromuscular Orthotic) (1).pdf",
        "clinical": "Protocol clinical pictures (2) (1) (1).pdf",
        "cbct": "Vizbriz CBCT Updated Protocol.docx (1).pdf",
    }

    filename = protocol_files.get((protocol_key or "").lower())
    if not filename:
        logger.warning("Requested unknown imaging protocol: %s", protocol_key)
        return abort(404)

    # Same Documentation folder as Hebrew docs (DOCUMENTATION_DIR on server)
    docs_dir = _documentation_base_dir()
    english_imaging_dir = os.path.join(docs_dir, "english", "Imaging protocols")
    file_path = os.path.join(english_imaging_dir, filename)
    if not os.path.exists(file_path):
        # Fallback: legacy static path (in repo, so works without Documentation)
        static_dir = os.path.join(current_app.root_path, "static", "fwdvizbrizimagingprotocols")
        file_path = os.path.join(static_dir, filename)
    if not os.path.exists(file_path):
        logger.error("Imaging protocol file not found: %s", file_path)
        return abort(404)

    return send_file(file_path, mimetype="application/pdf")


@login_required
def documentation() -> Any:
    """Documentation hub page."""
    try:
        docs_dir = _documentation_base_dir()
        manifest_path = os.path.join(docs_dir, "documentation_manifest.json")
        if os.path.exists(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as f:
                doc_manifest = json.load(f) or {}
        else:
            doc_manifest = {"english": {}, "hebrew": {}, "hidden_system": {"english": {}, "hebrew": {}}}
    except Exception as e:
        logger.warning("Failed to load documentation manifest: %s", e)
        doc_manifest = {"english": {}, "hebrew": {}, "hidden_system": {"english": {}, "hebrew": {}}}

    doc_manifest.setdefault("english", {})
    doc_manifest.setdefault("hebrew", {})
    doc_manifest.setdefault("hidden_system", {"english": {}, "hebrew": {}})

    # System items (can be hidden via hidden_system)
    hidden_eng = (doc_manifest.get("hidden_system") or {}).get("english", {})
    hidden_heb = (doc_manifest.get("hidden_system") or {}).get("hebrew", {})
    hidden_imaging = set(hidden_eng.get("Imaging protocols") or [])
    hidden_patient = set(hidden_heb.get("הוראות למטופל") or [])

    system_imaging_items = []
    if "intraoral" not in hidden_imaging:
        system_imaging_items.append(
            {"title": "Intraoral protocol", "url": url_for("main.download_imaging_protocol", protocol_key="intraoral")}
        )
    if "clinical" not in hidden_imaging:
        system_imaging_items.append(
            {"title": "Clinical photos protocol", "url": url_for("main.download_imaging_protocol", protocol_key="clinical")}
        )
    if "cbct" not in hidden_imaging:
        system_imaging_items.append(
            {"title": "CBCT protocol", "url": url_for("main.download_imaging_protocol", protocol_key="cbct")}
        )

    system_patient_items = []
    if "oral_appliance_care" not in hidden_patient:
        system_patient_items.append(
            {
                "title": "הוראות למטופל - טיפול ושימוש בהתקן האוראלי",
                "url": url_for("main.download_patient_document", doc_key="oral_appliance_care"),
            }
        )
    if "post_delivery_instructions" not in hidden_patient:
        system_patient_items.append(
            {
                "title": "הנחיות לאחר קבלת התקן אורלי לטיפול בדום נשימה חסימתי בשינה",
                "url": url_for("main.download_patient_document", doc_key="post_delivery_instructions"),
            }
        )
    if "informed_consent_sleep_related_breathing" not in hidden_patient:
        system_patient_items.append(
            {
                "title": "הסכמה מדעת לטיפול בהפרעות נשימה הקשורות לשינה",
                "url": url_for("main.download_patient_document", doc_key="informed_consent_sleep_related_breathing"),
            }
        )

    # Merge system items + uploaded extras
    english_section = doc_manifest.get("english") or {}
    hebrew_section = doc_manifest.get("hebrew") or {}

    # Build English section - include all folders, not just "Imaging protocols"
    doc_manifest_render_english: dict[str, list[dict[str, Any]]] = {}
    for folder_name, folder_files in english_section.items():
        if folder_name == "Imaging protocols":
            # Merge system items with uploaded files for Imaging protocols
            doc_manifest_render_english[folder_name] = system_imaging_items + (folder_files or [])
        else:
            # Include all other folders as-is
            doc_manifest_render_english[folder_name] = folder_files or []

    # Build Hebrew section - include all folders, not just "הוראות למטופל"
    doc_manifest_render_hebrew: dict[str, list[dict[str, Any]]] = {}
    for folder_name, folder_files in hebrew_section.items():
        if folder_name == "הוראות למטופל":
            # Merge system items with uploaded files for הוראות למטופל
            doc_manifest_render_hebrew[folder_name] = system_patient_items + (folder_files or [])
        else:
            # Include all other folders as-is
            doc_manifest_render_hebrew[folder_name] = folder_files or []

    doc_manifest_render = {"english": doc_manifest_render_english, "hebrew": doc_manifest_render_hebrew}

    return render_template("documentation.html", doc_manifest=doc_manifest_render)


@login_required
def download_local_document(lang: str, relpath: str) -> Any:
    """Serve locally stored documentation files referenced by the documentation manifest."""
    lang_key = (lang or "").strip().lower()
    if lang_key not in {"english", "hebrew"}:
        return abort(404)

    safe_rel = os.path.normpath(relpath or "")
    if safe_rel.startswith("..") or os.path.isabs(safe_rel):
        return abort(404)

    docs_dir = _documentation_base_dir()
    base_dir = os.path.join(docs_dir, lang_key)
    file_path = os.path.abspath(os.path.join(base_dir, safe_rel))

    if not file_path.startswith(os.path.abspath(base_dir) + os.sep):
        return abort(404)
    if not os.path.exists(file_path):
        return abort(404)

    return send_file(file_path)


def _documentation_base_dir() -> str:
    """Documentation root: DOCUMENTATION_DIR env, or repo sibling 'Documentation', or flask_app/static/documentation fallback."""
    env_dir = os.environ.get("DOCUMENTATION_DIR", "").strip()
    if env_dir and os.path.isdir(env_dir):
        return os.path.abspath(env_dir)
    repo_docs = os.path.abspath(os.path.join(current_app.root_path, "..", "Documentation"))
    if os.path.isdir(repo_docs):
        return repo_docs
    static_docs = os.path.join(current_app.root_path, "static", "documentation")
    return os.path.abspath(static_docs)


@login_required
def download_patient_document(doc_key: str) -> Any:
    """Serve specific patient documents (Hebrew PDFs) from the Documentation directory."""
    patient_docs = {
        "oral_appliance_care": "הוראות למטופל - טיפול ושימוש בהתקן האוראלי.pdf",
        "post_delivery_instructions": "הנחיות לאחר קבלת התקן אורלי לטיפול בדום נשימה חסימתי בשינה.pdf",
        "informed_consent_sleep_related_breathing": "הסכמה מדעת לטיפול בהפרעות נשימה הקשורות לשינה.pdf",
    }

    filename = patient_docs.get((doc_key or "").strip())
    if not filename:
        logger.warning("Requested unknown patient document: %s", doc_key)
        return abort(404)

    docs_dir = _documentation_base_dir()
    hebrew_dir = os.path.join(docs_dir, "hebrew", "הוראות למטופל")
    file_path = os.path.join(hebrew_dir, filename)
    if not os.path.exists(file_path):
        legacy_path = os.path.join(docs_dir, filename)
        if os.path.exists(legacy_path):
            file_path = legacy_path
        else:
            logger.error(
                "Patient document file not found. doc_key=%s, tried: %s and %s",
                doc_key, file_path, legacy_path,
            )
            return abort(404)

    return send_file(file_path, mimetype="application/pdf")


def _redirect_imaging_legacy(protocol_key: str) -> Any:
    """Redirect old /imaging-protocols/<key> to /documentation/imaging/<key>."""
    return redirect(url_for("main.download_imaging_protocol", protocol_key=protocol_key), code=301)


def register_docs_routes(main) -> None:
    # Keep endpoint names stable (e.g. url_for('main.documentation')) by setting endpoint= explicitly.
    # Canonical doc URLs under /documentation/
    main.add_url_rule(
        "/documentation/imaging/<protocol_key>",
        endpoint="download_imaging_protocol",
        view_func=download_imaging_protocol,
        methods=["GET"],
    )
    main.add_url_rule(
        "/imaging-protocols/<protocol_key>",
        endpoint="download_imaging_protocol_legacy",
        view_func=_redirect_imaging_legacy,
        methods=["GET"],
    )
    main.add_url_rule("/documentation", endpoint="documentation", view_func=documentation, methods=["GET"])
    main.add_url_rule(
        "/documentation/local/<lang>/<path:relpath>",
        endpoint="download_local_document",
        view_func=download_local_document,
        methods=["GET"],
    )
    main.add_url_rule(
        "/documentation/patient/<doc_key>",
        endpoint="download_patient_document",
        view_func=download_patient_document,
        methods=["GET"],
    )

