import pydicom
import os
import subprocess
import tempfile
from flask_app.logging_config import logger

def validate_mpr_requirements(ds):
    """
    Validate if a DICOM file meets MPR requirements.
    Returns (is_valid, error_message)
    """
    try:
        # Check if it's a CT image
        if ds.Modality != 'CT':
            return False, f"Not a CT image (Modality: {ds.Modality})"

        # Check if it has required tags for MPR
        required_tags = [
            'ImagePositionPatient',
            'ImageOrientationPatient',
            'PixelSpacing',
            'SliceThickness'
        ]

        missing_tags = []
        for tag in required_tags:
            if not hasattr(ds, tag):
                missing_tags.append(tag)

        if missing_tags:
            return False, f"Missing required tags: {', '.join(missing_tags)}"

        return True, ""

    except Exception as e:
        return False, f"Error validating MPR requirements: {str(e)}"

def convert_dicom_to_uncompressed(input_path, output_path):
    """
    Convert a compressed DICOM file to uncompressed format using gdcmconv.
    Returns True if successful, False otherwise.
    """
    try:
        # Check if gdcmconv is available
        try:
            subprocess.run(['gdcmconv', '--version'], capture_output=True, check=True)
        except (subprocess.SubprocessError, FileNotFoundError):
            logger.error("gdcmconv not found. Please install GDCM tools.")
            return False

        # Convert the file
        result = subprocess.run([
            'gdcmconv',
            '--raw',  # Use raw (uncompressed) transfer syntax
            input_path,
            output_path
        ], capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"Conversion failed: {result.stderr}")
            return False

        # Verify the output file exists and is readable
        if not os.path.exists(output_path):
            logger.error("Output file was not created")
            return False

        try:
            ds = pydicom.dcmread(output_path, stop_before_pixels=True)
            if str(ds.file_meta.TransferSyntaxUID) != '1.2.840.10008.1.2.1':  # Uncompressed
                logger.error("Output file is not uncompressed")
                return False
        except Exception as e:
            logger.error(f"Error verifying output file: {str(e)}")
            return False

        return True

    except Exception as e:
        logger.error(f"Error in convert_dicom_to_uncompressed: {str(e)}")
        return False

def analyze_dicom_for_multiframe(ds, file_path):
    """
    Analyze a DICOM dataset to determine if it's multi-frame
    Args:
        ds: pydicom.Dataset object
        file_path: Path to the DICOM file
    Returns:
        dict: Analysis results
    """
    import os
    analysis = {
        'is_multiframe': False,
        'reason': '',
        'number_of_frames': None,
        'modality': getattr(ds, 'Modality', 'Unknown'),
        'manufacturer': getattr(ds, 'Manufacturer', 'Unknown'),
        'model': getattr(ds, 'ManufacturerModelName', 'Unknown'),
        'file_size_mb': round(os.path.getsize(file_path) / (1024 * 1024), 2),
        'indicators': []
    }
    # Check 1: NumberOfFrames attribute (most reliable)
    if hasattr(ds, 'NumberOfFrames'):
        analysis['number_of_frames'] = ds.NumberOfFrames
        analysis['indicators'].append(f"NumberOfFrames: {ds.NumberOfFrames}")
        if ds.NumberOfFrames > 1:
            analysis['is_multiframe'] = True
            analysis['reason'] = f"Confirmed multi-frame with {ds.NumberOfFrames} frames"
            return analysis
        elif ds.NumberOfFrames == 1:
            analysis['reason'] = "Single frame DICOM (NumberOfFrames = 1)"
            return analysis
    else:
        analysis['indicators'].append("No NumberOfFrames attribute")
    # Check 2: Pixel Array Shape (if we can load it)
    try:
        pixel_array = ds.pixel_array
        analysis['indicators'].append(f"Pixel Array Shape: {pixel_array.shape}")
        if len(pixel_array.shape) > 2 and pixel_array.shape[0] > 1:
            analysis['indicators'].append(f"Pixel array has {pixel_array.shape[0]} frames")
            analysis['is_multiframe'] = True
            analysis['reason'] = f"Multi-frame detected from pixel array shape: {pixel_array.shape}"
            return analysis
        else:
            analysis['indicators'].append("Pixel array is single frame")
    except Exception as e:
        analysis['indicators'].append(f"Could not load pixel array: {str(e)}")
    # Check 3: File size (heuristic)
    if analysis['file_size_mb'] > 10:
        analysis['indicators'].append(f"Large file size ({analysis['file_size_mb']} MB) - typical for multi-frame")
    else:
        analysis['indicators'].append(f"File size: {analysis['file_size_mb']} MB")
    # Check 4: Manufacturer hints
    manufacturer = analysis['manufacturer'].lower()
    if 'icordicon' in manufacturer or 'cordicon' in manufacturer:
        analysis['indicators'].append("iCordicon device detected - likely multi-frame")
        analysis['is_multiframe'] = True
        analysis['reason'] = "iCordicon device detected - likely multi-frame"
        return analysis
    elif 'carestream' in manufacturer:
        analysis['indicators'].append("Carestream device detected - may be multi-frame")
    elif 'sirona' in manufacturer:
        analysis['indicators'].append("Sirona device detected - may be multi-frame")
    # Check 5: Modality hints
    modality = analysis['modality']
    if modality in ['CT', 'CBCT', 'XA']:
        analysis['indicators'].append(f"{modality} modality - commonly multi-frame")
    elif modality == 'CR':
        analysis['indicators'].append(f"{modality} modality - usually single frame")
    # If we get here, it's not clearly multi-frame
    if not analysis['is_multiframe']:
        analysis['reason'] = "No clear indicators of multi-frame DICOM found"
    return analysis 