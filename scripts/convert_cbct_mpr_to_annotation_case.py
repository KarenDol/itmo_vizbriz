#!/usr/bin/env python3
"""
Convert CBCT MPR folder to annotation dataset case.

This script copies an existing CBCT_MPR folder into the new annotation_dataset structure
following the requirements v1.1 specification.

Usage:
    python scripts/convert_cbct_mpr_to_annotation_case.py --patient-id 10312 --folder-name "Michael_Hellenbecht_1" --case-id CBCT_0001
    python scripts/convert_cbct_mpr_to_annotation_case.py --patient-id 10312 --folder-name "Michael_Hellenbecht_1" --case-id CBCT_0001 --include-reference-views
"""

import os
import sys
import json
import boto3
import argparse
from typing import Optional, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask_app import create_app
from flask_app.logging_config import logger
from flask_app.annotator.structure_config import (
    SUPPORTED_STRUCTURES,
    build_default_structure_state,
)


def extract_voxel_spacing(mpr_manifest: Dict) -> Dict:
    """
    Extract voxel spacing from MPR manifest.
    
    Args:
        mpr_manifest: MPR manifest dictionary
    
    Returns:
        Dict with x_mm, y_mm, z_mm
    """
    # Try to get spacing from volume.spacing_mm
    spacing = None
    if 'volume' in mpr_manifest and 'spacing_mm' in mpr_manifest['volume']:
        vol_spacing = mpr_manifest['volume']['spacing_mm']
        spacing = {
            'x_mm': vol_spacing.get('x', 0.3),
            'y_mm': vol_spacing.get('y', 0.3),
            'z_mm': vol_spacing.get('z', 0.3)
        }
    elif 'spacing_mm' in mpr_manifest:
        # Try top-level spacing_mm
        spacing_mm = mpr_manifest['spacing_mm']
        if 'axial' in spacing_mm:
            axial_spacing = spacing_mm['axial']
            spacing = {
                'x_mm': axial_spacing.get('col', 0.3),
                'y_mm': axial_spacing.get('row', 0.3),
                'z_mm': mpr_manifest.get('volume', {}).get('spacing_mm', {}).get('z', 0.3)
            }
    
    # Default if not found
    if not spacing:
        logger.warning("Could not extract voxel spacing from manifest, using defaults")
        spacing = {'x_mm': 0.3, 'y_mm': 0.3, 'z_mm': 0.3}
    
    return spacing


def list_s3_objects_sorted(s3_client, bucket: str, prefix: str) -> List[str]:
    """
    List S3 objects under prefix and return sorted list of keys.
    """
    objects = []
    paginator = s3_client.get_paginator('list_objects_v2')
    
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        if 'Contents' in page:
            for obj in page['Contents']:
                if obj['Key'].endswith('.png'):
                    objects.append(obj['Key'])
    
    # Sort by filename to maintain order
    objects.sort()
    return objects


def convert_cbct_mpr_to_annotation_case(
    patient_id: int,
    folder_name: str,
    case_id: str,
    source_bucket: str = None,
    dest_bucket: str = 'vizbrizknowledgebase',
    include_reference_views: bool = False,
    dry_run: bool = False,
    skip_file_copy: bool = False
) -> Dict:
    """
    Convert CBCT MPR folder to annotation dataset case.
    
    Args:
        patient_id: Source patient ID
        folder_name: Source CBCT folder name
        case_id: Target case ID (e.g., CBCT_0001)
        source_bucket: Source S3 bucket (defaults to config)
        dest_bucket: Destination S3 bucket
        include_reference_views: Whether to copy coronal/sagittal slices
        dry_run: If True, only validate without copying
    
    Returns:
        Dict with conversion results
    """
    app = create_app()
    
    with app.app_context():
        # Get source bucket from config if not provided
        if not source_bucket:
            source_bucket = app.config.get('S3_BUCKET') or app.config.get('S3_BUCKET_NAME')
            if not source_bucket:
                raise ValueError("Source S3 bucket not configured. Please specify --source-bucket")
        
        region = (app.config.get('AWS_REGION')
                  or os.getenv('AWS_REGION')
                  or 'us-east-1')
        
        s3_client = boto3.client('s3', region_name=region)
        
        # Validate case_id format
        if not case_id.startswith('CBCT_') or not case_id[5:].isdigit():
            raise ValueError(f"Invalid case_id format. Expected CBCT_XXXX, got {case_id}")
        
        logger.info(f"Converting CBCT MPR to annotation case:")
        logger.info(f"  Source: patients/{patient_id}/imaging/cbct_mpr/{folder_name}/")
        logger.info(f"  Destination: annotation_dataset/{case_id}/")
        logger.info(f"  Source bucket: {source_bucket} (passed as parameter: {source_bucket is not None})")
        logger.info(f"  Dest bucket: {dest_bucket} (passed as parameter, default would be 'vizbrizknowledgebase')")
        logger.info(f"  Flask app config S3_BUCKET_NAME: {app.config.get('S3_BUCKET_NAME')}")
        logger.info(f"  Flask app config S3_BUCKET: {app.config.get('S3_BUCKET')}")
        
        if dry_run:
            logger.info("DRY RUN MODE - No files will be copied")
        
        # Source paths
        source_prefix = f"patients/{patient_id}/imaging/cbct_mpr/{folder_name}/"
        source_manifest_key = f"{source_prefix}manifest.json"
        
        # Destination paths
        dest_prefix = f"annotation_dataset/{case_id}/"
        dest_slices_prefix = f"{dest_prefix}slices/"
        dest_metadata_prefix = f"{dest_prefix}metadata/"
        dest_mpr_manifest_key = f"{dest_metadata_prefix}mpr_manifest.json"
        dest_voxel_spacing_key = f"{dest_metadata_prefix}voxel_spacing.json"
        dest_manifest_key = f"{dest_prefix}manifest.json"
        
        results = {
            'case_id': case_id,
            'source': {
                'patient_id': patient_id,
                'folder_name': folder_name,
                'bucket': source_bucket
            },
            'destination': {
                'bucket': dest_bucket,
                'prefix': dest_prefix
            },
            'files_copied': 0,
            'errors': []
        }
        
        # Step 1: Load source MPR manifest
        logger.info("Step 1: Loading source MPR manifest...")
        try:
            response = s3_client.get_object(Bucket=source_bucket, Key=source_manifest_key)
            mpr_manifest = json.loads(response['Body'].read().decode('utf-8'))
            logger.info(f"✓ Loaded MPR manifest (version: {mpr_manifest.get('version', 'unknown')})")
        except s3_client.exceptions.NoSuchKey:
            raise FileNotFoundError(f"MPR manifest not found: {source_manifest_key}")
        except Exception as e:
            raise RuntimeError(f"Failed to load MPR manifest: {e}")
        
        # Step 2: Extract voxel spacing
        logger.info("Step 2: Extracting voxel spacing...")
        voxel_spacing = extract_voxel_spacing(mpr_manifest)
        logger.info(f"✓ Voxel spacing: {voxel_spacing}")
        
        # Step 3: Copy axial slices (or skip if skip_file_copy=True)
        if skip_file_copy:
            logger.info("Step 3: Skipping file copy - will reference source files directly")
            # Just count files to get num_slices
            source_axial_prefix = f"{source_prefix}axial/"
            axial_slices = list_s3_objects_sorted(s3_client, source_bucket, source_axial_prefix)
            if not axial_slices:
                raise ValueError(f"No axial slices found at {source_axial_prefix}")
            logger.info(f"Found {len(axial_slices)} axial slices (not copying - will load from source)")
        else:
            logger.info("Step 3: Copying axial slices...")
            source_axial_prefix = f"{source_prefix}axial/"
            axial_slices = list_s3_objects_sorted(s3_client, source_bucket, source_axial_prefix)
            
            if not axial_slices:
                raise ValueError(f"No axial slices found at {source_axial_prefix}")
            
            logger.info(f"Found {len(axial_slices)} axial slices")
        
        if not dry_run and not skip_file_copy:
            # Copy and rename slices in parallel for better performance
            def copy_single_slice(args):
                idx, source_key, dest_filename = args
                dest_key = f"{dest_slices_prefix}{dest_filename}"
                try:
                    # Check if file already exists (skip if it does)
                    try:
                        s3_client.head_object(Bucket=dest_bucket, Key=dest_key)
                        return (idx, True, 'skipped')  # Already exists
                    except s3_client.exceptions.ClientError:
                        # File doesn't exist, proceed with copy
                        pass
                    
                    copy_source = {'Bucket': source_bucket, 'Key': source_key}
                    s3_client.copy_object(
                        CopySource=copy_source,
                        Bucket=dest_bucket,
                        Key=dest_key,
                        ContentType='image/png'
                    )
                    return (idx, True, None)
                except Exception as e:
                    return (idx, False, str(e))
            
            # Prepare copy tasks
            copy_tasks = [
                (idx, source_key, f"axial_{idx:03d}.png")
                for idx, source_key in enumerate(axial_slices)
            ]
            
            # Execute in parallel (use up to 20 workers for S3 operations)
            copied_count = 0
            skipped_count = 0
            with ThreadPoolExecutor(max_workers=20) as executor:
                future_to_task = {
                    executor.submit(copy_single_slice, task): task
                    for task in copy_tasks
                }
                
                for future in as_completed(future_to_task):
                    idx, success, status = future.result()
                    if success:
                        if status == 'skipped':
                            skipped_count += 1
                        else:
                            results['files_copied'] += 1
                            copied_count += 1
                        total_processed = copied_count + skipped_count
                        if total_processed % 50 == 0:
                            logger.info(f"  Processed {total_processed}/{len(axial_slices)} slices (copied: {copied_count}, skipped: {skipped_count})...")
                    else:
                        source_key = copy_tasks[idx][1]
                        error_msg = f"Failed to copy {source_key}: {status}"
                        logger.error(error_msg)
                        results['errors'].append(error_msg)
            
            logger.info(f"✓ Processed {len(axial_slices)} axial slices: {copied_count} copied, {skipped_count} skipped (already existed)")
        
        num_slices = len(axial_slices)
        
        # Step 4: Copy MPR manifest to metadata
        logger.info("Step 4: Copying MPR manifest to metadata...")
        if not dry_run:
            try:
                copy_source = {'Bucket': source_bucket, 'Key': source_manifest_key}
                s3_client.copy_object(
                    CopySource=copy_source,
                    Bucket=dest_bucket,
                    Key=dest_mpr_manifest_key,
                    ContentType='application/json'
                )
                results['files_copied'] += 1
                logger.info(f"✓ Copied MPR manifest to {dest_mpr_manifest_key}")
            except Exception as e:
                error_msg = f"Failed to copy MPR manifest: {e}"
                logger.error(error_msg)
                results['errors'].append(error_msg)
        
        # Step 5: Create voxel_spacing.json
        logger.info("Step 5: Creating voxel_spacing.json...")
        if not dry_run:
            try:
                s3_client.put_object(
                    Bucket=dest_bucket,
                    Key=dest_voxel_spacing_key,
                    Body=json.dumps(voxel_spacing, indent=2).encode('utf-8'),
                    ContentType='application/json'
                )
                results['files_copied'] += 1
                logger.info(f"✓ Created voxel_spacing.json")
            except Exception as e:
                error_msg = f"Failed to create voxel_spacing.json: {e}"
                logger.error(error_msg)
                results['errors'].append(error_msg)
        
        # Step 6: Create annotation manifest
        logger.info("Step 6: Creating annotation manifest...")
        annotation_manifest = {
            "case_id": case_id,
            "num_slices": num_slices,
            "voxel_spacing_mm": [
                voxel_spacing['x_mm'],
                voxel_spacing['y_mm'],
                voxel_spacing['z_mm']
            ],
            "structures": {
                struct: build_default_structure_state()
                for struct in SUPPORTED_STRUCTURES
            },
            "source_mpr_manifest": "metadata/mpr_manifest.json",
            "source": {
                "bucket": source_bucket,
                "patient_id": patient_id,
                "folder_name": folder_name,
                "prefix": source_prefix
            }
        }
        
        if not dry_run:
            try:
                s3_client.put_object(
                    Bucket=dest_bucket,
                    Key=dest_manifest_key,
                    Body=json.dumps(annotation_manifest, indent=2).encode('utf-8'),
                    ContentType='application/json'
                )
                results['files_copied'] += 1
                logger.info(f"✓ Created annotation manifest")
            except Exception as e:
                error_msg = f"Failed to create annotation manifest: {e}"
                logger.error(error_msg)
                results['errors'].append(error_msg)
        
        # Step 7: Create empty folder structure (by creating placeholder files)
        logger.info("Step 7: Creating folder structure...")
        folders_to_create = [
            f"{dest_prefix}segmentations/",
        ]
        for struct in SUPPORTED_STRUCTURES:
            folders_to_create.append(f"{dest_prefix}masks_pred/{struct}/")
            folders_to_create.append(f"{dest_prefix}masks_corrected/{struct}/")
        
        if include_reference_views:
            folders_to_create.extend([
                f"{dest_metadata_prefix}mpr_extra/coronal/",
                f"{dest_metadata_prefix}mpr_extra/sagittal/",
            ])
        
        if not dry_run:
            for folder_prefix in folders_to_create:
                # Create folder by uploading a placeholder (S3 doesn't have real folders)
                placeholder_key = f"{folder_prefix}.gitkeep"
                try:
                    s3_client.put_object(
                        Bucket=dest_bucket,
                        Key=placeholder_key,
                        Body=b'',
                        ContentType='text/plain'
                    )
                    results['files_copied'] += 1
                except Exception as e:
                    logger.warning(f"Failed to create folder {folder_prefix}: {e}")
        
        logger.info(f"✓ Created folder structure")
        
        # Step 8: Optionally copy coronal/sagittal slices
        if include_reference_views:
            if skip_file_copy:
                logger.info("Step 8: Skipping reference views copy - will reference source files directly")
            else:
                logger.info("Step 8: Copying reference views (coronal/sagittal)...")
            for plane in ['coronal', 'sagittal']:
                source_plane_prefix = f"{source_prefix}{plane}/"
                plane_slices = list_s3_objects_sorted(s3_client, source_bucket, source_plane_prefix)
                
                if plane_slices:
                    logger.info(f"Found {len(plane_slices)} {plane} slices")
                    dest_plane_prefix = f"{dest_metadata_prefix}mpr_extra/{plane}/"
                    
                    if not dry_run and not skip_file_copy:
                        def copy_reference_slice(source_key):
                            filename = source_key.split('/')[-1]
                            dest_key = f"{dest_plane_prefix}{filename}"
                            try:
                                # Check if file already exists (skip if it does)
                                try:
                                    s3_client.head_object(Bucket=dest_bucket, Key=dest_key)
                                    return (source_key, True, 'skipped')  # Already exists
                                except s3_client.exceptions.ClientError:
                                    # File doesn't exist, proceed with copy
                                    pass
                                
                                copy_source = {'Bucket': source_bucket, 'Key': source_key}
                                s3_client.copy_object(
                                    CopySource=copy_source,
                                    Bucket=dest_bucket,
                                    Key=dest_key,
                                    ContentType='image/png'
                                )
                                return (source_key, True, None)
                            except Exception as e:
                                return (source_key, False, str(e))
                        
                        # Copy in parallel
                        copied_count = 0
                        skipped_count = 0
                        with ThreadPoolExecutor(max_workers=20) as executor:
                            future_to_key = {
                                executor.submit(copy_reference_slice, source_key): source_key
                                for source_key in plane_slices
                            }
                            
                            for future in as_completed(future_to_key):
                                source_key, success, status = future.result()
                                if success:
                                    if status == 'skipped':
                                        skipped_count += 1
                                    else:
                                        results['files_copied'] += 1
                                        copied_count += 1
                                    total_processed = copied_count + skipped_count
                                    if total_processed % 50 == 0:
                                        logger.info(f"  Processed {total_processed}/{len(plane_slices)} {plane} slices (copied: {copied_count}, skipped: {skipped_count})...")
                                else:
                                    error_msg = f"Failed to copy {source_key}: {status}"
                                    logger.error(error_msg)
                                    results['errors'].append(error_msg)
                        
                        logger.info(f"✓ Processed {len(plane_slices)} {plane} slices: {copied_count} copied, {skipped_count} skipped (already existed)")
                        
                        logger.info(f"✓ Copied {len(plane_slices)} {plane} slices")
        
        # Summary
        logger.info("\n" + "="*60)
        logger.info("CONVERSION SUMMARY")
        logger.info("="*60)
        logger.info(f"Case ID: {case_id}")
        logger.info(f"Source: patients/{patient_id}/imaging/cbct_mpr/{folder_name}/")
        logger.info(f"Destination: annotation_dataset/{case_id}/")
        logger.info(f"Files copied: {results['files_copied']}")
        logger.info(f"Errors: {len(results['errors'])}")
        
        if results['errors']:
            logger.error("\nErrors encountered:")
            for error in results['errors']:
                logger.error(f"  - {error}")
        
        if dry_run:
            logger.info("\nDRY RUN - No files were actually copied")
        else:
            logger.info(f"\n✓ Conversion complete! Case {case_id} is ready for annotation.")
        
        return results


def validate_case_structure(case_id: str, bucket: str = 'vizbrizknowledgebase') -> Dict:
    """
    Validate that a case has the correct structure.
    
    Args:
        case_id: Case ID to validate
        bucket: S3 bucket name
    
    Returns:
        Dict with validation results
    """
    app = create_app()
    
    with app.app_context():
        region = (app.config.get('AWS_REGION')
                  or os.getenv('AWS_REGION')
                  or 'us-east-1')
        
        s3_client = boto3.client('s3', region_name=region)
        prefix = f"annotation_dataset/{case_id}/"
        
        validation = {
            'case_id': case_id,
            'valid': True,
            'errors': [],
            'warnings': [],
            'files_found': {}
        }
        
        # Required files/folders
        required = {
            'slices': f"{prefix}slices/",
            'manifest': f"{prefix}manifest.json",
            'mpr_manifest': f"{prefix}metadata/mpr_manifest.json",
            'voxel_spacing': f"{prefix}metadata/voxel_spacing.json",
            'segmentations': f"{prefix}segmentations/",
        }
        for struct in SUPPORTED_STRUCTURES:
            required[f'masks_pred_{struct}'] = f"{prefix}masks_pred/{struct}/"
            required[f'masks_corrected_{struct}'] = f"{prefix}masks_corrected/{struct}/"
        
        logger.info(f"Validating case structure for {case_id}...")
        
        for name, key in required.items():
            try:
                # Check if exists (list objects or head_object)
                if key.endswith('/'):
                    # It's a folder, list objects
                    response = s3_client.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1)
                    exists = 'Contents' in response
                else:
                    # It's a file, use head_object
                    try:
                        s3_client.head_object(Bucket=bucket, Key=key)
                        exists = True
                    except s3_client.exceptions.NoSuchKey:
                        exists = False
                
                validation['files_found'][name] = exists
                
                if not exists:
                    validation['valid'] = False
                    validation['errors'].append(f"Missing required: {name} ({key})")
                else:
                    logger.info(f"✓ Found: {name}")
            except Exception as e:
                validation['valid'] = False
                validation['errors'].append(f"Error checking {name}: {e}")
        
        # Count slices
        slices_prefix = f"{prefix}slices/"
        try:
            response = s3_client.list_objects_v2(Bucket=bucket, Prefix=slices_prefix)
            if 'Contents' in response:
                slice_count = len([obj for obj in response['Contents'] if obj['Key'].endswith('.png')])
                validation['files_found']['num_slices'] = slice_count
                logger.info(f"✓ Found {slice_count} slices")
            else:
                validation['warnings'].append("No slices found in slices/ directory")
        except Exception as e:
            validation['warnings'].append(f"Could not count slices: {e}")
        
        # Validate manifest structure
        try:
            response = s3_client.get_object(Bucket=bucket, Key=f"{prefix}manifest.json")
            manifest = json.loads(response['Body'].read().decode('utf-8'))
            
            required_fields = ['case_id', 'num_slices', 'voxel_spacing_mm', 'structures']
            for field in required_fields:
                if field not in manifest:
                    validation['errors'].append(f"Manifest missing field: {field}")
                    validation['valid'] = False
            
            if 'structures' in manifest:
                for struct in SUPPORTED_STRUCTURES:
                    if struct not in manifest['structures']:
                        validation['errors'].append(f"Manifest missing structure: {struct}")
                        validation['valid'] = False
            
            logger.info("✓ Manifest structure validated")
        except Exception as e:
            validation['errors'].append(f"Failed to validate manifest: {e}")
            validation['valid'] = False
        
        logger.info("\n" + "="*60)
        logger.info("VALIDATION SUMMARY")
        logger.info("="*60)
        logger.info(f"Case ID: {case_id}")
        logger.info(f"Valid: {validation['valid']}")
        logger.info(f"Errors: {len(validation['errors'])}")
        logger.info(f"Warnings: {len(validation['warnings'])}")
        
        if validation['errors']:
            logger.error("\nErrors:")
            for error in validation['errors']:
                logger.error(f"  - {error}")
        
        if validation['warnings']:
            logger.warning("\nWarnings:")
            for warning in validation['warnings']:
                logger.warning(f"  - {warning}")
        
        return validation


def main():
    parser = argparse.ArgumentParser(
        description='Convert CBCT MPR folder to annotation dataset case'
    )
    parser.add_argument(
        '--patient-id',
        type=int,
        required=True,
        help='Source patient ID'
    )
    parser.add_argument(
        '--folder-name',
        type=str,
        required=True,
        help='Source CBCT folder name'
    )
    parser.add_argument(
        '--case-id',
        type=str,
        required=True,
        help='Target case ID (e.g., CBCT_0001)'
    )
    parser.add_argument(
        '--source-bucket',
        type=str,
        help='Source S3 bucket (defaults to config)'
    )
    parser.add_argument(
        '--dest-bucket',
        type=str,
        default='vizbrizknowledgebase',
        help='Destination S3 bucket (default: vizbrizknowledgebase)'
    )
    parser.add_argument(
        '--include-reference-views',
        action='store_true',
        help='Copy coronal and sagittal slices to metadata/mpr_extra/'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Validate without copying files'
    )
    parser.add_argument(
        '--validate-only',
        action='store_true',
        help='Only validate existing case structure (skip conversion)'
    )
    
    args = parser.parse_args()
    
    if args.validate_only:
        # Just validate existing case
        validation = validate_case_structure(args.case_id, args.dest_bucket)
        sys.exit(0 if validation['valid'] else 1)
    else:
        # Convert
        try:
            results = convert_cbct_mpr_to_annotation_case(
                patient_id=args.patient_id,
                folder_name=args.folder_name,
                case_id=args.case_id,
                source_bucket=args.source_bucket,
                dest_bucket=args.dest_bucket,
                include_reference_views=args.include_reference_views,
                dry_run=args.dry_run
            )
            
            # Validate after conversion (unless dry run)
            if not args.dry_run:
                logger.info("\n" + "="*60)
                logger.info("VALIDATING CONVERTED CASE")
                logger.info("="*60)
                validation = validate_case_structure(args.case_id, args.dest_bucket)
                
                if validation['valid']:
                    logger.info("\n✓ Case structure is valid!")
                    sys.exit(0)
                else:
                    logger.error("\n✗ Case structure validation failed!")
                    sys.exit(1)
            else:
                sys.exit(0)
                
        except Exception as e:
            logger.error(f"Conversion failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            sys.exit(1)


if __name__ == '__main__':
    main()

