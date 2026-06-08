#!/usr/bin/env python3
"""
Cron Document Queue Manager

This script should run periodically (e.g., every 15 minutes) to:
1. Check for patients with new files in S3
2. Add them to the document_processing_queue
3. Trigger the extraction script to process the queue

Usage:
    python cron_document_queue_manager.py
"""

import sys
import os
import logging
import subprocess
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DB_CONFIG = {
    'host': 'vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com',
    'user': 'admin',
    'password': 'Vizbriz2025!',
    'database': 'vizbriz',
    'port': 3306
}


def check_for_new_files():
    """
    Check S3 for patients with new files.
    
    TODO: Implement your logic to detect new files.
    This should return a list of patient IDs that have new documents.
    
    For now, this is a placeholder that you'll replace with your
    existing file detection logic.
    """
    import mysql.connector
    import boto3
    
    # This is where YOUR existing logic goes
    # Example: Check S3 for recent uploads, compare with database, etc.
    
    patients_with_new_files = []
    
    # PLACEHOLDER: Replace with your actual file detection logic
    # Example:
    # s3_client = boto3.client('s3')
    # recent_uploads = get_recent_s3_uploads(s3_client)
    # patients_with_new_files = identify_patients_from_uploads(recent_uploads)
    
    logger.info(f"Found {len(patients_with_new_files)} patients with new files")
    return patients_with_new_files


def add_patients_to_queue(patient_ids):
    """
    Add patients to the document processing queue.
    
    Args:
        patient_ids: List of patient IDs to add to queue
        
    Returns:
        Number of patients added
    """
    import mysql.connector
    
    if not patient_ids:
        logger.info("No patients to add to queue")
        return 0
    
    added = 0
    skipped = 0
    
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        for patient_id in patient_ids:
            try:
                # Check if already in queue (pending or processing)
                cursor.execute("""
                    SELECT id, status FROM document_processing_queue
                    WHERE patient_id = %s 
                    AND status IN ('pending', 'processing')
                """, (patient_id,))
                
                existing = cursor.fetchone()
                if existing:
                    logger.info(f"Patient {patient_id} already in queue (status: {existing[1]}), skipping")
                    skipped += 1
                    continue
                
                # Add to queue with higher priority for cron-detected files
                cursor.execute("""
                    INSERT INTO document_processing_queue
                    (patient_id, source, priority, batch_size, notes, status)
                    VALUES (%s, 'cron', 5, 3, 'New files detected by cron', 'pending')
                """, (patient_id,))
                conn.commit()
                
                logger.info(f"✅ Added patient {patient_id} to queue")
                added += 1
                
            except Exception as e:
                logger.error(f"❌ Error adding patient {patient_id} to queue: {e}")
                continue
        
        cursor.close()
        conn.close()
        
        logger.info(f"Queue update complete: {added} added, {skipped} skipped")
        return added
        
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return 0


def trigger_queue_processing():
    """
    Trigger the document extraction script to process the queue.
    This calls the extraction script in --mode queue.
    
    Returns:
        bool: True if processing started successfully
    """
    script_path = os.path.join(
        os.path.dirname(__file__),
        'flask_app/config/document_observation_extractor_phase2.py'
    )
    
    venv_python = os.path.join(
        os.path.dirname(__file__),
        'venv/bin/python'
    )
    
    try:
        logger.info("🚀 Triggering queue processing...")
        
        # Run the extraction script in queue mode
        result = subprocess.run(
            [venv_python, script_path, '--mode', 'queue'],
            cwd=os.path.dirname(__file__),
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout
        )
        
        if result.returncode == 0:
            logger.info("✅ Queue processing completed successfully")
            logger.info(f"Output: {result.stdout[-500:]}")  # Last 500 chars
            return True
        else:
            logger.error(f"❌ Queue processing failed with code {result.returncode}")
            logger.error(f"Error: {result.stderr[-500:]}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error("❌ Queue processing timed out after 1 hour")
        return False
    except Exception as e:
        logger.error(f"❌ Error triggering queue processing: {e}")
        return False


def get_queue_stats():
    """Get current queue statistics"""
    import mysql.connector
    
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT 
                status,
                COUNT(*) as count
            FROM document_processing_queue
            GROUP BY status
        """)
        
        stats = {row['status']: row['count'] for row in cursor.fetchall()}
        
        cursor.close()
        conn.close()
        
        return stats
        
    except Exception as e:
        logger.error(f"Error getting queue stats: {e}")
        return {}


def main():
    """
    Main cron job logic:
    1. Check for new files
    2. Add patients to queue
    3. Trigger processing
    """
    logger.info("="*60)
    logger.info("📋 Starting Document Queue Manager")
    logger.info("="*60)
    
    # Get initial queue stats
    initial_stats = get_queue_stats()
    logger.info(f"Initial queue stats: {initial_stats}")
    
    # Step 1: Check for new files and add to queue
    logger.info("\n🔍 Step 1: Checking for new files...")
    patients_with_new_files = check_for_new_files()
    
    if patients_with_new_files:
        logger.info(f"📝 Step 2: Adding {len(patients_with_new_files)} patients to queue...")
        added = add_patients_to_queue(patients_with_new_files)
        logger.info(f"✅ Added {added} new patients to queue")
    else:
        logger.info("ℹ️  No new files detected")
    
    # Step 2: Process the queue (processes ALL pending items, from UI and cron)
    logger.info("\n⚙️  Step 3: Processing queue (all pending items)...")
    success = trigger_queue_processing()
    
    # Get final queue stats
    final_stats = get_queue_stats()
    logger.info(f"\n📊 Final queue stats: {final_stats}")
    
    logger.info("\n" + "="*60)
    if success:
        logger.info("✅ Cron job completed successfully")
    else:
        logger.info("⚠️  Cron job completed with errors")
    logger.info("="*60)
    
    return 0 if success else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)

