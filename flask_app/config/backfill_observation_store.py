#!/usr/bin/env python3
"""
Backfill new nullable columns in observation_store for multi-value metrics.

Safe behavior:
- Does NOT copy values across documents/episodes
- Only enriches existing rows from their own extracted_observations JSON
- All new fields remain optional (NULL when unknown)

Usage examples:
  python -m flask_app.config.backfill_observation_store --patient 10318 --limit 500
  python -m flask_app.config.backfill_observation_store --all --batch-size 200
"""
from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

import mysql.connector


# Reuse DB settings from the Phase 2 extractor if available
try:
    from flask_app.config.document_observation_extractor_phase2 import DB_CONFIG  # type: ignore
except Exception:
    # Fallback: edit if running standalone
    DB_CONFIG = {
        'host': 'localhost',
        'user': 'root',
        'password': '',
        'database': 'vizbriz',
        'port': 3306,
    }


METRIC_KEYS = {
    'sleep_study.ahi': 'ahi',
    'sleep_study.odi': 'odi',
    'sleep_study.o2_nadir_pct': 'o2_nadir_pct',
    'sleep_study.sleep_efficiency_pct': 'sleep_efficiency_pct',
    'sleep_study.snoring.avg_db': 'snoring_avg_db',
    'sleep_study.snoring.max_db': 'snoring_max_db',
}


def make_episode_id(patient_id: int, s3_key: Optional[str], file_name: Optional[str]) -> str:
    base = f"{patient_id}:{s3_key or file_name or ''}"
    return hashlib.sha1(base.encode('utf-8')).hexdigest()[:16]


def _decimal_or_none(val) -> Optional[Decimal]:
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None


def _extract_metric_from_json(raw_json: str) -> Tuple[Optional[str], Optional[Decimal], Optional[str]]:
    """Return (metric_key, metric_value, metric_phase) from extracted_observations JSON.

    Only parse rows that used our structured format (with 'path' and 'value').
    """
    try:
        data = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
    except Exception:
        return None, None, None

    if isinstance(data, dict):
        path = data.get('path')
        value = data.get('value')
        phase = None
        if not path or value is None:
            return None, None, None

        # Path to key mapping
        metric_key = None
        if path in METRIC_KEYS:
            metric_key = METRIC_KEYS[path]
        elif path.startswith('sleep_study.'):
            # Fallback: take last token
            metric_key = path.split('.')[-1]

        metric_value = _decimal_or_none(value)
        return metric_key, metric_value, phase

    return None, None, None


def _get_file_info(cur, patient_id: int, file_name: Optional[str]):
    """Lookup s3_key and upload_date from files/adminfiles by patient_id + file name."""
    if not file_name:
        return None, None
    # files
    cur.execute(
        """
        SELECT s3_key, upload_date FROM files
        WHERE patient_id=%s AND name=%s
        LIMIT 1
        """,
        (patient_id, file_name),
    )
    row = cur.fetchone()
    if row:
        return row[0], row[1]

    # adminfiles
    cur.execute(
        """
        SELECT s3_key, upload_date FROM adminfiles
        WHERE patient_id=%s AND name=%s
        LIMIT 1
        """,
        (patient_id, file_name),
    )
    row = cur.fetchone()
    if row:
        return row[0], row[1]

    return None, None


def backfill_patient(patient_id: int, limit_rows: Optional[int] = None) -> int:
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor()

    sel = (
        "SELECT id, file_name, patient_id, source_type, extracted_observations "
        "FROM observation_store "
        "WHERE patient_id=%s"
    )
    if limit_rows:
        sel += " LIMIT %s"
        cur.execute(sel, (patient_id, limit_rows))
    else:
        cur.execute(sel, (patient_id,))

    rows = cur.fetchall()
    updated = 0

    upd_sql = (
        "UPDATE observation_store SET "
        "metric_key=%s, metric_value_decimal=%s, "
        "source_kind=%s, study_type=%s, observed_at=%s, mention_date=%s, "
        "episode_id=%s, facility=%s, s3_key=%s "
        "WHERE id=%s"
    )

    for oid, file_name, pid, source_type, raw_json in rows:
        metric_key, metric_value, _ = _extract_metric_from_json(raw_json)
        if not metric_key and not metric_value:
            continue  # nothing to enrich

        # Determine source_kind (strict A): sleep_study if path started with sleep_study.*
        source_kind = 'sleep_study' if metric_key in ('ahi', 'odi', 'o2_nadir_pct', 'sleep_efficiency_pct', 'snoring_avg_db', 'snoring_max_db') else 'report'
        # derived file info
        s3_key, upload_date = _get_file_info(cur, pid, file_name)
        episode_id = make_episode_id(pid, s3_key, file_name)

        observed_at = upload_date if source_kind == 'sleep_study' else None
        mention_date = upload_date if source_kind != 'sleep_study' else None
        study_type = None  # Unknown by default in backfill
        facility = None

        cur.execute(
            upd_sql,
            (
                metric_key, str(metric_value) if metric_value is not None else None,
                source_kind, study_type, observed_at, mention_date,
                episode_id, facility, s3_key, oid,
            ),
        )
        updated += 1

    conn.commit()
    cur.close()
    conn.close()
    return updated


def backfill_all(limit_patients: Optional[int] = None, limit_rows_per_patient: Optional[int] = None) -> int:
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT patient_id FROM observation_store WHERE patient_id IS NOT NULL ORDER BY patient_id DESC")
    patient_ids = [pid for (pid,) in cur.fetchall()]
    if limit_patients:
        patient_ids = patient_ids[:limit_patients]
    cur.close(); conn.close()

    total = 0
    for pid in patient_ids:
        total += backfill_patient(pid, limit_rows_per_patient)
    return total


def main():
    ap = argparse.ArgumentParser(description="Backfill observation_store new metric/provenance columns")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--patient', type=int, help='Single patient id to backfill')
    g.add_argument('--all', action='store_true', help='Backfill all patients found in observation_store')
    ap.add_argument('--limit', type=int, default=None, help='Limit number of rows per patient')
    ap.add_argument('--limit-patients', type=int, default=None, help='Limit number of patients when using --all')
    args = ap.parse_args()

    if args.patient:
        updated = backfill_patient(args.patient, args.limit)
        print(f"Backfill complete for patient {args.patient}: updated {updated} rows")
    else:
        total = backfill_all(args.limit_patients, args.limit)
        print(f"Backfill complete for all patients: updated {total} rows")


if __name__ == '__main__':
    main()


