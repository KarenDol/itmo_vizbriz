#!/usr/bin/env python3
"""
Standalone SimpleITK reslice tester.

Generates axial/coronal/sagittal PNG stacks directly from a DICOM series
so you can validate the 3D transforms without touching the Flask/S3 stack.
Prints shape/spacing/direction info and optionally normalizes CBCT greyscale
values into a pseudo-HU range before windowing.
"""

import argparse
import os
import sys
from typing import Optional, Tuple

import numpy as np

try:
    import SimpleITK as sitk
except ImportError:
    print("SimpleITK is required for this test script.", file=sys.stderr)
    sys.exit(1)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flask_app.utils import cbct_mpr_generator as mpr  # noqa: E402


def load_series(dicom_dir: str, max_slices: Optional[int]) -> sitk.Image:
    """Load a DICOM series from disk (optionally clamped to first N files)."""
    reader = sitk.ImageSeriesReader()
    series_ids = reader.GetGDCMSeriesIDs(dicom_dir)
    if not series_ids:
        raise RuntimeError(f"No DICOM series found in {dicom_dir}")
    file_names = reader.GetGDCMSeriesFileNames(dicom_dir, series_ids[0])
    if max_slices is not None:
        if max_slices <= 0:
            raise ValueError("--max-slices must be positive")
        file_names = file_names[:max_slices]
    reader.SetFileNames(file_names)
    return reader.Execute()


def window_and_save(image: sitk.Image, out_dir: str, window: float, level: float) -> int:
    """Apply window/level to each slice and save PNGs."""
    os.makedirs(out_dir, exist_ok=True)
    arr = sitk.GetArrayFromImage(image).astype(np.float32)
    low = level - window / 2.0
    high = level + window / 2.0
    arr = np.clip((arr - low) / (high - low + 1e-6), 0.0, 1.0)
    arr = (arr * 255).astype(np.uint8)

    for idx, sl in enumerate(arr):
        sitk.WriteImage(sitk.GetImageFromArray(sl), os.path.join(out_dir, f"{idx:03d}.png"))
    return arr.shape[0]


def resample_with_transform(image: sitk.Image, transform: sitk.Euler3DTransform, size, spacing, direction):
    """Resample the image using a provided Euler transform and output geometry."""
    resampler = sitk.ResampleImageFilter()
    resampler.SetReferenceImage(image)
    resampler.SetInterpolator(sitk.sitkLinear)
    resampler.SetDefaultPixelValue(0.0)
    resampler.SetSize(size)
    resampler.SetOutputSpacing(spacing)
    resampler.SetOutputDirection(direction)
    resampler.SetOutputOrigin(image.GetOrigin())

    center_idx = [s / 2.0 for s in image.GetSize()]
    transform.SetCenter(image.TransformContinuousIndexToPhysicalPoint(center_idx))
    resampler.SetTransform(transform)

    return resampler.Execute(image)


def make_coronal(image: sitk.Image) -> sitk.Image:
    """Reformat to coronal orientation (X horizontal, Z vertical)."""
    size = (image.GetSize()[0], image.GetSize()[2], image.GetSize()[1])
    spacing = (image.GetSpacing()[0], image.GetSpacing()[2], image.GetSpacing()[1])
    direction = (
        1, 0, 0,
        0, 0, 1,
        0, 1, 0,
    )
    transform = sitk.Euler3DTransform()
    transform.SetRotation(np.pi / 2.0, 0.0, 0.0)  # +90° about X
    return resample_with_transform(image, transform, size, spacing, direction)


def make_sagittal(image: sitk.Image) -> sitk.Image:
    """Reformat to sagittal orientation (Y horizontal, Z vertical)."""
    size = (image.GetSize()[1], image.GetSize()[2], image.GetSize()[0])
    spacing = (image.GetSpacing()[1], image.GetSpacing()[2], image.GetSpacing()[0])
    direction = (
        0, 1, 0,
        0, 0, 1,
        1, 0, 0,
    )
    transform = sitk.Euler3DTransform()
    transform.SetRotation(0.0, -np.pi / 2.0, 0.0)  # -90° about Y
    return resample_with_transform(image, transform, size, spacing, direction)


def compute_cbct_soft_tissue_window(volume: np.ndarray) -> Tuple[float, float]:
    """Derive CBCT soft-tissue window/level using robust percentiles."""
    p5 = np.percentile(volume, 5)
    p95 = np.percentile(volume, 95)
    level = float((p5 + p95) / 2.0)
    window = float(p95 - p5)
    window = float(np.clip(window, 1200.0, 1800.0))
    level = float(np.clip(level, 200.0, 400.0))
    return window, level


def main():
    parser = argparse.ArgumentParser(description="SimpleITK-based MPR tester")
    parser.add_argument("dicom_dir", help="Directory containing a DICOM series")
    parser.add_argument("output_dir", help="Directory to store generated PNG stacks")
    parser.add_argument("--window", type=float, default=None, help="Window width (leave blank to auto-detect)")
    parser.add_argument("--level", type=float, default=None, help="Window level (leave blank to auto-detect)")
    parser.add_argument("--max-slices", type=int, default=None, help="Load only first N DICOM slices")
    parser.add_argument(
        "--normalize",
        action="store_true",
        help="Normalize CBCT intensities to pseudo-HU before windowing",
    )
    parser.add_argument(
        "--auto-window",
        action="store_true",
        help="Automatically derive CBCT soft-tissue window/level from percentiles",
    )
    args = parser.parse_args()

    image = load_series(args.dicom_dir, args.max_slices)
    spacing = image.GetSpacing()
    direction = image.GetDirection()
    origin = image.GetOrigin()

    volume = sitk.GetArrayFromImage(image).astype(np.float32)
    raw_min = float(np.min(volume))
    raw_max = float(np.max(volume))
    print("SHAPE (Z,Y,X):", volume.shape)
    print("SPACING (x,y,z):", spacing)
    print("DIRECTION:", direction)
    print(f"RAW RANGE: {raw_min:.1f} → {raw_max:.1f}")

    if args.normalize:
        volume = mpr.normalize_cbct_intensity(volume)
        norm_min = float(np.min(volume))
        norm_max = float(np.max(volume))
        print(f"NORMALIZED RANGE: {norm_min:.1f} → {norm_max:.1f}")
        image = sitk.GetImageFromArray(volume)
        image.SetSpacing(spacing)
        image.SetOrigin(origin)
        image.SetDirection(direction)

    window = args.window
    level = args.level
    if args.auto_window or window is None or level is None:
        window, level = compute_cbct_soft_tissue_window(volume)
        print(f"AUTO WINDOW: W={window:.1f}, L={level:.1f}")

    axial = image
    coronal = make_coronal(image)
    sagittal = make_sagittal(image)

    counts = {
        "axial": window_and_save(axial, os.path.join(args.output_dir, "axial"), window, level),
        "coronal": window_and_save(coronal, os.path.join(args.output_dir, "coronal"), window, level),
        "sagittal": window_and_save(sagittal, os.path.join(args.output_dir, "sagittal"), window, level),
    }

    msg = f"{image.GetSize()[2]} axial slices loaded"
    if args.max_slices:
        msg += f" (clamped to first {args.max_slices})"
    print(msg)
    print("Generated PNG stacks:")
    for plane, count in counts.items():
        print(f"  {plane}: {count} slices -> {os.path.join(args.output_dir, plane)}")


if __name__ == "__main__":
    main()
