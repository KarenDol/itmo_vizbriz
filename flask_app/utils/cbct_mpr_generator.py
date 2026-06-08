import os
import json
import tempfile
import time
import shutil
from collections import Counter
from typing import List, Tuple

import boto3
import numpy as np
import pydicom
from flask import current_app
from PIL import Image

try:
    import SimpleITK as sitk
    _HAS_SIMPLEITK = True
except ImportError:  # pragma: no cover
    sitk = None
    _HAS_SIMPLEITK = False

from flask_app.logging_config import logger


def _cleanup_stale_mpr_temp_dirs(base_dir: str, max_age_seconds: int) -> None:
    """
    Best-effort cleanup of stale temp dirs under the MPR temp base.

    Why: if a worker is killed mid-run, the TemporaryDirectory cleanup does not run,
    leaving large DICOM + memmap + PNG artifacts behind. We sweep on the next run
    to avoid filling the server disk.
    """
    if not base_dir or max_age_seconds <= 0:
        return
    if not os.path.isdir(base_dir):
        return

    now = time.time()
    try:
        entries = os.listdir(base_dir)
    except OSError as exc:
        logger.warning("MPR temp cleanup: failed to list %s: %s", base_dir, exc)
        return

    removed = 0
    for name in entries:
        path = os.path.join(base_dir, name)
        if not os.path.isdir(path):
            continue
        # TemporaryDirectory commonly prefixes with "tmp"; keep this conservative.
        if not name.startswith("tmp"):
            continue
        try:
            age_seconds = now - os.path.getmtime(path)
        except OSError:
            continue
        if age_seconds < max_age_seconds:
            continue
        try:
            shutil.rmtree(path, ignore_errors=False)
            removed += 1
        except Exception as exc:
            logger.warning("MPR temp cleanup: failed to remove %s: %s", path, exc)

    if removed:
        logger.info("MPR temp cleanup: removed %d stale temp dirs under %s", removed, base_dir)


def _list_s3_objects(s3_client, bucket: str, prefix: str) -> List[dict]:
    """List all objects under a prefix."""
    objects = []
    continuation_token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        resp = s3_client.list_objects_v2(**kwargs)
        objects.extend(resp.get("Contents", []))
        if not resp.get("IsTruncated"):
            break
        continuation_token = resp.get("NextContinuationToken")
    return objects


def _download_dicom_series(s3_client, bucket: str, objects: List[dict], tmp_dir: str) -> List[Tuple[pydicom.dataset.Dataset, str]]:
    datasets = []
    for obj in objects:
        key = obj["Key"]
        if not key.lower().endswith(('.dcm', '.dicom', '.dcom')):
            continue
        relative = key.split('/')[-1]
        local_path = os.path.join(tmp_dir, relative)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        s3_client.download_file(bucket, key, local_path)
        try:
            ds = pydicom.dcmread(local_path, stop_before_pixels=True)
            datasets.append((ds, local_path))
        except Exception as exc:
            logger.error("Failed to read DICOM %s: %s", key, exc)
    return datasets


def _validate_geometry(datasets: List[Tuple[pydicom.dataset.Dataset, str]]) -> dict:
    """
    Validate DICOM geometry tags and detect broken series.
    
    Returns:
        dict with geometry_status ('OK' or 'NEEDS_NORMALIZATION'), diagnostics, and metadata
    """
    diagnostics = {
        "geometry_status": "OK",
        "issues": [],
        "missing_tags": [],
        "slice_count": len(datasets),
        "has_ipp": True,
        "has_iop": True,
        "normal_drift_deg": 0.0,
        "spacing_variance_pct": 0.0,
        "sort_method": "ipp_projection",
    }
    
    if not datasets:
        diagnostics["geometry_status"] = "NEEDS_NORMALIZATION"
        diagnostics["issues"].append("No datasets")
        return diagnostics
    
    # Collect IPP and IOP from all slices
    ipp_list = []
    iop_list = []
    instance_numbers = []
    
    for ds, path in datasets:
        # Check IPP
        if hasattr(ds, "ImagePositionPatient") and len(ds.ImagePositionPatient) >= 3:
            ipp = [float(ds.ImagePositionPatient[i]) for i in range(3)]
            ipp_list.append(ipp)
        else:
            diagnostics["has_ipp"] = False
            diagnostics["missing_tags"].append("ImagePositionPatient")
        
        # Check IOP
        if hasattr(ds, "ImageOrientationPatient") and len(ds.ImageOrientationPatient) >= 6:
            iop = [float(ds.ImageOrientationPatient[i]) for i in range(6)]
            iop_list.append(iop)
        else:
            diagnostics["has_iop"] = False
            diagnostics["missing_tags"].append("ImageOrientationPatient")
        
        # Instance number for fallback
        if hasattr(ds, "InstanceNumber"):
            instance_numbers.append(int(ds.InstanceNumber))
        else:
            instance_numbers.append(None)
    
    # Validate IOP consistency and compute slice normal
    slice_normal = None
    if diagnostics["has_iop"] and iop_list:
        first_iop = iop_list[0]
        row_cosines = np.array(first_iop[0:3])
        col_cosines = np.array(first_iop[3:6])
        
        # Validate unit vectors
        row_mag = np.linalg.norm(row_cosines)
        col_mag = np.linalg.norm(col_cosines)
        if row_mag < 0.9 or row_mag > 1.1 or col_mag < 0.9 or col_mag > 1.1:
            diagnostics["issues"].append(f"IOP not unit vectors: row_mag={row_mag:.3f}, col_mag={col_mag:.3f}")
            diagnostics["geometry_status"] = "NEEDS_NORMALIZATION"
        
        # Compute slice normal
        slice_normal = np.cross(row_cosines, col_cosines)
        slice_normal = slice_normal / np.linalg.norm(slice_normal)
        
        # Check IOP consistency across slices (normal drift)
        if len(iop_list) > 1:
            max_drift = 0.0
            for iop in iop_list[1:]:
                rc = np.array(iop[0:3])
                cc = np.array(iop[3:6])
                sn = np.cross(rc, cc)
                sn = sn / (np.linalg.norm(sn) + 1e-10)
                dot = np.clip(np.dot(slice_normal, sn), -1.0, 1.0)
                angle_deg = np.degrees(np.arccos(abs(dot)))
                max_drift = max(max_drift, angle_deg)
            
            diagnostics["normal_drift_deg"] = float(max_drift)
            if max_drift > 2.0:  # Threshold: 2 degrees
                diagnostics["issues"].append(f"Slice normal drift: {max_drift:.2f}° (threshold: 2°)")
                diagnostics["geometry_status"] = "NEEDS_NORMALIZATION"
    else:
        diagnostics["issues"].append("Missing/invalid IOP - cannot compute slice normal")
        diagnostics["geometry_status"] = "NEEDS_NORMALIZATION"
        diagnostics["sort_method"] = "fallback_pca" if diagnostics["has_ipp"] else "instance_number"
    
    # Validate IPP and compute spacing variance
    if diagnostics["has_ipp"] and len(ipp_list) >= 2 and slice_normal is not None:
        # Compute projection of each IPP onto slice normal
        projections = [np.dot(np.array(ipp), slice_normal) for ipp in ipp_list]
        sorted_proj = sorted(projections)
        
        # Compute inter-slice spacings
        spacings = [sorted_proj[i+1] - sorted_proj[i] for i in range(len(sorted_proj)-1)]
        if spacings:
            mean_spacing = np.mean(spacings)
            std_spacing = np.std(spacings)
            variance_pct = (std_spacing / (mean_spacing + 1e-10)) * 100
            
            diagnostics["spacing_variance_pct"] = float(variance_pct)
            diagnostics["mean_slice_spacing_mm"] = float(mean_spacing)
            
            if variance_pct > 5.0:  # Threshold: 5% variance
                diagnostics["issues"].append(f"Non-uniform spacing: {variance_pct:.1f}% variance (threshold: 5%)")
                diagnostics["geometry_status"] = "NEEDS_NORMALIZATION"
        
        # Check if IPP step is colinear with slice normal
        if len(ipp_list) >= 2:
            ipp_diff = np.array(ipp_list[1]) - np.array(ipp_list[0])
            ipp_diff_norm = ipp_diff / (np.linalg.norm(ipp_diff) + 1e-10)
            colinearity = abs(np.dot(ipp_diff_norm, slice_normal))
            if colinearity < 0.95:  # Should be ~1.0 for proper alignment
                diagnostics["issues"].append(f"IPP step not colinear with normal: dot={colinearity:.3f}")
                diagnostics["geometry_status"] = "NEEDS_NORMALIZATION"
    
    # Check for conflicting sort orders (IPP vs InstanceNumber)
    if diagnostics["has_ipp"] and slice_normal is not None and any(x is not None for x in instance_numbers):
        projections = [np.dot(np.array(ipp), slice_normal) for ipp in ipp_list]
        ipp_order = np.argsort(projections)
        
        valid_instance = [(i, num) for i, num in enumerate(instance_numbers) if num is not None]
        if len(valid_instance) == len(instance_numbers):
            inst_order = np.argsort(instance_numbers)
            if not np.array_equal(ipp_order, inst_order) and not np.array_equal(ipp_order, inst_order[::-1]):
                diagnostics["issues"].append("IPP sort order conflicts with InstanceNumber order")
                # Don't mark as NEEDS_NORMALIZATION - just use IPP
    
    return diagnostics


def _sort_datasets_by_ipp(datasets: List[Tuple[pydicom.dataset.Dataset, str]], diagnostics: dict) -> List[Tuple[pydicom.dataset.Dataset, str]]:
    """
    Sort datasets by ImagePositionPatient projection onto slice normal.
    Falls back to InstanceNumber or PCA if IPP/IOP unavailable.
    """
    if not datasets:
        return datasets
    
    # Try IPP projection sorting (preferred method)
    if diagnostics.get("has_iop") and diagnostics.get("has_ipp"):
        try:
            first_ds = datasets[0][0]
            iop = first_ds.ImageOrientationPatient
            row_cosines = np.array([float(iop[i]) for i in range(3)])
            col_cosines = np.array([float(iop[i]) for i in range(3, 6)])
            slice_normal = np.cross(row_cosines, col_cosines)
            slice_normal = slice_normal / np.linalg.norm(slice_normal)
            
            def ipp_projection(item):
                ds = item[0]
                ipp = np.array([float(ds.ImagePositionPatient[i]) for i in range(3)])
                return np.dot(ipp, slice_normal)
            
            datasets_sorted = sorted(datasets, key=ipp_projection)
            logger.info("Sorted %d slices by IPP projection onto slice normal", len(datasets_sorted))
            return datasets_sorted
        except Exception as e:
            logger.warning("IPP projection sort failed: %s, falling back", e)
    
    # Fallback: PCA on IPP points if IOP missing but IPP available
    if diagnostics.get("has_ipp"):
        try:
            ipp_points = []
            for ds, path in datasets:
                ipp = np.array([float(ds.ImagePositionPatient[i]) for i in range(3)])
                ipp_points.append(ipp)
            ipp_points = np.array(ipp_points)
            
            # PCA to find dominant axis
            centered = ipp_points - np.mean(ipp_points, axis=0)
            _, _, vh = np.linalg.svd(centered)
            dominant_axis = vh[0]  # First principal component
            
            def pca_projection(item):
                ds = item[0]
                ipp = np.array([float(ds.ImagePositionPatient[i]) for i in range(3)])
                return np.dot(ipp, dominant_axis)
            
            datasets_sorted = sorted(datasets, key=pca_projection)
            logger.info("Sorted %d slices by PCA projection (IOP missing)", len(datasets_sorted))
            return datasets_sorted
        except Exception as e:
            logger.warning("PCA sort failed: %s, falling back to InstanceNumber", e)
    
    # Final fallback: InstanceNumber
    def instance_key(item):
        ds = item[0]
        if hasattr(ds, "InstanceNumber"):
            return int(ds.InstanceNumber)
        return 0

    datasets_sorted = sorted(datasets, key=instance_key)
    logger.info("Sorted %d slices by InstanceNumber (fallback)", len(datasets_sorted))
    return datasets_sorted


def _sort_datasets(datasets: List[Tuple[pydicom.dataset.Dataset, str]]) -> List[Tuple[pydicom.dataset.Dataset, str]]:
    """Legacy wrapper - validates geometry and sorts by IPP projection."""
    diagnostics = _validate_geometry(datasets)
    return _sort_datasets_by_ipp(datasets, diagnostics)


def _compute_spacing(datasets: List[pydicom.dataset.Dataset]) -> Tuple[float, float, float]:
    first = datasets[0]
    pixel_spacing = first.PixelSpacing if hasattr(first, "PixelSpacing") else [1.0, 1.0]
    row_spacing = float(pixel_spacing[0])
    col_spacing = float(pixel_spacing[1])

    if hasattr(first, "SliceThickness"):
        slice_spacing = float(first.SliceThickness)
    else:
        slice_spacing = row_spacing
        if len(datasets) > 1 and hasattr(first, "ImagePositionPatient"):
            z_positions = []
            for ds in datasets:
                if hasattr(ds, "ImagePositionPatient"):
                    z_positions.append(ds.ImagePositionPatient[2])
            if len(z_positions) >= 2:
                z_positions = sorted(z_positions)
                slice_spacing = abs(z_positions[1] - z_positions[0])

    return row_spacing, col_spacing, slice_spacing


def normalize_cbct_intensity(raw: np.ndarray) -> np.ndarray:
    """
    Normalize CBCT grayscale values to pseudo-HU for proper window/level.
    Works across Vatech, Carestream, Planmeca, Morita, NewTom.
    
    Args:
        raw: Raw pixel array (float32, after RescaleSlope/Intercept)
    
    Returns:
        Normalized array in pseudo-HU range (0-3000)
    """
    min_v = np.percentile(raw, 0.1)
    max_v = np.percentile(raw, 99.9)
    
    # Avoid division by zero
    if max_v <= min_v:
        max_v = min_v + 1.0
    
    # Normalize to 0-3000 range
    scaled = (raw - min_v) / (max_v - min_v)
    scaled *= 3000.0
    
    return scaled.astype(np.float32)


def apply_window_level(img: np.ndarray, window: float, level: float) -> np.ndarray:
    """
    Apply window/level transformation to convert 16-bit DICOM data to 8-bit PNG.
    
    Args:
        img: Input image array (float32, in pseudo-HU after normalize_cbct_intensity)
        window: Window width (W)
        level: Window level/center (L)
    
    Returns:
        8-bit uint8 array ready for PNG export
    """
    if window <= 0:
        window = 2000.0
    low = level - window / 2.0
    high = level + window / 2.0
    img_clipped = np.clip(img, low, high)
    img_norm = (img_clipped - low) / (high - low)  # 0-1 range
    img_8bit = (img_norm * 255.0).astype(np.uint8)
    return img_8bit


def _window_image(data: np.ndarray, center: float, width: float) -> np.ndarray:
    """Legacy wrapper - use apply_window_level instead."""
    return apply_window_level(data, width, center)


# Window/Level presets for CBCT airway imaging
# Matches Vatech, Planmeca, Morita, Carestream, NewTom
WINDOW_PRESETS = {
    "soft_tissue": {"window": 2000.0, "level": 1200.0, "label": "Soft Tissue (Airway)"},
    "bone": {"window": 2000.0, "level": 500.0, "label": "Bone"},
    "airway": {"window": 1600.0, "level": -400.0, "label": "Airway (QA)"},
}


def _determine_window(ds: pydicom.dataset.Dataset) -> Tuple[float, float]:
    """
    Determine window/level from DICOM metadata.
    Returns (level, window) for soft-tissue airway preset by default.
    """
    def _as_float(value, default):
        if value is None:
            return default
        if isinstance(value, (list, tuple)):
            value = value[0]
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    # Use soft-tissue airway preset as default (W: 400, L: 40)
    default_level = WINDOW_PRESETS["soft_tissue"]["level"]
    default_window = WINDOW_PRESETS["soft_tissue"]["window"]
    
    # Try to get from DICOM metadata, but fall back to soft-tissue preset
    center = _as_float(getattr(ds, "WindowCenter", None), default_level)
    width = _as_float(getattr(ds, "WindowWidth", None), default_window)
    if width <= 0:
        width = default_window
    
    return center, width


def _decode_pixels(ds: pydicom.dataset.Dataset, path: str) -> np.ndarray:
    """
    Load DICOM pixel data correctly in 16-bit, preserving full bit depth.
    Returns float32 array (not normalized to 0-255).
    RescaleSlope and RescaleIntercept are applied later in the pipeline.
    """
    try:
        # Load pixel array - preserve original bit depth (16-bit)
        pixels = ds.pixel_array
        
        # Check bit depth for logging
        bits_stored = getattr(ds, "BitsStored", None)
        bits_allocated = getattr(ds, "BitsAllocated", None)
        if bits_stored:
            logger.debug("DICOM %s: BitsStored=%s, BitsAllocated=%s", path, bits_stored, bits_allocated)
        
        # Convert to float32 to preserve full dynamic range
        # Do NOT normalize to 0-255 at this stage
        pixels_float = pixels.astype(np.float32)
        
        # Verify we have a reasonable range (not already 8-bit)
        pixel_min, pixel_max = float(np.min(pixels_float)), float(np.max(pixels_float))
        if pixel_max - pixel_min < 256:
            logger.warning(
                "DICOM %s appears to be 8-bit (range %.1f-%.1f). Expected 16-bit data.",
                path, pixel_min, pixel_max
            )
        else:
            logger.debug("DICOM %s pixel range: %.1f to %.1f", path, pixel_min, pixel_max)
        
        return pixels_float
    except Exception as exc:
        logger.warning("pydicom failed to decode %s: %s", path, exc)
        if not _HAS_SIMPLEITK:
            raise RuntimeError(
                "Missing compression handlers (install 'gdcm' or 'pylibjpeg' or add SimpleITK)."
            ) from exc

        try:
            image = sitk.ReadImage(path)
            array = sitk.GetArrayFromImage(image).astype(np.float32)
            # SimpleITK returns (frames, rows, cols); most CBCT slices are single frame
            if array.ndim == 3 and array.shape[0] == 1:
                array = array[0]
            elif array.ndim == 3 and array.shape[0] > 1:
                logger.warning(
                    "DICOM %s contains %s frames; using the first frame for volume stacking.",
                    path,
                    array.shape[0]
                )
                array = array[0]
            return array
        except Exception as sitk_exc:
            logger.error("SimpleITK fallback failed for %s: %s", path, sitk_exc)
            raise RuntimeError(
                "Unable to decode compressed pixel data. Install 'gdcm' or 'pylibjpeg'"
            ) from sitk_exc


def _estimate_total_slices(datasets: List[pydicom.dataset.Dataset]) -> int:
    total = 0
    for ds in datasets:
        frames = getattr(ds, "NumberOfFrames", None)
        try:
            frames_int = int(frames)
        except (TypeError, ValueError):
            frames_int = 0
        if frames_int and frames_int > 0:
            total += frames_int
        else:
            total += 1
    return total



def _resample_volume_isotropic(volume: np.ndarray, row_spacing: float, col_spacing: float, slice_spacing: float, max_bytes: float, max_dimension: int = 0) -> Tuple[np.ndarray, float, float, float, bool]:
    """Resample volume to isotropic spacing, optionally downsampling large volumes.
    
    Args:
        max_dimension: If > 0, downsample so no dimension exceeds this value (e.g., 512)
    """
    spacings = [float(slice_spacing), float(row_spacing), float(col_spacing)]
    if not all(np.isfinite(s) and s > 0 for s in spacings):
        return volume, row_spacing, col_spacing, slice_spacing, False

    target_spacing = float(min(spacings))
    
    # If max_dimension specified, calculate spacing to fit within limit
    if max_dimension > 0:
        current_max_dim = max(volume.shape)
        if current_max_dim > max_dimension:
            # Increase spacing to reduce dimensions
            scale_factor = current_max_dim / max_dimension
            target_spacing = target_spacing * scale_factor
            logger.info(
                "Downsampling: max dimension %d -> %d (scale %.2fx, new spacing %.3fmm)",
                current_max_dim, max_dimension, scale_factor, target_spacing
            )
    
    if max(spacings) - target_spacing < 1e-3 and max_dimension <= 0:
        return volume, row_spacing, col_spacing, slice_spacing, False

    if not _HAS_SIMPLEITK:
        logger.warning(
            "SimpleITK is not available; skipping isotropic resampling which can cause sagittal/coronal blurring."
        )
        return volume, row_spacing, col_spacing, slice_spacing, False

    try:
        sitk_image = sitk.GetImageFromArray(np.asarray(volume, dtype=np.float32))
        sitk_image.SetSpacing((float(col_spacing), float(row_spacing), float(slice_spacing)))

        original_spacing = np.array(sitk_image.GetSpacing(), dtype=np.float64)
        original_size = np.array(sitk_image.GetSize(), dtype=np.int32)

        new_spacing = np.array([target_spacing, target_spacing, target_spacing], dtype=np.float64)
        new_size = np.maximum(
            np.round(original_size * (original_spacing / new_spacing)).astype(np.int32),
            1
        )

        prospective_bytes = int(np.prod(new_size) * 4)
        if prospective_bytes > max_bytes:
            human_size = prospective_bytes / (1024 ** 3)
            human_limit = max_bytes / (1024 ** 3)
            logger.warning(
                "Skipping isotropic resample (%.2f GiB) because it would exceed the configured %.2f GiB limit.",
                human_size,
                human_limit
            )
            return volume, row_spacing, col_spacing, slice_spacing, False

        resampler = sitk.ResampleImageFilter()
        resampler.SetInterpolator(sitk.sitkLinear)
        resampler.SetOutputSpacing(tuple(new_spacing.tolist()))
        resampler.SetSize([int(x) for x in new_size.tolist()])
        resampler.SetOutputOrigin(sitk_image.GetOrigin())
        resampler.SetOutputDirection(sitk_image.GetDirection())

        resampled = resampler.Execute(sitk_image)
        volume_iso = sitk.GetArrayFromImage(resampled)
        volume_iso = np.asarray(volume_iso, dtype=np.float32)

        return volume_iso, target_spacing, target_spacing, target_spacing, True
    except Exception as exc:
        logger.error("Failed to resample CBCT volume to isotropic spacing: %s", exc)
        return volume, row_spacing, col_spacing, slice_spacing, False

def _reslice_volume(sitk_image: sitk.Image, plane: str) -> sitk.Image:
    """
    Reslice volume using SimpleITK for proper MPR orientation.
    Fixes wrong orientation, upside-down slices, left/right inversion, and misaligned planes.
    
    Args:
        sitk_image: SimpleITK image (3D volume)
        plane: 'axial', 'sagittal', or 'coronal'
    
    Returns:
        Resliced SimpleITK image
    """
    if not _HAS_SIMPLEITK:
        raise RuntimeError("SimpleITK is required for proper MPR reslicing")
    
    spacing = sitk_image.GetSpacing()
    origin = sitk_image.GetOrigin()
    size = sitk_image.GetSize()
    
    # Define new direction matrices for each plane
    # Note: We keep the original direction and handle flips in slice extraction
    # This ensures proper orientation matching DICOM viewer
    if plane == 'axial':
        new_direction = [1, 0, 0, 0, 1, 0, 0, 0, 1]
    elif plane == 'coronal':
        # Coronal: keep original direction, will flip vertically in slice extraction
        new_direction = [1, 0, 0, 0, 1, 0, 0, 0, 1]
    elif plane == 'sagittal':
        # Sagittal: keep original direction, will flip vertically in slice extraction
        new_direction = [1, 0, 0, 0, 1, 0, 0, 0, 1]
    else:
        raise ValueError(f"Unknown plane '{plane}'")
    
    new_size = [size[0], size[1], size[2]]
    
    resampler = sitk.ResampleImageFilter()
    resampler.SetInterpolator(sitk.sitkLinear)
    resampler.SetOutputSpacing(spacing)
    resampler.SetOutputOrigin(origin)
    resampler.SetOutputDirection(new_direction)
    resampler.SetSize(new_size)
    
    return resampler.Execute(sitk_image)


# Vendor-specific orientation corrections
# NOTE: These need to be empirically determined by comparing with Weasis for each vendor
# Set confidence to 0 to disable vendor hints and use direction matrix analysis instead
# Format: flip values + confidence score
VENDOR_ORIENTATION_HINTS = {
    # Vatech: Common dental CBCT
    # TODO: Empirically determine correct values by testing with actual Vatech files
    "vatech": {
        "flip_axial_x": False,
        "flip_axial_y": False,
        "flip_coronal_x": False,
        "flip_coronal_y": False,
        "flip_sagittal_x": False,
        "flip_sagittal_y": False,
        "confidence": 0,  # DISABLED - use direction matrix instead
        "notes": "Vatech - needs empirical testing"
    },
    # Other vendors - all disabled until empirically tested
    "carestream": {"confidence": 0, "notes": "Needs empirical testing"},
    "planmeca": {"confidence": 0, "notes": "Needs empirical testing"},
    "i-cat": {"confidence": 0, "notes": "Needs empirical testing"},
    "newtom": {"confidence": 0, "notes": "Needs empirical testing"},
}


def _detect_vendor(datasets: List[Tuple[pydicom.dataset.Dataset, str]]) -> Tuple[str, dict]:
    """
    Detect CBCT vendor from DICOM metadata.
    
    Returns:
        (vendor_key, hint_dict) or (None, None) if unknown
    """
    if not datasets:
        return None, None
    
    ds = datasets[0][0]
    
    # Collect identifying info
    manufacturer = str(getattr(ds, "Manufacturer", "")).lower()
    model = str(getattr(ds, "ManufacturerModelName", "")).lower()
    institution = str(getattr(ds, "InstitutionName", "")).lower()
    station = str(getattr(ds, "StationName", "")).lower()
    
    combined = f"{manufacturer} {model} {institution} {station}"
    
    # Match against known vendors
    if "vatech" in combined or "ez3d" in combined or "pax-i" in combined:
        return "vatech", VENDOR_ORIENTATION_HINTS.get("vatech")
    if "carestream" in combined or "cs 9" in combined or "cs9" in combined:
        return "carestream", VENDOR_ORIENTATION_HINTS.get("carestream")
    if "planmeca" in combined or "promax" in combined:
        return "planmeca", VENDOR_ORIENTATION_HINTS.get("planmeca")
    if "i-cat" in combined or "icat" in combined or "imaging sciences" in combined:
        return "i-cat", VENDOR_ORIENTATION_HINTS.get("i-cat")
    if "newtom" in combined or "cefla" in combined or "qr s" in combined:
        return "newtom", VENDOR_ORIENTATION_HINTS.get("newtom")
    
    logger.info("Unknown vendor: manufacturer='%s', model='%s'", manufacturer, model)
    return None, None


def _infer_orientation_from_ipp(datasets: List[Tuple[pydicom.dataset.Dataset, str]]) -> Tuple[List[float], List[float], List[float], str]:
    """
    Infer orientation when ImageOrientationPatient (IOP) is missing.
    Uses PCA on ImagePositionPatient points to estimate the slice axis,
    then assumes standard axial acquisition for in-plane axes.
    
    Returns:
        (row_dir, col_dir, slice_normal, method) - direction vectors and inference method used
    """
    # Collect all IPP points
    ipp_points = []
    for ds, path in datasets:
        if hasattr(ds, "ImagePositionPatient") and len(ds.ImagePositionPatient) >= 3:
            ipp = [float(ds.ImagePositionPatient[i]) for i in range(3)]
            ipp_points.append(ipp)
    
    if len(ipp_points) < 3:
        # Not enough points for PCA - use identity (assume standard axial)
        logger.warning("Not enough IPP points for orientation inference, using identity")
        return [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], "identity_fallback"
    
    ipp_array = np.array(ipp_points)
    
    # PCA to find the principal axis (slice direction)
    centered = ipp_array - np.mean(ipp_array, axis=0)
    try:
        _, _, vh = np.linalg.svd(centered)
        # First principal component is the direction of maximum variance (slice axis)
        slice_axis = vh[0]
        slice_axis = slice_axis / np.linalg.norm(slice_axis)
        
        # Determine which anatomical axis this is closest to
        # Standard axes: X=L-R, Y=A-P, Z=S-I (in LPS)
        abs_slice = np.abs(slice_axis)
        dominant_idx = int(np.argmax(abs_slice))
        
        # Build orthogonal coordinate system
        # The slice axis becomes the Z of our volume
        # Choose X and Y to be orthogonal and aligned with remaining axes
        
        if dominant_idx == 2:  # Slice axis is mostly S-I (standard axial)
            # Standard axial: slice along Z, rows along Y (A-P), cols along X (L-R)
            row_dir = [1.0, 0.0, 0.0]  # X = L-R
            col_dir = [0.0, 1.0, 0.0]  # Y = A-P
            normal_dir = list(slice_axis if slice_axis[2] > 0 else -slice_axis)
            method = "pca_axial"
        elif dominant_idx == 1:  # Slice axis is mostly A-P (coronal acquisition)
            # Coronal: slice along Y, rows along Z (S-I), cols along X (L-R)
            row_dir = [1.0, 0.0, 0.0]  # X = L-R
            col_dir = [0.0, 0.0, 1.0]  # Z = S-I
            normal_dir = list(slice_axis if slice_axis[1] > 0 else -slice_axis)
            method = "pca_coronal"
        else:  # Slice axis is mostly L-R (sagittal acquisition)
            # Sagittal: slice along X, rows along Z (S-I), cols along Y (A-P)
            row_dir = [0.0, 1.0, 0.0]  # Y = A-P
            col_dir = [0.0, 0.0, 1.0]  # Z = S-I
            normal_dir = list(slice_axis if slice_axis[0] > 0 else -slice_axis)
            method = "pca_sagittal"
        
        logger.info("Inferred orientation from IPP via PCA: slice_axis=%s, method=%s", 
                   slice_axis.tolist(), method)
        return row_dir, col_dir, normal_dir, method
        
    except Exception as e:
        logger.warning("PCA orientation inference failed: %s, using identity", e)
        return [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], "identity_fallback"


def _determine_anatomical_axes(direction: Tuple[float, ...]) -> dict:
    """
    Analyze direction matrix to determine which volume axis corresponds to which anatomical axis.
    
    DICOM LPS convention:
    - X increases toward patient's Left
    - Y increases toward patient's Posterior  
    - Z increases toward patient's Superior
    
    Returns dict with:
    - 'volume_to_anatomical': mapping of volume axis (0,1,2) to anatomical axis ('LR','AP','SI')
    - 'axis_signs': sign of each axis (positive = toward L/P/S, negative = toward R/A/I)
    """
    # Direction matrix is 3x3: columns are the directions of volume axes in patient coordinates
    dir_matrix = np.array(direction).reshape(3, 3)
    
    # For each volume axis, find which anatomical axis it most closely aligns with
    anatomical_labels = ['LR', 'AP', 'SI']  # X=LR, Y=AP, Z=SI in LPS
    
    volume_to_anatomical = {}
    axis_signs = {}
    
    for vol_axis in range(3):
        col = dir_matrix[:, vol_axis]  # Direction of this volume axis in patient space
        
        # Find which patient axis (X/LR, Y/AP, Z/SI) this aligns with most
        abs_col = np.abs(col)
        dominant_patient_axis = int(np.argmax(abs_col))
        sign = 1 if col[dominant_patient_axis] > 0 else -1
        
        volume_to_anatomical[vol_axis] = anatomical_labels[dominant_patient_axis]
        axis_signs[vol_axis] = sign
    
    return {
        'volume_to_anatomical': volume_to_anatomical,
        'axis_signs': axis_signs,
        'direction_matrix': dir_matrix.tolist()
    }


def _save_plane_slices(
    volume: np.ndarray,
    plane: str,
    level: float,
    window: float,
    spacing: Tuple[float, float, float],
    origin: Tuple[float, float, float],
    direction: Tuple[float, ...],
    output_dir: str,
    vendor_hints: dict = None,
) -> int:
    """
    Save MPR slices with proper anatomical orientation based on DICOM direction matrix.
    
    Standard radiological display conventions:
    - Axial: looking from feet toward head, R on viewer's left, A at top
    - Coronal: looking from front, R on viewer's left, S at top
    - Sagittal: looking from patient's right side, A on viewer's left, S at top
    
    Args:
        volume: 3D volume array (float32) in (Z, Y, X) order (numpy convention)
        plane: 'axial', 'coronal', or 'sagittal'
        level: Window level (L)
        window: Window width (W)
        spacing: (x, y, z) spacing in mm (SimpleITK convention)
        origin: (x, y, z) origin in mm
        direction: 3x3 direction matrix flattened (row-major), maps volume to patient LPS
        output_dir: Output directory for PNG files
    
    Returns:
        Number of slices saved
    """
    os.makedirs(output_dir, exist_ok=True)

    if not _HAS_SIMPLEITK:
        raise RuntimeError("SimpleITK is required for proper MPR reslicing")

    if direction is None:
        direction = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)

    # Build SimpleITK image from numpy volume
    sitk_image = sitk.GetImageFromArray(volume)
    sitk_image.SetSpacing((float(spacing[0]), float(spacing[1]), float(spacing[2])))
    sitk_image.SetOrigin(tuple(origin))
    sitk_image.SetDirection(tuple(direction))

    def save_slice_array(slice_arr: np.ndarray, idx: int):
        arr = apply_window_level(slice_arr.astype(np.float32), window, level)
        Image.fromarray(arr, 'L').save(os.path.join(output_dir, f"{plane}_{idx:03d}.png"))

    # Analyze direction matrix to understand anatomical orientation
    axis_info = _determine_anatomical_axes(direction)
    vol_to_anat = axis_info['volume_to_anatomical']
    axis_signs = axis_info['axis_signs']
    
    logger.info("Plane %s: axis mapping %s, signs %s", plane, vol_to_anat, axis_signs)

    # Find which SimpleITK axis (0,1,2) corresponds to each anatomical direction
    # SimpleITK uses (i, j, k) = (X, Y, Z) ordering
    lr_axis = next((k for k, v in vol_to_anat.items() if v == 'LR'), 0)
    ap_axis = next((k for k, v in vol_to_anat.items() if v == 'AP'), 1)
    si_axis = next((k for k, v in vol_to_anat.items() if v == 'SI'), 2)
    
    permuter = sitk.PermuteAxesImageFilter()
    flipper = sitk.FlipImageFilter()

    # Check for vendor-specific flip overrides
    use_vendor_hints = vendor_hints is not None and vendor_hints.get('confidence', 0) > 0.7
    
    if plane == 'axial':
        # Axial: slices along SI, rows=AP (A at top), cols=LR (R on left)
        # Standard view: looking from feet toward head
        # SimpleITK arrays are already in LPS space - after correct permutation,
        # rows naturally go A→P and we want A at top, so NO vertical flip needed
        permute_order = [lr_axis, ap_axis, si_axis]
        permuter.SetOrder(permute_order)
        resliced = permuter.Execute(sitk_image)
        
        if use_vendor_hints:
            flip_x = vendor_hints.get('flip_axial_x', False)
            flip_y = vendor_hints.get('flip_axial_y', False)
            logger.info("Using vendor hints for axial: flip_x=%s, flip_y=%s", flip_x, flip_y)
        else:
            # Only flip X if LR axis points toward R (need R on viewer's left)
            flip_x = axis_signs[lr_axis] < 0
            flip_y = False  # No vertical flip - SimpleITK LPS already correct
        flipper.SetFlipAxes([flip_x, flip_y, False])
        resliced = flipper.Execute(resliced)
        
    elif plane == 'coronal':
        # Coronal: slices along AP, rows=SI (S at top), cols=LR (R on left)
        # Standard view: looking from front of patient
        # SimpleITK arrays are already in LPS space - after correct permutation,
        # rows naturally go S→I and we want S at top, so NO vertical flip needed
        permute_order = [lr_axis, si_axis, ap_axis]
        permuter.SetOrder(permute_order)
        resliced = permuter.Execute(sitk_image)
        
        if use_vendor_hints:
            flip_x = vendor_hints.get('flip_coronal_x', False)
            flip_y = vendor_hints.get('flip_coronal_y', False)
            logger.info("Using vendor hints for coronal: flip_x=%s, flip_y=%s", flip_x, flip_y)
        else:
            # Only flip X if LR axis points toward R (need R on viewer's left)
            flip_x = axis_signs[lr_axis] < 0
            flip_y = False  # No vertical flip - SimpleITK LPS already correct
        flipper.SetFlipAxes([flip_x, flip_y, False])
        resliced = flipper.Execute(resliced)
        
    elif plane == 'sagittal':
        # Sagittal: slices along LR, rows=SI (S at top), cols=AP (A on left)
        # Standard view: looking from patient's right side
        permute_order = [ap_axis, si_axis, lr_axis]
        permuter.SetOrder(permute_order)
        resliced = permuter.Execute(sitk_image)
        
        if use_vendor_hints:
            flip_x = vendor_hints.get('flip_sagittal_x', False)
            flip_y = vendor_hints.get('flip_sagittal_y', False)
            logger.info("Using vendor hints for sagittal: flip_x=%s, flip_y=%s", flip_x, flip_y)
        else:
            flip_x = axis_signs[ap_axis] < 0
            flip_y = axis_signs[si_axis] < 0
        flipper.SetFlipAxes([flip_x, flip_y, False])
        resliced = flipper.Execute(resliced)
    else:
        raise ValueError(f"Unknown plane '{plane}'")

    arr = sitk.GetArrayFromImage(resliced)  # shape: (slices, rows, cols)
    count = arr.shape[0]
    for idx in range(count):
        save_slice_array(arr[idx, :, :], idx)
    
    logger.info("Saved %d %s slices (flips applied: x=%s, y=%s)", count, plane, flip_x, flip_y)
    return count


def generate_cbct_mpr(patient_id: int, folder_name: str, overwrite: bool = True, progress_callback=None) -> Tuple[bool, str]:
    """Generate MPR stacks for a CBCT series and upload to S3.
    
    Args:
        progress_callback: Optional callable(percent: int, message: str) for progress updates
    """
    def update_progress(percent: int, message: str):
        if progress_callback:
            try:
                progress_callback(percent, message)
            except Exception:
                pass  # Don't fail on progress update errors
    bucket = current_app.config.get('S3_BUCKET') or current_app.config.get('S3_BUCKET_NAME')
    if not bucket:
        return False, "S3 bucket is not configured"

    region = (current_app.config.get('AWS_REGION')
              or os.getenv('AWS_REGION')
              or current_app.config.get('BEDROCK_AWS_REGION')
              or 'us-east-1')

    s3_client = boto3.client('s3', region_name=region)

    source_prefix = f"patients/{patient_id}/imaging/cbct/{folder_name}/"
    dest_prefix = f"patients/{patient_id}/imaging/cbct_mpr/{folder_name}/"
    start_time = time.monotonic()

    logger.info("Listing CBCT objects for prefix %s", source_prefix)
    objects = _list_s3_objects(s3_client, bucket, source_prefix)
    dicom_objects = [obj for obj in objects if obj['Key'].lower().endswith(('.dcm', '.dicom', '.dcom'))]

    if not dicom_objects:
        return False, "No DICOM files found under prefix"

    max_source_bytes = current_app.config.get('CBCT_MPR_MAX_SOURCE_BYTES', 2_500_000_000)  # ~2.3 GiB - safe for servers with 8GB+ RAM
    total_source_bytes = sum(obj.get('Size', 0) for obj in dicom_objects)
    if total_source_bytes > max_source_bytes:
        human_total = total_source_bytes / (1024 ** 3)
        human_limit = max_source_bytes / (1024 ** 3)
        message = (
            f"CBCT folder is too large for in-app MPR ({human_total:.2f} GiB > {human_limit:.2f} GiB limit). "
            "Please down-sample or generate offline."
        )
        logger.warning(
            "Rejecting MPR generation for patient %s folder %s: source bytes %.2f GiB exceeds limit %.2f GiB",
            patient_id,
            folder_name,
            human_total,
            human_limit,
        )
        return False, message

    max_dicom_files = current_app.config.get('CBCT_MPR_MAX_DICOM_FILES', 1200)
    if len(dicom_objects) > max_dicom_files:
        message = (
            f"CBCT folder contains {len(dicom_objects)} DICOM files which exceeds the "
            f"limit of {max_dicom_files} for in-app MPR."
        )
        logger.warning(
            "Rejecting MPR generation for patient %s folder %s: %s",
            patient_id,
            folder_name,
            message,
        )
        return False, message

    if overwrite:
        logger.info("Removing existing MPR objects under %s", dest_prefix)
        existing = _list_s3_objects(s3_client, bucket, dest_prefix)
        if existing:
            delete_items = [{'Key': obj['Key']} for obj in existing]
            for chunk_start in range(0, len(delete_items), 1000):
                chunk = delete_items[chunk_start:chunk_start + 1000]
                s3_client.delete_objects(Bucket=bucket, Delete={'Objects': chunk})

    # Use disk-based temp directory instead of RAM-based /tmp (tmpfs)
    # This prevents "no space left" errors for large CBCT files
    mpr_temp_base = current_app.config.get('CBCT_MPR_TEMP_DIR') or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'tmp_mpr'
    )
    os.makedirs(mpr_temp_base, exist_ok=True)
    stale_age_seconds = int(current_app.config.get("CBCT_MPR_TEMP_DIR_MAX_AGE_SECONDS", 24 * 3600))
    _cleanup_stale_mpr_temp_dirs(mpr_temp_base, stale_age_seconds)
    
    with tempfile.TemporaryDirectory(dir=mpr_temp_base) as tmp_dir:
        update_progress(5, f"Downloading {len(dicom_objects)} DICOM files...")
        logger.info("Downloading %s DICOM files to %s", len(dicom_objects), tmp_dir)
        download_start = time.monotonic()
        datasets_with_paths = _download_dicom_series(s3_client, bucket, dicom_objects, tmp_dir)
        download_duration = time.monotonic() - download_start
        logger.info(
            "Downloaded %s DICOM slices in %.2f seconds",
            len(datasets_with_paths),
            download_duration
        )
        if not datasets_with_paths:
            return False, "Failed to download DICOM series"
        update_progress(20, f"Downloaded {len(datasets_with_paths)} files. Validating geometry...")

        # Validate geometry and sort by IPP projection
        geometry_diagnostics = _validate_geometry(datasets_with_paths)
        logger.info(
            "Geometry validation: status=%s, issues=%s, sort_method=%s",
            geometry_diagnostics.get("geometry_status"),
            geometry_diagnostics.get("issues", []),
            geometry_diagnostics.get("sort_method")
        )
        
        datasets_sorted = _sort_datasets_by_ipp(datasets_with_paths, geometry_diagnostics)
        # Some CBCT folders may contain mixed-resolution DICOMs (e.g., localizers/scouts).
        # Pick the most common (Rows, Columns) and drop the rest up-front so we don't
        # accidentally key off an outlier and then skip the real series.
        dims_counter = Counter()
        for ds_meta, _local_path in datasets_sorted:
            r = int(getattr(ds_meta, "Rows", 0) or 0)
            c = int(getattr(ds_meta, "Columns", 0) or 0)
            if r > 0 and c > 0:
                dims_counter[(r, c)] += 1

        if dims_counter:
            (mode_rows, mode_cols), mode_count = dims_counter.most_common(1)[0]
            if len(dims_counter) > 1:
                logger.warning(
                    "Detected mixed DICOM dimensions for patient %s folder %s; using most common %dx%d (%d/%d) and skipping others: %s",
                    patient_id,
                    folder_name,
                    mode_rows,
                    mode_cols,
                    mode_count,
                    sum(dims_counter.values()),
                    dict(dims_counter),
                )
            datasets_sorted = [
                (ds_meta, local_path)
                for (ds_meta, local_path) in datasets_sorted
                if int(getattr(ds_meta, "Rows", 0) or 0) == mode_rows
                and int(getattr(ds_meta, "Columns", 0) or 0) == mode_cols
            ]

        dataset_only = [item[0] for item in datasets_sorted]

        if not dataset_only:
            return False, "No valid DICOM datasets found"

        rows = int(getattr(dataset_only[0], "Rows", 0) or 0)
        cols = int(getattr(dataset_only[0], "Columns", 0) or 0)
        if rows <= 0 or cols <= 0:
            logger.error(
                "Unable to determine slice dimensions for patient %s folder %s (Rows=%s, Columns=%s)",
                patient_id,
                folder_name,
                rows,
                cols,
            )
            return False, "Unable to determine slice dimensions from DICOM metadata"

        total_slices_expected = _estimate_total_slices(dataset_only)
        if total_slices_expected <= 0:
            return False, "Unable to determine slice count for CBCT series"

        max_slices = current_app.config.get('CBCT_MPR_MAX_TOTAL_SLICES', 2000)
        if total_slices_expected > max_slices:
            message = (
                f"CBCT folder has {total_slices_expected} slices, exceeding the limit of {max_slices}."
            )
            logger.warning(
                "Rejecting MPR generation for patient %s folder %s: %s",
                patient_id,
                folder_name,
                message,
            )
            return False, message

        row_spacing, col_spacing, slice_spacing = _compute_spacing(dataset_only)

        estimated_working_bytes = total_slices_expected * rows * cols * 4  # float32 volume
        # Reduced to 2.5 GB to be safe on 8GB systems (SimpleITK needs 2-3x working memory)
        max_working_bytes = current_app.config.get('CBCT_MPR_MAX_WORKING_BYTES', 2_500_000_000)  # 2.5 GB working memory
        
        # Calculate if downsampling is needed
        max_dimension = 0  # 0 = no downsampling
        if estimated_working_bytes > max_working_bytes:
            # Calculate scale factor needed to fit within memory limit
            scale_factor = (estimated_working_bytes / max_working_bytes) ** (1/3)  # cube root for 3D
            current_max_dim = max(total_slices_expected, rows, cols)
            max_dimension = int(current_max_dim / scale_factor)
            max_dimension = max(256, min(max_dimension, 768))  # Clamp between 256 and 768
            
            human_estimated = estimated_working_bytes / (1024 ** 3)
            logger.info(
                "Large volume detected (%.2f GiB). Will downsample to max dimension %d.",
                human_estimated, max_dimension
            )

        volume_path = os.path.join(tmp_dir, 'volume_float32.dat')
        volume_memmap = np.memmap(
            volume_path,
            dtype=np.float32,
            mode='w+',
            shape=(total_slices_expected, rows, cols)
        )

        dicom_center, dicom_width = _determine_window(dataset_only[0])
        window_center = float(dicom_center)
        window_width = float(dicom_width)
        slice_index = 0
        skipped_dim_mismatch = 0
        skip_dim_mismatch = bool(
            current_app.config.get("CBCT_MPR_SKIP_DIM_MISMATCH_SLICES", False)
        )
        min_intensity = float('inf')
        max_intensity = float('-inf')

        for ds_meta, local_path in datasets_sorted:
            if slice_index >= total_slices_expected:
                logger.warning(
                    "Reached slice cap (%s) while processing patient %s folder %s; skipping remaining files.",
                    total_slices_expected,
                    patient_id,
                    folder_name,
                )
                break
            try:
                ds_full = pydicom.dcmread(local_path)
            except Exception as exc:
                logger.error("Failed to re-read DICOM %s: %s", local_path, exc)
                del volume_memmap
                return False, f"Failed to read DICOM slice: {exc}"

            pixels = _decode_pixels(ds_full, local_path)
            slope = getattr(ds_full, "RescaleSlope", 1.0)
            intercept = getattr(ds_full, "RescaleIntercept", 0.0)
            slope32 = np.float32(slope)
            intercept32 = np.float32(intercept)

            if pixels.ndim == 2:
                if pixels.shape != (rows, cols):
                    if skip_dim_mismatch:
                        skipped_dim_mismatch += 1
                        logger.warning(
                            "Slice dimension mismatch for %s (expected %sx%s, found %s) – skipping slice",
                            local_path,
                            rows,
                            cols,
                            pixels.shape,
                        )
                        continue
                    else:
                        logger.error(
                            "Slice dimension mismatch for %s (expected %sx%s, found %s)",
                            local_path,
                            rows,
                            cols,
                            pixels.shape,
                        )
                        del volume_memmap
                        return False, "Slice dimensions vary across the CBCT series"

                slice_data = np.asarray(pixels, dtype=np.float32)
                if slope != 1.0 or intercept != 0.0:
                    slice_data = slice_data * slope32 + intercept32
                slice_min = float(np.min(slice_data))
                slice_max = float(np.max(slice_data))
                if np.isfinite(slice_min):
                    min_intensity = min(min_intensity, slice_min)
                if np.isfinite(slice_max):
                    max_intensity = max(max_intensity, slice_max)
                volume_memmap[slice_index, :, :] = slice_data
                slice_index += 1
            elif pixels.ndim == 3:
                start_index = slice_index
                frame_count = pixels.shape[0]
                if frame_count > 1:
                    logger.info("Expanding multi-frame DICOM %s into %s slices", local_path, frame_count)
                for frame_idx in range(frame_count):
                    if slice_index >= total_slices_expected:
                        logger.warning(
                            "More slices detected than expected for patient %s folder %s; truncating remainder.",
                            patient_id,
                            folder_name,
                        )
                        break
                    frame = pixels[frame_idx, ...]
                    if frame.shape != (rows, cols):
                        if skip_dim_mismatch:
                            skipped_dim_mismatch += 1
                            logger.warning(
                                "Frame dimension mismatch for %s frame %s (expected %sx%s, found %s) – skipping file",
                                local_path,
                                frame_idx,
                                rows,
                                cols,
                                frame.shape,
                            )
                            # Roll back any frames already written for this file.
                            slice_index = start_index
                            break
                        else:
                            logger.error(
                                "Frame dimension mismatch for %s frame %s (expected %sx%s, found %s)",
                                local_path,
                                frame_idx,
                                rows,
                                cols,
                                frame.shape,
                            )
                            del volume_memmap
                            return False, "Slice dimensions vary across the CBCT series"
                    slice_data = np.asarray(frame, dtype=np.float32)
                    if slope != 1.0 or intercept != 0.0:
                        slice_data = slice_data * slope32 + intercept32
                    slice_min = float(np.min(slice_data))
                    slice_max = float(np.max(slice_data))
                    if np.isfinite(slice_min):
                        min_intensity = min(min_intensity, slice_min)
                    if np.isfinite(slice_max):
                        max_intensity = max(max_intensity, slice_max)
                    volume_memmap[slice_index, :, :] = slice_data
                    slice_index += 1
                # If we rolled back due to mismatch, skip to next file.
                if slice_index == start_index and frame_count > 0:
                    continue
                if slice_index >= total_slices_expected:
                    break
            else:
                del volume_memmap
                raise RuntimeError(
                    f"Unsupported pixel array shape {pixels.shape} for {local_path}; expected 2D or 3D"
                )

        actual_slices = slice_index
        if actual_slices == 0:
            del volume_memmap
            return False, "No slices were processed in CBCT series"

        if skipped_dim_mismatch:
            logger.info(
                "Skipped %d DICOM slice(s)/file(s) due to dimension mismatch for patient %s folder %s",
                skipped_dim_mismatch,
                patient_id,
                folder_name,
            )

        if actual_slices < total_slices_expected:
            logger.info(
                "Processed %s slices (expected %s) for patient %s folder %s",
                actual_slices,
                total_slices_expected,
                patient_id,
                folder_name,
            )

        volume_memmap.flush()
        # Important: force a real in-memory ndarray and close the memmap.
        # If `volume_view` stays as a memmap/view, the underlying file can stay
        # mapped/open in a long-running server process, delaying disk reclamation.
        volume_view = np.array(volume_memmap[:actual_slices, :, :], dtype=np.float32, copy=True)
        try:
            mmap_obj = getattr(volume_memmap, "_mmap", None)
            if mmap_obj is not None:
                mmap_obj.close()
        except Exception:
            pass
        del volume_memmap
        # Best-effort delete the memmap backing file early (tmp dir cleanup will also remove it).
        try:
            os.remove(volume_path)
        except OSError:
            pass
        update_progress(35, f"Built volume ({actual_slices} slices). Resampling...")

        volume_view, row_spacing, col_spacing, slice_spacing, resampled = _resample_volume_isotropic(
            volume_view,
            row_spacing,
            col_spacing,
            slice_spacing,
            max_working_bytes,
            max_dimension
        )
        if resampled:
            logger.info(
                "Resampled CBCT volume to isotropic spacing %.3f mm; new shape %s",
                slice_spacing,
                volume_view.shape,
            )
        
        # Check if normalization is needed
        # If data is already in reasonable HU range (-1000 to 3000), skip normalization
        raw_min = float(np.min(volume_view))
        raw_max = float(np.max(volume_view))
        logger.info("Volume intensity range (raw): %.1f to %.1f", raw_min, raw_max)
        
        normalization_applied = False
        # Apply CBCT pseudo-HU normalization only if data is not already in HU-like range
        # Most CBCT data after RescaleSlope/Intercept should be in HU range already
        if raw_min < -500 or raw_max > 4000:
            logger.info("Applying CBCT pseudo-HU normalization (data outside expected HU range)...")
            volume_view = normalize_cbct_intensity(volume_view)
            min_intensity = float(np.min(volume_view))
            max_intensity = float(np.max(volume_view))
            logger.info("Volume intensity range after normalization: %.1f to %.1f", 
                        min_intensity, max_intensity)
            normalization_applied = True
        else:
            logger.info("Skipping normalization - data already in HU-like range")
            min_intensity = raw_min
            max_intensity = raw_max

        if not np.isfinite(min_intensity) or not np.isfinite(max_intensity):
            volume_min = float(np.min(volume_view))
            volume_max = float(np.max(volume_view))
            if np.isfinite(volume_min):
                min_intensity = volume_min
            if np.isfinite(volume_max):
                max_intensity = volume_max

        # Use percentile-based adaptive Window/Level
        # This is the standard approach that works across all CBCT manufacturers
        # by adapting to the actual data distribution rather than fixed presets
        try:
            # Sample the volume for percentile calculation
            # Use subsampling for large volumes (every Nth voxel) for speed
            volume_flat = volume_view.ravel()
            if len(volume_flat) > 10_000_000:
                # Subsample for very large volumes
                step = len(volume_flat) // 5_000_000
                sample_data = volume_flat[::step]
            else:
                sample_data = volume_flat
            
            # For CBCT: DON'T exclude zeros - air is meaningful data (around 0 in HU)
            # Use wide percentiles (P0.5 to P99.5) to capture full useful range
            # This matches how Weasis and other medical viewers calculate auto W/L
            p_low = float(np.percentile(sample_data, 0.5))
            p_high = float(np.percentile(sample_data, 99.5))
            
            # Window = range between percentiles
            # For CBCT, ensure minimum window of 2000 for good soft tissue visibility
            calculated_width = p_high - p_low
            window_width = max(calculated_width, 2000.0)
            
            # Level = center, but biased slightly toward the higher values
            # where bone and soft tissue typically are (not air)
            # Use weighted average: 40% p_low, 60% p_high
            window_level = p_low * 0.4 + p_high * 0.6
            
            logger.info(
                "Adaptive W/L: W=%.1f, L=%.1f (P0.5=%.1f, P99.5=%.1f, calculated_width=%.1f, data range: %.1f to %.1f)", 
                window_width, window_level, p_low, p_high, calculated_width, min_intensity, max_intensity
            )
        except Exception as e:
            # Fallback: use full data range if percentile calculation fails
            window_width = max(max_intensity - min_intensity, 2000.0)
            window_level = (min_intensity + max_intensity) / 2.0
            logger.warning("Percentile calculation failed, using full range fallback W/L (W=%.1f, L=%.1f): %s", 
                          window_width, window_level, e)
        
        # Log intensity range for debugging, but don't use it for window/level
        if not np.isfinite(min_intensity) or not np.isfinite(max_intensity):
            volume_min = float(np.min(volume_view))
            volume_max = float(np.max(volume_view))
            if np.isfinite(volume_min):
                min_intensity = volume_min
            if np.isfinite(volume_max):
                max_intensity = volume_max
        
        if max_intensity <= min_intensity:
            range_padding = max(abs(max_intensity), abs(min_intensity), 1.0)
            min_intensity = -range_padding
            max_intensity = range_padding
        
        # Compute auto-range for metadata only (not used for window/level)
        auto_center = float((min_intensity + max_intensity) / 2.0)
        auto_width = float(max(max_intensity - min_intensity, 1.0))
        
        logger.info(
            "Using soft-tissue airway preset: Window=%s, Level=%s (intensity range: %.1f to %.1f)",
            window_width, window_level, min_intensity, max_intensity
        )

        logger.info(
            "Volume shape %s | spacing (row=%.3f, col=%.3f, slice=%.3f)",
            volume_view.shape,
            row_spacing,
            col_spacing,
            slice_spacing,
        )

        origin = getattr(dataset_only[0], "ImagePositionPatient", [0.0, 0.0, 0.0])
        origin = [float(origin[0]), float(origin[1]), float(origin[2])] if len(origin) >= 3 else [0.0, 0.0, 0.0]
        
        # Get orientation - prefer IOP from DICOM, fall back to PCA inference from IPP
        orientation_method = "dicom_iop"
        has_valid_iop = (
            hasattr(dataset_only[0], "ImageOrientationPatient") and 
            len(getattr(dataset_only[0], "ImageOrientationPatient", [])) >= 6
        )
        
        if has_valid_iop:
            orientation_values = dataset_only[0].ImageOrientationPatient
            row_dir = [float(orientation_values[0]), float(orientation_values[1]), float(orientation_values[2])]
            col_dir = [float(orientation_values[3]), float(orientation_values[4]), float(orientation_values[5])]
            
            # Validate: check if row and col are approximately unit vectors and orthogonal
            row_mag = np.linalg.norm(row_dir)
            col_mag = np.linalg.norm(col_dir)
            dot_product = abs(np.dot(row_dir, col_dir))
            
            if row_mag < 0.9 or row_mag > 1.1 or col_mag < 0.9 or col_mag > 1.1 or dot_product > 0.1:
                logger.warning(
                    "IOP validation failed (row_mag=%.3f, col_mag=%.3f, dot=%.3f), inferring from IPP",
                    row_mag, col_mag, dot_product
                )
                has_valid_iop = False
        
        if not has_valid_iop:
            # Infer orientation from IPP using PCA
            row_dir, col_dir, normal_dir, orientation_method = _infer_orientation_from_ipp(datasets_sorted)
            logger.info("Using inferred orientation (method=%s) due to missing/invalid IOP", orientation_method)
        else:
            normal_dir = list(np.cross(row_dir, col_dir))
            # Normalize the normal
            normal_mag = np.linalg.norm(normal_dir)
            if normal_mag > 0:
                normal_dir = [n / normal_mag for n in normal_dir]
        
        # --- Enforce that normal_dir matches the actual slice stacking direction ---
        # This prevents orientation mismatch when slice sorting and direction matrix disagree
        def _unit(v):
            v = np.array(v, dtype=np.float64)
            n = np.linalg.norm(v)
            return v / (n + 1e-12)
        
        # Use first/last IPP from the *sorted* list to infer actual stacking direction
        ipp0 = np.array([float(x) for x in datasets_sorted[0][0].ImagePositionPatient[:3]])
        ippN = np.array([float(x) for x in datasets_sorted[-1][0].ImagePositionPatient[:3]])
        stack_dir = _unit(ippN - ipp0)
        
        normal_np = _unit(normal_dir)
        dot_product = float(np.dot(stack_dir, normal_np))
        logger.info("Slice stacking consistency check: dot(stack_dir, normal_dir)=%.4f", dot_product)
        
        # If they disagree (dot product negative), flip the normal so direction_matrix matches stacking
        if dot_product < 0:
            normal_dir = (-normal_np).tolist()
            logger.info("Flipped normal_dir to match IPP stacking direction")
        else:
            normal_dir = normal_np.tolist()
        
        direction_matrix = tuple(row_dir + col_dir + normal_dir)
        
        # Analyze anatomical axis alignment for diagnostics
        anatomical_axis_info = _determine_anatomical_axes(direction_matrix)
        anatomical_axis_info['orientation_method'] = orientation_method
        logger.info("Anatomical axis mapping: %s (method=%s)", anatomical_axis_info, orientation_method)

        volume_shape = {
            "x": int(volume_view.shape[2]),
            "y": int(volume_view.shape[1]),
            "z": int(volume_view.shape[0])
        }
        voxel_spacing = {
            "x": float(col_spacing),
            "y": float(row_spacing),
            "z": float(slice_spacing)
        }

        # Prepare stacks
        out_axial = os.path.join(tmp_dir, 'axial')
        out_coronal = os.path.join(tmp_dir, 'coronal')
        out_sagittal = os.path.join(tmp_dir, 'sagittal')

        # Prepare spacing and origin for SimpleITK reslicing
        # Note: SimpleITK uses (x, y, z) order for spacing
        sitk_spacing = (float(col_spacing), float(row_spacing), float(slice_spacing))
        sitk_origin = tuple(origin)
        
        # Detect vendor for orientation hints
        vendor_name, vendor_hints = _detect_vendor(datasets_sorted)
        if vendor_name:
            logger.info("Detected vendor: %s (confidence: %.0f%%)", 
                       vendor_name, vendor_hints.get('confidence', 0) * 100)
        else:
            logger.info("Unknown vendor - using direction matrix for orientation")
            vendor_hints = None

        # Save slices using SimpleITK reslicing for proper orientation
        update_progress(45, "Generating axial slices...")
        axial_count = _save_plane_slices(
            volume_view, 'axial', window_level, window_width,
            sitk_spacing, sitk_origin, direction_matrix, out_axial, vendor_hints
        )
        update_progress(60, f"Generated {axial_count} axial. Generating coronal...")
        coronal_count = _save_plane_slices(
            volume_view, 'coronal', window_level, window_width,
            sitk_spacing, sitk_origin, direction_matrix, out_coronal, vendor_hints
        )
        update_progress(75, f"Generated {coronal_count} coronal. Generating sagittal...")
        sagittal_count = _save_plane_slices(
            volume_view, 'sagittal', window_level, window_width,
            sitk_spacing, sitk_origin, direction_matrix, out_sagittal, vendor_hints
        )
        update_progress(85, f"Generated {sagittal_count} sagittal. Uploading to S3...")

        planes_metadata = {
            "axial": {
                "axis": "z",
                "row_axis": "y",
                "column_axis": "x",
                "count": axial_count,
                "pixel_spacing_mm": {"row": float(row_spacing), "column": float(col_spacing)},
                "voxel_spacing_mm": float(slice_spacing),
                "flip_vertical": False,
                "flip_horizontal": False
            },
            "coronal": {
                "axis": "y",
                "row_axis": "z",  # Row increases along superior-inferior (Z axis)
                "column_axis": "x",  # Column increases along left-right (X axis)
                "count": coronal_count,
                "pixel_spacing_mm": {"row": float(slice_spacing), "column": float(col_spacing)},
                "voxel_spacing_mm": float(row_spacing),
                "flip_vertical": False,  # No flip needed - rotation handles orientation
                "flip_horizontal": False
            },
            "sagittal": {
                "axis": "x",
                "row_axis": "z",  # Row increases along superior-inferior (Z axis)
                "column_axis": "y",  # Column increases along anterior-posterior (Y axis)
                "count": sagittal_count,
                "pixel_spacing_mm": {"row": float(slice_spacing), "column": float(row_spacing)},
                "voxel_spacing_mm": float(col_spacing),
                "flip_vertical": False,  # No flip needed - rotation handles orientation
                "flip_horizontal": False
            }
        }

        intensity_stats = {
            "min": float(min_intensity),
            "max": float(max_intensity)
        }
        
        # Build window presets dictionary
        window_presets = {
            "soft_tissue": {
                "label": WINDOW_PRESETS["soft_tissue"]["label"],
                "center": float(WINDOW_PRESETS["soft_tissue"]["level"]),
                "width": float(WINDOW_PRESETS["soft_tissue"]["window"]),
                "source": "soft_tissue_airway_preset"
            },
            "bone": {
                "label": WINDOW_PRESETS["bone"]["label"],
                "center": float(WINDOW_PRESETS["bone"]["level"]),
                "width": float(WINDOW_PRESETS["bone"]["window"]),
                "source": "bone_preset"
            },
            "airway": {
                "label": WINDOW_PRESETS["airway"]["label"],
                "center": float(WINDOW_PRESETS["airway"]["level"]),
                "width": float(WINDOW_PRESETS["airway"]["window"]),
                "source": "airway_qa_preset"
            },
            "auto": {
                "label": "Auto (Full Range)",
                "center": float(auto_center),
                "width": float(auto_width),
                "source": "auto_range"
            }
        }
        if np.isfinite(dicom_center) and np.isfinite(dicom_width):
            window_presets["dicom"] = {
                "label": "DICOM Suggested",
                "center": float(dicom_center),
                "width": float(dicom_width),
                "source": "dicom"
            }

        manifest = {
            "version": 3,  # Bumped for geometry diagnostics
            "patient_id": patient_id,
            "folder": folder_name,
            "window": {"center": window_level, "width": window_width, "source": "percentile_adaptive"},
            "spacing_mm": {
                "axial": {"row": row_spacing, "col": col_spacing},
                "coronal": {"row": slice_spacing, "col": col_spacing},
                "sagittal": {"row": slice_spacing, "col": row_spacing}
            },
            "counts": {
                "axial": axial_count,
                "coronal": coronal_count,
                "sagittal": sagittal_count
            },
            "intensity": intensity_stats,
            "volume": {
                "shape": volume_shape,
                "spacing_mm": voxel_spacing,
                "origin_mm": {"x": origin[0], "y": origin[1], "z": origin[2]},
                "orientation": {
                    "row": row_dir,
                    "column": col_dir,
                    "normal": normal_dir
                }
            },
            "planes": planes_metadata,
            "window_presets": window_presets,
            "geometry": {
                "status": geometry_diagnostics.get("geometry_status", "UNKNOWN"),
                "sort_method": geometry_diagnostics.get("sort_method", "unknown"),
                "issues": geometry_diagnostics.get("issues", []),
                "normal_drift_deg": geometry_diagnostics.get("normal_drift_deg", 0.0),
                "spacing_variance_pct": geometry_diagnostics.get("spacing_variance_pct", 0.0),
            }
        }

        logger.info("Uploading MPR stacks to %s", dest_prefix)
        upload_items = [
            (out_axial, 'axial', axial_count),
            (out_coronal, 'coronal', coronal_count),
            (out_sagittal, 'sagittal', sagittal_count),
        ]

        for folder_path, plane, count in upload_items:
            for file_name in os.listdir(folder_path):
                local_file = os.path.join(folder_path, file_name)
                key = f"{dest_prefix}{plane}/{file_name}"
                s3_client.upload_file(local_file, bucket, key, ExtraArgs={'ContentType': 'image/png'})

        manifest_key = f"{dest_prefix}manifest.json"
        s3_client.put_object(
            Bucket=bucket,
            Key=manifest_key,
            Body=json.dumps(manifest, indent=2).encode('utf-8'),
            ContentType='application/json'
        )
        
        # Upload detailed diagnostics for debugging
        diagnostics_output = {
            "geometry": geometry_diagnostics,
            "anatomical_axes": {
                "volume_to_anatomical": {str(k): v for k, v in anatomical_axis_info['volume_to_anatomical'].items()},
                "axis_signs": {str(k): v for k, v in anatomical_axis_info['axis_signs'].items()},
                "direction_matrix": anatomical_axis_info['direction_matrix'],
                "orientation_method": anatomical_axis_info.get('orientation_method', orientation_method),
                "row_dir": row_dir,
                "col_dir": col_dir,
                "slice_normal": normal_dir,
            },
            "vendor": {
                "detected": vendor_name,
                "confidence": vendor_hints.get('confidence', 0) if vendor_hints else 0,
                "hints_applied": vendor_hints is not None,
                "notes": vendor_hints.get('notes', '') if vendor_hints else "Unknown vendor - using direction matrix",
            },
            "processing": {
                "downsampled": max_dimension > 0,
                "max_dimension": max_dimension,
                "normalization_applied": normalization_applied,
                "window_level_method": "percentile_adaptive",
                "window_width": window_width,
                "window_level": window_level,
            },
            "timing": {
                "download_seconds": download_duration,
            }
        }
        diagnostics_key = f"{dest_prefix}diagnostics.json"
        s3_client.put_object(
            Bucket=bucket,
            Key=diagnostics_key,
            Body=json.dumps(diagnostics_output, indent=2).encode('utf-8'),
            ContentType='application/json'
        )
        logger.info("Uploaded diagnostics to %s", diagnostics_key)

        del volume_view

    total_duration = time.monotonic() - start_time
    logger.info(
        "Completed CBCT MPR generation for patient %s folder %s in %.2f seconds",
        patient_id,
        folder_name,
        total_duration
    )
    return True, f"MPR data uploaded to s3://{bucket}/{dest_prefix}"


