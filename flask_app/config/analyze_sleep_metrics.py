#!/usr/bin/env python3
"""
Analyze sleep-study and report metrics across the system and export a CSV for pattern tuning.

Outputs one row per observation row, capturing both promoted numeric metrics (metric_key/value)
and any schema paths present in JSON (extracted_observations.path/value).

Usage examples:
  - Analyze all patients, write CSV:
      python -m flask_app.config.analyze_sleep_metrics --output /tmp/sleep_metrics.csv

  - Analyze a single patient:
      python -m flask_app.config.analyze_sleep_metrics --patient-id 46351 --output /tmp/sleep_46351.csv

Notes:
  - This script reads DB credentials via the shared DB_CONFIG from document_observation_extractor_phase2.
  - No Flask app context is needed.
  - File size kept <500 lines, single responsibility: analysis/export.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from typing import Any, Dict, Optional

import mysql.connector  # type: ignore

# Reuse DB config from the extractor module to avoid duplication
try:
    from flask_app.config.document_observation_extractor_phase2 import DB_CONFIG
except Exception:
    # Fallback: read from environment variables for maximum portability
    import os
    DB_CONFIG = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', '3306')),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'database': os.getenv('DB_NAME', 'vizbriz'),
    }


def _coerce_iso(dt: Optional[datetime | str]) -> Optional[str]:
    if not dt:
        return None
    if isinstance(dt, datetime):
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        # Attempt to parse common formats
        return datetime.fromisoformat(str(dt).replace('Z', '').replace(' ', 'T')).strftime('%Y-%m-%dT%H:%M:%SZ')
    except Exception:
        return str(dt)


def query_rows(patient_id: Optional[int]) -> list[Dict[str, Any]]:
    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        conn.cursor()
        cursor = conn.cursor(dictionary=True)

        base_sql = (
            "SELECT id, patient_id, file_name, s3_key, source_type, source_text, "
            "       extracted_observations, provider, created_at, updated_at, "
            "       metric_key, metric_value_decimal, metric_unit, metric_phase, "
            "       observed_at, mention_date, document_date, observed_at_source, "
            "       source_kind, study_type, episode_id, facility, file_section, snippet "
            "FROM observation_store "
            "WHERE (source_kind IN ('sleep_study','report') OR source_kind IS NULL)"
        )

        params: list[Any] = []
        if patient_id is not None:
            base_sql += " AND patient_id = %s"
            params.append(patient_id)

        base_sql += " ORDER BY patient_id, COALESCE(observed_at, mention_date, document_date, created_at)"
        cursor.execute(base_sql, params)
        rows = cursor.fetchall() or []
        return rows
    finally:
        conn.close()


def extract_json_fields(row: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Best-effort to read $.path and $.value from extracted_observations JSON."""
    data = row.get('extracted_observations')
    if not data:
        return None, None
    try:
        obj = json.loads(data) if isinstance(data, str) else data
        path = obj.get('path') if isinstance(obj, dict) else None
        value = obj.get('value') if isinstance(obj, dict) else None
        # normalize strings
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        return (str(path) if path is not None else None, str(value) if value is not None else None)
    except Exception:
        return None, None


def write_csv(rows: list[Dict[str, Any]], output_path: str) -> int:
    fieldnames = [
        'patient_id', 'source_kind', 'source_type', 'file_name', 's3_key', 'episode_id', 'study_type',
        'date', 'observed_at', 'mention_date', 'document_date', 'observed_at_source',
        'metric_key', 'metric_value_decimal', 'metric_unit', 'metric_phase',
        'json_path', 'json_value', 'snippet', 'provider', 'created_at', 'id'
    ]

    count = 0
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            json_path, json_value = extract_json_fields(r)
            out = {
                'patient_id': r.get('patient_id'),
                'source_kind': r.get('source_kind'),
                'source_type': r.get('source_type'),
                'file_name': r.get('file_name'),
                's3_key': r.get('s3_key'),
                'episode_id': r.get('episode_id'),
                'study_type': r.get('study_type'),
                'date': _coerce_iso(r.get('observed_at') or r.get('mention_date') or r.get('document_date')),
                'observed_at': _coerce_iso(r.get('observed_at')),
                'mention_date': _coerce_iso(r.get('mention_date')),
                'document_date': _coerce_iso(r.get('document_date')),
                'observed_at_source': r.get('observed_at_source'),
                'metric_key': r.get('metric_key'),
                'metric_value_decimal': r.get('metric_value_decimal'),
                'metric_unit': r.get('metric_unit'),
                'metric_phase': r.get('metric_phase'),
                'json_path': json_path,
                'json_value': json_value,
                'snippet': r.get('snippet'),
                'provider': r.get('provider'),
                'created_at': _coerce_iso(r.get('created_at')),
                'id': r.get('id'),
            }
            writer.writerow(out)
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description='Export sleep/report metrics for pattern tuning.')
    parser.add_argument('--patient-id', type=int, default=None, help='Optional single patient id to analyze')
    parser.add_argument('--output', type=str, required=True, help='Output CSV path')
    args = parser.parse_args()

    rows = query_rows(args.patient_id)
    written = write_csv(rows, args.output)
    print(f"Wrote {written} rows to {args.output}")


if __name__ == '__main__':
    main()


