#!/usr/bin/env python3
"""
Multi-frame DICOM Detection Guide and Utility

This script shows how to identify multi-frame DICOM files (like from iCordicon)
and provides utilities to analyze DICOM files.
"""

import pydicom
import os
import sys

def is_multiframe_dicom(dicom_path):
    """
    Check if a DICOM file is multi-frame
    
    Args:
        dicom_path (str): Path to the DICOM file
        
    Returns:
        dict: Analysis results with details about the DICOM file
    """
    try:
        # Load the DICOM file
        ds = pydicom.dcmread(dicom_path)
        
        analysis = {
            'is_multiframe': False,
            'number_of_frames': None,
            'modality': getattr(ds, 'Modality', 'Unknown'),
            'manufacturer': getattr(ds, 'Manufacturer', 'Unknown'),
            'model': getattr(ds, 'ManufacturerModelName', 'Unknown'),
            'pixel_array_shape': None,
            'file_size_mb': round(os.path.getsize(dicom_path) / (1024 * 1024), 2),
            'has_pixel_data': hasattr(ds, 'PixelData'),
            'details': []
        }
        
        # Check 1: NumberOfFrames attribute
        if hasattr(ds, 'NumberOfFrames'):
            analysis['number_of_frames'] = ds.NumberOfFrames
            analysis['details'].append(f"NumberOfFrames: {ds.NumberOfFrames}")
            
            if ds.NumberOfFrames > 1:
                analysis['is_multiframe'] = True
                analysis['details'].append("✅ This is a multi-frame DICOM file")
            else:
                analysis['details'].append("❌ Single frame DICOM (NumberOfFrames = 1)")
        else:
            analysis['details'].append("❌ No NumberOfFrames attribute found")
        
        # Check 2: Pixel Array Shape (if we can load it)
        try:
            pixel_array = ds.pixel_array
            analysis['pixel_array_shape'] = pixel_array.shape
            analysis['details'].append(f"Pixel Array Shape: {pixel_array.shape}")
            
            # If the first dimension > 1, it's likely multi-frame
            if len(pixel_array.shape) > 2 and pixel_array.shape[0] > 1:
                analysis['details'].append(f"✅ Pixel array has {pixel_array.shape[0]} frames")
                if not analysis['is_multiframe']:
                    analysis['details'].append("⚠️  Pixel array suggests multi-frame but NumberOfFrames not set")
            else:
                analysis['details'].append("❌ Pixel array is single frame")
                
        except Exception as e:
            analysis['details'].append(f"⚠️  Could not load pixel array: {str(e)}")
        
        # Check 3: File size (multi-frame files are typically larger)
        if analysis['file_size_mb'] > 10:  # Arbitrary threshold
            analysis['details'].append(f"📁 Large file size ({analysis['file_size_mb']} MB) - typical for multi-frame")
        else:
            analysis['details'].append(f"📁 File size: {analysis['file_size_mb']} MB")
        
        # Check 4: Manufacturer/Model hints
        manufacturer = analysis['manufacturer'].lower()
        if 'icordicon' in manufacturer or 'cordicon' in manufacturer:
            analysis['details'].append("🔍 iCordicon device detected - likely multi-frame")
        elif 'carestream' in manufacturer:
            analysis['details'].append("🔍 Carestream device detected - may be multi-frame")
        elif 'sirona' in manufacturer:
            analysis['details'].append("🔍 Sirona device detected - may be multi-frame")
        
        # Check 5: Modality hints
        modality = analysis['modality']
        if modality in ['CT', 'CBCT', 'XA']:
            analysis['details'].append(f"🔍 {modality} modality - commonly multi-frame")
        elif modality == 'CR':
            analysis['details'].append(f"🔍 {modality} modality - usually single frame")
        
        return analysis
        
    except Exception as e:
        return {
            'is_multiframe': False,
            'error': str(e),
            'details': [f"❌ Error reading DICOM file: {str(e)}"]
        }

def analyze_dicom_file(dicom_path):
    """
    Comprehensive DICOM file analysis
    
    Args:
        dicom_path (str): Path to the DICOM file
    """
    print(f"🔍 Analyzing DICOM file: {dicom_path}")
    print("=" * 60)
    
    if not os.path.exists(dicom_path):
        print(f"❌ File not found: {dicom_path}")
        return
    
    analysis = is_multiframe_dicom(dicom_path)
    
    if 'error' in analysis:
        print(f"❌ Error: {analysis['error']}")
        return
    
    # Print results
    print(f"📊 Analysis Results:")
    print(f"   Multi-frame: {'✅ YES' if analysis['is_multiframe'] else '❌ NO'}")
    print(f"   Number of Frames: {analysis['number_of_frames'] or 'Unknown'}")
    print(f"   Modality: {analysis['modality']}")
    print(f"   Manufacturer: {analysis['manufacturer']}")
    print(f"   Model: {analysis['model']}")
    print(f"   File Size: {analysis['file_size_mb']} MB")
    print(f"   Pixel Array Shape: {analysis['pixel_array_shape']}")
    
    print(f"\n📋 Details:")
    for detail in analysis['details']:
        print(f"   {detail}")
    
    # Final recommendation
    print(f"\n🎯 Recommendation:")
    if analysis['is_multiframe']:
        print("   ✅ This file can be split into individual DCM slices")
        print("   💡 Use the multi-frame DICOM splitter tool")
    else:
        print("   ❌ This file is not multi-frame - no splitting needed")
        print("   💡 Use the regular CBCT upload tool")

def detect_multiframe_indicators(dicom_path):
    """
    Quick detection of multi-frame indicators without loading pixel data
    
    Args:
        dicom_path (str): Path to the DICOM file
        
    Returns:
        dict: Quick analysis results
    """
    try:
        ds = pydicom.dcmread(dicom_path, stop_before_pixels=True)
        
        indicators = {
            'has_number_of_frames': hasattr(ds, 'NumberOfFrames'),
            'number_of_frames': getattr(ds, 'NumberOfFrames', None),
            'modality': getattr(ds, 'Modality', 'Unknown'),
            'manufacturer': getattr(ds, 'Manufacturer', 'Unknown'),
            'file_size_mb': round(os.path.getsize(dicom_path) / (1024 * 1024), 2),
            'is_likely_multiframe': False,
            'confidence': 'low'
        }
        
        # Quick heuristics
        confidence_score = 0
        
        # NumberOfFrames > 1 is the most reliable indicator
        if indicators['has_number_of_frames'] and indicators['number_of_frames'] > 1:
            indicators['is_likely_multiframe'] = True
            confidence_score += 3
            indicators['confidence'] = 'high'
        
        # Large file size (>10MB) suggests multi-frame
        if indicators['file_size_mb'] > 10:
            confidence_score += 1
        
        # CT/CBCT modality often multi-frame
        if indicators['modality'] in ['CT', 'CBCT']:
            confidence_score += 1
        
        # iCordicon manufacturer
        if 'icordicon' in indicators['manufacturer'].lower():
            confidence_score += 2
            indicators['confidence'] = 'high'
        
        # Set confidence based on score
        if confidence_score >= 3:
            indicators['confidence'] = 'high'
        elif confidence_score >= 2:
            indicators['confidence'] = 'medium'
        
        return indicators
        
    except Exception as e:
        return {'error': str(e)}

def main():
    """Main function for command line usage"""
    if len(sys.argv) < 2:
        print("Usage: python multiframe_dicom_detection.py <dicom_file_path>")
        print("\nExample:")
        print("  python multiframe_dicom_detection.py /path/to/icordicon_file.dcm")
        return
    
    dicom_path = sys.argv[1]
    analyze_dicom_file(dicom_path)

if __name__ == "__main__":
    main() 