#!/usr/bin/env python3
from __future__ import annotations
"""
Generate and persist Claude bounding boxes for CBCT slices.

This runs the bbox-only phase (multi-slice context → JSON) without invoking SAM.
"""

import argparse
import json
import logging
from pathlib import Path
from typing import List, Tuple

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask_app.annotator.structure_config import SUPPORTED_STRUCTURES  # noqa: E402
from scripts.llm_segment_cbct_slice import (  # noqa: E402
    generate_bbox_for_slice,
    get_annotation_bucket,
    get_s3_client,
    load_slice_context,
)

logger = logging.getLogger(__name__)


def _load_manifest(case_id: str) -> dict:
    bucket = get_annotation_bucket()
    s3_client = get_s3_client()
    key = f"annotation_dataset/{case_id}/manifest.json"
    response = s3_client.get_object(Bucket=bucket, Key=key)
    manifest = json.loads(response["Body"].read().decode("utf-8"))
    return manifest


def _determine_slice_range(manifest: dict, requested_range: Tuple[int, int] | None) -> Tuple[int, int]:
    num_slices = manifest.get("num_slices") or manifest.get("num_axial_slices")
    if not isinstance(num_slices, int) or num_slices <= 0:
        raise ValueError("Manifest missing num_slices field")
    if requested_range:
        start, end = requested_range
        if start < 0 or start >= num_slices:
            raise ValueError(f"Slice start {start} out of bounds (0-{num_slices - 1})")
        end = min(max(end, start), num_slices - 1)
        return start, end
    return 0, num_slices - 1


def process_slice(case_id: str, structures: List[str], slice_index: int) -> None:
    context = load_slice_context(case_id, slice_index)
    for structure in structures:
        logger.info("Generating bbox for %s slice %s", structure, slice_index)
        generate_bbox_for_slice(
            case_id,
            structure,
            slice_index,
            context=context,
            save=True,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate/store Claude bounding boxes for CBCT slices.")
    parser.add_argument("case_id", help="Case identifier (e.g., CBCT_0001)")
    parser.add_argument(
        "--structure",
        action="append",
        choices=SUPPORTED_STRUCTURES,
        help="Structure(s) to process (defaults to all supported structures). Repeat for multiple.",
    )
    parser.add_argument(
        "--slice-range",
        nargs=2,
        type=int,
        metavar=("START", "END"),
        help="Optional inclusive slice range (0-indexed).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level))

    if not args.case_id.startswith("CBCT_"):
        logger.error("Invalid case_id format. Expected CBCT_XXXX, got %s", args.case_id)
        return

    structures = args.structure or SUPPORTED_STRUCTURES
    manifest = _load_manifest(args.case_id)
    slice_range = _determine_slice_range(manifest, tuple(args.slice_range) if args.slice_range else None)
    start_slice, end_slice = slice_range
    total_slices = end_slice - start_slice + 1

    logger.info(
        "Processing case %s structures=%s slices=%s-%s (count=%s)",
        args.case_id,
        ", ".join(structures),
        start_slice,
        end_slice,
        total_slices,
    )

    for slice_index in range(start_slice, end_slice + 1):
        try:
            process_slice(args.case_id, structures, slice_index)
        except Exception as exc:
            logger.error("Failed slice %s: %s", slice_index, exc)


if __name__ == "__main__":
    main()

