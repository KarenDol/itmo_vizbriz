#!/usr/bin/env python3
"""
Document-Based Observation Extraction System - Phase 1
Document Discovery & Categorization

This script implements Phase 1 of the document observation extraction system:
1. Document discovery from both 'files' and 'adminfiles' tables
2. Document type mapping and categorization
3. Binary file filtering (exclude .dcm, .stl, etc.)
4. Integration with existing observation_store table
"""

import os
import sys
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import mysql.connector
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('document_extraction.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Database configuration
DB_CONFIG = {
    'host': 'vizbrizapp-202606.ch8koiygcu36.us-east-2.rds.amazonaws.com',
    'user': 'admin',
    'password': 'Vizbriz2025!',
    'database': 'vizbriz',
    'port': 3306
}

# Binary file extensions to exclude
BINARY_EXTENSIONS = {
    '.dcm', '.stl', '.bin', '.exe', '.dll', '.so', '.dylib', '.obj', '.o',
    '.zip', '.tar', '.gz', '.rar', '.7z', '.bz2', '.xz',
    '.mp3', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv',
    '.db', '.sqlite', '.sqlite3', '.mdb', '.accdb'
}

# Document type mapping for observation_store.source_type
DOCUMENT_TYPE_MAPPING = {
    # Files table subcategories
    'sleep-test': 'sleep_test',
    'questionnaire': 'questionnaire',
    'intraoral-scan': 'intraoral_scan',
    'medical-background': 'medical_background',
    'consent': 'consent_form',
    'insurance': 'insurance_document',
    'payment': 'payment_document',
    
    # Adminfiles table file_categories
    'cbct observations': 'cbct_report',
    'patient report': 'patient_report',
    'sleep study': 'sleep_study',
    'consultation notes': 'consultation_notes',
    'treatment plan': 'treatment_plan',
    'follow-up': 'follow_up_notes',
    'prescription': 'prescription',
    'lab results': 'lab_results',
    'imaging': 'imaging_report',
    'medical history': 'medical_history',
    'surgical notes': 'surgical_notes',
    'discharge summary': 'discharge_summary',
    
    # Default mappings
    'default': 'general_medical'
}

def is_binary_file(filename: str) -> bool:
    """
    Check if a file is binary based on its extension.
    
    Args:
        filename (str): The filename to check
        
    Returns:
        bool: True if the file is binary, False otherwise
    """
    if not filename:
        return True
    
    # Get file extension
    file_ext = os.path.splitext(filename.lower())[1]
    return file_ext in BINARY_EXTENSIONS

def map_document_type_to_source_type(category: str, subcategory: str = None, file_category: str = None) -> str:
    """
    Map document category/subcategory to standardized source_type for observation_store.
    
    Args:
        category (str): Document category (from files table)
        subcategory (str): Document subcategory (from files table)
        file_category (str): File category (from adminfiles table)
        
    Returns:
        str: Standardized source_type for observation_store
    """
    # Priority: file_category (adminfiles) > subcategory (files) > category (files)
    if file_category:
        file_category_lower = file_category.lower().strip()
        
        # Special handling for sleep study reports - prioritize sleep study over questionnaire
        if 'sleep' in file_category_lower or 'level' in file_category_lower or 'report' in file_category_lower:
            if 'sleep' in file_category_lower:
                return 'sleep_study'
            else:
                return 'report'
        
        # Check for specific mappings
        for key, value in DOCUMENT_TYPE_MAPPING.items():
            if key in file_category_lower:
                return value
        # If no specific mapping found, treat as report
        return 'report'
    
    if subcategory:
        subcategory_lower = subcategory.lower().strip()
        for key, value in DOCUMENT_TYPE_MAPPING.items():
            if key == subcategory_lower:
                return value
    
    if category:
        category_lower = category.lower().strip()
        for key, value in DOCUMENT_TYPE_MAPPING.items():
            if key == category_lower:
                return value
    
    # Default fallback
    return DOCUMENT_TYPE_MAPPING.get('default', 'general_medical')

def discover_patient_documents(patient_id: int) -> List[Dict]:
    """
    Discover all non-binary documents for a patient from both 'files' and 'adminfiles' tables.
    
    Args:
        patient_id (int): The patient ID to discover documents for
        
    Returns:
        List[Dict]: List of document dictionaries with metadata
    """
    conn = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        documents = []
        
        # Query files table - only get unanalyzed files
        cursor.execute("""
            SELECT 
                id,
                name,
                patient_id,
                upload_date,
                file_type,
                file_size,
                s3_key,
                category,
                subcategory,
                comment,
                'files' as source_table
            FROM files 
            WHERE patient_id = %s AND (analyzed IS NULL OR analyzed = FALSE)
            ORDER BY upload_date DESC
        """, (patient_id,))
        
        files_results = cursor.fetchall()
        
        for file_record in files_results:
            # Skip binary files
            if is_binary_file(file_record['name']):
                logger.debug(f"Skipping binary file: {file_record['name']}")
                continue
            
            # Skip billing, CBCT, clinical images, and intraoral scan files (not relevant for clinical analysis)
            subcategory_lower = (file_record.get('subcategory') or '').lower()
            category_lower = (file_record.get('category') or '').lower()
            
            # Check both subcategory and category for exclusions
            if (subcategory_lower in ['billing', 'cbct', 'clinical-images', 'clinical images', 'intraoral-scan', 'clinical-pictures'] or 
                category_lower in ['billing', 'cbct', 'clinical-images', 'clinical images', 'intraoral-scan', 'clinical-pictures']):
                logger.info(f"Skipping {subcategory_lower}/{category_lower} file: {file_record['name']}")
                continue
            
            # Map document type
            source_type = map_document_type_to_source_type(
                category=file_record['category'],
                subcategory=file_record['subcategory']
            )
            
            document = {
                'id': file_record['id'],
                'name': file_record['name'],
                'patient_id': file_record['patient_id'],
                'upload_date': file_record['upload_date'],
                'file_type': file_record['file_type'],
                'file_size': file_record['file_size'],
                's3_key': file_record['s3_key'],
                'source_table': file_record['source_table'],
                'source_type': source_type,
                'category': file_record['category'],
                'subcategory': file_record['subcategory'],
                'comment': file_record['comment']
            }
            documents.append(document)
        
        # Query adminfiles table - only get unanalyzed files
        cursor.execute("""
            SELECT 
                id,
                name,
                patient_id,
                upload_date,
                file_type,
                file_size,
                s3_key,
                is_public,
                file_category,
                'adminfiles' as source_table
            FROM adminfiles 
            WHERE patient_id = %s AND (analyzed IS NULL OR analyzed = FALSE)
            ORDER BY upload_date DESC
        """, (patient_id,))
        
        adminfiles_results = cursor.fetchall()
        
        for adminfile_record in adminfiles_results:
            # Skip binary files
            if is_binary_file(adminfile_record['name']):
                logger.debug(f"Skipping binary file: {adminfile_record['name']}")
                continue
            
            # Skip billing, CBCT, clinical images, and intraoral scan files (not relevant for clinical analysis)
            file_category_lower = (adminfile_record.get('file_category') or '').lower()
            if file_category_lower in ['billing', 'cbct', 'clinical-images', 'clinical images', 'intraoral-scan', 'clinical-pictures']:
                logger.info(f"Skipping {file_category_lower} file: {adminfile_record['name']}")
                continue
            
            # Map document type
            source_type = map_document_type_to_source_type(
                category=None,
                subcategory=None,
                file_category=adminfile_record['file_category']
            )
            
            document = {
                'id': adminfile_record['id'],
                'name': adminfile_record['name'],
                'patient_id': adminfile_record['patient_id'],
                'upload_date': adminfile_record['upload_date'],
                'file_type': adminfile_record['file_type'],
                'file_size': adminfile_record['file_size'],
                's3_key': adminfile_record['s3_key'],
                'source_table': adminfile_record['source_table'],
                'source_type': source_type,
                'is_public': adminfile_record['is_public'],
                'file_category': adminfile_record['file_category']
            }
            documents.append(document)
        
        logger.info(f"Discovered {len(documents)} documents for patient {patient_id}")
        return documents
        
    except Exception as e:
        logger.error(f"Error discovering documents for patient {patient_id}: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_document_statistics(patient_id: int) -> Dict:
    """
    Get statistics about discovered documents for a patient.
    
    Args:
        patient_id (int): The patient ID
        
    Returns:
        Dict: Statistics about the patient's documents
    """
    documents = discover_patient_documents(patient_id)
    
    if not documents:
        return {
            'total_documents': 0,
            'by_source_type': {},
            'by_file_type': {},
            'by_source_table': {},
            'total_size_mb': 0
        }
    
    # Calculate statistics
    stats = {
        'total_documents': len(documents),
        'by_source_type': {},
        'by_file_type': {},
        'by_source_table': {},
        'total_size_mb': 0
    }
    
    for doc in documents:
        # Source type statistics
        source_type = doc['source_type']
        stats['by_source_type'][source_type] = stats['by_source_type'].get(source_type, 0) + 1
        
        # File type statistics
        file_type = doc['file_type'] or 'unknown'
        stats['by_file_type'][file_type] = stats['by_file_type'].get(file_type, 0) + 1
        
        # Source table statistics
        source_table = doc['source_table']
        stats['by_source_table'][source_table] = stats['by_source_table'].get(source_table, 0) + 1
        
        # Size statistics
        if doc['file_size']:
            stats['total_size_mb'] += doc['file_size'] / (1024 * 1024)
    
    return stats

def test_document_discovery():
    """
    Test function to demonstrate document discovery functionality.
    """
    # Test with a sample patient ID (you can change this)
    test_patient_id = 24579  # Use an existing patient ID
    
    logger.info(f"Testing document discovery for patient {test_patient_id}")
    logger.info("=" * 60)
    
    # Discover documents
    documents = discover_patient_documents(test_patient_id)
    
    if not documents:
        logger.info("No documents found for this patient")
        return
    
    # Display discovered documents
    logger.info(f"Found {len(documents)} documents:")
    logger.info("-" * 60)
    
    for i, doc in enumerate(documents, 1):
        logger.info(f"{i}. {doc['name']}")
        logger.info(f"   Type: {doc['source_type']} (from {doc['source_table']})")
        logger.info(f"   File Type: {doc['file_type']}")
        logger.info(f"   Size: {doc['file_size']} bytes")
        logger.info(f"   Upload Date: {doc['upload_date']}")
        logger.info(f"   S3 Key: {doc['s3_key']}")
        logger.info("-" * 40)
    
    # Display statistics
    stats = get_document_statistics(test_patient_id)
    logger.info("Document Statistics:")
    logger.info(f"Total Documents: {stats['total_documents']}")
    logger.info(f"Total Size: {stats['total_size_mb']:.2f} MB")
    logger.info("By Source Type:")
    for source_type, count in stats['by_source_type'].items():
        logger.info(f"  {source_type}: {count}")
    logger.info("By File Type:")
    for file_type, count in stats['by_file_type'].items():
        logger.info(f"  {file_type}: {count}")

if __name__ == "__main__":
    # Run the test function
    test_document_discovery() 