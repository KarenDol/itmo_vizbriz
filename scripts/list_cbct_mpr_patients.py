#!/usr/bin/env python3
"""
List all patients who have CBCT_MPR folders in S3.

This script scans S3 for patients with CBCT MPR data that can be used
to create training cases for the CBCT OSA Annotator.

Usage:
    python scripts/list_cbct_mpr_patients.py
    python scripts/list_cbct_mpr_patients.py --output csv
    python scripts/list_cbct_mpr_patients.py --bucket my-bucket
"""

import os
import sys
import json
import boto3
import argparse
from collections import defaultdict
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask_app import create_app
from flask_app.models import Patient


def list_cbct_mpr_patients(bucket_name=None, output_format='table'):
    """
    List all patients with CBCT MPR folders in S3.
    
    Args:
        bucket_name: S3 bucket name (defaults to config)
        output_format: 'table', 'json', or 'csv'
    
    Returns:
        List of dicts with patient_id, folder_name, and manifest info
    """
    app = create_app()
    
    with app.app_context():
        # Get bucket from config if not provided
        if not bucket_name:
            bucket_name = app.config.get('S3_BUCKET') or app.config.get('S3_BUCKET_NAME')
            if not bucket_name:
                print("Error: S3 bucket not configured. Please specify --bucket")
                return []
        
        region = (app.config.get('AWS_REGION')
                  or os.getenv('AWS_REGION')
                  or 'us-east-1')
        
        print(f"Scanning S3 bucket: {bucket_name}")
        print(f"Region: {region}")
        print(f"Looking for CBCT MPR folders at: patients/*/imaging/cbct_mpr/*/")
        print()
        
        s3_client = boto3.client('s3', region_name=region)
        
        # List all objects with prefix "patients/"
        prefix = "patients/"
        results = []
        patient_folders = defaultdict(list)
        
        print("Scanning S3...")
        paginator = s3_client.get_paginator('list_objects_v2')
        
        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix, Delimiter='/'):
            # Get patient IDs (first level)
            if 'CommonPrefixes' in page:
                for cp in page['CommonPrefixes']:
                    patient_prefix = cp['Prefix']  # e.g., "patients/12345/"
                    
                    # Check for imaging/cbct_mpr/ subfolder
                    cbct_mpr_prefix = f"{patient_prefix}imaging/cbct_mpr/"
                    
                    try:
                        # List folders under cbct_mpr/
                        cbct_response = s3_client.list_objects_v2(
                            Bucket=bucket_name,
                            Prefix=cbct_mpr_prefix,
                            Delimiter='/'
                        )
                        
                        if 'CommonPrefixes' in cbct_response:
                            for folder_cp in cbct_response['CommonPrefixes']:
                                folder_path = folder_cp['Prefix']
                                # Extract folder name
                                # e.g., "patients/12345/imaging/cbct_mpr/folder_name/"
                                parts = folder_path.rstrip('/').split('/')
                                if len(parts) >= 5:
                                    patient_id = parts[1]  # Extract patient ID
                                    folder_name = parts[4]  # Extract folder name
                                    
                                    # Check if manifest.json exists
                                    manifest_key = f"{folder_path}manifest.json"
                                    has_manifest = False
                                    manifest_info = None
                                    
                                    try:
                                        manifest_obj = s3_client.head_object(
                                            Bucket=bucket_name,
                                            Key=manifest_key
                                        )
                                        has_manifest = True
                                        
                                        # Try to get manifest to extract info
                                        try:
                                            manifest_response = s3_client.get_object(
                                                Bucket=bucket_name,
                                                Key=manifest_key
                                            )
                                            manifest_data = json.loads(
                                                manifest_response['Body'].read().decode('utf-8')
                                            )
                                            manifest_info = {
                                                'counts': manifest_data.get('counts', {}),
                                                'version': manifest_data.get('version', 'unknown')
                                            }
                                        except:
                                            pass
                                    except:
                                        pass
                                    
                                    if has_manifest:
                                        patient_folders[patient_id].append({
                                            'patient_id': int(patient_id),
                                            'folder_name': folder_name,
                                            'manifest_key': manifest_key,
                                            'manifest_info': manifest_info
                                        })
                    except Exception as e:
                        # Skip if error listing this patient's folders
                        continue
        
        # Convert to flat list
        for patient_id, folders in patient_folders.items():
            for folder_info in folders:
                results.append(folder_info)
        
        # Sort by patient_id, then folder_name
        results.sort(key=lambda x: (x['patient_id'], x['folder_name']))
        
        # Output results
        if output_format == 'json':
            print(json.dumps(results, indent=2))
        elif output_format == 'csv':
            print("patient_id,folder_name,has_manifest,axial_slices,coronal_slices,sagittal_slices")
            for r in results:
                manifest = r.get('manifest_info', {})
                counts = manifest.get('counts', {}) if manifest else {}
                print(f"{r['patient_id']},{r['folder_name']},True,"
                      f"{counts.get('axial', 0)},{counts.get('coronal', 0)},{counts.get('sagittal', 0)}")
        else:  # table format
            print(f"\nFound {len(results)} CBCT MPR folders across {len(patient_folders)} patients\n")
            print(f"{'Patient ID':<12} {'Folder Name':<40} {'Axial':<8} {'Coronal':<8} {'Sagittal':<8}")
            print("-" * 90)
            
            for r in results:
                manifest = r.get('manifest_info', {})
                counts = manifest.get('counts', {}) if manifest else {}
                axial = counts.get('axial', 0)
                coronal = counts.get('coronal', 0)
                sagittal = counts.get('sagittal', 0)
                
                print(f"{r['patient_id']:<12} {r['folder_name']:<40} {axial:<8} {coronal:<8} {sagittal:<8}")
            
            print(f"\nTotal: {len(results)} MPR folders")
        
        return results


def main():
    parser = argparse.ArgumentParser(
        description='List all patients with CBCT MPR folders in S3'
    )
    parser.add_argument(
        '--bucket',
        type=str,
        help='S3 bucket name (defaults to config)'
    )
    parser.add_argument(
        '--output',
        choices=['table', 'json', 'csv'],
        default='table',
        help='Output format (default: table)'
    )
    
    args = parser.parse_args()
    
    results = list_cbct_mpr_patients(
        bucket_name=args.bucket,
        output_format=args.output
    )
    
    if not results:
        print("\nNo CBCT MPR folders found in S3.")
        sys.exit(1)


if __name__ == '__main__':
    main()

