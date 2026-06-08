#!/usr/bin/env python3
"""
Simple File Validation System
Validates files using available information without external dependencies
"""

import re
from typing import Dict, List

class SimpleFileValidator:
    """Simple but effective file validation"""
    
    def __init__(self):
        # HIPAA consent validation terms
        self.hipaa_terms = [
            'hipaa', 'consent', 'authorization', 'protected health information', 
            'phi', 'privacy', 'disclosure', 'patient signature', 'treatment authorization'
        ]
        
        # Valid file types for HIPAA documents
        self.valid_types = [
            'application/pdf',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/msword',
            'image/jpeg',
            'image/png',
            'image/tiff'
        ]
    
    def validate_hipaa_file(self, file_name: str, file_type: str, comment: str = "") -> Dict:
        """Validate if file is a legitimate HIPAA consent form"""
        
        validation = {
            'is_valid': False,
            'score': 0,
            'found_terms': [],
            'issues': [],
            'strengths': []
        }
        
        # 1. Check filename for HIPAA terms
        file_name_lower = file_name.lower()
        filename_terms = [term for term in self.hipaa_terms if term in file_name_lower]
        
        if filename_terms:
            validation['found_terms'].extend(filename_terms)
            validation['strengths'].append(f"Filename contains: {', '.join(filename_terms)}")
            validation['score'] += 30
        else:
            validation['issues'].append("Filename doesn't contain HIPAA/consent terms")
        
        # 2. Check file type
        if file_type.lower() in self.valid_types:
            validation['strengths'].append(f"Valid document type: {file_type}")
            validation['score'] += 25
        else:
            validation['issues'].append(f"Invalid file type: {file_type}")
        
        # 3. Check comment for HIPAA terms
        if comment:
            comment_lower = comment.lower()
            comment_terms = [term for term in self.hipaa_terms if term in comment_lower]
            
            if comment_terms:
                validation['found_terms'].extend(comment_terms)
                validation['strengths'].append(f"Comment contains: {', '.join(comment_terms)}")
                validation['score'] += 25
            else:
                validation['issues'].append("Comment doesn't contain HIPAA/consent terms")
        else:
            validation['issues'].append("No comment provided")
        
        # 4. Check for signature indicators
        signature_indicators = ['signed', 'signature', 'patient signature', 'date signed']
        all_text = f"{file_name_lower} {comment.lower()}"
        
        signature_found = any(indicator in all_text for indicator in signature_indicators)
        if signature_found:
            validation['strengths'].append("Contains signature indicators")
            validation['score'] += 20
        else:
            validation['issues'].append("No signature indicators found")
        
        # 5. Remove duplicate terms
        validation['found_terms'] = list(set(validation['found_terms']))
        
        # Determine if valid
        validation['is_valid'] = (
            validation['score'] >= 70 and  # Minimum score
            len(validation['found_terms']) >= 2 and  # At least 2 HIPAA terms
            file_type.lower() in self.valid_types  # Valid file type
        )
        
        return validation

# Example usage
if __name__ == "__main__":
    validator = SimpleFileValidator()
    
    # Test cases
    test_files = [
        {
            'name': 'HIPAA_Consent_Form_Signed.pdf',
            'type': 'application/pdf',
            'comment': 'Signed HIPAA consent and authorization form'
        },
        {
            'name': 'random_document.pdf',
            'type': 'application/pdf',
            'comment': 'Some random document'
        },
        {
            'name': 'patient_consent.docx',
            'type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'comment': 'Patient consent form for treatment'
        }
    ]
    
    for test_file in test_files:
        print(f"\n=== Validating: {test_file['name']} ===")
        result = validator.validate_hipaa_file(
            file_name=test_file['name'],
            file_type=test_file['type'],
            comment=test_file['comment']
        )
        
        print(f"Valid: {'✅' if result['is_valid'] else '❌'}")
        print(f"Score: {result['score']}/100")
        print(f"Found terms: {', '.join(result['found_terms'])}")
        
        if result['strengths']:
            print(f"Strengths: {', '.join(result['strengths'])}")
        if result['issues']:
            print(f"Issues: {', '.join(result['issues'])}") 