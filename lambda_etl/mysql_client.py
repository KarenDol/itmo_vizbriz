"""
MySQL-based idempotency tracking for ETL Lambda function
Replaces DynamoDB with existing MySQL database
"""

import json
import logging
import mysql.connector
from mysql.connector import Error
from typing import Optional, Dict, Any, List
from datetime import datetime
import os

logger = logging.getLogger(__name__)

class MySQLIdempotencyClient:
    """MySQL client for tracking processed S3 objects and ensuring idempotency"""
    
    def __init__(self):
        """Initialize MySQL connection using environment variables"""
        self.connection = None
        self.connect()
    
    def connect(self):
        """Establish MySQL connection"""
        try:
            self.connection = mysql.connector.connect(
                host=os.environ.get('MYSQL_HOST', 'localhost'),
                port=int(os.environ.get('MYSQL_PORT', 3306)),
                database=os.environ.get('MYSQL_DATABASE', 'vizbriz'),
                user=os.environ.get('MYSQL_USER', 'root'),
                password=os.environ.get('MYSQL_PASSWORD', ''),
                charset='utf8mb4',
                collation='utf8mb4_unicode_ci',
                autocommit=True
            )
            logger.info("MySQL connection established successfully")
        except Error as e:
            logger.error(f"Error connecting to MySQL: {e}")
            raise
    
    def is_already_processed(self, s3_bucket: str, s3_key: str, etag: str) -> bool:
        """
        Check if an S3 object has already been processed successfully
        
        Args:
            s3_bucket: S3 bucket name
            s3_key: S3 object key
            etag: S3 object ETag
            
        Returns:
            bool: True if already processed successfully, False otherwise
        """
        try:
            cursor = self.connection.cursor(dictionary=True)
            
            query = """
                SELECT status, processed_at, case_id, patient_rid 
                FROM processed_objects 
                WHERE s3_bucket = %s AND s3_key = %s AND etag = %s
            """
            
            cursor.execute(query, (s3_bucket, s3_key, etag))
            result = cursor.fetchone()
            cursor.close()
            
            if result and result['status'] == 'DONE':
                logger.info(f"Object already processed: {s3_bucket}/{s3_key} (ETag: {etag})")
                return True
            
            return False
            
        except Error as e:
            logger.error(f"Error checking if object is already processed: {e}")
            return False
    
    def mark_processing_started(self, s3_bucket: str, s3_key: str, etag: str) -> int:
        """
        Mark an object as processing started
        
        Args:
            s3_bucket: S3 bucket name
            s3_key: S3 object key
            etag: S3 object ETag
            
        Returns:
            int: Database record ID
        """
        try:
            cursor = self.connection.cursor()
            
            # Insert or update record
            query = """
                INSERT INTO processed_objects (s3_bucket, s3_key, etag, status, processed_at)
                VALUES (%s, %s, %s, 'PROCESSING', NOW())
                ON DUPLICATE KEY UPDATE 
                    status = 'PROCESSING',
                    processed_at = NOW(),
                    updated_at = NOW()
            """
            
            cursor.execute(query, (s3_bucket, s3_key, etag))
            record_id = cursor.lastrowid
            
            # If it was an update, get the existing ID
            if record_id == 0:
                cursor.execute(
                    "SELECT id FROM processed_objects WHERE s3_bucket = %s AND s3_key = %s AND etag = %s",
                    (s3_bucket, s3_key, etag)
                )
                result = cursor.fetchone()
                record_id = result[0] if result else None
            
            cursor.close()
            logger.info(f"Marked object as processing: {s3_bucket}/{s3_key} (ID: {record_id})")
            return record_id
            
        except Error as e:
            logger.error(f"Error marking object as processing started: {e}")
            raise
    
    def mark_processing_completed(self, record_id: int, case_id: str, patient_rid: str, 
                                 extraction_coverage: float, processing_duration_ms: int):
        """
        Mark an object as successfully processed
        
        Args:
            record_id: Database record ID
            case_id: Generated case ID
            patient_rid: Generated patient RID
            extraction_coverage: Fraction of fields successfully extracted
            processing_duration_ms: Processing time in milliseconds
        """
        try:
            cursor = self.connection.cursor()
            
            query = """
                UPDATE processed_objects 
                SET status = 'DONE',
                    case_id = %s,
                    patient_rid = %s,
                    extraction_coverage = %s,
                    processing_duration_ms = %s,
                    processed_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
            """
            
            cursor.execute(query, (case_id, patient_rid, extraction_coverage, 
                                 processing_duration_ms, record_id))
            cursor.close()
            
            logger.info(f"Marked object as completed: Record ID {record_id}")
            
        except Error as e:
            logger.error(f"Error marking object as completed: {e}")
            raise
    
    def mark_processing_failed(self, record_id: int, error_message: str):
        """
        Mark an object as failed processing
        
        Args:
            record_id: Database record ID
            error_message: Error description
        """
        try:
            cursor = self.connection.cursor()
            
            query = """
                UPDATE processed_objects 
                SET status = 'FAILED',
                    error_message = %s,
                    updated_at = NOW()
                WHERE id = %s
            """
            
            cursor.execute(query, (error_message, record_id))
            cursor.close()
            
            logger.error(f"Marked object as failed: Record ID {record_id}, Error: {error_message}")
            
        except Error as e:
            logger.error(f"Error marking object as failed: {e}")
            raise
    
    def log_validation_error(self, record_id: int, validation_type: str, severity: str, 
                           message: str, field_name: str = None):
        """
        Log validation errors and warnings
        
        Args:
            record_id: Database record ID
            validation_type: Type of validation (SCHEMA, BUSINESS_LOGIC, DATA_QUALITY)
            severity: Error severity (ERROR, WARNING, INFO)
            message: Error message
            field_name: Optional field name
        """
        try:
            cursor = self.connection.cursor()
            
            query = """
                INSERT INTO validation_logs 
                (processed_object_id, validation_type, severity, message, field_name)
                VALUES (%s, %s, %s, %s, %s)
            """
            
            cursor.execute(query, (record_id, validation_type, severity, message, field_name))
            cursor.close()
            
        except Error as e:
            logger.error(f"Error logging validation error: {e}")
    
    def update_extraction_pattern_stats(self, pattern_name: str, success: bool):
        """
        Update extraction pattern success/failure statistics
        
        Args:
            pattern_name: Name of the extraction pattern
            success: Whether the extraction was successful
        """
        try:
            cursor = self.connection.cursor()
            
            if success:
                query = """
                    UPDATE extraction_patterns 
                    SET success_count = success_count + 1,
                        last_used_at = NOW(),
                        updated_at = NOW()
                    WHERE pattern_name = %s
                """
            else:
                query = """
                    UPDATE extraction_patterns 
                    SET failure_count = failure_count + 1,
                        last_used_at = NOW(),
                        updated_at = NOW()
                    WHERE pattern_name = %s
                """
            
            cursor.execute(query, (pattern_name,))
            cursor.close()
            
        except Error as e:
            logger.error(f"Error updating extraction pattern stats: {e}")
    
    def get_processing_stats(self, days: int = 7) -> Dict[str, Any]:
        """
        Get processing statistics for the last N days
        
        Args:
            days: Number of days to look back
            
        Returns:
            dict: Processing statistics
        """
        try:
            cursor = self.connection.cursor(dictionary=True)
            
            query = """
                SELECT 
                    COUNT(*) as total_files,
                    SUM(CASE WHEN status = 'DONE' THEN 1 ELSE 0 END) as successful,
                    SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed,
                    AVG(processing_duration_ms) as avg_processing_time,
                    AVG(extraction_coverage) as avg_extraction_coverage
                FROM processed_objects 
                WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
            """
            
            cursor.execute(query, (days,))
            result = cursor.fetchone()
            cursor.close()
            
            return result or {}
            
        except Error as e:
            logger.error(f"Error getting processing stats: {e}")
            return {}
    
    def close(self):
        """Close MySQL connection"""
        if self.connection and self.connection.is_connected():
            self.connection.close()
            logger.info("MySQL connection closed")

# Global instance for Lambda reuse
mysql_client = None

def get_mysql_client() -> MySQLIdempotencyClient:
    """Get or create MySQL client instance (for Lambda reuse)"""
    global mysql_client
    if mysql_client is None:
        mysql_client = MySQLIdempotencyClient()
    return mysql_client
