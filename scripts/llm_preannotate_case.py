#!/usr/bin/env python3
"""
LLM Pre-Annotation Batch Processing Script

This script processes all slices for a case/structure through LLM segmentation
and saves the predicted masks for human annotators to review and correct.

Usage:
    python scripts/llm_preannotate_case.py <case_id> <structure> [--slice-range START END]
    
Examples:
    # Process all slices for airway
    python scripts/llm_preannotate_case.py CBCT_0001 airway
    
    # Process specific slice range
    python scripts/llm_preannotate_case.py CBCT_0001 airway --slice-range 0 50
    
    # Process all supported structures for a case
    for structure in airway uvula soft_palate tongue_base tongue_body mandible_outline lateral_pharyngeal_walls nasal_airway; do
        python scripts/llm_preannotate_case.py CBCT_0001 $structure
    done
"""

import sys
import os
import json
import logging
import argparse
import boto3
from pathlib import Path
from typing import List, Tuple

# Add parent directory to path to import flask_app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.llm_segment_cbct_slice import (
    load_slice_from_s3,
    call_llm_for_segmentation,
    validate_mask,
    save_mask_to_s3,
    get_annotation_bucket,
    get_s3_client
)
from scripts.build_mask_volume import build_mask_volume, MASK_SOURCE_DIRS
from flask_app.annotator.structure_config import SUPPORTED_STRUCTURES, ensure_manifest_structures

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_case_info(case_id: str) -> dict:
    """Get case metadata including number of slices."""
    bucket = get_annotation_bucket()
    s3_client = get_s3_client()
    
    manifest_key = f"annotation_dataset/{case_id}/manifest.json"
    
    try:
        response = s3_client.get_object(Bucket=bucket, Key=manifest_key)
        manifest = json.loads(response['Body'].read().decode('utf-8'))
        return manifest
    except s3_client.exceptions.NoSuchKey:
        raise FileNotFoundError(f"Case {case_id} not found - manifest.json missing")
    except Exception as e:
        raise Exception(f"Error loading case info: {e}")


def update_manifest_llm_status(case_id: str, structure: str, status: str, 
                               processed_slices: List[int], failed_slices: List[int]):
    """Update manifest.json with LLM processing status."""
    bucket = get_annotation_bucket()
    s3_client = get_s3_client()
    
    manifest_key = f"annotation_dataset/{case_id}/manifest.json"
    
    try:
        # Load existing manifest
        response = s3_client.get_object(Bucket=bucket, Key=manifest_key)
        manifest = json.loads(response['Body'].read().decode('utf-8'))
        
        ensure_manifest_structures(manifest)
        
        # Update LLM status
        manifest['structures'][structure]['llm_status'] = status
        manifest['structures'][structure]['llm_processed_slices'] = sorted(processed_slices)
        manifest['structures'][structure]['llm_failed_slices'] = sorted(failed_slices)
        manifest['structures'][structure]['llm_processed_count'] = len(processed_slices)
        manifest['structures'][structure]['llm_failed_count'] = len(failed_slices)
        
        # Save updated manifest
        s3_client.put_object(
            Bucket=bucket,
            Key=manifest_key,
            Body=json.dumps(manifest, indent=2),
            ContentType='application/json'
        )
        
        logger.info(f"Updated manifest.json with LLM status for {structure}")
        
    except Exception as e:
        logger.error(f"Error updating manifest: {e}")


def process_slice_with_retry(case_id: str, structure: str, slice_index: int, 
                             max_retries: int = 3) -> Tuple[bool, str]:
    """
    Process a single slice with retry logic.
    
    Returns:
        (success: bool, error_message: str)
    """
    import base64
    from PIL import Image
    import io
    
    logger.info(f"Processing slice {slice_index}...")
    
    # Load slice from S3
    try:
        image_bytes = load_slice_from_s3(case_id, slice_index)
    except Exception as e:
        error_msg = f"Failed to load slice: {e}"
        logger.error(error_msg)
        return False, error_msg
    
    # Call LLM with retries
    result = None
    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            logger.info(f"Retry attempt {attempt}/{max_retries}")
        
        result = call_llm_for_segmentation(image_bytes, structure)
        
        if result.get('success'):
            break
        
        if attempt < max_retries:
            logger.warning(f"Attempt {attempt} failed: {result.get('error')}, retrying...")
        else:
            error_msg = f"All {max_retries} attempts failed. Last error: {result.get('error')}"
            logger.error(error_msg)
            # Save blank mask on failure
            try:
                img = Image.open(io.BytesIO(image_bytes))
                blank_mask = Image.new('L', img.size, 0)
                mask_buffer = io.BytesIO()
                blank_mask.save(mask_buffer, format='PNG')
                mask_bytes = mask_buffer.getvalue()
                save_mask_to_s3(case_id, structure, slice_index, mask_bytes)
                logger.info(f"Saved blank mask for failed slice {slice_index}")
            except Exception as e:
                logger.error(f"Failed to save blank mask: {e}")
            return False, error_msg
    
    # Decode base64 mask
    try:
        mask_base64 = result.get('mask_base64')
        mask_bytes = base64.b64decode(mask_base64)
    except Exception as e:
        error_msg = f"Failed to decode base64 mask: {e}"
        logger.error(error_msg)
        return False, error_msg
    
    # Validate mask
    if not validate_mask(mask_bytes, image_bytes):
        logger.warning(f"Mask validation failed for slice {slice_index}, but saving anyway...")
    
    # Save mask to S3
    if save_mask_to_s3(case_id, structure, slice_index, mask_bytes):
        logger.info(f"✅ Successfully processed slice {slice_index}")
        return True, ""
    else:
        error_msg = "Failed to save mask to S3"
        logger.error(error_msg)
        return False, error_msg


def process_case(case_id: str, structure: str, slice_range: Tuple[int, int] = None, 
                max_retries: int = 3) -> dict:
    """
    Process all slices for a case/structure.
    
    Args:
        case_id: Case identifier (e.g., CBCT_0001)
        structure: Structure name (airway, uvula, tongue)
        slice_range: Optional tuple (start, end) to process specific range
        max_retries: Maximum retry attempts per slice
    
    Returns:
        dict with processing results
    """
    logger.info(f"Starting pre-annotation for {case_id} - {structure}")
    
    # Get case info
    try:
        case_info = get_case_info(case_id)
        num_slices = case_info.get('num_slices', case_info.get('num_axial_slices', 0))
        
        if num_slices == 0:
            raise ValueError(f"No slices found in case {case_id}")
        
        logger.info(f"Case {case_id} has {num_slices} slices")
        
    except Exception as e:
        logger.error(f"Failed to get case info: {e}")
        return {
            'success': False,
            'error': str(e),
            'processed_slices': [],
            'failed_slices': []
        }
    
    # Determine slice range
    if slice_range:
        start_slice, end_slice = slice_range
        end_slice = min(end_slice, num_slices - 1)
    else:
        start_slice = 0
        end_slice = num_slices - 1
    
    total_slices = end_slice - start_slice + 1
    logger.info(f"Processing slices {start_slice} to {end_slice} ({total_slices} total)")
    
    # Update manifest - mark as in progress
    update_manifest_llm_status(case_id, structure, 'in_progress', [], [])
    
    # Process each slice
    processed_slices = []
    failed_slices = []
    
    for slice_index in range(start_slice, end_slice + 1):
        success, error = process_slice_with_retry(case_id, structure, slice_index, max_retries)
        
        if success:
            processed_slices.append(slice_index)
        else:
            failed_slices.append(slice_index)
        
        # Progress update every 10 slices
        if (slice_index - start_slice + 1) % 10 == 0:
            progress = len(processed_slices) + len(failed_slices)
            logger.info(f"Progress: {progress}/{total_slices} slices processed "
                       f"({len(processed_slices)} successful, {len(failed_slices)} failed)")
    
    # Update manifest - mark as completed
    status = 'completed' if len(failed_slices) == 0 else 'completed_with_errors'
    update_manifest_llm_status(case_id, structure, status, processed_slices, failed_slices)
    
    # Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"Pre-annotation complete for {case_id} - {structure}")
    logger.info(f"Total slices: {total_slices}")
    logger.info(f"✅ Successful: {len(processed_slices)}")
    logger.info(f"❌ Failed: {len(failed_slices)}")
    if failed_slices:
        logger.info(f"Failed slices: {failed_slices[:10]}{'...' if len(failed_slices) > 10 else ''}")
    logger.info(f"{'='*60}\n")
    
    return {
        'success': True,
        'case_id': case_id,
        'structure': structure,
        'total_slices': total_slices,
        'processed_slices': processed_slices,
        'failed_slices': failed_slices,
        'success_count': len(processed_slices),
        'failed_count': len(failed_slices)
    }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Pre-annotate CBCT case using LLM segmentation',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument('case_id', help='Case identifier (e.g., CBCT_0001)')
    parser.add_argument('structure', choices=SUPPORTED_STRUCTURES,
                       help='Structure to segment')
    parser.add_argument('--slice-range', nargs=2, type=int, metavar=('START', 'END'),
                       help='Process specific slice range (0-indexed, inclusive)')
    parser.add_argument('--max-retries', type=int, default=3,
                       help='Maximum retry attempts per slice (default: 3)')
    parser.add_argument('--skip-volume', action='store_true',
                       help='Skip assembling 3D mask volume + QA summary')
    parser.add_argument('--volume-source', choices=list(MASK_SOURCE_DIRS.keys()), default='pred',
                       help='Mask source ("pred" or "corrected") used when stacking volume (default: pred)')
    
    args = parser.parse_args()
    
    # Validate case_id format
    if not args.case_id.startswith('CBCT_'):
        logger.error(f"Invalid case_id format: {args.case_id}. Expected CBCT_XXXX")
        sys.exit(1)
    
    # Process case
    slice_range = None
    if args.slice_range:
        slice_range = tuple(args.slice_range)
    
    result = process_case(
        args.case_id,
        args.structure,
        slice_range=slice_range,
        max_retries=args.max_retries
    )

    volume_result = None
    if result.get('success') and not args.skip_volume:
        try:
            volume_result = build_mask_volume(
                args.case_id,
                args.structure,
                source=args.volume_source
            )
            logger.info("Mask volume stored at s3://%s/%s", get_annotation_bucket(), volume_result['volume_key'])
            logger.info("QA summary stored at s3://%s/%s", get_annotation_bucket(), volume_result['qa_key'])
        except Exception as exc:
            logger.error("Failed to assemble mask volume: %s", exc)
    
    if result.get('success'):
        print(f"\n✅ Pre-annotation complete!")
        print(f"   Case: {result['case_id']}")
        print(f"   Structure: {result['structure']}")
        print(f"   Successful: {result['success_count']}/{result['total_slices']}")
        print(f"   Failed: {result['failed_count']}/{result['total_slices']}")
        print(f"\n   Masks saved to: annotation_dataset/{result['case_id']}/masks_pred/{result['structure']}/")
        print(f"   Ready for human annotation at: /annotator/cbct/{result['case_id']}")
        if volume_result:
            bucket = get_annotation_bucket()
            print(f"   Volume S3 key: s3://{bucket}/{volume_result['volume_key']}")
            print(f"   QA JSON S3 key: s3://{bucket}/{volume_result['qa_key']}")
        sys.exit(0)
    else:
        print(f"\n❌ Pre-annotation failed: {result.get('error')}")
        sys.exit(1)


if __name__ == '__main__':
    main()

