import json
import logging
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pydicom
import SimpleITK as sitk

logger = logging.getLogger(__name__)


def load_cbct_volume(dicom_dir: Path) -> Tuple[sitk.Image, dict]:
    """Load DICOM directory into a SimpleITK volume."""
    reader = sitk.ImageSeriesReader()
    series_ids = reader.GetGDCMSeriesIDs(str(dicom_dir))
    if not series_ids:
        raise FileNotFoundError(f"No DICOM series found in {dicom_dir}")

    # Use first series for now; future: allow selection
    series_files = reader.GetGDCMSeriesFileNames(str(dicom_dir), series_ids[0])
    reader.SetFileNames(series_files)
    volume = reader.Execute()

    meta = {
        "series_id": series_ids[0],
        "num_slices": volume.GetSize()[2],
        "spacing": volume.GetSpacing(),
        "origin": volume.GetOrigin(),
        "direction": volume.GetDirection(),
    }
    return volume, meta


def load_png_stack(png_dir: Path, spacing: Tuple[float, float, float] = (1.0, 1.0, 1.0)) -> Tuple[sitk.Image, dict]:
    """Load a stack of PNGs (sorted by filename) into a volume."""
    png_files = sorted([p for p in png_dir.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"}])
    if not png_files:
        raise FileNotFoundError(f"No PNG files found in {png_dir}")

    import imageio.v3 as iio

    slices = [iio.imread(str(p)) for p in png_files]
    arr = np.stack(slices, axis=0).astype(np.uint8)
    volume = sitk.GetImageFromArray(arr)
    volume.SetSpacing(spacing)
    volume.SetOrigin((0.0, 0.0, 0.0))
    volume.SetDirection((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
    meta = {
        "series_id": "png_stack",
        "num_slices": arr.shape[0],
        "spacing": spacing,
    }
    return volume, meta


def normalize_volume_to_uint8(volume: sitk.Image) -> sitk.Image:
    arr = sitk.GetArrayFromImage(volume).astype(np.float32)  # [z, y, x]
    min_val, max_val = np.percentile(arr, [0.5, 99.5])
    arr = np.clip(arr, min_val, max_val)
    arr = (arr - min_val) / (max_val - min_val + 1e-6)
    arr_uint8 = (arr * 255.0).astype(np.uint8)

    norm_img = sitk.GetImageFromArray(arr_uint8)
    norm_img.CopyInformation(volume)
    return norm_img


def ensure_superior_inferior_order(volume: sitk.Image, tolerance: float = 1e-6) -> Tuple[sitk.Image, bool]:
    arr = sitk.GetArrayFromImage(volume)
    spacing = list(volume.GetSpacing())
    origin = list(volume.GetOrigin())
    direction = volume.GetDirection()

    flipped = False
    if spacing[2] < -tolerance:
        spacing[2] = abs(spacing[2])
        origin[2] = origin[2] - spacing[2] * (arr.shape[0] - 1)
        arr = arr[::-1]
        flipped = True

    ordered = sitk.GetImageFromArray(arr)
    ordered.SetSpacing(tuple(spacing))
    ordered.SetOrigin(tuple(origin))
    ordered.SetDirection(direction)
    return ordered, flipped


def drop_extraneous_slices(
    volume: sitk.Image, top_drop: int = 5, bottom_drop: int = 5
) -> sitk.Image:
    arr = sitk.GetArrayFromImage(volume)
    if top_drop + bottom_drop >= arr.shape[0]:
        raise ValueError("Drop counts exceed number of slices")
    trimmed = arr[top_drop : arr.shape[0] - bottom_drop]

    trimmed_img = sitk.GetImageFromArray(trimmed)
    spacing = list(volume.GetSpacing())
    origin = list(volume.GetOrigin())
    origin[2] = origin[2] + top_drop * spacing[2]
    trimmed_img.SetSpacing(volume.GetSpacing())
    trimmed_img.SetDirection(volume.GetDirection())
    trimmed_img.SetOrigin(tuple(origin))
    return trimmed_img


def save_slices_to_png(
    volume: sitk.Image,
    output_dir: Path,
    limit: Optional[int] = None,
    offset: int = 0,
):
    import imageio.v3 as iio

    arr = sitk.GetArrayFromImage(volume)  # [z, y, x]
    output_dir.mkdir(parents=True, exist_ok=True)

    num_slices = arr.shape[0]
    for idx in range(num_slices):
        if limit is not None and idx >= limit:
            break
        fname = output_dir / f"slice_{idx:04d}.png"
        iio.imwrite(fname, arr[idx])

    meta = {
        "num_slices": num_slices if limit is None else min(limit, num_slices),
        "spacing": volume.GetSpacing(),
    }
    with open(output_dir / "meta.json", "w") as fp:
        json.dump(meta, fp, indent=2, default=float)

