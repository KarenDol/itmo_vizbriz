"""
Script to find and fix patients stuck in 'processing' status
Run this periodically to clean up stuck processing entries
"""

from flask_app import db, create_app
from sqlalchemy import text
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

def find_stuck_patients(hours_threshold=2):
    """
    Find patients that have been in 'processing' status for too long
    
    Args:
        hours_threshold: Number of hours to consider as "stuck"
    
    Returns:
        List of stuck queue entries
    """
    app = create_app()
    with app.app_context():
        query = text("""
            SELECT 
                id,
                patient_id,
                status,
                requested_at,
                started_at,
                TIMESTAMPDIFF(MINUTE, started_at, NOW()) as minutes_processing,
                error_message,
                retry_count
            FROM document_processing_queue
            WHERE status = 'processing'
            AND started_at < NOW() - INTERVAL :hours_threshold HOUR
            ORDER BY started_at ASC
        """)
        
        result = db.session.execute(query, {'hours_threshold': hours_threshold})
        stuck_entries = result.fetchall()
        
        return stuck_entries


def fix_stuck_patients(hours_threshold=2, mark_as_failed=True):
    """
    Find and fix patients stuck in processing status
    
    Args:
        hours_threshold: Hours to consider as stuck
        mark_as_failed: If True, mark as failed. If False, reset to pending for retry.
    
    Returns:
        Number of entries fixed
    """
    app = create_app()
    with app.app_context():
        stuck = find_stuck_patients(hours_threshold)
        
        if not stuck:
            logger.info("No stuck patients found")
            return 0
        
        logger.warning(f"Found {len(stuck)} stuck patients in processing status")
        
        fixed_count = 0
        for entry in stuck:
            queue_id = entry.id
            patient_id = entry.patient_id
            minutes_stuck = entry.minutes_processing
            
            logger.warning(f"Fixing stuck entry: queue_id={queue_id}, patient_id={patient_id}, stuck for {minutes_stuck} minutes")
            
            if mark_as_failed:
                # Mark as failed
                update_query = text("""
                    UPDATE document_processing_queue 
                    SET status = 'failed', 
                        completed_at = NOW(),
                        error_message = :error_msg
                    WHERE id = :queue_id
                """)
                error_msg = f'Processing stuck for {minutes_stuck} minutes - marked as failed by cleanup script'
            else:
                # Reset to pending for retry
                update_query = text("""
                    UPDATE document_processing_queue 
                    SET status = 'pending', 
                        started_at = NULL,
                        retry_count = retry_count + 1,
                        error_message = :error_msg
                    WHERE id = :queue_id
                """)
                error_msg = f'Processing stuck for {minutes_stuck} minutes - reset to pending for retry'
            
            try:
                db.session.execute(
                    update_query,
                    {
                        'queue_id': queue_id,
                        'error_msg': error_msg
                    }
                )
                db.session.commit()
                fixed_count += 1
                logger.info(f"Fixed queue entry {queue_id} for patient {patient_id}")
            except Exception as e:
                logger.error(f"Failed to fix queue entry {queue_id}: {e}")
                db.session.rollback()
        
        return fixed_count


def get_processing_stats():
    """Get statistics about processing queue"""
    app = create_app()
    with app.app_context():
        stats_query = text("""
            SELECT 
                status,
                COUNT(*) as count,
                MIN(requested_at) as oldest,
                MAX(requested_at) as newest,
                AVG(TIMESTAMPDIFF(MINUTE, requested_at, COALESCE(completed_at, NOW()))) as avg_duration_minutes
            FROM document_processing_queue
            GROUP BY status
        """)
        
        result = db.session.execute(stats_query)
        stats = {}
        for row in result:
            stats[row.status] = {
                'count': row.count,
                'oldest': row.oldest,
                'newest': row.newest,
                'avg_duration_minutes': row.avg_duration_minutes
            }
        
        return stats


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == 'fix':
        # Fix stuck patients
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else 2
        count = fix_stuck_patients(hours_threshold=hours)
        print(f"Fixed {count} stuck patients")
    elif len(sys.argv) > 1 and sys.argv[1] == 'stats':
        # Show statistics
        stats = get_processing_stats()
        print("\nProcessing Queue Statistics:")
        print("=" * 50)
        for status, data in stats.items():
            print(f"\n{status.upper()}:")
            print(f"  Count: {data['count']}")
            print(f"  Oldest: {data['oldest']}")
            print(f"  Newest: {data['newest']}")
            print(f"  Avg Duration: {data['avg_duration_minutes']:.1f} minutes")
    else:
        # Find stuck patients
        hours = int(sys.argv[1]) if len(sys.argv) > 1 else 2
        stuck = find_stuck_patients(hours)
        print(f"\nFound {len(stuck)} patients stuck in processing (>{hours} hours):")
        print("=" * 70)
        for entry in stuck:
            print(f"Queue ID: {entry.id}, Patient ID: {entry.patient_id}, "
                  f"Stuck for: {entry.minutes_processing} minutes, "
                  f"Started: {entry.started_at}")
        print("\nTo fix them, run: python stuck_patients_cleanup.py fix [hours]")
        print("To see stats, run: python stuck_patients_cleanup.py stats")

