#!/usr/bin/env python3
"""
Upload Level 4 case cards to Knowledge Base
Generates anonymized case cards and uploads to S3 for KB ingestion
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask_app import create_app
from flask_app.services.l4_kb_uploader import L4KBUploader
from flask_app.models import L4DeviceDesign

def upload_to_kb(format="json", upload_to_s3=True, output_dir=None):
    """Upload case cards to knowledge base"""
    
    app = create_app()
    
    with app.app_context():
        print("="*80)
        print("Uploading Level 4 Case Cards to Knowledge Base")
        print("="*80)
        print(f"\nFormat: {format}")
        print(f"Upload to S3: {upload_to_s3}")
        if output_dir:
            print(f"Local output: {output_dir}")
        print()
        
        uploader = L4KBUploader()
        
        if upload_to_s3:
            # Upload to S3
            print("Uploading to S3...")
            results = uploader.upload_all_case_cards(format=format)
            
            print(f"\nResults:")
            print(f"  Total: {results['total']}")
            print(f"  Successful: {results['successful']}")
            print(f"  Failed: {results['failed']}")
            
            if results.get('uploaded_keys'):
                print(f"\nUploaded files:")
                for key in results['uploaded_keys'][:10]:  # Show first 10
                    print(f"  - {key}")
                if len(results['uploaded_keys']) > 10:
                    print(f"  ... and {len(results['uploaded_keys']) - 10} more")
        
        if output_dir:
            # Save locally
            print(f"\nSaving to local directory: {output_dir}")
            designs = L4DeviceDesign.query.all()
            saved = 0
            for design in designs:
                path = uploader.save_case_card_locally(design, output_dir=output_dir, format=format)
                if path:
                    saved += 1
            print(f"Saved {saved}/{len(designs)} case cards locally")
        
        print("\n" + "="*80)
        print("Done!")
        print("="*80)
        print("\nNext steps:")
        print("1. Files are now in S3 (or local directory)")
        print("2. Knowledge base will automatically sync from S3")
        print("3. Chatbot can now use clustering to match similar cases")
        print("4. Query KB with: 'Find similar cases with AHI 10-15 and posterior tongue'")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Upload Level 4 case cards to KB")
    parser.add_argument("--format", choices=["json", "text"], default="json",
                       help="Output format (json or text)")
    parser.add_argument("--no-s3", action="store_true",
                       help="Don't upload to S3 (only save locally)")
    parser.add_argument("--output-dir", type=str,
                       help="Local directory to save files (for testing)")
    
    args = parser.parse_args()
    
    upload_to_kb(
        format=args.format,
        upload_to_s3=not args.no_s3,
        output_dir=args.output_dir
    )
