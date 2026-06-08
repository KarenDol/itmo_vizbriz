"""
Document processing queue SLA: drop stale jobs so they are never auto-retried.

Default 5 minutes from request (pending) or from start (processing):
- DELETE rows where status=pending and requested_at is older than SLA
- DELETE rows where status=processing and COALESCE(started_at, requested_at) is older than SLA

Rows are removed (not marked failed) so we never hit UNIQUE(patient_id, status) when a prior
failed row already exists for that patient.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SLA_MINUTES = 5


def abandon_expired_document_queue_rows(
    conn: Any = None,
    *,
    sla_minutes: int = DEFAULT_SLA_MINUTES,
) -> int:
    """
    Remove expired queue rows (never analyzed by this queue entry).

    If conn is None, opens a short-lived mysql connection using phase2 DB_CONFIG.

    Returns total rows deleted.
    """
    import mysql.connector

    from flask_app.config.document_observation_extractor_phase2 import DB_CONFIG

    close_conn = False
    if conn is None:
        conn = mysql.connector.connect(**DB_CONFIG)
        close_conn = True

    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM document_processing_queue
        WHERE status = 'pending'
          AND requested_at < (NOW() - INTERVAL %s MINUTE)
        """,
        (sla_minutes,),
    )
    n1 = cur.rowcount

    cur.execute(
        """
        DELETE FROM document_processing_queue
        WHERE status = 'processing'
          AND COALESCE(started_at, requested_at) < (NOW() - INTERVAL %s MINUTE)
        """,
        (sla_minutes,),
    )
    n2 = cur.rowcount

    conn.commit()
    cur.close()
    if close_conn:
        conn.close()

    total = int(n1 or 0) + int(n2 or 0)
    if total:
        logger.info(
            "document_queue_sla: removed %s row(s) (pending=%s, processing=%s), sla=%sm",
            total,
            n1,
            n2,
            sla_minutes,
        )
    return total


def purge_entire_document_queue(conn: Any = None) -> int:
    """DELETE all rows from document_processing_queue. Returns rows deleted."""
    import mysql.connector

    from flask_app.config.document_observation_extractor_phase2 import DB_CONFIG

    close_conn = False
    if conn is None:
        conn = mysql.connector.connect(**DB_CONFIG)
        close_conn = True
    cur = conn.cursor()
    cur.execute("DELETE FROM document_processing_queue")
    n = cur.rowcount
    conn.commit()
    cur.close()
    if close_conn:
        conn.close()
    logger.warning("document_queue_sla: purged entire queue (%s rows)", n)
    return int(n or 0)
