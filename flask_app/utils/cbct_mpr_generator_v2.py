import os
import json
import tempfile
import time
from typing import List, Tuple
from collections import Counter

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


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------

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


def _is_likely_dicom(filename: str) -> bool:
    """Check if a filename is likely a DICOM file.
    
    Detects:
    - Files with .dcm, .dicom extensions
    - Files with no extension (common in CBCT exports)
    - Purely numeric filenames (000001, 000002, etc.)
    """
    filename_lower = filename.lower()
    
    # Has DICOM extension
    if filename_lower.endswith((".dcm", ".dicom", ".dcom")):
        return True
    
    # No extension at all (very common in CBCT)
    if "." not in filename:
        return True
    
    # Purely numeric (also common)
    base_name = filename.split(".")[0]
    if base_name.isdigit():
        return True
    
    return False


def _download_dicom_series(
    s3_client,
    bucket: str,
    objects: List[dict],
    tmp_dir: str,
) -> List[Tuple[pydicom.dataset.Dataset, str]]:
    """Download a DICOM series to a temp dir and read headers (no pixels)."""
    datasets = []
    for obj in objects:
        key = obj["Key"]
        filename = key.split("/")[-1]
        
        # Skip directories
        if key.endswith("/") or not filename:
            continue
            
        # Check if likely DICOM
        if not _is_likely_dicom(filename):
            continue
            
        relative = filename
        local_path = os.path.join(tmp_dir, relative)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        s3_client.download_file(bucket, key, local_path)
        try:
            ds = pydicom.dcmread(local_path, stop_before_pixels=True)
            datasets.append((ds, local_path))
        except Exception as exc:
            logger.error("Failed to read DICOM %s: %s", key, exc)
    return datasets


def _sort_datasets(
    datasets: List[Tuple[pydicom.dataset.Dataset, str]]
) -> List[Tuple[pydicom.dataset.Dataset, str]]:
    """Sort slices along the acquisition direction."""

    def sort_key(item):
        ds = item[0]
        if hasattr(ds, "InstanceNumber"):
            return ds.InstanceNumber
        if hasattr(ds, "ImagePositionPatient"):
            return ds.ImagePositionPatient[2]
        return 0

    datasets.sort(key=sort_key)
    return datasets


# ---------------------------------------------------------------------------
# Geometry / spacing
# ---------------------------------------------------------------------------

def _compute_spacing(
    datasets: List[pydicom.dataset.Dataset]
) -> Tuple[float, float, float]:
    """Compute row/col/slice spacing in mm."""
    first = datasets[0]
    pixel_spacing = getattr(first, "PixelSpacing", [1.0, 1.0])
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


def _estimate_total_slices(datasets: List[pydicom.dataset.Dataset]) -> int:
    """Count total slices, handling multi-frame objects."""
    total = 0
    for ds in datasets:
        frames = getattr(ds, "NumberOfFrames", None)
        try:
            frames_int = int(frames)
        except (TypeError, ValueError):
            frames_int = 0
        total += frames_int if frames_int > 0 else 1
    return total


# ---------------------------------------------------------------------------
# Intensity handling / window-level
# ---------------------------------------------------------------------------

WINDOW_PRESETS = {
    # CBCT-specific presets (dental/maxillofacial)
    # Updated to match Weasis defaults for better visualization
    "cbct_default": {"window": 5000.0, "level": 1500.0, "label": "CBCT Default"},
    "bone": {"window": 4000.0, "level": 1000.0, "label": "Bone"},
    "soft_tissue": {"window": 400.0, "level": 40.0, "label": "Soft Tissue"},
    "airway": {"window": 1600.0, "level": -400.0, "label": "Airway"},
}


def apply_window_level(img: np.ndarray, window: float, level: float) -> np.ndarray:
    """Apply window/level to a float32 image and map to 8-bit."""
    if window <= 0:
        window = 1500.0
    low = level - window / 2.0
    high = level + window / 2.0
    img_clipped = np.clip(img, low, high)
    img_norm = (img_clipped - low) / (high - low + 1e-6)
    return (img_norm * 255.0).astype(np.uint8)


def _determine_window_from_dicom(
    ds: pydicom.dataset.Dataset
) -> Tuple[float, float]:
    """Try to read W/L from DICOM; fall back to soft-tissue preset."""

    def _as_float(value, default):
        if value is None:
            return default
        if isinstance(value, (list, tuple)):
            value = value[0]
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    default_level = WINDOW_PRESETS["soft_tissue"]["level"]
    default_window = WINDOW_PRESETS["soft_tissue"]["window"]
    center = _as_float(getattr(ds, "WindowCenter", None), default_level)
    width = _as_float(getattr(ds, "WindowWidth", None), default_window)
    if width <= 0:
        width = default_window
    return center, width


# ---------------------------------------------------------------------------
# Pixel decoding
# ---------------------------------------------------------------------------

def _decode_pixels(ds: pydicom.dataset.Dataset, path: str) -> np.ndarray:
    """Load DICOM pixel data as float32, preserving full bit depth."""
    try:
        pixels = ds.pixel_array
        pixels_float = pixels.astype(np.float32)
        bits_stored = getattr(ds, "BitsStored", None)
        bits_allocated = getattr(ds, "BitsAllocated", None)
        if bits_stored:
            logger.debug(
                "DICOM %s: BitsStored=%s BitsAllocated=%s",
                path,
                bits_stored,
                bits_allocated,
            )
        pixel_min = float(np.min(pixels_float))
        pixel_max = float(np.max(pixels_float))
        logger.debug(
            "DICOM %s raw pixel range: %.1f → %.1f", path, pixel_min, pixel_max
        )
        return pixels_float
    except Exception as exc:
        logger.warning("pydicom failed to decode %s: %s", path, exc)
        if not _HAS_SIMPLEITK:
            raise RuntimeError(
                "Missing compression handlers; install 'gdcm' or 'pylibjpeg' "
                "or enable SimpleITK."
            ) from exc
        try:
            image = sitk.ReadImage(path)
            array = sitk.GetArrayFromImage(image).astype(np.float32)
            if array.ndim == 3 and array.shape[0] == 1:
                array = array[0]
            elif array.ndim == 3 and array.shape[0] > 1:
                logger.warning(
                    "DICOM %s has %d frames; using first frame for volume.",
                    path,
                    array.shape[0],
                )
                array = array[0]
            return array
        except Exception as sitk_exc:
            logger.error("SimpleITK fallback failed for %s: %s", path, sitk_exc)
            raise RuntimeError(
                "Unable to decode compressed pixel data; install 'gdcm' or 'pylibjpeg'."
            ) from sitk_exc


# ---------------------------------------------------------------------------
# Volume resampling (optional isotropic)
# ---------------------------------------------------------------------------

def _resample_volume_isotropic(
    volume: np.ndarray,
    row_spacing: float,
    col_spacing: float,
    slice_spacing: float,
    max_bytes: float,
) -> Tuple[np.ndarray, float, float, float, bool]:
    """Resample a volume to isotropic spacing using SimpleITK (if available)."""
    spacings = [float(slice_spacing), float(row_spacing), float(col_spacing)]
    if not all(np.isfinite(s) and s > 0 for s in spacings):
        return volume, row_spacing, col_spacing, slice_spacing, False
    target_spacing = float(min(spacings))
    if max(spacings) - target_spacing < 1e-3:
        return volume, row_spacing, col_spacing, slice_spacing, False
    if not _HAS_SIMPLEITK:
        logger.warning(
            "SimpleITK not available; skipping isotropic resampling "
            "(sagittal/coronal may look slightly stretched)."
        )
        return volume, row_spacing, col_spacing, slice_spacing, False
    try:
        sitk_image = sitk.GetImageFromArray(np.asarray(volume, dtype=np.float32))
        sitk_image.SetSpacing(
            (float(col_spacing), float(row_spacing), float(slice_spacing))
        )
        original_spacing = np.array(sitk_image.GetSpacing(), dtype=np.float64)
        original_size = np.array(sitk_image.GetSize(), dtype=np.int32)
        new_spacing = np.array(
            [target_spacing, target_spacing, target_spacing], dtype=np.float64
        )
        new_size = np.maximum(
            np.round(original_size * (original_spacing / new_spacing)).astype(np.int32),
            1,
        )
        prospective_bytes = int(np.prod(new_size) * 4)
        if prospective_bytes > max_bytes:
            human_size = prospective_bytes / (1024 ** 3)
            human_limit = max_bytes / (1024 ** 3)
            logger.warning(
                "Skipping isotropic resample (%.2f GiB) – exceeds %.2f GiB limit.",
                human_size,
                human_limit,
            )
            return volume, row_spacing, col_spacing, slice_spacing, False
        resampler = sitk.ResampleImageFilter()
        resampler.SetInterpolator(sitk.sitkLinear)
        resampler.SetOutputSpacing(tuple(new_spacing.tolist()))
        resampler.SetSize([int(x) for x in new_size.tolist()])
        resampler.SetOutputOrigin(sitk_image.GetOrigin())
        resampler.SetOutputDirection(sitk_image.GetDirection())
        resampled = resampler.Execute(sitk_image)
        volume_iso = sitk.GetArrayFromImage(resampled).astype(np.float32)
        return volume_iso, target_spacing, target_spacing, target_spacing, True
    except Exception as exc:
        logger.error("Isotropic resampling failed: %s", exc)
        return volume, row_spacing, col_spacing, slice_spacing, False


# ---------------------------------------------------------------------------
# 3D → MPR slice extraction
# ---------------------------------------------------------------------------

def _save_plane_slices(
    volume: np.ndarray,
    plane: str,
    level: float,
    window: float,
    spacing_xyz: Tuple[float, float, float],
    origin_xyz: Tuple[float, float, float],
    direction: Tuple[float, ...],
    output_dir: str,
) -> int:
    """Save axial / coronal / sagittal PNG stacks from a 3D volume."""
    os.makedirs(output_dir, exist_ok=True)
    Z, Y, X = volume.shape

    def save_slice(slice_img: np.ndarray, idx: int):
        arr = apply_window_level(slice_img.astype(np.float32), window, level)
        Image.fromarray(arr, mode="L").save(
            os.path.join(output_dir, f"{plane}_{idx:03d}.png")
        )

    if plane == "axial":
        for z in range(Z):
            save_slice(volume[z, :, :], z)
        return Z
    if plane == "coronal":
        for y in range(Y):
            img = np.flipud(volume[:, y, :])
            save_slice(img, y)
        return Y
    if plane == "sagittal":
        for x in range(X):
            img = np.flipud(volume[:, :, x])
            save_slice(img, x)
        return X
    raise ValueError(f"Unknown plane '{plane}'")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_cbct_mpr(
    patient_id: int,
    folder_name: str,
    overwrite: bool = True,
) -> Tuple[bool, str]:
    """Generate axial/coronal/sagittal PNG stacks for a CBCT series."""
    bucket = current_app.config.get("S3_BUCKET") or current_app.config.get(
        "S3_BUCKET_NAME"
    )
    if not bucket:
        return False, "S3 bucket is not configured"
    region = (
        current_app.config.get("AWS_REGION")
        or os.getenv("AWS_REGION")
        or current_app.config.get("BEDROCK_AWS_REGION")
        or "us-east-1"
    )
    s3_client = boto3.client("s3", region_name=region)
    source_prefix = f"patients/{patient_id}/imaging/cbct/{folder_name}/"
    dest_prefix = f"patients/{patient_id}/imaging/cbct_mpr/{folder_name}/"
    start_time = time.monotonic()
    logger.info("Listing CBCT DICOM objects under %s", source_prefix)
    objects = _list_s3_objects(s3_client, bucket, source_prefix)
    dicom_objects = [
        obj
        for obj in objects
        if obj["Key"].lower().endswith((".dcm", ".dicom", ".dcom"))
    ]
    if not dicom_objects:
        return False, "No DICOM files found under prefix"

    max_source_bytes = current_app.config.get(
        "CBCT_MPR_MAX_SOURCE_BYTES", 2_500_000_000  # 2.5 GB - safe for servers with 8GB+ RAM
    )
    total_source_bytes = sum(obj.get("Size", 0) for obj in dicom_objects)
    if total_source_bytes > max_source_bytes:
        human_total = total_source_bytes / (1024 ** 3)
        human_limit = max_source_bytes / (1024 ** 3)
        msg = (
            f"CBCT folder too large for in-app MPR "
            f"({human_total:.2f} GiB > {human_limit:.2f} GiB)."
        )
        logger.warning(
            "Rejecting MPR generation for patient %s / %s: %s",
            patient_id,
            folder_name,
            msg,
        )
        return False, msg

    max_dicom_files = current_app.config.get("CBCT_MPR_MAX_DICOM_FILES", 1200)
    if len(dicom_objects) > max_dicom_files:
        msg = (
            f"CBCT folder contains {len(dicom_objects)} DICOM files, "
            f"exceeding limit {max_dicom_files}."
        )
        logger.warning(
            "Rejecting MPR generation for patient %s / %s: %s",
            patient_id,
            folder_name,
            msg,
        )
        return False, msg

    if overwrite:
        logger.info("Removing existing MPR objects under %s", dest_prefix)
        existing = _list_s3_objects(s3_client, bucket, dest_prefix)
        if existing:
            delete_items = [{"Key": obj["Key"]} for obj in existing]
            for i in range(0, len(delete_items), 1000):
                chunk = delete_items[i : i + 1000]
                s3_client.delete_objects(Bucket=bucket, Delete={"Objects": chunk})

    with tempfile.TemporaryDirectory() as tmp_dir:
        logger.info(
            "Downloading %d DICOM files for patient %s / %s into %s",
            len(dicom_objects),
            patient_id,
            folder_name,
            tmp_dir,
        )
        t0 = time.monotonic()
        datasets_with_paths = _download_dicom_series(
            s3_client, bucket, dicom_objects, tmp_dir
        )
        logger.info(
            "Downloaded %d DICOM files in %.2f s",
            len(datasets_with_paths),
            time.monotonic() - t0,
        )
        if not datasets_with_paths:
            return False, "Failed to download DICOM series"

        datasets_sorted = _sort_datasets(datasets_with_paths)
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

        dataset_only = [ds for (ds, _) in datasets_sorted]
        if not dataset_only:
            return False, "No valid DICOM datasets found"

        rows = int(getattr(dataset_only[0], "Rows", 0) or 0)
        cols = int(getattr(dataset_only[0], "Columns", 0) or 0)
        if rows <= 0 or cols <= 0:
            logger.error(
                "Unable to determine slice dimensions (Rows=%s, Cols=%s)",
                rows,
                cols,
            )
            return False, "Unable to determine slice dimensions"

        total_slices_expected = _estimate_total_slices(dataset_only)
        if total_slices_expected <= 0:
            return False, "Unable to determine slice count"

        max_slices = current_app.config.get("CBCT_MPR_MAX_TOTAL_SLICES", 2000)
        if total_slices_expected > max_slices:
            msg = (
                f"CBCT folder has {total_slices_expected} slices, "
                f"exceeding limit {max_slices}."
            )
            logger.warning(
                "Rejecting MPR generation for patient %s / %s: %s",
                patient_id,
                folder_name,
                msg,
            )
            return False, msg

        row_spacing, col_spacing, slice_spacing = _compute_spacing(dataset_only)
        max_working_bytes = current_app.config.get(
            "CBCT_MPR_MAX_WORKING_BYTES", 2_000_000_000  # 2 GB working memory
        )
        estimated_working_bytes = total_slices_expected * rows * cols * 4
        if estimated_working_bytes > max_working_bytes:
            human_estimated = estimated_working_bytes / (1024 ** 3)
            human_limit = max_working_bytes / (1024 ** 3)
            msg = (
                f"Estimated working set {human_estimated:.2f} GiB exceeds "
                f"limit {human_limit:.2f} GiB."
            )
            logger.warning(
                "Rejecting MPR generation for patient %s / %s: %s",
                patient_id,
                folder_name,
                msg,
            )
            return False, msg

        volume_path = os.path.join(tmp_dir, "volume_float32.dat")
        volume_memmap = np.memmap(
            volume_path,
            dtype=np.float32,
            mode="w+",
            shape=(total_slices_expected, rows, cols),
        )

        dicom_center, dicom_width = _determine_window_from_dicom(dataset_only[0])
        slice_index = 0
        skipped_dim_mismatch = 0
        skip_dim_mismatch = bool(
            current_app.config.get("CBCT_MPR_SKIP_DIM_MISMATCH_SLICES", False)
        )
        min_intensity = float("inf")
        max_intensity = float("-inf")

        for ds_meta, local_path in datasets_sorted:
            if slice_index >= total_slices_expected:
                logger.warning(
                    "Reached expected slice cap (%d) – extra slices ignored.",
                    total_slices_expected,
                )
                break
            try:
                ds_full = pydicom.dcmread(local_path)
            except Exception as exc:
                logger.error("Failed to re-read DICOM %s: %s", local_path, exc)
                del volume_memmap
                return False, f"Failed to read DICOM slice: {exc}"

            pixels = _decode_pixels(ds_full, local_path)
            slope = float(getattr(ds_full, "RescaleSlope", 1.0))
            intercept = float(getattr(ds_full, "RescaleIntercept", 0.0))

            if pixels.ndim == 2:
                if pixels.shape != (rows, cols):
                    if skip_dim_mismatch:
                        skipped_dim_mismatch += 1
                        logger.warning(
                            "Slice dimension mismatch for %s; expected %dx%d got %s – skipping slice",
                            local_path,
                            rows,
                            cols,
                            pixels.shape,
                        )
                        continue
                    else:
                        logger.error(
                            "Slice dimension mismatch for %s; expected %dx%d got %s",
                            local_path,
                            rows,
                            cols,
                            pixels.shape,
                        )
                        del volume_memmap
                        return False, "Slice dimensions vary in CBCT series"
                slice_data = pixels.astype(np.float32) * slope + intercept
                smin = float(np.min(slice_data))
                smax = float(np.max(slice_data))
                if np.isfinite(smin):
                    min_intensity = min(min_intensity, smin)
                if np.isfinite(smax):
                    max_intensity = max(max_intensity, smax)
                volume_memmap[slice_index, :, :] = slice_data
                slice_index += 1
            elif pixels.ndim == 3:
                start_index = slice_index
                frame_count = pixels.shape[0]
                if frame_count > 1:
                    logger.info(
                        "Expanding multi-frame DICOM %s into %d frames",
                        local_path,
                        frame_count,
                    )
                for f in range(frame_count):
                    if slice_index >= total_slices_expected:
                        break
                    frame = pixels[f, ...]
                    if frame.shape != (rows, cols):
                        if skip_dim_mismatch:
                            skipped_dim_mismatch += 1
                            logger.warning(
                                "Frame dimension mismatch for %s frame %d; expected %dx%d got %s – skipping file",
                                local_path,
                                f,
                                rows,
                                cols,
                                frame.shape,
                            )
                            # Roll back any frames already written for this file.
                            slice_index = start_index
                            break
                        else:
                            logger.error(
                                "Frame dimension mismatch for %s frame %d; expected %dx%d got %s",
                                local_path,
                                f,
                                rows,
                                cols,
                                frame.shape,
                            )
                            del volume_memmap
                            return False, "Frame dimensions vary in CBCT series"
                    slice_data = frame.astype(np.float32) * slope + intercept
                    smin = float(np.min(slice_data))
                    smax = float(np.max(slice_data))
                    if np.isfinite(smin):
                        min_intensity = min(min_intensity, smin)
                    if np.isfinite(smax):
                        max_intensity = max(max_intensity, smax)
                    volume_memmap[slice_index, :, :] = slice_data
                    slice_index += 1
                # If we rolled back due to mismatch, skip to next file.
                if slice_index == start_index and frame_count > 0:
                    continue
            else:
                del volume_memmap
                raise RuntimeError(
                    f"Unsupported pixel array shape {pixels.shape} "
                    f"for %s; expected 2D or 3D." % local_path
                )

        actual_slices = slice_index
        if actual_slices == 0:
            del volume_memmap
            return False, "No slices processed in CBCT series"

        if skipped_dim_mismatch:
            logger.info(
                "Skipped %d DICOM slice(s)/file(s) due to dimension mismatch for patient %s folder %s",
                skipped_dim_mismatch,
                patient_id,
                folder_name,
            )
        if actual_slices < total_slices_expected:
            logger.info(
                "Processed %d slices (expected %d)",
                actual_slices,
                total_slices_expected,
            )

        volume_memmap.flush()
        volume_view = np.asarray(volume_memmap[:actual_slices, :, :], dtype=np.float32)
        del volume_memmap

        logger.info(
            "Initial volume shape (Z,Y,X) = %s, intensity range %.1f → %.1f",
            volume_view.shape,
            min_intensity,
            max_intensity,
        )

        volume_view, row_spacing, col_spacing, slice_spacing, did_resample = (
            _resample_volume_isotropic(
                volume_view,
                row_spacing,
                col_spacing,
                slice_spacing,
                max_working_bytes,
            )
        )
        if did_resample:
            logger.info(
                "Resampled to isotropic spacing %.3f mm; new shape %s",
                slice_spacing,
                volume_view.shape,
            )

        raw_min = float(np.min(volume_view))
        raw_max = float(np.max(volume_view))
        logger.info("Final volume raw range: %.1f → %.1f", raw_min, raw_max)

        # Determine optimal window/level for this specific CBCT dataset
        # Strategy: Use DICOM-specified values if valid, otherwise auto-calculate
        
        # Check if DICOM has valid window/level
        use_dicom_wl = (
            np.isfinite(dicom_center) and 
            np.isfinite(dicom_width) and 
            dicom_width > 100  # Sanity check - width should be meaningful
        )
        
        if use_dicom_wl:
            # Use DICOM-specified window/level
            window_level = float(dicom_center)
            window_width = float(dicom_width)
            logger.info(
                "Using DICOM-specified W=%.1f L=%.1f for PNG export.",
                window_width,
                window_level,
            )
        else:
            # Auto-calculate based on actual data distribution
            # Use percentile-based windowing for robustness
            flat_data = volume_view.flatten()
            
            # Remove background (typically very low values)
            threshold = np.percentile(flat_data, 10)
            foreground = flat_data[flat_data > threshold]
            
            if len(foreground) > 1000:
                # Calculate robust min/max using percentiles
                p1 = float(np.percentile(foreground, 1))
                p99 = float(np.percentile(foreground, 99))
                
                # Window = range, Level = center
                window_width = max(p99 - p1, 500)  # Minimum width of 500
                window_level = (p1 + p99) / 2.0
                
                logger.info(
                    "Auto-calculated W=%.1f L=%.1f from data (p1=%.1f, p99=%.1f)",
                    window_width,
                    window_level,
                    p1,
                    p99,
                )
            else:
                # Fallback to preset
                window_level = float(WINDOW_PRESETS["cbct_default"]["level"])
                window_width = float(WINDOW_PRESETS["cbct_default"]["window"])
                logger.info(
                    "Using CBCT default preset W=%.1f L=%.1f (fallback).",
                    window_width,
                    window_level,
                )

        origin = getattr(dataset_only[0], "ImagePositionPatient", [0.0, 0.0, 0.0])
        origin = (
            [float(origin[0]), float(origin[1]), float(origin[2])]
            if len(origin) >= 3
            else [0.0, 0.0, 0.0]
        )
        orientation_values = getattr(
            dataset_only[0],
            "ImageOrientationPatient",
            [1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        )
        if len(orientation_values) < 6:
            orientation_values = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        row_dir = [
            float(orientation_values[0]),
            float(orientation_values[1]),
            float(orientation_values[2]),
        ]
        col_dir = [
            float(orientation_values[3]),
            float(orientation_values[4]),
            float(orientation_values[5]),
        ]
        normal_dir = list(np.cross(row_dir, col_dir))

        volume_shape = {
            "x": int(volume_view.shape[2]),
            "y": int(volume_view.shape[1]),
            "z": int(volume_view.shape[0]),
        }
        voxel_spacing = {
            "x": float(col_spacing),
            "y": float(row_spacing),
            "z": float(slice_spacing),
        }

        out_axial = os.path.join(tmp_dir, "axial")
        out_coronal = os.path.join(tmp_dir, "coronal")
        out_sagittal = os.path.join(tmp_dir, "sagittal")
        spacing_xyz = (float(col_spacing), float(row_spacing), float(slice_spacing))
        origin_xyz = (float(origin[0]), float(origin[1]), float(origin[2]))
        direction_tuple = tuple(orientation_values)

        axial_count = _save_plane_slices(
            volume_view,
            "axial",
            window_level,
            window_width,
            spacing_xyz,
            origin_xyz,
            direction_tuple,
            out_axial,
        )
        coronal_count = _save_plane_slices(
            volume_view,
            "coronal",
            window_level,
            window_width,
            spacing_xyz,
            origin_xyz,
            direction_tuple,
            out_coronal,
        )
        sagittal_count = _save_plane_slices(
            volume_view,
            "sagittal",
            window_level,
            window_width,
            spacing_xyz,
            origin_xyz,
            direction_tuple,
            out_sagittal,
        )

        planes_metadata = {
            "axial": {
                "axis": "z",
                "row_axis": "y",
                "column_axis": "x",
                "count": axial_count,
                "pixel_spacing_mm": {
                    "row": float(row_spacing),
                    "column": float(col_spacing),
                },
                "voxel_spacing_mm": float(slice_spacing),
                "flip_vertical": False,
                "flip_horizontal": False,
            },
            "coronal": {
                "axis": "y",
                "row_axis": "z",
                "column_axis": "x",
                "count": coronal_count,
                "pixel_spacing_mm": {
                    "row": float(slice_spacing),
                    "column": float(col_spacing),
                },
                "voxel_spacing_mm": float(row_spacing),
                "flip_vertical": True,
                "flip_horizontal": False,
            },
            "sagittal": {
                "axis": "x",
                "row_axis": "z",
                "column_axis": "y",
                "count": sagittal_count,
                "pixel_spacing_mm": {
                    "row": float(slice_spacing),
                    "column": float(row_spacing),
                },
                "voxel_spacing_mm": float(col_spacing),
                "flip_vertical": True,
                "flip_horizontal": False,
            },
        }

        intensity_stats = {"min": float(raw_min), "max": float(raw_max)}
        auto_center = float((raw_min + raw_max) / 2.0)
        auto_width = float(max(raw_max - raw_min, 1.0))
        window_presets = {
            "cbct_default": {
                "label": WINDOW_PRESETS["cbct_default"]["label"],
                "center": float(WINDOW_PRESETS["cbct_default"]["level"]),
                "width": float(WINDOW_PRESETS["cbct_default"]["window"]),
                "source": "cbct_default_preset",
            },
            "bone": {
                "label": WINDOW_PRESETS["bone"]["label"],
                "center": float(WINDOW_PRESETS["bone"]["level"]),
                "width": float(WINDOW_PRESETS["bone"]["window"]),
                "source": "bone_preset",
            },
            "soft_tissue": {
                "label": WINDOW_PRESETS["soft_tissue"]["label"],
                "center": float(WINDOW_PRESETS["soft_tissue"]["level"]),
                "width": float(WINDOW_PRESETS["soft_tissue"]["window"]),
                "source": "soft_tissue_preset",
            },
            "airway": {
                "label": WINDOW_PRESETS["airway"]["label"],
                "center": float(WINDOW_PRESETS["airway"]["level"]),
                "width": float(WINDOW_PRESETS["airway"]["window"]),
                "source": "airway_preset",
            },
            "auto": {
                "label": "Auto (Full Range)",
                "center": auto_center,
                "width": auto_width,
                "source": "auto_range",
            },
        }
        if np.isfinite(dicom_center) and np.isfinite(dicom_width):
            window_presets["dicom"] = {
                "label": "DICOM Suggested",
                "center": float(dicom_center),
                "width": float(dicom_width),
                "source": "dicom",
            }

        manifest = {
            "version": 2,
            "patient_id": patient_id,
            "folder": folder_name,
            "window": {
                "center": window_level,
                "width": window_width,
                "source": "cbct_default_preset",
            },
            "spacing_mm": {
                "axial": {"row": row_spacing, "col": col_spacing},
                "coronal": {"row": slice_spacing, "col": col_spacing},
                "sagittal": {"row": slice_spacing, "col": row_spacing},
            },
            "counts": {
                "axial": axial_count,
                "coronal": coronal_count,
                "sagittal": sagittal_count,
            },
            "intensity": intensity_stats,
            "volume": {
                "shape": volume_shape,
                "spacing_mm": voxel_spacing,
                "origin_mm": {
                    "x": origin[0],
                    "y": origin[1],
                    "z": origin[2],
                },
                "orientation": {
                    "row": row_dir,
                    "column": col_dir,
                    "normal": normal_dir,
                },
            },
            "planes": planes_metadata,
            "window_presets": window_presets,
        }

        logger.info("Uploading MPR PNG stacks to s3://%s/%s", bucket, dest_prefix)
        for folder_path, plane, count in [
            (out_axial, "axial", axial_count),
            (out_coronal, "coronal", coronal_count),
            (out_sagittal, "sagittal", sagittal_count),
        ]:
            for file_name in os.listdir(folder_path):
                local_file = os.path.join(folder_path, file_name)
                key = f"{dest_prefix}{plane}/{file_name}"
                s3_client.upload_file(
                    local_file,
                    bucket,
                    key,
                    ExtraArgs={"ContentType": "image/png"},
                )

        manifest_key = f"{dest_prefix}manifest.json"
        s3_client.put_object(
            Bucket=bucket,
            Key=manifest_key,
            Body=json.dumps(manifest, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        del volume_view

    total_duration = time.monotonic() - start_time
    logger.info(
        "Completed CBCT MPR generation for patient %s folder %s in %.2f seconds",
        patient_id,
        folder_name,
        total_duration,
    )
    return True, f"MPR data uploaded to s3://{bucket}/{dest_prefix}"


















