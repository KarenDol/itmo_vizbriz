#!/usr/bin/env python3
"""
Export normalized CBCT axial slices to PNGs for inspection.

Usage:
    python -m segmentation.cbct_slice_export /path/to/dicom_dir --out /tmp/cbct_preview --limit 50
"""
import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from segmentation.cbct_loader import (
    load_cbct_volume,
    load_png_stack,
    normalize_volume_to_uint8,
    ensure_superior_inferior_order,
    drop_extraneous_slices,
    save_slices_to_png,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Export CBCT slices for QA.")
    parser.add_argument("dicom_dir", type=Path, help="Path to DICOM directory")
    parser.add_argument("--out", type=Path, required=True, help="Output folder")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of slices")
    parser.add_argument("--drop-top", type=int, default=5, help="Slices to drop from top")
    parser.add_argument("--drop-bottom", type=int, default=5, help="Slices to drop from bottom")
    return parser.parse_args()


def main():
    logging.basicConfig(level=logging.INFO)
    args = parse_args()

    png_candidates = []
    if args.dicom_dir.is_dir():
        png_candidates = list(args.dicom_dir.glob("*.png"))
        if not png_candidates:
            png_candidates = list(args.dicom_dir.glob("*.jpg"))

    if png_candidates:
        volume, meta = load_png_stack(args.dicom_dir)
        logging.info("Loaded PNG stack: %s", meta)
    else:
        volume, meta = load_cbct_volume(args.dicom_dir)
        logging.info("Loaded DICOM volume: %s", meta)

    logging.info("Loaded volume: %s", meta)

    try:
        volume, flipped = ensure_superior_inferior_order(volume)
        if flipped:
            logging.info("Volume flipped to ensure superior→inferior ordering.")
    except Exception as exc:
        logging.warning("Skipping orientation enforcement: %s", exc)

    norm = normalize_volume_to_uint8(volume)
    base_out = args.out
    base_out.mkdir(parents=True, exist_ok=True)

    trimmed = drop_extraneous_slices(norm, top_drop=args.drop_top, bottom_drop=args.drop_bottom)
    logging.info(
        "Trimmed volume to %d slices (dropped top=%d, bottom=%d)",
        trimmed.GetSize()[2],
        args.drop_top,
        args.drop_bottom,
    )

    save_slices_to_png(trimmed, base_out, limit=args.limit)
    logging.info("Saved slices to %s", base_out)


if __name__ == "__main__":
    main()

