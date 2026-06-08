"""
CBCT slice preprocessing utilities for LLM segmentation.

Operations:
- intensity normalization
- CLAHE contrast enhancement
- gamma correction
- sharpening filter

These steps increase soft-tissue contrast so visual LLMs can detect
airway/tongue/soft palate/nasal structures more reliably.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image


@dataclass
class PreprocessConfig:
    clahe_clip_limit: float = 2.0
    clahe_tile_grid: int = 8
    gamma: float = 1.5
    sharpen_kernel: np.ndarray = np.array(
        [
            [0, -1, 0],
            [-1, 5, -1],
            [0, -1, 0],
        ],
        dtype=np.float32,
    )


def normalize_to_uint8(arr: np.ndarray) -> np.ndarray:
    """Normalize any numeric array to uint8 [0, 255]."""
    arr = arr.astype(np.float32)
    min_val = arr.min()
    max_val = arr.max()
    if max_val - min_val < 1e-6:
        return np.zeros_like(arr, dtype=np.uint8)
    norm = (arr - min_val) / (max_val - min_val)
    return (norm * 255.0).clip(0, 255).astype(np.uint8)


def preprocess_slice(arr: np.ndarray, config: PreprocessConfig | None = None) -> np.ndarray:
    """
    Apply CLAHE + gamma + sharpening to a grayscale slice array.

    Args:
        arr: Grayscale image as numpy array (uint8/uint16/float).
        config: Optional preprocessing config.

    Returns:
        uint8 numpy array with enhanced contrast.
    """
    if config is None:
        config = PreprocessConfig()

    if arr.ndim == 3 and arr.shape[-1] in (3, 4):
        arr = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)

    if arr.dtype != np.uint8:
        arr = normalize_to_uint8(arr)

    clahe = cv2.createCLAHE(
        clipLimit=config.clahe_clip_limit,
        tileGridSize=(config.clahe_tile_grid, config.clahe_tile_grid),
    )
    enhanced = clahe.apply(arr)

    gamma = np.power(enhanced / 255.0, config.gamma)
    gamma = (gamma * 255.0).clip(0, 255).astype(np.uint8)

    sharpened = cv2.filter2D(gamma, -1, config.sharpen_kernel)
    return sharpened


def preprocess_png_bytes(image_bytes: bytes, config: PreprocessConfig | None = None) -> bytes:
    """Convenience helper: take PNG bytes, return preprocessed PNG bytes."""
    image = Image.open(io.BytesIO(image_bytes)).convert("L")
    arr = np.array(image)
    processed = preprocess_slice(arr, config=config)
    output = io.BytesIO()
    Image.fromarray(processed).save(output, format="PNG")
    return output.getvalue()

