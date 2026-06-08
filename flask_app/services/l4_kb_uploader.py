#!/usr/bin/env python3
"""
Level 4 Knowledge Base Uploader
Uploads anonymized case cards to S3 for knowledge base ingestion
"""

import json
import logging
import boto3
from typing import Dict, Any, Optional
from pathlib import Path
from flask_app.services.l4_case_card_generator import L4CaseCardGenerator
from flask_app.models import L4DeviceDesign, L4DeviceOption
from flask_app.extensions import db

logger = logging.getLogger(__name__)


class L4KBUploader:
    """Handles uploading Level 4 case cards to knowledge base via S3"""
    
    def __init__(self, s3_bucket: Optional[str] = None, kb_s3_prefix: str = "level4-case-cards/"):
        """
        Initialize KB uploader
        
        Args:
            s3_bucket: S3 bucket name (defaults to app config)
            kb_s3_prefix: S3 prefix for KB files (default: "level4-case-cards/")
        """
        import os
        self.s3_bucket = s3_bucket or os.getenv('S3_BUCKET_NAME')
        self.kb_s3_prefix = kb_s3_prefix
        self.card_generator = L4CaseCardGenerator()
        
        # Initialize S3 client
        try:
            self.s3_client = boto3.client('s3')
        except Exception as e:
            logger.warning(f"Could not initialize S3 client: {e}")
            self.s3_client = None
    
    def upload_case_card(self, device_design: L4DeviceDesign, 
                        format: str = "json") -> Optional[str]:
        """
        Generate and upload a case card to S3
        
        Args:
            device_design: L4DeviceDesign object
            format: "json" or "text"
            
        Returns:
            S3 key/path if successful, None otherwise
        """
        try:
            # Generate case card
            device_options = device_design.device_options.all()
            case_card = self.card_generator.generate_case_card(
                device_design=device_design,
                device_options=device_options
            )
            
            # Convert to format
            if format == "json":
                content = self.card_generator.generate_case_card_json(case_card)
                extension = "json"
            else:
                content = self.card_generator.generate_case_card_text(case_card)
                extension = "txt"
            
            # Generate S3 key
            # Use source_report_id but sanitize for S3
            report_id = device_design.source_report_id.replace(" ", "_").replace("(", "").replace(")", "")
            s3_key = f"{self.kb_s3_prefix}{report_id}_{device_design.design_context}.{extension}"
            
            # Upload to S3
            if self.s3_client and self.s3_bucket:
                self.s3_client.put_object(
                    Bucket=self.s3_bucket,
                    Key=s3_key,
                    Body=content.encode('utf-8'),
                    ContentType='application/json' if format == "json" else 'text/plain',
                    Metadata={
                        'case_type': 'level4_device_design',
                        'design_context': device_design.design_context,
                        'source_report': device_design.source_report_id
                    }
                )
                logger.info(f"Uploaded case card to S3: s3://{self.s3_bucket}/{s3_key}")
                return s3_key
            else:
                logger.warning("S3 client not available, skipping upload")
                return None
                
        except Exception as e:
            logger.error(f"Error uploading case card: {e}", exc_info=True)
            return None
    
    def upload_all_case_cards(self, format: str = "json") -> Dict[str, Any]:
        """
        Upload all device designs as case cards
        
        Args:
            format: "json" or "text"
            
        Returns:
            Dictionary with upload results
        """
        results = {
            "total": 0,
            "successful": 0,
            "failed": 0,
            "uploaded_keys": []
        }
        
        try:
            # Get all device designs
            device_designs = L4DeviceDesign.query.all()
            results["total"] = len(device_designs)
            
            for design in device_designs:
                s3_key = self.upload_case_card(design, format=format)
                if s3_key:
                    results["successful"] += 1
                    results["uploaded_keys"].append(s3_key)
                else:
                    results["failed"] += 1
            
            logger.info(f"Uploaded {results['successful']}/{results['total']} case cards")
            
        except Exception as e:
            logger.error(f"Error uploading case cards: {e}", exc_info=True)
            results["error"] = str(e)
        
        return results
    
    def save_case_card_locally(self, device_design: L4DeviceDesign,
                              output_dir: str = "/tmp/level4-case-cards",
                              format: str = "json") -> Optional[str]:
        """
        Save case card to local file system (for testing or manual upload)
        
        Args:
            device_design: L4DeviceDesign object
            output_dir: Output directory
            format: "json" or "text"
            
        Returns:
            File path if successful
        """
        try:
            # Create output directory
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            
            # Generate case card
            device_options = device_design.device_options.all()
            case_card = self.card_generator.generate_case_card(
                device_design=device_design,
                device_options=device_options
            )
            
            # Convert to format
            if format == "json":
                content = self.card_generator.generate_case_card_json(case_card)
                extension = "json"
            else:
                content = self.card_generator.generate_case_card_text(case_card)
                extension = "txt"
            
            # Generate filename
            report_id = device_design.source_report_id.replace(" ", "_").replace("(", "").replace(")", "")
            filename = f"{report_id}_{device_design.design_context}.{extension}"
            filepath = Path(output_dir) / filename
            
            # Write file
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            
            logger.info(f"Saved case card to: {filepath}")
            return str(filepath)
            
        except Exception as e:
            logger.error(f"Error saving case card: {e}", exc_info=True)
            return None
