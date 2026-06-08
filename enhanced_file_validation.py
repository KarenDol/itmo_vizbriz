#!/usr/bin/env python3
"""
Enhanced File Validation System
Validates files by actually reading their content instead of simulating it
"""

import os
import re
import json
from typing import Dict, List, Optional, Tuple
import PyPDF2
import docx
from PIL import Image
import pytesseract
import io

class EnhancedFileValidator:
    """Enhanced file validation with real content extraction"""
    
    def __init__(self):
        self.hipaa_consent_terms = [
            'hipaa', 'consent', 'authorization', 'protected health information', 
            'phi', 'privacy', 'disclosure', 'patient signature', 'treatment authorization',
            'health insurance portability', 'accountability act'
        ]
        
        self.required_sections = [
            'patient authorization',
            'use and disclosure',
            'protected health information',
            'patient signature'
        ]
    
    def extract_text_from_pdf(self, file_path: str) -> str:
        """Extract text from PDF files"""
        try:
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                for page in pdf_reader.pages:
                    text += page.extract_text() + "\n"
                return text.lower()
        except Exception as e:
            print(f"Error extracting PDF text: {e}")
            return ""
    
    def extract_text_from_docx(self, file_path: str) -> str:
        """Extract text from DOCX files"""
        try:
            doc = docx.Document(file_path)
            text = ""
            for paragraph in doc.paragraphs:
                text += paragraph.text + "\n"
            return text.lower()
        except Exception as e:
            print(f"Error extracting DOCX text: {e}")
            return ""
    
    def extract_text_from_image(self, file_path: str) -> str:
        """Extract text from images using OCR"""
        try:
            image = Image.open(file_path)
            text = pytesseract.image_to_string(image)
            return text.lower()
        except Exception as e:
            print(f"Error extracting image text: {e}")
            return ""
    
    def extract_text_from_txt(self, file_path: str) -> str:
        """Extract text from plain text files"""
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                return file.read().lower()
        except Exception as e:
            print(f"Error extracting text: {e}")
            return ""
    
    def validate_hipaa_consent_content(self, file_path: str, file_type: str) -> Dict:
        """Validate if file contains genuine HIPAA consent content"""
        validation_result = {
            'is_valid': False,
            'found_terms': [],
            'missing_sections': [],
            'content_score': 0,
            'validation_method': 'content_extraction',
            'extracted_text': '',
            'errors': []
        }
        
        try:
            # Extract text based on file type
            if file_type.lower() == 'application/pdf':
                extracted_text = self.extract_text_from_pdf(file_path)
            elif file_type.lower() in ['application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/msword']:
                extracted_text = self.extract_text_from_docx(file_path)
            elif file_type.lower().startswith('image/'):
                extracted_text = self.extract_text_from_image(file_path)
            elif file_type.lower() in ['text/plain', 'text/html']:
                extracted_text = self.extract_text_from_txt(file_path)
            else:
                validation_result['errors'].append(f"Unsupported file type: {file_type}")
                return validation_result
            
            validation_result['extracted_text'] = extracted_text
            
            # Check for HIPAA/consent terms
            found_terms = []
            for term in self.hipaa_consent_terms:
                if term in extracted_text:
                    found_terms.append(term)
            
            validation_result['found_terms'] = found_terms
            
            # Check for required sections
            missing_sections = []
            for section in self.required_sections:
                if section not in extracted_text:
                    missing_sections.append(section)
            
            validation_result['missing_sections'] = missing_sections
            
            # Calculate content score
            term_score = len(found_terms) / len(self.hipaa_consent_terms) * 50
            section_score = (len(self.required_sections) - len(missing_sections)) / len(self.required_sections) * 50
            validation_result['content_score'] = term_score + section_score
            
            # Determine if valid
            validation_result['is_valid'] = (
                len(found_terms) >= 3 and  # At least 3 HIPAA terms
                len(missing_sections) <= 1 and  # Missing at most 1 required section
                validation_result['content_score'] >= 70  # Minimum content score
            )
            
        except Exception as e:
            validation_result['errors'].append(f"Validation error: {str(e)}")
        
        return validation_result
    
    def validate_file_structure(self, file_path: str, file_type: str) -> Dict:
        """Validate file structure and format"""
        structure_result = {
            'is_valid': False,
            'file_size': 0,
            'file_extension': '',
            'structure_issues': [],
            'validation_method': 'structure_analysis'
        }
        
        try:
            # Get file info
            file_size = os.path.getsize(file_path)
            file_extension = os.path.splitext(file_path)[1].lower()
            
            structure_result['file_size'] = file_size
            structure_result['file_extension'] = file_extension
            
            # Validate file size (not too small, not too large)
            if file_size < 1024:  # Less than 1KB
                structure_result['structure_issues'].append("File too small - likely empty or corrupted")
            elif file_size > 50 * 1024 * 1024:  # More than 50MB
                structure_result['structure_issues'].append("File too large - may not be a valid document")
            
            # Validate file extension matches MIME type
            expected_extensions = {
                'application/pdf': '.pdf',
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
                'application/msword': '.doc',
                'image/jpeg': '.jpg',
                'image/png': '.png',
                'text/plain': '.txt'
            }
            
            expected_ext = expected_extensions.get(file_type.lower())
            if expected_ext and file_extension != expected_ext:
                structure_result['structure_issues'].append(f"File extension {file_extension} doesn't match MIME type {file_type}")
            
            # Determine if structure is valid
            structure_result['is_valid'] = len(structure_result['structure_issues']) == 0
            
        except Exception as e:
            structure_result['structure_issues'].append(f"Structure validation error: {str(e)}")
        
        return structure_result
    
    def validate_signature_presence(self, file_path: str, file_type: str) -> Dict:
        """Check for signature indicators in the document"""
        signature_result = {
            'has_signature': False,
            'signature_indicators': [],
            'validation_method': 'signature_detection'
        }
        
        try:
            # Extract text content
            if file_type.lower() == 'application/pdf':
                extracted_text = self.extract_text_from_pdf(file_path)
            elif file_type.lower().startswith('image/'):
                extracted_text = self.extract_text_from_image(file_path)
            else:
                extracted_text = self.extract_text_from_docx(file_path) if 'word' in file_type.lower() else ""
            
            # Look for signature indicators
            signature_indicators = [
                'signed', 'signature', 'signed by', 'patient signature', 'date signed',
                'authorized by', 'signature line', 'sign here', 'patient initials'
            ]
            
            found_indicators = []
            for indicator in signature_indicators:
                if indicator in extracted_text.lower():
                    found_indicators.append(indicator)
            
            signature_result['signature_indicators'] = found_indicators
            signature_result['has_signature'] = len(found_indicators) >= 2  # At least 2 signature indicators
            
        except Exception as e:
            signature_result['signature_indicators'].append(f"Signature detection error: {str(e)}")
        
        return signature_result
    
    def comprehensive_validation(self, file_path: str, file_type: str, file_name: str, comment: str = "") -> Dict:
        """Perform comprehensive file validation"""
        print(f"=== Comprehensive Validation for {file_name} ===")
        
        # Basic metadata validation
        metadata_valid = self.validate_metadata(file_name, comment)
        print(f"Metadata validation: {'✅' if metadata_valid else '❌'}")
        
        # Structure validation
        structure_result = self.validate_file_structure(file_path, file_type)
        print(f"Structure validation: {'✅' if structure_result['is_valid'] else '❌'}")
        if structure_result['structure_issues']:
            print(f"  Issues: {', '.join(structure_result['structure_issues'])}")
        
        # Content validation
        content_result = self.validate_hipaa_consent_content(file_path, file_type)
        print(f"Content validation: {'✅' if content_result['is_valid'] else '❌'}")
        print(f"  Content score: {content_result['content_score']:.1f}/100")
        print(f"  Found terms: {', '.join(content_result['found_terms'])}")
        if content_result['missing_sections']:
            print(f"  Missing sections: {', '.join(content_result['missing_sections'])}")
        
        # Signature validation
        signature_result = self.validate_signature_presence(file_path, file_type)
        print(f"Signature validation: {'✅' if signature_result['has_signature'] else '❌'}")
        if signature_result['signature_indicators']:
            print(f"  Signature indicators: {', '.join(signature_result['signature_indicators'])}")
        
        # Overall validation result
        overall_valid = (
            metadata_valid and
            structure_result['is_valid'] and
            content_result['is_valid'] and
            signature_result['has_signature']
        )
        
        return {
            'overall_valid': overall_valid,
            'metadata_valid': metadata_valid,
            'structure_valid': structure_result['is_valid'],
            'content_valid': content_result['is_valid'],
            'signature_valid': signature_result['has_signature'],
            'content_score': content_result['content_score'],
            'found_terms': content_result['found_terms'],
            'signature_indicators': signature_result['signature_indicators'],
            'validation_details': {
                'metadata': {'valid': metadata_valid},
                'structure': structure_result,
                'content': content_result,
                'signature': signature_result
            }
        }
    
    def validate_metadata(self, file_name: str, comment: str) -> bool:
        """Validate file metadata (name and comment)"""
        file_name_lower = file_name.lower()
        comment_lower = comment.lower()
        
        # Check for HIPAA/consent terms in metadata
        metadata_terms = ['hipaa', 'consent', 'authorization']
        found_terms = [term for term in metadata_terms if term in file_name_lower or term in comment_lower]
        
        return len(found_terms) >= 1  # At least one term in metadata

# Example usage
if __name__ == "__main__":
    validator = EnhancedFileValidator()
    
    # Example validation
    test_file = "example_hipaa_consent.pdf"
    if os.path.exists(test_file):
        result = validator.comprehensive_validation(
            file_path=test_file,
            file_type="application/pdf",
            file_name="HIPAA_Consent_Form_Signed.pdf",
            comment="Signed HIPAA consent and authorization form"
        )
        
        print(f"\n=== Final Result ===")
        print(f"Overall Valid: {'✅' if result['overall_valid'] else '❌'}")
        print(f"Content Score: {result['content_score']:.1f}/100")
    else:
        print("Test file not found. Create a sample HIPAA consent PDF to test.") 