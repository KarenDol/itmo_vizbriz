"""
Direct sleep-study extraction for UI / queue jobs (no full phase2 document pass).

Runs OpenAI structured analysis via sleep_study_analysis_pipeline, then refreshes
minimal canonical JSON. Full ``process_patient_documents`` remains available in
document_observation_extractor_phase2 for CLI/cron ``--mode queue`` and other tools.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[str], None]]


def _preferred_sleep_source_for_l3(ss_out: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """
    Last successfully processed sleep **study** file in this pipeline run.

    Skips our own auto L3 PDFs if they were misclassified as sleep-like documents.
    """
    details = ss_out.get("details") or []
    for d in reversed(details):
        if not isinstance(d, dict) or not d.get("success") or d.get("skipped"):
            continue
        fn = (d.get("file") or "").strip()
        if not fn:
            continue
        low = fn.lower()
        if low.startswith("level_3_l1_sleepai_") and low.endswith(".pdf"):
            continue
        # Optional s3 if pipeline adds it later
        sk = (d.get("s3_key") or "").strip() or None
        return fn, sk
    return None, None


def run_direct_sleep_extraction_for_patient(
    patient_id: int,
    *,
    progress_callback: ProgressCb = None,
) -> Dict[str, Any]:
    """
    Returns a dict including ``sleep_pipeline`` (aggregate stats from
    ``run_sleep_study_pipeline_for_patient``) and ``queue_outcome`` in
    {completed, failed, message}.
    """
    try:
        from flask_app.services.document_queue_sla import abandon_expired_document_queue_rows

        _n = abandon_expired_document_queue_rows()
        if _n:
            logger.info(
                "document_queue_sla (direct_sleep_extraction): removed %s stale row(s)",
                _n,
            )
    except Exception as e:
        logger.warning("document_queue_sla (direct_sleep_extraction): %s", e)

    if progress_callback:
        progress_callback(
            "Downloading the sleep study file from storage and sending it to OpenAI for analysis "
            "(PDF via API file input; images via vision). Set SLEEP_STUDY_OPENAI_API_KEY or OPENAI_API_KEY."
        )

    from flask_app.config.sleep_study_analysis_pipeline import (
        run_sleep_study_pipeline_for_patient,
    )

    ss_out = run_sleep_study_pipeline_for_patient(patient_id, force=False)
    logger.info(
        "direct_sleep_extraction patient=%s files=%s processed=%s skipped=%s failed=%s",
        patient_id,
        ss_out.get("files_considered"),
        ss_out.get("processed"),
        ss_out.get("skipped"),
        ss_out.get("failed"),
    )

    if progress_callback:
        progress_callback(
            f"Sleep pipeline: {ss_out.get('processed', 0)} processed, "
            f"{ss_out.get('skipped', 0)} skipped, {ss_out.get('failed', 0)} failed"
        )

    canonical_ok = False
    if progress_callback:
        progress_callback("Rebuilding minimal canonical JSON (skipping extra timeline LLM)...")
    try:
        from flask_app.config.document_observation_extractor_phase2 import (
            create_minimal_canonical_json_for_patient,
        )
        from flask_app.services.cache_service import CacheService

        canon_result = create_minimal_canonical_json_for_patient(
            patient_id,
            skip_timeline_llm=True,
            skip_quiz_risk_snapshot=True,
            skip_observation_text_numerical_pass=True,
        )
        canonical_ok = bool(canon_result.get("success"))
        if not canonical_ok:
            logger.warning(
                "direct_sleep_extraction: canonical refresh returned failure: %s",
                canon_result.get("message"),
            )
        else:
            try:
                CacheService.invalidate_patient_cache(patient_id)
            except Exception as inv_e:
                logger.warning(
                    "direct_sleep_extraction: cache invalidate after canonical failed: %s",
                    inv_e,
                )
    except Exception as e:
        logger.warning("direct_sleep_extraction: canonical refresh failed: %s", e)
        if progress_callback:
            progress_callback(f"Canonical refresh warning: {e}")

    n_files = int(ss_out.get("files_considered") or 0)
    processed = int(ss_out.get("processed") or 0)
    skipped = int(ss_out.get("skipped") or 0)
    failed = int(ss_out.get("failed") or 0)

    level3_auto = None
    if processed > 0:
        if progress_callback:
            progress_callback(
                "Generating Level 3 report (Level 1 questionnaire + sleep AI)…"
            )
        try:
            from flask_app.services.level3_integrated_from_sleep import (
                generate_and_store_level3_integrated_after_sleep,
            )

            pref_fn, pref_sk = _preferred_sleep_source_for_l3(ss_out)
            level3_auto = generate_and_store_level3_integrated_after_sleep(
                patient_id,
                snapshot_file_name=pref_fn,
                snapshot_s3_key=pref_sk,
            )
            if level3_auto.get("success"):
                logger.info(
                    "direct_sleep_extraction: Level 3 auto-generated for patient=%s %s",
                    patient_id,
                    level3_auto.get("s3_key"),
                )
            else:
                logger.info(
                    "direct_sleep_extraction: Level 3 auto skipped/failed for patient=%s: %s",
                    patient_id,
                    level3_auto,
                )
        except Exception as l3_e:
            logger.warning("direct_sleep_extraction: Level 3 auto exception: %s", l3_e)
            level3_auto = {"success": False, "error": str(l3_e)}

    if n_files == 0:
        outcome = "completed"
        msg = "No sleep-like documents found; canonical refreshed."
    elif processed > 0 or skipped > 0:
        outcome = "completed"
        msg = (
            f"Direct sleep analysis finished: {processed} newly analyzed, "
            f"{skipped} already up to date, {failed} failed."
        )
    else:
        outcome = "failed"
        msg = f"Direct sleep analysis failed for all {n_files} file(s)."

    out = {
        "mode": "direct_sleep_study_v1",
        "sleep_pipeline": ss_out,
        "canonical_refreshed": canonical_ok,
        "level3_auto": level3_auto,
        "queue_outcome": outcome,
        "queue_message": msg,
    }
    return out
