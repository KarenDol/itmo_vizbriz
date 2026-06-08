"""
File Categorization Service for Reports & Files Tab

This service wraps the existing fetch_patient_details() function and provides
categorization logic specific to the Reports & Files tab requirements.

Maintains backward compatibility while adding new categorization capabilities.
"""

import logging
from typing import Dict, List, Optional, Tuple
import os
import mimetypes
from flask import current_app

logger = logging.getLogger(__name__)


class FileCategorization:
    """Service for categorizing patient files for the Reports & Files tab"""
    
    # Report level definitions
    REPORT_LEVELS = {
        1: {
            "title": "Sleep Risk Screening Report – Patient Awareness & Motivation",
            "keywords": ["sleep risk", "screening", "level 1", "awareness"],
            "description": "Initial screening report",
        },
        2: {
            "title": "Action-Driven Sleep Risk Report – Personalized Call to Action",
            "keywords": ["action", "level 2", "call to action"],
            "description": "Actionable recommendations report",
        },
        3: {
            "title": "Integrated Sleep Data Report – Therapeutic Pathways Report",
            "keywords": ["integrated", "level 3", "therapeutic", "pathways"],
            "description": "Comprehensive sleep data analysis",
        },
        4: {
            "title": "Personalized Treatment Planning Report – Treatment Guidance Report",
            "keywords": ["treatment planning", "level 4", "guidance"],
            "description": "Treatment planning recommendations",
        },
        5: {
            "title": "OSA Therapy Response Report – Patient-Reported Progress Evaluation",
            "keywords": ["therapy response", "level 5", "progress"],
            "description": "Therapy response tracking",
        },
        6: {
            "title": "OSA Therapy Effectiveness Report – Data-Integrated Validation of Therapeutic Outcomes",
            "keywords": ["effectiveness", "level 6", "outcomes", "validation"],
            "description": "Therapeutic effectiveness validation",
        },
        7: {
            "title": "OSA Transitional Re-Assessment Report – Integrated Evaluation & Insight for Continued Multi-Phase Care",
            "keywords": ["transitional", "level 7", "reassessment", "multi-phase"],
            "description": "Comprehensive reassessment",
        }
    }
    
    @staticmethod
    def categorize_for_reports_tab(patient_id: int, uploaded_files: Dict, 
                                   uploaded_files_one_dcm: Dict,
                                   cbct_directories: List[str],
                                   admin_files_list: List = None) -> Dict:
        """
        Transform existing file structure into Reports & Files tab format
        
        Args:
            patient_id: Patient ID
            uploaded_files: Dictionary from fetch_patient_details()
            uploaded_files_one_dcm: DICOM files dictionary
            cbct_directories: List of CBCT directory names
            admin_files_list: List of AdminFile objects
            
        Returns:
            Dictionary with categorized files: {reports, images, dicom, stl, documents}
        """
        logger.info(f"Categorizing files for Reports & Files tab - Patient {patient_id}")
        
        result = {
            'reports': [],
            'images': [],
            'dicom': [],
            'stl': [],
            'documents': []
        }
        
        # 1. Extract Reports (from admin files and categorized uploads)
        result['reports'] = FileCategorization._extract_reports(
            uploaded_files.get('reports', []),
            admin_files_list
        )
        
        # 2. Extract Images (clinical pictures, etc.)
        result['images'] = FileCategorization._extract_images(uploaded_files)
        
        # 3. Extract DICOM folders
        result['dicom'] = FileCategorization._extract_dicom_folders(
            cbct_directories,
            uploaded_files,
            patient_id
        )
        
        # 4. Extract STL files
        result['stl'] = FileCategorization._extract_stl_files(uploaded_files)
        
        # 5. Extract Documents (billing, medical background, etc.)
        result['documents'] = FileCategorization._extract_documents(uploaded_files)
        
        # Log summary
        logger.info(f"Patient {patient_id} file summary: "
                   f"Reports={len(result['reports'])}, "
                   f"Images={len(result['images'])}, "
                   f"DICOM={len(result['dicom'])}, "
                   f"STL={len(result['stl'])}, "
                   f"Documents={len(result['documents'])}")
        
        return result
    
    @staticmethod
    def _extract_reports(report_files: List[Dict], admin_files_list: List = None) -> List[Dict]:
        """Extract and categorize report files by level"""
        reports = []
        
        # Process regular report files
        for file_data in report_files:
            report = {
                'id': file_data.get('id'),
                'name': file_data.get('name', ''),
                'file_size': file_data.get('file_size', 0),
                's3_key': file_data.get('s3_key', ''),
                'file_table': 'adminfiles',
                'report_level': FileCategorization._detect_report_level(file_data.get('name', '')),
                'display_title': None,
                'created_at': file_data.get('upload_date'),
                'category': file_data.get('category', 'reports')
            }
            
            # Set display title based on detected level
            if report['report_level']:
                level_info = FileCategorization.REPORT_LEVELS.get(report['report_level'])
                if level_info:
                    report['display_title'] = level_info['title']
            
            reports.append(report)
        
        # Process admin files if provided
        if admin_files_list:
            for admin_file in admin_files_list:
                # Check if already in list
                if any(r['id'] == admin_file.id and r['file_table'] == 'adminfiles' for r in reports):
                    continue
                
                # Include ALL database fields from adminfiles table
                report = {
                    # Database fields from adminfiles table
                    'id': admin_file.id,
                    'name': admin_file.name,
                    'patient_id': admin_file.patient_id,
                    'file_type': admin_file.file_type,
                    'file_size': admin_file.file_size or 0,
                    's3_key': admin_file.s3_key,
                    'upload_date': admin_file.upload_date.isoformat() if admin_file.upload_date else None,
                    'is_public': admin_file.is_public,
                    'file_category': admin_file.file_category,
                    'analyzed': admin_file.analyzed,
                    
                    # Extra fields for Reports & Files tab
                    'file_table': 'adminfiles',
                    'report_level': FileCategorization._detect_report_level(admin_file.name),
                    'display_title': None,
                }
                
                # Set display title if not already set
                if not report['display_title'] and report['report_level']:
                    level_info = FileCategorization.REPORT_LEVELS.get(report['report_level'])
                    if level_info:
                        report['display_title'] = level_info['title']
                
                reports.append(report)
        
        # Sort by report level (ascending), then by date (descending)
        reports.sort(key=lambda x: (x['report_level'] or 999, -(x['created_at'].timestamp() if x['created_at'] else 0)))
        
        return reports
    
    @staticmethod
    def _extract_images(uploaded_files: Dict) -> List[Dict]:
        """Extract image files (clinical pictures, etc.)"""
        images = []
        
        # Categories that contain images
        image_categories = ['clinical_pictures', 'intraoral_scan']
        
        for category in image_categories:
            files = uploaded_files.get(category, [])
            for file_data in files:
                filename = file_data.get('name', '').lower()
                
                # Check if it's an image file
                if FileCategorization._is_image_file(filename):
                    images.append({
                        # Database fields from files table
                        'id': file_data.get('id'),
                        'name': file_data.get('name', ''),
                        'patient_id': file_data.get('patient_id'),
                        'upload_date': file_data.get('upload_date'),
                        'file_type': file_data.get('file_type'),
                        'file_size': file_data.get('file_size', 0),
                        's3_key': file_data.get('s3_key'),
                        'category': file_data.get('category', category),
                        'subcategory': file_data.get('subcategory'),
                        'comment': file_data.get('comment'),
                        'mapping': file_data.get('mapping'),
                        'analyzed': file_data.get('analyzed', False),
                        
                        # Extra fields for Reports & Files tab
                        'file_table': 'files',
                        'is_panoramic': 'panoramic' in filename or 'pano' in filename
                    })
        
        return images
    
    @staticmethod
    def _extract_dicom_folders(cbct_directories: List[str], uploaded_files: Dict, 
                               patient_id: int) -> List[Dict]:
        """Extract DICOM folder information"""
        dicom_folders = []
        
        for dir_name in cbct_directories:
            # Extract display name (remove prefix)
            display_name = dir_name.replace(f'patients/{patient_id}/cbct/', '')
            
            dicom_folders.append({
                'folder_id': dir_name,
                'display': display_name,
                'study_uid': None,  # Could be extracted from DICOM metadata
                'count': None,  # File count - would need to query S3
                'patient_id': patient_id
            })
        
        return dicom_folders
    
    @staticmethod
    def _extract_stl_files(uploaded_files: Dict) -> List[Dict]:
        """Extract STL files"""
        stl_files = []
        
        # Check all categories for STL files
        for category, files in uploaded_files.items():
            for file_data in files:
                filename = file_data.get('name', '').lower()
                
                if filename.endswith('.stl'):
                    stl_files.append({
                        # Database fields from files table
                        'id': file_data.get('id'),
                        'name': file_data.get('name', ''),
                        'patient_id': file_data.get('patient_id'),
                        'upload_date': file_data.get('upload_date'),
                        'file_type': file_data.get('file_type'),
                        'file_size': file_data.get('file_size', 0),
                        's3_key': file_data.get('s3_key'),
                        'category': file_data.get('category', category),
                        'subcategory': file_data.get('subcategory'),
                        'comment': file_data.get('comment'),
                        'mapping': file_data.get('mapping'),
                        'analyzed': file_data.get('analyzed', False),
                        
                        # Extra fields for Reports & Files tab
                        'file_table': 'files',
                        'viewer_url': f'/reports-files/viewer/stl/{file_data.get("id")}'
                    })
        
        return stl_files
    
    @staticmethod
    def _extract_documents(uploaded_files: Dict) -> List[Dict]:
        """Extract document files (billing, medical background, etc.)"""
        documents = []
        
        # Categories that contain documents
        document_categories = ['billing', 'medical_background', 'sleep_test']
        
        for category in document_categories:
            files = uploaded_files.get(category, [])
            for file_data in files:
                filename = file_data.get('name', '').lower()
                
                # Skip image files and STL files
                if FileCategorization._is_image_file(filename) or filename.endswith('.stl'):
                    continue
                
                # Determine if previewable
                previewable = filename.endswith(('.pdf', '.docx', '.doc', '.txt'))
                
                documents.append({
                    'id': file_data.get('id'),
                    'name': file_data.get('name', ''),
                    'file_size': file_data.get('file_size', 0),
                    'file_table': 'files',
                    'category': 'Documents' if category == 'billing' else category,
                    'previewable': previewable,
                    'created_at': None
                })
        
        return documents
    
    @staticmethod
    def _detect_report_level(filename: str) -> Optional[int]:
        """
        Detect report level from filename
        
        Args:
            filename: Name of the file
            
        Returns:
            Report level (1-7) or None if not detected
        """
        filename_lower = filename.lower()
        
        # Try direct level detection
        for level in range(1, 8):
            if f'level {level}' in filename_lower or f'level_{level}' in filename_lower:
                return level
        
        # Try keyword matching
        for level, info in FileCategorization.REPORT_LEVELS.items():
            keywords = info.get('keywords', [])
            for keyword in keywords:
                if keyword.lower() in filename_lower:
                    return level
        
        return None
    
    @staticmethod
    def _is_image_file(filename: str) -> bool:
        """Check if file is an image based on extension"""
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp'}
        ext = os.path.splitext(filename.lower())[1]
        return ext in image_extensions
    
    @staticmethod
    def get_report_level_info(level: int) -> Optional[Dict]:
        """Get information about a specific report level"""
        return FileCategorization.REPORT_LEVELS.get(level)
    
    @staticmethod
    def get_all_report_levels() -> Dict:
        """Get all report level definitions"""
        return FileCategorization.REPORT_LEVELS

