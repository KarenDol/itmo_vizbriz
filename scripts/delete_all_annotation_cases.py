#!/usr/bin/env python3
"""
Delete all annotation cases from the annotation dataset.

This script removes all cases from the annotation_dataset/ directory in S3.
Use with caution - this is irreversible!

Usage:
    python scripts/delete_all_annotation_cases.py
    python scripts/delete_all_annotation_cases.py --dry-run  # Preview what would be deleted
    python scripts/delete_all_annotation_cases.py --confirm  # Actually delete
"""

import os
import sys
import boto3
import argparse
from typing import List

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask_app import create_app
from flask_app.logging_config import logger


def delete_all_annotation_cases(
    bucket: str = 'vizbrizknowledgebase',
    dry_run: bool = True,
    confirm: bool = False
) -> dict:
    """
    Delete all annotation cases from the annotation dataset.
    
    Args:
        bucket: S3 bucket containing annotation cases
        dry_run: If True, only list what would be deleted (default: True for safety)
        confirm: If True, actually delete (requires explicit confirmation)
    
    Returns:
        Dict with deletion results
    """
    app = create_app()
    
    with app.app_context():
        region = (app.config.get('AWS_REGION')
                  or os.getenv('AWS_REGION')
                  or 'us-east-1')
        
        s3_client = boto3.client('s3', region_name=region)
        
        prefix = 'annotation_dataset/'
        
        results = {
            'cases_found': [],
            'objects_deleted': 0,
            'errors': []
        }
        
        logger.info(f"Scanning for annotation cases in bucket: {bucket}, prefix: {prefix}")
        
        # List all objects under annotation_dataset/
        paginator = s3_client.get_paginator('list_objects_v2')
        all_objects = []
        
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            if 'Contents' in page:
                for obj in page['Contents']:
                    all_objects.append(obj['Key'])
        
        if not all_objects:
            logger.info("No annotation cases found - nothing to delete")
            return results
        
        # Group objects by case_id (extract from path: annotation_dataset/CBCT_XXXX/...)
        cases = {}
        for key in all_objects:
            # Extract case_id from path
            parts = key.split('/')
            if len(parts) >= 2 and parts[0] == 'annotation_dataset':
                case_id = parts[1]
                if case_id.startswith('CBCT_'):
                    if case_id not in cases:
                        cases[case_id] = []
                    cases[case_id].append(key)
        
        results['cases_found'] = list(cases.keys())
        logger.info(f"Found {len(cases)} annotation cases:")
        for case_id in sorted(cases.keys()):
            num_files = len(cases[case_id])
            logger.info(f"  - {case_id}: {num_files} files")
        
        if dry_run and not confirm:
            logger.info("\n=== DRY RUN MODE ===")
            logger.info("No files will be deleted. Use --confirm to actually delete.")
            logger.info(f"Total objects that would be deleted: {len(all_objects)}")
            return results
        
        if not confirm:
            logger.error("\n=== SAFETY CHECK FAILED ===")
            logger.error("You must use --confirm flag to actually delete files.")
            logger.error("This is a safety measure to prevent accidental deletion.")
            return results
        
        # Actually delete all objects
        logger.warning(f"\n=== DELETING ALL ANNOTATION CASES ===")
        logger.warning(f"This will delete {len(all_objects)} objects from {len(cases)} cases")
        logger.warning("This action is IRREVERSIBLE!")
        
        # Delete in batches (S3 allows up to 1000 objects per delete request)
        batch_size = 1000
        deleted_count = 0
        
        for i in range(0, len(all_objects), batch_size):
            batch = all_objects[i:i + batch_size]
            
            # Prepare delete request
            delete_objects = [{'Key': key} for key in batch]
            
            try:
                response = s3_client.delete_objects(
                    Bucket=bucket,
                    Delete={
                        'Objects': delete_objects,
                        'Quiet': False
                    }
                )
                
                # Count successful deletions
                if 'Deleted' in response:
                    deleted_count += len(response['Deleted'])
                
                # Log errors if any
                if 'Errors' in response:
                    for error in response['Errors']:
                        error_msg = f"Failed to delete {error['Key']}: {error['Message']}"
                        logger.error(error_msg)
                        results['errors'].append(error_msg)
                
                logger.info(f"Deleted batch {i//batch_size + 1}: {len(batch)} objects")
                
            except Exception as e:
                error_msg = f"Error deleting batch: {e}"
                logger.error(error_msg)
                results['errors'].append(error_msg)
        
        results['objects_deleted'] = deleted_count
        logger.info(f"\n=== DELETION COMPLETE ===")
        logger.info(f"Deleted {deleted_count} objects from {len(cases)} cases")
        
        if results['errors']:
            logger.warning(f"Encountered {len(results['errors'])} errors during deletion")
        
        return results


def main():
    parser = argparse.ArgumentParser(description='Delete all annotation cases')
    parser.add_argument('--bucket', default='vizbrizknowledgebase',
                       help='S3 bucket containing annotation cases (default: vizbrizknowledgebase)')
    parser.add_argument('--dry-run', action='store_true', default=True,
                       help='Preview what would be deleted (default: True)')
    parser.add_argument('--confirm', action='store_true',
                       help='Actually delete files (required for deletion)')
    
    args = parser.parse_args()
    
    # If --confirm is used, disable dry-run
    if args.confirm:
        args.dry_run = False
    
    results = delete_all_annotation_cases(
        bucket=args.bucket,
        dry_run=args.dry_run,
        confirm=args.confirm
    )
    
    if args.dry_run and not args.confirm:
        print("\nTo actually delete, run with --confirm flag:")
        print(f"  python {sys.argv[0]} --confirm")
    elif results['objects_deleted'] > 0:
        print(f"\n✓ Successfully deleted {results['objects_deleted']} objects")
        print(f"  Cases removed: {len(results['cases_found'])}")
        if results['errors']:
            print(f"  Errors: {len(results['errors'])}")


if __name__ == '__main__':
    main()
