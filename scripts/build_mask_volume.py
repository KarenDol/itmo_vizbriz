#!/usr/bin/env python3
"""
Assemble per-slice masks into a 3D volume (NIfTI) and emit QA metrics.

Usage:
    python scripts/build_mask_volume.py CBCT_0001 airway --source pred
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import boto3
import numpy as np
import SimpleITK as sitk
from PIL import Image

# Ensure project root on sys.path so we can reuse shared helpers
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask_app.annotator.structure_config import SUPPORTED_STRUCTURES, ensure_manifest_structures  # noqa: E402
from scripts.llm_segment_cbct_slice import (  # noqa: E402
    get_annotation_bucket,
    get_s3_client,
    load_slice_from_s3,
)

logger = logging.getLogger(__name__)

MASK_SOURCE_DIRS = {
    "pred": "masks_pred",
    "corrected": "masks_corrected",
}

QA_OUTPUT_PREFIX = "qa"
VOLUME_OUTPUT_PREFIX = "mask_volumes"


@dataclass
class CaseMetadata:
    num_slices: int
    spacing: Tuple[float, float, float]


def _load_manifest(case_id: str) -> dict:
    bucket = get_annotation_bucket()
    s3_client = get_s3_client()
    manifest_key = f"annotation_dataset/{case_id}/manifest.json"
    response = s3_client.get_object(Bucket=bucket, Key=manifest_key)
    manifest = json.loads(response["Body"].read().decode("utf-8"))
    ensure_manifest_structures(manifest)
    return manifest


def _get_case_metadata(case_id: str) -> CaseMetadata:
    manifest = _load_manifest(case_id)
    num_slices = manifest.get("num_slices") or manifest.get("num_axial_slices")
    if not isinstance(num_slices, int) or num_slices <= 0:
        raise ValueError(f"Manifest missing num_slices for case {case_id}")

    spacing = manifest.get("voxel_spacing_mm") or manifest.get("voxel_spacing") or [0.3, 0.3, 0.3]
    spacing_tuple = tuple(float(v) for v in spacing[:3])
    if len(spacing_tuple) != 3:
        spacing_tuple = (0.3, 0.3, 0.3)

    return CaseMetadata(num_slices=num_slices, spacing=spacing_tuple)  # type: ignore[arg-type]


def _mask_key(case_id: str, structure: str, slice_index: int, source_dir: str) -> str:
    filename = f"axial_{slice_index:03d}.png"
    return f"annotation_dataset/{case_id}/{source_dir}/{structure}/{filename}"


def _download_mask_png(case_id: str, structure: str, slice_index: int, source_dir: str) -> Optional[bytes]:
    bucket = get_annotation_bucket()
    s3_client = get_s3_client()
    key = _mask_key(case_id, structure, slice_index, source_dir)
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read()
    except s3_client.exceptions.NoSuchKey:
        return None


def _ensure_shape(case_id: str, slice_index: int, reference_shape: Optional[Tuple[int, int]]) -> Tuple[int, int]:
    if reference_shape:
        return reference_shape
    slice_bytes = load_slice_from_s3(case_id, slice_index)
    img = Image.open(io.BytesIO(slice_bytes)).convert("L")
    shape = (img.height, img.width)
    img.close()
    return shape


def _stack_masks(
    case_id: str,
    structure: str,
    source: str,
    metadata: CaseMetadata,
) -> Tuple[np.ndarray, Dict[str, List]]:
    source_dir = MASK_SOURCE_DIRS[source]
    slice_arrays: List[np.ndarray] = []
    missing_slices: List[int] = []
    slice_metrics: List[Dict[str, float]] = []
    shape_hint: Optional[Tuple[int, int]] = None

    for slice_index in range(metadata.num_slices):
        mask_bytes = _download_mask_png(case_id, structure, slice_index, source_dir)
        if mask_bytes is None:
            missing_slices.append(slice_index)
            if shape_hint is None:
                shape_hint = _ensure_shape(case_id, slice_index, None)
            height, width = shape_hint
            mask_arr = np.zeros((height, width), dtype=np.uint8)
        else:
            img = Image.open(io.BytesIO(mask_bytes)).convert("L")
            mask_arr = np.array(img, dtype=np.uint8)
            img.close()
            shape_hint = (mask_arr.shape[0], mask_arr.shape[1])

        binary_mask = np.where(mask_arr > 127, 255, 0).astype(np.uint8)
        nonzero = int(np.count_nonzero(binary_mask))
        total = binary_mask.size
        ratio = (nonzero / total) if total else 0.0
        slice_metrics.append(
            {
                "slice": slice_index,
                "nonzero_pixels": nonzero,
                "coverage_ratio": ratio,
            }
        )
        slice_arrays.append(binary_mask)

    volume = np.stack(slice_arrays, axis=0)
    qa_lists = {
        "missing_slices": missing_slices,
        "slice_metrics": slice_metrics,
    }
    return volume, qa_lists


def _write_volume_to_s3(case_id: str, structure: str, source: str, volume: np.ndarray, spacing: Tuple[float, float, float]):
    img = sitk.GetImageFromArray(volume)
    img.SetSpacing(spacing)
    img.SetOrigin((0.0, 0.0, 0.0))
    img.SetDirection((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))

    bucket = get_annotation_bucket()
    s3_client = boto3.client("s3")  # separate client for upload_file compatibility
    key = f"annotation_dataset/{case_id}/{VOLUME_OUTPUT_PREFIX}/{source}/{structure}.nii.gz"

    with tempfile.NamedTemporaryFile(suffix=".nii.gz") as tmp:
        sitk.WriteImage(img, tmp.name, True)
        s3_client.upload_file(tmp.name, bucket, key)

    return key


def _write_qa_summary(case_id: str, structure: str, source: str, summary: dict):
    bucket = get_annotation_bucket()
    s3_client = get_s3_client()
    key = f"annotation_dataset/{case_id}/{QA_OUTPUT_PREFIX}/{source}/{structure}.json"
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(summary, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    return key


def build_mask_volume(case_id: str, structure: str, source: str = "pred") -> dict:
    if structure not in SUPPORTED_STRUCTURES:
        raise ValueError(f"Unsupported structure: {structure}")
    if source not in MASK_SOURCE_DIRS:
        raise ValueError(f"Invalid source '{source}'. Expected one of {list(MASK_SOURCE_DIRS)}")

    metadata = _get_case_metadata(case_id)
    volume, qa_lists = _stack_masks(case_id, structure, source, metadata)

    nonzero_voxels = int(np.count_nonzero(volume))
    total_voxels = int(volume.size)
    spacing_product = metadata.spacing[0] * metadata.spacing[1] * metadata.spacing[2]
    volume_mm3 = nonzero_voxels * spacing_product

    volume_key = _write_volume_to_s3(case_id, structure, source, volume, metadata.spacing)
    qa_payload = {
        "case_id": case_id,
        "structure": structure,
        "source": source,
        "num_slices": metadata.num_slices,
        "spacing_mm": metadata.spacing,
        "nonzero_voxels": nonzero_voxels,
        "total_voxels": total_voxels,
        "structure_volume_mm3": volume_mm3,
        "nonzero_ratio": (nonzero_voxels / total_voxels) if total_voxels else 0.0,
        **qa_lists,
    }
    qa_key = _write_qa_summary(case_id, structure, source, qa_payload)

    logger.info(
        "Built volume for %s/%s (%s). Nonzero voxels=%s (%.4f%%), volume=%.2f mm^3",
        case_id,
        structure,
        source,
        nonzero_voxels,
        100 * qa_payload["nonzero_ratio"],
        volume_mm3,
    )
    if qa_lists["missing_slices"]:
        logger.warning("Missing %s mask slices: %s", source, qa_lists["missing_slices"][:10])

    return {
        "volume_key": volume_key,
        "qa_key": qa_key,
        "summary": qa_payload,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stack mask PNGs into NIfTI volume and log QA metrics.")
    parser.add_argument("case_id", help="Case identifier (e.g., CBCT_0001)")
    parser.add_argument("structure", choices=SUPPORTED_STRUCTURES, help="Structure to assemble")
    parser.add_argument(
        "--source",
        choices=list(MASK_SOURCE_DIRS.keys()),
        default="pred",
        help="Mask source folder (pred vs corrected). Default: pred",
    )
    return parser.parse_args()


def main():
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    args = parse_args()
    if not args.case_id.startswith("CBCT_"):
        logger.error("Invalid case_id format. Expected CBCT_XXXX, got %s", args.case_id)
        return

    try:
        result = build_mask_volume(args.case_id, args.structure, source=args.source)
        print("\n✅ Mask volume assembled")
        print(f"   Case: {args.case_id}")
        print(f"   Structure: {args.structure}")
        print(f"   Source: {args.source}")
        print(f"   Volume S3 key: {result['volume_key']}")
        print(f"   QA JSON S3 key: {result['qa_key']}")
    except Exception as exc:
        logger.exception("Failed to build mask volume: %s", exc)
        print(f"\n❌ Failed: {exc}")


if __name__ == "__main__":
    main()

