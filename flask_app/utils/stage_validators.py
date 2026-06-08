"""
Centralized Stage Validation Utilities
This module contains validation functions that can be used by both the manifest validator
and the main routes to ensure consistency.
"""

from typing import Dict, Any, Optional
from sqlalchemy import text
from flask import current_app
from flask_app import db

def validate_hipaa_consent_signed_simple(patient_id: int) -> Dict[str, Any]:
    """
    Simple HIPAA consent validation - checks for 'HIPAA' or 'consent' in filename or comment
    Returns: {'is_completed': bool, 'file_info': dict, 'status_message': str}
    """
    try:
        # Check for billing files with HIPAA or consent terms
        result = db.session.execute(
            text("""
                SELECT f.id, f.name, f.file_type, f.subcategory, f.comment, f.upload_date
                FROM files f
                WHERE f.patient_id = :pid 
                AND LOWER(f.subcategory) = LOWER('billing')
                AND (
                    LOWER(f.name) LIKE '%hipaa%' 
                    OR LOWER(f.name) LIKE '%consent%'
                    OR LOWER(f.comment) LIKE '%hipaa%'
                    OR LOWER(f.comment) LIKE '%consent%'
                )
                ORDER BY f.upload_date DESC
                LIMIT 1
            """),
            {'pid': patient_id}
        ).first()
        
        if result:
            # Determine which term was found
            file_name_lower = result.name.lower()
            comment_lower = (result.comment or '').lower()
            
            found_terms = []
            if 'hipaa' in file_name_lower or 'hipaa' in comment_lower:
                found_terms.append('hipaa')
            if 'consent' in file_name_lower or 'consent' in comment_lower:
                found_terms.append('consent')
            
            return {
                'is_completed': True,
                'file_info': {
                    'id': result.id,
                    'name': result.name,
                    'file_type': result.file_type,
                    'subcategory': result.subcategory,
                    'upload_date': result.upload_date,
                    'found_terms': found_terms
                },
                'status_message': f"HIPAA consent form found on {result.upload_date.strftime('%B %d, %Y')}"
            }
        else:
            return {
                'is_completed': False,
                'file_info': None,
                'status_message': 'HIPAA consent forms not found - please upload files with "HIPAA" or "consent" in filename or comment under billing category'
            }
            
    except Exception as e:
        current_app.logger.error(f"Error validating HIPAA consent for patient {patient_id}: {e}")
        return {
            'is_completed': False,
            'file_info': None,
            'status_message': f'Error validating HIPAA consent: {str(e)}'
        }

def validate_intraoral_scan_uploaded_simple(patient_id: int) -> Dict[str, Any]:
    """
    Simple intraoral scan validation - checks for STL files in intraoral-scan subcategory
    Returns: {'is_completed': bool, 'file_info': dict, 'status_message': str}
    """
    try:
        result = db.session.execute(
            text("""
                SELECT f.id, f.name, f.upload_date, f.file_type, f.subcategory
                FROM files f
                WHERE f.patient_id = :pid
                AND LOWER(f.subcategory) = LOWER('intraoral-scan')
                AND LOWER(f.name) LIKE '%.stl'
                ORDER BY f.upload_date DESC
                LIMIT 1
            """),
            {'pid': patient_id}
        ).first()
        
        if result:
            return {
                'is_completed': True,
                'file_info': {
                    'id': result.id,
                    'name': result.name,
                    'file_type': result.file_type,
                    'subcategory': result.subcategory,
                    'upload_date': result.upload_date
                },
                'status_message': f"Intraoral scan file uploaded on {result.upload_date.strftime('%B %d, %Y')}"
            }
        else:
            return {
                'is_completed': False,
                'file_info': None,
                'status_message': 'Intraoral scan files (STL format) not uploaded - please upload STL files under intraoral-scan category'
            }
            
    except Exception as e:
        current_app.logger.error(f"Error validating intraoral scan for patient {patient_id}: {e}")
        return {
            'is_completed': False,
            'file_info': None,
            'status_message': f'Error validating intraoral scan: {str(e)}'
        }

def validate_cbct_observation_report_simple(patient_id: int) -> Dict[str, Any]:
    """
    Simple CBCT observation report validation - checks for admin files with CBCT Observations category
    Returns: {'is_completed': bool, 'file_info': dict, 'status_message': str}
    """
    try:
        result = db.session.execute(
            text("""
                SELECT af.id, af.name, af.upload_date, af.file_category
                FROM adminfiles af
                WHERE af.patient_id = :pid
                AND LOWER(af.file_category) = LOWER('CBCT Observations')
                ORDER BY af.upload_date DESC
                LIMIT 1
            """),
            {'pid': patient_id}
        ).first()
        
        if result:
            return {
                'is_completed': True,
                'file_info': {
                    'id': result.id,
                    'name': result.name,
                    'file_category': result.file_category,
                    'upload_date': result.upload_date
                },
                'status_message': f"CBCT observation report uploaded on {result.upload_date.strftime('%B %d, %Y')}"
            }
        else:
            return {
                'is_completed': False,
                'file_info': None,
                'status_message': 'CBCT observation report not uploaded - please ask VizBriz to upload report'
            }
            
    except Exception as e:
        current_app.logger.error(f"Error validating CBCT observation for patient {patient_id}: {e}")
        return {
            'is_completed': False,
            'file_info': None,
            'status_message': f'Error validating CBCT observation: {str(e)}'
        }

# Add more stage validators as needed...
def validate_stage_simple(patient_id: int, stage_key: str) -> Dict[str, Any]:
    """
    Centralized stage validation dispatcher
    """
    validators = {
        'hipaa_consent_signed': validate_hipaa_consent_signed_simple,
        'intraoral_scan_uploaded': validate_intraoral_scan_uploaded_simple,
        'cbct_observation_report_uploaded': validate_cbct_observation_report_simple,
    }
    
    validator_func = validators.get(stage_key)
    if validator_func:
        return validator_func(patient_id)
    else:
        return {
            'is_completed': False,
            'file_info': None,
            'status_message': f'No validator found for stage: {stage_key}'
        } 